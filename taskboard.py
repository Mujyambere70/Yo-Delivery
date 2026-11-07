"""
taskboard.py - a small Kanban task board (mixin class used by Store), from the TaskFlow project.

Every seller, every courier and the admin team has one board with three columns: To do, Doing, Done.
People add their own tasks and drag cards between the columns. Yo also adds tasks by itself:
    seller   "Pack order OD..." for every new order with their products, "Restock ..." when stock is low
    courier  "Give ₹... cash to the Yo hub" when the courier keeps a lot of cash
    admin    a new seller or courier application, a return request
A task made by Yo is never added twice for the same thing (the "ref" of the open task).
"""

from datetime import date, datetime

from core import ShopError, now

BOARD_COLUMNS = {"todo": "To do", "doing": "Doing", "done": "Done"}
PRIORITIES = {"low": "Low", "normal": "Normal", "high": "High"}
LOW_STOCK = 5
CASH_LIMIT = 2_000_00          # remind the courier to bring the cash when they keep more than ₹2,000

BOARD_SCHEMA = """
CREATE TABLE IF NOT EXISTS board_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT, board TEXT NOT NULL, title TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'todo', priority TEXT NOT NULL DEFAULT 'normal', due TEXT NOT NULL DEFAULT '',
    ref TEXT NOT NULL DEFAULT '', link TEXT NOT NULL DEFAULT '', auto INTEGER NOT NULL DEFAULT 0,
    position INTEGER NOT NULL DEFAULT 0, created_by TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL, done_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_board_tasks ON board_tasks(board);
"""


