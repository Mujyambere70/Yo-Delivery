"""
maintenance.py - jobs that keep the shop running by themselves, and backups (mixin class used by Store).

Every minute (on the first visit of that minute):
    * card orders that were not paid in time are cancelled (their stock comes back)
    * customers get a message for every order whose status moved on
    * Yo Courier: the network moves parcels between hubs, waiting jobs go to couriers, demo couriers drive
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
               "notifications", "push_subscriptions", "activity", "settings", "fx_rates",
               # version 3 (parents before children: a restore inserts in this order and deletes in reverse)
               "wallet_txns", "couriers", "shipments", "shipment_events", "courier_track", "posts", "quiz_questions",
               "quiz_attempts", "points_ledger", "user_coupons", "board_tasks", "photo_checks",
               # version 4 - Yo Tickets
               "ticket_operators", "ticket_services", "ticket_trips", "ticket_bookings", "ticket_passengers",
               "ticket_seats", "ticket_staff",
               # version 5 - chat, product texts in other languages
               "chat_threads", "chat_messages", "product_texts",
               # version 6 - staff, customs, mobile money
               "offices", "staff", "staff_log", "staff_shifts", "staff_leave", "seller_team", "customs_rules",
               "customs_declarations", "mobile_payments", "staff_rota", "staff_tasks", "tax_rules", "bank_accounts",
               "bank_transfers", "gtfs_feeds", "id_checks", "chat_examples",
               # version 7 - people, money, partners, support, analytics
               "staff_contracts", "income_tax_rules", "payroll_runs", "payslips", "approvals", "staff_trainings",
               "staff_offices", "tax_category_rates", "invoice_counters", "invoices", "payment_events",
               "reconciliations", "partners", "partner_events", "support_tickets", "support_messages",
               "parcel_claims", "privacy_requests", "experiments", "exp_events", "fact_daily"]


class MaintenanceMixin:
    _last_minute = 0

    def housekeeping(self, today=None):
        """Called before requests; does real work at most once a minute per server process."""
        if time.time() - self._last_minute < 60:
            return None
        self._last_minute = time.time()
        done = {}
        for name, job in (("expired", self.expire_unpaid), ("messages", self.sync_order_updates),
                          ("courier", self.courier_tick), ("tickets", self.tickets_tick), ("fx", self.hourly_fx),
                          ("jobs", self.run_jobs), ("alerts", self.check_alerts), ("bank", self.settle_bank_transfers),
                          ("cache", self.cache_clean)):
            try:
                done[name] = job()
            except Exception as e:                        # one failing job must not stop the shop
                done[name + "_error"] = str(e)[:120]
        done.update(self.daily_tasks(today) or {})
        return done

    def hourly_fx(self, hour=None):
        """Exchange rates every hour (version 5; before: once a day). Only one server process does it."""
        if str(self.env.get("FX_AUTO", "1")) == "0":
            return None
        hour = hour or now()[:13]
        if not self.claim_period("last_fx_hour", hour):
            return None
        return self.refresh_fx_rates()

    def claim_period(self, key, value):
        """Version 7: True for exactly one process (of all the machines) per key and value - an atomic claim in the
        database instead of "read, then write" (two machines could both read the old value)."""
        with self.db() as conn:
            if conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO NOTHING",
                            (key, value)).rowcount:
                return True
            return bool(conn.execute("UPDATE settings SET value = ? WHERE key = ? AND value != ?",
                                     (value, key, value)).rowcount)

    def daily_tasks(self, today=None):
        today = (today or date.today()).isoformat()
        if not self.claim_period("last_daily_tasks", today):      # only one process of one machine does it per day
            return None
        done = {}
        try:
            done["fx"] = self.refresh_fx_rates()
        except Exception as e:
            done["fx_error"] = str(e)[:120]
        # version 6: seller payouts and the recommendation model go through the job queue (jobs.py): if one
        # fails it is tried again later, and the monitoring page shows it
        self.enqueue("payouts", {"day": today}, unique_key=f"payouts:{today}")
        self.enqueue("learn", {}, unique_key=f"learn:{today}")
        self.enqueue("content", {}, unique_key=f"content:{today}")      # version 6: text + picture neighbours
        try:
            done["id_images_deleted"] = self.purge_id_images()             # version 6: ID photos are not kept
        except Exception:
            pass
        # version 7: the data warehouse of the managers, the keeping times of personal data
        self.enqueue("warehouse", {}, unique_key=f"warehouse:{today}")
        try:
            done["retention"] = self.apply_retention()
        except Exception as e:
            done["retention_error"] = str(e)[:120]
        done["jobs"] = self.run_jobs(limit=5)
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
        data = {"app": "Yo Delivery", "version": 7, "created_at": now(), "tables": {}}
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
        for fill in ("ensure_tax_rules", "ensure_chat_model"):     # version 6: a backup of an older version
            if hasattr(self, fill):
                getattr(self, fill)()
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
