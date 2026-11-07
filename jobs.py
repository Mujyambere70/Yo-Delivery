"""
jobs.py - version 6 (mixin class used by Store): a job queue in the database, request numbers, health check and
alerts, so several server processes (Gunicorn workers or Fly machines) can share the background work.

Job queue
    enqueue(kind, payload) writes a row to the table jobs. Any process takes a waiting job with a conditional
    UPDATE (only one process can win it), runs the handler of its kind and marks it done; a failing job is tried
    again later (1, 4, 9, 16 ... minutes) and after max_attempts it stays "failed" for a person to look at.
    unique_key makes sure the same job (for example "the payouts of 2026-11-07") is waiting only once.
Monitoring
    Every request adds to the numbers of its minute (requests, server errors, total and slowest time) in the table
    request_stats. /admin/monitor shows the last hour, the queue and the open alerts; /healthz answers a load
    balancer or an uptime service (database time, places file, waiting jobs).
Alerts
    check_alerts() opens an alert (once per kind) when there are many server errors, failed jobs or old waiting
    jobs, and tells the administrators; it closes the alert when things are normal again.
"""

import json
import os
import socket
import threading
import time
import traceback
from datetime import datetime, timedelta

from core import ShopError, now

JOBS_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'waiting', run_at TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 5, locked_by TEXT NOT NULL DEFAULT '', locked_at TEXT NOT NULL DEFAULT '',
    last_error TEXT NOT NULL DEFAULT '', unique_key TEXT, created_at TEXT NOT NULL, done_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_jobs_waiting ON jobs(status, run_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_unique ON jobs(unique_key) WHERE status IN ('waiting', 'running');
CREATE TABLE IF NOT EXISTS request_stats (
    minute TEXT PRIMARY KEY, requests INTEGER NOT NULL DEFAULT 0, errors INTEGER NOT NULL DEFAULT 0,
    total_ms INTEGER NOT NULL DEFAULT 0, max_ms INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, message TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Open',
    opened_at TEXT NOT NULL, closed_at TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_alerts_open ON alerts(kind) WHERE status = 'Open';
"""

WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"
LOCK_MINUTES = 10                       # a running job whose process died is taken again after this
ALERT_ERRORS = 10                       # server errors in 10 minutes
ALERT_OLD_JOB_MIN = 30                  # a waiting job older than this


class JobsMixin:
    _job_handlers = None
    _stats_lock = threading.Lock()

    def job_handlers(self):
        """kind -> function(payload). Version 6 moves the daily work and the slow work here."""
        if self._job_handlers is None:
            self._job_handlers = {
                "payouts": lambda p: self.auto_payouts(p.get("day")),
                "learn": lambda p: self.learn_recommendations(force=bool(p.get("force"))),
                "content": lambda p: self.build_content_similar(),
                "warehouse": lambda p: self.build_warehouse(),                 # version 7
                "warm_routes": lambda p: self._warm_job(p),
                "fx": lambda p: self.refresh_fx_rates(),
                "alerts": lambda p: self.check_alerts(),
                "test": lambda p: self._test_job(p),
                "bank_settle": lambda p: self.settle_bank_transfers(),
            }
        return self._job_handlers

    # ---------------------------------------------------------------- queue
    def enqueue(self, kind, payload=None, unique_key=None, delay_s=0, max_attempts=5, conn=None):
        """Add a job; returns its id (None when the same unique job is already waiting)."""
        if kind not in self.job_handlers():
            raise ShopError(f"Unknown job: {kind}")
        run_at = (datetime.now() + timedelta(seconds=delay_s)).strftime("%Y-%m-%d %H:%M:%S")
        sql = ("INSERT INTO jobs (kind, payload, status, run_at, max_attempts, unique_key, created_at) "
               "VALUES (?, ?, 'waiting', ?, ?, ?, ?)"
               + (" ON CONFLICT (unique_key) WHERE status IN ('waiting', 'running') DO NOTHING" if unique_key else ""))
        args = (kind, json.dumps(payload or {}), run_at, max_attempts, unique_key, now())

        def go(c):
            cur = c.execute(sql, args)
            return cur.lastrowid if cur.rowcount else None
        if conn is not None:
            return go(conn)
        with self.db() as c:
            return go(c)

    def _claim_jobs(self, limit):
        """Take up to `limit` jobs that are due; a job is won by exactly one process."""
        stale = (datetime.now() - timedelta(minutes=LOCK_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
        won = []
        with self.db() as conn:
            conn.execute("UPDATE jobs SET status = 'waiting', locked_by = '' WHERE status = 'running' AND locked_at < ?",
                         (stale,))
            ids = [r[0] for r in conn.execute("SELECT id FROM jobs WHERE status = 'waiting' AND run_at <= ? "
                                              "ORDER BY run_at, id LIMIT ?", (now(), limit * 3))]
        for jid in ids:
            with self.db() as conn:
                if conn.execute("UPDATE jobs SET status = 'running', locked_by = ?, locked_at = ?, "
                                "attempts = attempts + 1 WHERE id = ? AND status = 'waiting'",
                                (WORKER_ID, now(), jid)).rowcount:
                    won.append(dict(conn.execute("SELECT * FROM jobs WHERE id = ?", (jid,)).fetchone()))
            if len(won) >= limit:
                break
        return won

    def run_jobs(self, limit=5):
        """Run due jobs (called every minute by housekeeping, or by a separate worker process)."""
        done = {"ok": 0, "failed": 0, "retry": 0}
        for job in self._claim_jobs(limit):
            handler = self.job_handlers().get(job["kind"])
            try:
                if handler is None:
                    raise ShopError(f"No handler for {job['kind']}")
                handler(json.loads(job["payload"] or "{}"))
                with self.db() as conn:
                    conn.execute("UPDATE jobs SET status = 'done', done_at = ?, last_error = '' WHERE id = ? AND locked_by = ?",
                                 (now(), job["id"], WORKER_ID))
                done["ok"] += 1
            except Exception as e:                       # the job is tried again later
                err = (f"{type(e).__name__}: {e}\n" + traceback.format_exc(limit=3))[-900:]
                final = job["attempts"] >= job["max_attempts"]
                later = (datetime.now() + timedelta(minutes=job["attempts"] ** 2)).strftime("%Y-%m-%d %H:%M:%S")
                with self.db() as conn:
                    conn.execute("UPDATE jobs SET status = ?, run_at = ?, last_error = ?, locked_by = '' "
                                 "WHERE id = ? AND locked_by = ?",
                                 ("failed" if final else "waiting", later, err, job["id"], WORKER_ID))
                done["failed" if final else "retry"] += 1
        return done

    def retry_job(self, job_id):
        with self.db() as conn:
            if not conn.execute("UPDATE jobs SET status = 'waiting', attempts = 0, run_at = ?, last_error = '' "
                                "WHERE id = ? AND status = 'failed'", (now(), job_id)).rowcount:
                raise ShopError("Only failed jobs can be tried again.")

    def job_summary(self):
        with self.db() as conn:
            by = [dict(r) for r in conn.execute("SELECT kind, status, COUNT(*) AS n FROM jobs GROUP BY kind, status "
                                                "ORDER BY kind, status")]
            recent = [dict(r) for r in conn.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT 25")]
            failed = [dict(r) for r in conn.execute("SELECT * FROM jobs WHERE status = 'failed' ORDER BY id DESC LIMIT 25")]
            oldest = conn.execute("SELECT MIN(run_at) FROM jobs WHERE status = 'waiting'").fetchone()[0]
        totals = {}
        for r in by:
            totals[r["status"]] = totals.get(r["status"], 0) + r["n"]
        return {"by": by, "recent": recent, "failed": failed, "totals": totals, "oldest_waiting": oldest or ""}

    def _test_job(self, p):
        if p.get("fail"):
            raise RuntimeError("This test job fails on purpose")
        return True

    def _warm_job(self, p):
        for a, b in p.get("pairs", []):
            self.road_route(tuple(a), tuple(b))

    # ---------------------------------------------------------------- request numbers
    _pending = None

    def record_request(self, ms, status):
        """Count one request in this minute (kept in memory, written at most every 20 seconds)."""
        minute = now()[:16]
        with self._stats_lock:
            if self._pending is None:
                self._pending = {"since": time.time(), "rows": {}}
            r = self._pending["rows"].setdefault(minute, [0, 0, 0, 0])
            r[0] += 1
            r[1] += int(status >= 500)
            r[2] += int(ms)
            r[3] = max(r[3], int(ms))
            if time.time() - self._pending["since"] < 20:
                return
            rows, self._pending = self._pending["rows"], None
        self.flush_stats(rows)

    def flush_stats(self, rows=None):
        if rows is None:
            with self._stats_lock:
                rows, self._pending = (self._pending or {}).get("rows", {}), None
        if not rows:
            return
        try:
            with self.db() as conn:
                for minute, (n, err, total, mx) in rows.items():
                    conn.execute("INSERT INTO request_stats (minute, requests, errors, total_ms, max_ms) VALUES (?, ?, ?, ?, ?) "
                                 "ON CONFLICT (minute) DO UPDATE SET requests = request_stats.requests + excluded.requests, "
                                 "errors = request_stats.errors + excluded.errors, "
                                 "total_ms = request_stats.total_ms + excluded.total_ms, "
                                 "max_ms = CASE WHEN excluded.max_ms > request_stats.max_ms THEN excluded.max_ms "
                                 "ELSE request_stats.max_ms END", (minute, n, err, total, mx))
        except Exception:
            pass                                          # numbers must never break a page

    def request_numbers(self, minutes=60):
        start = (datetime.now() - timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M")
        with self.db() as conn:
            rows = [dict(r) for r in conn.execute("SELECT * FROM request_stats WHERE minute >= ? ORDER BY minute", (start,))]
            old = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M")
            conn.execute("DELETE FROM request_stats WHERE minute < ?", (old,))
        n = sum(r["requests"] for r in rows)
        return {"rows": rows, "requests": n, "errors": sum(r["errors"] for r in rows),
                "avg_ms": round(sum(r["total_ms"] for r in rows) / n) if n else 0,
                "max_ms": max([r["max_ms"] for r in rows] or [0]),
                "peak": max([r["requests"] for r in rows] or [0])}

    # ---------------------------------------------------------------- health and alerts
    def health(self):
        out = {"ok": True, "version": "6", "worker": WORKER_ID}
        t = time.time()
        try:
            with self.db() as conn:
                conn.execute("SELECT 1").fetchone()
                out["jobs_waiting"] = conn.execute("SELECT COUNT(*) FROM jobs WHERE status = 'waiting'").fetchone()[0]
                out["jobs_failed"] = conn.execute("SELECT COUNT(*) FROM jobs WHERE status = 'failed'").fetchone()[0]
            out["db_ms"] = round((time.time() - t) * 1000, 1)
        except Exception as e:
            out.update(ok=False, db_error=type(e).__name__)
        try:
            import geo
            out["places"] = geo.get("Kigali") is not None
        except Exception:
            out["places"] = False
        out["ok"] = out["ok"] and out["places"]
        return out

    def check_alerts(self):
        """Open or close the alerts; the administrators get a message when an alert opens."""
        ten = (datetime.now() - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M")
        old = (datetime.now() - timedelta(minutes=ALERT_OLD_JOB_MIN)).strftime("%Y-%m-%d %H:%M:%S")
        with self.db() as conn:
            errors = conn.execute("SELECT COALESCE(SUM(errors), 0) FROM request_stats WHERE minute >= ?", (ten,)).fetchone()[0]
            failed = conn.execute("SELECT COUNT(*) FROM jobs WHERE status = 'failed'").fetchone()[0]
            stuck = conn.execute("SELECT COUNT(*) FROM jobs WHERE status = 'waiting' AND run_at < ?", (old,)).fetchone()[0]
        state = {"errors": (errors >= ALERT_ERRORS, f"{errors} server errors in the last 10 minutes"),
                 "jobs_failed": (failed > 0, f"{failed} background jobs failed"),
                 "jobs_stuck": (stuck > 0, f"{stuck} jobs are waiting for more than {ALERT_OLD_JOB_MIN} minutes")}
        opened = []
        with self.db() as conn:
            for kind, (bad, text) in state.items():
                if bad:
                    if conn.execute("INSERT INTO alerts (kind, message, status, opened_at) VALUES (?, ?, 'Open', ?) "
                                    "ON CONFLICT (kind) WHERE status = 'Open' DO NOTHING", (kind, text, now())).rowcount:
                        opened.append(text)
                        for (uid,) in conn.execute("SELECT id FROM users WHERE is_admin = 1").fetchall():
                            self.notify(conn, uid, "Alert: {0}".replace("{0}", text),
                                        "Open the monitoring page for details.", url="/admin/monitor")
                else:
                    conn.execute("UPDATE alerts SET status = 'Closed', closed_at = ? WHERE kind = ? AND status = 'Open'",
                                 (now(), kind))
        return opened

    def alerts(self, limit=30):
        with self.db() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM alerts ORDER BY status = 'Open' DESC, id DESC LIMIT ?",
                                                  (limit,))]

    def open_alerts(self):
        with self.db() as conn:
            return conn.execute("SELECT COUNT(*) FROM alerts WHERE status = 'Open'").fetchone()[0]
