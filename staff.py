"""
staff.py - version 6 (mixin class used by Store): the people who run Yo Delivery.

Offices
    The offices form a tree: Head office -> country offices -> regional / city offices -> hubs, warehouses,
    support centres, teams and transport companies. Every worker belongs to one office.
Workers and managers
    A worker is a normal user account with a staff record: office, job title, kind (office staff, hub or
    warehouse worker, courier, transport staff) and the abilities the worker has. A manager is a worker with the
    abilities "staff.manage" / "staff.abilities": he adds, moves, suspends and removes the workers of his own office
    and of the offices under it, and he can give them only the abilities he has himself. The administrator
    (users.is_admin) can do everything everywhere, and only he can give the ability "all" (everything).
Abilities
    Every admin page needs one ability (ABILITY_FOR_PATH): to see a page the "view" ability, to change something
    (POST) the "manage" ability. Pages that are not in the list stay for the administrator only.
Also here
    shifts (clock in / clock out, hours of the week), time off asked by a worker and decided by his manager, the
    log of every staff change, the staff ID card (a signed QR code that anybody can check), parcel scans at hubs
    and warehouses, and the employees of a seller (seller_team) with their own small set of abilities.
"""

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta

import courier as C
from core import ShopError, now

STAFF_SCHEMA = """
CREATE TABLE IF NOT EXISTS offices (
    id INTEGER PRIMARY KEY AUTOINCREMENT, parent_id INTEGER REFERENCES offices(id), name TEXT NOT NULL,
    kind TEXT NOT NULL, country TEXT NOT NULL DEFAULT '', place TEXT NOT NULL DEFAULT '',
    operator_id INTEGER, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_offices_parent ON offices(parent_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_head_office ON offices(kind) WHERE kind = 'head';
CREATE TABLE IF NOT EXISTS staff (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL UNIQUE REFERENCES users(id),
    office_id INTEGER NOT NULL REFERENCES offices(id), title TEXT NOT NULL, kind TEXT NOT NULL,
    is_manager INTEGER NOT NULL DEFAULT 0, role TEXT NOT NULL DEFAULT '', abilities TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'Active', added_by INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_staff_office ON staff(office_id);
CREATE TABLE IF NOT EXISTS staff_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, actor_id INTEGER, staff_id INTEGER, office_id INTEGER,
    action TEXT NOT NULL, details TEXT NOT NULL DEFAULT '', at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS staff_shifts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, staff_id INTEGER NOT NULL REFERENCES staff(id),
    office_id INTEGER NOT NULL, start_at TEXT NOT NULL, end_at TEXT NOT NULL DEFAULT '',
    minutes INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_shift_open ON staff_shifts(staff_id) WHERE end_at = '';
CREATE TABLE IF NOT EXISTS staff_leave (
    id INTEGER PRIMARY KEY AUTOINCREMENT, staff_id INTEGER NOT NULL REFERENCES staff(id),
    from_day TEXT NOT NULL, to_day TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'Waiting', decided_by INTEGER, decided_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS seller_team (
    id INTEGER PRIMARY KEY AUTOINCREMENT, seller_id INTEGER NOT NULL REFERENCES sellers(id),
    user_id INTEGER NOT NULL UNIQUE REFERENCES users(id), title TEXT NOT NULL DEFAULT '',
    abilities TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'Active', added_by INTEGER,
    created_at TEXT NOT NULL
);
"""

# key -> (group, what the worker may do)
ABILITIES = {
    "dashboard.view": ("General", "See the dashboard and the numbers of the business"),
    "board.use": ("General", "Use the team task board"),
    "orders.view": ("Shop", "See orders"),
    "orders.manage": ("Shop", "Change the status of orders and cancel them"),
    "returns.manage": ("Shop", "Handle returns and refunds"),
    "products.manage": ("Shop", "Add and change products and categories"),
    "sellers.manage": ("Shop", "Approve sellers and pay them"),
    "reviews.manage": ("Shop", "Hide or show reviews"),
    "coupons.manage": ("Shop", "Make and change coupons"),
    "customers.view": ("People", "See customers"),
    "customers.manage": ("People", "Block customers and change their details"),
    "shipments.view": ("Courier", "See shipments"),
    "shipments.manage": ("Courier", "Change shipments and give them to couriers"),
    "couriers.manage": ("Courier", "Approve, suspend and pay couriers"),
    "hub.scan": ("Courier", "Scan parcels at a hub or a warehouse"),
    "customs.manage": ("Courier", "Change the customs rules for parcels abroad"),
    "tickets.view": ("Tickets", "See ticket bookings and trips"),
    "tickets.manage": ("Tickets", "Delay or cancel trips (with automatic refunds)"),
    "tickets.check": ("Tickets", "Check tickets on board (conductor app)"),
    "transport.manage": ("Tickets", "Manage transport companies, routes and conductors"),
    "yopay.view": ("Money", "See the Yo Pay ledger and money reports"),
    "tax.manage": ("Money", "Change the tax rules of the countries"),
    "rewards.manage": ("Content", "Quiz questions and rewards"),
    "blog.manage": ("Content", "Review and publish blog posts and seller stories"),
    "model.manage": ("Content", "The recommendation model"),
    "chat.moderate": ("Trust", "Moderate chats between customers and sellers"),
    "photos.review": ("Trust", "Check courier photos and private files"),
    "activity.view": ("System", "See the activity log"),
    "monitor.view": ("System", "See monitoring, errors and the job queue"),
    "database.view": ("System", "Database viewer, CSV export and backups"),
    "settings.manage": ("System", "Change the shop settings"),
    "staff.view": ("Staff", "See workers and offices of my branch"),
    "staff.manage": ("Staff", "Add, move, suspend and remove workers of my branch"),
    "staff.abilities": ("Staff", "Give abilities to workers (only abilities I have)"),
    "offices.manage": ("Staff", "Add and change offices of my branch"),
    "shifts.manage": ("Staff", "Plan shifts, approve time off and see the hours of my branch"),
    "tasks.manage": ("Staff", "Give tasks to the workers of my branch and follow them"),
    # version 7
    "payroll.manage": ("Money", "Make payroll runs and approve those of other people"),
    "invoices.view": ("Money", "See tax invoices and download e-invoices"),
    "payments.manage": ("Money", "Reconcile payments with the statements of the providers"),
    "partners.manage": ("Courier", "Courier partner companies and their API keys"),
    "support.manage": ("People", "Answer support questions and decide parcel claims"),
    "risk.review": ("Trust", "Release or cancel orders held by the safety check"),
    "analytics.view": ("General", "See analytics and hub forecasts of my branch"),
    "privacy.manage": ("System", "See the privacy register and the data requests"),
}
ALL = "all"                                      # every ability; only the administrator gives it

