"""Tests for version 3, the Yo super app: Yo Courier (prices, routing, courier jobs, proof of delivery,
network, demo couriers, live map, privacy), Yo Pay (ledger, top-up, transfers, EMI), Yo Rewards
(daily quiz, points, personal coupons), Blog & News (safe Markdown, review of seller stories, SEO files),
task boards, automatic photo checks, the new pages in 7 languages and the one-time demo content."""

import io
import json
import os
import re
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import support  # noqa: E402
import blog  # noqa: E402
import courier as C  # noqa: E402
import demo_v3  # noqa: E402
import photocheck  # noqa: E402
import places  # noqa: E402
import yopay  # noqa: E402
from core import ShopError  # noqa: E402
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter  # noqa: E402

T0 = datetime(2026, 11, 6, 10, 0, 0)


def at(seconds):
    return T0 + timedelta(seconds=seconds)


def when(seconds=0):
    return at(seconds).strftime("%Y-%m-%d %H:%M:%S")


SENDER = {"from_name": "Test User", "from_mobile": "9811111111", "from_address": "12 Main Road, Sector 9",
          "from_city": "Ambala Cantt", "payment": "wallet", "service": "standard", "to_country": "IN",
          "kind": "parcel", "weight": "1", "contents": "A phone charger", "to_name": "Neha Verma",
          "to_mobile": "9812000003", "to_address": "House 5, Model Town", "to_city": "Ambala Cantt"}


class Base(unittest.TestCase):
    def setUp(self):
        self.note = support.fake_notifier(email=False, sms=False)
        self.s = support.make_store(seed=False, notifier=self.note)
        self.s.load_catalog()
        self.uid = self.s.register("Test User", "test.user@realmail.local", "9811111111", "Secret123")
        self.n = 0

    def courier(self, city="Ambala Cantt", vehicle="motorbike", online=True, demo=False, pos=None, name="Rider"):
        self.n += 1
        uid = self.s.register(f"{name} {self.n}", f"rider{self.n}@realmail.local", f"98222000{self.n:02d}", "Secret123",
                              wallet=0)
        cid = self.s.apply_courier(uid, {"vehicle": vehicle, "city": city, "vehicle_no": "HR01AB1234", "agree": "1"})
        self.s.decide_courier(cid, "Approved")
        lat, lng = pos or places.position(city)
        with self.s.db() as conn:
            conn.execute("UPDATE couriers SET demo = ?, lat = ?, lng = ? WHERE id = ?", (int(demo), lat, lng, cid))
        if online:
            self.s.set_online(cid, True, when=when())
        return cid, uid

    def book(self, **kw):
        return self.s.book_shipment(self.uid, dict(SENDER, **kw), when=when())

    def row(self, number):
        with self.s.db() as conn:
            return dict(conn.execute("SELECT * FROM shipments WHERE number = ?", (number,)).fetchone())

    def wallet(self, uid):
        return self.s.get_user(uid)["wallet"]


# =====================================================================================
class TestCourierPrices(unittest.TestCase):
    def q(self, **kw):
        base = dict(kind="parcel", weight_kg="1", from_city="Ambala Cantt", to_city="Ambala Cantt", to_country="IN")
        base.update(kw)
        return C.quote(**base)

    def test_zones_and_distance(self):
        self.assertEqual(self.q()["zone"], "local")
        self.assertEqual(self.q()["distance_km"], 0)
        self.assertEqual(self.q(to_city="Chandigarh")["zone"], "regional")
        self.assertEqual(self.q(to_city="Bengaluru")["zone"], "national")
        self.assertEqual(self.q(to_country="AE", to_city="Dubai")["zone"], "near")
        self.assertEqual(self.q(to_country="RU", to_city="Krasnoyarsk")["zone"], "world")
        # haversine x road factor: Ambala Cantt - Chandigarh is about 45 km in a straight line
        km = places.haversine_km(places.position("Ambala Cantt"), places.position("Chandigarh"))
        self.assertTrue(40 < km < 50, km)
        self.assertEqual(self.q(to_city="Chandigarh")["distance_km"], round(km * places.ROAD_FACTOR, 1))

    def test_volume_weight_and_price_lines(self):
        q = self.q(weight_kg="3", length="50", width="40", height="30")       # 60,000 cm3 / 5000 = 12 kg
        self.assertEqual(q["volume_kg"], 12)
        self.assertEqual(q["charge_g"], 12000)
        self.assertEqual(q["lines"][0], ("Up to 0.5 kg", 4900))
        self.assertEqual(q["lines"][1][1], 23 * 1000)                          # 23 more half-kilos at ₹10
        self.assertEqual(q["tax"], round(q["subtotal"] * 18 / 100 / 100) * 100)
        self.assertEqual(q["price"], q["subtotal"] + q["tax"])

    def test_documents_express_fragile_insurance(self):
        doc = self.q(kind="document", weight_kg="0.3")
        self.assertEqual(doc["subtotal"], 4900 - 1000)                         # -20% rounded to whole rupees
        exp = self.q(service="express")
        self.assertEqual(exp["subtotal"], 4900 + 1000 + round((5900 * 0.6) / 100) * 100)
        ins = self.q(fragile=True, insured=True, declared="5000")
        self.assertIn(("Fragile handling", 4000), ins["lines"])
        self.assertIn(("Insurance", 10000), ins["lines"])                     # 2% of ₹5,000
        with self.assertRaises(ShopError):
            self.q(insured=True, declared="0")
        abroad = self.q(to_country="RW", to_city="Kigali")
        self.assertEqual(abroad["tax"], 0)                                     # no Indian GST on export
        self.assertEqual(abroad["price"], 180000 + 50000)                     # 1 kg = 2 half-kilos, "world" zone

    def test_impossible_parcels(self):
        for kw in ({"kind": "grocery", "to_country": "FR", "to_city": "Lyon"}, {"kind": "grocery", "to_city": "Mumbai"},
                   {"kind": "document", "weight_kg": "3"}, {"weight_kg": "0"}, {"weight_kg": "51"},
                   {"weight_kg": "abc"}, {"from_city": "Atlantis"}, {"to_city": "Nowhere"}, {"kind": "pizza"},
                   {"length": "200", "width": "1", "height": "1"}, {"to_country": "XX"}, {"service": "rocket"},
                   {"to_country": "RU", "weight_kg": "25"}):
            with self.assertRaises(ShopError, msg=kw):
                self.q(**kw)

    def test_places_helpers(self):
        self.assertEqual(places.city("  ambala   CANTT "), "Ambala Cantt")
        a = places.near_centre("Mullana", "House 1")
        self.assertEqual(a, places.near_centre("Mullana", "house   1"))          # same text -> same point
        self.assertLess(places.haversine_km(a, places.position("Mullana")), 4.1)
        self.assertTrue(places.point_ok("Mullana", 30.28, 77.05))
        self.assertFalse(places.point_ok("Mullana", 28.6, 77.2))                # Delhi is not near Mullana
        self.assertFalse(places.point_ok("Mullana", "x", 1))
        mid = places.along([(30.0, 76.0), (30.0, 77.0)], 0.5)
        self.assertAlmostEqual(mid[1], 76.5, places=2)


