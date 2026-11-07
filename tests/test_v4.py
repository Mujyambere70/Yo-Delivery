"""Tests for version 4, Yo Tickets: routes and timetables, seat maps, fares and GST, time zones, seat holds (the
same seat can never be sold twice), payment with Yo Pay / test card / EMI, cancellation and refund rules, trips
cancelled or delayed by the operator, reminders, Yo Rewards points, the signed QR e-ticket and the conductor
app, the live position, the pages in 7 languages and the one-time demo content."""

import os
import re
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import support  # noqa: E402
import demo_v4  # noqa: E402
import tickets as TK  # noqa: E402
from core import ShopError  # noqa: E402

T0 = datetime(2026, 11, 6, 4, 0, 0)            # UTC (09:30 in India)
DAY = "2026-11-08"


class Base(unittest.TestCase):
    def setUp(self):
        self.s = support.make_store(seed=False, notifier=support.fake_notifier(email=False, sms=False),
                                    env={"YO_TICKET_FILL": "0"})
        self.uid = self.user("Test User", "test.user@realmail.local", "9811111111")
        self.n = 0

    def user(self, name, email, mobile, money=50_000_00):
        uid = self.s.register(name, email, mobile, "Secret123", wallet=0)
        if money:
            with self.s.db() as conn:
                self.s.wallet_move(conn, uid, money, "topup", "TEST", "Test money")
        return uid

    def other(self, money=50_000_00):
        self.n += 1
        return self.user(f"Other {self.n}", f"other{self.n}@realmail.local", f"98330000{self.n:02d}", money)

    def trip(self, mode="bus", a="Ambala Cantt", b="New Delhi", day=DAY, idx=0):
        return self.s.search_trips(mode, a, b, day, 1, at=T0)["trips"][idx]

    def seats(self, trip_id, n=1, cls=None, fee=None):
        _, secs = self.s.seat_map(trip_id, at=T0)
        free = [x for sec in secs for row in sec["rows"] for x in row
                if x and not x["taken"] and (cls is None or x["cls"] == cls) and (fee is None or bool(x["fee"]) == fee)]
        return [x["code"] for x in free[:n]]

    def form(self, n, payment="wallet", **extra):
        f = {"contact_mobile": "9811111111", "payment": payment}
        for i in range(n):
            f.update({f"name_{i}": f"Passenger {i + 1}", f"age_{i}": "30"})
        if payment == "card":
            f.update(card="4242 4242 4242 4242", expiry="12/30", cvc="123", card_name="Test User")
        f.update(extra)
        return f

    def book(self, trip_id, seats, payment="wallet", uid=None, at=T0, **extra):
        uid = uid or self.uid
        pnr = self.s.hold_seats(uid, trip_id, seats, at=at)
        self.s.pay_booking(uid, pnr, self.form(len(seats), payment, **extra), at=at + timedelta(minutes=1))
        return pnr

    def wallet(self, uid=None):
        return self.s.get_user(uid or self.uid)["wallet"]

    def notes(self, uid=None, like="%"):
        with self.s.db() as conn:
            return [r[0] for r in conn.execute("SELECT subject FROM notifications WHERE user_id = ? AND channel = 'site' "
                                               "AND subject LIKE ? ORDER BY id", (uid or self.uid, like))]

    def depart(self, pnr):
        return TK._dt(self.s.get_booking(pnr)["depart_utc"])


