"""
taxes.py - version 6 (mixin class used by Store): the tax rule of each country.

India keeps its rules (GST 18 % on courier services, the GST of Yo Tickets in tickets.py). For the other countries
a parcel sent inside the country now pays the VAT of that country on the courier service (Rwanda VAT 18 %,
Russia НДС 22 %, Kenya VAT 16 % ...). Parcels to another country pay no VAT here (an export of a service); the
receiver may pay import duties and taxes (customs.py).

The rates that come with the demo are EXAMPLES taken from public sources in 2026 (they are marked "example"):
before real use a person with the ability "tax.manage" must check every rule with a tax adviser of the country and
save it, and the shop must be registered for VAT in each country where it sells.
"""

from core import ShopError, now

TAX_SCHEMA = """
CREATE TABLE IF NOT EXISTS tax_rules (
    country TEXT PRIMARY KEY, name TEXT NOT NULL, services_pct REAL NOT NULL DEFAULT 0,
    goods_pct REAL NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '', example INTEGER NOT NULL DEFAULT 1,
    updated_by TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL
);
"""

# country, tax name, services %, goods %, note  (EXAMPLES - see the top of this file)
EXAMPLE_RULES = [
    ("IN", "GST", 18, 18, "Courier services 18 %; goods 5-28 % by kind (the shop shows prices with GST)"),
    ("RW", "VAT", 18, 18, "Rwanda Revenue Authority, standard rate"),
    ("KE", "VAT", 16, 16, "Kenya Revenue Authority, standard rate"),
    ("UG", "VAT", 18, 18, "Uganda Revenue Authority, standard rate"),
    ("TZ", "VAT", 18, 18, "Tanzania Revenue Authority, standard rate"),
    ("RU", "НДС", 22, 22, "Standard rate from 1 January 2026 (was 20 %)"),
    ("KZ", "VAT", 16, 16, "Standard rate from 1 January 2026 (was 12 %)"),
    ("AE", "VAT", 5, 5, "Federal Tax Authority, standard rate"),
    ("GB", "VAT", 20, 20, "HMRC, standard rate"),
    ("FR", "TVA", 20, 20, "Standard rate"),
    ("DE", "MwSt", 19, 19, "Standard rate"),
    ("ZA", "VAT", 15, 15, "SARS, standard rate"),
    ("NG", "VAT", 7.5, 7.5, "FIRS, standard rate"),
    ("CN", "VAT", 6, 13, "Modern services 6 %, goods 13 %"),
    ("US", "Sales tax", 0, 0, "No federal VAT; state and city sales taxes are not handled by this demo"),
]


class TaxMixin:
    def ensure_tax_rules(self):
        with self.db() as conn:
            for cc, name, serv, goods, note in EXAMPLE_RULES:
                conn.execute("INSERT INTO tax_rules (country, name, services_pct, goods_pct, note, example, updated_at) "
                             "VALUES (?, ?, ?, ?, ?, 1, ?) ON CONFLICT (country) DO NOTHING",
                             (cc, name, serv, goods, note, now()))

    def tax_rules(self):
        with self.db() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM tax_rules ORDER BY country")]

    def tax_rule(self, country, conn=None):
        def read(c):
            r = c.execute("SELECT * FROM tax_rules WHERE country = ?", ((country or "").upper(),)).fetchone()
            return dict(r) if r else None
        if conn is not None:
            return read(conn)
        with self.db() as c:
            return read(c)

    def save_tax_rule(self, user_id, country, name, services_pct, goods_pct, note="", delete=False):
        country = str(country or "").strip().upper()
        if len(country) != 2 or not country.isalpha():
            raise ShopError("Please choose the country.")
        if not self.can(user_id, "tax.manage"):
            raise ShopError("You do not have the ability to change tax rules.")
        u = self.get_user(user_id)
        with self.db() as conn:
            if delete:
                if country == "IN":
                    raise ShopError("The rule of India is needed by the shop.")
                conn.execute("DELETE FROM tax_rules WHERE country = ?", (country,))
                return
            try:
                serv, goods = float(services_pct), float(goods_pct)
            except (TypeError, ValueError):
                raise ShopError("Please write the rates as numbers.")
            if not (0 <= serv <= 40 and 0 <= goods <= 40):
                raise ShopError("A rate must be between 0 and 40 %.")
            name = " ".join(str(name or "").split())[:20] or "VAT"
            conn.execute("INSERT INTO tax_rules (country, name, services_pct, goods_pct, note, example, updated_by, "
                         "updated_at) VALUES (?, ?, ?, ?, ?, 0, ?, ?) ON CONFLICT (country) DO UPDATE SET "
                         "name = excluded.name, services_pct = excluded.services_pct, goods_pct = excluded.goods_pct, "
                         "note = excluded.note, example = 0, updated_by = excluded.updated_by, "
                         "updated_at = excluded.updated_at",
                         (country, name, serv, goods, " ".join(str(note or "").split())[:200], u["name"], now()))

    def courier_tax(self, q):
        """The tax of a courier quote: GST in India, the VAT of the country for a parcel inside another country."""
        if not q.get("domestic") or q.get("from_country") != q.get("to_country"):
            return 0, ""
        rule = self.tax_rule(q["from_country"])
        if not rule or rule["services_pct"] <= 0:
            return 0, ""
        pct = rule["services_pct"]
        tax = int(round(q["subtotal"] * pct / 100))
        return tax, f"{rule['name']} {pct:g}%"
