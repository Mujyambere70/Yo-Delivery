"""
payments.py - money for Yo Delivery (mixin class used by Store):

  * Exchange rates: prices are kept in rupees; to show them in another currency the shop downloads
    the day's rates from the free open.er-api.com service (no key needed). Without a rate the price
    is simply shown in rupees - the shop never guesses a rate.

  * Card payments through a payment gateway in TEST MODE:
      - Stripe Checkout, when STRIPE_SECRET_KEY (sk_test_...) is set. The customer pays on Stripe's
        own page; Stripe tells the shop with a signed webhook (STRIPE_WEBHOOK_SECRET) and the shop
        also checks the session when the customer comes back.
      - Otherwise the built-in test gateway (a page in the shop that accepts only test cards).
    No real money is taken in either mode.

  * Refunds: to the card through the same gateway, or to the Yo Pay wallet.
"""

import hashlib
import hmac
import json
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request

import world
from core import ShopError, now

FX_URL = "https://open.er-api.com/v6/latest/USD"
STRIPE_API = "https://api.stripe.com/v1"

# test cards of the built-in gateway (the same numbers Stripe uses in test mode)
TEST_CARDS = {
    "4242424242424242": ("ok", ""),
    "5555555555554444": ("ok", ""),
    "4000000000000002": ("declined", "Your card was declined."),
    "4000000000009995": ("declined", "Your card has insufficient funds."),
}


def luhn_ok(number):
    """The check digit test every card number passes (ISO/IEC 7812)."""
    digits = [int(d) for d in number][::-1]
    total = sum(d if i % 2 == 0 else (d * 2 - 9 if d * 2 > 9 else d * 2) for i, d in enumerate(digits))
    return len(number) >= 12 and total % 10 == 0


def fetch_rates(timeout=15):
    """Units of every supported currency for 1 US dollar, from open.er-api.com."""
    with urllib.request.urlopen(FX_URL, timeout=timeout) as resp:       # nosec B310 - a fixed https address
        data = json.loads(resp.read().decode())
    if data.get("result") != "success":
        raise RuntimeError("exchange-rate service did not answer")
    return {c: float(data["rates"][c]) for c in world.CURRENCIES if c in data.get("rates", {})}


def stripe_signature_ok(payload, header, secret, tolerance=300, at=None):
    """Check the Stripe-Signature header of a webhook: HMAC-SHA256 of '<timestamp>.<body>'."""
    try:
        parts = dict(p.split("=", 1) for p in header.split(","))
        stamp, sent = int(parts["t"]), [v for k, v in (p.split("=", 1) for p in header.split(",")) if k == "v1"]
    except (ValueError, KeyError):
        return False
    if abs((at or time.time()) - stamp) > tolerance:
        return False
    expected = hmac.new(secret.encode(), f"{stamp}.".encode() + payload, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, s) for s in sent)


