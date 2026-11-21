"""Tests for version 8: the list of services (the worker's first page, the "three lines" menu, quick find and the
admin menu), the site content (contacts, pages with versions and languages, banners, help articles, staff news,
colleagues), public holidays and leave rules (counting days, contracts, payroll, year statement), clock-in with the
office code and the office network, India's e-invoice number and the review of tax rates, webhooks to courier
partners, the speed profile of couriers, live vehicles and alerts, the live help desk and e-mails from customers,
the hourly refresh, the demo content and the pages of version 8."""

import hashlib
import json
import os
import sys
import unittest
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import support  # noqa: E402
import einvoice  # noqa: E402
import holidays  # noqa: E402
import providers  # noqa: E402
import routeplan  # noqa: E402
import services  # noqa: E402
import sitecontent  # noqa: E402
import staff as staff_rules  # noqa: E402
import v8extra  # noqa: E402
import webhooks  # noqa: E402
from core import ShopError, now  # noqa: E402
from test_v6 import Base  # noqa: E402
from test_v7 import ADDR, Couriers, first_of_last_month  # noqa: E402


class TestServices(Base):
    def test_first_page_of_a_worker_follows_role_and_abilities(self):
        _, support_uid = self.worker(self.haryana, "support", kind="support")
        _, hub_uid = self.worker(self.ambala, "hub", kind="hub")
        _, fin_uid = self.worker(self.india, "finance")
        _, plain_uid = self.worker(self.ambala, "", abilities=["board.use"])
        _, odd_uid = self.worker(self.ambala, "hub", abilities=["board.use"])      # role "hub" without the ability
        customer = self.s.register("Buyer", "buyer@realmail.local", "9811111199", "Secret123")
        self.assertEqual(self.s.work_home(support_uid)["endpoint"], "admin_support")
        self.assertEqual(self.s.work_home(hub_uid)["endpoint"], "hub_page")
        self.assertEqual(self.s.work_home(fin_uid)["endpoint"], "admin_payroll")
        self.assertEqual(self.s.work_home(plain_uid)["endpoint"], "staff_home")
        self.assertEqual(self.s.work_home(odd_uid)["endpoint"], "staff_home")
        self.assertEqual(self.s.work_home(self.admin)["position"], "Administrator")
        self.assertIsNone(self.s.work_home(customer))
        home = self.s.work_home(hub_uid)
        self.assertEqual((home["label"], home["office"]), ("Scan parcels at the hub", "Ambala hub"))
        self.assertTrue(home["position"])                                         # every worker has a position

    def test_suspended_worker_has_no_work_button(self):
        sid, uid = self.worker(self.ambala, "hub", kind="hub")
        self.s.set_worker_status(self.admin, sid, "Suspended")
        self.assertIsNone(self.s.work_home(uid))
        self.assertEqual(self.s.nav_for(uid), [])

    def test_menu_lists_only_what_a_person_may_open(self):
        _, hub_uid = self.worker(self.ambala, "hub", kind="hub")
        customer = self.s.register("Buyer", "buyer@realmail.local", "9811111199", "Secret123")
        hub = dict(self.s.menu_for(hub_uid))
        labels = [i["label"] for i in hub["My work"]]
        self.assertIn("Scan parcels at the hub", labels)
        self.assertIn("Shipments", labels)
        self.assertNotIn("Payroll", labels)
        self.assertNotIn("Site content", labels)
        self.assertNotIn("My work", dict(self.s.menu_for(customer)))
        anon = dict(self.s.menu_for(None))
        self.assertEqual(list(anon), ["Yo services", "Shop by category"])
        self.assertEqual(len(anon["Shop by category"]), len(self.s.categories()))
        everything = [i["label"] for i in dict(self.s.menu_for(self.admin))["My work"]]
        self.assertIn("Site content", everything)
        self.assertIn("Public holidays", everything)

    def test_admin_menu_is_grouped(self):
        _, uid = self.worker(self.india, "finance")
        groups = self.s.nav_for(uid)
        names = [g for g, _ in groups]
        self.assertEqual(names, [g for g in services.GROUPS if g in names])        # the fixed order of the groups
        money = dict(groups)["Money"]
        self.assertIn("admin_payroll", [i["endpoint"] for i in money])
        self.assertNotIn("People", names)                                          # finance sees no staff pages
        self.assertEqual(self.s.nav_for(self.s.register("Buyer", "b2@realmail.local", "9811111188", "Secret123")), [])

    def test_search_words_match_any_order(self):
        self.assertTrue(services.matches("pay salary", "Payroll", "salary payslip pay run tax"))
        self.assertTrue(services.matches("  TRACK ", "Track an order or parcel", ""))
        self.assertFalse(services.matches("salary invoice", "Payroll", "salary payslip pay run tax"))


class TestQuickFind(Couriers):
    def test_parcel_question_customer_and_pages(self):
        number = self.book()
        other = self.s.register("Other", "other.v8@realmail.local", "9811111177", "Secret123")
        mine = self.s.quick_find(self.uid, number)
        self.assertEqual((mine[0]["kind"], mine[0]["endpoint"]), ("Parcel", "courier_shipment"))
        self.assertEqual(self.s.quick_find(other, number)[0]["endpoint"], "courier_track")     # anybody can follow it
        self.assertEqual(self.s.quick_find(self.admin, number.lower())[0]["endpoint"], "admin_shipment")
        ticket = self.s.open_support_ticket(self.uid, "parcel", "Where is my parcel", "It is late since Monday")
        self.assertEqual(self.s.quick_find(self.uid, ticket)[0]["endpoint"], "support_ticket_page")
        self.assertEqual([r for r in self.s.quick_find(other, ticket) if r["kind"] == "Question"], [])
        self.assertEqual(self.s.quick_find(self.admin, ticket)[0]["endpoint"], "admin_support_ticket")
        people = [r for r in self.s.quick_find(self.admin, "sender.v8") if r["kind"] == "Customer"]
        self.assertEqual(people, [])                                               # no such e-mail
        people = [r for r in self.s.quick_find(self.admin, "sender.v7") if r["kind"] == "Customer"]
        self.assertEqual(people[0]["args"], {"uid": self.uid})
        self.assertEqual([r for r in self.s.quick_find(other, "sender.v7") if r["kind"] == "Customer"], [])
        pages = [r["endpoint"] for r in self.s.quick_find(other, "wallet")]
        self.assertIn("pay_home", pages)
        self.assertNotIn("admin_yopay", pages)
        self.assertIn("admin_yopay", [r["endpoint"] for r in self.s.quick_find(self.admin, "wallet")])
        self.assertEqual(self.s.quick_find(self.admin, "x"), [])                   # one letter is not a search


