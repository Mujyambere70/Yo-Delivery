"""Tests for version 6: the people who run Yo Delivery (office tree, workers, managers, abilities, the guard of the
admin pages, the activity log, shift plan and attendance, tasks and performance) and the version 6 list: the VAT of
each country, bank payouts and mobile money in test mode, customs forms, the route plan of a courier, postal codes,
GTFS timetables, multi-city trips, smarter chat moderation, translation drafts, text + picture recommendations,
ID checks with face matching (with consent), the job queue, the shared cache and accessibility basics."""

import io
import os
import re
import sys
import unittest
import zipfile
from datetime import date, datetime, timedelta
from html.parser import HTMLParser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import support  # noqa: E402
import addresscheck  # noqa: E402
import bankpay  # noqa: E402
import chat  # noqa: E402
import chatmodel  # noqa: E402
import chat_seed7  # noqa: E402
import idcheck  # noqa: E402
import routeplan  # noqa: E402
import translate  # noqa: E402
import world  # noqa: E402
from core import ShopError  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FACE = os.path.join(HERE, "fixtures", "face.jpg")
SAMPLE_GTFS = os.path.join(os.path.dirname(HERE), "data", "gtfs", "sample_rwanda.zip")


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def next_weekday(wd):
    d = date.today() + timedelta(days=1)
    while d.weekday() != wd:
        d += timedelta(days=1)
    return d.isoformat()


class Base(unittest.TestCase):
    """A small shop with an office tree: head -> India -> Haryana -> Ambala hub, and head -> Rwanda -> Kigali hub."""
    env = {"YO_TICKET_FILL": "0"}

    def setUp(self):
        self.s = support.make_store(seed=False, notifier=support.fake_notifier(email=False, sms=False), env=self.env)
        self.admin = self.s.register("Head Admin", "head.admin@realmail.local", "9800000001", "Secret123", wallet=0)
        self.s.make_admin(self.admin)
        with self.s.db() as conn:
            self.head = conn.execute("SELECT id FROM offices WHERE kind = 'head'").fetchone()[0]
        a = self.admin
        self.india = self.s.add_office(a, self.head, "India office", "country", "IN", "New Delhi")
        self.haryana = self.s.add_office(a, self.india, "Haryana office", "region", "IN", "Chandigarh")
        self.ambala = self.s.add_office(a, self.haryana, "Ambala hub", "hub", "IN", "Ambala Cantt")
        self.rwanda = self.s.add_office(a, self.head, "Rwanda office", "country", "RW", "Kigali")
        self.kigali = self.s.add_office(a, self.rwanda, "Kigali hub", "hub", "RW", "Kigali")
        self.n = 10

    def worker(self, office, role="", manager=False, abilities=None, kind="office", by=None):
        self.n += 1
        email = f"worker{self.n}@realmail.local"
        sid, _ = self.s.add_worker(by or self.admin, {"email": email, "name": f"Worker {self.n}",
                                                      "mobile": f"98000{self.n:05d}", "office_id": office, "kind": kind,
                                                      "role": role, "is_manager": manager,
                                                      "abilities": abilities or []})
        return sid, self.s.staff_member(sid)["user_id"]


