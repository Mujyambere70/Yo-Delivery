"""
demo_v8.py - the demo content of version 8, added once to a new database or to a database of an earlier version
(the same "claim a settings key" rule as demo_v3.py ... demo_v7.py). All people, firms and numbers are fictional.

    * staff news from three managers, the network of the Ambala hub (a documentation address range) and one more
      public holiday of India added by a manager
    * the Returns page also in Russian and French (a visitor who reads in these languages sees the translation)
    * the webhook of the demo courier partner: its address is a made-up one, so messages are "Test (not sent)"
    * GPS points of demo deliveries in Ambala Cantt (marked by the word "demo" in this file only) and the speed profile
    * some category tax rates checked by the finance worker, with the day they are valid from
    * a message of the timetable company and the place of one bus (GTFS-Realtime), counters of help articles,
      an e-mail of the demo customer that became a question
"""

import random
from datetime import datetime, timedelta

import demo_data
import v8extra
from core import ShopError, now

NEWS = [
    ("anita.verma@example.com", "India office", "New: clock in with the office code",
     "Phones without GPS can now clock in with the six digits on the screen at the entrance of every hub. "
     "The code changes every minute.", True),
    ("rohit.malhotra@example.com", "Ambala hub", "Peak week: please check your shifts",
     "Many parcels are forecast for next week. Open 'My work' and look at your shifts. Ask me if a day does not fit.", False),
    ("claudine.uwimana@example.com", "Rwanda office", "Public holidays are in the calendar",
     "The holidays of next year are saved. A public holiday inside your time off is not counted as a holiday day.", False),
]
RETURNS_RU = ("Возврат и возврат денег", """Вы можете вернуть доставленный заказ в течение {return_days} дней: откройте заказ и нажмите «Вернуть этот заказ», затем выберите причину.

- Курьер заберёт посылку по вашему адресу.
- После проверки деньги возвращаются тем же способом, которым вы платили: оплата картой - на карту, оплата из кошелька и UPI - в кошелёк Yo Pay.
- На каждый возврат оформляется налоговая кредит-нота.

Вопросы? Звоните {phone} ({hours}) или пишите на {email}.""")
RETURNS_FR = ("Retours et remboursements", """Vous pouvez retourner une commande livrée dans un délai de {return_days} jours : ouvrez la commande, appuyez sur « Retourner cette commande », puis choisissez la raison.

- Un coursier récupère le colis à votre adresse.
- Après notre contrôle, l'argent revient par le moyen de paiement utilisé : la carte pour un paiement par carte, le portefeuille Yo Pay pour un paiement par portefeuille ou UPI.
- Une note de crédit fiscale est établie pour chaque remboursement.

Des questions ? Appelez le {phone} ({hours}) ou écrivez à {email}.""")
WEBHOOK = "https://hooks.highland-express.example/yo"
OFFICE_NET = ("Ambala hub", "203.0.113.0/24")          # TEST-NET-3: an address range kept for documentation
# hour of the day -> usual speed in the demo town (slow in the morning and in the evening)
DEMO_SPEED = {8: 19, 9: 17, 10: 22, 11: 25, 12: 26, 13: 27, 14: 26, 15: 24, 16: 21, 17: 16, 18: 14, 19: 18, 20: 24}


def ensure_v8(store):
    with store.db() as conn:
        claimed = conn.execute("INSERT INTO settings (key, value) VALUES ('v8_demo', ?) ON CONFLICT (key) DO NOTHING",
                               (now(),)).rowcount
        if not claimed:
            return False
    for part in (_news, _pages, _office, _holiday, _webhook, _speed, _rates, _travel, _help):
        try:
            part(store)
        except (ShopError, LookupError, TypeError, ValueError):     # the demo goes on without this part
            pass
    return True


def _admin(store):
    with store.db() as conn:
        r = conn.execute("SELECT id FROM users WHERE is_admin = 1 ORDER BY id LIMIT 1").fetchone()
    return r[0] if r else None


def _uid(store, email):
    u = store.find_user(email)
    return u["id"] if u else None


