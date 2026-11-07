"""Tests for version 5, Yo Delivery for the whole world: 485 665 places (every Rwanda village, Indian post offices
with PIN codes, every settlement of Russia, the towns of the world), home currencies and the visitor's time zone, courier pickup and
delivery anywhere (road routes with a cache, demo robots in new places), Yo Tickets worldwide with connections,
waiting lists and round trips, product texts in several languages, customer-seller chat with moderation,
automatic seller payouts, face detection, quiz in 7 languages, learned recommendations and "my data"."""

import os
import re
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import support  # noqa: E402
import courier as C  # noqa: E402
import geo  # noqa: E402
import tickets as TK  # noqa: E402
import world  # noqa: E402
from core import ShopError  # noqa: E402

T0 = datetime(2026, 11, 6, 4, 0, 0)            # UTC
DAY = "2026-11-09"


class Base(unittest.TestCase):
    env = {"YO_TICKET_FILL": "0"}

    def setUp(self):
        self.s = support.make_store(seed=False, notifier=support.fake_notifier(email=False, sms=False), env=self.env)
        self.n = 0
        self.uid = self.user("Test User", "test.user@realmail.local", "9811111111")

    def user(self, name, email, mobile, money=50_000_00):
        uid = self.s.register(name, email, mobile, "Secret123", wallet=0)
        if money:
            with self.s.db() as conn:
                self.s.wallet_move(conn, uid, money, "topup", "TEST", "Test money")
        return uid

    def other(self, money=50_000_00):
        self.n += 1
        return self.user(f"Other {self.n}", f"other{self.n}@realmail.local", f"98440000{self.n:02d}", money)

    def wallet(self, uid):
        return self.s.get_user(uid)["wallet"]


# ---------------------------------------------------------------- places of the world

class TestWorldPlaces(unittest.TestCase):
    def test_every_rwanda_village_and_indian_pin_code(self):
        st = geo.stats()
        self.assertGreater(st["total"], 300_000)
        self.assertGreater(st["rw_villages"], 14_000)
        self.assertGreater(st["in_post"], 150_000)
        self.assertGreater(st["per_country"]["RU"], 5_000)
        mugunga = [p for p in geo.search("Mugunga", cc="RW", limit=10) if p["district"] == "Gakenke"]
        self.assertTrue(mugunga, "the village of Mugunga in Gakenke district")
        self.assertIn("Northern Province", mugunga[0]["label"])
        self.assertEqual(geo.search("133207")[0]["name"], "Mullana")              # an Indian PIN code

    def test_search_in_russian_letters_and_by_country(self):
        p = geo.search("Красноярск", limit=1)[0]
        self.assertEqual((p["name"], p["cc"]), ("Krasnoyarsk", "RU"))
        self.assertEqual(p["tz"], "Asia/Krasnoyarsk")
        self.assertTrue(all(x["cc"] == "IN" for x in geo.search("Ambala", cc="IN")))
        self.assertEqual(geo.search("Moscow", limit=1)[0]["id"], "Moscow")          # the Yo hub first

    def test_every_settlement_of_russia(self):
        st = geo.stats()
        self.assertGreater(st["ru_villages"], 150_000)
        v = [p for p in geo.search("Хилово", cc="RU", limit=10) if p["region"] == "Pskov Oblast"]
        self.assertTrue(v, "the village of Khilovo (Pskov Oblast)")
        self.assertEqual((v[0]["name"], v[0]["kind"], v[0]["approx"], v[0]["tz"]), ("Khilovo", "village", 0, "Europe/Moscow"))
        self.assertEqual(geo.get(v[0]["id"], "ru")["name"], "Хилово")             # Russian name in the Russian version
        self.assertEqual(geo.get("g1502026", "ru")["name"], "Красноярск")
        kirensk = geo.search("Kirensk", cc="RU", limit=1)[0]
        self.assertEqual(kirensk["region"], "Irkutsk Oblast")                       # was wrong in the old data
        self.assertEqual(C.resolve(v[0]["id"], "RU")["cc"], "RU")                  # the courier goes there
        self.assertEqual(TK.place(v[0]["id"])["key"], v[0]["id"])                   # and tickets know it

    def test_courier_resolve_keeps_the_33_cities(self):
        self.assertTrue(C.resolve("ambala cantt", "IN")["legacy"])
        self.assertIsNone(C.resolve("Atlantis", "IN"))
        self.assertIsNone(C.resolve("Moscow", "IN"))                                # wrong country
        self.assertEqual(C.resolve("Kigali", "RW")["cc"], "RW")