class TestStaff(Base):
    def test_office_tree_and_branch(self):
        boss, boss_uid = self.worker(self.haryana, "office_manager", manager=True)
        hub, _ = self.worker(self.ambala, "hub")
        rw, _ = self.worker(self.kigali, "hub")
        tree = [o["name"] for o in self.s.office_tree()]
        self.assertEqual(tree[0], self.s.office(self.head)["name"])
        self.assertLess(tree.index("India office"), tree.index("Haryana office"))
        self.assertLess(tree.index("Haryana office"), tree.index("Ambala hub"))
        mine = {w["id"] for w in self.s.workers(boss_uid)}
        self.assertIn(hub, mine)
        self.assertNotIn(rw, mine)                                   # Rwanda is not in his branch
        self.assertIsNone(self.s.branch_of(self.admin))              # the administrator sees everything

    def test_manager_gives_only_his_own_abilities(self):
        boss, boss_uid = self.worker(self.haryana, "office_manager", manager=True)
        sid, _ = self.worker(self.ambala, "hub", by=boss_uid, abilities=["board.use"])   # a manager adds a worker
        with self.assertRaises(ShopError):                           # he has no "orders.manage" himself
            self.s.update_worker(boss_uid, sid, {"abilities": ["hub.scan", "orders.manage"], "office_id": self.ambala,
                                                 "title": "Hub worker", "kind": "hub"})
        with self.assertRaises(ShopError):                           # "all" only from the administrator
            self.s.update_worker(boss_uid, sid, {"abilities": ["all"], "office_id": self.ambala, "title": "X",
                                                 "kind": "hub"})
        self.s.update_worker(boss_uid, sid, {"abilities": ["board.use", "staff.view"],
                                             "office_id": self.ambala, "title": "Hub lead", "kind": "hub"})
        self.assertIn("staff.view", self.s.staff_member(sid)["ability_set"])
        with self.assertRaises(ShopError):                           # not into an office outside his branch
            self.worker(self.kigali, "hub", by=boss_uid, abilities=["board.use"])

    def test_suspend_and_remove_take_access_at_once(self):
        sid, uid = self.worker(self.ambala, "support")
        self.assertTrue(self.s.can(uid, "orders.view"))
        self.s.set_worker_status(self.admin, sid, "Suspended", "Holiday cover ended")
        self.assertFalse(self.s.can(uid, "orders.view"))
        self.s.set_worker_status(self.admin, sid, "Active")
        self.assertTrue(self.s.can(uid, "orders.view"))
        self.s.set_worker_status(self.admin, sid, "Removed", "Left the company")
        self.assertFalse(self.s.can(uid, "orders.view"))
        self.assertIsNone(self.s.worker_of(uid))

    def test_staff_log_with_filters(self):
        sid, uid = self.worker(self.ambala, "hub")
        self.s.set_worker_status(self.admin, sid, "Suspended", "Late three times")
        rows = self.s.staff_log(self.admin, 50, action="suspend")
        self.assertTrue(rows and all("uspend" in (r["action"] + r["details"]) for r in rows))
        self.s.log_staff_action(uid, "/admin/orders/5", ["status", "note"], 200)
        pages = self.s.staff_log(self.admin, 50, kind="pages")
        self.assertEqual(pages[0]["details"], "POST /admin/orders/5 [status, note]")
        self.assertTrue(all(r["action"] != "Page action" for r in self.s.staff_log(self.admin, 50, kind="changes")))


class TestStaffWork(Base):
    def test_time_off_and_staff_card(self):
        boss, boss_uid = self.worker(self.haryana, "office_manager", manager=True)
        sid, uid = self.worker(self.ambala, "support")
        start = date.today() + timedelta(days=5)
        lid = self.s.request_leave(uid, start.isoformat(), (start + timedelta(days=1)).isoformat(), "Wedding")
        with self.assertRaises(ShopError):                              # the same days twice
            self.s.request_leave(uid, start.isoformat(), start.isoformat())
        self.s.decide_leave(boss_uid, lid, True)
        self.assertEqual(self.s.leave_list(uid, mine=True)[0]["status"], "Approved")
        token = self.s.staff_card_token(sid)
        card = self.s.check_staff_card(token)
        self.assertEqual((card["office"], card["active"]), ("Ambala hub", True))
        self.assertIsNone(self.s.check_staff_card(token[:-2] + "xx"))   # a changed card is refused
        self.s.set_worker_status(self.admin, sid, "Suspended")
        self.assertFalse(self.s.check_staff_card(token)["active"])

    def test_seller_employees(self):
        owner = self.s.register("Shop Owner", "owner@realmail.local", "9800000777", "Secret123", wallet=0)
        self.s.apply_seller(owner, {"shop_name": "Owner Test Shop", "about": "Handmade baskets and bags from Ambala",
                                    "country": "IN", "city": "Ambala Cantt", "payout_details": "owner@upi"})
        shop = self.s.seller_of(owner)
        helper = self.s.register("Shop Helper", "helper@realmail.local", "9800000778", "Secret123", wallet=0)
        self.s.add_seller_employee(owner, "helper@realmail.local", "Packer", ["seller.orders"])
        got, abilities = self.s.seller_membership(helper)
        self.assertEqual(got["id"], shop["id"])
        self.assertEqual(set(abilities), {"seller.orders"})


