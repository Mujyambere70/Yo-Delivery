"""
workforce.py - version 6 (mixin class used by Store): the work plan of the staff.

Shift plan (rota) and attendance
    A manager with the ability "shifts.manage" plans the shifts of the workers of his branch: a day, a start and an
    end time in the time of the office (Kigali, Krasnoyarsk, Ambala...). The worker sees his plan on /staff and
    clocks in and out there (staff.py). Attendance compares the plan with the clock:
        On leave (approved time off), Absent (no clock-in and the shift is over), Late (clock-in more than
        LATE_MIN minutes after the start), On time, Not started yet; work without a plan is "Not planned".
Tasks
    A manager with the ability "tasks.manage" gives a task to a worker of his branch (title, details, priority, due
    day). The worker moves it To do -> In progress -> Done; the manager can also cancel it. Every move is a
    conditional UPDATE on the old status, so two clicks at the same time cannot both win.
Performance
    For every worker of the branch and a period: hours worked, shifts, late and absent days, tasks done and done on
    time, and the work of his kind - parcels delivered (couriers), hub scans, tickets checked (conductors) and chat
    messages decided (moderators). Simple numbers that a manager reads together with the person, not a ranking.
"""

from datetime import date, datetime, timedelta

import geo
import tickets as TK
from core import ShopError, now
from staff import ALL, split

WORKFORCE_SCHEMA = """
CREATE TABLE IF NOT EXISTS staff_rota (
    id INTEGER PRIMARY KEY AUTOINCREMENT, staff_id INTEGER NOT NULL REFERENCES staff(id),
    office_id INTEGER NOT NULL, day TEXT NOT NULL, start_hm TEXT NOT NULL, end_hm TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '', created_by INTEGER, created_at TEXT NOT NULL, UNIQUE (staff_id, day)
);
CREATE INDEX IF NOT EXISTS idx_staff_rota_day ON staff_rota(day);
CREATE TABLE IF NOT EXISTS staff_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT, office_id INTEGER NOT NULL, staff_id INTEGER NOT NULL REFERENCES staff(id),
    title TEXT NOT NULL, details TEXT NOT NULL DEFAULT '', priority TEXT NOT NULL DEFAULT 'Normal',
    due_day TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'To do', note TEXT NOT NULL DEFAULT '',
    created_by INTEGER, created_at TEXT NOT NULL, started_at TEXT NOT NULL DEFAULT '', done_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_staff_tasks_staff ON staff_tasks(staff_id, status);
"""

LATE_MIN = 10
TASK_STATUSES = ("To do", "In progress", "Done", "Cancelled")
PRIORITIES = ("Low", "Normal", "High", "Urgent")
# who may move a task from one status to another: the worker himself ("me") or his manager ("boss")
TASK_MOVES = {("To do", "In progress"): "me", ("In progress", "Done"): "me", ("To do", "Done"): "me",
              ("In progress", "To do"): "me", ("To do", "Cancelled"): "boss", ("In progress", "Cancelled"): "boss",
              ("Done", "In progress"): "boss", ("Cancelled", "To do"): "boss"}


def _hm(text):
    try:
        t = datetime.strptime(str(text).strip(), "%H:%M")
    except ValueError:
        raise ShopError("Please write the time like 08:30.")
    return t.strftime("%H:%M")


def _day(text):
    try:
        return datetime.strptime(str(text).strip(), "%Y-%m-%d").date()
    except ValueError:
        raise ShopError("Please choose a day.")


def _clean(text, limit):
    return " ".join(str(text or "").split())[:limit]