# ---------------------------------------------------------------- currencies and time zones

class TestCurrenciesAndTime(unittest.TestCase):
    def test_home_currencies(self):
        self.assertGreaterEqual(len(world.CURRENCIES), 150)
        for code in ("RWF", "RUB", "INR", "KES", "BRL", "JPY"):
            self.assertIn(code, world.CURRENCIES)
        self.assertEqual(world.default_currency("RW"), "RWF")
        self.assertEqual(world.default_currency("RU"), "RUB")
        self.assertEqual(world.default_currency("", "ru"), "RUB")
        self.assertIn("RWF", world.ZERO_DECIMAL)
        self.assertEqual(world.country_from_languages("rw-RW,rw;q=0.9,en;q=0.5"), "RW")
        self.assertEqual(world.card_currency("RWF"), "RWF")
        self.assertEqual(world.card_currency("XOF"), "USD")                        # charged in US dollars

    def test_visitor_time_zone(self):
        self.assertTrue(world.valid_tz("Asia/Krasnoyarsk"))
        self.assertFalse(world.valid_tz("Mars/Olympus"))
        from zoneinfo import ZoneInfo
        want = datetime(2026, 11, 6, 4, 0).replace(tzinfo=world.SERVER_TZ).astimezone(ZoneInfo("Asia/Tokyo"))
        world.set_context("en", "INR", {}, tz="Asia/Tokyo")
        try:
            self.assertIn(want.strftime("%I:%M %p"), world.format_when("2026-11-06 04:00:00"))   # server -> Tokyo
            self.assertIn("04:00 AM", world.format_when("2026-11-06 04:00:00", convert=False))
        finally:
            world.set_context("en", "INR", {}, tz="")


# ---------------------------------------------------------------- courier anywhere

