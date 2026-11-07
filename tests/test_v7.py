"""Tests for version 7: contracts, holiday balance, payroll with dual control and example income tax rules, training
certificates, helper offices, clock-in at the office; tax invoices by kind of goods with GSTIN/INN checks, credit
notes and UBL e-invoices; signed payment callbacks and reconciliation; the courier partner API, delivery time windows,
signatures, HS codes and advance customs data; the postal-code directory and GTFS-Realtime delays; more chat examples,
an outside moderation service and the live check of the ID check; the data warehouse, the hub forecast and A/B tests;
the help desk, parcel claims and the safety check of orders; the atomic daily claim, the Redis cache layer, the privacy
register, security headers and the pages of version 7."""

import io
import json
import os
import sys
import time
import unittest
import zipfile
from datetime import date, datetime, timedelta, timezone
from xml.etree import ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import support  # noqa: E402
import addresscheck  # noqa: E402
import analytics  # noqa: E402
import courier as C  # noqa: E402
import courierplus  # noqa: E402
import gtfsrt  # noqa: E402
import idcheck  # noqa: E402
import invoices  # noqa: E402
import partners  # noqa: E402
import people  # noqa: E402
import providers  # noqa: E402
import routeplan  # noqa: E402
from core import ShopError, now  # noqa: E402
from test_v6 import FACE, SAMPLE_GTFS, Base, read_bytes  # noqa: E402

ADDR = {"name": "Test User", "mobile": "9811111111", "line1": "12 Main Road", "city": "Ambala",
        "state": "Haryana", "pincode": "133001", "kind": "Home"}


def first_of_last_month():
    d = date.today().replace(day=1) - timedelta(days=1)
    return d.replace(day=1)


class People(Base):
    def contract(self, sid, amount, start=None, **kw):
        data = {"kind": "permanent", "start_day": (start or date(date.today().year, 1, 1)).isoformat(),
                "pay_kind": "month", "pay_amount": str(amount), "hours_week": "40", "leave_days": "24", **kw}
        return self.s.save_contract(self.admin, sid, data)


