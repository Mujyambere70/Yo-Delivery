"""
demo_v9.py - the demo content of version 9, added once to a new database or to a database of an earlier version
(the same "claim a settings key" rule as demo_v3.py ... demo_v8.py). All people, firms and numbers are fictional.

    * three help articles, the first banner and the contact lines also in Russian and French
    * a draft of the Returns page that is published by itself in two days
    * a month of searches: some found nothing, some had a typing mistake that was corrected
    * one shift swap that a colleague accepted (it waits for the manager) and the first-days list of a new worker
    * stars for answered help-desk questions, a photo added to one question, the usual way to pay of the demo customer
    * a few sign-in tries that were stopped by the limit (from documentation network addresses)
    * one backup that was restored into an empty database to prove it works
"""

import io
import random
from datetime import date, datetime, timedelta

import demo_data
from core import ShopError, now

KB_TEXT = {
    "ru": [("Где мой заказ?", "Откройте «Ваши заказы» и нажмите на заказ: там видно каждый шаг доставки. "
                               "Номер для отслеживания есть и в письме об отправке. Вопросы? Звоните {phone} ({hours})."),
           ("Как вернуть товар?", "Откройте доставленный заказ и нажмите «Вернуть этот заказ» в течение {return_days} дней. "
                                  "Курьер заберёт посылку, а деньги вернутся тем же способом, которым вы платили."),
           ("Какие способы оплаты есть?", "Карта, UPI, кошелёк Yo Pay и оплата при получении (в Индии). "
                                          "Все платежи в этом демо-магазине работают в тестовом режиме.")],
    "fr": [("Où est ma commande ?", "Ouvrez « Vos commandes » et choisissez la commande : chaque étape de la livraison y est "
                                    "visible. Le numéro de suivi est aussi dans le message d'expédition."),
           ("Comment retourner un article ?", "Ouvrez la commande livrée et appuyez sur « Retourner cette commande » dans un "
                                              "délai de {return_days} jours. Un coursier récupère le colis."),
           ("Quels moyens de paiement ?", "Carte, UPI, portefeuille Yo Pay et paiement à la livraison (en Inde). "
                                          "Dans cette boutique de démonstration, tous les paiements sont en mode test.")],
}
SLIDE_TEXT = {"ru": {"label": "Новое", "title": "Покупайте, отправляйте и путешествуйте", "button": "Смотреть"},
              "fr": {"label": "Nouveau", "title": "Achetez, envoyez et voyagez", "button": "Voir"}}
CONTACT_TEXT = {"ru": {"tagline": "Покупки, посылки и билеты в одном аккаунте.", "hours": "Каждый день, 8:00-22:00 (время Индии)"},
                "fr": {"tagline": "Achats, colis et billets avec un seul compte.", "hours": "Tous les jours, 8 h - 22 h (heure de l'Inde)"}}
SEARCHES = [("telescope", 0), ("snowboard", 0), ("kurta xxl", 0), ("cricket helmet", 0), ("sheos", None),
            ("wach", None), ("refnd", None), ("earphones", None), ("track parcel", None), ("ludo board", 0)]
DRAFT = ("Returns and refunds", """You can return a delivered order within {return_days} days: open the order, press "Return this order" and choose the reason.

- A courier collects the parcel at your address.
- After our check the money goes back the way you paid: card payments to the card, wallet and UPI payments to the Yo Pay wallet.
- New in winter: a return of shoes can also be handed in at the Ambala hub.

Questions? Call {phone} ({hours}) or write to {email}.""")


def ensure_v9(store):
    with store.db() as conn:
        claimed = conn.execute("INSERT INTO settings (key, value) VALUES ('v9_demo', ?) ON CONFLICT (key) DO NOTHING",
                               (now(),)).rowcount
        if not claimed:
            return False
    for part in (_texts, _draft, _searches, _swap, _first_days, _help_desk, _customer, _limits, _backup):
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


def _texts(store):
    admin = _admin(store)
    with store.db() as conn:
        kb = [r["id"] for r in conn.execute("SELECT id FROM kb_articles ORDER BY sort_no, id LIMIT 3")]
        slide = conn.execute("SELECT id FROM site_slides ORDER BY sort_no, id LIMIT 1").fetchone()
    for lang, rows in KB_TEXT.items():
        for aid, (q, a) in zip(kb, rows):
            store.save_content_texts(admin, "kb", aid, lang, {"question": q, "answer": a})
        if slide is not None:
            store.save_content_texts(admin, "slide", slide["id"], lang, SLIDE_TEXT[lang])
        store.save_content_texts(admin, "contact", "site", lang, CONTACT_TEXT[lang])


def _draft(store):
    when = (datetime.now() + timedelta(days=2)).replace(hour=9, minute=0).strftime("%Y-%m-%d %H:%M")
    store.save_page_draft(_admin(store), "returns", "en", DRAFT[0], DRAFT[1], when)


