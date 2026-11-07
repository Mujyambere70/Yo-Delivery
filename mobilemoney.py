"""
mobilemoney.py - version 6 (mixin class used by Store): local ways to pay in TEST MODE - M-Pesa (Kenya, Tanzania),
MTN MoMo (Rwanda, Uganda, Ghana and more), SBP (the Faster Payments System of Russia) and UPI (India).

How it works (like the real services, but nothing leaves this server and no real money moves):
    1. the customer chooses the way to pay and writes his phone number (or his UPI ID)
    2. we "send a request to his phone": a page in test mode stands for the phone and has Approve / Decline
    3. approved -> the money is added to Yo Pay once (a conditional UPDATE: a second click changes nothing);
       declined or not answered in 10 minutes -> nothing is taken
Prices stay in rupees inside the shop; the request shows the amount in the customer's currency with the rate of
the shop (world.convert). A real launch needs a contract with each provider (Safaricom Daraja, MTN MoMo API,
an SBP bank, a UPI payment service provider) and their webhooks instead of the test page.
"""

import re
import secrets
from datetime import datetime, timedelta

import world
from core import ShopError, now
from yopay import MAX_BALANCE, TOPUP_MAX, TOPUP_MIN, parse_rupees

MOBILE_SCHEMA = """
CREATE TABLE IF NOT EXISTS mobile_payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ref TEXT NOT NULL UNIQUE, user_id INTEGER NOT NULL REFERENCES users(id),
    method TEXT NOT NULL, country TEXT NOT NULL, account TEXT NOT NULL, amount INTEGER NOT NULL,
    currency TEXT NOT NULL, amount_local REAL NOT NULL, status TEXT NOT NULL DEFAULT 'Waiting',
    created_at TEXT NOT NULL, expires_at TEXT NOT NULL, decided_at TEXT NOT NULL DEFAULT ''
);
"""

# method -> (name, countries, what the customer writes)
METHODS = {
    "mpesa": ("M-Pesa", ("KE", "TZ"), "phone"),
    "momo": ("MTN MoMo", ("RW", "UG", "GH", "CM", "CI", "ZM", "BJ", "CG"), "phone"),
    "sbp": ("SBP (Faster Payments)", ("RU",), "phone"),
    "upi": ("UPI", ("IN",), "upi"),
}
WAIT_MIN = 10
UPI_RE = re.compile(r"^[a-z0-9._-]{2,64}@[a-z][a-z0-9]{1,63}$")


def methods_for(country):
    """The local ways to pay of a country (all of them are shown, the country's own first)."""
    own = [k for k, v in METHODS.items() if country in v[1]]
    return own + [k for k in METHODS if k not in own]


def mask(account):
    a = str(account)
    if "@" in a:
        name, host = a.split("@", 1)
        return name[:2] + "***@" + host
    return a[:-6] + "***" + a[-3:] if len(a) > 8 else "***" + a[-3:]


class MobileMoneyMixin:
    def start_mobile_payment(self, user_id, method, amount, account, country=None):
        if method not in METHODS:
            raise ShopError("Please choose how to pay.")
        name, countries, kind = METHODS[method]
        amount = parse_rupees(amount)
        if not TOPUP_MIN <= amount <= TOPUP_MAX:
            raise ShopError(f"You can add from {world.price(TOPUP_MIN)} to {world.price(TOPUP_MAX)} at a time.")
        user = self.get_user(user_id)
        if user["wallet"] + amount > MAX_BALANCE:
            raise ShopError("Your Yo Pay balance would be too high.")
        country = (country or (countries[0] if len(countries) == 1 else user["country"])).upper()
        if country not in countries:
            raise ShopError(f"{name} works in: " + ", ".join(world.country_name(c) for c in countries) + ".")
        if kind == "upi":
            account = str(account or "").strip().lower()
            if not UPI_RE.match(account):
                raise ShopError("Please write your UPI ID, for example name@okbank.")
        else:
            account = world.clean_mobile(country, account)
            if account is None:
                raise ShopError(f"Please write the {name} phone number with the country code.")
        currency = world.default_currency(country)
        try:
            local = world.convert(amount, currency, self.rates())        # smallest unit x100, like the shop
        except Exception:
            local = None
        if local is None:                                # no rate for this currency yet: the request is in rupees
            currency, local = "INR", amount
        local = local / 100
        ref = "MM" + secrets.token_hex(6).upper()
        expires = (datetime.now() + timedelta(minutes=WAIT_MIN)).strftime("%Y-%m-%d %H:%M:%S")
        with self.db() as conn:
            if conn.execute("SELECT COUNT(*) FROM mobile_payments WHERE user_id = ? AND status = 'Waiting' "
                            "AND expires_at > ?", (user_id, now())).fetchone()[0] >= 3:
                raise ShopError("You have 3 requests waiting. Please answer them on your phone first.")
            conn.execute("INSERT INTO mobile_payments (ref, user_id, method, country, account, amount, currency, "
                         "amount_local, status, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Waiting', ?, ?)",
                         (ref, user_id, method, country, account, amount, currency, float(local or 0), now(), expires))
        return ref

    def mobile_payment(self, ref, user_id=None):
        with self.db() as conn:
            r = conn.execute("SELECT * FROM mobile_payments WHERE ref = ?", (str(ref or "").upper()[:20],)).fetchone()
        if r is None or (user_id is not None and r["user_id"] != user_id):
            return None
        d = dict(r)
        d["method_name"] = METHODS[d["method"]][0]
        d["account_masked"] = mask(d["account"])
        if d["status"] == "Waiting" and d["expires_at"] <= now():
            d["status"] = "Expired"
        return d

    def answer_mobile_payment(self, user_id, ref, approve):
        """The answer "on the phone" (test mode). Money is added only once."""
        p = self.mobile_payment(ref, user_id)
        if p is None:
            raise ShopError("Payment not found.")
        with self.db() as conn:
            if not approve:
                n = conn.execute("UPDATE mobile_payments SET status = 'Declined', decided_at = ? WHERE id = ? "
                                 "AND status = 'Waiting'", (now(), p["id"])).rowcount
                if not n:
                    raise ShopError("This request was answered already.")
                return "Declined"
            n = conn.execute("UPDATE mobile_payments SET status = 'Paid', decided_at = ? WHERE id = ? AND status = 'Waiting' "
                             "AND expires_at > ?", (now(), p["id"], now())).rowcount
            if not n:
                raise ShopError("This request was answered already or it expired. Please start again.")
            self.wallet_move(conn, user_id, p["amount"], "topup", p["ref"],
                             f"{p['method_name']} {p['account_masked']} (test mode)")
            conn.execute("INSERT INTO payments (order_number, gateway, payment_ref, currency, amount, status, message, "
                         "created_at) VALUES (?, ?, ?, ?, ?, 'Succeeded', ?, ?)",
                         (p["ref"], "mobile:" + p["method"], p["ref"], p["currency"], p["amount"],
                          "Test mode mobile money", now()))
        return "Paid"