# ready sets of abilities (a manager can still tick more or fewer boxes)
ROLES = {
    "support": ("Customer support", ["dashboard.view", "orders.view", "orders.manage", "returns.manage",
                                     "customers.view", "shipments.view", "tickets.view", "chat.moderate", "board.use",
                                     "support.manage", "risk.review"]),
    "finance": ("Finance", ["dashboard.view", "orders.view", "returns.manage", "yopay.view", "sellers.manage",
                            "tax.manage", "payroll.manage", "invoices.view", "payments.manage"]),
    "catalog": ("Catalog and content", ["products.manage", "reviews.manage", "coupons.manage", "blog.manage",
                                        "rewards.manage", "board.use"]),
    "moderator": ("Trust and safety", ["chat.moderate", "photos.review", "reviews.manage", "blog.manage"]),
    "hub": ("Hub / warehouse worker", ["hub.scan", "shipments.view", "board.use"]),
    "courier_lead": ("Courier team lead", ["shipments.view", "shipments.manage", "couriers.manage", "hub.scan",
                                           "customs.manage", "partners.manage", "analytics.view"]),
    "courier": ("Courier", []),
    "conductor": ("Conductor", ["tickets.check"]),
    "ticket_desk": ("Ticket desk", ["tickets.view", "tickets.manage"]),
    "transport_manager": ("Transport manager", ["transport.manage", "tickets.view", "tickets.manage",
                                                "tickets.check", "staff.view", "staff.manage", "staff.abilities",
                                                "shifts.manage", "tasks.manage"]),
    "office_manager": ("Office manager", ["dashboard.view", "board.use", "staff.view", "staff.manage",
                                          "staff.abilities", "offices.manage", "shifts.manage", "tasks.manage",
                                          "analytics.view"]),
    "country_manager": ("Country manager", ["dashboard.view", "board.use", "orders.view", "shipments.view",
                                            "tickets.view", "customers.view", "yopay.view", "staff.view",
                                            "staff.manage", "staff.abilities", "offices.manage", "shifts.manage",
                                            "tasks.manage", "analytics.view"]),
    "it": ("IT and systems", ["monitor.view", "activity.view", "database.view", "settings.manage",
                              "privacy.manage"]),
}

WORKER_KINDS = {"office": "Office staff", "hub": "Hub / warehouse worker", "courier": "Courier",
                "transport": "Transport staff", "support": "Support agent"}
OFFICE_KINDS = {"head": "Head office", "country": "Country office", "region": "Regional office",
                "city": "City office", "hub": "Hub", "warehouse": "Warehouse", "support": "Support centre",
                "transport": "Transport company", "team": "Team"}
STAFF_STATUSES = ("Active", "Suspended", "Removed")

# admin page -> (ability to see it, ability to change something on it); the first prefix that matches wins
ABILITY_FOR_PATH = [
    ("/admin/account", "self", "self"),
    ("/admin/orders", "orders.view", "orders.manage"),
    ("/admin/returns", "orders.view", "returns.manage"),
    ("/admin/products", "products.manage", "products.manage"),
    ("/admin/categories", "products.manage", "products.manage"),
    ("/admin/sellers", "sellers.manage", "sellers.manage"),
    ("/admin/shipments", "shipments.view", "shipments.manage"),
    ("/admin/couriers", "couriers.manage", "couriers.manage"),
    ("/admin/customs", "customs.manage", "customs.manage"),
    ("/admin/tickets/staff", "transport.manage", "transport.manage"),
    ("/admin/tickets/services", "transport.manage", "transport.manage"),
    ("/admin/tickets/gtfs", "transport.manage", "transport.manage"),
    ("/admin/tickets", "tickets.view", "tickets.manage"),
    ("/admin/customers", "customers.view", "customers.manage"),
    ("/admin/reviews", "reviews.manage", "reviews.manage"),
    ("/admin/coupons", "coupons.manage", "coupons.manage"),
    ("/admin/yopay", "yopay.view", "yopay.view"),
    ("/admin/taxes", "yopay.view", "tax.manage"),
    ("/admin/bank", "yopay.view", "yopay.view"),
    ("/admin/rewards", "rewards.manage", "rewards.manage"),
    ("/admin/blog", "blog.manage", "blog.manage"),
    ("/admin/photos", "photos.review", "photos.review"),
    ("/admin/private", "photos.review", "photos.review"),
    ("/admin/id-checks", "photos.review", "photos.review"),
    ("/admin/chat", "chat.moderate", "chat.moderate"),
    ("/admin/model", "model.manage", "model.manage"),
    ("/admin/activity", "activity.view", "activity.view"),
    ("/admin/monitor", "monitor.view", "monitor.view"),
    ("/admin/database", "database.view", "database.view"),
    ("/admin/backups", "database.view", "database.view"),
    ("/admin/settings", "settings.manage", "settings.manage"),
    ("/admin/staff/rota", "staff.view", "shifts.manage"),
    ("/admin/staff/attendance", "staff.view", "shifts.manage"),
    ("/admin/staff/tasks", "staff.view", "tasks.manage"),
    ("/admin/staff/performance", "staff.view", "staff.view"),
    # version 7
    ("/admin/staff/training", "staff.view", "staff.manage"),
    ("/admin/payroll", "payroll.manage", "payroll.manage"),
    ("/admin/approvals", "payroll.manage", "payroll.manage"),
    ("/admin/invoices", "invoices.view", "invoices.view"),
    ("/admin/tax-categories", "yopay.view", "tax.manage"),
    ("/admin/payments", "yopay.view", "payments.manage"),
    ("/admin/partners", "partners.manage", "partners.manage"),
    ("/admin/support", "support.manage", "support.manage"),
    ("/admin/claims", "support.manage", "support.manage"),
    ("/admin/risk", "orders.view", "risk.review"),
    ("/admin/analytics", "analytics.view", "analytics.view"),
    ("/admin/forecast", "analytics.view", "analytics.view"),
    ("/admin/experiments", "model.manage", "model.manage"),
    ("/admin/privacy", "privacy.manage", "privacy.manage"),
    ("/admin/staff", "staff.view", "staff.manage"),
    ("/admin/offices", "staff.view", "offices.manage"),
]

