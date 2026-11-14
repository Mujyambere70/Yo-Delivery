"""
v8extra.py - version 8 (mixin class used by Store): smaller answers to the "future scope" list of version 7.

Couriers
    speed profile   the real speed of the couriers of a town at every hour of the day, learned from their own GPS
                    points (table courier_track), used for the arrival times of the route plan instead of the fixed
                    25 km/h
Travel
    live vehicles   where a bus or train is now (GTFS-Realtime VehiclePosition)
    service alerts  messages of the operator about a line or a trip (GTFS-Realtime Alert), shown on the ticket and
                    sent once to the passengers of the trip
    background poll the saved address of a live feed is read every two minutes by the job queue (version 7 only
                    read a file that a person uploaded)
Help desk
    live messages   the page of a question asks every few seconds for the messages after the last one it has
    inbound e-mail  a signed endpoint for the e-mail provider: an e-mail from a customer opens a question, and an
                    answer with [S000123] in its subject is added to that question
Data
    hourly refresh  the analytics tables are rebuilt every hour (version 7: once a night)
    holidays        the hub forecast marks public holidays and uses what earlier holidays did to the hub
"""

import hashlib
import hmac
import json
import re
import urllib.request
from datetime import date, datetime, timedelta

from google.transit import gtfs_realtime_pb2

import places
import routeplan
from core import ShopError, now
from webhooks import _NoRedirect, check_url, public_addresses

EXTRA_V8_SCHEMA = """
CREATE TABLE IF NOT EXISTS speed_profile (
    city TEXT NOT NULL, hour INTEGER NOT NULL, kmh REAL NOT NULL, samples INTEGER NOT NULL, built_at TEXT NOT NULL,
    PRIMARY KEY (city, hour)
);
CREATE TABLE IF NOT EXISTS gtfs_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, feed_id INTEGER NOT NULL, alert_key TEXT NOT NULL,
    trip_id TEXT NOT NULL DEFAULT '', route_id TEXT NOT NULL DEFAULT '', header TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '', effect TEXT NOT NULL DEFAULT '', starts_at TEXT NOT NULL DEFAULT '',
    ends_at TEXT NOT NULL DEFAULT '', first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, UNIQUE (feed_id, alert_key)
);
CREATE TABLE IF NOT EXISTS gtfs_vehicles (
    feed_id INTEGER NOT NULL, trip_id TEXT NOT NULL, label TEXT NOT NULL DEFAULT '', lat REAL NOT NULL, lng REAL NOT NULL,
    stop_status TEXT NOT NULL DEFAULT '', at TEXT NOT NULL, PRIMARY KEY (feed_id, trip_id)
);
CREATE TABLE IF NOT EXISTS inbound_emails (
    id INTEGER PRIMARY KEY AUTOINCREMENT, message_id TEXT NOT NULL UNIQUE, from_hash TEXT NOT NULL, result TEXT NOT NULL,
    ticket TEXT NOT NULL DEFAULT '', received_at TEXT NOT NULL
);
"""

MIN_SAMPLES = 20
ALERT_EFFECTS = {1: "No service", 2: "Reduced service", 3: "Significant delays", 4: "Detour", 5: "Additional service",
                 6: "Modified service", 7: "Other effect", 8: "Unknown effect", 9: "Stop moved"}
VEHICLE_STATUS = {0: "Arriving at a stop", 1: "Stopped at a stop", 2: "On the way to the next stop"}
MAX_FEED_BYTES = 5_000_000
POLL_EVERY_MIN = 2
_CODE = re.compile(r"^GT(\d+)-(.*)-\d+-\d+$")
_TICKET_IN_SUBJECT = re.compile(r"\[(S\d{6})\]", re.I)


def _ts(seconds):
    return datetime.fromtimestamp(int(seconds)).strftime("%Y-%m-%d %H:%M:%S") if seconds else ""


def _text(translated):
    """The English text of a GTFS TranslatedString (or the first one)."""
    best = ""
    for t in translated.translation:
        if t.language.lower().startswith("en"):
            return t.text.strip()
        best = best or t.text.strip()
    return best


