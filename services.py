"""
services.py - version 8 (mixin class used by Store): one list of every service and page of Yo Delivery, used by

    * the admin side menu (grouped, with a filter box),
    * the "three lines" menu of the shop (profile, my work, services, categories, my account) and its search,
    * "My work": the page a worker needs first (by his role and abilities) and the button that opens it,
    * the quick find of the staff: an order, parcel, ticket, question, customer, worker or page by a few letters.

Before version 8 the admin menu was a long list inside a template, the worker's pages were worked out in a view and
the shop header had its own links. Now a new page is added in ONE place and shows up everywhere a person can look
for it - but only for people who may open it (the same abilities as the pages themselves, see staff.py).
"""

import re

import staff as S

# endpoint, icon (HTML entity), label, endpoints that light it up, badge key, ability to see it, group, search words
NAV = [
    ("admin_dashboard", "&#128202;", "Dashboard", ["admin_dashboard"], "", "dashboard.view", "General", "numbers sales today overview"),
    ("board_page", "&#128203;", "Task board", ["board_page"], "board", "board.use", "General", "kanban to do team tasks"),
    ("admin_orders", "&#128230;", "Orders", ["admin_orders", "admin_order"], "", "orders.view", "Shop", "order status pack ship cancel"),
    ("admin_returns", "&#8634;", "Returns", ["admin_returns"], "returns", "orders.view", "Shop", "refund return request"),
    ("admin_risk", "&#128737;", "Safety checks", ["admin_risk"], "risk", "orders.view", "Shop", "fraud risk held order release"),
    ("admin_products", "&#127991;", "Products", ["admin_products", "admin_product_new", "admin_product_edit"], "products", "products.manage", "Shop", "catalog stock price approve"),
    ("admin_categories", "&#128193;", "Categories", ["admin_categories"], "", "products.manage", "Shop", "category kinds of goods"),
    ("admin_sellers", "&#127978;", "Sellers", ["admin_sellers"], "sellers", "sellers.manage", "Shop", "marketplace shop approve payout"),
    ("admin_reviews", "&#11088;", "Reviews", ["admin_reviews"], "", "reviews.manage", "Shop", "stars rating hide"),
    ("admin_coupons", "&#127873;", "Coupons", ["admin_coupons"], "", "coupons.manage", "Shop", "discount code promo"),
    ("admin_shipments", "&#128666;", "Shipments", ["admin_shipments", "admin_shipment"], "shipments", "shipments.view", "Courier", "parcel tracking assign"),
    ("admin_couriers", "&#128757;", "Couriers", ["admin_couriers"], "couriers", "couriers.manage", "Courier", "driver rider approve"),
    ("admin_partners", "&#129309;", "Courier partners", ["admin_partners"], "", "partners.manage", "Courier", "partner api key webhook"),
    ("admin_customs", "&#128203;", "Customs rules", ["admin_customs"], "", "customs.manage", "Courier", "duty abroad declaration"),
    ("admin_claims", "&#128176;", "Parcel claims", ["admin_claims"], "claims", "support.manage", "Courier", "lost damaged compensation"),
    ("admin_tickets", "&#127915;", "Tickets", ["admin_tickets", "admin_ticket", "admin_ticket_trips", "admin_ticket_services", "admin_ticket_staff"], "tickets", "tickets.view", "Tickets", "bus train flight booking pnr trip"),
    ("admin_gtfs", "&#128467;", "Timetables (GTFS)", ["admin_gtfs"], "", "transport.manage", "Tickets", "timetable import live delays alerts"),
    ("admin_yopay", "&#128179;", "Yo Pay", ["admin_yopay"], "", "yopay.view", "Money", "wallet ledger balance"),
    ("admin_invoices", "&#129534;", "Invoices", ["admin_invoices"], "", "invoices.view", "Money", "tax invoice credit note e-invoice irn"),
    ("admin_taxes", "&#129534;", "Tax rules", ["admin_taxes"], "", "yopay.view", "Money", "vat gst country rate"),
    ("admin_tax_categories", "&#127991;", "Tax by kind of goods", ["admin_tax_categories"], "", "yopay.view", "Money", "gst rate category checked"),
    ("admin_bank", "&#127974;", "Bank transfers", ["admin_bank"], "", "yopay.view", "Money", "payout seller bank"),
    ("admin_reconcile", "&#9878;", "Reconcile payments", ["admin_reconcile"], "", "yopay.view", "Money", "statement provider compare callbacks"),
    ("admin_payroll", "&#128176;", "Payroll", ["admin_payroll", "admin_payroll_run"], "", "payroll.manage", "Money", "salary payslip pay run tax"),
    ("admin_approvals", "&#9997;", "Approvals", ["admin_approvals"], "approvals", "payroll.manage", "Money", "second person dual control approve"),
    ("admin_customers", "&#128101;", "Customers", ["admin_customers", "admin_customer"], "", "customers.view", "People", "user account block"),
    ("admin_support", "&#127911;", "Support desk", ["admin_support", "admin_support_ticket"], "support", "support.manage", "People", "help question ticket answer"),
    ("admin_staff", "&#128188;", "Staff", ["admin_staff", "admin_staff_edit", "admin_staff_log"], "leave", "staff.view", "People", "worker employee abilities team"),
    ("admin_offices", "&#127970;", "Offices", ["admin_offices"], "", "staff.view", "People", "branch hub tree place clock-in code"),
    ("admin_rota", "&#128197;", "Shifts & attendance", ["admin_rota", "admin_attendance"], "", "staff.view", "People", "rota shift plan clock late absent"),
    ("admin_tasks", "&#9989;", "Staff tasks", ["admin_tasks", "admin_performance"], "staff_tasks", "staff.view", "People", "task performance"),
    ("admin_training", "&#127891;", "Training & contracts", ["admin_training", "admin_staff_people"], "", "staff.view", "People", "course certificate contract"),
    ("admin_holidays", "&#127958;", "Public holidays", ["admin_holidays"], "", "staff.view", "People", "holiday calendar leave rules country"),
    ("admin_notices", "&#128227;", "Staff notices", ["admin_notices"], "", "staff.view", "People", "announcement news for workers"),
    ("admin_swaps", "&#128260;", "Shift swaps", ["admin_swaps"], "", "staff.view", "People", "swap exchange shift colleague approve"),
    ("admin_site", "&#128221;", "Site content", ["admin_site", "admin_site_page", "admin_site_contacts", "admin_site_slides", "admin_translate", "admin_page_preview"], "", "site.manage", "Content", "about contact pages banners footer edit text"),
    ("admin_kb", "&#128218;", "Help articles", ["admin_kb"], "", "site.manage", "Content", "knowledge base faq help centre"),
    ("admin_search_misses", "&#128269;", "Searches with no result", ["admin_search_misses"], "", "site.manage", "Content", "search log missing words typo"),
    ("admin_blog", "&#128240;", "Blog & News", ["admin_blog", "admin_post_edit"], "posts", "blog.manage", "Content", "story post article"),
    ("admin_rewards", "&#127873;", "Rewards & Quiz", ["admin_rewards"], "", "rewards.manage", "Content", "points quiz questions"),
    ("admin_photos", "&#128247;", "Photo checks", ["admin_photos"], "", "photos.review", "Trust", "picture blur check"),
    ("admin_id_checks", "&#129706;", "ID checks", ["admin_id_checks"], "id_checks", "photos.review", "Trust", "identity document face live"),
    ("admin_chat", "&#128172;", "Chat moderation", ["admin_chat"], "chat", "chat.moderate", "Trust", "messages held rude model"),
    ("admin_analytics", "&#128202;", "Analytics", ["admin_analytics"], "", "analytics.view", "Data", "warehouse numbers branch csv"),
    ("admin_forecast", "&#128200;", "Hub forecast", ["admin_forecast"], "", "analytics.view", "Data", "parcels workers next weeks holidays"),
    ("admin_model", "&#129504;", "Recommendations", ["admin_model"], "", "model.manage", "Data", "similar products model"),
    ("admin_experiments", "&#128300;", "A/B tests", ["admin_experiments"], "", "model.manage", "Data", "experiment variant"),
    ("admin_monitor", "&#128200;", "Monitoring", ["admin_monitor"], "alerts", "monitor.view", "System", "health jobs queue alerts cache"),
    ("admin_limits", "&#9203;", "Rate limits", ["admin_limits"], "", "monitor.view", "System", "too many requests blocked account login"),
    ("admin_activity", "&#128221;", "Activity Log", ["admin_activity"], "", "activity.view", "System", "log who did what"),
    ("admin_database", "&#128451;", "Database", ["admin_database"], "", "database.view", "System", "tables rows"),
    ("admin_backups", "&#128190;", "Backups", ["admin_backups"], "", "database.view", "System", "backup restore export"),
    ("admin_privacy", "&#128274;", "Privacy register", ["admin_privacy"], "", "privacy.manage", "System", "personal data keeping times gdpr"),
    ("admin_settings", "&#9881;", "Shop Settings", ["admin_settings"], "", "settings.manage", "System", "fees limits announcement two-step"),
    ("admin_account", "&#128100;", "My Account", ["admin_account"], "", "self", "System", "password two-step profile"),
]
GROUPS = ["General", "Shop", "Courier", "Tickets", "Money", "People", "Content", "Trust", "Data", "System"]

