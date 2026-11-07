"""
demo_v4.py - the demo content of version 4 (Yo Tickets), added once to a new database or to a database of
an earlier version (the same "claim a settings key" rule as demo_v3.py).

    * a conductor account of Saffron Roadways:  conductor@example.com / Conductor@1234
    * tickets of the demo customers: trips already made (with Yo Rewards points), a cancelled ticket and
      upcoming bus, train and flight tickets
    * two blog posts about Yo Tickets (English and Russian)
All people are fictional and use example.com addresses: they are never emailed or texted.
"""

from datetime import datetime, timedelta

import tickets as TK
from core import ShopError

CONDUCTOR = ("Gurpreet Kaur", "conductor@example.com", "9812000020", "Conductor@1234")

# (email, mode, from, to, days from today, passengers, payment, finally)  finally: done / cancel / '' (upcoming)
TRIPS = [
    ("demo@example.com", "bus", "Ambala Cantt", "New Delhi", -21, [("Ravi Kumar", 31, "M")], "wallet", "done"),
    ("demo@example.com", "flight", "Chandigarh", "Mumbai", -12, [("Ravi Kumar", 31, "M"), ("Kavya Kumar", 29, "F")],
     "card", "done"),
    ("demo@example.com", "train", "Kurukshetra", "New Delhi", -5, [("Ravi Kumar", 31, "M")], "wallet", "cancel"),
    ("demo@example.com", "bus", "Ambala Cantt", "New Delhi", 1, [("Ravi Kumar", 31, "M")], "wallet", ""),
    ("demo@example.com", "train", "Chandigarh", "Mumbai", 9, [("Ravi Kumar", 31, "M"), ("Kavya Kumar", 29, "F")],
     "wallet", ""),
    ("demo@example.com", "flight", "New Delhi", "Dubai", 24, [("Ravi Kumar", 31, "M")], "card", ""),
    ("priya.sharma@example.com", "bus", "Ambala City", "Chandigarh", -9, [("Priya Sharma", 27, "F")], "wallet", "done"),
    ("arjun.mehta@example.com", "train", "Chandigarh", "New Delhi", 3, [("Arjun Mehta", 35, "M")], "card", ""),
    ("anna.smirnova@example.com", "flight", "New Delhi", "Moscow", 16, [("Anna Smirnova", 34, "F")], "card", ""),
    ("amani.uwase@example.com", "flight", "Mumbai", "Kigali", 30, [("Amani Uwase", 26, "F")], "card", ""),
]

POSTS = [
    {"slug": "yo-tickets-bus-train-flight", "category": "news", "cover": "images/photos/tickets.jpg", "days": 0,
     "title": "Yo Tickets: bus, train and flight tickets with your own seat",
     "summary": "Yo Delivery 4 adds tickets: search, choose your seat on the map, pay with Yo Pay and show a QR e-ticket.",
     "tags": "Yo Tickets, travel, news",
     "body": """Yo Delivery started with parcels and shopping. With **Yo Tickets** you can now travel with us too: buses in the Ambala Division and across North India, trains to every big city and flights in India and abroad.

## How it works
1. **Search** - choose bus, train or flight, the two cities and the date. You see every trip with its time, the seats left and the price.
2. **Choose your seat** - a window seat on the bus, a lower berth on the train or an aisle seat on the plane. Taken seats are grey.
3. **Pay in 10 minutes** - your seats are kept for you while you pay with Yo Pay or a card. Bigger amounts can be paid by EMI.
4. **Show the QR code** - the e-ticket has a QR code. The conductor scans it and you are on board.

> No airport in your town? Search from Ambala and we show the flights from Chandigarh, the nearest airport.

## Fair rules
Bus and flight prices go up when a trip fills up, so booking early is cheaper. You can cancel until the trip leaves; the refund depends on how early you cancel, and if the company cancels a trip you get **all** your money back at once. After every trip you get Yo Rewards points.

Try it: [Ambala Cantt to New Delhi by bus](/tickets/bus/ambala-cantt-to-new-delhi) or [New Delhi to Dubai by plane](/tickets/flight/new-delhi-to-dubai)."""},
    {"slug": "yo-tickets-bilety-na-avtobus-poezd-samolet", "category": "news", "cover": "images/photos/tickets.jpg",
     "days": 0, "lang": "ru",
     "title": "Yo Tickets: билеты на автобус, поезд и самолёт",
     "summary": "Выберите место на схеме, оплатите через Yo Pay и покажите QR-код электронного билета.",
     "tags": "Yo Tickets, путешествия",
     "body": """В Yo Delivery 4 появились **билеты**: автобусы по Харьяне и северу Индии, поезда во все большие города и самолёты по Индии и за границу.

## Как это работает
1. **Поиск** - выберите автобус, поезд или самолёт, города и дату. Вы видите время, свободные места и цену.
2. **Место** - у окна в автобусе, нижняя полка в поезде или место у прохода в самолёте.
3. **Оплата за 10 минут** - места держатся для вас, пока вы платите через Yo Pay или картой.
4. **QR-код** - кондуктор сканирует электронный билет, и вы в пути.

> Летите в Москву? Рейсы из Нью-Дели есть каждый день.

Если компания отменяет рейс, вы сразу получаете **все** деньги обратно. После каждой поездки начисляются баллы Yo Rewards.

Попробуйте: [Нью-Дели - Москва](/tickets/flight/new-delhi-to-moscow)."""},
]