def _office_ids(store):
    with store.db() as conn:
        return {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM offices")}


def _news(store):
    offices = _office_ids(store)
    for email, office, title, body, pinned in NEWS:
        uid = _uid(store, email)
        if uid and office in offices:
            store.post_staff_news(uid, title, body, offices[office], pinned)


def _pages(store):
    admin = _admin(store)
    for lang, (title, body) in (("ru", RETURNS_RU), ("fr", RETURNS_FR)):
        store.save_site_page(admin, "returns", lang, title, body)


def _office(store):
    offices = _office_ids(store)
    if OFFICE_NET[0] in offices:
        store.set_office_network(_admin(store), offices[OFFICE_NET[0]], OFFICE_NET[1])


def _holiday(store):
    """A manager adds a holiday of his country that is not one of the three national days (checked on the web on
    13 November 2026: Guru Nanak Jayanti is on 24 November 2026)."""
    boss = _uid(store, "rohit.malhotra@example.com")
    if boss:
        store.save_holiday(boss, "IN", "2026-11-24", "Guru Nanak Jayanti")


def _webhook(store):
    admin = _admin(store)
    partners = store.partners()
    if not partners:
        return
    pid = partners[0]["id"]
    store.set_partner_webhook(admin, pid, WEBHOOK)
    store.ping_partner_webhook(admin, pid)
    with store.db() as conn:
        s = conn.execute("SELECT * FROM shipments WHERE partner_id = ? ORDER BY id LIMIT 1", (pid,)).fetchone()
    if s is not None:
        store.queue_webhook(pid, "shipment.assigned", s["number"], store._parcel_data(s))
    for d in store.webhook_deliveries(pid):                          # made-up address: marked, never called
        store.send_webhook(d["id"])
    with store.db() as conn:
        conn.execute("UPDATE jobs SET status = 'done', done_at = ? WHERE kind = 'webhook' AND status = 'waiting'", (now(),))


def _speed(store):
    """GPS points of 12 demo deliveries a day for 6 days, about every 45 seconds, at the usual speed of the hour."""
    rng = random.Random(8)
    with store.db() as conn:
        ships = [r["id"] for r in conn.execute("SELECT id FROM shipments WHERE to_city = 'Ambala Cantt' ORDER BY id LIMIT 6")]
        if not ships:
            return
        base = datetime.now().replace(minute=0, second=0, microsecond=0)
        rows = []
        for day in range(1, 7):
            for hour, kmh in DEMO_SPEED.items():
                sid = ships[(day + hour) % len(ships)]
                t = (base - timedelta(days=day)).replace(hour=hour, minute=rng.randrange(0, 20))
                lat, lng = 30.3375 + rng.uniform(-0.01, 0.01), 76.8562 + rng.uniform(-0.01, 0.01)
                for _ in range(14):
                    rows.append((sid, round(lat, 5), round(lng, 5), t.strftime("%Y-%m-%d %H:%M:%S")))
                    speed = max(6.0, kmh + rng.uniform(-3, 3))
                    metres = speed / 1.3 * 1000 / 3600 * 45          # straight-line metres in 45 seconds
                    lat += metres / 111_000 * rng.choice((1, 1, -1)) * 0.7
                    lng += metres / 96_000 * 0.7
                    t += timedelta(seconds=45)
                rows.append((sid, round(lat, 5), round(lng, 5), (t + timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")))
        rows.sort(key=lambda r: (r[0], r[3]))
        conn.executemany("INSERT INTO courier_track (shipment_id, lat, lng, at) VALUES (?, ?, ?, ?)", rows)
    store.build_speed_profile()


def _rates(store):
    kavya = _uid(store, "kavya.iyer@example.com")                    # finance: has the ability tax.manage
    if not kavya:
        return
    for r in store.category_rates("IN"):
        if r["category"] in ("books", "electronics", "watches"):
            store.mark_rate_checked(kavya, "category", r["id"], "2025-09-22", "", "Demo check: compared with the example table of the project")


def _travel(store):
    feeds = store.gtfs_feeds()
    if not feeds:
        return
    feed = feeds[0]
    with store.db() as conn:
        svc = conn.execute("SELECT code FROM ticket_services WHERE code LIKE ? ORDER BY id LIMIT 1", (f"GT{feed['id']}-%",)).fetchone()
    if svc is None:
        return
    trip_id = svc["code"].split("-", 1)[1].rsplit("-", 2)[0]
    store.apply_gtfs_rt(feed["id"], v8extra.sample_live_feed(
        vehicles=[(trip_id, -1.7052, 29.9351, "Bus RAD 412 K")],
        alerts=[("demo-alert-1", trip_id, "Road works near Rulindo", "The bus can be up to 15 minutes late this week.", 3)]))


def _help(store):
    rng = random.Random(88)
    with store.db() as conn:
        for a in conn.execute("SELECT id FROM kb_articles ORDER BY id").fetchall():
            opened = rng.randrange(8, 240)
            good = rng.randrange(2, max(3, opened // 6))
            conn.execute("UPDATE kb_articles SET opened = ?, helpful = ?, not_helpful = ? WHERE id = ?",
                         (opened, good, rng.randrange(0, max(1, good // 4)), a["id"]))
    store.inbound_email("<demo-mail-1@mail.example>", demo_data.DEMO_EMAIL, "Can I change the address of my order?",
                        "Hello, I placed an order this morning and I want it delivered to my office. Is that possible?")