class TestCourierAnywhere(Base):
    def test_quote_inside_rwanda_and_russia(self):
        q = self.s.courier_quote({"kind": "parcel", "weight": "1", "from_country": "RW", "from_city": "Kigali",
                                  "to_country": "RW", "to_city": "Mugunga", "to_place": "rw6833"})
        self.assertEqual(q["zone"], "regional")                    # 52 km: the nearby rates
        self.assertEqual(q["tax_label"], "VAT 18%")                # no Indian GST; version 6: the VAT of Rwanda
        self.assertEqual(q["to_place"], "rw6833")
        self.assertIn("Gakenke", q["to_label"])
        q = self.s.courier_quote({"kind": "parcel", "weight": "1", "from_country": "RU", "from_city": "Krasnoyarsk",
                                  "to_country": "RU", "to_city": "Moscow"})
        self.assertEqual(q["zone"], "national")
        self.assertEqual(q["zone_name"], "Whole country")
        self.assertGreater(q["distance_km"], 3000)

    def test_unknown_place_inside_the_country_is_refused(self):
        with self.assertRaises(ShopError):
            self.s.courier_quote({"kind": "parcel", "weight": "1", "from_country": "RW", "from_city": "Kigali",
                                  "to_country": "RW", "to_city": "Qwzxville"})
        with self.assertRaisesRegex(ShopError, "abroad"):
            self.s.courier_quote({"kind": "grocery", "weight": "1", "from_country": "RW", "from_city": "Kigali",
                                  "to_country": "UG", "to_city": "Kampala"})

    def book_rw(self):
        return self.s.book_shipment(self.uid, {
            "kind": "parcel", "contents": "School books", "weight": "1", "from_country": "RW", "from_city": "Kigali",
            "from_name": "Eric H", "from_mobile": "+250788123456", "from_address": "KN 5 Rd, Kiyovu",
            "to_country": "RW", "to_city": "Mugunga", "to_place": "rw6833", "to_name": "Aline U",
            "to_mobile": "+250788654321", "to_address": "Near the health centre", "service": "standard",
            "payment": "wallet"})

    def test_robot_courier_appears_in_a_new_place(self):
        with self.s.db() as conn:
            conn.execute("INSERT INTO settings (key, value) VALUES ('world_robots', '1') "
                         "ON CONFLICT (key) DO UPDATE SET value = '1'")
        number = self.book_rw()
        with self.s.db() as conn:
            s = conn.execute("SELECT * FROM shipments WHERE number = ?", (number,)).fetchone()
            c = conn.execute("SELECT c.*, u.email FROM couriers c JOIN users u ON u.id = c.user_id WHERE c.id = ?",
                             (s["courier_id"],)).fetchone()
        self.assertEqual((s["from_country"], s["from_place"], s["to_place"]), ("RW", "Kigali", "rw6833"))
        self.assertEqual((c["place"], c["demo"]), ("Kigali", 1))
        self.assertTrue(c["email"].endswith("@couriers.example"))  # a reserved domain: never emailed

    def test_no_robots_without_the_demo_switch(self):
        number = self.book_rw()
        with self.s.db() as conn:
            self.assertIsNone(conn.execute("SELECT courier_id FROM shipments WHERE number = ?",
                                           (number,)).fetchone()[0])

    def test_courier_serves_villages_around_the_town(self):
        self.assertTrue(C.serves("Kigali", "Kigali"))
        self.assertFalse(C.serves("Ambala Cantt", "Mullana"))      # two Yo cities: each its own couriers
        near = geo.search("Kimironko", cc="RW", limit=1)[0]["id"]
        self.assertTrue(C.serves("Kigali", near))
        self.assertFalse(C.serves("Kigali", "rw6833"))             # Mugunga is 52 km away

    def test_road_route_cache(self):
        calls = []
        real = geo.osrm_route

        def fake(a, b, timeout=3):
            calls.append((a, b))
            return 12.3, [list(a), [0.5, 0.5], list(b)]
        geo.osrm_route = fake
        try:
            store = support.make_store(seed=False, env={"YO_ROUTING": "1"})
            r1 = store.road_route((30.0, 76.0), (30.1, 76.1))
            r2 = store.road_route((30.0, 76.0), (30.1, 76.1))
        finally:
            geo.osrm_route = real
        self.assertEqual((r1["km"], r1["source"], len(r1["line"])), (12.3, "osrm", 3))
        self.assertEqual(r2["km"], 12.3)
        self.assertEqual(len(calls), 1)                             # the second answer came from the cache
        straight = self.s.road_route((31.0, 76.0), (31.1, 76.1))    # YO_ROUTING=0: straight line x 1.3
        self.assertEqual(straight["source"], "straight")

    def test_address_with_a_village_and_a_pin(self):
        self.s.add_address(self.uid, {"country": "RW", "name": "Aline Uwase", "mobile": "+250788654321",
                                      "line1": "House 12, near the market", "city": "Mugunga", "place_id": "rw6833",
                                      "lat": "-1.70", "lng": "29.79", "state_abroad": "Northern Province"})
        a = self.s.addresses(self.uid)[0]
        self.assertEqual((a["place_id"], a["city"]), ("rw6833", "Mugunga"))
        self.assertAlmostEqual(a["lat"], -1.70, places=2)


