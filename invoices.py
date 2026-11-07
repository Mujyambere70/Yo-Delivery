"""
invoices.py - version 7 (mixin class used by Store): tax invoices, credit notes and e-invoices.

    tax rates by kind of goods   the shop's prices include the tax; `tax_category_rates` gives the rate of each category of
                                 a country, with an optional price limit per piece (example for India after GST 2.0 of
                                 22 September 2025: clothes and shoes up to Rs 2,500 a piece 5 %, above 18 %; books 0 %;
                                 grocery 5 %; electronics, watches, appliances 18 %). A person with "tax.manage" checks them.
    registration                 a seller saves his tax number (GSTIN in India, INN in Russia), checked with its check
                                 digits; a registered seller issues a "Tax invoice", a seller who is not registered a
                                 "Bill of supply" without tax (he may not charge GST)
    numbering                    one gapless series per issuer and financial year (India: 1 April - 31 March), counted in
                                 the database: YO/2026-27/000001, SHOPSLUG/2026-27/000001; credit notes CN/...
    invoices                     an order gets one invoice per seller (and one of Yo Delivery for the delivery charge),
                                 made once (UNIQUE order + issuer); an approved return gets a credit note
    e-invoice                    every invoice and credit note can be downloaded as UBL 2.1 XML (the open OASIS format
                                 used by Peppol and EN 16931). Sending it to a tax portal (India's IRP, Russia's EDO
                                 operators) needs a contract with that portal and stays out of the demo.
"""

import json
from datetime import date, datetime
from xml.etree import ElementTree as ET

import world
from core import ShopError, now

INVOICE_SCHEMA = """
CREATE TABLE IF NOT EXISTS tax_category_rates (
    id INTEGER PRIMARY KEY AUTOINCREMENT, country TEXT NOT NULL, category TEXT NOT NULL, rate REAL NOT NULL,
    max_unit_price INTEGER NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '', updated_by INTEGER, updated_at TEXT NOT NULL,
    UNIQUE (country, category, max_unit_price)
);
CREATE TABLE IF NOT EXISTS invoice_counters (
    series TEXT PRIMARY KEY, last INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT, number TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, order_id INTEGER NOT NULL,
    issuer TEXT NOT NULL, seller_id INTEGER, issuer_name TEXT NOT NULL, issuer_tax_id TEXT NOT NULL DEFAULT '',
    buyer_name TEXT NOT NULL, buyer_address TEXT NOT NULL DEFAULT '', currency TEXT NOT NULL DEFAULT 'INR',
    taxable INTEGER NOT NULL, tax INTEGER NOT NULL, total INTEGER NOT NULL, lines TEXT NOT NULL,
    of_invoice TEXT NOT NULL DEFAULT '', return_id INTEGER, issued_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_invoice_once ON invoices(order_id, issuer) WHERE kind != 'Credit note';
CREATE UNIQUE INDEX IF NOT EXISTS idx_credit_once ON invoices(return_id, issuer) WHERE kind = 'Credit note';
"""

# India after GST 2.0 (examples, checked 2026-11-07): (category, rate %, up to this price a piece in paise or 0, note)
EXAMPLE_CATEGORY_RATES = [
    ("IN", "electronics", 18, 0, "Phones, laptops, TVs and other electronics"),
    ("IN", "fashion", 5, 250000, "Clothes up to Rs 2,500 a piece"),
    ("IN", "fashion", 18, 0, "Clothes above Rs 2,500 a piece"),
    ("IN", "footwear", 5, 250000, "Shoes up to Rs 2,500 a pair"),
    ("IN", "footwear", 18, 0, "Shoes above Rs 2,500 a pair"),
    ("IN", "watches", 18, 0, "Watches"),
    ("IN", "grocery", 5, 0, "Packaged food, tea and coffee (most items)"),
    ("IN", "home", 18, 0, "Household appliances (kitchen utensils are 5 %)"),
    ("IN", "books", 0, 0, "Printed books are nil-rated"),
]
DELIVERY_RATE = 18                        # a delivery charge is a service (GST 18 % in India)
YO_GSTIN = "27AAAAA0000A1Z"               # + check character: a made-up number of the demo company (Maharashtra)
GST_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def gstin_check_char(first14):
    s = 0
    for i, ch in enumerate(first14):
        v = GST_CHARS.index(ch) * (1 if i % 2 == 0 else 2)
        s += v // 36 + v % 36
    return GST_CHARS[(36 - s % 36) % 36]


