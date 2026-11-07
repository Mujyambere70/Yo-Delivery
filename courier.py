"""
courier.py - Yo Courier: send documents, parcels and groceries (mixin class used by Store).

The idea of the first Yo Delivery project (2023): "delivery of documents, groceries and packages for
individuals and businesses in the Ambala Division, with real-time tracking, and orders routed
automatically to delivery drivers".

How a shipment moves
    Booked -> Courier assigned -> Picked up -> (In transit -> At destination hub) -> Out for delivery -> Delivered
    * local      (same city):          one courier picks up and delivers (leg "both")
    * regional / national / abroad:    a courier picks up and brings it to the Yo hub (leg "pickup"), the
                                       network carries it to the destination city, and a courier there delivers
                                       it (leg "delivery") - or a partner company when Yo has no courier there.
Routing
    The nearest online courier in the city, whose vehicle can carry the weight, with the fewest jobs,
    gets the job (score = km to the pickup point + 3 km for every job already open). Real couriers come
    before the demo robots. Couriers can also
    take open jobs themselves; a job can be taken only once (conditional UPDATE).
Proof of delivery
    The sender gets a 4-digit delivery code and gives it to the receiver. The courier must type it,
    with the name of the person who received the parcel (and can add a photo). 5 wrong codes lock the job.
Money
    Price = first 0.5 kg + every further 0.5 kg + km, by zone; Express x1.6; fragile handling; insurance;
    GST 18% in India. Weight = the bigger of the real weight and the volume weight (L x W x H / 5000).
    Paid from Yo Pay, or in cash at pickup (the courier keeps the cash and gives it to Yo later).
    The courier's earning goes to their Yo Pay wallet when the job is done.
Demo couriers
    The demo couriers made by demo_data.py (couriers.demo = 1) are robots: they drive along the route by
    themselves, so a visitor can watch the live map. They only run in the demo mode (YO_FAST_TRACKING not "0").
    Real couriers (and the demo courier account a visitor can try) are moved only by their own actions;
    a job not picked up in time goes back to the list (10 minutes in the demo, 45 minutes for real).
"""

import hashlib
import json
import math
import random
import secrets
from datetime import datetime, timedelta
from functools import lru_cache

import geo
import places
import world
from core import ShopError, now, to_dt

BOOKED, ASSIGNED, PICKED, TRANSIT, AT_HUB, OUT, DELIVERED = (
    "Booked", "Courier assigned", "Picked up", "In transit", "At destination hub", "Out for delivery", "Delivered")
FAILED, CANCELLED, RETURNED = "Delivery failed", "Cancelled", "Returned to sender"
# version 7: when the receiver can take the parcel (local time of the receiver)
DELIVERY_WINDOWS = {"": "Any time", "09-12": "9:00 - 12:00", "12-15": "12:00 - 15:00", "15-18": "15:00 - 18:00",
                    "18-21": "18:00 - 21:00"}
LOCAL_STEPS = [BOOKED, ASSIGNED, PICKED, OUT, DELIVERED]
NETWORK_STEPS = [BOOKED, ASSIGNED, PICKED, TRANSIT, AT_HUB, OUT, DELIVERED]
OPEN = (BOOKED, ASSIGNED, PICKED, TRANSIT, AT_HUB, OUT)

KINDS = {"document": "Documents", "parcel": "Parcel", "grocery": "Groceries"}
SERVICES = {"standard": "Standard", "express": "Express"}
VEHICLES = {"bicycle": ("Bicycle", 5_000), "motorbike": ("Motorbike", 20_000), "car": ("Car", 50_000),
            "van": ("Van", 300_000)}                                    # name, max weight in grams
COURIER_STATUSES = ["Pending", "Approved", "Rejected", "Suspended"]

# zone: (first 0.5 kg, every further 0.5 kg, per km) in paise; max weight in kg
RATE_CARD = {
    "local": (49_00, 10_00, 0, 50),
    "regional": (69_00, 20_00, 1_00, 50),
    "national": (99_00, 35_00, 10, 30),
    "near": (900_00, 300_00, 0, 20),
    "world": (1800_00, 500_00, 0, 20),
}
ZONE_NAMES = {"local": "Same city", "regional": "Ambala Division & Tricity", "national": "India",
              "near": "Abroad (near)", "world": "Abroad (world)"}
EXPRESS = 1.6
FRAGILE_FEE = 40_00
INSURANCE_RATE, INSURANCE_MIN = 2, 20_00          # 2 % of the declared value, at least ₹20
GST_RATE = 18
VOLUME_DIVISOR = 5000                             # cm3 per kg (the usual courier rule)
MAX_SIDE_CM = 120
MAX_JOBS = 3                                      # open jobs a courier can have at the same time
CODE_TRIES = 5
COURIER_SHARE = {"both": 70, "pickup": 25, "delivery": 25}   # % of the price before tax
ROBOTS_PER_PLACE = 2                              # demo robots made for one town or village at most
# first names of the demo robots that appear in new places (fictional people)
ROBOT_NAMES = {
    "IN": ["Ravi Kumar", "Sunita Devi", "Arjun Mehta", "Kavita Joshi", "Mohit Sharma", "Anjali Gupta"],
    "RW": ["Eric Habimana", "Aline Uwase", "Jean Nshimiyimana", "Grace Mukamana", "Patrick Niyonzima",
           "Divine Ingabire"],
    "RU": ["Alexei Smirnov", "Olga Ivanova", "Dmitry Kuznetsov", "Anna Popova", "Sergei Volkov", "Irina Sokolova"],
    "": ["Alex Morgan", "Sam Lee", "Maria Silva", "Omar Haddad", "Lena Novak", "Kofi Mensah"],
}

COURIER_SCHEMA = """
CREATE TABLE IF NOT EXISTS couriers (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL UNIQUE REFERENCES users(id),
    vehicle TEXT NOT NULL, vehicle_no TEXT NOT NULL DEFAULT '', city TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Pending', reason TEXT NOT NULL DEFAULT '', demo INTEGER NOT NULL DEFAULT 0,
    online INTEGER NOT NULL DEFAULT 0, lat REAL, lng REAL, last_seen TEXT NOT NULL DEFAULT '',
    photo TEXT NOT NULL DEFAULT '', photo_check TEXT NOT NULL DEFAULT '',
    rating_sum INTEGER NOT NULL DEFAULT 0, rating_count INTEGER NOT NULL DEFAULT 0,
    jobs_done INTEGER NOT NULL DEFAULT 0, earned INTEGER NOT NULL DEFAULT 0, cash_in_hand INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, decided_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS shipments (
    id INTEGER PRIMARY KEY AUTOINCREMENT, number TEXT NOT NULL UNIQUE, user_id INTEGER NOT NULL,
    kind TEXT NOT NULL, contents TEXT NOT NULL, weight_g INTEGER NOT NULL, length_cm INTEGER NOT NULL DEFAULT 0,
    width_cm INTEGER NOT NULL DEFAULT 0, height_cm INTEGER NOT NULL DEFAULT 0, charge_g INTEGER NOT NULL,
    declared INTEGER NOT NULL DEFAULT 0, fragile INTEGER NOT NULL DEFAULT 0, insured INTEGER NOT NULL DEFAULT 0,
    service TEXT NOT NULL, zone TEXT NOT NULL, distance_km REAL NOT NULL,
    from_name TEXT NOT NULL, from_mobile TEXT NOT NULL, from_address TEXT NOT NULL, from_city TEXT NOT NULL,
    from_lat REAL NOT NULL, from_lng REAL NOT NULL,
    to_name TEXT NOT NULL, to_mobile TEXT NOT NULL, to_address TEXT NOT NULL, to_city TEXT NOT NULL,
    to_country TEXT NOT NULL DEFAULT 'IN', to_postcode TEXT NOT NULL DEFAULT '', to_lat REAL, to_lng REAL,
    subtotal INTEGER NOT NULL, tax INTEGER NOT NULL, price INTEGER NOT NULL, breakdown TEXT NOT NULL,
    payment TEXT NOT NULL, paid INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL, leg TEXT NOT NULL, courier_id INTEGER, pickup_courier_id INTEGER,
    start_lat REAL, start_lng REAL, delivery_code TEXT NOT NULL, code_tries INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0, wait_until TEXT NOT NULL DEFAULT '',
    pod_name TEXT NOT NULL DEFAULT '', pod_photo TEXT NOT NULL DEFAULT '', pod_by TEXT NOT NULL DEFAULT '',
    rating INTEGER NOT NULL DEFAULT 0, feedback TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL, assigned_at TEXT NOT NULL DEFAULT '', picked_at TEXT NOT NULL DEFAULT '',
    moved_at TEXT NOT NULL DEFAULT '', delivered_at TEXT NOT NULL DEFAULT '', expected TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_shipments_user ON shipments(user_id);
CREATE INDEX IF NOT EXISTS idx_shipments_courier ON shipments(courier_id);
CREATE TABLE IF NOT EXISTS shipment_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, shipment_id INTEGER NOT NULL REFERENCES shipments(id),
    status TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', place TEXT NOT NULL DEFAULT '',
    by_who TEXT NOT NULL DEFAULT '', at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_shipment_events ON shipment_events(shipment_id);
CREATE TABLE IF NOT EXISTS courier_track (
    id INTEGER PRIMARY KEY AUTOINCREMENT, shipment_id INTEGER NOT NULL, lat REAL NOT NULL, lng REAL NOT NULL,
    at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_courier_track ON courier_track(shipment_id);
"""

