"""
v7_views.py - version 7 pages (people, money, partners, couriers):

    /staff/me, /staff/payslips/<id>        my contract, holiday balance, trainings, helper offices, payslips
    /admin/staff/<id>/people                contract, training and helper offices of a worker
    /admin/staff/training                   missing and expiring certificates of my branch
    /admin/offices/<id>/place               where an office is (clock-in distance, remote work)
    /admin/payroll, /admin/payroll/<id>     payroll runs (made by one person, approved by another, paid in test mode)
    /admin/approvals                        dual control: what waits for a second person
    /admin/invoices, /invoices/<id>(.xml)   tax invoices, credit notes and UBL e-invoices; /orders/<n>/invoices
    /admin/tax-categories                   tax rates by kind of goods
    /seller/tax, /seller/invoices           the seller's tax number and his invoices
    /api/payments/<provider>/callback       signed payment callbacks of providers
    /admin/payments/reconcile               settlement statements against our books
    /admin/partners, /api/partner/v1/...    courier partner companies and their API; /partners/api documents it
    /driver/job/<n>/signature               the receiver's signature (proof of delivery)
    /courier/s/<n>/customs.json             advance customs data of a parcel abroad
    /.well-known/assetlinks.json            the courier app as an Android app (Trusted Web Activity)
    /admin/tickets/gtfs/<id>/live           live delays (GTFS-Realtime file or feed address)
"""

import json

from flask import Response, abort, flash, jsonify, redirect, render_template, request, url_for

import courierplus
import invoices as INV
import partners as P
import people
import world
from core import ShopError