def gstin_ok(text):
    """India: 2 digits of the state, the PAN (5 letters, 4 digits, 1 letter), 1 entity code, 'Z', 1 check character."""
    g = str(text or "").replace(" ", "").upper()
    if len(g) != 15 or not g[:2].isdigit() or not g[2:7].isalpha() or not g[7:11].isdigit() or not g[11].isalpha() \
            or g[13] != "Z" or any(c not in GST_CHARS for c in g):
        return False
    return gstin_check_char(g[:14]) == g[14]


def inn_ok(text):
    """Russia: INN of a company (10 digits) or of a person / sole trader (12 digits) with its check digits."""
    d = str(text or "").strip()
    if not d.isdigit() or len(d) not in (10, 12):
        return False
    n = [int(c) for c in d]

    def check(weights, digits):
        return sum(w * x for w, x in zip(weights, digits)) % 11 % 10
    if len(n) == 10:
        return check([2, 4, 10, 3, 5, 9, 4, 6, 8], n) == n[9]
    return (check([7, 2, 4, 10, 3, 5, 9, 4, 6, 8], n) == n[10]
            and check([3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8], n) == n[11])


def financial_year(d):
    """India's financial year of a day: 2026-11-07 -> '2026-27'."""
    y = d.year if d.month >= 4 else d.year - 1
    return f"{y}-{str(y + 1)[2:]}"


def split_inclusive(amount, rate):
    """A price with tax included -> (taxable value, tax), in paise, adding up exactly."""
    taxable = round(amount * 100 / (100 + rate)) if rate else amount
    return taxable, amount - taxable


