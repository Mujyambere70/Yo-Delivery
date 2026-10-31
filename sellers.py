"""
sellers.py - multi-seller marketplace (mixin class used by Store).

  * Any customer can apply to sell ("Sell on Yo Delivery"). The admin approves or rejects the shop.
  * An approved seller adds and edits only their own products. A new or changed product of a seller
    waits for the admin's check before it appears in the shop (approved = 0).
  * Orders are delivered by Yo Delivery (like "fulfilled by the marketplace"). For every item the
    seller earns the price minus the shop's commission (fixed on the order item when it was sold).
  * Earnings count only delivered items of orders that were not cancelled or returned. The admin
    records payouts; the seller sees the balance still to be paid.
"""

import re

import world
from core import ShopError, now

SELLER_STATUSES = ["Pending", "Approved", "Rejected", "Suspended"]


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "shop"


class SellersMixin:

    # ===== shops =====

    def apply_seller(self, user_id, f):
        name = " ".join(str(f.get("shop_name", "")).split())[:60]
        about = " ".join(str(f.get("about", "")).split())[:400]
        country = str(f.get("country", "IN")).upper()
        city = " ".join(str(f.get("city", "")).split())[:60]
        payout = " ".join(str(f.get("payout_details", "")).split())[:120]
        if len(name) < 3:
            raise ShopError("Please enter the name of your shop (at least 3 characters).")
        if len(about) < 20:
            raise ShopError("Please describe what you sell (at least 20 characters).")
        if country not in world.DIAL_CODES or not city:
            raise ShopError("Please choose the country and city your products are sent from.")
        if len(payout) < 6:
            raise ShopError("Please enter where we should pay your earnings (UPI ID, IBAN or account number).")
        with self.db() as conn:
            if conn.execute("SELECT 1 FROM sellers WHERE user_id = ?", (user_id,)).fetchone():
                raise ShopError("You have already applied to sell on Yo Delivery.")
            if conn.execute("SELECT 1 FROM sellers WHERE lower(shop_name) = ?", (name.lower(),)).fetchone():
                raise ShopError("A shop with this name already exists. Please choose another name.")
            slug, n = _slug(name), 2
            base = slug
            while conn.execute("SELECT 1 FROM sellers WHERE slug = ?", (slug,)).fetchone():
                slug, n = f"{base}-{n}", n + 1
            cur = conn.execute("INSERT INTO sellers (user_id, shop_name, slug, about, country, city, payout_details, "
                               "commission, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                               (user_id, name, slug, about, country, city, payout,
                                float(self.setting("seller_commission")), now()))
            return cur.lastrowid

    def seller_of(self, user_id):
        with self.db() as conn:
            r = conn.execute("SELECT * FROM sellers WHERE user_id = ?", (user_id,)).fetchone()
        return dict(r) if r else None

    def seller(self, seller_id=None, slug=None):
        with self.db() as conn:
            r = conn.execute("SELECT * FROM sellers WHERE id = ? OR slug = ?", (seller_id, slug)).fetchone()
        if r is None:
            raise ShopError("Shop not found.")
        return dict(r)

    def sellers(self, status=""):
        sql = ("SELECT s.*, u.name AS owner, u.email, "
               "(SELECT COUNT(*) FROM products p WHERE p.seller_id = s.id) AS products, "
               "(SELECT COUNT(*) FROM products p WHERE p.seller_id = s.id AND p.approved = 0) AS waiting "
               "FROM sellers s JOIN users u ON u.id = s.user_id")
        args = []
        if status:
            sql += " WHERE s.status = ?"
            args.append(status)
        with self.db() as conn:
            rows = [dict(r) for r in conn.execute(sql + " ORDER BY s.created_at DESC", args)]
        for r in rows:
            r.update(self.seller_balance(r["id"]))
        return rows

    def shop_names(self):
        """{seller_id: (name, slug)} of approved shops - for "Sold by" on product pages."""
        with self.db() as conn:
            return {r["id"]: (r["shop_name"], r["slug"]) for r in conn.execute(
                "SELECT id, shop_name, slug FROM sellers WHERE status = 'Approved'")}

    def decide_seller(self, seller_id, status, reason="", commission=None):
        if status not in SELLER_STATUSES[1:]:
            raise ShopError("Choose Approved, Rejected or Suspended.")
        reason = " ".join(str(reason or "").split())[:200]
        if status != "Approved" and len(reason) < 3:
            raise ShopError("Please write the reason (the seller sees it).")
        s = self.seller(seller_id)
        with self.db() as conn:
            if commission not in (None, ""):
                try:
                    commission = float(commission)
                except ValueError:
                    raise ShopError("Commission must be a number.")
                if not 0 <= commission <= 50:
                    raise ShopError("Commission must be between 0 and 50 %.")
                conn.execute("UPDATE sellers SET commission = ? WHERE id = ?", (commission, seller_id))
            conn.execute("UPDATE sellers SET status = ?, reason = ?, decided_at = ? WHERE id = ?",
                         (status, reason if status != "Approved" else "", now(), seller_id))
            # a suspended or rejected shop disappears from the shop at once
            conn.execute("UPDATE products SET active = ? WHERE seller_id = ?",
                         (1 if status == "Approved" else 0, seller_id))
            if status != "Approved":
                conn.execute("DELETE FROM cart WHERE product_id IN (SELECT id FROM products WHERE seller_id = ?)",
                             (seller_id,))
            subject = {"Approved": "Your shop {0} is open on Yo Delivery",
                       "Rejected": "Your seller application was not approved",
                       "Suspended": "Your shop {0} was suspended"}[status]
            body = {"Approved": "Welcome! You can now add products in your Seller Centre. Each product appears in the "
                                "shop after a quick check by our team.",
                    "Rejected": "Sorry, your application to sell on Yo Delivery was not approved. Reason: {1}",
                    "Suspended": "Your shop was suspended and its products are hidden. Reason: {1}"}[status]
            self.notify(conn, s["user_id"], subject.replace("{0}", s["shop_name"]),
                        body.replace("{1}", reason), url="/seller")
        return s

    def update_shop(self, seller_id, f):
        about = " ".join(str(f.get("about", "")).split())[:400]
        payout = " ".join(str(f.get("payout_details", "")).split())[:120]
        if len(about) < 20 or len(payout) < 6:
            raise ShopError("Please fill in the description (20+ characters) and the payout details.")
        with self.db() as conn:
            conn.execute("UPDATE sellers SET about = ?, payout_details = ? WHERE id = ?", (about, payout, seller_id))

    # ===== products of a seller =====

    def seller_products(self, seller_id):
        with self.db() as conn:
            rows = conn.execute("SELECT * FROM products WHERE seller_id = ? ORDER BY created_at DESC",
                                (seller_id,)).fetchall()
        from store import _product
        return [_product(r) for r in rows]

    def save_seller_product(self, seller_id, f, product_id=None):
        """A seller adds or edits one of their own products. It waits for the admin's approval."""
        s = self.seller(seller_id)
        if s["status"] != "Approved":
            raise ShopError("Your shop is not approved, so you cannot change products now.")
        if product_id:
            p = self.get_product(pid=product_id)
            if p.seller_id != seller_id:
                raise ShopError("This is not your product.")
        f = dict(f)
        slug = self.save_product(f, product_id)
        with self.db() as conn:
            conn.execute("UPDATE products SET seller_id = ?, approved = 0 WHERE slug = ?", (seller_id, slug))
        return slug

    def approve_product(self, product_id, approve=True):
        p = self.get_product(pid=product_id)
        with self.db() as conn:
            conn.execute("UPDATE products SET approved = ?, active = ? WHERE id = ?",
                         (1 if approve else 0, 1 if approve else 0, product_id))
            if p.seller_id:
                owner = conn.execute("SELECT user_id FROM sellers WHERE id = ?", (p.seller_id,)).fetchone()[0]
                self.notify(conn, owner,
                            ("Product approved: {0}" if approve else "Product not approved: {0}").replace("{0}", p.name[:60]),
                            "It is now visible in the shop." if approve else
                            "It was checked by our team and is not shown in the shop. Please edit it and try again.",
                            url="/seller/products")
        return p

    def products_waiting(self):
        with self.db() as conn:
            rows = conn.execute("SELECT * FROM products WHERE approved = 0 ORDER BY created_at").fetchall()
        from store import _product
        return [_product(r) for r in rows]

    # ===== sales, earnings and payouts =====

    def seller_sales(self, seller_id, limit=200):
        """Items of this seller in customers' orders (no customer contact details - privacy)."""
        with self.db() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT i.*, o.number, o.placed_at, o.cancelled_at, o.country, o.id AS oid, o.payment_status, "
                "o.paid_at FROM order_items i JOIN orders o ON o.id = i.order_id WHERE i.seller_id = ? "
                "ORDER BY o.placed_at DESC LIMIT ?", (seller_id, limit))]
            returned = {r[0] for r in conn.execute("SELECT order_id FROM returns WHERE status = 'Refunded'")}
        for r in rows:
            gross = r["price"] * r["qty"]
            r["gross"] = gross
            r["fee"] = round(gross * r["commission"] / 100)
            r["net"] = gross - r["fee"]
            o = {"id": r["oid"], "placed_at": r["placed_at"], "cancelled_at": r["cancelled_at"],
                 "payment_status": r["payment_status"], "paid_at": r["paid_at"]}
            r["status"] = "Returned" if r["oid"] in returned else self.status(o)
            r["earned"] = r["status"] == "Delivered"
        return rows

    def seller_balance(self, seller_id):
        sales = self.seller_sales(seller_id, limit=100000)
        earned = sum(r["net"] for r in sales if r["earned"])
        pending = sum(r["net"] for r in sales if r["status"] not in ("Delivered", "Cancelled", "Returned",
                                                                      "Awaiting payment"))
        with self.db() as conn:
            paid = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM payouts WHERE seller_id = ?",
                                (seller_id,)).fetchone()[0]
        units = sum(r["qty"] for r in sales if r["status"] not in ("Cancelled", "Returned", "Awaiting payment"))
        gross = sum(r["gross"] for r in sales if r["status"] not in ("Cancelled", "Returned", "Awaiting payment"))
        return {"earned": earned, "pending": pending, "paid": paid, "balance": earned - paid, "units": units,
                "gross": gross}

    def record_payout(self, seller_id, rupees_amount, note, by):
        try:
            amount = round(float(rupees_amount) * 100)
        except (TypeError, ValueError):
            raise ShopError("Enter the amount in rupees, e.g. 2500.")
        bal = self.seller_balance(seller_id)["balance"]
        if not 0 < amount <= bal:
            raise ShopError("The payout must be more than 0 and not more than the balance to pay.")
        s = self.seller(seller_id)
        with self.db() as conn:
            conn.execute("INSERT INTO payouts (seller_id, amount, note, paid_by, created_at) VALUES (?, ?, ?, ?, ?)",
                         (seller_id, amount, " ".join(str(note or "").split())[:120], by, now()))
            self.notify(conn, s["user_id"], "Payout sent",
                        "We sent you {0} for your sales on Yo Delivery.".replace(
                            "{0}", world.format_money(amount, "INR")), url="/seller")
        return amount

    def payouts(self, seller_id):
        with self.db() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM payouts WHERE seller_id = ? ORDER BY id DESC",
                                                  (seller_id,))]
