"""
tools/make_places.py - builds the world place files of Yo Delivery (run once by the developer, not by the app).

Sources (free, downloaded from the npm registry; GeoNames data is CC BY 4.0, OurAirports is public domain):
    all-the-cities 3.1.0      138 398 towns and villages with 1 000+ people (GeoNames cities1000)
    country-state-city 3.2.1  250 countries (currency, phone code, time zones), 4 963 regions, 148 038 towns
    rwanda 2.1.2              every province, district, sector, cell and village (umudugudu) of Rwanda
    indian-pincodes 1.1.0     154 874 post offices of India (most villages have one) with district and state
    airports-json 1.0.0 + @nwpr/airport-codes 3.0.3   airports with IATA code, size and time zone
Output (yo_delivery_py/data/geo/):
    countries.json, regions.tsv.gz, places.tsv.gz, airports.tsv.gz
Places without their own coordinates (Rwanda villages, Indian post offices) get the position of their district
centre (or region centre) and approx = 1; the app says that the point is approximate.
Run:  python tools/make_places.py <folder with the unpacked npm packages>
"""

import gzip
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import unicodedata
from collections import defaultdict

SRC = sys.argv[1] if len(sys.argv) > 1 else "."
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "data", "geo")
sys.path.insert(0, ROOT)
import places  # noqa: E402  (the 33 cities of the Yo network and the 12 big cities abroad of version 3)

os.makedirs(OUT, exist_ok=True)


def load(*parts):
    with open(os.path.join(SRC, *parts), encoding="utf-8") as f:
        return json.load(f)


def key(text):
    t = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(min(1, h)))


class Grid:
    """Nearest-point search in 1-degree cells (fast enough for 300 000 points)."""

    def __init__(self):
        self.cells = defaultdict(list)

    def add(self, lat, lng, item):
        self.cells[(int(math.floor(lat)), int(math.floor(lng)))].append((lat, lng, item))

    def nearest(self, lat, lng, test=None, rings=2):
        best = None
        cx, cy = int(math.floor(lat)), int(math.floor(lng))
        for dx in range(-rings, rings + 1):
            for dy in range(-rings, rings + 1):
                for la, lo, it in self.cells.get((cx + dx, cy + dy), ()):
                    if test and not test(it):
                        continue
                    d = km((lat, lng), (la, lo))
                    if best is None or d < best[0]:
                        best = (d, it)
        return best


