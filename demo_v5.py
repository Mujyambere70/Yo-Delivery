"""
demo_v5.py - the demo content of version 5 (the whole world), added once to a new database or to a database of
an earlier version (the same "claim a settings key" rule as demo_v3.py and demo_v4.py).

    * demo robot couriers appear in any town or village where a parcel is booked (setting world_robots = 1)
    * the daily quiz in French, Spanish, Arabic, Chinese and Kinyarwanda
    * product texts of the demo seller in Russian, French and Kinyarwanda
    * a conversation between a customer and the demo seller, and one message held for the moderator
    * a demo customer in Rwanda (a village address in Gakenke district, prices in Rwandan francs)
    * a full train class (waiting list), and two blog posts about version 5 (English and Russian)
All people are fictional and use example.com addresses: they are never emailed or texted.
"""

from datetime import datetime, timedelta

import tickets as TK
from core import ShopError
from quiz_world import ensure_quiz_languages

SELLER_EMAIL = "seller@example.com"
RW_CUSTOMER = ("Amani Uwase", "amani.uwase@example.com", "+250 788 555 014")

TEXTS = {
    "homeease-cooker": {
        "ru": ("Скороварка HomeEase 5 л из нержавеющей стали", "Подходит для газа и индукции\nДва клапана безопасности\nГарантия 5 лет"),
        "fr": ("Autocuiseur HomeEase 5 L en inox", "Pour gaz et induction\nDeux soupapes de sécurité\nGarantie 5 ans"),
        "rw": ("Inkono yo guteka vuba HomeEase litiro 5", "Ikora ku gaze no ku mashanyarazi\nIfite uburyo bubiri bwo kwirinda impanuka\nGaranti y'imyaka 5"),
    },
    "homeease-kettle": {
        "ru": ("Электрочайник HomeEase 1,7 л", "Закипает за 4 минуты\nАвтоматически выключается\nЗащита от включения без воды"),
        "fr": ("Bouilloire électrique HomeEase 1,7 L", "Bout en 4 minutes\nArrêt automatique\nProtection sans eau"),
        "rw": ("Isafuriya y'amashanyarazi HomeEase litiro 1,7", "Ibira mu minota 4\nYizimya ubwayo\nNtikora idafite amazi"),
    },
}

POSTS = [
    {"slug": "yo-delivery-5-whole-world", "category": "news", "cover": "images/photos/courier.jpg", "days": 0,
     "title": "Yo Delivery 5: every town and village of the world",
     "summary": "485,665 places, 154 currencies, couriers and tickets everywhere, chat with sellers and more.",
     "tags": "news, world, Rwanda, Russia, India",
     "body": """Yo Delivery 5 is for the whole world.

* **Every town and village.** Type the first letters: all towns with 1,000+ people, every village of Rwanda (more than 14,000, with the sector and the cell), every Indian post office with its PIN code, and every settlement of Russia (more than 150,000 villages) - also in Russian letters.
* **Your own money.** Prices in 154 currencies, chosen from your country. Times are shown in your own time zone.
* **Courier anywhere.** Pickup and delivery in any town or village, a real street map (OpenStreetMap) and the road distance.
* **Tickets everywhere.** Buses in every country, trains where there are trains, flights between 3,214 airports - with one change when there is no direct flight, round trips and a waiting list for full trains.
* **Ask the seller.** Chat with sellers, with moderation for your safety.
* **Your data.** Download everything we keep about you, or delete your account.

Try it: [send a parcel from Kigali to Mugunga](/courier/book?from_country=RW&from_city=Kigali&from_place=Kigali)."""},
    {"slug": "yo-delivery-5-ves-mir", "category": "news", "cover": "images/photos/tickets.jpg", "days": 0, "lang": "ru",
     "title": "Yo Delivery 5: все города и сёла мира",
     "summary": "485 665 мест, 154 валюты, курьер и билеты по всему миру, чат с продавцами.",
     "tags": "новости, мир, Россия, Руанда, Индия",
     "body": """Yo Delivery 5 работает для всего мира.

* **Все города и сёла.** Начните писать название: все города мира с населением от 1000 человек, все деревни Руанды, все почтовые отделения Индии с PIN-кодом и все населённые пункты России (больше 150 000 сёл и деревень) - можно писать по-русски.
* **Своя валюта.** Цены в 154 валютах, время - в вашем часовом поясе.
* **Курьер везде.** Забор и доставка в любом городе или селе, настоящая карта улиц (OpenStreetMap).
* **Билеты по всему миру.** Рейсы с пересадкой, билеты туда и обратно, лист ожидания на поезда.
* **Чат с продавцом** с модерацией и **мои данные**: скачать всё или удалить аккаунт.

Попробуйте: [Красноярск - Москва](/tickets/search?mode=train&from=Krasnoyarsk&to=Moscow)."""},
]