class TestSiteContent(Base):
    def editor(self):
        return self.worker(self.india, "catalog")[1]

    def test_contacts_are_checked_and_logged(self):
        ed = self.editor()
        _, hub_uid = self.worker(self.ambala, "hub")
        c = self.s.contacts()
        self.assertEqual((c["phone"], c["email"]), ("1800-000-9696", "support@yodelivery.example"))
        self.assertEqual(c["address_lines"], ["12 Mall Road, Ambala Cantt", "Haryana 133001, India"])
        with self.assertRaises(ShopError):
            self.s.save_contacts(hub_uid, {"phone": "1800-111-2222"})             # no ability
        for bad in ({"email": "not-an-email"}, {"phone": "call me"}, {"facebook": "http://facebook.example/yo"},
                    {"instagram": "javascript:alert(1)"}, {"company": ""}):
            with self.assertRaises(ShopError):
                self.s.save_contacts(ed, bad)
        changed = self.s.save_contacts(ed, {"phone": "+250 788 000 111", "hours": "Mon - Sat, 8 AM - 6 PM",
                                            "telegram": "https://t.me/yodelivery_demo", "email": c["email"]})
        self.assertEqual(sorted(changed), ["Opening hours", "Support phone", "Telegram channel"])
        c = self.s.contacts()
        self.assertEqual(c["social"], [("telegram", "Telegram channel", "https://t.me/yodelivery_demo")])
        self.assertEqual(self.s.save_contacts(ed, {"phone": "+250 788 000 111"}), [])     # nothing changed
        self.assertEqual(self.s.fill_text("Call {phone} ({hours}) {unknown}"),
                         "Call +250 788 000 111 (Mon - Sat, 8 AM - 6 PM) {unknown}")
        self.assertEqual(self.s.activity(action="Site contacts changed")[0]["details"], "phone, hours, telegram")

    def test_pages_keep_versions_and_languages(self):
        ed = self.editor()
        self.assertEqual({p["slug"] for p in self.s.site_pages()}, {"about", "delivery", "returns", "terms", "privacy",
                                                                    "careers"})
        self.assertNotIn("careers", [p["slug"] for p in self.s.site_pages(footer_only=True)])
        first = self.s.site_page("terms")["body"]
        self.s.save_site_page(ed, "terms", "en", "Terms of use", "These are the new terms of the demo shop.")
        self.s.save_site_page(ed, "terms", "en", "Terms of use", "These are the newest terms of the demo shop.")
        versions = self.s.page_versions("terms")
        self.assertEqual([v["body"] for v in versions], ["These are the new terms of the demo shop.", first])
        self.s.restore_page_version(ed, versions[-1]["id"])                        # the first text comes back
        self.assertEqual(self.s.site_page("terms")["body"], first)
        self.assertEqual(len(self.s.page_versions("terms")), 3)                    # and today's text was kept
        with self.assertRaises(ShopError):
            self.s.save_site_page(ed, "shipping", "ru", "Доставка", "Текст страницы о доставке.")   # English first
        self.s.save_site_page(ed, "terms", "ru", "Условия", "Это условия использования демо-магазина.")
        self.assertEqual(self.s.site_page("terms", "ru")["title"], "Условия")
        self.assertEqual(self.s.site_page("terms", "fr")["title"], "Terms of use")  # no French text: English
        self.assertIsNone(self.s.site_page("terms", "fr", exact=True))
        self.assertTrue(self.s.delete_site_page(ed, "terms", "ru"))
        self.assertEqual(self.s.site_page("terms", "ru")["lang"], "en")
        with self.assertRaises(ShopError):
            self.s.delete_site_page(ed, "about", "en")
        for slug in ("New Page", "admin", "x"):
            with self.assertRaises(ShopError):
                self.s.save_site_page(ed, slug, "en", "A title", "A text that is long enough.")
        self.s.save_site_page(ed, "shipping-rules", "en", "Shipping rules", "Free above {free_delivery_from}. <script>x</script>",
                              published=False)
        page = self.s.site_page("shipping-rules")
        html = self.s.page_html(page)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("{free_delivery_from}", html)
        self.assertEqual(page["published"], 0)

    def test_page_keeps_twenty_versions(self):
        ed = self.editor()
        for i in range(sitecontent.KEEP_VERSIONS + 5):
            self.s.save_site_page(ed, "careers", "en", "Work with us", f"Text number {i} of the careers page.")
        self.assertEqual(len(self.s.page_versions("careers")), sitecontent.KEEP_VERSIONS)

    def test_banners(self):
        ed = self.editor()
        self.assertEqual(len(self.s.slides(active_only=True)), 4)
        self.assertEqual(sitecontent.slide_title_html("Run *further* / Pay <less>"), "Run <span>further</span><br>Pay &lt;less&gt;")
        self.assertNotIn("{money", self.s.slides()[2]["title_html"])                # {money:1499} became a price
        self.assertEqual(sitecontent.safe_link("javascript:alert(1)"), "/")
        self.assertEqual(sitecontent.safe_link("//evil.example/x"), "/")
        self.assertEqual(sitecontent.safe_link("/c/grocery?sort=new"), "/c/grocery?sort=new")
        self.assertEqual(sitecontent.safe_link("https://yodelivery.example/sale"), "https://yodelivery.example/sale")
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        self.s.save_slide(ed, None, {"title": "Winter *sale*", "link": "javascript:x", "active": "1", "from_day": tomorrow,
                                     "image": sitecontent.PHOTOS[1]})
        new = next(sl for sl in self.s.slides() if sl["title"] == "Winter *sale*")
        self.assertEqual((new["link"], new["shown"]), ("/", False))                # starts tomorrow
        self.assertEqual(len(self.s.slides(active_only=True, today=date.today() + timedelta(days=2))), 5)
        with self.assertRaises(ShopError):
            self.s.save_slide(ed, None, {"title": "Bad picture", "image": "../../etc/passwd"})
        with self.assertRaises(ShopError):
            self.s.save_slide(ed, None, {"title": "Days", "from_day": "2027-02-02", "to_day": "2027-01-01"})
        self.s.delete_slide(ed, new["id"])
        self.assertEqual(len(self.s.slides()), 4)

    def test_help_articles_search_and_votes(self):
        ed = self.editor()
        best = self.s.kb_search("when do I get my refund")[0]
        self.assertEqual(best["question"], "When do I get my refund?")
        self.assertEqual(self.s.kb_search("xyzzy plugh"), [])
        self.assertEqual(self.s.kb_search("the a of"), [])                         # only small words
        self.assertIn("cancel", self.s.kb_search("cancelling orders")[0]["question"].lower())   # endings are ignored
        self.s.save_kb_article(ed, None, {"topic": "Courier", "question": "Can I send a bicycle?", "published": "1",
                                          "answer": "Yes, in a box up to 30 kg. Call {phone} first.", "words": "bike cycle"})
        a = self.s.kb_search("bike")[0]
        self.assertEqual(a["slug"], "can-i-send-a-bicycle")
        self.s.kb_vote(a["id"], True)
        self.s.kb_vote(a["id"], False)
        self.s.kb_opened(a["id"])
        a = self.s.kb_article(article_id=a["id"])
        self.assertEqual((a["helpful"], a["not_helpful"], a["opened"]), (1, 1, 1))
        self.s.save_kb_article(ed, a["id"], {"topic": "Courier", "question": a["question"], "answer": a["answer"]})
        self.assertEqual(self.s.kb_search("bike"), [])                             # not published any more
        with self.assertRaises(ShopError):
            self.s.save_kb_article(ed, None, {"topic": "Cooking", "question": "How to cook rice?", "answer": "Boil it well."})
        with self.assertRaises(ShopError):
            self.s.save_kb_article(self.worker(self.ambala, "hub")[1], None, {"topic": "Courier", "question": "Why not me?",
                                                                               "answer": "No ability for this."})

    def test_staff_news_reach_the_branch_only(self):
        _, boss = self.worker(self.haryana, "office_manager", manager=True)
        _, hub_uid = self.worker(self.ambala, "hub")
        _, rw_uid = self.worker(self.kigali, "hub")
        nid = self.s.post_staff_news(boss, "Fire drill on Friday", "At 10:00 in every office of Haryana.")
        self.s.post_staff_news(self.admin, "Happy new year", "", pinned=True)
        with self.assertRaises(ShopError):
            self.s.post_staff_news(boss, "For Rwanda", "Not my branch", self.kigali)
        with self.assertRaises(ShopError):
            self.s.post_staff_news(hub_uid, "From a worker", "No")
        seen = self.s.staff_news_for(hub_uid, mark_seen=True)
        self.assertEqual([n["title"] for n in seen], ["Happy new year", "Fire drill on Friday"])   # pinned first
        self.assertTrue(all(n["is_new"] for n in seen))
        self.assertFalse(any(n["is_new"] for n in self.s.staff_news_for(hub_uid)))
        self.assertEqual([n["title"] for n in self.s.staff_news_for(rw_uid)], ["Happy new year"])
        self.assertEqual(next(n for n in self.s.staff_news_list(boss) if n["id"] == nid)["seen_by"], 1)
        old = self.s.post_staff_news(boss, "Old notice", "", until_day=(date.today() - timedelta(days=1)).isoformat())
        self.assertNotIn(old, [n["id"] for n in self.s.staff_news_for(hub_uid)])
        self.s.delete_staff_news(boss, nid)
        self.assertNotIn(nid, [n["id"] for n in self.s.staff_news_for(hub_uid)])

    def test_colleagues_and_manager(self):
        boss_sid, boss = self.worker(self.haryana, "office_manager", manager=True)
        _, a = self.worker(self.ambala, "hub")
        _, b = self.worker(self.ambala, "hub")
        _, rw = self.worker(self.kigali, "hub")
        mine = self.s.colleagues(a)
        self.assertEqual({p["user_id"] for p in mine}, {a, b, boss})
        self.assertTrue(all(p["mobile"] == "" for p in mine))                      # no phone numbers for a worker
        self.assertTrue(any(p["mobile"] for p in self.s.colleagues(boss)))
        self.assertNotIn(rw, {p["user_id"] for p in self.s.colleagues(boss)})
        self.assertEqual(self.s.manager_of(a)["name"], self.s.get_user(boss)["name"])
        self.assertIsNone(self.s.manager_of(boss))                                 # nobody above him is a manager
        self.assertEqual(self.s.job_info(a)["path"][-1], "Ambala hub")
        self.assertEqual(self.s.job_info(a)["manager"]["title"], self.s.staff_member(boss_sid)["title"])
        self.assertEqual(self.s.colleagues(self.s.register("Buyer", "b3@realmail.local", "9811111166", "Secret123")), [])