def spread(lat, lng, seed, radius_km):
    """A stable point a few km from a centre (villages of one district are not drawn on one dot)."""
    h = int.from_bytes(hashlib.sha256(seed.encode()).digest()[:4], "big")
    ang = (h % 3600) / 3600 * 2 * math.pi
    dist = 0.5 + (h // 3600 % 1000) / 1000 * radius_km
    return (round(lat + dist / 111 * math.cos(ang), 5),
            round(lng + dist / (111 * max(0.2, math.cos(math.radians(lat)))) * math.sin(ang), 5))


# ------------------------------------------------------------------ countries, regions
csc = os.path.join("country-state-city-3.2.1", "package", "lib", "assets")
countries = {}
for c in load(csc, "country.json"):
    countries[c["isoCode"]] = {"name": c["name"], "currency": c["currency"], "phone": c["phonecode"],
                               "lat": float(c["latitude"] or 0), "lng": float(c["longitude"] or 0),
                               "zones": [z["zoneName"] for z in (c.get("timezones") or [])]}
regions = {}
for s in load(csc, "state.json"):
    try:
        regions[(s["countryCode"], s["isoCode"])] = (s["name"], float(s["latitude"]), float(s["longitude"]))
    except (TypeError, ValueError):
        regions[(s["countryCode"], s["isoCode"])] = (s["name"], None, None)
region_grid = Grid()
for (cc, code), (name, la, lo) in regions.items():
    if la is not None:
        region_grid.add(la, lo, (cc, name))

# ------------------------------------------------------------------ airports (+ time zones)
tz_by_iata = {a["iata"]: a["tz"] for a in load("nwpr-airport-codes-3.0.3", "package", "dist", "airports.json")
              if a.get("iata") and a.get("tz") and a["tz"] != "\\N"}
airports, tz_grid = [], Grid()
for a in load("nwpr-airport-codes-3.0.3", "package", "dist", "airports.json"):
    if a.get("tz") and a["tz"] != "\\N" and a.get("country"):
        tz_grid.add(a["latitude"], a["longitude"], a["tz"])
for a in load("airports-json-1.0.0", "package", "data", "airports.json"):
    if a["type"] not in ("large_airport", "medium_airport") or a["scheduled_service"] != "yes" or not a["iata_code"]:
        continue
    cc = a["iso_country"]
    la, lo = float(a["latitude_deg"]), float(a["longitude_deg"])
    tz = tz_by_iata.get(a["iata_code"])
    if not tz:
        z = countries.get(cc, {}).get("zones") or []
        tz = z[0] if len(z) == 1 else (tz_grid.nearest(la, lo) or (0, "UTC"))[1]
    airports.append([a["iata_code"], a["name"], a["municipality"] or a["name"], cc, round(la, 5), round(lo, 5), tz,
                     "large" if a["type"] == "large_airport" else "medium"])
print("airports", len(airports))


def tz_for(cc, la, lo):
    """The time zone of a point: the country's zone, or (big countries) the zone of the nearest airport that
    uses one of the country's zones."""
    z = countries.get(cc, {}).get("zones") or []
    if len(z) == 1:
        return z[0]
    for rings in (3, 8, 20):
        best = tz_grid.nearest(la, lo, (lambda t: t in z) if z else None, rings=rings)
        if best:
            return best[1]
    return z[0] if z else "UTC"


# ------------------------------------------------------------------ towns of the world
out = []          # id, name, alt, kind, cc, region, district, area, lat, lng, pop, tz, pin, approx
csc_cities = load(csc, "city.json")
csc_grid = Grid()
for name, cc, scode, la, lo in csc_cities:
    try:
        csc_grid.add(float(la), float(lo), (name, cc, scode))
    except (TypeError, ValueError):
        pass


def region_of(cc, la, lo):
    near = csc_grid.nearest(la, lo, lambda it: it[1] == cc, rings=1)
    if near and near[0] < 60:
        return regions.get((cc, near[1][2]), ("",))[0]
    near = region_grid.nearest(la, lo, lambda it: it[0] == cc, rings=3)
    return near[1][1] if near else ""


# the hubs of the Yo network (their id is their name, as in versions 3 and 4)
legacy = []
for n, c in places.CITIES.items():
    legacy.append([n, n, "", "hub", "IN", c[0], "", "", c[2], c[3], 0, "Asia/Kolkata", c[1], 0])
for cc, (n, la, lo) in places.WORLD_CITIES.items():
    legacy.append([n, n, "", "hub", cc, "", "", "", la, lo, 0, tz_for(cc, la, lo), "", 0])
legacy_grid = Grid()
for row in legacy:
    legacy_grid.add(row[8], row[9], (key(row[1]), row[4]))

seen = Grid()
towns = json.load(open(os.path.join(SRC, "cities1000.json"), encoding="utf-8"))
for t in towns:
    lo, la = t["loc"]["coordinates"]
    cc = t["country"]
    if cc not in countries:
        continue
    k, ak = key(t["name"]), key(t["altName"])
    dup = legacy_grid.nearest(la, lo, lambda it: it[1] == cc and it[0] in (k, ak), rings=1)
    if dup and dup[0] < 25:
        continue                                            # the Yo hub of this city is already in the list
    pop = int(t["population"] or 0)
    kind = "city" if t["featureCode"] in ("PPLC", "PPLA") or pop >= 100_000 else "town" if pop >= 10_000 else "village"
    row = [f"g{t['cityId']}", t["name"], t["altName"] if t["altName"] != t["name"] else "", kind, cc,
           region_of(cc, la, lo), "", "", round(la, 5), round(lo, 5), pop, tz_for(cc, la, lo), "", 0]
    out.append(row)
    seen.add(la, lo, (k, cc))
print("towns 1000+", len(out))
added = 0
for i, (name, cc, scode, la, lo) in enumerate(csc_cities):
    try:
        la, lo = float(la), float(lo)
    except (TypeError, ValueError):
        continue
    k = key(name)
    near = seen.nearest(la, lo, lambda it: it[1] == cc and it[0] == k, rings=1)
    if near and near[0] < 8:
        continue
    dup = legacy_grid.nearest(la, lo, lambda it: it[1] == cc and it[0] == k, rings=1)
    if dup and dup[0] < 25:
        continue
    out.append([f"c{i}", name, "", "town", cc, regions.get((cc, scode), ("",))[0], "", "", round(la, 5),
                round(lo, 5), 0, tz_for(cc, la, lo), "", 0])
    added += 1
print("more towns", added)

# ------------------------------------------------------------------ Rwanda: provinces, districts, sectors, cells, villages
rw_js = os.path.join(SRC, "rw2", "package")
tree = json.loads(subprocess.check_output(["node", "-e", "console.log(JSON.stringify(require('./data/provinces')))"],
                                          cwd=rw_js))
PROVINCE = {"kigali": "Kigali City", "north": "Northern Province", "south": "Southern Province",
            "east": "Eastern Province", "west": "Western Province"}
# the town where each district office is (historic names in brackets are how GeoNames often calls them)
RW_HQ = {"Musanze": ["Musanze", "Ruhengeri"], "Huye": ["Huye", "Butare"], "Rubavu": ["Gisenyi", "Rubavu"],
         "Rusizi": ["Cyangugu", "Rusizi"], "Muhanga": ["Gitarama", "Muhanga"], "Karongi": ["Kibuye", "Karongi"],
         "Ngoma": ["Kibungo", "Ngoma"], "Bugesera": ["Nyamata", "Bugesera"], "Gicumbi": ["Byumba", "Gicumbi"],
         "Nyamagabe": ["Gikongoro", "Nyamagabe"], "Nyarugenge": ["Kigali"], "Gasabo": ["Kigali"], "Kicukiro": ["Kigali"]}
rw_points = [(key(r[1]), r[8], r[9]) for r in out + legacy if r[4] == "RW"]


# approximate centres of the 30 district offices (latitude, longitude) - the dataset has no towns for some districts
RW_DISTRICTS = {
    "Nyarugenge": (-1.950, 30.059), "Gasabo": (-1.910, 30.100), "Kicukiro": (-1.970, 30.103),
    "Gakenke": (-1.694, 29.784), "Burera": (-1.473, 29.833), "Rulindo": (-1.760, 29.995), "Gicumbi": (-1.576, 30.068),
    "Musanze": (-1.500, 29.635), "Nyabihu": (-1.633, 29.517), "Rubavu": (-1.702, 29.256), "Rutsiro": (-1.940, 29.320),
    "Karongi": (-2.060, 29.348), "Ngororero": (-1.865, 29.625), "Nyamasheke": (-2.330, 29.090),
    "Rusizi": (-2.485, 28.908), "Muhanga": (-2.074, 29.757), "Kamonyi": (-2.010, 29.900), "Ruhango": (-2.230, 29.780),
    "Nyanza": (-2.352, 29.751), "Huye": (-2.597, 29.739), "Gisagara": (-2.610, 29.830), "Nyaruguru": (-2.640, 29.550),
    "Nyamagabe": (-2.480, 29.570), "Bugesera": (-2.149, 30.093), "Rwamagana": (-1.949, 30.435),
    "Kayonza": (-1.900, 30.510), "Ngoma": (-2.160, 30.543), "Kirehe": (-2.270, 30.650), "Gatsibo": (-1.600, 30.410),
    "Nyagatare": (-1.294, 30.328),
}


def rw_centre(district, province):
    if district in RW_DISTRICTS:
        return RW_DISTRICTS[district][0], RW_DISTRICTS[district][1], 0
    for name in RW_HQ.get(district, [district]):
        for k, la, lo in rw_points:
            if k == key(name):
                return la, lo, 0
    pkey = {"Kigali City": "kigali"}.get(province, province.split()[0].lower())
    for (cc, code), (name, la, lo) in regions.items():
        if cc == "RW" and la is not None and pkey in key(name):
            return la, lo, 1
    return -1.94, 29.87, 1


def walk_rw():
    n = 0
    rows = []

    def items(node):
        if isinstance(node, dict):
            return node.items()
        return [(v, None) for v in node]

    for pkey, pnode in tree.items():
        province = PROVINCE.get(pkey.lower(), pkey.title())
        districts = pnode.get("districts", pnode) if isinstance(pnode, dict) else {}
        for dname, dnode in districts.items():
            district = dname.title()
            la, lo, _ = rw_centre(district, province)
            sectors = dnode.get("sectors", dnode) if isinstance(dnode, dict) else {}
            for sname, snode in sectors.items():
                sector = sname.title()
                p = spread(la, lo, f"{district}/{sector}", 9)
                n += 1
                rows.append([f"rw{n}", sector, "", "sector", "RW", province, district, "", p[0], p[1], 0,
                             "Africa/Kigali", "", 1])
                cells = snode if isinstance(snode, dict) else {}
                for cname, villages in cells.items():
                    cp = spread(p[0], p[1], f"{district}/{sector}/{cname}", 3)
                    n += 1
                    rows.append([f"rw{n}", cname, "", "cell", "RW", province, district, f"{sector} sector", cp[0],
                                 cp[1], 0, "Africa/Kigali", "", 1])
                    for v in villages or []:
                        vp = spread(cp[0], cp[1], f"{district}/{sector}/{cname}/{v}", 1.5)
                        n += 1
                        rows.append([f"rw{n}", v, "", "village", "RW", province, district,
                                     f"{sector} sector, {cname} cell", vp[0], vp[1], 0, "Africa/Kigali", "", 1])
    return rows


rw = walk_rw()
print("Rwanda", len(rw), {k: sum(1 for r in rw if r[3] == k) for k in ("sector", "cell", "village")})
out += rw

# ------------------------------------------------------------------ India: post offices (villages and city localities)
in_points = defaultdict(list)
for r in out + legacy:
    if r[4] == "IN":
        in_points[key(r[1])].append((r[8], r[9], key(r[5])))


def title(text):
    return " ".join(w.capitalize() if w.isupper() or w.islower() else w for w in str(text).replace("&", "and").split())


def in_centre(district, state):
    cands = in_points.get(key(district), [])
    for la, lo, st in cands:
        if key(state)[:5] in st or not st:
            return la, lo, 0
    if cands:
        return cands[0][0], cands[0][1], 0
    for (cc, code), (name, la, lo) in regions.items():
        if cc == "IN" and la is not None and key(name) == key(state):
            return la, lo, 1
    return 22.0, 79.0, 1


seen_in, n = set(), 0
for p in load("indian-pincodes-1.1.0", "package", "data", "pincodes.json"):
    name = re.sub(r"\s+(B\.?O|S\.?O|H\.?O|G\.?P\.?O)\.?$", "", str(p["name"]).strip(), flags=re.I)
    pin = str(p["pincode"])
    k = (key(name), pin)
    if k in seen_in or not k[0]:
        continue
    seen_in.add(k)
    district, state = title(p["district"]), title(p["state"])
    la, lo, _ = in_centre(district, state)
    q = spread(la, lo, f"{state}/{district}/{name}/{pin}", 18)
    n += 1
    out.append([f"in{n}", title(name), "", "locality", "IN", state, district, "", q[0], q[1], 0, "Asia/Kolkata",
                pin, 1])
print("India post offices", n)

# ------------------------------------------------------------------ write
out = legacy + out
with gzip.open(os.path.join(OUT, "places.tsv.gz"), "wt", encoding="utf-8", compresslevel=9) as f:
    for r in out:
        f.write("\t".join(str(x).replace("\t", " ") for x in r) + "\n")
with gzip.open(os.path.join(OUT, "airports.tsv.gz"), "wt", encoding="utf-8", compresslevel=9) as f:
    for a in airports:
        f.write("\t".join(str(x).replace("\t", " ") for x in a) + "\n")
with gzip.open(os.path.join(OUT, "regions.tsv.gz"), "wt", encoding="utf-8", compresslevel=9) as f:
    for (cc, code), (name, la, lo) in sorted(regions.items()):
        f.write("\t".join(str(x) for x in (cc, code, name, la if la is not None else "", lo if lo is not None else "")) + "\n")
json.dump(countries, open(os.path.join(OUT, "countries.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=0)
print("places", len(out), "->", OUT)
