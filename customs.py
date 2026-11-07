"""
customs.py - version 6 (mixin class used by Store): the customs declaration of parcels that go to another country.

    * the sender says why he sends the parcel (gift, sale, documents...) and lists what is inside: description,
      number of pieces and value of each line; documents need no value
    * goods that no country lets in by post (weapons, explosives, drugs, fake money, live animals, flammable
      gas and liquids...) are refused at once; each country can add its own words (customs_rules.forbidden)
    * the duties and taxes the receiver may pay are estimated with the rule of the country (customs_rules):
      nothing up to the duty-free value, then a percentage of the value. The rules that come with the demo are
      EXAMPLES, not the law of any country: staff with the ability "customs.manage" must enter the real ones.
    * the declaration is printed with the label (/courier/s/<number>/customs, in the style of a CN 22 / CN 23 form)
"""

import json
import re

from core import ShopError, now

CUSTOMS_SCHEMA = """
CREATE TABLE IF NOT EXISTS customs_rules (
    country TEXT PRIMARY KEY, duty_free INTEGER NOT NULL DEFAULT 0, rate_pct REAL NOT NULL DEFAULT 0,
    forbidden TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', example INTEGER NOT NULL DEFAULT 1,
    updated_by INTEGER, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS customs_declarations (
    shipment_id INTEGER PRIMARY KEY REFERENCES shipments(id), reason TEXT NOT NULL, items TEXT NOT NULL,
    total_value INTEGER NOT NULL, duty_estimate INTEGER NOT NULL DEFAULT 0, rule_country TEXT NOT NULL DEFAULT '*',
    created_at TEXT NOT NULL
);
"""

REASONS = {"gift": "Gift", "sale": "Sale of goods", "documents": "Documents", "sample": "Commercial sample",
           "return": "Returned goods", "personal": "Personal belongings"}
MAX_LINES = 5
# goods that are not accepted in international post anywhere (the words are searched in the item lines)
FORBIDDEN = {
    "weapons": ["gun", "pistol", "rifle", "revolver", "firearm", "weapon", "ammunition", "bullets", "sword",
                "taser", "pepper spray"],
    "explosives": ["explosive", "dynamite", "grenade", "firework", "fireworks", "detonator", "gunpowder"],
    "drugs": ["drug", "drugs", "narcotic", "cocaine", "heroin", "cannabis", "marijuana", "opium", "meth"],
    "money": ["counterfeit", "fake money", "fake currency", "banknotes", "cash"],
    "animals": ["live animal", "live animals", "live bird", "live fish"],
    "flammable": ["petrol", "gasoline", "kerosene", "diesel", "lighter fluid", "gas cylinder", "lighter", "matches",
                  "paint thinner"],
    "batteries": ["power bank", "loose lithium", "lithium battery"],
}
FORBIDDEN_NAMES = {"weapons": "weapons and ammunition", "explosives": "explosives and fireworks",
                   "drugs": "drugs", "money": "cash and counterfeit money", "animals": "live animals",
                   "flammable": "flammable liquids and gas", "batteries": "loose lithium batteries and power banks"}
# example rules of the demo (marked as examples on every page): duty-free value in paise, percentage
EXAMPLE_RULES = {"*": (2_000_00, 20.0, "", "Example rule for every other country.")}


def _words(text):
    return " " + re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()) + " "


def forbidden_in(text, extra=""):
    """The kind of forbidden goods found in a text (or None)."""
    t = _words(text)
    for kind, words in FORBIDDEN.items():
        if any(_words(w) in t for w in words):
            return FORBIDDEN_NAMES[kind]
    for w in [x.strip() for x in str(extra or "").split(",") if x.strip()]:
        if _words(w) in t:
            return w
    return None


