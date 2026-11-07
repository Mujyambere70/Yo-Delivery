"""
gtfs.py - version 6 (mixin class used by Store): real timetables from transport companies in the GTFS format.

GTFS (General Transit Feed Specification) is the open format in which bus, train and ferry companies publish their
timetables: a ZIP file with the text tables agency.txt, stops.txt, routes.txt, trips.txt, stop_times.txt and
calendar.txt. A person with the ability "transport.manage" uploads the file on /admin/tickets/gtfs:

    1. every stop is matched to the nearest town or village of our place list (at most MATCH_KM away)
    2. the company becomes a transport company (ticket_operators); every trip gives a service for each pair of its
       stops (A -> B, A -> C, B -> C ...), with the real departure time, the real travel time, the days of the
       week of calendar.txt and the first and the last valid day
    3. for a route that has GTFS services, Yo Tickets shows only these (the generated demo timetable is not used)
Seats, prices, holds, payments and e-tickets work as for every other trip; fares follow the rules of Yo Tickets
(the fares of the company are not read in this version). data/gtfs/sample_rwanda.zip is a small sample feed of a
fictional company (Kigali - Base - Gakenke - Musanze).
"""

import csv
import io
import json
import re
import zipfile
from datetime import datetime

import geo
import tickets as TK
from core import ShopError, now

GTFS_SCHEMA = """
CREATE TABLE IF NOT EXISTS gtfs_feeds (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, operator_id INTEGER NOT NULL, mode TEXT NOT NULL,
    stops INTEGER NOT NULL DEFAULT 0, matched INTEGER NOT NULL DEFAULT 0, trips INTEGER NOT NULL DEFAULT 0,
    services INTEGER NOT NULL DEFAULT 0, valid_from TEXT NOT NULL DEFAULT '', valid_to TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1, imported_by INTEGER, created_at TEXT NOT NULL
);
"""

MATCH_KM = 20
MAX_BYTES = 20 * 1024 * 1024
MAX_TRIPS = 2000
ROUTE_TYPES = {"3": "bus", "700": "bus", "200": "bus", "2": "train", "100": "train", "101": "train", "102": "train",
               "103": "train", "106": "train", "1100": "flight"}
CLASSES = {"bus": ["seater"], "train": ["CC", "EC"], "flight": ["Y"]}
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _table(z, name, required=True):
    names = {n.split("/")[-1]: n for n in z.namelist()}
    if name not in names:
        if required:
            raise ShopError(f"The file has no {name}. A GTFS file needs agency, stops, routes, trips, stop_times "
                            "and calendar.")
        return []
    with z.open(names[name]) as f:
        text = io.TextIOWrapper(f, encoding="utf-8-sig", newline="")
        return list(csv.DictReader(text))


def _minutes(hms):
    """GTFS times can be after midnight of the service day: 25:10:00 = 01:10 the next day."""
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\s*", str(hms or ""))
    if not m:
        return None
    return int(m.group(1)) * 60 + int(m.group(2))