# =====================================================================================
class TestRoutesFaresAndSeats(unittest.TestCase):
    def test_route_rules_give_clear_messages(self):
        with self.assertRaisesRegex(ShopError, "Barara"):
            TK.check_route("train", "Mullana", "New Delhi")
        with self.assertRaisesRegex(ShopError, "too far"):
            TK.check_route("bus", "Ambala Cantt", "Chennai")
        with self.assertRaisesRegex(ShopError, "inside one country"):          # version 5: also between neighbours
            TK.check_route("bus", "New Delhi", "Dubai")
        with self.assertRaisesRegex(ShopError, "No direct flights"):
            TK.check_route("flight", "Chandigarh", "Moscow")
        with self.assertRaisesRegex(ShopError, "same airport"):
            TK.check_route("flight", "Ambala Cantt", "Mohali")
        with self.assertRaisesRegex(ShopError, "different"):
            TK.check_route("bus", "Kalka", "kalka")
        a, b, notes = TK.check_route("flight", "Ambala Cantt", "Mumbai")      # no airport: the nearest one
        self.assertEqual((a["name"], a["airport"], b["airport"]), ("Chandigarh", "IXC", "BOM"))
        self.assertIn("nearest airport to Ambala Cantt is Chandigarh", notes[0])

    def test_timetable_is_always_the_same_for_a_route(self):
        one, two = TK.timetable("train", "Chandigarh", "Mumbai"), TK.timetable("train", "Chandigarh", "Mumbai")
        self.assertEqual(one, two)
        self.assertEqual(len({s["code"] for s in one}), len(one))
        self.assertTrue(all(s["classes"] == ["SL", "3A", "2A"] for s in one))          # long trip: sleeper classes
        self.assertTrue(all(s["classes"] == ["CC", "EC"] for s in TK.timetable("train", "Ambala Cantt", "New Delhi")))
        self.assertTrue(any(s["days"] != "1111111" for s in one))                    # long trains: some days only
        for s in TK.timetable("bus", "Ambala Cantt", "New Delhi"):
            self.assertTrue(4 * 60 <= s["duration"] <= 7 * 60, s)                       # about 250 km by road

    def test_seat_maps(self):
        self.assertEqual(TK.capacity("bus", ["seater"]), {"seater": 40})
        self.assertEqual(TK.capacity("bus", ["sleeper"]), {"sleeper": 30})
        self.assertEqual(TK.capacity("train", ["SL", "3A", "2A"]), {"SL": 3 * 72, "3A": 2 * 64, "2A": 48})
        self.assertEqual(TK.capacity("train", ["CC", "EC"]), {"CC": 3 * 75, "EC": 56})
        self.assertEqual(TK.capacity("flight", ["Y", "J"]), {"J": 12, "Y": 144})
        idx = TK.seat_index("train", ["SL", "3A", "2A"])
        self.assertEqual((idx["B1-1"]["kind"], idx["B1-7"]["kind"], idx["S2-8"]["kind"], idx["A1-2"]["kind"]),
                         ("Lower", "Side lower", "Side upper", "Upper"))
        fl = TK.seat_index("flight", ["Y", "J"])
        self.assertEqual((fl["12C"]["fee"], fl["12C"]["tag"]), (TK.SEAT_FEES["legroom"], "Extra legroom"))
        self.assertEqual(fl["4A"]["fee"], TK.SEAT_FEES["front"])
        self.assertEqual((fl["20B"]["fee"], fl["20A"]["fee"], fl["2A"]["fee"]), (0, TK.SEAT_FEES["window_aisle"], 0))
        self.assertEqual(fl["2A"]["cls"], "J")

    def test_time_zones(self):
        self.assertEqual(TK.to_utc(datetime(2026, 11, 20, 10, 0), "Asia/Kolkata"), datetime(2026, 11, 20, 4, 30))
        self.assertEqual(TK.to_local(datetime(2026, 11, 20, 4, 30), "Asia/Dubai"), datetime(2026, 11, 20, 8, 30))
        self.assertEqual(TK.to_local(datetime(2026, 11, 20, 4, 30), "Asia/Kathmandu"), datetime(2026, 11, 20, 10, 15))
        self.assertEqual(TK.tz_label("Asia/Kolkata"), "IST")
        self.assertEqual(TK.duration_text(135), "2h 15m")

    def test_fares_demand_and_gst(self):
        bus = {"mode": "bus", "distance_km": 253.0, "from_city": "Ambala Cantt", "to_city": "New Delhi"}
        train = {"mode": "train", "distance_km": 243.0, "from_city": "Ambala Cantt", "to_city": "New Delhi"}
        fl = {"mode": "flight", "distance_km": 2200.0, "from_city": "New Delhi", "to_city": "Dubai"}
        self.assertEqual(TK.fare_for(bus, "seater"), 380_00)                           # 253 km x ₹1.50
        self.assertGreater(TK.fare_for(bus, "seater", 0.8), TK.fare_for(bus, "seater", 0.1))
        self.assertEqual(TK.fare_for(train, "CC", 0.9), TK.fare_for(train, "CC", 0.0))   # train fares are fixed
        far, soon = T0 + timedelta(days=40), T0 + timedelta(days=2)
        self.assertGreater(TK.fare_for(fl, "Y", 0, soon, T0), TK.fare_for(fl, "Y", 0, far, T0))
        self.assertEqual(TK.fare_for(fl, "Y", 0, far, T0), 5200_00 + 400 * 2200)       # abroad: base + per km
        self.assertEqual((TK.gst_rate(train, "SL"), TK.gst_rate(fl, "J"), TK.gst_rate(bus, "seater")), (0, 12, 5))

    def test_price_lines_add_up(self):
        fl = {"mode": "flight", "distance_km": 1150.0, "from_city": "New Delhi", "to_city": "Mumbai"}
        idx = TK.seat_index("flight", ["Y", "J"])
        m = TK.price_booking(fl, "Y", 7000_00, [idx["12C"], idx["20B"]])
        self.assertEqual(m["seat_fees"], 450_00)
        self.assertEqual(m["airport_fees"], 2 * 450_00)
        self.assertEqual(m["conv_fee"], 2 * 20_00)
        self.assertEqual(m["tax"], round((14000_00 + 450_00) * 0.05 / 100) * 100)
        self.assertEqual(m["total"], sum(line[1] for line in m["lines"]))
        self.assertTrue(all(line[1] % 100 == 0 for line in m["lines"]))                 # whole rupees
        self.assertEqual(m["lines"][0], ["Fare", 14000_00, 2])

    def test_refund_rules(self):
        b = {"total": 1000_00, "conv_fee": 20_00}
        dep = T0 + timedelta(days=10)
        for mode, hours, pct in [("bus", 30, 10), ("bus", 13, 25), ("bus", 5, 50), ("bus", 2, 100), ("train", 50, 5),
                                 ("train", 20, 25), ("flight", 80, 15), ("flight", 2, 100)]:
            got = TK.refund_for(b, mode, dep, dep - timedelta(hours=hours))
            self.assertEqual(got[0], pct, (mode, hours))
            self.assertEqual(got[1] + got[2], 980_00)                                   # the convenience fee stays
        self.assertEqual(TK.refund_for(b, "bus", dep, dep, operator_cancelled=True), (0, 0, 1000_00))


