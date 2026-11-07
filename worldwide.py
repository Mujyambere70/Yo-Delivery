"""
worldwide.py - version 5 (mixin class used by Store): road routes with a cache, product texts in several
languages, "my data" (download everything about me / delete my account), a limit on wrong passwords and
automatic payouts to sellers (to their Yo Pay wallet).

Road routes
    The road distance and the line of the road come from OSRM (the routing engine of OpenStreetMap, free demo
    server). Each answer is kept in the table route_cache (and in memory), so the same route is asked only once.
    Without internet (or with YO_ROUTING=0) the straight line x 1.3 is used, so the shop always works.
"""

import json
import threading
from datetime import datetime, timedelta

import geo
from core import ShopError, now

WORLD_SCHEMA = """
CREATE TABLE IF NOT EXISTS route_cache (
    a TEXT NOT NULL, b TEXT NOT NULL, km REAL NOT NULL, line TEXT NOT NULL, source TEXT NOT NULL,
    created_at TEXT NOT NULL, PRIMARY KEY (a, b)
);
CREATE TABLE IF NOT EXISTS login_failures (
    id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL, at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_login_failures_key ON login_failures(key);
CREATE TABLE IF NOT EXISTS product_texts (
    product_id INTEGER NOT NULL, lang TEXT NOT NULL, name TEXT NOT NULL, about TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL, PRIMARY KEY (product_id, lang)
);
"""

LOGIN_WINDOW_MIN = 15
LOGIN_MAX = {"acct": 5, "ip": 30}           # wrong passwords per 15 minutes: per account, per internet address
PAYOUT_MIN = 100 * 100                       # automatic payouts from ₹100
# my data: table -> column with the user id (what "download my data" contains)
MY_TABLES = {"addresses": "user_id", "orders": "user_id", "returns": "user_id", "reviews": "user_id",
             "wishlist": "user_id", "cart": "user_id", "wallet_txns": "user_id", "notifications": "user_id",
             "shipments": "user_id", "ticket_bookings": "user_id", "points_ledger": "user_id",
             "quiz_attempts": "user_id", "user_coupons": "user_id", "couriers": "user_id", "sellers": "user_id",
             "chat_messages": "sender_id", "activity": "user_id", "views": "user_id"}
SECRET_COLUMNS = {"password", "totp_secret", "totp_last_step", "token", "code_hash", "delivery_code", "endpoint",
                  "p256dh", "auth"}

_memory = {}
_memory_lock = threading.Lock()
MEMORY_ROUTES = 3000


def _key(p):
    return f"{float(p[0]):.4f},{float(p[1]):.4f}"