class TestPeople(People):
    def test_contract_and_holiday_balance(self):
        sid, uid = self.worker(self.ambala, "hub")
        self.contract(sid, 30000)
        bal = self.s.leave_balance(sid)
        self.assertEqual(bal["earned"], 24 * date.today().month / 12)
        mon = date.today() + timedelta(days=(7 - date.today().weekday()) % 7 or 7)
        too_long = mon + timedelta(days=7 * 6)                    # 6 weeks of holiday is more than he has
        with self.assertRaises(ShopError):
            self.s.request_leave_kind(uid, mon.isoformat(), too_long.isoformat(), "Annual")
        self.s.request_leave_kind(uid, mon.isoformat(), too_long.isoformat(), "Sick")   # sick days use no balance
        with self.s.db() as conn:
            self.assertEqual(conn.execute("SELECT kind FROM staff_leave WHERE staff_id = ?", (sid,)).fetchone()[0], "Sick")
        with self.assertRaises(ShopError):
            self.s.save_contract(self.admin, sid, {"kind": "fixed", "start_day": "2026-01-01", "pay_amount": "100"})

    def test_income_tax_rules(self):
        ru, rw, inr = (self.s.income_tax_rule(c) for c in ("RU", "RW", "IN"))
        self.assertEqual(self.s.month_tax(ru, 100_000_00), (13_000_00, 0))
        # Russia counts from 1 January: above 2.4 million roubles a year the part above pays 15 %
        tax, _ = self.s.month_tax(ru, 400_000_00, ytd_gross=2_200_000_00, ytd_tax=286_000_00)
        self.assertEqual(tax, round((2_400_000 * 0.13 + 200_000 * 0.15 - 286_000) * 100))
        self.assertEqual(self.s.month_tax(rw, 300_000_00), (54_000_00, 18_900_00))
        self.assertEqual(self.s.month_tax(inr, 100_000_00)[0], 0)        # 12 lakh - 75,000 is under the rebate limit
        self.assertEqual(self.s.month_tax(inr, 150_000_00)[0], round(145_000 * 1.04 / 12 * 100))

    def test_payroll_needs_two_people_and_makes_payslips(self):
        russia = self.s.add_office(self.admin, self.head, "Russia office", "country", "RU", "Krasnoyarsk")
        sid, uid = self.worker(russia, "hub")
        self.contract(sid, 100000, start=first_of_last_month() - timedelta(days=40))
        fin, fin_uid = self.worker(self.india, "finance")
        month = first_of_last_month().strftime("%Y-%m")
        run = self.s.create_payroll(fin_uid, "RU", month)
        with self.assertRaises(ShopError):
            self.s.create_payroll(fin_uid, "RU", month)              # one payroll per country and month
        with self.assertRaises(ShopError):
            self.s.approve_payroll(fin_uid, run)                     # not by the person who made it
        with self.assertRaises(ShopError):
            self.s.pay_payroll(fin_uid, run)                         # not before it is approved
        self.s.approve_payroll(self.admin, run)
        self.s.pay_payroll(fin_uid, run)
        with self.assertRaises(ShopError):
            self.s.pay_payroll(fin_uid, run)                         # paid once
        slip = self.s.my_payslips(uid)[0]
        self.assertEqual((slip["gross"], slip["tax"], slip["net"]), (100_000_00, 13_000_00, 87_000_00))
        self.assertEqual(self.s.approvals("Waiting"), [])
        hub, hub_uid = self.worker(self.ambala, "hub")
        with self.assertRaises(ShopError):
            self.s.create_payroll(hub_uid, "IN", month)              # no ability "payroll.manage"

    def test_unpaid_leave_lowers_the_pay(self):
        sid, uid = self.worker(self.kigali, "hub")
        first = first_of_last_month()
        self.contract(sid, 300000, start=first - timedelta(days=60))
        with self.s.db() as conn:
            conn.execute("INSERT INTO staff_leave (staff_id, from_day, to_day, reason, status, kind, created_at) "
                         "VALUES (?, ?, ?, '', 'Approved', 'Unpaid', ?)",
                         (sid, (first + timedelta(days=2)).isoformat(), (first + timedelta(days=4)).isoformat(), now()))
        run = self.s.create_payroll(self.admin, "RW", first.strftime("%Y-%m"))
        slip = self.s.payroll_run(run)["slips"][0]
        days = (first.replace(month=first.month % 12 + 1, year=first.year + (first.month == 12)) - first).days
        self.assertEqual(slip["unpaid_days"], 3)
        self.assertEqual(slip["gross"], 300_000_00 - round(300_000_00 * 3 / days))
        self.assertIn("Unpaid time off", [x[0] for x in slip["line_list"]])

    def test_training_certificates_can_be_required(self):
        sid, uid = self.worker(self.ambala, "hub")
        self.assertTrue(self.s.can(uid, "hub.scan"))
        with self.s.db() as conn:
            conn.execute("UPDATE settings SET value = '1' WHERE key = 'require_training'")
        self.assertFalse(self.s.can(uid, "hub.scan"))                # no "Safety at the hub" yet
        self.assertTrue(self.s.can(uid, "board.use"))                # other abilities still work
        self.s.add_training(self.admin, sid, "hub_safety", (date.today() - timedelta(days=30 * 25)).isoformat())
        self.assertFalse(self.s.can(uid, "hub.scan"))                # 25 months ago: expired (24 months)
        self.s.add_training(self.admin, sid, "hub_safety", date.today().isoformat(), "HS-1")
        self.assertTrue(self.s.can(uid, "hub.scan"))
        self.assertEqual(self.s.training_report(self.admin), [])
        with self.assertRaises(ShopError):
            self.s.add_training(self.admin, sid, "hub_safety", (date.today() + timedelta(days=3)).isoformat())

    def test_helper_office_shifts(self):
        boss_rw, boss_uid = self.worker(self.rwanda, "office_manager", manager=True)
        sid, uid = self.worker(self.ambala, "hub")
        day = date.today() + timedelta(days=2)
        with self.assertRaises(ShopError):                           # not his worker, not helping there
            self.s.plan_shift(boss_uid, sid, day.isoformat(), "09:00", "17:00", office_id=self.kigali)
        self.s.assign_helper_office(self.admin, sid, self.kigali, day.isoformat(), (day + timedelta(days=6)).isoformat())
        self.s.plan_shift(boss_uid, sid, day.isoformat(), "09:00", "17:00", office_id=self.kigali)
        with self.assertRaises(ShopError):                           # outside the days he helps
            self.s.plan_shift(boss_uid, sid, (day + timedelta(days=9)).isoformat(), "09:00", "17:00",
                              office_id=self.kigali)
        with self.s.db() as conn:
            self.assertEqual(conn.execute("SELECT office_id FROM staff_rota WHERE staff_id = ?", (sid,)).fetchone()[0],
                             self.kigali)
        self.assertIn(sid, [h["id"] for h in self.s.helpers_in(boss_uid)])
        rota_ids = [r["worker"]["id"] for r in self.s.rota(boss_uid, day.isoformat(), 1)["rows"]]
        self.assertIn(sid, rota_ids)

    def test_clock_in_must_be_near_the_office(self):
        sid, uid = self.worker(self.ambala, "hub")
        self.s.set_office_location(self.admin, self.ambala, 30.3333, 76.8333, 300)
        with self.assertRaises(ShopError):
            self.s.clock_in_at(uid, 30.70, 76.80, 20)                # Chandigarh, about 40 km away
        self.assertEqual(self.s.clock_in_at(uid, 30.3340, 76.8335, 15), "")
        self.s.clock_out(uid)
        self.assertEqual(self.s.clock_in_at(uid, None, None), "no location")
        self.s.clock_out(uid)
        self.s.set_office_location(self.admin, self.ambala, 30.3333, 76.8333, 300, remote_ok=True)
        self.assertEqual(self.s.clock_in_at(uid, 30.70, 76.80, 20), "remote")
        with self.s.db() as conn:
            r = conn.execute("SELECT in_flag, in_dist_m FROM staff_shifts WHERE staff_id = ? AND end_at = ''",
                             (sid,)).fetchone()
        self.assertEqual(r["in_flag"], "remote")
        self.assertGreater(r["in_dist_m"], 30000)

    def test_dual_control_for_the_ability_all(self):
        x, x_uid = self.worker(self.india, "it")
        y, y_uid = self.worker(self.india, "it")
        with self.assertRaises(ShopError):
            self.s.set_dual_control(self.admin, True)               # nobody else has "all" yet
        base = {"office_id": self.india, "title": "IT", "kind": "office"}
        self.s.update_worker(self.admin, x, {**base, "abilities": ["all"]})
        self.s.set_dual_control(self.admin, True)
        self.assertEqual(self.s.update_worker(self.admin, y, {**base, "abilities": ["all", "monitor.view"]}), "waiting")
        self.assertNotIn("all", self.s.staff_member(y)["ability_set"])
        self.assertIn("monitor.view", self.s.staff_member(y)["ability_set"])
        a = self.s.approvals("Waiting")[0]
        with self.assertRaises(ShopError):
            self.s.decide_approval(self.admin, a["id"], True)      # the person who asked cannot decide
        self.s.decide_approval(x_uid, a["id"], True)
        self.assertIn("all", self.s.staff_member(y)["ability_set"])