# ---------------------------------------------------------------- Yo Tickets worldwide

class TestTicketsWorld(Base):
    def test_rules_of_the_world(self):
        with self.assertRaisesRegex(ShopError, "no passenger trains"):
            TK.check_route("train", "Kigali", "Huye")
        a, b, _ = TK.check_route("bus", "Kigali", "Kampala")       # neighbours: a bus crosses the border
        self.assertEqual((a["country"], b["country"]), ("RW", "UG"))
        a, b, notes = TK.check_route("flight", "rw6833", "Moscow")
        self.assertEqual(a["name"], "Kigali")
        self.assertIn("nearest airport to Mugunga", notes[0])
        a, b, _ = TK.check_route("train", "Krasnoyarsk", "Moscow")
        self.assertEqual(a["tz"], "Asia/Krasnoyarsk")
        svc = {"mode": "train", "from_city": a["key"], "to_city": b["key"]}
        self.assertEqual(TK.gst_rate(svc, "SL"), 0)                  # no Indian GST in Russia
        self.assertEqual(TK.class_name("2A", "RU"), "Sleeper compartment (4 berths)")

    def test_connection_is_paid_and_cancelled_together(self):
        with self.s.db() as conn:
            self.s.wallet_move(conn, self.uid, 300_000_00, "topup", "TEST", "Money for long flights")
        r = self.s.search_trips("flight", "Krasnoyarsk", "Kigali", DAY, 1, at=T0)
        self.assertFalse(r["trips"])
        self.assertTrue(r["connections"])
        c = r["connections"][0]
        self.assertGreaterEqual(c["change_min"], 90)
        l1, l2 = c["legs"]
        s1 = self.free(l1["trip_id"], "Y")
        p1 = self.s.hold_seats(self.uid, l1["trip_id"], s1, at=T0)
        p2 = self.s.hold_seats(self.uid, l2["trip_id"], self.free(l2["trip_id"], "Y"), at=T0, link=p1)
        b = self.s.get_booking(p1, self.uid)
        self.assertEqual(b["linked"]["pnr"], p2)
        before = self.wallet(self.uid)
        f = {"contact_mobile": "9811111111", "payment": "wallet", "name_0": "Jean M", "age_0": "26"}
        self.s.pay_booking(self.uid, p1, f, at=T0 + timedelta(minutes=1))
        b1, b2 = self.s.get_booking(p1, self.uid), self.s.get_booking(p2, self.uid)
        self.assertEqual((b1["status"], b2["status"]), (TK.CONFIRMED, TK.CONFIRMED))
        self.assertEqual(before - self.wallet(self.uid), b1["total"] + b2["total"])
        self.assertEqual(b2["passengers"][0]["name"], "Jean M")
        self.s.cancel_booking(self.uid, p1, at=T0 + timedelta(hours=1))
        self.assertEqual(self.s.get_booking(p2, self.uid)["status"], TK.CANCELLED)

    def test_second_flight_needs_the_same_seats_count(self):
        c = self.s.search_trips("flight", "Krasnoyarsk", "Kigali", DAY, 1, at=T0)["connections"][0]
        p1 = self.s.hold_seats(self.uid, c["legs"][0]["trip_id"], self.free(c["legs"][0]["trip_id"], "Y"), at=T0)
        with self.assertRaisesRegex(ShopError, "1 seats"):
            self.s.hold_seats(self.uid, c["legs"][1]["trip_id"], self.free(c["legs"][1]["trip_id"], "Y", 2),
                              at=T0, link=p1)

    def free(self, trip_id, cls, n=1):
        _, secs = self.s.seat_map(trip_id, at=T0)
        return [x["code"] for sec in secs for row in sec["rows"] for x in row
                if x and not x["taken"] and x["cls"] == cls and not x["fee"]][:n]

    def fill(self, trip_id, cls, leave=0):
        """Other travellers take every seat of a class (booking 0 = sold by other channels)."""
        codes = self.free(trip_id, cls, 10_000)
        with self.s.db() as conn:
            for code in codes[leave:]:
                conn.execute("INSERT INTO ticket_seats (trip_id, seat, booking_id, created_at) VALUES (?, ?, 0, ?)",
                             (trip_id, code, "2026-11-06 04:00:00"))
        return codes[:leave]

    def test_waiting_list_is_confirmed_when_a_seat_comes_back(self):
        t = self.s.search_trips("train", "Chandigarh", "New Delhi", DAY, 1, at=T0)["trips"][0]
        cls = t["classes"][0]["cls"]
        left = self.fill(t["trip_id"], cls, leave=1)
        other = self.other()
        po = self.s.hold_seats(other, t["trip_id"], left, at=T0)
        self.s.pay_booking(other, po, {"contact_mobile": "9811111112", "payment": "wallet", "name_0": "Other",
                                       "age_0": "40"}, at=T0)
        with self.assertRaisesRegex(ShopError, "only for full train"):
            self.s.join_waitlist(self.uid, t["trip_id"], "XX", 1, at=T0)
        pnr = self.s.join_waitlist(self.uid, t["trip_id"], cls, 1, at=T0)
        self.s.pay_booking(self.uid, pnr, {"contact_mobile": "9811111111", "payment": "wallet", "name_0": "Me",
                                           "age_0": "26"}, at=T0)
        b = self.s.get_booking(pnr, self.uid)
        self.assertEqual((b["status"], b["wl"], b["passengers"][0]["label"]), (TK.WAITLISTED, 1, "WL 1"))
        self.s.cancel_booking(other, po, at=T0 + timedelta(hours=1))           # a seat comes back
        b = self.s.get_booking(pnr, self.uid)
        self.assertEqual(b["status"], TK.CONFIRMED)
        self.assertEqual(b["passengers"][0]["seat"], left[0])

    def test_waiting_list_not_confirmed_gets_all_the_money_back(self):
        t = self.s.search_trips("train", "Chandigarh", "New Delhi", DAY, 1, at=T0)["trips"][0]
        cls = t["classes"][0]["cls"]
        self.fill(t["trip_id"], cls)
        before = self.wallet(self.uid)
        pnr = self.s.join_waitlist(self.uid, t["trip_id"], cls, 2, at=T0)
        self.s.pay_booking(self.uid, pnr, {"contact_mobile": "9811111111", "payment": "wallet", "name_0": "Ann",
                                           "age_0": "26", "name_1": "Bob", "age_1": "27"}, at=T0)
        self.assertLess(self.wallet(self.uid), before)
        depart = TK._dt(t["depart_utc"])
        done = self.s.tickets_tick(at=depart - timedelta(hours=TK.CHART_HOURS - 0.5))
        self.assertEqual(done["waitlist_refunded"], 1)
        self.assertEqual(self.wallet(self.uid), before)
        self.assertEqual(self.s.get_booking(pnr, self.uid)["status"], TK.CANCELLED)

    def test_round_trip_return_has_no_convenience_fee(self):
        go = self.s.search_trips("bus", "Ambala Cantt", "New Delhi", DAY, 1, at=T0)["trips"][0]
        p1 = self.s.hold_seats(self.uid, go["trip_id"], self.free(go["trip_id"], go["classes"][0]["cls"]), at=T0)
        back = self.s.search_trips("bus", "New Delhi", "Ambala Cantt", "2026-11-12", 1, at=T0)["trips"][0]
        with self.assertRaisesRegex(ShopError, "round trip"):                        # the way there is not paid yet
            self.s.hold_seats(self.uid, back["trip_id"], self.free(back["trip_id"], back["classes"][0]["cls"]),
                              at=T0, return_of=p1)
        self.s.pay_booking(self.uid, p1, {"contact_mobile": "9811111111", "payment": "wallet", "name_0": "Me",
                                          "age_0": "26"}, at=T0)
        p2 = self.s.hold_seats(self.uid, back["trip_id"], self.free(back["trip_id"], back["classes"][0]["cls"]),
                               at=T0, return_of=p1)
        b2 = self.s.get_booking(p2, self.uid)
        self.assertEqual((b2["conv_fee"], b2["return_of"]), (0, p1))
        same = self.s.search_trips("bus", "Ambala Cantt", "New Delhi", "2026-11-12", 1, at=T0)["trips"][0]
        with self.assertRaisesRegex(ShopError, "back the same way"):
            self.s.hold_seats(self.uid, same["trip_id"], self.free(same["trip_id"], same["classes"][0]["cls"]),
                              at=T0, return_of=p1)