class TaskBoardMixin:

    def board(self, board):
        with self.db() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM board_tasks WHERE board = ? AND (status <> 'done' OR done_at >= ?) "
                "ORDER BY position, id", (board, _days_ago(14)))]
        today = date.today().isoformat()
        cols = {k: [] for k in BOARD_COLUMNS}
        for r in rows:
            r["late"] = bool(r["due"]) and r["due"] < today and r["status"] != "done"
            cols.setdefault(r["status"], []).append(r)
        return cols

    def board_counts(self, board):
        with self.db() as conn:
            return {r[0]: r[1] for r in conn.execute(
                "SELECT status, COUNT(*) FROM board_tasks WHERE board = ? AND status <> 'done' GROUP BY status", (board,))}

    def add_task(self, board, f, by="", conn=None, auto=False):
        title = " ".join(str(f.get("title", "")).split())[:120]
        if len(title) < 2:
            raise ShopError("Please write the task.")
        notes = str(f.get("notes", "")).strip()[:1000]
        priority = f.get("priority", "normal") if f.get("priority") in PRIORITIES else "normal"
        due = str(f.get("due", "")).strip()
        if due:
            try:
                due = date.fromisoformat(due).isoformat()
            except ValueError:
                raise ShopError("Please choose the date from the calendar.")
        status = f.get("status", "todo") if f.get("status") in BOARD_COLUMNS else "todo"
        ref, link = str(f.get("ref", ""))[:40], str(f.get("link", ""))[:200]
        if link and not link.startswith("/"):
            link = ""

        def write(c):
            if auto and ref and c.execute("SELECT 1 FROM board_tasks WHERE board = ? AND ref = ? AND status <> 'done'",
                                          (board, ref)).fetchone():
                return None
            pos = c.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM board_tasks WHERE board = ?", (board,)).fetchone()[0]
            when = now()
            cur = c.execute("INSERT INTO board_tasks (board, title, notes, status, priority, due, ref, link, auto, "
                            "position, created_by, created_at, updated_at, done_at) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (board, title, notes, status, priority, due, ref, link, int(auto), pos, by, when, when,
                             when if status == "done" else ""))
            return cur.lastrowid
        if conn is not None:
            return write(conn)
        with self.db() as c:
            return write(c)

    def _task(self, conn, board, task_id):
        t = conn.execute("SELECT * FROM board_tasks WHERE id = ? AND board = ?", (task_id, board)).fetchone()
        if t is None:
            raise ShopError("Task not found.")
        return t

    def move_task(self, board, task_id, status, before_id=None):
        """Drag and drop: put the card in a column, above the card `before_id` (or at the end)."""
        if status not in BOARD_COLUMNS:
            raise ShopError("Unknown column.")
        with self.db() as conn:
            self._task(conn, board, task_id)
            ids = [r[0] for r in conn.execute("SELECT id FROM board_tasks WHERE board = ? AND status = ? AND id <> ? "
                                              "ORDER BY position, id", (board, status, task_id))]
            try:
                at = ids.index(int(before_id)) if before_id not in (None, "", "null") else len(ids)
            except (ValueError, TypeError):
                at = len(ids)
            ids.insert(at, int(task_id))
            when = now()
            conn.execute("UPDATE board_tasks SET status = ?, updated_at = ?, done_at = CASE WHEN ? = 'done' THEN ? "
                         "ELSE '' END WHERE id = ?", (status, when, status, when, task_id))
            for pos, tid in enumerate(ids, start=1):
                conn.execute("UPDATE board_tasks SET position = ? WHERE id = ? AND board = ?", (pos, tid, board))

    def edit_task(self, board, task_id, f):
        with self.db() as conn:
            self._task(conn, board, task_id)
        title = " ".join(str(f.get("title", "")).split())[:120]
        if len(title) < 2:
            raise ShopError("Please write the task.")
        due = str(f.get("due", "")).strip()
        if due:
            try:
                due = date.fromisoformat(due).isoformat()
            except ValueError:
                raise ShopError("Please choose the date from the calendar.")
        priority = f.get("priority") if f.get("priority") in PRIORITIES else "normal"
        with self.db() as conn:
            conn.execute("UPDATE board_tasks SET title = ?, notes = ?, priority = ?, due = ?, updated_at = ? "
                         "WHERE id = ? AND board = ?",
                         (title, str(f.get("notes", "")).strip()[:1000], priority, due, now(), task_id, board))

    def delete_task(self, board, task_id):
        with self.db() as conn:
            self._task(conn, board, task_id)
            conn.execute("DELETE FROM board_tasks WHERE id = ? AND board = ?", (task_id, board))

    # ===== tasks made by Yo =====

    def auto_tasks_for_order(self, conn, order_number, order_id):
        """New order: a 'pack' task for every seller in it, and 'restock' when their stock is low."""
        rows = conn.execute("SELECT oi.seller_id, oi.name, oi.qty, p.stock, p.slug, p.name AS pname FROM order_items oi "
                            "JOIN products p ON p.id = oi.product_id WHERE oi.order_id = ? AND oi.seller_id IS NOT NULL",
                            (order_id,)).fetchall()
        by_seller = {}
        for r in rows:
            by_seller.setdefault(r["seller_id"], []).append(r)
        for seller_id, items in by_seller.items():
            board = f"seller:{seller_id}"
            what = ", ".join(f"{r['qty']} x {r['name'][:30]}" for r in items)
            self.add_task(board, {"title": f"Pack order {order_number}", "notes": what, "priority": "high",
                                  "due": date.today().isoformat(), "ref": order_number,
                                  "link": "/seller/orders"}, by="Yo", conn=conn, auto=True)
            for r in items:
                if r["stock"] <= LOW_STOCK:
                    self.add_task(board, {"title": f"Restock {r['pname'][:60]} (only {r['stock']} left)",
                                          "ref": "stock:" + r["slug"], "link": "/seller/products"},
                                  by="Yo", conn=conn, auto=True)

    def auto_task_admin(self, conn, title, ref, link, priority="normal"):
        self.add_task("admin", {"title": title, "ref": ref, "link": link, "priority": priority},
                      by="Yo", conn=conn, auto=True)

    def auto_task_cash(self, conn, courier_id):
        c = conn.execute("SELECT cash_in_hand FROM couriers WHERE id = ?", (courier_id,)).fetchone()
        if c and c[0] > CASH_LIMIT:
            import world
            self.add_task(f"courier:{courier_id}", {"title": f"Give {world.format_money(c[0], 'INR')} cash to the Yo hub",
                                                    "priority": "high", "ref": "cash", "link": "/driver"},
                          by="Yo", conn=conn, auto=True)


def _days_ago(days):
    from datetime import timedelta
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
