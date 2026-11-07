"""
v5_views.py - the pages of version 5: messages between customers and sellers (/messages), "my data"
(download / delete the account), product texts in other languages and automatic payouts (Seller Centre), and
for the admin the chat moderation queue and the recommendation model (/admin/chat, /admin/model).
"""

import json

from flask import Response, abort, flash, redirect, render_template, request, session, url_for

import world
from core import ShopError


def register(app, store, h):
    login_required, admin_required, seller_required = h.login_required, h.admin_required, h.seller_required
    user_id, act = h.user_id, h.act

    # ---------------------------------------------------------------- messages

    @app.route("/messages")
    @login_required
    def messages():
        return render_template("messages/list.html", threads=store.chat_threads(user_id()))

    @app.route("/messages/new", methods=["POST"])
    @login_required
    def messages_new():
        try:
            tid = store.start_chat(user_id(), request.form.get("product_id", 0) or 0,
                                   request.form.get("order", ""), request.form.get("text", ""))
            act("Chat", f"Wrote to a seller (conversation {tid})")
            return redirect(url_for("messages_thread", thread_id=tid))
        except (ShopError, ValueError) as e:
            flash(str(e), "error")
            return redirect(request.referrer or url_for("messages"))

    @app.route("/messages/<int:thread_id>", methods=["GET", "POST"])
    @login_required
    def messages_thread(thread_id):
        if request.method == "POST":
            try:
                r = store.send_chat(user_id(), thread_id, request.form.get("text", ""))
                if r["status"] != "Visible":
                    flash(r["reason"], "info")
                elif r["reason"]:
                    flash(r["reason"], "info")
            except ShopError as e:
                flash(str(e), "error")
            return redirect(url_for("messages_thread", thread_id=thread_id) + "#end")
        try:
            t, msgs = store.chat_thread(user_id(), thread_id)
        except ShopError:
            abort(404)
        return render_template("messages/thread.html", t=t, msgs=msgs)

    @app.route("/messages/report/<int:message_id>", methods=["POST"])
    @login_required
    def messages_report(message_id):
        try:
            store.report_chat(user_id(), message_id)
            flash("Thank you. A moderator will look at this message.", "success")
        except ShopError as e:
            flash(str(e), "error")
        return redirect(request.referrer or url_for("messages"))

    # ---------------------------------------------------------------- my data

    @app.route("/account/data")
    @login_required
    def my_data():
        return render_template("account_data.html", user=store.get_user(user_id()))

    @app.route("/account/data/download")
    @login_required
    def my_data_download():
        data = store.export_my_data(user_id())
        act("Privacy", "Downloaded my data")
        body = json.dumps(data, ensure_ascii=False, indent=1, default=str)
        return Response(body, mimetype="application/json",
                        headers={"Content-Disposition": f"attachment; filename=yo-delivery-my-data-{user_id()}.json"})

    @app.route("/account/delete", methods=["POST"])
    @login_required
    def my_account_delete():
        if request.form.get("confirm", "").strip().upper() not in ("DELETE", "УДАЛИТЬ"):
            flash("Please type DELETE to confirm.", "error")
            return redirect(url_for("my_data"))
        uid = user_id()
        try:
            store.delete_my_account(uid, request.form.get("password", ""))
        except ShopError as e:
            flash(str(e), "error")
            return redirect(url_for("my_data"))
        act("Privacy", "Account deleted by the customer", uid=uid)
        session.clear()
        flash("Your account is deleted. Thank you for using Yo Delivery.", "success")
        return redirect(url_for("index"))

    # ---------------------------------------------------------------- Seller Centre

    @app.route("/seller/products/<int:pid>/texts", methods=["GET", "POST"])
    @seller_required
    def seller_product_texts(pid):
        shop = request.shop
        try:
            p = store.get_product(pid=pid)
        except ShopError:
            abort(404)
        if p.seller_id != shop["id"]:
            abort(404)
        drafts = {}
        if request.method == "POST" and request.form.get("action") == "draft":      # version 6: drafts to check
            have = store.product_texts(pid)
            empty = [lang for lang in world.LANGUAGES if lang != "en" and not have.get(lang)]
            drafts = store.translation_drafts(p, empty)
            if drafts:
                flash("Drafts made for the empty languages. Please read and correct them, then press Save.", "info")
            return render_template("seller/texts.html", shop=shop, p=p, texts={**have, **drafts}, drafts=drafts,
                                   LANGS={k: v for k, v in world.LANGUAGES.items() if k != "en"})
        if request.method == "POST":
            texts = {lang: (request.form.get(f"name_{lang}", ""), request.form.get(f"about_{lang}", ""))
                     for lang in world.LANGUAGES if lang != "en"}
            try:
                store.save_product_texts(pid, texts, seller_id=shop["id"])
                act("Seller", f"Product texts in other languages: {p.name[:40]}")
                flash("Saved. Customers now see your texts in their language.", "success")
                return redirect(url_for("seller_product_texts", pid=pid))
            except ShopError as e:
                flash(str(e), "error")
        return render_template("seller/texts.html", shop=shop, p=p, texts=store.product_texts(pid), drafts=drafts,
                               LANGS={k: v for k, v in world.LANGUAGES.items() if k != "en"})

    @app.route("/seller/payouts/auto", methods=["POST"])
    @seller_required
    def seller_auto_payout():
        on = request.form.get("on") == "1"
        store.set_auto_payout(request.shop["id"], on)
        flash("Automatic payouts to Yo Pay are on." if on else "Automatic payouts are off: the admin pays you.", "success")
        return redirect(url_for("seller_orders"))

    # ---------------------------------------------------------------- admin

    @app.route("/admin/chat", methods=["GET", "POST"])
    @admin_required
    def admin_chat():
        if request.method == "POST":
            me = store.get_user(user_id())
            try:
                new = store.decide_chat(int(request.form.get("message_id", 0)), request.form.get("action") == "approve",
                                        me["name"])
                act("Admin", f"Chat message {request.form.get('message_id')}: {new}")
                flash(f"Message {new.lower()}.", "success")
            except (ShopError, ValueError) as e:
                flash(str(e), "error")
            return redirect(url_for("admin_chat"))
        return h.admin_page("admin/chat.html", queue=store.chat_queue(), model=store.chat_model_report())

    @app.route("/admin/model", methods=["GET", "POST"])
    @admin_required
    def admin_model():
        if request.method == "POST":
            report = store.learn_recommendations(force=request.form.get("force") == "1")
            act("Admin", "Trained the recommendation model")
            flash("The model is in use." if report.get("used") else
                  "The model was trained but is not used: " + report.get("reason", ""), "info")
            return redirect(url_for("admin_model"))
        return h.admin_page("admin/model.html", report=store.rec_model_report(), content=store.content_report())
