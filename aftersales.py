"""
aftersales.py - returns and refunds (mixin class used by Store).

A customer can ask to return a delivered order within `return_days` days (admin setting, default 7).
The admin approves (the stock comes back and the money is refunded) or rejects with a reason.

Refund to:  card orders -> the same card (Stripe test mode or the test gateway)
            wallet, UPI and Cash on Delivery orders -> the Yo Pay wallet
The amount is what the customer paid for the items (the order total without the delivery charge).
"""

from datetime import timedelta

import world
from core import ShopError, now, to_dt

RETURN_REASONS = ["Damaged or broken", "Wrong item received", "Item does not match the description",
                  "Size or fit is not right", "No longer needed", "Other"]


class ReturnsMixin:

    def delivered_at(self, o):
        for name, when, done in self.tracking(o):
            if name == "Delivered" and done and when:
                return when
        return ""

    def return_window(self, o):
        """(can the customer ask for a return now?, last day)"""
        when = self.delivered_at(o)
        if not when or o["cancelled_at"]:
            return False, ""
        last = to_dt(when) + timedelta(days=self.setting("return_days"))
        return now() <= last.strftime("%Y-%m-%d %H:%M:%S"), last.strftime("%Y-%m-%d %H:%M:%S")

    def order_return(self, order_id):
        with self.db() as conn:
            r = conn.execute("SELECT * FROM returns WHERE order_id = ? ORDER BY id DESC LIMIT 1", (order_id,)).fetchone()
        return dict(r) if r else None

    def request_return(self, user_id, number, reason, details=""):
        o = self.get_order(user_id, number)
        if reason not in RETURN_REASONS:
            raise ShopError("Please choose the reason for the return.")
        details = " ".join(str(details or "").split())[:500]
        if reason == "Other" and len(details) < 10:
            raise ShopError("Please tell us a little more (at least 10 characters).")
        ok, last = self.return_window(o)
        if not ok:
            raise ShopError("This order can no longer be returned." if last else
                            "You can ask for a return after the order is delivered.")
        amount = o["total"] - o["delivery_fee"] - o.get("refunded", 0)
        with self.db() as conn:
            if conn.execute("SELECT 1 FROM returns WHERE order_id = ? AND status IN ('Requested', 'Refunded')",
                            (o["id"],)).fetchone():
                raise ShopError("A return was already requested for this order.")
            conn.execute("INSERT INTO returns (order_id, user_id, reason, details, amount, refund_to, created_at) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (o["id"], user_id, reason, details, amount,
                          "Card" if o["payment_method"] == "Card" else "Yo Pay Wallet", now()))
            self.notify(conn, user_id, "Return requested for order {0}".replace("{0}", number),
                        "We received your return request. A courier will pick up the parcel; the refund follows "
                        "after our check.", order_number=number, url=f"/orders/{number}")
        return amount

    def returns(self, status=""):
        sql = ("SELECT r.*, o.number, o.payment_method, o.currency, o.total, u.name AS customer, u.email "
               "FROM returns r JOIN orders o ON o.id = r.order_id JOIN users u ON u.id = r.user_id")
        args = []
        if status:
            sql += " WHERE r.status = ?"
            args.append(status)
        with self.db() as conn:
            return [dict(r) for r in conn.execute(sql + " ORDER BY r.id DESC", args)]

    def decide_return(self, return_id, approve, by, note=""):
        note = " ".join(str(note or "").split())[:200]
        if not approve and len(note) < 3:
            raise ShopError("Please write the reason for rejecting the return (the customer sees it).")
        with self.db() as conn:
            r = conn.execute("SELECT * FROM returns WHERE id = ?", (return_id,)).fetchone()
            if r is None:
                raise ShopError("Return not found.")
            r = dict(r)
            if conn.execute("UPDATE returns SET status = ?, note = ?, decided_at = ?, decided_by = ? "
                            "WHERE id = ? AND status = 'Requested'",
                            ("Refunded" if approve else "Rejected", note, now(), by, return_id)).rowcount == 0:
                raise ShopError("This return was already handled.")
            o = dict(conn.execute("SELECT * FROM orders WHERE id = ?", (r["order_id"],)).fetchone())
            number = o["number"]
            if approve:
                for it in conn.execute("SELECT product_id, qty FROM order_items WHERE order_id = ?", (o["id"],)).fetchall():
                    conn.execute("UPDATE products SET stock = stock + ? WHERE id = ?", (it[1], it[0]))
                if r["refund_to"] != "Card":
                    conn.execute("UPDATE users SET wallet = wallet + ? WHERE id = ?", (r["amount"], r["user_id"]))
                    conn.execute("UPDATE orders SET refunded = refunded + ? WHERE id = ?", (r["amount"], o["id"]))
                shown = world.format_money(world.convert(r["amount"], o["currency"], {o["currency"]: o["fx_rate"],
                                                                                     "INR": 1.0})
                                           if o["currency"] != "INR" else r["amount"], o["currency"])
                self.notify(conn, r["user_id"], "Refund for order {0}".replace("{0}", number),
                            ("Your return was accepted. {0} was refunded to your {1}.")
                            .replace("{0}", shown).replace("{1}", "card" if r["refund_to"] == "Card" else "Yo Pay wallet"),
                            order_number=number, url=f"/orders/{number}")
            else:
                self.notify(conn, r["user_id"], "Return not accepted for order {0}".replace("{0}", number),
                            "Sorry, your return was not accepted. Reason: {0}".replace("{0}", note),
                            order_number=number, url=f"/orders/{number}")
        if approve and r["refund_to"] == "Card":
            self.refund_card(o, r["amount"], "Return " + r["reason"])
        return r