class TestHolidays(Base):
    def contract(self, sid, days=None, amount=30000):
        data = {"kind": "permanent", "start_day": f"{date.today().year - 1}-01-01", "pay_kind": "month",
                "pay_amount": str(amount), "hours_week": "40"}
        if days is not None:
            data["leave_days"] = str(days)
        return self.s.save_contract(self.admin, sid, data)

    def russia(self):
        ru = self.s.add_office(self.admin, self.head, "Russia office", "country", "RU", "Moscow")
        return self.s.add_office(self.admin, ru, "Krasnoyarsk office", "city", "RU", "Krasnoyarsk")

    def test_calendars_of_2027(self):
        rw = {h["day"]: h for h in self.s.holidays("RW", 2027)}
        self.assertEqual(len(rw), 17)
        self.assertEqual(rw["2027-07-05"]["kind"], "lieu")
        self.assertEqual((rw["2027-03-09"]["by_moon"], rw["2027-02-01"]["by_moon"]), (1, 0))
        self.assertEqual([h["day"] for h in self.s.holidays("IN", 2027)], ["2027-01-26", "2027-08-15", "2027-10-02"])
        self.assertTrue(self.s.is_working_day("RU", date(2027, 2, 20)))            # a worked Saturday
        self.assertFalse(self.s.is_working_day("RU", date(2027, 2, 22)))           # the Monday off in its place
        self.assertFalse(self.s.is_working_day("RU", date(2027, 1, 7)))
        self.assertTrue(self.s.is_working_day("IN", date(2027, 2, 22)))
        self.assertEqual(self.s.working_days("RU", date(2027, 1, 1), date(2027, 1, 17)), 5)   # 11 to 15 January
        self.assertEqual(self.s.working_days("RW", date(2027, 3, 25), date(2027, 3, 30)), 2)  # Good Friday, Easter Monday
        self.assertEqual(self.s.working_days("KE", date(2027, 3, 25), date(2027, 3, 30)), 4)  # no calendar: Mon - Fri

    def test_leave_counts_by_the_rule_of_the_country(self):
        self.assertEqual(self.s.leave_days_count("RW", date(2027, 3, 25), date(2027, 3, 30)), 2)
        self.assertEqual(self.s.leave_days_count("RU", date(2027, 5, 7), date(2027, 5, 11)), 4)   # calendar days - 9 May
        self.assertEqual(self.s.leave_days_count("IN", date(2027, 1, 25), date(2027, 1, 29)), 4)  # Republic Day
        sid, uid = self.worker(self.kigali, "hub")
        self.contract(sid, 18)
        self.s.request_leave_kind(uid, "2027-03-25", "2027-03-30", "Annual")
        self.assertEqual(self.s.leave_balance(sid, 2027, today=date(2027, 12, 31))["used"], 2)
        self.assertEqual(self.s.leave_balance(sid, 2027, today=date(2027, 12, 31))["unit"], "working")

    def test_contract_respects_the_minimum_of_the_country(self):
        krsk = self.russia()
        sid, _ = self.worker(krsk, "hub")
        with self.assertRaises(ShopError) as e:
            self.contract(sid, 20)
        self.assertIn("at least 28 calendar days", str(e.exception))
        self.contract(sid)                                                         # no number: the minimum is used
        self.assertEqual(self.s.contract_of(sid)["leave_days"], 28)
        rw_sid, _ = self.worker(self.kigali, "hub")
        with self.assertRaises(ShopError):
            self.contract(rw_sid, 17)
        self.contract(rw_sid, 18)
        in_sid, _ = self.worker(self.ambala, "hub")
        self.contract(in_sid)
        self.assertEqual(self.s.contract_of(in_sid)["leave_days"], 20)             # the old default is above India's 15

    def test_who_may_change_the_calendar(self):
        _, rw_boss = self.worker(self.rwanda, "country_manager", manager=True)
        _, hub_uid = self.worker(self.kigali, "hub")
        self.s.save_holiday(rw_boss, "rw", "2028-01-01", "New Year's Day")
        self.assertEqual(self.s.holidays("RW", 2028)[0]["name"], "New Year's Day")
        self.s.save_holiday(rw_boss, "RW", "2028-01-01", "New Year")                 # the same day again: changed
        self.assertEqual([h["name"] for h in self.s.holidays("RW", 2028)], ["New Year"])
        with self.assertRaises(ShopError):
            self.s.save_holiday(rw_boss, "IN", "2028-01-26", "Republic Day")         # not his country
        with self.assertRaises(ShopError):
            self.s.save_holiday(hub_uid, "RW", "2028-02-01", "Heroes' Day")
        with self.assertRaises(ShopError):
            self.s.save_holiday(self.admin, "RU", "2028-02-23", "Working day", "workday")   # a Wednesday
        self.s.save_holiday(self.admin, "KE", "2028-12-12", "Jamhuri Day")
        self.assertIn("KE", self.s.holiday_countries())
        self.s.delete_holiday(rw_boss, self.s.holidays("RW", 2028)[0]["id"])
        self.assertEqual(self.s.holidays("RW", 2028), [])

    def test_leave_rules(self):
        rules = {r["country"]: r for r in self.s.leave_rules()}
        self.assertEqual((rules["RW"]["min_days"], rules["RU"]["unit"], rules["RU"]["holiday_rate"]), (18, "calendar", 2))
        _, hub_uid = self.worker(self.ambala, "hub")
        with self.assertRaises(ShopError):
            self.s.save_leave_rule(hub_uid, "KE", 21, "working", 2)
        with self.assertRaises(ShopError):
            self.s.save_leave_rule(self.admin, "KE", 21, "weeks", 2)
        self.s.save_leave_rule(self.admin, "KE", 21, "working", 2, "To check with a lawyer", checked=True)
        ke = self.s.leave_rule("ke")
        self.assertEqual((ke["min_days"], ke["checked_by"]), (21, self.admin))

    def test_work_on_a_public_holiday_is_paid_by_the_rule(self):
        krsk = self.russia()
        sid, uid = self.worker(krsk, "hub")
        self.contract(sid, amount=173333)                                          # about 1,000 roubles an hour
        month = first_of_last_month()
        hol, normal = month.replace(day=10), month.replace(day=11)
        self.s.save_holiday(self.admin, "RU", hol.isoformat(), "Test holiday")
        with self.s.db() as conn:
            for d in (hol, normal):
                conn.execute("INSERT INTO staff_shifts (staff_id, office_id, start_at, end_at, minutes) VALUES (?, ?, ?, ?, 480)",
                             (sid, krsk, f"{d.isoformat()} 09:00:00", f"{d.isoformat()} 17:00:00"))
        run = self.s.create_payroll(self.admin, "RU", month.strftime("%Y-%m"))
        slip = self.s.payroll_run(run)["slips"][0]
        lines = {ln[0]: ln for ln in slip["line_list"]}
        extra = lines["Work on public holidays"]
        per_min = 173333_00 / (40 * 52 / 12 * 60)
        self.assertEqual(extra[1], "8 h 00 min x2")
        self.assertEqual(extra[2], round(480 * per_min))                           # only the extra half: x2 - x1
        self.assertEqual(slip["gross"], lines["Salary"][2] + extra[2])

    def test_no_holiday_pay_where_the_rule_is_one(self):
        sid, _ = self.worker(self.kigali, "hub")
        self.contract(sid, 18, amount=250000)
        month = first_of_last_month()
        hol = month.replace(day=10)
        self.s.save_holiday(self.admin, "RW", hol.isoformat(), "Test holiday")
        with self.s.db() as conn:
            conn.execute("INSERT INTO staff_shifts (staff_id, office_id, start_at, end_at, minutes) VALUES (?, ?, ?, ?, 480)",
                         (sid, self.kigali, f"{hol.isoformat()} 09:00:00", f"{hol.isoformat()} 17:00:00"))
            c = dict(conn.execute("SELECT * FROM staff_contracts WHERE staff_id = ?", (sid,)).fetchone())
            self.assertIsNone(self.s.holiday_work_line(conn, {"id": sid}, c, month, month.replace(day=28)))

    def test_year_statement(self):
        sid, uid = self.worker(self.kigali, "hub")
        self.contract(sid, 18, amount=300000)
        _, fin = self.worker(self.rwanda, "finance")
        month = first_of_last_month()
        run = self.s.create_payroll(fin, "RW", month.strftime("%Y-%m"))
        self.s.approve_payroll(self.admin, run)
        self.s.pay_payroll(fin, run)
        st = self.s.year_statement(uid, month.year)
        self.assertEqual(len(st["months"]), 1)
        self.assertEqual(st["total"]["gross"], 300000_00)
        self.assertEqual(st["total"]["net"], st["total"]["gross"] - st["total"]["tax"] - st["total"]["social"])
        self.assertEqual((st["currency"], st["years"]), ("RWF", [month.year]))
        self.assertEqual(self.s.year_statement(uid, month.year - 3)["months"], [])
        self.assertIsNone(self.s.year_statement(self.admin, month.year))           # the administrator is no worker

    def test_holiday_factor(self):
        start = date(2027, 1, 4)                                                   # a Monday; India: 26 January
        self.s.save_holiday(self.admin, "IN", "2027-02-09", "Second test holiday")
        series = []
        for i in range(56):
            d = start + timedelta(days=i)
            series.append((d.isoformat(), 50 if d.isoformat() in ("2027-01-26", "2027-02-09") else 100))
        self.assertEqual(self.s.holiday_factor(series, "IN"), 0.5)
        self.assertIsNone(self.s.holiday_factor(series[:30], "IN"))                # one holiday is not enough
        self.assertIsNone(self.s.holiday_factor([], "IN"))