# the worker's own pages (everybody with a staff record)
WORKER_PAGES = [
    ("staff_home", "&#128188;", "My work", "", "today shift clock in tasks"),
    ("staff_me", "&#128196;", "My contract, holidays and payslips", "", "salary leave balance training statement"),
    ("staff_card", "&#129706;", "My staff ID card", "", "badge qr"),
    ("hub_page", "&#128230;", "Scan parcels at the hub", "hub.scan", "scan sort in out"),
    ("staff_directory", "&#128101;", "Colleagues", "", "directory people phone office who"),
]
# services for everybody
PUBLIC = [
    ("index", "&#128717;", "Shop", "buy products store home"),
    ("courier_home", "&#128666;", "Send a parcel (Yo Courier)", "courier pickup delivery book"),
    ("track", "&#128205;", "Track an order or parcel", "tracking where is my"),
    ("tickets_home", "&#127915;", "Bus, train and flight tickets", "travel trip book ticket"),
    ("pay_home", "&#128179;", "Yo Pay wallet", "money balance add send"),
    ("pay_emi", "&#128290;", "EMI calculator", "instalments monthly"),
    ("rewards_home", "&#127873;", "Rewards & daily quiz", "points coupons quiz"),
    ("blog_home", "&#128240;", "Blog & News", "stories articles"),
    ("places_page", "&#127757;", "Places we deliver to", "town village country postal code"),
    ("help_page", "&#10067;", "Help Centre", "faq answers questions"),
    ("support_home", "&#127911;", "Ask our support team", "question problem contact"),
    ("contact_page", "&#9742;", "Contact us", "phone email address office"),
    ("about", "&#8505;", "About Yo Delivery", "company developer"),
    ("sell", "&#127978;", "Sell on Yo Delivery", "seller marketplace"),
    ("driver_home", "&#128757;", "Deliver with Yo", "courier job rider"),
]
ACCOUNT = [
    ("account", "&#128100;", "My profile", "name email password language"),
    ("orders", "&#128230;", "My orders", "returns buy again invoice"),
    ("courier_shipments", "&#128666;", "My shipments", "parcels sent"),
    ("tickets_my", "&#127915;", "My trips", "tickets pnr"),
    ("notifications", "&#128276;", "Notifications", "messages from yo"),
    ("messages", "&#128172;", "Messages", "chat with sellers"),
    ("wishlist", "&#9825;", "Wishlist", "saved items"),
    ("addresses", "&#128205;", "Saved addresses", "delivery address"),
    ("my_data", "&#128274;", "My data and privacy", "download delete account"),
]

