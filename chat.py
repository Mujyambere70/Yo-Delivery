"""
chat.py - version 5 (mixin class used by Store): chat between customers and sellers, with moderation.

How it works
    A customer asks a seller about a product (or an order). One conversation ("thread") per customer, seller and
    product. Both sides see the messages in their account; the other side gets a notification.
Moderation (automatic first, then the admin)
    * phone numbers, e-mail addresses and links are hidden ("[hidden by Yo]"): payments and talks stay on Yo
      Delivery, where the buyer is protected - the message is still delivered;
    * rude words (a short list in the 7 languages) and messages that look like a payment outside the shop
      ("pay me directly", "Western Union", ...) are held: the admin approves or hides them;
    * at most 20 messages in 10 minutes per person (spam);
    * every message can be reported; reported messages go to the admin queue too.
Privacy
    The seller sees only the customer's first name - never the address, phone or e-mail.
"""

import re

from chatmodel import HOLD_P
from datetime import datetime, timedelta

from core import ShopError, now

CHAT_SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_threads (
    id INTEGER PRIMARY KEY AUTOINCREMENT, customer_id INTEGER NOT NULL, seller_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL DEFAULT 0, order_number TEXT NOT NULL DEFAULT '', subject TEXT NOT NULL,
    created_at TEXT NOT NULL, last_at TEXT NOT NULL, customer_seen TEXT NOT NULL DEFAULT '',
    seller_seen TEXT NOT NULL DEFAULT '', UNIQUE (customer_id, seller_id, product_id, order_number)
);
CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, thread_id INTEGER NOT NULL, sender_id INTEGER NOT NULL,
    side TEXT NOT NULL, body TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Visible', reason TEXT NOT NULL DEFAULT '',
    reported INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, decided_by TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_thread ON chat_messages(thread_id);
