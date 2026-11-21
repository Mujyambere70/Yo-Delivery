"""
v9.py - version 9 (mixin class used by Store): the 21 steps that came out of the review of version 8.

Content   1 translations of help articles, banners and contacts   2 pictures in pages, drafts with a publishing time
          3 preview of a draft
Search    4 one search over products, help, pages and numbers that forgives typing mistakes
          5 a list of the words people searched for and did not find
Workers   6 numbers of my own job on "My work"   7 swapping a shift with a colleague   8 a checklist for new workers
          9 who clocked in with the office code, shown on the code screen
Customers 10 "is typing" and files in help-desk questions   11 a preferred way to pay and "buy again"
          12 changing the address of an order before it is shipped   13 stars for a help-desk answer
Sellers   14 best products, returns by reason, low stock   15 many products from one CSV file
Partners  16 the bus of the operator (live feed) on the ticket map; the age of the courier's GPS point on the parcel map   17 a tool that checks a webhook signature
Quality   18 tools/release_check.py (crawler + browser)   19 /status   20 limits per person with a report
          21 a backup every night that is restored into an empty database to prove that it works
"""

import csv
import difflib
import io
import os
import re
import tempfile
import time
from datetime import date, datetime, timedelta

import world
from core import ShopError, now
from providers import signature_ok

V9_SCHEMA = """
CREATE TABLE IF NOT EXISTS content_texts (
    kind TEXT NOT NULL, ref TEXT NOT NULL, lang TEXT NOT NULL, field TEXT NOT NULL, value TEXT NOT NULL,
    updated_by INTEGER, updated_at TEXT NOT NULL, PRIMARY KEY (kind, ref, lang, field)
);
CREATE TABLE IF NOT EXISTS search_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, q TEXT NOT NULL, hits INTEGER NOT NULL, fixed TEXT NOT NULL DEFAULT '',
    signed_in INTEGER NOT NULL DEFAULT 0, at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_search_log_at ON search_log(at);
CREATE TABLE IF NOT EXISTS shift_swaps (
    id INTEGER PRIMARY KEY AUTOINCREMENT, rota_id INTEGER NOT NULL REFERENCES staff_rota(id),
    from_staff INTEGER NOT NULL, to_staff INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'Asked',
    created_at TEXT NOT NULL, decided_by INTEGER, decided_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS onboarding_done (
    staff_id INTEGER NOT NULL, item TEXT NOT NULL, done_by INTEGER, done_at TEXT NOT NULL, PRIMARY KEY (staff_id, item)
);
CREATE TABLE IF NOT EXISTS support_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id INTEGER NOT NULL REFERENCES support_tickets(id), name TEXT NOT NULL,
    mime TEXT NOT NULL, media TEXT NOT NULL, size INTEGER NOT NULL, by_user INTEGER NOT NULL, from_staff INTEGER NOT NULL,
    at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS support_typing (
    ticket_id INTEGER NOT NULL, side TEXT NOT NULL, at REAL NOT NULL, PRIMARY KEY (ticket_id, side)
);
CREATE TABLE IF NOT EXISTS support_ratings (
    ticket_id INTEGER PRIMARY KEY, stars INTEGER NOT NULL, note TEXT NOT NULL DEFAULT '', at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rate_hits (
    bucket TEXT NOT NULL, slot INTEGER NOT NULL, n INTEGER NOT NULL, PRIMARY KEY (bucket, slot)
);
CREATE TABLE IF NOT EXISTS rate_blocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT, what TEXT NOT NULL, who TEXT NOT NULL, at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS backup_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT, backup_id INTEGER NOT NULL, ok INTEGER NOT NULL, tables INTEGER NOT NULL,
    row_count INTEGER NOT NULL, note TEXT NOT NULL DEFAULT '', at TEXT NOT NULL
);
"""

TEXT_KINDS = {"kb": ("question", "answer"), "slide": ("label", "title", "subtitle", "button"),
              "contact": ("tagline", "hours", "address", "footer_note")}
ONBOARDING = [                     # key, text, checked by the system?
    ("contract", "A contract is saved", True), ("training", "At least one training course is recorded", True),
    ("clock", "Clocked in for the first time", True), ("card", "Staff ID card shown to the manager", False),
    ("tools", "Knows where his own tool is (the My work button)", False),
    ("rules", "Read the rules about customer data", False), ("buddy", "Met the colleague who helps in the first weeks", False),
]
LIMITS = {"search": (40, 60), "login": (10, 600), "upload": (12, 600)}       # name -> (how many, in seconds)
FILE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "application/pdf": ".pdf"}
MAX_FILE = 2_000_000
CSV_COLUMNS = ("name", "brand", "category", "price", "mrp", "stock", "about", "tags")
CSV_MAX = 200
_IMG = re.compile(r'!<a href="(/img/site/[\w./-]+)"[^>]*>([^<]*)</a>')


def fix_word(word, vocabulary):
    """The closest known word for a mistyped one ("sheos" -> "shoes"), or the word itself."""
    if word in vocabulary or len(word) < 4 or word.isdigit():
        return word
    near = difflib.get_close_matches(word, vocabulary, n=1, cutoff=0.74)
    return near[0] if near else word