class TestMoney(unittest.TestCase):
    def setUp(self):
        self.s = support.make_store(seed=True)
        self.uid = self.s.register("Test User", "tax.user@realmail.local", "9811111111", "Secret123")
        self.addr = self.s.add_address(self.uid, ADDR)
        self.admin = self.s.register("Admin Two", "admin.two@realmail.local", "9800000002", "Secret123", wallet=0)
        self.s.make_admin(self.admin)

    def buy(self, cart, payment="cod"):
        self.s.save_cart(self.uid, cart)
        number = self.s.place_order(self.uid, self.addr, payment)
        with self.s.db() as conn:
            return dict(conn.execute("SELECT * FROM orders WHERE number = ?", (number,)).fetchone())

    def own_products(self):
        with self.s.db() as conn:
            rows = [dict(r) for r in conn.execute("SELECT * FROM products WHERE seller_id IS NULL AND active = 1 "
                                                  "ORDER BY price, id")]
        by = {}
        for r in rows:
            by.setdefault(r["category"], r)
        return by

    def test_gstin_and_inn_check_digits(self):
        g = "27AAAAA0000A1Z"
        good = g + invoices.gstin_check_char(g)
        self.assertTrue(invoices.gstin_ok(good))
        bad = good[:-1] + ("0" if good[-1] != "0" else "1")
        self.assertFalse(invoices.gstin_ok(bad))
        self.assertFalse(invoices.gstin_ok("27AAAAA0000A1X5"))
        self.assertTrue(invoices.inn_ok("7707083893"))
        self.assertFalse(invoices.inn_ok("7707083894"))
        self.assertTrue(invoices.inn_ok("500100732259"))
        self.assertFalse(invoices.inn_ok("500100732258"))
        self.assertEqual(invoices.financial_year(date(2026, 11, 7)), "2026-27")
        self.assertEqual(invoices.financial_year(date(2027, 3, 31)), "2026-27")

    def test_invoices_by_kind_of_goods_and_gapless_numbers(self):
        p = self.own_products()
        cart = {str(p["electronics"]["id"]): 1, str(p["books"]["id"]): 1, str(p["grocery"]["id"]): 2}
        o = self.buy(cart)
        invs = self.s.issue_invoices(o["id"])
        self.assertEqual(len(invs), 1)
        inv = invs[0]
        fy = invoices.financial_year(date.today())
        self.assertRegex(inv["number"], rf"^YO/{fy}/\d{{6}}$")
        rates = {ln["category"]: ln["rate"] for ln in inv["line_list"]}
        self.assertEqual((rates["electronics"], rates["books"], rates["grocery"]), (18, 0, 5))
        self.assertEqual(inv["total"], sum(ln["total"] for ln in inv["line_list"]))
        self.assertEqual(inv["taxable"] + inv["tax"], inv["total"])
        self.assertEqual([i["id"] for i in self.s.issue_invoices(o["id"])], [inv["id"]])     # made once
        o2 = self.buy({str(p["books"]["id"]): 1})
        n1, n2 = int(inv["number"][-6:]), int(self.s.issue_invoices(o2["id"])[0]["number"][-6:])
        self.assertEqual(n2, n1 + 1)                                                          # no gaps

    def test_clothes_rate_depends_on_the_price(self):
        self.assertEqual(self.s.rate_for("IN", "fashion", 2499_00), 5)
        self.assertEqual(self.s.rate_for("IN", "fashion", 2501_00), 18)
        self.assertEqual(invoices.split_inclusive(118_00, 18), (100_00, 18_00))

    def test_seller_invoice_and_credit_note(self):
        import demo_data
        owner = self.s.find_user(demo_data.SELLER_EMAIL)["id"]
        shop = self.s.seller_of(owner)
        with self.s.db() as conn:                       # the demo gives this seller a GSTIN: start without one
            conn.execute("UPDATE sellers SET tax_id = '', tax_registered = 0 WHERE id = ?", (shop["id"],))
            prod = dict(conn.execute("SELECT * FROM products WHERE seller_id = ? AND active = 1 AND approved = 1 "
                                     "AND stock > 0", (shop["id"],)).fetchone())
        o = self.buy({str(prod["id"]): 1})
        inv = next(i for i in self.s.issue_invoices(o["id"]) if i["seller_id"] == shop["id"])
        self.assertEqual((inv["kind"], inv["tax"]), ("Bill of supply", 0))    # a seller who is not registered
        with self.assertRaises(ShopError):
            self.s.save_seller_tax_id(owner, "27AAAAA0000A1Z0" if invoices.gstin_check_char("27AAAAA0000A1Z") != "0"
                                      else "27AAAAA0000A1Z1")
        g = "07ABCDE1234F1Z"
        self.s.save_seller_tax_id(owner, g + invoices.gstin_check_char(g))
        o2 = self.buy({str(prod["id"]): 1})
        inv2 = next(i for i in self.s.issue_invoices(o2["id"]) if i["seller_id"] == shop["id"])
        self.assertEqual(inv2["kind"], "Tax invoice")
        self.assertGreater(inv2["tax"], 0)
        with self.s.db() as conn:
            rid = conn.execute("INSERT INTO returns (order_id, user_id, reason, details, status, amount, refund_to, "
                               "created_at) VALUES (?, ?, 'Damaged or broken', '', 'Requested', ?, 'Yo Pay', ?)",
                               (o2["id"], self.uid, prod["price"], now())).lastrowid
        self.s.decide_return(rid, True, self.admin)
        cn = [i for i in self.s.invoices_of_order(o2["id"]) if i["kind"] == "Credit note"]
        self.assertEqual(len(cn), 1)
        self.assertEqual(cn[0]["of_invoice"], inv2["number"])
        self.assertEqual(cn[0]["total"], -prod["price"])

    def test_ubl_e_invoice(self):
        p = self.own_products()
        o = self.buy({str(p["electronics"]["id"]): 1})
        inv = self.s.issue_invoices(o["id"])[0]
        root = ET.fromstring(self.s.invoice_ubl(inv["id"]).split("?>", 1)[1])
        ns = {"cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
              "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"}
        self.assertTrue(root.tag.endswith("}Invoice"))
        self.assertEqual(root.find("cbc:ID", ns).text, inv["number"])
        self.assertEqual(root.find("cac:TaxTotal/cbc:TaxAmount", ns).text, f"{inv['tax'] / 100:.2f}")
        self.assertEqual(root.find("cac:LegalMonetaryTotal/cbc:PayableAmount", ns).text, f"{inv['total'] / 100:.2f}")
        self.assertEqual(len(root.findall("cac:InvoiceLine", ns)), len(inv["line_list"]))

    def test_signed_payment_callbacks_are_used_once(self):
        s = support.make_store(seed=False, env={"MOMO_WEBHOOK_SECRET": "test-secret-123"})
        uid = s.register("Amani Test", "amani.cb@realmail.local", "0788111222", "Secret123", wallet=0, country="RW")
        ref = s.start_mobile_payment(uid, "momo", "500", "0788111222")
        body = json.dumps({"event_id": "ev-1", "type": "payment.succeeded", "ref": ref, "amount": "500.00",
                           "currency": "INR"}).encode()
        self.assertEqual(s.payment_callback("momo", "t=1,v1=00", body)[0], 401)
        old = providers.sign("test-secret-123", body, time.time() - 3600)
        self.assertEqual(s.payment_callback("momo", old, body)[0], 401)      # too old (replay)
        self.assertEqual(s.payment_callback("mpesa", providers.sign("x", body), body)[0], 503)  # no secret set
        self.assertEqual(s.payment_callback("momo", providers.sign("test-secret-123", body), body), (200, "paid"))
        self.assertEqual(s.payment_callback("momo", providers.sign("test-secret-123", body), body),
                         (200, "already done"))
        self.assertEqual(s.get_user(uid)["wallet"], 500_00)

    def test_reconciliation_of_a_statement(self):
        uid = self.s.register("Amani Rec", "amani.rec@realmail.local", "0788111333", "Secret123", wallet=0, country="RW")
        refs = []
        for amount in ("500", "300"):
            ref = self.s.start_mobile_payment(uid, "momo", amount, "0788111333")
            self.s.answer_mobile_payment(uid, ref, True)
            refs.append(ref)
        day = date.today().isoformat()
        csv_text = (f"date,reference,amount,currency,status\n{day},{refs[0]},500.00,INR,settled\n"
                    f"{day},{refs[1]},299.00,INR,settled\n{day},UNKNOWN1,50.00,INR,settled\n")
        with self.assertRaises(ShopError):
            self.s.reconcile(self.uid, "momo", csv_text)                    # a customer cannot
        r = self.s.reconcile(self.admin, "momo", csv_text)
        self.assertEqual((len(r["matched"]), len(r["different"]), len(r["missing_ours"]), len(r["missing_theirs"])),
                         (1, 1, 1, 0))
        self.assertEqual(self.s.reconciliations()[0]["different"], 1)