class InvoiceMixin:

    # ---------------------------------------------------------------- rates
    def ensure_category_rates(self):
        with self.db() as conn:
            for c, cat, rate, cap, note in EXAMPLE_CATEGORY_RATES:
                conn.execute("INSERT INTO tax_category_rates (country, category, rate, max_unit_price, note, updated_at) "
                             "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (country, category, max_unit_price) DO NOTHING",
                             (c, cat, rate, cap, note, now()))

    def category_rates(self, country=""):
        with self.db() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM tax_category_rates WHERE (? = '' OR country = ?) ORDER BY country, category, "
                "CASE WHEN max_unit_price = 0 THEN 1 ELSE 0 END, max_unit_price", (country, country))]

    def save_category_rate(self, actor_id, country, category, rate, max_unit_price=0, note="", delete=False):
        if not self.can(actor_id, "tax.manage"):
            raise ShopError("You do not have the ability to do this. Ask your manager.")
        country = str(country or "").upper()
        if len(country) != 2 or not category:
            raise ShopError("Please choose the country and the category.")
        try:
            rate = float(rate)
            cap = int(round(float(max_unit_price or 0) * 100))
        except (TypeError, ValueError):
            raise ShopError("Please write the rate and the price limit as numbers.")
        if not 0 <= rate <= 50 or cap < 0:
            raise ShopError("The rate must be between 0 and 50.")
        with self.db() as conn:
            if delete:
                conn.execute("DELETE FROM tax_category_rates WHERE country = ? AND category = ? AND max_unit_price = ?",
                             (country, category, cap))
            else:
                conn.execute("INSERT INTO tax_category_rates (country, category, rate, max_unit_price, note, updated_by, "
                             "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (country, category, max_unit_price) DO "
                             "UPDATE SET rate = excluded.rate, note = excluded.note, updated_by = excluded.updated_by, "
                             "updated_at = excluded.updated_at",
                             (country, category, rate, cap, " ".join(str(note).split())[:120], actor_id, now()))
        self.log(actor_id, "Tax rate of a category", f"{country} {category} {rate:g} %")

    def rate_for(self, country, category, unit_price, conn=None):
        """The tax rate of a piece of this category and price (the standard goods rate when no row fits)."""
        def read(c):
            for r in c.execute("SELECT rate, max_unit_price FROM tax_category_rates WHERE country = ? AND category = ? "
                               "ORDER BY CASE WHEN max_unit_price = 0 THEN 1 ELSE 0 END, max_unit_price",
                               (country, category)).fetchall():
                if r["max_unit_price"] == 0 or unit_price <= r["max_unit_price"]:
                    return float(r["rate"])
            t = c.execute("SELECT goods_pct FROM tax_rules WHERE country = ?", (country,)).fetchone()
            return float(t[0]) if t else 0.0
        if conn is not None:
            return read(conn)
        with self.db() as c:
            return read(c)

    # ---------------------------------------------------------------- seller registration
    def save_seller_tax_id(self, user_id, tax_id):
        shop = self.seller_of(user_id) if hasattr(self, "seller_of") else None
        with self.db() as conn:
            if shop is None:
                r = conn.execute("SELECT * FROM sellers WHERE user_id = ?", (user_id,)).fetchone()
                shop = dict(r) if r else None
            if shop is None:
                raise ShopError("Only the owner of the shop can change this.")
            t = str(tax_id or "").replace(" ", "").upper()
            if t:
                ok = gstin_ok(t) if shop["country"] in ("IN", "") else inn_ok(t) if shop["country"] == "RU" else len(t) >= 5
                if not ok:
                    raise ShopError("This tax number is not correct. Please check it (the last character is a check digit).")
            conn.execute("UPDATE sellers SET tax_id = ?, tax_registered = ? WHERE id = ?", (t, 1 if t else 0, shop["id"]))
        return t

    # ---------------------------------------------------------------- numbering
    def _next_number(self, conn, series):
        conn.execute("INSERT INTO invoice_counters (series, last) VALUES (?, 1) ON CONFLICT (series) DO UPDATE SET "
                     "last = invoice_counters.last + 1", (series,))
        n = conn.execute("SELECT last FROM invoice_counters WHERE series = ?", (series,)).fetchone()[0]
        return f"{series}/{n:06d}"

    # ---------------------------------------------------------------- invoices
    def issue_invoices(self, order_id):
        """The invoices of an order (made once, then read). Returns the list."""
        with self.db() as conn:
            o = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
            if o is None:
                raise ShopError("Order not found.")
            o = dict(o)
            if o["cancelled_at"]:
                raise ShopError("A cancelled order has no invoice.")
            have = {r["issuer"] for r in conn.execute("SELECT issuer FROM invoices WHERE order_id = ? AND kind != "
                                                      "'Credit note'", (order_id,))}
            items = [dict(r) for r in conn.execute(
                "SELECT i.*, p.category FROM order_items i LEFT JOIN products p ON p.id = i.product_id WHERE i.order_id = ?",
                (order_id,))]
            groups = {}
            for it in items:
                groups.setdefault(it["seller_id"] or 0, []).append(it)
            if o["delivery_fee"]:
                groups.setdefault(0, [])
            fy = financial_year(datetime.fromisoformat(o["placed_at"][:19]).date())
            country = "IN"                                   # the shop sells from India
            for seller_id, its in groups.items():
                issuer = f"seller:{seller_id}" if seller_id else "yo"
                if issuer in have:
                    continue
                if seller_id:
                    s = dict(conn.execute("SELECT * FROM sellers WHERE id = ?", (seller_id,)).fetchone())
                    name, tax_id, registered, prefix = s["shop_name"], s.get("tax_id") or "", bool(s.get("tax_registered")), \
                        (s["slug"] or f"S{seller_id}").upper()[:12]
                else:
                    name, tax_id, registered, prefix = "Yo Delivery (demo company)", YO_GSTIN + gstin_check_char(YO_GSTIN), \
                        True, "YO"
                lines, taxable_sum, tax_sum = [], 0, 0
                share = 1.0
                if o["coupon_discount"] and o["items_total"]:
                    share = 1 - o["coupon_discount"] / o["items_total"]          # the coupon lowers every line alike
                for it in its:
                    amount = round(it["price"] * it["qty"] * share)
                    rate = self.rate_for(country, it.get("category") or "", it["price"], conn) if registered else 0.0
                    taxable, tax = split_inclusive(amount, rate)
                    lines.append({"name": it["name"], "qty": it["qty"], "unit": it["price"], "rate": rate,
                                  "taxable": taxable, "tax": tax, "total": amount, "category": it.get("category") or ""})
                    taxable_sum += taxable
                    tax_sum += tax
                if not seller_id and o["delivery_fee"]:
                    taxable, tax = split_inclusive(o["delivery_fee"], DELIVERY_RATE)
                    lines.append({"name": "Delivery charge (service)", "qty": 1, "unit": o["delivery_fee"],
                                  "rate": DELIVERY_RATE, "taxable": taxable, "tax": tax, "total": o["delivery_fee"],
                                  "category": "service"})
                    taxable_sum += taxable
                    tax_sum += tax
                if not lines:
                    continue
                kind = "Tax invoice" if registered else "Bill of supply"
                number = self._next_number(conn, f"{prefix}/{fy}")
                conn.execute("INSERT INTO invoices (number, kind, order_id, issuer, seller_id, issuer_name, issuer_tax_id, "
                             "buyer_name, buyer_address, currency, taxable, tax, total, lines, issued_at) VALUES "
                             "(?, ?, ?, ?, ?, ?, ?, ?, ?, 'INR', ?, ?, ?, ?, ?)",
                             (number, kind, order_id, issuer, seller_id or None, name, tax_id if registered else "",
                              o["ship_name"], f"{o['ship_address']} {o['ship_pincode']}".strip(), taxable_sum, tax_sum,
                              taxable_sum + tax_sum, json.dumps(lines), now()))
        return self.invoices_of_order(order_id)

    def invoices_of_order(self, order_id):
        with self.db() as conn:
            rows = [dict(r) for r in conn.execute("SELECT * FROM invoices WHERE order_id = ? ORDER BY id", (order_id,))]
        for r in rows:
            r["line_list"] = json.loads(r["lines"])
            r["by_rate"] = _by_rate(r["line_list"])
        return rows

    def invoice(self, invoice_id):
        with self.db() as conn:
            r = conn.execute("SELECT i.*, o.number AS order_number, o.user_id FROM invoices i JOIN orders o "
                             "ON o.id = i.order_id WHERE i.id = ?", (invoice_id,)).fetchone()
        if r is None:
            return None
        r = dict(r)
        r["line_list"] = json.loads(r["lines"])
        r["by_rate"] = _by_rate(r["line_list"])
        return r

    def invoice_list(self, seller_id=None, limit=200):
        with self.db() as conn:
            if seller_id:
                rows = conn.execute("SELECT i.*, o.number AS order_number FROM invoices i JOIN orders o ON o.id = i.order_id "
                                    "WHERE i.seller_id = ? ORDER BY i.id DESC LIMIT ?", (seller_id, limit))
            else:
                rows = conn.execute("SELECT i.*, o.number AS order_number FROM invoices i JOIN orders o ON o.id = i.order_id "
                                    "ORDER BY i.id DESC LIMIT ?", (limit,))
            return [dict(r) for r in rows]

    def credit_note_for_return(self, conn, return_id):
        """An approved return: a credit note against each invoice of the order, for the refunded share."""
        r = conn.execute("SELECT * FROM returns WHERE id = ?", (return_id,)).fetchone()
        if r is None:
            return []
        made = []
        invs = [dict(x) for x in conn.execute("SELECT * FROM invoices WHERE order_id = ? AND kind != 'Credit note'",
                                              (r["order_id"],))]
        goods_total = sum(sum(ln["total"] for ln in json.loads(i["lines"]) if ln["category"] != "service") for i in invs)
        for inv in invs:
            if conn.execute("SELECT 1 FROM invoices WHERE return_id = ? AND issuer = ? AND kind = 'Credit note'",
                            (return_id, inv["issuer"])).fetchone():
                continue
            lines = []
            for ln in json.loads(inv["lines"]):
                if ln["category"] == "service" or not goods_total:
                    continue                                   # the delivery charge is not refunded
                amount = round(r["amount"] * ln["total"] / goods_total)
                taxable, tax = split_inclusive(amount, ln["rate"])
                lines.append({**ln, "taxable": -taxable, "tax": -tax, "total": -amount})
            if not lines:
                continue
            fy = financial_year(date.today())
            prefix = inv["number"].split("/")[0]
            number = self._next_number(conn, f"CN-{prefix}/{fy}")
            conn.execute("INSERT INTO invoices (number, kind, order_id, issuer, seller_id, issuer_name, issuer_tax_id, "
                         "buyer_name, buyer_address, currency, taxable, tax, total, lines, of_invoice, return_id, issued_at) "
                         "VALUES (?, 'Credit note', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (number, inv["order_id"], inv["issuer"], inv["seller_id"], inv["issuer_name"], inv["issuer_tax_id"],
                          inv["buyer_name"], inv["buyer_address"], inv["currency"], sum(x["taxable"] for x in lines),
                          sum(x["tax"] for x in lines), sum(x["total"] for x in lines), json.dumps(lines), inv["number"],
                          return_id, now()))
            made.append(number)
        return made

    def decide_return(self, return_id, approve, by, note=""):
        """Version 7: an approved return also gets its credit notes."""
        result = super().decide_return(return_id, approve, by, note)
        if approve:
            with self.db() as conn:
                self.credit_note_for_return(conn, return_id)
        return result

    # ---------------------------------------------------------------- UBL 2.1
    def invoice_ubl(self, invoice_id):
        inv = self.invoice(invoice_id)
        if inv is None:
            raise ShopError("Invoice not found.")
        return ubl_xml(inv)


