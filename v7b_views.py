"""
v7b_views.py - more version 7 pages (help desk, claims, safety checks, analytics, privacy):

    /support, /support/<n>                   a customer's questions and the answers
    /admin/support, /admin/support/<n>       the queue of the support team (sorted by the promised answer time)
    /courier/s/<n>/claim                     the sender claims for a lost or damaged parcel; /admin/claims decides
    /admin/risk                              orders that wait for the safety check
    /admin/analytics (+ .csv)                numbers of my branch from the data warehouse
    /admin/forecast                          parcels and workers needed per day for a hub (Holt-Winters)
    /admin/experiments                       A/B tests and their results
    /admin/privacy                           privacy register, keeping times, data requests, data residency
"""

from flask import Response, abort, flash, redirect, render_template, request, url_for

import analytics as A
import privacy
import helpdesk as SUP
from core import ShopError


def register(app, store, h):

    # ================================================================ help desk (customer)
    @app.route("/support", methods=["GET", "POST"])
    @h.login_required
    def support_home():
        uid = h.user_id()
        if request.method == "POST":
            f = request.form
            try:
                number = store.open_support_ticket(uid, f.get("topic"), f.get("subject"), f.get("body"),
                                                   f.get("about", ""), urgent=bool(f.get("urgent")))
                flash("Thank you! We got your question.", "success")
                return redirect(url_for("support_ticket_page", number=number))
            except ShopError as e:
                flash(str(e), "error")
        return render_template("support/home.html", tickets=store.my_support_tickets(uid), topics=SUP.TOPICS,
                               about=request.args.get("about", ""), topic=request.args.get("topic", ""),
                               sla=SUP.SLA_HOURS)

    @app.route("/support/<number>", methods=["GET", "POST"])
    @h.login_required
    def support_ticket_page(number):
        uid = h.user_id()
        t = store.support_ticket(number, uid)
        if t is None:
            abort(404)
        if request.method == "POST":
            try:
                store.support_reply(uid, number, request.form.get("body"), close=bool(request.form.get("close")))
                flash("Sent.", "success")
            except ShopError as e:
                flash(str(e), "error")
            return redirect(url_for("support_ticket_page", number=number))
        return render_template("support/ticket.html", t=t, topics=SUP.TOPICS)

    # ================================================================ help desk (staff)
    @app.route("/admin/support")
    @h.admin_required
    def admin_support():
        status = request.args.get("status", "")
        mine = request.args.get("mine") == "1"
        return h.admin_page("admin/support.html", rows=store.support_queue(status, h.user_id() if mine else None),
                            numbers=store.support_numbers(), status=status, mine=mine, topics=SUP.TOPICS)

    @app.route("/admin/support/<number>", methods=["GET", "POST"])
    @h.admin_required
    def admin_support_ticket(number):
        if request.method == "POST":
            f = request.form
            try:
                if f.get("action") == "take":
                    store.take_support_ticket(h.user_id(), number)
                    flash("The question is yours.", "success")
                else:
                    store.support_reply(h.user_id(), number, f.get("body"), close=f.get("action") == "close")
                    flash("Answer sent.", "success")
            except ShopError as e:
                flash(str(e), "error")
            return redirect(url_for("admin_support_ticket", number=number))
        t = store.support_ticket(number)
        if t is None:
            abort(404)
        return h.admin_page("admin/support_ticket.html", t=t, topics=SUP.TOPICS)

    # ================================================================ claims
    @app.route("/courier/s/<number>/claim", methods=["GET", "POST"])
    @h.login_required
    def parcel_claim(number):
        uid = h.user_id()
        with store.db() as conn:
            s = conn.execute("SELECT * FROM shipments WHERE number = ? AND user_id = ?", (number.upper(), uid)).fetchone()
        if s is None:
            abort(404)
        s = dict(s)
        if request.method == "POST":
            f = request.form
            try:
                store.file_claim(uid, number, f.get("kind"), f.get("details"), f.get("amount"))
                flash("Your claim was sent. A person of our support team decides within 5 working days.", "success")
                return redirect(url_for("parcel_claim", number=number))
            except ShopError as e:
                flash(str(e), "error")
        return render_template("support/claim.html", s=s, cover=store.claim_cover(s), kinds=SUP.CLAIM_KINDS,
                               claims=[c for c in store.claims(user_id=uid) if c["number"] == s["number"]],
                               limit=SUP.UNINSURED_LIMIT)

    @app.route("/admin/claims", methods=["GET", "POST"])
    @h.admin_required
    def admin_claims():
        if request.method == "POST":
            f = request.form
            try:
                paid = store.decide_claim(h.user_id(), int(f.get("id") or 0), f.get("action") == "pay",
                                          f.get("payout") or None, f.get("note", ""))
                flash("Claim paid to the Yo Pay wallet." if paid else "Claim refused.", "success")
            except (ShopError, ValueError) as e:
                flash(str(e), "error")
            return redirect(url_for("admin_claims"))
        return h.admin_page("admin/claims.html", waiting=store.claims("Waiting"), done=store.claims()[:40],
                            kinds=SUP.CLAIM_KINDS)

    # ================================================================ safety check of orders
    @app.route("/admin/risk", methods=["GET", "POST"])
    @h.admin_required
    def admin_risk():
        if request.method == "POST":
            try:
                store.decide_risk(h.user_id(), request.form.get("number", ""), request.form.get("action") == "release")
                flash("Saved.", "success")
            except ShopError as e:
                flash(str(e), "error")
            return redirect(url_for("admin_risk"))
        return h.admin_page("admin/risk.html", rows=store.risk_queue(), hold=SUP.RISK_HOLD)

    # ================================================================ analytics
    @app.route("/admin/analytics")
    @h.admin_required
    def admin_analytics():
        days = max(7, min(int(request.args.get("days", 28) or 28), 90))
        if request.args.get("rebuild") and store.can(h.user_id(), "analytics.view"):
            store.build_warehouse()
        hubs = [o for o in store.offices() if o["kind"] in ("hub", "warehouse") and o["place"]]
        return h.admin_page("admin/analytics.html", a=store.analytics(h.user_id(), days), days=days,
                            metrics=A.METRICS, hubs=hubs)

    @app.route("/admin/analytics.csv")
    @h.admin_required
    def admin_analytics_csv():
        return Response(store.analytics_csv(h.user_id()), mimetype="text/csv",
                        headers={"Content-Disposition": "attachment; filename=yo-analytics.csv"})

    @app.route("/admin/forecast")
    @h.admin_required
    def admin_forecast():
        hubs = [o for o in store.offices() if o["kind"] in ("hub", "warehouse") and o["place"]]
        _, places = store._branch_scope(h.user_id())
        if places is not None:
            hubs = [o for o in hubs if o["place"] in places]
        office_id = request.args.get("office", type=int) or (hubs[0]["id"] if hubs else None)
        fc = None
        if office_id:
            try:
                fc = store.hub_forecast(h.user_id(), office_id)
            except ShopError as e:
                flash(str(e), "error")
        return h.admin_page("admin/forecast.html", hubs=hubs, fc=fc, office_id=office_id)

    @app.route("/admin/experiments", methods=["GET", "POST"])
    @h.admin_required
    def admin_experiments():
        if request.method == "POST":
            try:
                store.set_experiment_active(h.user_id(), request.form.get("key"), request.form.get("on") == "1")
                flash("Saved.", "success")
            except ShopError as e:
                flash(str(e), "error")
            return redirect(url_for("admin_experiments"))
        return h.admin_page("admin/experiments.html", r=store.ab_results("similar_products"), min_views=A.MIN_VIEWS)

    # ================================================================ privacy
    @app.route("/admin/privacy", methods=["GET", "POST"])
    @h.admin_required
    def admin_privacy():
        done = None
        if request.method == "POST":
            done = store.apply_retention()
            flash("Keeping times applied: " + ", ".join(f"{k} {v}" for k, v in done.items()), "success")
            return redirect(url_for("admin_privacy"))
        return h.admin_page("admin/privacy.html", register=store.privacy_register(), retention=privacy.RETENTION,
                            residency=privacy.RESIDENCY, requests=store.privacy_requests())
