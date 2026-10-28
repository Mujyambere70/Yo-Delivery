"""
store.py - all the shop logic of Yo Delivery (works with an SQLite database).

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
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta

from werkzeug.security import check_password_hash, generate_password_hash

import catalog

# Default shop settings (the admin can change them on the Settings page)
FREE_DELIVERY_FROM = 499 * 100     # free delivery for orders of ₹499 or more
DELIVERY_FEE = 40 * 100
SIGNUP_WALLET = 2000 * 100         # demo Yo Pay balance for new users
DEFAULT_SETTINGS = {"free_delivery_from": FREE_DELIVERY_FROM, "delivery_fee": DELIVERY_FEE,
                    "signup_wallet": SIGNUP_WALLET, "cod_limit": 50000 * 100, "announcement": ""}
MAX_QTY = 10
CATEGORY_NAMES = {}                # slug -> name, kept up to date by Store

# Order tracking: minutes after the order for each step.
# FAST mode (default) is for demos: an order is delivered in about 8 minutes.
FAST_TRACKING = os.environ.get("YO_FAST_TRACKING", "1") != "0"
TRACK_STEPS = ["Order placed", "Packed", "Shipped", "Out for delivery", "Delivered"]
TRACK_MINUTES = [0, 1, 3, 5, 8] if FAST_TRACKING else [0, 6 * 60, 24 * 60, 46 * 60, 52 * 60]

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$")
MOBILE_RE = re.compile(r"^[6-9]\d{9}$")
PINCODE_RE = re.compile(r"^[1-9]\d{5}$")
UPI_RE = re.compile(r"^[a-zA-Z0-9.\-_]{2,}@[a-zA-Z]{2,}$")

STATES = ["Andhra Pradesh", "Assam", "Bihar", "Chandigarh", "Chhattisgarh", "Delhi", "Goa", "Gujarat",
          "Haryana", "Himachal Pradesh", "Jammu and Kashmir", "Jharkhand", "Karnataka", "Kerala",
          "Madhya Pradesh", "Maharashtra", "Odisha", "Punjab", "Rajasthan", "Tamil Nadu", "Telangana",
          "Uttar Pradesh", "Uttarakhand", "West Bengal"]


class ShopError(Exception):
    """An error message that can be shown to the customer."""


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def to_dt(text):
    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")


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


def delivery_days(pincode):
    """Rough delivery time from the Ambala warehouse."""
    if pincode.startswith("13"):
        return 1          # Ambala / nearby
    if pincode[:2] in ("11", "12", "14", "15", "16", "17"):
        return 2          # Delhi, Haryana, Punjab, Himachal
    return 4


def delivery_date_text(pincode, start=None):
    days = delivery_days(pincode)
    d = (start or datetime.now()) + timedelta(days=days)
    return ("Tomorrow, " if days == 1 else "") + d.strftime("%a, %d %b")


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
CREATE TABLE IF NOT EXISTS order_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL REFERENCES orders(id),
    step INTEGER NOT NULL, at TEXT NOT NULL, note TEXT NOT NULL DEFAULT ''
);
"""

ACTIONS = ["Signed up", "Logged in", "Login failed", "Logged out", "Added to cart", "Order placed",
           "Order cancelled", "Review posted", "Address added", "Profile updated", "Password changed",
           "Admin update", "Admin: products", "Admin: customers", "Admin: orders", "Admin: settings"]


