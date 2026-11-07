"""
pay_views.py - Yo Pay pages: wallet, add money, send money, statement, EMI calculator.
"""

import csv
import io

from flask import Response, flash, jsonify, redirect, render_template, request, url_for

import world
import yopay
from core import ShopError


def register(app, store, h):
    login_required, user_id, act = h.login_required, h.user_id, h.act

    def emi_from(amount):
        """The smallest monthly payment of all EMI plans (shown on product pages), or None."""
        plans = yopay.emi_plans(amount)
        return min(p["monthly"] for p in plans) if plans else None
    app.jinja_env.globals["emi_from"] = emi_from

    @app.route("/pay")
    @login_required
    def pay_home():
        uid = user_id()
        return render_template("pay/home.html", s=store.wallet_summary(uid), rows=store.wallet_statement(uid, 50),
                               courier=store.courier_of(uid), KINDS=yopay.WALLET_KINDS)

    @app.route("/pay/add", methods=["GET", "POST"])
    @login_required
    def pay_add():
        f = request.form if request.method == "POST" else {"amount": request.args.get("amount", "500")}
        if request.method == "POST":
            try:
                ok, message, ref = store.topup_with_test_card(user_id(), f.get("amount"), f.get("card"), f.get("expiry"),
                                                              f.get("cvc"), f.get("name"))
                act("Payment", f"Yo Pay top-up {ref} {'ok' if ok else 'declined'}")
                if ok:
                    flash("Money added to your Yo Pay wallet.", "success")
                    return redirect(url_for("pay_home"))
                flash(message, "error")
            except ShopError as e:
                flash(str(e), "error")
        return render_template("pay/add.html", f=f, low=yopay.TOPUP_MIN, high=yopay.TOPUP_MAX)

    @app.route("/pay/send", methods=["GET", "POST"])
    @login_required
    def pay_send():
        f = request.form if request.method == "POST" else request.args
        if request.method == "POST":
            try:
                ref, payee = store.send_money(user_id(), f.get("to"), f.get("amount"), f.get("note"), f.get("password"))
                act("Payment", f"Yo Pay transfer {ref} to user {payee['id']}")
                flash(f"Money sent to {payee['name']}.", "success")
                return redirect(url_for("pay_home"))
            except ShopError as e:
                flash(str(e), "error")
        return render_template("pay/send.html", f=f, wallet=store.get_user(user_id())["wallet"],
                               high=yopay.SEND_MAX, day=yopay.SEND_PER_DAY)

    @app.route("/pay/payee")
    @login_required
    def pay_payee():
        """Show the name before sending (first name and initial only)."""
        p = store.find_payee(request.args.get("to", ""))
        if p is None or p["blocked"] or p["id"] == user_id():
            return jsonify(ok=False)
        parts = p["name"].split()
        return jsonify(ok=True, name=parts[0] + (f" {parts[-1][0]}." if len(parts) > 1 else ""))

    @app.route("/pay/statement.csv")
    @login_required
    def pay_statement_csv():
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["Date", "Type", "Reference", "Details", "Amount (INR)", "Balance (INR)"])
        for r in reversed(store.wallet_statement(user_id(), 5000)):
            w.writerow([r["created_at"], r["kind_name"], r["ref"], r["note"], f"{r['amount'] / 100:.2f}",
                        f"{r['balance'] / 100:.2f}"])
        return Response("﻿" + out.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": "attachment; filename=yo_pay_statement.csv"})

    @app.route("/pay/emi")
    def pay_emi():
        try:
            amount = yopay.parse_rupees(request.args.get("amount") or "15000")
        except ShopError:
            amount = 15000_00
        return render_template("pay/emi.html", amount=amount, plans=yopay.emi_plans(amount), EMI_FROM=yopay.EMI_FROM,
                               NO_COST_FROM=yopay.NO_COST_FROM, PLANS=yopay.EMI_PLANS)

    @app.route("/pay/emi.json")
    def pay_emi_json():
        try:
            amount = yopay.parse_rupees(request.args.get("amount"))
        except ShopError as e:
            return jsonify(ok=False, error=str(e))
        return jsonify(ok=True, plans=[dict(p, monthly_text=world.price(p["monthly"]), total_text=world.price(p["total"]),
                                            interest_text=world.price(p["interest"])) for p in yopay.emi_plans(amount)])
