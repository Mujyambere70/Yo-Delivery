"""
addresscheck.py - version 6: checks of postal codes and addresses before a parcel or an order goes out.

    * the form of the postal code of 186 countries (data/geo/postcodes.json, from the npm package
      postcode-validator, MIT licence) - 110001 in India, 660049 in Russia, SW1A 1AA in Britain, 75008 in France
    * countries without postal codes (Rwanda, Kenya, the Emirates ...): the field may stay empty
    * India: the PIN code must belong to a post office we know (154,905 post offices with their PIN), and its
      district should be the district of the place that was chosen ("PIN 133207 is in Ambala, not in Pune")
    * version 7: 11 more countries have a directory of their postal codes (GeoNames, CC BY 4.0): Russia, Belarus,
      Germany, France, Great Britain (outward part), the USA, Spain, Italy, Turkey, South Africa and Kenya. A code
      that is not in the directory gets a warning, and a code far from the chosen place gets a warning with the town
      of the code ("660049 is Krasnoyarsk")
The checks give errors (the address cannot be saved) and warnings (the person can save it, but should look again).
"""

import json
import os
import re
from functools import lru_cache

import geo
import world

HERE = os.path.dirname(os.path.abspath(__file__))
EXAMPLES = {"IN": "133207", "RU": "660049", "US": "10001", "GB": "SW1A 1AA", "FR": "75008", "DE": "10115",
            "CN": "100000", "ES": "28001", "KZ": "050000", "BY": "220030", "JP": "100-0001", "CA": "K1A 0B1"}


FAR_KM = {"RU": 250, "US": 120, "ZA": 120, "KE": 120}     # the directory's positions of big countries are rough


@lru_cache(maxsize=1)
def _dir_countries():
    try:
        return frozenset(geo.postcode_countries())
    except Exception:                                     # an old places file without the directory
        return frozenset()


@lru_cache(maxsize=1)
def _rules():
    try:
        with open(os.path.join(HERE, "data", "geo", "postcodes.json"), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return {cc: re.compile(p, re.I if ci else 0) for cc, (p, ci) in data.items() if len(cc) == 2}


def uses_postcodes(country):
    return country not in world.NO_POSTCODE and (country in _rules() or country in world.POSTCODE_RULES)


def postcode_ok(country, value):
    """True / False, or None when we do not know the form of this country."""
    value = " ".join(str(value or "").upper().split())
    rule = world.POSTCODE_RULES.get(country)
    if rule:
        return bool(re.fullmatch(rule[0], value))
    r = _rules().get(country)
    if r is None:
        return None
    return bool(r.match(value))


def pin_offices(pin):
    """The Indian post offices of a PIN code (empty if we do not know it)."""
    if not re.fullmatch(r"[1-9]\d{5}", str(pin or "")):
        return []
    rows = [p for p in geo.search(str(pin), cc="IN", limit=10) if p.get("pin") == str(pin)]
    return sorted(rows, key=lambda p: not p.get("district"))     # post offices (with their district) first


def check(country, postcode="", place_id="", city=""):
    """{"errors": [...], "warnings": [...], "hint": "..."} for an address."""
    country = str(country or "IN").upper()
    postcode = " ".join(str(postcode or "").upper().split())
    out = {"errors": [], "warnings": [], "hint": "", "example": EXAMPLES.get(country, "")}
    if not postcode:
        if uses_postcodes(country):
            out["warnings"].append("Most addresses in this country have a postal code. Please add it if you know it.")
        else:
            out["hint"] = "This country does not use postal codes: you can leave the field empty."
        return out
    ok = postcode_ok(country, postcode)
    if ok is False:
        ex = EXAMPLES.get(country)
        out["errors"].append("This postal code does not look right for this country."
                             + (f" Example: {ex}." if ex else ""))
        return out
    if country in world.NO_POSTCODE:
        out["hint"] = "This country does not use postal codes: you can leave the field empty."
    if country != "IN" and country in _dir_countries():
        e = geo.postcode(country, postcode)
        if e is None:
            out["warnings"].append("We do not find this postal code in the directory of the country. Please check it.")
            return out
        out["hint"] = f"{postcode}: {e['place']}, {e['state']}".rstrip(", ")
        place = geo.get(place_id) if place_id else None
        if place and place.get("cc") == country:
            km = geo.haversine_km((place["lat"], place["lng"]), (e["lat"], e["lng"]))
            if km > FAR_KM.get(country, 60):
                out["warnings"].append(f"Postal code {postcode} belongs to {e['place']} ({e['state']}), about "
                                       f"{km:.0f} km from the place you chose. Please check one of them.")
        return out
    if country == "IN":
        offices = pin_offices(postcode)
        if not offices:
            out["warnings"].append("We do not know this PIN code. Please check it on your post or bank papers.")
            return out
        o = offices[0]
        where = ", ".join(x for x in (o["district"], o["region"]) if x)
        out["hint"] = f"PIN {postcode}: {o['name']}, {where}"
        place = geo.get(place_id) if place_id else None
        if place and place.get("cc") == "IN" and place.get("district") and o.get("district"):
            if geo.key(place["district"]) != geo.key(o["district"]) and place.get("pin") != postcode:
                out["warnings"].append(f"PIN {postcode} is in {o['district']} district, but the place you chose is in "
                                       f"{place['district']} district. Please check one of them.")
        elif place and place.get("cc") == "IN" and place.get("region") and o.get("region"):
            if geo.key(place["region"]) != geo.key(o["region"]) and place.get("pin") != postcode:
                out["warnings"].append(f"PIN {postcode} is in {o['region']}, but the place you chose is in "
                                       f"{place['region']}. Please check one of them.")
        elif city and not place:
            names = {geo.key(x["name"]) for x in offices} | {geo.key(x["district"]) for x in offices}
            if geo.key(city) not in names and not any(geo.key(city) in n or n in geo.key(city) for n in names if n):
                out["warnings"].append(f"PIN {postcode} belongs to {o['name']} ({where}). Is the town right?")
    return out