class CustomsMixin:
    def ensure_customs_rules(self):
        with self.db() as conn:
            for c, (free, rate, forb, note) in EXAMPLE_RULES.items():
                conn.execute("INSERT INTO customs_rules (country, duty_free, rate_pct, forbidden, note, example, updated_at) "
                             "VALUES (?, ?, ?, ?, ?, 1, ?) ON CONFLICT (country) DO NOTHING", (c, free, rate, forb, note, now()))

    def customs_rules(self):
        with self.db() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM customs_rules ORDER BY country = '*' DESC, country")]

    def customs_rule(self, country, conn=None):
        def read(c):
            r = c.execute("SELECT * FROM customs_rules WHERE country = ?", (str(country or "").upper(),)).fetchone() \
                or c.execute("SELECT * FROM customs_rules WHERE country = '*'").fetchone()
            return dict(r) if r else {"country": "*", "duty_free": 0, "rate_pct": 0.0, "forbidden": "", "note": "",
                                      "example": 1}
        if conn is not None:
            return read(conn)
        with self.db() as c:
            return read(c)

    def save_customs_rule(self, user_id, country, duty_free_rupees, rate_pct, forbidden="", note="", delete=False):
        country = str(country or "").strip().upper()
        if not (country == "*" or re.fullmatch(r"[A-Z]{2}", country)):
            raise ShopError("Please write the country as two letters (or * for every other country).")
        with self.db() as conn:
            if delete:
                if country == "*":
                    raise ShopError("The rule for every other country cannot be deleted.")
                conn.execute("DELETE FROM customs_rules WHERE country = ?", (country,))
                return
            try:
                free = int(round(float(duty_free_rupees or 0) * 100))
                rate = float(rate_pct or 0)
            except ValueError:
                raise ShopError("Please write numbers for the duty-free value and the percentage.")
            if free < 0 or not 0 <= rate <= 100:
                raise ShopError("The percentage must be between 0 and 100.")
            conn.execute("INSERT INTO customs_rules (country, duty_free, rate_pct, forbidden, note, example, updated_by, "
                         "updated_at) VALUES (?, ?, ?, ?, ?, 0, ?, ?) ON CONFLICT (country) DO UPDATE SET "
                         "duty_free = excluded.duty_free, rate_pct = excluded.rate_pct, forbidden = excluded.forbidden, "
                         "note = excluded.note, example = 0, updated_by = excluded.updated_by, updated_at = excluded.updated_at",
                         (country, free, rate, " ".join(str(forbidden).split())[:400], " ".join(str(note).split())[:200],
                          user_id, now()))

    def customs_declaration(self, f, q):
        """Read and check the customs part of the booking form (parcels to another country)."""
        reason = f.get("customs_reason") or ("documents" if q["kind"] == "document" else "gift")
        if reason not in REASONS:
            raise ShopError("Please choose why you send the parcel.")
        descs = f.get("customs_desc") or []
        qtys = f.get("customs_qty") or []
        vals = f.get("customs_value") or []
        hss = f.get("customs_hs") or []                  # version 7: HS codes (6 digits, optional)
        if isinstance(descs, str):
            descs, qtys, vals = [descs], [qtys], [vals]
        if isinstance(hss, str):
            hss = [hss]
        lines = []
        for i, d in enumerate(descs[:MAX_LINES]):
            d = " ".join(str(d or "").split())[:80]
            if not d:
                continue
            try:
                qty = int(qtys[i] if i < len(qtys) and str(qtys[i]).strip() else 1)
                value = int(round(float(vals[i] if i < len(vals) and str(vals[i]).strip() else 0) * 100))
            except ValueError:
                raise ShopError("Please write the number of pieces and the value as numbers.")
            if not 1 <= qty <= 999 or value < 0:
                raise ShopError("Please check the number of pieces and the value of each line.")
            hs = "".join(ch for ch in str(hss[i] if i < len(hss) else "") if ch.isdigit())
            if hs and len(hs) not in (6, 8, 10):
                raise ShopError("An HS code has 6 digits (for example 610910 for cotton T-shirts).")
            lines.append({"desc": d, "qty": qty, "value": value, "hs": hs[:6]})
        if not lines:                                    # the old form: one line from "What is inside"
            lines = [{"desc": " ".join(str(f.get("contents", "")).split())[:80], "qty": 1,
                      "value": int(round(float(f.get("declared") or 0) * 100))}]
        total = sum(x["value"] for x in lines)
        if reason != "documents" and total <= 0:
            raise ShopError("Please write the value of what is inside (customs needs it).")
        rule = self.customs_rule(q["to_country"])
        for x in lines + [{"desc": f.get("contents", "")}]:
            bad = forbidden_in(x["desc"], rule["forbidden"])
            if bad:
                raise ShopError(f"We cannot send {bad} abroad. Please remove them from the parcel.")
        duty = 0 if reason == "documents" or total <= rule["duty_free"] else int(round(total * rule["rate_pct"] / 100))
        return {"reason": reason, "items": lines, "total_value": total, "duty_estimate": duty,
                "rule_country": rule["country"], "example": bool(rule["example"])}

    def save_customs(self, conn, shipment_id, d):
        conn.execute("INSERT INTO customs_declarations (shipment_id, reason, items, total_value, duty_estimate, "
                     "rule_country, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (shipment_id, d["reason"], json.dumps(d["items"]), d["total_value"], d["duty_estimate"],
                      d["rule_country"], now()))

    def customs_of(self, shipment_id):
        with self.db() as conn:
            r = conn.execute("SELECT * FROM customs_declarations WHERE shipment_id = ?", (shipment_id,)).fetchone()
        if r is None:
            return None
        d = dict(r)
        d["items"] = json.loads(d["items"])
        d["reason_name"] = REASONS.get(d["reason"], d["reason"])
        return d
