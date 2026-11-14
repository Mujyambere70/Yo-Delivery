"""
webhooks.py - version 8 (mixin class used by Store): Yo Delivery tells its courier partners what happened.

In version 7 a partner company had to ask "which parcels are mine?" again and again (GET /api/partner/v1/shipments).
Now a partner can give us one https address (its webhook) and we send it a small signed JSON message when

    shipment.assigned      a parcel was handed to this partner
    shipment.cancelled     the sender cancelled a parcel of this partner
    shipment.updated       our staff changed the status of a parcel of this partner
    ping                   the test button of the admin page

Every message is saved first (table webhook_deliveries) and sent by the job queue, so a slow partner never slows a
customer down. A message that the partner does not answer with 2xx is tried again after 1, 4, 9, 16 and 25 minutes;
after six tries it is marked "Failed" and can be sent again by hand. The partner checks the header

    X-Yo-Signature: t=<unix time>,v1=<hex HMAC-SHA256(secret, "<t>.<body>")>

with the same secret that signs its own calls (providers.signature_ok does exactly this check).

Safety: only https addresses; no IP addresses, no "localhost" and no private networks (the host name is looked up
and every address is checked before we connect, and redirects are not followed). Addresses of made-up companies
(*.example, example.com ...) are never called: their messages get the status "Test (not sent)".
"""

import ipaddress
import json
import socket
import urllib.error
import urllib.request
from urllib.parse import urlsplit

import courier as C
from core import ShopError, now
from providers import sign

WEBHOOK_SCHEMA = """
CREATE TABLE IF NOT EXISTS webhook_deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT, partner_id INTEGER NOT NULL REFERENCES partners(id), event TEXT NOT NULL,
    number TEXT NOT NULL DEFAULT '', payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Waiting',
    tries INTEGER NOT NULL DEFAULT 0, last_code INTEGER NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL, sent_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_webhook_partner ON webhook_deliveries(partner_id, id);
"""

EVENTS_OUT = ("shipment.assigned", "shipment.cancelled", "shipment.updated", "ping")
MAX_TRIES = 6
TIMEOUT_S = 6
_FAKE_HOSTS = ("example.com", "example.org", "example.net")
_FAKE_ENDS = (".example", ".test", ".invalid", ".localhost")


def is_demo_host(host):
    host = str(host or "").lower().rstrip(".")
    return host in _FAKE_HOSTS or host.endswith(_FAKE_ENDS) or any(host.endswith("." + h) for h in _FAKE_HOSTS)


def check_url(url):
    """Returns (clean url, host) or raises ShopError."""
    url = str(url or "").strip()
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host or parts.username or parts.password or len(url) > 300:
        raise ShopError("The webhook address must start with https:// and have no user name in it.")
    if parts.port not in (None, 443, 8443):
        raise ShopError("The webhook can use the ports 443 or 8443 only.")
    try:
        ipaddress.ip_address(host.strip("[]"))
        raise ShopError("Please use the name of the server, not an IP address.")
    except ValueError:
        pass
    if host == "localhost" or "." not in host:
        raise ShopError("This address is not on the internet.")
    return url, host


def public_addresses(host):
    """The IP addresses of a host - or ShopError when one of them is private, local or reserved."""
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except OSError:
        raise ShopError("The server name could not be found.")
    out = []
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise ShopError("The server is on a private network.")
        out.append(str(ip))
    return out


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None


def http_post(url, body, headers):
    """POST and return the status code. Used only for addresses that passed check_url and public_addresses."""
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with opener.open(req, timeout=TIMEOUT_S) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