class TestClockIn(Base):
    def test_office_code_changes_every_minute(self):
        t = 1_800_000_000
        code = self.s.office_code(self.ambala, t)
        self.assertRegex(code, r"^\d{6}$")
        self.assertEqual(code, self.s.office_code(self.ambala, t + 30))            # the same minute
        self.assertNotEqual(code, self.s.office_code(self.ambala, t + 61))
        self.assertNotEqual(code, self.s.office_code(self.kigali, t))              # another office: another code
        self.assertTrue(self.s.office_code_ok(self.ambala, code, t))
        self.assertTrue(self.s.office_code_ok(self.ambala, f"{code[:3]} {code[3:]}", t + 60))   # the minute before
        self.assertFalse(self.s.office_code_ok(self.ambala, code, t + 121))
        self.assertFalse(self.s.office_code_ok(self.kigali, code, t))
        self.assertFalse(self.s.office_code_ok(self.ambala, "12345", t))

    def test_clock_in_with_code_network_or_gps(self):
        self.s.set_office_location(self.admin, self.ambala, 30.3375, 76.8562, 300, False)
        self.s.set_office_network(self.admin, self.ambala, "203.0.113.0/24, 2001:db8:12::/48")
        sid, uid = self.worker(self.ambala, "hub")
        with self.assertRaises(ShopError):                                         # far away, no code, another network
            self.s.clock_in_proved(uid, 28.61, 77.20, 20, "", "198.51.100.7")
        with self.assertRaises(ShopError):
            self.s.clock_in_proved(uid, None, None, None, "000000" if self.s.office_code(self.ambala) != "000000"
                                   else "111111", "198.51.100.7")
        self.assertEqual(self.s.clock_in_proved(uid, 28.61, 77.20, 20, self.s.office_code(self.ambala), ""), ("", "code"))
        self.s.clock_out(uid)
        self.assertEqual(self.s.clock_in_proved(uid, None, None, None, "", "203.0.113.45"), ("", "network"))
        self.s.clock_out(uid)
        self.assertEqual(self.s.clock_in_proved(uid, None, None, None, "", "2001:db8:12:1::5"), ("", "network"))
        self.s.clock_out(uid)
        self.assertEqual(self.s.clock_in_proved(uid, 30.3376, 76.8563, 15, "", "198.51.100.7"), ("", "gps"))
        with self.s.db() as conn:
            proofs = [r["in_proof"] for r in conn.execute("SELECT in_proof FROM staff_shifts WHERE staff_id = ? ORDER BY id",
                                                          (sid,))]
        self.assertEqual(proofs, ["code", "network", "network", "gps"])

    def test_office_network_is_checked(self):
        for bad in ("office wifi", "10.0.0.0/8", "2001:db8::/16", ", ".join(f"203.0.{i}.0/24" for i in range(9))):
            with self.assertRaises(ShopError):
                self.s.set_office_network(self.admin, self.ambala, bad)
        _, rw_boss = self.worker(self.rwanda, "country_manager", manager=True)
        with self.assertRaises(ShopError):
            self.s.set_office_network(rw_boss, self.ambala, "203.0.113.0/24")      # not his branch
        self.assertFalse(self.s.ip_in_ranges("not an ip", "203.0.113.0/24"))
        self.assertFalse(self.s.ip_in_ranges("203.0.113.5", ""))


class TestEInvoice(unittest.TestCase):
    def setUp(self):
        self.s = support.make_store(seed=True)
        self.uid = self.s.register("Test User", "einv.user@realmail.local", "9811111111", "Secret123")
        self.addr = self.s.add_address(self.uid, ADDR)
        self.admin = self.s.find_user("admin@example.com")["id"]

    def test_irn_of_the_official_example(self):
        self.assertEqual(einvoice.irn("29AAFCC9980M1ZR", "2019-20", "INV", "2019-20/KA/1"),
                         "23f498ee41441ecad30f72ba5b9907506c3df70a17b0e0dff46b76a786400662")
        self.assertEqual(len(einvoice.irn("27aaaaa0000a1z5", "2026-27", "CRN", "YO/2026-27/000001")), 64)

    def test_einvoice_of_a_tax_invoice(self):
        with self.s.db() as conn:
            pid = conn.execute("SELECT id FROM products WHERE seller_id IS NULL AND active = 1 AND category = 'books' "
                               "AND stock > 0 ORDER BY price LIMIT 1").fetchone()[0]
        self.s.save_cart(self.uid, {str(pid): 2})
        number = self.s.place_order(self.uid, self.addr, "cod")
        with self.s.db() as conn:
            oid = conn.execute("SELECT id FROM orders WHERE number = ?", (number,)).fetchone()[0]
        inv = self.s.issue_invoices(oid)[0]
        e = self.s.einvoice(inv["id"])
        full = self.s.invoice(inv["id"])
        self.assertEqual(e["irn"], hashlib.sha256(f"{full['issuer_tax_id']}{e['fin_year']}INV{full['number']}".encode()).hexdigest())
        doc = e["json"]
        self.assertEqual((doc["Version"], doc["DocDtls"]["Typ"], doc["DocDtls"]["No"]), ("1.1", "INV", full["number"]))
        self.assertEqual(doc["ValDtls"]["TotInvVal"], full["total"] / 100)
        self.assertEqual(round(sum(i["TotItemVal"] for i in doc["ItemList"]), 2), full["total"] / 100)
        self.assertEqual(doc["ItemList"][0]["HsnCd"], "4901")
        qr = json.loads(e["qr"])
        self.assertEqual((qr["Irn"], qr["ItemCnt"], qr["Demo"]), (e["irn"], len(doc["ItemList"]), "not registered with the IRP"))
        self.assertTrue(any("16" in p for p in e["problems"]))                     # our numbers are longer than 16
        self.assertTrue(any("not signed" in p for p in e["problems"]))
        self.assertEqual(json.loads(self.s.einvoice_json(inv["id"]))["SellerDtls"]["Gstin"], full["issuer_tax_id"])
        self.assertIsNone(self.s.einvoice(999999))

    def test_rates_are_checked_by_a_second_person(self):
        kavya = self.s.find_user("kavya.iyer@example.com")["id"]                   # finance: tax.manage
        neha = self.s.find_user("neha.gill@example.com")["id"]                     # support: no tax.manage
        with self.assertRaises(ShopError):
            self.s.mark_rate_checked(neha, "country", "RW")
        self.s.mark_rate_checked(kavya, "country", "RW", "2026-01-01", "", "Law of the country (test)")
        rw = next(r for r in self.s.rates_review()["countries"] if r["country"] == "RW")
        self.assertEqual((rw["status"], rw["checked_name"]), ("Checked", "Kavya Iyer"))
        self.s.save_tax_rule(self.admin, "KE", "VAT", 16, 16, "typed by the administrator")
        with self.assertRaises(ShopError):
            self.s.mark_rate_checked(self.admin, "country", "KE")                  # he typed it himself
        self.s.mark_rate_checked(kavya, "country", "KE")
        self.s.save_category_rate(kavya, "IN", "books", 5, 0, "changed")
        books = next(r for r in self.s.category_rates("IN") if r["category"] == "books")
        with self.assertRaises(ShopError):
            self.s.mark_rate_checked(kavya, "category", books["id"])               # her own change
        self.s.mark_rate_checked(self.admin, "category", books["id"], "2026-01-01", "2026-06-30")
        review = self.s.rates_review(today=date(2026, 7, 1))
        self.assertEqual(next(r for r in review["categories"] if r["id"] == books["id"])["status"], "Expired")
        self.assertEqual(self.s.rate_status({"checked_at": "2026-01-01 10:00:00", "updated_at": "2026-02-01 10:00:00"}),
                         "Changed after the check")
        self.assertEqual(self.s.rate_status({"checked_at": ""}), "Example")
        self.assertEqual(self.s.rate_status({"checked_at": "2026-01-01 10:00:00", "updated_at": "2025-01-01 10:00:00",
                                             "valid_from": "2099-01-01"}), "Not valid yet")
        with self.assertRaises(ShopError):
            self.s.mark_rate_checked(kavya, "country", "RW", "2026-05-01", "2026-01-01")