class Couriers(unittest.TestCase):
    def setUp(self):
        self.s = support.make_store(seed=False, notifier=support.fake_notifier(email=False, sms=False))
        self.s.load_catalog()
        self.admin = self.s.register("Admin Three", "admin.three@realmail.local", "9800000003", "Secret123", wallet=0)
        self.s.make_admin(self.admin)
        self.uid = self.s.register("Sender", "sender.v7@realmail.local", "9811111111", "Secret123")
        with self.s.db() as conn:
            self.s.wallet_move(conn, self.uid, 50_000_00, "topup", "T", "test")

    def book(self, **kw):
        f = {"from_name": "Sender", "from_mobile": "9811111111", "from_address": "12 Main Road, Sector 9",
             "from_city": "Ambala Cantt", "payment": "wallet", "service": "standard", "to_country": "IN",
             "kind": "parcel", "weight": "1", "contents": "A phone charger", "to_name": "Neha Verma",
             "to_mobile": "9812000003", "to_address": "House 5, Model Town", "to_city": "Chandigarh", **kw}
        return self.s.book_shipment(self.uid, f)

    def row(self, number):
        with self.s.db() as conn:
            return dict(conn.execute("SELECT * FROM shipments WHERE number = ?", (number,)).fetchone())


class TestCouriers(Couriers):
    def partner_call(self, key_id, secret, events):
        body = json.dumps({"events": events}).encode()
        p = self.s.partner_auth(key_id, providers.sign(secret, body), body)
        return p, (self.s.partner_events_in(p, json.loads(body)) if p else None)

    def test_partner_api(self):
        key_id, secret = self.s.add_partner(self.admin, "Highland Express", "RW KE")
        self.assertNotIn(secret, json.dumps(self.s.partners()))                 # never shown again
        number = self.book(time_window="12-15")
        self.s.hand_to_partner(self.admin, number, self.s.partners()[0]["id"])
        at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        p, res = self.partner_call(key_id, secret, [{"event_id": "e1", "number": number, "type": "picked_up", "at": at}])
        self.assertEqual(res[0]["result"], "moved")
        self.assertEqual(self.row(number)["status"], C.PICKED)
        _, res = self.partner_call(key_id, secret, [{"event_id": "e1", "number": number, "type": "picked_up", "at": at},
                                                    {"event_id": "e2", "number": "YO00000000", "type": "delivered",
                                                     "at": at}])
        self.assertEqual([r["result"] for r in res], ["already done", "not your parcel"])
        _, res = self.partner_call(key_id, secret, [{"event_id": "e3", "number": number, "type": "delivered", "at": at,
                                                     "receiver": "Neha"}])
        r = self.row(number)
        self.assertEqual((r["status"], r["pod_name"], r["pod_by"]), (C.DELIVERED, "Neha", "partner:Highland Express"))
        body = json.dumps({"events": []}).encode()
        self.assertIsNone(self.s.partner_auth(key_id, providers.sign("wrong", body), body))
        self.assertEqual(self.s.partner_shipments(p), [])                       # finished parcels are not listed
        self.assertIn("/api/partner/v1/events", partners.openapi()["paths"])

    def test_partner_api_over_http(self):
        app = support.make_app(seed=False)
        st = app.store
        admin = st.register("Admin Http", "admin.http@realmail.local", "9800000009", "Secret123", wallet=0)
        st.make_admin(admin)
        key_id, secret = st.add_partner(admin, "Http Partner")
        c = app.test_client()
        body = json.dumps({"events": [{"event_id": "x", "number": "YO1", "type": "picked_up",
                                       "at": "2026-11-07T10:00:00Z"}]}).encode()
        r = c.post("/api/partner/v1/events", data=body, content_type="application/json")
        self.assertEqual(r.status_code, 401)
        r = c.post("/api/partner/v1/events", data=body, content_type="application/json",
                   headers={"X-Yo-Key": key_id, "X-Yo-Signature": providers.sign(secret, body)})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["results"][0]["result"], "not your parcel")
        self.assertIn("paths", c.get("/api/partner/openapi.json").get_json())

    def test_time_windows_change_the_route(self):
        near = {"id": "a", "kind": "drop", "needs": None, "point": (30.40, 76.78), "window": (1080, 1260)}
        far = {"id": "b", "kind": "drop", "needs": None, "point": (30.50, 76.80), "window": (540, 600)}
        self.assertEqual([s["id"] for s in routeplan.plan((30.37, 76.78), [near, far])], ["a", "b"])  # no times
        order = routeplan.plan((30.37, 76.78), [near, far], t0=540)            # 9:00, b closes at 10:00
        self.assertEqual([s["id"] for s in order], ["b", "a"])
        times, late = routeplan.timeline((30.37, 76.78), order, 540)
        self.assertEqual(late, 0)
        self.assertGreaterEqual(times[1][0], 1080)                             # waits until 18:00 for a
        number = self.book(time_window="18-21")
        self.assertEqual(self.row(number)["time_window"], "18-21")

    def test_signature_proof_of_delivery(self):
        from PIL import Image, ImageDraw
        empty = io.BytesIO()
        Image.new("RGBA", (600, 200), (0, 0, 0, 0)).save(empty, "PNG")
        import base64
        url = lambda b: "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()   # noqa: E731
        with self.assertRaises(ShopError):
            courierplus.signature_png(url(empty))                                 # nothing drawn
        with self.assertRaises(ShopError):
            courierplus.signature_png("data:image/jpeg;base64,AAAA")
        drawn = io.BytesIO()
        im = Image.new("RGBA", (600, 200), (0, 0, 0, 0))
        ImageDraw.Draw(im).line([(30, 150), (200, 40), (380, 160), (560, 50)], fill=(0, 0, 0, 255), width=6)
        im.save(drawn, "PNG")
        self.assertTrue(courierplus.signature_png(url(drawn)).startswith(b"\x89PNG"))
        number = self.book()
        cid = self.s.apply_courier(self.admin, {"vehicle": "motorbike", "city": "Ambala Cantt",
                                                "vehicle_no": "HR01AB1234", "agree": "1"})
        with self.s.db() as conn:
            conn.execute("UPDATE shipments SET status = ?, courier_id = ? WHERE number = ?", (C.DELIVERED, cid, number))
        with self.assertRaises(ShopError):
            self.s.save_pod_signature(cid + 1, number, url(drawn))               # not his parcel
        self.s.save_pod_signature(cid, number, url(drawn))
        with self.assertRaises(ShopError):
            self.s.save_pod_signature(cid, number, url(drawn))                   # once
        self.assertTrue(self.s.media(self.row(number)["pod_signature"]))

    def test_hs_codes_and_advance_customs_data(self):
        q = self.s.courier_quote({"kind": "parcel", "weight": "1", "from_city": "Ambala Cantt", "to_city": "Kigali",
                                  "to_country": "RW"})
        with self.assertRaises(ShopError):
            self.s.customs_declaration({"customs_reason": "gift", "customs_desc": ["Shirt"], "customs_qty": ["1"],
                                        "customs_value": ["700"], "customs_hs": ["61"]}, q)
        d = self.s.customs_declaration({"customs_reason": "gift", "customs_desc": ["Shirt"], "customs_qty": ["1"],
                                        "customs_value": ["700"], "customs_hs": ["6109.10"]}, q)
        self.assertEqual(d["items"][0]["hs"], "610910")
        number = self.book(to_city="Kigali", to_country="RW", to_mobile="+250788123456", customs_reason="gift",
                           customs_desc=["Shirt"], customs_qty=["1"], customs_value=["700"], customs_hs=["610910"])
        ead = self.s.advance_customs_data(number)
        self.assertEqual(ead["receiver"]["country"], "RW")
        self.assertEqual(ead["items"][0]["hs_code"], "610910")
        self.assertEqual(ead["total_value"], 700)
        self.assertIsNone(self.s.advance_customs_data(self.book()))            # a parcel inside India has none
        self.assertEqual(courierplus.assetlinks({}), [])
        self.assertEqual(courierplus.assetlinks({"ANDROID_APP_ID": "app.yo.courier", "ANDROID_APP_SHA256": "AA:BB"})
                         [0]["target"]["package_name"], "app.yo.courier")