# the page a worker opens first, by role; the ability is checked again before it is offered
ROLE_HOME = {"support": "admin_support", "finance": "admin_payroll", "catalog": "admin_products",
             "moderator": "admin_chat", "hub": "hub_page", "courier_lead": "admin_shipments", "courier": "driver_home",
             "conductor": "conductor_home", "ticket_desk": "admin_tickets", "transport_manager": "admin_tickets",
             "office_manager": "admin_staff", "country_manager": "admin_dashboard", "it": "admin_monitor"}
ABILITY_HOME = [("hub.scan", "hub_page"), ("support.manage", "admin_support"), ("payroll.manage", "admin_payroll"),
                ("products.manage", "admin_products"), ("chat.moderate", "admin_chat"),
                ("tickets.manage", "admin_tickets"), ("shipments.manage", "admin_shipments"),
                ("staff.manage", "admin_staff"), ("monitor.view", "admin_monitor"), ("site.manage", "admin_site")]
HOME_LABELS = {"hub_page": "Scan parcels at the hub", "driver_home": "Courier app", "conductor_home": "Conductor app"}
_NAV_BY_ENDPOINT = {n[0]: n for n in NAV}

_NUMBER = [("order", re.compile(r"^OD\d{6,}$")), ("shipment", re.compile(r"^YC\d{6,}$")),
           ("ticket", re.compile(r"^S\d{6}$")), ("pnr", re.compile(r"^[A-HJ-NP-Z2-9]{6}$"))]


