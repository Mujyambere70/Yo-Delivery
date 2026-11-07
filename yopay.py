"""
yopay.py - Yo Pay, the wallet of the Yo super app (mixin class used by Store).

  * Every change of a wallet balance goes through wallet_move(): the balance and a line in the
    wallet_txns ledger are written in the SAME database transaction, and a payment can never make the
    balance negative (conditional UPDATE ... WHERE wallet + amount >= 0, safe with many servers).
  * Add money with a card (built-in test gateway - test cards only, no real money).
  * Send money to another Yo user by email or mobile number; the password is asked every time,
    like a UPI PIN. Limits per transfer and per day.
  * Statement on the site and as a CSV file.
  * EMI calculator: monthly instalments for bigger orders (the standard reducing-balance formula).

Money is in paise (₹1 = 100 paise), like the rest of the shop.
"""

import random
from datetime import datetime

from werkzeug.security import check_password_hash

import world
from core import ShopError, now

WALLET_KINDS = {
    "opening": "Opening balance", "signup": "Welcome bonus", "topup": "Money added by card",
    "order": "Shop order", "refund": "Refund", "transfer_out": "Money sent", "transfer_in": "Money received",
    "courier": "Courier booking", "earning": "Courier earning", "admin": "Changed by Yo Delivery",
    "reward": "Reward", "ticket": "Ticket booking", "payout": "Seller payout",
}
MAX_BALANCE = 200_000 * 100           # demo limit of a wallet: ₹2,00,000
TOPUP_MIN, TOPUP_MAX = 100 * 100, 50_000 * 100
SEND_MIN, SEND_MAX = 1 * 100, 25_000 * 100
SEND_PER_DAY = 50_000 * 100

# EMI plans (demo numbers, not a real bank offer): months -> yearly interest rate in %
EMI_PLANS = [(3, 13.0), (6, 14.0), (9, 15.0), (12, 16.0), (18, 16.0), (24, 16.0)]
EMI_FROM = 2_500 * 100                # EMI is offered for amounts of ₹2,500 or more
NO_COST_FROM = 10_000 * 100           # "no-cost EMI" (3 and 6 months) from ₹10,000
NO_COST_MONTHS = (3, 6)

YOPAY_SCHEMA = """
CREATE TABLE IF NOT EXISTS wallet_txns (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, amount INTEGER NOT NULL,
    kind TEXT NOT NULL, ref TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
    balance INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wallet_txns_user ON wallet_txns(user_id);
"""


def emi_monthly(principal, yearly_rate, months):
    """Monthly instalment in paise: P * r * (1 + r)^n / ((1 + r)^n - 1), r = monthly rate."""
    if months <= 0:
        raise ValueError("months")
    r = yearly_rate / 12 / 100
    if r == 0:
        return round(principal / months)
    f = (1 + r) ** months
    return round(principal * r * f / (f - 1))


def emi_plans(amount):
    """All EMI plans for an amount (paise). Empty when the amount is too small."""
    if amount < EMI_FROM:
        return []
    plans = []
    for months, rate in EMI_PLANS:
        no_cost = amount >= NO_COST_FROM and months in NO_COST_MONTHS
        if no_cost:                     # the shop pays the interest: it is given back as a discount
            monthly, total = round(amount / months), amount
        else:
            monthly = emi_monthly(amount, rate, months)
            total = monthly * months
        plans.append({"months": months, "rate": 0.0 if no_cost else rate, "monthly": monthly,
                      "interest": total - amount, "total": total, "no_cost": no_cost})
    return plans


def card_check(card, expiry, cvc, name):
    """Check a card form of the built-in test gateway. Returns (digits, ok, message)."""
    from payments import TEST_CARDS, luhn_ok
    card = "".join(ch for ch in str(card) if ch.isdigit())
    if not luhn_ok(card):
        raise ShopError("The card number is not valid.")
    try:
        month, year = (int(x) for x in str(expiry).replace(" ", "").split("/"))
        year += 2000 if year < 100 else 0
    except ValueError:
        raise ShopError("Please enter the expiry date as MM/YY.")
    today = datetime.now()
    if not 1 <= month <= 12 or (year, month) < (today.year, today.month):
        raise ShopError("This card has expired.")
    if not (str(cvc).isdigit() and len(str(cvc)) in (3, 4)):
        raise ShopError("Please enter the 3-digit security code (CVC).")
    if len(" ".join(str(name).split())) < 2:
        raise ShopError("Please enter the name on the card.")
    result, message = TEST_CARDS.get(card, ("declined", "Test mode: please use a test card, e.g. 4242 4242 4242 4242."))
    return card, result == "ok", message


