"""
partners.py - version 7 (mixin class used by Store): an API for real courier partner companies.

Until version 6 the partner companies were simulated. Now a partner gets an account with an API key:

    POST /api/partner/v1/events            tracking events of the partner's parcels (a list, at most 100 at a time)
    GET  /api/partner/v1/shipments         the parcels handed to the partner that are not finished yet
    GET  /api/partner/v1/shipments/<n>     one parcel (addresses and phone only while the partner has it)
    GET  /api/partner/openapi.json         the description of the API (OpenAPI 3.0); /partners/api shows it to people

Every call carries the headers X-Yo-Key (the key id) and X-Yo-Signature: t=<unix time>,v1=<HMAC-SHA256 of
"<t>.<body>" with the partner's secret>. The secret is shown once when the key is made; a signature older than
5 minutes is refused (no replay); every event has an event_id and is used once, so a partner may send it again.
Events move a parcel only forward (picked up -> in transit -> at hub -> out for delivery -> delivered / returned);
a parcel that belongs to a partner is no longer moved by the demo simulation.
"""

import hashlib
import json
import secrets
from datetime import datetime

import courier as C
from core import ShopError, now
from providers import signature_ok

PARTNERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS partners (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, countries TEXT NOT NULL DEFAULT '',
    key_id TEXT NOT NULL UNIQUE, secret TEXT NOT NULL, secret_hint TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1,
    created_by INTEGER, created_at TEXT NOT NULL, last_call_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS partner_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, partner_id INTEGER NOT NULL REFERENCES partners(id), event_id TEXT NOT NULL,
    number TEXT NOT NULL, kind TEXT NOT NULL, at TEXT NOT NULL, place TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '', result TEXT NOT NULL, received_at TEXT NOT NULL, UNIQUE (partner_id, event_id)
);
"""

EVENTS = {"picked_up": C.PICKED, "in_transit": C.TRANSIT, "at_hub": C.AT_HUB, "out_for_delivery": C.OUT,
          "delivered": C.DELIVERED, "failed_attempt": None, "returned": C.RETURNED}
ORDER = [C.BOOKED, C.ASSIGNED, C.PICKED, C.TRANSIT, C.AT_HUB, C.OUT, C.DELIVERED]
FINISHED = (C.DELIVERED, C.RETURNED, C.CANCELLED)
MAX_EVENTS = 100


def openapi():
    ev = {"type": "object", "required": ["event_id", "number", "type", "at"], "properties": {
        "event_id": {"type": "string", "maxLength": 80, "description": "Your id of the event (used once)"},
        "number": {"type": "string", "description": "Yo parcel number, e.g. YO12345678"},
        "type": {"type": "string", "enum": list(EVENTS)},
        "at": {"type": "string", "format": "date-time"},
        "place": {"type": "string", "maxLength": 80}, "note": {"type": "string", "maxLength": 200},
        "receiver": {"type": "string", "maxLength": 60, "description": "Name of the receiver (delivered)"}}}
    sec = [{"YoKey": [], "YoSignature": []}]
    return {
        "openapi": "3.0.3",
        "info": {"title": "Yo Delivery partner API", "version": "1.0",
                 "description": "Tracking events of courier partner companies. Sign every request: "
                                "X-Yo-Signature: t=<unix time>,v1=<hex HMAC-SHA256(secret, '<t>.<body>')>."},
        "components": {"securitySchemes": {
            "YoKey": {"type": "apiKey", "in": "header", "name": "X-Yo-Key"},
            "YoSignature": {"type": "apiKey", "in": "header", "name": "X-Yo-Signature"}},
            "schemas": {"Event": ev}},
        "paths": {
            "/api/partner/v1/events": {"post": {
                "summary": "Send tracking events", "security": sec,
                "requestBody": {"required": True, "content": {"application/json": {"schema": {
                    "type": "object", "properties": {"events": {"type": "array", "maxItems": MAX_EVENTS,
                                                                "items": {"$ref": "#/components/schemas/Event"}}}}}}},
                "responses": {"200": {"description": "The result of every event"}, "401": {"description": "Bad key or signature"}}}},
            "/api/partner/v1/shipments": {"get": {
                "summary": "My parcels that are not finished", "security": sec,
                "responses": {"200": {"description": "List of parcels"}}}},
            "/api/partner/v1/shipments/{number}": {"get": {
                "summary": "One of my parcels", "security": sec,
                "parameters": [{"name": "number", "in": "path", "required": True, "schema": {"type": "string"}}],
                "responses": {"200": {"description": "The parcel"}, "404": {"description": "Not one of your parcels"}}}},
        }}


class PartnersMixin:

    def add_partner(self, actor_id, name, countries=""):
        if not self.can(actor_id, "partners.manage"):
            raise ShopError("You do not have the ability to do this. Ask your manager.")
        name = " ".join(str(name or "").split())[:60]
        if len(name) < 2:
            raise ShopError("Please write the name of the company.")
        key_id, secret = "pk_" + secrets.token_hex(8), "ps_" + secrets.token_urlsafe(24)
        with self.db() as conn:
            if conn.execute("SELECT 1 FROM partners WHERE name = ?", (name,)).fetchone():
                raise ShopError("There is a partner with this name already.")
            conn.execute("INSERT INTO partners (name, countries, key_id, secret, secret_hint, created_by, created_at) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?)", (name, " ".join(str(countries).upper().split())[:60], key_id,
                                                          secret, secret[-4:], actor_id, now()))
        self.log(actor_id, "Partner added", name)
        return key_id, secret                                       # the secret is shown only now

    def rotate_partner_secret(self, actor_id, partner_id):
        if not self.can(actor_id, "partners.manage"):
            raise ShopError("You do not have the ability to do this. Ask your manager.")
        secret = "ps_" + secrets.token_urlsafe(24)
        with self.db() as conn:
            if not conn.execute("UPDATE partners SET secret = ?, secret_hint = ? WHERE id = ?",
                                (secret, secret[-4:], partner_id)).rowcount:
                raise ShopError("Partner not found.")
        self.log(actor_id, "Partner secret changed", str(partner_id))
        return secret

    def set_partner_active(self, actor_id, partner_id, active):
        if not self.can(actor_id, "partners.manage"):
            raise ShopError("You do not have the ability to do this. Ask your manager.")
        with self.db() as conn:
            conn.execute("UPDATE partners SET active = ? WHERE id = ?", (1 if active else 0, partner_id))

    def partners(self):
        with self.db() as conn:
            rows = [dict(r) for r in conn.execute("SELECT * FROM partners ORDER BY name")]
            for r in rows:
                r.pop("secret", None)
                r["open"] = conn.execute("SELECT COUNT(*) FROM shipments WHERE partner_id = ? AND status NOT IN (?, ?, ?)",
                                         (r["id"], *FINISHED)).fetchone()[0]
                r["events"] = conn.execute("SELECT COUNT(*) FROM partner_events WHERE partner_id = ?",
                                           (r["id"],)).fetchone()[0]
        return rows

    def hand_to_partner(self, actor_id, number, partner_id):
        if not self.can(actor_id, "shipments.manage"):
            raise ShopError("You do not have the ability to do this. Ask your manager.")
        with self.db() as conn:
            p = conn.execute("SELECT * FROM partners WHERE id = ? AND active = 1", (partner_id,)).fetchone()
            if p is None:
                raise ShopError("Partner not found.")
            s = conn.execute("SELECT * FROM shipments WHERE number = ?", (str(number or "").strip().upper(),)).fetchone()
            if s is None:
                raise ShopError("No parcel with this number.")
            if s["status"] in FINISHED or s["courier_id"]:
                raise ShopError("This parcel is with a Yo courier or finished.")
            conn.execute("UPDATE shipments SET partner_id = ?, partner_ref = '', moved_at = ? WHERE id = ?",
                         (p["id"], now(), s["id"]))
            self._event(conn, s["id"], s["status"], f"Handed to our partner {p['name']}", "", "Yo", now())
        self.log(actor_id, "Parcel handed to a partner", f"{s['number']} -> {p['name']}")

    # ---------------------------------------------------------------- the API
    def partner_auth(self, key_id, signature, body, at=None):
        with self.db() as conn:
            p = conn.execute("SELECT * FROM partners WHERE key_id = ? AND active = 1", (str(key_id or "")[:40],)).fetchone()
        if p is None or not signature_ok(p["secret"], signature, body, at):
            return None
        with self.db() as conn:
            conn.execute("UPDATE partners SET last_call_at = ? WHERE id = ?", (now(), p["id"]))
        return dict(p)

    def partner_shipments(self, partner, number=None):
        with self.db() as conn:
            if number:
                rows = conn.execute("SELECT * FROM shipments WHERE partner_id = ? AND number = ?",
                                    (partner["id"], str(number).upper()))
            else:
                rows = conn.execute("SELECT * FROM shipments WHERE partner_id = ? AND status NOT IN (?, ?, ?) "
                                    "ORDER BY id", (partner["id"], *FINISHED))
            out = []
            for s in [dict(r) for r in rows.fetchall()]:
                active = s["status"] not in FINISHED
                out.append({"number": s["number"], "status": s["status"], "weight_g": s["weight_g"],
                            "from": {"city": s["from_city"], "address": s["from_address"] if active else "",
                                     "name": s["from_name"], "phone": s["from_mobile"] if active else ""},
                            "to": {"city": s["to_city"], "country": s["to_country"], "postcode": s["to_postcode"],
                                   "address": s["to_address"] if active else "", "name": s["to_name"],
                                   "phone": s["to_mobile"] if active else ""},
                            "window": s.get("time_window", ""), "created_at": s["created_at"]})
        return out

    def partner_events_in(self, partner, payload):
        events = payload.get("events") if isinstance(payload, dict) else None
        if not isinstance(events, list) or not events:
            raise ShopError("Send a JSON object with a list 'events'.")
        if len(events) > MAX_EVENTS:
            raise ShopError(f"At most {MAX_EVENTS} events at a time.")
        return [self._partner_event(partner, e) for e in events]

    def _partner_event(self, partner, e):
        try:
            event_id, number, kind = str(e["event_id"])[:80], str(e["number"]).strip().upper()[:20], str(e["type"])
            at = datetime.fromisoformat(str(e["at"]).replace("Z", "+00:00")).replace(tzinfo=None)
        except (KeyError, ValueError, TypeError):
            return {"event_id": str(e.get("event_id", "")) if isinstance(e, dict) else "", "result": "bad event"}
        if kind not in EVENTS:
            return {"event_id": event_id, "result": "unknown type"}
        place, note = " ".join(str(e.get("place", "")).split())[:80], " ".join(str(e.get("note", "")).split())[:200]
        when = at.isoformat(timespec="seconds")
        with self.db() as conn:
            if conn.execute("SELECT 1 FROM partner_events WHERE partner_id = ? AND event_id = ?",
                            (partner["id"], event_id)).fetchone():
                return {"event_id": event_id, "result": "already done"}
            s = conn.execute("SELECT * FROM shipments WHERE number = ? AND partner_id = ?", (number, partner["id"])).fetchone()
            if s is None:
                result = "not your parcel"
            elif s["status"] in FINISHED:
                result = "parcel finished"
            else:
                result = self._apply_partner_event(conn, partner, dict(s), kind, place, note, when,
                                                   " ".join(str(e.get("receiver", "")).split())[:60])
            conn.execute("INSERT INTO partner_events (partner_id, event_id, number, kind, at, place, note, result, "
                         "received_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (partner["id"], event_id, number, kind, when, place, note, result, now()))
        return {"event_id": event_id, "result": result}

    def _apply_partner_event(self, conn, partner, s, kind, place, note, when, receiver):
        who = f"Partner {partner['name']}"
        new = EVENTS[kind]
        if new is None:                                             # a failed attempt: only a line in the tracking
            self._event(conn, s["id"], s["status"], note or "Delivery attempt failed (partner)", place, who, when)
            conn.execute("UPDATE shipments SET attempts = attempts + 1 WHERE id = ?", (s["id"],))
            return "saved"
        if new == C.RETURNED:
            conn.execute("UPDATE shipments SET status = ?, moved_at = ? WHERE id = ? AND status = ?",
                         (new, when, s["id"], s["status"]))
            self._event(conn, s["id"], new, note or "Returned to the sender by our partner", place, who, when)
            self.notify(conn, s["user_id"], "Returned: {0}".replace("{0}", s["number"]), note or "Returned to the sender.",
                        url=f"/courier/s/{s['number']}")
            return "moved"
        old_i = ORDER.index(s["status"]) if s["status"] in ORDER else 0
        if ORDER.index(new) <= old_i and new != C.AT_HUB:
            return "old event (parcel is further already)"
        extra, args = "", []
        if new == C.DELIVERED:
            extra, args = ", delivered_at = ?, pod_name = ?, pod_by = ?", [when, receiver or s["to_name"].split()[0],
                                                                         "partner:" + partner["name"][:30]]
        elif new == C.PICKED:
            extra, args = ", picked_at = ?", [when]
        n = conn.execute(f"UPDATE shipments SET status = ?, moved_at = ?{extra} WHERE id = ? AND status = ?",
                         (new, when, *args, s["id"], s["status"])).rowcount
        if not n:
            return "parcel changed a moment ago, send again"
        self._event(conn, s["id"], new, note or new, place, who, when)
        if new in (C.OUT, C.DELIVERED):
            title = "Delivered: {0}" if new == C.DELIVERED else "Out for delivery: {0}"
            self.notify(conn, s["user_id"], title.replace("{0}", s["number"]), f"{new} ({partner['name']})",
                        url=f"/courier/s/{s['number']}")
        return "moved"

    def partner_event_log(self, partner_id=None, limit=100):
        where, args = ("WHERE e.partner_id = ? ", [partner_id]) if partner_id else ("", [])
        with self.db() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT e.*, p.name AS partner FROM partner_events e JOIN partners p ON p.id = e.partner_id "
                f"{where}ORDER BY e.id DESC LIMIT ?", (*args, limit))]


def body_hash(body):
    return hashlib.sha256(body).hexdigest()


def as_json(body):
    try:
        return json.loads(body or b"{}")
    except ValueError:
        raise ShopError("The body must be JSON.")
