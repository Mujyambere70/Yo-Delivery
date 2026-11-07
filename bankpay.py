"""
bankpay.py - version 6 (mixin class used by Store): payouts of sellers to a bank account, in TEST MODE.

    * the seller adds a bank account; the number is checked like a bank does it:
        IBAN (Europe, the Gulf, Turkey, Kazakhstan ...): length of the country + the mod-97 check (ISO 13616)
        India: IFSC code (4 letters, 0, 6 letters or digits) + account number of 9 to 18 digits
        Russia: BIK (9 digits, 04...) + account of 20 digits with the control key of the Bank of Russia
        other countries (Rwanda, Kenya ...): bank name + account number of 8 to 20 digits (checked by the bank)
    * the shop keeps only the name, the bank, the LAST 4 digits and a fingerprint (HMAC) of the number - never the
      whole number. A real launch would hand the number to the payout provider (Stripe Connect, a bank API) and keep
      only its token.
    * when the seller chooses "bank", the daily payout becomes a bank transfer: status Sent, then Paid when the
      (test) bank answers - here the job queue does it a few minutes later. Nothing leaves this server.
"""

import hashlib
import hmac
import re
from datetime import datetime, timedelta

from core import ShopError, now

BANK_SCHEMA = """
CREATE TABLE IF NOT EXISTS bank_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, seller_id INTEGER NOT NULL UNIQUE REFERENCES sellers(id),
    holder TEXT NOT NULL, country TEXT NOT NULL, scheme TEXT NOT NULL, bank TEXT NOT NULL,
    last4 TEXT NOT NULL, fingerprint TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bank_transfers (
    id INTEGER PRIMARY KEY AUTOINCREMENT, seller_id INTEGER NOT NULL REFERENCES sellers(id),
    payout_id INTEGER NOT NULL UNIQUE REFERENCES payouts(id), amount INTEGER NOT NULL, bank TEXT NOT NULL,
    last4 TEXT NOT NULL, ref TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Sent', created_at TEXT NOT NULL,
    settle_after TEXT NOT NULL, settled_at TEXT NOT NULL DEFAULT ''
);
"""

IBAN_LENGTH = {
    "AD": 24, "AE": 23, "AL": 28, "AT": 20, "AZ": 28, "BA": 20, "BE": 16, "BG": 22, "BH": 22, "BR": 29, "BY": 28,
    "CH": 21, "CR": 22, "CY": 28, "CZ": 24, "DE": 22, "DK": 18, "DO": 28, "EE": 20, "EG": 29, "ES": 24, "FI": 18,
    "FO": 18, "FR": 27, "GB": 22, "GE": 22, "GI": 23, "GL": 18, "GR": 27, "GT": 28, "HR": 21, "HU": 28, "IE": 22,
    "IL": 23, "IQ": 23, "IS": 26, "IT": 27, "JO": 30, "KW": 30, "KZ": 20, "LB": 28, "LC": 32, "LI": 21, "LT": 20,
    "LU": 20, "LV": 21, "MC": 27, "MD": 24, "ME": 22, "MK": 19, "MR": 27, "MT": 31, "MU": 30, "NL": 18, "NO": 15,
    "PK": 24, "PL": 28, "PS": 29, "PT": 25, "QA": 29, "RO": 24, "RS": 22, "SA": 24, "SC": 31, "SE": 24, "SI": 19,
    "SK": 24, "SM": 27, "ST": 25, "SV": 28, "TL": 23, "TN": 24, "TR": 26, "UA": 29, "VA": 22, "VG": 24, "XK": 20,
}
SETTLE_MINUTES = 3                      # the test bank "answers" after a few minutes
SCHEME_NAMES = {"iban": "IBAN", "ifsc": "IFSC + account", "bik": "BIK + account", "local": "Bank account"}


def _digits(text):
    return re.sub(r"\D", "", str(text or ""))


def iban_ok(iban):
    """ISO 13616: move the first 4 characters to the end, letters -> numbers (A = 10), the number mod 97 is 1."""
    s = re.sub(r"\s", "", str(iban or "")).upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}", s):
        return False
    if IBAN_LENGTH.get(s[:2], len(s)) != len(s):
        return False
    n = "".join(str(int(ch, 36)) for ch in s[4:] + s[:4])
    return int(n) % 97 == 1


def ifsc_ok(code):
    return bool(re.fullmatch(r"[A-Z]{4}0[A-Z0-9]{6}", str(code or "").strip().upper()))


def ru_account_ok(bik, account):
    """Bank of Russia: the last 3 digits of the BIK + the 20 digits of the account, weights 7 1 3 7 1 3 ...,
    the sum of the last digits of the products must end in 0."""
    bik, account = _digits(bik), _digits(account)
    if len(bik) != 9 or not bik.startswith("04") or len(account) != 20:
        return False
    s = bik[-3:] + account
    return sum(int(d) * w for d, w in zip(s, [7, 1, 3] * 8)) % 10 == 0


