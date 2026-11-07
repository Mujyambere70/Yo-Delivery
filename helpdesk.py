"""
helpdesk.py - version 7 (mixin class used by Store): the help desk, parcel claims and the safety check of orders.

    help desk   a customer opens a support ticket (about an order, a parcel, a ticket, money or something else); it gets
                a number and a promise: a first answer within 24 hours (4 hours when it is urgent). Support workers
                (ability "support.manage") see the queue sorted by that time, take tickets, answer and close them; the
                customer answers on the same page. Late tickets are counted on the page and on the monitoring page.
    claims      the sender of a parcel that is lost (not delivered 3 days after the expected day) or damaged (reported
                within 7 days of the delivery) claims money back. An insured parcel is covered up to its declared value;
                a parcel without insurance up to Rs 2,000 (example limit). A support worker decides; the money goes to
                the sender's Yo Pay wallet, once.
    risk check  every new order gets a risk score from simple rules (new account, big amount, many orders in an hour,
                cash on delivery of a big amount, failed payments, the same phone on many accounts). With 70 points or
                more the order waits ("Safety check") until a worker with "risk.review" releases or cancels it. A new
                customer's first big cash order alone (25 + 20 + 15 = 60) is not held: one more sign is needed.
"""

from datetime import datetime, timedelta

import courier as C
from core import ShopError, now, to_dt

SUPPORT_SCHEMA = """
CREATE TABLE IF NOT EXISTS support_tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT, number TEXT NOT NULL UNIQUE, user_id INTEGER NOT NULL REFERENCES users(id),
    topic TEXT NOT NULL, about TEXT NOT NULL DEFAULT '', subject TEXT NOT NULL, priority TEXT NOT NULL DEFAULT 'Normal',
    status TEXT NOT NULL DEFAULT 'Open', assigned_to INTEGER, due_at TEXT NOT NULL, first_answer_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, closed_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_support_status ON support_tickets(status, due_at);
CREATE TABLE IF NOT EXISTS support_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id INTEGER NOT NULL REFERENCES support_tickets(id),
    sender_id INTEGER NOT NULL, from_staff INTEGER NOT NULL DEFAULT 0, body TEXT NOT NULL, at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS parcel_claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT, shipment_id INTEGER NOT NULL REFERENCES shipments(id),
    user_id INTEGER NOT NULL REFERENCES users(id), kind TEXT NOT NULL, details TEXT NOT NULL,
    amount INTEGER NOT NULL, cover INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'Waiting', payout INTEGER NOT NULL DEFAULT 0,
    decided_by INTEGER, decided_at TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_claim_once ON parcel_claims(shipment_id) WHERE status IN ('Waiting', 'Paid');
"""

TOPICS = {"order": "An order", "parcel": "A parcel", "ticket": "A travel ticket", "money": "Money and Yo Pay",
          "account": "My account", "other": "Something else"}
SLA_HOURS = {"Normal": 24, "Urgent": 4}
STATUSES = ("Open", "Answered", "Closed")
CLAIM_KINDS = {"lost": "Lost parcel", "damaged": "Damaged parcel"}
UNINSURED_LIMIT = 200000                          # Rs 2,000 (example limit of liability without insurance)
DAMAGE_DAYS = 7
LOST_AFTER_DAYS = 3
RISK_HOLD = 70


def _clean(text, n):
    return " ".join(str(text or "").split())[:n]


