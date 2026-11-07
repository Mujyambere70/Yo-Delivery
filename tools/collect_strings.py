"""
tools/collect_strings.py - lists the English texts of the shop pages that a language does not translate yet.

It opens the customer, seller and courier pages (the admin panel is English only) in the chosen
language, records every text the page translator cannot find in translations/<lang>.json, and adds
the messages written in the Python code (errors, flash messages, notifications).
Run:  python tools/collect_strings.py --lang=ru            (default ru)
"""

import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import i18n  # noqa: E402

LANG = next((a.split("=")[1] for a in sys.argv if a.startswith("--lang=")), "ru")
ONLY_V3 = "--all" not in sys.argv
seen = []
_orig = i18n.translate_text


def record(text, lang=None):
    if lang and lang != LANG:                   # e.g. a message for an English-speaking customer
        return _orig(text, lang)
    done = _orig(text, lang or LANG)
    key = " ".join(str(text or "").split())
    if done == text and key and re.search(r"[A-Za-z]{2}", key) and key not in seen \
            and not re.search(r"\b(YC|OD|TOP|TRF|RWD)\d{6}", key):       # filled-in messages: see the {0} patterns
        seen.append(key)
    return done


i18n.translate_text = record

from app import create_app  # noqa: E402

app = create_app(os.path.join(tempfile.mkdtemp(), "c.db"), env={"FX_ON_SEED": "0"}, housekeeping=False)
store = app.store


def client(login=None, password=None):
    c = app.test_client()
    if login:
        html = c.get("/login").get_data(as_text=True)
        token = re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)
        c.post("/login", data={"csrf_token": token, "login": login, "password": password})
    c.get("/lang/" + LANG)
    return c


with store.db() as conn:
    numbers = [r[0] for r in conn.execute("SELECT number FROM shipments ORDER BY id")]
    slugs = [r[0] for r in conn.execute("SELECT slug FROM posts WHERE status = 'Published'")]
guest = client()
for url in ["/courier", "/courier/track", "/courier/track/" + numbers[0], "/blog", "/blog/c/guides", "/blog/c/deals",
            "/rewards", "/pay/emi", "/pay/emi?amount=60000", "/driver/apply", "/blog/nope"] + \
           ["/blog/" + s for s in slugs]:
    guest.get(url)
ravi = client("demo@example.com", "Demo@1234")
for url in ["/courier/book", "/courier/shipments", "/pay", "/pay/add", "/pay/send", "/rewards", "/rewards/quiz",
            "/driver/apply", "/", "/checkout", "/p/nova-pro-12", "/account"] + \
           [f"/courier/s/{n}" for n in numbers[:4]] + [f"/courier/s/{numbers[0]}/label"]:
    ravi.get(url)
html = ravi.get("/rewards/quiz").get_data(as_text=True)
token = re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)
ravi.post("/rewards/quiz", data={"csrf_token": token, **{f"q{q}": "0" for q in set(re.findall(r'name="q(\d+)"', html))}})
ravi.get("/rewards/quiz")
courier = client("courier@example.com", "Courier@1234")
for url in ["/driver", "/board/courier", f"/driver/job/{numbers[0]}"]:
    courier.get(url)
seller = client("seller@example.com", "Seller@1234")
for url in ["/seller/stories", "/seller/stories/new", "/board/seller", "/seller", "/seller/products"]:
    seller.get(url)

# version 4: Yo Tickets (guest, traveller with a held booking, conductor)
from datetime import date, timedelta  # noqa: E402
day = (date.today() + timedelta(days=3)).isoformat()
for q in ["mode=bus&from=Ambala+Cantt&to=New+Delhi", "mode=bus&from=Chandigarh&to=Jaipur",
          "mode=train&from=Ambala+Cantt&to=New+Delhi", "mode=train&from=Chandigarh&to=Mumbai",
          "mode=flight&from=Ambala+Cantt&to=Mumbai", "mode=flight&from=New+Delhi&to=Moscow",
          "mode=train&from=Mullana&to=Mumbai", "mode=bus&from=Ambala+Cantt&to=Chennai"]:
    guest.get(f"/tickets/search?{q}&date={day}&pax=2")
