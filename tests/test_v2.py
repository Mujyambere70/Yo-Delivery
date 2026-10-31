"""Tests for version 2: international shop, languages and currencies, card payments, messages,
the multi-seller marketplace, recommendations, returns, password reset, two-factor login,
backups and data that survives a restart."""

import hashlib
import hmac
import io
import json
import os
import re
import sys
import time
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import support  # noqa: E402
import otp  # noqa: E402
import world  # noqa: E402
from store import ShopError  # noqa: E402

RATES = {"USD": 1.0, "INR": 88.0, "RUB": 80.0, "EUR": 0.86, "RWF": 1450.0, "AED": 3.6725, "CNY": 7.1, "GBP": 0.75}
ADDR_IN = {"name": "Test User", "mobile": "9811111111", "line1": "12 Main Road", "city": "Ambala",
           "state": "Haryana", "pincode": "133001", "kind": "Home"}
ADDR_RU = {"name": "Ivan Petrov", "mobile": "+7 916 555 0101", "line1": "ul. Lenina 5, kv. 2", "city": "Krasnoyarsk",
           "pincode": "660049", "country": "RU", "kind": "Home"}
ADDR_RW = {"name": "Aline Mukamana", "mobile": "+250 788 555 101", "line1": "KN 5 Rd, house 10", "city": "Kigali",
           "pincode": "", "country": "RW", "kind": "Home"}


def ago(days=0, hours=0):
    return (datetime.now() - timedelta(days=days, hours=hours)).strftime("%Y-%m-%d %H:%M:%S")


class Base(unittest.TestCase):
    def setUp(self):
        self.note = support.fake_notifier()
        self.s = support.make_store(seed=False, notifier=self.note)
        self.s.load_catalog()
        self.s.save_fx_rates(RATES, "test")
        self.uid = self.s.register("Test User", "test.user@realmail.local", "9811111111", "Secret123")
        self.addr = self.s.add_address(self.uid, ADDR_IN)
        self.phone = self.s.get_product(slug="nova-x5-blue")
        self.tea = self.s.get_product(slug="teatime-assam-500g")

    def order(self, uid=None, addr=None, payment="wallet", items=None, when=None, currency=None, **kw):
        uid, addr = uid or self.uid, addr or self.addr
        self.s.save_cart(uid, items or {self.tea.id: 2})
        if payment == "wallet":
            with self.s.db() as conn:
                conn.execute("UPDATE users SET wallet = 10000000 WHERE id = ?", (uid,))
        return self.s.place_order(uid, addr, payment, when=when, currency=currency, rates=self.s.rates(), **kw)