def read_alerts(blob):
    """GTFS-Realtime bytes -> [{"key", "trip_id", "route_id", "header", "details", "effect", "starts_at", "ends_at"}]."""
    feed = gtfs_realtime_pb2.FeedMessage()
    try:
        feed.ParseFromString(blob)
    except Exception:
        raise ShopError("This is not a GTFS-Realtime file.")
    out = []
    for e in feed.entity:
        if not e.HasField("alert"):
            continue
        a = e.alert
        header = _text(a.header_text)[:160]
        if not header:
            continue
        start = min((p.start for p in a.active_period if p.start), default=0)
        end = max((p.end for p in a.active_period if p.end), default=0)
        targets = [(ie.trip.trip_id, ie.route_id) for ie in a.informed_entity] or [("", "")]
        for n, (trip_id, route_id) in enumerate(targets[:20]):
            out.append({"key": f"{e.id or header[:40]}#{n}"[:80], "trip_id": trip_id[:60], "route_id": route_id[:60],
                        "header": header, "details": _text(a.description_text)[:600],
                        "effect": ALERT_EFFECTS.get(a.effect, ""), "starts_at": _ts(start), "ends_at": _ts(end)})
    return out


def read_vehicles(blob):
    """GTFS-Realtime bytes -> [{"trip_id", "label", "lat", "lng", "status", "at"}] (only vehicles on a trip)."""
    feed = gtfs_realtime_pb2.FeedMessage()
    try:
        feed.ParseFromString(blob)
    except Exception:
        raise ShopError("This is not a GTFS-Realtime file.")
    out = []
    for e in feed.entity:
        if not e.HasField("vehicle"):
            continue
        v = e.vehicle
        if not v.trip.trip_id or not v.HasField("position"):
            continue
        lat, lng = v.position.latitude, v.position.longitude
        if not (-90 <= lat <= 90 and -180 <= lng <= 180) or (lat == 0 and lng == 0):
            continue
        out.append({"trip_id": v.trip.trip_id[:60], "label": (v.vehicle.label or v.vehicle.id)[:40],
                    "lat": round(lat, 5), "lng": round(lng, 5), "status": VEHICLE_STATUS.get(v.current_status, ""),
                    "at": _ts(v.timestamp or feed.header.timestamp)})
    return out


def sample_live_feed(vehicles=(), alerts=(), when=None):
    """A GTFS-Realtime file for tests and the demo. vehicles = [(trip_id, lat, lng, label)];
    alerts = [(id, trip_id, header, details, effect number)]."""
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = int((when or datetime.now()).timestamp())
    for i, (trip_id, lat, lng, label) in enumerate(vehicles):
        e = feed.entity.add()
        e.id = f"v{i}"
        e.vehicle.trip.trip_id = trip_id
        e.vehicle.position.latitude = lat
        e.vehicle.position.longitude = lng
        e.vehicle.vehicle.label = label
        e.vehicle.current_status = gtfs_realtime_pb2.VehiclePosition.IN_TRANSIT_TO
        e.vehicle.timestamp = feed.header.timestamp
    for alert_id, trip_id, header, details, effect in alerts:
        e = feed.entity.add()
        e.id = alert_id
        if trip_id:
            e.alert.informed_entity.add().trip.trip_id = trip_id
        e.alert.header_text.translation.add(text=header, language="en")
        if details:
            e.alert.description_text.translation.add(text=details, language="en")
        e.alert.effect = effect
    return feed.SerializeToString()


_TOPIC_WORDS = (("parcel", ("parcel", "shipment", "courier", "pickup", "pick-up")),
                ("ticket", ("ticket", "bus", "train", "flight", "seat", "trip")),
                ("money", ("refund", "payment", "paid", "wallet", "money", "charged", "invoice")),
                ("account", ("password", "account", "login", "sign in", "e-mail address")),
                ("order", ("order", "delivery", "delivered", "return")))


def topic_of(text):
    """The help-desk topic of an e-mail, from simple words (first match wins; else "other")."""
    low = str(text or "").lower()
    for topic, words in _TOPIC_WORDS:
        if any(w in low for w in words):
            return topic
    return "other"


def http_get(url, limit=MAX_FEED_BYTES):
    """GET an https address that passed check_url and public_addresses; at most `limit` bytes."""
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, headers={"User-Agent": "YoDelivery/8", "Accept": "application/x-protobuf"})
    with opener.open(req, timeout=8) as resp:
        blob = resp.read(limit + 1)
    if len(blob) > limit:
        raise ShopError("The live feed is too large.")
    return blob