CREATE INDEX IF NOT EXISTS idx_chat_threads_customer ON chat_threads(customer_id);
CREATE INDEX IF NOT EXISTS idx_chat_threads_seller ON chat_threads(seller_id);
"""

VISIBLE, HELD, HIDDEN = "Visible", "Held", "Hidden"
MAX_LEN = 1000
RATE = (20, 10)                     # 20 messages in 10 minutes
MASK = "[hidden by Yo]"

PHONE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{7,}\d(?!\w)")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
LINK = re.compile(r"(https?://\S+|www\.\S+|\b[\w-]+\.(com|net|org|ru|in|rw|io|me|info|biz|xyz|link|ly)\b\S*)", re.I)
# a short list of rude words (7 languages) - enough to hold the message for a person to look at
RUDE = ["idiot", "stupid", "fool", "bastard", "shit", "fuck", "damn you",
        "дурак", "идиот", "тупой", "сволочь", "блин",
        "imbécile", "connard", "merde", "idiota", "estúpido", "mierda", "غبي", "حمار", "傻瓜", "笨蛋", "ikigoryi", "injiji"]
OFF_SHOP = ["pay me directly", "pay outside", "western union", "moneygram", "send money to my", "bank transfer to me",
            "оплати напрямую", "переведи на карту", "payez-moi directement", "págame directamente", "nyishyura ku ruhande"]


def moderate(text):
    """(clean text, status, reason) for one message."""
    body = " ".join(str(text or "").replace("\r", "").split(" ")).strip()
    body = re.sub(r"\n{3,}", "\n\n", body)[:MAX_LEN]
    if not body.strip():
        raise ShopError("Please write a message.")
    reasons = []
    masked = EMAIL.sub(MASK, body)
    masked = LINK.sub(MASK, masked)
    masked = PHONE.sub(MASK, masked)
    if masked != body:
        reasons.append("Contact details or links were hidden: please talk and pay only on Yo Delivery.")
    from chatmodel import normalize             # version 6: "id1ot", "p.a.y m3 directly" are found too
    norm = normalize(masked)
    low = masked.lower() + " \n " + norm + " \n " + re.sub(r"([^\W\d_])\1+", r"\1", norm)
    status = VISIBLE
    if any(w in low for w in RUDE):
        status = HELD
        reasons.append("Rude words: the message waits for a moderator.")
    if any(w in low for w in OFF_SHOP):
        status = HELD
        reasons.append("Payment outside the shop: the message waits for a moderator.")
    return masked, status, " ".join(reasons)


class ChatMixin:

    def _thread_row(self, conn, thread_id):
        r = conn.execute("SELECT t.*, s.shop_name, s.user_id AS seller_user, u.name AS customer_name, p.name AS product, "
                         "p.slug FROM chat_threads t JOIN sellers s ON s.id = t.seller_id JOIN users u "
                         "ON u.id = t.customer_id LEFT JOIN products p ON p.id = t.product_id WHERE t.id = ?",
                         (thread_id,)).fetchone()
        if r is None:
            raise ShopError("Conversation not found.")
        return dict(r)

    def _side(self, t, user_id, admin=False):
        if user_id == t["customer_id"]:
            return "customer"
        if user_id == t["seller_user"]:
            return "seller"
        if admin:
            return "admin"
        raise ShopError("Conversation not found.")

    def start_chat(self, customer_id, product_id=0, order_number="", text="", when=None):
        """A customer writes to the seller of a product (or of an order). Returns the thread id."""
        when = when or now()
        with self.db() as conn:
            seller_id, subject = None, ""
            if product_id:
                p = conn.execute("SELECT id, name, seller_id FROM products WHERE id = ?", (int(product_id),)).fetchone()
                if p is None or not p["seller_id"]:
                    raise ShopError("This product is sold by Yo Delivery itself: please use Help & Support.")
                seller_id, subject = p["seller_id"], p["name"][:120]
            elif order_number:
                o = conn.execute("SELECT o.id, o.number, o.user_id, MIN(i.seller_id) AS seller_id FROM orders o "
                                 "JOIN order_items i ON i.order_id = o.id WHERE o.number = ? AND i.seller_id IS NOT NULL "
                                 "GROUP BY o.id, o.number, o.user_id", (order_number,)).fetchone()
                if o is None or o["user_id"] != customer_id:
                    raise ShopError("Order not found.")
                seller_id, subject = o["seller_id"], f"Order {o['number']}"
            else:
                raise ShopError("Please choose a product or an order.")
            s = conn.execute("SELECT user_id, status FROM sellers WHERE id = ?", (seller_id,)).fetchone()
            if s is None or s["status"] != "Approved":
                raise ShopError("This seller cannot get messages now.")
            if s["user_id"] == customer_id:
                raise ShopError("This is your own shop.")
            conn.execute("INSERT INTO chat_threads (customer_id, seller_id, product_id, order_number, subject, created_at, "
                         "last_at) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                         (customer_id, seller_id, int(product_id or 0), order_number or "", subject, when, when))
            tid = conn.execute("SELECT id FROM chat_threads WHERE customer_id = ? AND seller_id = ? AND product_id = ? "
                               "AND order_number = ?", (customer_id, seller_id, int(product_id or 0),
                                                        order_number or "")).fetchone()[0]
        if str(text or "").strip():
            self.send_chat(customer_id, tid, text, when=when)
        return tid

    def send_chat(self, user_id, thread_id, text, when=None):
        """Send a message; returns {"id", "status", "reason"}. Held messages are seen only by the sender."""
        when = when or now()
        body, status, reason = moderate(text)
        if status == VISIBLE and hasattr(self, "chat_bad_probability"):     # version 6: the model that learns
            if self.chat_bad_probability(body) >= HOLD_P:
                status = HELD
                reason = (reason + " " if reason else "") + "The moderation model thinks a moderator should read " \
                                                             "this message first."
            elif hasattr(self, "external_moderation") and self.external_moderation(body):   # version 7
                status = HELD
                reason = "The moderation service thinks a moderator should read this message first."
        since = (datetime.strptime(when, "%Y-%m-%d %H:%M:%S") - timedelta(minutes=RATE[1])).strftime("%Y-%m-%d %H:%M:%S")
        with self.db() as conn:
            t = self._thread_row(conn, thread_id)
            side = self._side(t, user_id)
            sent = conn.execute("SELECT COUNT(*) FROM chat_messages WHERE sender_id = ? AND created_at >= ?",
                                (user_id, since)).fetchone()[0]
            if sent >= RATE[0]:
                raise ShopError(f"Too many messages. Please wait a few minutes (at most {RATE[0]} in {RATE[1]} minutes).")
            mid = conn.execute("INSERT INTO chat_messages (thread_id, sender_id, side, body, status, reason, created_at) "
                               "VALUES (?, ?, ?, ?, ?, ?, ?)", (thread_id, user_id, side, body, status, reason, when)).lastrowid
            seen_col = "customer_seen" if side == "customer" else "seller_seen"
            conn.execute(f"UPDATE chat_threads SET last_at = ?, {seen_col} = ? WHERE id = ?", (when, when, thread_id))
            if status == VISIBLE:
                self._chat_notify(conn, t, side, body)
        return {"id": mid, "status": status, "reason": reason, "body": body}

    def _chat_notify(self, conn, t, side, body):
        to = t["seller_user"] if side == "customer" else t["customer_id"]
        who = t["customer_name"].split()[0] if side == "customer" else t["shop_name"]
        self.notify(conn, to, "New message from {0}".replace("{0}", who),
                    f"{t['subject']}: {body[:140]}", url=f"/messages/{t['id']}")

    def chat_threads(self, user_id):
        """My conversations: as a customer and, if I have a shop, as a seller (newest first, with unread flag)."""
        with self.db() as conn:
            rows = conn.execute(
                "SELECT t.*, s.shop_name, s.user_id AS seller_user, u.name AS customer_name, "
                "(SELECT body FROM chat_messages m WHERE m.thread_id = t.id AND (m.status = 'Visible' OR m.sender_id = ?) "
                "ORDER BY m.id DESC LIMIT 1) AS last_body FROM chat_threads t JOIN sellers s ON s.id = t.seller_id "
                "JOIN users u ON u.id = t.customer_id WHERE t.customer_id = ? OR s.user_id = ? ORDER BY t.last_at DESC",
                (user_id, user_id, user_id)).fetchall()
            out = []
            for r in rows:
                r = dict(r)
                side = "customer" if r["customer_id"] == user_id else "seller"
                seen = r["customer_seen"] if side == "customer" else r["seller_seen"]
                r["side"] = side
                r["other"] = r["shop_name"] if side == "customer" else r["customer_name"].split()[0]
                r["unread"] = conn.execute(
                    "SELECT COUNT(*) FROM chat_messages WHERE thread_id = ? AND sender_id <> ? AND status = 'Visible' "
                    "AND created_at > ?", (r["id"], user_id, seen)).fetchone()[0]
                out.append(r)
        return out

    def chat_unread(self, user_id):
        return sum(t["unread"] for t in self.chat_threads(user_id))

    def chat_thread(self, user_id, thread_id, admin=False, mark_seen=True):
        """A conversation and its messages (held / hidden ones only for their sender and the admin)."""
        with self.db() as conn:
            t = self._thread_row(conn, thread_id)
            side = self._side(t, user_id, admin)
            rows = [dict(r) for r in conn.execute("SELECT * FROM chat_messages WHERE thread_id = ? ORDER BY id",
                                                  (thread_id,))]
            if mark_seen and side in ("customer", "seller"):
                conn.execute(f"UPDATE chat_threads SET {side}_seen = ? WHERE id = ?", (now(), thread_id))
        msgs = [m for m in rows if side == "admin" or m["status"] == VISIBLE or m["sender_id"] == user_id]
        for m in msgs:
            m["mine"] = m["sender_id"] == user_id
            m["who"] = t["shop_name"] if m["side"] == "seller" else "Yo Delivery" if m["side"] == "admin" else \
                t["customer_name"].split()[0]
        t["side"] = side
        t["other"] = t["shop_name"] if side == "customer" else t["customer_name"].split()[0]
        return t, msgs

    def report_chat(self, user_id, message_id):
        """The receiver reports a message: it is hidden from them and goes to the admin queue."""
        with self.db() as conn:
            m = conn.execute("SELECT * FROM chat_messages WHERE id = ?", (message_id,)).fetchone()
            if m is None:
                raise ShopError("Message not found.")
            t = self._thread_row(conn, m["thread_id"])
            self._side(t, user_id)
            if m["sender_id"] == user_id:
                raise ShopError("You cannot report your own message.")
            conn.execute("UPDATE chat_messages SET reported = 1, status = ?, reason = ? WHERE id = ? AND status = ?",
                         (HELD, "Reported by the other person", message_id, VISIBLE))

    def chat_queue(self):
        """Messages waiting for the admin: held by the filters or reported."""
        with self.db() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT m.*, t.subject, u.name AS sender FROM chat_messages m JOIN chat_threads t ON t.id = m.thread_id "
                "JOIN users u ON u.id = m.sender_id WHERE m.status = ? ORDER BY m.id", (HELD,))]

    def decide_chat(self, message_id, approve, admin_name):
        """The admin lets a held message through (the other side is notified) or hides it for good."""
        with self.db() as conn:
            m = conn.execute("SELECT * FROM chat_messages WHERE id = ?", (message_id,)).fetchone()
            if m is None or m["status"] != HELD:
                raise ShopError("This message is not waiting for a decision.")
            new = VISIBLE if approve else HIDDEN
            conn.execute("UPDATE chat_messages SET status = ?, decided_by = ? WHERE id = ? AND status = ?",
                         (new, admin_name, message_id, HELD))
            if hasattr(self, "learn_chat_decision"):     # version 6: every decision teaches the model
                self.learn_chat_decision(conn, message_id, m["body"], approve)
            if approve and not m["reported"]:
                self._chat_notify(conn, self._thread_row(conn, m["thread_id"]), m["side"], m["body"])
        return new
