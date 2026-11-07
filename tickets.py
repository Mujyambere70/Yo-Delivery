"""
tickets.py - Yo Tickets: bus, train and flight tickets with seat maps and e-tickets (mixin class used by Store).

How it works
    Search      from / to / date / passengers. The services of a route (a bus, a train or a flight that runs
                every day or on some days of the week) are made the first time the route is searched. They are
                always the same for the same route, because a hash of the route seeds the timetable generator,
                and they are saved in ticket_services (the admin can switch a service off).
                A trip = one service on one date (ticket_trips), made when somebody looks at that date.
    Seat map    every taken seat of a trip is one row of ticket_seats with UNIQUE(trip_id, seat), so two
                travellers can never get the same seat - even at the same moment on two servers.
    Hold        choosing seats makes a "Held" booking for 10 minutes. Paying in time confirms it; otherwise
                the seats are given back (the minute job, or the next search).
    Pay         Yo Pay, or a card of the built-in test gateway (EMI by card for bigger amounts).
    E-ticket    a 6-character PNR and a QR code with a signed link; the conductor (operator staff) scans it.
    Cancel      the refund depends on the mode and on the hours left before departure (REFUND_RULES).
                When the operator cancels a trip, everybody gets all the money back.
    Times       saved in UTC; shown in the local time of the city (India: IST, Dubai: GST, ...).
Version 5 (the whole world)
    Any town or village of the world (geo.py) can be the start or the end: buses inside a country (and between
    some neighbours), trains in the countries that have them (not in Rwanda), flights between 3 214 airports.
    A place without a station / an airport gets the nearest one. Flights without a direct service get
    connections with one change (two linked bookings, paid together); trains that are full have a waiting list
    (confirmed automatically when seats come back, all the money back if not confirmed at chart time); a return
    trip gets the convenience fee for free.
Demo
    The operators are fictional. A new trip starts with some seats already taken by "other travellers"
    (booking_id 0), so the seat map looks real; YO_TICKET_FILL=0 switches this off.
"""

import hashlib
import hmac
import json
import random
import secrets
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import geo
import places
import world
from core import ShopError, now

try:
    from zoneinfo import ZoneInfo
except ImportError:                                  # very old Python: fixed offsets below are used
    ZoneInfo = None

MODES = {"bus": "Bus", "train": "Train", "flight": "Flight"}
MODE_ICONS = {"bus": "🚌", "train": "🚆", "flight": "✈️"}

# code, name, mode, rating (all fictional companies)
OPERATORS = [
    ("SRW", "Saffron Roadways", "bus", 4.3), ("NOS", "NightOwl Sleepers", "bus", 4.1),
    ("GHT", "Green Hills Travels", "bus", 4.4), ("NSR", "Northern Star Rail", "train", 4.2),
    ("PRL", "Peninsula Rail", "train", 4.0), ("SMA", "SkyMango Air", "flight", 4.2),
    ("LTW", "Lotus Wings", "flight", 4.4), ("CLI", "Cloudline International", "flight", 4.5),
    # version 5: the rest of the world (fictional too)
    ("CCB", "Continental Coaches", "bus", 4.1), ("INZ", "Inzira Coaches", "bus", 4.3),
    ("VCL", "Volga Coachlines", "bus", 4.0), ("MRL", "Meridian Rail", "train", 4.2),
    ("TGR", "Taiga Rail", "train", 4.1), ("NMB", "Nimbus Air", "flight", 4.3), ("SVS", "Savanna Sky", "flight", 4.2),
]
FLIGHT_PREFIX = {"SMA": "SM", "LTW": "LW", "CLI": "CL", "NMB": "NM", "SVS": "SV"}

CLASS_NAMES = {
    "seater": "AC Seater (2+2)", "sleeper": "AC Sleeper (2+1)",                                   # bus
    "CC": "AC Chair Car", "EC": "Executive Chair Car", "SL": "Sleeper", "3A": "AC 3 Tier", "2A": "AC 2 Tier",  # train
    "Y": "Economy", "J": "Business",                                                             # flight
}
WORLD_CLASS_NAMES = {"CC": "Second class", "EC": "First class", "SL": "Open sleeper",
                     "2A": "Sleeper compartment (4 berths)", "3A": "Sleeper (6 berths)", "seater": "Coach seat",
                     "sleeper": "Coach bed"}
COACHES = {"SL": ["S1", "S2", "S3"], "3A": ["B1", "B2"], "2A": ["A1"], "CC": ["C1", "C2", "C3"], "EC": ["E1"]}

# class: (paise per km, minimum or base fare in paise, GST %)
FARES = {
    "seater": (150, 99_00, 5), "sleeper": (220, 299_00, 5),
    "CC": (115, 245_00, 5), "EC": (230, 520_00, 5), "SL": (48, 145_00, 0), "3A": (125, 495_00, 5),
    "2A": (180, 760_00, 5),
    "Y": (450, 1800_00, 5), "J": (1250, 6500_00, 12),
}
INTL_FARES = {"Y": (400, 5200_00, 5), "J": (1300, 16000_00, 12)}       # flights abroad
CONVENIENCE_FEE = 20_00                                              # per passenger, not refunded
AIRPORT_FEE = {"domestic": 450_00, "international": 1250_00}        # per passenger
SEAT_FEES = {"front": 350_00, "legroom": 450_00, "window_aisle": 150_00}
DEMAND_MAX = 0.30                                                    # bus and flight fares rise up to +30 % when full
MAX_PASSENGERS = 6
HOLD_MINUTES = 10
MAX_HOLDS = 2                                                        # held bookings a person can have at once
CLOSE_MINUTES = {"bus": 10, "train": 20, "flight": 60}               # booking closes before departure
BOARDING_FROM_HOURS = 3                                              # the conductor can check tickets from then
SEARCH_DAYS = 120

# hours before departure -> % of the refundable amount kept as the cancellation charge
REFUND_RULES = {
    "bus": [(24, 10), (12, 25), (4, 50), (0, 100)],
    "train": [(48, 5), (12, 25), (4, 50), (0, 100)],
    "flight": [(72, 15), (24, 30), (3, 60), (0, 100)],
}

HELD, CONFIRMED, CANCELLED, EXPIRED, COMPLETED = "Held", "Confirmed", "Cancelled", "Expired", "Completed"
WAITLISTED = "Waitlisted"
WAITLIST_MAX = 40                     # waiting-list bookings per train class
CHART_HOURS = 4                       # the chart is made 4 hours before departure: the waiting list ends
CONNECT_MIN = {"same": 60, "intl": 90}   # minutes to change planes
CONNECT_MAX_H = 12
TRIP_STEPS = ["Booked", "Boarding", "On the way", "Arrived"]

# airports (IATA codes) and towns without a railway station
AIRPORTS = {
    "Chandigarh": "IXC", "New Delhi": "DEL", "Mumbai": "BOM", "Bengaluru": "BLR", "Chennai": "MAA",
    "Kolkata": "CCU", "Hyderabad": "HYD", "Ahmedabad": "AMD", "Pune": "PNQ", "Jaipur": "JAI", "Lucknow": "LKO",
    "Dehradun": "DED", "Dubai": "DXB", "Moscow": "SVO", "Kigali": "KGL", "Paris": "CDG", "Madrid": "MAD",
    "Beijing": "PEK", "London": "LHR", "New York": "JFK", "Kathmandu": "KTM", "Singapore": "SIN",
    "Berlin": "BER", "Nairobi": "NBO",
}
GATEWAYS = {"New Delhi": None, "Mumbai": None, "Bengaluru": {"Dubai", "Singapore", "London"},
            "Chandigarh": {"Dubai"}}                                 # Indian airports with flights abroad (None = all)
NO_STATION = {"Mullana", "Naraingarh", "Pehowa", "Zirakpur", "Noida"}
# countries without passenger trains (version 5; a short list of the clear cases)
NO_RAIL = {"RW", "BI", "IS", "CY", "MT", "BT", "NE", "TD", "LY", "YE", "KW", "QA", "BH", "OM", "SO", "CF", "GW",
           "HT", "PG", "SB", "MV", "BN", "TL", "LB", "SL", "GM", "SS", "AD", "SM", "MC", "BZ", "GY", "SR", "AG", "BS",
           "BB", "DM", "GD", "KN", "LC", "VC", "WS", "TO", "VU", "KI", "MH", "FM", "NR", "PW", "TV", "SC", "CV", "KM",
           "ST", "FJ", "AF"}
# buses also cross these borders (neighbouring countries)
BUS_GROUPS = [{"RW", "UG", "KE", "TZ", "BI", "CD"}, {"RU", "BY", "KZ", "KG", "UZ", "AM", "GE"},
              {"IN", "NP", "BT", "BD"}, {"AE", "OM", "SA", "QA", "BH", "KW", "JO"}, {"US", "CA", "MX"},
              {"FR", "DE", "ES", "PT", "IT", "BE", "NL", "LU", "CH", "AT", "CZ", "PL", "SK", "HU", "SI", "HR", "DK",
               "SE", "NO", "FI", "EE", "LV", "LT", "RO", "BG", "GR", "RS", "BA", "ME", "MK", "AL", "GB", "IE"},
              {"AR", "BR", "CL", "UY", "PY", "BO", "PE", "EC", "CO"}, {"ZA", "BW", "NA", "ZW", "MZ", "LS", "SZ", "ZM"},
              {"SN", "GM", "ML", "CI", "GH", "TG", "BJ", "NG", "BF"}, {"TH", "LA", "KH", "VN", "MY", "SG"}]
AFRICA = {"DZ", "AO", "BJ", "BW", "BF", "BI", "CM", "CV", "CF", "TD", "KM", "CG", "CD", "CI", "DJ", "EG", "GQ", "ER",
          "SZ", "ET", "GA", "GM", "GH", "GN", "GW", "KE", "LS", "LR", "LY", "MG", "MW", "ML", "MR", "MU", "MA", "MZ",
          "NA", "NE", "NG", "RW", "ST", "SN", "SC", "SL", "SO", "ZA", "SS", "SD", "TZ", "TG", "TN", "UG", "ZM", "ZW"}
# the big transfer airports for flights with one change
WORLD_HUBS = ["DXB", "DOH", "IST", "SVO", "FRA", "CDG", "LHR", "AMS", "ADD", "NBO", "JNB", "SIN", "BKK", "HKG",
              "PEK", "DEL", "BOM", "JFK", "ORD", "GRU", "SYD", "CAI"]
LONG_HAUL_KM = 4000                   # longer direct flights only to or from a big transfer airport
WORLD_TZ = {"AE": "Asia/Dubai", "RU": "Europe/Moscow", "RW": "Africa/Kigali", "FR": "Europe/Paris",
            "ES": "Europe/Madrid", "CN": "Asia/Shanghai", "GB": "Europe/London", "US": "America/New_York",
            "NP": "Asia/Kathmandu", "SG": "Asia/Singapore", "DE": "Europe/Berlin", "KE": "Africa/Nairobi"}
TZ_SHORT = {"Asia/Kolkata": "IST"}
_OFFSETS = {"Asia/Kolkata": 330, "Asia/Dubai": 240, "Europe/Moscow": 180, "Africa/Kigali": 120,
            "Europe/Paris": 60, "Europe/Madrid": 60, "Asia/Shanghai": 480, "Europe/London": 0,
            "America/New_York": -300, "Asia/Kathmandu": 345, "Asia/Singapore": 480, "Europe/Berlin": 60,
            "Africa/Nairobi": 180}                                    # only if the time-zone database is missing

POPULAR_ROUTES = [
    ("bus", "Ambala Cantt", "New Delhi"), ("bus", "Chandigarh", "Dehradun"), ("bus", "Kurukshetra", "Chandigarh"),
    ("bus", "Yamunanagar", "Chandigarh"), ("train", "Ambala Cantt", "New Delhi"), ("train", "Kalka", "New Delhi"),
    ("train", "Chandigarh", "Mumbai"), ("flight", "Chandigarh", "Mumbai"), ("flight", "New Delhi", "Dubai"),
    ("flight", "New Delhi", "Moscow"), ("flight", "Mumbai", "Kigali"), ("flight", "New Delhi", "London"),
]