class TestPlacesAndTravel(unittest.TestCase):
    def test_postal_code_directory(self):
        r = addresscheck.check("RU", "660049")
        self.assertIn("Красноярск", r["hint"])
        import geo
        moscow = geo.search("Moscow", "RU", limit=1)[0]
        r = addresscheck.check("RU", "660049", moscow["id"])
        self.assertTrue(any("660049" in w for w in r["warnings"]))
        self.assertTrue(addresscheck.check("DE", "99999")["warnings"])          # not in the directory
        self.assertEqual(addresscheck.check("DE", "10115")["warnings"], [])
        self.assertIn("Westminster", addresscheck.check("GB", "SW1A 1AA")["hint"])

    def test_gtfs_realtime_delays_and_cancellations(self):
        s = support.make_store(seed=False, notifier=support.fake_notifier(email=False, sms=False),
                               env={"YO_TICKET_FILL": "0"})
        admin = s.register("Admin Four", "admin.four@realmail.local", "9800000004", "Secret123", wallet=0)
        s.make_admin(admin)
        s.ensure_ticket_operators()
        res = s.import_gtfs(admin, read_bytes(SAMPLE_GTFS), "Sample Highland Coaches")
        feed_id = res["feed_id"] if isinstance(res, dict) and "feed_id" in res else s.gtfs_feeds()[0]["id"]
        with zipfile.ZipFile(SAMPLE_GTFS) as z:
            trip_id = z.read("trips.txt").decode().splitlines()[1].split(",")
        with s.db() as conn:
            svc = dict(conn.execute("SELECT * FROM ticket_services WHERE code LIKE ? ORDER BY id LIMIT 1",
                                    (f"GT{feed_id}-%",)).fetchone())
        tid = svc["code"].split("-", 1)[1].rsplit("-", 2)[0]
        day = (date.today() + timedelta(days=3)).isoformat()
        with s.db() as conn:
            trip = s._trip(conn, svc, day)
        blob = gtfsrt.sample_feed([(tid, day.replace("-", ""), 900)])
        done = s.apply_gtfs_rt(feed_id, blob)
        self.assertGreaterEqual(done["delayed"], 1)
        with s.db() as conn:
            self.assertEqual(conn.execute("SELECT delay_min FROM ticket_trips WHERE id = ?", (trip["id"],)).fetchone()[0],
                             15)
        self.assertEqual(s.apply_gtfs_rt(feed_id, blob)["delayed"], 0)        # the same delay: no new message
        s.apply_gtfs_rt(feed_id, gtfsrt.sample_feed([(tid, day.replace("-", ""), None)]))
        with s.db() as conn:
            self.assertEqual(conn.execute("SELECT status FROM ticket_trips WHERE id = ?", (trip["id"],)).fetchone()[0],
                             "Cancelled")
        with self.assertRaises(ShopError):
            s.apply_gtfs_rt(feed_id, b"not a feed")
        self.assertTrue(trip_id)


