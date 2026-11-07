"""
demo_v6.py - the demo content of version 6 (the people who run Yo Delivery), added once to a new database or to
a database of an earlier version (the same "claim a settings key" rule as demo_v3.py ... demo_v5.py).

    * an office tree: head office -> India, Rwanda, Russia -> regional and city offices -> hubs, a warehouse, a
      support centre and the office of a transport company
    * 13 workers with roles, managers and abilities (all fictional, example.com addresses: never emailed or texted;
      the password of every demo worker is Staff@2026)
    * the shift plan of this week, clock-ins of the past days (on time, late, absent), time off, tasks
    * the staff log fills itself while the demo is made
"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from werkzeug.security import generate_password_hash

from core import ShopError, now

PASSWORD = "Staff@2026"

# key, parent key, name, kind, country, place
OFFICES = [
    ("in", "head", "India office", "country", "IN", "New Delhi"),
    ("hr", "in", "Haryana & Punjab regional office", "region", "IN", "Chandigarh"),
    ("amb", "hr", "Ambala hub", "hub", "IN", "Ambala Cantt"),
    ("chd", "hr", "Chandigarh support centre", "support", "IN", "Chandigarh"),
    ("rw", "head", "Rwanda office", "country", "RW", "Kigali"),
    ("kgl", "rw", "Kigali hub", "hub", "RW", "Kigali"),
    ("gak", "rw", "Gakenke office", "city", "RW", "rw6833"),
    ("ru", "head", "Russia office", "country", "RU", "Moscow"),
    ("kja", "ru", "Krasnoyarsk office", "city", "RU", "g1502026"),
    ("kjw", "kja", "Krasnoyarsk warehouse", "warehouse", "RU", "g1502026"),
]

# name, email, mobile, office key, kind, role, manager
WORKERS = [
    ("Anita Verma", "anita.verma@example.com", "9812000101", "in", "office", "country_manager", True),
    ("Kavya Iyer", "kavya.iyer@example.com", "9812000102", "in", "office", "finance", False),
    ("Rohit Malhotra", "rohit.malhotra@example.com", "9812000103", "hr", "office", "office_manager", True),
    ("Sunil Kumar", "sunil.kumar@example.com", "9812000104", "amb", "hub", "hub", False),
    ("Neha Gill", "neha.gill@example.com", "9812000105", "chd", "support", "support", False),
    ("Arjun Mehta", "arjun.mehta@example.com", "9812000106", "chd", "support", "moderator", False),
    ("Claudine Uwimana", "claudine.uwimana@example.com", "0788000107", "rw", "office", "country_manager", True),
    ("Eric Habimana", "eric.habimana@example.com", "0788000108", "kgl", "hub", "hub", False),
    ("Jean Bosco Niyonzima", "jeanbosco.niyonzima@example.com", "0788000109", "kgl", "hub", "courier_lead", True),
    ("Diane Mukamana", "diane.mukamana@example.com", "0788000110", "gak", "support", "support", False),
    ("Olga Smirnova", "olga.smirnova@example.com", "9130000111", "ru", "office", "country_manager", True),
    ("Dmitry Volkov", "dmitry.volkov@example.com", "9130000112", "kja", "office", "office_manager", True),
    ("Irina Petrova", "irina.petrova@example.com", "9130000113", "kjw", "hub", "hub", False),
]

TASKS = [
    ("rohit.malhotra@example.com", "sunil.kumar@example.com", "Count the parcels at gate 2 before 18:00", "High", 0, "In progress"),
    ("rohit.malhotra@example.com", "neha.gill@example.com", "Call the customers whose parcels are late", "Normal", 1, "To do"),
    ("anita.verma@example.com", "kavya.iyer@example.com", "Check the seller payouts of last week", "Normal", -2, "To do"),
    ("claudine.uwimana@example.com", "eric.habimana@example.com", "Label the shelves for Northern Province", "Normal", 2, "Done"),
    ("claudine.uwimana@example.com", "diane.mukamana@example.com", "Visit three new sellers in Gakenke", "Low", 5, "To do"),
    ("dmitry.volkov@example.com", "irina.petrova@example.com", "Check the winter packing material", "Urgent", 0, "To do"),
]


def ensure_v6(store):
    with store.db() as conn:
        claimed = conn.execute("INSERT INTO settings (key, value) VALUES ('v6_demo', ?) ON CONFLICT (key) DO NOTHING",
                               (now(),)).rowcount
        if not claimed:
            return False
    try:
        _people(store)
    except (ShopError, LookupError, TypeError, ValueError):     # the demo goes on without this part
        pass
    return True


def _admin(store):
    with store.db() as conn:
        r = conn.execute("SELECT id FROM users WHERE is_admin = 1 ORDER BY id LIMIT 1").fetchone()
    return r[0] if r else None


def _local_to_server(day, hm, tz, minutes=0):
    local = datetime.fromisoformat(f"{day} {hm}").replace(tzinfo=ZoneInfo(tz)) + timedelta(minutes=minutes)
    return local.astimezone().replace(tzinfo=None)


def _people(store):
    admin = _admin(store)
    if not admin:
        return
    store.ensure_head_office()
    with store.db() as conn:
        head = conn.execute("SELECT id FROM offices WHERE kind = 'head'").fetchone()[0]
    ids = {"head": head}
    for key, parent, name, kind, country, place in OFFICES:
        ids[key] = store.add_office(admin, ids[parent], name, kind, country, place)
    staff = {}
    for name, email, mobile, office, kind, role, manager in WORKERS:
        sid, _ = store.add_worker(admin, {"email": email, "name": name, "mobile": mobile, "office_id": ids[office],
                                          "kind": kind, "role": role, "is_manager": manager})
        staff[email] = sid
    with store.db() as conn:
        for email in staff:
            conn.execute("UPDATE users SET password = ? WHERE email = ?", (generate_password_hash(PASSWORD), email))
        conn.execute("UPDATE users SET country = 'RW', currency = 'RWF' WHERE email LIKE '%@example.com' AND email IN "
                     f"({', '.join('?' * 4)})", ("claudine.uwimana@example.com", "eric.habimana@example.com",
                                                   "jeanbosco.niyonzima@example.com", "diane.mukamana@example.com"))
        conn.execute("UPDATE users SET country = 'RU', currency = 'RUB', lang = 'ru' WHERE email IN (?, ?, ?)",
                     ("olga.smirnova@example.com", "dmitry.volkov@example.com", "irina.petrova@example.com"))
    _rota(store, admin, staff)
    _tasks(store, staff)
    with store.db() as conn:
        uid = conn.execute("SELECT id FROM users WHERE email = 'neha.gill@example.com'").fetchone()[0]
    start = date.today() + timedelta(days=10)
    store.request_leave(uid, start.isoformat(), (start + timedelta(days=2)).isoformat(), "Sister's wedding")
    try:
        store.build_content_similar()                 # "similar products" from texts and pictures
    except Exception:
        pass
    try:                                              # a real timetable (GTFS) for Kigali - Musanze
        import os
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "gtfs", "sample_rwanda.zip"), "rb") as f:
            store.import_gtfs(admin, f.read())
    except (OSError, ShopError):
        pass


def _rota(store, admin, staff):
    """This week: 09:00-17:00 for the hub and support workers; clock-ins on the days before today."""
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    late = {("sunil.kumar@example.com", 1), ("irina.petrova@example.com", 0)}
    absent = {("eric.habimana@example.com", 1)}
    for email in ("sunil.kumar@example.com", "neha.gill@example.com", "arjun.mehta@example.com",
                  "eric.habimana@example.com", "diane.mukamana@example.com", "irina.petrova@example.com"):
        sid = staff[email]
        w = store.staff_member(sid)
        with store.db() as conn:
            o = dict(conn.execute("SELECT * FROM offices WHERE id = ?", (w["office_id"],)).fetchone())
        tz = store.office_tz(o) or "Asia/Kolkata"
        start = "08:00" if o["kind"] in ("hub", "warehouse") else "09:00"
        end = "16:00" if start == "08:00" else "17:00"
        with store.db() as conn:                         # planned for the past days too (the demo week)
            for i in range(6):
                d = (monday + timedelta(days=i)).isoformat()
                conn.execute("INSERT INTO staff_rota (staff_id, office_id, day, start_hm, end_hm, created_by, created_at) "
                             "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (staff_id, day) DO NOTHING",
                             (sid, w["office_id"], d, start, end, admin, now()))
            for i in range(today.weekday()):
                d = (monday + timedelta(days=i)).isoformat()
                if (email, i) in absent:
                    continue
                came = _local_to_server(d, start, tz, 27 if (email, i) in late else -6 + i)
                left = _local_to_server(d, end, tz, 4 + i)
                conn.execute("INSERT INTO staff_shifts (staff_id, office_id, start_at, end_at, minutes) VALUES (?, ?, ?, ?, ?)",
                             (sid, w["office_id"], came.strftime("%Y-%m-%d %H:%M:%S"), left.strftime("%Y-%m-%d %H:%M:%S"),
                              int((left - came).total_seconds() // 60)))
    store.plan_shift(admin, staff["kavya.iyer@example.com"], today.isoformat(), "10:00", "18:00", "Month-end payouts", 5)


def _tasks(store, staff):
    with store.db() as conn:
        users = {r["email"]: r["id"] for r in conn.execute("SELECT id, email FROM users WHERE email LIKE '%@example.com'")}
    for boss, worker, title, priority, due_in, status in TASKS:
        due = date.today() + timedelta(days=due_in)
        tid = store.give_task(users[boss], staff[worker], title, "", max(due, date.today()).isoformat(), priority)
        if due_in < 0:                                   # an overdue task (made "earlier")
            with store.db() as conn:
                conn.execute("UPDATE staff_tasks SET due_day = ?, created_at = ? WHERE id = ?",
                             (due.isoformat(), (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S"), tid))
        if status in ("In progress", "Done"):
            store.move_staff_task(users[worker], tid, "In progress")
        if status == "Done":
            store.move_staff_task(users[worker], tid, "Done")
