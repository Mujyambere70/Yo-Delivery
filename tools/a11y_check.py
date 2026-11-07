"""
tools/a11y_check.py - versions 6 and 7: an accessibility audit of the main pages with axe-core (the open-source checker
used by browsers' developer tools), run by the developer before a release:

    npm pack axe-core@4.10.2 && tar xzf axe-core-4.10.2.tgz         (once, outside the project)
    python tools/a11y_check.py path/to/package/axe.min.js [report.json]

It starts the shop on a free port with the demo data, opens every page in Chromium (Playwright), runs axe with
the WCAG 2.1 A and AA rules and prints the problems per page. Version 7 adds the new pages and a keyboard check:
the first Tab of a page must reach the "Skip to the content" link, it must show a visible focus ring, and Enter
must move to the main content. tests/test_v6.py checks the simple rules (labels,
alt texts, the language of the page) on every test run without a browser.
"""

import json
import logging
import os
import socket
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
logging.disable(logging.CRITICAL)

PAGES = [("guest", "/"), ("guest", "/search?q=kettle"), ("guest", "/p/homeease-kettle"), ("guest", "/courier"),
         ("guest", "/tickets"), ("guest", "/places?q=Mugunga"), ("guest", "/login"), ("guest", "/register"),
         ("demo", "/courier/book"), ("demo", "/account/addresses"), ("demo", "/pay"), ("demo", "/messages"),
         ("demo", "/id-check"), ("staff", "/staff"), ("admin", "/admin/staff"), ("admin", "/admin/staff/rota"),
         ("admin", "/admin/staff/tasks"), ("admin", "/admin/taxes"),
         # version 7
         ("demo", "/support"), ("staff", "/staff/me"), ("admin", "/admin/payroll"), ("admin", "/admin/approvals"),
         ("admin", "/admin/invoices"), ("admin", "/admin/partners"), ("admin", "/admin/support"),
         ("admin", "/admin/claims"), ("admin", "/admin/risk"), ("admin", "/admin/analytics"),
         ("admin", "/admin/forecast"), ("admin", "/admin/experiments"), ("admin", "/admin/privacy"),
         ("guest", "/partners/api")]
KEYBOARD = [("guest", "/"), ("demo", "/support"), ("admin", "/admin/payroll")]


def keyboard_check(page, base, url):
    """First Tab -> the skip link with a visible focus ring; Enter -> the main content."""
    page.goto(base + url)
    page.wait_for_load_state("networkidle")
    page.keyboard.press("Tab")
    first = page.evaluate("""() => { const e = document.activeElement, st = getComputedStyle(e);
        return {cls: e.className, href: e.getAttribute('href'), visible: e.getBoundingClientRect().height > 0,
                ring: st.outlineStyle !== 'none' && parseFloat(st.outlineWidth) > 0}; }""")
    page.keyboard.press("Enter")
    page.wait_for_timeout(200)
    moved = page.evaluate("() => location.hash === '#main' || document.activeElement.id === 'main'")
    ok = "skip-link" in (first["cls"] or "") and first["href"] == "#main" and first["visible"] and first["ring"] and moved
    return ok, {**first, "moved": moved}
USERS = {"demo": ("demo@example.com", "Demo@1234"), "admin": ("admin@example.com", "Admin@1234"),
         "staff": ("sunil.kumar@example.com", "Staff@2026")}


def main(axe_path, out=None):
    from app import create_app
    from werkzeug.serving import make_server
    from playwright.sync_api import sync_playwright
    app = create_app(os.path.join(tempfile.mkdtemp(), "a11y.db"), housekeeping=False,
                     env={"FX_ON_SEED": "0", "YO_ROUTING": "0"})
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    srv = make_server("127.0.0.1", port, app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    axe = open(axe_path, encoding="utf-8").read()
    report = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        pages = {}
        for who in ("guest", "demo", "admin", "staff"):
            ctx = browser.new_context(viewport={"width": 1280, "height": 900})
            page = ctx.new_page()
            if who in USERS:
                page.goto(f"http://127.0.0.1:{port}/login")
                page.fill("input[name=login]", USERS[who][0])
                page.fill("input[name=password]", USERS[who][1])
                with page.expect_navigation():
                    page.press("input[name=password]", "Enter")        # submits the login form itself
                page.wait_for_load_state("networkidle")
                if "/login" in page.url:
                    raise SystemExit(f"Could not sign in as {who}.")
            pages[who] = page
        for who, url in PAGES:
            page = pages[who]
            page.goto(f"http://127.0.0.1:{port}{url}")
            page.wait_for_load_state("networkidle")
            if "/login" in page.url and url != "/login":
                print(f"{url:32} NOT CHECKED: the page asked to sign in")
                continue
            page.add_script_tag(content=axe)
            res = page.evaluate("""async () => (await axe.run(document, {runOnly: {type: 'tag',
                values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']}})).violations.map(v => ({id: v.id,
                impact: v.impact, help: v.help, nodes: v.nodes.length,
                where: v.nodes.slice(0, 3).map(n => n.target.join(' ') + ' ' + JSON.stringify(
                    (n.any[0] || {}).data || {}).slice(0, 120))}))""")
            report[url] = res
            print(f"{url:32} {len(res)} problems", "; ".join(f"{v['id']}({v['nodes']})" for v in res))
        for who, url in KEYBOARD:
            ok, info = keyboard_check(pages[who], f"http://127.0.0.1:{port}", url)
            report["keyboard " + url] = info
            print(f"keyboard {url:23} {'ok' if ok else 'PROBLEM'} {info}")
        browser.close()
    srv.shutdown()
    if out:
        with open(out, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=1)
    return report


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