for url in ["/tickets", "/tickets/bus/ambala-cantt-to-new-delhi", "/tickets/train/chandigarh-to-mumbai",
            "/tickets/flight/new-delhi-to-moscow", "/tickets/v/NOPE12.000000000000"]:
    guest.get(url)
with store.db() as conn:
    trip_ids = [r[0] for r in conn.execute("SELECT MIN(t.id) FROM ticket_trips t JOIN ticket_services s "
                                           "ON s.id = t.service_id WHERE t.day = ? GROUP BY s.mode, s.classes", (day,))]
    pnrs = [r[0] for r in conn.execute("SELECT pnr FROM ticket_bookings ORDER BY id")]
for t in trip_ids:
    guest.get(f"/tickets/trip/{t}?pax=2")
    ravi.get(f"/tickets/trip/{t}?pax=2")
for url in ["/tickets", "/tickets/my"] + [f"/tickets/b/{p}" for p in pnrs]:
    ravi.get(url)
_, secs = store.seat_map(trip_ids[0])
free = [x["code"] for sec in secs for row in sec["rows"] for x in row if x and not x["taken"]][:2]
pnr = store.hold_seats(store.login("demo@example.com", "Demo@1234")["id"], trip_ids[0], free)
ravi.get(f"/tickets/b/{pnr}/pay")
ravi.get(f"/tickets/v/{store.ticket_token(pnrs[0])}")
conductor = client("conductor@example.com", "Conductor@1234")
conductor.get("/conductor")
with store.db() as conn:
    srw = conn.execute("SELECT t.id FROM ticket_trips t JOIN ticket_services s ON s.id = t.service_id JOIN "
                       "ticket_operators o ON o.id = s.operator_id WHERE o.code = 'SRW' ORDER BY t.id").fetchone()
if srw:
    conductor.get(f"/conductor/trip/{srw[0]}")
conductor.get(f"/tickets/v/{store.ticket_token(pnrs[0])}")

# version 5: places of the world, courier anywhere, tickets with connections / waiting list / round trips,
# messages, my data, product texts
for url in ["/places", "/places?q=Mugunga", "/places?q=133207&cc=IN", "/places/search?q=Gakenke",
            f"/tickets/search?mode=flight&from=Krasnoyarsk&to=Kigali&date={day}&pax=1",
            f"/tickets/search?mode=bus&from=Kigali&to=rw6833&date={day}&pax=1&ret={day}",
            f"/tickets/search?mode=train&from=Kigali&to=Huye&date={day}&pax=1",
            f"/tickets/search?mode=train&from=Krasnoyarsk&to=Moscow&date={day}&pax=1"]:
    guest.get(url)
    ravi.get(url)
for url in ["/messages", "/account/data", "/courier/book?from_country=RW&from_city=Kigali&from_place=Kigali",
            "/courier", "/addresses", "/p/homeease-cooker"]:
    ravi.get(url)
with store.db() as conn:
    threads = [r[0] for r in conn.execute("SELECT id FROM chat_threads")]
    full = conn.execute("SELECT trip_id, COUNT(*) FROM ticket_seats WHERE booking_id = 0 GROUP BY trip_id "
                        "ORDER BY 2 DESC").fetchone()
for t in threads:
    ravi.get(f"/messages/{t}")
    seller.get(f"/messages/{t}")
if full:
    ravi.get(f"/tickets/trip/{full[0]}?pax=1&cls=" + (store.seat_map(full[0])[0]["classes"][-1]["cls"]))
for url in ["/seller/orders", "/messages"] + [f"/seller/products/{p.id}/texts" for p in store.products(limit=200)
                                             if p.seller_id][:1]:
    seller.get(url)
c2 = client()
c2.get(f"/tickets/search?mode=flight&from=Krasnoyarsk&to=Kigali&date={day}&pax=1")

# version 6: the pages of the workers (/staff, /hub, staff card), ID check, bank payouts and team of a seller,
# mobile money, multi-city trips, customs forms and the route plan of a courier
hub = client("sunil.kumar@example.com", "Staff@2026")
for url in ["/staff", "/staff/card", "/hub", "/id-check"]:
    hub.get(url)