class TestTravelChanges(Base):
    def test_bus_with_one_change(self):
        day = next_weekday(2)
        r = self.s.search_trips("bus", "Ambala Cantt", "Mumbai", day, 1)
        self.assertEqual(r["trips"], [])
        self.assertTrue(r["connections"])                                # too far for one bus: one change
        c = r["connections"][0]
        self.assertLess(c["legs"][0]["arrive_utc"], c["legs"][1]["depart_utc"])

    def test_health_check(self):
        app = support.make_app(seed=False)
        r = app.test_client().get("/healthz")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["ok"])


class TestAdminGuard(unittest.TestCase):
    """The admin pages through the web: abilities, the log of every change and two-step sign-in."""

    @classmethod
    def setUpClass(cls):
        cls.app = support.make_app(seed=True)
        cls.s = cls.app.store

    def client(self, email, password):
        c = self.app.test_client()
        support.login(c, email, password)
        return c

    def test_pages_follow_the_abilities(self):
        c = self.client("neha.gill@example.com", "Staff@2026")         # support: orders.view + orders.manage
        self.assertEqual(c.get("/admin/orders").status_code, 200)
        self.assertEqual(c.get("/admin/settings").status_code, 403)
        self.assertEqual(c.get("/admin/staff").status_code, 403)
        hub = self.client("sunil.kumar@example.com", "Staff@2026")
        self.assertEqual(hub.get("/staff").status_code, 200)
        self.assertEqual(hub.get("/admin/orders").status_code, 403)
        boss = self.client("rohit.malhotra@example.com", "Staff@2026")
        html = boss.get("/admin/staff").get_data(as_text=True)
        self.assertIn("Sunil Kumar", html)                              # his branch
        self.assertNotIn("Eric Habimana", html)                         # Rwanda is not his branch

    def test_every_admin_change_is_logged(self):
        c = self.client("rohit.malhotra@example.com", "Staff@2026")
        tok = support.token(c, "/admin/staff/tasks")
        sid = self.s.find_user("sunil.kumar@example.com")["id"]
        staff_id = self.s.worker_of(sid)["id"]
        c.post("/admin/staff/tasks", data={"csrf_token": tok, "staff_id": staff_id, "title": "Sweep gate 3",
                                           "priority": "Low"})
        rows = self.s.staff_log(self.s.find_user("admin@example.com")["id"], 20, kind="pages")
        self.assertTrue(any(r["details"].startswith("POST /admin/staff/tasks [") and "Sweep" not in r["details"]
                            for r in rows))                              # field names, never the values

    def test_two_step_sign_in_for_workers(self):
        with self.s.db() as conn:
            conn.execute("INSERT INTO settings (key, value) VALUES ('staff_need_2fa', '1') "
                         "ON CONFLICT (key) DO UPDATE SET value = '1'")
        try:
            c = self.client("neha.gill@example.com", "Staff@2026")
            r = c.get("/admin/orders")
            self.assertEqual(r.status_code, 302)
            self.assertIn("/admin/account", r.headers["Location"])
            self.assertEqual(c.get("/admin/account").status_code, 200)
        finally:
            with self.s.db() as conn:
                conn.execute("UPDATE settings SET value = '0' WHERE key = 'staff_need_2fa'")

    def test_demo_staff_and_pages(self):
        with self.s.db() as conn:
            self.assertGreaterEqual(conn.execute("SELECT COUNT(*) FROM staff").fetchone()[0], 13)
            self.assertGreater(conn.execute("SELECT COUNT(*) FROM staff_rota").fetchone()[0], 30)
        c = self.client("admin@example.com", "Admin@1234")
        for url in ("/admin/staff", "/admin/offices", "/admin/staff/rota", "/admin/staff/attendance",
                    "/admin/staff/tasks", "/admin/staff/performance", "/admin/staff/log", "/admin/taxes",
                    "/admin/bank", "/admin/tickets/gtfs", "/admin/id-checks", "/admin/monitor", "/admin/chat",
                    "/admin/model", "/admin/customs"):
            self.assertEqual(c.get(url).status_code, 200, url)

    def test_pages_are_accessible(self):
        """Simple accessibility rules on every main page: the language of the page, a text for every picture and a
        label for every field (tools/a11y_check.py runs the full WCAG check with axe-core)."""

        class Check(HTMLParser):
            def __init__(self):
                super().__init__()
                self.lang, self.problems, self.labels, self.fields = None, [], set(), []

            def handle_starttag(self, tag, attrs):
                a = dict(attrs)
                if tag == "html":
                    self.lang = a.get("lang")
                if tag == "img" and "alt" not in a:
                    self.problems.append(f"img without alt: {a.get('src')}")
                if tag == "label" and a.get("for"):
                    self.labels.add(a["for"])
                if tag in ("input", "select", "textarea") and a.get("type") not in ("hidden", "submit", "button"):
                    self.fields.append(a)
                self.stack.append(tag) if hasattr(self, "stack") else None

        c = self.client("admin@example.com", "Admin@1234")
        for url in ("/", "/p/homeease-kettle", "/courier", "/tickets", "/places?q=Mugunga", "/admin/staff/rota",
                    "/admin/staff/tasks", "/admin/taxes", "/id-check"):
            html = c.get(url).get_data(as_text=True)
            chk = Check()
            chk.feed(html)
            self.assertTrue(chk.lang, url)
            self.assertEqual(chk.problems, [], url)
            for f in chk.fields:                         # a field needs a label (for=id, around it) or an aria-label
                if f.get("aria-label") or f.get("aria-labelledby") or f.get("id") in chk.labels or f.get("title"):
                    continue
                name = re.escape(f.get("name", ""))
                around = re.search(rf"<label[^>]*>(?:(?!</label>).)*name=\"{name}\"", html, re.S)
                self.assertTrue(around, f"{url}: field {f.get('name')} has no label")


