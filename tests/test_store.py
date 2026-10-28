"""Unit tests for store.py (shop logic, no web server)."""

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import demo_data  # noqa: E402
from store import ShopError, Store, delivery_days, rupees  # noqa: E402

ADDR = {"name": "Test User", "mobile": "9811111111", "line1": "12 Main Road", "city": "Ambala",
        "state": "Haryana", "pincode": "133001", "kind": "Home"}


class TestStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Store(os.path.join(self.tmp.name, "t.db"))
        demo_data.seed(self.s)
        self.uid = self.s.register("Test User", "test@example.com", "9811111111", "Secret123")
        self.addr = self.s.add_address(self.uid, ADDR)
        self.phone = self.s.get_product(slug="nova-x5-blue")
        self.tea = self.s.get_product(slug="teatime-assam-500g")

    def tearDown(self):
        self.tmp.cleanup()

    def buy(self, cart, payment="cod", coupon="", upi=""):
        self.s.save_cart(self.uid, cart)
        return self.s.place_order(self.uid, self.addr, payment, coupon, upi)

    # ----- helpers -----
    def test_rupees(self):
        self.assertEqual(rupees(149900), "₹1,499")
        self.assertEqual(rupees(1499900), "₹14,999")
        self.assertEqual(rupees(1234567800), "₹1,23,45,678")
        self.assertEqual(rupees(15050), "₹150.50")

    def test_delivery_days(self):
        self.assertEqual(delivery_days("133001"), 1)
        self.assertEqual(delivery_days("110001"), 2)
        self.assertEqual(delivery_days("560001"), 4)

    # ----- users -----
    def test_register_and_login(self):
        self.assertEqual(self.s.login("TEST@example.com", "Secret123")["id"], self.uid)
        self.assertEqual(self.s.login("9811111111", "Secret123")["id"], self.uid)
        with self.assertRaises(ShopError):
            self.s.login("test@example.com", "wrong")
        self.assertNotIn("Secret123", self.s.login("test@example.com", "Secret123")["password"])

    def test_register_validation(self):
        for args in [("A", "a@b.co", "9822222222", "Secret123"), ("Ann", "bad", "9822222222", "Secret123"),
                     ("Ann", "a@b.co", "12345", "Secret123"), ("Ann", "a@b.co", "9822222222", "short"),
                     ("Ann", "test@example.com", "9822222222", "Secret123")]:
            with self.assertRaises(ShopError, msg=args):
                self.s.register(*args)

    def test_address_rules(self):
        with self.assertRaises(ShopError):
            self.s.add_address(self.uid, {**ADDR, "pincode": "12"})
        second = self.s.add_address(self.uid, {**ADDR, "city": "Karnal", "is_default": "yes"})
        self.assertEqual(self.s.default_address(self.uid)["id"], second)
        self.s.delete_address(self.uid, second)
        self.assertEqual(self.s.default_address(self.uid)["id"], self.addr)

    # ----- catalog -----
    def test_catalog_and_search(self):
        self.assertGreaterEqual(len(self.s.products()), 40)
        self.assertTrue(all("nova" in p.name.lower() for p in self.s.search("nova")))
        prices = [p.price for p in self.s.search(category="footwear", sort="price_asc")]
        self.assertEqual(prices, sorted(prices))
        self.assertTrue(all(p.rating >= 4.4 for p in self.s.search(min_rating=4.4)))
        self.assertTrue(all(p.discount >= 60 for p in self.s.search(min_discount=60)))
        self.assertEqual({p.brand for p in self.s.search(brands=["Chrono"])}, {"Chrono"})
        self.assertEqual(self.s.search("nothingmatchesthis"), [])

    def test_product_properties(self):
        self.assertEqual(self.phone.discount, 25)
        self.assertEqual(len(self.phone.images), 3)

    # ----- cart & coupons -----
    def test_cart_limits(self):
        cart = self.s.cart_set({}, self.phone.id, 3)
        self.assertEqual(cart, {self.phone.id: 3})
        self.assertEqual(self.s.cart_set(cart, self.phone.id, 0), {})
        low = self.s.get_product(slug="zentra-book-14")
        with self.assertRaises(ShopError):
            self.s.cart_set({}, low.id, low.stock + 1) if low.stock < 10 else (_ for _ in ()).throw(ShopError())

    def test_price_summary_and_coupons(self):
        items = [(self.tea, 1)]                                     # ₹249 -> delivery fee
        s = self.s.price_summary(items)
        self.assertEqual((s["delivery_fee"], s["total"]), (4000, 24900 + 4000))
        self.assertIn("more", self.s.price_summary(items, "YO10")["coupon_error"])
        items = [(self.phone, 1)]
        s = self.s.price_summary(items, "YO10")
        self.assertEqual((s["coupon_discount"], s["delivery_fee"]), (20000, 0))   # 10% capped at ₹200
        self.assertEqual(self.s.price_summary(items, "FAKE")["coupon_error"], "This coupon code is not valid.")
        self.assertEqual(self.s.price_summary(items, "WELCOME100", self.uid)["coupon_discount"], 10000)

    # ----- orders -----
    def test_place_order_cod(self):
        stock = self.phone.stock
        number = self.buy({self.phone.id: 2}, "cod")
        o = self.s.get_order(self.uid, number)
        self.assertEqual(o["total"], 2 * self.phone.price)
        self.assertEqual(o["status"], "Order placed")
        self.assertTrue(o["can_cancel"])
        self.assertEqual(self.s.get_product(slug="nova-x5-blue").stock, stock - 2)
        self.assertEqual(self.s.get_cart(self.uid), {})

    def test_wallet_payment_and_refund(self):
        before = self.s.get_user(self.uid)["wallet"]
        number = self.buy({self.tea.id: 2}, "wallet")       # ₹498 + ₹40 delivery (below ₹499)
        self.assertEqual(self.s.get_user(self.uid)["wallet"], before - 2 * self.tea.price - 4000)
        self.s.cancel_order(self.uid, number)
        self.assertEqual(self.s.get_user(self.uid)["wallet"], before)
        self.assertEqual(self.s.get_order(self.uid, number)["status"], "Cancelled")

    def test_not_enough_wallet_changes_nothing(self):
        stock = self.phone.stock
        with self.assertRaises(ShopError):
            self.buy({self.phone.id: 1}, "wallet")                  # ₹14,999 > ₹2,000 balance
        self.assertEqual(self.s.get_product(slug="nova-x5-blue").stock, stock)
        self.assertEqual(self.s.get_cart(self.uid), {self.phone.id: 1})   # cart kept

    def test_upi_needs_valid_id(self):
        with self.assertRaises(ShopError):
            self.buy({self.tea.id: 1}, "upi", upi="not-an-upi")
        self.assertTrue(self.buy({self.tea.id: 1}, "upi", upi="test@okyo").startswith("OD"))

    def test_welcome_coupon_only_first_order(self):
        self.buy({self.tea.id: 3}, "cod", "WELCOME100")
        with self.assertRaises(ShopError):
            self.buy({self.tea.id: 3}, "cod", "WELCOME100")

    def test_tracking_and_review(self):
        number = self.buy({self.tea.id: 1})
        o = self.s.get_order(self.uid, number)
        self.assertFalse(self.s.can_review(self.uid, self.tea.id))
        with self.s.db() as conn:                                   # pretend it was ordered 3 days ago
            old = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute("UPDATE orders SET placed_at = ? WHERE number = ?", (old, number))
        o = self.s.get_order(self.uid, number)
        self.assertEqual(o["status"], "Delivered")
        self.assertFalse(o["can_cancel"])
        self.assertTrue(all(done for _, _, done in self.s.tracking(o)))
        count = self.s.get_product(slug=self.tea.slug).rating_count
        self.s.add_review(self.uid, self.tea.id, 5, "Great tea", "Strong taste, fresh pack, fast delivery.")
        self.assertEqual(self.s.get_product(slug=self.tea.slug).rating_count, count + 1)
        with self.assertRaises(ShopError):                          # only one review
            self.s.add_review(self.uid, self.tea.id, 4, "Again", "Trying to review twice here.")

    def test_wishlist_toggle(self):
        self.assertTrue(self.s.toggle_wishlist(self.uid, self.phone.id))
        self.assertIn(self.phone.id, self.s.wishlist_ids(self.uid))
        self.assertFalse(self.s.toggle_wishlist(self.uid, self.phone.id))

    def test_demo_data(self):
        demo = self.s.login(demo_data.DEMO_EMAIL, demo_data.DEMO_PASSWORD)
        statuses = [o["status"] for o in self.s.orders(demo["id"])]
        self.assertIn("Delivered", statuses)
        self.assertIn("Cancelled", statuses)


if __name__ == "__main__":
    unittest.main()