# =====================================================================================
class TestBooking(Base):
    def test_wallet_booking_ledger_and_cancel(self):
        before = self.wallet(self.uid)
        number = self.book()
        r = self.row(number)
        self.assertTrue(number.startswith("YC") and len(number) == 14)
        self.assertEqual(r["status"], C.BOOKED)                  # no courier online yet
        self.assertEqual(self.wallet(self.uid), before - r["price"])
        self.assertTrue(r["paid"])
        self.assertRegex(r["delivery_code"], r"^\d{4}$")
        self.s.cancel_shipment(self.uid, number)
        self.assertEqual(self.wallet(self.uid), before)
        self.assertEqual(self.row(number)["status"], C.CANCELLED)
        with self.assertRaises(ShopError):
            self.s.cancel_shipment(self.uid, number)
        self.assertEqual(self.s.wallet_audit(), [])
        kinds = [t["kind"] for t in self.s.wallet_statement(self.uid)]
        self.assertEqual(kinds[:2], ["refund", "courier"])

    def test_not_enough_money_books_nothing(self):
        with self.s.db() as conn:
            conn.execute("UPDATE users SET wallet = 100 WHERE id = ?", (self.uid,))
        with self.assertRaises(ShopError):
            self.book()
        self.assertEqual(self.s.my_shipments(self.uid), [])

    def test_validation_of_people_and_payment(self):
        for kw in ({"to_mobile": "123"}, {"from_name": "A"}, {"to_address": "x"}, {"contents": ""},
                   {"payment": "bitcoin"}, {"to_country": "RW", "to_city": "Kigali", "to_mobile": "+250788555014",
                                            "payment": "cash"}, {"to_country": "RU", "to_city": "Moscow",
                                                                 "to_mobile": "+79161234567", "to_postcode": "12"}):
            with self.assertRaises(ShopError, msg=kw):
                self.book(**kw)

    def test_cash_and_my_location(self):
        number = self.book(payment="cash", from_lat="30.3401", from_lng="76.8455")
        r = self.row(number)
        self.assertFalse(r["paid"])
        self.assertEqual((r["from_lat"], r["from_lng"]), (30.3401, 76.8455))
        far = self.book(payment="cash", from_lat="28.61", from_lng="77.20")       # Delhi: ignored for Ambala
        self.assertNotEqual(self.row(far)["from_lat"], 28.61)


