"""Integration tests: Flask pages + store + database (Flask test client)."""

import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app  # noqa: E402

DEMO = {"login": "demo@example.com", "password": "Demo@1234"}


class TestShop(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.app = create_app(os.path.join(self.tmp.name, f"{self._testMethodName}.db"))
        self.c = self.app.test_client()
        self.store = self.app.store

    def token(self, url="/login"):
        html = self.c.get(url).get_data(as_text=True)
        return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)

    def post(self, url, page="/login", **data):
        data["csrf_token"] = self.token(page)
        return self.c.post(url, data=data, follow_redirects=True)

    def text(self, r):
        return r.get_data(as_text=True)

    def login(self):
        return self.post("/login", **DEMO)

    def pid(self, slug):
        return self.store.get_product(slug=slug).id

    # ---------- browsing ----------
    def test_public_pages(self):
        for url in ["/", "/search?q=watch", "/c/grocery", "/p/nova-x5-blue", "/cart", "/help", "/login", "/signup"]:
            r = self.c.get(url)
            self.assertEqual(r.status_code, 200, url)
            self.assertIn("demo e-commerce website", self.text(r), url)
        self.assertEqual(self.c.get("/p/does-not-exist").status_code, 404)

    def test_all_images_exist_and_are_jpg_or_png(self):
        folder = os.path.join(self.app.root_path, "static", "images")
        for root, _, files in os.walk(folder):
            for f in files:
                self.assertRegex(f, r"\.(jpg|png)$")
        for p in self.store.products():
            for img in p.images:
                self.assertTrue(os.path.exists(os.path.join(self.app.root_path, "static", img)), img)

    def test_search_filters_and_sort(self):
        html = self.text(self.c.get("/search?q=stridex&sort=price_desc"))
        self.assertIn("StrideX Runner", html)
        self.assertNotIn("Chrono Classic", html)
        html = self.text(self.c.get("/c/watches?brand=Chrono&max=1600"))
        self.assertIn("Chrono Sport", html)
        self.assertNotIn("Chrono Steel", html)
        self.assertIn("no results", self.text(self.c.get("/search?q=zzzzzz")))

    def test_product_page_pincode(self):
        html = self.text(self.c.get("/p/nova-x5-blue?pin=133001"))
        self.assertIn("Tomorrow", html)
        self.assertIn("valid 6-digit", self.text(self.c.get("/p/nova-x5-blue?pin=12")))

    # ---------- guest cart ----------
    def test_guest_cart_moves_into_account(self):
        self.post("/cart/add", page="/p/teatime-assam-500g", product_id=self.pid("teatime-assam-500g"), qty=2)
        self.assertIn("TeaTime Strong Assam Tea", self.text(self.c.get("/cart")))
        r = self.c.get("/checkout")
        self.assertIn("/login", r.location)                         # must sign in to check out
        self.login()
        demo = self.store.login("demo@example.com", "Demo@1234")
        self.assertEqual(self.store.get_cart(demo["id"]).get(self.pid("teatime-assam-500g")), 2)

    # ---------- full purchase ----------
    def test_checkout_with_coupon_and_tracking(self):
        self.login()
        r = self.post("/cart/coupon", page="/cart", code="yo10")
        self.assertIn("Coupon YO10 applied", self.text(r))
        demo = self.store.login("demo@example.com", "Demo@1234")
        address = self.store.default_address(demo["id"])
        r = self.post("/checkout", page="/checkout", address_id=address["id"], payment="upi", upi_id="ravi@okyo")
        html = self.text(r)
        self.assertIn("Order placed, thank you!", html)
        number = re.search(r'id="orderNumber">(OD\d+)<', html).group(1)
        order = self.store.get_order(demo["id"], number)
        self.assertEqual(order["coupon"], "YO10")
        self.assertIn("Order placed", self.text(self.c.get(f"/orders/{number}")))
        self.assertIn("Tax Invoice", self.text(self.c.get(f"/orders/{number}/invoice")))
        r = self.post(f"/orders/{number}/cancel", page=f"/orders/{number}")
        self.assertIn("cancelled", self.text(r))
        self.assertIn("refunded", self.text(r))

    def test_checkout_errors(self):
        self.login()
        demo = self.store.login("demo@example.com", "Demo@1234")
        address = self.store.default_address(demo["id"])
        r = self.post("/checkout", page="/checkout", address_id=address["id"], payment="upi", upi_id="bad")
        self.assertIn("valid UPI ID", self.text(r))

    def test_signup_gets_wallet(self):
        r = self.post("/signup", page="/signup", name="Neha Gupta", email="neha@example.com", mobile="9700000001",
                      password="Neha2026x", password2="Neha2026x")
        self.assertIn("Welcome to Yo Delivery", self.text(r))
        self.assertIn("₹2,000", self.text(self.c.get("/account")))

    def test_wishlist_needs_login_then_works(self):
        r = self.post("/wishlist/toggle", page="/", product_id=self.pid("nova-pro-12"), next="/")
        self.assertIn("sign in", self.text(r).lower())
        self.login()
        r = self.post("/wishlist/toggle", page="/", product_id=self.pid("book-flask"), next="/wishlist")
        self.assertIn("Web Development with Flask", self.text(r))

    def test_review_after_delivery(self):
        self.login()
        r = self.post("/p/book-python/review", page="/p/book-python/review", rating="5", title="Very useful",
                      body="Clear examples, helped me in my exams.")
        self.assertIn("review was published", self.text(r))
        self.assertIn("Very useful", self.text(r))

    def test_other_users_order_hidden(self):
        demo = self.store.login("demo@example.com", "Demo@1234")
        number = self.store.orders(demo["id"])[0]["number"]
        self.post("/signup", page="/signup", name="Other Person", email="o@example.com", mobile="9700000002",
                  password="Other2026x", password2="Other2026x")
        self.assertEqual(self.c.get(f"/orders/{number}").status_code, 404)

    def test_security(self):
        self.assertEqual(self.c.post("/login", data=DEMO).status_code, 400)          # no CSRF token
        html = self.text(self.c.get("/search?q=<script>alert(1)</script>"))
        self.assertNotIn("<script>alert(1)</script>", html)
        self.login()
        r = self.post("/login?next=//evil.example", page="/", **DEMO)
        self.assertEqual(r.request.path, "/")


if __name__ == "__main__":
    unittest.main()