# the employees of a seller: what the shop owner can let them do
SELLER_ABILITIES = {
    "seller.products": "Add and change products (and their texts in other languages)",
    "seller.orders": "See and pack orders",
    "seller.stories": "Write stories for the blog",
    "seller.board": "Use the shop's task board",
    "seller.payouts": "See payouts",
}
SELLER_PATHS = [("/seller/bank", "owner"), ("/seller/tax", "owner"), ("/seller/invoices", "seller.orders"), ("/seller/products", "seller.products"), ("/seller/orders", "seller.orders"),
                ("/seller/stories", "seller.stories"), ("/seller/payouts", "owner"), ("/seller/settings", "owner"),
                ("/seller/team", "owner"), ("/board/seller", "seller.board")]

HUB_ACTIONS = {"in": "Scanned in at the hub", "sorted": "Sorted at the hub", "out": "Left the hub",
               "problem": "Problem at the hub"}
WEEK_MAX_MIN = 7 * 24 * 60


def ability_for_path(path, method="GET"):
    """The ability an admin page needs (None = only the administrator)."""
    if path.rstrip("/") == "/admin":
        return "dashboard.view"
    for prefix, see, change in ABILITY_FOR_PATH:
        if path == prefix or path.startswith(prefix + "/") or path.startswith(prefix + "."):
            return see if method in ("GET", "HEAD") else change
    return None


def seller_ability_for_path(path):
    for prefix, need in SELLER_PATHS:
        if path == prefix or path.startswith(prefix + "/"):
            return need
    return None


def split(abilities):
    return {a for a in str(abilities or "").split() if a == ALL or a in ABILITIES or a in SELLER_ABILITIES}


def join(abilities):
    return " ".join(sorted(set(abilities)))


