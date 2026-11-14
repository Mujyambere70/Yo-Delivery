"""
einvoice.py - version 8 (mixin class used by Store): India's e-invoice for our tax invoices, and the review of the
tax rates by a second person.

E-invoice
    In India a business invoice is sent to the Invoice Registration Portal (IRP), which gives it an Invoice Reference
    Number (IRN) and a signed QR code. The IRN is not secret: it is the SHA-256 hash of

        GSTIN of the supplier + financial year + document type (INV, CRN, DBN) + document number

    written as 64 hexadecimal characters. Version 8 computes this number for every tax invoice, builds the JSON file
    of the e-invoice format (version 1.1) and prints a QR code with the same fields as the official one.
    The invoice is NOT registered: Yo Delivery is a demo with a made-up GSTIN and nothing is sent to the IRP. The
    page says so, the QR code is not signed, and einvoice_problems() lists what a real registration would still need
    (for example: the official format allows 16 characters for the document number, and a product needs its own HSN
    code - here every category has one example code).
Rate review
    The tax rates of the shop are examples typed in by us. Now every rate (of a country, of a kind of goods) can be
    marked "checked" by a person with the ability "tax.manage" - not the person who typed the rate - with the day
    from which and until which it is valid. The tax pages show: example, checked, changed after the check, expired.
"""

import hashlib
import json
import re
from datetime import date, datetime

from core import ShopError, now
from invoices import financial_year

DOC_TYPES = {"Tax invoice": "INV", "Credit note": "CRN", "Debit note": "DBN"}
# one EXAMPLE code of the Harmonized System per category of the demo shop (a real invoice needs the code of each product)
HSN_EXAMPLE = {"books": "4901", "watches": "9102", "footwear": "6404", "fashion": "6109", "electronics": "8517",
               "grocery": "0902", "home": "7323", "service": "9968"}
STATE_OF_GSTIN = {"03": "Punjab", "06": "Haryana", "07": "Delhi", "09": "Uttar Pradesh", "24": "Gujarat",
                  "27": "Maharashtra", "29": "Karnataka", "33": "Tamil Nadu"}
MAX_DOC_NO = 16
_DOC_NO = re.compile(r"^[A-Za-z0-9/-]{1,16}$")


def irn(gstin, fin_year, doc_type, doc_no):
    """The Invoice Reference Number: SHA-256 of the four parts joined without spaces (64 hex characters)."""
    text = f"{str(gstin).strip().upper()}{fin_year}{doc_type}{str(doc_no).strip()}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _rupees(paise):
    return round(int(paise) / 100, 2)


def _dt(text):
    return datetime.fromisoformat(str(text)[:19])


