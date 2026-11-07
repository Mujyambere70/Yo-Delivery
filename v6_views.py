"""
v6_views.py - version 6 pages that are not about staff:

    /admin/monitor              requests of the last hour, the job queue, alerts (and "try again" for failed jobs)
    /admin/customs              customs rules per country (duty-free value, percentage, extra forbidden goods)
    /courier/s/<number>/customs the customs declaration of a parcel abroad (to print with the label)
    /driver/route               the order of the stops when a courier has several parcels
    /pay/mobile                 add money with M-Pesa, MTN MoMo, SBP or UPI (test mode)
    /pay/mobile/<ref>           the "phone" of the test mode: approve or decline the request
    /tickets/multi              a trip through several towns (multi-city)
"""

from datetime import date, timedelta

from flask import abort, flash, redirect, render_template, request, url_for

import customs as CU
import mobilemoney as MM
import tickets as TK
import yopay
from core import ShopError


def register(app, store, h):

    # ------------------------------------------------------------ monitoring
    @app.route("/admin/monitor", methods=["GET", "POST"])
    @h.admin_required
    def admin_monitor():
        if request.method == "POST":
            try:
                if request.form.get("action") == "retry":
                    store.retry_job(int(request.form.get("job_id") or 0))
                    flash("The job will run again in a minute.", "success")
                elif request.form.get("action") == "run":
                    done = store.run_jobs(limit=10)
                    flash(f"Jobs run now: {done['ok']} done, {done['retry']} to try again, {done['failed']} failed.",
                          "success")
                elif request.form.get("action") == "alerts":
                    opened = store.check_alerts()
                    flash("New alerts: " + "; ".join(opened) if opened else "No new alerts.", "info")
                h.act("Admin: monitoring", request.form.get("action", ""))
            except (ShopError, ValueError) as e:
                flash(str(e), "error")
            return redirect(url_for("admin_monitor"))
        store.flush_stats()
        return h.admin_page("admin/monitor.html", nums=store.request_numbers(60), jobs=store.job_summary(),
                            alerts=store.alerts(), health=store.health(), cache=store.cache_numbers())

    # ------------------------------------------------------------ customs
    @app.route("/admin/customs", methods=["GET", "POST"])
    @h.admin_required
    def admin_customs():
        if request.method == "POST":
            f = request.form
            try:
                store.save_customs_rule(h.user_id(), f.get("country"), f.get("duty_free"), f.get("rate_pct"),
                                        f.get("forbidden", ""), f.get("note", ""), delete=f.get("action") == "delete")
                h.act("Admin: customs rule", f.get("country", ""))
                flash("Customs rule saved." if f.get("action") != "delete" else "Customs rule deleted.", "success")
            except ShopError as e:
                flash(str(e), "error")
            return redirect(url_for("admin_customs"))
        return h.admin_page("admin/customs.html", rules=store.customs_rules(), FORBIDDEN=CU.FORBIDDEN_NAMES)

    @app.route("/courier/s/<number>/customs")
    @h.login_required
    def courier_customs(number):
        uid = h.user_id()
        with store.db() as conn:
            r = conn.execute("SELECT user_id FROM shipments WHERE number = ?", (str(number).upper()[:20],)).fetchone()
        if r is None or (r["user_id"] != uid and not store.can(uid, "shipments.view")):
            abort(404)
        s = store.get_shipment(number, r["user_id"])
        d = store.customs_of(s["id"])
        if d is None:
            abort(404)
        return render_template("courier/customs.html", s=s, d=d)

    # ------------------------------------------------------------ courier: several parcels
    @app.route("/driver/route")
    @h.login_required
    def driver_route():
        c = store.courier_of(h.user_id())
        if not c or c["status"] != "Approved":
            return redirect(url_for("driver_home"))
        return render_template("driver/route.html", c=c, plan=store.courier_route_plan(c["id"]))

    # ------------------------------------------------------------ mobile money (test mode)
    @app.route("/pay/mobile", methods=["GET", "POST"])
    @h.login_required
    def pay_mobile():
        u = store.get_user(h.user_id())
        f = request.form if request.method == "POST" else {"amount": request.args.get("amount", "500")}
        if request.method == "POST":
            try:
                ref = store.start_mobile_payment(u["id"], f.get("method"), f.get("amount"), f.get("account"),
                                                 f.get("country") or None)
                h.act("Payment", f"Mobile money {ref} started")
                return redirect(url_for("pay_mobile_phone", ref=ref))
            except ShopError as e:
                flash(str(e), "error")
        order = MM.methods_for(u["country"])
        return render_template("pay/mobile.html", f=f, methods=[(k, MM.METHODS[k]) for k in order],
                               low=yopay.TOPUP_MIN, high=yopay.TOPUP_MAX, country=u["country"])

    @app.route("/pay/mobile/<ref>", methods=["GET", "POST"])
    @h.login_required
    def pay_mobile_phone(ref):
        p = store.mobile_payment(ref, h.user_id())
        if p is None:
            abort(404)
        if request.method == "POST":
            try:
                result = store.answer_mobile_payment(h.user_id(), ref, request.form.get("answer") == "approve")
                h.act("Payment", f"Mobile money {p['ref']} {result}")
                if result == "Paid":
                    flash("Money added to your Yo Pay wallet.", "success")
                    return redirect(url_for("pay_home"))
                flash("The request was declined. Nothing was taken.", "info")
            except ShopError as e:
                flash(str(e), "error")
            return redirect(url_for("pay_mobile_phone", ref=ref))
        return render_template("pay/mobile_phone.html", p=p)

    # ------------------------------------------------------------ tickets: several towns
    @app.route("/tickets/multi")
    def tickets_multi():
        a = request.args
        stops = [a.get(f"s{i}", "").strip() for i in range(5)]
        modes = [a.get(f"m{i}", "bus") for i in range(4)]
        day = a.get("date") or (date.today() + timedelta(days=1)).isoformat()
        pax = a.get("pax", "1")
        plan = None
        if sum(1 for s in stops if s) >= 2 and a.get("go"):
            try:
                plan = store.plan_multi_city(stops, modes, day, pax)
            except ShopError as e:
                flash(str(e), "error")
        return render_template("tickets/multi.html", stops=stops, modes=modes, day=day, pax=pax, plan=plan,
                               MODES=TK.MODES, ICONS=TK.MODE_ICONS, today=date.today().isoformat(),
                               last_day=(date.today() + timedelta(days=TK.SEARCH_DAYS)).isoformat(),
                               MAX_PAX=TK.MAX_PASSENGERS)