class WorldMixin:

    # ------------------------------------------------------------------ wrong passwords

    def login_allowed(self, login, ip=""):
        """Raise when there were too many wrong passwords for this account or address in the last 15 minutes."""
        since = (datetime.now() - timedelta(minutes=LOGIN_WINDOW_MIN)).strftime("%Y-%m-%d %H:%M:%S")
        with self.db() as conn:
            for kind, key in (("acct", str(login or "").strip().lower()[:120]), ("ip", str(ip or "")[:64])):
                if key and conn.execute("SELECT COUNT(*) FROM login_failures WHERE key = ? AND at >= ?",
                                        (f"{kind}:{key}", since)).fetchone()[0] >= LOGIN_MAX[kind]:
                    raise ShopError(f"Too many wrong passwords. Please wait {LOGIN_WINDOW_MIN} minutes or reset "
                                    f"your password.")

    def login_failed(self, login, ip=""):
        with self.db() as conn:
            for kind, key in (("acct", str(login or "").strip().lower()[:120]), ("ip", str(ip or "")[:64])):
                if key:
                    conn.execute("INSERT INTO login_failures (key, at) VALUES (?, ?)", (f"{kind}:{key}", now()))
            conn.execute("DELETE FROM login_failures WHERE at < ?",
                         ((datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),))

    def login_ok(self, login):
        with self.db() as conn:
            conn.execute("DELETE FROM login_failures WHERE key = ?", ("acct:" + str(login or "").strip().lower()[:120],))

    # ------------------------------------------------------------------ my data (privacy)

    def export_my_data(self, user_id):
        """Everything Yo Delivery keeps about one customer, as JSON-ready data (no passwords or secret keys)."""
        def clean(row):
            return {k: v for k, v in dict(row).items() if k not in SECRET_COLUMNS}
        with self.db() as conn:
            u = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if u is None:
                raise ShopError("Account not found.")
            data = {"app": "Yo Delivery", "exported_at": now(), "account": clean(u), "tables": {}}
            for table, col in MY_TABLES.items():
                try:
                    data["tables"][table] = [clean(r) for r in conn.execute(
                        f"SELECT * FROM {table} WHERE {col} = ? ORDER BY 1", (user_id,))]
                except Exception:
                    data["tables"][table] = []
            orders = [o["id"] for o in data["tables"]["orders"]]
            if orders:
                data["tables"]["order_items"] = [clean(r) for r in conn.execute(
                    f"SELECT * FROM order_items WHERE order_id IN ({', '.join('?' * len(orders))})", orders)]
            tickets = [b["id"] for b in data["tables"]["ticket_bookings"]]
            if tickets:
                data["tables"]["ticket_passengers"] = [clean(r) for r in conn.execute(
                    f"SELECT * FROM ticket_passengers WHERE booking_id IN ({', '.join('?' * len(tickets))})", tickets)]
        return data

    def delete_my_account(self, user_id, password):
        """The customer deletes the account: personal data is removed; orders, tickets and payments stay without
        the name (the law asks shops to keep invoices). Refused while money or deliveries are still open."""
        from werkzeug.security import check_password_hash
        with self.db() as conn:
            u = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if u is None:
                raise ShopError("Account not found.")
            if not check_password_hash(u["password"], str(password or "")):
                raise ShopError("The password is not correct.")
            if u["is_admin"]:
                raise ShopError("An admin account cannot be deleted here.")
            if u["wallet"] > 0:
                raise ShopError("Your Yo Pay balance is not zero. Please use or send the money first.")
            open_orders = [o for o in conn.execute("SELECT * FROM orders WHERE user_id = ? AND cancelled_at = ''",
                                                   (user_id,)).fetchall()
                           if self.status(dict(o)) not in ("Delivered", "Cancelled")]
            if open_orders:
                raise ShopError("You have orders on the way. Please wait until they are delivered.")
            if conn.execute("SELECT 1 FROM shipments WHERE user_id = ? AND status NOT IN ('Delivered', 'Cancelled')",
                            (user_id,)).fetchone():
                raise ShopError("You have parcels on the way. Please wait until they are delivered.")
            if conn.execute("SELECT 1 FROM ticket_bookings WHERE user_id = ? AND status IN ('Held', 'Confirmed', "
                            "'Waitlisted')", (user_id,)).fetchone():
                raise ShopError("You have tickets for trips still to come. Please cancel them or travel first.")
            gone = f"deleted-{user_id}"
            conn.execute("UPDATE users SET name = 'Deleted user', email = ?, mobile = ?, password = '!', blocked = 1, "
                         "totp_secret = '', totp_enabled = 0, notify_email = 0, notify_sms = 0 WHERE id = ?",
                         (f"{gone}@deleted.example", gone, user_id))
            for table in ("addresses", "cart", "wishlist", "notifications", "push_subscriptions", "password_resets",
                          "user_coupons", "login_failures"):
                try:
                    if table == "login_failures":
                        conn.execute("DELETE FROM login_failures WHERE key IN (?, ?)",
                                     (f"acct:{u['email'].lower()}", f"acct:{u['mobile']}"))
                    else:
                        conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
                except Exception:
                    pass
            conn.execute("UPDATE views SET user_id = NULL WHERE user_id = ?", (user_id,))
            conn.execute("UPDATE reviews SET author = 'A customer', user_id = NULL WHERE user_id = ?", (user_id,))
            conn.execute("UPDATE chat_messages SET body = '[deleted]' WHERE sender_id = ?", (user_id,))
            conn.execute("UPDATE shipments SET from_name = 'Deleted', from_mobile = '', from_address = '[deleted]', "
                         "to_name = 'Deleted', to_mobile = '', to_address = '[deleted]' WHERE user_id = ?", (user_id,))
            conn.execute("UPDATE ticket_passengers SET name = 'Deleted', age = 0, gender = '' WHERE booking_id IN "
                         "(SELECT id FROM ticket_bookings WHERE user_id = ?)", (user_id,))
            conn.execute("UPDATE ticket_bookings SET contact_mobile = '' WHERE user_id = ?", (user_id,))
            conn.execute("UPDATE couriers SET status = 'Closed', online = 0 WHERE user_id = ?", (user_id,))
            try:
                conn.execute("DELETE FROM rec_factors WHERE kind = 'u' AND key = ?", (str(user_id),))
            except Exception:
                pass
        return True

    # ------------------------------------------------------------------ automatic payouts to sellers

    def auto_payouts(self, day=None):
        """Once a day: every approved seller who did not switch it off gets the balance of delivered sales in
        Yo Pay (from ₹100). Only one server process does it per day. Returns {seller id: amount}."""
        day = day or now()[:10]
        with self.db() as conn:
            claimed = conn.execute("INSERT INTO settings (key, value) VALUES ('last_payout_day', ?) "
                                   "ON CONFLICT (key) DO UPDATE SET value = excluded.value "
                                   "WHERE settings.value <> excluded.value", (day,)).rowcount
            if not claimed:
                return {}
            sellers = conn.execute("SELECT id, user_id, shop_name, payout_to FROM sellers WHERE status = 'Approved' "
                                   "AND auto_payout = 1").fetchall()
        paid = {}
        for s in sellers:
            amount = self.seller_balance(s["id"])["balance"]
            if amount < PAYOUT_MIN:
                continue
            with self.db() as conn:
                pid = conn.execute("INSERT INTO payouts (seller_id, amount, note, paid_by, created_at) "
                                   "VALUES (?, ?, ?, 'auto', ?)", (s["id"], amount, "Automatic payout to Yo Pay",
                                                                   now())).lastrowid
                to_bank = s["payout_to"] == "bank" and self.bank_transfer(conn, s["id"], pid, amount)   # version 6
                if not to_bank:
                    self.wallet_move(conn, s["user_id"], amount, "payout", f"P{pid}", f"Payout for {s['shop_name']}")
                import world
                self.notify(conn, s["user_id"], "Payout sent",
                            "We sent you {0} for your sales on Yo Delivery.".replace(
                                "{0}", world.format_money(amount, "INR"))
                            + (" (bank transfer, test mode)" if to_bank else " (Yo Pay)"),
                            url="/seller/bank" if to_bank else "/pay")
            paid[s["id"]] = amount
        return paid

    def set_auto_payout(self, seller_id, on):
        with self.db() as conn:
            conn.execute("UPDATE sellers SET auto_payout = ? WHERE id = ?", (1 if on else 0, seller_id))

    # ------------------------------------------------------------------ road routes

    def road_route(self, a, b, conn=None, fetch=True):
        """{"km", "line": [[lat, lng], ...], "source": "osrm" | "straight"} between two points.
        conn: use the caller's transaction (no second connection). fetch=False: only the cache, no internet
        (for code that runs inside a transaction - it must never wait for the network)."""
        a, b = (float(a[0]), float(a[1])), (float(b[0]), float(b[1]))
        if geo.haversine_km(a, b) < 0.05:
            return {"km": 0.0, "line": [list(a), list(b)], "source": "straight"}
        ka, kb = _key(a), _key(b)
        hit = _memory.get((ka, kb))
        if hit:
            return hit
        row = None
        try:
            if conn is not None:
                row = conn.execute("SELECT km, line, source FROM route_cache WHERE a = ? AND b = ?", (ka, kb)).fetchone()
            else:
                with self.db() as c:
                    row = c.execute("SELECT km, line, source FROM route_cache WHERE a = ? AND b = ?", (ka, kb)).fetchone()
        except Exception:
            row = None
        if row:
            out = {"km": row["km"], "line": json.loads(row["line"]), "source": row["source"]}
            self._remember(ka, kb, out)
            return out
        if str(getattr(self, "env", {}).get("YO_ROUTING", "1")) == "0":
            fetch = False
        r = geo.osrm_route(a, b) if fetch else None
        if r is None:                                # no answer: straight line (not kept, internet may come back)
            return {"km": geo.straight_road_km(a, b), "line": [list(a), list(b)], "source": "straight"}
        out = {"km": r[0], "line": r[1], "source": "osrm"}
        self._remember(ka, kb, out)
        sql = ("INSERT INTO route_cache (a, b, km, line, source, created_at) VALUES (?, ?, ?, ?, ?, ?) "
               "ON CONFLICT (a, b) DO NOTHING")
        args = (ka, kb, out["km"], json.dumps(out["line"]), "osrm", now())
        try:
            if conn is not None:
                conn.execute(sql, args)
            else:
                with self.db() as c:
                    c.execute(sql, args)
        except Exception:                            # the cache is only a help
            pass
        return out

    @staticmethod
    def _remember(ka, kb, out):
        with _memory_lock:
            if len(_memory) >= MEMORY_ROUTES:
                _memory.clear()
            _memory[(ka, kb)] = out

    def route_cache_size(self):
        with self.db() as conn:
            return conn.execute("SELECT COUNT(*) FROM route_cache").fetchone()[0]

    # ------------------------------------------------------------------ product texts in other languages

    def product_texts(self, product_id):
        """{lang: {"name", "about"}} written by the seller (the main text stays in products)."""
        with self.db() as conn:
            return {r["lang"]: {"name": r["name"], "about": r["about"]} for r in conn.execute(
                "SELECT lang, name, about FROM product_texts WHERE product_id = ?", (product_id,))}

    def save_product_texts(self, product_id, texts, seller_id=None):
        """texts: {lang: (name, about)}. Empty name = remove that language. seller_id: only their own product."""
        import world
        with self.db() as conn:
            p = conn.execute("SELECT id, seller_id FROM products WHERE id = ?", (product_id,)).fetchone()
            if p is None or (seller_id is not None and p["seller_id"] != seller_id):
                raise ShopError("Product not found.")
            for lang, (name, about) in texts.items():
                if lang not in world.LANGUAGES or lang == "en":
                    continue
                name, about = " ".join(str(name or "").split())[:120], str(about or "").strip()[:4000]
                if not name:
                    conn.execute("DELETE FROM product_texts WHERE product_id = ? AND lang = ?", (product_id, lang))
                    continue
                if len(name) < 3:
                    raise ShopError("The product name must have at least 3 letters.")
                conn.execute("INSERT INTO product_texts (product_id, lang, name, about, updated_at) VALUES (?, ?, ?, ?, ?) "
                             "ON CONFLICT (product_id, lang) DO UPDATE SET name = excluded.name, about = excluded.about, "
                             "updated_at = excluded.updated_at", (product_id, lang, name, about, now()))

    def localize_products(self, products, lang=None):
        """The seller's text in the visitor's language on product objects (in place; English stays the default).
        Used by the pages only - orders, seller and admin forms keep the original text."""
        import world
        lang = lang or world.current_lang()
        if lang == "en" or not products:
            return products
        ids = [p.id for p in products]
        with self.db() as conn:
            texts = {r["product_id"]: r for r in conn.execute(
                f"SELECT product_id, name, about FROM product_texts WHERE lang = ? AND product_id IN "
                f"({', '.join('?' * len(ids))})", [lang] + ids)}
        for p in products:
            t = texts.get(p.id)
            if t:
                p.name_original = p.name
                p.name = t["name"]
                lines = [x.strip(" -•\t") for x in t["about"].splitlines() if x.strip(" -•\t")]
                if lines:
                    p.about = lines
                p.translated = lang
        return products