boss = client("rohit.malhotra@example.com", "Staff@2026")
boss.get("/staff")
for url in ["/seller/team", "/seller/bank"]:
    seller.get(url)
for url in ["/pay/mobile", "/id-check", f"/tickets/multi?s0=Ambala+Cantt&s1=New+Delhi&s2=Mumbai&m0=train&m1=flight&date={day}&go=1",
            "/tickets/multi"]:
    ravi.get(url)
with store.db() as conn:
    abroad = [r[0] for r in conn.execute("SELECT s.number FROM shipments s JOIN customs_declarations d "
                                         "ON d.shipment_id = s.id LIMIT 1")]
for n in abroad:
    ravi.get(f"/courier/s/{n}/customs")
courier.get("/driver/route")
for p in [p for p in store.products(limit=200) if p.seller_id][:1]:
    html = seller.get(f"/seller/products/{p.id}/texts").get_data(as_text=True)
    tok = re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)
    seller.post(f"/seller/products/{p.id}/texts", data={"csrf_token": tok, "action": "draft"})

# version 7: my work and payslips, help desk and claims, tax invoices, the seller's tax page, the partner API page,
# the signature of a courier, delivery time windows
for url in ["/staff", "/staff/me", "/staff/payslips"]:
    hub.get(url)
with store.db() as conn:
    slips = [r[0] for r in conn.execute("SELECT p.id FROM payslips p JOIN staff s ON s.id = p.staff_id JOIN users u "
                                        "ON u.id = s.user_id WHERE u.email = 'eric.habimana@example.com'")]
    orders = [r[0] for r in conn.execute("SELECT o.number FROM orders o JOIN users u ON u.id = o.user_id "
                                         "WHERE u.email = 'demo@example.com' ORDER BY o.id DESC LIMIT 2")]
    invs = [r[0] for r in conn.execute("SELECT i.id FROM invoices i JOIN orders o ON o.id = i.order_id JOIN users u "
                                       "ON u.id = o.user_id WHERE u.email = 'demo@example.com' LIMIT 2")]
    tickets = [r[0] for r in conn.execute("SELECT number FROM support_tickets")]
    delivered = [r[0] for r in conn.execute("SELECT s.number FROM shipments s JOIN users u ON u.id = s.user_id "
                                            "WHERE u.email = 'demo@example.com' AND s.status = 'Delivered' LIMIT 1")]
eric = client("eric.habimana@example.com", "Staff@2026")
for url in ["/staff/me", "/staff/payslips"] + [f"/staff/payslips/{i}" for i in slips[:1]]:
    eric.get(url)
for url in ["/support", "/support?topic=parcel&about=YC1"] + [f"/support/{t}" for t in tickets] + \
           [f"/orders/{o}/invoices" for o in orders] + [f"/invoices/{i}" for i in invs] + \
           [f"/courier/s/{n}/claim" for n in delivered] + [f"/courier/s/{n}" for n in delivered] + \
           ["/courier/book"] + [f"/orders/{o}" for o in orders]:
    ravi.get(url)
for url in ["/partners/api", "/support"]:
    guest.get(url)
for url in ["/seller/tax", "/seller/invoices"]:
    seller.get(url)
courier.get("/driver/route")
courier.get(f"/driver/job/{numbers[-1]}")

# messages written in the Python code of the new modules
pattern = re.compile(r'(?:ShopError|WrongCode|flash|notify\([^,]+,[^,]+,)\(?\s*(f?)"((?:[^"\\]|\\.)+)"')
files = ["courier.py", "courier_views.py", "yopay.py", "pay_views.py", "rewards.py", "rewards_views.py", "blog.py",
         "blog_views.py", "taskboard.py", "board_views.py", "photocheck.py", "store.py", "tickets.py", "tickets_views.py",
         "worldwide.py", "chat.py", "learn.py", "world_views.py", "v5_views.py", "app.py",
         "staff.py", "staff_views.py", "workforce.py", "workforce_views.py", "v6_views.py", "v6b_views.py",
         "mobilemoney.py", "customs.py", "bankpay.py", "addresscheck.py", "idcheck.py", "translate.py",
         "people.py", "invoices.py", "providers.py", "partners.py", "courierplus.py", "gtfsrt.py", "helpdesk.py",
         "v7_views.py", "v7b_views.py", "routeplan.py"]
