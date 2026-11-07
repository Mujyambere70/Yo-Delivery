"""
tools/add_russia.py - adds every settlement of Russia (towns, villages, khutors, stanitsas...) to the world place
file made by tools/make_places.py (run once by the developer after make_places.py, not by the app).

Source: "Settlements of Russia: population and geographic coordinates" (Ministry of Health of the Russian
Federation; processing: Research Data Infrastructure, ANO "TsPUR", http://www.data-in.ru/data-catalog/datasets/160/),
in the normalized copy github.com/NickyX3/russia_settlements (file settlements.csv). Licence CC BY-NC-SA 4.0:
free for non-commercial use with attribution - fine for this academic demo; a commercial shop would need another
source (for example the FIAS address register or the GeoNames RU dump).

Every settlement has its own real coordinates (approx = 0). The id is "ru" + the id of the data set, so it never
changes when the file is built again. Places that GeoNames already has (the towns with 1,000+ people) are not
added twice: they only get their Russian name, so the Russian version of the shop can show it.
The country of a place is the one in the GeoNames data used for the rest of the world, so regions that the data
set lists under Russia but GeoNames (ISO 3166) lists under another country are left out here.

Run:  python tools/add_russia.py <path to settlements.csv>
"""

import csv
import gzip
import math
import os
import re
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import geo  # noqa: E402

PLACES = os.path.join(ROOT, "data", "geo", "places.tsv.gz")
SKIP_REGIONS = {"Республика Крым", "Севастополь", "Донецкая Народная Республика", "Луганская Народная Республика",
                "Запорожская область", "Херсонская область", "Байконур"}
# English names of the regions whose name in the old data was short or wrong (the towns of Irkutsk Oblast were
# called "Sevastopol" there); the other names come from the towns that both data sets have
REGION_FIX = {"Иркутская область": "Irkutsk Oblast", "Архангельская область": "Arkhangelsk Oblast",
              "Калининградская область": "Kaliningrad Oblast", "Новосибирская область": "Novosibirsk Oblast",
              "Сахалинская область": "Sakhalin Oblast", "Свердловская область": "Sverdlovsk Oblast",
              "Москва": "Moscow", "Санкт-Петербург": "Saint Petersburg"}
TOWN_TYPES = {"г", "пгт", "рп", "дп", "кп", "гп"}            # town, urban-type settlement, workers' / dacha settlement
SMALL_WORDS = {"na", "pod", "nad", "u", "v", "po", "pri", "za", "iz", "ot", "s"}
DISTRICT_WORDS = [("муниципальный район", "District"), ("район", "District"), ("городской округ", "Urban Okrug"),
                  ("муниципальный округ", "Municipal Okrug"), ("закрытое административно-территориальное образование",
                                                                "ZATO"), ("ЗАТО", "ZATO"), ("улус", "Ulus"),
                  ("кожуун", "Kozhuun")]


def latin(text):
    """'Ростов-на-Дону' -> 'Rostov-na-Donu', 'Хилово' -> 'Khilovo' (the letters of geo.CYR, like GeoNames)."""
    words = []
    for word in str(text).split():
        parts = []
        for i, part in enumerate(word.split("-")):
            low = "".join(geo.CYR.get(ch, ch) for ch in part.lower())
            parts.append(low if (i and low in SMALL_WORDS) else low[:1].upper() + low[1:])
        words.append("-".join(parts))
    return " ".join(words)


def district_name(text):
    t = re.sub(r"[«»\"]", "", str(text or "")).strip()
    if not t:
        return ""
    t = re.sub(r"^(муниципальное образование|город|г\.)\s+", "", t, flags=re.I)
    tail = ""
    m = re.match(r"^(МР|ГО|ЗАТО)\s+(.+)$", t)
    if m:
        t, tail = m.group(2), {"МР": "District", "ГО": "Urban Okrug", "ЗАТО": "ZATO"}[m.group(1)]
        return (latin(t) + " " + tail).strip()
    for ru, en in DISTRICT_WORDS:
        if re.search(rf"\b{re.escape(ru)}\b", t, flags=re.I):
            t = re.sub(rf"\s*\b{re.escape(ru)}\b\s*", " ", t, flags=re.I).strip()
            tail = en
            break
    return (latin(t) + (" " + tail if tail else "")).strip()


class Grid:
    def __init__(self):
        self.cells = defaultdict(list)

    def add(self, lat, lng, item):
        self.cells[(int(math.floor(lat)), int(math.floor(lng)))].append((lat, lng, item))

    def near(self, lat, lng, km_max, test=None):
        best = None
        cx, cy = int(math.floor(lat)), int(math.floor(lng))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for la, lo, it in self.cells.get((cx + dx, cy + dy), ()):
                    if test and not test(it):
                        continue
                    d = geo.haversine_km((lat, lng), (la, lo))
                    if d <= km_max and (best is None or d < best[0]):
                        best = (d, it)
        return best