class TestTrust(Base):
    def test_more_chat_examples(self):
        import chat_seed7
        import chatmodel
        self.assertGreaterEqual(len(self.s.chat_examples()), 200)
        langs = sum(1 for t in chat_seed7.BAD if any(ord(ch) > 0x3000 for ch in t))       # Chinese
        self.assertGreater(langs, 3)
        ex = [(t, "bad") for t in chatmodel.SEED_BAD + chat_seed7.BAD] + \
             [(t, "ok") for t in chatmodel.SEED_OK + chat_seed7.OK]
        report = chatmodel.evaluate(ex)
        self.assertGreaterEqual(report["caught"], 70)
        self.assertGreater(self.s.chat_bad_probability("переведите предоплату мне на карту"), 0.5)

    def test_outside_moderation_service(self):
        import urllib.request
        s = support.make_store(seed=False, env={"MODERATION_URL": "https://moderation.invalid/v1",
                                                "MODERATION_KEY": "k"})

        class Answer:
            def __init__(self, flagged):
                self.data = json.dumps({"results": [{"flagged": flagged}]}).encode()

            def read(self, n=-1):
                return self.data

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        real = urllib.request.urlopen
        try:
            urllib.request.urlopen = lambda req, timeout=2: Answer(True)
            self.assertTrue(s.external_moderation("anything"))
            urllib.request.urlopen = lambda req, timeout=2: Answer(False)
            self.assertFalse(s.external_moderation("anything"))

            def down(req, timeout=2):
                raise OSError("down")
            urllib.request.urlopen = down
            self.assertIsNone(s.external_moderation("anything"))             # the shop goes on
        finally:
            urllib.request.urlopen = real
        self.assertIsNone(self.s.external_moderation("anything"))            # not set up

    def frames(self):
        from PIL import Image
        img = Image.open(FACE).convert("RGB")
        W, H = img.size

        def crop(cx, cy, scale):
            w, h = int(W * scale), int(H * scale)
            x0, y0 = int(cx * W - w / 2), int(cy * H - h / 2)
            return img.crop((x0, y0, x0 + w, y0 + h)).resize((480, 480))
        return crop(0.5, 0.5, 0.9), crop(0.5, 0.45, 0.65), crop(0.42, 0.5, 0.9)

    def test_live_check(self):
        base, closer, left = self.frames()
        self.assertTrue(idcheck.liveness([base, closer, left], ["closer", "left"])["ok"])
        self.assertFalse(idcheck.liveness([base, base, base], ["closer", "left"])["ok"])       # a still photo
        self.assertFalse(idcheck.liveness([base, closer, left], ["closer", "right"])["ok"])    # wrong way
        steps = idcheck.new_live_steps()
        self.assertEqual(len(set(steps)), 2)

    def test_id_check_with_live_photos(self):
        def jpg(im):
            b = io.BytesIO()
            im.save(b, "JPEG")
            return b.getvalue()
        sid, uid = self.worker(self.ambala, "hub")
        base, closer, left = self.frames()
        face = read_bytes(FACE)
        cid = self.s.start_id_check(uid, "staff", "id_card", "IN", face, face, True,
                                    live_frames=[jpg(base), jpg(closer), jpg(left)], live_steps=["closer", "left"])
        r = next(c for c in self.s.id_checks(user_id=uid) if c["id"] == cid)
        self.assertEqual(r["live_ok"], 1)
        self.assertIn("Live check passed", r["hint"])