class TestWebhooks(Couriers):
    URL = "https://api.partner-company.test.yo-check.net/hook"

    def partner(self, url=None):
        key_id, secret = self.s.add_partner(self.admin, "Hill Couriers", "RW")
        pid = self.s.partners()[0]["id"]
        self.s.set_partner_webhook(self.admin, pid, url or self.URL)
        return pid, secret

    def test_addresses_that_are_refused(self):
        for bad in ("http://partner.example/hook", "https://127.0.0.1/hook", "https://localhost/hook", "https://intranet/hook",
                    "https://user:pw@partner.example/hook", "https://partner.example:22/hook", "ftp://partner.example/x",
                    "https://[::1]/hook"):
            with self.assertRaises(ShopError, msg=bad):
                webhooks.check_url(bad)
        self.assertEqual(webhooks.check_url(" https://partner.example:8443/hook ")[1], "partner.example")
        self.assertTrue(webhooks.is_demo_host("hooks.highland-express.example"))
        self.assertTrue(webhooks.is_demo_host("example.com"))
        self.assertFalse(webhooks.is_demo_host("example.com.evil.net"))
        key_id, _ = self.s.add_partner(self.admin, "Bad Partner", "RW")
        with self.assertRaises(ShopError):
            self.s.set_partner_webhook(self.admin, self.s.partners()[0]["id"], "http://insecure.example/hook")
        with self.assertRaises(ShopError):
            self.s.set_partner_webhook(self.uid, self.s.partners()[0]["id"], self.URL)       # a customer

    def test_signed_message_is_sent_once(self):
        pid, secret = self.partner()
        got = []
        self.s.webhook_sender = lambda url, body, headers: got.append((url, body, headers)) or 204
        number = self.book()
        with self.s.db() as conn:
            conn.execute("UPDATE shipments SET status = 'Booked', courier_id = NULL WHERE number = ?", (number,))
        self.s.hand_to_partner(self.admin, number, pid)
        self.assertEqual(self.s.run_jobs(5)["ok"], 1)
        url, body, headers = got[0]
        self.assertEqual(url, self.URL)
        self.assertTrue(providers.signature_ok(secret, headers["X-Yo-Signature"], body))
        self.assertFalse(providers.signature_ok("another-secret", headers["X-Yo-Signature"], body))
        msg = json.loads(body)
        self.assertEqual((msg["type"], msg["number"], headers["X-Yo-Event"]), ("shipment.assigned", number, "shipment.assigned"))
        self.assertEqual(msg["data"]["to_city"], "Chandigarh")
        d = self.s.webhook_deliveries(pid)[0]
        self.assertEqual((d["status"], d["tries"], d["last_code"]), ("Sent", 1, 204))
        self.assertEqual(self.s.send_webhook(d["id"]), "skip")                     # never sent twice
        self.assertEqual(len(got), 1)

    def test_failed_message_is_tried_again_and_can_be_resent(self):
        pid, _ = self.partner()
        answers = [500, 503]
        self.s.webhook_sender = lambda url, body, headers: answers.pop(0) if answers else 200
        did = self.s.ping_partner_webhook(self.admin, pid)
        self.assertEqual(self.s.run_jobs(5)["retry"], 1)
        d = self.s.webhook_deliveries(pid)[0]
        self.assertEqual((d["status"], d["tries"], d["last_code"]), ("Waiting", 1, 500))
        with self.s.db() as conn:                                                  # the job waits one minute: make it due
            conn.execute("UPDATE jobs SET run_at = ? WHERE kind = 'webhook'", (now(),))
        self.assertEqual(self.s.run_jobs(5)["retry"], 1)
        with self.s.db() as conn:
            conn.execute("UPDATE jobs SET run_at = ? WHERE kind = 'webhook'", (now(),))
        self.assertEqual(self.s.run_jobs(5)["ok"], 1)
        self.assertEqual(self.s.webhook_deliveries(pid)[0]["status"], "Sent")
        with self.assertRaises(ShopError):
            self.s.resend_webhook(self.admin, did)                                 # only a failed one
        with self.s.db() as conn:
            conn.execute("UPDATE webhook_deliveries SET status = 'Waiting', tries = ? WHERE id = ?", (webhooks.MAX_TRIES - 1, did))
        self.s.webhook_sender = lambda url, body, headers: 410
        with self.assertRaises(ShopError):
            self.s.send_webhook(did)
        self.assertEqual(self.s.webhook_deliveries(pid)[0]["status"], "Failed")    # the last try
        self.s.webhook_sender = lambda url, body, headers: 200
        self.s.resend_webhook(self.admin, did)
        self.s.run_jobs(5)
        self.assertEqual(self.s.webhook_deliveries(pid)[0]["status"], "Sent")

    def test_made_up_address_is_never_called(self):
        pid, _ = self.partner("https://hooks.hill-couriers.example/yo")
        did = self.s.ping_partner_webhook(self.admin, pid)
        self.assertEqual(self.s.send_webhook(did), "test")                         # webhook_sender is None: the real path
        d = self.s.webhook_deliveries(pid)[0]
        self.assertEqual((d["status"], d["last_code"]), ("Test (not sent)", 0))

    def test_cancelled_parcel_and_partner_without_webhook(self):
        pid, _ = self.partner()
        self.s.webhook_sender = lambda url, body, headers: 200
        number = self.book()
        with self.s.db() as conn:
            conn.execute("UPDATE shipments SET status = 'Booked', courier_id = NULL WHERE number = ?", (number,))
        self.s.hand_to_partner(self.admin, number, pid)
        self.s.cancel_shipment(self.uid, number)
        self.assertEqual([d["event"] for d in self.s.webhook_deliveries(pid)], ["shipment.cancelled", "shipment.assigned"])
        self.s.set_partner_webhook(self.admin, pid, "")
        self.assertIsNone(self.s.queue_webhook(pid, "ping"))
        with self.assertRaises(ShopError):
            self.s.ping_partner_webhook(self.admin, pid)
        with self.assertRaises(ShopError):
            self.s.queue_webhook(pid, "shipment.exploded")


class TestCourierSpeed(Couriers):
    def track(self, number, start, kmh, points=30, step_s=30):
        """GPS points going east at a steady speed (km/h along the road factor of the route plan)."""
        metres = kmh / routeplan.ROAD * 1000 / 3600 * step_s
        with self.s.db() as conn:
            sid = conn.execute("SELECT id FROM shipments WHERE number = ?", (number,)).fetchone()[0]
            for i in range(points):
                t = start + timedelta(seconds=i * step_s)
                conn.execute("INSERT INTO courier_track (shipment_id, lat, lng, at) VALUES (?, ?, ?, ?)",
                             (sid, 30.34, 76.80 + i * metres / 96_090, t.strftime("%Y-%m-%d %H:%M:%S")))

    def test_speed_is_learned_by_town_and_hour(self):
        a, b = self.book(to_city="Ambala Cantt"), self.book(to_city="Ambala Cantt")
        day = datetime.now().replace(hour=9, minute=0, second=0, microsecond=0) - timedelta(days=2)
        self.track(a, day, 12)                                                     # slow at 9
        self.track(b, day.replace(hour=13), 30)                                    # fast at 13
        self.assertEqual(self.s.build_speed_profile(), 3)                          # 9 h, 13 h and the whole day
        prof = {r["hour"]: r for r in self.s.speed_profile("Ambala Cantt")}
        self.assertAlmostEqual(prof[9]["kmh"], 12, delta=0.6)
        self.assertAlmostEqual(prof[13]["kmh"], 30, delta=1.5)
        self.assertEqual(prof[-1]["samples"], 58)
        speed, note = self.s.speed_function("Ambala Cantt")
        self.assertAlmostEqual(speed(9 * 60 + 20), 12, delta=0.6)
        self.assertAlmostEqual(speed(18 * 60), prof[-1]["kmh"])                    # no data at 18 h: the whole day
        self.assertIn("58 GPS measurements", note)
        self.assertEqual(self.s.speed_function("Kigali"), (None, ""))

    def test_bad_points_are_left_out(self):
        a = self.book(to_city="Ambala Cantt")
        day = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0) - timedelta(days=1)
        self.track(a, day, 2, points=25)                                           # standing in traffic or at a door
        self.track(a, day + timedelta(hours=2), 140, points=25)                    # a GPS jump
        self.assertEqual(self.s.build_speed_profile(), 0)

    def test_route_plan_uses_the_learned_speed(self):
        order = [{"point": (30.37, 76.90), "window": None}]
        slow = routeplan.timeline((30.37, 76.78), order, 540)[0][0][0]             # 25 km/h
        routeplan.set_speed(lambda minute: 50)
        try:
            fast = routeplan.timeline((30.37, 76.78), order, 540)[0][0][0]
        finally:
            routeplan.set_speed(None)
        self.assertAlmostEqual((slow - 540) / (fast - 540), 2, places=3)
        self.assertEqual(routeplan.timeline((30.37, 76.78), order, 540)[0][0][0], slow)


