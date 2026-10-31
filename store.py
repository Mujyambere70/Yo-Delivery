"""
store.py - all the shop logic of Yo Delivery.

The data is kept in SQLite on a computer, or in PostgreSQL when DATABASE_URL is set (online),
through db.py. Free hosts like Render delete local files at every restart - with PostgreSQL
the shop keeps its orders, customers and uploaded pictures.

Main parts
    Users & addresses      register(), login(), add_address() ...
    Catalog                search(), get_product(), reviews ...
    Cart & wishlist        cart_set(), cart_items(), price_summary() ...
    Orders                 place_order(), tracking(), cancel_order() ...

Money is stored in paise (integer). ₹1 = 100 paise.
"""

import json
import os
import random
import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from werkzeug.security import check_password_hash, generate_password_hash

import catalog
import world
from aftersales import ReturnsMixin
from core import ShopError, now, to_dt          # noqa: F401  (ShopError, now and to_dt are used by app.py too)
from db import Database
from maintenance import MaintenanceMixin
from notices import NoticesMixin
from notify import Notifier
from payments import PaymentsMixin
from recommend import RecommendMixin
from sellers import SellersMixin

# Default shop settings (the admin can change them on the Settings page)
FREE_DELIVERY_FROM = 499 * 100     # free delivery for orders of ₹499 or more
DELIVERY_FEE = 40 * 100
SIGNUP_WALLET = 2000 * 100         # demo Yo Pay balance for new users
DEFAULT_SETTINGS = {"free_delivery_from": FREE_DELIVERY_FROM, "delivery_fee": DELIVERY_FEE,
                    "signup_wallet": SIGNUP_WALLET, "cod_limit": 50000 * 100, "announcement": "",
                    # shipping abroad (the shop's own prices, in paise)
                    "intl_fee_near": 900 * 100, "intl_fee_world": 1800 * 100, "intl_free_from": 25000 * 100,
                    # marketplace
                    "seller_commission": 10, "return_days": 7}
TEXT_SETTINGS = {"announcement"}
MAX_QTY = 10
CATEGORY_NAMES = {}                # slug -> name, kept up to date by Store

# Order tracking: minutes after the order for each step.
# FAST mode (default) is for demos: an order is delivered in about 8 minutes.
FAST_TRACKING = os.environ.get("YO_FAST_TRACKING", "1") != "0"
TRACK_STEPS = ["Order placed", "Packed", "Shipped", "Out for delivery", "Delivered"]
PAYMENT_WINDOW_MINUTES = 30        # an unpaid card order is cancelled after this time (stock comes back)
TRACK_MINUTES = [0, 1, 3, 5, 8] if FAST_TRACKING else [0, 6 * 60, 24 * 60, 46 * 60, 52 * 60]

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$")
MOBILE_RE = re.compile(r"^[6-9]\d{9}$")
PINCODE_RE = re.compile(r"^[1-9]\d{5}$")
UPI_RE = re.compile(r"^[a-zA-Z0-9.\-_]{2,}@[a-zA-Z]{2,}$")

STATES = ["Andhra Pradesh", "Assam", "Bihar", "Chandigarh", "Chhattisgarh", "Delhi", "Goa", "Gujarat",
          "Haryana", "Himachal Pradesh", "Jammu and Kashmir", "Jharkhand", "Karnataka", "Kerala",
          "Madhya Pradesh", "Maharashtra", "Odisha", "Punjab", "Rajasthan", "Tamil Nadu", "Telangana",
          "Uttar Pradesh", "Uttarakhand", "West Bengal"]




def rupees(paise):
    """149900 -> '₹1,499'  |  15050 -> '₹150.50'  (Indian grouping: ₹1,49,999)"""
    r, p = divmod(int(round(paise)), 100)
    s = str(r)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts) + "," + tail
    return f"₹{s}" + (f".{p:02d}" if p else "")


def delivery_days(pincode, country="IN"):
    """Rough delivery time from the Ambala warehouse (India by PIN code, other countries by zone)."""
    return world.delivery_days(country, pincode)


def delivery_date_text(pincode, start=None, country="IN"):
    days = delivery_days(pincode, country)
    d = (start or datetime.now()) + timedelta(days=days)
    text = world.format_when(d, with_time=False)
    return ("Tomorrow, " if days == 1 else "") + text


@dataclass
class Product:
    id: int
    slug: str
    name: str
    brand: str
    category: str
    price: int
    mrp: int
    rating_sum: float
    rating_count: int
    stock: int
    art: str
    about: list
    specs: dict
    tags: str
    created_at: str
    active: int = 1
    seller_id: int = None
    approved: int = 1

    @property
    def rating(self):
        return round(self.rating_sum / self.rating_count, 1) if self.rating_count else 0

    @property
    def discount(self):
        return round((self.mrp - self.price) * 100 / self.mrp) if self.mrp > self.price else 0

    @property
    def images(self):
        return [f"images/products/{self.slug}-{i}.jpg" for i in (1, 2, 3)]

    @property
    def image(self):
        return self.images[0]

    @property
    def category_name(self):
        return CATEGORY_NAMES.get(self.category, self.category.title())