# ---------------------------------------------------------------- sellers, chat, payouts

class SellerBase(Base):
    def setUp(self):
        super().setUp()
        self.seller_user = self.user("Meera K", "meera.k@realmail.local", "9822222222", money=0)
        self.shop = self.s.apply_seller(self.seller_user, {"shop_name": "Test Kitchen", "about": "Pots and pans for tests",
                                                           "country": "IN", "city": "Ambala",
                                                           "payout_details": "meera@okyo"})
        self.s.decide_seller(self.shop, "Approved")
        with self.s.db() as conn:
            self.pid = conn.execute(
                "INSERT INTO products (slug, name, brand, category, price, mrp, rating_sum, rating_count, stock, art, "
                "about, specs, tags, created_at, seller_id) VALUES ('test-pan', 'Test Pan 24 cm', 'TestBrand', 'home', "
                "99900, 129900, 0, 0, 12, '', '[\"Non-stick\"]', '{}', 'pan', '2026-11-01 10:00:00', ?)",
                (self.shop,)).lastrowid


class TestChat(SellerBase):
    def test_contacts_are_hidden_and_rude_words_held(self):
        tid = self.s.start_chat(self.uid, self.pid, text="Hello, call me on +91 98123 45678 or a@b.com")
        t, msgs = self.s.chat_thread(self.seller_user, tid)
        self.assertNotIn("98123", msgs[0]["body"])
        self.assertNotIn("a@b.com", msgs[0]["body"])
        self.assertIn("[hidden by Yo]", msgs[0]["body"])
        self.assertEqual(t["other"], "Test")                                    # the seller sees the first name only
        r = self.s.send_chat(self.seller_user, tid, "You are stupid")
        self.assertEqual(r["status"], "Held")
        self.assertEqual(len(self.s.chat_thread(self.uid, tid)[1]), 1)          # the customer does not see it
        self.assertEqual(len(self.s.chat_queue()), 1)
        self.s.decide_chat(r["id"], True, "Admin")
        self.assertEqual(len(self.s.chat_thread(self.uid, tid)[1]), 2)

    def test_report_and_strangers(self):
        tid = self.s.start_chat(self.uid, self.pid, text="Is it in stock?")
        mid = self.s.send_chat(self.seller_user, tid, "Pay me directly by bank transfer to me")["id"]
        self.assertEqual(self.s.chat_queue()[0]["id"], mid)                      # payment outside the shop: held
        ok = self.s.send_chat(self.seller_user, tid, "Yes, 12 pieces.")["id"]
        self.s.report_chat(self.uid, ok)
        self.assertEqual(len(self.s.chat_queue()), 2)
        with self.assertRaises(ShopError):
            self.s.chat_thread(self.other(), tid)                                 # nobody else can read it
        with self.assertRaisesRegex(ShopError, "own shop"):
            self.s.start_chat(self.seller_user, self.pid, text="hi")

    def test_too_many_messages(self):
        tid = self.s.start_chat(self.uid, self.pid)
        for i in range(20):
            self.s.send_chat(self.uid, tid, f"Message {i}")
        with self.assertRaisesRegex(ShopError, "Too many"):
            self.s.send_chat(self.uid, tid, "one more")