class TestLiveTravel(Base):
    def feed(self):
        with self.s.db() as conn:
            cur = conn.execute("INSERT INTO gtfs_feeds (name, operator_id, mode, created_at, rt_url) VALUES "
                               "('Test feed', 1, 'bus', ?, 'https://feeds.transit-company.example/rt.pb')", (now(),))
            return cur.lastrowid

    def test_vehicles_and_alerts_are_read(self):
        blob = v8extra.sample_live_feed(vehicles=[("T1", -1.95, 30.06, "Bus 7"), ("", 1.0, 1.0, "no trip"), ("T2", 0, 0, "zero")],
                                        alerts=[("a1", "T1", "Road closed", "Use the other stop.", 4), ("a2", "", "Strike on Monday", "", 1)])
        self.assertEqual(v8extra.read_vehicles(blob), [{"trip_id": "T1", "label": "Bus 7", "lat": -1.95, "lng": 30.06,
                                                         "status": "On the way to the next stop",
                                                         "at": v8extra.read_vehicles(blob)[0]["at"]}])
        alerts = v8extra.read_alerts(blob)
        self.assertEqual([(a["trip_id"], a["header"], a["effect"]) for a in alerts],
                         [("T1", "Road closed", "Detour"), ("", "Strike on Monday", "No service")])
        with self.assertRaises(ShopError):
            v8extra.read_alerts(b"\xff\xfenot a feed")

    def test_trip_shows_its_vehicle_and_alerts(self):
        fid = self.feed()
        blob = v8extra.sample_live_feed(vehicles=[("T1", -1.95, 30.06, "Bus 7")],
                                        alerts=[("a1", "T1", "Road closed", "Use the other stop.", 4), ("a2", "", "Strike on Monday", "", 1),
                                                ("a3", "T9", "Other trip", "", 3)])
        done = self.s.apply_gtfs_rt(fid, blob)
        self.assertEqual((done["vehicles"], done["alerts"]), (1, 3))
        self.s.apply_gtfs_rt(fid, blob)                                            # the same file again: nothing doubles
        self.assertEqual(len(self.s.gtfs_alerts(fid)), 3)
        live = self.s.feed_live(f"GT{fid}-T1-0-1")
        self.assertEqual(live["vehicle"]["label"], "Bus 7")
        self.assertEqual([a["header"] for a in live["alerts"]], ["Strike on Monday", "Road closed"])
        self.assertEqual(self.s.feed_live(f"GT{fid}-T9-0-1")["vehicle"], None)
        later = datetime.now() + timedelta(minutes=20)
        self.assertIsNone(self.s.feed_live(f"GT{fid}-T1-0-1", at=later)["vehicle"])   # an old position is not shown
        self.assertEqual(self.s.feed_live(f"GT{fid}-T1-0-1", at=datetime.now() + timedelta(days=2))["alerts"], [])
        self.assertEqual(self.s.feed_live("bus:delhi>agra:1"), {"vehicle": None, "alerts": []})

    def test_live_feeds_are_polled_in_the_background(self):
        fid = self.feed()
        calls = []
        self.s.rt_fetcher = lambda url: calls.append(url) or v8extra.sample_live_feed(alerts=[("a1", "", "Snow", "", 3)])
        at = datetime(2027, 1, 5, 10, 7)
        self.assertEqual(self.s.gtfs_poll_due(at), "queued")
        self.assertIsNone(self.s.gtfs_poll_due(at.replace(minute=6)))              # the same two minutes: once
        self.assertEqual(self.s.run_jobs(5)["ok"], 1)
        self.assertEqual(calls, ["https://feeds.transit-company.example/rt.pb"])
        self.assertEqual(self.s.gtfs_alerts(fid)[0]["header"], "Snow")
        self.assertEqual(self.s.gtfs_poll_due(at.replace(minute=8)), "queued")

        def broken(url):
            raise OSError("no network")
        self.s.rt_fetcher = broken
        self.assertEqual(self.s.poll_gtfs_rt(), {"feeds": 1, "ok": 0, "failed": 1})
        with self.s.db() as conn:
            self.assertEqual(conn.execute("SELECT rt_result FROM gtfs_feeds WHERE id = ?", (fid,)).fetchone()[0], "Not read: OSError")
        self.s.rt_fetcher = None                                                   # the real path: a made-up host
        self.assertEqual(self.s.poll_gtfs_rt()["failed"], 1)
        with self.s.db() as conn:
            conn.execute("UPDATE gtfs_feeds SET rt_url = ''")
        self.assertIsNone(self.s.gtfs_poll_due(at.replace(minute=30)))             # no address: nothing to do


class TestHelpDesk(Couriers):
    env_secret = "inbound-test-secret"

    def test_messages_after_the_last_one(self):
        other = self.s.register("Other", "other2.v8@realmail.local", "9811111155", "Secret123")
        number = self.s.open_support_ticket(self.uid, "parcel", "Where is my parcel", "It is late since Monday")
        first = self.s.support_messages_after(number, self.uid)
        self.assertEqual(len(first["messages"]), 1)
        last = first["messages"][-1]["id"]
        self.assertEqual(self.s.support_messages_after(number, self.uid, last)["messages"], [])
        self.s.support_reply(self.admin, number, "It arrives tomorrow.")
        new = self.s.support_messages_after(number, self.uid, last)
        self.assertEqual([(m["body"], m["staff"]) for m in new["messages"]], [("It arrives tomorrow.", True)])
        self.assertEqual(new["status"], "Answered")
        self.assertIsNone(self.s.support_messages_after(number, other))            # not his question
        self.assertEqual(len(self.s.support_messages_after(number, self.admin, "abc")["messages"]), 2)

    def test_email_from_a_customer(self):
        result, number = self.s.inbound_email("<m1@mail.test>", "Sender <SENDER.V7@realmail.local>", "My parcel is late",
                                              "Hello, where is it?\nOn Mon, 3 May 2027 Yo wrote:\n> old text")
        self.assertEqual(result, "opened")
        t = self.s.support_ticket(number)
        self.assertEqual((t["topic"], t["subject"], t["messages"][0]["body"]), ("parcel", "My parcel is late", "Hello, where is it?"))
        self.assertEqual(self.s.inbound_email("<m1@mail.test>", "sender.v7@realmail.local", "again", "x"), ("duplicate", ""))
        self.s.support_reply(self.admin, number, "We look for it.")
        self.assertEqual(self.s.inbound_email("<m2@mail.test>", "sender.v7@realmail.local", f"Re: [{number}] My parcel is late",
                                              "Thank you!"), ("replied", number))
        self.assertEqual(self.s.support_ticket(number)["messages"][-1]["body"], "Thank you!")
        self.assertEqual(self.s.inbound_email("<m3@mail.test>", "stranger@realmail.local", "Hi", "Hello")[0], "unknown sender")
        self.assertEqual(self.s.inbound_email("", "sender.v7@realmail.local", "Hi", "Hello")[0], "refused: bad message")
        other = self.s.register("Other", "other3.v8@realmail.local", "9811111144", "Secret123")
        result, n2 = self.s.inbound_email("<m4@mail.test>", "other3.v8@realmail.local", f"[{number}] not mine", "Let me in")
        self.assertEqual(result, "opened")                                         # a new question, not a reply to
        self.assertNotEqual(n2, number)                                            # somebody else's question
        self.assertEqual(self.s.support_ticket(n2)["user_id"], other)
        log = self.s.inbound_email_log()
        self.assertEqual(len(log), 4)
        self.assertNotIn("realmail", json.dumps(log))                              # no e-mail address is kept

    def test_topic_of_an_email(self):
        self.assertEqual(v8extra.topic_of("My bus ticket"), "ticket")
        self.assertEqual(v8extra.topic_of("Refund for order OD123"), "money")
        self.assertEqual(v8extra.topic_of("I forgot my password"), "account")
        self.assertEqual(v8extra.topic_of("Hello there"), "other")

    def test_inbound_endpoint_needs_the_secret(self):
        body, header = v8extra.sign_json(self.env_secret, {"message_id": "<x@y>"})
        self.assertFalse(self.s.inbound_email_ok(header, body))                    # no secret set: switched off
        s2 = support.make_store(seed=False, env={"INBOUND_EMAIL_SECRET": self.env_secret})
        self.assertTrue(s2.inbound_email_ok(header, body))
        self.assertFalse(s2.inbound_email_ok(header, body + b" "))
        self.assertFalse(s2.inbound_email_ok("t=1,v1=00", body))


class TestBackground(Base):
    def test_analytics_are_rebuilt_once_an_hour(self):
        at = datetime(2027, 3, 1, 14, 5)
        self.assertEqual(self.s.hourly_warehouse(at), "queued")
        self.assertIsNone(self.s.hourly_warehouse(at.replace(minute=50)))
        self.assertEqual(self.s.hourly_warehouse(at.replace(hour=15)), "queued")
        with self.s.db() as conn:
            kinds = sorted(r["kind"] for r in conn.execute("SELECT kind FROM jobs WHERE status = 'waiting'"))
        self.assertEqual(kinds, ["speed_profile", "warehouse"])                     # the same job is not queued twice
        self.assertEqual(self.s.run_jobs(5)["ok"], 2)
        self.assertIn("webhook", self.s.job_handlers())
        self.assertIn("gtfs_live", self.s.job_handlers())