class EInvoiceMixin:

    # ------------------------------------------------------------------ e-invoice
    def einvoice(self, invoice_id):
        """{"irn", "json", "qr", "problems", "doc_type"} for a tax invoice or a credit note with a GSTIN, else None."""
        inv = self.invoice(invoice_id)
        if inv is None or inv["kind"] not in DOC_TYPES or not inv["issuer_tax_id"]:
            return None
        doc_type = DOC_TYPES[inv["kind"]]
        when = _dt(inv["issued_at"])
        fy = financial_year(when.date())
        number = irn(inv["issuer_tax_id"], fy, doc_type, inv["number"])
        state = inv["issuer_tax_id"][:2]
        items, main_hsn = [], ""
        for n, ln in enumerate(inv["line_list"], 1):
            hsn = HSN_EXAMPLE.get(ln.get("category") or "", "")
            main_hsn = main_hsn or hsn
            items.append({"SlNo": str(n), "PrdDesc": ln["name"][:300], "IsServc": "Y" if ln.get("category") == "service" else "N",
                          "HsnCd": hsn, "Qty": ln["qty"], "Unit": "NOS", "UnitPrice": _rupees(ln["taxable"] / max(1, ln["qty"])),
                          "TotAmt": _rupees(ln["taxable"]), "AssAmt": _rupees(ln["taxable"]), "GstRt": ln["rate"],
                          "IgstAmt": 0, "CgstAmt": _rupees(ln["tax"] / 2), "SgstAmt": _rupees(ln["tax"] - ln["tax"] // 2),
                          "TotItemVal": _rupees(ln["total"])})
        doc = {
            "Version": "1.1",
            "TranDtls": {"TaxSch": "GST", "SupTyp": "B2C"},
            "DocDtls": {"Typ": doc_type, "No": inv["number"], "Dt": when.strftime("%d/%m/%Y")},
            "SellerDtls": {"Gstin": inv["issuer_tax_id"], "LglNm": inv["issuer_name"], "Stcd": state},
            "BuyerDtls": {"Gstin": "URP", "LglNm": inv["buyer_name"], "Pos": state, "Addr1": inv["buyer_address"][:100]},
            "ItemList": items,
            "ValDtls": {"AssVal": _rupees(inv["taxable"]), "CgstVal": _rupees(inv["tax"] / 2),
                        "SgstVal": _rupees(inv["tax"] - inv["tax"] // 2), "IgstVal": 0, "TotInvVal": _rupees(inv["total"])},
        }
        qr = {"SellerGstin": inv["issuer_tax_id"], "BuyerGstin": "URP", "DocNo": inv["number"], "DocTyp": doc_type,
              "DocDt": when.strftime("%d/%m/%Y"), "TotInvVal": _rupees(inv["total"]), "ItemCnt": len(items),
              "MainHsnCode": main_hsn, "Irn": number, "Demo": "not registered with the IRP"}
        return {"irn": number, "fin_year": fy, "doc_type": doc_type, "json": doc, "qr": json.dumps(qr, separators=(",", ":")),
                "problems": self.einvoice_problems(inv), "state": STATE_OF_GSTIN.get(state, state)}

    @staticmethod
    def einvoice_problems(inv):
        """What stands between this invoice and a real registration at the IRP (plain sentences)."""
        out = ["Nothing is sent to the Invoice Registration Portal: this is a demo with a made-up GSTIN, so the QR code "
               "is not signed."]
        if not _DOC_NO.match(inv["number"]):
            out.append(f"The document number has {len(inv['number'])} characters; the official format allows "
                       f"{MAX_DOC_NO} (letters, digits, / and -).")
        out.append("The buyer has no GSTIN (a sale to a person). E-invoices are required for sales to businesses.")
        if any(not HSN_EXAMPLE.get(ln.get("category") or "") for ln in inv["line_list"]):
            out.append("Some lines have no HSN code.")
        out.append("HSN codes are one example code per category; a real invoice needs the code of each product.")
        return out

    def einvoice_json(self, invoice_id):
        e = self.einvoice(invoice_id)
        return json.dumps(e["json"], ensure_ascii=False, indent=2) if e else None

    # ------------------------------------------------------------------ rates checked by a second person
    def mark_rate_checked(self, actor_id, kind, key, valid_from="", valid_to="", note=""):
        """kind "country": key = country code (table tax_rules); kind "category": key = id of tax_category_rates."""
        if not self.can(actor_id, "tax.manage"):
            raise ShopError("You do not have the ability to check tax rates.")
        days = []
        for d in (valid_from, valid_to):
            d = str(d or "").strip()
            if d:
                try:
                    d = date.fromisoformat(d).isoformat()
                except ValueError:
                    raise ShopError("Please check the dates.")
            days.append(d)
        if days[0] and days[1] and days[1] < days[0]:
            raise ShopError("The last day is before the first day.")
        note = " ".join(str(note or "").split())[:200]
        me = self.get_user(actor_id)
        with self.db() as conn:
            if kind == "country":
                r = conn.execute("SELECT updated_by, example FROM tax_rules WHERE country = ?", (str(key).upper(),)).fetchone()
                if r is None:
                    raise ShopError("Rate not found.")
                if not r["example"] and r["updated_by"] == me["name"]:
                    raise ShopError("A rate must be checked by another person than the one who changed it.")
                conn.execute("UPDATE tax_rules SET checked_by = ?, checked_at = ?, valid_from = ?, valid_to = ?, "
                             "check_note = ? WHERE country = ?", (actor_id, now(), days[0], days[1], note, str(key).upper()))
            elif kind == "category":
                r = conn.execute("SELECT updated_by FROM tax_category_rates WHERE id = ?", (int(key),)).fetchone()
                if r is None:
                    raise ShopError("Rate not found.")
                if r["updated_by"] and r["updated_by"] == actor_id:
                    raise ShopError("A rate must be checked by another person than the one who changed it.")
                conn.execute("UPDATE tax_category_rates SET checked_by = ?, checked_at = ?, valid_from = ?, valid_to = ?, "
                             "check_note = ? WHERE id = ?", (actor_id, now(), days[0], days[1], note, int(key)))
            else:
                raise ShopError("Rate not found.")
        self.log(actor_id, "Tax rate checked", f"{kind} {key}")

    @staticmethod
    def rate_status(row, today=None):
        """"Example" (never checked), "Checked", "Changed after the check" or "Expired"."""
        day = (today or date.today()).isoformat()
        if not row.get("checked_at"):
            return "Example"
        if row.get("valid_to") and row["valid_to"] < day:
            return "Expired"
        if (row.get("updated_at") or "") > row["checked_at"]:
            return "Changed after the check"
        if row.get("valid_from") and row["valid_from"] > day:
            return "Not valid yet"
        return "Checked"

    def rates_review(self, today=None):
        """Both rate tables with their status and who checked them; and the counts by status."""
        with self.db() as conn:
            names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM users WHERE id IN (SELECT checked_by "
                                                              "FROM tax_rules UNION SELECT checked_by FROM tax_category_rates)")}
        countries, cats = self.tax_rules(), self.category_rates()
        counts = {}
        for r in countries + cats:
            r["status"] = self.rate_status(r, today)
            r["checked_name"] = names.get(r.get("checked_by"), "")
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        return {"countries": countries, "categories": cats, "counts": counts}
