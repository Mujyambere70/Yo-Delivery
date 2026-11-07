"""
providers.py - version 7 (mixin class used by Store): the money side of payment providers, ready for live use.

    callbacks        a provider confirms a payment server-to-server: POST /api/payments/<provider>/callback with the JSON
                     {"event_id", "type": "payment.succeeded" | "payment.failed", "ref", "amount", "currency"} and the header
                     X-Yo-Signature: t=<unix time>,v1=<hex HMAC-SHA256(secret, "<t>.<body>")>. The secret of each provider
                     comes from an environment variable (MPESA_WEBHOOK_SECRET, MOMO_WEBHOOK_SECRET, SBP_WEBHOOK_SECRET,
                     UPI_WEBHOOK_SECRET, STRIPE_WEBHOOK_SECRET); without it the endpoint answers 503. Old signatures
                     (more than 5 minutes) are refused, and every event is used once (UNIQUE provider + event_id), so a
                     provider may send it again safely. Real providers sign in their own ways (an adapter per provider
                     turns their call into this form); the demo stays in test mode.
    reconciliation   the finance team uploads the settlement statement of a provider (CSV: date, reference, amount,
                     currency, status). Every line is compared with the payments in our books: matched, different amount,
                     missing in our books, missing in the statement. The result is kept for the auditors.
"""

import csv
import hashlib
import hmac
import io
import json
import time

from core import ShopError, now

PROVIDERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS payment_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, provider TEXT NOT NULL, event_id TEXT NOT NULL, kind TEXT NOT NULL,
    ref TEXT NOT NULL, amount INTEGER NOT NULL DEFAULT 0, currency TEXT NOT NULL DEFAULT '', result TEXT NOT NULL,
    received_at TEXT NOT NULL, UNIQUE (provider, event_id)
);
CREATE TABLE IF NOT EXISTS reconciliations (
    id INTEGER PRIMARY KEY AUTOINCREMENT, provider TEXT NOT NULL, first_day TEXT NOT NULL, last_day TEXT NOT NULL,
    lines INTEGER NOT NULL, matched INTEGER NOT NULL, different INTEGER NOT NULL, missing_ours INTEGER NOT NULL,
    missing_theirs INTEGER NOT NULL, details TEXT NOT NULL, made_by INTEGER NOT NULL, made_at TEXT NOT NULL
);
"""

PROVIDERS = {"mpesa": ("M-Pesa", "MPESA_WEBHOOK_SECRET", "mobile:mpesa"),
             "momo": ("MTN MoMo", "MOMO_WEBHOOK_SECRET", "mobile:momo"),
             "sbp": ("SBP", "SBP_WEBHOOK_SECRET", "mobile:sbp"),
             "upi": ("UPI", "UPI_WEBHOOK_SECRET", "mobile:upi"),
             "stripe": ("Stripe (cards)", "STRIPE_WEBHOOK_SECRET", "stripe")}
MAX_AGE_S = 300


def sign(secret, body, t=None):
    t = int(t if t is not None else time.time())
    mac = hmac.new(secret.encode(), f"{t}.".encode() + (body if isinstance(body, bytes) else body.encode()),
                   hashlib.sha256).hexdigest()
    return f"t={t},v1={mac}"


def signature_ok(secret, header, body, at=None):
    try:
        parts = dict(p.split("=", 1) for p in str(header or "").split(","))
        t = int(parts["t"])
    except (ValueError, KeyError):
        return False
    if abs((at if at is not None else time.time()) - t) > MAX_AGE_S:
        return False
    expected = sign(secret, body, t).split("v1=")[1]
    return hmac.compare_digest(expected, parts.get("v1", ""))


def _money(text):
    return int(round(float(str(text).replace(",", "").strip()) * 100))


class ProvidersMixin:

    def payment_callback(self, provider, header, body, at=None):
        """Returns (http status, answer)."""
        if provider not in PROVIDERS:
            return 404, "unknown provider"
        secret = self.env.get(PROVIDERS[provider][1], "")
        if not secret:
            return 503, "callbacks of this provider are not set up"
        if not signature_ok(secret, header, body, at):
            return 401, "bad signature"
        try:
            ev = json.loads(body)
            event_id, kind, ref = str(ev["event_id"])[:80], str(ev["type"]), str(ev["ref"])[:80]
            amount = _money(ev.get("amount", 0))
        except (ValueError, KeyError, TypeError):
            return 400, "bad event"
        if kind not in ("payment.succeeded", "payment.failed"):
            return 200, "ignored"
        with self.db() as conn:
            if conn.execute("SELECT 1 FROM payment_events WHERE provider = ? AND event_id = ?",
                            (provider, event_id)).fetchone():
                return 200, "already done"
            conn.execute("INSERT INTO payment_events (provider, event_id, kind, ref, amount, currency, result, received_at) "
                         "VALUES (?, ?, ?, ?, ?, ?, 'received', ?) ON CONFLICT (provider, event_id) DO NOTHING",
                         (provider, event_id, kind, ref, amount, str(ev.get("currency", ""))[:3], now()))
        result = self._apply_event(provider, kind, ref, amount)
        with self.db() as conn:
            conn.execute("UPDATE payment_events SET result = ? WHERE provider = ? AND event_id = ?",
                         (result[:120], provider, event_id))
        return 200, result

    def _apply_event(self, provider, kind, ref, amount):
        p = self.mobile_payment(ref) if hasattr(self, "mobile_payment") else None
        if p is None:
            return "no payment with this reference"
        if amount and amount != p["amount"]:
            return "amount differs: kept for a person"
        try:
            self.answer_mobile_payment(p["user_id"], ref, kind == "payment.succeeded")
            return "paid" if kind == "payment.succeeded" else "declined"
        except ShopError as e:
            return str(e)

    def payment_events(self, limit=100):
        with self.db() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM payment_events ORDER BY id DESC LIMIT ?", (limit,))]

    # ---------------------------------------------------------------- reconciliation
    def reconcile(self, actor_id, provider, csv_text):
        if not self.can(actor_id, "payments.manage"):
            raise ShopError("You do not have the ability to do this. Ask your manager.")
        if provider not in PROVIDERS:
            raise ShopError("Please choose the provider.")
        rows = []
        try:
            reader = csv.DictReader(io.StringIO(csv_text.strip()))
            for r in reader:
                r = {k.strip().lower(): (v or "").strip() for k, v in r.items() if k}
                if not r.get("reference"):
                    continue
                rows.append({"day": r.get("date", "")[:10], "ref": r["reference"], "amount": _money(r.get("amount", "0")),
                             "currency": r.get("currency", "").upper(), "status": r.get("status", "").lower()})
        except (ValueError, csv.Error):
            raise ShopError("The statement must be a CSV file with the columns date, reference, amount, currency, status.")
        if not rows:
            raise ShopError("The statement must be a CSV file with the columns date, reference, amount, currency, status.")
        first, last = min(r["day"] for r in rows), max(r["day"] for r in rows)
        gateway = PROVIDERS[provider][2]
        with self.db() as conn:
            ours = {r["payment_ref"]: dict(r) for r in conn.execute(
                "SELECT * FROM payments WHERE gateway = ? AND status = 'Succeeded' AND created_at >= ? AND created_at < ?",
                (gateway, first, last + "T99"))}
        out = {"matched": [], "different": [], "missing_ours": [], "missing_theirs": []}
        seen = set()
        for r in rows:
            if r["status"] not in ("", "settled", "succeeded", "paid"):
                continue
            o = ours.get(r["ref"])
            if o is None:
                out["missing_ours"].append(r)
            elif o["amount"] != r["amount"] or (r["currency"] and r["currency"] != o["currency"]):
                out["different"].append({**r, "ours": o["amount"], "our_currency": o["currency"]})
                seen.add(r["ref"])
            else:
                out["matched"].append(r)
                seen.add(r["ref"])
        out["missing_theirs"] = [{"ref": k, "amount": v["amount"], "currency": v["currency"], "day": v["created_at"][:10]}
                                 for k, v in ours.items() if k not in seen]
        with self.db() as conn:
            cur = conn.execute("INSERT INTO reconciliations (provider, first_day, last_day, lines, matched, different, "
                               "missing_ours, missing_theirs, details, made_by, made_at) VALUES "
                               "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                               (provider, first, last, len(rows), len(out["matched"]), len(out["different"]),
                                len(out["missing_ours"]), len(out["missing_theirs"]), json.dumps(out), actor_id, now()))
        return {"id": cur.lastrowid, "provider": provider, "first": first, "last": last, "lines": len(rows), **out}

    def reconciliations(self, limit=50):
        with self.db() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT r.*, u.name AS made_by_name FROM reconciliations r JOIN users u ON u.id = r.made_by "
                "ORDER BY r.id DESC LIMIT ?", (limit,))]
        for r in rows:
            r["detail"] = json.loads(r["details"])
        return rows