def _plus_hours(text, hours):
    return (datetime.strptime(text, "%Y-%m-%d %H:%M:%S") + timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")


class SupportMixin:

    # ================================================================ help desk
    def open_support_ticket(self, user_id, topic, subject, body, about="", urgent=False, when=None):
        if topic not in TOPICS:
            raise ShopError("Please choose what your question is about.")
        subject, body = _clean(subject, 120), str(body or "").strip()[:3000]
        if len(subject) < 3 or len(body) < 5:
            raise ShopError("Please write a short title and your question.")
        when = when or now()
        priority = "Urgent" if urgent else "Normal"
        with self.db() as conn:
            open_n = conn.execute("SELECT COUNT(*) FROM support_tickets WHERE user_id = ? AND status != 'Closed'",
                                  (user_id,)).fetchone()[0]
            if open_n >= 5:
                raise ShopError("You have 5 open questions. Please wait for our answers first.")
            cur = conn.execute("INSERT INTO support_tickets (number, user_id, topic, about, subject, priority, due_at, "
                               "created_at, updated_at) VALUES ('', ?, ?, ?, ?, ?, ?, ?, ?)",
                               (user_id, topic, _clean(about, 40).upper(), subject, priority,
                                _plus_hours(when, SLA_HOURS[priority]), when, when))
            number = f"S{cur.lastrowid:06d}"
            conn.execute("UPDATE support_tickets SET number = ? WHERE id = ?", (number, cur.lastrowid))
            conn.execute("INSERT INTO support_messages (ticket_id, sender_id, from_staff, body, at) VALUES (?, ?, 0, ?, ?)",
                         (cur.lastrowid, user_id, body, when))
            self.notify(conn, user_id, "We got your question: {0}".replace("{0}", number),
                        f"We answer within {SLA_HOURS[priority]} hours.", url=f"/support/{number}")
        return number

    def support_ticket(self, number, user_id=None):
        with self.db() as conn:
            t = conn.execute("SELECT t.*, u.name AS customer, a.name AS agent FROM support_tickets t JOIN users u ON "
                             "u.id = t.user_id LEFT JOIN users a ON a.id = t.assigned_to WHERE t.number = ?",
                             (str(number).upper(),)).fetchone()
            if t is None or (user_id is not None and t["user_id"] != user_id):
                return None
            msgs = [dict(m) for m in conn.execute(
                "SELECT m.*, u.name FROM support_messages m JOIN users u ON u.id = m.sender_id WHERE m.ticket_id = ? "
                "ORDER BY m.id", (t["id"],))]
        d = dict(t)
        d["messages"] = msgs
        d["late"] = not d["first_answer_at"] and d["status"] != "Closed" and d["due_at"] < now()
        return d

    def my_support_tickets(self, user_id):
        with self.db() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM support_tickets WHERE user_id = ? ORDER BY id DESC",
                                                  (user_id,))]

    def support_queue(self, status="", mine_of=None):
        q = "SELECT t.*, u.name AS customer, a.name AS agent FROM support_tickets t JOIN users u ON u.id = t.user_id " \
            "LEFT JOIN users a ON a.id = t.assigned_to"
        where, args = [], []
        if status:
            where.append("t.status = ?")
            args.append(status)
        else:
            where.append("t.status != 'Closed'")
        if mine_of:
            where.append("t.assigned_to = ?")
            args.append(mine_of)
        with self.db() as conn:
            rows = [dict(r) for r in conn.execute(q + " WHERE " + " AND ".join(where) + " ORDER BY t.due_at LIMIT 200",
                                                  args)]
        t_now = now()
        for r in rows:
            r["late"] = not r["first_answer_at"] and r["status"] != "Closed" and r["due_at"] < t_now
        return rows

    def support_numbers(self):
        t_now = now()
        with self.db() as conn:
            open_n = conn.execute("SELECT COUNT(*) FROM support_tickets WHERE status != 'Closed'").fetchone()[0]
            late = conn.execute("SELECT COUNT(*) FROM support_tickets WHERE status != 'Closed' AND first_answer_at = '' "
                                "AND due_at < ?", (t_now,)).fetchone()[0]
            answered = [dict(r) for r in conn.execute(
                "SELECT created_at, first_answer_at, due_at FROM support_tickets WHERE first_answer_at != '' "
                "ORDER BY id DESC LIMIT 200")]
        in_time = sum(1 for r in answered if r["first_answer_at"] <= r["due_at"])
        return {"open": open_n, "late": late, "answered": len(answered),
                "in_time_pct": round(100 * in_time / len(answered)) if answered else None}

    def support_reply(self, user_id, number, body, close=False):
        body = str(body or "").strip()[:3000]
        t = self.support_ticket(number)
        if t is None:
            raise ShopError("Question not found.")
        staff = t["user_id"] != user_id
        if staff and not self.can(user_id, "support.manage"):
            raise ShopError("Question not found.")
        if not body and not close:
            raise ShopError("Please write your answer.")
        when = now()
        with self.db() as conn:
            if t["status"] == "Closed" and not staff:
                conn.execute("UPDATE support_tickets SET status = 'Open', closed_at = '' WHERE id = ?", (t["id"],))
            if body:
                conn.execute("INSERT INTO support_messages (ticket_id, sender_id, from_staff, body, at) VALUES "
                             "(?, ?, ?, ?, ?)", (t["id"], user_id, 1 if staff else 0, body, when))
            if staff:
                status = "Closed" if close else "Answered"
                conn.execute("UPDATE support_tickets SET status = ?, updated_at = ?, assigned_to = COALESCE(assigned_to, ?), "
                             "first_answer_at = CASE WHEN first_answer_at = '' THEN ? ELSE first_answer_at END, "
                             "closed_at = ? WHERE id = ?", (status, when, user_id, when if body else "",
                                                             when if close else "", t["id"]))
                self.notify(conn, t["user_id"], "Answer to your question {0}".replace("{0}", t["number"]),
                            body[:200] or "Your question is closed.", url=f"/support/{t['number']}")
            else:
                status = "Closed" if close else "Open"
                conn.execute("UPDATE support_tickets SET status = ?, updated_at = ?, closed_at = ? WHERE id = ?",
                             (status, when, when if close else "", t["id"]))

    def take_support_ticket(self, user_id, number):
        if not self.can(user_id, "support.manage"):
            raise ShopError("You do not have the ability to do this. Ask your manager.")
        with self.db() as conn:
            if not conn.execute("UPDATE support_tickets SET assigned_to = ? WHERE number = ? AND status != 'Closed'",
                                (user_id, str(number).upper())).rowcount:
                raise ShopError("Question not found.")

    # ================================================================ claims
    def claim_cover(self, s):
        return s["declared"] if s["insured"] else min(s["declared"] or UNINSURED_LIMIT, UNINSURED_LIMIT)

    def file_claim(self, user_id, number, kind, details, amount, at=None):
        if kind not in CLAIM_KINDS:
            raise ShopError("Please choose: lost or damaged.")
        details = str(details or "").strip()[:2000]
        if len(details) < 10:
            raise ShopError("Please tell us what happened (at least a sentence).")
        try:
            amount = int(round(float(str(amount).replace(",", "")) * 100))
        except (TypeError, ValueError):
            raise ShopError("Please write the amount you claim as a number.")
        t = at or datetime.now()
        with self.db() as conn:
            s = conn.execute("SELECT * FROM shipments WHERE number = ? AND user_id = ?",
                             (str(number).upper(), user_id)).fetchone()
            if s is None:
                raise ShopError("Only the sender can claim for a parcel.")
            if kind == "damaged":
                if s["status"] != C.DELIVERED or not s["delivered_at"]:
                    raise ShopError("A damaged parcel can be claimed after it is delivered.")
                if t - to_dt(s["delivered_at"]) > timedelta(days=DAMAGE_DAYS):
                    raise ShopError(f"Damage must be reported within {DAMAGE_DAYS} days of the delivery.")
            else:
                if s["status"] in (C.DELIVERED, C.CANCELLED, C.RETURNED):
                    raise ShopError("This parcel is not lost.")
                if t < to_dt(s["expected"]) + timedelta(days=LOST_AFTER_DAYS):
                    raise ShopError(f"A parcel counts as lost {LOST_AFTER_DAYS} days after the expected day.")
            cover = self.claim_cover(s)
            if amount <= 0 or amount > cover:
                raise ShopError("You can claim at most {0} for this parcel.".replace("{0}", f"Rs {cover / 100:,.2f}"))
            if conn.execute("SELECT 1 FROM parcel_claims WHERE shipment_id = ? AND status IN ('Waiting', 'Paid')",
                            (s["id"],)).fetchone():
                raise ShopError("There is a claim for this parcel already.")
            cur = conn.execute("INSERT INTO parcel_claims (shipment_id, user_id, kind, details, amount, cover, created_at) "
                               "VALUES (?, ?, ?, ?, ?, ?, ?)", (s["id"], user_id, kind, details, amount, cover, now()))
        return cur.lastrowid

    def claims(self, status="", user_id=None):
        where, args = [], []
        if status:
            where.append("c.status = ?")
            args.append(status)
        if user_id:
            where.append("c.user_id = ?")
            args.append(user_id)
        with self.db() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT c.*, s.number, s.insured, s.declared, s.status AS parcel_status, u.name FROM parcel_claims c "
                "JOIN shipments s ON s.id = c.shipment_id JOIN users u ON u.id = c.user_id"
                + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY c.id DESC LIMIT 200", args)]

    def decide_claim(self, actor_id, claim_id, approve, payout=None, note=""):
        if not self.can(actor_id, "support.manage"):
            raise ShopError("You do not have the ability to do this. Ask your manager.")
        with self.db() as conn:
            c = conn.execute("SELECT c.*, s.number FROM parcel_claims c JOIN shipments s ON s.id = c.shipment_id "
                             "WHERE c.id = ?", (claim_id,)).fetchone()
            if c is None:
                raise ShopError("Claim not found.")
            if c["user_id"] == actor_id:
                raise ShopError("Nobody can decide his own claim.")
            if not approve:
                if not conn.execute("UPDATE parcel_claims SET status = 'Refused', decided_by = ?, decided_at = ?, note = ? "
                                    "WHERE id = ? AND status = 'Waiting'", (actor_id, now(), _clean(note, 200),
                                                                            claim_id)).rowcount:
                    raise ShopError("This claim was decided already.")
                self.notify(conn, c["user_id"], "Claim refused: {0}".replace("{0}", c["number"]), _clean(note, 200),
                            url=f"/courier/s/{c['number']}")
                return 0
            pay = c["amount"] if payout in (None, "") else int(round(float(payout) * 100))
            if not 0 < pay <= c["cover"]:
                raise ShopError("The payout must be more than 0 and at most the cover.")
            if not conn.execute("UPDATE parcel_claims SET status = 'Paid', payout = ?, decided_by = ?, decided_at = ?, "
                                "note = ? WHERE id = ? AND status = 'Waiting'",
                                (pay, actor_id, now(), _clean(note, 200), claim_id)).rowcount:
                raise ShopError("This claim was decided already.")
            self.wallet_move(conn, c["user_id"], pay, "claim", c["number"], f"Claim for parcel {c['number']}")
            self.notify(conn, c["user_id"], "Claim paid: {0}".replace("{0}", c["number"]),
                        "The money is in your Yo Pay wallet.", url="/pay")
            return pay

    # ================================================================ risk check of orders
    def risk_score(self, conn, order):
        reasons = []
        u = conn.execute("SELECT * FROM users WHERE id = ?", (order["user_id"],)).fetchone()
        placed = to_dt(order["placed_at"])
        if u and placed - to_dt(u["created_at"]) < timedelta(days=1):
            reasons.append((25, "new account"))
        if order["total"] >= 2000000:
            reasons.append((20, "big amount"))
        hour_ago = (placed - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        n = conn.execute("SELECT COUNT(*) FROM orders WHERE user_id = ? AND placed_at >= ? AND id != ?",
                         (order["user_id"], hour_ago, order["id"])).fetchone()[0]
        if n >= 3:
            reasons.append((25, f"{n + 1} orders in an hour"))
        if order["payment_method"] in ("COD", "Cash on Delivery", "cod") and order["total"] >= 1000000:
            reasons.append((15, "big cash on delivery"))
        day_ago = (placed - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        fails = conn.execute("SELECT COUNT(*) FROM orders WHERE user_id = ? AND payment_status = 'Failed' AND placed_at >= ?",
                             (order["user_id"], day_ago)).fetchone()[0]
        if fails >= 3:
            reasons.append((30, "failed payments"))
        if order["ship_mobile"]:
            others = conn.execute("SELECT COUNT(DISTINCT user_id) FROM orders WHERE ship_mobile = ? AND user_id != ?",
                                  (order["ship_mobile"], order["user_id"])).fetchone()[0]
            if others >= 3:
                reasons.append((20, "the same phone on many accounts"))
        return sum(p for p, _ in reasons), [r for _, r in reasons]

    def after_order_placed(self, number):
        with self.db() as conn:
            o = conn.execute("SELECT * FROM orders WHERE number = ?", (number,)).fetchone()
            if o is not None:
                score, why = self.risk_score(conn, dict(o))
                status = "Review" if score >= RISK_HOLD else ""
                conn.execute("UPDATE orders SET risk_score = ?, risk_reasons = ?, risk_status = ? WHERE id = ?",
                             (score, ", ".join(why), status, o["id"]))

    def risk_queue(self):
        with self.db() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT o.*, u.name, u.email, u.created_at AS user_since FROM orders o JOIN users u ON u.id = o.user_id "
                "WHERE o.risk_status = 'Review' ORDER BY o.id")]

    def decide_risk(self, actor_id, number, release):
        if not self.can(actor_id, "risk.review"):
            raise ShopError("You do not have the ability to do this. Ask your manager.")
        with self.db() as conn:
            o = conn.execute("SELECT * FROM orders WHERE number = ? AND risk_status = 'Review'", (number,)).fetchone()
            if o is None:
                raise ShopError("This order is not waiting for a check.")
            if release:
                conn.execute("UPDATE orders SET risk_status = 'Released', risk_released_at = ? WHERE id = ? "
                             "AND risk_status = 'Review'", (now(), o["id"]))
        if not release:
            self.cancel_order(o["user_id"], number)
            with self.db() as conn:
                conn.execute("UPDATE orders SET risk_status = 'Cancelled' WHERE id = ?", (o["id"],))
        self.log(actor_id, "Safety check of an order", f"{number}: {'released' if release else 'cancelled'}")