class TestPartnerPage(unittest.TestCase):
    def test_parcel_page_opens_after_partner_events(self):
        """Found by crawling every page: an event of a partner was saved with a "T" in its time and the parcel page crashed."""
        app = support.make_app(seed=True)
        with app.store.db() as conn:
            rows = conn.execute("SELECT s.number, u.email FROM shipments s JOIN users u ON u.id = s.user_id WHERE "
                                "s.partner_id IS NOT NULL").fetchall()
            times = [r["at"] for r in conn.execute("SELECT at FROM shipment_events")]
        self.assertTrue(rows)
        self.assertFalse([t for t in times if "T" in t])
        c = app.test_client()
        support.login(c, "demo@example.com", "Demo@1234")
        for r in rows:
            if r["email"] == "demo@example.com":
                self.assertEqual(c.get(f"/courier/s/{r['number']}").status_code, 200, r["number"])


class TestBackup(Base):
    def test_backup_keeps_the_site_content(self):
        self.s.save_contacts(self.admin, {"phone": "+250 788 000 222"})
        self.s.save_site_page(self.admin, "terms", "en", "Terms of use", "The terms before the backup was made.")
        self.s.post_staff_news(self.admin, "Before the backup", "")
        data = self.s.unpack(self.s.pack(self.s.export_data()))
        self.assertGreaterEqual(data["version"], 8)                                 # 9 since version 9
        self.s.save_contacts(self.admin, {"phone": "+250 788 000 333"})
        self.s.save_site_page(self.admin, "terms", "en", "Terms of use", "The terms after the backup was made.")
        self.s.import_data(data)
        self.assertEqual(self.s.contacts()["phone"], "+250 788 000 222")
        self.assertEqual(self.s.site_page("terms")["body"], "The terms before the backup was made.")
        self.assertEqual(len(self.s.page_versions("terms")), 1)
        self.assertEqual([n["title"] for n in self.s.staff_news_list(self.admin)], ["Before the backup"])
        old = dict(data, version=7, tables={t: rows for t, rows in data["tables"].items() if t not in (
            "site_contacts", "site_pages", "site_page_versions", "site_slides", "kb_articles", "staff_news", "staff_news_seen",
            "public_holidays", "leave_rules")})
        self.s.import_data(old)                                                    # a backup of version 7
        self.assertEqual(len(self.s.site_pages()), 6)                              # the first texts are back
        self.assertEqual(self.s.contacts()["phone"], "1800-000-9696")
        self.assertEqual(len(self.s.holidays("RW", 2027)), 17)
        self.assertEqual(self.s.leave_rule("RU")["min_days"], 28)


class TestDemo(unittest.TestCase):
    def test_demo_content_of_version_8_once(self):
        import demo_data
        s = support.make_store(seed=True)

        def counts():
            with s.db() as conn:
                return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]       # noqa: S608
                        for t in ("staff_news", "site_pages", "site_slides", "kb_articles", "public_holidays", "leave_rules",
                                  "webhook_deliveries", "speed_profile", "gtfs_alerts", "gtfs_vehicles", "inbound_emails",
                                  "courier_track", "support_tickets")}
        first = counts()
        self.assertEqual((first["staff_news"], first["site_pages"], first["site_slides"], first["leave_rules"],
                          first["webhook_deliveries"], first["gtfs_alerts"], first["gtfs_vehicles"], first["inbound_emails"]),
                         (3, 8, 4, 3, 2, 1, 1, 1))
        self.assertEqual({d["status"] for d in s.webhook_deliveries()}, {"Test (not sent)"})   # a made-up address
        self.assertEqual(s.site_page("returns", "ru")["lang"], "ru")
        self.assertEqual(s.rates_review()["counts"].get("Checked"), 3)
        self.assertGreaterEqual(first["speed_profile"], 10)
        speed, note = s.speed_function("Ambala Cantt")
        self.assertLess(speed(18 * 60), speed(13 * 60))                            # the evening is slower
        neha = s.find_user("neha.gill@example.com")["id"]
        self.assertEqual(s.work_home(neha)["endpoint"], "admin_support")
        self.assertEqual(len(s.staff_news_for(s.find_user("sunil.kumar@example.com")["id"])), 2)
        demo_data.seed(s)                                                          # again: nothing new
        self.assertEqual(counts(), first)


