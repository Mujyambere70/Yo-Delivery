"""
demo_v7.py - the demo content of version 7, added once to a new database or to a database of an earlier version
(the same "claim a settings key" rule as demo_v3.py ... demo_v6.py). All people, firms and numbers are fictional.

    * contracts of the demo workers (pay in rupees, Rwandan francs and roubles - example amounts), holiday days,
      safety courses (one worker misses one), a worker who helps in another office, the places of the offices
    * a payroll of Rwanda that was made, approved and paid (test mode) and a payroll of India that waits for the
      second person: Kavya Iyer (finance) made it, the administrator approves it
    * a courier partner (Highland Express) with one parcel; its secret is not kept anywhere readable
    * the demo seller's GSTIN (a made-up number with a correct check character) and the tax invoices of the orders
    * questions to the help desk (one answered, one urgent and late), a parcel claim, an order in the safety check
    * 8 weeks of hub numbers (marked "demo") and the data warehouse
"""

from datetime import date, timedelta

import demo_data
import invoices as INV
from core import ShopError, now

# email, kind, pay a month (in the office's money), holiday days, last day (None: permanent)
CONTRACTS = [
    ("anita.verma@example.com", "permanent", 180000, 24, None),
    ("kavya.iyer@example.com", "permanent", 95000, 24, None),
    ("rohit.malhotra@example.com", "permanent", 120000, 24, None),
    ("sunil.kumar@example.com", "permanent", 28000, 18, None),
    ("neha.gill@example.com", "permanent", 32000, 18, None),
    ("arjun.mehta@example.com", "fixed", 35000, 18, 30),          # ends in 30 days: shown to his manager
    ("claudine.uwimana@example.com", "permanent", 1800000, 21, None),
    ("eric.habimana@example.com", "permanent", 250000, 18, None),
    ("jeanbosco.niyonzima@example.com", "permanent", 400000, 18, None),
    ("diane.mukamana@example.com", "part", 280000, 18, None),
    ("olga.smirnova@example.com", "permanent", 250000, 28, None),
    ("dmitry.volkov@example.com", "permanent", 150000, 28, None),
    ("irina.petrova@example.com", "permanent", 70000, 28, None),
]

# email, course, months ago
TRAININGS = [
    ("sunil.kumar@example.com", "hub_safety", 3), ("irina.petrova@example.com", "hub_safety", 14),
    ("neha.gill@example.com", "data_protection", 5), ("arjun.mehta@example.com", "data_protection", 2),
    ("kavya.iyer@example.com", "money_fraud", 7), ("jeanbosco.niyonzima@example.com", "road_safety", 9),
    ("jeanbosco.niyonzima@example.com", "hub_safety", 9),
    # Eric Habimana (Kigali hub) has no "Safety at the hub" yet: the training page lists him
]

# office name, lat, lng, distance in metres, people may clock in from elsewhere
PLACES = [
    ("Ambala hub", 30.3375, 76.8562, 300, False),
    ("Chandigarh support centre", 30.7046, 76.7179, 300, True),
    ("Kigali hub", -1.9686, 30.1395, 300, False),
    ("Krasnoyarsk warehouse", 56.0184, 92.8672, 400, False),
]

PARTNER = ("Highland Express", "RW KE UG")
SELLER_GSTIN = "06ABCDE1234F1Z"          # 06 = Haryana; a made-up number, the check character is added below


def ensure_v7(store):
    with store.db() as conn:
        claimed = conn.execute("INSERT INTO settings (key, value) VALUES ('v7_demo', ?) ON CONFLICT (key) DO NOTHING",
                               (now(),)).rowcount
        if not claimed:
            return False
    for part in (_people, _payroll, _partner, _seller_and_invoices, _help_desk, _safety_check, _numbers):
        try:
            part(store)
        except (ShopError, LookupError, TypeError, ValueError):     # the demo goes on without this part
            pass
    return True


def _admin(store):
    with store.db() as conn:
        r = conn.execute("SELECT id FROM users WHERE is_admin = 1 ORDER BY id LIMIT 1").fetchone()
    return r[0] if r else None