# =====================================================================================
class TestRoutingAndJobs(Base):
    def test_nearest_free_courier_with_capacity_gets_the_job(self):
        far, _ = self.courier(pos=(30.40, 76.85))
        near, _ = self.courier(pos=(30.336, 76.848))
        bike, _ = self.courier(vehicle="bicycle", pos=(30.3361, 76.8481))
        heavy = self.book(weight="8")                       # too heavy for the bicycle (5 kg)
        self.assertEqual(self.row(heavy)["courier_id"], near)
        self.assertEqual(self.row(heavy)["status"], C.ASSIGNED)
        light = self.book(weight="1")                       # the bicycle is nearest and has no job
        self.assertEqual(self.row(light)["courier_id"], bike)
        self.assertNotEqual(self.row(self.book())["courier_id"], None)
        self.assertIsNotNone(far)

    def test_real_couriers_before_robots_offline_and_stale_skipped(self):
        robot, _ = self.courier(demo=True, pos=(30.336, 76.848))
        real, _ = self.courier(pos=(30.38, 76.84))
        self.assertEqual(self.row(self.book())["courier_id"], real)
        with self.s.db() as conn:                           # a real courier not seen for an hour is not used
            conn.execute("UPDATE couriers SET last_seen = ? WHERE id = ?", (when(-3600), real))
        self.assertEqual(self.row(self.book())["courier_id"], robot)
        self.s.set_online(robot, False)
        self.assertIsNone(self.row(self.book())["courier_id"])

    def test_max_jobs_and_job_taken_only_once(self):
        c1, _ = self.courier()
        numbers = [self.book() for _ in range(C.MAX_JOBS + 1)]
        self.assertEqual([self.row(n)["courier_id"] for n in numbers[:C.MAX_JOBS]], [c1] * C.MAX_JOBS)
        last = numbers[-1]
        self.assertIsNone(self.row(last)["courier_id"])
        with self.assertRaises(ShopError):
            self.s.take_job(c1, last)                       # already MAX_JOBS jobs
        c2, _ = self.courier(online=False)
        c3, _ = self.courier(online=False)
        self.s.take_job(c2, last)
        with self.assertRaises(ShopError):
            self.s.take_job(c3, last)                       # the second courier is too late
        self.assertEqual(self.row(last)["courier_id"], c2)
        other, _ = self.courier(city="Mullana", online=False)
        n2 = self.book()
        with self.assertRaises(ShopError):
            self.s.take_job(other, n2)

    def test_local_delivery_with_code_cash_and_earning(self):
        cid, cuid = self.courier()
        number = self.book(payment="cash")
        r = self.row(number)
        with self.assertRaises(ShopError):
            self.s.job_pickup(cid, number)                  # must confirm the cash first
        self.s.job_pickup(cid, number, cash_collected=True, when=when(60))
        self.assertEqual(self.row(number)["status"], C.OUT)
        self.assertEqual(self.s.courier_of(cuid)["cash_in_hand"], r["price"])
        code = r["delivery_code"]
        wrong = "0000" if code != "0000" else "1111"
        with self.assertRaises(C.WrongCode):
            self.s.job_deliver(cid, number, wrong, "Neha")
        self.assertEqual(self.row(number)["code_tries"], 1)             # the wrong try was saved
        self.s.job_deliver(cid, number, code, "Neha", when=when(300))
        r = self.row(number)
        self.assertEqual((r["status"], r["pod_name"], r["pod_by"]), (C.DELIVERED, "Neha", "courier"))
        self.assertEqual(self.wallet(cuid), r["subtotal"] * 70 // 100)
        self.assertEqual(self.s.courier_of(cuid)["jobs_done"], 1)
        subjects = [n["subject"] for n in self.s.notifications(self.uid)]
        self.assertTrue(any(s.startswith("Delivered:") for s in subjects))
        self.assertEqual(self.s.wallet_audit(), [])
        with self.assertRaises(ShopError):
            self.s.job_deliver(cid, number, code, "Neha")

    def test_five_wrong_codes_lock_the_job(self):
        cid, _ = self.courier()
        number = self.book()
        self.s.job_pickup(cid, number)
        code = self.row(number)["delivery_code"]
        wrong = str((int(code) + 1) % 10000).zfill(4)
        for _ in range(C.CODE_TRIES):
            with self.assertRaises(ShopError):
                self.s.job_deliver(cid, number, wrong, "X")
        with self.assertRaises(ShopError):
            self.s.job_deliver(cid, number, code, "Neha")       # even the right code is refused now
        self.assertEqual(self.row(number)["status"], C.OUT)

    def test_network_shipment_two_couriers_and_hub(self):
        pick, puid = self.courier()
        drop, duid = self.courier(city="Chandigarh")
        number = self.book(to_city="Chandigarh")
        self.assertEqual(self.row(number)["leg"], "pickup")
        self.s.job_pickup(pick, number, when=when(10))
        self.assertEqual(self.row(number)["status"], C.PICKED)
        with self.assertRaises(ShopError):
            self.s.job_deliver(pick, number, "1234", "X")
        self.s.job_handover(pick, number, when=when(20))
        r = self.row(number)
        self.assertEqual((r["status"], r["leg"], r["courier_id"]), (C.TRANSIT, "delivery", None))
        self.s.courier_tick(at=at(20 + C.BOT["network"] - 5))
        self.assertEqual(self.row(number)["status"], C.TRANSIT)
        self.s.courier_tick(at=at(20 + C.BOT["network"] + 1))
        r = self.row(number)
        self.assertEqual((r["status"], r["courier_id"]), (C.OUT, drop))     # the courier in Chandigarh got it
        self.s.job_deliver(drop, number, r["delivery_code"], "Arjun")
        self.assertEqual(self.wallet(puid), r["subtotal"] * 25 // 100)
        self.assertEqual(self.wallet(duid), r["subtotal"] * 25 // 100)
        names = [e["status"] for e in self.s.get_shipment(number, self.uid)["events"]]
        self.assertEqual(names, [C.BOOKED, C.ASSIGNED, C.PICKED, C.TRANSIT, C.AT_HUB, C.OUT, C.DELIVERED])

    def test_international_goes_through_partner(self):
        pick, _ = self.courier()
        with self.s.db() as conn:
            conn.execute("UPDATE users SET wallet = 1000000 WHERE id = ?", (self.uid,))
        number = self.book(to_country="RW", to_city="Kigali", to_mobile="+250788555014", declared="1500")
        self.s.job_pickup(pick, number, when=when(10))
        self.s.job_handover(pick, number, when=when(20))
        t = 20
        for _ in range(12):
            t += 60
            self.s.courier_tick(at=at(t))
        r = self.row(number)
        self.assertEqual((r["status"], r["pod_by"]), (C.DELIVERED, "partner"))

    def test_failed_attempts_then_return(self):
        cid, _ = self.courier()
        number = self.book()
        self.s.job_pickup(cid, number, when=when(10))
        with self.assertRaises(ShopError):
            self.s.job_fail(cid, number, "Aliens")
        self.s.job_fail(cid, number, "Receiver not available", when=when(20))
        r = self.row(number)
        self.assertEqual((r["status"], r["attempts"], r["courier_id"]), (C.AT_HUB, 1, None))
        self.s.courier_tick(at=at(30))                                   # still waiting before the retry time
        self.assertIsNone(self.row(number)["courier_id"])
        self.s.courier_tick(at=at(20 + C.BOT["retry"] + 1))
        r = self.row(number)
        self.assertEqual((r["status"], r["courier_id"]), (C.OUT, cid))
        self.s.job_fail(cid, number, "Receiver not available", when=when(200))
        self.assertEqual(self.row(number)["status"], C.RETURNED)

    def test_slow_courier_loses_the_job(self):
        slow, suid = self.courier(pos=(30.336, 76.848))
        number = self.book()
        self.assertEqual(self.row(number)["courier_id"], slow)
        other, _ = self.courier(pos=(30.37, 76.85))
        with self.s.db() as conn:                           # the other courier is still "seen"
            conn.execute("UPDATE couriers SET last_seen = ? WHERE id = ?", (when(C.BOT["slow_courier"]), other))
        self.s.courier_tick(at=at(C.BOT["slow_courier"] + 5))
        self.assertEqual(self.row(number)["courier_id"], other)
        self.assertFalse(self.s.courier_of(suid)["online"])

    def test_demo_robot_drives_and_delivers(self):
        robot, ruid = self.courier(demo=True)
        number = self.book(to_address="Far away street 99")
        p1 = self.s.live(number, self.uid, at=at(30))["courier"]
        p2 = self.s.live(number, self.uid, at=at(60))["courier"]
        self.assertNotEqual((p1["lat"], p1["lng"]), (p2["lat"], p2["lng"]))   # moving on the map
        t = 0
        while self.row(number)["status"] != C.DELIVERED and t < 1000:
            t += 30
            self.s.courier_tick(at=at(t))
        self.assertEqual(self.row(number)["status"], C.DELIVERED)
        self.assertLess(t, 400)
        self.assertGreater(self.wallet(ruid), 0)
        # a real courier is never moved by the tick
        real, real_uid = self.courier(pos=(30.30, 76.80))
        self.s.set_online(robot, False)
        n2 = self.book()
        self.assertEqual(self.row(n2)["courier_id"], real)
        self.s.courier_tick(at=at(200))
        self.assertEqual(self.row(n2)["status"], C.ASSIGNED)
        c = self.s.courier_of(real_uid)
        self.assertEqual((c["lat"], c["lng"]), (30.30, 76.80))

    def test_live_map_privacy(self):
        cid, _ = self.courier()
        number = self.book()
        self.s.courier_ping(cid, 30.3371, 76.8492, when=when(5))
        self.s.courier_ping(cid, 30.3391, 76.8512, when=when(15))
        mine = self.s.live(number, self.uid, at=at(20))
        stranger = self.s.live(number, None, at=at(20))
        r = self.row(number)
        self.assertEqual((mine["pickup"]["lat"], mine["pickup"]["lng"]), (r["from_lat"], r["from_lng"]))
        self.assertEqual((stranger["pickup"]["lat"], stranger["pickup"]["lng"]), places.position("Ambala Cantt"))
        self.assertEqual(len(mine["trail"]), 2)
        self.assertEqual(stranger["trail"], [])
        public = self.s.get_shipment(number, public=True)
        self.assertEqual((public["delivery_code"], public["to_address"], public["from_mobile"]), ("", "", ""))
        with self.assertRaises(ShopError):
            self.s.get_shipment(number, user_id=9999)
        with self.assertRaises(ShopError):
            self.s.live("YC000")
        with self.assertRaises(ShopError):
            self.s.courier_ping(cid, 123, 500)

    def test_rating_once_after_delivery(self):
        cid, cuid = self.courier()
        number = self.book()
        with self.assertRaises(ShopError):
            self.s.rate_shipment(self.uid, number, 5)
        self.s.job_pickup(cid, number)
        self.s.job_deliver(cid, number, self.row(number)["delivery_code"], "Neha")
        self.s.rate_shipment(self.uid, number, 4, "Good")
        with self.assertRaises(ShopError):
            self.s.rate_shipment(self.uid, number, 5)
        self.assertEqual(self.s.courier_of(cuid)["rating"], 4.0)

    def test_courier_application_rules(self):
        uid = self.s.register("New Rider", "new.rider@realmail.local", "9822299999", "Secret123")
        with self.assertRaises(ShopError):
            self.s.apply_courier(uid, {"vehicle": "car", "city": "Ambala Cantt", "vehicle_no": "", "agree": "1"})
        with self.assertRaises(ShopError):
            self.s.apply_courier(uid, {"vehicle": "bicycle", "city": "Qwzxville", "agree": "1"})   # no such place
        with self.assertRaises(ShopError):
            self.s.apply_courier(uid, {"vehicle": "bicycle", "city": "Mullana"})
        cid = self.s.apply_courier(uid, {"vehicle": "bicycle", "city": "Mullana", "agree": "1"})
        with self.assertRaises(ShopError):
            self.s.set_online(cid, True)                    # not approved yet
        with self.assertRaises(ShopError):
            self.s.apply_courier(uid, {"vehicle": "bicycle", "city": "Mullana", "agree": "1"})
        self.s.decide_courier(cid, "Suspended", "Test")
        self.assertEqual(self.s.courier_of(uid)["status"], "Suspended")


# =====================================================================================
class TestYoPay(Base):
    def test_ledger_never_negative_and_always_adds_up(self):
        bal = self.wallet(self.uid)
        with self.s.db() as conn:
            self.s.wallet_move(conn, self.uid, -bal, "order", "X")
        with self.assertRaises(ShopError):
            with self.s.db() as conn:
                self.s.wallet_move(conn, self.uid, -1, "order", "Y")
        self.assertEqual(self.wallet(self.uid), 0)
        with self.assertRaises(ShopError):
            with self.s.db() as conn:
                self.s.wallet_move(conn, self.uid, 0, "order")
        self.assertEqual(self.s.wallet_audit(), [])
        last = self.s.wallet_statement(self.uid)[0]
        self.assertEqual((last["amount"], last["balance"]), (-bal, 0))

    def test_older_wallets_get_an_opening_line(self):
        with self.s.db() as conn:
            conn.execute("DELETE FROM wallet_txns WHERE user_id = ?", (self.uid,))
        self.assertTrue(self.s.wallet_audit())
        self.s.open_wallet_ledgers()
        self.s.open_wallet_ledgers()                        # only once
        self.assertEqual(self.s.wallet_audit(), [])
        self.assertEqual([t["kind"] for t in self.s.wallet_statement(self.uid)], ["opening"])

    def test_topup_with_test_cards(self):
        before = self.wallet(self.uid)
        ok, msg, ref = self.s.topup_with_test_card(self.uid, "1,500", "4242 4242 4242 4242", "12/40", "123", "Test")
        self.assertTrue(ok)
        self.assertEqual(self.wallet(self.uid), before + 150000)
        ok, msg, _ = self.s.topup_with_test_card(self.uid, "500", "4000000000000002", "12/40", "123", "Test")
        self.assertFalse(ok)
        self.assertIn("declined", msg)
        self.assertEqual(self.wallet(self.uid), before + 150000)
        for args in (("500", "4242424242424241", "12/40", "123", "T"), ("500", "4242424242424242", "01/20", "123", "T"),
                     ("50", "4242424242424242", "12/40", "123", "T"), ("60000", "4242424242424242", "12/40", "123", "T"),
                     ("abc", "4242424242424242", "12/40", "123", "T"), ("500", "4242424242424242", "12/40", "1", "T")):
            with self.assertRaises(ShopError, msg=args):
                self.s.topup_with_test_card(self.uid, *args)
        with self.s.db() as conn:
            conn.execute("UPDATE users SET wallet = ? WHERE id = ?", (yopay.MAX_BALANCE - 100, self.uid))
        with self.assertRaises(ShopError):
            self.s.topup_with_test_card(self.uid, "500", "4242424242424242", "12/40", "123", "Test")

    def test_send_money(self):
        friend = self.s.register("Neha Verma", "neha.v@realmail.local", "9833300000", "Secret123", wallet=0)
        before = self.wallet(self.uid)
        with self.assertRaises(ShopError):
            self.s.send_money(self.uid, "neha.v@realmail.local", "100", "", "wrong-password")
        ref, payee = self.s.send_money(self.uid, " 98333 00000 ", "250.50", "Tea", "Secret123")
        self.assertEqual(payee["id"], friend)
        self.assertEqual(self.wallet(friend), 25050)
        self.assertEqual(self.wallet(self.uid), before - 25050)
        self.assertIn("You received", self.s.notifications(friend)[0]["subject"])
        for to, amount in (("test.user@realmail.local", "10"), ("nobody@realmail.local", "10"),
                           ("neha.v@realmail.local", "0"), ("neha.v@realmail.local", "30000")):
            with self.assertRaises(ShopError):
                self.s.send_money(self.uid, to, amount, "", "Secret123")
        with self.s.db() as conn:
            conn.execute("UPDATE users SET wallet = 20000000 WHERE id = ?", (self.uid,))
        self.s.send_money(self.uid, "neha.v@realmail.local", "25000", "", "Secret123")
        self.s.send_money(self.uid, "neha.v@realmail.local", "24000", "", "Secret123")
        with self.assertRaises(ShopError):                  # ₹50,000 a day
            self.s.send_money(self.uid, "neha.v@realmail.local", "1000", "", "Secret123")

    def test_emi_formula_and_plans(self):
        p = 60000_00
        r = 16 / 12 / 100
        expected = p * r * (1 + r) ** 12 / ((1 + r) ** 12 - 1)
        self.assertEqual(yopay.emi_monthly(p, 16, 12), round(expected))
        self.assertTrue(5443_00 < yopay.emi_monthly(p, 16, 12) < 5445_00)
        self.assertEqual(yopay.emi_monthly(1200, 0, 12), 100)
        self.assertEqual(yopay.emi_plans(2000_00), [])
        small = yopay.emi_plans(5000_00)
        self.assertFalse(any(x["no_cost"] for x in small))
        big = {x["months"]: x for x in yopay.emi_plans(12000_00)}
        self.assertTrue(big[3]["no_cost"] and big[6]["no_cost"] and not big[12]["no_cost"])
        self.assertEqual(big[6]["total"], 12000_00)
        self.assertEqual(big[6]["interest"], 0)
        self.assertGreater(big[24]["interest"], big[12]["interest"])

    def test_order_with_emi_and_coupon_rules(self):
        addr = self.s.add_address(self.uid, {"name": "Test User", "mobile": "9811111111", "line1": "12 Main Road",
                                             "city": "Ambala", "state": "Haryana", "pincode": "133001", "kind": "Home"})
        laptop = self.s.get_product(slug="zentra-book-14")
        self.s.save_cart(self.uid, {laptop.id: 1})
        with self.assertRaises(ShopError):                   # EMI only with a card
            self.s.place_order(self.uid, addr, "cod", emi_months=6)
        number = self.s.place_order(self.uid, addr, "card", emi_months=6)
        o = self.s.get_order(self.uid, number)
        self.assertEqual(o["emi_months"], 6)
        plan = next(x for x in yopay.emi_plans(o["total"]) if x["months"] == 6)
        self.assertEqual(o["emi_monthly"], plan["monthly"])
        self.s.save_cart(self.uid, {laptop.id: 1})
        with self.assertRaises(ShopError):
            self.s.place_order(self.uid, addr, "card", emi_months=7)


# =====================================================================================
class TestRewards(Base):
    def setUp(self):
        super().setUp()
        demo_v3._quiz_bank(self.s)
        self.other = self.s.register("Second Player", "second@realmail.local", "9844400000", "Secret123")

    def play(self, uid, right=5, day="2026-11-06", minutes=2):
        attempt, qs = self.s.start_quiz(uid, "en", day=day, when=f"{day} 09:00:00")
        full = {q["id"]: q for q in self.s._questions(json.loads(attempt["question_ids"]), with_answers=True)}
        answers = {str(q["id"]): (full[q["id"]]["answer"] if i < right else (full[q["id"]]["answer"] + 1) % 4)
                   for i, q in enumerate(qs)}
        return self.s.finish_quiz(uid, answers, day=day, when=f"{day} 09:{minutes:02d}:00")

    def test_same_questions_for_everyone_and_no_answers_sent(self):
        a1, q1 = self.s.start_quiz(self.uid, "en", day="2026-11-06")
        a2, q2 = self.s.start_quiz(self.other, "en", day="2026-11-06")
        self.assertEqual([q["id"] for q in q1], [q["id"] for q in q2])
        self.assertEqual(len(q1), 5)
        self.assertNotIn("answer", q1[0])
        _, q3 = self.s.start_quiz(self.other, "en", day="2026-11-07")
        self.assertNotEqual([q["id"] for q in q1], [q["id"] for q in q3])
        _, ru = self.s.start_quiz(self.uid, "ru", day="2026-11-08")
        self.assertTrue(all(re.search("[а-яА-Я]", q["question"]) for q in ru))
        a, _ = self.s.start_quiz(self.uid, "fr", day="2026-11-09")            # no French bank: English
        self.assertEqual(a["lang"], "en")

    def test_points_bonus_streak_and_one_try(self):
        r = self.play(self.uid, 5)
        self.assertEqual((r["correct"], r["points"], r["streak"]), (5, 70, 1))
        with self.assertRaises(ShopError):
            self.play(self.uid, 5)
        r2 = self.play(self.uid, 3, day="2026-11-07")
        self.assertEqual((r2["streak"], r2["points"]), (2, 30 + 5))
        r3 = self.play(self.uid, 2, day="2026-11-08")
        self.assertEqual((r3["streak"], r3["points"]), (0, 20))
        late = self.play(self.other, 5, minutes=30)
        self.assertEqual((late["correct"], late["points"]), (0, 0))
        self.assertEqual(self.s.points(self.uid), 70 + 35 + 20)
        board = self.s.leaderboard(days=3650)
        self.assertEqual(board[0]["name"], "Test U.")

    def test_coupons_and_wallet_rewards(self):
        with self.assertRaises(ShopError):
            self.s.redeem(self.uid, "c50")
        with self.s.db() as conn:
            self.s.add_points(conn, self.uid, 500, "Test")
        got = self.s.redeem(self.uid, "c50")
        code = got["code"]
        self.assertEqual(self.s.points(self.uid), 400)
        before = self.wallet(self.uid)
        self.s.redeem(self.uid, "w50")
        self.assertEqual(self.wallet(self.uid), before + 5000)
        self.assertEqual(self.s.points(self.uid), 200)
        with self.assertRaises(ShopError):
            self.s.redeem(self.uid, "c400")
        with self.assertRaises(ShopError):
            self.s.coupon_value(code, 100000, self.other)                     # someone else's coupon
        with self.assertRaises(ShopError):
            self.s.coupon_value(code, 30000, self.uid)                        # below ₹499
        self.assertEqual(self.s.coupon_value(code, 60000, self.uid), 5000)
        addr = self.s.add_address(self.uid, {"name": "Test User", "mobile": "9811111111", "line1": "12 Main Road",
                                             "city": "Ambala", "state": "Haryana", "pincode": "133001", "kind": "Home"})
        shoes = self.s.get_product(slug="stridex-runner-teal")
        self.s.save_cart(self.uid, {shoes.id: 1})
        number = self.s.place_order(self.uid, addr, "cod", coupon=code)
        self.assertEqual(self.s.get_order(self.uid, number)["coupon_discount"], 5000)
        self.s.save_cart(self.uid, {shoes.id: 1})
        with self.assertRaises(ShopError):
            self.s.place_order(self.uid, addr, "cod", coupon=code)
        self.s.cancel_order(self.uid, number)                                 # the coupon comes back
        self.assertEqual(self.s.coupon_value(code, 60000, self.uid), 5000)
        with self.s.db() as conn:
            conn.execute("UPDATE user_coupons SET expires_at = '2020-01-01 00:00:00' WHERE code = ?", (code,))
        with self.assertRaises(ShopError):
            self.s.coupon_value(code, 60000, self.uid)
        self.assertEqual([c["state"] for c in self.s.my_coupons(self.uid)], ["Expired"])

    def test_points_for_reviews_and_delivered_orders_once(self):
        with self.s.db() as conn:
            self.assertTrue(self.s.add_points(conn, self.uid, 20, "Product review", "5", once=True))
            self.assertFalse(self.s.add_points(conn, self.uid, 20, "Product review", "5", once=True))
        self.assertEqual(self.s.points(self.uid), 20)

    def test_question_bank_admin(self):
        with self.assertRaises(ShopError):
            self.s.save_question({"question": "Short?", "option0": "a", "option1": "b", "option2": "c",
                                  "option3": "d", "answer": 1})
        with self.assertRaises(ShopError):
            self.s.save_question({"question": "What is two plus two?", "option0": "4", "option1": "4",
                                  "option2": "5", "option3": "6", "answer": 0})
        qid = self.s.save_question({"question": "What is two plus two?", "option0": "3", "option1": "4",
                                    "option2": "5", "option3": "6", "answer": "1", "lang": "en"})
        self.s.toggle_question(qid)
        self.assertFalse(next(q for q in self.s.quiz_questions("en") if q["id"] == qid)["active"])


# =====================================================================================
class TestBlog(Base):
    def test_markdown_is_safe(self):
        out = blog.render_markdown("## Hi <script>alert(1)</script>\n\n**bold** and [x](javascript:alert(1)) "
                                   "[ok](https://example.org) [in](/courier)\n\n- one\n- two\n\n> quote\n\n"
                                   "[[product:book-python]]", lambda s: f"<card {s}>")
        self.assertNotIn("<script>", out)
        self.assertIn("&lt;script&gt;", out)
        self.assertNotIn('href="javascript', out)
        self.assertIn('<a href="https://example.org" rel="nofollow noopener" target="_blank">ok</a>', out)
        self.assertIn('<a href="/courier">in</a>', out)
        self.assertIn("<ul><li>one</li><li>two</li></ul>", out)
        self.assertIn("<blockquote>quote</blockquote>", out)
        self.assertIn("<card book-python>", out)
        self.assertIn("<strong>bold</strong>", out)
        self.assertEqual(blog.reading_minutes("word " * 401), 3)

    def test_seller_story_review_and_publish(self):
        seller = self.s.register("Shop Owner", "owner@realmail.local", "9855500000", "Secret123")
        shop = self.s.apply_seller(seller, {"shop_name": "Test Shop", "about": "We sell good things for homes.",
                                            "country": "IN", "city": "Ambala", "payout_details": "owner@okyo"})
        body = "Our little shop story. " * 10
        with self.assertRaises(ShopError):
            self.s.save_post({"title": "Deals for you today", "body": body, "category": "deals"}, seller, "Shop",
                             seller_id=shop)
        slug = self.s.save_post({"title": "How we started our shop", "body": body, "category": "stories",
                                 "status": "Published"}, seller, "Shop Owner", seller_id=shop)
        post = self.s.get_post(slug, any_status=True)
        self.assertEqual(post["status"], "Review")                    # a seller cannot publish alone
        with self.assertRaises(ShopError):
            self.s.get_post(slug)
        self.s.set_post_status(post["id"], "Published")
        self.assertEqual(self.s.get_post(slug, count_view=True)["status"], "Published")
        self.assertEqual(self.s.get_post(slug)["views"], 1)
        self.s.save_post({"title": "How we started our shop!", "body": body, "category": "stories"}, seller,
                         "Shop Owner", post["id"], seller_id=shop)
        self.assertEqual(self.s.post_by_id(post["id"])["status"], "Review")   # a changed story is checked again
        with self.assertRaises(ShopError):
            self.s.delete_post(post["id"], seller_id=shop + 99)
        slug2 = self.s.save_post({"title": "How we started our shop", "body": body, "category": "news",
                                  "status": "Published"}, 1, "Admin")
        self.assertEqual(slug2, slug + "-2")                          # addresses stay unique


# =====================================================================================
class TestTaskBoard(Base):
    def test_add_move_edit_delete_and_isolation(self):
        a = self.s.add_task("seller:1", {"title": "First"})
        b = self.s.add_task("seller:1", {"title": "Second", "priority": "high", "due": "2020-01-01"})
        cols = self.s.board("seller:1")
        self.assertEqual([t["title"] for t in cols["todo"]], ["First", "Second"])
        self.assertTrue(cols["todo"][1]["late"])
        self.s.move_task("seller:1", b, "todo", before_id=a)
        self.assertEqual([t["id"] for t in self.s.board("seller:1")["todo"]], [b, a])
        self.s.move_task("seller:1", a, "done")
        self.assertEqual([t["id"] for t in self.s.board("seller:1")["done"]], [a])
        with self.assertRaises(ShopError):
            self.s.move_task("seller:2", a, "todo")                 # another board cannot touch it
        with self.assertRaises(ShopError):
            self.s.move_task("seller:1", a, "nowhere")
        with self.assertRaises(ShopError):
            self.s.add_task("seller:1", {"title": "x"})
        with self.assertRaises(ShopError):
            self.s.add_task("seller:1", {"title": "Bad date", "due": "31/12/2026"})
        self.s.edit_task("seller:1", b, {"title": "Second (changed)", "priority": "low"})
        self.assertEqual(self.s.board("seller:1")["todo"][0]["title"], "Second (changed)")
        self.s.delete_task("seller:1", b)
        self.assertEqual(self.s.board("seller:1")["todo"], [])

    def test_auto_tasks_from_orders_are_not_repeated(self):
        seller = self.s.register("Meera K", "meera@realmail.local", "9866600000", "Secret123")
        shop = self.s.apply_seller(seller, {"shop_name": "Kitchen Test", "about": "Kitchen tools for Indian homes.",
                                            "country": "IN", "city": "Ambala", "payout_details": "meera@okyo"})
        self.s.decide_seller(shop, "Approved")
        cooker = self.s.get_product(slug="homeease-cooker")
        with self.s.db() as conn:
            conn.execute("UPDATE products SET seller_id = ?, stock = 3 WHERE id = ?", (shop, cooker.id))
        addr = self.s.add_address(self.uid, {"name": "Test User", "mobile": "9811111111", "line1": "12 Main Road",
                                             "city": "Ambala", "state": "Haryana", "pincode": "133001", "kind": "Home"})
        for _ in range(2):
            self.s.save_cart(self.uid, {cooker.id: 1})
            self.s.place_order(self.uid, addr, "cod")
        titles = [t["title"] for t in self.s.board(f"seller:{shop}")["todo"]]
        self.assertEqual(sum(t.startswith("Pack order") for t in titles), 2)
        self.assertEqual(sum(t.startswith("Restock") for t in titles), 1)    # one open restock card only


# =====================================================================================
class TestPhotoCheck(Base):
    def picture(self, size=800, blur=0, dark=1.0):
        img = Image.new("RGB", (size, size), "white")
        d = ImageDraw.Draw(img)
        for i in range(0, size // 2 - 40, 40):
            d.rectangle([i, i, i + 30, size - i], outline=(30, 60, 200), width=3)
            d.text((i + 5, i + 5), "Yo", fill=(200, 40, 20))
        if blur:
            img = img.filter(ImageFilter.GaussianBlur(blur))
        if dark != 1.0:
            img = ImageEnhance.Brightness(img).enhance(dark)
        return img

    def test_sharp_blurry_dark_small(self):
        good = photocheck.analyse(self.picture())
        self.assertEqual(good["problems"], [])
        self.assertTrue(any("blurry" in p for p in photocheck.analyse(self.picture(blur=4))["problems"]))
        self.assertTrue(any("dark" in p for p in photocheck.analyse(self.picture(dark=0.15))["problems"]))
        self.assertTrue(any("small" in p for p in photocheck.analyse(self.picture(size=200))["problems"]))
        self.assertGreater(good["score"], photocheck.analyse(self.picture(blur=4))["score"])

    def test_same_photo_of_another_account_is_found(self):
        img = self.picture()
        h1, h2 = photocheck.dhash(img), photocheck.dhash(img.resize((300, 300)))
        self.assertLessEqual(photocheck.distance(h1, h2), photocheck.SAME_PHOTO)
        self.assertGreater(photocheck.distance(h1, photocheck.dhash(img.rotate(90))), photocheck.SAME_PHOTO)
        first = self.s.save_photo_check("private/couriers/1.jpg", "courier", photocheck.analyse(img), owner="1")
        self.assertEqual(first["same_as"], [])
        again = self.s.save_photo_check("private/couriers/2.jpg", "courier", photocheck.analyse(img.resize((500, 500))),
                                        owner="2")
        self.assertEqual(again["same_as"], ["private/couriers/1.jpg"])
        self.assertLess(again["score"], first["score"])
        own = self.s.save_photo_check("private/couriers/3.jpg", "courier", photocheck.analyse(img), owner="1")
        self.assertNotIn("private/couriers/1.jpg", own["same_as"])        # the same person may upload again


# =====================================================================================
class TestPages(unittest.TestCase):
    """The new pages through the web app (one seeded app for the whole class)."""

    @classmethod
    def setUpClass(cls):
        cls.app = support.make_app()
        cls.store = cls.app.store
        with cls.store.db() as conn:
            cls.numbers = [r[0] for r in conn.execute("SELECT number FROM shipments ORDER BY id")]
            cls.slugs = [r[0] for r in conn.execute("SELECT slug FROM posts WHERE status = 'Published'")]

    def client(self, login=None, password=None):
        c = self.app.test_client()
        if login:
            support.login(c, login, password)
        return c

    def post(self, c, url, **data):
        page = c.get("/courier").get_data(as_text=True)
        data["csrf_token"] = re.search(r'data-token="([^"]+)"', page).group(1)
        return c.post(url, data=data)

    def test_public_pages_and_seo_files(self):
        c = self.client()
        for url in ["/courier", "/courier/track", "/blog", "/blog/c/guides", "/rewards", "/pay/emi?amount=45000",
                    "/driver/apply", "/blog/" + self.slugs[0], "/courier/track/" + self.numbers[0]]:
            self.assertIn(c.get(url).status_code, (200, 302), url)
        self.assertEqual(c.get("/blog/c/nonsense").status_code, 404)
        self.assertEqual(c.get("/blog/no-such-post").status_code, 404)
        sitemap = c.get("/sitemap.xml").get_data(as_text=True)
        self.assertIn("/blog/" + self.slugs[0], sitemap)
        self.assertIn("/p/book-python", sitemap)
        self.assertIn("Sitemap:", c.get("/robots.txt").get_data(as_text=True))
        self.assertIn("<rss", c.get("/blog/feed.xml").get_data(as_text=True))
        page = c.get("/blog/" + self.slugs[0]).get_data(as_text=True)
        self.assertIn('"@type": "BlogPosting"', page)
        self.assertIn('rel="canonical"', page)
        self.assertTrue(c.get("/pay/emi.json?amount=20000").get_json()["ok"])

    def test_tracking_page_hides_private_data(self):
        number = self.numbers[0]
        with self.store.db() as conn:
            s = conn.execute("SELECT * FROM shipments WHERE number = ?", (number,)).fetchone()
        page = self.client().get("/courier/track/" + number).get_data(as_text=True)
        self.assertNotIn(s["delivery_code"], re.sub(r"YC\d+|&#\d+;", "", page))      # (emoji like &#127978; can hold 4 digits)
        self.assertNotIn(s["to_address"], page)
        self.assertNotIn(s["from_mobile"], page)
        data = self.client().get(f"/courier/live/{number}.json").get_json()
        self.assertEqual(data["trail"], [])
        stranger = self.client("anna.smirnova@example.com", "Shop@2026")
        self.assertEqual(stranger.get(f"/courier/s/{number}").status_code, 404)

    def test_book_and_follow_a_shipment(self):
        c = self.client("demo@example.com", "Demo@1234")
        r = self.post(c, "/courier/book", kind="document", contents="Letters", weight="0.2", from_name="Ravi Kumar",
                      from_mobile="9876543210", from_address="House 214, Sector 9", from_city="Ambala Cantt",
                      to_country="IN", to_city="Ambala City", to_name="Priya Sharma", to_mobile="9812000001",
                      to_address="House 5, Model Town", service="standard", payment="wallet")
        self.assertEqual(r.status_code, 302)
        number = r.headers["Location"].rsplit("/", 1)[-1]
        page = c.get(f"/courier/s/{number}").get_data(as_text=True)
        with self.store.db() as conn:
            code = conn.execute("SELECT delivery_code FROM shipments WHERE number = ?", (number,)).fetchone()[0]
        self.assertIn(code, page)
        live = c.get(f"/courier/live/{number}.json").get_json()
        self.assertTrue(live["ok"])
        self.assertIsNotNone(live["courier"])                 # a demo courier in Ambala Cantt took it
        q = self.post(c, "/courier/quote", kind="parcel", weight="2", from_city="Ambala Cantt", to_country="IN",
                      to_city="Mumbai", service="standard").get_json()
        self.assertTrue(q["ok"] and q["zone"] == "India")
        self.assertIn("label", c.get(f"/courier/s/{number}/label").get_data(as_text=True).lower())

    def test_courier_app_and_admin(self):
        c = self.client("courier@example.com", "Courier@1234")
        self.assertEqual(c.get("/driver").status_code, 200)
        self.assertEqual(self.post(c, "/driver/online", online="1").status_code, 302)
        r = self.post(c, "/driver/ping", lat="30.3361", lng="76.8481")
        self.assertTrue(r.get_json()["ok"])
        self.assertEqual(self.client().get("/img/private/couriers/1.jpg").status_code, 404)
        guest = self.client("anna.smirnova@example.com", "Shop@2026")
        self.assertEqual(guest.get("/driver").status_code, 302)             # not a courier: apply page
        self.assertEqual(guest.get("/admin/shipments").status_code, 403)
        admin = self.client("admin@example.com", "Admin@1234")
        for url in ["/admin/shipments", "/admin/couriers", "/admin/blog", "/admin/rewards", "/admin/yopay",
                    "/admin/photos", "/board/admin", "/admin/shipments/" + self.numbers[0], "/admin/blog/new"]:
            self.assertEqual(admin.get(url).status_code, 200, url)
        with self.store.db() as conn:
            photo = conn.execute("SELECT photo FROM couriers WHERE photo <> '' LIMIT 1").fetchone()[0]
        self.assertEqual(admin.get("/admin/private/" + photo).status_code, 200)
        self.assertEqual(self.store.wallet_audit(), [])

    def test_pay_rewards_and_boards_through_pages(self):
        c = self.client("demo@example.com", "Demo@1234")
        for url in ["/pay", "/pay/add", "/pay/send", "/rewards", "/rewards/quiz"]:
            self.assertEqual(c.get(url).status_code, 200, url)
        quiz = c.get("/rewards/quiz").get_data(as_text=True)
        self.assertNotIn('"answer"', quiz)
        csv_text = c.get("/pay/statement.csv").get_data(as_text=True)
        self.assertIn("Balance (INR)", csv_text)
        self.assertEqual(self.post(c, "/pay/send", to="neha.verma@example.com", amount="10", note="",
                                   password="Demo@1234").status_code, 302)
        seller = self.client("seller@example.com", "Seller@1234")
        for url in ["/board/seller", "/seller/stories", "/seller/stories/new"]:
            self.assertEqual(seller.get(url).status_code, 200, url)
        self.assertEqual(c.get("/board/seller").status_code, 404)           # Ravi has no shop
        self.assertEqual(c.get("/board/admin").status_code, 403)

    def test_new_pages_in_every_language(self):
        c = self.client("demo@example.com", "Demo@1234")
        expect = {"ru": "Отследить отправление", "fr": "Suivre un envoi", "es": "Seguir un envío", "ar": "تتبع شحنة",
                  "zh": "跟踪寄件", "rw": "Kurikirana icyoherezwa"}
        for lang, text in expect.items():
            c.get("/lang/" + lang)
            page = c.get("/courier").get_data(as_text=True)
            self.assertIn(text, page, lang)
            for url in ["/pay", "/rewards", "/blog", "/courier/shipments", "/pay/emi"]:
                self.assertEqual(c.get(url).status_code, 200, (lang, url))
        self.assertIn('dir="rtl"', c.get("/lang/ar") and c.get("/courier").get_data(as_text=True))
        c.get("/lang/en")


class TestDemoContent(unittest.TestCase):
    def test_v3_content_once_and_for_a_version_2_database(self):
        s = support.make_store(seed=True)
        self.assertFalse(demo_v3.ensure_v3(s))                       # already there: nothing added twice
        with s.db() as conn:
            counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                      for t in ("couriers", "shipments", "posts", "quiz_questions", "board_tasks")}
            self.assertTrue(all(counts.values()), counts)
            delivered = conn.execute("SELECT COUNT(*) FROM shipments WHERE status = 'Delivered'").fetchone()[0]
            partner = conn.execute("SELECT COUNT(*) FROM shipments WHERE partner_id IS NOT NULL AND partner_id != 0"
                                   ).fetchone()[0]                  # version 7: one demo parcel is with a partner
            self.assertEqual(delivered + partner, counts["shipments"])
            # a version 2 database: no v3 content and no marker yet
            for t in ("parcel_claims", "customs_declarations", "shipment_events", "shipments", "couriers", "posts", "quiz_attempts", "quiz_questions",
                      "points_ledger", "user_coupons", "board_tasks", "photo_checks"):
                conn.execute(f"DELETE FROM {t}")
            conn.execute("DELETE FROM settings WHERE key = 'v3_demo'")
        import demo_data
        demo_data.seed(s)                                            # a restart of the server
        with s.db() as conn:
            self.assertGreater(conn.execute("SELECT COUNT(*) FROM couriers").fetchone()[0], 5)
            self.assertGreater(conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0], 5)
        demo_data.seed(s)
        with s.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM users WHERE email = 'courier@example.com'").fetchone()[0], 1)
        self.assertEqual(s.wallet_audit(), [])
        # a backup holds the new tables too, and a restore brings them back
        with s.db() as conn:
            before = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("shipments", "wallet_txns", "posts")}
        blob = s.pack(s.export_data())
        with s.db() as conn:
            conn.execute("DELETE FROM customs_declarations")
            conn.execute("DELETE FROM shipment_events")
            conn.execute("DELETE FROM shipments")
        s.import_data(s.unpack(blob))
        with s.db() as conn:
            after = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("shipments", "wallet_txns", "posts")}
        self.assertEqual(before, after)
        self.assertEqual(s.wallet_audit(), [])


if __name__ == "__main__":
    unittest.main()
