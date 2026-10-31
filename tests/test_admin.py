"""Tests for the admin panel and the activity log."""

import io
import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import support  # noqa: E402


ADMIN = {"login": "admin@example.com", "password": "Admin@1234"}
DEMO = {"login": "demo@example.com", "password": "Demo@1234"}


class AdminBase(unittest.TestCase):
    """Shared setup: a fresh database for every test + small helpers."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.app = support.make_app()
        self.c = self.app.test_client()
        self.store = self.app.store

    def post(self, url, page="/login", **data):
        html = self.c.get(page).get_data(as_text=True)
        data["csrf_token"] = re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)
        return self.c.post(url, data=data, follow_redirects=True)

    def text(self, r):
        return r.get_data(as_text=True)



class TestAdmin(AdminBase):
    def test_admin_pages_need_admin(self):
        r = self.c.get("/admin")
        self.assertIn("/login", r.location)                     # guests -> sign in
        self.post("/login", **DEMO)
        self.assertEqual(self.c.get("/admin").status_code, 403)  # normal customer -> refused
        self.assertEqual(self.c.get("/admin/database/users").status_code, 403)

    def test_admin_login_opens_dashboard(self):
        r = self.post("/login", **ADMIN)
        html = self.text(r)
        for words in ["Total sales", "Latest orders", "Live activity", "Top products", "Low stock"]:
            self.assertIn(words, html)

    def test_all_admin_pages(self):
        self.post("/login", **ADMIN)
        number = self.store.all_orders()[0]["number"]
        for url in ["/admin/orders", f"/admin/orders/{number}", "/admin/customers", "/admin/customers/2",
                    "/admin/products", "/admin/activity", "/admin/database", "/admin/database/orders"]:
            self.assertEqual(self.c.get(url).status_code, 200, url)
        self.assertEqual(self.c.get("/admin/database/sqlite_master").status_code, 404)

    def test_database_viewer_hides_passwords(self):
        self.post("/login", **ADMIN)
        html = self.text(self.c.get("/admin/database/users"))
        self.assertIn("demo@example.com", html)
        self.assertIn("(hidden)", html)
        self.assertNotIn("scrypt:", html)
        self.assertNotIn("pbkdf2:", html)
        csv = self.text(self.c.get("/admin/database/users.csv"))
        self.assertNotIn("scrypt:", csv)
        self.assertNotIn("pbkdf2:", csv)

    def test_activity_is_logged(self):
        self.post("/login", login="demo@example.com", password="wrong")
        self.post("/login", **DEMO)
        self.post("/cart/add", page="/p/book-flask", product_id=self.store.get_product(slug="book-flask").id)
        actions = [a["action"] for a in self.store.activity(limit=5)]
        self.assertEqual(actions[:3], ["Added to cart", "Logged in", "Login failed"])
        self.c.get("/logout")
        self.post("/login", **ADMIN)
        html = self.text(self.c.get("/admin/activity?action=Login+failed"))
        self.assertIn("demo@example.com", html)

    def test_admin_edits_stock_and_price(self):
        self.post("/login", **ADMIN)
        p = self.store.get_product(slug="nova-pro-12")
        r = self.post("/admin/products", page="/admin/products", product_id=p.id, price="25999", mrp="34999", stock="50")
        self.assertIn("Saved", self.text(r))
        p2 = self.store.get_product(slug="nova-pro-12")
        self.assertEqual((p2.price, p2.stock), (2599900, 50))
        r = self.post("/admin/products", page="/admin/products", product_id=p.id, price="40000", mrp="34999", stock="5")
        self.assertIn("MRP cannot be lower", self.text(r))
        self.assertIn("₹25,999", self.text(self.c.get("/p/nova-pro-12")))   # store shows new price

    def test_admin_cancels_order(self):
        demo = self.store.login("demo@example.com", "Demo@1234")
        self.store.save_cart(demo["id"], {self.store.get_product(slug="book-dsa").id: 1})
        number = self.store.place_order(demo["id"], self.store.default_address(demo["id"])["id"], "cod")
        self.post("/login", **ADMIN)
        r = self.post(f"/admin/orders/{number}", page=f"/admin/orders/{number}")
        self.assertIn("cancelled by admin", self.text(r))
        self.assertEqual(self.store.get_order(demo["id"], number)["status"], "Cancelled")

    def test_dashboard_numbers_match_database(self):
        st = self.store.admin_stats()
        orders = self.store.all_orders()
        self.assertEqual(st["orders"], len(orders))
        self.assertEqual(st["revenue"], sum(o["total"] for o in orders if not o["cancelled_at"]))


class TestAdminManagement(AdminBase):
    """Adding products, categories, coupons, managing customers, orders, reviews, settings."""

    def setUp(self):
        super().setUp()
        self.post("/login", **ADMIN)

    def png(self, color="red"):
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (300, 200), color).save(buf, "PNG")
        buf.seek(0)
        return buf

    def upload(self, url, files, **data):
        html = self.c.get(url).get_data(as_text=True)
        data["csrf_token"] = re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)
        data.update(files)
        return self.c.post(url, data=data, content_type="multipart/form-data", follow_redirects=True)

    def new_product(self, **extra):
        data = dict(name="Test Gaming Mouse with RGB Lights", brand="ClickPro", category="electronics", price="799",
                    mrp="1499", stock="30", about="6 buttons\nRGB lights", specs="DPI: 7200\nWarranty: 1 year",
                    tags="mouse gaming", active="yes")
        data.update(extra)
        return self.upload("/admin/products/new", {"images": [(self.png(), "mouse.png")]}, **data)

    def tearDown(self):
        import images
        for p in self.store.products(active_only=False):
            if p.art == "upload":
                images.delete_product_images(p.slug, self.store)
        super().tearDown()

    def test_add_product_with_picture(self):
        r = self.new_product()
        self.assertIn("New product added", self.text(r))
        p = self.store.search("gaming mouse")[0]
        self.assertEqual((p.price, p.stock, p.specs["DPI"]), (79900, 30, "7200"))
        for img in p.images:                                     # 3 gallery pictures were made
            self.assertIsNotNone(self.store.media(img[len("images/"):]), img)    # saved in the database
            self.assertEqual(self.c.get("/img/" + img[len("images/"):]).status_code, 200)
        html = self.text(self.c.get(f"/p/{p.slug}"))
        self.assertIn("Test Gaming Mouse", html)
        self.assertIn("47% off", html)

    def test_add_product_errors(self):
        r = self.upload("/admin/products/new", {}, name="Thing without picture", brand="X1", category="books",
                        price="10", mrp="20", stock="1", about="x")
        self.assertIn("upload at least one product picture", self.text(r))
        bad = io.BytesIO(b"this is not an image")
        r = self.upload("/admin/products/new", {"images": [(bad, "fake.jpg")]}, name="Fake picture product",
                        brand="X1", category="books", price="10", mrp="20", stock="1", about="x")
        self.assertIn("not a valid picture", self.text(r))
        self.assertEqual(self.store.search("fake picture"), [])          # nothing half-saved
        r = self.new_product(price="2000", mrp="1000")
        self.assertIn("MRP cannot be lower", self.text(r))

    def test_edit_hide_and_delete_product(self):
        self.new_product()
        p = self.store.search("gaming mouse")[0]
        r = self.upload(f"/admin/products/{p.id}/edit", {}, name="Test Gaming Mouse Pro", brand="ClickPro",
                        category="electronics", price="899", mrp="1499", stock="12", about="Updated", specs="", tags="")
        self.assertIn("Product updated", self.text(r))
        self.assertEqual(self.store.get_product(pid=p.id).price, 89900)
        self.post(f"/admin/products/{p.id}/hide", page="/admin/products")
        self.assertEqual(self.store.search("gaming mouse"), [])          # gone from the shop
        self.c.get("/logout")
        self.assertEqual(self.c.get(f"/p/{p.slug}").status_code, 404)
        self.post("/login", **ADMIN)
        self.post(f"/admin/products/{p.id}/delete", page="/admin/products")
        with self.assertRaises(Exception):
            self.store.get_product(pid=p.id)

    def test_sold_product_cannot_be_deleted(self):
        sold = self.store.all_orders()[0]["items"][0]["product_id"]
        r = self.post(f"/admin/products/{sold}/delete", page="/admin/products")
        self.assertIn("Hide it instead", self.text(r))

    def test_categories(self):
        r = self.upload("/admin/categories", {"image": (self.png("blue"), "toys.png")}, name="Toys & Games")
        self.assertIn("saved", self.text(r))
        self.assertIn("Toys &amp; Games", self.text(self.c.get("/")))       # in the shop menu
        self.new_product(category="toys-games", name="Remote Control Racing Car")
        self.assertIn("Remote Control Racing Car", self.text(self.c.get("/c/toys-games")))
        r = self.post("/admin/categories", page="/admin/categories", slug="toys-games", action="delete")
        self.assertIn("still has products", self.text(r))
        self.assertIsNotNone(self.store.media("categories/toys-games.jpg"))     # the picture is in the database

    def test_coupons(self):
        r = self.post("/admin/coupons", page="/admin/coupons", action="save", code="diwali50", kind="flat",
                      amount="50", min_order="300", active="yes")
        self.assertIn("DIWALI50 saved", self.text(r))
        items = [(self.store.get_product(slug="book-dsa"), 1)]
        self.assertEqual(self.store.price_summary(items, "DIWALI50")["coupon_discount"], 5000)
        self.post("/admin/coupons", page="/admin/coupons", action="toggle", code="DIWALI50")
        self.assertIn("not valid", self.store.price_summary(items, "DIWALI50")["coupon_error"])

    def test_manage_customer(self):
        uid = self.store.login("demo@example.com", "Demo@1234")["id"]
        page = f"/admin/customers/{uid}"
        before = self.store.get_user(uid)["wallet"]
        self.post(page, page=page, action="wallet", amount="-1000", reason="test")
        self.assertEqual(self.store.get_user(uid)["wallet"], before - 100000)
        before -= 100000
        self.post(page, page=page, action="wallet", amount="250")
        self.assertEqual(self.store.get_user(uid)["wallet"], before + 25000)
        self.post(page, page=page, action="password", new_password="NewPass2026")
        self.store.login("demo@example.com", "NewPass2026")
        self.post(page, page=page, action="details", name="Ravi Kumar", email="ravi@example.com",
                  mobile="9876543210", blocked="yes")
        with self.assertRaises(Exception):                       # blocked -> cannot sign in
            self.store.login("ravi@example.com", "NewPass2026")

    def test_admin_cannot_block_self(self):
        me = self.store.login("admin@example.com", "Admin@1234")["id"]
        r = self.post(f"/admin/customers/{me}", page=f"/admin/customers/{me}", action="details", name="Store Admin",
                      email="admin@example.com", mobile="9000000001", blocked="yes", is_admin="yes")
        self.assertIn("cannot remove your own admin rights or block yourself", self.text(r))

    def test_order_status_by_admin(self):
        demo = self.store.login("demo@example.com", "Demo@1234")
        self.store.save_cart(demo["id"], {self.store.get_product(slug="book-flask").id: 1})
        number = self.store.place_order(demo["id"], self.store.default_address(demo["id"])["id"], "cod")
        page = f"/admin/orders/{number}"
        r = self.post(page, page=page, action="status", status="Out for delivery", note="Courier Rakesh")
        self.assertIn("Out for delivery", self.text(r))
        o = self.store.get_order(demo["id"], number)
        self.assertEqual(o["status"], "Out for delivery")
        self.assertFalse(o["can_cancel"])
        r = self.post(page, page=page, action="status", status="Packed")
        self.assertIn("can only move forward", self.text(r))

    def test_delete_review(self):
        r = self.store.all_reviews()[0]
        count = self.store.get_product(pid=r["product_id"]).rating_count
        self.post("/admin/reviews", page="/admin/reviews", review_id=r["id"])
        self.assertEqual(self.store.get_product(pid=r["product_id"]).rating_count, count - 1)

    def test_settings_and_announcement(self):
        self.post("/admin/settings", page="/admin/settings", free_delivery_from="999", delivery_fee="60",
                  cod_limit="20000", signup_wallet="500", announcement="Diwali sale is live!")
        items = [(self.store.get_product(slug="book-dsa"), 1)]           # ₹399 < ₹999
        self.assertEqual(self.store.price_summary(items)["delivery_fee"], 6000)
        self.assertIn("Diwali sale is live!", self.text(self.c.get("/")))

    def test_admin_changes_own_details_and_password(self):
        self.post("/admin/account", page="/admin/account", action="details", name="Jean Michel",
                  email="boss@example.com", mobile="9000000001")
        r = self.post("/admin/account", page="/admin/account", action="password", old_password="Admin@1234",
                      new_password="Boss2026x", new_password2="Boss2026x")
        self.assertIn("password was changed", self.text(r))
        self.c.get("/logout")
        r = self.post("/login", login="boss@example.com", password="Boss2026x")
        self.assertIn("Jean Michel", self.text(r))
        self.assertIn("Dashboard", self.text(r))


if __name__ == "__main__":
    unittest.main()