class TestSellerExtras(SellerBase):
    def test_product_texts_in_other_languages(self):
        self.s.save_product_texts(self.pid, {"ru": ("Тестовый товар", "Первая строка\nВторая строка"),
                                             "fr": ("", "")}, seller_id=self.shop)
        p = self.s.localize_products([self.s.get_product(pid=self.pid)], "ru")[0]
        self.assertEqual(p.name, "Тестовый товар")
        self.assertEqual(p.about, ["Первая строка", "Вторая строка"])
        self.assertNotEqual(self.s.get_product(pid=self.pid).name, "Тестовый товар")   # the original stays
        with self.assertRaises(ShopError):
            self.s.save_product_texts(self.pid, {"ru": ("X", "")}, seller_id=self.shop + 99)

    def test_automatic_payout_to_yo_pay(self):
        with self.s.db() as conn:
            conn.execute("INSERT INTO settings (key, value) VALUES ('last_payout_day', '2000-01-01') "
                         "ON CONFLICT (key) DO UPDATE SET value = '2000-01-01'")
        real = self.s.seller_balance
        self.s.seller_balance = lambda sid: {"balance": 2_500_00} if sid == self.shop else real(sid)
        try:
            paid = self.s.auto_payouts(day="2026-11-06")
            again = self.s.auto_payouts(day="2026-11-06")                       # once a day only
        finally:
            self.s.seller_balance = real
        self.assertEqual(paid, {self.shop: 2_500_00})
        self.assertEqual(again, {})
        self.assertEqual(self.wallet(self.seller_user), 2_500_00)
        self.assertEqual(self.s.payouts(self.shop)[0]["paid_by"], "auto")
        self.s.set_auto_payout(self.shop, False)
        self.assertEqual(self.s.seller(self.shop)["auto_payout"], 0)


