"""
staff_views.py - version 6: the pages of the people who run Yo Delivery.

    /staff                 My work: my office, my abilities, clock in / out, time off, my team (managers)
    /staff/card            my staff ID card (signed QR code); /staff/verify/<token> lets anybody check it
    /hub                   scan parcels at a hub or a warehouse
    /admin/staff           workers of my branch, add a worker; /admin/staff/<id> change abilities, move, remove
    /admin/offices         the office tree; add, rename or close offices of my branch
    /admin/staff/log       every change of workers and offices
    /seller/team           the shop owner adds employees with their own abilities
"""

from flask import abort, flash, redirect, render_template, request, url_for

import staff as S
from core import ShopError


def register(app, store, h):

    def worker_page(view):
        """Pages for workers (and the administrator)."""
        from functools import wraps

        @wraps(view)
        def wrapper(*a, **kw):
            u = store.get_user(h.user_id())
            w = store.worker_of(h.user_id())
            if not (u and u["is_admin"]) and (not w or w["status"] == "Removed"):
                flash("This page is for the workers of Yo Delivery.", "info")
                return redirect(url_for("index"))
            return view(*a, **kw)
        return h.login_required(wrapper)

    def form_data():
        f = request.form
        return {"email": f.get("email", ""), "name": f.get("name", ""), "mobile": f.get("mobile", ""),
                "office_id": f.get("office_id", ""), "title": f.get("title", ""), "kind": f.get("kind", "office"),
                "role": f.get("role", ""), "abilities": f.getlist("abilities"), "is_manager": f.get("is_manager") == "1",
                "vehicle": f.get("vehicle", "")}

    def common():
        uid = h.user_id()
        return {"ABILITIES": S.ABILITIES, "ROLES": S.ROLES, "WORKER_KINDS": S.WORKER_KINDS,
                "OFFICE_KINDS": S.OFFICE_KINDS, "my_abilities": store.abilities_of(uid), "ALL": S.ALL,
                "branch_offices": [o for o in store.office_tree() if store.branch_of(uid) is None
                                   or o["id"] in store.branch_of(uid)]}

    # ------------------------------------------------------------ My work
    @app.route("/staff")
    @worker_page
    def staff_home():
        uid = h.user_id()
        w = store.worker_of(uid)
        if w is None:                              # the administrator has no staff record
            return redirect(url_for("admin_staff"))
        abil = store.abilities_of(uid)
        pages, seen = [], set()                    # one link per ability (the first page of it)
        for prefix, see, _ in S.ABILITY_FOR_PATH:
            if see != "self" and see not in seen and (S.ALL in abil or see in abil):
                seen.add(see)
                pages.append((prefix, see))
        team = store.workers(uid) if (S.ALL in abil or "staff.view" in abil) else []
        return render_template("staff/home.html", w=w, path=store.office_path(w["office_id"]), pages=pages,
                               shift=store.shift_status(uid), leave_mine=store.leave_list(uid, mine=True),
                               leave_team=[r for r in store.leave_list(uid) if r["status"] == "Waiting"],
                               team=[t for t in team if t["id"] != w["id"] and t["status"] != "Removed"][:30],
                               on_leave=store.on_leave_today(w["id"]), rota=store.my_rota(uid, 7),
                               tasks=[t for t in store.task_list(uid, mine=True) if t["status"] in ("To do", "In progress")
                                      or (t["done_at"] or "")[:10] == h.now()[:10]],
                               is_conductor=store.staff_of(uid) is not None,
                               is_courier=bool(store.courier_of(uid)), **common())

    @app.route("/staff/clock", methods=["POST"])
    @worker_page
    def staff_clock():
        try:
            if request.form.get("action") == "in":
                f = request.form                          # version 7: the position of the phone
                flag = store.clock_in_at(h.user_id(), f.get("lat") or None, f.get("lng") or None, f.get("acc") or None)
                flash("You are clocked in. Have a good shift!", "success")
                if flag == "no location":
                    flash("We could not read your position, so your manager sees this clock-in as 'no location'.", "info")
                elif flag == "remote":
                    flash("You clocked in away from your office (remote work is allowed in your office).", "info")
            else:
                store.clock_out(h.user_id())
                flash("You are clocked out.", "success")
        except ShopError as e:
            flash(str(e), "error")
        return redirect(url_for("staff_home"))

    @app.route("/staff/leave", methods=["POST"])
    @worker_page
    def staff_leave():
        try:
            store.request_leave_kind(h.user_id(), request.form.get("from_day"), request.form.get("to_day"),
                                     request.form.get("kind", "Annual"), request.form.get("reason", ""))
            flash("Your request was sent to your manager.", "success")
        except ShopError as e:
            flash(str(e), "error")
        return redirect(url_for("staff_home"))

    @app.route("/staff/leave/<int:leave_id>/<decision>", methods=["POST"])
    @worker_page
    def staff_leave_decide(leave_id, decision):
        try:
            store.decide_leave(h.user_id(), leave_id, decision == "approve")
            flash("Time off approved." if decision == "approve" else "Time off refused.", "success")
        except ShopError as e:
            flash(str(e), "error")
        return redirect(request.form.get("back") or url_for("staff_home"))

    @app.route("/staff/card")
    @worker_page
    def staff_card():
        w = store.worker_of(h.user_id())
        if w is None:
            abort(404)
        token = store.staff_card_token(w["id"])
        return render_template("staff/card.html", w=w, token=token,
                               verify_url=h.base_url() + url_for("staff_verify", token=token).lstrip("/"))

    @app.route("/staff/verify/<token>")
    def staff_verify(token):
        """Public: is this person really working for Yo Delivery? (name, job and office only)"""
        return render_template("staff/verify.html", v=store.check_staff_card(token[:60]))

    # ------------------------------------------------------------ hubs and warehouses
    @app.route("/hub", methods=["GET", "POST"])
    @worker_page
    def hub_page():
        uid = h.user_id()
        if not store.can(uid, "hub.scan"):
            flash("Only hub and warehouse workers can scan parcels.", "info")
            return redirect(url_for("staff_home"))
        result = None
        if request.method == "POST":
            try:
                result = store.hub_scan(uid, request.form.get("number"), request.form.get("action", "in"),
                                        request.form.get("note", ""))
                h.act("Hub scan", f"{result['number']} {request.form.get('action')}")
                flash(f"{result['number']}: {S.HUB_ACTIONS[request.form.get('action', 'in')]}."
                      + (" The parcel is now at the destination hub." if result["moved"] else ""), "success")
            except ShopError as e:
                flash(str(e), "error")
        return render_template("staff/hub.html", w=store.worker_of(uid), parcels=store.hub_parcels(uid),
                               ACTIONS=S.HUB_ACTIONS, result=result)

    # ------------------------------------------------------------ admin: workers
    @app.route("/admin/staff", methods=["GET", "POST"])
    @h.admin_required
    def admin_staff():
        uid = h.user_id()
        link = None
        if request.method == "POST":
            try:
                sid, token = store.add_worker(uid, form_data())
                h.act("Staff: worker added", request.form.get("email", ""))
                flash("Worker added.", "success")
                if token:
                    link = h.base_url() + f"reset/{token}"
                    flash("A new account was made. Give the worker this one-time link (valid 48 hours) to choose a "
                          "password: " + link, "info")
                return redirect(url_for("admin_staff_edit", staff_id=sid))
            except ShopError as e:
                flash(str(e), "error")
        office = request.args.get("office", "")
        return h.admin_page("admin/staff.html", rows=store.workers(uid, office or None, request.args.get("status", ""),
                                                                  request.args.get("q", "").strip()),
                            office=office, status=request.args.get("status", ""), q=request.args.get("q", ""),
                            nums=store.staff_numbers(uid), f=request.form, **common())

    @app.route("/admin/staff/<int:staff_id>", methods=["GET", "POST"])
    @h.admin_required
    def admin_staff_edit(staff_id):
        uid = h.user_id()
        t = store.staff_member(staff_id)
        branch = store.branch_of(uid)
        if t is None or (branch is not None and t["office_id"] not in branch):
            abort(404)
        if request.method == "POST":
            action = request.form.get("action", "save")
            try:
                if action == "save":
                    store.update_worker(uid, staff_id, form_data())
                    flash("Saved.", "success")
                elif action in ("suspend", "restore", "remove"):
                    store.set_worker_status(uid, staff_id, {"suspend": "Suspended", "restore": "Active",
                                                            "remove": "Removed"}[action], request.form.get("reason", ""))
                    flash({"suspend": "Worker suspended.", "restore": "Worker can work again.",
                           "remove": "Worker removed."}[action], "success")
                h.act("Staff: " + action, t["email"])
                if action == "remove":
                    return redirect(url_for("admin_staff"))
            except ShopError as e:
                flash(str(e), "error")
            return redirect(url_for("admin_staff_edit", staff_id=staff_id))
        me = store.worker_of(uid)
        u = store.get_user(uid)
        may = bool(u["is_admin"]) or (me is not None and store._may_manage(False, me, branch or set(), t)
                                      and store.can(uid, "staff.manage"))
        return h.admin_page("admin/staff_edit.html", t=t, may=may, path=store.office_path(t["office_id"]),
                            shift=store.shift_status(t["user_id"]),
                            log=[r for r in store.staff_log(uid, 300) if r["staff_id"] == staff_id][:20], **common())

    @app.route("/admin/staff/log")
    @h.admin_required
    def admin_staff_log():
        a = request.args
        return h.admin_page("admin/staff_log.html", rows=store.staff_log(h.user_id(), 300, a.get("who", "").strip(),
                                                                         a.get("action", "").strip(),
                                                                         a.get("office") or None,
                                                                         int(a.get("days") or 0), a.get("kind", "")),
                            fil=a, **common())

    # ------------------------------------------------------------ admin: offices
    @app.route("/admin/offices", methods=["GET", "POST"])
    @h.admin_required
    def admin_offices():
        uid = h.user_id()
        if request.method == "POST":
            f = request.form
            try:
                if f.get("action") == "add":
                    store.add_office(uid, int(f.get("parent_id") or 0), f.get("name"), f.get("kind"),
                                     f.get("country", ""), f.get("place", ""), int(f.get("operator_id") or 0) or None)
                    flash("Office added.", "success")
                else:
                    store.rename_office(uid, int(f.get("office_id") or 0), f.get("name"), f.get("action") != "close")
                    flash("Office closed." if f.get("action") == "close" else "Office saved.", "success")
                h.act("Staff: offices", f.get("name", ""))
            except (ShopError, ValueError) as e:
                flash(str(e) if isinstance(e, ShopError) else "Please check the form.", "error")
            return redirect(url_for("admin_offices"))
        branch = store.branch_of(uid)
        tree = [o for o in store.office_tree() if branch is None or o["id"] in branch]
        managers = {}
        for wk in store.workers(uid):
            if wk["is_manager"] and wk["status"] == "Active":
                managers.setdefault(wk["office_id"], []).append(wk["name"])
        return h.admin_page("admin/offices.html", tree=tree, managers=managers, operators=store.ticket_operators(),
                            **common())

    # ------------------------------------------------------------ the employees of a seller
    @app.route("/seller/team", methods=["GET", "POST"])
    @h.seller_required
    def seller_team_page():
        shop = request.shop
        if request.method == "POST":
            try:
                if request.form.get("action") == "remove":
                    store.remove_seller_employee(h.user_id(), int(request.form.get("member_id") or 0))
                    flash("Employee removed.", "success")
                else:
                    store.add_seller_employee(h.user_id(), request.form.get("email"), request.form.get("title"),
                                              request.form.getlist("abilities"))
                    flash("Employee added. He can now open the Seller Centre with his own account.", "success")
            except ShopError as e:
                flash(str(e), "error")
            return redirect(url_for("seller_team_page"))
        return render_template("seller/team.html", shop=shop, team=store.seller_team(shop["id"]),
                               SELLER_ABILITIES=S.SELLER_ABILITIES)
