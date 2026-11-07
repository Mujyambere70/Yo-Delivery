"""
demo_data.py - fills an empty Yo Delivery database:
products (from catalog.py), one demo customer with addresses, past orders, a wishlist
and items in the cart, more customers in India and abroad (Russia, Rwanda, France, China,
UAE, Spain) with orders in their own currency and language, one approved marketplace seller
with sales, one seller application waiting for approval, product views for recommendations,
a return request, the activity history and the admin account.
All customers and shops here are fictional; all emails use example.com, so no message is
ever sent to them.
"""

import random
from datetime import datetime, timedelta

import demo_v3
import demo_v4
import demo_v5
import demo_v6
import demo_v7

DEMO_EMAIL = "demo@example.com"
DEMO_MOBILE = "9876543210"
DEMO_PASSWORD = "Demo@1234"

ADMIN_EMAIL = "admin@example.com"
ADMIN_MOBILE = "9000000001"
ADMIN_PASSWORD = "Admin@1234"
ADMIN_NAME = "Jean Michel Mujyambere"          # founder and store administrator (the developer)

SELLER_EMAIL = "seller@example.com"
SELLER_PASSWORD = "Seller@1234"

# name, email, mobile (international form), country, language, address line, city, post code
WORLD_CUSTOMERS = [
    ("Anna Smirnova", "anna.smirnova@example.com", "+7 916 555 0142", "RU", "ru", "ul. Tverskaya 12, kv. 8", "Moscow", "125009"),
    ("Amani Uwase", "amani.uwase@example.com", "+250 788 555 014", "RW", "rw", "KG 11 Ave, house 27", "Kigali", ""),
    ("Camille Laurent", "camille.laurent@example.com", "+33 6 55 50 14 20", "FR", "fr", "18 rue des Lilas", "Lyon", "69003"),
    ("Li Wei", "li.wei@example.com", "+86 138 5550 1420", "CN", "zh", "Room 1203, 88 Century Avenue", "Shanghai", "200120"),
    ("Omar Al Mansoori", "omar.almansoori@example.com", "+971 50 555 0142", "AE", "ar", "Villa 14, Street 22, Al Barsha", "Dubai", ""),
    ("Lucia Fernandez", "lucia.fernandez@example.com", "+34 612 555 014", "ES", "es", "Calle Mayor 41, 2B", "Madrid", "28013"),
]

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
    """Make sure the shop has an admin (also for older databases). Never creates a second one:
    if the admin already changed their email or mobile, the existing account is kept as it is."""
    from core import ShopError
    with store.db() as conn:
        row = conn.execute("SELECT id FROM users WHERE is_admin = 1 ORDER BY id LIMIT 1").fetchone()
        if row is None:
            row = conn.execute("SELECT id FROM users WHERE email = ? OR mobile = ?", (ADMIN_EMAIL, ADMIN_MOBILE)).fetchone()
    if row:
        uid = row["id"]
    else:
        try:
            uid = store.register(ADMIN_NAME, ADMIN_EMAIL, ADMIN_MOBILE, ADMIN_PASSWORD, wallet=0, when=_ago(90))
        except ShopError:                                   # another process created it at the same moment
            return None
    store.make_admin(uid)
    return uid