def _product(row):
    d = dict(row)
    d["about"] = json.loads(d["about"])
    d["specs"] = json.loads(d["specs"])
    return Product(**d)


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL, email TEXT NOT NULL UNIQUE, mobile TEXT NOT NULL UNIQUE,
    password TEXT NOT NULL, wallet INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS addresses (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id),
    name TEXT NOT NULL, mobile TEXT NOT NULL, line1 TEXT NOT NULL, line2 TEXT NOT NULL DEFAULT '',
    city TEXT NOT NULL, state TEXT NOT NULL, pincode TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'Home',
    is_default INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT NOT NULL UNIQUE, name TEXT NOT NULL, brand TEXT NOT NULL,
    category TEXT NOT NULL, price INTEGER NOT NULL, mrp INTEGER NOT NULL, rating_sum REAL NOT NULL,
    rating_count INTEGER NOT NULL, stock INTEGER NOT NULL CHECK (stock >= 0), art TEXT NOT NULL,
    about TEXT NOT NULL, specs TEXT NOT NULL, tags TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cart (
    user_id INTEGER NOT NULL, product_id INTEGER NOT NULL, qty INTEGER NOT NULL,
    PRIMARY KEY (user_id, product_id)
);
CREATE TABLE IF NOT EXISTS wishlist (
    user_id INTEGER NOT NULL, product_id INTEGER NOT NULL, added_at TEXT NOT NULL,
    PRIMARY KEY (user_id, product_id)
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT, number TEXT NOT NULL UNIQUE, user_id INTEGER NOT NULL,
    ship_name TEXT NOT NULL, ship_mobile TEXT NOT NULL, ship_address TEXT NOT NULL, ship_pincode TEXT NOT NULL,
    mrp_total INTEGER NOT NULL, items_total INTEGER NOT NULL, coupon TEXT NOT NULL DEFAULT '',
    coupon_discount INTEGER NOT NULL DEFAULT 0, delivery_fee INTEGER NOT NULL, total INTEGER NOT NULL,
    payment_method TEXT NOT NULL, payment_note TEXT NOT NULL DEFAULT '',
    placed_at TEXT NOT NULL, expected TEXT NOT NULL, cancelled_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL REFERENCES orders(id),
    product_id INTEGER NOT NULL, slug TEXT NOT NULL, name TEXT NOT NULL,
    price INTEGER NOT NULL, mrp INTEGER NOT NULL, qty INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER NOT NULL, user_id INTEGER,
    author TEXT NOT NULL, rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    title TEXT NOT NULL, body TEXT NOT NULL, verified INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_products_cat ON products(category);
CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id);
CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, user_id INTEGER, user_name TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL, details TEXT NOT NULL DEFAULT '', ip TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_activity_at ON activity(at);
CREATE TABLE IF NOT EXISTS categories (
    slug TEXT PRIMARY KEY, name TEXT NOT NULL, image TEXT NOT NULL DEFAULT '', position INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS coupons (
    code TEXT PRIMARY KEY, description TEXT NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('flat', 'percent')),
    amount INTEGER NOT NULL, max_off INTEGER NOT NULL, min_order INTEGER NOT NULL,
    first_order_only INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS media (
    name TEXT PRIMARY KEY, mime TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS order_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL REFERENCES orders(id),
    step INTEGER NOT NULL, at TEXT NOT NULL, note TEXT NOT NULL DEFAULT ''
);
-- marketplace: other shops sell on Yo Delivery
CREATE TABLE IF NOT EXISTS sellers (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL UNIQUE REFERENCES users(id),
    shop_name TEXT NOT NULL UNIQUE, slug TEXT NOT NULL UNIQUE, about TEXT NOT NULL DEFAULT '',
    country TEXT NOT NULL DEFAULT 'IN', city TEXT NOT NULL DEFAULT '', payout_details TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'Pending', commission REAL NOT NULL DEFAULT 10, reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL, decided_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS payouts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, seller_id INTEGER NOT NULL REFERENCES sellers(id),
    amount INTEGER NOT NULL, note TEXT NOT NULL DEFAULT '', paid_by TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);
-- what visitors look at (for recommendations)
CREATE TABLE IF NOT EXISTS views (
    id INTEGER PRIMARY KEY AUTOINCREMENT, visitor TEXT NOT NULL, user_id INTEGER,
    product_id INTEGER NOT NULL, at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_views_visitor ON views(visitor);
CREATE INDEX IF NOT EXISTS idx_views_product ON views(product_id);
-- messages to customers: in the site, by email, SMS and push
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, order_number TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL, destination TEXT NOT NULL DEFAULT '', subject TEXT NOT NULL, body TEXT NOT NULL,
    status TEXT NOT NULL, created_at TEXT NOT NULL, seen INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id);
CREATE TABLE IF NOT EXISTS push_subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, endpoint TEXT NOT NULL UNIQUE,
    p256dh TEXT NOT NULL, auth TEXT NOT NULL, device TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS password_resets (
    token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires_at TEXT NOT NULL, used INTEGER NOT NULL DEFAULT 0
);
-- payments through the card gateway (Stripe test mode or the built-in test gateway)
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT, order_number TEXT NOT NULL, gateway TEXT NOT NULL,
    session_id TEXT NOT NULL DEFAULT '', payment_ref TEXT NOT NULL DEFAULT '', currency TEXT NOT NULL,
    amount INTEGER NOT NULL, status TEXT NOT NULL, message TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS returns (
    id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL REFERENCES orders(id), user_id INTEGER NOT NULL,
    reason TEXT NOT NULL, details TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'Requested',
    amount INTEGER NOT NULL DEFAULT 0, refund_to TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL, decided_at TEXT NOT NULL DEFAULT '', decided_by TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS fx_rates (
    currency TEXT PRIMARY KEY, per_usd REAL NOT NULL, updated_at TEXT NOT NULL, source TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS backups (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, kind TEXT NOT NULL,
    created_by TEXT NOT NULL DEFAULT '', size INTEGER NOT NULL, data TEXT NOT NULL
);
"""

ACTIONS = ["Signed up", "Logged in", "Login failed", "Logged out", "Added to cart", "Order placed",
           "Order cancelled", "Payment", "Return requested", "Review posted", "Address added", "Profile updated",
           "Password changed", "Password reset", "Seller", "Admin update", "Admin: products", "Admin: customers",
           "Admin: orders", "Admin: sellers", "Admin: settings"]


EXTRA_SCHEMA = ""                  # tables of the newer modules are added by them
MIGRATIONS = {
    "users": [("is_admin", "INTEGER NOT NULL DEFAULT 0"), ("blocked", "INTEGER NOT NULL DEFAULT 0"),
              ("country", "TEXT NOT NULL DEFAULT 'IN'"), ("lang", "TEXT NOT NULL DEFAULT 'en'"),
              ("currency", "TEXT NOT NULL DEFAULT ''"), ("totp_secret", "TEXT NOT NULL DEFAULT ''"),
              ("totp_enabled", "INTEGER NOT NULL DEFAULT 0"), ("totp_last_step", "INTEGER NOT NULL DEFAULT 0"),
              ("notify_email", "INTEGER NOT NULL DEFAULT 1"), ("notify_sms", "INTEGER NOT NULL DEFAULT 1")],
    "addresses": [("country", "TEXT NOT NULL DEFAULT 'IN'")],
    "products": [("active", "INTEGER NOT NULL DEFAULT 1"), ("seller_id", "INTEGER"),
                 ("approved", "INTEGER NOT NULL DEFAULT 1")],
    "orders": [("country", "TEXT NOT NULL DEFAULT 'IN'"), ("currency", "TEXT NOT NULL DEFAULT 'INR'"),
               ("fx_rate", "REAL NOT NULL DEFAULT 1"), ("total_local", "INTEGER NOT NULL DEFAULT 0"),
               ("payment_status", "TEXT NOT NULL DEFAULT 'Paid'"), ("payment_ref", "TEXT NOT NULL DEFAULT ''"),
               ("paid_at", "TEXT NOT NULL DEFAULT ''"), ("courier", "TEXT NOT NULL DEFAULT ''"),
               ("awb", "TEXT NOT NULL DEFAULT ''"), ("notified_step", "INTEGER NOT NULL DEFAULT -1"),
               ("lang", "TEXT NOT NULL DEFAULT 'en'"), ("refunded", "INTEGER NOT NULL DEFAULT 0")],
    "order_items": [("seller_id", "INTEGER"), ("commission", "REAL NOT NULL DEFAULT 0")],
}


class Store(PaymentsMixin, NoticesMixin, SellersMixin, RecommendMixin, ReturnsMixin, MaintenanceMixin):
    def __init__(self, db_path, driver=None, notifier=None, env=None):
        """db_path: an SQLite file, or a postgresql:// URL (DATABASE_URL).
        notifier: who sends emails / SMS / push (notify.Notifier); env: settings like STRIPE_SECRET_KEY."""
        self.db_path = str(db_path)
        self.env = os.environ if env is None else env
        self.notifier = notifier or Notifier(env=self.env)
        self.database = Database(self.db_path, driver=driver)
        if not self.database.is_postgres:
            self._replace_old_database()
        self.database.init_schema(SCHEMA + EXTRA_SCHEMA)
        with self.db() as conn:
            # databases made by earlier versions get the new columns
            for table, cols in MIGRATIONS.items():
                have = set(conn.columns(table))
                for col, ddl in cols:
                    if col not in have:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
            # first run: categories, coupons and settings come from catalog.py / defaults
            if not conn.execute("SELECT 1 FROM categories").fetchone():
                conn.executemany("INSERT INTO categories (slug, name, image, position) VALUES (?, ?, ?, ?)",
                                 [(c[0], c[1], c[2], i) for i, c in enumerate(catalog.CATEGORIES)])
            if not conn.execute("SELECT 1 FROM coupons").fetchone():
                conn.executemany(
                    "INSERT INTO coupons (code, description, kind, amount, max_off, min_order, first_order_only) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [(code, c[0], c[1], c[2], c[3], c[4], int(code == "WELCOME100")) for code, c in catalog.COUPONS.items()])
            conn.executemany("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO NOTHING",
                             [(k, str(v)) for k, v in DEFAULT_SETTINGS.items()])
        self.refresh_categories()

    def _replace_old_database(self):
        """If the file was made by an older version of Yo Delivery (different tables),
        keep it as a backup and start a fresh database."""
        if not os.path.exists(self.db_path):
            return
        conn = sqlite3.connect(self.db_path)
        try:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            user_cols = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
            product_cols = {r[1] for r in conn.execute("PRAGMA table_info(products)")}
        finally:
            conn.close()
        old = (tables and ("users" in tables and "mobile" not in user_cols
                           or "products" in tables and "rating_sum" not in product_cols
                           or "products" not in tables))
        if old:
            backup = self.db_path + ".old-backup"
            if os.path.exists(backup):
                os.remove(backup)
            os.rename(self.db_path, backup)
            print(f"Old database found - saved as {backup}, creating a new one.")

    def db(self):
        """Open the database; commit when the block ends OK, roll back on error, always close."""
        return self.database.connect()

    @property
    def is_postgres(self):
        return self.database.is_postgres

    def secret_key(self):
        """The key that signs login cookies. Saved in the database, so logins survive a restart
        and every server process uses the same key."""
        with self.db() as conn:
            conn.execute("INSERT INTO settings (key, value) VALUES ('secret_key', ?) ON CONFLICT (key) DO NOTHING",
                         (secrets.token_hex(32),))
            return conn.execute("SELECT value FROM settings WHERE key = 'secret_key'").fetchone()[0]

    # =================================================================
    # Users
    # =================================================================

    def register(self, name, email, mobile, password, wallet=None, when=None, country="IN", lang="en"):
        """New customer from any country. The mobile number is checked with the rules of that country."""
        if wallet is None:
            wallet = self.setting("signup_wallet")
        country = (country or "IN").upper()
        name, email = " ".join(name.split()), email.strip().lower()
        if country not in world.DIAL_CODES:
            raise ShopError("Please choose your country.")
        if len(name) < 2:
            raise ShopError("Please enter your name.")
        if not EMAIL_RE.match(email):
            raise ShopError("Please enter a valid email address.")
        mobile = world.clean_mobile(country, mobile)
        if not mobile:
            raise ShopError("Please enter a valid 10-digit mobile number." if country == "IN"
                            else "Please enter a valid mobile number for your country.")
        check_password(password)
        lang = lang if lang in world.LANGUAGES else "en"
        with self.db() as conn:
            if conn.execute("SELECT 1 FROM users WHERE email = ? OR mobile = ?", (email, mobile)).fetchone():
                raise ShopError("An account with this email or mobile number already exists. Please sign in.")
            cur = conn.execute("INSERT INTO users (name, email, mobile, password, wallet, created_at, country, lang, "
                               "currency) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                               (name.title(), email, mobile, generate_password_hash(password), wallet, when or now(),
                                country, lang, world.default_currency(country, lang)))
            return cur.lastrowid

    def login(self, login, password):
        login = login.strip().lower()
        phone = re.sub(r"[\s().-]", "", login)
        with self.db() as conn:
            user = conn.execute("SELECT * FROM users WHERE email = ? OR mobile = ? OR mobile = ?",
                                (login, phone, "+" + phone.lstrip("+"))).fetchone()
        if user is None or not check_password_hash(user["password"], password):
            raise ShopError("Incorrect email/mobile number or password.")
        if user["blocked"]:
            raise ShopError("This account has been blocked. Please contact Yo Delivery customer support.")
        return dict(user)

    def get_user(self, user_id):
        with self.db() as conn:
            row = conn.execute("SELECT id, name, email, mobile, wallet, created_at, is_admin, blocked, country, lang, "
                               "currency, totp_enabled, notify_email, notify_sms FROM users WHERE id = ?",
                               (user_id,)).fetchone()
        return dict(row) if row else None

    def update_user(self, user_id, name, email, mobile, country=None):
        user = self.get_user(user_id)
        country = (country or user["country"] or "IN").upper()
        name, email = " ".join(name.split()), email.strip().lower()
        mobile = world.clean_mobile(country, mobile)
        if country not in world.DIAL_CODES or len(name) < 2 or not EMAIL_RE.match(email) or not mobile:
            raise ShopError("Please enter a valid name, email and mobile number.")
        with self.db() as conn:
            if conn.execute("SELECT 1 FROM users WHERE (email = ? OR mobile = ?) AND id != ?",
                            (email, mobile, user_id)).fetchone():
                raise ShopError("This email or mobile number is used by another account.")
            conn.execute("UPDATE users SET name = ?, email = ?, mobile = ?, country = ? WHERE id = ?",
                         (name.title(), email, mobile, country, user_id))

    def set_preferences(self, user_id, lang=None, currency=None):
        """Language and currency the customer chose (alerts and emails use them too)."""
        with self.db() as conn:
            if lang in world.LANGUAGES:
                conn.execute("UPDATE users SET lang = ? WHERE id = ?", (lang, user_id))
            if currency in world.CURRENCIES:
                conn.execute("UPDATE users SET currency = ? WHERE id = ?", (currency, user_id))

    def change_password(self, user_id, old, new):
        with self.db() as conn:
            row = conn.execute("SELECT password FROM users WHERE id = ?", (user_id,)).fetchone()
            if not check_password_hash(row["password"], old):
                raise ShopError("Current password is wrong.")
            check_password(new, "New password")
            conn.execute("UPDATE users SET password = ? WHERE id = ?", (generate_password_hash(new), user_id))

    # ----- addresses -----

    def addresses(self, user_id):
        with self.db() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM addresses WHERE user_id = ? ORDER BY is_default DESC, id DESC", (user_id,))]

    def default_address(self, user_id):
        a = self.addresses(user_id)
        return a[0] if a else None

    def add_address(self, user_id, f):
        d = {k: " ".join(str(f.get(k, "")).split()) for k in
             ("name", "mobile", "line1", "line2", "city", "state", "pincode", "kind", "country")}
        country = (d["country"] or "IN").upper()
        if country not in world.DIAL_CODES:
            raise ShopError("Please choose the country.")
        if len(d["name"]) < 2:
            raise ShopError("Please enter the receiver's name.")
        mobile = world.clean_mobile(country, d["mobile"])
        if not mobile:
            raise ShopError("Please enter a valid 10-digit mobile number." if country == "IN"
                            else "Please enter a valid mobile number for this country.")
        d["mobile"] = mobile
        if len(d["line1"]) < 5:
            raise ShopError("Please enter house number and street.")
        if country == "IN" and (not d["city"] or d["state"] not in STATES):
            raise ShopError("Please enter the city and choose the state.")
        if not d["city"]:
            raise ShopError("Please enter the city.")
        postcode = world.clean_postcode(country, d["pincode"])
        if postcode is None:
            raise ShopError("Please enter a valid 6-digit pincode." if country == "IN" else "Please enter a valid postal code.")
        d["pincode"] = postcode
        d["kind"] = d["kind"] if d["kind"] in ("Home", "Work") else "Home"
        with self.db() as conn:
            first = not conn.execute("SELECT 1 FROM addresses WHERE user_id = ?", (user_id,)).fetchone()
            make_default = first or f.get("is_default") == "yes"
            if make_default:
                conn.execute("UPDATE addresses SET is_default = 0 WHERE user_id = ?", (user_id,))
            cur = conn.execute(
                "INSERT INTO addresses (user_id, name, mobile, line1, line2, city, state, pincode, kind, is_default, "
                "country) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, d["name"].title(), d["mobile"], d["line1"], d["line2"], d["city"].title(),
                 d["state"], d["pincode"], d["kind"], int(make_default), country))
            return cur.lastrowid

    def set_default_address(self, user_id, address_id):
        with self.db() as conn:
            if conn.execute("SELECT 1 FROM addresses WHERE id = ? AND user_id = ?", (address_id, user_id)).fetchone():
                conn.execute("UPDATE addresses SET is_default = CASE WHEN id = ? THEN 1 ELSE 0 END WHERE user_id = ?",
                             (address_id, user_id))

    def delete_address(self, user_id, address_id):
        with self.db() as conn:
            conn.execute("DELETE FROM addresses WHERE id = ? AND user_id = ?", (address_id, user_id))
            if not conn.execute("SELECT 1 FROM addresses WHERE user_id = ? AND is_default = 1", (user_id,)).fetchone():
                conn.execute("UPDATE addresses SET is_default = 1 WHERE id = "
                             "(SELECT MAX(id) FROM addresses WHERE user_id = ?)", (user_id,))

    # =================================================================
    # Catalog
    # =================================================================

    def is_empty(self):
        with self.db() as conn:
            return conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0

    def load_catalog(self):
        """Insert products from catalog.py (only once) with some sample reviews."""
        rng = random.Random(7)
        with self.db() as conn:
            for i, item in enumerate(catalog.PRODUCTS):
                created = (datetime.now() - timedelta(days=len(catalog.PRODUCTS) - i)).strftime("%Y-%m-%d %H:%M:%S")
                cur = conn.execute(
                    "INSERT INTO products (slug, name, brand, category, price, mrp, rating_sum, rating_count, stock, "
                    "art, about, specs, tags, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (item["slug"], item["name"], item["brand"], item["category"], item["price"] * 100,
                     item["mrp"] * 100, item["rating"] * item["reviews"], item["reviews"], item["stock"],
                     item["art"], json.dumps(item["about"]), json.dumps(item["specs"]), item["tags"], created))
                pid = cur.lastrowid
                for _ in range(rng.randint(3, 5)):
                    stars = rng.choices([5, 4, 3], weights=[6, 3, 1])[0]
                    title, body = rng.choice(catalog.REVIEW_TEXTS[stars])
                    when = (datetime.now() - timedelta(days=rng.randint(2, 120), hours=rng.randint(0, 20)))
                    conn.execute("INSERT INTO reviews (product_id, author, rating, title, body, created_at) "
                                 "VALUES (?, ?, ?, ?, ?, ?)",
                                 (pid, rng.choice(catalog.REVIEWERS), stars, title, body,
                                  when.strftime("%Y-%m-%d %H:%M:%S")))

    def get_product(self, slug=None, pid=None):
        with self.db() as conn:
            row = conn.execute("SELECT * FROM products WHERE slug = ? OR id = ?", (slug, pid)).fetchone()
        if row is None:
            raise ShopError("Product not found.")
        return _product(row)

    def products(self, ids=None, category=None, limit=None, order="popular", active_only=True):
        sql, args = "SELECT * FROM products WHERE 1 = 1", []
        if active_only:
            sql += " AND active = 1 AND approved = 1"
        if ids is not None:
            if not ids:
                return []
            sql += f" AND id IN ({','.join('?' * len(ids))})"
            args += list(ids)
        if category:
            sql += " AND category = ?"
            args.append(category)
        sql += {"popular": " ORDER BY rating_count DESC", "deals": " ORDER BY (mrp - price) * 1.0 / mrp DESC",
                "new": " ORDER BY created_at DESC", "rating": " ORDER BY COALESCE(rating_sum / NULLIF(rating_count, 0), 0) DESC"}[order]
        if limit:
            sql += f" LIMIT {int(limit)}"
        with self.db() as conn:
            items = [_product(r) for r in conn.execute(sql, args)]
        if ids is not None:                       # keep the order the ids were given in
            pos = {pid: i for i, pid in enumerate(ids)}
            items.sort(key=lambda x: pos.get(x.id, 0))
        return items

    def search(self, q="", category="", brands=(), min_price=None, max_price=None, min_rating=0,
               min_discount=0, sort="relevance"):
        """Search + filters + sorting for the product list page."""
        sql, args = "SELECT * FROM products WHERE active = 1 AND approved = 1", []
        words = [w for w in re.split(r"\s+", q.lower().strip()) if w][:6]
        for w in words:
            sql += " AND (lower(name) LIKE ? OR lower(brand) LIKE ? OR tags LIKE ? OR category LIKE ?)"
            args += [f"%{w}%"] * 4
        if category:
            sql += " AND category = ?"
            args.append(category)
        if brands:
            sql += f" AND brand IN ({','.join('?' * len(brands))})"
            args += list(brands)
        if min_price is not None:
            sql += " AND price >= ?"
            args.append(int(min_price * 100))
        if max_price is not None:
            sql += " AND price <= ?"
            args.append(int(max_price * 100))
        if min_rating:
            sql += " AND COALESCE(rating_sum / NULLIF(rating_count, 0), 0) >= ?"
            args.append(float(min_rating))
        if min_discount:
            sql += " AND (mrp - price) * 100.0 / mrp >= ?"
            args.append(float(min_discount))
        sql += {"price_asc": " ORDER BY price ASC", "price_desc": " ORDER BY price DESC",
                "rating": " ORDER BY COALESCE(rating_sum / NULLIF(rating_count, 0), 0) DESC, rating_count DESC",
                "discount": " ORDER BY (mrp - price) * 1.0 / mrp DESC", "new": " ORDER BY created_at DESC"
                }.get(sort, " ORDER BY rating_count DESC")
        with self.db() as conn:
            return [_product(r) for r in conn.execute(sql, args)]

    def brands(self, category=""):
        with self.db() as conn:
            if category:
                rows = conn.execute("SELECT DISTINCT brand FROM products WHERE active = 1 AND approved = 1 "
                                    "AND category = ? ORDER BY brand", (category,))
            else:
                rows = conn.execute("SELECT DISTINCT brand FROM products WHERE active = 1 AND approved = 1 "
                                    "ORDER BY brand")
            return [r[0] for r in rows]

    def reviews(self, product_id, limit=10):
        with self.db() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM reviews WHERE product_id = ? ORDER BY created_at DESC LIMIT ?", (product_id, limit))]

    def rating_breakdown(self, product):
        """Star bars (5 to 1) based on the average rating, like big shopping sites show."""
        r, n = product.rating, product.rating_count
        weights = {5: max(r - 3.2, 0.2) ** 2, 4: 1.0, 3: max(4.6 - r, 0.15), 2: max(4.4 - r, 0.08) / 2,
                   1: max(4.5 - r, 0.1) / 1.5}
        total = sum(weights.values())
        return [(s, round(n * weights[s] / total)) for s in (5, 4, 3, 2, 1)]

    def can_review(self, user_id, product_id):
        with self.db() as conn:
            bought = conn.execute(
                "SELECT o.id, o.placed_at, o.cancelled_at FROM orders o JOIN order_items i ON i.order_id = o.id "
                "WHERE o.user_id = ? AND i.product_id = ? AND o.cancelled_at = ''", (user_id, product_id)).fetchall()
            done = conn.execute("SELECT 1 FROM reviews WHERE user_id = ? AND product_id = ?",
                                (user_id, product_id)).fetchone()
        delivered = any(self._order_step(dict(r)) == len(TRACK_STEPS) - 1 for r in bought)
        return delivered and not done

    def add_review(self, user_id, product_id, rating, title, body):
        if not self.can_review(user_id, product_id):
            raise ShopError("You can review a product after it is delivered to you (one review per product).")
        try:
            rating = int(rating)
        except (TypeError, ValueError):
            rating = 0
        if not 1 <= rating <= 5:
            raise ShopError("Please choose a star rating.")
        title, body = " ".join(title.split())[:80], body.strip()[:1000]
        if len(title) < 3 or len(body) < 10:
            raise ShopError("Please write a title and at least 10 characters of review.")
        user = self.get_user(user_id)
        with self.db() as conn:
            conn.execute("INSERT INTO reviews (product_id, user_id, author, rating, title, body, created_at) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (product_id, user_id, user["name"].split()[0] + " " + user["name"].split()[-1][0] + ".",
                          rating, title, body, now()))
            conn.execute("UPDATE products SET rating_sum = rating_sum + ?, rating_count = rating_count + 1 "
                         "WHERE id = ?", (rating, product_id))

    # =================================================================
    # Cart & wishlist  (cart = dict {product_id: qty})
    # =================================================================

    def get_cart(self, user_id):
        with self.db() as conn:
            return {r["product_id"]: r["qty"] for r in
                    conn.execute("SELECT product_id, qty FROM cart WHERE user_id = ?", (user_id,))}

    def save_cart(self, user_id, cart):
        with self.db() as conn:
            conn.execute("DELETE FROM cart WHERE user_id = ?", (user_id,))
            conn.executemany("INSERT INTO cart (user_id, product_id, qty) VALUES (?, ?, ?)",
                             [(user_id, int(pid), q) for pid, q in cart.items() if q > 0])

    def cart_set(self, cart, product_id, qty):
        """Change a quantity in a cart dict (checks stock). Returns the new cart."""
        product = self.get_product(pid=int(product_id))
        qty = max(0, min(int(qty), MAX_QTY))
        if (not product.active or not product.approved) and qty > 0:
            raise ShopError("Sorry, this product is no longer available.")
        if qty > product.stock:
            raise ShopError(f"Only {product.stock} left in stock for {product.brand} {product.name[:30]}...")
        cart = {int(k): v for k, v in cart.items()}
        if qty == 0:
            cart.pop(product.id, None)
        else:
            cart[product.id] = qty
        return cart

    def cart_items(self, cart):
        cart = {int(k): v for k, v in cart.items()}
        items = self.products(ids=list(cart))
        return [(p, cart[p.id]) for p in items]

    def shipping_fee(self, items_total, country="IN"):
        """Delivery charge: India - free above the limit, else the fee; abroad - by zone, free above its limit."""
        zone = world.shipping_zone(country)
        if zone == "domestic":
            return 0 if items_total >= self.setting("free_delivery_from") else self.setting("delivery_fee")
        if items_total >= self.setting("intl_free_from"):
            return 0
        return self.setting("intl_fee_near" if zone == "near" else "intl_fee_world")

    def price_summary(self, items, coupon="", user_id=None, country="IN"):
        mrp_total = sum(p.mrp * q for p, q in items)
        items_total = sum(p.price * q for p, q in items)
        coupon = coupon.strip().upper()
        coupon_discount, coupon_error = 0, ""
        if coupon:
            try:
                coupon_discount = self.coupon_value(coupon, items_total, user_id)
            except ShopError as e:
                coupon_error, coupon = str(e), ""
        domestic = world.shipping_zone(country) == "domestic"
        free_from = self.setting("free_delivery_from" if domestic else "intl_free_from")
        fee = self.shipping_fee(items_total - coupon_discount, country) if items else 0
        total = items_total - coupon_discount + fee
        return {"count": sum(q for _, q in items), "mrp_total": mrp_total, "items_total": items_total,
                "product_discount": mrp_total - items_total, "coupon": coupon, "coupon_discount": coupon_discount,
                "coupon_error": coupon_error, "delivery_fee": fee, "total": total,
                "savings": mrp_total - items_total + coupon_discount,
                "free_delivery_gap": max(0, free_from - (items_total - coupon_discount)), "country": country,
                "international": not domestic}

    def coupon_value(self, code, items_total, user_id=None):
        with self.db() as conn:
            c = conn.execute("SELECT * FROM coupons WHERE code = ? AND active = 1", (code,)).fetchone()
        if c is None:
            raise ShopError("This coupon code is not valid.")
        kind, amount, max_off, minimum = c["kind"], c["amount"], c["max_off"], c["min_order"]
        if items_total < minimum * 100:
            raise ShopError(f"Add items worth {rupees(minimum * 100 - items_total)} more to use {code}.")
        if c["first_order_only"] and user_id is not None:
            with self.db() as conn:
                if conn.execute("SELECT 1 FROM orders WHERE user_id = ? AND cancelled_at = ''", (user_id,)).fetchone():
                    raise ShopError(f"{code} is only for your first order.")
        if kind == "flat":
            return amount * 100
        return min(items_total * amount // 100, max_off * 100)

    def wishlist(self, user_id):
        with self.db() as conn:
            ids = [r[0] for r in conn.execute(
                "SELECT product_id FROM wishlist WHERE user_id = ? ORDER BY added_at DESC", (user_id,))]
        return self.products(ids=ids)

    def wishlist_ids(self, user_id):
        with self.db() as conn:
            return {r[0] for r in conn.execute("SELECT product_id FROM wishlist WHERE user_id = ?", (user_id,))}

    def toggle_wishlist(self, user_id, product_id):
        """Add or remove. Returns True if the product is now in the wishlist."""
        self.get_product(pid=product_id)
        with self.db() as conn:
            if conn.execute("DELETE FROM wishlist WHERE user_id = ? AND product_id = ?",
                            (user_id, product_id)).rowcount:
                return False
            conn.execute("INSERT INTO wishlist (user_id, product_id, added_at) VALUES (?, ?, ?)",
                         (user_id, product_id, now()))
            return True

    # =================================================================
    # Orders
    # =================================================================

    def place_order(self, user_id, address_id, payment, coupon="", upi_id="", when=None, currency=None, rates=None):
        """Create an order from the user's cart. Everything happens in ONE database transaction:
        stock is reduced, wallet is charged, the cart is emptied - or nothing changes at all.

        payment: cod (India only), wallet, upi (India only, simulated) or card. A card order waits for the
        payment gateway (status 'Pending'); its stock is kept for PAYMENT_WINDOW_MINUTES.
        currency/rates: the customer's currency and today's rates - the order remembers the amount in it."""
        cart = self.get_cart(user_id)
        items = self.cart_items(cart)
        if not items:
            raise ShopError("Your cart is empty.")
        address = next((a for a in self.addresses(user_id) if a["id"] == int(address_id or 0)), None)
        if address is None:
            raise ShopError("Please choose a delivery address.")
        country = address.get("country") or "IN"
        summary = self.price_summary(items, coupon, user_id, country)
        if coupon and summary["coupon_error"]:
            raise ShopError(summary["coupon_error"])
        user = self.get_user(user_id)

        status, paid_at = "Paid", None
        if payment == "cod":
            if country != "IN":
                raise ShopError("Cash on Delivery is available only in India.")
            if summary["total"] > self.setting("cod_limit"):
                raise ShopError(f"Cash on Delivery is available for orders up to {rupees(self.setting('cod_limit'))}.")
            method, note, status = "Cash on Delivery", f"Pay {rupees(summary['total'])} in cash or UPI at your door", "COD"
        elif payment == "wallet":
            method, note = "Yo Pay Wallet", "Paid from Yo Pay balance"
        elif payment == "upi":
            if country != "IN":
                raise ShopError("UPI works only for delivery in India. Please pay by card.")
            if not UPI_RE.match(upi_id.strip()):
                raise ShopError("Please enter a valid UPI ID, for example name@okyo.")
            method, note = "UPI", f"Paid via UPI ({upi_id.strip().lower()}) - demo payment"
        elif payment == "card":
            method, note, status = "Card", "Waiting for the card payment", "Pending"
        else:
            raise ShopError("Please choose a payment method.")

        currency = currency if currency in world.CURRENCIES else "INR"
        local = world.convert(summary["total"], currency, rates or {})
        if local is None:                                   # no exchange rate today: charge in rupees
            currency, local = "INR", summary["total"]
        fx = local / summary["total"] if summary["total"] else 1.0

        when = when or now()
        with self.db() as conn:
            for p, q in items:
                lock = " FOR UPDATE" if conn.is_postgres else ""
                stock = conn.execute("SELECT stock FROM products WHERE id = ?" + lock, (p.id,)).fetchone()[0]
                if q > stock:
                    raise ShopError(f"Sorry, only {stock} left of {p.name[:40]}. Please update your cart.")
                conn.execute("UPDATE products SET stock = stock - ? WHERE id = ?", (q, p.id))
            if payment == "wallet":
                bal = conn.execute("SELECT wallet FROM users WHERE id = ?", (user_id,)).fetchone()[0]
                if bal < summary["total"]:
                    raise ShopError(f"Not enough Yo Pay balance ({rupees(bal)}). Choose another payment method.")
                conn.execute("UPDATE users SET wallet = wallet - ? WHERE id = ?", (summary["total"], user_id))
            number = "OD" + to_dt(when).strftime("%y%m%d") + "".join(random.choices("0123456789", k=8))
            line3 = (address["state"] + " - " if address["state"] else "") + address["pincode"]
            addr_text = ", ".join(x for x in (address["line1"], address["line2"], address["city"], line3,
                                              world.country_name(country) if country != "IN" else "") if x)
            expected = (to_dt(when) + timedelta(days=delivery_days(address["pincode"], country) + 1)
                        ).strftime("%Y-%m-%d %H:%M:%S")
            domestic = world.shipping_zone(country) == "domestic"
            courier = "Yo Express" if domestic else "Yo Global Express"
            awb = "YX" + "".join(random.choices("0123456789", k=10)) + country
            paid_at = when if status in ("Paid", "COD") else ""
            cur = conn.execute(
                "INSERT INTO orders (number, user_id, ship_name, ship_mobile, ship_address, ship_pincode, mrp_total, "
                "items_total, coupon, coupon_discount, delivery_fee, total, payment_method, payment_note, placed_at, "
                "expected, country, currency, fx_rate, total_local, payment_status, paid_at, courier, awb, lang, "
                "notified_step) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (number, user_id, address["name"], address["mobile"], addr_text, address["pincode"],
                 summary["mrp_total"], summary["items_total"], summary["coupon"], summary["coupon_discount"],
                 summary["delivery_fee"], summary["total"], method, note, when, expected, country, currency, fx,
                 local, status, paid_at, courier, awb, user["lang"] or "en", -1))
            order_id = cur.lastrowid
            sellers = {r[0]: r[1] for r in conn.execute("SELECT id, commission FROM sellers")}
            conn.executemany(
                "INSERT INTO order_items (order_id, product_id, slug, name, price, mrp, qty, seller_id, commission) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(order_id, p.id, p.slug, p.name, p.price, p.mrp, q, p.seller_id, sellers.get(p.seller_id, 0))
                 for p, q in items])
            conn.execute("DELETE FROM cart WHERE user_id = ?", (user_id,))
        return number

    @staticmethod
    def _step(placed_at):
        minutes = (datetime.now() - to_dt(placed_at)).total_seconds() / 60
        return max(i for i, m in enumerate(TRACK_MINUTES) if minutes >= m)

    def _events(self, order_id):
        """Steps the admin set by hand: {step: time}."""
        with self.db() as conn:
            return {r["step"]: r["at"] for r in conn.execute(
                "SELECT step, MAX(at) AS at FROM order_events WHERE order_id = ? GROUP BY step", (order_id,))}

    def _order_step(self, order):
        """Current step of an order: set by the admin if he changed it, otherwise automatic by time
        (the clock starts when the order is paid; a card order waiting for payment stays at step 0)."""
        events = self._events(order["id"])
        if events:
            return max(events)
        if order.get("payment_status") == "Pending" or order.get("payment_status") == "Failed":
            return 0
        return self._step(self._clock_start(order))

    @staticmethod
    def _clock_start(order):
        """Tracking starts when the order is placed - for a card order, when it is paid."""
        if order.get("payment_method") == "Card" and order.get("paid_at"):
            return order["paid_at"]
        return order["placed_at"]

    def tracking(self, order):
        """List of tracking steps: (name, time text or '', done?)."""
        if order["cancelled_at"]:
            return [("Order placed", order["placed_at"], True), ("Cancelled", order["cancelled_at"], True)]
        events = self._events(order["id"])
        step = self._order_step(order)
        placed = to_dt(self._clock_start(order))
        out = []
        for i, name in enumerate(TRACK_STEPS):
            if i > step:
                t = ""
            elif events:
                t = order["placed_at"] if i == 0 else events.get(i, "")
            else:
                t = (placed + timedelta(minutes=TRACK_MINUTES[i])).strftime("%Y-%m-%d %H:%M:%S")
            out.append((name, t, i <= step))
        return out

    def status(self, order):
        if order["cancelled_at"]:
            return "Cancelled"
        if order.get("payment_status") in ("Pending", "Failed"):
            return "Awaiting payment"
        return TRACK_STEPS[self._order_step(order)]

    def orders(self, user_id):
        with self.db() as conn:
            orders = [dict(r) for r in conn.execute(
                "SELECT * FROM orders WHERE user_id = ? ORDER BY placed_at DESC", (user_id,))]
            for o in orders:
                o["items"] = [dict(r) for r in conn.execute("SELECT * FROM order_items WHERE order_id = ?", (o["id"],))]
                o["status"] = self.status(o)
        return orders

    def get_order(self, user_id, number):
        with self.db() as conn:
            o = conn.execute("SELECT * FROM orders WHERE number = ? AND user_id = ?", (number, user_id)).fetchone()
            if o is None:
                raise ShopError("Order not found.")
            o = dict(o)
            o["items"] = [dict(r) for r in conn.execute("SELECT * FROM order_items WHERE order_id = ?", (o["id"],))]
        o["status"] = self.status(o)
        o["step"] = self._order_step(o)
        o["can_cancel"] = not o["cancelled_at"] and o["step"] < 2   # before "Shipped"
        o["manual"] = bool(self._events(o["id"]))
        o["international"] = o.get("country", "IN") != "IN"
        return o

    def cancel_order(self, user_id, number):
        o = self.get_order(user_id, number)
        if not o["can_cancel"]:
            raise ShopError("This order has already been shipped and cannot be cancelled.")
        with self.db() as conn:
            if conn.execute("UPDATE orders SET cancelled_at = ? WHERE id = ? AND cancelled_at = ''",
                            (now(), o["id"])).rowcount == 0:
                raise ShopError("This order was already cancelled.")
            for it in o["items"]:
                conn.execute("UPDATE products SET stock = stock + ? WHERE id = ?", (it["qty"], it["product_id"]))
            if o["payment_method"] in ("Yo Pay Wallet", "UPI"):
                conn.execute("UPDATE users SET wallet = wallet + ? WHERE id = ?", (o["total"], user_id))
                conn.execute("UPDATE orders SET refunded = ? WHERE id = ?", (o["total"], o["id"]))
        if o["payment_method"] == "Card" and o.get("payment_status") == "Paid":
            self.refund_card(o, o["total"], "Order cancelled")
        return o

    # =================================================================
    # Activity log & admin panel
    # =================================================================

    def log(self, user_id, action, details="", ip="", when=None):
        """Save one website activity (login, order, ...) for the admin panel."""
        name = ""
        if user_id:
            u = self.get_user(user_id)
            name = u["name"] if u else ""
        with self.db() as conn:
            conn.execute("INSERT INTO activity (at, user_id, user_name, action, details, ip) VALUES (?, ?, ?, ?, ?, ?)",
                         (when or now(), user_id or None, name, action, details[:200], ip or ""))

    def make_admin(self, user_id, admin=True):
        with self.db() as conn:
            conn.execute("UPDATE users SET is_admin = ? WHERE id = ?", (int(admin), user_id))

    def activity(self, action="", q="", user_id=None, limit=200):
        sql, args = "SELECT * FROM activity WHERE 1 = 1", []
        if action:
            sql += " AND action = ?"
            args.append(action)
        if user_id:
            sql += " AND user_id = ?"
            args.append(user_id)
        if q:
            sql += " AND (lower(user_name) LIKE ? OR lower(details) LIKE ? OR ip LIKE ?)"
            args += [f"%{q.lower()}%"] * 3
        sql += " ORDER BY at DESC, id DESC LIMIT ?"
        args.append(int(limit))
        with self.db() as conn:
            return [dict(r) for r in conn.execute(sql, args)]

    def admin_stats(self, days=14):
        """Numbers for the admin dashboard."""
        today = datetime.now().strftime("%Y-%m-%d")
        with self.db() as conn:
            one = lambda sql, *a: conn.execute(sql, a).fetchone()[0]
            stats = {
                "revenue": one("SELECT COALESCE(SUM(total), 0) FROM orders WHERE cancelled_at = ''"),
                "orders": one("SELECT COUNT(*) FROM orders"),
                "cancelled": one("SELECT COUNT(*) FROM orders WHERE cancelled_at != ''"),
                "orders_today": one("SELECT COUNT(*) FROM orders WHERE placed_at >= ?", today),
                "revenue_today": one("SELECT COALESCE(SUM(total), 0) FROM orders WHERE cancelled_at = '' AND placed_at >= ?", today),
                "customers": one("SELECT COUNT(*) FROM users WHERE is_admin = 0"),
                "new_customers": one("SELECT COUNT(*) FROM users WHERE is_admin = 0 AND created_at >= ?",
                                     (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")),
                "products": one("SELECT COUNT(*) FROM products WHERE active = 1"),
                "hidden_products": one("SELECT COUNT(*) FROM products WHERE active = 0"),
                "low_stock": one("SELECT COUNT(*) FROM products WHERE active = 1 AND stock <= 10"),
                "items_sold": one("SELECT COALESCE(SUM(i.qty), 0) FROM order_items i JOIN orders o ON o.id = i.order_id "
                                  "WHERE o.cancelled_at = ''"),
                "logins_today": one("SELECT COUNT(*) FROM activity WHERE action = 'Logged in' AND at >= ?", today),
                "carts": one("SELECT COUNT(DISTINCT user_id) FROM cart"),
            }
            stats["avg_order"] = stats["revenue"] // max(stats["orders"] - stats["cancelled"], 1)
            start = datetime.now().date() - timedelta(days=days - 1)
            rows = conn.execute("SELECT substr(placed_at, 1, 10) d, COUNT(*) n, SUM(total) s FROM orders "
                                "WHERE cancelled_at = '' AND placed_at >= ? GROUP BY d", (start.isoformat(),)).fetchall()
            by_day = {r["d"]: (r["n"], r["s"]) for r in rows}
            stats["chart"] = [{"day": (start + timedelta(days=i)).strftime("%d %b"),
                               "orders": by_day.get((start + timedelta(days=i)).isoformat(), (0, 0))[0],
                               "sales": by_day.get((start + timedelta(days=i)).isoformat(), (0, 0))[1]}
                              for i in range(days)]
            stats["top_products"] = [dict(r) for r in conn.execute(
                "SELECT MAX(i.slug) AS slug, MAX(i.name) AS name, SUM(i.qty) AS qty, SUM(i.qty * i.price) AS sales "
                "FROM order_items i JOIN orders o ON o.id = i.order_id WHERE o.cancelled_at = '' GROUP BY i.product_id "
                "ORDER BY qty DESC, sales DESC LIMIT 5")]
            stats["by_category"] = [dict(r) for r in conn.execute(
                "SELECT p.category, SUM(i.qty * i.price) sales FROM order_items i JOIN orders o ON o.id = i.order_id "
                "JOIN products p ON p.id = i.product_id WHERE o.cancelled_at = '' GROUP BY p.category ORDER BY sales DESC")]
            stats["payments"] = [dict(r) for r in conn.execute(
                "SELECT payment_method, COUNT(*) n FROM orders GROUP BY payment_method ORDER BY n DESC")]
        return stats

    def all_orders(self, status="", q="", limit=300):
        sql = "SELECT o.*, u.name AS customer, u.email FROM orders o JOIN users u ON u.id = o.user_id WHERE 1 = 1"
        args = []
        if q:
            sql += " AND (o.number LIKE ? OR lower(u.name) LIKE ? OR u.email LIKE ? OR o.ship_pincode LIKE ?)"
            args += [f"%{q.strip().lower()}%"] * 4
        sql += " ORDER BY o.placed_at DESC LIMIT ?"
        args.append(limit)
        with self.db() as conn:
            orders = [dict(r) for r in conn.execute(sql, args)]
            for o in orders:
                o["items"] = [dict(r) for r in conn.execute("SELECT * FROM order_items WHERE order_id = ?", (o["id"],))]
                o["status"] = self.status(o)
        return [o for o in orders if not status or o["status"] == status]

    def admin_order(self, number):
        with self.db() as conn:
            row = conn.execute("SELECT user_id FROM orders WHERE number = ?", (number,)).fetchone()
        if row is None:
            raise ShopError("Order not found.")
        o = self.get_order(row["user_id"], number)
        o["customer"] = self.get_user(row["user_id"])
        return o

    def all_customers(self, q=""):
        sql = ("SELECT u.id, u.name, u.email, u.mobile, u.wallet, u.created_at, u.is_admin, u.blocked, "
               "(SELECT COUNT(*) FROM orders o WHERE o.user_id = u.id) AS orders, "
               "(SELECT COALESCE(SUM(total), 0) FROM orders o WHERE o.user_id = u.id AND o.cancelled_at = '') AS spent, "
               "(SELECT MAX(at) FROM activity a WHERE a.user_id = u.id) AS last_seen "
               "FROM users u WHERE 1 = 1")
        args = []
        if q:
            sql += " AND (lower(u.name) LIKE ? OR u.email LIKE ? OR u.mobile LIKE ?)"
            args += [f"%{q.strip().lower()}%"] * 3
        with self.db() as conn:
            return [dict(r) for r in conn.execute(sql + " ORDER BY u.created_at DESC", args)]

    def admin_update_product(self, product_id, price, mrp, stock):
        try:
            price, mrp, stock = round(float(price) * 100), round(float(mrp) * 100), int(stock)
        except (TypeError, ValueError):
            raise ShopError("Price, MRP and stock must be numbers.")
        if price <= 0 or mrp < price:
            raise ShopError("Price must be more than 0 and MRP cannot be lower than the price.")
        if not 0 <= stock <= 100000:
            raise ShopError("Stock must be between 0 and 100000.")
        p = self.get_product(pid=product_id)
        with self.db() as conn:
            conn.execute("UPDATE products SET price = ?, mrp = ?, stock = ? WHERE id = ?", (price, mrp, stock, p.id))
        return p

    # ----- read-only database viewer -----

    HIDDEN_COLUMNS = {"password"}

    def tables(self):
        with self.db() as conn:
            names = sorted(n for n in conn.tables() if not n.startswith("sqlite_"))
            return [(n, conn.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0]) for n in names]

    def table_rows(self, name, limit=100, offset=0):
        """Columns + rows of one table. The table name is checked against the real list (no SQL injection)
        and password hashes are hidden."""
        if name not in dict(self.tables()):
            raise ShopError("Unknown table.")
        with self.db() as conn:
            columns = conn.columns(name)
            order = "id" if "id" in columns else columns[0]
            cur = conn.execute(f'SELECT * FROM "{name}" ORDER BY {order} DESC LIMIT ? OFFSET ?', (limit, offset))
            cols = [d[0] for d in cur.description]
            rows = [["•••••• (hidden)" if c in self.HIDDEN_COLUMNS else v for c, v in zip(cols, r)] for r in cur]
        return cols, rows

    # =================================================================
    # Admin: settings, categories, coupons
    # =================================================================

    def setting(self, key):
        with self.db() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        value = row["value"] if row else str(DEFAULT_SETTINGS[key])
        return value if key in TEXT_SETTINGS else int(float(value))

    def settings(self):
        return {k: self.setting(k) for k in DEFAULT_SETTINGS}

    def update_settings(self, f):
        new = {}
        try:
            for key in ("free_delivery_from", "delivery_fee", "signup_wallet", "cod_limit", "intl_fee_near",
                        "intl_fee_world", "intl_free_from"):
                new[key] = round(float(f.get(key, self.setting(key) / 100)) * 100)
                if not 0 <= new[key] <= 10_000_000 * 100:
                    raise ValueError
            for key, low, high in (("seller_commission", 0, 50), ("return_days", 0, 60)):
                new[key] = int(float(f.get(key, self.setting(key))))
                if not low <= new[key] <= high:
                    raise ValueError
        except (TypeError, ValueError):
            raise ShopError("Please enter valid amounts (0 or more), a commission of 0-50 % and 0-60 return days.")
        new["announcement"] = " ".join(f.get("announcement", "").split())[:160]
        with self.db() as conn:
            conn.executemany("INSERT INTO settings (key, value) VALUES (?, ?) "
                             "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                             [(k, str(v)) for k, v in new.items()])

    def refresh_categories(self):
        CATEGORY_NAMES.clear()
        CATEGORY_NAMES.update({c["slug"]: c["name"] for c in self.categories()})

    def categories(self):
        with self.db() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT c.*, (SELECT COUNT(*) FROM products p WHERE p.category = c.slug AND p.active = 1) AS products "
                "FROM categories c ORDER BY position, name")]

    def save_category(self, name, slug=None, image=None):
        name = " ".join(name.split())
        if not 2 <= len(name) <= 40:
            raise ShopError("Category name must be 2 to 40 characters.")
        with self.db() as conn:
            if slug:                                            # rename
                if not conn.execute("SELECT 1 FROM categories WHERE slug = ?", (slug,)).fetchone():
                    raise ShopError("Category not found.")
                conn.execute("UPDATE categories SET name = ? WHERE slug = ?", (name, slug))
                if image:
                    conn.execute("UPDATE categories SET image = ? WHERE slug = ?", (image, slug))
            else:                                               # new
                slug = make_slug(name)
                if conn.execute("SELECT 1 FROM categories WHERE slug = ?", (slug,)).fetchone():
                    raise ShopError("A category with this name already exists.")
                pos = conn.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM categories").fetchone()[0]
                conn.execute("INSERT INTO categories (slug, name, image, position) VALUES (?, ?, ?, ?)",
                             (slug, name, image or "logo.png", pos))
        self.refresh_categories()
        return slug

    def delete_category(self, slug):
        with self.db() as conn:
            if conn.execute("SELECT 1 FROM products WHERE category = ?", (slug,)).fetchone():
                raise ShopError("This category still has products. Move or delete them first.")
            conn.execute("DELETE FROM categories WHERE slug = ?", (slug,))
        self.refresh_categories()

    def coupons(self, active_only=False):
        with self.db() as conn:
            sql = "SELECT * FROM coupons" + (" WHERE active = 1" if active_only else "") + " ORDER BY min_order"
            return [dict(r) for r in conn.execute(sql)]

    def save_coupon(self, f):
        code = re.sub(r"[^A-Z0-9]", "", f.get("code", "").upper())[:20]
        if len(code) < 3:
            raise ShopError("Coupon code must have at least 3 letters/numbers.")
        kind = f.get("kind")
        if kind not in ("flat", "percent"):
            raise ShopError("Choose flat (₹) or percent (%) discount.")
        try:
            amount, max_off, min_order = int(f.get("amount")), int(f.get("max_off") or 0), int(f.get("min_order") or 0)
        except (TypeError, ValueError):
            raise ShopError("Amount, maximum discount and minimum order must be whole rupees.")
        if amount <= 0 or (kind == "percent" and amount > 90) or min_order < 0:
            raise ShopError("Discount must be more than 0 (and at most 90%).")
        if kind == "flat":
            max_off = amount
            if min_order and amount >= min_order:
                raise ShopError("A flat discount must be smaller than the minimum order.")
        elif max_off <= 0:
            raise ShopError("Please set the maximum discount in ₹ for a percent coupon.")
        desc = " ".join(f.get("description", "").split())[:100] or (
            f"₹{amount} off" if kind == "flat" else f"{amount}% off up to ₹{max_off:,}") + (
            f" on orders above ₹{min_order:,}" if min_order else "")
        with self.db() as conn:
            conn.execute("INSERT INTO coupons (code, description, kind, amount, max_off, min_order, "
                         "first_order_only, active) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (code) DO UPDATE SET "
                         "description = excluded.description, kind = excluded.kind, amount = excluded.amount, "
                         "max_off = excluded.max_off, min_order = excluded.min_order, "
                         "first_order_only = excluded.first_order_only, active = excluded.active",
                         (code, desc, kind, amount, max_off, min_order, int(f.get("first_order_only") == "yes"),
                          int(f.get("active", "yes") == "yes")))
        return code

    def toggle_coupon(self, code):
        with self.db() as conn:
            conn.execute("UPDATE coupons SET active = 1 - active WHERE code = ?", (code,))

    def delete_coupon(self, code):
        with self.db() as conn:
            conn.execute("DELETE FROM coupons WHERE code = ?", (code,))

    # =================================================================
    # Admin: products
    # =================================================================

    def save_product(self, f, product_id=None):
        """Add a new product (product_id=None) or update all details of an existing one."""
        name = " ".join(f.get("name", "").split())[:160]
        brand = " ".join(f.get("brand", "").split())[:40]
        category = f.get("category", "")
        if len(name) < 5:
            raise ShopError("Product name must be at least 5 characters.")
        if len(brand) < 2:
            raise ShopError("Please enter the brand.")
        if category not in CATEGORY_NAMES:
            raise ShopError("Please choose a category.")
        try:
            price, mrp = round(float(f.get("price")) * 100), round(float(f.get("mrp")) * 100)
            stock = int(f.get("stock"))
        except (TypeError, ValueError):
            raise ShopError("Price, MRP and stock must be numbers.")
        if price <= 0 or mrp < price:
            raise ShopError("Price must be more than 0 and MRP cannot be lower than the price.")
        if not 0 <= stock <= 100000:
            raise ShopError("Stock must be between 0 and 100000.")
        about = [" ".join(line.split()) for line in f.get("about", "").splitlines() if line.strip()][:10]
        if not about:
            raise ShopError("Please write at least one line in 'About this item'.")
        specs = {}
        for line in f.get("specs", "").splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                if k.strip() and v.strip():
                    specs[" ".join(k.split())[:40]] = " ".join(v.split())[:120]
        tags = " ".join(f.get("tags", "").lower().split())[:200]
        active = int(f.get("active", "yes") == "yes")
        with self.db() as conn:
            if product_id:
                old = self.get_product(pid=product_id)
                conn.execute("UPDATE products SET name = ?, brand = ?, category = ?, price = ?, mrp = ?, stock = ?, "
                             "about = ?, specs = ?, tags = ?, active = ? WHERE id = ?",
                             (name, brand, category, price, mrp, stock, json.dumps(about), json.dumps(specs), tags,
                              active, product_id))
                return old.slug
            slug = make_slug(f"{brand} {name}")[:60].strip("-")
            base, n = slug, 2
            while conn.execute("SELECT 1 FROM products WHERE slug = ?", (slug,)).fetchone():
                slug, n = f"{base}-{n}", n + 1
            conn.execute("INSERT INTO products (slug, name, brand, category, price, mrp, rating_sum, rating_count, "
                         "stock, art, about, specs, tags, created_at, active) "
                         "VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?, 'upload', ?, ?, ?, ?, ?)",
                         (slug, name, brand, category, price, mrp, stock, json.dumps(about), json.dumps(specs),
                          tags, now(), active))
            return slug

    def set_product_active(self, product_id, active):
        with self.db() as conn:
            conn.execute("UPDATE products SET active = ? WHERE id = ?", (int(active), product_id))
            if not active:                              # remove from carts so nobody can buy it
                conn.execute("DELETE FROM cart WHERE product_id = ?", (product_id,))

    def delete_product(self, product_id):
        """Delete only if it was never ordered; sold products can only be hidden (orders keep history)."""
        p = self.get_product(pid=product_id)
        with self.db() as conn:
            if conn.execute("SELECT 1 FROM order_items WHERE product_id = ?", (product_id,)).fetchone():
                raise ShopError("This product is in customers' orders, so it cannot be deleted. Hide it instead.")
            for table in ("cart", "wishlist", "reviews"):
                conn.execute(f"DELETE FROM {table} WHERE product_id = ?", (product_id,))
            conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
        return p

    # =================================================================
    # Admin: customers
    # =================================================================

    def admin_update_user(self, user_id, f, acting_admin_id):
        self.update_user(user_id, f.get("name", ""), f.get("email", ""), f.get("mobile", ""), f.get("country"))
        is_admin, blocked = int(f.get("is_admin") == "yes"), int(f.get("blocked") == "yes")
        if user_id == acting_admin_id and (not is_admin or blocked):
            raise ShopError("You cannot remove your own admin rights or block yourself.")
        with self.db() as conn:
            conn.execute("UPDATE users SET is_admin = ?, blocked = ? WHERE id = ?", (is_admin, blocked, user_id))

    def admin_set_password(self, user_id, new):
        check_password(new)
        with self.db() as conn:
            conn.execute("UPDATE users SET password = ? WHERE id = ?", (generate_password_hash(new), user_id))

    def adjust_wallet(self, user_id, rupees_change):
        try:
            change = round(float(rupees_change) * 100)
        except (TypeError, ValueError):
            raise ShopError("Enter an amount like 500 or -200.")
        if change == 0 or abs(change) > 1_000_000 * 100:
            raise ShopError("Enter an amount between -10,00,000 and 10,00,000 (not 0).")
        with self.db() as conn:
            bal = conn.execute("SELECT wallet FROM users WHERE id = ?", (user_id,)).fetchone()["wallet"]
            if bal + change < 0:
                raise ShopError(f"The wallet has only {rupees(bal)}; it cannot go below zero.")
            conn.execute("UPDATE users SET wallet = wallet + ? WHERE id = ?", (change, user_id))
        return bal + change

    def delete_user(self, user_id, acting_admin_id):
        if user_id == acting_admin_id:
            raise ShopError("You cannot delete your own account.")
        with self.db() as conn:
            if conn.execute("SELECT 1 FROM orders WHERE user_id = ?", (user_id,)).fetchone():
                raise ShopError("This customer has orders, so the account cannot be deleted. Block it instead.")
            if conn.execute("SELECT 1 FROM sellers WHERE user_id = ?", (user_id,)).fetchone():
                raise ShopError("This account owns a seller shop. Suspend the shop instead.")
            for table in ("addresses", "cart", "wishlist", "notifications", "push_subscriptions", "password_resets"):
                conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
            conn.execute("UPDATE views SET user_id = NULL WHERE user_id = ?", (user_id,))
            conn.execute("UPDATE reviews SET user_id = NULL WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))

    # =================================================================
    # Admin: reviews and order status
    # =================================================================

    def all_reviews(self, q="", rating=None, limit=300):
        sql = ("SELECT r.*, p.name AS product, p.slug FROM reviews r JOIN products p ON p.id = r.product_id "
               "WHERE 1 = 1")
        args = []
        if q:
            sql += " AND (lower(r.author) LIKE ? OR lower(r.title) LIKE ? OR lower(r.body) LIKE ? OR lower(p.name) LIKE ?)"
            args += [f"%{q.lower()}%"] * 4
        if rating:
            sql += " AND r.rating = ?"
            args.append(int(rating))
        sql += " ORDER BY r.created_at DESC LIMIT ?"
        args.append(limit)
        with self.db() as conn:
            return [dict(r) for r in conn.execute(sql, args)]

    def delete_review(self, review_id):
        with self.db() as conn:
            r = conn.execute("SELECT * FROM reviews WHERE id = ?", (review_id,)).fetchone()
            if r is None:
                raise ShopError("Review not found.")
            conn.execute("DELETE FROM reviews WHERE id = ?", (review_id,))
            conn.execute("UPDATE products SET rating_sum = CASE WHEN rating_sum > ? THEN rating_sum - ? ELSE 0 END, "
                         "rating_count = CASE WHEN rating_count > 0 THEN rating_count - 1 ELSE 0 END WHERE id = ?",
                         (r["rating"], r["rating"], r["product_id"]))
            return dict(r)

    def set_order_status(self, number, step_name, note=""):
        """Admin moves an order to Packed / Shipped / Out for delivery / Delivered (cannot go back)."""
        o = self.admin_order(number)
        if o["cancelled_at"]:
            raise ShopError("This order is cancelled.")
        if step_name not in TRACK_STEPS[1:]:
            raise ShopError("Choose a valid status.")
        step, current = TRACK_STEPS.index(step_name), self._order_step(o)
        if step <= current:
            raise ShopError(f"The order is already '{TRACK_STEPS[current]}'. Status can only move forward.")
        with self.db() as conn:
            if not o["manual"]:          # keep the steps the automatic timeline had already reached
                placed = to_dt(o["placed_at"])
                for i in range(1, min(current, step - 1) + 1):
                    t = (placed + timedelta(minutes=TRACK_MINUTES[i])).strftime("%Y-%m-%d %H:%M:%S")
                    conn.execute("INSERT INTO order_events (order_id, step, at, note) VALUES (?, ?, ?, '')",
                                 (o["id"], i, t))
            for i in range(max(current if o["manual"] else min(current, step - 1), 0) + 1, step + 1):
                conn.execute("INSERT INTO order_events (order_id, step, at, note) VALUES (?, ?, ?, ?)",
                             (o["id"], i, now(), note[:120] if i == step else ""))
        return o


def make_slug(text):
    """'Nova X5 Pro (Blue)' -> 'nova-x5-pro-blue'"""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "item"


def check_password(password, label="Password"):
    if len(password or "") < 8 or not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        raise ShopError(f"{label} must be at least 8 characters with letters and numbers.")


def is_example_email(email):
    """Addresses on reserved example domains (RFC 2606) belong to demo customers: never email or text them."""
    domain = str(email or "").rsplit("@", 1)[-1].lower()
    return domain in ("example.com", "example.org", "example.net") or domain.endswith((".example", ".test", ".invalid"))