TICKETS_SCHEMA = """
CREATE TABLE IF NOT EXISTS ticket_operators (
    id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL UNIQUE, name TEXT NOT NULL, mode TEXT NOT NULL,
    rating REAL NOT NULL DEFAULT 4.0, active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS ticket_services (
    id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL UNIQUE, number TEXT NOT NULL, name TEXT NOT NULL,
    mode TEXT NOT NULL, operator_id INTEGER NOT NULL REFERENCES ticket_operators(id),
    from_city TEXT NOT NULL, to_city TEXT NOT NULL, depart_local TEXT NOT NULL, duration_min INTEGER NOT NULL,
    days TEXT NOT NULL DEFAULT '1111111', classes TEXT NOT NULL, distance_km REAL NOT NULL,
    active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ticket_services_route ON ticket_services(mode, from_city, to_city);
CREATE TABLE IF NOT EXISTS ticket_trips (
    id INTEGER PRIMARY KEY AUTOINCREMENT, service_id INTEGER NOT NULL REFERENCES ticket_services(id),
    day TEXT NOT NULL, depart_utc TEXT NOT NULL, arrive_utc TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Scheduled', delay_min INTEGER NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL, UNIQUE (service_id, day)
);
CREATE TABLE IF NOT EXISTS ticket_bookings (
    id INTEGER PRIMARY KEY AUTOINCREMENT, pnr TEXT NOT NULL UNIQUE, user_id INTEGER NOT NULL REFERENCES users(id),
    trip_id INTEGER NOT NULL REFERENCES ticket_trips(id), cls TEXT NOT NULL, status TEXT NOT NULL,
    fare_total INTEGER NOT NULL, seat_fees INTEGER NOT NULL DEFAULT 0, airport_fees INTEGER NOT NULL DEFAULT 0,
    conv_fee INTEGER NOT NULL DEFAULT 0, tax INTEGER NOT NULL DEFAULT 0, total INTEGER NOT NULL,
    lines TEXT NOT NULL, contact_mobile TEXT NOT NULL DEFAULT '', payment TEXT NOT NULL DEFAULT '',
    card_last4 TEXT NOT NULL DEFAULT '', emi_months INTEGER NOT NULL DEFAULT 0, emi_monthly INTEGER NOT NULL DEFAULT 0,
    refund INTEGER NOT NULL DEFAULT 0, charge INTEGER NOT NULL DEFAULT 0, cancel_reason TEXT NOT NULL DEFAULT '',
    hold_until TEXT NOT NULL, created_at TEXT NOT NULL, paid_at TEXT NOT NULL DEFAULT '',
    cancelled_at TEXT NOT NULL DEFAULT '', reminded INTEGER NOT NULL DEFAULT 0,
    wl INTEGER NOT NULL DEFAULT 0, link_pnr TEXT NOT NULL DEFAULT '', return_of TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_ticket_bookings_user ON ticket_bookings(user_id);
CREATE INDEX IF NOT EXISTS idx_ticket_bookings_trip ON ticket_bookings(trip_id);
CREATE TABLE IF NOT EXISTS ticket_passengers (
    id INTEGER PRIMARY KEY AUTOINCREMENT, booking_id INTEGER NOT NULL REFERENCES ticket_bookings(id),
    seat TEXT NOT NULL, name TEXT NOT NULL DEFAULT '', age INTEGER NOT NULL DEFAULT 0,
    gender TEXT NOT NULL DEFAULT '', fare INTEGER NOT NULL, boarded_at TEXT NOT NULL DEFAULT '', boarded_by INTEGER
);
CREATE INDEX IF NOT EXISTS idx_ticket_passengers ON ticket_passengers(booking_id);
CREATE TABLE IF NOT EXISTS ticket_seats (
    id INTEGER PRIMARY KEY AUTOINCREMENT, trip_id INTEGER NOT NULL REFERENCES ticket_trips(id),
    seat TEXT NOT NULL, booking_id INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, UNIQUE (trip_id, seat)
);
CREATE TABLE IF NOT EXISTS ticket_staff (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL UNIQUE REFERENCES users(id),
    operator_id INTEGER NOT NULL REFERENCES ticket_operators(id), created_at TEXT NOT NULL
);
"""


# =====================================================================
# time
# =====================================================================

def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _fmt(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _dt(text):
    return datetime.strptime(str(text)[:19], "%Y-%m-%d %H:%M:%S")


def _at(at):
    """`at` (for tests and the demo data): a UTC datetime or 'YYYY-MM-DD HH:MM:SS'; None = now."""
    if at is None:
        return utc_now()
    return at if isinstance(at, datetime) else _dt(at)


def _when(at):
    """The time written in the database: now, or the chosen time of the demo data / tests."""
    return now() if at is None else _fmt(_at(at))


def to_local(utc_dt, tz):
    try:
        return utc_dt.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(tz)).replace(tzinfo=None)
    except Exception:
        return utc_dt + timedelta(minutes=_OFFSETS.get(tz, 0))


def to_utc(local_dt, tz):
    try:
        return local_dt.replace(tzinfo=ZoneInfo(tz)).astimezone(timezone.utc).replace(tzinfo=None)
    except Exception:
        return local_dt - timedelta(minutes=_OFFSETS.get(tz, 0))