# ---------------------------------------------------------------- photos, quiz, model, privacy, login

class TestSmarterChecks(Base):
    def test_face_detection_on_courier_photos(self):
        import photocheck
        from PIL import Image
        if photocheck.face_detector() is None:
            self.skipTest("OpenCV is not installed")
        here = os.path.dirname(os.path.abspath(__file__))
        with Image.open(os.path.join(here, "fixtures", "face.jpg")) as img:        # NASA photo (public domain)
            r = photocheck.analyse(img, "courier")
        self.assertEqual(r["details"]["faces"], 1)
        self.assertFalse(any("face" in p for p in r["problems"]))
        blank = Image.new("RGB", (400, 400), (120, 160, 200))
        r = photocheck.analyse(blank, "courier")
        self.assertEqual(r["details"]["faces"], 0)
        self.assertTrue(any("No face" in p for p in r["problems"]))
        self.assertNotIn("faces", photocheck.analyse(blank, "product")["details"])   # products: no face check

    def test_quiz_in_seven_languages(self):
        from quiz_world import BANKS, ensure_quiz_languages
        added = ensure_quiz_languages(self.s)
        self.assertEqual(added, 12 * len(BANKS))
        self.assertEqual(ensure_quiz_languages(self.s), 0)
        for lang in ("fr", "es", "ar", "zh", "rw"):
            self.assertEqual(self.s.quiz_language(lang), lang)
            self.assertEqual(len(self.s.todays_questions(lang, "2026-11-06")), 5)

    def test_recommendation_model_learns_and_is_checked(self):
        import learn
        import numpy as np
        rng = np.random.default_rng(1)                           # two groups of customers with different tastes
        m = np.zeros((60, 20))
        for u in range(60):
            group = range(0, 10) if u < 30 else range(10, 20)
            for i in rng.choice(list(group), 5, replace=False):
                m[u, i] = 3
        model, popular, tested = learn.hit_rate(m)
        self.assertEqual(tested, 60)
        self.assertGreater(model, popular)                       # the model finds the hidden product more often
        report = self.s.learn_recommendations()
        self.assertFalse(report["used"])                         # a new shop: not enough data, the rules stay
        self.assertIn("Not enough data", report["reason"])

    def test_export_and_delete_my_data(self):
        self.s.add_address(self.uid, {"country": "IN", "name": "Test User", "mobile": "9811111111",
                                      "line1": "House 5, Mall Road", "city": "Ambala Cantt", "state": "Haryana",
                                      "pincode": "133001"})
        data = self.s.export_my_data(self.uid)
        self.assertEqual(data["account"]["email"], "test.user@realmail.local")
        self.assertNotIn("password", data["account"])
        self.assertEqual(len(data["tables"]["addresses"]), 1)
        with self.assertRaisesRegex(ShopError, "balance"):
            self.s.delete_my_account(self.uid, "Secret123")
        with self.s.db() as conn:
            self.s.wallet_move(conn, self.uid, -self.wallet(self.uid), "admin", "TEST", "Spent")
        with self.assertRaisesRegex(ShopError, "password"):
            self.s.delete_my_account(self.uid, "wrong")
        self.s.delete_my_account(self.uid, "Secret123")
        u = self.s.get_user(self.uid)
        self.assertEqual((u["name"], u["blocked"]), ("Deleted user", 1))
        self.assertEqual(self.s.addresses(self.uid), [])
        with self.assertRaises(ShopError):
            self.s.login("test.user@realmail.local", "Secret123")

    def test_too_many_wrong_passwords(self):
        for _ in range(5):
            self.s.login_allowed("someone@realmail.local", "10.0.0.1")
            self.s.login_failed("someone@realmail.local", "10.0.0.1")
        with self.assertRaisesRegex(ShopError, "Too many wrong passwords"):
            self.s.login_allowed("someone@realmail.local", "10.0.0.2")
        self.s.login_allowed("other@realmail.local", "10.0.0.2")             # other accounts are not blocked
        self.s.login_ok("someone@realmail.local")
        self.s.login_allowed("someone@realmail.local", "10.0.0.2")