def parse_rupees(value, label="amount"):
    try:
        paise = round(float(str(value).replace(",", "").replace("₹", "").strip()) * 100)
    except (TypeError, ValueError):
        raise ShopError(f"Please enter the {label} in rupees, for example 500.")
    if paise <= 0:
        raise ShopError(f"Please enter the {label} in rupees, for example 500.")
    return paise


class YoPayMixin:

    # ===== the one place where balances change =====

    def wallet_move(self, conn, user_id, amount, kind, ref="", note="", when=None):
        """Add (amount > 0) or take (amount < 0) money, inside the caller's transaction. Returns the new balance."""
        amount = int(amount)
        if amount == 0:
            raise ShopError("The amount cannot be zero.")
        if amount < 0:
            changed = conn.execute("UPDATE users SET wallet = wallet + ? WHERE id = ? AND wallet + ? >= 0",
                                   (amount, user_id, amount)).rowcount
            if not changed:
                bal = conn.execute("SELECT wallet FROM users WHERE id = ?", (user_id,)).fetchone()
                raise ShopError(f"Not enough Yo Pay balance ({world.price(bal[0] if bal else 0)}).")
        else:
            if not conn.execute("UPDATE users SET wallet = wallet + ? WHERE id = ?", (amount, user_id)).rowcount:
                raise ShopError("Account not found.")
        bal = conn.execute("SELECT wallet FROM users WHERE id = ?", (user_id,)).fetchone()[0]
        conn.execute("INSERT INTO wallet_txns (user_id, amount, kind, ref, note, balance, created_at) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?)", (user_id, amount, kind, ref, note[:200], bal, when or now()))
        return bal

    def open_wallet_ledgers(self):
        """Wallets made before version 3 get one 'opening balance' line, so the statement adds up."""
        with self.db() as conn:
            conn.execute("INSERT INTO wallet_txns (user_id, amount, kind, ref, note, balance, created_at) "
                         "SELECT id, wallet, 'opening', '', 'Balance before the statement started', wallet, ? "
                         "FROM users u WHERE wallet <> 0 AND NOT EXISTS "
                         "(SELECT 1 FROM wallet_txns t WHERE t.user_id = u.id)", (now(),))

    # ===== statement =====

    def wallet_statement(self, user_id, limit=200):
        with self.db() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM wallet_txns WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, limit))]
        for r in rows:
            r["kind_name"] = WALLET_KINDS.get(r["kind"], r["kind"])
        return rows

    def wallet_summary(self, user_id):
        month = datetime.now().strftime("%Y-%m")
        with self.db() as conn:
            bal = conn.execute("SELECT wallet FROM users WHERE id = ?", (user_id,)).fetchone()[0]
            r = conn.execute("SELECT COALESCE(SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END), 0), "
                             "COALESCE(SUM(CASE WHEN amount < 0 THEN -amount ELSE 0 END), 0) "
                             "FROM wallet_txns WHERE user_id = ? AND created_at LIKE ? AND kind <> 'opening'",
                             (user_id, month + "%")).fetchone()
        return {"balance": bal, "in_month": r[0], "out_month": r[1]}

    # ===== add money =====

    def topup_with_test_card(self, user_id, amount, card, expiry, cvc, name):
        """Add money with the built-in test gateway. Returns (ok, message, ref)."""
        amount = parse_rupees(amount)
        if not TOPUP_MIN <= amount <= TOPUP_MAX:
            raise ShopError(f"You can add from {world.price(TOPUP_MIN)} to {world.price(TOPUP_MAX)} at a time.")
        user = self.get_user(user_id)
        if user["wallet"] + amount > MAX_BALANCE:
            raise ShopError(f"A Yo Pay wallet can keep at most {world.price(MAX_BALANCE)}.")
        digits, ok, message = card_check(card, expiry, cvc, name)
        ref = "TOP" + datetime.now().strftime("%y%m%d") + "".join(random.choices("0123456789", k=8))
        with self.db() as conn:
            conn.execute("INSERT INTO payments (order_number, gateway, payment_ref, currency, amount, status, message, "
                         "created_at) VALUES (?, 'test', ?, 'INR', ?, ?, ?, ?)",
                         (ref, ref, amount, "Succeeded" if ok else "Failed",
                          f"wallet top-up, card ending {digits[-4:]}" + (f" - {message}" if message else ""), now()))
            if ok:
                self.wallet_move(conn, user_id, amount, "topup", ref, f"Card ending {digits[-4:]} (test mode)")
        return ok, message, ref

    # ===== send money =====

    def find_payee(self, text):
        text = " ".join(str(text or "").split())
        if not text:
            return None
        digits = "".join(ch for ch in text if ch.isdigit())
        with self.db() as conn:
            r = conn.execute("SELECT id, name, email, mobile, blocked FROM users WHERE lower(email) = ? OR mobile = ? "
                             "OR mobile = ?", (text.lower(), text, digits[-10:] if len(digits) >= 10 else text)
                             ).fetchone()
        return dict(r) if r else None

    def send_money(self, user_id, to, amount, note, password):
        amount = parse_rupees(amount)
        if not SEND_MIN <= amount <= SEND_MAX:
            raise ShopError(f"You can send from {world.price(SEND_MIN)} to {world.price(SEND_MAX)} at a time.")
        payee = self.find_payee(to)
        if payee is None or payee["blocked"]:
            raise ShopError("No Yo Delivery account has this email or mobile number.")
        if payee["id"] == user_id:
            raise ShopError("You cannot send money to yourself.")
        with self.db() as conn:
            row = conn.execute("SELECT password, name FROM users WHERE id = ?", (user_id,)).fetchone()
            if not check_password_hash(row["password"], str(password or "")):
                raise ShopError("The password is not correct. The money was not sent.")
            today = datetime.now().strftime("%Y-%m-%d")
            sent = conn.execute("SELECT COALESCE(SUM(-amount), 0) FROM wallet_txns WHERE user_id = ? "
                                "AND kind = 'transfer_out' AND created_at LIKE ?", (user_id, today + "%")).fetchone()[0]
            if sent + amount > SEND_PER_DAY:
                raise ShopError(f"You can send at most {world.price(SEND_PER_DAY)} per day "
                                f"(left today: {world.price(max(0, SEND_PER_DAY - sent))}).")
            if conn.execute("SELECT wallet FROM users WHERE id = ?", (payee["id"],)).fetchone()[0] + amount > MAX_BALANCE:
                raise ShopError("The wallet of this person is full. Please send less.")
            note = " ".join(str(note or "").split())[:80]
            ref = "TRF" + datetime.now().strftime("%y%m%d") + "".join(random.choices("0123456789", k=8))
            self.wallet_move(conn, user_id, -amount, "transfer_out", ref, f"To {payee['name']}" + (f" - {note}" if note else ""))
            self.wallet_move(conn, payee["id"], amount, "transfer_in", ref, f"From {row['name']}" + (f" - {note}" if note else ""))
            self.notify(conn, payee["id"], "You received {0}".replace("{0}", world.format_money(amount, "INR")),
                        f"{row['name']} sent you {world.format_money(amount, 'INR')} on Yo Pay."
                        + (f" {note}" if note else ""), url="/pay")
        return ref, payee

    # ===== admin =====

    def wallet_report(self, limit=300, kind=""):
        sql = ("SELECT t.*, u.name, u.email FROM wallet_txns t JOIN users u ON u.id = t.user_id "
               + ("WHERE t.kind = ? " if kind else "") + "ORDER BY t.id DESC LIMIT ?")
        with self.db() as conn:
            rows = [dict(r) for r in conn.execute(sql, ((kind, limit) if kind else (limit,)))]
            totals = {r[0]: r[1] for r in conn.execute("SELECT kind, SUM(amount) FROM wallet_txns GROUP BY kind")}
            held = conn.execute("SELECT COALESCE(SUM(wallet), 0) FROM users").fetchone()[0]
        for r in rows:
            r["kind_name"] = WALLET_KINDS.get(r["kind"], r["kind"])
        return rows, totals, held

    def wallet_audit(self):
        """Users whose balance is not equal to the sum of their ledger lines (should be none)."""
        with self.db() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT u.id, u.name, u.wallet, COALESCE(SUM(t.amount), 0) AS ledger FROM users u "
                "LEFT JOIN wallet_txns t ON t.user_id = u.id GROUP BY u.id, u.name, u.wallet "
                "HAVING u.wallet <> COALESCE(SUM(t.amount), 0)")]
