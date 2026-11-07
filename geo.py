"""
geo.py - the places of the world for Yo Delivery (version 5).

485 665 places: every town and village of the world with 1 000+ people (GeoNames), more towns of every country,
every settlement of Russia (151 212 villages, khutors and stanitsas with their own coordinates and Russian names),
every province, district, sector, cell and village of Rwanda, the post offices of India (they cover most villages,
with their PIN code), the 33 cities of the Yo network and 3 214 airports with scheduled flights.
The data files are in data/geo (made by tools/make_places.py and tools/add_russia.py). On the first start they are
loaded into a small read-only SQLite file (data/geo/places.db, about 100 MB) that every server process searches -
also when the shop itself uses PostgreSQL.

A place id is the name for the 45 places of versions 3 and 4 ("Ambala Cantt", "Moscow") and a short code for the
others ("g1502026" = Krasnoyarsk, "rw6833" = Mugunga sector, "in4521" = an Indian post office, "ru100" = the
village Dobroe in Tula Oblast). In the Russian version the places of Russia are shown with their Russian names.
Rwanda villages and Indian post offices have the position of their district centre plus a few km (approx = 1).

Road distances come from OSRM (OpenStreetMap routing, free demo server) and are cached; without internet the
straight-line distance x 1.3 is used, so the shop always works. Switch the service off with YO_ROUTING=0.
"""

import gzip
import json
import math
import os
import re
import sqlite3
import threading
import unicodedata
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
GEO_DIR = os.path.join(HERE, "data", "geo")
VERSION = "7"                 # version 7: + the postal-code directory
ROAD_FACTOR = 1.3
KIND_RANK = {"hub": 16, "city": 7, "town": 5, "sector": 4, "locality": 3, "cell": 2, "village": 2}
KIND_NAMES = {"hub": "Yo hub city", "city": "City", "town": "Town", "village": "Village", "sector": "Sector",
              "cell": "Cell", "locality": "Village / locality"}
_lock = threading.Lock()
_local = threading.local()

# Russian / Ukrainian / Belarusian letters -> Latin (so "Красноярск" finds "Krasnoyarsk")
CYR = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюяіїєў",
               ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t",
                "u", "f", "kh", "ts", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya", "i", "yi", "ye", "u"]))


CYRILLIC = re.compile("[а-яё]", re.I)
ID_RE = re.compile(r"(g|c|rw|in|ru)\d+")                  # the short place ids (see the top of this file)


def db_path():
    return os.environ.get("YO_PLACES_DB") or os.path.join(GEO_DIR, "places.db")