class TestWorkforce(Base):
    def test_shift_plan_and_attendance(self):
        boss, boss_uid = self.worker(self.haryana, "office_manager", manager=True)
        sid, uid = self.worker(self.ambala, "hub")
        rw, _ = self.worker(self.kigali, "hub")
        today = date.today()
        self.s.plan_shift(boss_uid, sid, (today + timedelta(days=1)).isoformat(), "08:00", "16:00", "Gate 2", 2)
        with self.assertRaises(ShopError):                            # Kigali is not in his branch
            self.s.plan_shift(boss_uid, rw, today.isoformat(), "09:00", "17:00")
        with self.assertRaises(ShopError):
            self.s.plan_shift(boss_uid, sid, today.isoformat(), "9 o'clock", "17:00")
        yesterday = (today - timedelta(days=1)).isoformat()
        before = (today - timedelta(days=2)).isoformat()
        with self.s.db() as conn:                                     # two days that are over
            for d in (yesterday, before):
                conn.execute("INSERT INTO staff_rota (staff_id, office_id, day, start_hm, end_hm, created_at) "
                             "VALUES (?, ?, ?, '09:00', '17:00', ?)", (sid, self.ambala, d, d))
            tz = self.s.office_tz(self.s.office(self.ambala))
            from zoneinfo import ZoneInfo
            came = datetime.fromisoformat(f"{yesterday} 09:40").replace(tzinfo=ZoneInfo(tz)).astimezone().replace(tzinfo=None)
            conn.execute("INSERT INTO staff_shifts (staff_id, office_id, start_at, end_at, minutes) VALUES (?, ?, ?, ?, 400)",
                         (sid, self.ambala, came.strftime("%Y-%m-%d %H:%M:%S"),
                          (came + timedelta(minutes=400)).strftime("%Y-%m-%d %H:%M:%S")))
        att = {r["worker"]["id"]: r["state"] for r in self.s.attendance(boss_uid, yesterday)["rows"]}
        self.assertEqual(att[sid], "Late")                            # 40 minutes after the start
        att = {r["worker"]["id"]: r["state"] for r in self.s.attendance(boss_uid, before)["rows"]}
        self.assertEqual(att[sid], "Absent")
        week = self.s.rota(boss_uid, before, 3)
        row = next(r for r in week["rows"] if r["worker"]["id"] == sid)
        self.assertEqual([c["state"] for c in row["cells"]], ["Absent", "Late", row["cells"][2]["state"]])
        hours = {h["office"]: h for h in self.s.hours_by_office(boss_uid, before, 3)}
        self.assertEqual(hours["Ambala hub"]["worked_min"], 400)
        self.assertEqual(hours["Ambala hub"]["absent"], 1)

    def test_tasks_and_performance(self):
        boss, boss_uid = self.worker(self.haryana, "office_manager", manager=True)
        sid, uid = self.worker(self.ambala, "hub")
        other, other_uid = self.worker(self.ambala, "hub")
        tid = self.s.give_task(boss_uid, sid, "Count the parcels at gate 2", "Before 18:00", date.today().isoformat(),
                               "High")
        with self.assertRaises(ShopError):                            # not his task
            self.s.move_staff_task(other_uid, tid, "In progress")
        with self.assertRaises(ShopError):                            # a worker cannot cancel
            self.s.move_staff_task(uid, tid, "Cancelled")
        self.s.move_staff_task(uid, tid, "In progress")
        with self.assertRaises(ShopError):                            # the second click finds a new status
            self.s.move_staff_task(uid, tid, "In progress")
        self.s.move_staff_task(uid, tid, "Done")
        mine = self.s.task_list(uid, mine=True)
        self.assertEqual(mine[0]["status"], "Done")
        late = self.s.give_task(boss_uid, other, "Old job", "", "", "Low")
        with self.s.db() as conn:
            conn.execute("UPDATE staff_tasks SET due_day = ? WHERE id = ?",
                         ((date.today() - timedelta(days=3)).isoformat(), late))
        self.assertTrue(next(t for t in self.s.task_list(boss_uid) if t["id"] == late)["overdue"])
        perf = {r["worker"]["id"]: r for r in self.s.performance(boss_uid, None, 7)["rows"]}
        self.assertEqual((perf[sid]["tasks_done"], perf[sid]["tasks_on_time"]), (1, 1))
        self.assertEqual(perf[other]["tasks_overdue"], 1)
        self.s.move_staff_task(boss_uid, late, "Cancelled")           # the manager may cancel


