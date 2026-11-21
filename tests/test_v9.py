"""Tests for version 9 (the 21 steps after the review of version 8): translations of help articles, banners and
contacts; drafts, pictures and the preview of pages; one search that forgives typing mistakes and the list of searches
without a result; the numbers of a worker's own job, shift swaps, the first-days checklist and the clock-in list of the
code screen; "is typing", files and stars at the help desk; the usual way to pay, "buy again" and a new address before
shipping; seller insights and the CSV import; the live vehicle on the maps; the webhook signature tool; the status
page; limits per person; the nightly backup with its restore test; the demo content and the pages of version 9."""

import io
import os
import sys
import unittest
from datetime import date, datetime, timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import support  # noqa: E402
import courier  # noqa: E402
import privacy  # noqa: E402
import providers  # noqa: E402
import services  # noqa: E402
import staff as staff_rules  # noqa: E402
import tickets  # noqa: E402
import v9  # noqa: E402
from core import ShopError, now  # noqa: E402
from test_v6 import Base  # noqa: E402
from test_v7 import ADDR  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.4\n%demo\n"


def admin_of(s):
    a = s.register("Content Admin", "content.admin@realmail.local", "9800000091", "Secret123", wallet=0)
    s.make_admin(a)
    return a


# ====================================================================== 1-3. content
class TestTranslations(Base):
    def test_help_article_banner_and_contacts_in_another_language(self):
        self.s.save_kb_article(self.admin, None, {"topic": "Orders", "question": "Where is my order?",
                                                  "answer": "Open Your Orders.", "published": "1"})
        aid = next(a["id"] for a in self.s.kb_articles() if a["question"] == "Where is my order?")
        self.s.save_content_texts(self.admin, "kb", aid, "ru", {"question": "Где мой заказ?", "answer": "Откройте «Ваши заказы»."})
        en = next(a for a in self.s.kb_articles() if a["id"] == aid)
        ru = next(a for a in self.s.kb_articles(lang="ru") if a["id"] == aid)
        fr = next(a for a in self.s.kb_articles(lang="fr") if a["id"] == aid)
        self.assertEqual((en["question"], ru["question"], fr["question"]), ("Where is my order?", "Где мой заказ?", "Where is my order?"))
        self.assertTrue(ru["translated"])
        self.s.save_slide(self.admin, None, {"title": "Big sale this week", "label": "New", "active": "1"})
        sid = next(x["id"] for x in self.s.slides() if x["title"] == "Big sale this week")
        self.s.save_content_texts(self.admin, "slide", sid, "fr", {"title": "Grande vente", "label": "", "subtitle": "", "button": ""})
        fr_slide = next(x for x in self.s.slides(lang="fr") if x["id"] == sid)
        self.assertEqual((fr_slide["title"], fr_slide["label"]), ("Grande vente", "New"))     # empty = the English text
        self.s.save_content_texts(self.admin, "contact", "site", "ru", {"hours": "Каждый день", "address": "Москва\nул. Тестовая"})
        c = self.s.contacts("ru")
        self.assertEqual((c["hours"], c["address_lines"]), ("Каждый день", ["Москва", "ул. Тестовая"]))
        self.assertNotEqual(self.s.contacts()["hours"], "Каждый день")

    def test_only_site_editors_translate_and_english_is_refused(self):
        _, hub = self.worker(self.ambala, "hub", kind="hub")
        with self.assertRaises(ShopError):
            self.s.save_content_texts(hub, "contact", "site", "ru", {"hours": "x"})
        with self.assertRaises(ShopError):
            self.s.save_content_texts(self.admin, "contact", "site", "en", {"hours": "x"})
        with self.assertRaises(ShopError):
            self.s.save_content_texts(self.admin, "secret", "1", "ru", {"hours": "x"})