def tz_label(tz):
    if tz in TZ_SHORT:
        return TZ_SHORT[tz]
    off = _OFFSETS.get(tz, 0)
    try:
        delta = datetime.now(ZoneInfo(tz)).utcoffset()
        off = int(delta.total_seconds() // 60)
    except Exception:
        pass
    sign = "+" if off >= 0 else "-"
    return f"UTC{sign}{abs(off) // 60}" + (f":{abs(off) % 60:02d}" if off % 60 else "")


def duration_text(minutes):
    h, m = divmod(int(minutes), 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


# =====================================================================
# places
# =====================================================================

AIRPORT_PLACE = {code: name for name, code in AIRPORTS.items()}       # IATA -> one of the 45 old places


def _legacy(name):
    key = " ".join(str(name or "").split()).lower()
    for n, c in places.CITIES.items():
        if n.lower() == key:
            return {"name": n, "key": n, "lat": c[2], "lng": c[3], "country": "IN", "tz": "Asia/Kolkata",
                    "airport": AIRPORTS.get(n), "station": n not in NO_STATION, "state": c[0], "legacy": True,
                    "kind": "hub", "pop": 10 ** 6}
    for cc, (n, lat, lng) in places.WORLD_CITIES.items():
        if n.lower() == key:
            return {"name": n, "key": n, "lat": lat, "lng": lng, "country": cc, "tz": WORLD_TZ[cc],
                    "airport": AIRPORTS.get(n), "station": cc not in NO_RAIL, "state": "", "legacy": True, "kind": "hub",
                    "pop": 10 ** 6}
    return None


def _country_tz(cc):
    c = geo.country(cc)
    return c["zones"][0] if c and c.get("zones") else "UTC"


def _airport_place(code):
    """An airport as a place of Yo Tickets: one of the old places, or "air:KJA"."""
    code = str(code or "").upper()
    if code in AIRPORT_PLACE:
        return _legacy(AIRPORT_PLACE[code])
    a = geo.airport(code)
    if a is None:
        return None
    return {"name": a["city"] or a["name"], "key": "air:" + code, "lat": a["lat"], "lng": a["lng"],
            "country": a["cc"], "tz": a["tz"] or _country_tz(a["cc"]), "airport": code, "station": False,
            "state": "", "legacy": False, "kind": "airport", "pop": 0, "airport_name": a["name"],
            "size": a["size"]}


@lru_cache(maxsize=8192)
def _place(text):
    p = _legacy(text)
    if p:
        return p
    if text.lower().startswith("air:"):
        return _airport_place(text[4:])
    g = geo.get(text) or geo.find(text)
    if g is None:
        return None
    if g["id"] in places.CITIES or any(c[0] == g["id"] for c in places.WORLD_CITIES.values()):
        return _legacy(g["id"])
    rail = g["cc"] not in NO_RAIL and (g["kind"] in ("city", "hub") or (g["pop"] or 0) >= 20000)
    own = None
    if g["kind"] in ("city", "hub") or (g["pop"] or 0) >= 50000:
        near = geo.nearest_airports(g["lat"], g["lng"], within_km=35, limit=1)
        own = near[0]["iata"] if near else None
    return {"name": g["name"], "key": g["id"], "lat": g["lat"], "lng": g["lng"], "country": g["cc"],
            "tz": g["tz"] or _country_tz(g["cc"]), "airport": own, "station": rail, "state": g["region"],
            "legacy": False, "kind": g["kind"], "pop": g["pop"] or 0, "label": g["label"]}


def place(name):
    """A place of Yo Tickets: one of the 45 places of version 4 (by name), an airport ("air:KJA"), or any town or
    village of the world (a geo id like "g1502026", or a name). Returns a new dict, or None."""
    text = " ".join(str(name or "").split())
    if not text:
        return None
    p = _place(text)
    return dict(p) if p else None


def place_name(key):
    p = place(key)
    return p["name"] if p else str(key)


def place_names(mode=None):
    india = sorted(places.CITIES)
    world_names = sorted(c[0] for c in places.WORLD_CITIES.values())
    return india if mode in ("bus", "train") else india + world_names


def slug(name):
    return "-".join(str(name).lower().split())


def place_by_slug(text):
    """The place key of a route page address ("new-delhi", "g1502026" or "air-kja")."""
    for n in place_names():
        if slug(n) == str(text).lower():
            return n
    text = str(text).lower()
    if text.startswith("air-"):
        return "air:" + text[4:].upper() if geo.airport(text[4:]) else None
    if geo.ID_RE.fullmatch(text) and geo.get(text):
        return text
    p = geo.find(text.replace("-", " "))
    return p["id"] if p else None


def route_slug(p):
    """The part of a route page address for one place."""
    key = p["key"]
    if key.startswith("air:"):
        return "air-" + key[4:].lower()
    return slug(p["name"]) if p.get("legacy") else key


def _km(a, b):
    return places.haversine_km((a["lat"], a["lng"]), (b["lat"], b["lng"]))


def nearest(p, test, limit_km):
    best = None
    for n in places.CITIES:
        q = place(n)
        if test(q):
            d = _km(p, q)
            if d <= limit_km and (best is None or d < best[1]):
                best = (q, d)
    return best


def nearest_station(p, limit_km=60):
    """(place, km) of the nearest town with a railway station (the 33 Yo cities first in India)."""
    best = nearest(p, lambda q: q["station"], limit_km) if p["country"] == "IN" else None
    if best or p.get("legacy"):
        return best
    for g in geo.nearby(p["lat"], p["lng"], km=limit_km, limit=40, min_rank=8):
        q = place(g["id"])
        if q and q["station"] and q["country"] == p["country"] and q["key"] != p["key"]:
            d = _km(p, q)
            if d <= limit_km and (best is None or d < best[1]):
                best = (q, d)
    return best


def to_airport(p, limit_km=160):
    """(airport place, note) for a flight from / to p - or (None, None) when there is no airport near."""
    if p["key"].startswith("air:") or (p.get("legacy") and p["airport"]):
        return p, None
    if p["airport"]:                                        # a big town with its own airport
        return _airport_place(p["airport"]), None
    if p.get("legacy"):
        near = nearest(p, lambda q: bool(q["airport"]), limit_km)
        if near:
            return near[0], (f"The nearest airport to {p['name']} is {near[0]['name']} ({near[0]['airport']}), "
                             f"{round(near[1] * places.ROAD_FACTOR)} km away.")
    for a in geo.nearest_airports(p["lat"], p["lng"], within_km=limit_km, limit=3):
        ap = _airport_place(a["iata"])
        if ap:
            km = _km(p, ap)
            return ap, (f"The nearest airport to {p['name']} is {ap['name']} ({ap['airport']}), "
                        f"{round(km * places.ROAD_FACTOR)} km away.")
    return None, None


def _size(p):
    if p.get("legacy"):
        return "large"
    a = geo.airport(p["airport"]) if p.get("airport") else None
    return a["size"] if a else "small"


def _is_hub(p):
    return p.get("airport") in WORLD_HUBS or p.get("airport") == country_hub(p["country"])


def _long_ok(far, km):
    """A flight from an Indian gateway to an airport abroad: big airports, and far ones only if they are hubs."""
    return _size(far) == "large" and (km <= LONG_HAUL_KM or _is_hub(far))


def _bus_neighbours(a, b):
    return any(a["country"] in g and b["country"] in g for g in BUS_GROUPS)


class NoDirect(ShopError):
    """No direct flight on this route - a connection with one change may be possible."""


def flight_ok(a, b):
    """Raise when there is no direct flight between two airports (a, b: airport places)."""
    km = _km(a, b)
    if a["key"] == b["key"]:
        raise ShopError("Both cities use the same airport. Please choose a bus or a train.")
    if a["country"] == b["country"]:
        if km < 200:
            raise ShopError("No flights on this short route. Please choose a bus or a train.")
        return
    if "IN" in (a["country"], b["country"]):
        home, far = (a, b) if a["country"] == "IN" else (b, a)
        gw = GATEWAYS.get(home["name"], False) if home.get("legacy") else False
        if gw is False or (gw is not None and far["name"] not in gw) or (gw is None and not _long_ok(far, km)):
            raise NoDirect(f"No direct flights between {a['name']} and {b['name']}. "
                           f"Please fly from New Delhi or Mumbai.")
        return
    sizes = {_size(a), _size(b)}
    world_hub = a.get("airport") in WORLD_HUBS or b.get("airport") in WORLD_HUBS
    if sizes == {"large"} and km <= 13000 and (km <= LONG_HAUL_KM or world_hub or (_is_hub(a) and _is_hub(b))):
        return
    if sizes == {"large", "medium"} and km <= 3000:
        return
    raise NoDirect(f"No direct flights between {a['name']} and {b['name']}.")


def check_route(mode, from_name, to_name):
    """(from place, to place, notes) for this mode - or a clear message why it is not possible.
    Flights: raises NoDirect when only a connection with one change is possible."""
    if mode not in MODES:
        raise ShopError("Please choose bus, train or flight.")
    a, b = place(from_name), place(to_name)
    if a is None or b is None:
        raise ShopError("Please choose the cities from the list.")
    if a["key"] == b["key"]:
        raise ShopError("The two cities must be different.")
    notes = []
    if mode in ("bus", "train") and a["country"] != b["country"] and not (mode == "bus" and _bus_neighbours(a, b)):
        raise ShopError("Buses and trains run inside one country (buses also between some neighbouring countries). "
                        "For other countries, please choose a flight.")
    if mode == "bus":
        km = places.road_km((a["lat"], a["lng"]), (b["lat"], b["lng"]))
        if km < 25:
            raise ShopError("These towns are too close for an intercity bus.")
        if km > 1100:
            if km > 2600:
                raise ShopError("No buses on this route (too far). Please try a train or a flight.")
            e = NoDirect("No direct bus on this route (too far for one bus).")   # version 6: a bus with one change
            e.places, e.notes, e.mode = (a, b), notes, "bus"
            raise e
    elif mode == "train":
        for p in (a, b):
            if p["country"] in NO_RAIL:
                raise ShopError(f"There are no passenger trains in {world.country_name(p['country'])}. "
                                f"Please choose a bus or a flight.")
        for p in (a, b):
            if not p["station"]:
                near = nearest_station(p)
                raise ShopError(f"{p['name']} has no railway station. The nearest one is "
                                f"{near[0]['name'] if near else 'not in our list'}.")
        if _km(a, b) < 30:
            raise ShopError("These towns are too close for a train ticket.")
    else:
        fixed = []
        for p in (a, b):
            ap, note = to_airport(p)
            if ap is None:
                raise ShopError(f"There is no airport near {p['name']}.")
            if note:
                notes.append(note)
            fixed.append(ap)
        a, b = fixed
        if a["key"] == b["key"]:
            raise ShopError("Both cities use the same airport. Please choose a bus or a train.")
        try:
            flight_ok(a, b)
        except NoDirect as e:
            e.places, e.notes = (a, b), notes              # the search offers a connection
            raise
    return a, b, notes


@lru_cache(maxsize=512)
def country_hub(cc):
    """The main airport of a country (version 4 places first, else the "large" airport of its biggest town)."""
    for name, code in AIRPORTS.items():
        p = _legacy(name)
        if p and p["country"] == cc and (cc != "IN" or code in ("DEL", "BOM")):
            return code
    best = None
    for a in geo.airports():
        if a["cc"] == cc and a["size"] == "large":
            town = geo.nearest_place(a["lat"], a["lng"])
            pop = (town or {}).get("pop") or 0
            if best is None or pop > best[0]:
                best = (pop, a["iata"])
    return best[1] if best else None


def ground_hubs(mode, a, b, limit=3):
    """Towns where a traveller can change buses or trains between a and b (version 6): big towns near the way,
    in the country of a or b, from where both trips are possible."""
    direct = _km(a, b)
    pad = 1.5
    box = geo.in_box(min(a["lat"], b["lat"]) - pad, max(a["lat"], b["lat"]) + pad,
                     min(a["lng"], b["lng"]) - pad, max(a["lng"], b["lng"]) + pad, limit=40, min_rank=5)
    out, seen = [], {a["key"], b["key"]}
    for p in box:
        if p["cc"] not in (a["country"], b["country"]) or (p["pop"] or 0) < 100_000:
            continue
        h = place(p["id"])
        if h is None or h["key"] in seen:
            continue
        seen.add(h["key"])
        detour = (_km(a, h) + _km(h, b)) / max(direct, 1)
        if detour > 1.35:
            continue
        try:
            check_route(mode, a["key"], h["key"])
            check_route(mode, h["key"], b["key"])
        except ShopError:                                 # also NoDirect: only one change
            continue
        out.append((detour, -(p["pop"] or 0), h))
    out.sort(key=lambda x: (round(x[0], 1), x[1]))
    return [h for _, _, h in out[:limit]]


def connection_hubs(a, b, limit=3):
    """Airports where a traveller can change planes between a and b (both flights direct, small detour)."""
    direct_km = _km(a, b)
    out, seen = [], {a["key"], b["key"]}
    for code in [country_hub(a["country"]), country_hub(b["country"])] + WORLD_HUBS:
        h = _airport_place(code) if code else None
        if h is None or h["key"] in seen:
            continue
        seen.add(h["key"])
        detour = (_km(a, h) + _km(h, b)) / max(direct_km, 1)
        if detour > 1.7:
            continue
        try:
            flight_ok(a, h)
            flight_ok(h, b)
        except ShopError:
            continue
        out.append((detour, h))
    out.sort(key=lambda x: x[0])
    return [h for _, h in out[:limit]]


# =====================================================================
# timetable (always the same for the same route)
# =====================================================================

def _rng(*parts):
    return random.Random(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest())


def _time(minutes):
    minutes = int(minutes) % (24 * 60)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def timetable(mode, from_name, to_name):
    """The services of one route: [{code, number, name, operator, depart, duration, days, classes, km}]."""
    a, b = place(from_name), place(to_name)
    if not (a["country"] == "IN" and b["country"] == "IN") and not (
            mode == "flight" and "IN" in (a["country"], b["country"])):
        return _world_timetable(mode, a, b)
    rng = _rng("yo-tickets", mode, a["key"], b["key"])
    out = []
    if mode == "bus":
        km = places.road_km((a["lat"], a["lng"]), (b["lat"], b["lng"]))
        count = 6 if km < 180 else 5 if km < 400 else 4
        night = km >= 280
        for i in range(count):
            sleeper = night and i >= 1
            depart = rng.randrange(19 * 60, 23 * 60 + 46, 15) if sleeper else rng.randrange(5 * 60, 17 * 60 + 1, 15)
            op = rng.choice(["SRW", "NOS", "GHT"] if sleeper else ["SRW", "GHT"])
            cls = "sleeper" if sleeper else "seater"
            out.append({"number": f"{op} {rng.randint(101, 989)}", "operator": op, "depart": _time(depart),
                        "name": "Multi-axle AC Sleeper (2+1)" if sleeper else "AC Seater Push-back (2+2)",
                        "duration": int(round((km / 50 * 60 + rng.randint(0, 25)) / 5) * 5),
                        "days": "1111111", "classes": [cls], "km": km})
    elif mode == "train":
        km = round(places.haversine_km((a["lat"], a["lng"]), (b["lat"], b["lng"])) * 1.25, 1)
        short = km < 400
        count = rng.randint(2, 4) if short else rng.randint(1, 3)
        kinds = ["Intercity", "Chair Car Express", "Shuttle"] if short else ["Superfast Express", "Express", "Mail"]
        for i in range(count):
            op = "NSR" if a["lat"] > 24 or b["lat"] > 24 else "PRL"
            if not short and rng.random() < 0.3:
                op = "PRL"
            days = "1111111"
            if km > 1300:
                run = sorted(rng.sample(range(7), rng.randint(3, 5)))
                days = "".join("1" if d in run else "0" for d in range(7))
            speed = 75 if short else 62
            out.append({"number": str(rng.randint(12001, 22999)), "operator": op,
                        "name": f"{a['name']} - {b['name']} {rng.choice(kinds)}",
                        "depart": _time(rng.randrange(4 * 60, 23 * 60 + 1, 5)),
                        "duration": int(round((km / speed * 60 + rng.randint(5, 40)) / 5) * 5),
                        "days": days, "classes": ["CC", "EC"] if short else ["SL", "3A", "2A"], "km": km})
    else:
        km = round(places.haversine_km((a["lat"], a["lng"]), (b["lat"], b["lng"])), 1)
        intl = a["country"] != "IN" or b["country"] != "IN"
        count = rng.randint(1, 2) if intl else rng.randint(2, 5)
        for i in range(count):
            op = "CLI" if intl and (i == 0 or rng.random() < 0.5) else rng.choice(["SMA", "LTW"])
            number = f"{FLIGHT_PREFIX[op]} {rng.randint(1000, 1999) if intl else rng.randint(101, 989)}"
            out.append({"number": number, "operator": op, "name": "Narrow-body jet" if not intl else "Wide-body jet",
                        "depart": _time(rng.randrange(5 * 60 + 30, 23 * 60 + 1, 5)),
                        "duration": int(round((35 + km / 13) / 5) * 5), "days": "1111111", "classes": ["Y", "J"],
                        "km": km})
    for i, s in enumerate(out):
        s["code"] = f"{mode}:{a['key']}>{b['key']}:{i}"
    return out


def _world_timetable(mode, a, b):
    """Version 5: the services of a route outside India (the same idea, the companies of that part of the world)."""
    rng = _rng("yo-tickets-world", mode, a["key"], b["key"])
    ccs = {a["country"], b["country"]}
    out = []
    if mode == "bus":
        km = places.road_km((a["lat"], a["lng"]), (b["lat"], b["lng"]))
        ops = ["INZ", "CCB"] if ccs <= BUS_GROUPS[0] else ["VCL", "CCB"] if ccs <= BUS_GROUPS[1] else ["CCB"]
        count = 5 if km < 180 else 4 if km < 400 else 3
        night = km >= 280
        for i in range(count):
            sleeper = night and i >= 1
            depart = rng.randrange(19 * 60, 23 * 60 + 46, 15) if sleeper else rng.randrange(5 * 60, 17 * 60 + 1, 15)
            op = rng.choice(ops)
            out.append({"number": f"{op} {rng.randint(101, 989)}", "operator": op, "depart": _time(depart),
                        "name": "Night coach with beds (2+1)" if sleeper else "Coach with reclining seats (2+2)",
                        "duration": int(round((km / 55 * 60 + rng.randint(0, 25)) / 5) * 5),
                        "days": "1111111", "classes": ["sleeper" if sleeper else "seater"], "km": km})
    elif mode == "train":
        km = round(places.haversine_km((a["lat"], a["lng"]), (b["lat"], b["lng"])) * 1.25, 1)
        short = km < 400
        op = "TGR" if ccs == {"RU"} and rng.random() < 0.6 else "MRL"
        count = rng.randint(2, 4) if short else rng.randint(1, 2)
        kinds = ["Regional Express", "InterCity"] if short else ["Night Express", "Long-distance Express"]
        for i in range(count):
            days = "1111111"
            if km > 1300:
                run = sorted(rng.sample(range(7), rng.randint(2, 5)))
                days = "".join("1" if d in run else "0" for d in range(7))
            speed = 80 if short else 65
            out.append({"number": str(rng.randint(100, 999)), "operator": op,
                        "name": f"{a['name']} - {b['name']} {rng.choice(kinds)}",
                        "depart": _time(rng.randrange(5 * 60, 23 * 60 + 1, 5)),
                        "duration": int(round((km / speed * 60 + rng.randint(5, 40)) / 5) * 5),
                        "days": days, "classes": ["CC", "EC"] if short else ["SL", "2A"], "km": km})
    else:
        km = round(places.haversine_km((a["lat"], a["lng"]), (b["lat"], b["lng"])), 1)
        intl = a["country"] != b["country"]
        ops = ["SVS", "NMB"] if ccs <= AFRICA else ["NMB"]
        count = rng.randint(1, 2) if intl or "medium" in (_size(a), _size(b)) else rng.randint(2, 4)
        for i in range(count):
            op = rng.choice(ops)
            number = f"{FLIGHT_PREFIX[op]} {rng.randint(1000, 1999) if intl else rng.randint(101, 989)}"
            out.append({"number": number, "operator": op, "name": "Wide-body jet" if km > 4000 else "Narrow-body jet",
                        "depart": _time(rng.randrange(5 * 60 + 30, 23 * 60 + 1, 5)),
                        "duration": int(round((35 + km / 13) / 5) * 5), "days": "1111111", "classes": ["Y", "J"],
                        "km": km})
    for i, s in enumerate(out):
        s["code"] = f"{mode}:{a['key']}>{b['key']}:{i}"
    return out


# =====================================================================
# seat maps
# =====================================================================

def _seat(code, label, kind, cls, fee=0, tag=""):
    return {"code": code, "label": label, "kind": kind, "cls": cls, "fee": fee, "tag": tag}


@lru_cache(maxsize=64)
def _sections(mode, classes):
    """The seat map of a service: [{name, cls, cols, rows: [[seat or None]]}] (None = aisle or gap)."""
    out = []
    if mode == "bus":
        if classes[0] == "seater":
            rows = []
            for r in range(10):
                n = r * 4
                rows.append([_seat(str(n + 1), str(n + 1), "Window", "seater"),
                             _seat(str(n + 2), str(n + 2), "Aisle", "seater"), None,
                             _seat(str(n + 3), str(n + 3), "Aisle", "seater"),
                             _seat(str(n + 4), str(n + 4), "Window", "seater")])
            out.append({"name": "Seats", "cls": "seater", "cols": 5, "rows": rows})
        else:
            for deck, letter in (("Lower deck", "L"), ("Upper deck", "U")):
                rows = []
                for r in range(5):
                    n = r * 3
                    rows.append([_seat(f"{letter}{n + 1}", f"{letter}{n + 1}", "Single", "sleeper"), None,
                                 _seat(f"{letter}{n + 2}", f"{letter}{n + 2}", "Double (aisle)", "sleeper"),
                                 _seat(f"{letter}{n + 3}", f"{letter}{n + 3}", "Double (window)", "sleeper")])
                out.append({"name": deck, "cls": "sleeper", "cols": 4, "rows": rows})
    elif mode == "train":
        berth = {"LB": "Lower", "MB": "Middle", "UB": "Upper", "SL": "Side lower", "SU": "Side upper"}
        for cls in classes:
            for coach in COACHES[cls]:
                rows = []
                if cls in ("SL", "3A"):
                    order = ["LB", "MB", "UB", "LB", "MB", "UB", "SL", "SU"]
                    for bay in range(9 if cls == "SL" else 8):
                        row = []
                        for p, b in enumerate(order):
                            n = bay * 8 + p + 1
                            if p == 6:
                                row.append(None)
                            row.append(_seat(f"{coach}-{n}", str(n), berth[b], cls))
                        rows.append(row)
                    cols = 9
                elif cls == "2A":
                    order = ["LB", "UB", "LB", "UB", "SL", "SU"]
                    for bay in range(8):
                        row = []
                        for p, b in enumerate(order):
                            n = bay * 6 + p + 1
                            if p == 4:
                                row.append(None)
                            row.append(_seat(f"{coach}-{n}", str(n), berth[b], cls))
                        rows.append(row)
                    cols = 7
                elif cls == "CC":
                    kinds = ["Window", "Middle", "Aisle", None, "Aisle", "Window"]
                    n = 0
                    for r in range(15):
                        row = []
                        for k in kinds:
                            if k is None:
                                row.append(None)
                                continue
                            n += 1
                            row.append(_seat(f"{coach}-{n}", str(n), k, cls))
                        rows.append(row)
                    cols = 6
                else:                                             # EC 2+2
                    kinds = ["Window", "Aisle", None, "Aisle", "Window"]
                    n = 0
                    for r in range(14):
                        row = []
                        for k in kinds:
                            if k is None:
                                row.append(None)
                                continue
                            n += 1
                            row.append(_seat(f"{coach}-{n}", str(n), k, cls))
                        rows.append(row)
                    cols = 5
                out.append({"name": f"Coach {coach}", "coach": coach, "cls": cls, "cols": cols, "rows": rows})
    else:
        rows = []
        for r in range(1, 4):
            rows.append([_seat(f"{r}A", f"{r}A", "Window", "J"), _seat(f"{r}C", f"{r}C", "Aisle", "J"), None,
                         _seat(f"{r}D", f"{r}D", "Aisle", "J"), _seat(f"{r}F", f"{r}F", "Window", "J")])
        out.append({"name": "Business", "cls": "J", "cols": 5, "rows": rows})
        rows = []
        for r in range(4, 28):
            row = []
            for letter, kind in (("A", "Window"), ("B", "Middle"), ("C", "Aisle"), (None, None), ("D", "Aisle"),
                                 ("E", "Middle"), ("F", "Window")):
                if letter is None:
                    row.append(None)
                    continue
                fee, tag = 0, ""
                if r in (12, 13):
                    fee, tag = SEAT_FEES["legroom"], "Extra legroom"
                elif r in (4, 5):
                    fee, tag = SEAT_FEES["front"], "Front row"
                elif kind != "Middle":
                    fee = SEAT_FEES["window_aisle"]
                row.append(_seat(f"{r}{letter}", f"{r}{letter}", kind, "Y", fee, tag))
            rows.append(row)
        out.append({"name": "Economy", "cls": "Y", "cols": 7, "rows": rows})
    return out


def sections(mode, classes):
    return _sections(mode, tuple(classes))


@lru_cache(maxsize=64)
def _seat_index(mode, classes):
    return {s["code"]: s for sec in _sections(mode, classes) for row in sec["rows"] for s in row if s}


def seat_index(mode, classes):
    return _seat_index(mode, tuple(classes))


def capacity(mode, classes):
    out = {}
    for s in seat_index(mode, classes).values():
        out[s["cls"]] = out.get(s["cls"], 0) + 1
    return out


# =====================================================================
# money
# =====================================================================

def _rupee(paise):
    return int(round(paise / 100)) * 100


def _towns(pts):
    try:
        import courier
        return courier.towns_near(pts)
    except Exception:
        return [{"label": n, "lat": la, "lng": ln} for n, la, ln in places.cities_near(pts)]


def is_international(service):
    a, b = place(service["from_city"]), place(service["to_city"])
    return bool(a and b and a["country"] != b["country"])


def touches_india(service):
    a, b = place(service["from_city"]), place(service["to_city"])
    return bool(a and b and "IN" in (a["country"], b["country"]))


def class_name(cls, country="IN"):
    """Class names: Indian Railways names in India, simple names elsewhere."""
    if country != "IN" and cls in WORLD_CLASS_NAMES:
        return WORLD_CLASS_NAMES[cls]
    return CLASS_NAMES[cls]


def fare_for(service, cls, taken_share=0.0, depart=None, at=None):
    """Fare of one passenger (paise, whole rupees) before seat fees, fees and GST."""
    mode = service["mode"]
    per_km, base, _gst = (INTL_FARES if mode == "flight" and is_international(service) else FARES)[cls]
    km = float(service["distance_km"])
    fare = base + per_km * km if mode == "flight" else max(base, per_km * km)
    if mode in ("bus", "flight"):
        fare *= 1 + DEMAND_MAX * max(0.0, min(1.0, taken_share))
    if mode == "flight" and depart is not None:
        days = (depart - _at(at)).total_seconds() / 86400
        fare *= 1.2 if days < 3 else 1.1 if days < 7 else 1.0
    return _rupee(fare)


def gst_rate(service, cls):
    """Indian GST on tickets to, from or inside India; outside India the fare includes the local taxes."""
    if not touches_india(service):
        return 0
    return (INTL_FARES if service["mode"] == "flight" and is_international(service) else FARES)[cls][2]


def price_booking(service, cls, fare, seats, round_trip=False):
    """All the money of a booking. Every line is rounded to whole rupees.
    round_trip: the return part of a round trip - no convenience fee."""
    n = len(seats)
    fare_total = fare * n
    seat_fees = sum(s["fee"] for s in seats)
    tax = _rupee((fare_total + seat_fees) * gst_rate(service, cls) / 100)
    airport = 0
    if service["mode"] == "flight":
        airport = AIRPORT_FEE["international" if is_international(service) else "domestic"] * n
    conv = 0 if round_trip else CONVENIENCE_FEE * n
    lines = [["Fare", fare_total, n]]             # [label, amount, passengers]: shown as "Fare (2 × ₹650)"
    if seat_fees:
        lines.append(["Seat selection", seat_fees])
    if airport:
        lines.append(["Airport fees", airport])
    lines.append(["Convenience fee (round trip: free)" if round_trip else "Convenience fee", conv])
    if tax:
        lines.append([f"GST {gst_rate(service, cls)}%", tax])
    return {"fare_total": fare_total, "seat_fees": seat_fees, "airport_fees": airport, "conv_fee": conv, "tax": tax,
            "total": fare_total + seat_fees + airport + conv + tax, "lines": lines}


def refund_for(booking, mode, depart, at=None, operator_cancelled=False):
    """(charge %, charge, refund) if the traveller cancels now."""
    if operator_cancelled:
        return 0, 0, booking["total"]
    hours = (depart - _at(at)).total_seconds() / 3600
    pct = 100
    for limit, p in REFUND_RULES[mode]:
        if hours >= limit:
            pct = p
            break
    refundable = booking["total"] - booking["conv_fee"]
    charge = _rupee(refundable * pct / 100)
    return pct, charge, max(0, refundable - charge)


def _pnr():
    return "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(6))


def _clean(text, limit=80):
    return " ".join(str(text or "").split())[:limit]


# =====================================================================
# the mixin
# =====================================================================

class TicketsMixin:

    @property
    def ticket_fill(self):
        return str(self.env.get("YO_TICKET_FILL", "1")) != "0"

    def ensure_ticket_operators(self):
        with self.db() as conn:
            for code, name, mode, rating in OPERATORS:
                conn.execute("INSERT INTO ticket_operators (code, name, mode, rating) VALUES (?, ?, ?, ?) "
                             "ON CONFLICT (code) DO NOTHING", (code, name, mode, rating))

    def _operator_ids(self, conn):
        return {r["code"]: r["id"] for r in conn.execute("SELECT id, code FROM ticket_operators")}

    def ticket_operators(self):
        with self.db() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM ticket_operators ORDER BY mode, name")]

    # ---------- services and trips ----------

    def _services(self, conn, mode, a, b):
        real = conn.execute("SELECT 1 FROM ticket_services s JOIN ticket_operators o ON o.id = s.operator_id "
                            "WHERE s.mode = ? AND s.from_city = ? AND s.to_city = ? AND s.source = 'gtfs' "
                            "AND s.active = 1 AND o.active = 1 LIMIT 1", (mode, a["key"], b["key"])).fetchone()
        if real:                                  # version 6: a real timetable (GTFS) replaces the generated one
            return [self._service_dict(r) for r in conn.execute(
                "SELECT s.*, o.name AS operator, o.code AS operator_code, o.rating FROM ticket_services s "
                "JOIN ticket_operators o ON o.id = s.operator_id WHERE s.mode = ? AND s.from_city = ? AND s.to_city = ? "
                "AND s.source = 'gtfs' AND s.active = 1 AND o.active = 1 ORDER BY s.depart_local",
                (mode, a["key"], b["key"]))]
        ops = self._operator_ids(conn)
        for s in timetable(mode, a["key"], b["key"]):
            conn.execute("INSERT INTO ticket_services (code, number, name, mode, operator_id, from_city, to_city, "
                         "depart_local, duration_min, days, classes, distance_km, created_at) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (code) DO NOTHING",
                         (s["code"], s["number"], s["name"], mode, ops[s["operator"]], a["key"], b["key"], s["depart"],
                          s["duration"], s["days"], json.dumps(s["classes"]), s["km"], now()))
        return [self._service_dict(r) for r in conn.execute(
            "SELECT s.*, o.name AS operator, o.code AS operator_code, o.rating FROM ticket_services s "
            "JOIN ticket_operators o ON o.id = s.operator_id WHERE s.mode = ? AND s.from_city = ? AND s.to_city = ? "
            "AND s.active = 1 AND o.active = 1 ORDER BY s.depart_local", (mode, a["key"], b["key"]))]

    @staticmethod
    def _service_dict(r):
        d = dict(r)
        d["classes"] = json.loads(d["classes"])
        return d

    def _trip(self, conn, service, day):
        """The trip of a service on a date (made the first time; then some seats are taken by others)."""
        a, b = place(service["from_city"]), place(service["to_city"])
        hh, mm = (int(x) for x in service["depart_local"].split(":"))
        depart = to_utc(datetime.strptime(day, "%Y-%m-%d").replace(hour=hh, minute=mm), a["tz"])
        arrive = depart + timedelta(minutes=service["duration_min"])
        made = conn.execute("INSERT INTO ticket_trips (service_id, day, depart_utc, arrive_utc, created_at) "
                            "VALUES (?, ?, ?, ?, ?) ON CONFLICT (service_id, day) DO NOTHING",
                            (service["id"], day, _fmt(depart), _fmt(arrive), now())).rowcount
        trip = dict(conn.execute("SELECT * FROM ticket_trips WHERE service_id = ? AND day = ?",
                                 (service["id"], day)).fetchone())
        if made and self.ticket_fill:
            self._fill(conn, service, trip)
        return trip

    def _fill(self, conn, service, trip):
        """Demo: other travellers already have some seats (more on trips that leave soon)."""
        rng = _rng("fill", service["code"], trip["day"])
        days = max(0.0, (_dt(trip["depart_utc"]) - utc_now()).total_seconds() / 86400)
        share = min(0.85, rng.uniform(0.12, 0.45) + max(0.0, 0.3 - days * 0.02))
        codes = list(seat_index(service["mode"], service["classes"]))
        taken = rng.sample(codes, int(len(codes) * share))
        when = now()
        for code in taken:
            conn.execute("INSERT INTO ticket_seats (trip_id, seat, booking_id, created_at) VALUES (?, ?, 0, ?) "
                         "ON CONFLICT DO NOTHING", (trip["id"], code, when))

    def _taken(self, conn, trip_id):
        return {r[0] for r in conn.execute("SELECT seat FROM ticket_seats WHERE trip_id = ?", (trip_id,))}

    def _release_expired(self, conn, at=None):
        stamp = _fmt(_at(at))
        freed = 0
        trips = set()
        for r in conn.execute("SELECT id, trip_id FROM ticket_bookings WHERE status = ? AND hold_until < ?",
                              (HELD, stamp)).fetchall():
            if conn.execute("UPDATE ticket_bookings SET status = ? WHERE id = ? AND status = ?",
                            (EXPIRED, r[0], HELD)).rowcount:
                if conn.execute("DELETE FROM ticket_seats WHERE booking_id = ?", (r[0],)).rowcount:
                    trips.add(r[1])
                freed += 1
        for trip_id in trips:
            self._promote(conn, trip_id, at)
        return freed

    def _availability(self, service, taken):
        cap = capacity(service["mode"], service["classes"])
        idx = seat_index(service["mode"], service["classes"])
        used = {}
        for code in taken:
            s = idx.get(code)
            if s:
                used[s["cls"]] = used.get(s["cls"], 0) + 1
        return {c: (cap[c], cap[c] - used.get(c, 0)) for c in service["classes"]}

    def _trip_view(self, service, trip, taken, at):
        """What the search results and the seat page show about one trip."""
        a, b = place(service["from_city"]), place(service["to_city"])
        depart, arrive = _dt(trip["depart_utc"]), _dt(trip["arrive_utc"])
        dl, al = to_local(depart, a["tz"]), to_local(arrive, b["tz"])
        avail = self._availability(service, taken)
        total_cap = sum(c for c, _ in avail.values())
        share = 1 - sum(f for _, f in avail.values()) / total_cap if total_cap else 0
        classes = []
        for c in service["classes"]:
            fare = fare_for(service, c, share, depart, at)
            classes.append({"cls": c, "name": class_name(c, a["country"]), "seats": avail[c][1], "capacity": avail[c][0],
                            "fare": fare, "from": _rupee(fare * (1 + gst_rate(service, c) / 100)),
                            "waitlist": service["mode"] == "train" and avail[c][1] == 0})
        closes = depart - timedelta(minutes=CLOSE_MINUTES[service["mode"]])
        return {"trip_id": trip["id"], "service_id": service["id"], "mode": service["mode"], "number": service["number"],
                "name": service["name"], "operator": service["operator"], "operator_code": service["operator_code"],
                "rating": service["rating"], "from_city": a["name"], "to_city": b["name"], "from_key": a["key"],
                "to_key": b["key"], "from_country": a["country"], "to_country": b["country"],
                "from_code": a["airport"] if service["mode"] == "flight" else "",
                "to_code": b["airport"] if service["mode"] == "flight" else "",
                "depart_utc": trip["depart_utc"], "arrive_utc": trip["arrive_utc"], "day": trip["day"],
                "depart_local": _fmt(dl), "arrive_local": _fmt(al), "plus_days": (al.date() - dl.date()).days,
                "depart_time": dl.strftime("%H:%M"), "arrive_time": al.strftime("%H:%M"),
                "from_tz": tz_label(a["tz"]), "to_tz": tz_label(b["tz"]), "duration": service["duration_min"],
                "duration_text": duration_text(service["duration_min"]), "km": service["distance_km"],
                "classes": classes, "share": share, "status": trip["status"], "delay": trip["delay_min"],
                "note": trip["note"], "days": service["days"],
                "bookable": trip["status"] == "Scheduled" and _at(at) < closes,
                "chart_done": _at(at) >= depart - timedelta(hours=CHART_HOURS),
                "cheapest": min(c["from"] for c in classes)}

    def search_trips(self, mode, from_name, to_name, day, passengers=1, at=None):
        """Trips of a route on one date (the local date of the departure city).
        Flights without a direct service: {"trips": [], "connections": [...]} (one change)."""
        try:
            a, b, notes = check_route(mode, from_name, to_name)
        except NoDirect as e:
            return self.search_connections(e.places[0], e.places[1], e.notes, day, passengers, at, str(e),
                                           mode=getattr(e, "mode", "flight"))
        try:
            d = datetime.strptime(str(day), "%Y-%m-%d").date()
        except ValueError:
            raise ShopError("Please choose the date of the journey.")
        today = to_local(_at(at), a["tz"]).date()
        if d < today:
            raise ShopError("This date has passed. Please choose today or a later date.")
        if d > today + timedelta(days=SEARCH_DAYS):
            raise ShopError(f"Tickets are sold up to {SEARCH_DAYS} days ahead.")
        try:
            passengers = int(passengers)
        except (TypeError, ValueError):
            passengers = 1
        if not 1 <= passengers <= MAX_PASSENGERS:
            raise ShopError(f"One booking can have 1 to {MAX_PASSENGERS} passengers.")
        out = []
        with self.db() as conn:
            self._release_expired(conn, at)
            for s in self._services(conn, mode, a, b):
                if s["days"][d.weekday()] != "1":
                    continue
                if s.get("valid_from") and not (s["valid_from"] <= d.isoformat() <= (s.get("valid_to") or "9999")):
                    continue                          # a GTFS timetable is valid only between two dates
                trip = self._trip(conn, s, d.isoformat())
                v = self._trip_view(s, trip, self._taken(conn, trip["id"]), at)
                v["enough"] = any(c["seats"] >= passengers for c in v["classes"])
                out.append(v)
        out.sort(key=lambda v: v["depart_utc"])
        if not out and mode in ("bus", "train"):          # version 6: no direct trip on this day -> one change
            c = self.search_connections(a, b, notes, d.isoformat(), passengers, at,
                                        f"No direct {mode} on this day.", mode=mode)
            if c["connections"]:
                return c
        return {"mode": mode, "from": a, "to": b, "day": d.isoformat(), "passengers": passengers, "notes": notes,
                "trips": out, "connections": []}

    def _day_checks(self, a, day, passengers, at):
        try:
            d = datetime.strptime(str(day), "%Y-%m-%d").date()
        except ValueError:
            raise ShopError("Please choose the date of the journey.")
        today = to_local(_at(at), a["tz"]).date()
        if d < today:
            raise ShopError("This date has passed. Please choose today or a later date.")
        if d > today + timedelta(days=SEARCH_DAYS):
            raise ShopError(f"Tickets are sold up to {SEARCH_DAYS} days ahead.")
        try:
            passengers = int(passengers)
        except (TypeError, ValueError):
            passengers = 1
        if not 1 <= passengers <= MAX_PASSENGERS:
            raise ShopError(f"One booking can have 1 to {MAX_PASSENGERS} passengers.")
        return d, passengers

    def search_connections(self, a, b, notes, day, passengers=1, at=None, why="", mode="flight"):
        """Trips with one change: flights (version 5), buses and trains (version 6). The first trip on the date,
        the second the same day or the next, with 1 to 12 hours to change (1.5 hours for flights between
        two countries)."""
        d, passengers = self._day_checks(a, day, passengers, at)
        hubs = connection_hubs(a, b) if mode == "flight" else ground_hubs(mode, a, b)
        found = []
        with self.db() as conn:
            self._release_expired(conn, at)
            for h in hubs:
                first = [s for s in self._services(conn, mode, a, h) if s["days"][d.weekday()] == "1"]
                second = self._services(conn, mode, h, b)
                if not first or not second:
                    continue
                legs1 = [self._trip_view(s, t, self._taken(conn, t["id"]), at)
                         for s in first for t in [self._trip(conn, s, d.isoformat())]]
                legs2 = []
                for k in (0, 1):
                    day2 = d + timedelta(days=k)
                    for s in second:
                        if s["days"][day2.weekday()] == "1":
                            t = self._trip(conn, s, day2.isoformat())
                            legs2.append(self._trip_view(s, t, self._taken(conn, t["id"]), at))
                gap_min = CONNECT_MIN["intl" if mode == "flight" and a["country"] != b["country"] else "same"]
                for v1 in legs1:
                    for v2 in legs2:
                        gap = (_dt(v2["depart_utc"]) - _dt(v1["arrive_utc"])).total_seconds() / 60
                        if gap_min <= gap <= CONNECT_MAX_H * 60:
                            total = (_dt(v2["arrive_utc"]) - _dt(v1["depart_utc"])).total_seconds() / 60
                            enough = (any(c["seats"] >= passengers for c in v1["classes"]) and
                                      any(c["seats"] >= passengers for c in v2["classes"]))
                            found.append({"legs": [v1, v2], "via": h["name"], "via_code": h.get("airport") or "",
                                          "change_min": int(gap), "change_text": duration_text(gap),
                                          "duration": int(total), "duration_text": duration_text(total),
                                          "depart_utc": v1["depart_utc"], "depart_local": v1["depart_local"],
                                          "arrive_local": v2["arrive_local"], "depart_time": v1["depart_time"],
                                          "arrive_time": v2["arrive_time"],
                                          "plus_days": (datetime.strptime(v2["arrive_local"][:10], "%Y-%m-%d").date()
                                                        - datetime.strptime(v1["depart_local"][:10], "%Y-%m-%d").date()).days,
                                          "cheapest": v1["cheapest"] + v2["cheapest"], "enough": enough,
                                          "bookable": v1["bookable"] and v2["bookable"]})
        found.sort(key=lambda c: (c["duration"], c["cheapest"]))
        best, seen = [], set()
        for c in found:                                  # at most 2 per first flight, 8 in all
            k = c["legs"][0]["trip_id"]
            if sum(1 for x in best if x["legs"][0]["trip_id"] == k) < 2:
                best.append(c)
            if len(best) >= 8:
                break
        best.sort(key=lambda c: c["depart_utc"])
        what = {"flight": "flights", "bus": "buses", "train": "trains"}[mode]
        return {"mode": mode, "from": a, "to": b, "day": d.isoformat(), "passengers": passengers,
                "notes": notes + ([why + f" Here are {what} with one change."] if why and best else
                                  [why] if why else []), "trips": [], "connections": best}

    def plan_multi_city(self, stops, modes, day, passengers=1, at=None):
        """Version 6: a trip through several towns (A -> B -> C -> D). For every leg the first trip (or trip with one
        change) that leaves at least 2 hours after the previous leg arrives, on that day or the 2 next days.
        Every leg is booked on its own (its own seats and ticket)."""
        stops = [str(x).strip() for x in stops if str(x or "").strip()]
        if len(stops) < 3:
            raise ShopError("Please write at least three towns (two legs).")
        if len(stops) > 5:
            raise ShopError("A trip can have at most 4 legs.")
        legs, after, d = [], None, str(day)
        for i in range(len(stops) - 1):
            mode = modes[i] if i < len(modes) and modes[i] in MODES else "bus"
            chosen, tried = None, None
            start = datetime.strptime(d, "%Y-%m-%d").date()
            for k in range(3):
                day_k = (start + timedelta(days=k)).isoformat()
                try:
                    r = self.search_trips(mode, stops[i], stops[i + 1], day_k, passengers, at)
                except ShopError as e:
                    raise ShopError(f"Leg {i + 1} ({stops[i]} - {stops[i + 1]}): {e}")
                tried = r
                options = [{"trip": t, "depart_utc": t["depart_utc"], "arrive_utc": t["arrive_utc"]}
                           for t in r["trips"] if t["enough"] and t["bookable"]]
                options += [{"conn": c, "depart_utc": c["depart_utc"], "arrive_utc": c["legs"][1]["arrive_utc"]}
                            for c in r["connections"] if c["enough"] and c["bookable"]]
                options = [o for o in options if after is None or _dt(o["depart_utc"]) >= after + timedelta(hours=2)]
                if options:
                    chosen = min(options, key=lambda o: o["depart_utc"])
                    break
            if chosen is None:
                raise ShopError(f"Leg {i + 1} ({stops[i]} - {stops[i + 1]}): no {mode} with free seats in the 3 days "
                                f"after the previous leg. Try another mode or date.")
            first = chosen.get("trip") or chosen["conn"]["legs"][0]
            last = chosen.get("trip") or chosen["conn"]["legs"][1]
            wait = (_dt(chosen["depart_utc"]) - after).total_seconds() / 60 if after else 0
            legs.append({"n": i + 1, "mode": mode, "from": tried["from"], "to": tried["to"], "trip": chosen.get("trip"),
                         "conn": chosen.get("conn"), "depart_local": first["depart_local"],
                         "arrive_local": last["arrive_local"], "wait_text": duration_text(wait) if after else "",
                         "cheapest": (chosen["trip"]["cheapest"] if chosen.get("trip") else chosen["conn"]["cheapest"])})
            after = _dt(chosen["arrive_utc"])
            d = to_local(after, tried["to"]["tz"]).date().isoformat()
        total = sum(x["cheapest"] for x in legs)
        return {"legs": legs, "passengers": int(passengers), "total_from": total * int(passengers),
                "start_day": str(day)}

    def route_timetable(self, mode, from_name, to_name):
        """The services of a route (for the route pages that search engines find)."""
        a, b, notes = check_route(mode, from_name, to_name)
        with self.db() as conn:
            rows = self._services(conn, mode, a, b)
        out = []
        for s in rows:
            hh, mm = (int(x) for x in s["depart_local"].split(":"))
            arr = (hh * 60 + mm + s["duration_min"])
            fare = fare_for(s, s["classes"][0])
            out.append(dict(s, arrive_local=_time(arr), plus_days=arr // (24 * 60),
                            duration_text=duration_text(s["duration_min"]),
                            runs=["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"] if s["days"] == "1111111" else
                            [d for i, d in enumerate(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]) if s["days"][i] == "1"],
                            daily=s["days"] == "1111111", fare_from=min(fare_for(s, c) for c in s["classes"]),
                            class_names=[CLASS_NAMES[c] for c in s["classes"]], first_fare=fare))
        return {"mode": mode, "from": a, "to": b, "notes": notes, "services": out}

    def _load_trip(self, conn, trip_id):
        t = conn.execute("SELECT * FROM ticket_trips WHERE id = ?", (trip_id,)).fetchone()
        if t is None:
            raise ShopError("Trip not found.")
        s = conn.execute("SELECT s.*, o.name AS operator, o.code AS operator_code, o.rating FROM ticket_services s "
                         "JOIN ticket_operators o ON o.id = s.operator_id WHERE s.id = ?", (t["service_id"],)).fetchone()
        return self._service_dict(s), dict(t)

    def seat_map(self, trip_id, at=None):
        """The trip and every seat with its state: free / taken."""
        with self.db() as conn:
            self._release_expired(conn, at)
            service, trip = self._load_trip(conn, trip_id)
            taken = self._taken(conn, trip_id)
        view = self._trip_view(service, trip, taken, at)
        fares = {c["cls"]: c["fare"] for c in view["classes"]}
        secs = []
        for sec in sections(service["mode"], service["classes"]):
            rows = [[dict(s, taken=s["code"] in taken, price=fares[s["cls"]] + s["fee"]) if s else None for s in row]
                    for row in sec["rows"]]
            secs.append(dict(sec, rows=rows, free=sum(1 for row in rows for s in row if s and not s["taken"])))
        return view, secs

    # ---------- hold, pay ----------

    def hold_seats(self, user_id, trip_id, seats, at=None, return_of="", link=""):
        """Keep the chosen seats for HOLD_MINUTES. Returns the PNR of the held booking.
        return_of: the PNR of the first part of a round trip (this booking is the way back: no convenience fee).
        link: the PNR of the first flight of a connection (held too): the two are paid together."""
        seats = [str(s).strip() for s in (seats or []) if str(s).strip()]
        if not seats:
            raise ShopError("Please choose at least one seat.")
        if len(set(seats)) != len(seats):
            raise ShopError("The same seat was chosen twice.")
        if len(seats) > MAX_PASSENGERS:
            raise ShopError(f"One booking can have at most {MAX_PASSENGERS} seats.")
        t = _at(at)
        with self.db() as conn:
            self._release_expired(conn, at)
            service, trip = self._load_trip(conn, trip_id)
            idx = seat_index(service["mode"], service["classes"])
            chosen = [idx.get(code) for code in seats]
            if None in chosen:
                raise ShopError("This seat does not exist on this trip.")
            classes = {s["cls"] for s in chosen}
            if len(classes) != 1:
                raise ShopError("Please choose seats of one class in one booking.")
            cls = classes.pop()
            depart = _dt(trip["depart_utc"])
            if trip["status"] != "Scheduled":
                raise ShopError("This trip was cancelled by the operator.")
            if t >= depart - timedelta(minutes=CLOSE_MINUTES[service["mode"]]):
                raise ShopError("Booking for this trip is closed.")
            held = conn.execute("SELECT COUNT(*) FROM ticket_bookings WHERE user_id = ? AND status = ?",
                                (user_id, HELD)).fetchone()[0]
            if held >= MAX_HOLDS:
                raise ShopError("You already have seats on hold. Please pay for them or let them expire first.")
            view = self._trip_view(service, trip, self._taken(conn, trip_id), at)
            fare = next(c["fare"] for c in view["classes"] if c["cls"] == cls)
            back = self._round_trip_check(conn, user_id, return_of, service) if return_of else ""
            first = self._link_check(conn, user_id, link, trip, len(chosen)) if link else None
            money = price_booking(service, cls, fare, chosen, round_trip=bool(back))
            pnr = _pnr()
            while conn.execute("SELECT 1 FROM ticket_bookings WHERE pnr = ?", (pnr,)).fetchone():
                pnr = _pnr()
            u = conn.execute("SELECT mobile FROM users WHERE id = ?", (user_id,)).fetchone()
            bid = conn.execute(
                "INSERT INTO ticket_bookings (pnr, user_id, trip_id, cls, status, fare_total, seat_fees, airport_fees, "
                "conv_fee, tax, total, lines, contact_mobile, hold_until, created_at, return_of, link_pnr) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (pnr, user_id, trip_id, cls, HELD, money["fare_total"], money["seat_fees"], money["airport_fees"],
                 money["conv_fee"], money["tax"], money["total"], json.dumps(money["lines"]), u["mobile"] if u else "",
                 _fmt(t + timedelta(minutes=HOLD_MINUTES)), _when(at), back, first["pnr"] if first else "")).lastrowid
            for s in chosen:                       # the database refuses a seat that somebody else has
                if not conn.execute("INSERT INTO ticket_seats (trip_id, seat, booking_id, created_at) VALUES (?, ?, ?, ?) "
                                    "ON CONFLICT DO NOTHING", (trip_id, s["code"], bid, _when(at))).rowcount:
                    raise ShopError(f"Seat {s['label']} was just taken by another traveller. Please choose another seat.")
                conn.execute("INSERT INTO ticket_passengers (booking_id, seat, fare) VALUES (?, ?, ?)",
                             (bid, s["code"], fare + s["fee"]))
            if first:                              # the first flight keeps the second one: paid together
                conn.execute("UPDATE ticket_bookings SET link_pnr = ?, hold_until = ? WHERE id = ?",
                             (pnr, _fmt(t + timedelta(minutes=HOLD_MINUTES)), first["id"]))
        return pnr

    def _round_trip_check(self, conn, user_id, return_of, service):
        """The way back of a round trip: the first booking is the traveller's, paid, and goes the other way."""
        r = conn.execute("SELECT b.*, s.from_city, s.to_city FROM ticket_bookings b JOIN ticket_trips t "
                         "ON t.id = b.trip_id JOIN ticket_services s ON s.id = t.service_id WHERE b.pnr = ?",
                         (str(return_of).upper(),)).fetchone()
        if r is None or r["user_id"] != user_id or r["status"] not in (CONFIRMED, WAITLISTED):
            raise ShopError("The first part of the round trip was not found.")
        a, b = place(r["from_city"]), place(r["to_city"])
        x, y = place(service["from_city"]), place(service["to_city"])
        if _km(a, y) > 160 or _km(b, x) > 160:
            raise ShopError("A return trip must go back the same way.")
        return r["pnr"]

    def _link_check(self, conn, user_id, link, trip, seats):
        """The first flight of a connection: held by the same traveller, same number of seats, early enough."""
        r = conn.execute("SELECT * FROM ticket_bookings WHERE pnr = ?", (str(link).upper(),)).fetchone()
        if r is None or r["user_id"] != user_id or r["status"] != HELD or r["link_pnr"]:
            raise ShopError("Please choose the seats of the first flight again.")
        n = conn.execute("SELECT COUNT(*) FROM ticket_passengers WHERE booking_id = ?", (r["id"],)).fetchone()[0]
        if n != seats:
            raise ShopError(f"Please choose {n} seats, as on the first flight.")
        t1 = conn.execute("SELECT arrive_utc, delay_min FROM ticket_trips WHERE id = ?", (r["trip_id"],)).fetchone()
        if _dt(trip["depart_utc"]) < _dt(t1["arrive_utc"]) + timedelta(minutes=CONNECT_MIN["same"]):
            raise ShopError("The second flight leaves too soon after the first one.")
        return dict(r)

    def join_waitlist(self, user_id, trip_id, cls, passengers, at=None):
        """A full train class: a booking on the waiting list (no seats yet). After payment it is confirmed by
        itself when seats come back; if not confirmed when the chart is made, all the money comes back."""
        try:
            n = int(passengers)
        except (TypeError, ValueError):
            n = 0
        if not 1 <= n <= MAX_PASSENGERS:
            raise ShopError(f"One booking can have 1 to {MAX_PASSENGERS} passengers.")
        t = _at(at)
        with self.db() as conn:
            self._release_expired(conn, at)
            service, trip = self._load_trip(conn, trip_id)
            if service["mode"] != "train" or cls not in service["classes"]:
                raise ShopError("A waiting list is only for full train classes.")
            if trip["status"] != "Scheduled":
                raise ShopError("This trip was cancelled by the operator.")
            depart = _dt(trip["depart_utc"])
            if t >= depart - timedelta(hours=CHART_HOURS):
                raise ShopError("The chart of this train is ready: the waiting list is closed.")
            view = self._trip_view(service, trip, self._taken(conn, trip_id), at)
            c = next(x for x in view["classes"] if x["cls"] == cls)
            if c["seats"] >= n:
                raise ShopError("There are free seats in this class: please choose them on the seat map.")
            waiting = conn.execute("SELECT COUNT(*) FROM ticket_bookings WHERE trip_id = ? AND cls = ? AND status = ?",
                                   (trip_id, cls, WAITLISTED)).fetchone()[0]
            if waiting >= WAITLIST_MAX:
                raise ShopError("The waiting list of this class is full (regret).")
            held = conn.execute("SELECT COUNT(*) FROM ticket_bookings WHERE user_id = ? AND status = ?",
                                (user_id, HELD)).fetchone()[0]
            if held >= MAX_HOLDS:
                raise ShopError("You already have seats on hold. Please pay for them or let them expire first.")
            money = price_booking(service, cls, c["fare"], [{"fee": 0}] * n)
            pnr = _pnr()
            while conn.execute("SELECT 1 FROM ticket_bookings WHERE pnr = ?", (pnr,)).fetchone():
                pnr = _pnr()
            u = conn.execute("SELECT mobile FROM users WHERE id = ?", (user_id,)).fetchone()
            bid = conn.execute(
                "INSERT INTO ticket_bookings (pnr, user_id, trip_id, cls, status, fare_total, seat_fees, airport_fees, "
                "conv_fee, tax, total, lines, contact_mobile, hold_until, created_at, wl) "
                "VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?, ?, ?, ?, -1)",
                (pnr, user_id, trip_id, cls, HELD, money["fare_total"], money["conv_fee"], money["tax"], money["total"],
                 json.dumps(money["lines"]), u["mobile"] if u else "", _fmt(t + timedelta(minutes=HOLD_MINUTES)),
                 _when(at))).lastrowid
            for _ in range(n):
                conn.execute("INSERT INTO ticket_passengers (booking_id, seat, fare) VALUES (?, '', ?)", (bid, c["fare"]))
        return pnr

    def _promote(self, conn, trip_id, at=None):
        """Seats came back: the waiting list gets them, first come first served. Returns the PNRs confirmed."""
        rows = conn.execute("SELECT * FROM ticket_bookings WHERE trip_id = ? AND status = ? ORDER BY wl, id",
                            (trip_id, WAITLISTED)).fetchall()
        if not rows:
            return []
        service, trip = self._load_trip(conn, trip_id)
        if trip["status"] != "Scheduled":
            return []
        done = []
        for r in rows:
            taken = self._taken(conn, trip_id)
            free = [code for code, s in seat_index(service["mode"], service["classes"]).items()
                    if s["cls"] == r["cls"] and code not in taken]
            people = conn.execute("SELECT id FROM ticket_passengers WHERE booking_id = ? ORDER BY id",
                                  (r["id"],)).fetchall()
            if len(free) < len(people):
                break                                        # first come, first served
            pairs = list(zip(people, free))
            if not all(conn.execute("INSERT INTO ticket_seats (trip_id, seat, booking_id, created_at) VALUES (?, ?, ?, ?) "
                                    "ON CONFLICT DO NOTHING", (trip_id, code, r["id"], _when(at))).rowcount
                       for _p, code in pairs):           # another server took a seat at the same moment: next time
                conn.execute("DELETE FROM ticket_seats WHERE booking_id = ?", (r["id"],))
                break
            if not conn.execute("UPDATE ticket_bookings SET status = ? WHERE id = ? AND status = ?",
                                (CONFIRMED, r["id"], WAITLISTED)).rowcount:
                conn.execute("DELETE FROM ticket_seats WHERE booking_id = ?", (r["id"],))
                continue
            got = []
            for p, code in pairs:
                conn.execute("UPDATE ticket_passengers SET seat = ? WHERE id = ?", (code, p["id"]))
                got.append(self._seat_label(service, code))
            self.notify(conn, r["user_id"], "Waiting list confirmed: {0}".replace("{0}", r["pnr"]),
                        f"Good news: your waiting-list ticket {r['pnr']} ({service['number']}) is confirmed. "
                        f"Seats: {', '.join(got)}.", url=f"/tickets/b/{r['pnr']}")
            done.append(r["pnr"])
        return done

    def pay_booking(self, user_id, pnr, f, at=None):
        """Passengers' names + payment. Confirms the booking only while the hold is valid."""
        b = self.get_booking(pnr, user_id)
        if b["status"] != HELD:
            raise ShopError("This booking is not waiting for payment.")
        second = b.get("linked")                        # a connection: the second flight is paid now too
        if second and second["status"] != HELD:
            second = None
        total = b["total"] + (second["total"] if second else 0)
        names = []
        for i, p in enumerate(b["passengers"]):
            name = _clean(f.get(f"name_{i}"), 60)
            if len(name) < 2:
                raise ShopError(f"Please enter the name of passenger {i + 1}.")
            try:
                age = int(str(f.get(f"age_{i}", "")).strip())
            except ValueError:
                age = 0
            if not 1 <= age <= 120:
                raise ShopError(f"Please enter the age of passenger {i + 1}.")
            gender = f.get(f"gender_{i}", "")
            names.append((name, age, gender if gender in ("M", "F", "X") else "", p["id"]))
        mobile = "".join(ch for ch in str(f.get("contact_mobile", "")) if ch.isdigit() or ch == "+")
        if not 7 <= len(mobile.lstrip("+")) <= 15:
            raise ShopError("Please enter a mobile number for messages about the trip.")
        payment = f.get("payment", "wallet")
        if payment not in ("wallet", "card"):
            raise ShopError("Please choose how to pay.")
        last4, emi_months, emi_monthly = "", 0, 0
        if payment == "card":
            from yopay import card_check, emi_plans
            card, ok, message = card_check(f.get("card", ""), f.get("expiry", ""), f.get("cvc", ""), f.get("card_name", ""))
            if not ok:
                raise ShopError(message)
            last4 = card[-4:]
            try:
                emi_months = int(f.get("emi_months") or 0)
            except ValueError:
                emi_months = 0
            if emi_months:
                plan = next((p for p in emi_plans(total) if p["months"] == emi_months), None)
                if plan is None:
                    raise ShopError("This EMI plan is not available for this amount.")
                emi_monthly = plan["monthly"]
        stamp = _fmt(_at(at))
        with self.db() as conn:
            for part in [b] + ([second] if second else []):
                new = WAITLISTED if part["wl"] else CONFIRMED
                wl = 0
                if part["wl"]:
                    wl = conn.execute("SELECT COALESCE(MAX(wl), 0) + 1 FROM ticket_bookings WHERE trip_id = ? AND cls = ? "
                                      "AND wl > 0", (part["trip_id"], part["cls"])).fetchone()[0]
                if not conn.execute("UPDATE ticket_bookings SET status = ?, payment = ?, card_last4 = ?, emi_months = ?, "
                                    "emi_monthly = ?, contact_mobile = ?, paid_at = ?, wl = ? WHERE id = ? AND status = ? "
                                    "AND hold_until >= ?", (new, payment, last4, emi_months, emi_monthly, mobile,
                                                            _when(at), wl, part["id"], HELD, stamp)).rowcount:
                    raise ShopError(f"Your seats were kept for {HOLD_MINUTES} minutes and the time is over. "
                                    f"Please choose the seats again.")
                if payment == "wallet":
                    self.wallet_move(conn, user_id, -part["total"], "ticket", part["pnr"],
                                     f"{MODES[part['mode']]} ticket {part['pnr']}", when=_when(at))
                for (name, age, gender, _pid), p in zip(names, part["passengers"]):
                    conn.execute("UPDATE ticket_passengers SET name = ?, age = ?, gender = ? WHERE id = ?",
                                 (name, age, gender, p["id"]))
                if new == WAITLISTED:
                    self.notify(conn, user_id, "Waiting list: {0}".replace("{0}", part["pnr"]),
                                f"{part['number']} {part['from_city']} - {part['to_city']}, {part['depart_local'][:16]}: "
                                f"you are number {wl} on the waiting list. We confirm the ticket by ourselves when "
                                f"seats come back; if not confirmed {CHART_HOURS} hours before departure, all the "
                                f"money comes back.", url=f"/tickets/b/{part['pnr']}")
                    self._promote(conn, part["trip_id"], at)
                else:
                    self.notify(conn, user_id, "Ticket confirmed: {0}".replace("{0}", part["pnr"]),
                                f"{part['number']} {part['from_city']} - {part['to_city']}, {part['depart_local'][:16]} "
                                f"({part['from_tz']}). Seats: {', '.join(p['label'] for p in part['passengers'])}. "
                                f"Show the e-ticket QR code when you board.", url=f"/tickets/b/{part['pnr']}")
        return pnr

    # ---------- reading bookings ----------

    def _booking_dict(self, conn, r):
        b = dict(r)
        service, trip = self._load_trip(conn, b["trip_id"])
        a, z = place(service["from_city"]), place(service["to_city"])
        idx = seat_index(service["mode"], service["classes"])
        b["passengers"] = [dict(p, label=self._seat_label(service, p["seat"]) if p["seat"] else f"WL {b['wl']}"
                                if b["wl"] > 0 else "WL", kind=idx.get(p["seat"], {}).get("kind", ""))
                           for p in conn.execute("SELECT * FROM ticket_passengers WHERE booking_id = ? ORDER BY id",
                                                 (b["id"],))]
        depart, arrive = _dt(trip["depart_utc"]), _dt(trip["arrive_utc"])
        dl, al = to_local(depart, a["tz"]), to_local(arrive, z["tz"])
        b.update(mode=service["mode"], number=service["number"], service_name=service["name"],
                 operator=service["operator"], operator_code=service["operator_code"], operator_id=service["operator_id"],
                 from_city=a["name"], to_city=z["name"], from_code=a["airport"] or "", to_code=z["airport"] or "",
                 from_key=a["key"], to_key=z["key"], from_country=a["country"], to_country=z["country"],
                 depart_utc=trip["depart_utc"], arrive_utc=trip["arrive_utc"], depart_local=_fmt(dl),
                 arrive_local=_fmt(al), from_tz=tz_label(a["tz"]), to_tz=tz_label(z["tz"]),
                 plus_days=(al.date() - dl.date()).days, duration_text=duration_text(service["duration_min"]),
                 trip_status=trip["status"], delay=trip["delay_min"], trip_note=trip["note"], day=trip["day"],
                 class_name=class_name(b["cls"], a["country"]), lines=json.loads(b["lines"]), service_id=service["id"])
        b["linked"] = None
        if b["link_pnr"]:                                 # the second flight of a connection
            r2 = conn.execute("SELECT * FROM ticket_bookings WHERE pnr = ?", (b["link_pnr"],)).fetchone()
            if r2 is not None and r2["id"] != b["id"]:
                b2 = dict(r2)
                b2["link_pnr"] = ""                       # (no loop)
                b["linked"] = self._booking_dict(conn, b2)
        return b

    @staticmethod
    def _seat_label(service, code):
        if not code:
            return "WL"
        if service["mode"] == "train":
            coach, n = code.split("-")
            return f"{coach}/{n}"
        return code

    def get_booking(self, pnr, user_id=None):
        pnr = str(pnr or "").strip().upper()
        with self.db() as conn:
            r = conn.execute("SELECT * FROM ticket_bookings WHERE pnr = ?", (pnr,)).fetchone()
            if r is None or (user_id is not None and r["user_id"] != user_id):
                raise ShopError("Booking not found.")
            return self._booking_dict(conn, r)

    def my_bookings(self, user_id):
        with self.db() as conn:
            rows = conn.execute("SELECT * FROM ticket_bookings WHERE user_id = ? AND status <> ? ORDER BY id DESC",
                                (user_id, EXPIRED)).fetchall()
            out = [self._booking_dict(conn, r) for r in rows]
        stamp = _fmt(utc_now())
        for b in out:
            b["upcoming"] = b["status"] in (HELD, CONFIRMED, WAITLISTED) and b["arrive_utc"] > stamp
        out.sort(key=lambda b: (not b["upcoming"], b["depart_utc"] if b["upcoming"] else ""), reverse=False)
        return out

    def trip_phase(self, b, at=None):
        """Booked / Boarding / On the way / Arrived (with the delay), or Cancelled."""
        if b["trip_status"] == "Cancelled" or b["status"] == CANCELLED:
            return "Cancelled", -1
        t = _at(at)
        delay = timedelta(minutes=b["delay"])
        depart, arrive = _dt(b["depart_utc"]) + delay, _dt(b["arrive_utc"]) + delay
        if t >= arrive:
            return "Arrived", 3
        if t >= depart:
            return "On the way", 2
        if t >= depart - timedelta(minutes=45 if b["mode"] == "flight" else 30):
            return "Boarding", 1
        return "Booked", 0

    def refund_preview(self, b, at=None):
        if b["status"] == WAITLISTED:                       # not confirmed yet: all back but the convenience fee
            return {"pct": 0, "charge": 0, "refund": b["total"] - b["conv_fee"],
                    "can": _at(at) < _dt(b["depart_utc"]) and b["trip_status"] == "Scheduled"}
        pct, charge, refund = refund_for(b, b["mode"], _dt(b["depart_utc"]), at)
        return {"pct": pct, "charge": charge, "refund": refund,
                "can": b["status"] == CONFIRMED and _at(at) < _dt(b["depart_utc"]) and b["trip_status"] == "Scheduled"}

    # ---------- cancel ----------

    def cancel_booking(self, user_id, pnr, at=None, _second=False):
        """The traveller cancels: held seats are just given back; a paid ticket is refunded by the rules.
        A connection is cancelled together (both flights); a waiting-list ticket gets all back but the fee."""
        b = self.get_booking(pnr, user_id)
        t = _at(at)
        if b.get("linked") and not _second and b["linked"]["status"] in (HELD, CONFIRMED, WAITLISTED):
            try:
                extra = self.cancel_booking(user_id, b["linked"]["pnr"], at, _second=True)
            except ShopError:
                extra = 0
        else:
            extra = 0
        with self.db() as conn:
            if b["status"] == HELD:
                if conn.execute("UPDATE ticket_bookings SET status = ?, cancelled_at = ? WHERE id = ? AND status = ?",
                                (CANCELLED, now(), b["id"], HELD)).rowcount:
                    if conn.execute("DELETE FROM ticket_seats WHERE booking_id = ?", (b["id"],)).rowcount:
                        self._promote(conn, b["trip_id"], at)
                return extra
            if b["status"] == WAITLISTED:
                refund = b["total"] - b["conv_fee"]
                if not conn.execute("UPDATE ticket_bookings SET status = ?, refund = ?, charge = 0, cancelled_at = ?, "
                                    "cancel_reason = ? WHERE id = ? AND status = ?",
                                    (CANCELLED, refund, _when(at), "Waiting list cancelled by the traveller", b["id"],
                                     WAITLISTED)).rowcount:
                    raise ShopError("This booking cannot be cancelled.")
                self._refund(conn, b, refund, "cancelled", _when(at))
                return refund + extra
            if b["status"] != CONFIRMED:
                raise ShopError("This booking cannot be cancelled.")
            if t >= _dt(b["depart_utc"]):
                raise ShopError("The trip has already left, so the ticket cannot be cancelled now.")
            pct, charge, refund = refund_for(b, b["mode"], _dt(b["depart_utc"]), t)
            if not conn.execute("UPDATE ticket_bookings SET status = ?, refund = ?, charge = ?, cancelled_at = ?, "
                                "cancel_reason = ? WHERE id = ? AND status = ?",
                                (CANCELLED, refund, charge, _when(at), "Cancelled by the traveller", b["id"],
                                 CONFIRMED)).rowcount:
                raise ShopError("This booking cannot be cancelled.")
            conn.execute("DELETE FROM ticket_seats WHERE booking_id = ?", (b["id"],))
            self._refund(conn, b, refund, "cancelled", _when(at))
            self._promote(conn, b["trip_id"], at)
            self.notify(conn, b["user_id"], "Ticket cancelled: {0}".replace("{0}", b["pnr"]),
                        f"Your ticket {b['pnr']} ({b['number']}, {b['from_city']} - {b['to_city']}) is cancelled. "
                        f"Refund: {world.price(refund)}" +
                        (" to Yo Pay." if b["payment"] == "wallet" else " to your card (test mode)."),
                        url=f"/tickets/b/{b['pnr']}")
        return refund + extra

    def _refund(self, conn, b, amount, why, when=None):
        if amount > 0 and b["payment"] == "wallet":
            self.wallet_move(conn, b["user_id"], amount, "refund", b["pnr"], f"Ticket {b['pnr']} {why}", when=when)

    # ---------- the operator / admin ----------

    def cancel_trip(self, trip_id, reason=""):
        """The operator cancels a trip: every traveller gets all the money back (fees too)."""
        reason = _clean(reason, 160) or "Cancelled by the operator"
        with self.db() as conn:
            if not conn.execute("UPDATE ticket_trips SET status = 'Cancelled', note = ? WHERE id = ? AND "
                                "status = 'Scheduled'", (reason, trip_id)).rowcount:
                raise ShopError("This trip is already cancelled.")
            rows = conn.execute("SELECT * FROM ticket_bookings WHERE trip_id = ? AND status IN (?, ?, ?)",
                                (trip_id, HELD, CONFIRMED, WAITLISTED)).fetchall()
            count = 0
            for r in rows:
                b = self._booking_dict(conn, r)
                full = b["total"] if b["status"] in (CONFIRMED, WAITLISTED) else 0
                if not conn.execute("UPDATE ticket_bookings SET status = ?, refund = ?, charge = 0, cancelled_at = ?, "
                                    "cancel_reason = ? WHERE id = ? AND status = ?",
                                    (CANCELLED, full, now(), reason, b["id"], b["status"])).rowcount:
                    continue
                self._refund(conn, b, full, "- trip cancelled by the operator")
                if b["status"] in (CONFIRMED, WAITLISTED):
                    count += 1
                    self.notify(conn, b["user_id"], "Trip cancelled: {0}".replace("{0}", b["number"]),
                                f"We are sorry: {b['number']} {b['from_city']} - {b['to_city']} on "
                                f"{b['depart_local'][:16]} is cancelled ({reason}). You get all your money back: "
                                f"{world.price(full)}.", url=f"/tickets/b/{b['pnr']}")
            conn.execute("DELETE FROM ticket_seats WHERE trip_id = ? AND booking_id <> 0", (trip_id,))
        return count

    def set_trip_delay(self, trip_id, minutes, note=""):
        try:
            minutes = int(minutes)
        except (TypeError, ValueError):
            raise ShopError("Please enter the delay in minutes.")
        if not 0 <= minutes <= 24 * 60:
            raise ShopError("The delay must be from 0 to 1440 minutes.")
        with self.db() as conn:
            if not conn.execute("UPDATE ticket_trips SET delay_min = ?, note = ? WHERE id = ? AND status = 'Scheduled'",
                                (minutes, _clean(note, 160), trip_id)).rowcount:
                raise ShopError("Trip not found or cancelled.")
            sent = 0
            for r in conn.execute("SELECT * FROM ticket_bookings WHERE trip_id = ? AND status = ?",
                                  (trip_id, CONFIRMED)).fetchall():
                b = self._booking_dict(conn, r)
                self.notify(conn, b["user_id"], "Delay: {0}".replace("{0}", b["number"]),
                            (f"{b['number']} {b['from_city']} - {b['to_city']} is late by {minutes} minutes."
                             if minutes else f"{b['number']} {b['from_city']} - {b['to_city']} is on time again.") +
                            (f" {_clean(note, 160)}" if note else ""), url=f"/tickets/b/{b['pnr']}")
                sent += 1
        return sent

    # ---------- the minute job ----------

    def tickets_tick(self, at=None):
        """Free expired holds, remind travellers 3 hours before, finish trips and give points."""
        t = _at(at)
        done = {"freed": 0, "reminded": 0, "completed": 0, "waitlist_refunded": 0}
        with self.db() as conn:
            done["freed"] = self._release_expired(conn, t)
            chart = conn.execute(
                "SELECT b.* FROM ticket_bookings b JOIN ticket_trips t ON t.id = b.trip_id WHERE b.status = ? "
                "AND t.depart_utc <= ?", (WAITLISTED, _fmt(t + timedelta(hours=CHART_HOURS)))).fetchall()
            for r in chart:                               # the chart is made: not confirmed -> all the money back
                if conn.execute("UPDATE ticket_bookings SET status = ?, refund = ?, charge = 0, cancelled_at = ?, "
                                "cancel_reason = ? WHERE id = ? AND status = ?",
                                (CANCELLED, r["total"], _when(at), "Waiting list not confirmed at chart time",
                                 r["id"], WAITLISTED)).rowcount:
                    b = self._booking_dict(conn, r)
                    self._refund(conn, b, b["total"], "- waiting list not confirmed")
                    self.notify(conn, b["user_id"], "Waiting list not confirmed: {0}".replace("{0}", b["pnr"]),
                                f"Sorry, no seats came back on {b['number']} {b['from_city']} - {b['to_city']}. "
                                f"All your money is back: {world.price(b['total'])}.", url=f"/tickets/b/{b['pnr']}")
                    done["waitlist_refunded"] += 1
            soon = conn.execute(
                "SELECT b.* FROM ticket_bookings b JOIN ticket_trips t ON t.id = b.trip_id WHERE b.status = ? "
                "AND b.reminded = 0 AND t.status = 'Scheduled' AND t.depart_utc > ? AND t.depart_utc <= ?",
                (CONFIRMED, _fmt(t), _fmt(t + timedelta(hours=3)))).fetchall()
            for r in soon:
                if conn.execute("UPDATE ticket_bookings SET reminded = 1 WHERE id = ? AND reminded = 0", (r["id"],)).rowcount:
                    b = self._booking_dict(conn, r)
                    self.notify(conn, b["user_id"], "Your trip is soon: {0}".replace("{0}", b["number"]),
                                f"{b['number']} {b['from_city']} - {b['to_city']} leaves at {b['depart_local'][11:16]} "
                                f"({b['from_tz']}). Seats: {', '.join(p['label'] for p in b['passengers'])}.",
                                url=f"/tickets/b/{b['pnr']}")
                    done["reminded"] += 1
            ended = conn.execute(
                "SELECT b.id, b.user_id, b.pnr, b.total, t.arrive_utc, t.delay_min FROM ticket_bookings b "
                "JOIN ticket_trips t ON t.id = b.trip_id WHERE b.status = ? AND t.arrive_utc < ?",
                (CONFIRMED, _fmt(t))).fetchall()
            for r in ended:
                if t < _dt(r["arrive_utc"]) + timedelta(minutes=r["delay_min"]):
                    continue
                if conn.execute("UPDATE ticket_bookings SET status = ? WHERE id = ? AND status = ?",
                                (COMPLETED, r["id"], CONFIRMED)).rowcount:
                    pts = r["total"] // 100 // 100                      # 1 point for every ₹100
                    if pts:
                        self.add_points(conn, r["user_id"], pts, "Trip with Yo Tickets", r["pnr"], once=True,
                                        when=_when(at) if at is None else _fmt(_dt(r["arrive_utc"])))
                    done["completed"] += 1
        return done

    # ---------- e-ticket QR and the conductor ----------

    def _ticket_key(self):
        with self.db() as conn:
            conn.execute("INSERT INTO settings (key, value) VALUES ('ticket_key', ?) ON CONFLICT (key) DO NOTHING",
                         (secrets.token_hex(32),))
            return conn.execute("SELECT value FROM settings WHERE key = 'ticket_key'").fetchone()[0]

    def ticket_token(self, pnr):
        sig = hmac.new(self._ticket_key().encode(), str(pnr).upper().encode(), hashlib.sha256).hexdigest()[:12]
        return f"{str(pnr).upper()}.{sig}"

    def check_token(self, token):
        """'PNR.signature' (or a link that ends with it) -> PNR, or None when the signature is wrong."""
        token = str(token or "").strip().rstrip("/").rsplit("/", 1)[-1]
        if "." not in token:
            return None
        pnr = token.split(".", 1)[0].upper()
        return pnr if hmac.compare_digest(self.ticket_token(pnr), f"{pnr}.{token.split('.', 1)[1].lower()}") else None

    def verify_ticket(self, token, at=None):
        """What anybody who scans the QR code sees: valid or not, the trip, the number of passengers. No names."""
        pnr = self.check_token(token)
        if pnr is None:
            return {"valid": False}
        try:
            b = self.get_booking(pnr)
        except ShopError:
            return {"valid": False}
        phase = self.trip_phase(b, at)[0]
        return {"valid": b["status"] in (CONFIRMED, COMPLETED), "pnr": pnr, "status": b["status"], "mode": b["mode"],
                "number": b["number"], "operator": b["operator"], "operator_id": b["operator_id"],
                "from_city": b["from_city"], "to_city": b["to_city"], "depart_local": b["depart_local"],
                "from_tz": b["from_tz"], "class_name": b["class_name"], "passengers": len(b["passengers"]),
                "phase": phase, "trip_id": b["trip_id"]}

    def staff_of(self, user_id):
        with self.db() as conn:
            r = conn.execute("SELECT st.*, o.name AS operator, o.code AS operator_code, o.mode FROM ticket_staff st "
                             "JOIN ticket_operators o ON o.id = st.operator_id WHERE st.user_id = ?", (user_id,)).fetchone()
        return dict(r) if r else None

    def add_staff(self, email, operator_code):
        email = str(email or "").strip().lower()
        with self.db() as conn:
            u = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
            if u is None:
                raise ShopError("No account with this email. The person must sign up first.")
            op = conn.execute("SELECT id FROM ticket_operators WHERE code = ?", (operator_code,)).fetchone()
            if op is None:
                raise ShopError("Operator not found.")
            if not conn.execute("INSERT INTO ticket_staff (user_id, operator_id, created_at) VALUES (?, ?, ?) "
                                "ON CONFLICT (user_id) DO NOTHING", (u["id"], op["id"], now())).rowcount:
                raise ShopError("This person is already staff of an operator.")
        return u["id"]

    def remove_staff(self, staff_id):
        with self.db() as conn:
            conn.execute("DELETE FROM ticket_staff WHERE id = ?", (staff_id,))

    def staff_list(self):
        with self.db() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT st.*, u.name, u.email, o.name AS operator, o.mode FROM ticket_staff st JOIN users u "
                "ON u.id = st.user_id JOIN ticket_operators o ON o.id = st.operator_id ORDER BY o.name, u.name")]

    def conductor_trips(self, user_id, at=None):
        """Trips of the conductor's company from 12 hours ago to 36 hours ahead (only trips somebody looked at)."""
        st = self.staff_of(user_id)
        if st is None:
            raise ShopError("This page is for the staff of a transport company.")
        t = _at(at)
        with self.db() as conn:
            rows = conn.execute(
                "SELECT t.* FROM ticket_trips t JOIN ticket_services s ON s.id = t.service_id WHERE s.operator_id = ? "
                "AND t.depart_utc >= ? AND t.depart_utc <= ? ORDER BY t.depart_utc",
                (st["operator_id"], _fmt(t - timedelta(hours=12)), _fmt(t + timedelta(hours=36)))).fetchall()
            out = []
            for r in rows:
                service, trip = self._load_trip(conn, r["id"])
                v = self._trip_view(service, trip, self._taken(conn, r["id"]), at)
                c = conn.execute("SELECT COUNT(p.id), COALESCE(SUM(CASE WHEN p.boarded_at <> '' THEN 1 ELSE 0 END), 0) "
                                 "FROM ticket_passengers p JOIN ticket_bookings b ON b.id = p.booking_id "
                                 "WHERE b.trip_id = ? AND b.status IN (?, ?)", (r["id"], CONFIRMED, COMPLETED)).fetchone()
                v.update(passengers=c[0], boarded=c[1])
                out.append(v)
        return st, out

    def manifest(self, user_id, trip_id, at=None):
        st = self.staff_of(user_id)
        if st is None:
            raise ShopError("This page is for the staff of a transport company.")
        with self.db() as conn:
            service, trip = self._load_trip(conn, trip_id)
            if service["operator_id"] != st["operator_id"]:
                raise ShopError("This trip belongs to another company.")
            view = self._trip_view(service, trip, self._taken(conn, trip_id), at)
            rows = conn.execute(
                "SELECT p.*, b.pnr, b.status AS booking_status FROM ticket_passengers p JOIN ticket_bookings b "
                "ON b.id = p.booking_id WHERE b.trip_id = ? AND b.status IN (?, ?) ORDER BY p.seat",
                (trip_id, CONFIRMED, COMPLETED)).fetchall()
            others = conn.execute("SELECT COUNT(*) FROM ticket_seats WHERE trip_id = ? AND booking_id = 0",
                                  (trip_id,)).fetchone()[0]
        tz = place(service["from_city"])["tz"]
        people = [dict(r, label=self._seat_label(service, r["seat"]),
                       boarded_local=to_local(_dt(r["boarded_at"]), tz).strftime("%H:%M") if r["boarded_at"] else "")
                  for r in rows]
        return st, view, people, others

    def board_ticket(self, user_id, token, at=None):
        """The conductor scans the QR code (or types the PNR): every passenger of the ticket is checked in once."""
        st = self.staff_of(user_id)
        if st is None:
            raise ShopError("Only the staff of a transport company can check tickets.")
        raw = str(token or "").strip()
        pnr = self.check_token(raw) if "." in raw else raw.upper()
        if not pnr:
            raise ShopError("This QR code is not a Yo ticket (wrong signature).")
        try:
            b = self.get_booking(pnr)
        except ShopError:
            raise ShopError("No ticket with this PNR.")
        if b["operator_id"] != st["operator_id"]:
            raise ShopError(f"This ticket is for {b['operator']}, not for {st['operator']}.")
        if b["status"] not in (CONFIRMED, COMPLETED):
            raise ShopError(f"This ticket is {b['status'].lower()} - not valid for travel.")
        if b["trip_status"] != "Scheduled":
            raise ShopError("This trip was cancelled.")
        t = _at(at)
        depart = _dt(b["depart_utc"]) + timedelta(minutes=b["delay"])
        if t > _dt(b["arrive_utc"]) + timedelta(minutes=b["delay"]):
            raise ShopError("This trip has already arrived.")
        result = {"pnr": pnr, "trip_id": b["trip_id"], "number": b["number"], "day": b["day"],
                  "names": [p["name"] for p in b["passengers"]], "seats": [p["label"] for p in b["passengers"]]}
        if b["passengers"] and all(p["boarded_at"] for p in b["passengers"]):      # scanned again: just say so
            return dict(result, already=True)
        if t < depart - timedelta(hours=BOARDING_FROM_HOURS):
            raise ShopError(f"Too early: boarding starts {BOARDING_FROM_HOURS} hours before departure "
                            f"({b['depart_local'][:16]} {b['from_tz']}).")
        with self.db() as conn:                        # boarding time in UTC (shown in the local time)
            changed = conn.execute("UPDATE ticket_passengers SET boarded_at = ?, boarded_by = ? WHERE booking_id = ? "
                                   "AND boarded_at = ''", (_fmt(t), user_id, b["id"])).rowcount
        return dict(result, already=changed == 0)

    # ---------- live status on the map ----------

    def trip_live(self, pnr, user_id, at=None):
        b = self.get_booking(pnr, user_id)
        a, z = place(b["from_key"]), place(b["to_key"])
        phase, step = self.trip_phase(b, at)
        vehicle = None
        if phase == "On the way":
            t = _at(at)
            delay = timedelta(minutes=b["delay"])
            depart, arrive = _dt(b["depart_utc"]) + delay, _dt(b["arrive_utc"]) + delay
            frac = (t - depart).total_seconds() / max(1, (arrive - depart).total_seconds())
            lat, lng = places.along([(a["lat"], a["lng"]), (z["lat"], z["lng"])], frac)
            vehicle = {"lat": lat, "lng": lng, "name": b["number"], "vehicle": {"bus": "bus", "train": "train",
                                                                                  "flight": "plane"}[b["mode"]]}
        pts = [(a["lat"], a["lng"]), (z["lat"], z["lng"])]
        return {"number": b["pnr"], "status": phase, "step": step, "steps": TRIP_STEPS,
                "pickup": {"lat": a["lat"], "lng": a["lng"], "label": a["name"]},
                "drop": {"lat": z["lat"], "lng": z["lng"], "label": z["name"]}, "hubs": [], "courier": vehicle,
                "trail": [], "towns": _towns(pts), "road": [],
                "done": phase in ("Arrived", "Cancelled"), "local": False, "by_timetable": True}

    # ---------- admin ----------

    def ticket_stats(self):
        with self.db() as conn:
            by_status = {r[0]: r[1] for r in conn.execute("SELECT status, COUNT(*) FROM ticket_bookings GROUP BY status")}
            sold = conn.execute("SELECT COUNT(*) FROM ticket_passengers p JOIN ticket_bookings b ON b.id = p.booking_id "
                                "WHERE b.status IN (?, ?)", (CONFIRMED, COMPLETED)).fetchone()[0]
            revenue = conn.execute("SELECT COALESCE(SUM(total), 0) FROM ticket_bookings WHERE status IN (?, ?)",
                                   (CONFIRMED, COMPLETED)).fetchone()[0]
            kept = conn.execute("SELECT COALESCE(SUM(charge), 0) FROM ticket_bookings WHERE status = ?",
                                (CANCELLED,)).fetchone()[0]
            by_mode = {r[0]: (r[1], r[2]) for r in conn.execute(
                "SELECT s.mode, COUNT(b.id), COALESCE(SUM(b.total), 0) FROM ticket_bookings b JOIN ticket_trips t "
                "ON t.id = b.trip_id JOIN ticket_services s ON s.id = t.service_id WHERE b.status IN (?, ?) "
                "GROUP BY s.mode", (CONFIRMED, COMPLETED))}
            services = conn.execute("SELECT COUNT(*) FROM ticket_services").fetchone()[0]
            trips = conn.execute("SELECT COUNT(*) FROM ticket_trips").fetchone()[0]
        return {"by_status": by_status, "sold": sold, "revenue": revenue, "kept": kept, "by_mode": by_mode,
                "services": services, "trips": trips}

    def admin_bookings(self, q="", status="", mode="", limit=200):
        sql = ("SELECT b.*, u.name AS account, u.email, s.mode, s.number, s.from_city, s.to_city, t.depart_utc "
               "FROM ticket_bookings b JOIN users u ON u.id = b.user_id JOIN ticket_trips t ON t.id = b.trip_id "
               "JOIN ticket_services s ON s.id = t.service_id WHERE 1 = 1")
        args = []
        if status:
            sql += " AND b.status = ?"
            args.append(status)
        if mode:
            sql += " AND s.mode = ?"
            args.append(mode)
        if q:
            sql += " AND (b.pnr LIKE ? OR u.email LIKE ? OR u.name LIKE ? OR s.number LIKE ?)"
            args += [f"%{q.strip()}%"] * 4
        sql += " ORDER BY b.id DESC LIMIT ?"
        args.append(limit)
        with self.db() as conn:
            rows = [dict(r) for r in conn.execute(sql, args)]
        for r in rows:
            r["from_key"], r["to_key"] = r["from_city"], r["to_city"]
            r["from_city"], r["to_city"] = place_name(r["from_city"]), place_name(r["to_city"])
        return rows

    def admin_trips(self, day="", mode="", q="", limit=200):
        sql = ("SELECT t.*, s.mode, s.number, s.name, s.from_city, s.to_city, s.classes, o.name AS operator, "
               "(SELECT COUNT(*) FROM ticket_bookings b WHERE b.trip_id = t.id AND b.status IN ('Confirmed', "
               "'Completed')) AS bookings, (SELECT COUNT(*) FROM ticket_seats x WHERE x.trip_id = t.id) AS taken "
               "FROM ticket_trips t JOIN ticket_services s ON s.id = t.service_id JOIN ticket_operators o "
               "ON o.id = s.operator_id WHERE 1 = 1")
        args = []
        if day:
            sql += " AND t.day = ?"
            args.append(day)
        if mode:
            sql += " AND s.mode = ?"
            args.append(mode)
        if q:
            sql += " AND (s.number LIKE ? OR s.from_city LIKE ? OR s.to_city LIKE ?)"
            args += [f"%{q.strip()}%"] * 3
        sql += " ORDER BY t.depart_utc DESC LIMIT ?"
        args.append(limit)
        with self.db() as conn:
            rows = [dict(r) for r in conn.execute(sql, args)]
        for r in rows:
            classes = json.loads(r["classes"])
            r["capacity"] = sum(capacity(r["mode"], classes).values())
            r["depart_local"] = _fmt(to_local(_dt(r["depart_utc"]), place(r["from_city"])["tz"]))
            r["from_key"], r["to_key"] = r["from_city"], r["to_city"]
            r["from_city"], r["to_city"] = place_name(r["from_city"]), place_name(r["to_city"])
        return rows

    def admin_services(self, mode="", q="", limit=300):
        sql = ("SELECT s.*, o.name AS operator FROM ticket_services s JOIN ticket_operators o ON o.id = s.operator_id "
               "WHERE 1 = 1")
        args = []
        if mode:
            sql += " AND s.mode = ?"
            args.append(mode)
        if q:
            sql += " AND (s.number LIKE ? OR s.from_city LIKE ? OR s.to_city LIKE ? OR s.name LIKE ?)"
            args += [f"%{q.strip()}%"] * 4
        sql += " ORDER BY s.mode, s.from_city, s.to_city, s.depart_local LIMIT ?"
        args.append(limit)
        with self.db() as conn:
            rows = [self._service_dict(r) for r in conn.execute(sql, args)]
        for r in rows:
            r["from_key"], r["to_key"] = r["from_city"], r["to_city"]
            r["from_city"], r["to_city"] = place_name(r["from_city"]), place_name(r["to_city"])
        return rows

    def toggle_service(self, service_id):
        with self.db() as conn:
            if not conn.execute("UPDATE ticket_services SET active = 1 - active WHERE id = ?", (service_id,)).rowcount:
                raise ShopError("Service not found.")
            return conn.execute("SELECT active FROM ticket_services WHERE id = ?", (service_id,)).fetchone()[0]
