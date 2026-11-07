"""
v6b_views.py - more version 6 pages (money, places, travel, trust):

    /admin/taxes            the tax rule of each country (examples until a person checks and saves them)
    /seller/bank            the seller's bank account and bank transfers (test mode)
    /admin/bank             all bank transfers (test mode)
    /admin/tickets/gtfs     import real timetables (GTFS) of transport companies
    /id-check               a courier or a worker proves who he is (with consent); /admin/id-checks decides
"""

from flask import abort, flash, redirect, render_template, request, session, url_for

import os

import bankpay
import gtfs
import idcheck
import world
from core import ShopError


def register(app, store, h):

    @app.route("/admin/taxes", methods=["GET", "POST"])
    @h.admin_required
    def admin_taxes():
        if request.method == "POST":
            f = request.form
            try:
                store.save_tax_rule(h.user_id(), f.get("country", ""), f.get("name", ""), f.get("services_pct", "0"),
                                    f.get("goods_pct", "0"), f.get("note", ""), delete=bool(f.get("delete")))
                flash("Tax rule deleted." if f.get("delete") else "Tax rule saved.", "success")
            except ShopError as e:
                flash(str(e), "error")
            return redirect(url_for("admin_taxes"))
        return h.admin_page("admin/taxes.html", rules=store.tax_rules(),
                            countries=world.countries(world.current_lang()))

    @app.route("/seller/bank", methods=["GET", "POST"])
    @h.seller_required
    def seller_bank():
        uid = h.user_id()
        shop = request.shop
        if request.method == "POST":
            f = request.form
            try:
                if f.get("action") == "remove":
                    store.remove_bank_account(uid)
                    flash("Bank account removed. Payouts go to Yo Pay again.", "success")
                elif f.get("action") == "payout_to":
                    store.set_payout_to(uid, f.get("payout_to", ""))
                    flash("Saved.", "success")
                else:
                    a = store.save_bank_account(uid, f)
                    flash(f"Bank account saved: {a['bank']} ****{a['last4']}. Payouts now go to this account (test mode).",
                          "success")
            except ShopError as e:
                flash(str(e), "error")
            return redirect(url_for("seller_bank"))
        return render_template("seller/bank.html", shop=shop, account=store.bank_account(shop["id"]),
                        transfers=store.bank_transfers(shop["id"]), countries=world.countries(world.current_lang()),
                        SCHEMES=bankpay.SCHEME_NAMES, IBAN=sorted(bankpay.IBAN_LENGTH))

    @app.route("/admin/tickets/gtfs", methods=["GET", "POST"])
    @h.admin_required
    def admin_gtfs():
        uid = h.user_id()
        if request.method == "POST":
            f = request.form
            try:
                if f.get("feed_id"):
                    store.set_gtfs_active(uid, int(f["feed_id"]), f.get("active") == "1")
                    flash("Saved.", "success")
                else:
                    up = request.files.get("file")
                    if f.get("sample"):
                        blob = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "gtfs",
                                                 "sample_rwanda.zip"), "rb").read()
                    elif up and up.filename:
                        blob = up.read(gtfs.MAX_BYTES + 1)
                    else:
                        raise ShopError("Please choose a GTFS file (.zip).")
                    r = store.import_gtfs(uid, blob, f.get("name", ""))
                    h.act("Timetable imported (GTFS)", f"{r['operator']}: {r['services']} services")
                    flash(f"Imported: {r['trips']} trips, {r['matched']} of {r['stops']} stops matched to places, "
                          f"{r['services']} services for sale.", "success")
            except (ShopError, ValueError) as e:
                flash(str(e) if isinstance(e, ShopError) else "Please check the form.", "error")
            return redirect(url_for("admin_gtfs"))
        return h.admin_page("admin/gtfs.html", feeds=store.gtfs_feeds())

    @app.route("/admin/bank")
    @h.admin_required
    def admin_bank():
        return h.admin_page("admin/bank.html", rows=store.bank_transfers(None, 200))

    @app.route("/id-check", methods=["GET", "POST"])
    @h.login_required
    def id_check():
        uid = h.user_id()
        if request.method == "POST":
            f = request.form
            try:
                if f.get("action") == "withdraw":
                    n = store.withdraw_id_consent(uid)
                    flash("Your ID photos were deleted." if n else "We keep no ID photos of you.", "success")
                else:
                    live = [x for x in (request.files.get(f"live{i}") for i in range(3)) if x and x.filename]
                    steps = session.get("live_steps") or []
                    store.start_id_check(uid, "courier" if store.courier_of(uid) else "staff" if store.worker_of(uid)
                                         else "customer", f.get("doc_type", ""), f.get("country", ""),
                                         request.files.get("doc"), request.files.get("selfie"),
                                         f.get("consent") == "yes", f.get("mrz1", ""), f.get("mrz2", ""),
                                         f.get("expiry", ""), live_frames=live if len(live) == 3 else None,
                                         live_steps=steps)
                    session.pop("live_steps", None)
                    h.act("ID check sent")
                    flash("Thank you! A person will check your ID soon.", "success")
            except ShopError as e:
                flash(str(e), "error")
            except Exception:
                flash("Please upload two photos (JPG or PNG): the document and a selfie.", "error")
            return redirect(url_for("id_check"))
        u = store.get_user(uid)
        session["live_steps"] = idcheck.new_live_steps()          # version 7: new instructions for every visit
        return render_template("id_check.html", checks=store.id_checks(user_id=uid, limit=10), user=u,
                               live_steps=[idcheck.LIVE_STEPS[s] for s in session["live_steps"]],
                               DOC_TYPES=idcheck.DOC_TYPES, RETENTION=idcheck.RETENTION_DAYS,
                               countries=world.countries(world.current_lang()))

    @app.route("/admin/id-checks", methods=["GET", "POST"])
    @h.admin_required
    def admin_id_checks():
        if request.method == "POST":
            f = request.form
            try:
                store.decide_id_check(h.user_id(), int(f.get("check_id") or 0), f.get("decision") == "approve",
                                      f.get("note", ""))
                flash("Decision saved.", "success")
            except (ShopError, ValueError) as e:
                flash(str(e) if isinstance(e, ShopError) else "Please check the form.", "error")
            return redirect(url_for("admin_id_checks", status=request.args.get("status", "Waiting")))
        status = request.args.get("status", "Waiting")
        if status not in ("Waiting", "Approved", "Refused", "Withdrawn", ""):
            abort(404)
        return h.admin_page("admin/id_checks.html", rows=store.id_checks(status, limit=200), status=status,
                            DOC_TYPES=idcheck.DOC_TYPES, SAME=idcheck.SAME_FROM, DIFF=idcheck.DIFFERENT_BELOW)
