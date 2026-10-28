"""
demo_data.py - fills an empty Yo Delivery database:
products (from catalog.py), one demo customer with addresses, past orders, a wishlist
and items in the cart, a few more customers with orders, the activity history,
and the admin account. All people here are fictional.
"""

import random
from datetime import datetime, timedelta

DEMO_EMAIL = "demo@example.com"
DEMO_MOBILE = "9876543210"
DEMO_PASSWORD = "Demo@1234"

ADMIN_EMAIL = "admin@example.com"
ADMIN_MOBILE = "9000000001"
ADMIN_PASSWORD = "Admin@1234"

OTHER_CUSTOMERS = [
    ("Priya Sharma", "priya.sharma@example.com", "9812000001", "Model Town", "Ambala City", "134003"),
    ("Arjun Mehta", "arjun.mehta@example.com", "9812000002", "Sector 17", "Chandigarh", "160017"),
    ("Neha Verma", "neha.verma@example.com", "9812000003", "Karol Bagh", "New Delhi", "110005"),
    ("Imran Khan", "imran.khan@example.com", "9812000004", "Civil Lines", "Ludhiana", "141001"),
    ("Sana Joseph", "sana.joseph@example.com", "9812000005", "Indiranagar", "Bengaluru", "560038"),
]
STATE_BY_PIN = {"13": "Haryana", "16": "Chandigarh", "11": "Delhi", "14": "Punjab", "56": "Karnataka"}


def _ago(days, hours=0, minutes=0):
    return (datetime.now() - timedelta(days=days, hours=hours, minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")


def ensure_admin(store):
    """Create the admin account if it does not exist yet (also for older databases)."""
    with store.db() as conn:
        row = conn.execute("SELECT id FROM users WHERE email = ?", (ADMIN_EMAIL,)).fetchone()
    if row:
        uid = row["id"]
    else:
        uid = store.register("Store Admin", ADMIN_EMAIL, ADMIN_MOBILE, ADMIN_PASSWORD, wallet=0, when=_ago(90))
    store.make_admin(uid)
    return uid


def seed(store):
    if not store.is_empty():
        ensure_admin(store)
        return
    store.load_catalog()
    rng = random.Random(11)
    ensure_admin(store)

    uid = store.register("Ravi Kumar", DEMO_EMAIL, DEMO_MOBILE, DEMO_PASSWORD, wallet=5000 * 100, when=_ago(60))
    store.log(uid, "Signed up", "New account with email " + DEMO_EMAIL, "49.36.12.8", when=_ago(60))
    home = store.add_address(uid, {"name": "Ravi Kumar", "mobile": DEMO_MOBILE, "line1": "House 214, Sector 9",
                                   "line2": "Near City Park", "city": "Ambala Cantt", "state": "Haryana",
                                   "pincode": "133001", "kind": "Home"})
    store.add_address(uid, {"name": "Ravi Kumar", "mobile": DEMO_MOBILE, "line1": "MMICT & BM Block, MMDU Campus",
                            "line2": "Mullana", "city": "Ambala", "state": "Haryana", "pincode": "133207",
                            "kind": "Work"})

    def buy(user, address, items, days_ago, payment="cod", coupon="", upi="", ip="49.36.12.8"):
        cart = {store.get_product(slug=slug).id: qty for slug, qty in items}
        store.save_cart(user, cart)
        when = _ago(days_ago, rng.randint(0, 9), rng.randint(0, 59))
        store.log(user, "Logged in", "", ip, when=when)
        number = store.place_order(user, address, payment, coupon, upi, when=when)
        total = store.get_order(user, number)["total"]
        store.log(user, "Order placed", f"{number} - {len(items)} item(s) - {payment.upper()} - "
                                        f"₹{total // 100:,}", ip, when=when)
        return number

    buy(uid, home, [("book-python", 1), ("trailpack-30l", 1)], 34, "upi", upi="ravi.kumar@okyo")
    buy(uid, home, [("farmfresh-basmati-5kg", 1), ("golddrop-sunflower-1l", 2), ("teatime-assam-500g", 1)], 12)
    buy(uid, home, [("soundwave-buds", 1)], 5, "wallet")
    cancelled = buy(uid, home, [("urbanfit-hoodie", 1)], 3)
    with store.db() as conn:
        conn.execute("UPDATE orders SET cancelled_at = ? WHERE number = ?", (_ago(3, 1), cancelled))
        conn.execute("UPDATE products SET stock = stock + 1 WHERE slug = 'urbanfit-hoodie'")
    store.log(uid, "Order cancelled", cancelled, "49.36.12.8", when=_ago(3, 1))

    for slug in ("nova-pro-12", "chrono-steel-silver", "stridex-runner-teal"):
        store.toggle_wishlist(uid, store.get_product(slug=slug).id)
    store.save_cart(uid, {store.get_product(slug="pulsefit-s2").id: 1,
                          store.get_product(slug="farmfresh-apples-1kg").id: 2})

    # ----- more customers so the admin dashboard has real numbers -----
    slugs = [p["slug"] for p in __import__("catalog").PRODUCTS]
    for i, (name, email, mobile, area, city, pin) in enumerate(OTHER_CUSTOMERS):
        joined = 40 - i * 6
        cid = store.register(name, email, mobile, "Shop@2026", when=_ago(joined))
        ip = f"106.{rng.randint(10, 220)}.{rng.randint(1, 250)}.{rng.randint(1, 250)}"
        store.log(cid, "Signed up", "New account with email " + email, ip, when=_ago(joined))
        addr = store.add_address(cid, {"name": name, "mobile": mobile, "line1": f"House {rng.randint(1, 400)}, {area}",
                                       "city": city, "state": STATE_BY_PIN[pin[:2]], "pincode": pin, "kind": "Home"})
        store.log(cid, "Address added", f"{city} {pin}", ip, when=_ago(joined))
        for _ in range(rng.randint(2, 4)):
            items = [(s, rng.choice([1, 1, 1, 2])) for s in rng.sample(slugs, rng.randint(1, 3))]
            buy(cid, addr, items, rng.randint(0, 13) if rng.random() < 0.8 else rng.randint(14, 30),
                rng.choice(["cod", "cod", "upi"]), upi=email.split("@")[0] + "@okyo", ip=ip)
        store.save_cart(cid, {})
    with store.db() as conn:                       # a few items running low, for the admin alert
        conn.execute("UPDATE products SET stock = 4 WHERE slug = 'nova-pro-12'")
        conn.execute("UPDATE products SET stock = 7 WHERE slug = 'chrono-rose-women'")
    store.log(None, "Login failed", "Wrong password for unknown@example.com", "185.220.101.4", when=_ago(1, 4))
    store.log(uid, "Login failed", "Wrong password for " + DEMO_EMAIL, "49.36.12.8", when=_ago(0, 6))