class WorkforceMixin:
    # ---------------------------------------------------------------- time of an office
    def office_tz(self, office):
        """The time zone of an office: of its place, else of its country, else the time of the server."""
        p = geo.get(office.get("place") or "") if office.get("place") else None
        if p and p.get("tz"):
            return p["tz"]
        if office.get("country"):
            return TK._country_tz(office["country"])
        return ""

    def _to_office_time(self, server_text, tz):
        """A time written by now() (server clock) as the time of the office."""
        if not server_text:
            return None
        d = datetime.fromisoformat(server_text)
        if not tz:
            return d
        from zoneinfo import ZoneInfo
        return d.astimezone(ZoneInfo(tz)).replace(tzinfo=None)

    # ---------------------------------------------------------------- shift plan
    def _target(self, conn, actor_id, staff_id, need, self_ok=False):
        admin, actor, branch = self._actor(conn, actor_id, need)
        t = conn.execute("SELECT s.*, o.place AS office_place, o.country AS office_country, o.name AS office "
                         "FROM staff s JOIN offices o ON o.id = s.office_id WHERE s.id = ?", (staff_id,)).fetchone()
        if t is None or t["status"] != "Active":
            raise ShopError("This worker is not active.")
        t = dict(t)
        if not admin:
            mine = self_ok and t["user_id"] == actor["user_id"]
            if not mine and not self._may_manage(admin, actor, branch, t):
                raise ShopError("This worker is not in your branch.")
        return t

    def plan_shift(self, actor_id, staff_id, day, start_hm, end_hm, note="", repeat_days=1, office_id=None):
        """Plan (or change) the shift of a worker on one day or on several days in a row.
        Version 7: office_id = another office where the worker helps on these days (staff_offices); the manager of
        that office may plan it too."""
        first, start, end = _day(day), _hm(start_hm), _hm(end_hm)
        if first < date.today() - timedelta(days=1):
            raise ShopError("Shifts can only be planned from today.")
        if start == end:
            raise ShopError("The shift must be longer than 0 minutes.")
        repeat = max(1, min(int(repeat_days or 1), 31))
        days = [(first + timedelta(days=i)).isoformat() for i in range(repeat)]
        with self.db() as conn:
            home = conn.execute("SELECT office_id FROM staff WHERE id = ?", (staff_id,)).fetchone()
            if office_id and home and int(office_id) != home["office_id"]:
                oid = int(office_id)
                for d in days:
                    if not conn.execute("SELECT 1 FROM staff_offices WHERE staff_id = ? AND office_id = ? AND "
                                        "from_day <= ? AND to_day >= ?", (staff_id, oid, d, d)).fetchone():
                        raise ShopError("The worker does not help in this office on {0}.".replace("{0}", d))
                try:                                       # his own manager ...
                    t = self._target(conn, actor_id, staff_id, "shifts.manage", self_ok=True)
                except ShopError:                          # ... or the manager of the office where he helps
                    if not self._helper_allowed(conn, actor_id, staff_id, oid, days):
                        raise ShopError("This worker is not in your branch.")
                    t = conn.execute("SELECT * FROM staff WHERE id = ? AND status = 'Active'", (staff_id,)).fetchone()
                    if t is None:
                        raise ShopError("This worker is not active.")
                t = dict(t)
                t["office_id"] = oid
            else:
                t = self._target(conn, actor_id, staff_id, "shifts.manage", self_ok=True)
            for i in range(repeat):
                d = (first + timedelta(days=i)).isoformat()
                conn.execute("INSERT INTO staff_rota (staff_id, office_id, day, start_hm, end_hm, note, created_by, "
                             "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (staff_id, day) DO UPDATE SET "
                             "start_hm = excluded.start_hm, end_hm = excluded.end_hm, note = excluded.note, "
                             "office_id = excluded.office_id",
                             (staff_id, t["office_id"], d, start, end, _clean(note, 120), actor_id, now()))
            self._staff_log(conn, actor_id, staff_id, t["office_id"], "Shift planned",
                            f"{first}{f' (+{repeat - 1} days)' if repeat > 1 else ''} {start}-{end}")
        return repeat

    def remove_planned_shift(self, actor_id, rota_id):
        with self.db() as conn:
            r = conn.execute("SELECT * FROM staff_rota WHERE id = ?", (rota_id,)).fetchone()
            if r is None:
                raise ShopError("This shift does not exist.")
            self._target(conn, actor_id, r["staff_id"], "shifts.manage", self_ok=True)
            conn.execute("DELETE FROM staff_rota WHERE id = ?", (rota_id,))
            self._staff_log(conn, actor_id, r["staff_id"], r["office_id"], "Planned shift removed", r["day"])

    def _branch_workers(self, user_id, office_id=None):
        own = [w for w in self.workers(user_id, office_id=office_id) if w["status"] == "Active"]
        if hasattr(self, "helpers_in"):                     # version 7: helpers from other branches, once
            seen = {w["id"] for w in own}
            for h in self.helpers_in(user_id, office_id):
                if h["id"] not in seen:
                    seen.add(h["id"])
                    h = dict(h)
                    h["office"] = f"{h['office']} \u2192 {h['help_office']}"
                    h["helper"] = True
                    own.append(h)
        return own

    def _planned(self, conn, staff_ids, first, last):
        if not staff_ids:
            return {}
        marks = ", ".join("?" * len(staff_ids))
        return {(r["staff_id"], r["day"]): dict(r) for r in conn.execute(
            f"SELECT * FROM staff_rota WHERE staff_id IN ({marks}) AND day >= ? AND day <= ?",
            (*staff_ids, first, last))}

    def _shifts_by_day(self, conn, workers, first, last):
        """{(staff_id, office day): [shift, ...]} with times in the time of each worker's office."""
        ids = [w["id"] for w in workers]
        if not ids:
            return {}
        tz = {w["id"]: self.office_tz({"place": w.get("office_place", ""), "country": w.get("office_country", "")})
              for w in workers}
        marks = ", ".join("?" * len(ids))
        lo = (datetime.fromisoformat(first) - timedelta(days=1)).strftime("%Y-%m-%d")
        hi = (datetime.fromisoformat(last) + timedelta(days=2)).strftime("%Y-%m-%d")
        out = {}
        for r in conn.execute(f"SELECT * FROM staff_shifts WHERE staff_id IN ({marks}) AND start_at >= ? AND start_at < ? "
                              "ORDER BY start_at", (*ids, lo, hi)):
            start = self._to_office_time(r["start_at"], tz[r["staff_id"]])
            end = self._to_office_time(r["end_at"], tz[r["staff_id"]]) if r["end_at"] else None
            day = start.strftime("%Y-%m-%d")
            if first <= day <= last:
                out.setdefault((r["staff_id"], day), []).append({"start": start, "end": end, "minutes": r["minutes"]})
        return out

    def _leave_days(self, conn, staff_ids, first, last):
        if not staff_ids:
            return set()
        marks = ", ".join("?" * len(staff_ids))
        out = set()
        for r in conn.execute(f"SELECT staff_id, from_day, to_day FROM staff_leave WHERE status = 'Approved' AND "
                              f"staff_id IN ({marks}) AND from_day <= ? AND to_day >= ?", (*staff_ids, last, first)):
            d = max(_day(r["from_day"]), _day(first))
            while d <= min(_day(r["to_day"]), _day(last)):
                out.add((r["staff_id"], d.isoformat()))
                d += timedelta(days=1)
        return out

    def _with_office(self, workers):
        rows = {o["id"]: o for o in self.offices()}
        for w in workers:
            o = rows.get(w["office_id"], {})
            w["office_place"], w["office_country"] = o.get("place", ""), o.get("country", "")
        return workers

    @staticmethod
    def _attendance_state(plan, shifts, on_leave, day, office_now):
        if on_leave:
            return "On leave"
        if plan is None:
            return "Not planned" if shifts else ""
        start = datetime.fromisoformat(f"{day} {plan['start_hm']}")
        end = datetime.fromisoformat(f"{day} {plan['end_hm']}")
        if end <= start:
            end += timedelta(days=1)                       # a night shift ends the next morning
        if not shifts:
            if office_now >= end:
                return "Absent"
            return "Not started" if office_now < start + timedelta(minutes=LATE_MIN) else "Late"
        first_in = min(s["start"] for s in shifts)
        return "Late" if first_in > start + timedelta(minutes=LATE_MIN) else "On time"

    def rota(self, user_id, first_day=None, days=7, office_id=None):
        """The plan of the week for the workers of my branch: rows of workers, a cell for each day."""
        first = _day(first_day) if first_day else date.today() - timedelta(days=date.today().weekday())
        days = max(1, min(int(days), 31))
        day_list = [(first + timedelta(days=i)).isoformat() for i in range(days)]
        workers = self._with_office(self._branch_workers(user_id, office_id))
        ids = [w["id"] for w in workers]
        with self.db() as conn:
            plan = self._planned(conn, ids, day_list[0], day_list[-1])
            shifts = self._shifts_by_day(conn, workers, day_list[0], day_list[-1])
            leave = self._leave_days(conn, ids, day_list[0], day_list[-1])
        rows = []
        for w in workers:
            tz = self.office_tz({"place": w["office_place"], "country": w["office_country"]})
            office_now = self._to_office_time(now(), tz)
            cells = []
            for d in day_list:
                p, s = plan.get((w["id"], d)), shifts.get((w["id"], d), [])
                cells.append({"day": d, "plan": p, "worked": sum(x["minutes"] for x in s),
                              "state": self._attendance_state(p, s, (w["id"], d) in leave, d, office_now)})
            rows.append({"worker": w, "tz": tz, "cells": cells,
                         "planned_min": sum(self._plan_minutes(c["plan"]) for c in cells if c["plan"])})
        return {"days": day_list, "rows": rows, "first": first.isoformat(),
                "prev": (first - timedelta(days=7)).isoformat(), "next": (first + timedelta(days=7)).isoformat()}

    @staticmethod
    def _plan_minutes(p):
        a = datetime.strptime(p["start_hm"], "%H:%M")
        b = datetime.strptime(p["end_hm"], "%H:%M")
        if b <= a:
            b += timedelta(days=1)
        return int((b - a).total_seconds() // 60)

    def attendance(self, user_id, day=None, office_id=None):
        """One day: every planned or worked shift of my branch with its state, and the numbers of the day."""
        d = (_day(day) if day else date.today()).isoformat()
        r = self.rota(user_id, d, 1, office_id)
        rows = []
        for row in r["rows"]:
            c = row["cells"][0]
            if c["plan"] or c["worked"] or c["state"]:
                with self.db() as conn:
                    sh = self._shifts_by_day(conn, [row["worker"]], d, d).get((row["worker"]["id"], d), [])
                rows.append({"worker": row["worker"], "plan": c["plan"], "state": c["state"], "worked": c["worked"],
                             "first_in": min((s["start"] for s in sh), default=None),
                             "last_out": max((s["end"] for s in sh if s["end"]), default=None)})
        counts = {k: sum(1 for x in rows if x["state"] == k)
                  for k in ("On time", "Late", "Absent", "On leave", "Not started", "Not planned")}
        return {"day": d, "rows": rows, "counts": counts,
                "prev": (_day(d) - timedelta(days=1)).isoformat(), "next": (_day(d) + timedelta(days=1)).isoformat()}

    def hours_by_office(self, user_id, first_day=None, days=7):
        """Hours worked and planned per office of my branch in a period."""
        r = self.rota(user_id, first_day, days)
        out = {}
        for row in r["rows"]:
            o = out.setdefault(row["worker"]["office_id"], {"office": row["worker"]["office"], "workers": 0,
                                                            "worked_min": 0, "planned_min": 0, "late": 0, "absent": 0})
            o["workers"] += 1
            o["worked_min"] += sum(c["worked"] for c in row["cells"])
            o["planned_min"] += row["planned_min"]
            o["late"] += sum(1 for c in row["cells"] if c["state"] == "Late")
            o["absent"] += sum(1 for c in row["cells"] if c["state"] == "Absent")
        return sorted(out.values(), key=lambda x: x["office"])

    def my_rota(self, user_id, days=7):
        w = self.worker_of(user_id)
        if not w:
            return []
        first = date.today().isoformat()
        last = (date.today() + timedelta(days=days - 1)).isoformat()
        with self.db() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM staff_rota WHERE staff_id = ? AND day >= ? AND day <= ? "
                                                  "ORDER BY day", (w["id"], first, last))]

    # ---------------------------------------------------------------- tasks
    def give_task(self, actor_id, staff_id, title, details="", due_day="", priority="Normal"):
        title = _clean(title, 120)
        if len(title) < 3:
            raise ShopError("Please write what the worker should do.")
        if priority not in PRIORITIES:
            priority = "Normal"
        due = _day(due_day).isoformat() if str(due_day or "").strip() else ""
        if due and due < date.today().isoformat():
            raise ShopError("The due day is in the past.")
        with self.db() as conn:
            t = self._target(conn, actor_id, staff_id, "tasks.manage", self_ok=True)
            cur = conn.execute("INSERT INTO staff_tasks (office_id, staff_id, title, details, priority, due_day, "
                               "created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                               (t["office_id"], staff_id, title, str(details or "").strip()[:2000], priority, due,
                                actor_id, now()))
            self._staff_log(conn, actor_id, staff_id, t["office_id"], "Task given", title)
            task_id = cur.lastrowid
            if t["user_id"] != actor_id:
                self.notify(conn, t["user_id"], "New task", title, url="/staff#tasks")
        return task_id

    def task_list(self, user_id, mine=False, status="", office_id=None, staff_id=None):
        with self.db() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT t.*, u.name AS worker, s.user_id, o.name AS office, c.name AS given_by FROM staff_tasks t "
                "JOIN staff s ON s.id = t.staff_id JOIN users u ON u.id = s.user_id JOIN offices o ON o.id = t.office_id "
                "LEFT JOIN users c ON c.id = t.created_by ORDER BY t.status IN ('Done', 'Cancelled'), "
                "CASE t.priority WHEN 'Urgent' THEN 0 WHEN 'High' THEN 1 WHEN 'Normal' THEN 2 ELSE 3 END, "
                "t.due_day = '', t.due_day, t.id DESC")]
        if mine:
            rows = [r for r in rows if r["user_id"] == user_id]
        else:
            branch = self.branch_of(user_id)
            if branch is not None:
                rows = [r for r in rows if r["office_id"] in branch]
            if office_id:
                sub = self.subtree(int(office_id))
                rows = [r for r in rows if r["office_id"] in sub]
        if staff_id:
            rows = [r for r in rows if r["staff_id"] == int(staff_id)]
        if status:
            rows = [r for r in rows if r["status"] == status]
        today = date.today().isoformat()
        for r in rows:
            r["overdue"] = bool(r["due_day"] and r["due_day"] < today and r["status"] not in ("Done", "Cancelled"))
        return rows

    def move_staff_task(self, user_id, task_id, status, note=""):
        if status not in TASK_STATUSES:
            raise ShopError("Unknown status.")
        with self.db() as conn:
            t = conn.execute("SELECT t.*, s.user_id, s.office_id AS worker_office, s.is_manager, s.abilities "
                             "FROM staff_tasks t JOIN staff s ON s.id = t.staff_id WHERE t.id = ?", (task_id,)).fetchone()
            if t is None:
                raise ShopError("This task does not exist.")
            who = TASK_MOVES.get((t["status"], status))
            if who is None:
                raise ShopError(f"A task cannot go from \"{t['status']}\" to \"{status}\".")
            if who == "me" and t["user_id"] != user_id:
                who = "boss"                                 # a manager may also move the task of his worker
            if who == "boss":
                admin, actor, branch = self._actor(conn, user_id, "tasks.manage")
                if not admin and t["office_id"] not in branch:
                    raise ShopError("This task is not in your branch.")
            extra = ""
            if status == "In progress":
                extra = ", started_at = '" + now() + "'"
            elif status == "Done":
                extra = ", done_at = '" + now() + "'"
            elif status == "To do":
                extra = ", started_at = '', done_at = ''"
            moved = conn.execute(f"UPDATE staff_tasks SET status = ?, note = ?{extra} WHERE id = ? AND status = ?",
                                 (status, _clean(note, 300) or t["note"], task_id, t["status"])).rowcount
            if not moved:
                raise ShopError("Somebody changed this task a moment ago. Please look again.")
            self._staff_log(conn, user_id, t["staff_id"], t["office_id"], "Task " + status.lower(), t["title"])

    # ---------------------------------------------------------------- performance
    def performance(self, user_id, first_day=None, days=30, office_id=None):
        first = _day(first_day) if first_day else date.today() - timedelta(days=days - 1)
        last = min(first + timedelta(days=days - 1), date.today())
        days = (last - first).days + 1
        r = self.rota(user_id, first.isoformat(), days, office_id)
        a, b = first.isoformat(), last.isoformat() + " 23:59:59"
        out = []
        with self.db() as conn:
            for row in r["rows"]:
                w = row["worker"]
                cells = row["cells"]
                tasks = [dict(t) for t in conn.execute("SELECT status, due_day, done_at FROM staff_tasks WHERE staff_id = ? "
                                                       "AND (done_at BETWEEN ? AND ? OR (status IN ('To do', 'In progress') "
                                                       "AND due_day != '' AND due_day <= ?))",
                                                       (w["id"], a, b, last.isoformat()))]
                done = [t for t in tasks if t["status"] == "Done"]
                on_time = [t for t in done if not t["due_day"] or t["done_at"][:10] <= t["due_day"]]
                work = {}
                c = conn.execute("SELECT id FROM couriers WHERE user_id = ?", (w["user_id"],)).fetchone()
                if c:
                    work["delivered"] = conn.execute("SELECT COUNT(*) FROM shipments WHERE courier_id = ? AND "
                                                     "status = 'Delivered' AND delivered_at BETWEEN ? AND ?",
                                                     (c["id"], a, b)).fetchone()[0]
                work["hub_scans"] = conn.execute("SELECT COUNT(*) FROM staff_log WHERE staff_id = ? AND action = "
                                                 "'Parcel scanned' AND at BETWEEN ? AND ?", (w["id"], a, b)).fetchone()[0]
                work["tickets_checked"] = conn.execute("SELECT COUNT(*) FROM ticket_passengers WHERE boarded_by = ? "
                                                       "AND boarded_at BETWEEN ? AND ?", (w["user_id"], a, b)).fetchone()[0]
                work["chats_decided"] = conn.execute("SELECT COUNT(*) FROM chat_messages WHERE decided_by = ? "
                                                     "AND created_at BETWEEN ? AND ?", (w["name"], a, b)).fetchone()[0]
                planned = sum(1 for x in cells if x["plan"])
                out.append({"worker": w, "worked_min": sum(x["worked"] for x in cells), "planned_min": row["planned_min"],
                            "shifts": planned, "late": sum(1 for x in cells if x["state"] == "Late"),
                            "absent": sum(1 for x in cells if x["state"] == "Absent"),
                            "attendance_pct": (round(100 * sum(1 for x in cells if x["state"] in ("On time", "Late"))
                                                     / max(1, sum(1 for x in cells if x["state"] in
                                                                  ("On time", "Late", "Absent")))) if planned else None),
                            "tasks_done": len(done), "tasks_on_time": len(on_time),
                            "tasks_overdue": sum(1 for t in tasks if t["status"] in ("To do", "In progress")),
                            "work": {k: v for k, v in work.items() if v}})
        return {"first": first.isoformat(), "last": last.isoformat(), "days": days, "rows": out}

    def worker_abilities_text(self, staff_row):
        s = split(staff_row.get("abilities"))
        return "everything" if ALL in s else ", ".join(sorted(s))