class TestPages(unittest.TestCase):
    def setUp(self):
        self.app = support.make_app(seed=True, env={"INBOUND_EMAIL_SECRET": "inbound-test-secret"})
        self.c = self.app.test_client()
        self.s = self.app.store

    def test_menu_is_consistent_with_the_page_rules(self):
        rules = {r.endpoint: r.rule for r in self.app.url_map.iter_rules()}
        for endpoint, _, label, match, _, need, group, _ in services.NAV:
            self.assertIn(endpoint, rules, label)
            self.assertIn(group, services.GROUPS)
            for m in match:
                self.assertIn(m, rules, m)
            if endpoint in ("board_page", "admin_account"):
                continue
            self.assertEqual(staff_rules.ability_for_path(rules[endpoint]), need, label)   # shown = may be opened
        for lst in (services.WORKER_PAGES, services.PUBLIC, services.ACCOUNT):
            for item in lst:
                self.assertIn(item[0], rules, item[0])
        for endpoint in list(services.ROLE_HOME.values()) + [e for _, e in services.ABILITY_HOME]:
            self.assertIn(endpoint, rules, endpoint)
        self.assertEqual(set(services.ROLE_HOME), set(staff_rules.ROLES))

    def test_header_menu_and_work_button(self):
        page = self.c.get("/").get_data(as_text=True)
        self.assertIn('id="menuBtn"', page)
        self.assertIn('id="catRow"', page)
        self.assertNotIn('class="work-btn"', page)
        for slug, name, _ in [(c["slug"], c["name"], 0) for c in self.s.categories()]:
            self.assertIn(f'/c/{slug}"', page)
        self.assertEqual(page.count("slide-h"), 4)                                 # the banners come from the database
        drawer = self.c.get("/menu?drawer=1").get_data(as_text=True)
        self.assertNotIn("<html", drawer)                                          # a piece of a page for the drawer
        self.assertIn("Sign in", drawer)
        self.assertIn("Shop by category", drawer)
        self.assertNotIn("My work", drawer)
        self.assertEqual(self.c.get("/work").status_code, 302)                     # sign in first
        r = support.login(self.c, "neha.gill@example.com", "Staff@2026")
        self.assertTrue(r.headers["Location"].endswith("/staff"))
        home = self.c.get("/staff").get_data(as_text=True)
        self.assertIn("You are signed in as Customer support (Chandigarh support centre)", home)
        self.assertIn('class="work-btn"', home)
        self.assertIn("Your work starts here", home)
        self.assertIn("News from my managers", home)
        self.assertIn("My manager", home)
        self.assertTrue(self.c.get("/work").headers["Location"].endswith("/admin/support"))
        drawer = self.c.get("/menu?drawer=1").get_data(as_text=True)
        self.assertIn("My position", drawer)
        self.assertIn("Support desk", drawer)
        self.assertNotIn("Payroll", drawer)
        account = self.c.get("/account").get_data(as_text=True)
        self.assertIn("My job at Yo Delivery", account)
        self.assertIn("Rohit Malhotra", account)                                   # her manager
        found = self.c.get("/menu?q=claims").get_data(as_text=True)
        self.assertIn("Parcel claims", found)
        self.assertNotIn("Coupons", found)
        self.assertEqual(self.c.get("/staff/directory").status_code, 200)
        self.assertEqual(self.c.get("/staff/statement").status_code, 200)
        self.assertEqual(self.c.get("/staff/clock-code?c=123456").status_code, 200)

    def test_customer_has_no_worker_pages(self):
        support.login(self.c, "demo@example.com", "Demo@1234")
        page = self.c.get("/account").get_data(as_text=True)
        self.assertNotIn("My job at Yo Delivery", page)
        self.assertNotIn('class="work-btn"', page)
        self.assertTrue(self.c.get("/work").headers["Location"].endswith("/account"))
        self.assertEqual(self.c.get("/staff/directory").status_code, 404)
        self.assertEqual(self.c.get("/staff/statement").status_code, 404)
        self.assertEqual(self.c.get("/admin/find?q=demo").status_code, 403)
        data = self.c.get("/find.json?q=wallet").get_json()
        self.assertIn("/pay", [r["url"] for r in data["results"]])
        self.assertFalse(any(r["url"].startswith("/admin") for r in data["results"]))

    def test_site_content_is_edited_in_the_admin_panel(self):
        support.login(self.c, "admin@example.com", "Admin@1234")
        tok = support.token(self.c, "/admin/site")
        r = self.c.post("/admin/site/contacts", data={"csrf_token": tok, "company": "Yo Delivery (demo)", "email": "help@yodelivery.example",
                                                      "phone": "+250 788 123 000", "address": "KN 5 Rd\nKigali, Rwanda"})
        self.assertEqual(r.status_code, 302)
        page = self.c.get("/").get_data(as_text=True)
        self.assertIn("+250 788 123 000", page)
        self.assertIn("KN 5 Rd", page)
        self.assertNotIn("12 Mall Road", page)
        contact = self.c.get("/contact").get_data(as_text=True)
        self.assertIn("help@yodelivery.example", contact)
        r = self.c.post("/admin/site/pages/about", data={"csrf_token": tok, "lang": "en", "title": "About us",
                                                         "body": "## Our story\n\nWe changed this text in the admin panel.",
                                                         "published": "1", "in_footer": "1"})
        self.assertEqual(r.status_code, 302)
        about = self.c.get("/about").get_data(as_text=True)
        self.assertIn("<h2>Our story</h2>", about)
        self.assertIn("We changed this text in the admin panel.", about)
        r = self.c.post("/admin/site/pages/new", data={"csrf_token": tok, "lang": "en", "slug": "", "title": "Gift cards",
                                                       "body": "Gift cards come soon to the demo shop.", "published": "1"})
        self.assertTrue(r.headers["Location"].endswith("/admin/site/pages/gift-cards?lang=en"))
        self.assertIn("Gift cards come soon", self.c.get("/page/gift-cards").get_data(as_text=True))
        self.assertEqual(self.c.get("/page/unknown-page").status_code, 404)
        r = self.c.post("/admin/kb", data={"csrf_token": tok, "topic": "Orders", "question": "Do you wrap gifts?",
                                           "answer": "Yes, choose gift wrap in the cart.", "published": "1"})
        self.assertEqual(r.status_code, 302)
        self.assertIn("Do you wrap gifts?", self.c.get("/help").get_data(as_text=True))
        for url in ("/admin/site", "/admin/site/pages/about?lang=ru", "/admin/site/pages/new", "/admin/kb", "/admin/kb?edit=1",
                    "/admin/staff/notices", "/admin/staff/holidays?country=RU&year=2027", "/admin/couriers/speed",
                    "/admin/find?q=payroll", "/admin/taxes", "/admin/tax-categories", "/admin/partners", "/admin/tickets/gtfs",
                    "/admin/forecast", "/admin/offices"):
            self.assertEqual(self.c.get(url).status_code, 200, url)
        ambala = next(o["id"] for o in self.s.offices() if o["name"] == "Ambala hub")
        code_page = self.c.get(f"/admin/offices/{ambala}/code").get_data(as_text=True)
        self.assertIn(self.s.office_code(ambala)[:3], code_page)
        layout = self.c.get("/admin").get_data(as_text=True)
        self.assertIn('id="admFilter"', layout)
        for group in services.GROUPS:
            self.assertIn(f"<h3>{group}</h3>", layout)
        found = self.c.get("/admin/find?q=anita.verma").get_data(as_text=True)       # a customer account and a worker
        self.assertIn("Anita Verma", found)
        self.assertIn('href="/admin/staff/', found)
        with self.s.db() as conn:
            parcel = conn.execute("SELECT number FROM shipments ORDER BY id LIMIT 1").fetchone()[0]
        self.assertTrue(self.c.get(f"/admin/find?q={parcel}").headers["Location"].endswith(f"/admin/shipments/{parcel}"))

    def test_worker_without_the_ability_cannot_edit_the_site(self):
        support.login(self.c, "neha.gill@example.com", "Staff@2026")
        self.assertEqual(self.c.get("/admin/site").status_code, 403)
        self.assertEqual(self.c.get("/admin/kb").status_code, 403)
        tok = support.token(self.c, "/staff")
        self.assertEqual(self.c.post("/admin/site/contacts", data={"csrf_token": tok, "phone": "123456"}).status_code, 403)
        layout = self.c.get("/admin/support").get_data(as_text=True)
        self.assertIn("Support desk", layout)
        self.assertNotIn("Site content", layout)
        self.assertNotIn("<h3>Money</h3>", layout)
        self.assertIn("Customer support", layout)                                  # her position in the top bar
        self.assertEqual(self.c.get("/admin/find?q=payroll").status_code, 200)

    def test_pages_in_the_language_of_the_visitor(self):
        self.assertIn("Returns and refunds", self.c.get("/page/returns").get_data(as_text=True))
        self.c.get("/lang/ru")
        ru = self.c.get("/page/returns").get_data(as_text=True)
        self.assertIn("Возврат и возврат денег", ru)
        self.assertIn("1800-000-9696", ru)                                         # the placeholder {phone}

    def test_help_centre(self):
        page = self.c.get("/help").get_data(as_text=True)
        self.assertIn("How long does delivery take?", page)
        self.assertIn('id="payments"', page)
        found = self.c.get("/help?q=refund").get_data(as_text=True)
        self.assertIn("When do I get my refund?", found)
        self.assertNotIn("How do I send a parcel?", found)
        self.assertIn("No article answers this yet", self.c.get("/help?q=zzzz").get_data(as_text=True))
        art = self.c.get("/help/a/can-i-cancel-my-order")
        self.assertEqual(art.status_code, 200)
        tok = support.token(self.c, "/help/a/can-i-cancel-my-order")
        self.c.post("/help/a/can-i-cancel-my-order", data={"csrf_token": tok, "helpful": "1"})
        a = self.s.kb_article(slug="can-i-cancel-my-order")
        self.assertGreaterEqual(a["opened"], 2)
        data = self.c.get("/help/suggest.json?q=where is my order").get_json()
        self.assertEqual(data["articles"][0]["question"], "Where is my order?")
        self.assertEqual(self.c.get("/help/a/no-such-article").status_code, 404)
        txt = self.c.get("/.well-known/security.txt")
        self.assertEqual(txt.mimetype, "text/plain")
        self.assertIn("Contact: mailto:security@yodelivery.example", txt.get_data(as_text=True))
        self.assertIn("Expires: ", txt.get_data(as_text=True))

    def test_live_messages_and_inbound_email(self):
        support.login(self.c, "demo@example.com", "Demo@1234")
        with self.s.db() as conn:
            t = conn.execute("SELECT number FROM support_tickets WHERE user_id = ? ORDER BY id LIMIT 1",
                             (self.s.find_user("demo@example.com")["id"],)).fetchone()
        number = t["number"]
        data = self.c.get(f"/support/{number}/messages.json").get_json()
        self.assertTrue(data["messages"])
        last = data["messages"][-1]["id"]
        self.assertEqual(self.c.get(f"/support/{number}/messages.json?after={last}").get_json()["messages"], [])
        self.assertIn("data-live-thread", self.c.get(f"/support/{number}").get_data(as_text=True))
        self.assertEqual(self.c.get("/support/S999999/messages.json").status_code, 404)
        self.assertIn("data-kb-suggest", self.c.get("/support").get_data(as_text=True))
        payload = {"message_id": "<page-test@mail.test>", "from": "demo@example.com", "subject": f"Re: [{number}] thanks",
                   "text": "Thank you, it arrived."}
        body, header = v8extra.sign_json("inbound-test-secret", payload)
        r = self.c.post("/api/inbound-email", data=body, headers={"X-Yo-Signature": header, "Content-Type": "application/json"})
        self.assertEqual(r.get_json(), {"result": "replied", "ticket": number})
        self.assertEqual(self.c.post("/api/inbound-email", data=body, headers={"X-Yo-Signature": "t=1,v1=bad"}).status_code, 401)
        new = self.c.get(f"/support/{number}/messages.json?after={last}").get_json()["messages"]
        self.assertEqual(new[0]["body"], "Thank you, it arrived.")

    def test_invoice_shows_the_einvoice_number(self):
        support.login(self.c, "demo@example.com", "Demo@1234")
        with self.s.db() as conn:
            inv = conn.execute("SELECT i.id FROM invoices i JOIN orders o ON o.id = i.order_id WHERE o.user_id = ? AND "
                               "i.kind = 'Tax invoice' AND i.issuer_tax_id != '' ORDER BY i.id LIMIT 1",
                               (self.s.find_user("demo@example.com")["id"],)).fetchone()
        page = self.c.get(f"/invoices/{inv['id']}").get_data(as_text=True)
        self.assertIn(self.s.einvoice(inv["id"])["irn"], page)
        self.assertIn("demo: not registered", page)
        r = self.c.get(f"/invoices/{inv['id']}/einvoice.json")
        self.assertEqual(r.get_json()["Version"], "1.1")
        c2 = self.app.test_client()
        support.login(c2, "neha.gill@example.com", "Staff@2026")                   # support has no invoices.view
        self.assertEqual(c2.get(f"/invoices/{inv['id']}/einvoice.json").status_code, 404)


if __name__ == "__main__":
    unittest.main()