class V8ExtraMixin:
    rt_fetcher = None                 # tests put a function (url) -> bytes here

    # ================================================================ background work
    def job_handlers(self):
        handlers = super().job_handlers()
        handlers.setdefault("speed_profile", lambda p: self.build_speed_profile())
        handlers.setdefault("gtfs_live", lambda p: self.poll_gtfs_rt())
        return handlers

    def housekeeping(self, today=None):
        done = super().housekeeping(today)
        if done is None:
            return None
        for name, job in (("warehouse_hour", self.hourly_warehouse), ("gtfs_live", self.gtfs_poll_due)):
            try:
                done[name] = job()
            except Exception as e:                       # one failing job must not stop the shop
                done[name + "_error"] = str(e)[:120]
        return done

    def hourly_warehouse(self, at=None):
        """Once an hour (one process of all the machines): rebuild the analytics tables with the job queue; once a
        day also the speed profile."""
        at = at or datetime.now()
        if not self.claim_period("last_warehouse_hour", at.strftime("%Y-%m-%d %H")):
            return None
        self.enqueue("warehouse", unique_key="warehouse")
        if self.claim_period("last_speed_profile", at.strftime("%Y-%m-%d")):
            self.enqueue("speed_profile", unique_key="speed_profile")
        return "queued"

    # ================================================================ couriers: speed by town and hour
    def build_speed_profile(self, days=28, today=None):
        """Median speed (km/h along the road factor) between two GPS points of the same parcel, by town and hour.
        Pairs more than 10 minutes or less than 20 seconds apart, standing (under 3 km/h) and impossible (over
        90 km/h) are left out. Returns the number of (town, hour) rows written."""
        since = ((today or date.today()) - timedelta(days=days)).isoformat()
        with self.db() as conn:
            rows = conn.execute("SELECT t.shipment_id, t.lat, t.lng, t.at, s.to_city FROM courier_track t JOIN shipments s "
                                "ON s.id = t.shipment_id WHERE t.at >= ? ORDER BY t.shipment_id, t.id", (since,)).fetchall()
        speeds, prev = {}, None
        for r in rows:
            if prev is not None and prev["shipment_id"] == r["shipment_id"]:
                try:
                    a, b = datetime.fromisoformat(prev["at"][:19]), datetime.fromisoformat(r["at"][:19])
                except ValueError:
                    prev = r
                    continue
                dt = (b - a).total_seconds()
                if 20 <= dt <= 600:
                    km = places.haversine_km((prev["lat"], prev["lng"]), (r["lat"], r["lng"])) * routeplan.ROAD
                    kmh = km / (dt / 3600)
                    if 3 <= kmh <= 90:
                        speeds.setdefault((r["to_city"] or "", a.hour), []).append(kmh)
                        speeds.setdefault((r["to_city"] or "", -1), []).append(kmh)      # hour -1 = the whole day
            prev = r
        built = now()
        with self.db() as conn:
            conn.execute("DELETE FROM speed_profile")
            n = 0
            for (city, hour), vals in sorted(speeds.items()):
                if len(vals) < MIN_SAMPLES:
                    continue
                vals.sort()
                mid = len(vals) // 2
                median = vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2
                conn.execute("INSERT INTO speed_profile (city, hour, kmh, samples, built_at) VALUES (?, ?, ?, ?, ?)",
                             (city, hour, round(median, 1), len(vals), built))
                n += 1
        return n

    def speed_profile(self, city=""):
        with self.db() as conn:
            if city:
                rows = conn.execute("SELECT * FROM speed_profile WHERE city = ? ORDER BY hour", (city,))
            else:
                rows = conn.execute("SELECT * FROM speed_profile ORDER BY city, hour")
            return [dict(r) for r in rows]

    def speed_function(self, city):
        """minute of the day -> km/h for this town: the learned speed of the hour, else of the whole day, else the
        fixed speed. Also returns a note for the page ("learned from 240 GPS points" or "")."""
        prof = {r["hour"]: r for r in self.speed_profile(city)}
        if not prof:
            return None, ""
        day = prof.get(-1)

        def kmh(minute):
            r = prof.get(int(minute) // 60 % 24) or day
            return r["kmh"] if r else routeplan.SPEED_KMH
        total = day["samples"] if day else sum(r["samples"] for r in prof.values())
        return kmh, f"Arrival times use the real speed of couriers in {city} (from {total} GPS measurements)."

    def courier_route_plan(self, courier_id):
        with self.db() as conn:
            c = conn.execute("SELECT city FROM couriers WHERE id = ?", (courier_id,)).fetchone()
        speed, note = self.speed_function(c["city"]) if c else (None, "")
        routeplan.set_speed(speed)
        try:
            plan = super().courier_route_plan(courier_id)
        finally:
            routeplan.set_speed(None)
        plan["speed_note"] = note
        return plan

    # ================================================================ hub forecast with public holidays
    def hub_forecast(self, user_id, office_id, h=14, today=None):
        fc = super().hub_forecast(user_id, office_id, h, today)
        if not fc.get("ready"):
            return fc
        country = (fc["office"].get("country") or "").upper()
        first = date.fromisoformat(fc["forecast"][0]["day"])
        last = date.fromisoformat(fc["forecast"][-1]["day"])
        hol = {d: x for d, x in self.holiday_map(country, first, last).items() if x["kind"] in ("public", "lieu")}
        history = self.hub_series(fc["office"]["place"], days=365, today=today)
        factor = self.holiday_factor(history, country)
        for row in fc["forecast"]:
            row["holiday"] = hol[row["day"]]["name"] if row["day"] in hol else ""
            if row["holiday"] and factor is not None:
                row["parcels_plain"] = row["parcels"]
                row["parcels"] = round(row["parcels"] * factor)
                row["workers"] = -(-row["parcels"] // fc["per_worker"]) if row["parcels"] > 0 else 0
        fc["holiday_factor"] = factor
        fc["holidays"] = len(hol)
        return fc

    # ================================================================ help desk: live messages
    def support_messages_after(self, number, user_id, after_id=0):
        """The messages of a question after message `after_id` - for the customer who asked or for a support
        agent. None when the person may not see the question."""
        staff = self.can(user_id, "support.manage")
        t = self.support_ticket(number, None if staff else user_id)
        if t is None:
            return None
        try:
            after_id = int(after_id or 0)
        except (TypeError, ValueError):
            after_id = 0
        return {"status": t["status"], "messages": [
            {"id": m["id"], "name": m["name"], "staff": bool(m["from_staff"]), "body": m["body"], "at": m["at"]}
            for m in t["messages"] if m["id"] > after_id]}

    # ================================================================ help desk: e-mail from customers
    def inbound_email_ok(self, header, body, at=None):
        """The e-mail provider signs every call with the secret INBOUND_EMAIL_SECRET (same scheme as the partners)."""
        from providers import signature_ok
        secret = str(self.env.get("INBOUND_EMAIL_SECRET") or "")
        return bool(secret) and signature_ok(secret, header, body, at)

    def inbound_email(self, message_id, sender, subject, text):
        """One e-mail from a customer. Returns (result, ticket number): "replied", "opened", "unknown sender",
        "duplicate" or "refused: ...". The sender's address is not stored - only its hash, to count repeats."""
        message_id = str(message_id or "").strip()[:200]
        sender = str(sender or "").strip().lower()
        m = re.search(r"<([^<>\s]+@[^<>\s]+)>", sender)
        sender = m.group(1) if m else sender
        if not message_id or "@" not in sender:
            return "refused: bad message", ""
        from_hash = hashlib.sha256(sender.encode()).hexdigest()[:24]
        with self.db() as conn:
            if conn.execute("SELECT 1 FROM inbound_emails WHERE message_id = ?", (message_id,)).fetchone():
                return "duplicate", ""
            u = conn.execute("SELECT id, blocked FROM users WHERE LOWER(email) = ?", (sender,)).fetchone()
        text = re.split(r"\n(?:On .{5,80} wrote:|-{2,} ?Original Message ?-{2,}|>)", str(text or ""))[0].strip()[:3000]
        subject = " ".join(str(subject or "").split())[:120]
        result, number = "unknown sender", ""
        if u is not None and not u["blocked"]:
            try:
                ref = _TICKET_IN_SUBJECT.search(subject)
                mine = self.support_ticket(ref.group(1).upper(), u["id"]) if ref else None
                if mine is not None:
                    self.support_reply(u["id"], mine["number"], text or "(empty e-mail)")
                    result, number = "replied", mine["number"]
                else:
                    clean = re.sub(r"^(re|fwd?|aw)\s*:\s*", "", subject, flags=re.I) or "Question by e-mail"
                    number = self.open_support_ticket(u["id"], topic_of(f"{clean} {text}"), clean, text or subject)
                    result = "opened"
            except ShopError as e:
                result = f"refused: {e}"[:120]
        with self.db() as conn:
            conn.execute("INSERT INTO inbound_emails (message_id, from_hash, result, ticket, received_at) VALUES "
                         "(?, ?, ?, ?, ?)", (message_id, from_hash, result, number, now()))
        return result, number

    def inbound_email_log(self, limit=30):
        with self.db() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM inbound_emails ORDER BY id DESC LIMIT ?", (limit,))]

    # ================================================================ travel: vehicles and alerts
    def apply_gtfs_rt(self, feed_id, blob):
        done = super().apply_gtfs_rt(feed_id, blob)
        vehicles, alerts = read_vehicles(blob), read_alerts(blob)
        done.update(vehicles=len(vehicles), alerts=len(alerts), alert_messages=0)
        t = now()
        with self.db() as conn:
            for v in vehicles:
                if conn.execute("SELECT 1 FROM gtfs_vehicles WHERE feed_id = ? AND trip_id = ?", (feed_id, v["trip_id"])).fetchone():
                    conn.execute("UPDATE gtfs_vehicles SET label = ?, lat = ?, lng = ?, stop_status = ?, at = ? WHERE "
                                 "feed_id = ? AND trip_id = ?", (v["label"], v["lat"], v["lng"], v["status"], v["at"] or t,
                                                                 feed_id, v["trip_id"]))
                else:
                    conn.execute("INSERT INTO gtfs_vehicles (feed_id, trip_id, label, lat, lng, stop_status, at) VALUES "
                                 "(?, ?, ?, ?, ?, ?, ?)", (feed_id, v["trip_id"], v["label"], v["lat"], v["lng"], v["status"],
                                                           v["at"] or t))
            new = []
            for a in alerts:
                if conn.execute("SELECT 1 FROM gtfs_alerts WHERE feed_id = ? AND alert_key = ?", (feed_id, a["key"])).fetchone():
                    conn.execute("UPDATE gtfs_alerts SET header = ?, details = ?, effect = ?, starts_at = ?, ends_at = ?, "
                                 "last_seen = ? WHERE feed_id = ? AND alert_key = ?",
                                 (a["header"], a["details"], a["effect"], a["starts_at"], a["ends_at"], t, feed_id, a["key"]))
                else:
                    conn.execute("INSERT INTO gtfs_alerts (feed_id, alert_key, trip_id, route_id, header, details, effect, "
                                 "starts_at, ends_at, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                 (feed_id, a["key"], a["trip_id"], a["route_id"], a["header"], a["details"], a["effect"],
                                  a["starts_at"], a["ends_at"], t, t))
                    new.append(a)
            for a in new:                                 # tell the passengers of the trip once
                if not a["trip_id"]:
                    continue
                like = f"GT{int(feed_id)}-{a['trip_id'][:24]}-%"
                for b in conn.execute(
                        "SELECT b.user_id, b.pnr FROM ticket_bookings b JOIN ticket_trips t ON t.id = b.trip_id JOIN "
                        "ticket_services s ON s.id = t.service_id WHERE s.code LIKE ? AND b.status = 'Confirmed' AND "
                        "t.status = 'Scheduled' AND t.day >= ?", (like, date.today().isoformat())).fetchall():
                    self.notify(conn, b["user_id"], "Message about your trip", a["header"] +
                                (f" ({a['effect']})" if a["effect"] else ""), url=f"/tickets/b/{b['pnr']}")
                    done["alert_messages"] += 1
        return done

    def feed_live(self, service_code, at=None):
        """What the operator says now about the trip behind a service code: {"vehicle": {...} or None, "alerts": [..]}.
        A vehicle position older than 15 minutes is not shown; an alert ends at its end time or when the feed did
        not repeat it for a day."""
        m = _CODE.match(str(service_code or ""))
        if not m:
            return {"vehicle": None, "alerts": []}
        feed_id, trip24 = int(m.group(1)), m.group(2)
        at = at or datetime.now()
        fresh = (at - timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M:%S")
        seen = (at - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        t = at.strftime("%Y-%m-%d %H:%M:%S")
        with self.db() as conn:
            v = None
            for r in conn.execute("SELECT * FROM gtfs_vehicles WHERE feed_id = ? AND at >= ?", (feed_id, fresh)):
                if r["trip_id"][:24] == trip24:
                    v = dict(r)
            alerts = [dict(r) for r in conn.execute(
                "SELECT * FROM gtfs_alerts WHERE feed_id = ? AND last_seen >= ? AND (ends_at = '' OR ends_at >= ?) "
                "ORDER BY id DESC", (feed_id, seen, t)) if r["trip_id"][:24] == trip24 or (not r["trip_id"] and not r["route_id"])]
        if v:
            near = places.cities_near([(v["lat"], v["lng"])], limit=60)
            v["city"] = min(near, key=lambda c: places.haversine_km((c[1], c[2]), (v["lat"], v["lng"])))[0] if near else ""
        return {"vehicle": v, "alerts": alerts[:5]}

    def booking_live(self, booking):
        """feed_live for a ticket (a booking dict with service_id)."""
        with self.db() as conn:
            r = conn.execute("SELECT code FROM ticket_services WHERE id = ?", (booking.get("service_id"),)).fetchone()
        return self.feed_live(r["code"] if r else "")

    def gtfs_alerts(self, feed_id=None, limit=40):
        with self.db() as conn:
            if feed_id:
                rows = conn.execute("SELECT * FROM gtfs_alerts WHERE feed_id = ? ORDER BY id DESC LIMIT ?", (feed_id, limit))
            else:
                rows = conn.execute("SELECT * FROM gtfs_alerts ORDER BY id DESC LIMIT ?", (limit,))
            return [dict(r) for r in rows]

    def gtfs_poll_due(self, at=None):
        """Every two minutes: queue one poll of the live feeds (when a feed has an address)."""
        at = at or datetime.now()
        slot = at.strftime("%Y-%m-%d %H:") + f"{at.minute // POLL_EVERY_MIN * POLL_EVERY_MIN:02d}"
        with self.db() as conn:
            if not conn.execute("SELECT 1 FROM gtfs_feeds WHERE rt_url != '' AND active = 1").fetchone():
                return None
        if not self.claim_period("last_gtfs_poll", slot):
            return None
        self.enqueue("gtfs_live", unique_key="gtfs_live", max_attempts=1)
        return "queued"

    def poll_gtfs_rt(self):
        """Read every saved live address once. A feed that fails keeps its last data and shows the reason."""
        with self.db() as conn:
            feeds = [dict(r) for r in conn.execute("SELECT id, rt_url FROM gtfs_feeds WHERE rt_url != '' AND active = 1")]
        out = {"feeds": len(feeds), "ok": 0, "failed": 0}
        for f in feeds:
            try:
                if self.rt_fetcher is not None:
                    blob = self.rt_fetcher(f["rt_url"])
                else:
                    url, host = check_url(f["rt_url"])
                    public_addresses(host)
                    blob = http_get(url)
                self.apply_gtfs_rt(f["id"], blob)
                out["ok"] += 1
            except Exception as e:
                out["failed"] += 1
                reason = str(e) if isinstance(e, ShopError) else type(e).__name__
                with self.db() as conn:
                    conn.execute("UPDATE gtfs_feeds SET rt_checked_at = ?, rt_result = ? WHERE id = ?",
                                 (now(), f"Not read: {reason}"[:120], f["id"]))
        return out


def sign_json(secret, payload):
    """Helper for tests and for people who try the inbound e-mail endpoint by hand."""
    body = json.dumps(payload, separators=(",", ":")).encode()
    t = int(datetime.now().timestamp())
    mac = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return body, f"t={t},v1={mac}"
