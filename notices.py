"""
notices.py - telling customers what happens to their orders (mixin class used by Store).

Every message is saved for the website (Account -> Notifications). It is also sent by email (Brevo or
SMTP), SMS (Twilio) and as a push notification to phones that installed the shop as an app - when those
services are configured and the customer allowed them. Messages are written in the customer's language.

Order status changes happen by time (demo tracking) or by the admin. `sync_order_updates()` runs about
once a minute: it finds orders whose status moved on and sends one message for the newest status.
A database "claim" (UPDATE ... WHERE notified_step = old) makes sure a message is never sent twice,
even with several server processes.

Also here: password reset links by email, and two-factor login (TOTP) for customers, sellers and admins.
"""

import hashlib
import secrets
from datetime import datetime, timedelta

import i18n
import otp
import push
import world
from core import ShopError, now

STEP_MESSAGES = {
    0: ("Order {0} placed", "Thank you! Your order {0} of {1} was placed. Expected delivery: {2}."),
    1: ("Order {0} packed", "Good news: your order {0} is packed and will be shipped soon."),
    2: ("Order {0} shipped", "Your order {0} was shipped with {1}. Tracking number: {2}."),
    3: ("Order {0} is out for delivery", "Your order {0} is out for delivery and will reach you today."),
    4: ("Order {0} delivered", "Your order {0} was delivered. Enjoy! You can return it within {1} days."),
}
RESET_MINUTES = 30