class TestMoney(Base):
    def test_vat_of_each_country(self):
        base = {"kind": "parcel", "weight": "2"}
        q = self.s.courier_quote(dict(base, from_city="Kigali", from_country="RW", to_city="Mugunga", to_place="rw6833",
                                      to_country="RW"))
        self.assertEqual((q["tax_label"], q["tax"]), ("VAT 18%", round(q["subtotal"] * 0.18 / 100) * 100))
        q = self.s.courier_quote(dict(base, from_city="Krasnoyarsk", from_country="RU", to_city="Moscow", to_country="RU"))
        self.assertEqual(q["tax_label"], "НДС 22%")
        q = self.s.courier_quote(dict(base, from_city="Ambala Cantt", to_city="Chandigarh", to_country="IN"))
        self.assertEqual(q["tax_label"], "GST 18%")
        q = self.s.courier_quote(dict(base, from_city="Kigali", from_country="RW", to_city="Nairobi", to_country="KE"))
        self.assertEqual(q["tax"], 0)                                 # a service abroad: no VAT here
        with self.assertRaises(ShopError):                            # only with the ability tax.manage
            sid, uid = self.worker(self.ambala, "hub")
            self.s.save_tax_rule(uid, "RW", "VAT", 10, 10)
        self.s.save_tax_rule(self.admin, "RW", "VAT", 16, 16, "Test")
        q = self.s.courier_quote(dict(base, from_city="Kigali", from_country="RW", to_city="Mugunga", to_place="rw6833",
                                      to_country="RW"))
        self.assertEqual(q["tax_label"], "VAT 16%")

    def test_bank_account_checks(self):
        self.assertTrue(bankpay.iban_ok("GB82 WEST 1234 5698 7654 32"))
        self.assertFalse(bankpay.iban_ok("GB82 WEST 1234 5698 7654 33"))           # one digit wrong
        self.assertFalse(bankpay.iban_ok("DE89 3704 0044 0532 0130 00 1"))         # wrong length
        self.assertTrue(bankpay.ifsc_ok("SBIN0001234"))
        self.assertFalse(bankpay.ifsc_ok("SBIN1001234"))
        self.assertTrue(bankpay.ru_account_ok("044525974", "40702810200000000000"))
        self.assertFalse(bankpay.ru_account_ok("044525974", "40702810300000000000"))

    def test_bank_payouts_in_test_mode(self):
        import demo_data
        s = support.make_store(seed=True)
        owner = s.find_user(demo_data.SELLER_EMAIL)["id"]
        shop = s.seller_of(owner)
        with self.assertRaises(ShopError):
            s.save_bank_account(owner, {"country": "IN", "holder": "Meera Kapoor", "ifsc": "SBIN0001234",
                                        "account": "12"})
        acc = s.save_bank_account(owner, {"country": "IN", "holder": "Meera Kapoor", "ifsc": "SBIN0001234",
                                          "account": "123456789012"})
        self.assertEqual(acc["last4"], "9012")
        with s.db() as conn:
            row = dict(conn.execute("SELECT * FROM bank_accounts WHERE seller_id = ?", (shop["id"],)).fetchone())
        self.assertNotIn("123456789012", " ".join(str(v) for v in row.values()))   # never the whole number
        balance = s.seller_balance(shop["id"])["balance"]
        wallet = s.get_user(owner)["wallet"]
        if balance < 100_00:
            self.skipTest("the demo seller has no balance today")
        paid = s.auto_payouts("2099-01-01")
        self.assertEqual(paid.get(shop["id"]), balance)
        self.assertEqual(s.get_user(owner)["wallet"], wallet)                      # not to Yo Pay
        t = s.bank_transfers(shop["id"])[0]
        self.assertEqual((t["status"], t["amount"]), ("Sent", balance))
        self.assertEqual(s.settle_bank_transfers(at=t["settle_after"]), 1)
        self.assertEqual(s.settle_bank_transfers(at=t["settle_after"]), 0)         # only once
        self.assertEqual(s.bank_transfers(shop["id"])[0]["status"], "Paid")

    def test_mobile_money_once(self):
        uid = self.s.register("Amani Test", "amani.test@realmail.local", "0788111222", "Secret123", wallet=0,
                              country="RW")
        ref = self.s.start_mobile_payment(uid, "momo", "500", "0788111222")
        self.assertEqual(self.s.answer_mobile_payment(uid, ref, True), "Paid")
        with self.assertRaises(ShopError):
            self.s.answer_mobile_payment(uid, ref, True)
        self.assertEqual(self.s.get_user(uid)["wallet"], 500_00)

    def test_customs_and_route_plan(self):
        q = self.s.courier_quote({"kind": "parcel", "weight": "1", "from_city": "Ambala Cantt", "to_city": "Kigali",
                                  "to_country": "RW"})
        d = self.s.customs_declaration({"customs_reason": "gift", "customs_desc": ["Books", "Shirt"],
                                        "customs_qty": ["2", "1"], "customs_value": ["800", "700"]}, q)
        self.assertEqual(d["total_value"], 1500_00)
        with self.assertRaises(ShopError):
            self.s.customs_declaration({"customs_reason": "gift", "customs_desc": ["Fireworks"], "customs_qty": ["1"],
                                        "customs_value": ["100"]}, q)
        stops = [{"id": "p1", "kind": "pickup", "needs": None, "point": (30.38, 76.78)},
                 {"id": "d1", "kind": "drop", "needs": "p1", "point": (30.70, 76.80)},
                 {"id": "p2", "kind": "pickup", "needs": None, "point": (30.60, 76.79)},
                 {"id": "d2", "kind": "drop", "needs": "p2", "point": (30.36, 76.77)}]
        order = [s["id"] for s in routeplan.plan((30.37, 76.78), stops)]
        self.assertLess(order.index("p1"), order.index("d1"))
        self.assertLess(order.index("p2"), order.index("d2"))


