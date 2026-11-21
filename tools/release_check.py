"""
tools/release_check.py - the checks that run before every release (version 9).

    1. crawler:  signs in as 11 kinds of users (guest, customer, admin, seller, courier, conductor, support agent,
                 hub worker, finance worker, manager, worker in Rwanda), follows every link of every page (at most
                 --pages per user) and lists each answer 500 (server error) or 404 (missing page). Static files and
                 the 6 languages of the shop are opened too.
    2. browser:  starts the shop on a free local port and clicks through the main ways of the shop in Chromium
                 (Playwright): sign in, search with a typo, cart, worker home, menu, help desk with "is typing",
                 a translated help article, the status page. JavaScript errors on any page count as problems.

Run:   python tools/release_check.py                 (both parts)
       python tools/release_check.py --crawl-only    (no browser needed)
       python tools/release_check.py --pages=200     (a quicker crawl)
The exit code is 0 only when nothing was found, so the script can stop a deployment (CI).
The database is a new temporary SQLite file with the demo data; nothing real is touched and no e-mail is sent.
"""

import collections
import logging
import os
import re
import socket
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.environ.pop("DATABASE_URL", None)                    # never the real database
logging.disable(logging.CRITICAL)

from app import create_app  # noqa: E402

ARGS = sys.argv[1:]
MAX_PAGES = int(next((a.split("=")[1] for a in ARGS if a.startswith("--pages=")), "700"))
USERS = {"guest": None, "customer": ("demo@example.com", "Demo@1234"), "admin": ("admin@example.com", "Admin@1234"),
         "seller": ("seller@example.com", "Seller@1234"), "courier": ("courier@example.com", "Courier@1234"),
         "conductor": ("conductor@example.com", "Conductor@1234"), "support": ("neha.gill@example.com", "Staff@2026"),
         "hub": ("sunil.kumar@example.com", "Staff@2026"), "finance": ("kavya.iyer@example.com", "Staff@2026"),
         "manager": ("rohit.malhotra@example.com", "Staff@2026"), "rwanda": ("eric.habimana@example.com", "Staff@2026")}
SKIP = re.compile(r"/logout|/lang/|/currency/|\.json|/delete|/export|download|backup|\.csv|\.xml$|\.ics|/admin/database/|"
                  r"mailto:|tel:|^#|javascript:|/push/|/sw\.js")
LINK = re.compile(r'(?:href|src|action)="([^"#]+)')


def make_app():
    env = {"FX_ON_SEED": "0", "FX_AUTO": "0", "YO_ROUTING": "0"}            # no internet calls while checking
    return create_app(os.path.join(tempfile.mkdtemp(), "release-check.db"), housekeeping=False, env=env)


def sign_in(client, who, password):
    html = client.get("/login").get_data(as_text=True)
    token = re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)
    return client.post("/login", data={"csrf_token": token, "login": who, "password": password}).status_code == 302