# =====================================================================================
class TestWorld(Base):
    def test_money_formats_and_conversion(self):
        self.assertEqual(world.format_money(149900, "INR", "en"), "₹1,499")
        self.assertEqual(world.format_money(149950, "INR", "en"), "₹1,499.50")
        self.assertIn("1 499", world.format_money(149900, "RUB", "ru").replace(" ", " "))
        self.assertEqual(world.convert(8800, "USD", RATES), 100)                 # ₹88 = $1.00
        self.assertEqual(world.convert(8800, "RWF", RATES), 145000)              # 1,450 RWF (x100)
        self.assertIsNone(world.convert(8800, "XYZ", RATES))
        self.assertNotIn(".", world.format_money(145000, "RWF", "en"))           # no decimals for RWF

    def test_phone_and_postcode_rules(self):
        self.assertEqual(world.clean_mobile("IN", "+91 98111 11111"), "9811111111")
        self.assertEqual(world.clean_mobile("RU", "+7 916 555 0101"), "+79165550101")
        self.assertIsNone(world.clean_mobile("RU", "+33 6 12 34 56 78"))          # wrong country code
        self.assertIsNone(world.clean_postcode("RU", "6600"))
        self.assertEqual(world.clean_postcode("RW", ""), "")                      # Rwanda has no post codes
        self.assertIsNone(world.clean_postcode("FR", ""))

    def test_country_names_in_every_language(self):
        for lang in world.LANGUAGES:
            names = dict(world.countries(lang))
            self.assertGreater(len(names), 200, lang)
            self.assertTrue(names["RW"], lang)
        self.assertEqual(dict(world.countries("ru"))["RW"], "Руанда")

    def test_international_order_in_own_currency(self):
        rid = self.s.register("Ivan Petrov", "ivan@realmail.local", "+7 916 555 0101", "Secret123", country="RU", lang="ru")
        self.assertEqual(self.s.get_user(rid)["currency"], "RUB")
        addr = self.s.add_address(rid, ADDR_RU)
        with self.assertRaises(ShopError):                                       # no COD abroad
            self.order(rid, addr, "cod", currency="RUB")
        with self.assertRaises(ShopError):                                       # no UPI abroad
            self.order(rid, addr, "upi", upi_id="ivan@okyo", currency="RUB")
        number = self.order(rid, addr, "card", items={self.phone.id: 1}, currency="RUB")
        o = self.s.get_order(rid, number)
        self.assertEqual((o["currency"], o["country"], o["courier"]), ("RUB", "RU", "Yo Global Express"))
        self.assertEqual(o["delivery_fee"], self.s.setting("intl_fee_near") if world.shipping_zone("RU") == "near"
                         else self.s.setting("intl_fee_world"))
        self.assertEqual(o["total_local"], world.convert(o["total"], "RUB", RATES))
        self.assertTrue(o["awb"].endswith("RU"))
        self.assertEqual(o["status"], "Awaiting payment")

    def test_address_without_post_code(self):
        aid = self.s.add_address(self.uid, ADDR_RW)
        a = [x for x in self.s.addresses(self.uid) if x["id"] == aid][0]
        self.assertEqual((a["country"], a["pincode"]), ("RW", ""))
        with self.assertRaises(ShopError):
            self.s.add_address(self.uid, dict(ADDR_RU, pincode=""))               # Russia needs one

    def test_free_international_delivery_from_limit(self):
        items = self.s.cart_items({self.s.get_product(slug="zentra-book-14").id: 1})
        s = self.s.price_summary(items, "", self.uid, "FR")
        self.assertTrue(s["international"])
        self.assertEqual(s["delivery_fee"], 0)                                   # above intl_free_from