# =====================================================================================
class TestSearchAndHold(Base):
    def test_search_makes_services_and_trips_once(self):
        r = self.s.search_trips("bus", "Ambala Cantt", "New Delhi", DAY, 2, at=T0)
        self.s.search_trips("bus", "ambala cantt", "New Delhi", DAY, 2, at=T0)
        with self.s.db() as conn:
            services = conn.execute("SELECT COUNT(*) FROM ticket_services").fetchone()[0]
            trips = conn.execute("SELECT COUNT(*) FROM ticket_trips").fetchone()[0]
        self.assertEqual(services, len(TK.timetable("bus", "Ambala Cantt", "New Delhi")))
        self.assertEqual(trips, len(r["trips"]))
        self.assertEqual([t["depart_utc"] for t in r["trips"]], sorted(t["depart_utc"] for t in r["trips"]))
        self.assertTrue(all(t["bookable"] and t["classes"][0]["seats"] == 40 for t in r["trips"]))
        for bad, msg in [(dict(day="2026-11-01"), "passed"), (dict(day="2027-06-01"), "120 days"),
                         (dict(pax=7), "1 to 6"), (dict(day="nonsense"), "date")]:
            with self.assertRaisesRegex(ShopError, msg):
                self.s.search_trips("bus", "Ambala Cantt", "New Delhi", bad.get("day", DAY), bad.get("pax", 1), at=T0)

    def test_the_same_seat_is_never_sold_twice(self):
        t = self.trip()
        seat = self.seats(t["trip_id"])
        self.s.hold_seats(self.uid, t["trip_id"], seat, at=T0)
        rival = self.other()
        with self.assertRaisesRegex(ShopError, "just taken"):
            self.s.hold_seats(rival, t["trip_id"], seat + self.seats(t["trip_id"]), at=T0)
        with self.s.db() as conn:                            # nothing of the refused hold is left
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM ticket_bookings WHERE user_id = ?",
                                          (rival,)).fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM ticket_seats WHERE trip_id = ?",
                                          (t["trip_id"],)).fetchone()[0], 1)
        view, _ = self.s.seat_map(t["trip_id"], at=T0)
        self.assertEqual(view["classes"][0]["seats"], 39)

    def test_hold_rules(self):
        t = self.trip()
        tid = t["trip_id"]
        with self.assertRaisesRegex(ShopError, "at most 6"):
            self.s.hold_seats(self.uid, tid, self.seats(tid, 7), at=T0)
        with self.assertRaisesRegex(ShopError, "twice"):
            self.s.hold_seats(self.uid, tid, ["1", "1"], at=T0)
        with self.assertRaisesRegex(ShopError, "does not exist"):
            self.s.hold_seats(self.uid, tid, ["99"], at=T0)
        rail = self.trip("train")
        with self.assertRaisesRegex(ShopError, "one class"):
            self.s.hold_seats(self.uid, rail["trip_id"], ["C1-1", "E1-1"], at=T0)
        with self.assertRaisesRegex(ShopError, "closed"):
            self.s.hold_seats(self.uid, tid, ["5"], at=TK._dt(t["depart_utc"]) - timedelta(minutes=5))
        self.s.hold_seats(self.uid, tid, ["1"], at=T0)
        self.s.hold_seats(self.uid, tid, ["2"], at=T0)
        with self.assertRaisesRegex(ShopError, "on hold"):
            self.s.hold_seats(self.uid, tid, ["3"], at=T0)

    def test_a_hold_expires_and_the_seat_comes_back(self):
        t = self.trip()
        pnr = self.s.hold_seats(self.uid, t["trip_id"], ["7"], at=T0)
        late = T0 + timedelta(minutes=TK.HOLD_MINUTES + 1)
        with self.assertRaisesRegex(ShopError, "time is over"):
            self.s.pay_booking(self.uid, pnr, self.form(1), at=late)
        self.assertEqual(self.s.tickets_tick(at=late)["freed"], 1)
        self.assertEqual(self.s.get_booking(pnr)["status"], TK.EXPIRED)
        self.s.hold_seats(self.other(), t["trip_id"], ["7"], at=late)                  # somebody else gets it
        self.assertEqual(self.wallet(), 50_000_00)                                     # nothing was charged


