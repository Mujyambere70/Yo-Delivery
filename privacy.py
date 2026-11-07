"""
privacy.py - version 7 (mixin class used by Store): the privacy register, keeping times and the log of data requests.

    register      what personal data the system keeps, why, on which legal basis, for how long and who may see it
                  (a "record of processing activities", GDPR art. 30; the same questions are asked by India's DPDP Act
                  2023 and Russia's law 152-FZ). It is part of the code, so it changes together with the tables.
    keeping times a daily job deletes or empties what is older than its keeping time (numbers of requests after 30 days,
                  site messages after a year, chat messages of closed chats after 2 years, staff logs after 3 years ...).
                  Invoices and the money ledger are kept 8 years (tax law); ID photos 30 days (version 6).
    data requests every "download my data" and "delete my account" is written to a log (who, what, when), so the shop
                  can show that it answered.
    residency     Russia's law 152-FZ asks that the personal data of Russian citizens are first recorded in a database in
                  Russia. The register shows this; a real launch in Russia needs a database server there (for example
                  a second DATABASE_URL for Russian accounts) - not part of the demo.
"""

from datetime import datetime, timedelta

from core import now

PRIVACY_SCHEMA = """
CREATE TABLE IF NOT EXISTS privacy_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, kind TEXT NOT NULL, at TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT ''
);
"""

# (data, tables, purpose, legal basis, keeping time, who may see it)
REGISTER = [
    ("Account: name, e-mail, phone, password hash, language", "users", "Sign-in, orders, messages",
     "Contract", "Until the account is deleted", "The person; staff with customers.view"),
    ("Addresses", "addresses, orders, shipments", "Delivery of orders and parcels", "Contract",
     "Orders and parcels 8 years (tax law); saved addresses until deleted", "The person; couriers during the job"),
    ("Orders, invoices, payments, Yo Pay ledger", "orders, invoices, payments, wallet_txns",
     "Selling, accounting, refunds", "Contract; legal duty (tax)", "8 years", "Finance staff (yopay.view, invoices.view)"),
    ("Courier GPS positions", "courier_track", "Live tracking of a parcel", "Contract; legitimate interest",
     "30 days", "The receiver during the delivery; shipments.view"),
    ("Chat messages", "chat_threads, chat_messages", "Questions to sellers; safety", "Contract; legitimate interest",
     "2 years after the last message", "The two sides; chat.moderate"),
    ("ID check photos (document, selfie)", "id_checks, media", "Proof of identity of couriers and workers",
     "Consent (can be taken back)", "30 days after the decision; the result is kept", "photos.review"),
    ("Workers: contracts, pay, payslips, shifts, positions at clock-in", "staff_contracts, payslips, staff_shifts",
     "Employment, pay, attendance", "Contract; legal duty (tax, labour law)", "Payslips 8 years; shifts 3 years",
     "The worker; payroll.manage, shifts.manage"),
    ("Training certificates", "staff_trainings", "Safety and the rules of each ability", "Legal duty; contract",
     "3 years after the worker leaves", "staff.manage"),
    ("Support questions and parcel claims", "support_tickets, support_messages, parcel_claims", "Help and compensation",
     "Contract", "3 years", "The person; support.manage"),
    ("Staff activity log", "staff_log, activity", "Security and accountability", "Legitimate interest",
     "3 years", "activity.view, staff.view"),
    ("Request numbers, A/B test events", "request_stats, exp_events", "Running and improving the site",
     "Legitimate interest", "30 days / 180 days", "monitor.view, model.manage"),
    ("Site messages", "notifications", "Telling the person about orders and parcels", "Contract", "1 year",
     "The person"),
]

# table, time column, days, what to do ("delete" or an UPDATE that empties personal fields)
RETENTION = [
    ("request_stats", "minute", 30, "delete"),
    ("exp_events", "at", 180, "delete"),
    ("notifications", "created_at", 365, "delete"),
    ("courier_track", "at", 30, "delete"),
    ("staff_log", "at", 3 * 365, "delete"),
    ("partner_events", "received_at", 2 * 365, "delete"),
    ("payment_events", "received_at", 2 * 365, "delete"),
]

RESIDENCY = [("RU", "Federal Law 152-FZ, art. 18 part 5: personal data of Russian citizens must first be recorded, "
                    "kept and changed in databases located in Russia."),
             ("IN", "DPDP Act 2023: transfers abroad are allowed except to countries the government restricts."),
             ("EU", "GDPR chapter V: transfers need an adequacy decision or safeguards (standard clauses).")]


class PrivacyMixin:

    def privacy_register(self):
        return [{"data": d, "tables": t, "purpose": p, "basis": b, "keep": k, "who": w} for d, t, p, b, k, w in REGISTER]

    def log_privacy_request(self, user_id, kind, note=""):
        with self.db() as conn:
            conn.execute("INSERT INTO privacy_requests (user_id, kind, at, note) VALUES (?, ?, ?, ?)",
                         (user_id, kind, now(), str(note)[:200]))

    def privacy_requests(self, limit=100):
        with self.db() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM privacy_requests ORDER BY id DESC LIMIT ?", (limit,))]

    def apply_retention(self, at=None):
        """Delete what is older than its keeping time. Returns {table: rows}."""
        t = at or datetime.now()
        out = {}
        with self.db() as conn:
            have = set(conn.tables()) if hasattr(conn, "tables") else None
            for table, col, days, what in RETENTION:
                if have is not None and table not in have:
                    continue
                if col not in conn.columns(table):
                    continue
                cutoff = (t - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
                out[table] = conn.execute(f"DELETE FROM {table} WHERE {col} < ?", (cutoff,)).rowcount   # noqa: S608
        return out

    def export_my_data(self, user_id, *args, **kwargs):
        data = super().export_my_data(user_id, *args, **kwargs)
        self.log_privacy_request(user_id, "export")
        return data

    def delete_my_account(self, user_id, *args, **kwargs):
        result = super().delete_my_account(user_id, *args, **kwargs)
        self.log_privacy_request(user_id, "delete")
        return result