# =====================================================================================
class TestPayments(Base):
    def card_order(self):
        return self.order(payment="card", items={self.phone.id: 1})

    def test_test_gateway_success_and_tracking_starts(self):
        number = self.card_order()
        stock = self.s.get_product(pid=self.phone.id).stock
        ok, msg = self.s.pay_with_test_card(self.uid, number, "4242 4242 4242 4242", "12/40", "123", "Test User")
        self.assertTrue(ok)
        o = self.s.get_order(self.uid, number)
        self.assertEqual((o["payment_status"], o["status"]), ("Paid", "Order placed"))
        self.assertEqual(self.s.get_product(pid=self.phone.id).stock, stock)      # stock taken once only
        with self.assertRaises(ShopError):                                       # cannot pay twice
            self.s.pay_with_test_card(self.uid, number, "4242424242424242", "12/40", "123", "Test User")

    def test_test_gateway_declines(self):
        number = self.card_order()
        ok, msg = self.s.pay_with_test_card(self.uid, number, "4000000000000002", "12/40", "123", "Test User")
        self.assertFalse(ok)
        self.assertIn("declined", msg.lower())
        self.assertEqual(self.s.get_order(self.uid, number)["payment_status"], "Failed")
        ok, _ = self.s.pay_with_test_card(self.uid, number, "4000000000009995", "12/40", "123", "Test User")
        self.assertFalse(ok)
        ok, _ = self.s.pay_with_test_card(self.uid, number, "5555555555554444", "12/40", "123", "Test User")
        self.assertTrue(ok)                                                      # a new try may succeed

    def test_card_checks(self):
        number = self.card_order()
        for card, exp, cvc, name, words in [("4242424242424241", "12/40", "123", "A B", "not valid"),
                                            ("4242424242424242", "01/20", "123", "A B", "expired"),
                                            ("4242424242424242", "12/40", "1", "A B", "security code"),
                                            ("4242424242424242", "12/40", "123", "", "name on the card")]:
            with self.assertRaises(ShopError) as e:
                self.s.pay_with_test_card(self.uid, number, card, exp, cvc, name)
            self.assertIn(words, str(e.exception))

    def test_unpaid_card_order_expires_and_stock_returns(self):
        stock = self.s.get_product(pid=self.phone.id).stock
        number = self.order(payment="card", items={self.phone.id: 1}, when=ago(hours=1))
        self.assertEqual(self.s.get_product(pid=self.phone.id).stock, stock - 1)
        self.assertEqual(self.s.expire_unpaid(), 1)
        o = self.s.get_order(self.uid, number)
        self.assertTrue(o["cancelled_at"])
        self.assertEqual(self.s.get_product(pid=self.phone.id).stock, stock)
        self.assertEqual(self.s.expire_unpaid(), 0)

    def test_live_stripe_keys_are_refused(self):
        self.s.env = {"STRIPE_SECRET_KEY": "sk_live_abc"}
        with self.assertRaises(ShopError):
            self.s.gateway()
        self.s.env = {"STRIPE_SECRET_KEY": "sk_test_abc"}
        self.assertEqual(self.s.gateway(), "stripe")

    def test_stripe_checkout_and_webhook(self):
        self.s.env = {"STRIPE_SECRET_KEY": "sk_test_abc", "STRIPE_WEBHOOK_SECRET": "whsec_test"}
        calls = []

        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_http(req, timeout=None):
            calls.append((req.get_method(), req.full_url, (req.data or b"").decode()))
            return Resp(json.dumps({"id": "cs_test_1", "url": "https://checkout.stripe.com/c/pay/cs_test_1"}).encode())
        self.s.http = fake_http
        number = self.card_order()
        url = self.s.start_card_payment(self.uid, number, "https://shop.example/")
        self.assertTrue(url.startswith("https://checkout.stripe.com/"))
        self.assertIn("client_reference_id=" + number, calls[0][2])
        self.assertIn("unit_amount%5D=", calls[0][2])
        event = json.dumps({"type": "checkout.session.completed", "data": {"object": {
            "client_reference_id": number, "payment_status": "paid", "payment_intent": "pi_test_1"}}}).encode()
        with self.assertRaises(ShopError):                                       # wrong signature
            self.s.stripe_webhook(event, "t=1,v1=bad")
        t = str(int(time.time()))
        sig = hmac.new(b"whsec_test", f"{t}.".encode() + event, hashlib.sha256).hexdigest()
        self.assertEqual(self.s.stripe_webhook(event, f"t={t},v1={sig}"), number)
        o = self.s.get_order(self.uid, number)
        self.assertEqual((o["payment_status"], o["payment_ref"]), ("Paid", "pi_test_1"))
        self.assertEqual(self.s.stripe_webhook(event, f"t={t},v1={sig}"), number)   # repeated event: no harm

    def test_zero_decimal_amount_for_gateway(self):
        self.assertEqual(self.s.gateway_amount(145000, "RWF"), 1450)
        self.assertEqual(self.s.gateway_amount(1999, "USD"), 1999)

    def test_cancel_paid_card_order_refunds_card(self):
        number = self.card_order()
        self.s.pay_with_test_card(self.uid, number, "4242424242424242", "12/40", "123", "Test User")
        wallet = self.s.get_user(self.uid)["wallet"]
        self.s.cancel_order(self.uid, number)
        o = self.s.get_order(self.uid, number)
        self.assertEqual(o["refunded"], o["total"])
        self.assertEqual(self.s.get_user(self.uid)["wallet"], wallet)           # money back to the card, not wallet
        self.assertIn("Refunded", [p["status"] for p in self.s.payments_of(number)])