def seed(store):
    if not store.is_empty():
        ensure_admin(store)
        demo_v3.ensure_v3(store)            # a version 2 database gets the new demo content once
        demo_v4.ensure_v4(store)            # and the Yo Tickets demo content (version 4) once
        demo_v5.ensure_v5(store)            # and the whole-world demo content (version 5) once
        demo_v6.ensure_v6(store)            # and the staff of Yo Delivery (version 6) once
        demo_v7.ensure_v7(store)            # and pay, invoices, partners, help desk and numbers (version 7) once
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

    if store.env.get("FX_ON_SEED", "1") != "0":
        try:                        # today's real exchange rates, when the internet is reachable
            store.refresh_fx_rates()
        except Exception:
            pass
    rates = store.rates()           # without rates the orders simply stay in rupees

    # ----- customers in other countries, each with their own language -----
    world_ids = {}
    for i, (name, email, mobile, country, lang, line1, city, postcode) in enumerate(WORLD_CUSTOMERS):
        joined = 30 - i * 3
        cid = store.register(name, email, mobile, "Shop@2026", when=_ago(joined), country=country, lang=lang)
        world_ids[email] = cid
        ip = f"{rng.randint(31, 213)}.{rng.randint(10, 220)}.{rng.randint(1, 250)}.{rng.randint(1, 250)}"
        store.log(cid, "Signed up", "New account with email " + email, ip, when=_ago(joined))
        addr = store.add_address(cid, {"name": name, "mobile": mobile, "line1": line1, "city": city,
                                       "pincode": postcode, "country": country, "kind": "Home"})
        user = store.get_user(cid)
        for k in range(2):
            items = [(sl, 1) for sl in rng.sample(slugs, rng.randint(1, 2))]
            store.save_cart(cid, {store.get_product(slug=sl).id: q for sl, q in items})
            when = _ago((4 if i == 0 else rng.randint(1, 20)) if k == 0 else rng.randint(0, 2),
                        rng.randint(0, 9), rng.randint(0, 59))
            store.log(cid, "Logged in", "", ip, when=when)
            number = store.place_order(cid, addr, "card", when=when, currency=user["currency"], rates=rates)
            with store.db() as conn:                # the card payment went through the test gateway
                ref = "pi_demo_" + number[-8:]
                conn.execute("UPDATE orders SET payment_status = 'Paid', paid_at = ?, payment_ref = ?, "
                             "payment_note = 'Card - Yo Delivery test gateway' WHERE number = ?", (when, ref, number))
                o = conn.execute("SELECT currency, total_local FROM orders WHERE number = ?", (number,)).fetchone()
                conn.execute("INSERT INTO payments (order_number, gateway, payment_ref, currency, amount, status, "
                             "message, created_at) VALUES (?, 'test', ?, ?, ?, 'Succeeded', ?, ?)",
                             (number, ref, o["currency"], o["total_local"], "Test card ending 4242", when))
            store.log(cid, "Order placed", f"{number} - {len(items)} item(s) - CARD", ip, when=when)
        store.save_cart(cid, {})

    # ----- marketplace: one approved seller with products and sales, one application waiting -----
    sid_user = store.register("Meera Kapoor", SELLER_EMAIL, "9812000099", SELLER_PASSWORD, when=_ago(45))
    store.log(sid_user, "Signed up", "New account with email " + SELLER_EMAIL, "49.36.40.17", when=_ago(45))
    shop = store.apply_seller(sid_user, {"shop_name": "HomeEase Kitchen Studio",
                                         "about": "Kitchen tools and lamps for Indian homes: cookers, kettles, pans "
                                                  "and LED lamps, packed and sent from Ambala within one day.",
                                         "country": "IN", "city": "Ambala", "payout_details": "meera.kapoor@okyo"})
    store.decide_seller(shop, "Approved")
    with store.db() as conn:
        conn.execute("UPDATE sellers SET created_at = ?, decided_at = ? WHERE id = ?", (_ago(44), _ago(43), shop))
        conn.execute("UPDATE products SET seller_id = ?, approved = 1 WHERE slug IN "
                     "('homeease-cooker', 'homeease-kettle', 'homeease-pan', 'glow-led-lamp')", (shop,))
    seller_addr = store.add_address(sid_user, {"name": "Meera Kapoor", "mobile": "9812000099",
                                               "line1": "Shop 6, Rai Market", "city": "Ambala Cantt",
                                               "state": "Haryana", "pincode": "133001", "kind": "Work"})
    for cid_email, slug_list, days in (("priya.sharma@example.com", [("homeease-cooker", 1)], 9),
                                       ("arjun.mehta@example.com", [("homeease-kettle", 1), ("glow-led-lamp", 1)], 6),
                                       ("neha.verma@example.com", [("homeease-pan", 2)], 1)):
        with store.db() as conn:
            cid = conn.execute("SELECT id FROM users WHERE email = ?", (cid_email,)).fetchone()[0]
            addr = conn.execute("SELECT id FROM addresses WHERE user_id = ?", (cid,)).fetchone()[0]
        buy(cid, addr, slug_list, days, "upi", upi=cid_email.split("@")[0] + "@okyo")
        store.save_cart(cid, {})
    if store.seller_balance(shop)["balance"] >= 50000:
        store.record_payout(shop, 500, "UPI meera.kapoor@okyo - demo payout", by=ADMIN_NAME)
    pending = store.register("Lucas Moreau", "lucas.moreau@example.com", "+33 6 55 50 99 31", "Shop@2026",
                             when=_ago(2), country="FR", lang="fr")
    store.apply_seller(pending, {"shop_name": "Atelier Lumiere", "about": "Handmade candles and table lamps "
                                 "from a small workshop in Lyon, shipped worldwide.", "country": "FR",
                                 "city": "Lyon", "payout_details": "FR76 0000 0000 0000 0000 0000 000"})

    # ----- what visitors looked at (for "customers also viewed" and "inspired by your views") -----
    groups = [["nova-x5-blue", "nova-x5-black", "soundwave-buds", "powermax-20000", "pulsefit-s2"],
              ["stridex-runner-teal", "stridex-runner-red", "urbanfit-tee-white", "trailpack-30l"],
              ["homeease-cooker", "homeease-kettle", "homeease-pan", "glow-led-lamp"],
              ["book-python", "book-flask", "book-dsa", "zentra-air-13"],
              ["chrono-steel-silver", "chrono-classic-gold", "shades-aviator"]]
    ids = {p["slug"]: store.get_product(slug=p["slug"]).id for p in __import__("catalog").PRODUCTS}
    with store.db() as conn:
        rows = []
        for v in range(60):
            g = rng.choice(groups)
            for sl in rng.sample(g, rng.randint(2, len(g))):
                rows.append((f"demo-visitor-{v}", None, ids[sl], _ago(rng.randint(0, 20), rng.randint(0, 23))))
        for sl in ("nova-pro-12", "soundwave-700", "nova-x5-blue", "chrono-steel-silver"):
            rows.append(("demo-ravi", uid, ids[sl], _ago(1, rng.randint(0, 23))))
        conn.executemany("INSERT INTO views (visitor, user_id, product_id, at) VALUES (?, ?, ?, ?)", rows)

    # ----- one delivered order with a return request (for the admin's Returns page) -----
    for cid in world_ids.values():
        with store.db() as conn:
            numbers = [r[0] for r in conn.execute("SELECT number FROM orders WHERE user_id = ? ORDER BY placed_at",
                                                  (cid,))]
        o = next((store.get_order(cid, n) for n in numbers if store.return_window(store.get_order(cid, n))[0]), None)
        if o:
            store.request_return(cid, o["number"], "Size or fit is not right", "The item is smaller than I expected.")
            break

    # every seeded order 'tells' its customer only about its current step (one message, not a flood of old ones)
    with store.db() as conn:
        orders = [dict(r) for r in conn.execute("SELECT * FROM orders")]
        for o in orders:
            conn.execute("UPDATE orders SET notified_step = ? WHERE id = ?", (store._order_step(o) - 1, o["id"]))
    store.sync_order_updates()          # in-site messages only: demo emails end with example.com and are skipped
    demo_v3.ensure_v3(store)            # version 3: couriers, shipments, blog, quiz, Yo Pay, task boards
    demo_v4.ensure_v4(store)            # version 4: Yo Tickets (conductor, tickets, posts)
    demo_v5.ensure_v5(store)            # version 5: the whole world (quiz languages, chat, product texts, ...)
    demo_v6.ensure_v6(store)            # version 6: offices, workers, shifts and tasks
    demo_v7.ensure_v7(store)            # version 7: pay, invoices, partners, help desk and numbers
