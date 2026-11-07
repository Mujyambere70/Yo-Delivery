"""
workforce_views.py - version 6: the shift plan, attendance, tasks and performance of the staff.

    /admin/staff/rota          the plan of the week (workers x days); plan or remove a shift
    /admin/staff/attendance    one day: planned shifts against clock-in / clock-out (late, absent, on leave)
    /admin/staff/tasks         give tasks to workers of my branch and follow them
    /admin/staff/performance   numbers per worker for a period (hours, attendance, tasks, work done)
    /staff/tasks/<id>          the worker moves his own task (start, done)
"""

from flask import flash, redirect, request, url_for

import staff as S
import workforce as WF
from core import ShopError


def register(app, store, h):

    def common():
        uid = h.user_id()
        branch = store.branch_of(uid)
        return {"branch_offices": [o for o in store.office_tree() if branch is None or o["id"] in branch],
                "my_abilities": store.abilities_of(uid), "ALL": S.ALL, "PRIORITIES": WF.PRIORITIES,
                "TASK_STATUSES": WF.TASK_STATUSES}

    def back(endpoint, **kw):
        return redirect(request.form.get("next") or url_for(endpoint, **kw))

    @app.route("/admin/staff/rota", methods=["GET", "POST"])
    @h.admin_required
    def admin_rota():
        uid = h.user_id()
        if request.method == "POST":
            f = request.form
            try:
                if f.get("remove"):
                    store.remove_planned_shift(uid, int(f["remove"]))
                    flash("Planned shift removed.", "success")
                else:
                    sid_text = str(f.get("staff_id") or "0")
                    sid, _, help_office = sid_text.partition(":")       # version 7: "12:7" = helps in office 7
                    n = store.plan_shift(uid, int(sid or 0), f.get("day", ""), f.get("start", ""),
                                         f.get("end", ""), f.get("note", ""), f.get("repeat") or 1,
                                         office_id=int(help_office) if help_office else None)
                    flash(f"Shift planned ({n} days)." if n > 1 else "Shift planned.", "success")
            except (ShopError, ValueError) as e:
                flash(str(e) if isinstance(e, ShopError) else "Please check the form.", "error")
            return back("admin_rota", start=f.get("week", ""), office=f.get("office", ""))
        a = request.args
        try:
            r = store.rota(uid, a.get("start") or None, 7, a.get("office") or None)
        except ShopError:
            r = store.rota(uid, None, 7, a.get("office") or None)
        return h.admin_page("admin/rota.html", r=r, office=a.get("office", ""),
                            hours=store.hours_by_office(uid, r["first"], 7), **common())

    @app.route("/admin/staff/attendance")
    @h.admin_required
    def admin_attendance():
        uid = h.user_id()
        try:
            r = store.attendance(uid, request.args.get("day") or None, request.args.get("office") or None)
        except ShopError:
            r = store.attendance(uid, None, request.args.get("office") or None)
        return h.admin_page("admin/attendance.html", r=r, office=request.args.get("office", ""), **common())

    @app.route("/admin/staff/tasks", methods=["GET", "POST"])
    @h.admin_required
    def admin_tasks():
        uid = h.user_id()
        a = request.args
        if request.method == "POST":
            f = request.form
            try:
                if f.get("task_id"):
                    store.move_staff_task(uid, int(f["task_id"]), f.get("status", ""), f.get("note", ""))
                    flash("Task updated.", "success")
                else:
                    store.give_task(uid, int(f.get("staff_id") or 0), f.get("title", ""), f.get("details", ""),
                                    f.get("due", ""), f.get("priority", "Normal"))
                    flash("Task given.", "success")
            except (ShopError, ValueError) as e:
                flash(str(e) if isinstance(e, ShopError) else "Please check the form.", "error")
            return back("admin_tasks")
        workers = [w for w in store.workers(uid) if w["status"] == "Active"]
        rows = store.task_list(uid, status=a.get("status", ""), office_id=a.get("office") or None,
                               staff_id=a.get("worker") or None)
        return h.admin_page("admin/staff_tasks.html", rows=rows, workers=workers, fil=a,
                            task_counts={s: sum(1 for t in store.task_list(uid) if t["status"] == s) for s in WF.TASK_STATUSES},
                            **common())

    @app.route("/admin/staff/performance")
    @h.admin_required
    def admin_performance():
        uid = h.user_id()
        days = request.args.get("days", "30")
        days = int(days) if days in ("7", "30", "90") else 30
        p = store.performance(uid, None, days, request.args.get("office") or None)
        return h.admin_page("admin/performance.html", p=p, days=days, office=request.args.get("office", ""), **common())

    @app.route("/staff/tasks/<int:task_id>", methods=["POST"])
    @h.login_required
    def staff_task_move(task_id):
        try:
            store.move_staff_task(h.user_id(), task_id, request.form.get("status", ""), request.form.get("note", ""))
            flash("Task updated.", "success")
        except ShopError as e:
            flash(str(e), "error")
        return redirect(url_for("staff_home") + "#tasks")