def norm(text):
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def matches(q, *texts):
    """Every word of the search is the start of a word (or inside a word) of the texts."""
    hay = norm(" ".join(texts))
    return all(w in hay for w in norm(q).split())


class ServicesMixin:

    # ------------------------------------------------------------------ what may this person open?
    def _may(self, user, abil, need):
        if need in ("", "self"):
            return True
        return bool(user and (user.get("is_admin") or S.ALL in abil or need in abil))

    def nav_for(self, user_id):
        """The admin menu of this person: [(group, [item dicts])], only pages he may open."""
        user = self.get_user(user_id) if user_id else None
        w = None if not user or user.get("is_admin") else self.worker_of(user_id)
        if not user or not (user.get("is_admin") or (w and w["status"] == "Active")):
            return []
        abil = self.abilities_of(user_id)
        groups = {g: [] for g in GROUPS}
        for endpoint, icon, label, match, badge, need, group, words in NAV:
            if self._may(user, abil, need):
                groups[group].append({"endpoint": endpoint, "icon": icon, "label": label, "match": match,
                                      "badge": badge, "words": words,
                                      "args": {"scope": "admin"} if endpoint == "board_page" else {}})
        return [(g, items) for g, items in groups.items() if items]

    def work_home(self, user_id):
        """Where this worker's work starts: {"endpoint", "label", "position", "office"} (or None for a customer).
        A courier opens the courier app, a hub worker the scanner, a support agent the support desk ...; the
        administrator the dashboard; everybody else "My work"."""
        user = self.get_user(user_id) if user_id else None
        if not user:
            return None
        w = self.worker_of(user_id)
        if w is None or w["status"] != "Active":
            if user.get("is_admin"):
                return {"endpoint": "admin_dashboard", "label": "Admin panel", "position": "Administrator", "office": ""}
            return None
        abil = self.abilities_of(user_id)
        endpoint = ROLE_HOME.get(w["role"] or "", "")
        if endpoint == "driver_home" and not self.courier_of(user_id):
            endpoint = ""
        if endpoint == "conductor_home" and not self.staff_of(user_id):
            endpoint = ""
        if endpoint in _NAV_BY_ENDPOINT and not self._may(user, abil, _NAV_BY_ENDPOINT[endpoint][5]):
            endpoint = ""
        if endpoint == "hub_page" and not self._may(user, abil, "hub.scan"):
            endpoint = ""
        if not endpoint:
            if w["kind"] == "courier" and self.courier_of(user_id):
                endpoint = "driver_home"
            else:
                endpoint = next((e for a, e in ABILITY_HOME if self._may(user, abil, a)), "staff_home")
        label = HOME_LABELS.get(endpoint) or (_NAV_BY_ENDPOINT[endpoint][2] if endpoint in _NAV_BY_ENDPOINT else "My work")
        return {"endpoint": endpoint, "label": label, "position": w["title"], "office": w["office"],
                "manager": bool(w["is_manager"])}

    def job_info(self, user_id):
        """Everything the profile shows about a worker's job (None for a customer)."""
        w = self.worker_of(user_id)
        if not w:
            return None
        role = S.ROLES.get(w["role"] or "")
        return {"position": w["title"], "kind": S.WORKER_KINDS.get(w["kind"], w["kind"]), "role": role[0] if role else "",
                "office": w["office"], "path": [o["name"] for o in self.office_path(w["office_id"])],
                "manager": self.manager_of(user_id), "is_manager": bool(w["is_manager"]), "status": w["status"],
                "since": w["created_at"], "abilities": len(w["ability_set"]), "home": self.work_home(user_id),
                "staff_id": w["id"]}

    def menu_for(self, user_id):
        """The "three lines" menu: [(title, [items])] with the worker's pages first. Items are dicts with endpoint,
        args, icon, label and search words; the view turns the endpoints into addresses."""
        user = self.get_user(user_id) if user_id else None
        out = []
        if user:
            w = self.worker_of(user_id)
            abil = self.abilities_of(user_id) if (w or user.get("is_admin")) else set()
            if (w and w["status"] == "Active") or user.get("is_admin"):
                mine = [{"endpoint": e, "args": {}, "icon": i, "label": lab, "words": words}
                        for e, i, lab, need, words in WORKER_PAGES if w and self._may(user, abil, need)]
                for _, items in self.nav_for(user_id):
                    mine += [dict(it) for it in items if it["endpoint"] != "admin_account"]
                if self.courier_of(user_id):
                    mine.append({"endpoint": "driver_home", "args": {}, "icon": "&#128757;", "label": "Courier app",
                                 "words": "jobs deliver route"})
                if self.staff_of(user_id):
                    mine.append({"endpoint": "conductor_home", "args": {}, "icon": "&#127915;", "label": "Conductor app",
                                 "words": "check tickets scan"})
                out.append(("My work", mine))
            shop = self.seller_membership(user_id)[0]
            if shop:
                out.append(("My shop", [{"endpoint": "seller_dashboard", "args": {}, "icon": "&#127978;",
                                         "label": "Seller Centre", "words": "shop products orders payouts"}]))
        out.append(("Yo services", [{"endpoint": e, "args": {}, "icon": i, "label": lab, "words": words}
                                    for e, i, lab, words in PUBLIC]))
        out.append(("Shop by category", [{"endpoint": "category", "args": {"category": c["slug"]}, "icon": "&#128722;",
                                          "label": c["name"], "words": "category " + c["slug"]}
                                         for c in self.categories()]))
        if user:
            out.append(("My account", [{"endpoint": e, "args": {}, "icon": i, "label": lab, "words": words}
                                       for e, i, lab, words in ACCOUNT]))
        return out

    # ------------------------------------------------------------------ quick find
    def quick_find(self, user_id, q, limit=6):
        """Find by a few letters. Returns [{"kind", "label", "sub", "endpoint", "args"}]; only what this person may
        see: his own orders, parcels, tickets and questions - and, with the ability, those of everybody."""
        q = str(q or "").strip()[:80]
        user = self.get_user(user_id) if user_id else None
        if len(q) < 2:
            return []
        abil = self.abilities_of(user_id) if user else set()

        def may(a):
            return self._may(user, abil, a)
        out = []
        code = q.upper().replace(" ", "")
        kind = next((k for k, rx in _NUMBER if rx.match(code)), "")
        with self.db() as conn:
            if kind == "order" and user:
                r = conn.execute("SELECT * FROM orders WHERE number = ?", (code,)).fetchone()   # (version 9: orders have no status column)
                r = dict(r, status=self.status(dict(r))) if r else None
                if r and may("orders.view"):
                    out.append({"kind": "Order", "label": r["number"], "sub": r["status"], "endpoint": "admin_order",
                                "args": {"number": r["number"]}})
                elif r and r["user_id"] == user_id:
                    out.append({"kind": "Order", "label": r["number"], "sub": r["status"], "endpoint": "order",
                                "args": {"number": r["number"]}})
            if kind == "shipment":
                r = conn.execute("SELECT number, user_id, status, to_city FROM shipments WHERE number = ?", (code,)).fetchone()
                if r and user and may("shipments.view"):
                    out.append({"kind": "Parcel", "label": r["number"], "sub": f"{r['status']} · {r['to_city']}",
                                "endpoint": "admin_shipment", "args": {"number": r["number"]}})
                elif r and user and r["user_id"] == user_id:
                    out.append({"kind": "Parcel", "label": r["number"], "sub": r["status"], "endpoint": "courier_shipment",
                                "args": {"number": r["number"]}})
                elif r:                                  # anybody can follow a parcel by its number
                    out.append({"kind": "Parcel", "label": r["number"], "sub": r["status"], "endpoint": "courier_track",
                                "args": {"number": r["number"]}})
            if kind == "ticket" and user:
                r = conn.execute("SELECT number, user_id, subject, status FROM support_tickets WHERE number = ?",
                                 (code,)).fetchone()
                if r and may("support.manage"):
                    out.append({"kind": "Question", "label": r["number"], "sub": r["subject"][:60],
                                "endpoint": "admin_support_ticket", "args": {"number": r["number"]}})
                elif r and r["user_id"] == user_id:
                    out.append({"kind": "Question", "label": r["number"], "sub": r["subject"][:60],
                                "endpoint": "support_ticket_page", "args": {"number": r["number"]}})
            if kind == "pnr" and user:
                r = conn.execute("SELECT pnr, user_id, status FROM ticket_bookings WHERE pnr = ?", (code,)).fetchone()
                if r and may("tickets.view"):
                    out.append({"kind": "Ticket", "label": r["pnr"], "sub": r["status"], "endpoint": "admin_ticket",
                                "args": {"pnr": r["pnr"]}})
                elif r and r["user_id"] == user_id:
                    out.append({"kind": "Ticket", "label": r["pnr"], "sub": r["status"], "endpoint": "tickets_booking",
                                "args": {"pnr": r["pnr"]}})
            like = "%" + q.lower().replace("%", "").replace("_", "") + "%"
            if user and may("customers.view") and not kind:
                for r in conn.execute("SELECT id, name, email FROM users WHERE LOWER(name) LIKE ? OR LOWER(email) LIKE ? "
                                      "OR mobile LIKE ? ORDER BY id LIMIT ?", (like, like, like, limit)):
                    out.append({"kind": "Customer", "label": r["name"], "sub": r["email"], "endpoint": "admin_customer",
                                "args": {"uid": r["id"]}})
        if user and not kind and (may("staff.view")):
            for wk in self.workers(user_id, q=q)[:limit]:
                out.append({"kind": "Worker", "label": wk["name"], "sub": f"{wk['title']} · {wk['office']}",
                            "endpoint": "admin_staff_edit", "args": {"staff_id": wk["id"]}})
        for title, items in self.menu_for(user_id):          # pages and services
            for it in items:
                if matches(q, it["label"], it["words"]):
                    out.append({"kind": "Page", "label": it["label"], "sub": title, "endpoint": it["endpoint"],
                                "args": it["args"]})
        seen, unique = set(), []
        for r in out:
            key = (r["endpoint"], tuple(sorted(r["args"].items())))
            if key not in seen:
                seen.add(key)
                unique.append(r)
        return unique[:30]