class WebhooksMixin:
    webhook_sender = None             # tests put a function (url, body, headers) -> status code here

    def job_handlers(self):
        handlers = super().job_handlers()
        handlers.setdefault("webhook", lambda p: self.send_webhook(p.get("id")))
        return handlers

    # ------------------------------------------------------------------ settings of a partner
    def set_partner_webhook(self, actor_id, partner_id, url):
        if not self.can(actor_id, "partners.manage"):
            raise ShopError("You do not have the ability to do this. Ask your manager.")
        url = str(url or "").strip()
        if url:
            url, _ = check_url(url)
        with self.db() as conn:
            if not conn.execute("UPDATE partners SET webhook_url = ? WHERE id = ?", (url, partner_id)).rowcount:
                raise ShopError("Partner not found.")
        self.log(actor_id, "Partner webhook changed", str(partner_id))

    # ------------------------------------------------------------------ queue
    def queue_webhook(self, partner_id, event, number="", data=None, conn=None):
        """Save a message for the partner (when he has a webhook) and put it on the job queue. Returns its id."""
        if event not in EVENTS_OUT:
            raise ShopError("Unknown event.")

        def go(c):
            p = c.execute("SELECT webhook_url, active FROM partners WHERE id = ?", (partner_id,)).fetchone()
            if p is None or not p["webhook_url"] or not p["active"]:
                return None
            cur = c.execute("INSERT INTO webhook_deliveries (partner_id, event, number, payload, created_at) "
                            "VALUES (?, ?, ?, '', ?)", (partner_id, event, number, now()))
            did = cur.lastrowid
            body = json.dumps({"id": f"whd_{did}", "type": event, "created_at": now(), "number": number,
                               "data": data or {}}, separators=(",", ":"), ensure_ascii=False)
            c.execute("UPDATE webhook_deliveries SET payload = ? WHERE id = ?", (body, did))
            self.enqueue("webhook", {"id": did}, unique_key=f"webhook:{did}", max_attempts=MAX_TRIES, conn=c)
            return did
        if conn is not None:
            return go(conn)
        with self.db() as c:
            return go(c)

    def send_webhook(self, delivery_id):
        """Send one saved message now. Raises when the partner did not answer 2xx (the job queue tries again)."""
        with self.db() as conn:
            d = conn.execute("SELECT d.*, p.webhook_url, p.secret, p.active FROM webhook_deliveries d JOIN partners p "
                             "ON p.id = d.partner_id WHERE d.id = ?", (delivery_id,)).fetchone()
        if d is None or d["status"] in ("Sent", "Test (not sent)"):
            return "skip"
        if not d["webhook_url"] or not d["active"]:
            self._webhook_result(delivery_id, "Failed", 0, "The partner has no webhook now.")
            return "skip"
        url, host = check_url(d["webhook_url"])
        if is_demo_host(host) and self.webhook_sender is None:
            self._webhook_result(delivery_id, "Test (not sent)", 0, "Made-up address: nothing was sent.")
            return "test"
        body = d["payload"].encode("utf-8")
        headers = {"Content-Type": "application/json", "User-Agent": "YoDelivery-Webhook/8",
                   "X-Yo-Event": d["event"], "X-Yo-Delivery": f"whd_{d['id']}", "X-Yo-Signature": sign(d["secret"], body)}
        final = d["tries"] + 1 >= MAX_TRIES
        try:
            if self.webhook_sender is not None:
                code = int(self.webhook_sender(url, body, headers))
            else:
                public_addresses(host)
                code = http_post(url, body, headers)
        except ShopError as e:
            self._webhook_result(delivery_id, "Failed" if final else "Waiting", 0, str(e))
            raise
        except Exception as e:                                # time-out, refused connection, TLS error ...
            self._webhook_result(delivery_id, "Failed" if final else "Waiting", 0, type(e).__name__)
            raise ShopError(f"Webhook not delivered: {type(e).__name__}")
        if 200 <= code < 300:
            self._webhook_result(delivery_id, "Sent", code, "")
            return "sent"
        self._webhook_result(delivery_id, "Failed" if final else "Waiting", code, f"The partner answered {code}.")
        raise ShopError(f"Webhook not delivered: HTTP {code}")

    def _webhook_result(self, delivery_id, status, code, error):
        with self.db() as conn:
            conn.execute("UPDATE webhook_deliveries SET status = ?, tries = tries + 1, last_code = ?, last_error = ?, "
                         "sent_at = ? WHERE id = ?", (status, code, error[:200], now() if status == "Sent" else "", delivery_id))

    def resend_webhook(self, actor_id, delivery_id):
        if not self.can(actor_id, "partners.manage"):
            raise ShopError("You do not have the ability to do this. Ask your manager.")
        with self.db() as conn:
            if not conn.execute("UPDATE webhook_deliveries SET status = 'Waiting', tries = 0, last_error = '' WHERE id = ? "
                                "AND status = 'Failed'", (delivery_id,)).rowcount:
                raise ShopError("Only a failed message can be sent again.")
            self.enqueue("webhook", {"id": int(delivery_id)}, unique_key=f"webhook:{delivery_id}", max_attempts=MAX_TRIES,
                         conn=conn)

    def ping_partner_webhook(self, actor_id, partner_id):
        if not self.can(actor_id, "partners.manage"):
            raise ShopError("You do not have the ability to do this. Ask your manager.")
        did = self.queue_webhook(partner_id, "ping", data={"message": "Hello from Yo Delivery"})
        if did is None:
            raise ShopError("This partner has no webhook address (or is switched off).")
        return did

    def webhook_deliveries(self, partner_id=None, limit=60):
        sql = ("SELECT d.id, d.partner_id, d.event, d.number, d.status, d.tries, d.last_code, d.last_error, d.created_at, "
               "d.sent_at, p.name AS partner FROM webhook_deliveries d JOIN partners p ON p.id = d.partner_id")
        with self.db() as conn:
            if partner_id:
                rows = conn.execute(sql + " WHERE d.partner_id = ? ORDER BY d.id DESC LIMIT ?", (partner_id, limit))
            else:
                rows = conn.execute(sql + " ORDER BY d.id DESC LIMIT ?", (limit,))
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------ what makes a message
    @staticmethod
    def _parcel_data(s):
        s = dict(s)
        return {"status": s["status"], "from_city": s["from_city"], "to_city": s["to_city"],
                "to_country": s.get("to_country") or "", "weight_g": s.get("weight_g"),
                "partner_ref": s.get("partner_ref") or ""}

    def hand_to_partner(self, actor_id, number, partner_id):
        super().hand_to_partner(actor_id, number, partner_id)
        with self.db() as conn:
            s = conn.execute("SELECT * FROM shipments WHERE number = ?", (str(number or "").strip().upper(),)).fetchone()
            if s is not None:
                self.queue_webhook(s["partner_id"], "shipment.assigned", s["number"], self._parcel_data(s), conn=conn)

    def cancel_shipment(self, user_id, number):
        super().cancel_shipment(user_id, number)
        with self.db() as conn:
            s = conn.execute("SELECT * FROM shipments WHERE number = ?", (number,)).fetchone()
            if s is not None and s["partner_id"] and s["status"] == C.CANCELLED:
                self.queue_webhook(s["partner_id"], "shipment.cancelled", s["number"], self._parcel_data(s), conn=conn)