class TestData(Base):
    def test_holt_winters_beats_last_week(self):
        week = [120, 110, 100, 105, 115, 80, 55]
        y = [week[i % 7] * (1 + 0.01 * i) for i in range(56)]
        params, fc = analytics.fit_holt_winters(y, 14)
        self.assertEqual(len(fc), 14)
        self.assertLess(abs(fc[0] - week[56 % 7] * 1.56) / (week[0] * 1.56), 0.1)
        bt = analytics.backtest(y, 14)
        self.assertLess(bt["model"], bt["naive"])
        with self.assertRaises(ValueError):
            analytics.holt_winters([1, 2, 3], 0.3, 0.1, 0.1)

    def test_warehouse_counts_orders(self):
        s = support.make_store(seed=True)       # (on PostgreSQL this empties the database of self.s)
        s.build_warehouse()
        admin = s.find_user("admin@example.com")
        a = s.analytics(admin["id"] if admin else self.admin, 90)
        self.assertGreater(a["totals"].get("orders", 0), 0)

    def test_branch_sees_only_its_numbers(self):
        boss, boss_uid = self.worker(self.rwanda, "country_manager", manager=True)
        self.s.seed_demo_history()
        mine = self.s.analytics(boss_uid, 28)
        self.assertEqual(mine["scope"], "RW")
        self.assertIn("hub_parcels", mine["totals"])
        hub_place = self.s.office(self.ambala)["place"]
        with self.s.db() as conn:
            india = conn.execute("SELECT COUNT(*) FROM fact_daily WHERE place = ?", (hub_place,)).fetchone()[0]
        self.assertGreater(india, 0)
        csv_text = self.s.analytics_csv(boss_uid)
        self.assertTrue(csv_text.startswith("day,orders"))

    def test_hub_forecast(self):
        self.s.seed_demo_history()
        fc = self.s.hub_forecast(self.admin, self.ambala)
        self.assertTrue(fc["ready"])
        self.assertEqual(len(fc["forecast"]), 14)
        self.assertTrue(all(r["workers"] >= 1 for r in fc["forecast"]))
        boss, boss_uid = self.worker(self.rwanda, "country_manager", manager=True)
        with self.assertRaises(ShopError):
            self.s.hub_forecast(boss_uid, self.ambala)                          # not his branch

    def test_ab_test(self):
        self.assertEqual(analytics.variant_of("t", "user-1"), analytics.variant_of("t", "user-1"))
        groups = {analytics.variant_of("t", f"u{i}") for i in range(50)}
        self.assertEqual(groups, {"A", "B"})
        z, p = analytics.two_proportion_z(40, 200, 10, 200)
        self.assertLess(p, 0.05)
        self.assertGreater(z, 0)
        self.assertIn("Not enough", self.s.ab_results("similar_products")["verdict"])
        with self.s.db() as conn:
            for v, clicks in (("A", 40), ("B", 10)):
                for i in range(200):
                    conn.execute("INSERT INTO exp_events (key, unit, variant, kind, at) VALUES ('similar_products', ?, "
                                 "?, 'view', ?)", (f"{v}{i}", v, now()))
                for i in range(clicks):
                    conn.execute("INSERT INTO exp_events (key, unit, variant, kind, at) VALUES ('similar_products', ?, "
                                 "?, 'click', ?)", (f"{v}{i}", v, now()))
        r = self.s.ab_results("similar_products")
        self.assertTrue(r["enough"])
        self.assertIn("Group A is better", r["verdict"])

    def test_ab_view_on_the_product_page(self):
        app = support.make_app(seed=True)
        c = app.test_client()
        st = app.store
        slug = st.products()[0].slug
        st.build_content_similar()
        c.get(f"/p/{slug}")
        c.get(f"/p/{slug}?rec=A")
        with st.db() as conn:
            kinds = {r[0] for r in conn.execute("SELECT kind FROM exp_events")}
        self.assertIn("click", kinds)