def _searches(store):
    """A month of searches, as visitors would type them (the corrections are worked out by the shop itself)."""
    rng = random.Random(9)
    rows = []
    for q, hits in SEARCHES:
        for _ in range(rng.randrange(2, 9)):
            at = datetime.now() - timedelta(days=rng.randrange(0, 29), minutes=rng.randrange(0, 1440))
            r = store.find_everything(None, q, record=False) if hits is None else {"total": 0, "fixed": ""}
            rows.append((q, r["total"], r["fixed"], rng.choice((0, 1)), at.strftime("%Y-%m-%d %H:%M:%S")))
    with store.db() as conn:
        conn.executemany("INSERT INTO search_log (q, hits, fixed, signed_in, at) VALUES (?, ?, ?, ?, ?)", sorted(rows, key=lambda r: r[4]))


def _swap(store):
    """A worker (of the Ambala hub when it has two) gives a shift of next week to a colleague, who said yes (it waits for the manager)."""
    day = (date.today() + timedelta(days=7)).isoformat()
    with store.db() as conn:
        rows = conn.execute("SELECT s.id, s.user_id, s.office_id, o.name FROM staff s JOIN offices o ON o.id = s.office_id "
                            "WHERE s.status = 'Active' AND s.is_manager = 0 ORDER BY CASE WHEN o.name = 'Ambala hub' THEN 0 ELSE 1 END, "
                            "s.office_id, s.id").fetchall()
        by_office = {}
        for r in rows:
            by_office.setdefault(r["office_id"], []).append(r)
        mates = next((m for m in by_office.values() if len(m) >= 2), [])
        if len(mates) < 2:
            return
        a, b = mates[0], mates[1]
        cur = conn.execute("INSERT INTO staff_rota (staff_id, office_id, day, start_hm, end_hm, created_by, created_at) "
                           "VALUES (?, ?, ?, '09:00', '17:00', ?, ?)", (a["id"], a["office_id"], day, _admin(store), now()))
        rota_id = cur.lastrowid
    sid = store.offer_swap(a["user_id"], rota_id, b["id"])
    store.answer_swap(b["user_id"], sid, True)


def _first_days(store):
    """The newest worker ticked two of the four steps that the system cannot see."""
    with store.db() as conn:
        w = conn.execute("SELECT s.id, s.user_id FROM staff s WHERE s.status = 'Active' ORDER BY s.id DESC LIMIT 1").fetchone()
    if w is not None:
        for item in ("card", "tools"):
            store.tick_onboarding(w["user_id"], w["id"], item)


def _help_desk(store):
    rng = random.Random(99)
    with store.db() as conn:
        done = conn.execute("SELECT number, user_id FROM support_tickets WHERE status IN ('Answered', 'Solved', 'Closed') "
                            "ORDER BY id").fetchall()
        open_one = conn.execute("SELECT number, user_id FROM support_tickets WHERE status = 'Open' ORDER BY id LIMIT 1").fetchone()
    notes = ["", "", "Fast and clear, thank you.", "", "The answer came late.", ""]
    for t in done:
        stars = rng.choice((5, 5, 4, 4, 3, 2))
        store.rate_support(t["user_id"], t["number"], stars, rng.choice(notes) if stars < 5 else "")
    t = open_one or (done[0] if done else None)
    if t is not None:
        store.add_support_file(t["user_id"], t["number"], "parcel-box.png", "image/png", _box_picture())


def _box_picture():
    """A small drawing of a damaged box (made here, so the demo needs no photo file)."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (360, 240), (241, 243, 246))
    d = ImageDraw.Draw(img)
    d.polygon([(70, 90), (180, 50), (290, 90), (180, 130)], fill=(214, 168, 112))
    d.polygon([(70, 90), (180, 130), (180, 220), (70, 180)], fill=(190, 140, 85))
    d.polygon([(180, 130), (290, 90), (290, 180), (180, 220)], fill=(170, 120, 70))
    d.line([(220, 120), (240, 150), (228, 170), (250, 200)], fill=(60, 40, 20), width=4)
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def _customer(store):
    uid = _uid(store, demo_data.DEMO_EMAIL)
    if uid:
        store.set_pay_pref(uid, "upi")


def _limits(store):
    rng = random.Random(19)
    rows = []
    for _ in range(5):
        at = datetime.now() - timedelta(days=rng.randrange(0, 6), minutes=rng.randrange(0, 1440))
        rows.append(("login", f"198.51.100.{rng.randrange(2, 250)}", at.strftime("%Y-%m-%d %H:%M:%S")))
    rows.append(("search", f"203.0.113.{rng.randrange(2, 250)}", (datetime.now() - timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S")))
    with store.db() as conn:
        conn.executemany("INSERT INTO rate_blocks (what, who, at) VALUES (?, ?, ?)", sorted(rows, key=lambda r: r[2]))


def _backup(store):
    backup_id, blob = store.save_backup("auto", "demo restore test")
    store.check_backup(backup_id, blob)