def ensure_v4(store):
    """Add the version 4 demo content once (new database or a database of an earlier version)."""
    with store.db() as conn:
        claimed = conn.execute("INSERT INTO settings (key, value) VALUES ('v4_demo', ?) ON CONFLICT (key) DO NOTHING",
                               (TK._fmt(datetime.now()),)).rowcount
    if not claimed:
        return False
    conductor = _conductor(store)
    for row in TRIPS:
        try:
            _trip(store, conductor, *row)
        except ShopError:                       # e.g. a route without a trip on that day: the demo goes on
            continue
    store.tickets_tick()                         # trips in the past become "Completed" with their points
    _top_up(store, "demo@example.com", 5_000_00)   # a visitor can still try Yo Pay with the demo account
    _posts(store)
    return True


def _top_up(store, email, at_least):
    uid = _uid(store, email)
    if not uid:
        return
    with store.db() as conn:
        bal = conn.execute("SELECT wallet FROM users WHERE id = ?", (uid,)).fetchone()[0]
        if bal < at_least:
            store.wallet_move(conn, uid, at_least - bal, "topup", "DEMO", "Money added by card")


def _uid(store, email):
    with store.db() as conn:
        r = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    return r[0] if r else None


def _conductor(store):
    name, email, mobile, password = CONDUCTOR
    uid = _uid(store, email) or store.register(name, email, mobile, password, wallet=0)
    if store.staff_of(uid) is None:
        store.add_staff(email, "SRW")
    return uid


def _service(store, mode, a, b, operator=None):
    a, b, _ = TK.check_route(mode, a, b)
    with store.db() as conn:
        services = store._services(conn, mode, a, b)
    if operator:
        services = [s for s in services if s["operator_code"] == operator] or services
    return services


def _trip(store, conductor, email, mode, a, b, days, people, payment, end):
    uid = _uid(store, email)
    if not uid:
        return None
    services = _service(store, mode, a, b, operator="SRW" if mode == "bus" else None)   # the conductor's company
    first = TK.place(services[0]["from_city"])
    local_day = (TK.to_local(TK.utc_now(), first["tz"]).date() + timedelta(days=days))
    service = next((s for s in services if s["days"][local_day.weekday()] == "1"), None)
    if service is None:                          # a train that does not run that day: the next day it runs
        for extra in range(1, 7):
            d = local_day + timedelta(days=extra)
            service = next((s for s in services if s["days"][d.weekday()] == "1"), None)
            if service:
                local_day = d
                break
    with store.db() as conn:
        trip = store._trip(conn, service, local_day.isoformat())
    depart = TK._dt(trip["depart_utc"])
    booked_at = min(TK.utc_now() - timedelta(minutes=5), depart - timedelta(days=4, hours=3))
    _, secs = store.seat_map(trip["id"], at=booked_at)
    cls = service["classes"][0] if mode != "train" else service["classes"][min(1, len(service["classes"]) - 1)]
    free = [s["code"] for sec in secs if sec["cls"] == cls for row in sec["rows"] for s in row
            if s and not s["taken"] and not s["fee"]]
    seats = free[len(free) // 3: len(free) // 3 + len(people)]
    pnr = store.hold_seats(uid, trip["id"], seats, at=booked_at)
    form = {"contact_mobile": "9812000000", "payment": payment}
    if payment == "card":
        form.update(card="4242 4242 4242 4242", expiry="12/30", cvc="123", card_name=people[0][0])
    else:
        need = store.get_booking(pnr)["total"]
        with store.db() as conn:                # the demo wallet always has enough for its own tickets
            bal = conn.execute("SELECT wallet FROM users WHERE id = ?", (uid,)).fetchone()[0]
            if bal < need:
                store.wallet_move(conn, uid, need - bal + 50_000, "topup", "DEMO", "Money added by card",
                                  when=TK._fmt(booked_at - timedelta(minutes=2)))
    for i, (name, age, gender) in enumerate(people):
        form.update({f"name_{i}": name, f"age_{i}": age, f"gender_{i}": gender})
    store.pay_booking(uid, pnr, form, at=booked_at + timedelta(minutes=3))
    if end == "cancel":
        store.cancel_booking(uid, pnr, at=depart - timedelta(days=2, hours=5))
    elif end == "done" and mode == "bus" and store.staff_of(conductor)["operator_code"] == service["operator_code"]:
        store.board_ticket(conductor, store.ticket_token(pnr), at=depart - timedelta(minutes=20))
    return pnr


def _posts(store):
    admin = store.get_user(_uid(store, "admin@example.com") or 1)
    with store.db() as conn:
        have = {r[0] for r in conn.execute("SELECT slug FROM posts")}
    for p in POSTS:
        if p["slug"] in have:
            continue
        when = TK._fmt(datetime.now() - timedelta(days=p["days"], hours=1))
        store.save_post(dict(p, status="Published", lang=p.get("lang", "en"), when=when), admin["id"], admin["name"])