def _staff(store):
    with store.db() as conn:
        return {r["email"]: (r["sid"], r["uid"], r["office_id"]) for r in conn.execute(
            "SELECT u.email, u.id AS uid, s.id AS sid, s.office_id FROM staff s JOIN users u ON u.id = s.user_id")}


def _office_ids(store):
    with store.db() as conn:
        return {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM offices")}


def _people(store):
    admin = _admin(store)
    staff, offices = _staff(store), _office_ids(store)
    if not admin or not staff:
        return
    store.ensure_office_locations()
    for name, lat, lng, fence, remote in PLACES:
        if name in offices:
            store.set_office_location(admin, offices[name], lat, lng, fence, remote_ok=remote)
    start = date(date.today().year - 1, 1, 1)
    for email, kind, pay, leave, ends_in in CONTRACTS:
        if email in staff:
            data = {"kind": kind, "start_day": start.isoformat(), "pay_kind": "month", "pay_amount": str(pay),
                    "hours_week": "25" if kind == "part" else "40", "leave_days": str(leave)}
            if ends_in:
                data["end_day"] = (date.today() + timedelta(days=ends_in)).isoformat()
            store.save_contract(admin, staff[email][0], data)
    for email, course, months in TRAININGS:
        if email in staff:
            store.add_training(admin, staff[email][0], course, (date.today() - timedelta(days=30 * months)).isoformat(),
                               f"DEMO-{course[:3].upper()}-{staff[email][0]:03d}")
    jb = staff.get("jeanbosco.niyonzima@example.com")
    if jb and "Gakenke office" in offices:                 # helps the Gakenke office for the next 10 days
        store.assign_helper_office(admin, jb[0], offices["Gakenke office"], date.today().isoformat(),
                                   (date.today() + timedelta(days=10)).isoformat())


def _months_back(n):
    d = date.today().replace(day=1)
    for _ in range(n):
        d = (d - timedelta(days=1)).replace(day=1)
    return d.strftime("%Y-%m")


def _payroll(store):
    admin, staff = _admin(store), _staff(store)
    kavya = staff.get("kavya.iyer@example.com")
    if not admin or not kavya:
        return
    rw = store.create_payroll(kavya[1], "RW", _months_back(2))       # made by finance, approved by the administrator,
    store.approve_payroll(admin, rw)                                 # paid in test mode
    store.pay_payroll(kavya[1], rw)
    store.create_payroll(kavya[1], "IN", _months_back(1))            # waits for the second person (the administrator)


def _partner(store):
    admin = _admin(store)
    store.add_partner(admin, *PARTNER)                               # the secret is shown once; the demo throws it away
    pid = store.partners()[0]["id"]
    with store.db() as conn:
        s = conn.execute("SELECT number FROM shipments WHERE status = 'Booked' AND (courier_id IS NULL OR courier_id = 0) "
                         "AND to_country = 'RW' ORDER BY id LIMIT 1").fetchone() or \
            conn.execute("SELECT number FROM shipments WHERE status = 'Booked' AND (courier_id IS NULL OR courier_id = 0) "
                         "ORDER BY id LIMIT 1").fetchone()
    if s is None:                                                    # the demo parcels are delivered: book one more
        demo = store.find_user(demo_data.DEMO_EMAIL)
        number = store.book_shipment(demo["id"], {
            "from_name": "Ravi Kumar", "from_mobile": demo_data.DEMO_MOBILE, "from_address": "House 214, Sector 9",
            "from_city": "Ambala Cantt", "payment": "wallet", "service": "standard", "kind": "parcel", "weight": "1",
            "contents": "Two books", "to_name": "Aline Uwase", "to_mobile": "+250788000321",
            "to_address": "KG 11 Ave, house 4", "to_city": "Kigali", "to_country": "RW", "time_window": "12-15",
            "customs_reason": "gift", "customs_desc": ["Books"], "customs_qty": ["2"], "customs_value": ["900"],
            "customs_hs": ["490199"]})
        with store.db() as conn:                                     # demo: the parcel waits at the hub for the partner
            conn.execute("UPDATE shipments SET status = 'Booked', courier_id = NULL WHERE number = ?", (number,))
        s = {"number": number}
    if s:
        store.hand_to_partner(admin, s["number"], pid)
        p = next(x for x in store.partners() if x["id"] == pid)
        at = now().replace(" ", "T") + "Z"
        store.partner_events_in(p, {"events": [{"event_id": "demo-1", "number": s["number"], "type": "picked_up",
                                                "at": at, "place": "Kigali", "note": "Demo event"}]})


def _seller_and_invoices(store):
    seller = store.find_user(demo_data.SELLER_EMAIL)
    if seller:
        store.save_seller_tax_id(seller["id"], SELLER_GSTIN + INV.gstin_check_char(SELLER_GSTIN))
    with store.db() as conn:
        ids = [r[0] for r in conn.execute("SELECT id FROM orders WHERE cancelled_at IS NULL OR cancelled_at = '' "
                                          "ORDER BY id")]
    for oid in ids:
        try:
            store.issue_invoices(oid)
        except ShopError:
            pass


def _help_desk(store):
    demo = store.find_user(demo_data.DEMO_EMAIL)
    staff = _staff(store)
    if not demo:
        return
    uid = demo["id"]
    with store.db() as conn:
        delivered = conn.execute("SELECT number, user_id FROM shipments WHERE status = 'Delivered' AND delivered_at >= ? "
                                 "ORDER BY user_id = ? DESC, delivered_at DESC LIMIT 1",
                                 ((date.today() - timedelta(days=5)).isoformat(), uid)).fetchone()
        order = conn.execute("SELECT number FROM orders WHERE user_id = ? ORDER BY id DESC LIMIT 1", (uid,)).fetchone()
    yesterday = (date.today() - timedelta(days=1)).isoformat() + " 10:15:00"
    t1 = store.open_support_ticket(uid, "order", "Can I change the delivery address?",
                                   "Hello, I will be at my office tomorrow. Can the parcel come to Sector 22 instead?",
                                   order["number"] if order else "", when=yesterday)
    neha = staff.get("neha.gill@example.com")
    if neha:
        store.take_support_ticket(neha[1], t1)
        store.support_reply(neha[1], t1, "Hello Ravi, yes - please open the order and choose 'Change address' before "
                                         "it leaves the hub. I have added a note for the courier.")
    store.open_support_ticket(uid, "money", "Money taken twice?",
                              "My Yo Pay shows two payments for one order. Please check.", "", urgent=True,
                              when=(date.today() - timedelta(days=1)).isoformat() + " 08:00:00")
    if delivered:
        store.file_claim(delivered["user_id"], delivered["number"], "damaged", "The box was wet and the cover of the book is torn.",
                         "300")


def _safety_check(store):
    """A new account that places four orders in one hour, the last one big and with cash on delivery: the last order
    waits for a person (new account 25 + four orders in an hour 25 + big amount 20 + big cash order 15 = 85)."""
    if store.find_user("pavel.demo@example.com"):
        return
    uid = store.register("Pavel Orlov", "pavel.demo@example.com", "9812000199", "Demo@1234")
    addr = store.add_address(uid, {"name": "Pavel Orlov", "mobile": "9812000199", "line1": "Flat 3, Mall Road",
                                   "city": "Ambala Cantt", "state": "Haryana", "pincode": "133001", "kind": "Home"})
    own = [p for p in store.products() if not p.seller_id and p.stock >= 3]
    for p in sorted(own, key=lambda p: p.price)[:3]:                 # three small orders first
        store.save_cart(uid, {str(p.id): 1})
        store.place_order(uid, addr, "cod")
    big = max((p for p in own if p.price <= 25000_00), key=lambda p: p.price)
    store.save_cart(uid, {str(big.id): -(-20000_00 // big.price)})
    store.place_order(uid, addr, "cod")


def _numbers(store):
    store.seed_demo_history()
    store.build_warehouse()