class TestDrafts(Base):
    def test_draft_is_hidden_until_published(self):
        self.s.save_site_page(self.admin, "terms", "en", "Terms", "The terms of today.")
        self.s.save_page_draft(self.admin, "terms", "en", "Terms", "The terms of next month.")
        self.assertEqual(self.s.site_page("terms")["body"], "The terms of today.")           # visitors: the old text
        self.assertTrue(next(p for p in self.s.site_pages() if p["slug"] == "terms")["draft"])
        self.s.publish_page_draft(self.admin, "terms", "en")
        page = self.s.site_page("terms")
        self.assertEqual((page["body"], page["draft_body"]), ("The terms of next month.", ""))
        self.assertIn("The terms of today.", [v["body"] for v in self.s.page_versions("terms")])   # kept as a version
        with self.assertRaises(ShopError):
            self.s.publish_page_draft(self.admin, "terms", "en")                               # nothing left to publish

    def test_draft_with_a_time_is_published_by_housekeeping(self):
        self.s.save_site_page(self.admin, "terms", "en", "Terms", "The terms of today.")
        when = (datetime.now() + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")
        self.s.save_page_draft(self.admin, "terms", "en", "Terms", "The terms that start soon.", when)
        self.assertEqual(self.s.publish_due_pages(), 0)
        self.assertEqual(self.s.publish_due_pages(at=datetime.now() + timedelta(hours=3)), 1)
        self.assertEqual(self.s.site_page("terms")["body"], "The terms that start soon.")
        with self.assertRaises(ShopError):
            self.s.save_page_draft(self.admin, "terms", "en", "Terms", "A text long enough.", "next Monday")
        with self.assertRaises(ShopError):
            self.s.save_page_draft(self.admin, "nothing-here", "en", "No page", "A text long enough.")

    def test_picture_line_becomes_an_image(self):
        html = self.s.page_html({"body": "Our hub:\n\n![The Ambala hub at night](/img/site/pic-0a1b2c.jpg)"})
        self.assertIn('<img src="/img/site/pic-0a1b2c.jpg" alt="The Ambala hub at night"', html)
        self.assertNotIn("<script", self.s.page_html({"body": "![x](javascript:alert(1))"}))


# ====================================================================== 4-5. search
class TestSearch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = support.make_store(seed=False)
        cls.s.load_catalog()

    def test_typing_mistakes_are_corrected(self):
        self.assertEqual(v9.fix_word("sheos", ["shoes", "shirt"]), "shoes")
        self.assertEqual(v9.fix_word("tv", ["the"]), "tv")                                  # short words are kept
        self.assertEqual(v9.fix_word("zzzzzz", ["shoes"]), "zzzzzz")
        r = self.s.find_everything(None, "wach", record=False)
        self.assertEqual(r["fixed"], "watch")
        self.assertTrue(r["products"])
        r = self.s.find_everything(None, "sheos wach", record=False)                         # no product has both words:
        self.assertEqual(r["fixed"], "shoes watch")                                         # each word is searched alone
        self.assertTrue(r["products"])
        self.assertEqual(self.s.find_everything(None, "x", record=False)["total"], 0)       # too short: no search

    def test_searches_without_a_result_are_listed(self):
        self.s.find_everything(None, "qwertyuiop gizmo")
        self.s.find_everything(None, "qwertyuiop gizmo")
        self.s.find_everything(7, "watch")
        m = self.s.search_misses()
        miss = {x["q"]: x["times"] for x in m["missed"]}
        self.assertEqual(miss.get("qwertyuiop gizmo"), 2)
        self.assertNotIn("watch", miss)
        with self.s.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM search_log WHERE q = 'watch' AND signed_in = 1").fetchone()[0], 1)
            cols = set(conn.columns("search_log"))
        self.assertFalse({"user_id", "ip"} & cols)                                          # no person in the log
        self.assertIn(("search_log", "at", 90, "delete"), privacy.RETENTION)

    def test_search_finds_own_orders_but_not_others(self):
        uid = self.s.register("Buyer Nine", "buyer.nine@realmail.local", "9811100009", "Secret123")
        other = self.s.register("Other Nine", "other.nine@realmail.local", "9811100010", "Secret123")
        p = next(x for x in self.s.products() if x.stock > 2)
        self.s.save_cart(uid, {str(p.id): 1})
        number = self.s.place_order(uid, self.s.add_address(uid, dict(ADDR, mobile="9811100009")), "cod")
        self.assertIn(number, [f["label"] for f in self.s.find_everything(uid, number, record=False)["found"]])
        self.assertNotIn(number, [f["label"] for f in self.s.find_everything(other, number, record=False)["found"]])
        self.assertEqual(self.s.find_everything(None, number, record=False)["found"], [])


# ====================================================================== 6-9. workers
class TestWorkers(Base):
    def test_role_numbers_follow_abilities(self):
        _, sup = self.worker(self.haryana, "support", kind="support")
        _, hub = self.worker(self.ambala, "hub", kind="hub")
        customer = self.s.register("Buyer", "buyer@realmail.local", "9811111199", "Secret123")
        self.s.open_support_ticket(customer, "order", "Late order", "Where is my order please?")
        labels = {label: n for label, n, _ in self.s.role_numbers(sup)}
        self.assertEqual(labels.get("Open questions"), 1)
        self.assertNotIn("Open questions", {label for label, _, _ in self.s.role_numbers(hub)})
        self.assertEqual(self.s.role_numbers(customer), [])
        endpoints = {e for _, _, e in self.s.role_numbers(sup) + self.s.role_numbers(hub)}
        nav = {n[0] for n in services.NAV} | {"hub_page", "admin_shipments", "admin_orders", "admin_returns", "admin_products"}
        self.assertTrue(endpoints <= nav, endpoints - nav)

    def swap_pair(self):
        a_sid, a_uid = self.worker(self.ambala, "hub", kind="hub")
        b_sid, b_uid = self.worker(self.ambala, "hub", kind="hub")
        day = (date.today() + timedelta(days=3)).isoformat()
        self.s.plan_shift(self.admin, a_sid, day, "09:00", "17:00")
        with self.s.db() as conn:
            rota = conn.execute("SELECT id FROM staff_rota WHERE staff_id = ? AND day = ?", (a_sid, day)).fetchone()[0]
        return a_sid, a_uid, b_sid, b_uid, rota, day

    def test_swap_needs_colleague_and_manager(self):
        a_sid, a_uid, b_sid, b_uid, rota, day = self.swap_pair()
        boss_sid, boss = self.worker(self.ambala, "", manager=True, abilities=["staff.view", "staff.manage"])
        sw = self.s.offer_swap(a_uid, rota, b_sid)
        with self.assertRaises(ShopError):
            self.s.decide_swap(boss, sw, True)                                  # the colleague has not said yes yet
        with self.assertRaises(ShopError):
            self.s.answer_swap(a_uid, sw, True)                                 # only the asked colleague answers
        self.s.answer_swap(b_uid, sw, True)
        self.assertEqual([x["status"] for x in self.s.swaps(b_uid)], ["Accepted"])
        self.assertIn(("Shift swaps to decide", 1), [(lbl, n) for lbl, n, _ in self.s.role_numbers(boss)])
        self.s.decide_swap(boss, sw, True)
        with self.s.db() as conn:
            self.assertEqual(conn.execute("SELECT staff_id FROM staff_rota WHERE id = ?", (rota,)).fetchone()[0], b_sid)
        self.assertEqual(self.s.swaps(a_uid), [])                               # nothing open any more
        self.assertEqual(self.s.swaps(a_uid, open_only=False)[0]["status"], "Approved")

    def test_swap_rules(self):
        a_sid, a_uid, b_sid, b_uid, rota, day = self.swap_pair()
        k_sid, _ = self.worker(self.kigali, "hub", kind="hub")
        with self.assertRaises(ShopError):
            self.s.offer_swap(a_uid, rota, k_sid)                               # another office
        with self.assertRaises(ShopError):
            self.s.offer_swap(b_uid, rota, a_sid)                               # not his own shift
        self.s.plan_shift(self.admin, b_sid, day, "10:00", "18:00")
        with self.assertRaises(ShopError):
            self.s.offer_swap(a_uid, rota, b_sid)                               # the colleague works that day
        c_sid, c_uid = self.worker(self.ambala, "hub", kind="hub")
        sw = self.s.offer_swap(a_uid, rota, c_sid)
        with self.assertRaises(ShopError):
            self.s.offer_swap(a_uid, rota, c_sid)                               # asked already
        self.s.answer_swap(c_uid, sw, True)
        _, other_boss = self.worker(self.kigali, "", manager=True, abilities=["staff.view", "staff.manage"])
        with self.assertRaises(ShopError):
            self.s.decide_swap(other_boss, sw, True)                            # not his branch
        _, plain = self.worker(self.ambala, "hub", kind="hub")
        with self.assertRaises(ShopError):
            self.s.decide_swap(plain, sw, True)
        self.s.decide_swap(self.admin, sw, False)
        with self.s.db() as conn:
            self.assertEqual(conn.execute("SELECT staff_id FROM staff_rota WHERE id = ?", (rota,)).fetchone()[0], a_sid)

    def test_first_days_checklist(self):
        sid, uid = self.worker(self.ambala, "hub", kind="hub")
        ob = self.s.onboarding_of(sid)
        self.assertEqual((ob["done"], ob["total"], ob["complete"]), (0, 7, False))
        self.s.tick_onboarding(uid, sid, "card")
        with self.assertRaises(ShopError):
            self.s.tick_onboarding(uid, sid, "contract")                         # the system checks this one
        _, other = self.worker(self.ambala, "hub", kind="hub")
        with self.assertRaises(ShopError):
            self.s.tick_onboarding(other, sid, "tools")                          # a colleague may not tick
        self.s.tick_onboarding(self.admin, sid, "tools")
        done = {i["key"] for i in self.s.onboarding_of(sid)["items"] if i["done"]}
        self.assertTrue({"card", "tools"} <= done)
        self.s.tick_onboarding(uid, sid, "card", done=False)
        self.assertNotIn("card", {i["key"] for i in self.s.onboarding_of(sid)["items"] if i["done"]})

    def test_code_screen_lists_recent_clock_ins_by_first_name(self):
        sid, uid = self.worker(self.ambala, "hub", kind="hub")
        self.s.clock_in_proved(uid, code=self.s.office_code(self.ambala))
        rows = self.s.recent_clock_ins(self.ambala)
        self.assertEqual([(r["name"], r["in_proof"]) for r in rows], [("Worker", "code")])
        self.assertEqual(self.s.recent_clock_ins(self.kigali), [])


# ====================================================================== 10-13 + 11-12. help desk and customers
class TestHelpDesk(Base):
    def setUp(self):
        super().setUp()
        self.customer = self.s.register("Buyer", "buyer@realmail.local", "9811111199", "Secret123")
        self.number = self.s.open_support_ticket(self.customer, "order", "Broken box", "The box was broken, see photo.")
        _, self.agent = self.worker(self.haryana, "support", kind="support")

    def test_typing_is_shown_to_the_other_side_only(self):
        self.s.support_typing(self.number, self.customer)
        self.assertTrue(self.s.support_messages_after(self.number, self.agent)["typing"])
        self.assertFalse(self.s.support_messages_after(self.number, self.customer)["typing"])
        stranger = self.s.register("Stranger", "stranger@realmail.local", "9811111198", "Secret123")
        self.s.support_typing(self.number, stranger)                            # ignored: not his question
        self.assertIsNone(self.s.support_messages_after(self.number, stranger))
        with self.s.db() as conn:
            conn.execute("UPDATE support_typing SET at = at - 10")
        self.assertFalse(self.s.support_messages_after(self.number, self.agent)["typing"])   # 6 seconds at most

    def test_files_are_checked_and_private(self):
        fid = self.s.add_support_file(self.customer, self.number, "box.png", "image/png", PNG)
        self.s.add_support_file(self.agent, self.number, "label.pdf", "application/pdf", PDF)
        with self.assertRaises(ShopError):
            self.s.add_support_file(self.customer, self.number, "fake.png", "image/png", b"MZ\x90\x00 not a picture")
        with self.assertRaises(ShopError):
            self.s.add_support_file(self.customer, self.number, "page.html", "text/html", b"<html>")
        with self.assertRaises(ShopError):
            self.s.add_support_file(self.customer, self.number, "big.pdf", "application/pdf", b"%PDF-" + b"0" * v9.MAX_FILE)
        files = self.s.support_files(self.number, self.customer)
        self.assertEqual([(f["name"], f["from_staff"]) for f in files], [("box.png", 0), ("label.pdf", 1)])
        self.assertEqual(self.s.support_file(self.number, self.agent, fid)[2], PNG)
        stranger = self.s.register("Stranger", "stranger@realmail.local", "9811111198", "Secret123")
        self.assertIsNone(self.s.support_file(self.number, stranger, fid))
        self.assertFalse(self.s.support_files(self.number, stranger))
        with self.s.db() as conn:
            media = conn.execute("SELECT media FROM support_files WHERE id = ?", (fid,)).fetchone()[0]
        self.assertTrue(media.startswith("private/support/"))

    def test_stars_after_an_answer(self):
        with self.assertRaises(ShopError):
            self.s.rate_support(self.customer, self.number, 5)                  # nothing answered yet
        self.s.support_reply(self.agent, self.number, "Sorry! A new one is on the way.")
        with self.assertRaises(ShopError):
            self.s.rate_support(self.customer, self.number, 9)
        with self.assertRaises(ShopError):
            self.s.rate_support(self.agent, self.number, 5)                     # only the customer rates
        self.s.rate_support(self.customer, self.number, 2, "  Slow   answer ")
        self.s.rate_support(self.customer, self.number, 4)                      # he may change his mind
        self.assertEqual(self.s.support_rating(self.number), {"stars": 4, "note": ""})
        self.assertEqual(self.s.support_rating_summary(), {"count": 1, "average": 4.0, "low": 0})


class TestCustomers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = support.make_store(seed=False)
        cls.s.load_catalog()

    def buyer(self, n):
        uid = self.s.register(f"Buyer {n}", f"buyer{n}@realmail.local", f"98222000{n:02d}", "Secret123")
        return uid, self.s.add_address(uid, dict(ADDR, mobile=f"98222000{n:02d}"))

    def test_usual_way_to_pay(self):
        uid, _ = self.buyer(1)
        self.assertEqual(self.s.pay_pref(uid), "")
        self.s.set_pay_pref(uid, "upi")
        self.assertEqual(self.s.pay_pref(uid), "upi")
        with self.assertRaises(ShopError):
            self.s.set_pay_pref(uid, "bitcoin")

    def test_buy_again_skips_what_is_not_sold_any_more(self):
        uid, addr = self.buyer(2)
        a, b = [p for p in self.s.products() if p.stock > 3][:2]
        self.s.save_cart(uid, {str(a.id): 2, str(b.id): 1})
        number = self.s.place_order(uid, addr, "cod")
        self.assertEqual(self.s.get_cart(uid), {})
        with self.s.db() as conn:
            conn.execute("UPDATE products SET active = 0 WHERE id = ?", (b.id,))
        added, missing = self.s.reorder(uid, number)
        self.assertEqual((added, missing), (1, [b.name]))
        self.assertEqual(self.s.get_cart(uid), {a.id: 2})
        other, _ = self.buyer(3)
        with self.assertRaises(ShopError):
            self.s.reorder(other, number)                                       # not his order

    def test_new_address_only_before_shipping_and_same_country(self):
        uid, addr = self.buyer(4)
        office = self.s.add_address(uid, dict(ADDR, mobile="9822200044", line1="5 Office Park", pincode="133006"))
        abroad = self.s.add_address(uid, dict(ADDR, mobile="9822200045", country="RW", city="Kigali", pincode="00000"))
        p = next(x for x in self.s.products() if x.stock > 2)
        self.s.save_cart(uid, {str(p.id): 1})
        number = self.s.place_order(uid, addr, "cod")
        self.s.change_order_address(uid, number, office)
        self.assertIn("5 Office Park", self.s.get_order(uid, number)["ship_address"])
        with self.assertRaises(ShopError):
            self.s.change_order_address(uid, number, abroad)
        with self.assertRaises(ShopError):
            self.s.change_order_address(uid, number, 999999)
        with self.s.db() as conn:
            conn.execute("UPDATE orders SET placed_at = ? WHERE number = ?",          # the demo clock moves it to "Shipped"
                         ((datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S"), number))
        self.assertFalse(self.s.get_order(uid, number)["can_cancel"])
        with self.assertRaises(ShopError):
            self.s.change_order_address(uid, number, addr)


# ====================================================================== 14-15. sellers
class TestSellers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = support.make_store(seed=True)
        cls.shop = cls.s.seller_of(cls.s.find_user("seller@example.com")["id"])

    def test_insights_of_one_shop(self):
        i = self.s.seller_insights(self.shop["id"])
        self.assertEqual(set(i), {"days", "best", "reasons", "low", "never", "sold", "money"})
        with self.s.db() as conn:
            mine = {r[0] for r in conn.execute("SELECT name FROM products WHERE seller_id = ?", (self.shop["id"],))}
        self.assertTrue({b["name"] for b in i["best"]} <= mine)
        self.assertTrue({x["name"] for x in i["low"]} <= mine)

    def test_csv_import(self):
        text = ("name,brand,category,price,mrp,stock,about,tags\n"
                "Cotton kurta blue,Ganga Weaves,fashion,799,999,25,Soft cotton | Machine wash,kurta cotton\n"
                "=HYPERLINK(\"x\"),Bad,fashion,10,10,1,,\n"
                "No price,Brand,fashion,,,3,,\n")
        r = self.s.import_products_csv(self.shop["id"], text)
        self.assertEqual(r["added"], 1)
        self.assertEqual([n for n, _ in r["errors"]], [3, 4])
        with self.s.db() as conn:
            p = conn.execute("SELECT active, seller_id FROM products WHERE name = 'Cotton kurta blue'").fetchone()
        self.assertEqual((p["active"], p["seller_id"]), (0, self.shop["id"]))   # switched off until it has a picture
        with self.assertRaises(ShopError):
            self.s.import_products_csv(self.shop["id"], "name,price\nx,1\n")
        with self.assertRaises(ShopError):
            self.s.import_products_csv(self.shop["id"], "name,brand,category,price,stock\n" + "a,b,fashion,1,1\n" * (v9.CSV_MAX + 1))


# ====================================================================== 16-21. maps, partners, quality
class TestMapsAndPartners(unittest.TestCase):
    def setUp(self):
        self.s = support.make_store(seed=False)

    def test_ticket_map_uses_the_operator_position(self):
        base = {"number": "PNR1", "status": "On the way", "courier": {"lat": 1.0, "lng": 2.0, "name": "B1", "vehicle": "bus"},
                "done": False, "by_timetable": True}
        with mock.patch.object(tickets.TicketsMixin, "trip_live", return_value=dict(base)), \
             mock.patch.object(type(self.s), "get_booking", return_value={"service_id": 1}), \
             mock.patch.object(type(self.s), "booking_live", return_value={
                 "vehicle": {"lat": -1.70, "lng": 29.93, "label": "Bus RAD 412 K", "at": "2026-11-19 10:00:05"}, "alerts": []}):
            d = self.s.trip_live("PNR1", 1)
        self.assertEqual((d["courier"]["lat"], d["courier"]["name"], d["source"], d["by_timetable"]),
                         (-1.70, "Bus RAD 412 K", "operator", False))
        with mock.patch.object(tickets.TicketsMixin, "trip_live", return_value=dict(base)), \
             mock.patch.object(type(self.s), "get_booking", return_value={"service_id": 1}), \
             mock.patch.object(type(self.s), "booking_live", return_value={"vehicle": None, "alerts": []}):
            d = self.s.trip_live("PNR1", 1)
        self.assertEqual((d["courier"]["lat"], d["source"]), (1.0, "timetable"))

    def test_parcel_map_tells_the_age_of_the_gps_point(self):
        base = {"number": "YC1", "courier": {"lat": 30.3, "lng": 76.8, "name": "Ravi", "vehicle": "van"}, "done": False}
        with mock.patch.object(courier.CourierMixin, "live", return_value=dict(base)):
            d = self.s.live("YC1")
        self.assertEqual((d["seen"], d["source"]), ("", "courier"))
        with mock.patch.object(courier.CourierMixin, "live", return_value=dict(base, courier=None)):
            self.assertEqual(self.s.live("YC1")["seen"], "")

    def test_signature_tool(self):
        body = '{"event": "shipment.delivered"}'
        header = providers.sign("whsec_x", body.encode())
        self.assertEqual(self.s.check_signature("whsec_x", header, body)["ok"], True)
        self.assertEqual(self.s.check_signature("whsec_x", header, body + " ")["ok"], False)
        self.assertEqual(self.s.check_signature("whsec_y", header, body)["ok"], False)
        old = providers.sign("whsec_x", body.encode(), t=1_700_000_000)
        r = self.s.check_signature("whsec_x", old, body)
        self.assertEqual((r["ok"], r["fresh"]), (True, False))                  # right, but too old for a real call
        self.assertFalse(self.s.check_signature("whsec_x", "v1=abc", body)["ok"])


class TestQuality(unittest.TestCase):
    def setUp(self):
        self.s = support.make_store(seed=False)

    def test_status_page_parts(self):
        parts = {p["part"]: p for p in self.s.system_status()}
        self.assertTrue(parts["Shop and accounts"]["ok"])
        self.assertEqual(parts["Backups"]["note"], "No backup yet")
        self.assertEqual(parts["Card payments"]["note"], "Test mode")
        with self.s.db() as conn:
            for i in range(60):
                conn.execute("INSERT INTO jobs (kind, payload, status, run_at, created_at) VALUES ('demo', '{}', 'waiting', ?, ?)",
                             ("2000-01-01 00:00:00", now()))
        self.assertFalse({p["part"]: p for p in self.s.system_status()}["Background work"]["ok"])

    def test_limits_per_person(self):
        n, _ = v9.LIMITS["login"]
        self.assertTrue(all(self.s.rate_ok("login", "198.51.100.7") for _ in range(n)))
        self.assertFalse(self.s.rate_ok("login", "198.51.100.7"))
        self.assertFalse(self.s.rate_ok("login", "198.51.100.7"))
        self.assertTrue(self.s.rate_ok("login", "198.51.100.8"))                # another address is not stopped
        r = self.s.rate_report()
        self.assertEqual((r["by"], [x["who"] for x in r["rows"]]), ({"login": 1}, ["198.51.100.7"]))   # written once

    def test_nightly_backup_is_restored_once_a_night(self):
        self.s.load_catalog()
        self.assertIsNone(self.s.nightly_backup(at=datetime(2027, 1, 5, 1, 30)))   # before 02:00
        res = self.s.nightly_backup(at=datetime(2027, 1, 5, 2, 10))
        self.assertTrue(res["ok"], res["note"])
        self.assertGreater(res["rows"], 50)
        self.assertIsNone(self.s.nightly_backup(at=datetime(2027, 1, 5, 4, 0)))    # once a night
        self.assertEqual(len(self.s.backup_checks()), 1)
        bid, blob = self.s.save_backup("auto", "broken test")
        bad = self.s.check_backup(bid, blob[: len(blob) // 2])
        self.assertFalse(bad["ok"])
        self.assertTrue(bad["note"])
        self.assertEqual(self.s.unpack(blob)["version"], 9)


# ====================================================================== pages
class TestPages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = support.make_app(seed=True)
        cls.s = cls.app.store

    def client(self, who=None, password=None):
        c = self.app.test_client()
        if who:
            self.assertEqual(support.login(c, who, password).status_code, 302)
        return c

    def test_public_pages(self):
        c = self.client()
        r = c.get("/find?q=sheos")
        self.assertEqual(r.status_code, 200)
        self.assertIn("shoes", r.get_data(as_text=True))
        self.assertEqual(c.get("/status").status_code, 200)
        self.assertIn('id="check"', c.get("/partners/api").get_data(as_text=True))
        for url in ("/admin/site/searches", "/admin/staff/swaps", "/admin/monitor/limits", "/seller/insights", "/seller/import"):
            self.assertIn(c.get(url).status_code, (302, 403), url)

    def test_signature_check_page(self):
        c = self.client()
        tok = support.token(c, "/partners/api")
        body = '{"a": 1}'
        r = c.post("/partners/api/check", data={"csrf_token": tok, "secret": "whsec_s", "body": body,
                                                "header": providers.sign("whsec_s", body.encode())})
        self.assertIn("The signature is right", r.get_data(as_text=True))
        r = c.post("/partners/api/check", data={"secret": "whsec_s", "body": body, "header": "t=1,v1=0"})
        self.assertEqual(r.status_code, 400)                                    # no CSRF token

    def test_admin_pages_and_permissions(self):
        admin = self.client("admin@example.com", "Admin@1234")
        kb = self.s.kb_articles()[0]["id"]
        for url in ("/admin/site/searches", f"/admin/site/translate/kb/{kb}", "/admin/site/translate/contact/site?lang=fr",
                    "/admin/staff/swaps", "/admin/monitor/limits", "/admin/backups", "/admin/site/pages/returns",
                    "/admin/site/pages/returns/preview"):
            self.assertEqual(admin.get(url).status_code, 200, url)
        self.assertEqual(admin.get("/admin/site/translate/secret/1").status_code, 404)
        hub = self.client("sunil.kumar@example.com", "Staff@2026")
        for url in ("/admin/site/searches", "/admin/monitor/limits", f"/admin/site/translate/kb/{kb}"):
            self.assertEqual(hub.get(url).status_code, 403, url)
        self.assertEqual(staff_rules.ability_for_path("/admin/staff/swaps", "POST"), "staff.manage")
        self.assertEqual(staff_rules.ability_for_path("/admin/backups/check", "POST"), "database.view")

    def test_translation_is_saved_from_the_form(self):
        admin = self.client("admin@example.com", "Admin@1234")
        kb = self.s.kb_articles()[-1]["id"]
        tok = support.token(admin, f"/admin/site/translate/kb/{kb}?lang=es")
        r = admin.post(f"/admin/site/translate/kb/{kb}", data={"csrf_token": tok, "lang": "es", "question": "¿Pregunta?", "answer": "Respuesta."})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(next(a for a in self.s.kb_articles(lang="es") if a["id"] == kb)["question"], "¿Pregunta?")

    def test_customer_pages(self):
        c = self.client("demo@example.com", "Demo@1234")
        uid = self.s.find_user("demo@example.com")["id"]
        tok = support.token(c, "/account")
        self.assertEqual(c.post("/account/pay-pref", data={"csrf_token": tok, "method": "wallet"}).status_code, 302)
        self.assertEqual(self.s.pay_pref(uid), "wallet")
        self.assertIn('"wallet"', c.get("/checkout").get_data(as_text=True))
        with self.s.db() as conn:
            number = conn.execute("SELECT number FROM orders WHERE user_id = ? AND cancelled_at = '' ORDER BY id DESC",
                                  (uid,)).fetchone()[0]
        self.assertIn("Buy again", c.get(f"/orders/{number}").get_data(as_text=True))
        tok = support.token(c, f"/orders/{number}")
        r = c.post(f"/orders/{number}/again", data={"csrf_token": tok})
        self.assertIn(r.headers["Location"].split("?")[0].rstrip("/").rsplit("/", 1)[-1], ("cart", number))

    def test_seller_pages(self):
        c = self.client("seller@example.com", "Seller@1234")
        self.assertEqual(c.get("/seller/insights").status_code, 200)
        tok = support.token(c, "/seller/import")
        data = {"csrf_token": tok, "file": (io.BytesIO(b"name,brand,category,price,stock\nCane basket small,Ambala Crafts,home,350,9\n"), "p.csv")}
        r = c.post("/seller/import", data=data, content_type="multipart/form-data")
        self.assertIn("1 products were added", r.get_data(as_text=True))

    def test_login_limit_answers_429(self):
        c = self.client()
        codes = []
        for i in range(v9.LIMITS["login"][0] + 1):                         # a new account each time (version 5 counts
            codes.append(c.post("/login", data={"csrf_token": support.token(c), "login": f"nobody{i}@realmail.local",
                                                 "password": "wrong"}, environ_base={"REMOTE_ADDR": "198.51.100.99"}).status_code)
        self.assertEqual(codes[-1], 429)
        self.assertNotIn(429, codes[:-1])

    def test_worker_home_shows_tiles_swaps_and_first_days(self):
        c = self.client("neha.gill@example.com", "Staff@2026")
        page = c.get("/staff").get_data(as_text=True)
        self.assertIn("role-tiles", page)
        self.assertIn('id="swaps"', page)


class TestDemo(unittest.TestCase):
    def test_demo_content_of_version_9_once(self):
        import demo_v9
        s = support.make_store(seed=True)

        def counts():
            with s.db() as conn:
                return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]          # noqa: S608
                        for t in ("content_texts", "search_log", "shift_swaps", "onboarding_done", "support_ratings",
                                  "support_files", "rate_blocks", "backup_checks")}
        first = counts()
        self.assertTrue(all(first.values()), first)
        self.assertFalse(demo_v9.ensure_v9(s))                                  # claimed once
        self.assertEqual(counts(), first)
        self.assertTrue(s.search_misses()["missed"])
        self.assertTrue(s.backup_checks()[0]["ok"])


if __name__ == "__main__":
    unittest.main()