# =====================================================================================
class TestMessages(Base):
    def test_order_messages_by_email_and_sms_once_per_step(self):
        self.s.set_notify_prefs(self.uid, True, True)
        number = self.order()
        self.assertEqual(self.s.sync_order_updates(), 1)                         # "order placed"
        kinds = [m[0] for m in self.note.sent]
        self.assertIn("email", kinds)
        self.assertIn("sms", kinds)
        self.assertTrue(any(number in m[2] for m in self.note.sent if m[0] == "email"))
        self.note.sent.clear()
        with self.s.db() as conn:                          # pretend the order was placed two days ago
            conn.execute("UPDATE orders SET placed_at = ?, paid_at = ? WHERE number = ?", (ago(2), ago(2), number))
        self.assertEqual(self.s.sync_order_updates(), 1)
        self.assertEqual(self.s.sync_order_updates(), 0)                         # never twice
        self.assertTrue(any("delivered" in m[2].lower() for m in self.note.sent if m[0] == "email"))

    def test_example_addresses_never_get_email(self):
        ex = self.s.register("Demo Person", "someone@example.com", "9811111112", "Secret123")
        addr = self.s.add_address(ex, dict(ADDR_IN, mobile="9811111112"))
        self.s.set_notify_prefs(ex, True, True)
        self.order(ex, addr)
        self.s.sync_order_updates()
        self.assertEqual(self.note.sent, [])
        self.assertTrue(self.s.notifications(ex))                               # still kept in the site
        self.assertEqual(self.s.unseen_count(ex), 1)
        self.s.mark_seen(ex)
        self.assertEqual(self.s.unseen_count(ex), 0)

    def test_messages_in_customer_language(self):
        rid = self.s.register("Ivan Petrov", "ivan@realmail.local", "+7 916 555 0101", "Secret123", country="RU", lang="ru")
        self.s.add_address(rid, ADDR_RU)
        with self.s.db() as conn:
            self.s.notify(conn, rid, "Order {0} placed".replace("{0}", "OD1"), "Thank you for your order.")
        subject = self.s.notifications(rid)[0]["subject"]
        self.assertNotEqual(subject, "Order OD1 placed")                        # translated to Russian
        self.assertIn("OD1", subject)

    def test_password_reset(self):
        self.assertTrue(self.s.start_password_reset("TEST.USER@realmail.local", "https://shop.example/"))
        link = re.search(r"https://shop\.example/reset/(\S+)", self.note.sent[-1][3]).group(1)
        self.assertTrue(self.s.reset_token_ok(link))
        with self.assertRaises(ShopError):
            self.s.reset_password(link, "short")
        self.s.reset_password(link, "NewSecret2026")
        self.assertIsNotNone(self.s.login("test.user@realmail.local", "NewSecret2026"))
        with self.assertRaises(ShopError):                                       # one use only
            self.s.reset_password(link, "OtherSecret2026")
        self.assertFalse(self.s.start_password_reset("nobody@realmail.local", "https://shop.example/"))
        self.assertFalse(self.s.start_password_reset("demo@example.com", "https://shop.example/"))

    def test_two_factor_login(self):
        secret = otp.new_secret()
        with self.assertRaises(ShopError):
            self.s.enable_2fa(self.uid, secret, "000000" if otp.totp(secret) != "000000" else "111111")
        self.s.enable_2fa(self.uid, secret, otp.totp(secret))
        self.assertTrue(self.s.get_user(self.uid)["totp_enabled"])
        with self.assertRaises(ShopError):                                       # same code cannot be used again
            self.s.verify_2fa(self.uid, otp.totp(secret))
        self.s.verify_2fa(self.uid, otp.totp(secret, at=time.time() + 30))
        self.s.reset_2fa(self.uid)
        self.assertFalse(self.s.get_user(self.uid)["totp_enabled"])


