"""
admin_v3.py - admin pages of the version 3 modules: shipments, couriers, blog, rewards, Yo Pay,
photo checks (the team task board is in board_views.py).
"""

import json

from flask import Response, abort, flash, redirect, render_template, request, url_for

import blog as B
import courier as C
import images
import rewards as R
import world
import yopay
from core import ShopError


def register(app, store, h):
    admin_required, admin_page, act, user_id = h.admin_required, h.admin_page, h.act, h.user_id

    # ---------------- Courier ----------------

    @app.route("/admin/shipments")
    @admin_required
    def admin_shipments():
        status, q = request.args.get("status", "open"), request.args.get("q", "").strip()[:40]
        rows, stats, money = store.admin_shipments(status, q)
        return admin_page("admin/shipments.html", rows=rows, stats=stats, money=money, status=status, q=q,
                          STEPS=C.NETWORK_STEPS + [C.CANCELLED, C.RETURNED])

    @app.route("/admin/shipments/<number>", methods=["GET", "POST"])
    @admin_required
    def admin_shipment(number):
        with store.db() as conn:
            row = conn.execute("SELECT * FROM shipments WHERE number = ?", (number,)).fetchone()
            if row is None:
                abort(404)
            s = store._shipment(conn, row, owner=True)
        if request.method == "POST":
            try:
                store.admin_assign(number, int(request.form.get("courier_id") or 0))
                act("Admin: courier", f"Gave {number} to courier #{request.form.get('courier_id')}")
                flash("The job was given to the courier.", "success")
            except (ShopError, ValueError) as e:
                flash(str(e), "error")
            return redirect(url_for("admin_shipment", number=number))
        couriers = [c for c in store.admin_couriers() if c["status"] == "Approved"]
        return admin_page("admin/shipment.html", s=s, couriers=couriers)

    @app.route("/admin/couriers", methods=["GET", "POST"])
    @admin_required
    def admin_couriers():
        if request.method == "POST":
            f = request.form
            try:
                cid = int(f.get("courier_id") or 0)
                if f.get("action") == "cash":
                    amount = store.courier_cash_deposit(cid)
                    act("Admin: courier", f"Courier #{cid} gave {world.format_money(amount, 'INR')} cash")
                    flash(f"Cash received: {world.format_money(amount, 'INR')}.", "success")
                else:
                    store.decide_courier(cid, f.get("status", ""), f.get("reason", ""))
                    act("Admin: courier", f"Courier #{cid} -> {f.get('status')}")
                    flash("Decision saved. The courier gets a message.", "success")
            except (ShopError, ValueError) as e:
                flash(str(e), "error")
            return redirect(url_for("admin_couriers"))
        rows = store.admin_couriers()
        for c in rows:
            c["check"] = store.photo_check(c["photo"]) if c["photo"] else None
        return admin_page("admin/couriers.html", rows=rows, STATUSES=C.COURIER_STATUSES)

    @app.route("/admin/private/<path:name>")
    @admin_required
    def admin_private(name):
        if not name.startswith("private/") or ".." in name:
            abort(404)
        found = store.media(name)
        if not found:
            abort(404)
        return Response(found[1], mimetype=found[0], headers={"Cache-Control": "private, no-store"})

    # ---------------- Blog ----------------

    @app.route("/admin/blog")
    @admin_required
    def admin_blog():
        status = request.args.get("status", "")
        return admin_page("admin/blog.html", posts=store.posts(status=status, limit=200), status=status,
                          STATUSES=B.POST_STATUSES, CATS=B.POST_CATEGORIES)

    @app.route("/admin/blog/new", methods=["GET", "POST"])
    @app.route("/admin/blog/<int:post_id>/edit", methods=["GET", "POST"])
    @admin_required
    def admin_post_edit(post_id=None):
        post = store.post_by_id(post_id) if post_id else None
        if post_id and post is None:
            abort(404)
        f = request.form if request.method == "POST" else (post or {"category": "news", "lang": "en", "status": "Draft"})
        if request.method == "POST":
            try:
                me = store.get_user(user_id())
                if post and post["seller_id"]:              # a seller story: the admin can edit the text and publish
                    slug = store.save_post(request.form, post["author_id"], post["author_name"], post_id)
                    store.set_post_status(post_id, request.form.get("status", "Review"))
                else:
                    slug = store.save_post(request.form, me["id"], me["name"], post_id)
                cover = request.files.get("cover_file")
                if cover and cover.filename:
                    name = images.save_blog_cover(cover, slug, store)
                    with store.db() as conn:
                        conn.execute("UPDATE posts SET cover = ? WHERE slug = ?", (name, slug))
                act("Admin: blog", f"Saved post {slug}")
                flash("Post saved.", "success")
                return redirect(url_for("admin_blog"))
            except ShopError as e:
                flash(str(e), "error")
        return admin_page("admin/post_form.html", f=f, post=post, CATS=B.POST_CATEGORIES, STATUSES=B.POST_STATUSES,
                          LANGUAGES=world.LANGUAGES, preview=B.render_markdown(f.get("body", "")) if post else "")

    @app.route("/admin/blog/<int:post_id>/<action>", methods=["POST"])
    @admin_required
    def admin_post_action(post_id, action):
        try:
            if action == "delete":
                cover = store.delete_post(post_id)
                if cover.startswith("blog/"):
                    store.delete_media(cover)
                flash("Post deleted.", "success")
            elif action in ("publish", "review", "draft"):
                store.set_post_status(post_id, {"publish": "Published", "review": "Review", "draft": "Draft"}[action],
                                      request.form.get("note", ""))
                flash("Status changed.", "success")
            else:
                abort(404)
            act("Admin: blog", f"Post #{post_id}: {action}")
        except ShopError as e:
            flash(str(e), "error")
        return redirect(url_for("admin_blog", status=request.args.get("status", "")))

    # ---------------- Rewards ----------------

    @app.route("/admin/rewards", methods=["GET", "POST"])
    @admin_required
    def admin_rewards():
        edit = None
        if request.method == "POST":
            f = request.form
            try:
                if f.get("action") == "toggle":
                    store.toggle_question(int(f.get("question_id")))
                else:
                    store.save_question(f, int(f.get("question_id")) if f.get("question_id") else None)
                    flash("Question saved.", "success")
                act("Admin: rewards", "Quiz question changed")
            except (ShopError, ValueError) as e:
                flash(str(e), "error")
                edit = f
            if edit is None:
                return redirect(url_for("admin_rewards", lang=request.args.get("lang", "")))
        lang = request.args.get("lang", "")
        qs = store.quiz_questions(lang)
        if request.args.get("edit"):
            edit = next((q for q in qs if str(q["id"]) == request.args.get("edit")), None)
        return admin_page("admin/rewards.html", qs=qs, lang=lang, edit=edit, st=store.rewards_stats(),
                          leaders=store.leaderboard(30, 10), LANGUAGES=world.LANGUAGES, REWARDS=R.REWARDS)

    # ---------------- Yo Pay ----------------

    @app.route("/admin/yopay")
    @admin_required
    def admin_yopay():
        kind = request.args.get("kind", "")
        rows, totals, held = store.wallet_report(300, kind if kind in yopay.WALLET_KINDS else "")
        return admin_page("admin/yopay.html", rows=rows, totals=totals, held=held, kind=kind, KINDS=yopay.WALLET_KINDS,
                          audit=store.wallet_audit())

    # ---------------- Photo checks ----------------

    @app.route("/admin/photos")
    @admin_required
    def admin_photos():
        purpose = request.args.get("purpose", "")
        rows = store.photo_checks(purpose if purpose in ("product", "courier", "pod") else None)
        return admin_page("admin/photos.html", rows=rows, purpose=purpose)