# demo timings (seconds) - a whole local delivery takes about 4 minutes on the live map
BOT = {"to_pickup": 70, "at_pickup": 15, "to_drop": 140, "to_hub": 70, "network": 150, "partner_out": 60,
       "partner_done": 120, "retry": 60, "no_courier": 120, "slow_courier": 600}
REAL_NETWORK_HOURS = {"regional": 6, "national": 30, "near": 96, "world": 160}


def _rupee(paise):
    """Round to whole rupees (prices on the site have no paise)."""
    return int(round(paise / 100)) * 100


def _clean(text, limit=200):
    return " ".join(str(text or "").split())[:limit]


def _num(value, label, low, high, cast=float):
    try:
        v = cast(str(value).replace(",", ".").strip())
    except (TypeError, ValueError):
        raise ShopError(f"Please enter the {label}.")
    if not (low <= v <= high) or (isinstance(v, float) and math.isnan(v)):
        raise ShopError(f"The {label} must be from {low:g} to {high:g}.")
    return v


SERVICE_KM = 15                 # a courier also serves the villages up to 15 km from the place where they work
HOME_KM = 100                   # Indian places this close to the main Yo hub get the regional rates
REGION_KM = 350                 # same state / province and not farther: regional rates (outside the home area)
NEARBY_KM = 60                  # any two places this close: regional rates too (small countries like Rwanda)
LOCAL_KM = 8                    # two places this close are "the same city"
NEAR_KM = 3500                  # abroad, sent from outside India: "near" zone up to this distance


def _legacy_place(name):
    c = places.CITIES[name]
    return {"id": name, "name": name, "cc": "IN", "lat": c[2], "lng": c[3], "region": c[0], "district": "",
            "pin": c[1], "legacy": True, "home": c[4] == "home", "kind": "hub", "label": f"{name}, {c[0]}, India"}


def _home(lat, lng, cc):
    return cc == "IN" and geo.haversine_km(places.position(places.HOME_CITY), (lat, lng)) <= HOME_KM


def _geo_place(p):
    if p["id"] in places.CITIES:
        return _legacy_place(p["id"])
    return {"id": p["id"], "name": p["name"], "cc": p["cc"], "lat": p["lat"], "lng": p["lng"], "region": p["region"],
            "district": p["district"], "pin": p["pin"] or "", "legacy": False, "kind": p["kind"],
            "home": _home(p["lat"], p["lng"], p["cc"]), "label": p["label"]}


def resolve(text="", cc=None, place_id=""):
    """A place where Yo can pick up or deliver - one of the 33 Yo cities or any town or village of the world
    (geo.py): a dict (id, name, cc, lat, lng, region, legacy, home) or None. cc: the country it must be in."""
    cc = str(cc or "").upper() or None
    place_id = str(place_id or "").strip()
    if place_id:
        if place_id in places.CITIES:
            return _legacy_place(place_id) if cc in (None, "IN") else None
        p = geo.get(place_id)
        if p and (cc is None or p["cc"] == cc):
            return _geo_place(p)
    if cc in (None, "IN"):
        n = places.city(text)
        if n:
            return _legacy_place(n)
    text = _clean(text, 80)
    if len(text) < 2:
        return None
    p = geo.find(text, cc=cc)
    if p and (cc is None or p["cc"] == cc):
        return _geo_place(p)
    return None


@lru_cache(maxsize=4096)
def place_pos(key):
    """(lat, lng) of a place id (a Yo city name or a geo id), or None."""
    if not key:
        return None
    if key in places.CITIES:
        return places.position(key)
    p = geo.get(key)
    return (p["lat"], p["lng"]) if p else None


def serves(courier_place, key):
    """Does a courier who works in courier_place take jobs in place `key`? The same place always; for the
    towns and villages of the world also the places up to SERVICE_KM away (the 33 Yo cities keep their own)."""
    if not key or not courier_place:
        return False
    if courier_place == key:
        return True
    if courier_place in places.CITIES and key in places.CITIES:
        return False
    a, b = place_pos(courier_place), place_pos(key)
    return bool(a and b) and geo.haversine_km(a, b) <= SERVICE_KM


def near_point(p, text):
    """An approximate point for an address in a place (the same text gives the same point)."""
    if p.get("legacy"):
        return places.near_centre(p["id"], text)
    small = p.get("kind") in ("village", "cell", "locality", "sector")
    h = hashlib.sha256((p["id"] + "|" + " ".join(str(text).lower().split())).encode()).digest()
    angle = h[0] / 255 * 2 * math.pi
    dist_km = (0.2 + h[1] / 255 * 0.8) if small else (1 + h[1] / 255 * 3)
    lat = p["lat"]
    return (round(lat + dist_km / 111.0 * math.cos(angle), 5),
            round(p["lng"] + dist_km / (111.0 * max(0.2, math.cos(math.radians(lat)))) * math.sin(angle), 5))


def zone_for(fp, tp):
    """local / regional / national inside one country; near / world abroad."""
    if fp["cc"] != tp["cc"]:
        if fp["cc"] == "IN":
            z = world.shipping_zone(tp["cc"])
            return z if z in ("near", "world") else "near"
        return "near" if geo.haversine_km((fp["lat"], fp["lng"]), (tp["lat"], tp["lng"])) < NEAR_KM else "world"
    both_old = fp.get("legacy") and tp.get("legacy")
    km = geo.haversine_km((fp["lat"], fp["lng"]), (tp["lat"], tp["lng"]))
    if fp["id"] and fp["id"] == tp["id"] or (not both_old and tp["id"] and km < LOCAL_KM):
        return "local"
    if fp["home"] and tp["home"]:
        return "regional"
    if not both_old and (km <= NEARBY_KM or fp["region"] and fp["region"] == tp["region"] and km <= REGION_KM):
        return "regional"
    return "national"


def zone_label(zone, from_cc="IN", home=True):
    """The name of a zone for the customer ("Ambala Division & Tricity" only around the home hub)."""
    if zone == "regional" and not home:
        return "Nearby region"
    if zone == "national" and from_cc != "IN":
        return "Whole country"
    return ZONE_NAMES[zone]


def zone_of(from_city, to_city, to_country):
    """Version 3 helper (the 33 Yo cities), kept for old code: same as zone_for."""
    fp = resolve(from_city, "IN")
    tp = resolve(to_city, to_country) or {"id": "", "cc": str(to_country).upper(), "lat": 0, "lng": 0,
                                          "home": False, "region": "", "legacy": False}
    return zone_for(fp, tp)