class TestPlacesAndTravel(Base):
    def test_postal_codes_of_many_countries(self):
        self.assertEqual(world.clean_postcode("GB", "sw1a 1aa"), "SW1A 1AA")
        self.assertEqual(world.clean_postcode("NL", "1012 AB"), "1012 AB")
        self.assertIsNone(world.clean_postcode("NL", "1012"))
        self.assertIsNone(world.clean_postcode("RU", "66004"))
        self.assertEqual(world.clean_postcode("RW", ""), "")                      # Rwanda has no postal codes
        r = addresscheck.check("IN", "133207", "Pune")
        self.assertTrue(r["warnings"])                                              # Haryana PIN, Maharashtra place
        self.assertIn("Ambala", r["hint"])
        self.assertFalse(addresscheck.check("IN", "133207", "", "Mullana")["warnings"])
        self.assertTrue(addresscheck.check("IN", "999999")["warnings"])           # an unknown PIN code

    def test_gtfs_timetable(self):
        with self.assertRaises(ShopError):
            self.s.import_gtfs(self.admin, b"not a zip")
        r = self.s.import_gtfs(self.admin, read_bytes(SAMPLE_GTFS))
        self.assertEqual((r["stops"], r["matched"], r["trips"]), (4, 4, 12))
        monday, sunday = next_weekday(0), next_weekday(6)
        trips = self.s.search_trips("bus", "Kigali", "Musanze", monday)["trips"]
        self.assertEqual([t["depart_local"][-8:-3] for t in trips], ["06:00", "09:30", "13:00", "17:00"])
        self.assertTrue(all(t["operator"] == "Sample Highland Coaches" for t in trips))
        self.assertEqual(len(self.s.search_trips("bus", "Kigali", "Musanze", sunday)["trips"]), 2)
        feed = self.s.gtfs_feeds()[0]
        self.s.set_gtfs_active(self.admin, feed["id"], False)                       # stop selling: the demo returns
        trips = self.s.search_trips("bus", "Kigali", "Musanze", monday)["trips"]
        self.assertTrue(trips and all(t["operator"] != "Sample Highland Coaches" for t in trips))
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("agency.txt", "agency_name\nX\n")
        with self.assertRaises(ShopError):
            self.s.import_gtfs(self.admin, buf.getvalue())

    def test_multi_city_trip(self):
        day = next_weekday(1)
        r = self.s.plan_multi_city(["Ambala Cantt", "Chandigarh", "New Delhi"], ["bus", "train"], day)
        self.assertEqual(len(r["legs"]), 2)
        with self.assertRaises(ShopError):
            self.s.plan_multi_city(["Ambala Cantt", "Chandigarh"], ["bus"], day)