class NoticesMixin:
    notifier = None             # set by Store (notify.Notifier)

    # ===== sending =====

    def _contact(self, conn, user_id):
        r = conn.execute("SELECT id, name, email, mobile, lang, notify_email, notify_sms FROM users WHERE id = ?",
                         (user_id,)).fetchone()
        return dict(r) if r else None

    def notify(self, conn, user_id, subject, body, order_number="", url="/account/notifications"):
        """Save a message (always) and send it by email / SMS / push after the commit (when possible).
        `subject` and `body` are English with {0}-style values already filled in; they are translated
        to the customer's language here."""
        c = self._contact(conn, user_id)
        if c is None:
            return
        lang = c["lang"] or "en"
        subject, body = i18n.translate_text(subject, lang), i18n.translate_text(body, lang)
        when = now()
        conn.execute("INSERT INTO notifications (user_id, order_number, channel, subject, body, status, created_at) "
                     "VALUES (?, ?, 'site', ?, ?, 'Delivered', ?)", (user_id, order_number, subject, body, when))
        from store import is_example_email
        if self.notifier is None or is_example_email(c["email"]):   # demo customers are never contacted
            return
        status, jobs = self.notifier.status(), []
        targets = [("email", c["notify_email"] and status["email"], c["email"]),
                   ("sms", c["notify_sms"] and status["sms"], c["mobile"])]
        for channel, wanted, to in targets:
            if wanted:
                cur = conn.execute("INSERT INTO notifications (user_id, order_number, channel, destination, subject, "
                                   "body, status, created_at, seen) VALUES (?, ?, ?, ?, ?, ?, 'Queued', ?, 1)",
                                   (user_id, order_number, channel, to, subject, body, when))
                text = (f"{i18n.translate_text('Hello {0},', lang).replace('{0}', c['name'].split()[0])}\n\n{body}\n\n"
                        f"- Yo Delivery" if channel == "email" else f"Yo Delivery: {body}")
                jobs.append((cur.lastrowid, channel, to, subject, text))
        subs = [dict(r) for r in conn.execute("SELECT * FROM push_subscriptions WHERE user_id = ?", (user_id,))]
        if subs:
            vapid = self.vapid_keys(conn)
            for s in subs:
                jobs.append((("push", s["id"]), "push", {**s, "vapid": vapid, "url": url}, subject, body))
        if jobs:
            conn.after_commit.append(lambda: self.notifier.deliver(jobs, self._notice_result))

    def _notice_result(self, notice_id, status):
        with self.db() as conn:
            if isinstance(notice_id, tuple):                    # a push message
                if status == "Failed: gone":                    # the phone unsubscribed
                    conn.execute("DELETE FROM push_subscriptions WHERE id = ?", (notice_id[1],))
                return
            conn.execute("UPDATE notifications SET status = ? WHERE id = ?", (status[:150], notice_id))

    def notifications(self, user_id, limit=60):
        with self.db() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM notifications WHERE user_id = ? AND channel = 'site' ORDER BY id DESC LIMIT ?",
                (user_id, limit))]

    def unseen_count(self, user_id):
        with self.db() as conn:
            return conn.execute("SELECT COUNT(*) FROM notifications WHERE user_id = ? AND channel = 'site' AND seen = 0",
                                (user_id,)).fetchone()[0]

    def mark_seen(self, user_id):
        with self.db() as conn:
            conn.execute("UPDATE notifications SET seen = 1 WHERE user_id = ? AND seen = 0", (user_id,))

    def set_notify_prefs(self, user_id, email_on, sms_on):
        with self.db() as conn:
            conn.execute("UPDATE users SET notify_email = ?, notify_sms = ? WHERE id = ?",
                         (1 if email_on else 0, 1 if sms_on else 0, user_id))

    # ===== order status messages =====

    def sync_order_updates(self):
        """Send one message for every order whose status moved on since the last message. Returns how many."""
        since = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d %H:%M:%S")
        with self.db() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM orders WHERE cancelled_at = '' AND payment_status IN ('Paid', 'COD') "
                "AND notified_step < 4 AND placed_at >= ?", (since,))]
        sent = 0
        for o in rows:
            step = self._order_step(o)
            if step <= o["notified_step"]:
                continue
            with self.db() as conn:
                if conn.execute("UPDATE orders SET notified_step = ? WHERE id = ? AND notified_step = ?",
                                (step, o["id"], o["notified_step"])).rowcount == 0:
                    continue                                   # another process sent it
                self.notify(conn, o["user_id"], *self.step_message(o, step), order_number=o["number"],
                            url=f"/orders/{o['number']}")
                if step == 4:                                   # delivered: 1 Yo Rewards point per ₹100
                    pts = o["total"] // 10000
                    if pts > 0:
                        self.add_points(conn, o["user_id"], pts, "Order delivered", o["number"], once=True)
                sent += 1
        return sent

    def step_message(self, o, step):
        subject, body = STEP_MESSAGES[step]
        lang = o.get("lang") or "en"
        values = {0: (o["number"], world.format_money(o["total_local"] or o["total"], o["currency"], lang),
                      world.format_when(o["expected"], with_time=False, lang=lang)),
                  1: (o["number"],), 2: (o["number"], o["courier"], o["awb"]), 3: (o["number"],),
                  4: (o["number"], str(self.setting("return_days")))}[step]
        return _fill(subject, (o["number"],)), _fill(body, values)

    # ===== password reset by email =====

    def start_password_reset(self, email, base_url):
        """Email a one-time link (valid RESET_MINUTES). Returns True if an email was sent.
        The answer to the visitor is always the same, so nobody can find out which emails have accounts."""
        email = str(email or "").strip().lower()
        with self.db() as conn:
            u = conn.execute("SELECT id, name, email, lang, blocked FROM users WHERE email = ?", (email,)).fetchone()
        from store import is_example_email
        if u is None or u["blocked"] or is_example_email(email) or not (self.notifier and self.notifier.email_provider()):
            return False
        token = secrets.token_urlsafe(32)
        expires = (datetime.now() + timedelta(minutes=RESET_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
        with self.db() as conn:
            conn.execute("DELETE FROM password_resets WHERE user_id = ?", (u["id"],))
            conn.execute("INSERT INTO password_resets (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                         (hashlib.sha256(token.encode()).hexdigest(), u["id"], expires))
        lang = u["lang"] or "en"
        T = lambda t: i18n.translate_text(t, lang)                          # noqa: E731
        text = (T("Hello {0},").replace("{0}", u["name"].split()[0]) + "\n\n" +
                T("Someone asked to reset the password of your Yo Delivery account. Open this link within 30 minutes "
                  "to choose a new password:") + f"\n{base_url}reset/{token}\n\n" +
                T("If it was not you, you can ignore this email: your password stays the same."))
        self.notifier.send_email(u["email"], T("Reset your Yo Delivery password"), text)
        return True

    def reset_password(self, token, new_password):
        from store import check_password
        from werkzeug.security import generate_password_hash
        check_password(new_password, "New password")
        h = hashlib.sha256(str(token).encode()).hexdigest()
        with self.db() as conn:
            r = conn.execute("SELECT * FROM password_resets WHERE token_hash = ?", (h,)).fetchone()
            if r is None or r["used"] or r["expires_at"] < now():
                raise ShopError("This link has expired or was already used. Please ask for a new one.")
            conn.execute("UPDATE password_resets SET used = 1 WHERE token_hash = ?", (h,))
            conn.execute("UPDATE users SET password = ? WHERE id = ?", (generate_password_hash(new_password), r["user_id"]))
            return r["user_id"]

    def reset_token_ok(self, token):
        h = hashlib.sha256(str(token).encode()).hexdigest()
        with self.db() as conn:
            r = conn.execute("SELECT used, expires_at FROM password_resets WHERE token_hash = ?", (h,)).fetchone()
        return bool(r and not r["used"] and r["expires_at"] >= now())

    # ===== two-factor login (TOTP) =====

    def enable_2fa(self, user_id, secret, code):
        step = otp.verify(secret, code)
        if step is None:
            raise ShopError("The code is not correct. Check the time on your phone and type the newest code.")
        with self.db() as conn:
            conn.execute("UPDATE users SET totp_secret = ?, totp_enabled = 1, totp_last_step = ? WHERE id = ?",
                         (secret, step, user_id))

    def verify_2fa(self, user_id, code):
        with self.db() as conn:
            r = conn.execute("SELECT totp_secret, totp_last_step FROM users WHERE id = ?", (user_id,)).fetchone()
        step = otp.verify(r["totp_secret"], code, r["totp_last_step"]) if r else None
        if step is None:
            raise ShopError("Wrong or expired code. Type the 6-digit code shown in your authenticator app.")
        with self.db() as conn:
            conn.execute("UPDATE users SET totp_last_step = ? WHERE id = ?", (step, user_id))

    def disable_2fa(self, user_id, password, code):
        from werkzeug.security import check_password_hash
        with self.db() as conn:
            r = conn.execute("SELECT password FROM users WHERE id = ?", (user_id,)).fetchone()
        if not check_password_hash(r["password"], password or ""):
            raise ShopError("Current password is wrong.")
        self.verify_2fa(user_id, code)
        self.reset_2fa(user_id)

    def reset_2fa(self, user_id):
        with self.db() as conn:
            conn.execute("UPDATE users SET totp_secret = '', totp_enabled = 0, totp_last_step = 0 WHERE id = ?",
                         (user_id,))

    # ===== phones (Web Push) =====

    def vapid_keys(self, conn=None):
        def read(c):
            rows = dict(c.execute("SELECT key, value FROM settings WHERE key IN ('vapid_private', 'vapid_public')")
                        .fetchall())
            return rows if len(rows) == 2 else None
        if conn is not None and read(conn):
            got = read(conn)
            return {"private": got["vapid_private"], "public": got["vapid_public"]}
        with self.db() as c:
            got = read(c)
            if not got:                                # the first process wins; the others read its keys
                keys = push.new_vapid_keys()
                c.execute("INSERT INTO settings (key, value) VALUES ('vapid_private', ?) ON CONFLICT (key) DO NOTHING",
                          (keys["private"],))
                c.execute("INSERT INTO settings (key, value) VALUES ('vapid_public', ?) ON CONFLICT (key) DO NOTHING",
                          (keys["public"],))
                got = read(c)
        return {"private": got["vapid_private"], "public": got["vapid_public"]}

    def add_push_subscription(self, user_id, sub, device=""):
        try:
            endpoint = str(sub["endpoint"])
            p256dh, auth = str(sub["keys"]["p256dh"]), str(sub["keys"]["auth"])
            if not endpoint.startswith("https://") or len(push.unb64u(p256dh)) != 65 or len(push.unb64u(auth)) != 16:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise ShopError("This browser sent an invalid subscription.")
        with self.db() as conn:
            conn.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))
            conn.execute("INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth, device, created_at) "
                         "VALUES (?, ?, ?, ?, ?, ?)", (user_id, endpoint, p256dh, auth, str(device)[:80], now()))

    def push_subscriptions(self, user_id):
        with self.db() as conn:
            return [dict(r) for r in conn.execute("SELECT id, device, created_at FROM push_subscriptions "
                                                  "WHERE user_id = ? ORDER BY id DESC", (user_id,))]

    def remove_push_subscription(self, user_id, sub_id):
        with self.db() as conn:
            conn.execute("DELETE FROM push_subscriptions WHERE id = ? AND user_id = ?", (sub_id, user_id))


def _fill(text, values):
    for i, v in enumerate(values):
        text = text.replace("{" + str(i) + "}", str(v))
    return text