# =====================================================================================
class TestMarketplace(Base):
    def setUp(self):
        super().setUp()
        self.sel_user = self.s.register("Shop Owner", "owner@realmail.local", "9811111199", "Secret123")
        self.shop = self.s.apply_seller(self.sel_user, {"shop_name": "Kigali Crafts", "about": "Handmade baskets and "
                                                        "bags from Kigali, Rwanda.", "country": "RW", "city": "Kigali",
                                                        "payout_details": "RW00 1234 5678"})

    def new_product(self):
        return self.s.save_seller_product(self.shop, {
            "name": "Agaseke Peace Basket", "brand": "KigaliCrafts", "category": "home", "price": "1000",
            "mrp": "1200", "stock": "10", "about": "Woven by hand", "specs": "Size: 20 cm", "tags": "basket"})

    def test_application_rules(self):
        with self.assertRaises(ShopError):
            self.s.apply_seller(self.sel_user, {"shop_name": "Second shop", "about": "x" * 30, "country": "IN",
                                                "city": "Ambala", "payout_details": "abc@okyo"})
        with self.assertRaises(ShopError):                                       # not approved yet
            self.new_product()
        self.assertEqual(self.s.seller(self.shop)["status"], "Pending")
        self.s.decide_seller(self.shop, "Approved", commission=12)
        self.assertEqual(self.s.seller(self.shop)["commission"], 12)

    def test_product_needs_approval_and_commission_is_kept(self):
        self.s.decide_seller(self.shop, "Approved", commission=12)
        slug = self.new_product()
        p = self.s.get_product(slug=slug)
        self.assertEqual(p.seller_id, self.shop)
        self.assertNotIn(slug, [x.slug for x in self.s.search("basket")])       # hidden until approved
        self.assertIn(p.id, [x.id for x in self.s.products_waiting()])
        self.s.approve_product(p.id)
        self.assertIn(slug, [x.slug for x in self.s.search("basket")])
        number = self.order(items={p.id: 2}, when=ago(3))
        sale = self.s.seller_sales(self.shop)[0]
        self.assertEqual((sale["gross"], sale["fee"], sale["net"]), (200000, 24000, 176000))
        self.assertEqual(sale["status"], "Delivered")
        bal = self.s.seller_balance(self.shop)
        self.assertEqual((bal["earned"], bal["balance"]), (176000, 176000))
        with self.assertRaises(ShopError):
            self.s.record_payout(self.shop, 5000, "too much", "admin")
        self.s.record_payout(self.shop, 1000, "UPI", "admin")
        self.assertEqual(self.s.seller_balance(self.shop)["balance"], 76000)
        self.s.decide_seller(self.shop, "Approved", commission=20)              # later change: old sale keeps 12%
        self.assertEqual(self.s.seller_sales(self.shop)[0]["fee"], 24000)
        self.assertTrue(number)

    def test_seller_cannot_touch_other_products(self):
        self.s.decide_seller(self.shop, "Approved")
        with self.assertRaises(ShopError):
            self.s.save_seller_product(self.shop, {"name": "Changed phone name", "brand": "Nova",
                                                   "category": "electronics", "price": "1", "mrp": "2", "stock": "1",
                                                   "about": "x"}, product_id=self.phone.id)

    def test_suspended_shop_disappears(self):
        self.s.decide_seller(self.shop, "Approved")
        p = self.s.get_product(slug=self.new_product())
        self.s.approve_product(p.id)
        self.s.decide_seller(self.shop, "Suspended", reason="Checks")
        self.assertEqual(self.s.search("basket"), [])


