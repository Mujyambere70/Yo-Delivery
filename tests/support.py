"""Shared test helpers.

The tests run on SQLite by default. To run the same tests on PostgreSQL:
    TEST_DATABASE_URL=postgresql://user@host:5432/dbname python -m unittest discover tests
(the database is emptied before every test - use a database only for tests!)
"""

import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

PG_URL = os.environ.get("TEST_DATABASE_URL", "")
DRIVER = None
if PG_URL:
    try:
        import psycopg as DRIVER                      # the real driver, when installed
    except ImportError:
        import pgmini as DRIVER                       # small built-in client for tests

# tests never call real services: no exchange-rate download, no email/SMS keys, no Stripe key
TEST_ENV = {"FX_ON_SEED": "0", "FX_AUTO": "0", "YO_ROUTING": "0"}
os.environ.setdefault("YO_ROUTING", "0")            # also for code that reads the environment directly


def target(path=None):
    """Where a test database lives: a new SQLite file, or an emptied PostgreSQL database."""
    if not PG_URL:
        return path or os.path.join(tempfile.mkdtemp(), "test.db")
    conn = DRIVER.connect(PG_URL)
    cur = conn.cursor()
    cur.execute("DROP SCHEMA public CASCADE")
    cur.execute("CREATE SCHEMA public")
    conn.commit()
    conn.close()
    return PG_URL


def make_app(seed=True, env=None, notifier=None):
    from app import create_app
    app = create_app(target(), seed=seed, notifier=notifier, driver=DRIVER, env=dict(TEST_ENV, **(env or {})),
                     housekeeping=False)
    app.config["TESTING"] = True
    return app


def make_store(seed=False, env=None, notifier=None):
    from store import Store
    import demo_data
    s = Store(target(), driver=DRIVER, notifier=notifier, env=dict(TEST_ENV, **(env or {})))
    if seed:
        demo_data.seed(s)
    return s


def token(client, url="/login"):
    html = client.get(url).get_data(as_text=True)
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def login(client, who, password):
    return client.post("/login", data={"csrf_token": token(client), "login": who, "password": password})


def fake_notifier(email=True, sms=True):
    from notify import Notifier

    class Recorder(Notifier):
        def __init__(self):
            env = {}
            if email:
                env.update(BREVO_API_KEY="test-key", ALERT_EMAIL_FROM="shop@yodelivery.example")
            if sms:
                env.update(TWILIO_ACCOUNT_SID="ACtest", TWILIO_AUTH_TOKEN="t", TWILIO_FROM="+15005550006")
            super().__init__(env=env)
            self.sent, self.background = [], False

        def send_email(self, to, subject, text, attachment=None):
            self.sent.append(("email", to, subject, text))

        def send_sms(self, to, text):
            self.sent.append(("sms", to, text))

    return Recorder()