class Store:
    def __init__(self, db_path):
        self.db_path = db_path
        self._replace_old_database()
        with self.db() as conn:
            conn.executescript(SCHEMA)
            # databases made by earlier versions get the new columns
            user_cols = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
            if "is_admin" not in user_cols:
                conn.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")
            if "blocked" not in user_cols:
                conn.execute("ALTER TABLE users ADD COLUMN blocked INTEGER NOT NULL DEFAULT 0")
            if "active" not in {r[1] for r in conn.execute("PRAGMA table_info(products)")}:
                conn.execute("ALTER TABLE products ADD COLUMN active INTEGER NOT NULL DEFAULT 1")
            # first run: categories, coupons and settings come from catalog.py / defaults
            if not conn.execute("SELECT 1 FROM categories").fetchone():
                conn.executemany("INSERT INTO categories (slug, name, image, position) VALUES (?, ?, ?, ?)",
                                 [(c[0], c[1], c[2], i) for i, c in enumerate(catalog.CATEGORIES)])
            if not conn.execute("SELECT 1 FROM coupons").fetchone():
                conn.executemany(
                    "INSERT INTO coupons (code, description, kind, amount, max_off, min_order, first_order_only) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [(code, c[0], c[1], c[2], c[3], c[4], int(code == "WELCOME100")) for code, c in catalog.COUPONS.items()])
            conn.executemany("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
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

    @contextmanager
    def db(self):
        """Open the database; commit when the block ends OK, roll back on error, always close."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    # =================================================================
    # Users
    # =================================================================

    def register(self, name, email, mobile, password, wallet=None, when=None):
        if wallet is None:
            wallet = self.setting("signup_wallet")
        name, email, mobile = " ".join(name.split()), email.strip().lower(), mobile.strip()
        if len(name) < 2:
            raise ShopError("Please enter your name.")
        if not EMAIL_RE.match(email):
            raise ShopError("Please enter a valid email address.")
        if not MOBILE_RE.match(mobile):
            raise ShopError("Please enter a valid 10-digit mobile number.")
        if len(password) < 8 or not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
            raise ShopError("Password must be at least 8 characters with letters and numbers.")
        with self.db() as conn:
            if conn.execute("SELECT 1 FROM users WHERE email = ? OR mobile = ?", (email, mobile)).fetchone():
                raise ShopError("An account with this email or mobile number already exists. Please sign in.")
            cur = conn.execute("INSERT INTO users (name, email, mobile, password, wallet, created_at) "
                               "VALUES (?, ?, ?, ?, ?, ?)",
                               (name.title(), email, mobile, generate_password_hash(password), wallet, when or now()))
            return cur.lastrowid

    def login(self, login, password):
        login = login.strip().lower()
        with self.db() as conn:
            user = conn.execute("SELECT * FROM users WHERE email = ? OR mobile = ?", (login, login)).fetchone()
        if user is None or not check_password_hash(user["password"], password):
            raise ShopError("Incorrect email/mobile number or password.")
        if user["blocked"]:
            raise ShopError("This account has been blocked. Please contact Yo Delivery customer support.")
        return dict(user)

    def get_user(self, user_id):
        with self.db() as conn:
            row = conn.execute("SELECT id, name, email, mobile, wallet, created_at, is_admin, blocked FROM users WHERE id = ?",
                               (user_id,)).fetchone()
        return dict(row) if row else None

    def update_user(self, user_id, name, email, mobile):
        name, email, mobile = " ".join(name.split()), email.strip().lower(), mobile.strip()
        if len(name) < 2 or not EMAIL_RE.match(email) or not MOBILE_RE.match(mobile):
            raise ShopError("Please enter a valid name, email and 10-digit mobile number.")
        with self.db() as conn:
            if conn.execute("SELECT 1 FROM users WHERE (email = ? OR mobile = ?) AND id != ?",
                            (email, mobile, user_id)).fetchone():
                raise ShopError("This email or mobile number is used by another account.")
            conn.execute("UPDATE users SET name = ?, email = ?, mobile = ? WHERE id = ?",
                         (name.title(), email, mobile, user_id))

    def change_password(self, user_id, old, new):
        with self.db() as conn:
            row = conn.execute("SELECT password FROM users WHERE id = ?", (user_id,)).fetchone()
            if not check_password_hash(row["password"], old):
                raise ShopError("Current password is wrong.")
            if len(new) < 8 or not re.search(r"[A-Za-z]", new) or not re.search(r"\d", new):
                raise ShopError("New password must be at least 8 characters with letters and numbers.")
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
             ("name", "mobile", "line1", "line2", "city", "state", "pincode", "kind")}
        if len(d["name"]) < 2:
            raise ShopError("Please enter the receiver's name.")
        if not MOBILE_RE.match(d["mobile"]):
            raise ShopError("Please enter a valid 10-digit mobile number.")
        if len(d["line1"]) < 5:
            raise ShopError("Please enter house number and street.")
        if not d["city"] or d["state"] not in STATES:
            raise ShopError("Please enter the city and choose the state.")
        if not PINCODE_RE.match(d["pincode"]):
            raise ShopError("Please enter a valid 6-digit pincode.")
        d["kind"] = d["kind"] if d["kind"] in ("Home", "Work") else "Home"
        with self.db() as conn:
            first = not conn.execute("SELECT 1 FROM addresses WHERE user_id = ?", (user_id,)).fetchone()
            make_default = first or f.get("is_default") == "yes"
            if make_default:
                conn.execute("UPDATE addresses SET is_default = 0 WHERE user_id = ?", (user_id,))
            cur = conn.execute(
                "INSERT INTO addresses (user_id, name, mobile, line1, line2, city, state, pincode, kind, is_default) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, d["name"].title(), d["mobile"], d["line1"], d["line2"], d["city"].title(),
                 d["state"], d["pincode"], d["kind"], int(make_default)))
            return cur.lastrowid

    def set_default_address(self, user_id, address_id):
        with self.db() as conn:
            if conn.execute("SELECT 1 FROM addresses WHERE id = ? AND user_id = ?", (address_id, user_id)).fetchone():
                conn.execute("UPDATE addresses SET is_default = (id = ?) WHERE user_id = ?", (address_id, user_id))

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
            sql += " AND active = 1"
        if ids is not None:
            if not ids:
                return []
            sql += f" AND id IN ({','.join('?' * len(ids))})"
            args += list(ids)
        if category:
            sql += " AND category = ?"
            args.append(category)
        sql += {"popular": " ORDER BY rating_count DESC", "deals": " ORDER BY (mrp - price) * 1.0 / mrp DESC",
                "new": " ORDER BY created_at DESC", "rating": " ORDER BY rating_sum / rating_count DESC"}[order]
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
        sql, args = "SELECT * FROM products WHERE active = 1", []
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
            sql += " AND rating_sum / rating_count >= ?"
            args.append(float(min_rating))
        if min_discount:
            sql += " AND (mrp - price) * 100.0 / mrp >= ?"
            args.append(float(min_discount))
        sql += {"price_asc": " ORDER BY price ASC", "price_desc": " ORDER BY price DESC",
                "rating": " ORDER BY rating_sum / rating_count DESC, rating_count DESC",
                "discount": " ORDER BY (mrp - price) * 1.0 / mrp DESC", "new": " ORDER BY created_at DESC"
                }.get(sort, " ORDER BY rating_count DESC")
        with self.db() as conn:
            return [_product(r) for r in conn.execute(sql, args)]

    def brands(self, category=""):
        with self.db() as conn:
            if category:
                rows = conn.execute("SELECT DISTINCT brand FROM products WHERE active = 1 AND category = ? "
                                    "ORDER BY brand", (category,))
            else:
                rows = conn.execute("SELECT DISTINCT brand FROM products WHERE active = 1 ORDER BY brand")
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
        if not product.active and qty > 0:
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

    def price_summary(self, items, coupon="", user_id=None):
        mrp_total = sum(p.mrp * q for p, q in items)
        items_total = sum(p.price * q for p, q in items)
        coupon = coupon.strip().upper()
        coupon_discount, coupon_error = 0, ""
        if coupon:
            try:
                coupon_discount = self.coupon_value(coupon, items_total, user_id)
            except ShopError as e:
                coupon_error, coupon = str(e), ""
        free_from = self.setting("free_delivery_from")
        fee = 0 if items_total - coupon_discount >= free_from or not items else self.setting("delivery_fee")
        total = items_total - coupon_discount + fee
        return {"count": sum(q for _, q in items), "mrp_total": mrp_total, "items_total": items_total,
                "product_discount": mrp_total - items_total, "coupon": coupon, "coupon_discount": coupon_discount,
                "coupon_error": coupon_error, "delivery_fee": fee, "total": total,
                "savings": mrp_total - items_total + coupon_discount,
                "free_delivery_gap": max(0, free_from - (items_total - coupon_discount))}

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

    def place_order(self, user_id, address_id, payment, coupon="", upi_id="", when=None):
        """Create an order from the user's cart. Everything happens in ONE database transaction:
        stock is reduced, wallet is charged, the cart is emptied - or nothing changes at all."""
        cart = self.get_cart(user_id)
        items = self.cart_items(cart)
        if not items:
            raise ShopError("Your cart is empty.")
        address = next((a for a in self.addresses(user_id) if a["id"] == int(address_id or 0)), None)
        if address is None:
            raise ShopError("Please choose a delivery address.")
        summary = self.price_summary(items, coupon, user_id)
        if coupon and summary["coupon_error"]:
            raise ShopError(summary["coupon_error"])

        if payment == "cod":
            if summary["total"] > self.setting("cod_limit"):
                raise ShopError(f"Cash on Delivery is available for orders up to {rupees(self.setting('cod_limit'))}.")
            method, note = "Cash on Delivery", f"Pay {rupees(summary['total'])} in cash or UPI at your door"
        elif payment == "wallet":
            method, note = "Yo Pay Wallet", "Paid from Yo Pay balance"
        elif payment == "upi":
            if not UPI_RE.match(upi_id.strip()):
                raise ShopError("Please enter a valid UPI ID, for example name@okyo.")
            method, note = "UPI", f"Paid via UPI ({upi_id.strip().lower()}) - demo payment"
        else:
            raise ShopError("Please choose a payment method.")

        when = when or now()
        with self.db() as conn:
            for p, q in items:
                stock = conn.execute("SELECT stock FROM products WHERE id = ?", (p.id,)).fetchone()[0]
                if q > stock:
                    raise ShopError(f"Sorry, only {stock} left of {p.name[:40]}. Please update your cart.")
                conn.execute("UPDATE products SET stock = stock - ? WHERE id = ?", (q, p.id))
            if payment == "wallet":
                bal = conn.execute("SELECT wallet FROM users WHERE id = ?", (user_id,)).fetchone()[0]
                if bal < summary["total"]:
                    raise ShopError(f"Not enough Yo Pay balance ({rupees(bal)}). Choose another payment method.")
                conn.execute("UPDATE users SET wallet = wallet - ? WHERE id = ?", (summary["total"], user_id))
            number = "OD" + to_dt(when).strftime("%y%m%d") + "".join(random.choices("0123456789", k=8))
            addr_text = ", ".join(x for x in (address["line1"], address["line2"], address["city"],
                                              address["state"] + " - " + address["pincode"]) if x)
            expected = (to_dt(when) + timedelta(days=delivery_days(address["pincode"]) + 1)).strftime("%Y-%m-%d %H:%M:%S")
            cur = conn.execute(
                "INSERT INTO orders (number, user_id, ship_name, ship_mobile, ship_address, ship_pincode, mrp_total, "
                "items_total, coupon, coupon_discount, delivery_fee, total, payment_method, payment_note, placed_at, "
                "expected) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (number, user_id, address["name"], address["mobile"], addr_text, address["pincode"],
                 summary["mrp_total"], summary["items_total"], summary["coupon"], summary["coupon_discount"],
                 summary["delivery_fee"], summary["total"], method, note, when, expected))
            conn.executemany(
                "INSERT INTO order_items (order_id, product_id, slug, name, price, mrp, qty) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(cur.lastrowid, p.id, p.slug, p.name, p.price, p.mrp, q) for p, q in items])
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
                "SELECT step, MAX(at) at FROM order_events WHERE order_id = ? GROUP BY step", (order_id,))}

    def _order_step(self, order):
        """Current step of an order: set by the admin if he changed it, otherwise automatic by time."""
        events = self._events(order["id"])
        if events:
            return max(events)
        return self._step(order["placed_at"])

    def tracking(self, order):
        """List of tracking steps: (name, time text or '', done?)."""
        if order["cancelled_at"]:
            return [("Order placed", order["placed_at"], True), ("Cancelled", order["cancelled_at"], True)]
        events = self._events(order["id"])
        step = self._order_step(order)
        placed = to_dt(order["placed_at"])
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
        o["can_cancel"] = not o["cancelled_at"] and self._order_step(o) < 2   # before "Shipped"
        o["manual"] = bool(self._events(o["id"]))
        return o

    def cancel_order(self, user_id, number):
        o = self.get_order(user_id, number)
        if not o["can_cancel"]:
            raise ShopError("This order has already been shipped and cannot be cancelled.")
        with self.db() as conn:
            conn.execute("UPDATE orders SET cancelled_at = ? WHERE id = ?", (now(), o["id"]))
            for it in o["items"]:
                conn.execute("UPDATE products SET stock = stock + ? WHERE id = ?", (it["qty"], it["product_id"]))
            if o["payment_method"] in ("Yo Pay Wallet", "UPI"):
                conn.execute("UPDATE users SET wallet = wallet + ? WHERE id = ?", (o["total"], user_id))
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
                "SELECT i.slug, i.name, SUM(i.qty) qty, SUM(i.qty * i.price) sales FROM order_items i "
                "JOIN orders o ON o.id = i.order_id WHERE o.cancelled_at = '' GROUP BY i.product_id "
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
            names = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
            return [(n, conn.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0]) for n in names]

    def table_rows(self, name, limit=100, offset=0):
        """Columns + rows of one table. The table name is checked against the real list (no SQL injection)
        and password hashes are hidden."""
        if name not in dict(self.tables()):
            raise ShopError("Unknown table.")
        with self.db() as conn:
            cur = conn.execute(f'SELECT * FROM "{name}" ORDER BY rowid DESC LIMIT ? OFFSET ?', (limit, offset))
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
        return value if key == "announcement" else int(value)

    def settings(self):
        return {k: self.setting(k) for k in DEFAULT_SETTINGS}

    def update_settings(self, f):
        new = {}
        try:
            for key in ("free_delivery_from", "delivery_fee", "signup_wallet", "cod_limit"):
                new[key] = round(float(f.get(key, "")) * 100)
                if not 0 <= new[key] <= 10_000_000 * 100:
                    raise ValueError
        except (TypeError, ValueError):
            raise ShopError("Please enter valid rupee amounts (0 or more).")
        new["announcement"] = " ".join(f.get("announcement", "").split())[:160]
        with self.db() as conn:
            conn.executemany("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
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
            conn.execute("INSERT OR REPLACE INTO coupons (code, description, kind, amount, max_off, min_order, "
                         "first_order_only, active) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
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
        self.update_user(user_id, f.get("name", ""), f.get("email", ""), f.get("mobile", ""))
        is_admin, blocked = int(f.get("is_admin") == "yes"), int(f.get("blocked") == "yes")
        if user_id == acting_admin_id and (not is_admin or blocked):
            raise ShopError("You cannot remove your own admin rights or block yourself.")
        with self.db() as conn:
            conn.execute("UPDATE users SET is_admin = ?, blocked = ? WHERE id = ?", (is_admin, blocked, user_id))

    def admin_set_password(self, user_id, new):
        if len(new) < 8 or not re.search(r"[A-Za-z]", new) or not re.search(r"\d", new):
            raise ShopError("Password must be at least 8 characters with letters and numbers.")
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
            for table in ("addresses", "cart", "wishlist"):
                conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
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
            conn.execute("UPDATE products SET rating_sum = MAX(rating_sum - ?, 0), "
                         "rating_count = MAX(rating_count - 1, 0) WHERE id = ?", (r["rating"], r["product_id"]))
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
