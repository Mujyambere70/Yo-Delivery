"""
tickets_views.py - the pages of Yo Tickets: search, seat map, payment and e-ticket (travellers), the conductor
app (/conductor, staff of a transport company) and the admin pages (/admin/tickets...).
"""

from datetime import date, timedelta
from functools import wraps

from flask import abort, flash, jsonify, redirect, render_template, request, url_for

import geo
import i18n
import tickets as TK
import world
from core import ShopError
from yopay import emi_plans


def register(app, store, h):
    login_required, admin_required, user_id, act = h.login_required, h.admin_required, h.user_id, h.act

    def form_defaults(src=None):
        src = src if src is not None else request.args
        mode = src.get("mode", "bus") if src.get("mode") in TK.MODES else "bus"
        f = {"mode": mode, "from": src.get("from", "Ambala Cantt"), "to": src.get("to", "New Delhi"),
             "from_place": src.get("from_place", ""), "to_place": src.get("to_place", ""),
             "date": src.get("date") or (date.today() + timedelta(days=1)).isoformat(),
             "pax": src.get("pax", "1"), "ret": src.get("ret", ""), "return_of": src.get("return_of", "")[:8]}
        for side in ("from", "to"):                     # a place key in the address: show its name
            key = f[side + "_place"] or (f[side] if f[side].startswith("air:") or geo.ID_RE.fullmatch(f[side])
                                         else "")
            if key:
                p = TK.place(key)
                if p:
                    f[side], f[side + "_place"] = p["name"], p["key"]
        return f

    def trip_args(**kw):
        """The round-trip / connection values that travel from page to page."""
        src = request.values
        out = {k: src.get(k) for k in ("ret", "return_of", "then", "link") if src.get(k)}
        out.update({k: v for k, v in kw.items() if v})
        return out

    def common():
        return {"MODES": TK.MODES, "ICONS": TK.MODE_ICONS, "PLACES": TK.place_names(), "INDIA": TK.place_names("bus"),
                "today": date.today().isoformat(), "last_day": (date.today() + timedelta(days=TK.SEARCH_DAYS)).isoformat(),
                "MAX_PAX": TK.MAX_PASSENGERS, "HOLD": TK.HOLD_MINUTES, "CHART": TK.CHART_HOURS,
                "WAITLISTED": TK.WAITLISTED}

    def popular():
        return [{"mode": m, "from": a, "to": b, "url": url_for("tickets_route", mode=m, route=f"{TK.slug(a)}-to-{TK.slug(b)}")}
                for m, a, b in TK.POPULAR_ROUTES]

    # ======================================================
    # Travellers
    # ======================================================

    @app.route("/tickets")
    def tickets_home():
        nxt = None
        if user_id():
            up = [b for b in store.my_bookings(user_id()) if b["upcoming"] and b["status"] == TK.CONFIRMED]
            nxt = up[0] if up else None
        return render_template("tickets/home.html", f=form_defaults(), popular=popular(), nxt=nxt,
                               RULES=TK.REFUND_RULES, CLOSE=TK.CLOSE_MINUTES, **common())

    @app.route("/tickets/search")
    def tickets_search():
        f = form_defaults()
        sort = request.args.get("sort", "depart")
        try:
            r = store.search_trips(f["mode"], f["from_place"] or f["from"], f["to_place"] or f["to"], f["date"], f["pax"])
        except ShopError as e:
            flash(str(e), "error")
            return render_template("tickets/results.html", f=f, r=None, sort=sort, popular=popular(), **common())
        if sort == "price":
            r["trips"].sort(key=lambda t: t["cheapest"])
            r["connections"].sort(key=lambda t: t["cheapest"])
        elif sort == "duration":
            r["trips"].sort(key=lambda t: t["duration"])
            r["connections"].sort(key=lambda t: t["duration"])
        d = date.fromisoformat(r["day"])
        days = [(d + timedelta(days=i)).isoformat() for i in range(-2, 3) if d + timedelta(days=i) >= date.today()]

        def link(**kw):
            q = {"mode": r["mode"], "from": r["from"]["key"], "to": r["to"]["key"], "date": r["day"],
                 "pax": r["passengers"], "sort": sort}
            q.update({k: f[k] for k in ("ret", "return_of") if f[k]})
            q.update(kw)
            return url_for("tickets_search", **q)

        def seats_link(trip_id, cls, **kw):
            return url_for("tickets_seats", trip_id=trip_id, cls=cls, pax=r["passengers"],
                           **{k: f[k] for k in ("ret", "return_of") if f[k]}, **kw)
        back_trip = None
        if f["return_of"] and user_id():
            try:
                back_trip = store.get_booking(f["return_of"], user_id())
            except ShopError:
                back_trip = None
        return render_template("tickets/results.html", f=f, r=r, sort=sort, days=days, link=link, seats_link=seats_link,
                               back_trip=back_trip, **common())

    @app.route("/tickets/<mode>/<route>")
    def tickets_route(mode, route):
        if mode not in TK.MODES or "-to-" not in route:
            abort(404)
        a, b = route.split("-to-", 1)
        a, b = TK.place_by_slug(a), TK.place_by_slug(b)
        if not a or not b:
            abort(404)
        try:
            r = store.route_timetable(mode, a, b)
        except ShopError:
            abort(404)
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        search_url = url_for("tickets_search", **{"mode": mode, "from": r["from"]["key"], "to": r["to"]["key"],
                                                  "date": tomorrow, "pax": 1})
        return render_template("tickets/route.html", r=r, mode=mode, tomorrow=tomorrow, popular=popular(),
                               search_url=search_url,
                               f={"mode": mode, "from": a, "to": b, "date": tomorrow, "pax": "1"}, **common())

    @app.route("/tickets/trip/<int:trip_id>")
    def tickets_seats(trip_id):
        try:
            view, secs = store.seat_map(trip_id)
        except ShopError:
            abort(404)
        try:
            pax = max(1, min(TK.MAX_PASSENGERS, int(request.args.get("pax", 1))))
        except ValueError:
            pax = 1
        cls = request.args.get("cls") or view["classes"][0]["cls"]
        if cls not in [c["cls"] for c in view["classes"]]:
            cls = view["classes"][0]["cls"]
        cur, rates = world.current_currency(), world.current_rates()       # the live total in the visitor's money
        if world.convert(100, cur, rates) is None:
            cur = "INR"
        for sec in secs:
            for row in sec["rows"]:
                for seat in row:
                    if seat:
                        seat["local"] = world.convert(seat["price"], cur, rates)
                        seat["kind_t"], seat["tag_t"] = i18n.translate_text(seat["kind"]), i18n.translate_text(seat["tag"])
        back = url_for("tickets_search", **{"mode": view["mode"], "from": view["from_key"], "to": view["to_key"],
                                            "date": view["day"], "pax": pax}, **{k: v for k, v in trip_args().items()
                                                                                 if k in ("ret", "return_of")})
        first = None
        if request.args.get("link") and user_id():          # the second flight of a connection
            try:
                first = store.get_booking(request.args["link"], user_id())
            except ShopError:
                first = None
        nxt = None
        if request.args.get("then"):
            try:
                nxt = store.seat_map(int(request.args["then"]))[0]
            except (ShopError, ValueError):
                nxt = None
        return render_template("tickets/seats.html", t=view, secs=secs, pax=pax, cls=cls, money_cur=cur, back=back,
                               keep=trip_args(), first=first, nxt=nxt, **common())

    @app.route("/tickets/trip/<int:trip_id>/waitlist", methods=["POST"])
    @login_required
    def tickets_waitlist(trip_id):
        try:
            pnr = store.join_waitlist(user_id(), trip_id, request.form.get("cls", ""), request.form.get("pax", 1))
            act("Tickets", f"Waiting list {pnr}")
            return redirect(url_for("tickets_pay", pnr=pnr, **trip_args()))
        except ShopError as e:
            flash(str(e), "error")
            return redirect(url_for("tickets_seats", trip_id=trip_id, pax=request.form.get("pax", 1),
                                    cls=request.form.get("cls", "")))

    @app.route("/tickets/trip/<int:trip_id>/hold", methods=["POST"])
    @login_required
    def tickets_hold(trip_id):
        keep = trip_args()
        try:
            pnr = store.hold_seats(user_id(), trip_id, request.form.getlist("seat"),
                                   return_of=keep.get("return_of", ""), link=keep.get("link", ""))
            act("Tickets", f"Held seats {pnr}")
            if keep.get("then"):                         # a connection: now the seats of the second flight
                return redirect(url_for("tickets_seats", trip_id=int(keep["then"]), pax=request.form.get("pax", 1),
                                        cls=request.form.get("cls", ""), link=pnr,
                                        **{k: v for k, v in keep.items() if k == "ret"}))
            return redirect(url_for("tickets_pay", pnr=keep.get("link") or pnr,
                                    **{k: v for k, v in keep.items() if k == "ret"}))
        except ShopError as e:
            flash(str(e), "error")
            return redirect(url_for("tickets_seats", trip_id=trip_id, pax=request.form.get("pax", 1),
                                    cls=request.form.get("cls", ""), **keep))

    @app.route("/tickets/b/<pnr>/pay", methods=["GET", "POST"])
    @login_required
    def tickets_pay(pnr):
        uid = user_id()
        try:
            b = store.get_booking(pnr, uid)
        except ShopError:
            abort(404)
        if b["status"] != TK.HELD:
            return redirect(url_for("tickets_booking", pnr=b["pnr"]))
        if request.method == "POST":
            try:
                store.pay_booking(uid, b["pnr"], request.form)
                act("Tickets", f"Paid {b['pnr']}")
                b = store.get_booking(b["pnr"], uid)
                flash(f"You are on the waiting list. PNR {b['pnr']}." if b["status"] == TK.WAITLISTED else
                      f"Your ticket is confirmed. PNR {b['pnr']}.", "success")
                return redirect(url_for("tickets_booking", pnr=b["pnr"], **{k: v for k, v in trip_args().items()
                                                                              if k == "ret"}))
            except ShopError as e:
                flash(str(e), "error")
                b = store.get_booking(pnr, uid)
                if b["status"] != TK.HELD:
                    return redirect(url_for("tickets_booking", pnr=b["pnr"]))
        me = store.get_user(uid)
        left = max(0, int((TK._dt(b["hold_until"]) - TK.utc_now()).total_seconds()))
        total = b["total"] + (b["linked"]["total"] if b.get("linked") and b["linked"]["status"] == TK.HELD else 0)
        return render_template("tickets/pay.html", b=b, me=me, left=left, form=request.form, total=total,
                               plans=emi_plans(total), keep=trip_args(), **common())

    @app.route("/tickets/b/<pnr>")
    @login_required
    def tickets_booking(pnr):
        try:
            b = store.get_booking(pnr, user_id())
        except ShopError:
            abort(404)
        if b["status"] == TK.HELD:
            return redirect(url_for("tickets_pay", pnr=b["pnr"]))
        phase, step = store.trip_phase(b)
        token = store.ticket_token(b["pnr"])
        ret_url = None
        if request.args.get("ret") and b["status"] in (TK.CONFIRMED, TK.WAITLISTED) and not b["return_of"]:
            ret_url = url_for("tickets_search", mode=b["mode"], **{"from": b["to_key"], "to": b["from_key"]},
                              date=request.args["ret"], pax=len(b["passengers"]), return_of=b["pnr"])
        return render_template("tickets/ticket.html", b=b, phase=phase, step=step, refund=store.refund_preview(b),
                               qr_url=h.base_url().rstrip("/") + url_for("tickets_verify", token=token),
                               ret_url=ret_url, **common())

    @app.route("/tickets/b/<pnr>/cancel", methods=["POST"])
    @login_required
    def tickets_cancel(pnr):
        try:
            refund = store.cancel_booking(user_id(), pnr)
            act("Tickets", f"Cancelled {pnr}")
            flash(f"The ticket is cancelled. Refund: {world.price(refund)}." if refund else
                  "The booking is cancelled.", "success")
        except ShopError as e:
            flash(str(e), "error")
        return redirect(url_for("tickets_booking", pnr=pnr))

    @app.route("/tickets/b/<pnr>/live.json")
    @login_required
    def tickets_live(pnr):
        try:
            return jsonify(ok=True, **store.trip_live(pnr, user_id()))
        except ShopError as e:
            return jsonify(ok=False, error=str(e)), 404

    @app.route("/tickets/my")
    @login_required
    def tickets_my():
        rows = store.my_bookings(user_id())
        return render_template("tickets/my.html", up=[b for b in rows if b["upcoming"]],
                               past=[b for b in rows if not b["upcoming"]], **common())

    @app.route("/tickets/v/<token>")
    def tickets_verify(token):
        v = store.verify_ticket(token)
        staff = store.staff_of(user_id()) if user_id() else None
        mine = bool(staff and v.get("valid") and staff["operator_id"] == v.get("operator_id"))
        return render_template("tickets/verify.html", v=v, token=token, staff=staff, mine=mine)

    # ======================================================
    # Conductor app (staff of a transport company)
    # ======================================================

    def staff_required(view):
        @wraps(view)
        def wrapper(*a, **kw):
            if store.staff_of(user_id()) is None:
                flash("The conductor app is for the staff of a transport company.", "info")
                return redirect(url_for("tickets_home"))
            return view(*a, **kw)
        return login_required(wrapper)

    @app.route("/conductor")
    @staff_required
    def conductor_home():
        st, trips = store.conductor_trips(user_id())
        return render_template("tickets/conductor.html", st=st, trips=trips, **common())

    @app.route("/conductor/trip/<int:trip_id>")
    @staff_required
    def conductor_trip(trip_id):
        try:
            st, t, people, others = store.manifest(user_id(), trip_id)
        except ShopError as e:
            flash(str(e), "error")
            return redirect(url_for("conductor_home"))
        return render_template("tickets/manifest.html", st=st, t=t, people=people, others=others, **common())

    @app.route("/conductor/check", methods=["POST"])
    @staff_required
    def conductor_check():
        wants_json = request.form.get("json") == "1"
        try:
            r = store.board_ticket(user_id(), request.form.get("token", ""))
            act("Tickets", f"Checked in {r['pnr']}")
            msg = (f"Already checked in: {r['pnr']}" if r["already"] else
                   f"Welcome on board: {', '.join(r['names'])} (seats {', '.join(r['seats'])})")
            if wants_json:                         # JSON is not translated by the page translator: do it here
                return jsonify(ok=True, already=r["already"], message=i18n.translate_text(msg), trip_id=r["trip_id"])
            flash(msg, "info" if r["already"] else "success")
            return redirect(url_for("conductor_trip", trip_id=r["trip_id"]))
        except ShopError as e:
            if wants_json:
                return jsonify(ok=False, message=i18n.translate_text(str(e)))
            flash(str(e), "error")
            back = request.form.get("trip_id")
            return redirect(url_for("conductor_trip", trip_id=int(back)) if back and back.isdigit()
                            else url_for("conductor_home"))

    # ======================================================
    # Admin
    # ======================================================

    @app.route("/admin/tickets")
    @admin_required
    def admin_tickets():
        q, status, mode = request.args.get("q", "").strip(), request.args.get("status", ""), request.args.get("mode", "")
        return h.admin_page("admin/tickets.html", st=store.ticket_stats(), rows=store.admin_bookings(q, status, mode),
                            q=q, status=status, mode=mode, MODES=TK.MODES,
                            STATUSES=[TK.HELD, TK.CONFIRMED, TK.COMPLETED, TK.CANCELLED, TK.EXPIRED])

    @app.route("/admin/tickets/b/<pnr>")
    @admin_required
    def admin_ticket(pnr):
        try:
            b = store.get_booking(pnr)
        except ShopError:
            abort(404)
        return h.admin_page("admin/ticket.html", b=b, phase=store.trip_phase(b)[0], owner=store.get_user(b["user_id"]))

    @app.route("/admin/tickets/trips", methods=["GET", "POST"])
    @admin_required
    def admin_ticket_trips():
        if request.method == "POST":
            trip_id = int(request.form.get("trip_id", 0))
            try:
                if request.form.get("action") == "cancel":
                    n = store.cancel_trip(trip_id, request.form.get("reason", ""))
                    act("Tickets", f"Cancelled trip #{trip_id}")
                    flash(f"The trip is cancelled. {n} travellers got all their money back.", "success")
                else:
                    n = store.set_trip_delay(trip_id, request.form.get("delay", 0), request.form.get("reason", ""))
                    act("Tickets", f"Delay of trip #{trip_id}")
                    flash(f"Saved. {n} travellers got a message.", "success")
            except ShopError as e:
                flash(str(e), "error")
            return redirect(url_for("admin_ticket_trips", **{k: v for k, v in request.args.items()}))
        day, mode, q = request.args.get("day", ""), request.args.get("mode", ""), request.args.get("q", "").strip()
        return h.admin_page("admin/ticket_trips.html", rows=store.admin_trips(day, mode, q), day=day, mode=mode, q=q,
                            MODES=TK.MODES)

    @app.route("/admin/tickets/services", methods=["GET", "POST"])
    @admin_required
    def admin_ticket_services():
        if request.method == "POST":
            try:
                on = store.toggle_service(int(request.form.get("service_id", 0)))
                act("Tickets", f"Service #{request.form.get('service_id')} {'on' if on else 'off'}")
                flash("The service is running again." if on else "The service is switched off (no new tickets).",
                      "success")
            except ShopError as e:
                flash(str(e), "error")
            return redirect(url_for("admin_ticket_services", **{k: v for k, v in request.args.items()}))
        mode, q = request.args.get("mode", ""), request.args.get("q", "").strip()
        return h.admin_page("admin/ticket_services.html", rows=store.admin_services(mode, q), mode=mode, q=q,
                            MODES=TK.MODES, CLASS_NAMES=TK.CLASS_NAMES)

    @app.route("/admin/tickets/staff", methods=["GET", "POST"])
    @admin_required
    def admin_ticket_staff():
        if request.method == "POST":
            try:
                if request.form.get("remove"):
                    store.remove_staff(int(request.form["remove"]))
                    act("Tickets", "Removed conductor")
                    flash("Removed.", "success")
                else:
                    store.add_staff(request.form.get("email"), request.form.get("operator"))
                    act("Tickets", f"Added conductor {request.form.get('email')}")
                    flash("The person can now open the conductor app.", "success")
            except ShopError as e:
                flash(str(e), "error")
            return redirect(url_for("admin_ticket_staff"))
        return h.admin_page("admin/ticket_staff.html", rows=store.staff_list(), operators=store.ticket_operators())