def register(app, store, h):

    def back(endpoint, **kw):
        return redirect(request.referrer or url_for(endpoint, **kw))

    # ================================================================ worker
    @app.route("/staff/me")
    @h.login_required
    def staff_me():
        w = store.worker_of(h.user_id())
        if not w:
            abort(404)
        return render_template("staff/me.html", w=w, contract=store.contract_of(w["id"]),
                               balance=store.leave_balance(w["id"]), trainings=store.trainings(w["id"]),
                               missing=[people.COURSES[c][0] for c in store.missing_training(w["id"], w["ability_set"])],
                               helps=store.helper_offices(w["id"]), slips=store.my_payslips(h.user_id()),
                               kinds=people.CONTRACT_KINDS)

    @app.route("/staff/payslips/<int:slip_id>")
    @h.login_required
    def staff_payslip(slip_id):
        slip = next((s for s in store.my_payslips(h.user_id()) if s["id"] == slip_id), None)
        if slip is None:
            abort(404)
        return render_template("staff/payslip.html", s=slip, w=store.worker_of(h.user_id()))

    @app.route("/staff/payslips")
    @h.login_required
    def staff_payslips():
        return redirect(url_for("staff_me") + "#payslips")

    # ================================================================ admin: people
    @app.route("/admin/staff/<int:staff_id>/people", methods=["GET", "POST"])
    @h.admin_required
    def admin_staff_people(staff_id):
        uid = h.user_id()
        if request.method == "POST":
            f = request.form
            try:
                what = f.get("what")
                if what == "contract":
                    store.save_contract(uid, staff_id, f)
                    flash("Contract saved.", "success")
                elif what == "training":
                    store.add_training(uid, staff_id, f.get("course"), f.get("done_on"), f.get("certificate", ""))
                    flash("Training saved.", "success")
                elif what == "helper":
                    store.assign_helper_office(uid, staff_id, f.get("office_id"), f.get("from_day"), f.get("to_day"))
                    flash("Saved: the worker helps in this office.", "success")
            except ShopError as e:
                flash(str(e), "error")
            return redirect(url_for("admin_staff_people", staff_id=staff_id))
        w = store.staff_member(staff_id)
        if w is None:
            abort(404)
        return h.admin_page("admin/staff_people.html", w=w, contract=store.contract_of(staff_id),
                            balance=store.leave_balance(staff_id), trainings=store.trainings(staff_id),
                            helps=store.helper_offices(staff_id), offices=store.offices(), courses=people.COURSES,
                            kinds=people.CONTRACT_KINDS, currency=people.CURRENCY_OF.get(w.get("office_country", ""), "INR"))

    @app.route("/admin/staff/training")
    @h.admin_required
    def admin_training():
        return h.admin_page("admin/training.html", report=store.training_report(h.user_id()), courses=people.COURSES,
                            ending=store.contracts_ending(h.user_id()),
                            required=str(store.setting("require_training")) == "1")

    @app.route("/admin/offices/<int:office_id>/place", methods=["POST"])
    @h.admin_required
    def admin_office_place(office_id):
        f = request.form
        try:
            store.set_office_location(h.user_id(), office_id, f.get("lat"), f.get("lng"), f.get("geofence_m"),
                                      bool(f.get("remote_ok")))
            flash("Saved.", "success")
        except ShopError as e:
            flash(str(e), "error")
        return redirect(url_for("admin_offices"))

    # ================================================================ payroll and approvals
    @app.route("/admin/payroll", methods=["GET", "POST"])
    @h.admin_required
    def admin_payroll():
        if request.method == "POST":
            try:
                rid = store.create_payroll(h.user_id(), request.form.get("country"), request.form.get("month"))
                flash("Payroll made. Another person must approve it.", "success")
                return redirect(url_for("admin_payroll_run", run_id=rid))
            except ShopError as e:
                flash(str(e), "error")
            return redirect(url_for("admin_payroll"))
        return h.admin_page("admin/payroll.html", runs=store.payroll_runs(), rules=store.income_tax_rules(),
                            countries=sorted({o["country"] for o in store.offices() if o["country"]}))

    @app.route("/admin/payroll/<int:run_id>", methods=["GET", "POST"])
    @h.admin_required
    def admin_payroll_run(run_id):
        if request.method == "POST":
            a = request.form.get("action")
            try:
                if a == "approve":
                    store.approve_payroll(h.user_id(), run_id)
                    flash("Payroll approved.", "success")
                elif a == "pay":
                    store.pay_payroll(h.user_id(), run_id)
                    flash("Payroll paid (test mode: no money moved). The workers got their payslips.", "success")
                elif a == "cancel":
                    store.cancel_payroll(h.user_id(), run_id)
                    flash("Payroll cancelled.", "success")
            except ShopError as e:
                flash(str(e), "error")
            return redirect(url_for("admin_payroll_run", run_id=run_id))
        r = store.payroll_run(run_id)
        if r is None:
            abort(404)
        return h.admin_page("admin/payroll_run.html", r=r, rule=store.income_tax_rule(r["country"]))

    @app.route("/admin/approvals", methods=["GET", "POST"])
    @h.admin_required
    def admin_approvals():
        if request.method == "POST":
            f = request.form
            try:
                if f.get("action") == "dual":
                    store.set_dual_control(h.user_id(), f.get("on") == "1")
                    flash("Saved.", "success")
                else:
                    store.decide_approval(h.user_id(), int(f.get("id") or 0), f.get("action") == "approve", f.get("note", ""))
                    flash("Decision saved.", "success")
            except (ShopError, ValueError) as e:
                flash(str(e), "error")
            return redirect(url_for("admin_approvals"))
        return h.admin_page("admin/approvals.html", waiting=store.approvals("Waiting"), done=store.approvals("")[:30],
                            dual=str(store.setting("dual_control")) == "1")

    # ================================================================ invoices and tax rates
    def _invoice_allowed(inv):
        uid = h.user_id()
        if inv["user_id"] == uid or store.can(uid, "invoices.view"):
            return True
        if inv["seller_id"]:
            shop, abil = store.seller_membership(uid)
            return bool(shop) and shop["id"] == inv["seller_id"] and ("owner" in abil or "seller.orders" in abil)
        return False

    @app.route("/orders/<number>/invoices")
    @h.login_required
    def order_invoices(number):
        with store.db() as conn:
            o = conn.execute("SELECT * FROM orders WHERE number = ?", (number,)).fetchone()
        if o is None or (o["user_id"] != h.user_id() and not store.can(h.user_id(), "invoices.view")):
            abort(404)
        try:
            invs = store.issue_invoices(o["id"])
        except ShopError as e:
            flash(str(e), "error")
            return redirect(url_for("order", number=number))
        if len(invs) == 1:
            return redirect(url_for("invoice_page", invoice_id=invs[0]["id"]))
        return render_template("invoices_of_order.html", invoices=invs, number=number)

    @app.route("/invoices/<int:invoice_id>")
    @h.login_required
    def invoice_page(invoice_id):
        inv = store.invoice(invoice_id)
        if inv is None or not _invoice_allowed(inv):
            abort(404)
        return render_template("tax_invoice.html", inv=inv)

    @app.route("/invoices/<int:invoice_id>.xml")
    @h.login_required
    def invoice_xml(invoice_id):
        inv = store.invoice(invoice_id)
        if inv is None or not _invoice_allowed(inv):
            abort(404)
        return Response(store.invoice_ubl(invoice_id), mimetype="application/xml",
                        headers={"Content-Disposition": f"attachment; filename={inv['number'].replace('/', '-')}.xml"})

    @app.route("/admin/invoices")
    @h.admin_required
    def admin_invoices():
        return h.admin_page("admin/invoices.html", invoices=store.invoice_list())

    @app.route("/admin/tax-categories", methods=["GET", "POST"])
    @h.admin_required
    def admin_tax_categories():
        if request.method == "POST":
            f = request.form
            try:
                store.save_category_rate(h.user_id(), f.get("country"), f.get("category"), f.get("rate"),
                                         f.get("max_unit_price") or 0, f.get("note", ""), delete=bool(f.get("delete")))
                flash("Saved.", "success")
            except ShopError as e:
                flash(str(e), "error")
            return redirect(url_for("admin_tax_categories"))
        return h.admin_page("admin/tax_categories.html", rates=store.category_rates(),
                            categories=store.categories())

    @app.route("/seller/tax", methods=["GET", "POST"])
    @h.seller_required
    def seller_tax():
        shop = request.shop
        if request.method == "POST":
            try:
                t = store.save_seller_tax_id(h.user_id(), request.form.get("tax_id", ""))
                flash("Saved: your invoices are tax invoices now." if t else
                      "Saved: you are not registered, your invoices are bills of supply.", "success")
            except ShopError as e:
                flash(str(e), "error")
            return redirect(url_for("seller_tax"))
        with store.db() as conn:
            s = dict(conn.execute("SELECT * FROM sellers WHERE id = ?", (shop["id"],)).fetchone())
        return render_template("seller/tax.html", shop=s, invoices=store.invoice_list(shop["id"], 30))

    @app.route("/seller/invoices")
    @h.seller_required
    def seller_invoices():
        return redirect(url_for("seller_tax") + "#invoices")

    # ================================================================ payment providers
    @app.route("/api/payments/<provider>/callback", methods=["POST"])
    def payment_callback(provider):
        code, text = store.payment_callback(provider, request.headers.get("X-Yo-Signature", ""), request.get_data())
        return jsonify({"result": text}), code

    @app.route("/admin/payments/reconcile", methods=["GET", "POST"])
    @h.admin_required
    def admin_reconcile():
        result = None
        if request.method == "POST":
            f = request.files.get("statement")
            text = f.read(2_000_000).decode("utf-8", "replace") if f and f.filename else request.form.get("csv", "")
            try:
                result = store.reconcile(h.user_id(), request.form.get("provider"), text)
                flash("Statement compared with our books.", "success")
            except ShopError as e:
                flash(str(e), "error")
        import providers
        return h.admin_page("admin/reconcile.html", result=result, history=store.reconciliations(),
                            events=store.payment_events(30), providers=providers.PROVIDERS)

    # ================================================================ partners
    @app.route("/admin/partners", methods=["GET", "POST"])
    @h.admin_required
    def admin_partners():
        new_key = None
        if request.method == "POST":
            f = request.form
            try:
                a = f.get("action")
                if a == "add":
                    key_id, secret = store.add_partner(h.user_id(), f.get("name"), f.get("countries", ""))
                    new_key = {"name": f.get("name"), "key_id": key_id, "secret": secret}
                    flash("Partner added. Copy the secret now: it is shown only once.", "success")
                elif a == "rotate":
                    secret = store.rotate_partner_secret(h.user_id(), int(f.get("id")))
                    new_key = {"name": f.get("name"), "key_id": f.get("key_id"), "secret": secret}
                    flash("New secret made. Copy it now: it is shown only once.", "success")
                elif a in ("on", "off"):
                    store.set_partner_active(h.user_id(), int(f.get("id")), a == "on")
                    flash("Saved.", "success")
                elif a == "hand":
                    store.hand_to_partner(h.user_id(), f.get("number"), int(f.get("partner_id") or 0))
                    flash("The parcel is with the partner now.", "success")
            except (ShopError, ValueError, TypeError) as e:
                flash(str(e), "error")
            if new_key is None:
                return redirect(url_for("admin_partners"))
        return h.admin_page("admin/partners.html", partners=store.partners(), log=store.partner_event_log(limit=40),
                            new_key=new_key)

    def _partner():
        body = request.get_data()
        p = store.partner_auth(request.headers.get("X-Yo-Key"), request.headers.get("X-Yo-Signature"), body)
        return p, body

    @app.route("/api/partner/v1/events", methods=["POST"])
    def partner_events_api():
        p, body = _partner()
        if p is None:
            return jsonify({"error": "bad key or signature"}), 401
        try:
            results = store.partner_events_in(p, P.as_json(body))
        except ShopError as e:
            return jsonify({"error": str(e)}), 400
        return jsonify({"results": results})

    @app.route("/api/partner/v1/shipments")
    def partner_shipments_api():
        p, _ = _partner()
        if p is None:
            return jsonify({"error": "bad key or signature"}), 401
        return jsonify({"shipments": store.partner_shipments(p)})

    @app.route("/api/partner/v1/shipments/<number>")
    def partner_shipment_api(number):
        p, _ = _partner()
        if p is None:
            return jsonify({"error": "bad key or signature"}), 401
        rows = store.partner_shipments(p, number)
        return (jsonify(rows[0]), 200) if rows else (jsonify({"error": "not one of your parcels"}), 404)

    @app.route("/api/partner/openapi.json")
    def partner_openapi():
        return jsonify(P.openapi())

    @app.route("/partners/api")
    def partner_api_page():
        return render_template("partner_api.html", spec=P.openapi(), events=P.EVENTS)

    # ================================================================ couriers
    @app.route("/driver/job/<number>/signature", methods=["POST"])
    @h.login_required
    def driver_signature(number):
        c = store.courier_of(h.user_id())
        if not c:
            abort(404)
        try:
            store.save_pod_signature(c["id"], number, request.form.get("signature", ""))
            flash("Signature saved.", "success")
        except ShopError as e:
            flash(str(e), "error")
        return redirect(url_for("driver_home"))

    @app.route("/courier/s/<number>/customs.json")
    @h.login_required
    def customs_json(number):
        with store.db() as conn:
            s = conn.execute("SELECT user_id FROM shipments WHERE number = ?", (number.upper(),)).fetchone()
        if s is None or (s["user_id"] != h.user_id() and not store.can(h.user_id(), "shipments.view")):
            abort(404)
        data = store.advance_customs_data(number)
        if data is None:
            abort(404)
        return Response(courierplus.ead_json(data), mimetype="application/json",
                        headers={"Content-Disposition": f"attachment; filename={number}-customs.json"})

    @app.route("/courier/s/<number>/signature.png")
    @h.login_required
    def parcel_signature(number):
        with store.db() as conn:
            s = conn.execute("SELECT user_id, pod_signature FROM shipments WHERE number = ?", (number.upper(),)).fetchone()
        if s is None or not s["pod_signature"] or (s["user_id"] != h.user_id() and not store.can(h.user_id(),
                                                                                                "shipments.view")):
            abort(404)
        found = store.media(s["pod_signature"])
        if not found:
            abort(404)
        return Response(found[1], mimetype=found[0], headers={"Cache-Control": "private, no-store"})

    @app.route("/.well-known/assetlinks.json")
    def assetlinks():
        return jsonify(courierplus.assetlinks(store.env))

    @app.route("/admin/tickets/gtfs/<int:feed_id>/live", methods=["POST"])
    @h.admin_required
    def admin_gtfs_live(feed_id):
        f = request.files.get("rt")
        try:
            if f and f.filename:
                d = store.apply_gtfs_rt(feed_id, f.read(5_000_000))
                flash(f"Live data: {d['updates']} trip updates, {d['delayed']} delays, {d['cancelled']} cancelled, "
                      f"{d['messages']} messages to travellers.", "success")
            else:
                store.set_gtfs_rt_url(h.user_id(), feed_id, request.form.get("rt_url", ""))
                flash("Saved.", "success")
        except ShopError as e:
            flash(str(e), "error")
        return redirect(url_for("admin_gtfs"))

    def minor_money(amount, currency="INR"):
        try:
            return world.format_money(amount or 0, currency)               # amounts are kept x 100
        except Exception:                                    # an unknown currency code
            return f"{(amount or 0) / 100:,.2f} {currency}"

    app.jinja_env.globals.update(minor_money=minor_money, COURSES=people.COURSES, LEAVE_KINDS=people.LEAVE_KINDS,
                                 INVOICE_MONEY=INV.money, DELIVERY_WINDOWS=__import__("courier").DELIVERY_WINDOWS,
                                 world_price=world.price, json_loads=json.loads)