def key(text):
    """'Kraśnik, Łódź' -> 'krasnik lodz'; Cyrillic is written with Latin letters."""
    t = "".join(CYR.get(ch, ch) for ch in str(text or "").lower())
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def ensure():
    """Build places.db from the data files once (about 20 seconds); later starts open it at once."""
    path = db_path()
    if os.path.exists(path):
        try:
            con = sqlite3.connect(path)
            ok = con.execute("SELECT value FROM meta WHERE key = 'version'").fetchone()
            con.close()
            if ok and ok[0] == VERSION:
                return path
        except sqlite3.Error:
            pass
    with _lock:
        if os.path.exists(path):
            try:
                con = sqlite3.connect(path)
                if con.execute("SELECT value FROM meta WHERE key = 'version'").fetchone()[0] == VERSION:
                    con.close()
                    return path
                con.close()
            except sqlite3.Error:
                pass
        tmp = path + ".building"
        if os.path.exists(tmp):
            os.remove(tmp)
        con = sqlite3.connect(tmp)
        con.executescript("""
            PRAGMA journal_mode = OFF; PRAGMA synchronous = OFF;
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE places (id TEXT PRIMARY KEY, name TEXT, alt TEXT, kind TEXT, cc TEXT, region TEXT,
                district TEXT, area TEXT, lat REAL, lng REAL, pop INTEGER, tz TEXT, pin TEXT, approx INTEGER,
                k TEXT, ak TEXT, rank REAL);
            CREATE TABLE airports (iata TEXT PRIMARY KEY, name TEXT, city TEXT, cc TEXT, lat REAL, lng REAL, tz TEXT,
                size TEXT);
            CREATE TABLE postcodes (cc TEXT, code TEXT, place TEXT, state TEXT, lat REAL, lng REAL,
                PRIMARY KEY (cc, code));
        """)
        rows = []
        with gzip.open(os.path.join(GEO_DIR, "places.tsv.gz"), "rt", encoding="utf-8") as f:
            for line in f:
                p = line.rstrip("\n").split("\t")
                pop = int(p[10] or 0)
                rank = KIND_RANK.get(p[3], 1) + (math.log10(pop) if pop > 0 else 0)
                rows.append((p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7], float(p[8]), float(p[9]), pop, p[11],
                             p[12], int(p[13]), key(p[1]), key(p[2]), round(rank, 3)))
                if len(rows) >= 20000:
                    con.executemany("INSERT OR IGNORE INTO places VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
                    rows = []
        con.executemany("INSERT OR IGNORE INTO places VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        with gzip.open(os.path.join(GEO_DIR, "airports.tsv.gz"), "rt", encoding="utf-8") as f:
            con.executemany("INSERT OR IGNORE INTO airports VALUES (?,?,?,?,?,?,?,?)",
                            [tuple(line.rstrip("\n").split("\t")) for line in f])
        pc = os.path.join(GEO_DIR, "postcode_dir.tsv.gz")          # version 7 (GeoNames postal codes, CC BY 4.0)
        if os.path.exists(pc):
            with gzip.open(pc, "rt", encoding="utf-8") as f:
                con.executemany("INSERT OR IGNORE INTO postcodes VALUES (?,?,?,?,?,?)",
                                [tuple(line.rstrip("\n").split("\t")) for line in f if line.strip()])
        con.executescript("""
            CREATE INDEX idx_k ON places(k, rank); CREATE INDEX idx_ak ON places(ak);
            CREATE INDEX idx_pin ON places(pin); CREATE INDEX idx_cc_k ON places(cc, k);
            CREATE INDEX idx_lat ON places(lat, lng);
        """)
        con.execute("INSERT INTO meta VALUES ('version', ?)", (VERSION,))
        con.commit()
        con.close()
        os.replace(tmp, path)
    return path


def _con():
    con = getattr(_local, "con", None)
    if con is None:
        con = sqlite3.connect(f"file:{ensure()}?mode=ro", uri=True, check_same_thread=False)
        con.row_factory = sqlite3.Row
        _local.con = con
    return con


_countries = None


def countries():
    global _countries
    if _countries is None:
        with open(os.path.join(GEO_DIR, "countries.json"), encoding="utf-8") as f:
            _countries = json.load(f)
    return _countries


def country(cc):
    return countries().get(str(cc or "").upper())


def _dict(r, lang="en"):
    if r is None:
        return None
    d = dict(r)
    d.pop("k", None), d.pop("ak", None)
    if lang == "ru" and d["cc"] == "RU" and CYRILLIC.search(d["alt"] or ""):
        d["name_en"], d["name"] = d["name"], d["alt"]           # "Красноярск" in the Russian version
    cname = _country_name(d["cc"], lang)
    parts = [d["area"], d["district"], d["region"], cname]
    d["country_name"] = cname
    d["label"] = ", ".join([d["name"]] + [x for x in parts if x and x != d["name"]])
    d["short"] = d["name"] + (f" ({d['district']})" if d["district"] else f" ({d['region']})" if d["region"] else "")
    return d


def _country_name(cc, lang="en"):
    try:
        import world
        return world.country_name(cc, lang)
    except Exception:
        c = country(cc)
        return c["name"] if c else cc


def get(place_id, lang="en"):
    """A place by its id (or None)."""
    if not place_id:
        return None
    return _dict(_con().execute("SELECT * FROM places WHERE id = ?", (str(place_id),)).fetchone(), lang)


def search(q, cc=None, limit=10, lang="en", kinds=None):
    """Places whose name starts with q (also a second name, a PIN code, or "village, district")."""
    q = str(q or "").strip()
    if not q:
        return []
    con = _con()
    if re.fullmatch(r"\d{6}", q):                             # an Indian PIN code
        rows = con.execute("SELECT * FROM places WHERE pin = ? ORDER BY rank DESC LIMIT ?", (q, limit)).fetchall()
        return [_dict(r, lang) for r in rows]
    parts = [key(p) for p in re.split(r"[,/]", q) if key(p)]
    if not parts:
        return []
    first, rest = parts[0], parts[1:]
    sql = ("SELECT * FROM places WHERE ((k >= ? AND k < ?) OR (ak >= ? AND ak < ?))"
           + (" AND cc = ?" if cc else "") + (f" AND kind IN ({','.join('?' * len(kinds))})" if kinds else "")
           + " ORDER BY (k = ?) DESC, rank DESC LIMIT ?")
    args = [first, first + "\uffff", first, first + "\uffff"] + ([cc.upper()] if cc else []) + list(kinds or []) + \
           [first, 400 if rest else limit]
    rows = con.execute(sql, args).fetchall()
    if rest:                                                  # "Mugunga, Gakenke" -> keep those in Gakenke
        rows = [r for r in rows if all(p in key(" ".join([r["area"], r["district"], r["region"], r["cc"],
                                                          _country_name(r["cc"])])) for p in rest)]
    return [_dict(r, lang) for r in rows[:limit]]


def find(name, cc=None, lang="en"):
    """The best place for a typed name (exact name first, the biggest place wins)."""
    if not name:
        return None
    p = get(name, lang)                                       # an id, or one of the 45 old city names
    if p:
        return p
    hits = search(name, cc=cc, limit=8, lang=lang)
    k = key(str(name).split(",")[0])
    exact = [h for h in hits if key(h["name"]) == k]
    return (exact or hits or [None])[0]


def haversine_km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(min(1.0, h)))


def straight_road_km(a, b):
    return round(haversine_km(a, b) * ROAD_FACTOR, 1)


def nearby(lat, lng, km=50, limit=12, min_rank=0):
    """Places around a point (for the map labels and "use my location")."""
    d = km / 111.0
    rows = _con().execute("SELECT * FROM places WHERE lat BETWEEN ? AND ? AND lng BETWEEN ? AND ? AND rank >= ? "
                          "ORDER BY rank DESC LIMIT ?",
                          (lat - d, lat + d, lng - d / max(0.2, math.cos(math.radians(lat))),
                           lng + d / max(0.2, math.cos(math.radians(lat))), min_rank, limit * 4)).fetchall()
    out = [_dict(r) for r in rows]
    out.sort(key=lambda p: haversine_km((lat, lng), (p["lat"], p["lng"])))
    return out[:limit]


def closest(lat, lng, km=20, min_rank=0):
    """Version 6: the place nearest to a point, also a small village (for GTFS stops and GPS points)."""
    d = km / 111.0
    c = max(0.2, math.cos(math.radians(lat)))
    rows = _con().execute("SELECT * FROM places WHERE lat BETWEEN ? AND ? AND lng BETWEEN ? AND ? AND rank >= ? "
                          "ORDER BY (lat - ?) * (lat - ?) + (lng - ?) * (lng - ?) * ? LIMIT 12",
                          (lat - d, lat + d, lng - d / c, lng + d / c, min_rank, lat, lat, lng, lng, c * c)).fetchall()
    best = min((_dict(r) for r in rows), key=lambda p: haversine_km((lat, lng), (p["lat"], p["lng"])), default=None)
    return best if best and haversine_km((lat, lng), (best["lat"], best["lng"])) <= km else None


def nearest_place(lat, lng):
    hits = nearby(lat, lng, km=25, limit=1, min_rank=2)
    return hits[0] if hits else None


def in_box(lat1, lat2, lng1, lng2, limit=14, min_rank=5):
    """The biggest places inside a box (names for the drawn map)."""
    rows = _con().execute("SELECT * FROM places WHERE lat BETWEEN ? AND ? AND lng BETWEEN ? AND ? AND rank >= ? "
                          "AND kind IN ('city', 'town') AND pop >= 20000 ORDER BY pop DESC LIMIT ?",
                          (lat1, lat2, lng1, lng2, min_rank, limit)).fetchall()
    return [_dict(r) for r in rows]


_stats = None


def stats():
    """How many places there are: in total, per kind, per country (for the /places page and the report)."""
    global _stats
    if _stats is None:
        con = _con()
        kinds = dict(con.execute("SELECT kind, COUNT(*) FROM places GROUP BY kind").fetchall())
        per = dict(con.execute("SELECT cc, COUNT(*) FROM places GROUP BY cc").fetchall())
        _stats = {"total": sum(kinds.values()), "kinds": kinds, "countries": len(per), "per_country": per,
                  "airports": con.execute("SELECT COUNT(*) FROM airports").fetchone()[0],
                  "rw_villages": kinds.get("village", 0) and con.execute(
                      "SELECT COUNT(*) FROM places WHERE cc = 'RW' AND kind = 'village'").fetchone()[0],
                  "ru_villages": con.execute(
                      "SELECT COUNT(*) FROM places WHERE cc = 'RU' AND kind = 'village'").fetchone()[0],
                  "in_post": con.execute("SELECT COUNT(*) FROM places WHERE pin != ''").fetchone()[0]}
    return _stats


# ------------------------------------------------------------------ airports

_airports = None


def postcode(cc, code):
    """Version 7: the directory entry of a postal code (dict) or None; Great Britain is looked up by its outward part."""
    code = " ".join(str(code or "").upper().split())
    if cc == "GB" and code:
        code = code.split()[0] if " " in code else code[:-3]
    r = _con().execute("SELECT * FROM postcodes WHERE cc = ? AND code = ?", (cc, code)).fetchone()
    return dict(r) if r else None


def postcode_countries():
    return {r[0] for r in _con().execute("SELECT DISTINCT cc FROM postcodes")}


def airports():
    global _airports
    if _airports is None:
        _airports = [dict(r) for r in _con().execute("SELECT * FROM airports")]
        for a in _airports:
            a["lat"], a["lng"] = float(a["lat"]), float(a["lng"])
    return _airports


def airport(iata):
    for a in airports():
        if a["iata"] == str(iata or "").upper():
            return a
    return None


def nearest_airports(lat, lng, within_km=200, large_only=False, limit=3):
    out = []
    for a in airports():
        if large_only and a["size"] != "large":
            continue
        d = haversine_km((lat, lng), (a["lat"], a["lng"]))
        if d <= within_km:
            out.append((d, a))
    out.sort(key=lambda x: x[0])
    return [dict(a, km=round(d)) for d, a in out[:limit]]


# ------------------------------------------------------------------ road distance (OSRM) with a fallback

OSRM = "https://router.project-osrm.org/route/v1/driving/{a};{b}?overview=simplified&geometries=geojson"


def osrm_route(a, b, timeout=3):
    """(km, [[lat, lng], ...]) by road, or None (no internet, no road - e.g. across the sea - or switched off)."""
    if os.environ.get("YO_ROUTING", "1") == "0":
        return None
    url = OSRM.format(a=f"{a[1]:.5f},{a[0]:.5f}", b=f"{b[1]:.5f},{b[0]:.5f}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "YoDelivery/5 (academic demo project)"})
        with urllib.request.urlopen(req, timeout=timeout) as r:     # nosec B310 - a fixed https address (OSRM)
            data = json.loads(r.read().decode())
        route = data["routes"][0]
        line = [[round(p[1], 5), round(p[0], 5)] for p in route["geometry"]["coordinates"]]
        return round(route["distance"] / 1000, 1), line
    except Exception:
        return None