def _day(text):
    try:
        return datetime.strptime(str(text).strip(), "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        return ""


def _stop_place(lat, lng, stop_name=""):
    """A bus station belongs to its town: a place named in the stop name ("Gakenke town") within MATCH_KM first,
    then a town or a city within 8 km, a sector (Rwanda) within 8 km, then the nearest place of any size."""
    for word in re.findall(r"[^\W\d_]{4,}", stop_name or ""):
        hits = [p for p in geo.search(word, limit=8) if geo.key(p["name"]) == geo.key(word)
                and geo.haversine_km((lat, lng), (p["lat"], p["lng"])) <= MATCH_KM]
        if hits:
            return max(hits, key=lambda p: geo.KIND_RANK.get(p["kind"], 0))
    for km, rank in ((8, 5), (8, 4), (MATCH_KM, 0)):
        p = geo.closest(lat, lng, km, min_rank=rank)
        if p:
            return p
    return None


def read_feed(blob):
    """Read and check a GTFS zip. Returns a dict of the tables we use."""
    if len(blob) > MAX_BYTES:
        raise ShopError("The GTFS file is larger than 20 MB. Please upload one company or one region at a time.")
    try:
        z = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile:
        raise ShopError("This is not a ZIP file. A GTFS timetable is a ZIP file with text tables.")
    feed = {name: _table(z, name + ".txt", name != "calendar") for name in
            ("agency", "stops", "routes", "trips", "stop_times", "calendar")}
    if not feed["agency"] or not feed["stops"] or not feed["trips"]:
        raise ShopError("The GTFS file has no company, no stops or no trips.")
    if len(feed["trips"]) > MAX_TRIPS:
        raise ShopError(f"The file has more than {MAX_TRIPS} trips. Please upload a smaller part.")
    return feed


class GtfsMixin:
    def import_gtfs(self, user_id, blob, feed_name=""):
        if not self.can(user_id, "transport.manage"):
            raise ShopError("You do not have the ability to manage transport companies.")
        feed = read_feed(blob)
        agency = feed["agency"][0]
        name = " ".join(str(feed_name or agency.get("agency_name") or "GTFS company").split())[:60]
        routes = {r["route_id"]: r for r in feed["routes"]}
        modes = {ROUTE_TYPES.get(str(r.get("route_type", "3")).strip(), "bus") for r in routes.values()} or {"bus"}
        mode = "train" if "train" in modes else "flight" if modes == {"flight"} else "bus"
        # 1. stops -> places
        stops, matched = {}, 0
        for s in feed["stops"]:
            try:
                lat, lng = float(s["stop_lat"]), float(s["stop_lon"])
            except (KeyError, TypeError, ValueError):
                continue
            p = _stop_place(lat, lng, s.get("stop_name", ""))
            if p:
                stops[s["stop_id"]] = {"key": TK.place(p["id"])["key"] if TK.place(p["id"]) else p["id"],
                                       "lat": lat, "lng": lng, "name": s.get("stop_name", "")}
                matched += 1
        # 2. the days of each service_id
        cal = {}
        for c in feed["calendar"]:
            cal[c["service_id"]] = ("".join("1" if str(c.get(d, "0")).strip() == "1" else "0" for d in DAYS),
                                    _day(c.get("start_date")), _day(c.get("end_date")))
        times = {}
        for st in feed["stop_times"]:
            times.setdefault(st["trip_id"], []).append(st)
        code = "G" + re.sub(r"[^A-Z]", "", name.upper())[:3].ljust(3, "X")
        with self.db() as conn:
            n = 1
            while conn.execute("SELECT 1 FROM ticket_operators WHERE code = ?", (code,)).fetchone():
                code, n = code[:3] + str(n), n + 1
            op = conn.execute("INSERT INTO ticket_operators (code, name, mode, rating) VALUES (?, ?, ?, 4.2)",
                              (code, name, mode)).lastrowid
            fid = conn.execute("INSERT INTO gtfs_feeds (name, operator_id, mode, stops, matched, imported_by, created_at) "
                               "VALUES (?, ?, ?, ?, ?, ?, ?)", (name, op, mode, len(feed["stops"]), matched, user_id,
                                                                now())).lastrowid
            services, trips, first, last = 0, 0, "", ""
            for t in feed["trips"]:
                days, vfrom, vto = cal.get(t.get("service_id"), ("1111111", "", ""))
                if days == "0000000":
                    continue
                route = routes.get(t.get("route_id"), {})
                rows = sorted(times.get(t["trip_id"], []), key=lambda x: int(x.get("stop_sequence") or 0))
                rows = [r for r in rows if r.get("stop_id") in stops]
                if len(rows) < 2:
                    continue
                trips += 1
                first = min(first or vfrom, vfrom) if vfrom else first
                last = max(last, vto)
                number = (route.get("route_short_name") or t.get("trip_short_name") or t["trip_id"])[:12]
                label = (t.get("trip_headsign") or route.get("route_long_name") or name)[:80]
                pairs = [(i, j) for i in range(len(rows)) for j in range(i + 1, len(rows))]
                if len(rows) > 10:                                   # long lines: from the start and to the end
                    pairs = [(i, j) for i, j in pairs if i == 0 or j == len(rows) - 1]
                for i, j in pairs:
                    a, b = stops[rows[i]["stop_id"]], stops[rows[j]["stop_id"]]
                    dep = _minutes(rows[i].get("departure_time") or rows[i].get("arrival_time"))
                    arr = _minutes(rows[j].get("arrival_time") or rows[j].get("departure_time"))
                    if a["key"] == b["key"] or dep is None or arr is None or arr <= dep:
                        continue
                    km = round(geo.haversine_km((a["lat"], a["lng"]), (b["lat"], b["lng"])) * 1.25, 1)
                    done = conn.execute(
                        "INSERT INTO ticket_services (code, number, name, mode, operator_id, from_city, to_city, "
                        "depart_local, duration_min, days, classes, distance_km, created_at, source, valid_from, "
                        "valid_to) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'gtfs', ?, ?) ON CONFLICT (code) DO NOTHING",
                        (f"GT{fid}-{t['trip_id'][:24]}-{i}-{j}", f"{code} {number}", label, mode, op, a["key"],
                         b["key"], f"{(dep // 60) % 24:02d}:{dep % 60:02d}", arr - dep, days,
                         json.dumps(CLASSES[mode]), max(km, 1.0), now(), vfrom, vto)).rowcount
                    services += done
            if not services:
                raise ShopError("No trip of this file connects two places we know (stops must be at most "
                                f"{MATCH_KM} km from a town or a village).")
            conn.execute("UPDATE gtfs_feeds SET trips = ?, services = ?, valid_from = ?, valid_to = ? WHERE id = ?",
                         (trips, services, first, last, fid))
        return {"feed_id": fid, "operator": code, "stops": len(feed["stops"]), "matched": matched, "trips": trips,
                "services": services}

    def gtfs_feeds(self):
        with self.db() as conn:
            return [dict(r) for r in conn.execute("SELECT f.*, o.code, u.name AS by_name FROM gtfs_feeds f "
                                                  "JOIN ticket_operators o ON o.id = f.operator_id "
                                                  "LEFT JOIN users u ON u.id = f.imported_by ORDER BY f.id DESC")]

    def set_gtfs_active(self, user_id, feed_id, active):
        if not self.can(user_id, "transport.manage"):
            raise ShopError("You do not have the ability to manage transport companies.")
        with self.db() as conn:
            f = conn.execute("SELECT * FROM gtfs_feeds WHERE id = ?", (feed_id,)).fetchone()
            if f is None:
                raise ShopError("Unknown timetable.")
            conn.execute("UPDATE gtfs_feeds SET active = ? WHERE id = ?", (int(bool(active)), feed_id))
            conn.execute("UPDATE ticket_operators SET active = ? WHERE id = ?", (int(bool(active)), f["operator_id"]))
