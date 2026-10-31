"""
maintenance.py - jobs that keep the shop running by themselves, and backups (mixin class used by Store).

Every minute (on the first visit of that minute):
    * card orders that were not paid in time are cancelled (their stock comes back)
    * customers get a message for every order whose status moved on
Once a day:
    * today's exchange rates are downloaded
    * a full backup is saved in the database (the last 7 are kept) and emailed to BACKUP_EMAIL if set

Backups are gzip-compressed JSON of every table. The admin can download one at any time and restore
the whole shop from such a file (a copy of the current data is saved first).
"""

import base64
import gzip
import json
import time
from datetime import date

from core import ShopError, now

DATA_TABLES = ["users", "addresses", "categories", "sellers", "products", "media", "cart", "wishlist", "coupons",
               "orders", "order_items", "order_events", "payments", "returns", "reviews", "payouts", "views",
               "notifications", "push_subscriptions", "activity", "settings", "fx_rates"]


class MaintenanceMixin:
    _last_minute = 0

    def housekeeping(self, today=None):
        """Called before requests; does real work at most once a minute per server process."""
        if time.time() - self._last_minute < 60:
            return None
        self._last_minute = time.time()
        done = {}
        for name, job in (("expired", self.expire_unpaid), ("messages", self.sync_order_updates)):
            try:
                done[name] = job()
            except Exception as e:                        # one failing job must not stop the shop
                done[name + "_error"] = str(e)[:120]
        done.update(self.daily_tasks(today) or {})
        return done

    def daily_tasks(self, today=None):
        today = (today or date.today()).isoformat()
        with self.db() as conn:                           # only one process does it per day
            if conn.execute("SELECT 1 FROM settings WHERE key = 'last_daily_tasks' AND value = ?",
                            (today,)).fetchone():
                return None
            conn.execute("INSERT INTO settings (key, value) VALUES ('last_daily_tasks', ?) "
                         "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (today,))
        done = {}
        try:
            done["fx"] = self.refresh_fx_rates()
        except Exception as e:
            done["fx_error"] = str(e)[:120]
        bid, blob = self.save_backup("auto", "system")
        done["backup"] = bid
        to = self.notifier.env.get("BACKUP_EMAIL") if self.notifier else ""
        if to and self.notifier.email_provider():
            try:
                self.notifier.send_email(to, f"Yo Delivery automatic backup {today}",
                                         "The automatic daily backup of the Yo Delivery database is attached.",
                                         attachment=(f"yo_delivery_backup_{today}.json.gz", blob))
                done["emailed"] = to
            except Exception as e:
                done["email_error"] = str(e)[:120]
        return done

    # ===== backups =====

    def export_data(self):
        data = {"app": "Yo Delivery", "version": 2, "created_at": now(), "tables": {}}
        with self.db() as conn:
            for t in DATA_TABLES:
                rows = conn.execute(f"SELECT * FROM {t}").fetchall()
                data["tables"][t] = [dict(zip(r.keys(), list(r))) for r in rows]
        return data

    @staticmethod
    def pack(data):
        return gzip.compress(json.dumps(data, ensure_ascii=False).encode())

    @staticmethod
    def unpack(blob):
        try:
            data = json.loads(gzip.decompress(blob) if blob[:2] == b"\x1f\x8b" else blob)
        except (OSError, ValueError):
            raise ShopError("This file is not a Yo Delivery backup.")
        if not isinstance(data, dict) or data.get("app") != "Yo Delivery" or "tables" not in data:
            raise ShopError("This file is not a Yo Delivery backup.")
        return data

    def import_data(self, data):
        """Replace ALL data with the backup, in one transaction (if anything fails, nothing changes)."""
        tables = data["tables"]
        with self.db() as conn:
            for t in reversed(DATA_TABLES):
                conn.execute(f"DELETE FROM {t}")
            for t in DATA_TABLES:
                have = set(conn.columns(t))
                for row in tables.get(t, []):
                    cols = [c for c in row if c in have]
                    conn.execute(f"INSERT INTO {t} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                                 [row[c] for c in cols])
                if "id" in have:
                    conn.reset_sequence(t)
        self.refresh_categories()
        return {t: len(tables.get(t, [])) for t in DATA_TABLES}

    def save_backup(self, kind, by=""):
        blob = self.pack(self.export_data())
        with self.db() as conn:
            cur = conn.execute("INSERT INTO backups (created_at, kind, created_by, size, data) VALUES (?, ?, ?, ?, ?)",
                               (now(), kind, by, len(blob), base64.b64encode(blob).decode()))
            if kind == "auto":                              # keep the last 7 automatic copies
                ids = [r[0] for r in conn.execute("SELECT id FROM backups WHERE kind = 'auto' ORDER BY id DESC")]
                for old in ids[7:]:
                    conn.execute("DELETE FROM backups WHERE id = ?", (old,))
            return cur.lastrowid, blob

    def backups(self):
        with self.db() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT id, created_at, kind, created_by, size FROM backups ORDER BY id DESC")]

    def backup_blob(self, backup_id):
        with self.db() as conn:
            r = conn.execute("SELECT data FROM backups WHERE id = ?", (backup_id,)).fetchone()
        if r is None:
            raise ShopError("Backup not found.")
        return base64.b64decode(r[0])

    # ===== pictures kept in the database (they survive restarts of the server) =====

    def save_media(self, name, mime, blob):
        with self.db() as conn:
            conn.execute("INSERT INTO media (name, mime, data, created_at) VALUES (?, ?, ?, ?) "
                         "ON CONFLICT (name) DO UPDATE SET mime = excluded.mime, data = excluded.data, "
                         "created_at = excluded.created_at", (name, mime, base64.b64encode(blob).decode(), now()))

    def media(self, name):
        with self.db() as conn:
            r = conn.execute("SELECT mime, data, created_at FROM media WHERE name = ?", (name,)).fetchone()
        return (r["mime"], base64.b64decode(r["data"]), r["created_at"]) if r else None

    def delete_media(self, prefix):
        with self.db() as conn:
            conn.execute("DELETE FROM media WHERE name LIKE ?", (prefix + "%",))