class StaffMixin:
    # ---------------------------------------------------------------- reading
    def worker_of(self, user_id, conn=None):
        """The active (or suspended) staff record of a user, with his office (or None)."""
        if not user_id:
            return None

        def read(c):
            r = c.execute("SELECT s.*, o.name AS office, o.kind AS office_kind, o.place AS office_place, "
                          "o.country AS office_country, o.operator_id, u.name AS name, u.email AS email "
                          "FROM staff s JOIN offices o ON o.id = s.office_id JOIN users u ON u.id = s.user_id "
                          "WHERE s.user_id = ? AND s.status != 'Removed'", (user_id,)).fetchone()
            if r is None:
                return None
            d = dict(r)
            d["ability_set"] = split(d["abilities"])
            return d
        if conn is not None:
            return read(conn)
        with self.db() as c:
            return read(c)

    def can(self, user_id, ability):
        """May this user do this? The administrator may do everything; a worker needs the ability."""
        u = self.get_user(user_id) if user_id else None
        if not u or u["blocked"]:
            return False
        if u["is_admin"]:
            return True
        w = self.worker_of(user_id)
        if not w or w["status"] != "Active":
            return False
        if ability == "self":
            return True
        return ALL in w["ability_set"] or ability in w["ability_set"]

    def abilities_of(self, user_id):
        u = self.get_user(user_id) if user_id else None
        if u and u["is_admin"]:
            return {ALL}
        w = self.worker_of(user_id)
        return w["ability_set"] if w and w["status"] == "Active" else set()

    def offices(self, conn=None):
        def read(c):
            return [dict(r) for r in c.execute(
                "SELECT o.*, (SELECT COUNT(*) FROM staff s WHERE s.office_id = o.id AND s.status != 'Removed') AS people "
                "FROM offices o ORDER BY o.id")]
        if conn is not None:
            return read(conn)
        with self.db() as c:
            return read(c)

    def office(self, office_id):
        with self.db() as conn:
            r = conn.execute("SELECT * FROM offices WHERE id = ?", (office_id,)).fetchone()
        return dict(r) if r else None

    def subtree(self, office_id, conn=None):
        """The ids of an office and of every office under it."""
        rows = self.offices(conn)
        kids = {}
        for o in rows:
            kids.setdefault(o["parent_id"], []).append(o["id"])
        out, todo = set(), [office_id]
        while todo:
            x = todo.pop()
            if x in out:
                continue
            out.add(x)
            todo.extend(kids.get(x, []))
        return out

    def office_path(self, office_id):
        """Head office > India > Haryana > Ambala hub (for the page titles)."""
        rows = {o["id"]: o for o in self.offices()}
        path, x, seen = [], office_id, set()
        while x in rows and x not in seen:
            seen.add(x)
            path.append(rows[x])
            x = rows[x]["parent_id"]
        return list(reversed(path))

    def office_tree(self, root_id=None):
        """The offices as a list with their depth (for the tree on the page), only the branch of root_id."""
        rows = self.offices()
        kids = {}
        for o in rows:
            kids.setdefault(o["parent_id"], []).append(o)
        out = []

        def walk(parent, depth):
            for o in sorted(kids.get(parent, []), key=lambda x: x["name"]):
                out.append(dict(o, depth=depth))
                walk(o["id"], depth + 1)
        if root_id is None:
            walk(None, 0)
        else:
            root = next((o for o in rows if o["id"] == root_id), None)
            if root:
                out.append(dict(root, depth=0))
                walk(root_id, 1)
        return out

    def branch_of(self, user_id):
        """The office ids a user may see and manage: all (administrator) or the branch of his office."""
        u = self.get_user(user_id)
        if u and u["is_admin"]:
            return None                           # None = every office
        w = self.worker_of(user_id)
        return self.subtree(w["office_id"]) if w and w["status"] == "Active" else set()

    def workers(self, user_id, office_id=None, status="", q=""):
        """The workers the user may see (his branch), newest first."""
        branch = self.branch_of(user_id)
        with self.db() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT s.*, o.name AS office, o.kind AS office_kind, u.name AS name, u.email AS email, u.mobile "
                "FROM staff s JOIN offices o ON o.id = s.office_id JOIN users u ON u.id = s.user_id "
                "ORDER BY s.status = 'Removed', o.name, s.is_manager DESC, u.name")]
        if branch is not None:
            rows = [r for r in rows if r["office_id"] in branch]
        if office_id:
            sub = self.subtree(int(office_id))
            rows = [r for r in rows if r["office_id"] in sub]
        if status:
            rows = [r for r in rows if r["status"] == status]
        if q:
            ql = q.lower()
            rows = [r for r in rows if ql in (r["name"] + " " + r["email"] + " " + r["title"]).lower()]
        for r in rows:
            r["ability_set"] = split(r["abilities"])
        return rows

    def staff_member(self, staff_id):
        with self.db() as conn:
            r = conn.execute("SELECT s.*, o.name AS office, o.kind AS office_kind, o.country AS office_country, "
                             "u.name AS name, u.email AS email, u.mobile FROM staff s JOIN offices o ON o.id = s.office_id "
                             "JOIN users u ON u.id = s.user_id WHERE s.id = ?", (staff_id,)).fetchone()
        if r is None:
            return None
        d = dict(r)
        d["ability_set"] = split(d["abilities"])
        return d

    def staff_log(self, user_id, limit=100, who="", action="", office_id=None, days=0, kind=""):
        """The log of my branch, newest first; who / action are parts of the names, kind = changes or pages."""
        branch = self.branch_of(user_id)
        sql, args = ("SELECT l.*, a.name AS actor, u.name AS worker, o.name AS office FROM staff_log l "
                     "LEFT JOIN users a ON a.id = l.actor_id LEFT JOIN staff s ON s.id = l.staff_id "
                     "LEFT JOIN users u ON u.id = s.user_id LEFT JOIN offices o ON o.id = l.office_id WHERE 1 = 1", [])
        if days:
            sql += " AND l.at >= ?"
            args.append((datetime.now() - timedelta(days=int(days))).strftime("%Y-%m-%d"))
        if kind == "pages":
            sql += " AND l.action = 'Page action'"
        elif kind == "changes":
            sql += " AND l.action != 'Page action'"
        with self.db() as conn:
            rows = [dict(r) for r in conn.execute(sql + " ORDER BY l.id DESC LIMIT ?", (*args, limit * 6))]
        if branch is not None:
            rows = [r for r in rows if r["office_id"] in branch]
        if office_id:
            sub = self.subtree(int(office_id))
            rows = [r for r in rows if r["office_id"] in sub]
        if who:
            w = who.lower()
            rows = [r for r in rows if w in ((r["actor"] or "") + " " + (r["worker"] or "")).lower()]
        if action:
            a = action.lower()
            rows = [r for r in rows if a in (r["action"] + " " + r["details"]).lower()]
        return rows[:limit]

    def log_staff_action(self, user_id, path, fields, status=200):
        """Every change made on an admin page: who, which page, which fields (never the values)."""
        w = self.worker_of(user_id)
        with self.db() as conn:
            self._staff_log(conn, user_id, w["id"] if w else None, w["office_id"] if w else None, "Page action",
                            f"POST {path}" + (f" [{', '.join(fields)[:200]}]" if fields else "")
                            + ("" if status < 400 else f" - refused ({status})"))

    def _staff_log(self, conn, actor_id, staff_id, office_id, action, details=""):
        conn.execute("INSERT INTO staff_log (actor_id, staff_id, office_id, action, details, at) VALUES (?, ?, ?, ?, ?, ?)",
                     (actor_id, staff_id, office_id, action, details[:500], now()))

    # ---------------------------------------------------------------- rules
    def _actor(self, conn, actor_id, need):
        """The administrator, or an active worker with this ability: returns (is_admin, worker, branch)."""
        u = conn.execute("SELECT is_admin, blocked FROM users WHERE id = ?", (actor_id,)).fetchone()
        if u is None or u["blocked"]:
            raise ShopError("Please sign in again.")
        if u["is_admin"]:
            return True, None, None
        w = self.worker_of(actor_id, conn)
        if not w or w["status"] != "Active" or not (ALL in w["ability_set"] or need in w["ability_set"]):
            raise ShopError("You do not have the ability to do this. Ask your manager.")
        return False, w, self.subtree(w["office_id"], conn)

    def _may_manage(self, admin, actor, branch, target):
        """A manager manages the workers of his branch, but not himself and not the managers of his own office."""
        if admin:
            return True
        if target["user_id"] == actor["user_id"]:
            return False
        if target["office_id"] not in branch:
            return False
        if target["office_id"] == actor["office_id"] and target["is_manager"]:
            return False
        return not (ALL in split(target["abilities"]) and ALL not in actor["ability_set"])

    def _check_grant(self, admin, actor, old, new):
        """New abilities must be ones the actor has himself; "all" only from the administrator."""
        added = set(new) - set(old)
        if admin:
            return
        if ALL in added:
            raise ShopError("Only the administrator can give the ability to do everything.")
        mine = actor["ability_set"]
        if ALL in mine:
            return
        missing = sorted(a for a in added if a not in mine)
        if missing:
            raise ShopError("You can only give abilities you have yourself: "
                            + ", ".join(ABILITIES.get(a, (0, a))[1] for a in missing) + ".")
        if added and "staff.abilities" not in mine:
            raise ShopError("You do not have the ability to give abilities.")

    # ---------------------------------------------------------------- offices
    def add_office(self, actor_id, parent_id, name, kind, country="", place="", operator_id=None):
        name = " ".join(str(name or "").split())[:80]
        if len(name) < 2:
            raise ShopError("Please write the name of the office.")
        if kind not in OFFICE_KINDS:
            raise ShopError("Please choose the kind of office.")
        if kind == "head":
            raise ShopError("There is only one head office.")
        with self.db() as conn:
            admin, actor, branch = self._actor(conn, actor_id, "offices.manage")
            parent = conn.execute("SELECT * FROM offices WHERE id = ? AND active = 1", (parent_id,)).fetchone()
            if parent is None:
                raise ShopError("Please choose the office this one belongs to.")
            if not admin and parent["id"] not in branch:
                raise ShopError("You can add offices only inside your own branch.")
            if place:
                p = C.resolve(place, (country or parent["country"] or "IN").upper(), place)
                if p is None:
                    raise ShopError("We could not find this town or village.")
                place, country = p["id"], p["cc"]
            if kind == "transport" and operator_id:
                if conn.execute("SELECT 1 FROM ticket_operators WHERE id = ?", (operator_id,)).fetchone() is None:
                    raise ShopError("Unknown transport company.")
            cur = conn.execute("INSERT INTO offices (parent_id, name, kind, country, place, operator_id, active, created_at) "
                               "VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
                               (parent["id"], name, kind, (country or parent["country"] or "").upper()[:2], place,
                                operator_id if kind == "transport" else None, now()))
            oid = cur.lastrowid
            self._staff_log(conn, actor_id, None, oid, "Office added", f"{name} ({OFFICE_KINDS[kind]}) under {parent['name']}")
        return oid

    def rename_office(self, actor_id, office_id, name, active=True):
        name = " ".join(str(name or "").split())[:80]
        if len(name) < 2:
            raise ShopError("Please write the name of the office.")
        with self.db() as conn:
            admin, actor, branch = self._actor(conn, actor_id, "offices.manage")
            o = conn.execute("SELECT * FROM offices WHERE id = ?", (office_id,)).fetchone()
            if o is None:
                raise ShopError("Office not found.")
            if not admin and (o["id"] not in branch or o["id"] == actor["office_id"]):
                raise ShopError("You can change only the offices under your own office.")
            if not active:
                if o["kind"] == "head":
                    raise ShopError("The head office cannot be closed.")
                if conn.execute("SELECT 1 FROM staff WHERE office_id = ? AND status != 'Removed'", (office_id,)).fetchone() \
                        or conn.execute("SELECT 1 FROM offices WHERE parent_id = ? AND active = 1", (office_id,)).fetchone():
                    raise ShopError("Move the workers and the offices of this office first.")
            conn.execute("UPDATE offices SET name = ?, active = ? WHERE id = ?", (name, int(bool(active)), office_id))
            self._staff_log(conn, actor_id, None, office_id, "Office changed" if active else "Office closed", name)

    # ---------------------------------------------------------------- workers
    def add_worker(self, actor_id, data):
        """Add a worker. data: email, name (new account), mobile, office_id, title, kind, role, abilities,
        is_manager, vehicle (couriers). Returns (staff_id, setup_link_token or None)."""
        email = str(data.get("email", "")).strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise ShopError("Please enter the e-mail address of the worker.")
        title = " ".join(str(data.get("title", "")).split())[:60]
        kind = data.get("kind", "office")
        if kind not in WORKER_KINDS:
            raise ShopError("Please choose the kind of worker.")
        role = data.get("role", "")
        wanted = split(" ".join(data.get("abilities") or [])) or set(ROLES.get(role, ("", []))[1])
        if not title:
            title = ROLES.get(role, (WORKER_KINDS[kind],))[0]
        token = None
        user = self.find_user(email)
        if user is None:                             # a new account: the worker sets his password with a link
            name = " ".join(str(data.get("name", "")).split())
            mobile = str(data.get("mobile", "")).strip()
            if len(name) < 2 or len(re.sub(r"\D", "", mobile)) < 7:
                raise ShopError("There is no account with this e-mail. To create one, enter the name and the "
                                "mobile number of the worker.")
            with self.db() as conn:                  # the account gets the country of the office (+250, +7 ...)
                o = conn.execute("SELECT country FROM offices WHERE id = ?", (data.get("office_id"),)).fetchone()
            uid = self.register(name, email, mobile, self._random_password(), wallet=0,
                                country=(o["country"] if o and o["country"] else "IN"))
            token = self.create_password_reset(uid)
            user = self.get_user(uid)
        if user["is_admin"]:
            raise ShopError("This person is an administrator already.")
        if user["blocked"]:
            raise ShopError("This account is blocked.")
        with self.db() as conn:
            admin, actor, branch = self._actor(conn, actor_id, "staff.manage")
            office = conn.execute("SELECT * FROM offices WHERE id = ? AND active = 1", (data.get("office_id"),)).fetchone()
            if office is None:
                raise ShopError("Please choose the office of the worker.")
            if not admin and office["id"] not in branch:
                raise ShopError("You can add workers only to your own branch.")
            manager = int(bool(data.get("is_manager")))
            if manager and not admin and office["id"] == actor["office_id"]:
                raise ShopError("Only your own manager can add another manager to your office.")
            self._check_grant(admin, actor, set(), wanted)
            old = conn.execute("SELECT * FROM staff WHERE user_id = ?", (user["id"],)).fetchone()
            if old is not None and old["status"] != "Removed":
                raise ShopError("This person already works for Yo Delivery.")
            if old is not None:                      # came back: the old record is used again
                conn.execute("UPDATE staff SET office_id = ?, title = ?, kind = ?, is_manager = ?, role = ?, "
                             "abilities = ?, status = 'Active', added_by = ?, updated_at = ? WHERE id = ? AND status = 'Removed'",
                             (office["id"], title, kind, manager, role, join(wanted), actor_id, now(), old["id"]))
                sid = old["id"]
            else:
                cur = conn.execute("INSERT INTO staff (user_id, office_id, title, kind, is_manager, role, abilities, "
                                   "status, added_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'Active', ?, ?, ?) "
                                   "ON CONFLICT (user_id) DO NOTHING",
                                   (user["id"], office["id"], title, kind, manager, role, join(wanted), actor_id, now(), now()))
                if not cur.rowcount:
                    raise ShopError("This person already works for Yo Delivery.")
                sid = conn.execute("SELECT id FROM staff WHERE user_id = ?", (user["id"],)).fetchone()[0]
            self._sync_links(conn, sid, data.get("vehicle"))
            self._staff_log(conn, actor_id, sid, office["id"], "Worker added",
                            f"{user['name']} - {title} ({WORKER_KINDS[kind]}){' - manager' if manager else ''}; "
                            f"abilities: {join(wanted) or '-'}")
            self.notify(conn, user["id"], "You now work for Yo Delivery",
                        f"{office['name']}: {title}. Open 'My work' to see your abilities and to clock in.", url="/staff")
        return sid, token

    def update_worker(self, actor_id, staff_id, data):
        """Change office, title, kind, manager flag and abilities of a worker."""
        with self.db() as conn:
            admin, actor, branch = self._actor(conn, actor_id, "staff.manage")
            t = conn.execute("SELECT * FROM staff WHERE id = ?", (staff_id,)).fetchone()
            if t is None or t["status"] == "Removed":
                raise ShopError("Worker not found.")
            if not self._may_manage(admin, actor, branch, t):
                raise ShopError("You can change only the workers of your own branch (not yourself).")
            office_id = int(data.get("office_id") or t["office_id"])
            office = conn.execute("SELECT * FROM offices WHERE id = ? AND active = 1", (office_id,)).fetchone()
            if office is None or (not admin and office_id not in branch):
                raise ShopError("You can move workers only inside your own branch.")
            kind = data.get("kind") or t["kind"]
            if kind not in WORKER_KINDS:
                raise ShopError("Please choose the kind of worker.")
            title = " ".join(str(data.get("title") or t["title"]).split())[:60]
            manager = int(bool(data.get("is_manager")))
            if manager and not t["is_manager"] and not admin and office_id == actor["office_id"]:
                raise ShopError("Only your own manager can add another manager to your office.")
            old = split(t["abilities"])
            new = split(" ".join(data.get("abilities") or [])) if "abilities" in data else old
            if new != old and not admin and not (ALL in actor["ability_set"] or "staff.abilities" in actor["ability_set"]):
                raise ShopError("You do not have the ability to give abilities.")
            self._check_grant(admin, actor, old, new)
            n = conn.execute("UPDATE staff SET office_id = ?, title = ?, kind = ?, is_manager = ?, abilities = ?, "
                             "role = ?, updated_at = ? WHERE id = ? AND status != 'Removed'",
                             (office_id, title, kind, manager, join(new), data.get("role", t["role"]) or "", now(),
                              staff_id)).rowcount
            if not n:
                raise ShopError("Worker not found.")
            self._sync_links(conn, staff_id, data.get("vehicle"))
            changes = []
            if office_id != t["office_id"]:
                changes.append(f"office -> {office['name']}")
            if title != t["title"]:
                changes.append(f"title -> {title}")
            if manager != t["is_manager"]:
                changes.append("manager" if manager else "not a manager")
            if new - old:
                changes.append("+ " + join(new - old))
            if old - new:
                changes.append("- " + join(old - new))
            self._staff_log(conn, actor_id, staff_id, office_id, "Worker changed", "; ".join(changes) or "no change")
            if new != old:
                self.notify(conn, t["user_id"], "Your abilities changed", "Open 'My work' to see what you can do now.",
                            url="/staff")

    def set_worker_status(self, actor_id, staff_id, status, reason=""):
        """Suspend, bring back (Active) or remove a worker."""
        if status not in STAFF_STATUSES:
            raise ShopError("Unknown status.")
        with self.db() as conn:
            admin, actor, branch = self._actor(conn, actor_id, "staff.manage")
            t = conn.execute("SELECT * FROM staff WHERE id = ?", (staff_id,)).fetchone()
            if t is None or t["status"] == "Removed":
                raise ShopError("Worker not found.")
            if not self._may_manage(admin, actor, branch, t):
                raise ShopError("You can change only the workers of your own branch (not yourself).")
            n = conn.execute("UPDATE staff SET status = ?, updated_at = ? WHERE id = ? AND status = ?",
                             (status, now(), staff_id, t["status"])).rowcount
            if not n:
                raise ShopError("Somebody else changed this worker just now. Please reload the page.")
            if status != "Active":
                self._end_shift(conn, staff_id)
            self._sync_links(conn, staff_id)
            self._staff_log(conn, actor_id, staff_id, t["office_id"],
                            {"Active": "Worker back", "Suspended": "Worker suspended", "Removed": "Worker removed"}[status],
                            reason)

    def _sync_links(self, conn, staff_id, vehicle=None):
        """Conductors (transport staff with "tickets.check" in a transport company office) and couriers follow
        the staff record: they get or lose the conductor app and the courier app with it."""
        s = conn.execute("SELECT s.*, o.kind AS office_kind, o.place, o.country, o.operator_id FROM staff s "
                         "JOIN offices o ON o.id = s.office_id WHERE s.id = ?", (staff_id,)).fetchone()
        active = s["status"] == "Active"
        abil = split(s["abilities"])
        if active and s["operator_id"] and (ALL in abil or "tickets.check" in abil):
            conn.execute("INSERT INTO ticket_staff (user_id, operator_id, created_at) VALUES (?, ?, ?) "
                         "ON CONFLICT (user_id) DO UPDATE SET operator_id = excluded.operator_id",
                         (s["user_id"], s["operator_id"], now()))
        else:
            conn.execute("DELETE FROM ticket_staff WHERE user_id = ?", (s["user_id"],))
        if s["kind"] == "courier":
            place = s["place"] or ""
            if active:
                if not place:
                    raise ShopError("A courier needs an office in a town (a hub or a city office).")
                conn.execute("INSERT INTO couriers (user_id, vehicle, city, place, status, created_at, decided_at) "
                             "VALUES (?, ?, ?, ?, 'Approved', ?, ?) ON CONFLICT (user_id) DO UPDATE SET "
                             "status = 'Approved', place = excluded.place, city = excluded.city",
                             (s["user_id"], (vehicle or "Motorbike")[:20], place, place, now(), now()))
            else:
                conn.execute("UPDATE couriers SET status = 'Suspended', online = 0 WHERE user_id = ?", (s["user_id"],))

    # ---------------------------------------------------------------- shifts and time off
    def clock_in(self, user_id):
        with self.db() as conn:
            w = self.worker_of(user_id, conn)
            if not w or w["status"] != "Active":
                raise ShopError("Only active workers can clock in.")
            n = conn.execute("INSERT INTO staff_shifts (staff_id, office_id, start_at) VALUES (?, ?, ?) "
                             "ON CONFLICT (staff_id) WHERE end_at = '' DO NOTHING", (w["id"], w["office_id"], now())).rowcount
            if not n:
                raise ShopError("You are clocked in already.")

    def clock_out(self, user_id):
        with self.db() as conn:
            w = self.worker_of(user_id, conn)
            if not w or not self._end_shift(conn, w["id"]):
                raise ShopError("You are not clocked in.")

    def _end_shift(self, conn, staff_id):
        r = conn.execute("SELECT * FROM staff_shifts WHERE staff_id = ? AND end_at = ''", (staff_id,)).fetchone()
        if r is None:
            return False
        end = now()
        minutes = max(0, int((datetime.fromisoformat(end) - datetime.fromisoformat(r["start_at"])).total_seconds() // 60))
        return bool(conn.execute("UPDATE staff_shifts SET end_at = ?, minutes = ? WHERE id = ? AND end_at = ''",
                                 (end, min(minutes, 16 * 60), r["id"])).rowcount)

    def shift_status(self, user_id):
        w = self.worker_of(user_id)
        if not w:
            return None
        week_start = (datetime.now() - timedelta(days=datetime.now().weekday())).strftime("%Y-%m-%d")
        with self.db() as conn:
            open_ = conn.execute("SELECT * FROM staff_shifts WHERE staff_id = ? AND end_at = ''", (w["id"],)).fetchone()
            done = conn.execute("SELECT COALESCE(SUM(minutes), 0) FROM staff_shifts WHERE staff_id = ? AND start_at >= ?",
                                (w["id"], week_start)).fetchone()[0]
            recent = [dict(r) for r in conn.execute("SELECT * FROM staff_shifts WHERE staff_id = ? ORDER BY id DESC LIMIT 6",
                                                    (w["id"],))]
        running = 0
        if open_:
            running = int((datetime.now() - datetime.fromisoformat(open_["start_at"])).total_seconds() // 60)
        return {"open": dict(open_) if open_ else None, "week_min": int(done) + running, "recent": recent}

    def request_leave(self, user_id, from_day, to_day, reason=""):
        try:
            a, b = datetime.strptime(from_day, "%Y-%m-%d").date(), datetime.strptime(to_day, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            raise ShopError("Please choose the first and the last day.")
        if b < a:
            raise ShopError("The last day is before the first day.")
        if a < datetime.now().date():
            raise ShopError("Time off can only be asked for today or later.")
        if (b - a).days > 60:
            raise ShopError("Please ask for at most 60 days at a time.")
        with self.db() as conn:
            w = self.worker_of(user_id, conn)
            if not w or w["status"] != "Active":
                raise ShopError("Only active workers can ask for time off.")
            if conn.execute("SELECT 1 FROM staff_leave WHERE staff_id = ? AND status IN ('Waiting', 'Approved') "
                            "AND from_day <= ? AND to_day >= ?", (w["id"], b.isoformat(), a.isoformat())).fetchone():
                raise ShopError("You have already asked for time off on these days.")
            cur = conn.execute("INSERT INTO staff_leave (staff_id, from_day, to_day, reason, created_at) VALUES (?, ?, ?, ?, ?)",
                               (w["id"], a.isoformat(), b.isoformat(), " ".join(str(reason).split())[:200], now()))
            self._staff_log(conn, user_id, w["id"], w["office_id"], "Time off asked", f"{a} - {b}")
            return cur.lastrowid

    def leave_list(self, user_id, mine=False):
        """My own requests (mine=True) or the requests of my branch that I may decide."""
        with self.db() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT l.*, s.user_id, s.office_id, s.is_manager, s.abilities, u.name, o.name AS office FROM staff_leave l "
                "JOIN staff s ON s.id = l.staff_id JOIN users u ON u.id = s.user_id JOIN offices o ON o.id = s.office_id "
                "ORDER BY l.id DESC LIMIT 200")]
        if mine:
            return [r for r in rows if r["user_id"] == user_id]
        u = self.get_user(user_id)
        if u and u["is_admin"]:
            return [r for r in rows if r["user_id"] != user_id]
        w = self.worker_of(user_id)
        if not w or not (ALL in w["ability_set"] or "shifts.manage" in w["ability_set"]):
            return []
        branch = self.subtree(w["office_id"])
        return [r for r in rows if self._may_manage(False, w, branch, r)]

    def decide_leave(self, actor_id, leave_id, approve):
        with self.db() as conn:
            admin, actor, branch = self._actor(conn, actor_id, "shifts.manage")
            r = conn.execute("SELECT l.*, s.user_id, s.office_id, s.is_manager, s.abilities FROM staff_leave l "
                             "JOIN staff s ON s.id = l.staff_id WHERE l.id = ?", (leave_id,)).fetchone()
            if r is None or not self._may_manage(admin, actor, branch, r):
                raise ShopError("You can decide only the time off of your own branch.")
            status = "Approved" if approve else "Refused"
            if not conn.execute("UPDATE staff_leave SET status = ?, decided_by = ?, decided_at = ? WHERE id = ? "
                                "AND status = 'Waiting'", (status, actor_id, now(), leave_id)).rowcount:
                raise ShopError("This request was decided already.")
            self._staff_log(conn, actor_id, r["staff_id"], r["office_id"], f"Time off {status.lower()}",
                            f"{r['from_day']} - {r['to_day']}")
            self.notify(conn, r["user_id"], f"Time off {status.lower()}", f"{r['from_day']} - {r['to_day']}", url="/staff")

    def on_leave_today(self, staff_id):
        today = datetime.now().strftime("%Y-%m-%d")
        with self.db() as conn:
            return conn.execute("SELECT 1 FROM staff_leave WHERE staff_id = ? AND status = 'Approved' AND from_day <= ? "
                                "AND to_day >= ?", (staff_id, today, today)).fetchone() is not None

    # ---------------------------------------------------------------- staff ID card
    def _staff_key(self):
        return (self.env.get("SECRET_KEY") or self.secret_key()) + ":staff-card"

    def staff_card_token(self, staff_id):
        sig = hmac.new(self._staff_key().encode(), f"staff:{staff_id}".encode(), hashlib.sha256).hexdigest()[:16]
        return f"{staff_id}.{sig}"

    def check_staff_card(self, token):
        """Anybody can check a staff card: the name, photo-free, job, office and whether the person works now."""
        try:
            sid = int(str(token).split(".", 1)[0])
        except ValueError:
            return None
        if not hmac.compare_digest(self.staff_card_token(sid), str(token)):
            return None
        s = self.staff_member(sid)
        if s is None:
            return None
        return {"name": s["name"], "title": s["title"], "office": s["office"], "kind": WORKER_KINDS.get(s["kind"], s["kind"]),
                "active": s["status"] == "Active", "status": s["status"]}

    # ---------------------------------------------------------------- hubs and warehouses
    def hub_scan(self, user_id, number, action, note=""):
        """A hub or warehouse worker scans a parcel. "in" at the hub of the receiver's town also moves the parcel
        from "In transit" to "At destination hub" (only once, even if two workers scan it at the same time)."""
        if action not in HUB_ACTIONS:
            raise ShopError("Unknown scan.")
        number = re.sub(r"[^A-Za-z0-9]", "", str(number or "")).upper()[:20]
        with self.db() as conn:
            w = self.worker_of(user_id, conn)
            u = conn.execute("SELECT is_admin FROM users WHERE id = ?", (user_id,)).fetchone()
            if not (u and u["is_admin"]) and not (w and w["status"] == "Active"
                                                  and (ALL in w["ability_set"] or "hub.scan" in w["ability_set"])):
                raise ShopError("Only hub and warehouse workers can scan parcels.")
            if not w or w["office_kind"] not in ("hub", "warehouse", "city") or not w["office_place"]:
                raise ShopError("Your office is not a hub or a warehouse with a town.")
            s = conn.execute("SELECT * FROM shipments WHERE number = ?", (number,)).fetchone()
            if s is None:
                raise ShopError("No parcel with this number.")
            if s["status"] not in C.OPEN:
                raise ShopError(f"This parcel is {s['status'].lower()} already.")
            moved = False
            if action == "in" and s["status"] == C.TRANSIT and C.serves(w["office_place"], self._key(s, "to")):
                moved = bool(conn.execute("UPDATE shipments SET status = ?, moved_at = ? WHERE id = ? AND status = ?",
                                          (C.AT_HUB, now(), s["id"], C.TRANSIT)).rowcount)
            status = C.AT_HUB if moved else s["status"]
            text = HUB_ACTIONS[action] + (f": {' '.join(str(note).split())[:120]}" if note else "")
            self._event(conn, s["id"], status, text, w["office"], f"Hub worker {w['name'].split()[0]}")
            if moved:
                self.notify(conn, s["user_id"], f"At the hub: {number}", "Your parcel arrived at the hub of the receiver's town.",
                            url=f"/courier/s/{number}")
            self._staff_log(conn, user_id, w["id"], w["office_id"], "Parcel scanned", f"{number}: {HUB_ACTIONS[action]}")
        return {"number": number, "status": status, "moved": moved}

    def hub_parcels(self, user_id):
        """The open parcels that go to or come from the town of the worker's hub."""
        w = self.worker_of(user_id)
        if not w or not w["office_place"]:
            return []
        with self.db() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM shipments WHERE status IN (?, ?, ?, ?) ORDER BY id DESC LIMIT 400",
                (C.PICKED, C.TRANSIT, C.AT_HUB, C.OUT))]
        out = []
        for s in rows:
            to_here = C.serves(w["office_place"], self._key(s, "to"))
            from_here = C.serves(w["office_place"], self._key(s, "from"))
            if to_here or from_here:
                s["direction"] = "in" if to_here else "out"
                out.append(s)
        return out[:80]

    # ---------------------------------------------------------------- the employees of a seller
    def seller_membership(self, user_id):
        """(shop, abilities): the shop of an owner (abilities = everything) or of an active employee."""
        shop = self.seller_of(user_id)
        if shop:
            return shop, set(SELLER_ABILITIES) | {"owner"}
        with self.db() as conn:
            r = conn.execute("SELECT * FROM seller_team WHERE user_id = ? AND status = 'Active'", (user_id,)).fetchone()
            if r is None:
                return None, set()
            shop = conn.execute("SELECT * FROM sellers WHERE id = ?", (r["seller_id"],)).fetchone()
        return (dict(shop) if shop else None), split(r["abilities"])

    def seller_team(self, seller_id):
        with self.db() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT t.*, u.name, u.email FROM seller_team t JOIN users u ON u.id = t.user_id WHERE t.seller_id = ? "
                "ORDER BY t.status, u.name", (seller_id,))]
        for r in rows:
            r["ability_set"] = split(r["abilities"])
        return rows

    def add_seller_employee(self, owner_id, email, title, abilities):
        shop = self.seller_of(owner_id)
        if not shop:
            raise ShopError("Only the owner of the shop can add employees.")
        user = self.find_user(str(email or "").strip().lower())
        if user is None:
            raise ShopError("There is no Yo Delivery account with this e-mail. The employee must sign up first.")
        if user["id"] == owner_id or self.seller_of(user["id"]):
            raise ShopError("This person has a shop of his own.")
        if self.worker_of(user["id"]) or user["is_admin"]:
            raise ShopError("This person works for Yo Delivery and cannot work for a shop.")
        abil = {a for a in abilities or [] if a in SELLER_ABILITIES}
        if not abil:
            raise ShopError("Please tick at least one thing the employee may do.")
        with self.db() as conn:
            cur = conn.execute("INSERT INTO seller_team (seller_id, user_id, title, abilities, status, added_by, created_at) "
                               "VALUES (?, ?, ?, ?, 'Active', ?, ?) ON CONFLICT (user_id) DO UPDATE SET "
                               "seller_id = excluded.seller_id, title = excluded.title, abilities = excluded.abilities, "
                               "status = 'Active', added_by = excluded.added_by "
                               "WHERE seller_team.status = 'Removed' OR seller_team.seller_id = excluded.seller_id",
                               (shop["id"], user["id"], " ".join(str(title or "").split())[:60] or "Shop assistant",
                                join(abil), owner_id, now()))
            if not cur.rowcount:
                raise ShopError("This person works for another shop.")
            self.notify(conn, user["id"], f"You now work for {shop['shop_name']}",
                        "Open the Seller Centre to start.", url="/seller")

    def remove_seller_employee(self, owner_id, member_id):
        shop = self.seller_of(owner_id)
        if not shop:
            raise ShopError("Only the owner of the shop can remove employees.")
        with self.db() as conn:
            if not conn.execute("UPDATE seller_team SET status = 'Removed' WHERE id = ? AND seller_id = ? AND status = 'Active'",
                                (member_id, shop["id"])).rowcount:
                raise ShopError("Employee not found.")

    # ---------------------------------------------------------------- numbers for the pages
    def staff_numbers(self, user_id):
        rows = self.workers(user_id)
        active = [r for r in rows if r["status"] == "Active"]
        with self.db() as conn:
            on_shift = {r[0] for r in conn.execute("SELECT staff_id FROM staff_shifts WHERE end_at = ''")}
        return {"workers": len(active), "managers": sum(1 for r in active if r["is_manager"]),
                "suspended": sum(1 for r in rows if r["status"] == "Suspended"),
                "on_shift": sum(1 for r in active if r["id"] in on_shift),
                "waiting_leave": sum(1 for r in self.leave_list(user_id) if r["status"] == "Waiting"),
                "kinds": {k: sum(1 for r in active if r["kind"] == k) for k in WORKER_KINDS}}

    def ensure_head_office(self):
        """Every database has a head office (the root of the tree)."""
        with self.db() as conn:
            conn.execute("INSERT INTO offices (parent_id, name, kind, country, place, active, created_at) "
                         "VALUES (NULL, 'Yo Delivery head office', 'head', 'IN', 'Ambala Cantt', 1, ?) "
                         "ON CONFLICT (kind) WHERE kind = 'head' DO NOTHING", (now(),))

    # ---------------------------------------------------------------- accounts of new workers
    def find_user(self, email):
        with self.db() as conn:
            r = conn.execute("SELECT * FROM users WHERE email = ?", (str(email or "").strip().lower(),)).fetchone()
        return dict(r) if r else None

    @staticmethod
    def _random_password():
        return secrets.token_urlsafe(18) + "aA1"

    def create_password_reset(self, user_id, hours=48):
        """A one-time link for a new worker to choose his password (shown to the manager who added him)."""
        token = secrets.token_urlsafe(32)
        expires = (datetime.now() + timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
        with self.db() as conn:
            conn.execute("DELETE FROM password_resets WHERE user_id = ?", (user_id,))
            conn.execute("INSERT INTO password_resets (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                         (hashlib.sha256(token.encode()).hexdigest(), user_id, expires))
        return token