class V9Mixin:

    # ================================================================ 1. translations of content
    def content_text(self, kind, ref, field, lang, default=""):
        if lang == "en":
            return default
        with self.db() as conn:
            r = conn.execute("SELECT value FROM content_texts WHERE kind = ? AND ref = ? AND lang = ? AND field = ?",
                             (kind, str(ref), lang, field)).fetchone()
        return r["value"] if r and r["value"] else default

    def content_texts(self, kind, ref):
        with self.db() as conn:
            rows = conn.execute("SELECT lang, field, value FROM content_texts WHERE kind = ? AND ref = ?", (kind, str(ref)))
            return {(r["lang"], r["field"]): r["value"] for r in rows}

    def _texts_for(self, kind, lang):
        with self.db() as conn:
            out = {}
            for r in conn.execute("SELECT ref, field, value FROM content_texts WHERE kind = ? AND lang = ?", (kind, lang)):
                if r["value"]:
                    out.setdefault(r["ref"], {})[r["field"]] = r["value"]
            return out

    def save_content_texts(self, actor_id, kind, ref, lang, values):
        """The text of one help article, banner or the contacts in one more language. Empty = show the English text."""
        self._site_actor(actor_id)
        if kind not in TEXT_KINDS or lang not in world.LANGUAGES or lang == "en":
            raise ShopError("Please choose what to translate and a language other than English.")
        with self.db() as conn:
            for field in TEXT_KINDS[kind]:
                if field not in values:
                    continue
                v = str(values.get(field) or "").replace("\r\n", "\n").strip()[:4000]
                conn.execute("DELETE FROM content_texts WHERE kind = ? AND ref = ? AND lang = ? AND field = ?",
                             (kind, str(ref), lang, field))
                if v:
                    conn.execute("INSERT INTO content_texts (kind, ref, lang, field, value, updated_by, updated_at) "
                                 "VALUES (?, ?, ?, ?, ?, ?, ?)", (kind, str(ref), lang, field, v, actor_id, now()))
        self.log(actor_id, "Content translated", f"{kind} {ref} ({lang})")

    def kb_articles(self, topic="", published_only=True, lang="en"):
        rows = super().kb_articles(topic, published_only)
        if lang != "en":
            tr = self._texts_for("kb", lang)
            for a in rows:
                t = tr.get(str(a["id"]), {})
                a["translated"] = "question" in t or "answer" in t
                a["question"], a["answer"] = t.get("question", a["question"]), t.get("answer", a["answer"])
        return rows

    def slides(self, active_only=False, today=None, lang="en"):
        rows = super().slides(active_only, today)
        if lang != "en":
            tr = self._texts_for("slide", lang)
            from sitecontent import slide_title_html
            for s in rows:
                t = tr.get(str(s["id"]), {})
                if t:
                    s.update({k: v for k, v in t.items() if k in TEXT_KINDS["slide"]})
                    s["title_html"] = slide_title_html(self.fill_text(s["title"]))
                    s["translated"] = True
        return rows

    def contacts(self, lang="en"):
        c = super().contacts()
        if lang != "en":
            for field, v in self._texts_for("contact", lang).get("site", {}).items():
                c[field] = v
            c["address_lines"] = [x.strip() for x in c["address"].split("\n") if x.strip()]
        return c

    def text_values(self):
        """{hours} and the other contact placeholders in the visitor's language (when the staff translated them)."""
        values = super().text_values()
        lang = world.current_lang() or "en"
        if lang != "en":
            c = self.contacts(lang)
            values.update(hours=c["hours"], company=c["company"])
        return values

    # ================================================================ 2 + 3. pictures, drafts, preview
    def page_html(self, page):
        """Version 9: a picture uploaded in the editor, written as ![words](/img/site/name.jpg)."""
        html = super().page_html(page)
        return _IMG.sub(r'<img src="\1" alt="\2" loading="lazy" class="page-img">', html) if "/img/site/" in html else html

    def site_pages(self, footer_only=False):
        rows = super().site_pages(footer_only)
        with self.db() as conn:
            drafts = {r["slug"] for r in conn.execute("SELECT slug FROM site_pages WHERE draft_body != ''")}
        for p in rows:
            p["draft"] = p["slug"] in drafts
        return rows

    def save_page_draft(self, actor_id, slug, lang, title, body, publish_at=""):
        """Keep a new text next to the published one. publish_at "" = wait for the button; else 'YYYY-MM-DD HH:MM'."""
        self._site_actor(actor_id)
        title, body = " ".join(str(title or "").split())[:120], str(body or "").replace("\r\n", "\n").strip()
        if len(title) < 2 or not 10 <= len(body) <= 20000:
            raise ShopError("Please write a title and a text of 10 to 20,000 characters.")
        when = str(publish_at or "").strip().replace("T", " ")
        if when:
            try:
                when = datetime.strptime(when[:16], "%Y-%m-%d %H:%M").strftime("%Y-%m-%d %H:%M:00")
            except ValueError:
                raise ShopError("Please check the publishing time.")
        with self.db() as conn:
            if not conn.execute("UPDATE site_pages SET draft_title = ?, draft_body = ?, publish_at = ? WHERE slug = ? AND "
                                "lang = ?", (title, body, when, slug, lang)).rowcount:
                raise ShopError("Save the page once before you make a draft of it.")
        self.log(actor_id, "Page draft saved", f"{slug} ({lang})" + (f", publish {when}" if when else ""))

    def publish_page_draft(self, actor_id, slug, lang):
        p = self.site_page(slug, lang, exact=True)
        if not p or not p.get("draft_body"):
            raise ShopError("This page has no draft.")
        self.save_site_page(actor_id, slug, lang, p["draft_title"], p["draft_body"], bool(p["published"]), bool(p["in_footer"]))
        with self.db() as conn:
            conn.execute("UPDATE site_pages SET draft_title = '', draft_body = '', publish_at = '' WHERE id = ?", (p["id"],))

    def publish_due_pages(self, at=None):
        """Drafts whose time has come are published in the name of the person who planned them last."""
        t = (at or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
        with self.db() as conn:
            due = [dict(r) for r in conn.execute("SELECT slug, lang, updated_by FROM site_pages WHERE draft_body != '' AND "
                                                 "publish_at != '' AND publish_at <= ?", (t,))]
        admin = self._first_admin()
        for p in due:
            self.publish_page_draft(admin, p["slug"], p["lang"])
        return len(due)

    def _first_admin(self):
        with self.db() as conn:
            r = conn.execute("SELECT id FROM users WHERE is_admin = 1 ORDER BY id LIMIT 1").fetchone()
        return r["id"] if r else None

    # ================================================================ 4 + 5. one search, and what was not found
    def search_vocabulary(self):
        with self.db() as conn:
            text = " ".join(f"{r['name']} {r['brand']} {r['tags']} {r['category']}" for r in conn.execute(
                "SELECT name, brand, tags, category FROM products WHERE active = 1 AND approved = 1"))
            text += " " + " ".join(r["question"] + " " + r["words"] for r in conn.execute(
                "SELECT question, words FROM kb_articles WHERE published = 1"))
            text += " " + " ".join(r["title"] for r in conn.execute("SELECT title FROM site_pages WHERE published = 1"))
        return sorted({w for w in re.findall(r"[a-z]{3,}", text.lower())})

    def find_everything(self, user_id, q, lang="en", record=True):
        """{"q", "fixed", "products", "articles", "pages", "found", "total"}; "fixed" is the corrected query or ""."""
        q = " ".join(str(q or "").split())[:80]
        out = {"q": q, "fixed": "", "products": [], "articles": [], "pages": [], "found": [], "total": 0}
        if len(q) < 2:
            return out
        words = re.findall(r"[a-z0-9]+", q.lower())
        vocab = self.search_vocabulary()
        fixed = " ".join(fix_word(w, vocab) for w in words)
        use = fixed if fixed != " ".join(words) else q
        out["fixed"] = fixed if use == fixed and use != q.lower() else ""
        out["products"] = self.search(use)[:12]
        out["articles"] = self.kb_search(use, limit=4)
        if len(use.split()) > 1:                    # "shoes watch": nothing has both words -> each word alone
            if not out["products"]:
                seen = set()
                for w in use.split():
                    for pr in self.search(w):
                        if pr.id not in seen and len(seen) < 12:
                            seen.add(pr.id); out["products"].append(pr)
            if not out["articles"]:
                ids = set()
                for w in use.split():
                    for a in self.kb_search(w, limit=4):
                        if a["id"] not in ids and len(ids) < 4:
                            ids.add(a["id"]); out["articles"].append(a)
        low = use.lower()
        out["pages"] = [p for p in self.site_pages() if p["published"] and any(w in p["title"].lower() for w in low.split())][:4]
        out["found"] = [r for r in self.quick_find(user_id, q)][:8]
        out["total"] = len(out["products"]) + len(out["articles"]) + len(out["pages"]) + len(out["found"])
        if record:
            with self.db() as conn:
                conn.execute("INSERT INTO search_log (q, hits, fixed, signed_in, at) VALUES (?, ?, ?, ?, ?)",
                             (q.lower()[:80], out["total"], out["fixed"], 1 if user_id else 0, now()))
        return out

    def search_misses(self, days=30, limit=40):
        """Queries with no result, most asked first: [{"q", "times", "last"}]. Also the corrected ones."""
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        with self.db() as conn:
            miss = [dict(r) for r in conn.execute(
                "SELECT q, COUNT(*) AS times, MAX(at) AS last FROM search_log WHERE hits = 0 AND at >= ? GROUP BY q "
                "ORDER BY COUNT(*) DESC, MAX(at) DESC LIMIT ?", (since, limit))]
            fixed = [dict(r) for r in conn.execute(
                "SELECT q, fixed, COUNT(*) AS times FROM search_log WHERE fixed != '' AND at >= ? GROUP BY q, fixed "
                "ORDER BY COUNT(*) DESC LIMIT ?", (since, limit))]
            total = conn.execute("SELECT COUNT(*) FROM search_log WHERE at >= ?", (since,)).fetchone()[0]
        return {"missed": miss, "fixed": fixed, "total": total}

    # ================================================================ 6. numbers of my own job
    def role_numbers(self, user_id):
        """Small tiles for "My work": only numbers of things this person may work on. [(label, number, endpoint)]"""
        w = self.worker_of(user_id)
        if not w or w["status"] != "Active":
            return []
        abil, out, today = self.abilities_of(user_id), [], date.today().isoformat()

        def count(sql, args=()):
            try:
                with self.db() as conn:
                    return conn.execute(sql, args).fetchone()[0]
            except Exception:
                return None
        tiles = [
            ("support.manage", "Open questions", "SELECT COUNT(*) FROM support_tickets WHERE status = 'Open'", (), "admin_support"),
            ("support.manage", "Parcel claims to decide", "SELECT COUNT(*) FROM parcel_claims WHERE status = 'Waiting'", (), "admin_claims"),
            ("hub.scan", "Parcels on the way to a hub", "SELECT COUNT(*) FROM shipments WHERE status IN ('Picked up', 'In transit', 'At destination hub')", (), "hub_page"),
            ("couriers.manage", "Parcels waiting for a courier", "SELECT COUNT(*) FROM shipments WHERE status = 'Booked' AND courier_id IS NULL", (), "admin_shipments"),
            ("orders.view", "Orders placed today", "SELECT COUNT(*) FROM orders WHERE placed_at >= ?", (today,), "admin_orders"),
            ("returns.manage", "Returns to decide", "SELECT COUNT(*) FROM returns WHERE status = 'Requested'", (), "admin_returns"),
            ("payroll.manage", "Payroll runs to approve or pay", "SELECT COUNT(*) FROM payroll_runs WHERE status IN ('Draft', 'Approved')", (), "admin_payroll"),
            ("staff.manage", "Time off to decide", "SELECT COUNT(*) FROM staff_leave WHERE status = 'Waiting'", (), "admin_staff"),
            ("staff.manage", "Shift swaps to decide", "SELECT COUNT(*) FROM shift_swaps WHERE status = 'Accepted'", (), "admin_swaps"),
            ("products.manage", "Products waiting for approval", "SELECT COUNT(*) FROM products WHERE approved = 0", (), "admin_products"),
            ("site.manage", "Searches with no result (30 days)", "SELECT COUNT(DISTINCT q) FROM search_log WHERE hits = 0", (), "admin_search_misses"),
        ]
        for need, label, sql, args, endpoint in tiles:
            if need in abil:
                n = count(sql, args)
                if n is not None:
                    out.append((label, n, endpoint))
        return out[:6]

    # ================================================================ 7. swapping a shift
    def offer_swap(self, user_id, rota_id, to_staff_id):
        """A worker asks a colleague of the same office to take one of his coming shifts."""
        w = self.worker_of(user_id)
        with self.db() as conn:
            r = conn.execute("SELECT * FROM staff_rota WHERE id = ?", (rota_id,)).fetchone()
            if not w or r is None or r["staff_id"] != w["id"] or r["day"] < date.today().isoformat():
                raise ShopError("You can give away only your own coming shifts.")
            other = conn.execute("SELECT id, office_id, status FROM staff WHERE id = ?", (to_staff_id,)).fetchone()
            if other is None or other["id"] == w["id"] or other["status"] != "Active" or other["office_id"] != r["office_id"]:
                raise ShopError("Please choose an active colleague of the same office.")
            if conn.execute("SELECT 1 FROM staff_rota WHERE staff_id = ? AND day = ?", (to_staff_id, r["day"])).fetchone():
                raise ShopError("This colleague already has a shift on that day.")
            if conn.execute("SELECT 1 FROM shift_swaps WHERE rota_id = ? AND status IN ('Asked', 'Accepted')", (rota_id,)).fetchone():
                raise ShopError("You already asked somebody to take this shift.")
            cur = conn.execute("INSERT INTO shift_swaps (rota_id, from_staff, to_staff, created_at) VALUES (?, ?, ?, ?)",
                               (rota_id, w["id"], to_staff_id, now()))
            uid = conn.execute("SELECT user_id FROM staff WHERE id = ?", (to_staff_id,)).fetchone()["user_id"]
            self.notify(conn, uid, "Can you take a shift?", f"{w['name']} asks you to take the shift of {r['day']} "
                        f"({r['start_hm']}-{r['end_hm']}).", url="/staff#swaps")
            return cur.lastrowid

    def answer_swap(self, user_id, swap_id, yes):
        w = self.worker_of(user_id)
        with self.db() as conn:
            s = conn.execute("SELECT * FROM shift_swaps WHERE id = ?", (swap_id,)).fetchone()
            if not w or s is None or s["to_staff"] != w["id"] or s["status"] != "Asked":
                raise ShopError("This request is not waiting for your answer.")
            conn.execute("UPDATE shift_swaps SET status = ? WHERE id = ?", ("Accepted" if yes else "Refused", swap_id))

    def decide_swap(self, actor_id, swap_id, approve):
        """The manager has the last word: only then the shift plan changes."""
        with self.db() as conn:
            admin, actor, branch = self._actor(conn, actor_id, "staff.manage")
            s = conn.execute("SELECT s.*, r.day, r.office_id FROM shift_swaps s JOIN staff_rota r ON r.id = s.rota_id "
                             "WHERE s.id = ?", (swap_id,)).fetchone()
            if s is None or s["status"] != "Accepted":
                raise ShopError("Only a swap that the colleague accepted can be decided.")
            if not admin and s["office_id"] not in branch:
                raise ShopError("You can decide only swaps of your own branch.")
            if approve:
                if conn.execute("SELECT 1 FROM staff_rota WHERE staff_id = ? AND day = ?", (s["to_staff"], s["day"])).fetchone():
                    raise ShopError("The colleague now has another shift on that day.")
                conn.execute("UPDATE staff_rota SET staff_id = ? WHERE id = ?", (s["to_staff"], s["rota_id"]))
            conn.execute("UPDATE shift_swaps SET status = ?, decided_by = ?, decided_at = ? WHERE id = ?",
                         ("Approved" if approve else "Rejected", actor_id, now(), swap_id))
            self._staff_log(conn, actor_id, s["from_staff"], s["office_id"], "Shift swap " + ("approved" if approve else "rejected"),
                            s["day"])

    def swap_colleagues(self, user_id):
        """Active colleagues of the same office: the people a worker may ask to take a shift."""
        w = self.worker_of(user_id)
        if not w:
            return []
        with self.db() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT s.id, u.name, s.title FROM staff s JOIN users u ON u.id = s.user_id WHERE s.office_id = ? AND "
                "s.status = 'Active' AND s.id != ? ORDER BY u.name", (w["office_id"], w["id"]))]

    def swaps(self, user_id=None, office_ids=None, open_only=True):
        """Swaps of one worker (asked by or of him) or of some offices, newest first."""
        sql = ("SELECT s.*, r.day, r.start_hm, r.end_hm, r.office_id, a.name AS from_name, b.name AS to_name FROM shift_swaps s "
               "JOIN staff_rota r ON r.id = s.rota_id JOIN staff sa ON sa.id = s.from_staff JOIN users a ON a.id = sa.user_id "
               "JOIN staff sb ON sb.id = s.to_staff JOIN users b ON b.id = sb.user_id ORDER BY s.id DESC LIMIT 100")
        with self.db() as conn:
            rows = [dict(r) for r in conn.execute(sql)]
        if user_id:
            w = self.worker_of(user_id)
            rows = [r for r in rows if w and w["id"] in (r["from_staff"], r["to_staff"])]
            for r in rows:
                r["to_me"] = bool(w) and r["to_staff"] == w["id"]
        if office_ids is not None:
            rows = [r for r in rows if r["office_id"] in office_ids]
        return [r for r in rows if not open_only or r["status"] in ("Asked", "Accepted")]

    # ================================================================ 8. first days of a new worker
    def onboarding_of(self, staff_id):
        with self.db() as conn:
            done = {r["item"]: r["done_at"] for r in conn.execute("SELECT item, done_at FROM onboarding_done WHERE staff_id = ?",
                                                                    (staff_id,))}
            auto = {
                "contract": conn.execute("SELECT 1 FROM staff_contracts WHERE staff_id = ?", (staff_id,)).fetchone(),
                "training": conn.execute("SELECT 1 FROM staff_trainings WHERE staff_id = ?", (staff_id,)).fetchone(),
                "clock": conn.execute("SELECT 1 FROM staff_shifts WHERE staff_id = ?", (staff_id,)).fetchone(),
            }
        items = [{"key": k, "text": text, "auto": is_auto, "done": bool(auto.get(k)) if is_auto else k in done,
                  "at": done.get(k, "")} for k, text, is_auto in ONBOARDING]
        return {"items": items, "done": sum(i["done"] for i in items), "total": len(items),
                "complete": all(i["done"] for i in items)}

    def tick_onboarding(self, actor_id, staff_id, item, done=True):
        """The worker himself or his manager ticks a step that the system cannot see."""
        if item not in {k for k, _, is_auto in ONBOARDING if not is_auto}:
            raise ShopError("This step is checked by the system.")
        w = self.worker_of(actor_id)
        if not (w and w["id"] == int(staff_id)):
            with self.db() as conn:
                self._target(conn, actor_id, staff_id, "staff.manage")
        with self.db() as conn:
            conn.execute("DELETE FROM onboarding_done WHERE staff_id = ? AND item = ?", (staff_id, item))
            if done:
                conn.execute("INSERT INTO onboarding_done (staff_id, item, done_by, done_at) VALUES (?, ?, ?, ?)",
                             (staff_id, item, actor_id, now()))

    # ================================================================ 9. who used the office code
    def recent_clock_ins(self, office_id, minutes=15):
        since = (datetime.now() - timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")
        with self.db() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT u.name, s.title, sh.start_at, sh.in_proof FROM staff_shifts sh JOIN staff s ON s.id = sh.staff_id "
                "JOIN users u ON u.id = s.user_id WHERE sh.office_id = ? AND sh.start_at >= ? ORDER BY sh.id DESC LIMIT 8",
                (office_id, since))]
        for r in rows:                                    # a screen on the wall: the first name is enough
            r["name"] = (r["name"] or "").split(" ")[0]
        return rows

    # ================================================================ 10. typing and files at the help desk
    def support_typing(self, number, user_id):
        t = self.support_ticket(number, None if self.can(user_id, "support.manage") else user_id)
        if t is None:
            return
        side = "staff" if t["user_id"] != user_id else "customer"
        with self.db() as conn:
            conn.execute("DELETE FROM support_typing WHERE ticket_id = ? AND side = ?", (t["id"], side))
            conn.execute("INSERT INTO support_typing (ticket_id, side, at) VALUES (?, ?, ?)", (t["id"], side, time.time()))

    def support_messages_after(self, number, user_id, after_id=0):
        data = super().support_messages_after(number, user_id, after_id)
        if data is None:
            return None
        t = self.support_ticket(number)
        other = "customer" if t["user_id"] != user_id else "staff"
        with self.db() as conn:
            r = conn.execute("SELECT at FROM support_typing WHERE ticket_id = ? AND side = ?", (t["id"], other)).fetchone()
        data["typing"] = bool(r) and time.time() - r["at"] < 6
        data["files"] = len(self.support_files(number, user_id) or [])
        return data

    def add_support_file(self, user_id, number, filename, mime, blob):
        """A photo or a PDF (2 MB at most) for a question. Stored as a private file: only the two sides can open it."""
        staff = self.can(user_id, "support.manage")
        t = self.support_ticket(number, None if staff else user_id)
        if t is None:
            raise ShopError("Question not found.")
        if t["status"] == "Closed":
            raise ShopError("This question is closed.")
        if mime not in FILE_TYPES:
            raise ShopError("Please attach a JPG or PNG picture or a PDF file.")
        if not 0 < len(blob) <= MAX_FILE:
            raise ShopError("The file is empty or larger than 2 MB.")
        head = blob[:8]
        real = ("image/jpeg" if head[:3] == b"\xff\xd8\xff" else "image/png" if head == b"\x89PNG\r\n\x1a\n"
                else "application/pdf" if head[:5] == b"%PDF-" else "")
        if real != mime:
            raise ShopError("The file is not what its name says.")
        with self.db() as conn:
            if conn.execute("SELECT COUNT(*) FROM support_files WHERE ticket_id = ?", (t["id"],)).fetchone()[0] >= 10:
                raise ShopError("A question can have 10 files at most.")
        import secrets
        media = f"private/support/{t['number']}-{secrets.token_hex(8)}{FILE_TYPES[mime]}"
        self.save_media(media, mime, blob)
        clean = re.sub(r"[^\w. -]", "_", os.path.basename(str(filename or "file")))[:60] or "file"
        with self.db() as conn:
            cur = conn.execute("INSERT INTO support_files (ticket_id, name, mime, media, size, by_user, from_staff, at) "
                               "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (t["id"], clean, mime, media, len(blob), user_id,
                                                                  1 if t["user_id"] != user_id else 0, now()))
            return cur.lastrowid

    def support_files(self, number, user_id):
        t = self.support_ticket(number, None if self.can(user_id, "support.manage") else user_id)
        if t is None:
            return None
        with self.db() as conn:
            return [dict(r) for r in conn.execute("SELECT id, name, mime, size, from_staff, at FROM support_files WHERE "
                                                  "ticket_id = ? ORDER BY id", (t["id"],))]

    def support_file(self, number, user_id, file_id):
        """(name, mime, bytes) or None - only for the customer who asked and for support workers."""
        if self.support_files(number, user_id) is None:
            return None
        with self.db() as conn:
            r = conn.execute("SELECT f.name, f.mime, f.media FROM support_files f JOIN support_tickets t ON t.id = f.ticket_id "
                             "WHERE f.id = ? AND t.number = ?", (file_id, number)).fetchone()
        if r is None:
            return None
        m = self.media(r["media"])
        return (r["name"], r["mime"], m[1] if isinstance(m, tuple) else m) if m else None

    # ================================================================ 13. stars for an answer
    def rate_support(self, user_id, number, stars, note=""):
        t = self.support_ticket(number, user_id)
        try:
            stars = int(stars)
        except (TypeError, ValueError):
            stars = 0
        if t is None or t["user_id"] != user_id:
            raise ShopError("Question not found.")
        if t["status"] not in ("Answered", "Solved", "Closed"):
            raise ShopError("You can rate a question after we answered it.")
        if not 1 <= stars <= 5:
            raise ShopError("Please choose 1 to 5 stars.")
        with self.db() as conn:
            conn.execute("DELETE FROM support_ratings WHERE ticket_id = ?", (t["id"],))
            conn.execute("INSERT INTO support_ratings (ticket_id, stars, note, at) VALUES (?, ?, ?, ?)",
                         (t["id"], stars, " ".join(str(note or "").split())[:300], now()))

    def support_rating(self, number):
        with self.db() as conn:
            r = conn.execute("SELECT r.stars, r.note FROM support_ratings r JOIN support_tickets t ON t.id = r.ticket_id "
                             "WHERE t.number = ?", (number,)).fetchone()
        return dict(r) if r else None

    def support_rating_summary(self):
        with self.db() as conn:
            r = conn.execute("SELECT COUNT(*) AS n, COALESCE(SUM(stars), 0) AS total, COALESCE(SUM(CASE WHEN stars <= 2 THEN 1 "
                             "ELSE 0 END), 0) AS low FROM support_ratings").fetchone()
        return {"count": r["n"], "average": round(r["total"] / r["n"], 2) if r["n"] else None, "low": r["low"]}

    # ================================================================ 11 + 12. pay preference, buy again, new address
    def set_pay_pref(self, user_id, method):
        if method not in ("", "card", "wallet", "upi", "cod"):
            raise ShopError("Please choose a way to pay from the list.")
        with self.db() as conn:
            conn.execute("UPDATE users SET pay_pref = ? WHERE id = ?", (method, user_id))

    def pay_pref(self, user_id):
        with self.db() as conn:
            r = conn.execute("SELECT pay_pref FROM users WHERE id = ?", (user_id,)).fetchone()
        return (r["pay_pref"] or "") if r else ""

    def reorder(self, user_id, number):
        """Put what can still be bought from an old order into the cart. Returns (lines added, names not available)."""
        o = self.get_order(user_id, number)
        cart = dict(self.get_cart(user_id))
        added, missing = 0, []
        for it in o["items"]:
            try:
                p = self.get_product(pid=it["product_id"])
            except ShopError:
                p = None
            if p is None or not p.active or not getattr(p, "approved", 1) or p.stock <= 0:
                missing.append(it["name"])
                continue
            cart[str(p.id)] = min(p.stock, max(int(cart.get(str(p.id), 0)), 0) + it["qty"])
            added += 1
        if added:
            self.save_cart(user_id, cart)
        return added, missing

    def change_order_address(self, user_id, number, address_id):
        """Before an order is shipped its address can change to another saved address in the same country
        (prices, tax and the delivery fee were counted for that country)."""
        o = self.get_order(user_id, number)
        if not o["can_cancel"]:
            raise ShopError("This order is already shipped (or cancelled); its address cannot change any more.")
        a = next((x for x in self.addresses(user_id) if x["id"] == int(address_id)), None)
        if a is None:
            raise ShopError("Please choose one of your saved addresses.")
        if (a.get("country") or "IN") != (o.get("country") or "IN"):
            raise ShopError("The new address must be in the same country as the order.")
        line = ", ".join(x for x in (a.get("line1"), a.get("line2"), a.get("city"), a.get("state")) if x)
        with self.db() as conn:
            conn.execute("UPDATE orders SET ship_name = ?, ship_mobile = ?, ship_address = ?, ship_pincode = ? WHERE id = ?",
                         (a["name"], a["mobile"], line, a["pincode"], o["id"]))
        self.log(user_id, "Order address changed", number)

    # ================================================================ 14 + 15. sellers
    def seller_insights(self, seller_id, days=90):
        since = (date.today() - timedelta(days=days)).isoformat()
        with self.db() as conn:
            best = [dict(r) for r in conn.execute(
                "SELECT p.name, SUM(i.qty) AS sold, SUM(i.qty * i.price) AS money FROM order_items i JOIN products p ON "
                "p.id = i.product_id JOIN orders o ON o.id = i.order_id WHERE p.seller_id = ? AND o.cancelled_at = '' AND "
                "o.placed_at >= ? GROUP BY p.id, p.name ORDER BY SUM(i.qty) DESC LIMIT 8", (seller_id, since))]
            reasons = [dict(r) for r in conn.execute(
                "SELECT r.reason, COUNT(DISTINCT r.id) AS times FROM returns r WHERE r.created_at >= ? AND EXISTS (SELECT 1 FROM "
                "order_items i JOIN products p ON p.id = i.product_id WHERE i.order_id = r.order_id AND p.seller_id = ?) "
                "GROUP BY r.reason ORDER BY COUNT(DISTINCT r.id) DESC", (since, seller_id))]
            low = [dict(r) for r in conn.execute("SELECT name, stock FROM products WHERE seller_id = ? AND active = 1 AND "
                                                 "stock <= 5 ORDER BY stock, name LIMIT 20", (seller_id,))]
            never = [dict(r) for r in conn.execute(
                "SELECT p.name FROM products p WHERE p.seller_id = ? AND p.active = 1 AND p.approved = 1 AND NOT EXISTS "
                "(SELECT 1 FROM order_items i JOIN orders o ON o.id = i.order_id WHERE i.product_id = p.id AND "
                "o.placed_at >= ?) ORDER BY p.name LIMIT 10", (seller_id, since))]
        return {"days": days, "best": best, "reasons": reasons, "low": low, "never": never,
                "sold": sum(b["sold"] for b in best), "money": sum(b["money"] for b in best)}

    def import_products_csv(self, seller_id, text):
        """Many products from one CSV file (columns: name, brand, category, price, mrp, stock, about, tags).
        Every good row becomes a product that is switched off until it has a picture and is approved.
        Returns {"added": n, "errors": [(row number, reason)]}."""
        try:
            rows = list(csv.DictReader(io.StringIO(str(text or "").lstrip("﻿"))))
        except csv.Error:
            raise ShopError("This file is not a CSV table.")
        if not rows:
            raise ShopError("The file has no rows.")
        if len(rows) > CSV_MAX:
            raise ShopError(f"A file can have {CSV_MAX} products at most.")
        head = {str(k or "").strip().lower() for k in rows[0]}
        need = {"name", "brand", "category", "price", "stock"} - head
        if need:
            raise ShopError("The file needs these columns: " + ", ".join(sorted(need)) + ".")
        added, errors = 0, []
        for n, row in enumerate(rows, 2):
            r = {str(k or "").strip().lower(): str(v or "").strip() for k, v in row.items()}
            if any(v[:1] in ("=", "+", "@") for v in r.values()):          # a formula would run in a spreadsheet
                errors.append((n, "A value starts with =, + or @."))
                continue
            form = {"name": r.get("name", ""), "brand": r.get("brand", ""), "category": r.get("category", "").lower(),
                    "price": r.get("price", ""), "mrp": r.get("mrp") or r.get("price", ""), "stock": r.get("stock", ""),
                    "about": (r.get("about") or r.get("name", "")).replace(" | ", "\n"), "tags": r.get("tags", ""), "active": "no"}
            try:
                self.save_seller_product(seller_id, form)
                added += 1
            except ShopError as e:
                errors.append((n, str(e)))
        return {"added": added, "errors": errors}

    # ================================================================ 17. webhook signature tool
    @staticmethod
    def check_signature(secret, header, body):
        """For the partner's programmer: is this X-Yo-Signature right for this body and secret? Nothing is stored."""
        body = body.encode("utf-8") if isinstance(body, str) else body
        m = re.search(r"t=(\d+)", str(header or ""))
        if not secret or not m:
            return {"ok": False, "why": "The header must look like t=1790000000,v1=..."}
        fresh = signature_ok(secret, header, body)
        anytime = signature_ok(secret, header, body, at=int(m.group(1)))
        return {"ok": bool(anytime), "fresh": bool(fresh),
                "why": "" if fresh else "The signature is right, but older than 5 minutes." if anytime else
                "The signature does not fit this body and secret."}

    # ================================================================ 19. status page
    def system_status(self):
        """[{"part", "ok", "note"}] - simple checks that a visitor may see (no secrets, no numbers of customers)."""
        parts = []

        def add(part, ok, note=""):
            parts.append({"part": part, "ok": bool(ok), "note": note})
        try:
            with self.db() as conn:
                conn.execute("SELECT 1").fetchone()
                waiting = conn.execute("SELECT COUNT(*) FROM jobs WHERE status = 'waiting' AND run_at <= ?", (now(),)).fetchone()[0]
                failed = conn.execute("SELECT COUNT(*) FROM jobs WHERE status = 'failed' AND done_at >= ?",
                                      ((datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S"),)).fetchone()[0]
                last = conn.execute("SELECT at, ok FROM backup_checks ORDER BY id DESC LIMIT 1").fetchone()
                feeds = conn.execute("SELECT COUNT(*) FROM gtfs_feeds WHERE rt_url != '' AND rt_result LIKE 'Not read%'").fetchone()[0]
            add("Shop and accounts", True)
            add("Background work", waiting < 50, "" if waiting < 50 else "Many jobs are waiting")
            add("Messages and webhooks", failed < 5, "" if failed < 5 else "Some jobs failed in the last 24 hours")
            add("Live timetables", feeds == 0, "" if feeds == 0 else "A live feed could not be read")
            add("Backups", last is None or bool(last["ok"]), "No backup yet" if last is None else f"Last restore test: {last['at'][:16]}")
        except Exception:
            add("Shop and accounts", False, "The database does not answer")
        add("Card payments", True, "Test mode")
        return parts

    # ================================================================ 20. limits per person
    def rate_ok(self, name, who):
        """True while this person (or address) is under the limit of this action; a refusal is written down."""
        limit, per = LIMITS[name]
        slot = int(time.time() // per)
        bucket = f"{name}:{who}"[:120]
        with self.db() as conn:
            r = conn.execute("SELECT n FROM rate_hits WHERE bucket = ? AND slot = ?", (bucket, slot)).fetchone()
            if r is None:
                conn.execute("DELETE FROM rate_hits WHERE slot < ?", (slot - 2,)) if slot % 7 == 0 else None
                conn.execute("INSERT INTO rate_hits (bucket, slot, n) VALUES (?, ?, 1)", (bucket, slot))
                return True
            if r["n"] >= limit:
                if r["n"] == limit:                       # write the block down once per window
                    conn.execute("INSERT INTO rate_blocks (what, who, at) VALUES (?, ?, ?)", (name, str(who)[:80], now()))
                conn.execute("UPDATE rate_hits SET n = n + 1 WHERE bucket = ? AND slot = ?", (bucket, slot))
                return False
            conn.execute("UPDATE rate_hits SET n = n + 1 WHERE bucket = ? AND slot = ?", (bucket, slot))
            return True

    def rate_report(self, days=7):
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        with self.db() as conn:
            rows = [dict(r) for r in conn.execute("SELECT what, who, at FROM rate_blocks WHERE at >= ? ORDER BY id DESC LIMIT 100",
                                                  (since,))]
        by = {}
        for r in rows:
            by[r["what"]] = by.get(r["what"], 0) + 1
        return {"rows": rows, "by": by, "limits": LIMITS}

    # ================================================================ 21. nightly backup with a restore test
    def nightly_backup(self, at=None):
        """Once a night (one machine only): make a backup, then prove it by restoring it into an empty database."""
        at = at or datetime.now()
        if at.hour < 2 or not self.claim_period("last_nightly_backup", at.strftime("%Y-%m-%d")):
            return None
        backup_id, blob = self.save_backup("auto", "nightly")
        return self.check_backup(backup_id, blob)

    def check_backup(self, backup_id, blob=None):
        """Restore the backup into a new empty SQLite database and compare every table with the backup itself."""
        blob = blob if blob is not None else self.backup_blob(backup_id)
        ok, tables, rows, note = False, 0, 0, ""
        try:
            data = self.unpack(blob)
            with tempfile.TemporaryDirectory() as tmp:
                copy = type(self)(os.path.join(tmp, "restore-test.db"), env=dict(self.env))
                copy.import_data(data)
                with copy.db() as conn:
                    bad = []
                    for t, want in data["tables"].items():
                        have = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]           # noqa: S608 (our own table names)
                        tables, rows = tables + 1, rows + have
                        if have != len(want):
                            bad.append(f"{t}: {have} of {len(want)}")
                ok, note = not bad, "; ".join(bad)[:200]
        except Exception as e:
            note = f"{type(e).__name__}: {e}"[:200]
        with self.db() as conn:
            conn.execute("INSERT INTO backup_checks (backup_id, ok, tables, row_count, note, at) VALUES (?, ?, ?, ?, ?, ?)",
                         (backup_id, 1 if ok else 0, tables, rows, note, now()))
        return {"ok": ok, "tables": tables, "rows": rows, "note": note}

    def backup_checks(self, limit=10):
        with self.db() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM backup_checks ORDER BY id DESC LIMIT ?", (limit,))]

    # ================================================================ 16. the vehicle on the live maps
    def trip_live(self, pnr, user_id, at=None):
        """The ticket map: when the operator's live feed has a fresh position of this bus, the map shows that position
        instead of the place worked out from the timetable."""
        d = super().trip_live(pnr, user_id, at)
        d["seen"], d["source"] = "", "timetable"
        if d.get("done"):
            return d
        try:
            live = self.booking_live(self.get_booking(pnr, user_id))
        except Exception:
            return d
        v = live.get("vehicle")
        if v and v.get("lat") is not None:
            mode = (d["courier"] or {}).get("vehicle") or "bus"
            d["courier"] = {"lat": v["lat"], "lng": v["lng"], "name": v.get("label") or d["number"], "vehicle": mode}
            d["by_timetable"], d["source"], d["seen"] = False, "operator", str(v["at"])[:19]
        return d

    def live(self, number, user_id=None, at=None):
        """The parcel map: also the time of the last GPS point of the courier ("seen"), so the page can say how old it is."""
        d = super().live(number, user_id, at)
        d["seen"], d["source"] = "", "courier"
        if d.get("courier"):
            with self.db() as conn:
                r = conn.execute("SELECT t.at FROM courier_track t JOIN shipments s ON s.id = t.shipment_id WHERE s.number = ? "
                                 "ORDER BY t.id DESC LIMIT 1", (str(number or "").strip().upper(),)).fetchone()
            d["seen"] = str(r["at"])[:19] if r else ""
        return d

    def housekeeping(self, today=None):
        done = super().housekeeping(today)
        if done is None:
            return None
        for name, job in (("pages", self.publish_due_pages), ("nightly_backup", self.nightly_backup)):
            try:
                done[name] = job()
            except Exception as e:
                done[name + "_error"] = str(e)[:120]
        return done