class TestSupport(Couriers):
    def test_help_desk(self):
        t0 = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
        number = self.s.open_support_ticket(self.uid, "parcel", "Where is my parcel?", "It is late, please check.",
                                            "YO123", when=t0)
        self.assertEqual(self.s.support_numbers()["late"], 1)
        agent = self.s.register("Agent", "agent.v7@realmail.local", "9800000077", "Secret123", wallet=0)
        with self.assertRaises(ShopError):
            self.s.support_reply(agent, number, "Hi")                          # not a support worker
        self.s.make_admin(agent)
        self.s.support_reply(agent, number, "It arrives tomorrow.")
        t = self.s.support_ticket(number, self.uid)
        self.assertEqual((t["status"], len(t["messages"])), ("Answered", 2))
        self.assertTrue(t["first_answer_at"])
        self.assertEqual(self.s.support_numbers()["late"], 0)
        self.assertIsNone(self.s.support_ticket(number, agent + 999))          # another customer cannot see it
        self.s.support_reply(self.uid, number, "", close=True)
        self.assertEqual(self.s.support_ticket(number)["status"], "Closed")

    def test_parcel_claims(self):
        number = self.book(declared="1000")
        with self.assertRaises(ShopError):
            self.s.file_claim(self.uid, number, "damaged", "The screen is broken.", "500")   # not delivered yet
        delivered = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        with self.s.db() as conn:
            conn.execute("UPDATE shipments SET status = ?, delivered_at = ? WHERE number = ?",
                         (C.DELIVERED, delivered, number))
        with self.assertRaises(ShopError):
            self.s.file_claim(self.uid, number, "damaged", "The screen is broken.", "5000")  # more than the cover
        cid = self.s.file_claim(self.uid, number, "damaged", "The screen is broken.", "800")
        with self.assertRaises(ShopError):
            self.s.file_claim(self.uid, number, "damaged", "Again the same story.", "100")   # one claim
        before = self.s.get_user(self.uid)["wallet"]
        self.assertEqual(self.s.decide_claim(self.admin, cid, True), 800_00)
        with self.assertRaises(ShopError):
            self.s.decide_claim(self.admin, cid, True)                         # paid once
        self.assertEqual(self.s.get_user(self.uid)["wallet"], before + 800_00)

    def test_safety_check_of_orders(self):
        s = support.make_store(seed=True)
        shared = "9877700001"
        p = s.get_product(slug="nova-x5-blue")
        for i in range(3):
            u = s.register(f"Other {i}", f"other{i}@realmail.local", f"98777000{i + 10}", "Secret123")
            a = s.add_address(u, dict(ADDR, mobile=shared))
            s.save_cart(u, {str(p.id): 1})
            s.place_order(u, a, "cod")
        big = max((x for x in s.products() if x.price <= 25000_00 and x.stock >= 6), key=lambda x: x.price)
        plain = s.register("Plain Buyer", "plain.buyer@realmail.local", "9877700088", "Secret123")
        s.save_cart(plain, {str(big.id): -(-20000_00 // big.price)})
        first = s.place_order(plain, s.add_address(plain, dict(ADDR, mobile="9877700088")), "cod")
        with s.db() as conn:
            o = dict(conn.execute("SELECT * FROM orders WHERE number = ?", (first,)).fetchone())
        self.assertEqual((o["risk_score"], o["risk_status"]), (60, ""))      # a first big cash order alone: not held
        new = s.register("New Buyer", "new.buyer@realmail.local", "9877700099", "Secret123")
        a = s.add_address(new, dict(ADDR, mobile=shared))
        s.save_cart(new, {str(big.id): -(-20000_00 // big.price)})
        number = s.place_order(new, a, "cod")
        with s.db() as conn:
            o = dict(conn.execute("SELECT * FROM orders WHERE number = ?", (number,)).fetchone())
        self.assertEqual(o["risk_score"], 80)                                  # + the same phone on 3 other accounts
        self.assertEqual((o["risk_status"], s.status(o)), ("Review", "Safety check"))
        admin = s.find_user("admin@example.com")["id"]
        queue = [r["number"] for r in s.risk_queue()]
        self.assertIn(number, queue)
        self.assertNotIn(first, queue)
        s.decide_risk(admin, number, True)
        with s.db() as conn:
            o = dict(conn.execute("SELECT * FROM orders WHERE number = ?", (number,)).fetchone())
        self.assertEqual(o["risk_status"], "Released")
        self.assertNotEqual(s.status(o), "Safety check")
        with self.assertRaises(ShopError):
            s.decide_risk(admin, number, False)                                 # decided already


class TestScaleAndPrivacy(Base):
    def test_one_claim_per_day_for_all_machines(self):
        self.assertTrue(self.s.claim_period("test_job", "2026-11-07"))
        self.assertFalse(self.s.claim_period("test_job", "2026-11-07"))
        self.assertTrue(self.s.claim_period("test_job", "2026-11-08"))

    def test_redis_layer_of_the_cache(self):
        class FakeRedis:
            def __init__(self):
                self.d, self.down = {}, False

            def get(self, k):
                if self.down:
                    raise ConnectionError("down")
                return self.d.get(k)

            def setex(self, k, ttl, v):
                if self.down:
                    raise ConnectionError("down")
                self.d[k] = v.encode()
        r = FakeRedis()
        self.s._redis = r
        self.assertEqual(self.s.cache_backend(), "redis")
        calls = []
        self.assertEqual(self.s.cached("t7", "k", 60, lambda: calls.append(1) or {"v": 1}), {"v": 1})
        self.assertTrue(any(k.startswith("yo:t7:") for k in r.d))
        import cache
        cache._LOCAL.clear()
        self.assertEqual(self.s.cached("t7", "k", 60, lambda: calls.append(1) or {"v": 2}), {"v": 1})   # from Redis
        self.assertEqual(len(calls), 1)
        r.down = True
        cache._LOCAL.clear()
        self.assertEqual(self.s.cached("t7", "k2", 60, lambda: {"v": 3}), {"v": 3})   # Redis down: the table
        self.s._redis = None

    def test_privacy_register_and_keeping_times(self):
        self.assertGreaterEqual(len(self.s.privacy_register()), 10)
        old = (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d %H:%M:%S")
        with self.s.db() as conn:
            conn.execute("INSERT INTO notifications (user_id, channel, subject, body, status, created_at) VALUES "
                         "(?, 'site', 'Old', 'Old', 'Delivered', ?)", (self.admin, old))
            conn.execute("INSERT INTO request_stats (minute, requests) VALUES (?, 1)", (old[:16],))
        done = self.s.apply_retention()
        self.assertGreaterEqual(done["notifications"], 1)
        self.assertGreaterEqual(done["request_stats"], 1)
        self.s.export_my_data(self.admin)
        self.assertEqual(self.s.privacy_requests()[0]["kind"], "export")

    def test_backup_has_the_new_tables(self):
        data = self.s.export_data()
        self.assertEqual(data["version"], 7)
        for t in ("payslips", "invoices", "partners", "support_tickets", "fact_daily"):
            self.assertIn(t, data["tables"])


class TestDemo(unittest.TestCase):
    def test_demo_content_of_version_7_once(self):
        import demo_data
        s = support.make_store(seed=True)

        def counts():
            with s.db() as conn:
                return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]       # noqa: S608
                        for t in ("staff_contracts", "staff_trainings", "payroll_runs", "payslips", "partners",
                                  "partner_events", "invoices", "support_tickets", "parcel_claims", "staff_offices")}
        first = counts()
        self.assertEqual((first["staff_contracts"], first["payroll_runs"], first["partners"], first["support_tickets"],
                          first["parcel_claims"], first["staff_offices"]), (13, 2, 1, 2, 1, 1))
        self.assertGreater(first["invoices"], 10)
        self.assertEqual([r["status"] for r in s.payroll_runs()], ["Draft", "Paid"])
        self.assertEqual(len(s.risk_queue()), 1)
        self.assertEqual(s.approvals("Waiting")[0]["kind"], "payroll")
        self.assertTrue(s.hub_forecast(s.find_user("admin@example.com")["id"],
                                       next(o["id"] for o in s.offices() if o["name"] == "Ambala hub"))["ready"])
        demo_data.seed(s)                                                    # again: nothing new
        self.assertEqual(counts(), first)


class TestPages(unittest.TestCase):
    def setUp(self):
        self.app = support.make_app(seed=True)
        self.c = self.app.test_client()

    def test_security_headers_and_skip_link(self):
        r = self.c.get("/")
        self.assertIn("camera=(self)", r.headers.get("Permissions-Policy", ""))
        self.assertEqual(r.headers.get("Cross-Origin-Opener-Policy"), "same-origin")
        self.assertNotIn("Strict-Transport-Security", r.headers)
        page = r.get_data(as_text=True)
        self.assertIn('class="skip-link" href="#main"', page)
        self.assertIn('id="main"', page)
        r = self.c.get("/", headers={"X-Forwarded-Proto": "https"})
        self.assertIn("max-age", r.headers.get("Strict-Transport-Security", ""))
        self.assertEqual(self.c.get("/.well-known/assetlinks.json").get_json(), [])

    def test_version_7_pages(self):
        support.login(self.c, "admin@example.com", "Admin@1234")
        for url in ("/admin/payroll", "/admin/approvals", "/admin/invoices", "/admin/tax-categories",
                    "/admin/payments/reconcile", "/admin/partners", "/admin/support", "/admin/claims", "/admin/risk",
                    "/admin/analytics", "/admin/forecast", "/admin/experiments", "/admin/privacy",
                    "/admin/staff/training", "/partners/api", "/support"):
            self.assertEqual(self.c.get(url).status_code, 200, url)
        self.assertEqual(self.c.get("/admin/analytics.csv").status_code, 200)

    def test_worker_without_ability_is_refused(self):
        st = self.app.store
        hub = st.find_user("sunil.kumar@example.com")
        if hub is None:
            self.skipTest("no demo staff")
        support.login(self.c, "sunil.kumar@example.com", "Staff@2026")
        self.assertEqual(self.c.get("/admin/payroll").status_code, 403)
        self.assertEqual(self.c.get("/staff/me").status_code, 200)


if __name__ == "__main__":
    unittest.main()