# =====================================================================================
class TestRecommendationsAndReturns(Base):
    def test_bought_together_and_viewed_together(self):
        other = self.s.register("Other", "other@realmail.local", "9811111133", "Secret123")
        oaddr = self.s.add_address(other, dict(ADDR_IN, mobile="9811111133"))
        buds = self.s.get_product(slug="soundwave-buds")
        self.order(items={self.phone.id: 1, buds.id: 1})
        self.order(other, oaddr, items={self.phone.id: 1, buds.id: 1})
        self.assertEqual(self.s.bought_together(self.phone.id)[0].id, buds.id)
        for v in ("v1", "v2", "v3"):
            self.s.record_view(v, None, self.phone.id)
            self.s.record_view(v, None, self.tea.id)
        self.assertIn(self.tea.id, [p.id for p in self.s.also_viewed(self.phone.id)])

    def test_recommended_follows_taste_and_skips_bought(self):
        for slug in ("chrono-steel-silver", "chrono-classic-gold"):
            self.s.record_view("me", self.uid, self.s.get_product(slug=slug).id)
        recs = self.s.recommended("me", self.uid, limit=6)
        self.assertTrue(recs)
        self.assertEqual(recs[0].category, "watches")
        self.order(items={recs[0].id: 1})
        self.assertNotIn(recs[0].id, [p.id for p in self.s.recommended("me", self.uid, limit=6)])

    def test_views_before_sign_in_are_kept(self):
        self.s.record_view("guest-1", None, self.phone.id)
        self.s.link_visitor("guest-1", self.uid)
        self.assertEqual(self.s.recently_viewed("other-browser", self.uid)[0].id, self.phone.id)

    def test_return_and_refund_to_wallet(self):
        number = self.order(when=ago(3))
        o = self.s.get_order(self.uid, number)
        self.assertEqual(o["status"], "Delivered")
        wallet = self.s.get_user(self.uid)["wallet"]
        with self.assertRaises(ShopError):
            self.s.request_return(self.uid, number, "Other", "short")
        amount = self.s.request_return(self.uid, number, "Damaged or broken", "Box was wet")
        self.assertEqual(amount, o["total"] - o["delivery_fee"])
        with self.assertRaises(ShopError):
            self.s.request_return(self.uid, number, "Damaged or broken")
        rid = self.s.returns("Requested")[0]["id"]
        self.s.decide_return(rid, True, "Admin")
        self.assertEqual(self.s.get_user(self.uid)["wallet"], wallet + amount)
        with self.assertRaises(ShopError):                                       # handled only once
            self.s.decide_return(rid, True, "Admin")

    def test_return_window_closes(self):
        number = self.order(when=ago(20))
        with self.assertRaises(ShopError):
            self.s.request_return(self.uid, number, "No longer needed")
        undelivered = self.order()
        with self.assertRaises(ShopError):
            self.s.request_return(self.uid, undelivered, "No longer needed")


# =====================================================================================
class TestDataSafety(unittest.TestCase):
    def test_data_survives_restart(self):
        path = support.target()
        from store import Store
        s1 = Store(path, driver=support.DRIVER, env=support.TEST_ENV)
        uid = s1.register("Keep Me", "keep.me@realmail.local", "9811111177", "Secret123")
        s1.save_media("products/test-1.jpg", "image/jpeg", b"\xff\xd8 picture")
        key = s1.secret_key()
        s2 = Store(path, driver=support.DRIVER, env=support.TEST_ENV)           # a new process after a restart
        self.assertEqual(s2.get_user(uid)["email"], "keep.me@realmail.local")
        self.assertEqual(s2.media("products/test-1.jpg")[1], b"\xff\xd8 picture")
        self.assertEqual(s2.secret_key(), key)                                    # logins stay valid

    def test_backup_and_restore(self):
        s = support.make_store(seed=True)
        before = {t: len(rows) for t, rows in s.export_data()["tables"].items()}
        blob = s.pack(s.export_data())
        bid, _ = s.save_backup("manual", by="Test")
        self.assertTrue(s.backups())
        with s.db() as conn:
            conn.execute("DELETE FROM reviews")
        s.import_data(s.unpack(blob))
        after = {t: len(rows) for t, rows in s.export_data()["tables"].items()}
        self.assertEqual(before["reviews"], after["reviews"])
        self.assertEqual(before, after)
        self.assertTrue(s.backup_blob(bid))

    def test_daily_tasks_run_once_per_day(self):
        s = support.make_store()
        s.fx_fetch = lambda: RATES
        from datetime import date
        self.assertIn("backup", s.daily_tasks(today=date(2026, 10, 31)))
        self.assertEqual(s.rates()["RUB"], 80.0)
        s.fx_fetch = lambda: {"RUB": 1.0}
        self.assertIsNone(s.daily_tasks(today=date(2026, 10, 31)))                                        # same day: nothing again
        self.assertEqual(s.rates()["RUB"], 80.0)