# ---------------------------------------------------------------- pages

class TestPages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = support.make_app()

    def setUp(self):
        self.c = self.app.test_client()

    def test_place_search_api_and_page(self):
        r = self.c.get("/places/search?q=Mugunga&cc=RW").get_json()
        self.assertTrue(any(p["district"] == "Gakenke" for p in r["places"]))
        self.assertEqual(self.c.get("/places/search?q=Красноярск").get_json()["places"][0]["cc"], "RU")
        html = self.c.get("/places?q=133207").get_data(as_text=True)
        self.assertIn("Mullana", html)
        self.assertEqual(self.c.get("/places/near?lat=-1.69&lng=29.78").get_json()["place"]["cc"], "RW")

    def test_ticket_connection_results(self):
        html = self.c.get("/tickets/search?mode=flight&from=Krasnoyarsk&to=Kigali&date="
                          + (datetime.now() + timedelta(days=4)).strftime("%Y-%m-%d")).get_data(as_text=True)
        self.assertIn("tk-conn", html)
        self.assertIn("then=", html)

    def test_demo_content_of_version_5(self):
        support.login(self.c, "demo@example.com", "Demo@1234")
        html = self.c.get("/messages").get_data(as_text=True)
        self.assertIn("HomeEase", html)
        self.assertEqual(self.c.get("/account/data/download").status_code, 200)
        page = self.c.get("/p/homeease-cooker", headers={"Cookie": "lang=ru"})
        self.assertEqual(page.status_code, 200)
        with self.app.store.db() as conn:
            self.assertGreaterEqual(conn.execute("SELECT COUNT(*) FROM quiz_questions WHERE lang = 'rw'").fetchone()[0], 12)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM chat_messages WHERE status = 'Held'").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