class TestTrust(Base):
    def test_chat_tricks_are_found(self):
        for text in ("you are an id1ot", "p.a.y m3 directly please", "stuuuupid seller", "W E S T E R N union"):
            self.assertEqual(chat.moderate(text)[1], chat.HELD, text)
        self.assertEqual(chat.moderate("Is it 5 kg? I paid 400 on the site")[1], chat.VISIBLE)

    def test_chat_model_learns_from_moderators(self):
        rep = self.s.chat_model_report()
        self.assertEqual(rep["examples"], len(chatmodel.SEED_BAD) + len(chatmodel.SEED_OK)
                         + len(chat_seed7.BAD) + len(chat_seed7.OK))                     # version 7: more examples
        self.assertIsNotNone(rep["check"])
        self.assertGreater(self.s.chat_bad_probability("переведи деньги мне на карту"), 0.9)
        self.assertLess(self.s.chat_bad_probability("спасибо, посылка пришла"), 0.5)
        text = "please call the gate guard at noon"
        before = self.s.chat_bad_probability(text)
        with self.s.db() as conn:
            for i in range(4):
                self.s.learn_chat_decision(conn, 900 + i, text + f" {i}", approve=False)
        self.assertGreater(self.s.chat_bad_probability(text), before)
        self.assertEqual(self.s.chat_model_report()["learned"], 4)                 # the cache was made new

    def test_translation_drafts(self):
        d = translate.draft("Stainless steel kettle\n2 years warranty", "ru", {})
        self.assertIn("нержавеющая сталь", d["text"])
        self.assertIn("гарантия", d["text"])
        self.assertEqual(d["from"], "memory")
        d = translate.draft("Works on gas and induction", "fr", {"works on gas and induction": "Pour gaz et induction"})
        self.assertEqual((d["text"], d["known"]), ("Pour gaz et induction", 100))
        orig = translate._service
        translate._service = lambda env, text, lang: "TRANSLATED" if env.get("TRANSLATE_URL") else None
        try:
            self.assertEqual(translate.draft("Hello", "es", {}, {"TRANSLATE_URL": "http://mt.local"})["from"], "service")
        finally:
            translate._service = orig

    def test_similar_products_from_text_and_pictures(self):
        s = support.make_store(seed=True)
        r = s.build_content_similar()
        self.assertEqual(r["products"], r["with_picture"])
        kettle = s.get_product(slug="homeease-kettle")
        names = [p.name for p in s.similar_products(kettle.id, 4)]
        self.assertTrue(any("HomeEase" in n for n in names), names)

    def test_mrz_check_digits(self):
        # the specimen passport of ICAO 9303 (Anna Maria Eriksson, Utopia)
        m = idcheck.parse_mrz("P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<",
                              "L898902C36UTO7408122F1204159ZE184226B<<<<<10")
        self.assertTrue(m["valid"])
        self.assertTrue(m["expired"])                                                # it ended in 2012
        bad = idcheck.parse_mrz("P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<",
                                "L898902C46UTO7408122F1204159ZE184226B<<<<<10")
        self.assertFalse(bad["checks"]["number"])

    def test_id_check_with_consent_and_retention(self):
        from PIL import Image, ImageEnhance
        sid, uid = self.worker(self.ambala, "hub")
        face = Image.open(FACE)
        selfie = ImageEnhance.Brightness(face.crop((10, 8, face.width - 6, face.height)).resize(
            (int(face.width * 0.8), int(face.height * 0.8)))).enhance(0.85)
        buf = io.BytesIO()
        selfie.save(buf, "JPEG")
        doc = read_bytes(FACE)
        with self.assertRaises(ShopError):                                           # no consent, no check
            self.s.start_id_check(uid, "staff", "passport", "IN", doc, buf.getvalue(), False)
        cid = self.s.start_id_check(uid, "staff", "id_card", "IN", doc, buf.getvalue(), True)
        c = self.s.id_checks(user_id=uid)[0]
        self.assertGreaterEqual(c["score"], idcheck.SAME_FROM)                       # the same person
        self.assertIn("look alike", c["hint"])
        self.assertTrue(self.s.media(c["doc_image"]))
        with self.assertRaises(ShopError):                                           # only one waiting check
            self.s.start_id_check(uid, "staff", "id_card", "IN", doc, buf.getvalue(), True)
        with self.assertRaises(ShopError):                                           # a hub worker cannot decide
            self.s.decide_id_check(uid, cid, True)
        self.s.decide_id_check(self.admin, cid, True)
        self.assertTrue(self.s.get_user(uid)["id_verified"])
        self.assertEqual(self.s.purge_id_images(), 0)                                # kept 30 days
        later = (datetime.now() + timedelta(days=idcheck.RETENTION_DAYS + 1)).strftime("%Y-%m-%d %H:%M:%S")
        self.assertEqual(self.s.purge_id_images(at=later), 1)
        self.assertIsNone(self.s.media(c["doc_image"]))
        self.assertEqual(self.s.id_checks(user_id=uid)[0]["status"], "Approved")     # the result stays

    def test_id_check_no_face(self):
        sid, uid = self.worker(self.ambala, "hub")
        from PIL import Image
        blank = io.BytesIO()
        Image.new("RGB", (400, 300), (200, 210, 220)).save(blank, "JPEG")
        self.s.start_id_check(uid, "staff", "passport", "IN", blank.getvalue(), read_bytes(FACE), True)
        c = self.s.id_checks(user_id=uid)[0]
        self.assertIsNone(c["score"])
        self.assertIn("No face found on the document", c["hint"])
        self.assertEqual(self.s.withdraw_id_consent(uid), 1)                         # photos deleted at once
        self.assertEqual(self.s.id_checks(user_id=uid)[0]["status"], "Withdrawn")


class TestScale(Base):
    def test_job_queue_and_retry(self):
        jid = self.s.enqueue("test", {"fail": 1}, unique_key="t1", max_attempts=2)
        self.assertIsNone(self.s.enqueue("test", {}, unique_key="t1"))                # waiting once
        self.s.run_jobs(5)
        with self.s.db() as conn:
            j = conn.execute("SELECT * FROM jobs WHERE id = ?", (jid,)).fetchone()
        self.assertIn(j["status"], ("waiting", "failed", "done"))
        self.assertIn("jobs_waiting", self.s.health())

    def test_shared_cache(self):
        calls = []

        def slow():
            calls.append(1)
            return {"n": len(calls)}
        self.assertEqual(self.s.cached("t", "k", 60, slow), {"n": 1})
        self.assertEqual(self.s.cached("t", "k", 60, slow), {"n": 1})
        import cache
        cache._LOCAL.clear()                                                         # another process
        self.assertEqual(self.s.cached("t", "k", 60, slow), {"n": 1})               # from the database
        self.s.cache_bump("t")
        self.assertEqual(self.s.cached("t", "k", 60, slow), {"n": 2})
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