# =====================================================================================
class TestPages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = support.make_app()
        cls.app.store.save_fx_rates(RATES, "test")

    def setUp(self):
        self.c = self.app.test_client()

    def test_language_and_currency_switch(self):
        self.c.get("/lang/ru")
        html = self.c.get("/").get_data(as_text=True)
        self.assertIn('lang="ru"', html)
        self.assertIn("₽", html)                                                  # Russian visitors see roubles
        self.c.get("/lang/ar")
        html = self.c.get("/").get_data(as_text=True)
        self.assertIn('dir="rtl"', html)
        self.c.get("/currency/USD")
        self.assertIn("$", self.c.get("/p/nova-x5-blue").get_data(as_text=True))

    def test_prices_are_not_frozen_for_the_next_visitor(self):
        a, b = self.app.test_client(), self.app.test_client()
        a.get("/currency/EUR")
        self.assertIn("€", a.get("/").get_data(as_text=True))
        b.get("/currency/INR")
        html = b.get("/").get_data(as_text=True)
        promo = html[html.find("WELCOME100"):html.find("WELCOME100") + 200]
        self.assertIn("₹", promo)
        self.assertNotIn("€", promo)

    def test_every_language_has_a_catalogue(self):
        import i18n
        for lang in world.LANGUAGES:
            if lang != "en":
                exact, patterns = i18n.catalog(lang)
                self.assertGreater(len(exact), 300, lang)

    def test_about_page_names_the_developer(self):
        html = self.c.get("/about").get_data(as_text=True)
        self.assertIn("Jean Michel Mujyambere", html)
        self.assertIn("github.com/Mujyambere70", html)
        self.assertIn("Jean Michel Mujyambere", self.c.get("/").get_data(as_text=True))    # footer credit

    def test_pay_page_flow(self):
        support.login(self.c, "demo@example.com", "Demo@1234")
        st = self.app.store
        uid = st.login("demo@example.com", "Demo@1234")["id"]
        st.save_cart(uid, {st.get_product(slug="book-flask").id: 1})
        tok = support.token(self.c, "/checkout")
        r = self.c.post("/checkout", data={"csrf_token": tok, "address_id": st.default_address(uid)["id"],
                                           "payment": "card"})
        self.assertIn("/pay/", r.location)
        number = r.location.rsplit("/", 1)[1]
        html = self.c.get(f"/pay/{number}").get_data(as_text=True)
        self.assertIn("TEST MODE", html)
        r = self.c.post(f"/pay/{number}", data={"csrf_token": tok, "card": "4242 4242 4242 4242", "expiry": "12/40",
                                                "cvc": "123", "name": "Ravi Kumar"}, follow_redirects=True)
        self.assertIn("Payment received", r.get_data(as_text=True))

    def test_seller_centre_needs_a_shop(self):
        support.login(self.c, "demo@example.com", "Demo@1234")
        self.assertIn("/sell", self.c.get("/seller").location)
        c2 = self.app.test_client()
        support.login(c2, "seller@example.com", "Seller@1234")
        html = c2.get("/seller").get_data(as_text=True)
        self.assertIn("HomeEase Kitchen Studio", html)
        self.assertEqual(c2.get("/admin").status_code, 403)

    def test_stripe_webhook_without_signature_is_refused(self):
        r = self.c.post("/payments/stripe/webhook", data=b"{}", headers={"Stripe-Signature": "t=1,v1=x"})
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