# =====================================================================================
class TestPayAndCancel(Base):
    def test_pay_with_yo_pay_writes_the_ledger(self):
        t = self.trip()
        pnr = self.book(t["trip_id"], ["9", "10"])
        b = self.s.get_booking(pnr, self.uid)
        self.assertEqual(b["status"], TK.CONFIRMED)
        self.assertEqual([p["name"] for p in b["passengers"]], ["Passenger 1", "Passenger 2"])
        self.assertEqual(self.wallet(), 50_000_00 - b["total"])
        with self.s.db() as conn:
            line = conn.execute("SELECT kind, amount FROM wallet_txns WHERE ref = ?", (pnr,)).fetchone()
        self.assertEqual((line[0], line[1]), ("ticket", -b["total"]))
        self.assertEqual(self.s.wallet_audit(), [])
        self.assertIn(f"Ticket confirmed: {pnr}", self.notes())

    def test_not_enough_money_keeps_the_seats_held(self):
        poor = self.other(money=0)
        t = self.trip()
        pnr = self.s.hold_seats(poor, t["trip_id"], ["11"], at=T0)
        with self.assertRaisesRegex(ShopError, "Not enough Yo Pay"):
            self.s.pay_booking(poor, pnr, self.form(1), at=T0)
        self.assertEqual(self.s.get_booking(pnr)["status"], TK.HELD)                   # still kept for this person
        self.s.pay_booking(poor, pnr, self.form(1, "card"), at=T0)
        self.assertEqual(self.s.get_booking(pnr)["payment"], "card")
        self.assertEqual(self.wallet(poor), 0)

    def test_card_declined_and_emi(self):
        fl = self.trip("flight", "New Delhi", "Dubai", "2026-11-28")
        pnr = self.s.hold_seats(self.uid, fl["trip_id"], self.seats(fl["trip_id"], 1, "Y", False), at=T0)
        with self.assertRaisesRegex(ShopError, "declined"):
            self.s.pay_booking(self.uid, pnr, self.form(1, "card", card="4000 0000 0000 0002"), at=T0)
        self.s.pay_booking(self.uid, pnr, self.form(1, "card", emi_months="6"), at=T0)
        b = self.s.get_booking(pnr)
        self.assertEqual((b["emi_months"], b["card_last4"]), (6, "4242"))
        self.assertGreater(b["emi_monthly"], b["total"] // 6 - 100)
        bus = self.trip()
        small = self.s.hold_seats(self.uid, bus["trip_id"], ["1"], at=T0)            # ₹400 is too small for EMI
        with self.assertRaisesRegex(ShopError, "EMI plan"):
            self.s.pay_booking(self.uid, small, self.form(1, "card", emi_months="3"), at=T0)

    def test_passenger_details_are_checked(self):
        t = self.trip()
        pnr = self.s.hold_seats(self.uid, t["trip_id"], ["1", "2"], at=T0)
        for change, msg in [({"name_1": "A"}, "name of passenger 2"), ({"age_0": "0"}, "age of passenger 1"),
                            ({"contact_mobile": "12"}, "mobile number"), ({"payment": "cash"}, "how to pay")]:
            with self.assertRaisesRegex(ShopError, msg):
                self.s.pay_booking(self.uid, pnr, self.form(2, **change), at=T0)
        self.assertEqual(self.s.get_booking(pnr)["status"], TK.HELD)

    def test_cancel_refund_follows_the_rules(self):
        t = self.trip()
        pnr = self.book(t["trip_id"], ["3"])
        b = self.s.get_booking(pnr)
        before = self.wallet()
        refund = self.s.cancel_booking(self.uid, pnr, at=self.depart(pnr) - timedelta(hours=30))
        self.assertEqual(refund, (b["total"] - b["conv_fee"]) - round((b["total"] - b["conv_fee"]) * 0.10 / 100) * 100)
        self.assertEqual(self.wallet(), before + refund)
        b = self.s.get_booking(pnr)
        self.assertEqual((b["status"], b["refund"], b["charge"] + b["refund"]), (TK.CANCELLED, refund,
                                                                                 b["total"] - b["conv_fee"]))
        with self.assertRaisesRegex(ShopError, "cannot be cancelled"):
            self.s.cancel_booking(self.uid, pnr, at=T0)
        self.s.hold_seats(self.other(), t["trip_id"], ["3"], at=T0)                   # the seat is free again
        late = self.book(t["trip_id"], ["4"])
        with self.assertRaisesRegex(ShopError, "already left"):
            self.s.cancel_booking(self.uid, late, at=self.depart(late) + timedelta(minutes=5))
        self.assertEqual(self.s.wallet_audit(), [])

    def test_giving_held_seats_back(self):
        t = self.trip()
        pnr = self.s.hold_seats(self.uid, t["trip_id"], ["5", "6"], at=T0)
        self.assertEqual(self.s.cancel_booking(self.uid, pnr, at=T0), 0)
        view, _ = self.s.seat_map(t["trip_id"], at=T0)
        self.assertEqual(view["classes"][0]["seats"], 40)

    def test_operator_cancels_a_trip_and_everybody_gets_all_the_money(self):
        t = self.trip()
        mine = self.book(t["trip_id"], ["1", "2"])
        friend = self.other()
        theirs = self.book(t["trip_id"], ["3"], payment="card", uid=friend)
        start = self.wallet()
        self.assertEqual(self.s.cancel_trip(t["trip_id"], "Road closed"), 2)
        b = self.s.get_booking(mine)
        self.assertEqual((b["status"], b["refund"], b["charge"]), (TK.CANCELLED, b["total"], 0))
        self.assertEqual(self.wallet(), start + b["total"])                            # fees too
        self.assertEqual(self.s.get_booking(theirs)["refund"], self.s.get_booking(theirs)["total"])
        self.assertTrue(self.notes(like="Trip cancelled%") and self.notes(friend, "Trip cancelled%"))
        again = self.s.search_trips("bus", "Ambala Cantt", "New Delhi", DAY, 1, at=T0)["trips"][0]
        self.assertFalse(again["bookable"])
        with self.assertRaisesRegex(ShopError, "cancelled by the operator"):
            self.s.hold_seats(self.uid, t["trip_id"], ["9"], at=T0)
        with self.assertRaisesRegex(ShopError, "already cancelled"):
            self.s.cancel_trip(t["trip_id"])

    def test_delay_is_sent_and_moves_the_boarding_time(self):
        t = self.trip()
        pnr = self.book(t["trip_id"], ["8"])
        self.assertEqual(self.s.set_trip_delay(t["trip_id"], 40, "Fog on the highway"), 1)
        self.assertTrue(self.notes(like="Delay:%"))
        b = self.s.get_booking(pnr)
        self.assertEqual(self.s.trip_phase(b, self.depart(pnr) + timedelta(minutes=20))[0], "Boarding")
        self.assertEqual(self.s.trip_phase(b, self.depart(pnr) + timedelta(minutes=45))[0], "On the way")
        with self.assertRaisesRegex(ShopError, "0 to 1440"):
            self.s.set_trip_delay(t["trip_id"], 5000)

    def test_reminder_once_then_completed_with_points_once(self):
        t = self.trip()
        pnr = self.book(t["trip_id"], ["12"])
        dep = self.depart(pnr)
        self.assertEqual(self.s.tickets_tick(at=dep - timedelta(hours=2))["reminded"], 1)
        self.assertEqual(self.s.tickets_tick(at=dep - timedelta(hours=1))["reminded"], 0)
        self.assertTrue(self.notes(like="Your trip is soon:%"))
        end = TK._dt(self.s.get_booking(pnr)["arrive_utc"]) + timedelta(minutes=1)
        self.assertEqual(self.s.tickets_tick(at=end)["completed"], 1)
        self.assertEqual(self.s.tickets_tick(at=end)["completed"], 0)
        b = self.s.get_booking(pnr)
        self.assertEqual(b["status"], TK.COMPLETED)
        self.assertEqual(self.s.points(self.uid), b["total"] // 10000)

    def test_demo_seats_of_other_travellers(self):
        s = support.make_store(seed=False)                                             # YO_TICKET_FILL on
        r = s.search_trips("flight", "New Delhi", "Mumbai", DAY, 1, at=T0)
        with s.db() as conn:
            taken = conn.execute("SELECT COUNT(*) FROM ticket_seats WHERE trip_id = ? AND booking_id = 0",
                                 (r["trips"][0]["trip_id"],)).fetchone()[0]
        self.assertTrue(0.10 * 156 <= taken <= 0.86 * 156, taken)
        self.assertEqual(s.ticket_stats()["sold"], 0)                                 # not counted as our sales


# =====================================================================================
class TestQrAndConductor(Base):
    def setUp(self):
        super().setUp()
        self.t = self.trip()
        self.pnr = self.book(self.t["trip_id"], ["1", "2"])
        self.dep = self.depart(self.pnr)
        self.staff = self.user("Conductor One", "conductor.one@realmail.local", "9844000001", 0)
        self.s.add_staff("conductor.one@realmail.local", self.t["operator_code"])

    def test_signed_qr_code(self):
        token = self.s.ticket_token(self.pnr)
        self.assertEqual(self.s.check_token(token), self.pnr)
        self.assertEqual(self.s.check_token("https://yo.example/tickets/v/" + token), self.pnr)
        self.assertIsNone(self.s.check_token(token[:-1] + ("0" if token[-1] != "0" else "1")))
        self.assertIsNone(self.s.check_token(self.pnr))
        v = self.s.verify_ticket(token, at=T0)
        self.assertTrue(v["valid"])
        self.assertEqual(v["passengers"], 2)
        self.assertNotIn("Passenger 1", str(v))                                         # no names in public
        self.assertFalse(self.s.verify_ticket("ABCDEF.123456789abc")["valid"])

    def test_boarding_rules(self):
        token = self.s.ticket_token(self.pnr)
        with self.assertRaisesRegex(ShopError, "Only the staff"):
            self.s.board_ticket(self.uid, token, at=self.dep)
        with self.assertRaisesRegex(ShopError, "Too early"):
            self.s.board_ticket(self.staff, token, at=self.dep - timedelta(hours=5))
        r = self.s.board_ticket(self.staff, token, at=self.dep - timedelta(minutes=30))
        self.assertEqual((r["names"], r["already"]), (["Passenger 1", "Passenger 2"], False))
        self.assertTrue(self.s.board_ticket(self.staff, self.pnr.lower(), at=self.dep)["already"])   # typed PNR
        self.assertTrue(self.s.board_ticket(self.staff, token, at=self.dep - timedelta(hours=6))["already"])  # not "too early"
        with self.assertRaisesRegex(ShopError, "already arrived"):
            self.s.board_ticket(self.staff, token, at=TK._dt(self.s.get_booking(self.pnr)["arrive_utc"]) +
                                timedelta(minutes=5))
        with self.assertRaisesRegex(ShopError, "wrong signature"):
            self.s.board_ticket(self.staff, token[:-2] + "zz", at=self.dep)
        other_staff = self.user("Conductor Two", "conductor.two@realmail.local", "9844000002", 0)
        self.s.add_staff("conductor.two@realmail.local", "PRL")
        with self.assertRaisesRegex(ShopError, "not for Peninsula Rail"):
            self.s.board_ticket(other_staff, token, at=self.dep)
        cancelled = self.book(self.t["trip_id"], ["3"])
        self.s.cancel_booking(self.uid, cancelled, at=T0)
        with self.assertRaisesRegex(ShopError, "not valid for travel"):
            self.s.board_ticket(self.staff, self.s.ticket_token(cancelled), at=self.dep)

    def test_manifest_and_trips_of_the_company(self):
        st, trips = self.s.conductor_trips(self.staff, at=self.dep - timedelta(hours=2))
        mine = [t for t in trips if t["trip_id"] == self.t["trip_id"]][0]
        self.assertEqual((mine["passengers"], mine["boarded"]), (2, 0))
        self.s.board_ticket(self.staff, self.s.ticket_token(self.pnr), at=self.dep)
        _, view, people, others = self.s.manifest(self.staff, self.t["trip_id"])
        self.assertEqual([p["name"] for p in people], ["Passenger 1", "Passenger 2"])
        self.assertTrue(all(p["boarded_at"] for p in people))
        rival = self.s.search_trips("bus", "Kurukshetra", "Chandigarh", DAY, 1, at=T0)["trips"]
        foreign = [t for t in rival if t["operator_code"] != self.t["operator_code"]]
        if foreign:
            with self.assertRaisesRegex(ShopError, "another company"):
                self.s.manifest(self.staff, foreign[0]["trip_id"])
        with self.assertRaisesRegex(ShopError, "already staff"):
            self.s.add_staff("conductor.one@realmail.local", "SRW")

    def test_live_position_from_the_timetable(self):
        self.assertIsNone(self.s.trip_live(self.pnr, self.uid, at=T0)["courier"])
        arrive = TK._dt(self.s.get_booking(self.pnr)["arrive_utc"])
        mid = self.dep + (arrive - self.dep) / 2
        live = self.s.trip_live(self.pnr, self.uid, at=mid)
        self.assertEqual((live["status"], live["courier"]["vehicle"]), ("On the way", "bus"))
        lat = live["courier"]["lat"]
        self.assertTrue(min(live["pickup"]["lat"], live["drop"]["lat"]) < lat < max(live["pickup"]["lat"], live["drop"]["lat"]))
        self.assertTrue(self.s.trip_live(self.pnr, self.uid, at=arrive + timedelta(minutes=1))["done"])
        with self.assertRaises(ShopError):
            self.s.trip_live(self.pnr, self.other(), at=mid)                            # only the owner


# =====================================================================================
class TestTicketPages(unittest.TestCase):
    """The pages through the web app (one seeded app for the whole class)."""

    @classmethod
    def setUpClass(cls):
        cls.app = support.make_app()
        cls.store = cls.app.store
        with cls.store.db() as conn:
            cls.pnr = conn.execute("SELECT b.pnr FROM ticket_bookings b JOIN users u ON u.id = b.user_id WHERE "
                                   "u.email = 'demo@example.com' AND b.status = 'Confirmed' ORDER BY b.id").fetchone()[0]

    def client(self, login=None, password=None, lang=None):
        c = self.app.test_client()
        if login:
            support.login(c, login, password)
        if lang:
            c.get("/lang/" + lang)
        return c

    def token(self, c, url="/tickets"):
        return re.search(r'name="csrf_token" value="([^"]+)"', c.get(url).get_data(as_text=True)).group(1)

    def test_public_pages_and_seo(self):
        c = self.client()
        day = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d")
        for url in ["/tickets", f"/tickets/search?mode=bus&from=Ambala+Cantt&to=New+Delhi&date={day}&pax=2",
                    f"/tickets/search?mode=flight&from=Ambala+Cantt&to=Mumbai&date={day}&pax=1&sort=price",
                    f"/tickets/search?mode=train&from=Mullana&to=Mumbai&date={day}",
                    "/tickets/bus/ambala-cantt-to-new-delhi", "/tickets/flight/new-delhi-to-moscow"]:
            self.assertEqual(c.get(url).status_code, 200, url)
        page = c.get(f"/tickets/search?mode=flight&from=Ambala+Cantt&to=Mumbai&date={day}").get_data(as_text=True)
        self.assertIn("nearest airport to Ambala Cantt is Chandigarh", page)
        trip = re.search(r"/tickets/trip/(\d+)\?cls=", page).group(1)
        seats = c.get(f"/tickets/trip/{trip}?pax=2").get_data(as_text=True)
        self.assertIn('id="seatForm"', seats)
        self.assertIn("Sign in to choose seats", seats)
        self.assertEqual(c.get("/tickets/bus/atlantis-to-new-delhi").status_code, 404)
        self.assertEqual(c.get("/tickets/trip/999999").status_code, 404)
        self.assertIn("/tickets/bus/ambala-cantt-to-new-delhi", c.get("/sitemap.xml").get_data(as_text=True))
        self.assertIn("Disallow: /conductor", c.get("/robots.txt").get_data(as_text=True))
        route = c.get("/tickets/train/chandigarh-to-mumbai").get_data(as_text=True)
        self.assertIn('rel="canonical"', route)

    def test_booking_through_the_web(self):
        c = self.client("demo@example.com", "Demo@1234")
        day = (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d")
        page = c.get(f"/tickets/search?mode=bus&from=Chandigarh&to=Dehradun&date={day}&pax=1").get_data(as_text=True)
        trip = re.search(r"/tickets/trip/(\d+)\?cls=", page).group(1)
        html = c.get(f"/tickets/trip/{trip}?pax=1").get_data(as_text=True)
        seat = re.search(r'name="seat" value="([^"]+)" data-label="[^"]+" data-local="\d+"\s+aria-label', html).group(1)
        token = re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)
        r = c.post(f"/tickets/trip/{trip}/hold", data={"csrf_token": token, "seat": [seat], "pax": 1})
        self.assertEqual(r.status_code, 302)
        pay_url = r.headers["Location"]
        pnr = pay_url.split("/")[3]
        self.assertIn('data-left="', c.get(pay_url).get_data(as_text=True))
        r = c.post(pay_url, data={"csrf_token": token, "name_0": "Ravi Kumar", "age_0": "31", "contact_mobile": "9812000000",
                                  "payment": "card", "card": "4242 4242 4242 4242", "expiry": "12/30", "cvc": "123",
                                  "card_name": "Ravi Kumar"})
        self.assertEqual(r.headers["Location"], f"/tickets/b/{pnr}")
        ticket = c.get(f"/tickets/b/{pnr}").get_data(as_text=True)
        self.assertIn('id="ticketQr"', ticket)
        qr = re.search(r'data-qr="[^"]*(/tickets/v/[^"]+)"', ticket).group(1)
        self.assertTrue(c.get(f"/tickets/b/{pnr}/live.json").get_json()["ok"])
        self.store.register("Web Stranger", "web.stranger@realmail.local", "9811000099", "Secret123")
        stranger = self.client("web.stranger@realmail.local", "Secret123")
        self.assertEqual(stranger.get(f"/tickets/b/{pnr}").status_code, 404)             # not their ticket
        self.assertEqual(stranger.get(f"/tickets/b/{pnr}/live.json").status_code, 404)
        guest = self.client()
        public = guest.get(qr).get_data(as_text=True)
        self.assertIn("Valid e-ticket", public)
        self.assertNotIn("Ravi Kumar", public.split("<main", 1)[1].split(">", 1)[1].split("</main>")[0])
        self.assertEqual(guest.get(f"/tickets/b/{pnr}").status_code, 302)              # sign in first
        self.assertEqual(c.get("/tickets/my").status_code, 200)

    def test_conductor_and_admin_pages(self):
        ravi = self.client("demo@example.com", "Demo@1234")
        self.assertEqual(ravi.get("/conductor").status_code, 302)                     # not staff
        self.assertEqual(ravi.get("/admin/tickets").status_code, 403)                 # not an admin
        cond = self.client("conductor@example.com", "Conductor@1234")
        self.assertEqual(cond.get("/conductor").status_code, 200)
        token = self.token(cond, "/conductor")
        r = cond.post("/conductor/check", data={"csrf_token": token, "token": "NOPE12.abcdefabcdef", "json": "1"})
        self.assertFalse(r.get_json()["ok"])
        admin = self.client("admin@example.com", "Admin@1234")
        for url in ["/admin/tickets", f"/admin/tickets/b/{self.pnr}", "/admin/tickets/trips", "/admin/tickets/services",
                    "/admin/tickets/staff", "/admin/tickets?status=Confirmed&mode=flight"]:
            self.assertEqual(admin.get(url).status_code, 200, url)

    def test_pages_in_other_languages(self):
        for lang, words in [("ru", ["Билеты на автобус, поезд и самолёт", "Куда"]), ("ar", ['dir="rtl"', "التذاكر"]),
                            ("zh", ["汽车票、火车票和机票"]), ("rw", ["Inzira zikunzwe"]), ("fr", ["Départ de"]),
                            ("es", ["Billetes de autobús"])]:
            html = self.client(lang=lang).get("/tickets").get_data(as_text=True)
            for w in words:
                self.assertIn(w, html, (lang, w))
        c = self.client("demo@example.com", "Demo@1234", "ru")
        html = c.get(f"/tickets/b/{self.pnr}").get_data(as_text=True)
        self.assertIn("Статус поездки", html)


# =====================================================================================
class TestDemoContent(unittest.TestCase):
    def test_v4_content_once_backup_and_an_older_database(self):
        s = support.make_store(seed=True)
        self.assertFalse(demo_v4.ensure_v4(s))
        with s.db() as conn:
            statuses = {r[0] for r in conn.execute("SELECT status FROM ticket_bookings")}
            self.assertTrue({"Completed", "Cancelled", "Confirmed"} <= statuses, statuses)
            self.assertTrue(conn.execute("SELECT 1 FROM points_ledger WHERE reason = 'Trip with Yo Tickets'").fetchone())
            self.assertTrue(conn.execute("SELECT 1 FROM ticket_passengers WHERE boarded_at <> ''").fetchone())
            self.assertTrue(conn.execute("SELECT 1 FROM posts WHERE slug = 'yo-tickets-bus-train-flight'").fetchone())
        cond = s.login("conductor@example.com", "Conductor@1234")
        self.assertEqual(s.staff_of(cond["id"])["operator_code"], "SRW")
        self.assertEqual(s.wallet_audit(), [])
        tables = ("ticket_trips", "ticket_bookings", "ticket_passengers", "ticket_seats", "ticket_staff")
        with s.db() as conn:
            before = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
        blob = s.pack(s.export_data())
        with s.db() as conn:
            for t in ("ticket_passengers", "ticket_seats", "ticket_bookings"):
                conn.execute(f"DELETE FROM {t}")
        s.import_data(s.unpack(blob))
        with s.db() as conn:
            after = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
        self.assertEqual(before, after)
        # a version 3 database: no tickets yet and no marker -> the restart adds them once
        with s.db() as conn:
            for t in ("ticket_passengers", "ticket_seats", "ticket_bookings", "ticket_staff"):
                conn.execute(f"DELETE FROM {t}")
            conn.execute("DELETE FROM points_ledger WHERE reason = 'Trip with Yo Tickets'")
            conn.execute("DELETE FROM settings WHERE key = 'v4_demo'")
        import demo_data
        demo_data.seed(s)
        demo_data.seed(s)
        with s.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM ticket_bookings").fetchone()[0], before["ticket_bookings"])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM users WHERE email = 'conductor@example.com'").fetchone()[0], 1)
        self.assertEqual(s.wallet_audit(), [])


if __name__ == "__main__":
    unittest.main()