for name in files if ONLY_V3 else [f for f in os.listdir(ROOT) if f.endswith(".py")]:
    src = open(os.path.join(ROOT, name), encoding="utf-8").read()
    for f, text in pattern.findall(src):
        text = text.replace('\\"', '"')
        if f:
            n = [0]

            def num(m):
                n[0] += 1
                return "{" + str(n[0] - 1) + "}"
            text = re.sub(r"\{[^}]+\}", num, text)
        record(text)
import courier as C  # noqa: E402
import blog as B  # noqa: E402
import yopay as Y  # noqa: E402
import taskboard as T  # noqa: E402
for d in (C.KINDS, C.SERVICES, C.ZONE_NAMES, B.POST_CATEGORIES, Y.WALLET_KINDS, T.BOARD_COLUMNS, T.PRIORITIES):
    for v in d.values():
        record(v)
for v in C.VEHICLES.values():
    record(v[0])
import tickets as TK  # noqa: E402
for d in (TK.MODES, TK.CLASS_NAMES):
    for v in d.values():
        record(v)
for v in TK.TRIP_STEPS + [TK.HELD, TK.CONFIRMED, TK.CANCELLED, TK.EXPIRED, TK.COMPLETED, "Scheduled",
                          "Window", "Aisle", "Middle", "Lower", "Upper", "Side lower", "Side upper", "Single",
                          "Double (aisle)", "Double (window)", "Extra legroom", "Front row", "Lower deck", "Upper deck",
                          "Seats", "Business", "Economy", "Narrow-body jet", "Wide-body jet",
                          "Multi-axle AC Sleeper (2+1)", "AC Seater Push-back (2+2)"]:
    record(v)
for s in C.NETWORK_STEPS + [C.FAILED, C.CANCELLED, C.RETURNED]:
    record(s)
import helpdesk as HD  # noqa: E402
import people as PE  # noqa: E402
for d in (HD.TOPICS, HD.CLAIM_KINDS, PE.CONTRACT_KINDS, C.DELIVERY_WINDOWS):
    for v in d.values():
        record(v)
for v in list(PE.LEAVE_KINDS) + [c[0] for c in PE.COURSES.values()] + ["Open", "Answered", "Closed", "Waiting", "Paid",
                                                                       "Refused", "Safety check", "Tax invoice",
                                                                       "Bill of supply", "Credit note"]:
    record(v)
import geo  # noqa: E402
for v in list(TK.WORLD_CLASS_NAMES.values()) + list(geo.KIND_NAMES.values()) + [TK.WAITLISTED, "Nearby region",
                                                                                "Whole country", "Waiting list"]:
    record(v)
# content is not interface text: blog posts, quiz questions, products and people are written in their own language
import json  # noqa: E402
with store.db() as conn:
    content = " ".join(" ".join(r) for r in conn.execute("SELECT title, summary, body, tags FROM posts"))
    content += " ".join(" ".join([r[0], r[2]] + json.loads(r[1])) for r in conn.execute(
        "SELECT question, options, explanation FROM quiz_questions"))
    content += " ".join(" ".join(r) for r in conn.execute("SELECT name, brand FROM products"))
    content += " ".join(r[0] for r in conn.execute("SELECT name FROM users"))
    content += " ".join(" ".join(r) for r in conn.execute("SELECT title, notes FROM board_tasks"))
    content += " ".join(" ".join(r) for r in conn.execute("SELECT name, code FROM ticket_operators"))
    content += " ".join(r[0] for r in conn.execute("SELECT name FROM ticket_passengers"))
    content += " ".join(" ".join(r) for r in conn.execute("SELECT name, about FROM product_texts"))
    content += " ".join(r[0] for r in conn.execute("SELECT body FROM chat_messages"))
content = " ".join(content.replace("*", "").replace("#", "").split())
seen = [t for t in seen if t not in content]
print("\n".join(seen))
print(len(seen), "missing for", LANG, file=sys.stderr)