class PaymentsMixin:
    env = os.environ
    fx_fetch = None             # tests put a function here instead of downloading rates
    http = None                 # tests put a fake HTTP opener here

    # ===== exchange rates =====

    def fx_rates(self):
        with self.db() as conn:
            return {r["currency"]: dict(r) for r in conn.execute("SELECT * FROM fx_rates ORDER BY currency")}

    def rates(self):
        """{currency: units per 1 US dollar} - what world.convert() needs."""
        return {c: r["per_usd"] for c, r in self.fx_rates().items()}

    def save_fx_rates(self, rates, source):
        rates = {c: float(v) for c, v in rates.items() if c in world.CURRENCIES and float(v) > 0}
        rates["USD"] = 1.0
        with self.db() as conn:
            for cur, per_usd in rates.items():
                conn.execute("INSERT INTO fx_rates (currency, per_usd, updated_at, source) VALUES (?, ?, ?, ?) "
                             "ON CONFLICT (currency) DO UPDATE SET per_usd = excluded.per_usd, "
                             "updated_at = excluded.updated_at, source = excluded.source",
                             (cur, per_usd, now(), source))
        return len(rates)

    def refresh_fx_rates(self):
        return self.save_fx_rates((self.fx_fetch or fetch_rates)(), "open.er-api.com")

    # ===== card gateway =====

    def gateway(self):
        key = self.env.get("STRIPE_SECRET_KEY", "")
        if key.startswith("sk_live"):
            raise ShopError("Live Stripe keys are not allowed: this is a demo shop. Use a test key (sk_test_...).")
        return "stripe" if key.startswith("sk_test_") else "test"

    def _stripe(self, method, path, data=None):
        body = urllib.parse.urlencode(data or {}).encode() if data is not None else None
        req = urllib.request.Request(STRIPE_API + path, data=body, method=method, headers={
            "Authorization": "Bearer " + self.env["STRIPE_SECRET_KEY"],
            "Content-Type": "application/x-www-form-urlencoded"})
        try:
            with (self.http or urllib.request.urlopen)(req, timeout=20) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read().decode())["error"]["message"]
            except Exception:
                msg = f"HTTP {e.code}"
            raise ShopError(f"The payment service answered: {msg}")
        except OSError:
            raise ShopError("The payment service cannot be reached right now. Please try again in a minute.")

    @staticmethod
    def gateway_amount(amount, currency):
        """Our amounts are x100; zero-decimal currencies (RWF) are sent to the gateway in whole units."""
        return amount // 100 if currency in world.ZERO_DECIMAL else amount

    def start_card_payment(self, user_id, number, base_url):
        """Begin paying a card order. Returns the address the customer must be sent to."""
        o = self.get_order(user_id, number)
        if o["payment_method"] != "Card" or o["cancelled_at"]:
            raise ShopError("This order cannot be paid by card.")
        if o["payment_status"] == "Paid":
            raise ShopError("This order is already paid.")
        gw = self.gateway()
        if gw == "test":
            return f"{base_url}pay/{number}"
        user = self.get_user(user_id)
        session = self._stripe("POST", "/checkout/sessions", {
            "mode": "payment",
            "success_url": f"{base_url}pay/{number}/return?session_id={{CHECKOUT_SESSION_ID}}",
            "cancel_url": f"{base_url}orders/{number}?payment=cancelled",
            "client_reference_id": number, "metadata[order]": number, "customer_email": user["email"],
            "line_items[0][quantity]": 1,
            "line_items[0][price_data][currency]": o["currency"].lower(),
            "line_items[0][price_data][unit_amount]": self.gateway_amount(o["total_local"], o["currency"]),
            "line_items[0][price_data][product_data][name]": f"Yo Delivery order {number}",
        })
        with self.db() as conn:
            conn.execute("INSERT INTO payments (order_number, gateway, session_id, currency, amount, status, created_at) "
                         "VALUES (?, 'stripe', ?, ?, ?, 'Started', ?)",
                         (number, session["id"], o["currency"], o["total_local"], now()))
        return session["url"]

    def pay_with_test_card(self, user_id, number, card, expiry, cvc, name):
        """The built-in test gateway. Returns (ok, message)."""
        o = self.get_order(user_id, number)
        if o["payment_method"] != "Card" or o["cancelled_at"] or o["payment_status"] == "Paid":
            raise ShopError("This order cannot be paid now.")
        card = "".join(ch for ch in str(card) if ch.isdigit())
        if not luhn_ok(card):
            raise ShopError("The card number is not valid.")
        try:
            month, year = (int(x) for x in str(expiry).replace(" ", "").split("/"))
            year += 2000 if year < 100 else 0
        except ValueError:
            raise ShopError("Please enter the expiry date as MM/YY.")
        today = time.localtime()
        if not 1 <= month <= 12 or (year, month) < (today.tm_year, today.tm_mon):
            raise ShopError("This card has expired.")
        if not (str(cvc).isdigit() and len(str(cvc)) in (3, 4)):
            raise ShopError("Please enter the 3-digit security code (CVC).")
        if len(" ".join(str(name).split())) < 2:
            raise ShopError("Please enter the name on the card.")
        result, message = TEST_CARDS.get(card, ("declined", "Test mode: please use a test card, e.g. "
                                                             "4242 4242 4242 4242."))
        ref = "tst_" + "".join(random.choices("abcdefghijklmnopqrstuvwxyz0123456789", k=16))
        with self.db() as conn:
            conn.execute("INSERT INTO payments (order_number, gateway, payment_ref, currency, amount, status, message, "
                         "created_at) VALUES (?, 'test', ?, ?, ?, ?, ?, ?)",
                         (number, ref, o["currency"], o["total_local"], "Succeeded" if result == "ok" else "Failed",
                          f"card ending {card[-4:]}" + (f" - {message}" if message else ""), now()))
        if result != "ok":
            with self.db() as conn:
                conn.execute("UPDATE orders SET payment_status = 'Failed' WHERE number = ? AND payment_status = 'Pending'",
                             (number,))
            return False, message
        self.mark_paid(number, ref, f"Card ending {card[-4:]} - test gateway (no real money)")
        return True, ""

    def finish_stripe(self, number, session_id):
        """The customer came back from Stripe: ask Stripe whether the session was paid."""
        session = self._stripe("GET", "/checkout/sessions/" + urllib.parse.quote(session_id))
        if session.get("client_reference_id") != number:
            raise ShopError("This payment does not belong to this order.")
        if session.get("payment_status") != "paid":
            return False
        self.mark_paid(number, session.get("payment_intent") or session_id, "Card - Stripe test mode")
        return True

    def stripe_webhook(self, payload, signature):
        """Stripe calls this when a Checkout Session is completed. Returns the order number or None."""
        secret = self.env.get("STRIPE_WEBHOOK_SECRET", "")
        if not secret or not stripe_signature_ok(payload, signature or "", secret):
            raise ShopError("Bad signature.")
        event = json.loads(payload.decode())
        if event.get("type") != "checkout.session.completed":
            return None
        obj = event["data"]["object"]
        number = obj.get("client_reference_id")
        if number and obj.get("payment_status") == "paid":
            self.mark_paid(number, obj.get("payment_intent") or obj.get("id"), "Card - Stripe test mode")
            return number
        return None

    def mark_paid(self, number, ref, note):
        """Paid exactly once, even if the webhook and the customer's return arrive at the same time."""
        with self.db() as conn:
            changed = conn.execute(
                "UPDATE orders SET payment_status = 'Paid', paid_at = ?, payment_ref = ?, payment_note = ? "
                "WHERE number = ? AND payment_status IN ('Pending', 'Failed') AND cancelled_at = ''",
                (now(), ref, note, number)).rowcount
            if changed:
                conn.execute("UPDATE payments SET status = 'Succeeded', payment_ref = ? WHERE order_number = ? "
                             "AND gateway = 'stripe' AND status = 'Started'", (ref, number))
        return bool(changed)

    def expire_unpaid(self, minutes=None):
        """Card orders not paid in time are cancelled and their stock comes back."""
        from store import PAYMENT_WINDOW_MINUTES
        limit = time.strftime("%Y-%m-%d %H:%M:%S",
                              time.localtime(time.time() - 60 * (minutes or PAYMENT_WINDOW_MINUTES)))
        with self.db() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT id, number FROM orders WHERE payment_status IN ('Pending', 'Failed') AND cancelled_at = '' "
                "AND placed_at < ?", (limit,))]
        done = 0
        for r in rows:
            with self.db() as conn:
                if conn.execute("UPDATE orders SET cancelled_at = ?, payment_status = 'Expired' WHERE id = ? "
                                "AND cancelled_at = '' AND payment_status IN ('Pending', 'Failed')",
                                (now(), r["id"])).rowcount:
                    for it in conn.execute("SELECT product_id, qty FROM order_items WHERE order_id = ?",
                                           (r["id"],)).fetchall():
                        conn.execute("UPDATE products SET stock = stock + ? WHERE id = ?", (it[1], it[0]))
                    done += 1
        return done

    # ===== refunds =====

    def refund_card(self, order, amount_inr, reason):
        """Give money back for a paid card order: through Stripe (test mode) or the test gateway.
        `amount_inr` is in shop rupees; the card gets the same share of what was charged in its currency."""
        local = int(order["total_local"] * amount_inr / order["total"]) if order["total"] else 0
        ref = order.get("payment_ref", "")
        if ref.startswith("pi_") and self.gateway() == "stripe":
            r = self._stripe("POST", "/refunds", {"payment_intent": ref,
                                                  "amount": self.gateway_amount(local, order["currency"]),
                                                  "metadata[reason]": reason[:200]})
            refund_ref = r.get("id", "")
        else:
            refund_ref = "tst_re_" + "".join(random.choices("abcdefghijklmnopqrstuvwxyz0123456789", k=12))
        with self.db() as conn:
            conn.execute("UPDATE orders SET refunded = refunded + ? WHERE number = ?", (amount_inr, order["number"]))
            conn.execute("INSERT INTO payments (order_number, gateway, payment_ref, currency, amount, status, message, "
                         "created_at) VALUES (?, ?, ?, ?, ?, 'Refunded', ?, ?)",
                         (order["number"], "stripe" if refund_ref.startswith("re_") else "test", refund_ref,
                          order["currency"], local, reason[:200], now()))
        return refund_ref

    def payments_of(self, number):
        with self.db() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM payments WHERE order_number = ? ORDER BY id",
                                                  (number,))]