class BankPayMixin:
    def _bank_key(self):
        return ((self.env.get("SECRET_KEY") or self.secret_key()) + ":bank").encode()

    def check_bank_account(self, country, f):
        """Check the form; returns (scheme, bank label, the whole number for the fingerprint)."""
        country = str(country or "").upper()
        if f.get("iban") or country in IBAN_LENGTH:
            iban = re.sub(r"\s", "", str(f.get("iban", ""))).upper()
            if not iban_ok(iban):
                raise ShopError("This IBAN is not correct. Please check the letters and digits.")
            return "iban", f"IBAN {iban[:2]} {iban[4:8]}", iban
        if country == "IN":
            code = str(f.get("ifsc", "")).strip().upper()
            acc = _digits(f.get("account"))
            if not ifsc_ok(code):
                raise ShopError("The IFSC code has 11 characters, for example SBIN0001234.")
            if not 9 <= len(acc) <= 18:
                raise ShopError("The account number has 9 to 18 digits.")
            return "ifsc", f"IFSC {code}", code + acc
        if country == "RU":
            bik, acc = _digits(f.get("bik")), _digits(f.get("account"))
            if not ru_account_ok(bik, acc):
                raise ShopError("The BIK (9 digits) and the account (20 digits) do not match. Please check them.")
            return "bik", f"BIK {bik}", bik + acc
        bank = " ".join(str(f.get("bank", "")).split())[:60]
        acc = _digits(f.get("account"))
        if len(bank) < 2:
            raise ShopError("Please write the name of the bank.")
        if not 8 <= len(acc) <= 20:
            raise ShopError("The account number has 8 to 20 digits.")
        return "local", bank, bank.upper() + acc

    def save_bank_account(self, owner_id, f):
        shop = self.seller_of(owner_id)
        if not shop:
            raise ShopError("Only the owner of the shop can add the bank account.")
        holder = " ".join(str(f.get("holder", "")).split())[:80]
        if len(holder) < 3:
            raise ShopError("Please write the name of the account holder.")
        country = str(f.get("country") or "IN").upper()
        scheme, bank, number = self.check_bank_account(country, f)
        last4 = _digits(number)[-4:] or number[-4:]
        finger = hmac.new(self._bank_key(), number.encode(), hashlib.sha256).hexdigest()[:24]
        with self.db() as conn:
            conn.execute("INSERT INTO bank_accounts (seller_id, holder, country, scheme, bank, last4, fingerprint, "
                         "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (seller_id) DO UPDATE SET "
                         "holder = excluded.holder, country = excluded.country, scheme = excluded.scheme, "
                         "bank = excluded.bank, last4 = excluded.last4, fingerprint = excluded.fingerprint, "
                         "created_at = excluded.created_at",
                         (shop["id"], holder, country, scheme, bank, last4, finger, now()))
            conn.execute("UPDATE sellers SET payout_to = 'bank' WHERE id = ?", (shop["id"],))
        return {"bank": bank, "last4": last4, "scheme": scheme}

    def bank_account(self, seller_id):
        with self.db() as conn:
            r = conn.execute("SELECT * FROM bank_accounts WHERE seller_id = ?", (seller_id,)).fetchone()
        return dict(r) if r else None

    def set_payout_to(self, owner_id, where):
        shop = self.seller_of(owner_id)
        if not shop or where not in ("wallet", "bank"):
            raise ShopError("Please choose where we send your money.")
        if where == "bank" and not self.bank_account(shop["id"]):
            raise ShopError("Please add your bank account first.")
        with self.db() as conn:
            conn.execute("UPDATE sellers SET payout_to = ? WHERE id = ?", (where, shop["id"]))

    def remove_bank_account(self, owner_id):
        shop = self.seller_of(owner_id)
        if not shop:
            raise ShopError("Only the owner of the shop can remove the bank account.")
        with self.db() as conn:
            conn.execute("DELETE FROM bank_accounts WHERE seller_id = ?", (shop["id"],))
            conn.execute("UPDATE sellers SET payout_to = 'wallet' WHERE id = ?", (shop["id"],))

    def bank_transfer(self, conn, seller_id, payout_id, amount):
        """Called inside the payout transaction instead of the Yo Pay move. Returns False if there is no account."""
        acc = conn.execute("SELECT * FROM bank_accounts WHERE seller_id = ?", (seller_id,)).fetchone()
        if acc is None:
            return False
        ref = f"YOB{payout_id:07d}"
        after = (datetime.now() + timedelta(minutes=SETTLE_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("INSERT INTO bank_transfers (seller_id, payout_id, amount, bank, last4, ref, created_at, settle_after) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (seller_id, payout_id, amount, acc["bank"], acc["last4"], ref,
                                                         now(), after))
        conn.execute("UPDATE payouts SET note = ? WHERE id = ?",
                     (f"Bank transfer {ref} to {acc['bank']} ****{acc['last4']} (test mode)", payout_id))
        return True

    def settle_bank_transfers(self, at=None):
        """The test bank answers: Sent -> Paid (a real launch reads the webhook of the payout provider)."""
        at = at or now()
        with self.db() as conn:
            rows = conn.execute("SELECT t.*, s.user_id FROM bank_transfers t JOIN sellers s ON s.id = t.seller_id "
                                "WHERE t.status = 'Sent' AND t.settle_after <= ?", (at,)).fetchall()
            done = 0
            for r in rows:
                if conn.execute("UPDATE bank_transfers SET status = 'Paid', settled_at = ? WHERE id = ? AND status = 'Sent'",
                                (at, r["id"])).rowcount:
                    done += 1
                    self.notify(conn, r["user_id"], "Money in your bank",
                                f"Transfer {r['ref']} to {r['bank']} ****{r['last4']} is paid (test mode).",
                                url="/seller/bank")
        return done

    def bank_transfers(self, seller_id=None, limit=50):
        with self.db() as conn:
            if seller_id:
                rows = conn.execute("SELECT * FROM bank_transfers WHERE seller_id = ? ORDER BY id DESC LIMIT ?",
                                    (seller_id, limit))
            else:
                rows = conn.execute("SELECT t.*, s.shop_name FROM bank_transfers t JOIN sellers s ON s.id = t.seller_id "
                                    "ORDER BY t.id DESC LIMIT ?", (limit,))
            return [dict(r) for r in rows]
