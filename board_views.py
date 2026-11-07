"""
board_views.py - the task board pages (seller, courier, admin team) and their small JSON API.
"""

from flask import abort, flash, jsonify, redirect, render_template, request, url_for

import taskboard as T
from core import ShopError


def register(app, store, h):
    user_id = h.user_id

    def board_of(scope):
        """Which board the signed-in person may use for this scope (or None)."""
        uid = user_id()
        if not uid:
            return None, None
        if scope == "seller":                       # the owner, or an employee allowed to use the board
            shop, abil = store.seller_membership(uid)
            ok = shop and shop["status"] == "Approved" and "seller.board" in abil
            return (f"seller:{shop['id']}", shop) if ok else (None, None)
        if scope == "courier":
            c = store.courier_of(uid)
            return (f"courier:{c['id']}", c) if c and c["status"] == "Approved" else (None, None)
        if scope == "admin":
            u = store.get_user(uid)
            return ("admin", u) if u and (u["is_admin"] or store.can(uid, "board.use")) else (None, None)
        return None, None

    def guard(scope):
        if not user_id():
            return None, redirect(url_for("login", next=request.full_path))
        board, owner = board_of(scope)
        if board is None:
            abort(403 if scope == "admin" else 404)
        return (board, owner), None

    @app.route("/board/<scope>")
    def board_page(scope):
        got, stop = guard(scope)
        if stop:
            return stop
        board, owner = got
        kw = dict(scope=scope, owner=owner, cols=store.board(board), COLS=T.BOARD_COLUMNS, PRIORITIES=T.PRIORITIES,
                  shop=owner if scope == "seller" else None)
        if scope == "admin":
            return h.admin_page("admin/board.html", **kw)
        return render_template("board/page.html", **kw)

    @app.route("/board/<scope>/add", methods=["POST"])
    def board_add(scope):
        got, stop = guard(scope)
        if stop:
            return stop
        try:
            store.add_task(got[0], request.form, by=store.get_user(user_id())["name"].split()[0])
        except ShopError as e:
            flash(str(e), "error")
        return redirect(url_for("board_page", scope=scope))

    @app.route("/board/<scope>/<int:task_id>/move", methods=["POST"])
    def board_move(scope, task_id):
        got, stop = guard(scope)
        if stop:
            return jsonify(ok=False), 401
        try:
            store.move_task(got[0], task_id, request.form.get("status"), request.form.get("before"))
        except ShopError as e:
            return jsonify(ok=False, error=str(e)), 400
        return jsonify(ok=True)

    @app.route("/board/<scope>/<int:task_id>/edit", methods=["POST"])
    def board_edit(scope, task_id):
        got, stop = guard(scope)
        if stop:
            return stop
        try:
            if request.form.get("delete") == "1":
                store.delete_task(got[0], task_id)
            elif request.form.get("status") in T.BOARD_COLUMNS and not request.form.get("title"):
                store.move_task(got[0], task_id, request.form.get("status"))
            else:
                store.edit_task(got[0], task_id, request.form)
        except ShopError as e:
            flash(str(e), "error")
        return redirect(url_for("board_page", scope=scope))