def _by_rate(lines):
    out = {}
    for ln in lines:
        b = out.setdefault(ln["rate"], {"rate": ln["rate"], "taxable": 0, "tax": 0})
        b["taxable"] += ln["taxable"]
        b["tax"] += ln["tax"]
    return sorted(out.values(), key=lambda x: x["rate"])


def _amt(paise):
    return f"{abs(paise) / 100:.2f}"


def ubl_xml(inv):
    """The invoice (or credit note) as UBL 2.1 XML with the EN 16931 core elements."""
    credit = inv["kind"] == "Credit note"
    root_tag = "CreditNote" if credit else "Invoice"
    ns = {"": f"urn:oasis:names:specification:ubl:schema:xsd:{root_tag}-2",
          "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
          "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"}
    for p, u in ns.items():
        ET.register_namespace(p, u)
    C = lambda t: f"{{{ns['cac']}}}{t}"                      # noqa: E731
    B = lambda t: f"{{{ns['cbc']}}}{t}"                      # noqa: E731
    root = ET.Element(f"{{{ns['']}}}{root_tag}")

    def sub(parent, tag, text=None, **attrs):
        e = ET.SubElement(parent, tag, attrs)
        if text is not None:
            e.text = str(text)
        return e
    cur = inv["currency"]
    sub(root, B("CustomizationID"), "urn:cen.eu:en16931:2017")
    sub(root, B("ID"), inv["number"])
    sub(root, B("IssueDate"), inv["issued_at"][:10])
    sub(root, B("CreditNoteTypeCode" if credit else "InvoiceTypeCode"), "381" if credit else "380")
    sub(root, B("DocumentCurrencyCode"), cur)
    sub(root, B("BuyerReference"), inv.get("order_number", ""))
    if credit and inv.get("of_invoice"):
        ref = sub(sub(root, C("BillingReference")), C("InvoiceDocumentReference"))
        sub(ref, B("ID"), inv["of_invoice"])

    def party(tag, name, tax_id, address):
        p = sub(sub(root, C(tag)), C("Party"))
        sub(sub(p, C("PartyName")), B("Name"), name)
        a = sub(p, C("PostalAddress"))
        if address:
            sub(a, B("StreetName"), address)
        sub(sub(a, C("Country")), B("IdentificationCode"), "IN")
        if tax_id:
            ts = sub(p, C("PartyTaxScheme"))
            sub(ts, B("CompanyID"), tax_id)
            sub(sub(ts, C("TaxScheme")), B("ID"), "VAT")
        sub(sub(p, C("PartyLegalEntity")), B("RegistrationName"), name)
    party("AccountingSupplierParty", inv["issuer_name"], inv["issuer_tax_id"], "")
    party("AccountingCustomerParty", inv["buyer_name"], "", inv["buyer_address"])
    tt = sub(root, C("TaxTotal"))
    sub(tt, B("TaxAmount"), _amt(inv["tax"]), currencyID=cur)
    for b in inv["by_rate"]:
        st = sub(tt, C("TaxSubtotal"))
        sub(st, B("TaxableAmount"), _amt(b["taxable"]), currencyID=cur)
        sub(st, B("TaxAmount"), _amt(b["tax"]), currencyID=cur)
        cat = sub(st, C("TaxCategory"))
        sub(cat, B("ID"), "S" if b["rate"] else ("Z" if inv["issuer_tax_id"] else "O"))
        sub(cat, B("Percent"), f"{b['rate']:g}")
        sub(sub(cat, C("TaxScheme")), B("ID"), "VAT")
    mt = sub(root, C("LegalMonetaryTotal"))
    sub(mt, B("LineExtensionAmount"), _amt(inv["taxable"]), currencyID=cur)
    sub(mt, B("TaxExclusiveAmount"), _amt(inv["taxable"]), currencyID=cur)
    sub(mt, B("TaxInclusiveAmount"), _amt(inv["total"]), currencyID=cur)
    sub(mt, B("PayableAmount"), _amt(inv["total"]), currencyID=cur)
    for i, ln in enumerate(inv["line_list"], 1):
        line = sub(root, C("CreditNoteLine" if credit else "InvoiceLine"))
        sub(line, B("ID"), i)
        sub(line, B("CreditedQuantity" if credit else "InvoicedQuantity"), ln["qty"], unitCode="H87")
        sub(line, B("LineExtensionAmount"), _amt(ln["taxable"]), currencyID=cur)
        item = sub(line, C("Item"))
        sub(item, B("Name"), ln["name"])
        cat = sub(item, C("ClassifiedTaxCategory"))
        sub(cat, B("ID"), "S" if ln["rate"] else ("Z" if inv["issuer_tax_id"] else "O"))
        sub(cat, B("Percent"), f"{ln['rate']:g}")
        sub(sub(cat, C("TaxScheme")), B("ID"), "VAT")
        price = sub(line, C("Price"))
        unit_taxable = round(abs(ln["taxable"]) / max(1, ln["qty"]))
        sub(price, B("PriceAmount"), _amt(unit_taxable), currencyID=cur)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")


def money(paise):
    return world.format_money(paise, "INR")