def crawl(app):
    """Every page every user can reach. Returns {(user, url): problem}."""
    problems, total = collections.OrderedDict(), 0
    for user, login in USERS.items():
        c = app.test_client()
        if login and not sign_in(c, *login):
            problems[(user, "/login")] = "cannot sign in"
            continue
        seen, queue = set(), ["/"]
        while queue and len(seen) < MAX_PAGES:
            url = queue.pop(0)
            if url in seen:
                continue
            seen.add(url)
            try:
                r = c.get(url)
            except Exception as e:                                             # noqa: BLE001 (a crash is the finding)
                problems[(user, url)] = f"crash {type(e).__name__}: {str(e)[:80]}"
                continue
            total += 1
            if r.status_code >= 500 or r.status_code == 404:
                problems[(user, url)] = r.status_code
            if r.status_code == 200 and r.mimetype == "text/html":
                for link in LINK.findall(r.get_data(as_text=True)):
                    link = link.replace("&amp;", "&")
                    if link.startswith("/") and not SKIP.search(link) and link not in seen:
                        queue.append(link)
        print(f"  {user:10} {len(seen):4} pages")
    guest = app.test_client()                                                 # every language once
    for lang in ("ru", "fr", "es", "ar", "zh", "rw"):
        guest.get(f"/lang/{lang}")
        for url in ("/", "/find?q=sheos", "/help", "/contact", "/status"):
            total += 1
            if guest.get(url).status_code != 200:
                problems[(f"guest-{lang}", url)] = "not 200"
    print(f"  requests: {total}")
    return problems


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def browser(app):
    """Click through the main ways of the shop. Returns a list of problems."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return ["Playwright is not installed (pip install playwright; playwright install chromium) - or use --crawl-only"]
    from werkzeug.serving import make_server
    port = free_port()
    server = make_server("127.0.0.1", port, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base, problems, js_errors = f"http://127.0.0.1:{port}", [], []

    def check(name, ok):
        print(("  PASS " if ok else "  FAIL ") + name)
        if not ok:
            problems.append(name)

    def page(b):
        ctx = b.new_context(viewport={"width": 1300, "height": 900})
        pg = ctx.new_page()
        pg.on("console", lambda m: js_errors.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: js_errors.append(str(e)))
        return ctx, pg

    def login(pg, who, password):
        pg.goto(base + "/login")
        pg.fill("input[name=login]", who)
        pg.fill("input[name=password]", password)
        pg.press("input[name=password]", "Enter")
        pg.wait_for_load_state()

    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            ctx, pg = page(b)
            pg.goto(base + "/find?q=sheos")
            check("search corrects 'sheos' to 'shoes'", "shoes" in pg.locator(".find-fixed").inner_text())
            pg.goto(base + "/status")
            check("status page says all parts work", pg.locator(".status-head.ok").count() == 1)
            pg.goto(base + "/lang/ru")
            pg.goto(base + "/help")
            check("help centre shows translated articles in Russian", "Где мой заказ?" in pg.content())
            ctx.close()

            ctx, pg = page(b)
            login(pg, "demo@example.com", "Demo@1234")
            check("customer signs in", "/login" not in pg.url)
            pg.goto(base + "/")
            pg.locator(".pcard .pcard-link").first.click()
            pg.wait_for_load_state()
            add = pg.locator("form[action*='cart'] button").first
            if add.count():
                add.click()
                pg.wait_for_load_state()
            pg.goto(base + "/cart")
            check("cart page opens", pg.locator("h1").count() > 0)
            pg.goto(base + "/checkout")
            check("checkout chooses the saved way to pay (UPI)", pg.locator("input[name=payment][value=upi]").is_checked())
            pg.goto(base + "/account")
            check("account shows 'My usual way to pay'", pg.locator("#pay").count() == 1)
            ctx.close()

            ctx, pg = page(b)
            login(pg, "rohit.malhotra@example.com", "Staff@2026")
            check("worker lands on My work with number tiles", "/staff" in pg.url)
            pg.goto(base + "/staff")
            check("swap form or list is on My work", pg.locator("#swaps").count() == 1)
            pg.goto(base + "/")
            pg.click("#menuBtn")
            pg.wait_for_selector(".menu-find input")
            pg.keyboard.press("Escape")
            check("menu opens and closes", pg.locator("#menuDrawer").is_hidden())
            ctx.close()

            ctx, pg = page(b)
            login(pg, "admin@example.com", "Admin@1234")
            for url in ("/admin/site/searches", "/admin/staff/swaps", "/admin/monitor/limits", "/admin/backups", "/admin/kb"):
                r = pg.goto(base + url)
                check(f"admin page {url}", r is not None and r.status == 200)
            ctx.close()
            b.close()
    finally:
        server.shutdown()
    noise = ("favicon", "tile.openstreetmap", "net::ERR", "Failed to load resource")     # no internet in a CI box
    real = sorted({e for e in js_errors if not any(n in e for n in noise)})
    check(f"no JavaScript errors ({len(real)})", not real)
    for e in real[:10]:
        print("     ", e[:160])
    return problems


def main():
    started = time.time()
    app = make_app()
    print("1. crawler")
    problems = [f"{user} {what} {url}" for (user, url), what in crawl(app).items()]
    for p in problems[:40]:
        print("   ", p)
    if "--crawl-only" not in ARGS:
        print("2. browser")
        problems += browser(app)
    print(f"{len(problems)} problems, {time.time() - started:.0f} s")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