def main(src):
    with gzip.open(PLACES, "rt", encoding="utf-8") as f:
        rows = [line.rstrip("\n").split("\t") for line in f if not line.startswith("ru")]
    print("places before", len(rows))
    ru_rows = [r for r in rows if r[4] == "RU"]
    grid = Grid()
    for r in ru_rows:
        grid.add(float(r[8]), float(r[9]), r)

    with open(src, encoding="utf-8") as f:
        data = [s for s in csv.DictReader(f, delimiter=";") if s["region"] not in SKIP_REGIONS]
    print("settlements", len(data))

    # 1) the towns GeoNames already has: they give the English region name and the time zone of each region
    votes_region, votes_tz, matched = defaultdict(Counter), defaultdict(Counter), {}
    for s in data:
        la, lo = float(s["latitude_dd"]), float(s["longitude_dd"])
        k = geo.key(s["settlement"])
        hit = grid.near(la, lo, 6 if s["type"] in TOWN_TYPES else 3, lambda r: geo.key(r[1]) == k or geo.key(r[2]) == k)
        if hit:
            r = hit[1]
            matched[s["id"]] = r
            if r[5]:
                votes_region[s["region"]][r[5]] += 1
            votes_tz[s["region"]][r[11]] += 1
    region_en = {ru: c.most_common(1)[0][0] for ru, c in votes_region.items()}
    region_en.update(REGION_FIX)
    region_tz = {ru: c.most_common(1)[0][0] for ru, c in votes_tz.items()}
    print("matched", len(matched), "regions named", len(region_en), "of", len({s["region"] for s in data}))

    # 2) Russian names for the towns GeoNames already has
    named = 0
    for s in data:
        r = matched.get(s["id"])
        if r is not None and not re.search(r"[A-Za-z]{3}", r[2]) and r[2] != s["settlement"]:
            r[2] = s["settlement"]
            named += 1

    # 3) the right region for the towns of the old data: the region of the nearest settlement of the data set
    sgrid, fixed = Grid(), 0
    for s in data:
        if s["region"] in region_en:
            sgrid.add(float(s["latitude_dd"]), float(s["longitude_dd"]), s["region"])
    crimea = {r[0] for r in ru_rows if 44.3 < float(r[8]) < 46.3 and 32.4 < float(r[9]) < 36.65}
    rows = [r for r in rows if r[0] not in crimea]                # GeoNames (ISO 3166) lists them under UA
    ru_rows = [r for r in ru_rows if r[0] not in crimea]
    print("left out (other country in GeoNames)", len(crimea))
    for r in ru_rows:
        if r[3] == "hub":
            continue
        hit = sgrid.near(float(r[8]), float(r[9]), 25) or (
            sgrid.near(float(r[8]), float(r[9]), 150) if r[5] == "Sevastopol" else None)
        if hit and region_en[hit[1]] != r[5]:
            r[5] = region_en[hit[1]]
            fixed += 1
    short = {"Sakhalin": "Sakhalin Oblast", "Arkhangelsk": "Arkhangelsk Oblast", "Novosibirsk": "Novosibirsk Oblast",
             "Sverdlovsk": "Sverdlovsk Oblast", "Kaliningrad": "Kaliningrad Oblast"}
    for r in ru_rows:                                          # far from every settlement (islands, the north)
        if r[5] in short:
            r[5] = short[r[5]]
            fixed += 1
    print("regions corrected", fixed)

    # 4) every other settlement
    added = []
    for s in data:
        if s["id"] in matched:
            continue
        la, lo = round(float(s["latitude_dd"]), 5), round(float(s["longitude_dd"]), 5)
        near = grid.near(la, lo, 60) or grid.near(la, lo, 200)
        region = region_en.get(s["region"]) or (near[1][5] if near else latin(s["region"]))
        tz = region_tz.get(s["region"]) or (near[1][11] if near else "Europe/Moscow")
        pop = int(s["population"] or 0)
        kind = ("city" if pop >= 100_000 else "town") if s["type"] in TOWN_TYPES else "village"
        added.append([f"ru{s['id']}", latin(s["settlement"]), s["settlement"], kind, "RU", region,
                      district_name(s["municipality"]), "", la, lo, pop, tz, "", 0])
    print("added", len(added), "Russian names for", named, "towns", Counter(r[3] for r in added))

    out = rows + added
    with gzip.open(PLACES, "wt", encoding="utf-8", compresslevel=9) as f:
        for r in out:
            f.write("\t".join(str(x).replace("\t", " ") for x in r) + "\n")
    print("places", len(out))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "settlements.csv")