def transit_text(zone, service, distance_km):
    """When the parcel should arrive (shown to the customer)."""
    express = service == "express"
    if zone == "local":
        return (2 if express else 4), "hours"
    if zone == "regional":
        return (8, "hours") if express else (1, "days")
    if zone == "national":
        days = 2 + int(distance_km // 800)
        return (max(1, math.ceil(days / 2)) if express else days), "days"
    days = {"near": (4, 6), "world": (5, 9)}[zone]
    return (days[0] if express else days[1]), "days"


def quote(kind, weight_kg, length=0, width=0, height=0, service="standard", from_city="", to_city="",
          to_country="IN", declared=0, fragile=False, insured=False, from_country="IN", from_place="", to_place="",
          router=None):
    """Price of a shipment (all amounts in paise). Raises ShopError for impossible parcels.
    Places: a Yo city name, or any town / village of the world (the id from the place search, or its name).
    router(a, b) -> km by road (OpenStreetMap); without it the straight line x 1.3 is used."""
    if kind not in KINDS:
        raise ShopError("Please choose what you are sending.")
    if service not in SERVICES:
        raise ShopError("Please choose the service.")
    from_country = str(from_country or "IN").upper()
    if from_country not in world.DIAL_CODES:
        raise ShopError("Please choose the country of the sender.")
    fp = resolve(from_city, from_country, from_place)
    if fp is None:
        raise ShopError("We could not find the pickup town or village. Please choose it from the list.")
    to_country = str(to_country or "IN").upper()
    if to_country not in world.DIAL_CODES:
        raise ShopError("Please choose the country of the receiver.")
    domestic = to_country == from_country
    tp = resolve(to_city, to_country, to_place)
    if tp is None:
        if domestic:
            raise ShopError("We could not find the delivery town or village. Please choose it from the list.")
        name = _clean(to_city, 60)
        if len(name) < 2:
            raise ShopError("Please enter the city of the receiver.")
        c = geo.country(to_country) or {"lat": 0.0, "lng": 0.0}
        tp = {"id": "", "name": name, "cc": to_country, "lat": c["lat"], "lng": c["lng"], "region": "",
              "district": "", "pin": "", "legacy": False, "home": False, "kind": "", "label": name}
    if not domestic and kind == "grocery":
        raise ShopError("Groceries cannot be sent abroad.")
    zone = zone_for(fp, tp)
    first, extra, per_km, max_kg = RATE_CARD[zone]
    if kind == "grocery" and zone not in ("local", "regional"):
        raise ShopError("Groceries are delivered only in the same city and the nearby region (they must stay fresh).")
    weight = _num(weight_kg, "weight in kg", 0.05, max_kg)
    if kind == "document" and weight > 2:
        raise ShopError("Documents can weigh at most 2 kg. Please send them as a parcel.")
    dims = [int(_num(x or 0, "size in cm", 0, MAX_SIDE_CM)) for x in (length, width, height)]
    volume_kg = (dims[0] * dims[1] * dims[2]) / VOLUME_DIVISOR if all(dims) else 0
    charge_kg = max(weight, volume_kg)
    halves = max(1, math.ceil(charge_kg / 0.5 - 1e-9))
    a, b = (fp["lat"], fp["lng"]), (tp["lat"], tp["lng"])
    if zone == "local":
        distance = 0.0
    elif domestic and router is not None:
        distance = round(float(router(a, b)), 1)
    else:
        distance = places.road_km(a, b)
    lines = [("Up to 0.5 kg", first)]
    if halves > 1:
        lines.append((f"Extra weight: {(halves - 1) * 0.5:g} kg", extra * (halves - 1)))
    if per_km and distance:
        lines.append((f"Distance: {distance:g} km", _rupee(per_km * distance)))
    base = sum(x[1] for x in lines)
    if kind == "document" and halves == 1:
        lines.append(("Documents discount", -_rupee(base * 0.2)))
    if service == "express":
        lines.append(("Express service", _rupee(sum(x[1] for x in lines) * (EXPRESS - 1))))
    if fragile:
        lines.append(("Fragile handling", FRAGILE_FEE))
    declared_paise = round(_num(declared or 0, "declared value", 0, 50_000 if domestic else 25_000) * 100)
    if insured:
        if declared_paise <= 0:
            raise ShopError("To insure the parcel, please enter its value.")
        lines.append(("Insurance", max(INSURANCE_MIN, _rupee(declared_paise * INSURANCE_RATE / 100))))
    subtotal = sum(x[1] for x in lines)
    tax = _rupee(subtotal * GST_RATE / 100) if from_country == "IN" and to_country == "IN" else 0
    amount, unit = transit_text(zone, service, distance)
    return {"kind": kind, "service": service, "zone": zone, "domestic": domestic,
            "zone_name": zone_label(zone, from_country, fp["home"] and tp["home"]), "from_city": fp["name"],
            "to_city": tp["name"], "to_country": to_country, "from_country": from_country, "from_place": fp["id"],
            "to_place": tp["id"], "from_label": fp["label"], "to_label": tp["label"], "from_point": a, "to_point": b,
            "from_kind": fp["kind"], "to_kind": tp["kind"],
            "weight_g": round(weight * 1000), "dims": dims,
            "volume_kg": round(volume_kg, 2), "charge_g": round(halves * 500), "distance_km": distance,
            "declared": declared_paise, "fragile": bool(fragile), "insured": bool(insured), "lines": lines,
            "subtotal": subtotal, "tax": tax, "price": subtotal + tax, "transit": amount, "transit_unit": unit}


def towns_near(points, limit=14):
    """Names for the drawn map: the Yo cities and the biggest towns of the world in the box around the points."""
    found = [{"label": n, "lat": la, "lng": ln} for n, la, ln in places.cities_near(points, limit)]
    lats, lngs = [p[0] for p in points], [p[1] for p in points]
    pad = max(0.08, (max(lats) - min(lats)) * 0.25, (max(lngs) - min(lngs)) * 0.25)
    have = {t["label"] for t in found}
    try:
        for p in geo.in_box(min(lats) - pad, max(lats) + pad, min(lngs) - pad, max(lngs) + pad, limit):
            if p["name"] not in have and len(found) < limit:
                found.append({"label": p["name"], "lat": p["lat"], "lng": p["lng"]})
                have.add(p["name"])
    except Exception:
        pass
    return found


def _mask(mobile):
    m = str(mobile or "")
    return m[:2] + "•" * max(0, len(m) - 4) + m[-2:] if len(m) > 4 else m


class CourierMixin:

    @property
    def courier_demo_mode(self):
        return self.env.get("YO_FAST_TRACKING", "1") != "0"

    # =================================================================
    # Sender: quote, book, cancel, rate
    # =================================================================

    def courier_quote(self, f):
        q = quote(f.get("kind", ""), f.get("weight", ""), f.get("length"), f.get("width"), f.get("height"),
                  f.get("service", "standard"), f.get("from_city", ""), f.get("to_city", ""),
                  f.get("to_country", "IN"), f.get("declared") or 0, bool(f.get("fragile")), bool(f.get("insured")),
                  from_country=f.get("from_country") or "IN", from_place=f.get("from_place", ""),
                  to_place=f.get("to_place", ""), router=lambda a, b: self.road_route(a, b)["km"])
        q["tax_label"] = f"GST {GST_RATE}%" if q["tax"] else ""
        if q["from_country"] != "IN" and hasattr(self, "courier_tax"):     # version 6: the VAT of each country
            tax, label = self.courier_tax(q)
            q["tax"], q["tax_label"] = _rupee(tax), label
            q["price"] = q["subtotal"] + q["tax"]
        return q

    def book_shipment(self, user_id, f, when=None):
        """Book a pickup. Payment: 'wallet' (taken now) or 'cash' (paid to the courier at pickup, India only)."""
        q = self.courier_quote(f)
        contents = _clean(f.get("contents"), 120)
        if len(contents) < 3:
            raise ShopError("Please write what is inside (for example: university documents, a phone charger).")
        sender = {k: _clean(f.get("from_" + k), 160) for k in ("name", "mobile", "address")}
        receiver = {k: _clean(f.get("to_" + k), 160) for k in ("name", "mobile", "address")}
        for who, d, country in (("sender", sender, q["from_country"]), ("receiver", receiver, q["to_country"])):
            if len(d["name"]) < 2:
                raise ShopError(f"Please enter the name of the {who}.")
            mobile = world.clean_mobile(country, d["mobile"])
            if mobile is None:
                raise ShopError(f"Please enter a valid mobile number of the {who}.")
            d["mobile"] = mobile
            if len(d["address"]) < 6:
                raise ShopError(f"Please enter the full address of the {who} (house, street, area).")
        postcode = ""
        if q["to_country"] != "IN":
            postcode = world.clean_postcode(q["to_country"], f.get("to_postcode", ""))
            if postcode is None:
                raise ShopError("The post code of the receiver is not valid.")
        payment = f.get("payment", "wallet")
        if payment not in ("wallet", "cash"):
            raise ShopError("Please choose how to pay.")
        if payment == "cash" and not q["domestic"]:
            raise ShopError("Parcels abroad must be paid with Yo Pay.")
        customs = None if q["domestic"] else self.customs_declaration(f, q)       # version 6: customs form
        # where exactly: the point chosen on the map / "use my location" when it is near the place,
        # else an approximate point near the centre of the town or village
        fc = q["from_city"]
        fp = resolve(place_id=q["from_place"])
        a = self._point_in(fp, f.get("from_lat"), f.get("from_lng")) or near_point(fp, sender["address"])
        if q["to_place"]:
            tp = resolve(place_id=q["to_place"])
            b = self._point_in(tp, f.get("to_lat"), f.get("to_lng")) or near_point(tp, receiver["address"])
            if q["zone"] == "local" and places.haversine_km(a, b) < 0.5:
                b = near_point(tp, receiver["address"] + " (b)")
        else:                                       # a place abroad we do not know: the centre of the country
            b = q["to_point"]
        when = when or now()
        created = to_dt(when)
        amount, unit = q["transit"], q["transit_unit"]
        expected = created + (timedelta(hours=amount) if unit == "hours" else timedelta(days=amount))
        number = "YC" + created.strftime("%y%m%d") + "".join(random.choices("0123456789", k=6))
        code = f"{secrets.randbelow(10000):04d}"
        leg = "both" if q["zone"] == "local" else "pickup"
        with self.db() as conn:
            cur = conn.execute(
                "INSERT INTO shipments (number, user_id, kind, contents, weight_g, length_cm, width_cm, height_cm, "
                "charge_g, declared, fragile, insured, service, zone, distance_km, from_name, from_mobile, "
                "from_address, from_city, from_lat, from_lng, to_name, to_mobile, to_address, to_city, to_country, "
                "to_postcode, to_lat, to_lng, subtotal, tax, price, breakdown, payment, paid, status, leg, "
                "delivery_code, created_at, expected, from_place, to_place, from_country) VALUES (?, ?, ?, ?, ?, ?, "
                "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "?, ?, ?)",
                (number, user_id, q["kind"], contents, q["weight_g"], *q["dims"], q["charge_g"], q["declared"],
                 int(q["fragile"]), int(q["insured"]), q["service"], q["zone"], q["distance_km"],
                 sender["name"], sender["mobile"], sender["address"], fc, a[0], a[1],
                 receiver["name"], receiver["mobile"], receiver["address"], q["to_city"], q["to_country"], postcode,
                 b[0], b[1], q["subtotal"], q["tax"], q["price"], json.dumps(q["lines"]), payment, 0, BOOKED, leg,
                 code, when, expected.strftime("%Y-%m-%d %H:%M:%S"), q["from_place"], q["to_place"],
                 q["from_country"]))
            sid = cur.lastrowid
            if q.get("tax_label"):
                conn.execute("UPDATE shipments SET tax_label = ? WHERE id = ?", (q["tax_label"], sid))
            win = str(f.get("time_window") or "")                     # version 7: when the receiver is at home
            if win in DELIVERY_WINDOWS and win:
                conn.execute("UPDATE shipments SET time_window = ? WHERE id = ?", (win, sid))
            if customs:
                self.save_customs(conn, sid, customs)
            if payment == "wallet":
                self.wallet_move(conn, user_id, -q["price"], "courier", number, f"Yo Courier {number}", when=when)
                conn.execute("UPDATE shipments SET paid = 1 WHERE id = ?", (sid,))
            self._event(conn, sid, BOOKED, f"{KINDS[q['kind']]}, {q['charge_g'] / 1000:g} kg, "
                                           f"{SERVICES[q['service']]}", fc, "Sender", when)
            self.notify(conn, user_id, "Pickup booked: {0}".replace("{0}", number),
                        f"Your shipment {number} to {receiver['name']} ({q['to_city']}) is booked. "
                        f"We are finding a courier near you.", url=f"/courier/s/{number}")
            self._assign(conn, sid, when)
        self._warm_routes(sid)
        return number

    @staticmethod
    def _point_in(p, lat, lng, limit_km=40):
        """The exact point the customer chose (map pin / my location) - only when it is near the place."""
        if not p or lat in (None, "") or lng in (None, ""):
            return None
        try:
            lat, lng = float(lat), float(lng)
        except (TypeError, ValueError):
            return None
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            return None
        if geo.haversine_km((p["lat"], p["lng"]), (lat, lng)) > limit_km:
            return None
        return round(lat, 5), round(lng, 5)

    def _event(self, conn, shipment_id, status, note="", place="", by="", when=None):
        conn.execute("INSERT INTO shipment_events (shipment_id, status, note, place, by_who, at) VALUES (?, ?, ?, ?, ?, ?)",
                     (shipment_id, status, note[:200], place, by, when or now()))

    def cancel_shipment(self, user_id, number):
        with self.db() as conn:
            s = conn.execute("SELECT * FROM shipments WHERE number = ? AND user_id = ?", (number, user_id)).fetchone()
            if s is None:
                raise ShopError("Shipment not found.")
            if conn.execute("UPDATE shipments SET status = ?, courier_id = NULL WHERE id = ? AND status IN (?, ?)",
                            (CANCELLED, s["id"], BOOKED, ASSIGNED)).rowcount == 0:
                raise ShopError("The courier has already picked it up, so it cannot be cancelled now.")
            if s["paid"] and s["payment"] == "wallet":
                self.wallet_move(conn, user_id, s["price"], "refund", number, f"Yo Courier {number} cancelled")
            self._event(conn, s["id"], CANCELLED, "Cancelled by the sender" +
                        (" - the money is back in Yo Pay" if s["paid"] and s["payment"] == "wallet" else ""),
                        s["from_city"], "Sender")
            if s["courier_id"]:
                self._notify_courier(conn, s["courier_id"], "Job cancelled: {0}".replace("{0}", number),
                                     f"The sender cancelled {number}. You do not need to go to the pickup.")

    def rate_shipment(self, user_id, number, stars, feedback=""):
        try:
            stars = int(stars)
        except (TypeError, ValueError):
            stars = 0
        if not 1 <= stars <= 5:
            raise ShopError("Please choose from 1 to 5 stars.")
        with self.db() as conn:
            s = conn.execute("SELECT * FROM shipments WHERE number = ? AND user_id = ?", (number, user_id)).fetchone()
            if s is None or s["status"] != DELIVERED:
                raise ShopError("You can rate the courier after the delivery.")
            if conn.execute("UPDATE shipments SET rating = ?, feedback = ? WHERE id = ? AND rating = 0",
                            (stars, _clean(feedback, 300), s["id"])).rowcount == 0:
                raise ShopError("You have already rated this delivery.")
            cid = s["courier_id"] or s["pickup_courier_id"]
            if cid:
                conn.execute("UPDATE couriers SET rating_sum = rating_sum + ?, rating_count = rating_count + 1 "
                             "WHERE id = ?", (stars, cid))

    # =================================================================
    # Reading shipments
    # =================================================================

    def _shipment(self, conn, row, owner=False):
        s = dict(row)
        s["events"] = [dict(e) for e in conn.execute(
            "SELECT * FROM shipment_events WHERE shipment_id = ? ORDER BY id", (s["id"],))]
        s["lines"] = json.loads(s["breakdown"])
        s["steps_list"] = LOCAL_STEPS if s["zone"] == "local" else NETWORK_STEPS
        reached = {e["status"] for e in s["events"]}
        s["steps"] = [{"name": n, "done": n in reached or s["status"] == DELIVERED,
                       "at": next((e["at"] for e in reversed(s["events"]) if e["status"] == n), "")}
                      for n in s["steps_list"]]
        s["kind_name"], s["service_name"] = KINDS[s["kind"]], SERVICES[s["service"]]
        fc = s["from_country"] if "from_country" in s else "IN"
        home = s["zone"] != "regional" or (fc == "IN" and all(
            k in places.CITIES and places.CITIES[k][4] == "home" for k in (self._key(s, "from"), self._key(s, "to"))))
        s["zone_name"] = zone_label(s["zone"], fc, home)
        s["active"] = s["status"] in OPEN
        s["can_cancel"] = s["status"] in (BOOKED, ASSIGNED)
        s["to_country_name"] = world.country_name(s["to_country"], world.current_lang())
        s["courier"] = None
        if s["courier_id"]:
            c = conn.execute("SELECT c.id, c.vehicle, c.rating_sum, c.rating_count, c.jobs_done, c.photo, u.name, "
                             "u.mobile FROM couriers c JOIN users u ON u.id = c.user_id WHERE c.id = ?",
                             (s["courier_id"],)).fetchone()
            if c:
                c = dict(c)
                c["first_name"] = c["name"].split()[0]
                c["vehicle_name"] = VEHICLES.get(c["vehicle"], ("", 0))[0]
                c["rating"] = round(c["rating_sum"] / c["rating_count"], 1) if c["rating_count"] else None
                c["mobile_masked"] = _mask(c["mobile"])
                if not owner:
                    c.pop("mobile")
                s["courier"] = c
        if not owner:                                   # the public page shows no addresses and no code
            for k in ("from_address", "to_address", "delivery_code", "from_mobile", "to_mobile", "pod_photo"):
                s[k] = ""
            s["from_name"], s["to_name"] = s["from_name"].split()[0], s["to_name"].split()[0]
        return s

    def get_shipment(self, number, user_id=None, public=False):
        """user_id: the sender (sees everything). public=True: anybody with the number (no addresses/code)."""
        number = str(number or "").strip().upper()
        with self.db() as conn:
            row = conn.execute("SELECT * FROM shipments WHERE number = ?", (number,)).fetchone()
            if row is None or (not public and row["user_id"] != user_id):
                raise ShopError("Shipment not found. Please check the consignment number.")
            return self._shipment(conn, row, owner=not public)

    def my_shipments(self, user_id):
        with self.db() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT number, to_name, to_city, to_country, kind, status, price, created_at, delivered_at, expected "
                "FROM shipments WHERE user_id = ? ORDER BY id DESC", (user_id,))]

    # =================================================================
    # Couriers: apply, go online, position
    # =================================================================

    def apply_courier(self, user_id, f):
        vehicle = f.get("vehicle", "")
        if vehicle not in VEHICLES:
            raise ShopError("Please choose your vehicle.")
        p = resolve(f.get("city", ""), f.get("country") or None, f.get("place", ""))
        if p is None:
            raise ShopError("Please choose the town or village where you will work.")
        vehicle_no = _clean(f.get("vehicle_no"), 20).upper()
        if vehicle != "bicycle" and len(vehicle_no) < 4:
            raise ShopError("Please enter the registration number of your vehicle.")
        if not f.get("agree"):
            raise ShopError("Please accept the rules for couriers.")
        with self.db() as conn:
            if conn.execute("SELECT 1 FROM couriers WHERE user_id = ?", (user_id,)).fetchone():
                raise ShopError("You have already applied to deliver with Yo.")
            cur = conn.execute("INSERT INTO couriers (user_id, vehicle, vehicle_no, city, place, demo, lat, lng, "
                               "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                               (user_id, vehicle, vehicle_no, p["name"], p["id"], 0, p["lat"], p["lng"], now()))
            return cur.lastrowid

    def courier_of(self, user_id):
        with self.db() as conn:
            r = conn.execute("SELECT c.*, u.name, u.email, u.mobile, u.wallet FROM couriers c JOIN users u "
                             "ON u.id = c.user_id WHERE c.user_id = ?", (user_id,)).fetchone()
        return self._courier_dict(r) if r else None

    @staticmethod
    def _courier_dict(r):
        c = dict(r)
        c["vehicle_name"], c["capacity_g"] = VEHICLES.get(c["vehicle"], ("", 0))
        c["rating"] = round(c["rating_sum"] / c["rating_count"], 1) if c["rating_count"] else None
        return c

    def set_online(self, courier_id, on, when=None):
        with self.db() as conn:
            c = conn.execute("SELECT status FROM couriers WHERE id = ?", (courier_id,)).fetchone()
            if c is None or c["status"] != "Approved":
                raise ShopError("Your courier account is not approved yet.")
            conn.execute("UPDATE couriers SET online = ?, last_seen = ? WHERE id = ?",
                         (1 if on else 0, when or now(), courier_id))
            if on:                                       # jobs waiting in the city can come to this courier now
                for s in conn.execute("SELECT id FROM shipments WHERE courier_id IS NULL AND status IN (?, ?) "
                                      "ORDER BY id", (BOOKED, AT_HUB)).fetchall():
                    self._assign(conn, s["id"], when)

    def courier_ping(self, courier_id, lat, lng, when=None):
        """The courier's phone sends its position (browser geolocation) every few seconds while online."""
        try:
            lat, lng = float(lat), float(lng)
        except (TypeError, ValueError):
            raise ShopError("Bad position.")
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            raise ShopError("Bad position.")
        when = when or now()
        with self.db() as conn:
            conn.execute("UPDATE couriers SET lat = ?, lng = ?, last_seen = ? WHERE id = ?",
                         (round(lat, 5), round(lng, 5), when, courier_id))
            for s in conn.execute("SELECT id FROM shipments WHERE courier_id = ? AND status IN (?, ?, ?)",
                                  (courier_id, ASSIGNED, PICKED, OUT)).fetchall():
                last = conn.execute("SELECT lat, lng FROM courier_track WHERE shipment_id = ? ORDER BY id DESC LIMIT 1",
                                    (s["id"],)).fetchone()
                if last is None or places.haversine_km((last["lat"], last["lng"]), (lat, lng)) > 0.03:
                    conn.execute("INSERT INTO courier_track (shipment_id, lat, lng, at) VALUES (?, ?, ?, ?)",
                                 (s["id"], round(lat, 5), round(lng, 5), when))

    # =================================================================
    # Routing: who gets the job
    # =================================================================

    @staticmethod
    def _key(s, which):
        """The place id of the pickup ("from") or delivery ("to") side ('' = a place abroad we do not know)."""
        k = s[which + "_place"] if which + "_place" in s.keys() else ""
        if k:
            return k
        if which == "from" or s["to_country"] == "IN":     # shipments of versions 3 and 4
            return s[which + "_city"] if s[which + "_city"] in places.CITIES else ""
        return ""

    def _hub(self, s, which):
        """The Yo hub of a side: the centre of its town (or the delivery point if the place is unknown)."""
        return place_pos(self._key(s, which)) or ((s["to_lat"], s["to_lng"]) if which == "to" else
                                                  (s["from_lat"], s["from_lng"]))

    def _leg_start(self, s):
        """Where the courier of the current leg must go first."""
        if s["leg"] == "delivery":
            return place_pos(self._key(s, "to"))
        return (s["from_lat"], s["from_lng"])

    def _serving(self, conn, key, online_only=True, when=None):
        """Approved couriers who work in (or near) a place."""
        if not key:
            return []
        sql = ("SELECT c.*, (SELECT COUNT(*) FROM shipments s WHERE s.courier_id = c.id AND s.status IN (?, ?, ?)) "
               "AS jobs FROM couriers c WHERE c.status = 'Approved'")
        args = [ASSIGNED, PICKED, OUT]
        if online_only:
            fresh = (to_dt(when or now()) - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
            sql += " AND c.online = 1 AND (c.demo = 1 OR c.last_seen >= ?)"
            args.append(fresh)
        return [c for c in conn.execute(sql, args).fetchall() if serves(c["place"] or c["city"], key)]

    def _candidates(self, conn, s, when):
        key = self._key(s, "to" if s["leg"] == "delivery" else "from")
        if not key:
            return []
        rows = self._serving(conn, key, when=when)
        start = self._leg_start(s)
        out = []
        for c in rows:
            c = self._courier_dict(c)
            if c["capacity_g"] < s["weight_g"] or c["jobs"] >= MAX_JOBS:
                continue
            here = (c["lat"], c["lng"]) if c["lat"] is not None else place_pos(c["place"] or c["city"])
            km = places.haversine_km(here, start) if start else 0
            out.append((round(km + 3 * c["jobs"], 3), c["id"], c, here))
        return sorted(out, key=lambda x: (x[2]["demo"], x[0], x[1]))      # real couriers before demo robots

    def _assign(self, conn, shipment_id, when=None):
        """Give a waiting job to the best courier. Returns the courier id or None."""
        when = when or now()
        s = conn.execute("SELECT * FROM shipments WHERE id = ?", (shipment_id,)).fetchone()
        if s is None or s["courier_id"] or s["status"] not in (BOOKED, AT_HUB) or (s["wait_until"] and s["wait_until"] > when):
            return None
        for score, cid, c, here in self._candidates(conn, s, when):
            if self._claim(conn, s, cid, here, when, "Given to the nearest free courier"):
                return cid
        robot = self._robot_for(conn, s, when)          # demo: a robot courier in any town or village of the world
        if robot and self._claim(conn, s, robot[0], robot[1], when, "Given to the nearest free courier"):
            return robot[0]
        return None

    def _claim(self, conn, s, courier_id, here, when, how):
        new = OUT if s["leg"] == "delivery" else ASSIGNED
        if conn.execute("UPDATE shipments SET courier_id = ?, status = ?, assigned_at = ?, moved_at = ?, start_lat = ?, "
                        "start_lng = ? WHERE id = ? AND courier_id IS NULL AND status = ?",
                        (courier_id, new, when, when, here[0], here[1], s["id"], s["status"])).rowcount == 0:
            return False
        if s["leg"] != "delivery":
            conn.execute("UPDATE shipments SET pickup_courier_id = ? WHERE id = ?", (courier_id, s["id"]))
        name = conn.execute("SELECT u.name FROM couriers c JOIN users u ON u.id = c.user_id WHERE c.id = ?",
                            (courier_id,)).fetchone()[0].split()[0]
        place = s["to_city"] if s["leg"] == "delivery" else s["from_city"]
        self._event(conn, s["id"], new, f"{how}: {name}", place, "Yo routing", when)
        self._notify_courier(conn, courier_id, "New job: {0}".replace("{0}", s["number"]),
                             f"Pick up {KINDS[s['kind']].lower()} {s['number']} in {place}. Open the Yo Courier app.")
        if new == ASSIGNED:
            self.notify(conn, s["user_id"], "Courier on the way: {0}".replace("{0}", s["number"]),
                        f"{name} will pick up your shipment {s['number']}.", url=f"/courier/s/{s['number']}")
        else:
            self.notify(conn, s["user_id"], "Out for delivery: {0}".replace("{0}", s["number"]),
                        f"{name} is bringing {s['number']} to {s['to_name']}. Delivery code: {s['delivery_code']}.",
                        url=f"/courier/s/{s['number']}")
        return True

    def world_robots_on(self, conn=None):
        """Demo only: robot couriers appear in any town or village of the world when a parcel is booked there."""
        if not self.courier_demo_mode:
            return False
        if conn is None:
            with self.db() as c:
                return self.world_robots_on(c)
        r = conn.execute("SELECT value FROM settings WHERE key = 'world_robots'").fetchone()
        return bool(r and r["value"] == "1")

    def _robot_for(self, conn, s, when):
        """A new demo robot courier for the place of the current leg (at most ROBOTS_PER_PLACE per place)."""
        key = self._key(s, "to" if s["leg"] == "delivery" else "from")
        if not key or not self.world_robots_on(conn):
            return None
        if conn.execute("SELECT COUNT(*) FROM couriers WHERE place = ? AND demo = 1",
                        (key,)).fetchone()[0] >= ROBOTS_PER_PLACE:
            return None
        p = resolve(place_id=key)
        if p is None:
            return None
        n = conn.execute("SELECT COUNT(*) FROM couriers WHERE demo = 1").fetchone()[0] + 1
        names = ROBOT_NAMES.get(p["cc"], ROBOT_NAMES[""])
        name = names[n % len(names)]
        for _ in range(20):
            email = f"robot{n}.{secrets.token_hex(3)}@couriers.example"       # a reserved domain: never contacted
            mobile = "7000" + "".join(random.choices("0123456789", k=6))
            if not conn.execute("SELECT 1 FROM users WHERE email = ? OR mobile = ?", (email, mobile)).fetchone():
                break
        uid = conn.execute("INSERT INTO users (name, email, mobile, password, wallet, created_at, country, lang, currency) "
                           "VALUES (?, ?, ?, ?, 0, ?, ?, 'en', '')",
                           (name, email, mobile, "!robot", when, p["cc"])).lastrowid
        here = near_point(p, name)
        vehicle = "van" if s["weight_g"] > VEHICLES["car"][1] else "car" if s["weight_g"] > VEHICLES["motorbike"][1] \
            else "motorbike"
        cid = conn.execute("INSERT INTO couriers (user_id, vehicle, vehicle_no, city, place, status, demo, online, lat, "
                           "lng, last_seen, created_at, decided_at) VALUES (?, ?, '', ?, ?, 'Approved', 1, 1, ?, ?, ?, ?, ?)",
                           (uid, vehicle, p["name"], key, here[0], here[1], when, when, when)).lastrowid
        return cid, here

    def _notify_courier(self, conn, courier_id, subject, body):
        r = conn.execute("SELECT user_id FROM couriers WHERE id = ?", (courier_id,)).fetchone()
        if r:
            self.notify(conn, r["user_id"], subject, body, url="/driver")

    # =================================================================
    # The courier's jobs
    # =================================================================

    def courier_jobs(self, courier_id):
        with self.db() as conn:
            mine = [self._job(r) for r in conn.execute(
                "SELECT * FROM shipments WHERE courier_id = ? AND status IN (?, ?, ?) ORDER BY id",
                (courier_id, ASSIGNED, PICKED, OUT))]
            c = conn.execute("SELECT * FROM couriers WHERE id = ?", (courier_id,)).fetchone()
            waiting = []
            if c and c["status"] == "Approved":
                mine_place = c["place"] or c["city"]
                waiting = [self._job(r) for r in conn.execute(
                    "SELECT * FROM shipments WHERE courier_id IS NULL AND status IN (?, ?) "
                    "AND (wait_until = '' OR wait_until <= ?) ORDER BY id LIMIT 400", (BOOKED, AT_HUB, now()))
                    if serves(mine_place, self._key(r, "from" if r["status"] == BOOKED else "to"))][:20]
                cap = VEHICLES[c["vehicle"]][1]
                waiting = [j for j in waiting if j["weight_g"] <= cap]
            done = [self._job(r) for r in conn.execute(
                "SELECT * FROM shipments WHERE (courier_id = ? OR pickup_courier_id = ?) AND status NOT IN (?, ?, ?) "
                "ORDER BY id DESC LIMIT 15", (courier_id, courier_id, ASSIGNED, PICKED, OUT))]
        return mine, waiting, done

    def _job(self, r):
        j = dict(r)
        j["kind_name"] = KINDS[j["kind"]]
        j["earning"] = j["subtotal"] * COURIER_SHARE[j["leg"]] // 100
        if j["leg"] == "delivery" or j["status"] in (PICKED, OUT) and j["leg"] == "both":
            j["go_to"], j["go_city"] = j["to_address"], j["to_city"]
        elif j["status"] == PICKED and j["leg"] == "pickup":
            j["go_to"], j["go_city"] = "Yo hub", j["from_city"]
        else:
            j["go_to"], j["go_city"] = j["from_address"], j["from_city"]
        j["next"] = {ASSIGNED: "pickup", PICKED: "handover", OUT: "deliver"}.get(j["status"], "")
        if j["status"] == OUT and j["leg"] == "both" and not j["picked_at"]:
            j["next"] = "pickup"
        return j

    def _my_job(self, conn, courier_id, number):
        s = conn.execute("SELECT * FROM shipments WHERE number = ? AND courier_id = ?", (number, courier_id)).fetchone()
        if s is None:
            raise ShopError("This job is not yours (or it is closed).")
        return s

    def take_job(self, courier_id, number, when=None):
        """A courier takes an open job from the list - only one courier can get it."""
        when = when or now()
        with self.db() as conn:
            c = self._courier_dict(conn.execute("SELECT * FROM couriers WHERE id = ?", (courier_id,)).fetchone())
            if c["status"] != "Approved":
                raise ShopError("Your courier account is not approved yet.")
            s = conn.execute("SELECT * FROM shipments WHERE number = ?", (number,)).fetchone()
            if s is None or s["courier_id"] or s["status"] not in (BOOKED, AT_HUB):
                raise ShopError("Sorry, another courier has already taken this job.")
            if not serves(c["place"] or c["city"], self._key(s, "to" if s["status"] == AT_HUB else "from")):
                raise ShopError("This job is in another city.")
            if s["weight_g"] > c["capacity_g"]:
                raise ShopError("This parcel is too heavy for your vehicle.")
            jobs = conn.execute("SELECT COUNT(*) FROM shipments WHERE courier_id = ? AND status IN (?, ?, ?)",
                                (courier_id, ASSIGNED, PICKED, OUT)).fetchone()[0]
            if jobs >= MAX_JOBS:
                raise ShopError(f"You can have at most {MAX_JOBS} jobs at the same time.")
            here = (c["lat"], c["lng"]) if c["lat"] is not None else place_pos(c["place"] or c["city"])
            if not self._claim(conn, s, courier_id, here, when, "Taken by the courier"):
                raise ShopError("Sorry, another courier has already taken this job.")

    def job_pickup(self, courier_id, number, cash_collected=False, when=None):
        when = when or now()
        with self.db() as conn:
            s = self._my_job(conn, courier_id, number)
            if s["leg"] == "delivery" or s["status"] != ASSIGNED:
                raise ShopError("This parcel is already picked up.")
            if s["payment"] == "cash" and not s["paid"]:
                if not cash_collected:
                    raise ShopError(f"Please collect {world.format_money(s['price'], 'INR')} in cash from the sender first.")
                conn.execute("UPDATE couriers SET cash_in_hand = cash_in_hand + ? WHERE id = ?", (s["price"], courier_id))
                conn.execute("UPDATE shipments SET paid = 1 WHERE id = ?", (s["id"],))
            new = OUT if s["leg"] == "both" else PICKED
            if conn.execute("UPDATE shipments SET status = ?, picked_at = ?, moved_at = ? WHERE id = ? AND status = ?",
                            (new, when, when, s["id"], ASSIGNED)).rowcount == 0:
                raise ShopError("This parcel is already picked up.")
            name = self._courier_name(conn, courier_id)
            self._event(conn, s["id"], PICKED, f"Picked up by {name}" +
                        (" - cash received" if s["payment"] == "cash" else ""), s["from_city"], name, when)
            if new == OUT:
                self._event(conn, s["id"], OUT, f"{name} is going to the receiver", s["to_city"], name, when)
                self.notify(conn, s["user_id"], "Picked up: {0}".replace("{0}", number),
                            f"{name} picked up {number} and is going to {s['to_name']}. "
                            f"Delivery code: {s['delivery_code']}.", url=f"/courier/s/{number}")
            else:
                self.notify(conn, s["user_id"], "Picked up: {0}".replace("{0}", number),
                            f"{name} picked up {number}. It goes to the Yo hub in {s['from_city']} now.",
                            url=f"/courier/s/{number}")

    def job_handover(self, courier_id, number, when=None):
        """Pickup leg finished: the parcel is at the Yo hub and travels to the destination city."""
        when = when or now()
        with self.db() as conn:
            s = self._my_job(conn, courier_id, number)
            if s["leg"] != "pickup" or s["status"] != PICKED:
                raise ShopError("This parcel cannot be handed over now.")
            if conn.execute("UPDATE shipments SET status = ?, courier_id = NULL, leg = 'delivery', moved_at = ? "
                            "WHERE id = ? AND status = ?", (TRANSIT, when, s["id"], PICKED)).rowcount == 0:
                raise ShopError("This parcel cannot be handed over now.")
            name = self._courier_name(conn, courier_id)
            self._pay_courier(conn, courier_id, s, "pickup", when)
            dest = self._dest(s)
            self._event(conn, s["id"], TRANSIT, f"Handed over at the Yo hub by {name}. On the way to {dest}",
                        s["from_city"], name, when)
            self.notify(conn, s["user_id"], "In transit: {0}".replace("{0}", number),
                        f"Your shipment {number} left {s['from_city']} and is on the way to {dest}.",
                        url=f"/courier/s/{number}")

    def job_deliver(self, courier_id, number, code, receiver, photo_name="", when=None):
        """Proof of delivery: the 4-digit code from the sender and the name of the person who took it."""
        when = when or now()
        receiver = _clean(receiver, 80)
        wrong = None
        with self.db() as conn:                  # a wrong code is counted (and saved) before the error is shown
            s = self._my_job(conn, courier_id, number)
            if s["status"] != OUT or (s["leg"] == "both" and not s["picked_at"]):
                raise ShopError("This parcel is not out for delivery.")
            if s["code_tries"] >= CODE_TRIES:
                raise ShopError("Too many wrong codes. Please call Yo support to finish this delivery.")
            if not secrets.compare_digest(str(code or "").strip(), s["delivery_code"]):
                conn.execute("UPDATE shipments SET code_tries = code_tries + 1 WHERE id = ?", (s["id"],))
                wrong = CODE_TRIES - s["code_tries"] - 1
        if wrong is not None:
            raise WrongCode(f"Wrong delivery code. Ask the receiver for the code ({wrong} tries left)."
                            if wrong else "Too many wrong codes. Please call Yo support to finish this delivery.")
        with self.db() as conn:
            s = self._my_job(conn, courier_id, number)
            if len(receiver) < 2:
                raise ShopError("Please write the name of the person who received the parcel.")
            if conn.execute("UPDATE shipments SET status = ?, delivered_at = ?, moved_at = ?, pod_name = ?, "
                            "pod_photo = ?, pod_by = ? WHERE id = ? AND status = ? AND code_tries < ?",
                            (DELIVERED, when, when, receiver, photo_name, "courier", s["id"], OUT,
                             CODE_TRIES)).rowcount == 0:
                raise ShopError("This parcel is not out for delivery.")
            name = self._courier_name(conn, courier_id)
            self._pay_courier(conn, courier_id, s, s["leg"], when)
            conn.execute("UPDATE couriers SET jobs_done = jobs_done + 1 WHERE id = ?", (courier_id,))
            self._event(conn, s["id"], DELIVERED, f"Received by {receiver} (delivery code checked)" +
                        (", photo taken" if photo_name else ""), s["to_city"], name, when)
            self.notify(conn, s["user_id"], "Delivered: {0}".replace("{0}", number),
                        f"{number} was delivered to {receiver}. Thank you for using Yo Courier!",
                        url=f"/courier/s/{number}")

    def job_fail(self, courier_id, number, reason, when=None):
        """The receiver was not there (or refused): the parcel goes to the Yo hub of the city."""
        when = when or now()
        reasons = ("Receiver not available", "Address not found", "Receiver refused the parcel",
                   "Receiver asked for another day")
        if reason not in reasons:
            raise ShopError("Please choose what happened.")
        with self.db() as conn:
            s = self._my_job(conn, courier_id, number)
            if s["status"] != OUT or (s["leg"] == "both" and not s["picked_at"]):
                raise ShopError("This parcel is not out for delivery.")
            attempts = s["attempts"] + 1
            name = self._courier_name(conn, courier_id)
            final = attempts >= 2 or reason == "Receiver refused the parcel"
            wait = (to_dt(when) + (timedelta(seconds=BOT["retry"]) if self.courier_demo_mode else timedelta(hours=12))
                    ).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute("UPDATE shipments SET status = ?, courier_id = NULL, leg = 'delivery', attempts = ?, "
                         "wait_until = ?, moved_at = ? WHERE id = ?",
                         (RETURNED if final else AT_HUB, attempts, wait, when, s["id"]))
            self._event(conn, s["id"], FAILED, f"{reason} (attempt {attempts})", s["to_city"], name, when)
            if final:
                self._event(conn, s["id"], RETURNED, "The parcel goes back to the sender", s["to_city"], "Yo hub", when)
            else:
                self._event(conn, s["id"], AT_HUB, "Kept at the Yo hub - we will try again", s["to_city"], "Yo hub", when)
            self.notify(conn, s["user_id"], "Delivery not possible: {0}".replace("{0}", number),
                        f"{reason}. " + ("The parcel will be returned to you." if final else
                                         "We will try again soon."), url=f"/courier/s/{number}")

    def _courier_name(self, conn, courier_id):
        return conn.execute("SELECT u.name FROM couriers c JOIN users u ON u.id = c.user_id WHERE c.id = ?",
                            (courier_id,)).fetchone()[0].split()[0]

    def _pay_courier(self, conn, courier_id, s, leg, when):
        share = s["subtotal"] * COURIER_SHARE[leg] // 100
        uid = conn.execute("SELECT user_id FROM couriers WHERE id = ?", (courier_id,)).fetchone()[0]
        self.wallet_move(conn, uid, share, "earning", s["number"], f"Yo Courier {s['number']} ({leg})", when=when)
        conn.execute("UPDATE couriers SET earned = earned + ? WHERE id = ?", (share, courier_id))

    # =================================================================
    # The network and the demo couriers (called every minute and by the live map)
    # =================================================================

    def courier_tick(self, at=None, only=None):
        """Move what moves by itself. `only`: one shipment id (the live map asks often)."""
        at = at or datetime.now()
        when = at.strftime("%Y-%m-%d %H:%M:%S")
        demo = self.courier_demo_mode
        moved = 0
        with self.db() as conn:
            sql = "SELECT * FROM shipments WHERE status IN (?, ?, ?, ?, ?, ?)"
            args = list(OPEN)
            if only:
                sql, args = sql + " AND id = ?", args + [only]
            rows = conn.execute(sql, args).fetchall()
            bots = {r["id"] for r in conn.execute("SELECT id FROM couriers WHERE demo = 1")} if demo else set()
        for s in rows:
            with self.db() as conn:
                s = conn.execute("SELECT * FROM shipments WHERE id = ?", (s["id"],)).fetchone()
                if s["status"] not in OPEN:
                    continue
                moved += self._tick_one(conn, s, at, when, bots, demo)
        return moved

    def _tick_one(self, conn, s, at, when, bots, demo):
        if s.get("partner_id") if isinstance(s, dict) else ("partner_id" in s.keys() and s["partner_id"]):
            return 0                                   # version 7: a real partner sends its own events
        since = (at - to_dt(s["moved_at"] or s["created_at"])).total_seconds()
        st = s["status"]
        if st in (BOOKED, AT_HUB) and not s["courier_id"]:
            if self._assign(conn, s["id"], when):
                return 1
            partner = st == AT_HUB and not self._city_has_couriers(conn, self._key(s, "to"))
            limit = BOT["no_courier"] if demo else 4 * 3600
            if st == BOOKED and since >= limit and not self._city_has_couriers(conn, self._key(s, "from")):
                return self._partner_pickup(conn, s, when)
            if st == AT_HUB and partner and since >= (BOT["partner_out"] if demo else 2 * 3600):
                return self._partner_out(conn, s, when)
            return 0
        if st == TRANSIT:
            limit = BOT["network"] if demo else REAL_NETWORK_HOURS.get(s["zone"], 24) * 3600
            if since >= limit:
                dest = self._dest(s)
                conn.execute("UPDATE shipments SET status = ?, moved_at = ? WHERE id = ? AND status = ?",
                             (AT_HUB, when, s["id"], TRANSIT))
                self._event(conn, s["id"], AT_HUB, f"Arrived at the destination hub", dest, "Yo network", when)
                self._assign(conn, s["id"], when)
                return 1
            return 0
        if st == OUT and not s["courier_id"]:                    # a partner company is delivering
            if since >= (BOT["partner_done"] if demo else 6 * 3600):
                conn.execute("UPDATE shipments SET status = ?, delivered_at = ?, moved_at = ?, pod_name = ?, pod_by = ? "
                             "WHERE id = ? AND status = ?", (DELIVERED, when, when, s["to_name"].split()[0],
                                                             "partner", s["id"], OUT))
                place = self._dest(s)
                self._event(conn, s["id"], DELIVERED, "Delivered by our partner courier", place, "Partner", when)
                self.notify(conn, s["user_id"], "Delivered: {0}".replace("{0}", s["number"]),
                            f"{s['number']} was delivered in {place}.", url=f"/courier/s/{s['number']}")
                return 1
            return 0
        if s["courier_id"] in bots:
            return self._bot_step(conn, s, since, when)
        if st == ASSIGNED and since >= (BOT["slow_courier"] if demo else 45 * 60):
            return self._take_back(conn, s, when)
        return 0

    def _take_back(self, conn, s, when):
        """The courier did not pick up in time: the job goes back to the list and the courier goes offline."""
        if conn.execute("UPDATE shipments SET courier_id = NULL, pickup_courier_id = NULL, status = ?, moved_at = ? "
                        "WHERE id = ? AND status = ? AND courier_id = ?",
                        (BOOKED, when, s["id"], ASSIGNED, s["courier_id"])).rowcount == 0:
            return 0
        conn.execute("UPDATE couriers SET online = 0 WHERE id = ?", (s["courier_id"],))
        self._event(conn, s["id"], BOOKED, "The courier did not come in time - finding another courier",
                    s["from_city"], "Yo routing", when)
        self._notify_courier(conn, s["courier_id"], "Job taken back: {0}".replace("{0}", s["number"]),
                             "You did not pick up in time, so the job went to another courier and you are offline now.")
        self._assign(conn, s["id"], when)
        return 1

    def _city_has_couriers(self, conn, key):
        """Is there any approved courier (online or not) for this place?"""
        return bool(self._serving(conn, key, online_only=False))

    def _dest(self, s):
        """The destination in words: the town, and the country when it is abroad."""
        home = s["from_country"] if "from_country" in s.keys() else "IN"
        if s["to_country"] == home:
            return s["to_city"]
        return f"{s['to_city']}, {world.country_name(s['to_country'], world.current_lang())}"

    def _partner_pickup(self, conn, s, when):
        if conn.execute("UPDATE shipments SET status = ?, leg = ?, picked_at = ?, moved_at = ?, paid = 1 "
                        "WHERE id = ? AND status = ? AND courier_id IS NULL",
                        (OUT if s["leg"] == "both" else TRANSIT, "both" if s["leg"] == "both" else "delivery", when,
                         when, s["id"], BOOKED)).rowcount:
            self._event(conn, s["id"], ASSIGNED, "No Yo courier free - a partner company picks it up",
                        s["from_city"], "Partner", when)
            self._event(conn, s["id"], PICKED, "Picked up by our partner courier", s["from_city"], "Partner", when)
            if s["leg"] != "both":
                self._event(conn, s["id"], TRANSIT, "On the way", s["from_city"], "Partner", when)
            else:
                self._event(conn, s["id"], OUT, "The partner courier is going to the receiver", s["to_city"],
                            "Partner", when)
            return 1
        return 0

    def _partner_out(self, conn, s, when):
        if conn.execute("UPDATE shipments SET status = ?, moved_at = ? WHERE id = ? AND status = ? AND courier_id IS NULL",
                        (OUT, when, s["id"], AT_HUB)).rowcount:
            place = self._dest(s)
            who = "Partner courier" if place == s["to_city"] else "Yo Global Express partner"
            self._event(conn, s["id"], OUT, f"{who} is delivering it", place, "Partner", when)
            self.notify(conn, s["user_id"], "Out for delivery: {0}".replace("{0}", s["number"]),
                        f"{s['number']} is out for delivery in {place}.", url=f"/courier/s/{s['number']}")
            return 1
        return 0

    def _bot_legs(self, s):
        """The parts of the way a demo courier drives in its current state, and the time it takes."""
        start = (s["start_lat"], s["start_lng"])
        pickup, drop = (s["from_lat"], s["from_lng"]), (s["to_lat"], s["to_lng"])
        if s["status"] == ASSIGNED:
            return [(start, pickup)], BOT["to_pickup"]
        if s["status"] == PICKED:                                 # pickup leg: to the Yo hub of the town
            return [(pickup, self._hub(s, "from"))], BOT["to_hub"]
        if s["status"] == OUT and s["leg"] == "both":
            return [(pickup, drop)], BOT["to_drop"]
        if s["status"] == OUT:                                    # delivery leg: hub -> receiver
            return [(start, self._hub(s, "to")), (self._hub(s, "to"), drop)], BOT["to_hub"] + BOT["to_drop"]
        return [], 0

    def _bot_route(self, s, conn=None):
        """The way as a list of points: along the roads when the route is known (cache), else straight."""
        legs, seconds = self._bot_legs(s)
        if not legs or any(None in p for leg in legs for p in leg):
            return (None, 0) if not legs else ([legs[0][0], legs[-1][1]], seconds)
        points = []
        for a, b in legs:
            line = self.road_route(a, b, conn=conn, fetch=False)["line"] if conn is not None else [a, b]
            points += [tuple(p) for p in line]
        points[0], points[-1] = tuple(legs[0][0]), tuple(legs[-1][1])
        return points, seconds

    def _warm_routes(self, shipment_id):
        """Ask the road routes a demo courier will drive (outside any transaction), so they are in the cache."""
        try:
            with self.db() as conn:
                s = conn.execute("SELECT * FROM shipments WHERE id = ?", (shipment_id,)).fetchone()
                bot = s is not None and s["courier_id"] and conn.execute(
                    "SELECT demo FROM couriers WHERE id = ?", (s["courier_id"],)).fetchone()["demo"]
            if not s:
                return
            pairs = [((s["from_lat"], s["from_lng"]), (s["to_lat"], s["to_lng"]))] if s["zone"] == "local" else []
            if bot:
                pairs += self._bot_legs(s)[0]
            for a, b in pairs:
                if None not in a and None not in b:
                    self.road_route(a, b)
        except Exception:
            pass

    def bot_position(self, s, at=None, conn=None):
        route, seconds = self._bot_route(s, conn)
        if not route or None in route[-1]:
            return None
        since = ((at or datetime.now()) - to_dt(s["moved_at"])).total_seconds()
        return places.along(route, since / seconds if seconds else 1)

    def _bot_step(self, conn, s, since, when):
        route, seconds = self._bot_route(s, conn)
        cid = s["courier_id"]
        if route and None not in route[-1]:
            p = places.along(route, since / seconds if seconds else 1)
            conn.execute("UPDATE couriers SET lat = ?, lng = ?, last_seen = ? WHERE id = ?", (p[0], p[1], when, cid))

        def due(after):            # the robot acts at the planned moment, even if the server looked later (it slept)
            return min(when, (to_dt(s["moved_at"]) + timedelta(seconds=after)).strftime("%Y-%m-%d %H:%M:%S"))
        if s["status"] == ASSIGNED and since >= BOT["to_pickup"] + BOT["at_pickup"]:
            t = due(BOT["to_pickup"] + BOT["at_pickup"])
            self._run_as_bot(lambda: self.job_pickup(cid, s["number"], cash_collected=True, when=t), conn)
            return 1
        if s["status"] == PICKED and since >= BOT["to_hub"]:
            t = due(BOT["to_hub"])
            self._run_as_bot(lambda: self.job_handover(cid, s["number"], when=t), conn)
            return 1
        if s["status"] == OUT and since >= seconds:
            t = due(seconds)
            self._run_as_bot(lambda: self.job_deliver(cid, s["number"], s["delivery_code"],
                                                      s["to_name"].split()[0], when=t), conn)
            return 1
        return 0

    @staticmethod
    def _run_as_bot(action, conn):
        """The demo courier uses the same rules as a real one (its own transaction, after this one)."""
        conn.after_commit.append(action)

    # =================================================================
    # The live map
    # =================================================================

    def live(self, number, user_id=None, at=None):
        """Everything the tracking map needs. Positions of the courier only while it is on this job."""
        number = str(number or "").strip().upper()
        with self.db() as conn:
            s = conn.execute("SELECT id FROM shipments WHERE number = ?", (number,)).fetchone()
        if s is None:
            raise ShopError("Shipment not found.")
        try:
            self.courier_tick(at=at, only=s["id"])
        except Exception:                              # the map must still show something
            pass
        with self.db() as conn:
            s = conn.execute("SELECT * FROM shipments WHERE id = ?", (s["id"],)).fetchone()
            owner = user_id is not None and s["user_id"] == user_id
            trail = [(r["lat"], r["lng"]) for r in conn.execute(
                "SELECT lat, lng FROM courier_track WHERE shipment_id = ? ORDER BY id DESC LIMIT 300", (s["id"],))][::-1]
            courier = None
            if s["courier_id"] and s["status"] in (ASSIGNED, PICKED, OUT):
                c = conn.execute("SELECT c.lat, c.lng, c.demo, c.vehicle, u.name FROM couriers c JOIN users u "
                                 "ON u.id = c.user_id WHERE c.id = ?", (s["courier_id"],)).fetchone()
                pos = self.bot_position(s, at, conn) if c["demo"] and self.courier_demo_mode else (c["lat"], c["lng"])
                if pos and pos[0] is not None:
                    courier = {"lat": pos[0], "lng": pos[1], "name": c["name"].split()[0], "vehicle": c["vehicle"]}
            last = conn.execute("SELECT status, note, place, at FROM shipment_events WHERE shipment_id = ? "
                                "ORDER BY id DESC LIMIT 1", (s["id"],)).fetchone()
        self._warm_routes(s["id"])
        pickup = {"lat": s["from_lat"], "lng": s["from_lng"], "label": s["from_city"]}
        drop = ({"lat": s["to_lat"], "lng": s["to_lng"], "label": s["to_city"]} if s["to_lat"] is not None else None)
        if not owner:              # strangers see only the town centres, never the exact doors
            pickup = dict(pickup, **dict(zip(("lat", "lng"), self._hub(s, "from"))))
            if drop and self._key(s, "to"):
                drop = dict(drop, **dict(zip(("lat", "lng"), self._hub(s, "to"))))
            trail = []
        pts = [(pickup["lat"], pickup["lng"])] + ([(drop["lat"], drop["lng"])] if drop else []) + \
              ([(courier["lat"], courier["lng"])] if courier else [])
        hubs = []
        if s["zone"] != "local":
            p = self._hub(s, "from")
            hubs.append({"lat": p[0], "lng": p[1], "label": "Yo hub " + s["from_city"]})
            if self._key(s, "to"):
                p = self._hub(s, "to")
                hubs.append({"lat": p[0], "lng": p[1], "label": "Yo hub " + s["to_city"]})
        # the road the courier drives now (OpenStreetMap), for the real map
        road = []
        if courier and s["courier_id"]:
            with self.db() as conn:
                route, _ = self._bot_route(s, conn)
            road = [list(p) for p in route] if route else []
        elif s["zone"] == "local" and drop and owner:
            road = self.road_route((pickup["lat"], pickup["lng"]), (drop["lat"], drop["lng"]), fetch=False)["line"]
        steps = LOCAL_STEPS if s["zone"] == "local" else NETWORK_STEPS
        return {"number": s["number"], "status": s["status"], "step": steps.index(s["status"]) if s["status"] in steps else -1,
                "steps": steps, "pickup": pickup, "drop": drop, "hubs": hubs, "courier": courier, "trail": trail,
                "towns": towns_near(pts), "road": road if owner else [],
                "last": dict(last) if last else None, "done": s["status"] not in OPEN, "local": s["zone"] == "local"}

    # =================================================================
    # Admin
    # =================================================================

    def admin_shipments(self, status="", q="", limit=300):
        sql = ("SELECT s.*, u.name AS sender_account FROM shipments s JOIN users u ON u.id = s.user_id WHERE 1 = 1")
        args = []
        if status == "open":
            sql += f" AND s.status IN ({', '.join('?' * len(OPEN))})"
            args += list(OPEN)
        elif status:
            sql += " AND s.status = ?"
            args.append(status)
        if q:
            sql += " AND (s.number LIKE ? OR s.from_name LIKE ? OR s.to_name LIKE ? OR s.to_city LIKE ?)"
            args += [f"%{q}%"] * 4
        with self.db() as conn:
            rows = [dict(r) for r in conn.execute(sql + " ORDER BY s.id DESC LIMIT ?", args + [limit])]
            stats = {r[0]: r[1] for r in conn.execute("SELECT status, COUNT(*) FROM shipments GROUP BY status")}
            money = conn.execute("SELECT COALESCE(SUM(price), 0) FROM shipments WHERE status <> ?", (CANCELLED,)).fetchone()[0]
        return rows, stats, money

    def admin_couriers(self):
        with self.db() as conn:
            rows = conn.execute(
                "SELECT c.*, u.name, u.email, u.mobile, (SELECT COUNT(*) FROM shipments s WHERE s.courier_id = c.id "
                "AND s.status IN (?, ?, ?)) AS jobs FROM couriers c JOIN users u ON u.id = c.user_id "
                "ORDER BY CASE c.status WHEN 'Pending' THEN 0 ELSE 1 END, c.id", (ASSIGNED, PICKED, OUT)).fetchall()
        return [self._courier_dict(r) for r in rows]

    def decide_courier(self, courier_id, status, reason=""):
        if status not in COURIER_STATUSES[1:]:
            raise ShopError("Unknown decision.")
        with self.db() as conn:
            c = conn.execute("SELECT * FROM couriers WHERE id = ?", (courier_id,)).fetchone()
            if c is None:
                raise ShopError("Courier not found.")
            conn.execute("UPDATE couriers SET status = ?, reason = ?, decided_at = ?, online = CASE WHEN ? = 'Approved' "
                         "THEN online ELSE 0 END WHERE id = ?", (status, _clean(reason, 200), now(), status, courier_id))
            text = {"Approved": "You can now go online in the Yo Courier app and take jobs.",
                    "Rejected": "Your application to deliver with Yo was not accepted.",
                    "Suspended": "Your courier account is paused. Please contact Yo support."}[status]
            self.notify(conn, c["user_id"], "Courier account: {0}".replace("{0}", status),
                        text + (f" {reason}" if reason else ""), url="/driver")

    def admin_assign(self, number, courier_id):
        with self.db() as conn:
            s = conn.execute("SELECT * FROM shipments WHERE number = ?", (number,)).fetchone()
            if s is None or s["courier_id"] or s["status"] not in (BOOKED, AT_HUB):
                raise ShopError("This shipment is not waiting for a courier.")
            c = conn.execute("SELECT * FROM couriers WHERE id = ? AND status = 'Approved'", (courier_id,)).fetchone()
            if c is None:
                raise ShopError("Choose an approved courier.")
            here = (c["lat"], c["lng"]) if c["lat"] is not None else place_pos(c["place"] or c["city"])
            if not self._claim(conn, s, c["id"], here, now(), "Given by the admin"):
                raise ShopError("This shipment is not waiting for a courier.")

    def courier_cash_deposit(self, courier_id, by=""):
        with self.db() as conn:
            c = conn.execute("SELECT cash_in_hand FROM couriers WHERE id = ?", (courier_id,)).fetchone()
            if c is None or c[0] <= 0:
                raise ShopError("This courier has no cash to give.")
            conn.execute("UPDATE couriers SET cash_in_hand = 0 WHERE id = ?", (courier_id,))
            return c[0]

    def courier_photo(self, shipment_number):
        return f"private/pod/{shipment_number}.jpg"


class WrongCode(ShopError):
    """A wrong delivery code - the counter of tries must be saved (the transaction is committed)."""