def ensure_v5(store):
    """Add the version 5 demo content once (new database or a database of an earlier version)."""
    with store.db() as conn:
        claimed = conn.execute("INSERT INTO settings (key, value) VALUES ('v5_demo', ?) ON CONFLICT (key) DO NOTHING",
                               (TK._fmt(datetime.now()),)).rowcount
        if not claimed:
            return False
        conn.execute("INSERT INTO settings (key, value) VALUES ('world_robots', '1') "
                     "ON CONFLICT (key) DO UPDATE SET value = '1'")
    ensure_quiz_languages(store)
    for step in (_texts, _chat, _rwanda_customer, _full_train, _posts):
        try:
            step(store)
        except (ShopError, LookupError, TypeError, ValueError):     # the demo goes on without this part
            continue
    try:
        store.learn_recommendations(force=True)
    except Exception:
        pass
    return True


def _uid(store, email):
    with store.db() as conn:
        r = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    return r[0] if r else None


def _texts(store):
    shop = store.seller_of(_uid(store, SELLER_EMAIL)) if _uid(store, SELLER_EMAIL) else None
    for slug, langs in TEXTS.items():
        p = store.get_product(slug=slug)
        store.save_product_texts(p.id, langs, seller_id=shop["id"] if shop else None)


def _chat(store):
    customer = _uid(store, "demo@example.com")
    seller = _uid(store, SELLER_EMAIL)
    if not customer or not seller:
        return
    p = store.get_product(slug="homeease-cooker")
    t0 = datetime.now() - timedelta(days=1, hours=3)
    stamp = lambda m: (t0 + timedelta(minutes=m)).strftime("%Y-%m-%d %H:%M:%S")   # noqa: E731
    tid = store.start_chat(customer, p.id, text="Hello! Does this cooker work on an induction stove?", when=stamp(0))
    store.send_chat(seller, tid, "Hello Ravi! Yes, the base works on gas and induction. We send it the same day.",
                    when=stamp(14))
    store.send_chat(customer, tid, "Great, thank you. Can I call you on 98120 00099?", when=stamp(20))   # number hidden
    store.send_chat(seller, tid, "Please write here, it is safer for both of us. Pay me directly and I give 10% off.",
                    when=stamp(31))                          # held for the moderator (payment outside the shop)


def _rwanda_customer(store):
    name, email, mobile = RW_CUSTOMER
    uid = _uid(store, email)
    if not uid:
        return
    with store.db() as conn:
        conn.execute("UPDATE users SET country = 'RW', currency = 'RWF' WHERE id = ?", (uid,))
        has = conn.execute("SELECT 1 FROM addresses WHERE user_id = ? AND place_id = 'rw6833'", (uid,)).fetchone()
    if not has:
        store.add_address(uid, {"country": "RW", "name": name, "mobile": mobile, "line1": "House 7, near Mugunga market",
                                "city": "Mugunga", "place_id": "rw6833", "state_abroad": "Northern Province",
                                "kind": "Home"})


def _full_train(store):
    """One class of a train tomorrow is sold out (other channels), so visitors can try the waiting list."""
    day = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    r = store.search_trips("train", "Chandigarh", "New Delhi", day, 1)
    if not r["trips"]:
        return
    t = r["trips"][0]
    cls = t["classes"][-1]["cls"]
    _, secs = store.seat_map(t["trip_id"])
    codes = [x["code"] for sec in secs for row in sec["rows"] for x in row if x and not x["taken"] and x["cls"] == cls]
    with store.db() as conn:
        for code in codes:
            conn.execute("INSERT INTO ticket_seats (trip_id, seat, booking_id, created_at) VALUES (?, ?, 0, ?) "
                         "ON CONFLICT DO NOTHING", (t["trip_id"], code, TK._fmt(TK.utc_now())))


def _posts(store):
    admin = store.get_user(_uid(store, "admin@example.com") or 1)
    with store.db() as conn:
        have = {r[0] for r in conn.execute("SELECT slug FROM posts")}
    for p in POSTS:
        if p["slug"] in have:
            continue
        when = TK._fmt(datetime.now() - timedelta(days=p["days"], minutes=30))
        store.save_post(dict(p, status="Published", lang=p.get("lang", "en"), when=when), admin["id"], admin["name"])
