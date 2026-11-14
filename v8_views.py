"""
v8_views.py - version 8 pages:

    /menu, /find.json, /work                the "three lines" menu with the profile, its search and the worker's button
    /contact, /page/<slug>                  contacts and pages written by the staff (About, Terms, Privacy ...)
    /help/a/<slug>, /help/suggest.json      help articles (the Help Centre itself is /help)
    /.well-known/security.txt               where to report a security problem
    /staff/directory                        colleagues
    /staff/statement/<year>                 the worker's pay of a year
    /staff/clock-code                       clock in with the code of the office screen
    /admin/find                             quick find: order, parcel, ticket, question, customer, worker or page
    /admin/site ...                         contacts, pages (with versions and languages) and banners
    /admin/kb ...                           help articles
    /admin/staff/notices                    news for the workers of a branch
    /admin/staff/holidays                   public holidays and the leave rules of the countries
    /admin/offices/<id>/code, /network      the clock-in code screen of an office and its network
    /admin/partners/<id>/webhook ...        the webhook of a courier partner, test and "send again"
    /admin/couriers/speed                   the learned speed of couriers by town and hour
    /admin/tickets/gtfs/poll                read the live feeds now
    /invoices/<id>/einvoice.json            India's e-invoice JSON (demo: not registered)
    /support/<n>/messages.json              new messages of a question (asked every few seconds by the page)
    /api/inbound-email                      e-mails from customers (signed by the e-mail provider)
"""

import json
from datetime import date

from flask import Response, abort, flash, jsonify, redirect, render_template, request, url_for
from werkzeug.routing import BuildError

import holidays as H
import images
import services as SV
import sitecontent as SC
import staff as S
import world
from core import ShopError


def register(app, store, h):

    # small pieces of version 8 data that existing pages show (invoice, ticket, partners, taxes, timetables)
    app.jinja_env.globals.update(einvoice=store.einvoice, booking_live=store.booking_live,
                                 webhook_deliveries=store.webhook_deliveries, rates_review=store.rates_review,
                                 gtfs_alerts=store.gtfs_alerts)

    def link(item):
        try:
            return url_for(item["endpoint"], **item.get("args", {}))
        except BuildError:
            return ""

    def menu_sections(q=""):
        out = []
        for title, items in store.menu_for(h.user_id()):
            rows = [dict(it, url=link(it)) for it in items if not q or SV.matches(q, it["label"], it["words"])]
            rows = [r for r in rows if r["url"]]
            if rows:
                out.append((title, rows))
        return out

    # ================================================================ the menu, its search, "my work"
    @app.route("/menu")
    def menu_page():
        q = request.args.get("q", "").strip()[:60]
        uid = h.user_id()
        found = [dict(r, url=link(r)) for r in store.quick_find(uid, q) if r["kind"] != "Page"] if q else []
        ctx = dict(sections=menu_sections(q), q=q, found=[r for r in found if r["url"]],
                   work=store.work_home(uid) if uid else None)
        if request.args.get("drawer"):
            return render_template("_menu_drawer.html", **ctx)
        return render_template("menu.html", **ctx)

    @app.route("/find.json")
    def find_json():
        q = request.args.get("q", "").strip()[:60]
        rows = [{"kind": r["kind"], "label": r["label"], "sub": r["sub"], "url": link(r)}
                for r in store.quick_find(h.user_id(), q)]
        return jsonify(results=[r for r in rows if r["url"]][:12])

    @app.route("/work")
    @h.login_required
    def my_work_go():
        """The button "My work": straight to the page this worker needs first."""
        work = store.work_home(h.user_id())
        if not work:
            flash("This page is for the workers of Yo Delivery.", "info")
            return redirect(url_for("account"))
        try:
            return redirect(url_for(work["endpoint"]))
        except BuildError:
            return redirect(url_for("staff_home"))

    # ================================================================ pages written by the staff
    @app.route("/contact")
    def contact_page():
        return render_template("contact.html", c=store.contacts(), pages=store.site_pages(footer_only=True))

    @app.route("/page/<slug>")
    def site_page(slug):
        if slug == "about":
            return redirect(url_for("about"))
        page = store.site_page(slug, world.current_lang())
        if page is None or not page["published"]:
            abort(404)
        return render_template("site_page.html", page=page, body=store.page_html(page),
                               translated=page["lang"] == world.current_lang())

    @app.route("/help/a/<slug>", methods=["GET", "POST"])
    def help_article(slug):
        a = store.kb_article(slug=slug)
        if a is None or not a["published"]:
            abort(404)
        if request.method == "POST":
            store.kb_vote(a["id"], request.form.get("helpful") == "1")
            flash("Thank you for telling us.", "success")
            return redirect(url_for("help_article", slug=slug))
        store.kb_opened(a["id"])
        values = store.text_values()
        return render_template("help_article.html", a=a, answer=SC.render_markdown(store.fill_text(a["answer"], values)),
                               more=[x for x in store.kb_articles(a["topic"]) if x["id"] != a["id"]][:6])

    @app.route("/help/suggest.json")
    def help_suggest():
        rows = store.kb_search(request.args.get("q", "")[:160], limit=4)
        return jsonify(articles=[{"question": a["question"], "url": url_for("help_article", slug=a["slug"])} for a in rows])

    @app.route("/.well-known/security.txt")
    def security_txt():
        c = store.contacts()
        year = date.today().year + 1
        text = (f"Contact: mailto:{c['security_email'] or c['email']}\n"
                f"Expires: {year}-12-31T23:59:59Z\n"
                "Preferred-Languages: en, ru, fr\n"
                f"Canonical: {h.base_url().rstrip('/')}/.well-known/security.txt\n"
                f"Policy: {h.base_url().rstrip('/')}{url_for('site_page', slug='terms')}\n"
                "# Yo Delivery is a demo project. Please do not test with other people's accounts or data.\n")
        return Response(text, mimetype="text/plain")

    # ================================================================ worker
    def worker_only():
        u = store.get_user(h.user_id())
        w = store.worker_of(h.user_id())
        if not (u and u["is_admin"]) and (not w or w["status"] != "Active"):
            abort(404)
        return w

    @app.route("/staff/directory")
    @h.login_required
    def staff_directory():
        worker_only()
        q = request.args.get("q", "").strip()[:60]
        return render_template("staff/directory.html", people=store.colleagues(h.user_id(), q), q=q,
                               full=store.can(h.user_id(), "staff.view"), ROLES=S.ROLES, WORKER_KINDS=S.WORKER_KINDS)

    @app.route("/staff/statement")
    @app.route("/staff/statement/<int:year>")
    @h.login_required
    def staff_statement(year=None):
        if not store.worker_of(h.user_id()):
            abort(404)
        st = store.year_statement(h.user_id(), year or date.today().year)
        return render_template("staff/statement.html", st=st)

    @app.route("/staff/clock-code")
    @h.login_required
    def staff_clock_code():
        """The address inside the QR code of the office screen: one press clocks the worker in."""
        w = worker_only()
        if w is None:
            return redirect(url_for("admin_staff"))
        return render_template("staff/clock_code.html", code="".join(ch for ch in request.args.get("c", "") if ch.isdigit())[:6],
                               w=w, shift=store.shift_status(h.user_id()))

    # ================================================================ admin: quick find
    @app.route("/admin/find")
    @h.admin_required
    def admin_find():
        q = request.args.get("q", "").strip()[:60]
        rows = [dict(r, url=link(r)) for r in store.quick_find(h.user_id(), q)] if q else []
        rows = [r for r in rows if r["url"]]
        if len(rows) == 1 and rows[0]["kind"] != "Page":
            return redirect(rows[0]["url"])
        return h.admin_page("admin/find.html", find_q=q, rows=rows)

    # ================================================================ admin: site content
    @app.route("/admin/site")
    @h.admin_required
    def admin_site():
        return h.admin_page("admin/site.html", c=store.contacts(), fields=SC.CONTACT_FIELDS, pages=store.site_pages(),
                            slides=store.slides(), LANGS=world.LANGUAGES, photos=SC.PHOTOS, articles=len(store.kb_articles(published_only=False)))

    @app.route("/admin/site/contacts", methods=["POST"])
    @h.admin_required
    def admin_site_contacts():
        try:
            changed = store.save_contacts(h.user_id(), request.form)
            flash("Saved: " + ", ".join(changed) + "." if changed else "Nothing was changed.", "success" if changed else "info")
        except ShopError as e:
            flash(str(e), "error")
        return redirect(url_for("admin_site") + "#contacts")

    @app.route("/admin/site/pages/<slug>", methods=["GET", "POST"])
    @h.admin_required
    def admin_site_page(slug):
        lang = request.values.get("lang", "en")
        if lang not in world.LANGUAGES:
            lang = "en"
        new = slug == "new"
        if request.method == "POST":
            f = request.form
            try:
                a = f.get("action", "save")
                if a == "delete":
                    store.delete_site_page(h.user_id(), slug, lang)
                    flash("Removed.", "success")
                    return redirect(url_for("admin_site") + "#pages")
                if a == "restore":
                    store.restore_page_version(h.user_id(), int(f.get("version") or 0))
                    flash("The earlier text is back. The text before it was kept as a version.", "success")
                    return redirect(url_for("admin_site_page", slug=slug, lang=lang))
                saved = store.save_site_page(h.user_id(), SC.slugify(f.get("slug") or f.get("title")) if new else slug, lang,
                                             f.get("title"), f.get("body"), bool(f.get("published")), bool(f.get("in_footer")))
                flash("Saved.", "success")
                return redirect(url_for("admin_site_page", slug=saved, lang=lang))
            except (ShopError, ValueError) as e:
                flash(str(e), "error")
                page = {"slug": f.get("slug", "") if new else slug, "title": f.get("title", ""), "body": f.get("body", ""),
                        "published": bool(f.get("published")), "in_footer": bool(f.get("in_footer")), "lang": lang}
                return h.admin_page("admin/site_page.html", page=page, new=new, lang=lang, LANGS=world.LANGUAGES,
                                    versions=[], english=None, preview=SC.render_markdown(store.fill_text(page["body"])),
                                    have=[])
        if new:
            page = {"slug": "", "title": "", "body": "", "published": 1, "in_footer": 1, "lang": "en"}
            return h.admin_page("admin/site_page.html", page=page, new=True, lang="en", LANGS=world.LANGUAGES, versions=[],
                                english=None, preview="", have=[])
        english = store.site_page(slug, "en", exact=True)
        if english is None:
            abort(404)
        page = store.site_page(slug, lang, exact=True) or dict(english, title="", body="", lang=lang, id=None)
        have = next((p["langs"] for p in store.site_pages() if p["slug"] == slug), [])
        return h.admin_page("admin/site_page.html", page=page, new=False, lang=lang, LANGS=world.LANGUAGES,
                            versions=store.page_versions(slug, lang), english=english if lang != "en" else None,
                            preview=store.page_html(page) if page["body"] else "", have=have)

    @app.route("/admin/site/slides", methods=["POST"])
    @h.admin_required
    def admin_site_slides():
        f = request.form
        try:
            if f.get("action") == "delete":
                store.delete_slide(h.user_id(), int(f.get("id") or 0))
                flash("The banner was removed.", "success")
            else:
                picture, file = "", request.files.get("picture")
                if file and file.filename:
                    picture = images.save_site_banner(file, store)
                store.save_slide(h.user_id(), int(f.get("id") or 0) or None, f, picture)
                flash("Saved.", "success")
        except (ShopError, ValueError) as e:
            flash(str(e), "error")
        return redirect(url_for("admin_site") + "#banners")

    # ================================================================ admin: help articles
    @app.route("/admin/kb", methods=["GET", "POST"])
    @h.admin_required
    def admin_kb():
        if request.method == "POST":
            f = request.form
            try:
                if f.get("action") == "delete":
                    store.delete_kb_article(h.user_id(), int(f.get("id") or 0))
                    flash("The article was removed.", "success")
                else:
                    store.save_kb_article(h.user_id(), int(f.get("id") or 0) or None, f)
                    flash("Saved.", "success")
                return redirect(url_for("admin_kb"))
            except (ShopError, ValueError) as e:
                flash(str(e), "error")
        edit = store.kb_article(article_id=request.args.get("edit", type=int)) if request.args.get("edit") else None
        return h.admin_page("admin/kb.html", articles=store.kb_articles(published_only=False), topics=SC.KB_TOPICS,
                            edit=edit, form=request.form if request.method == "POST" else None)

    # ================================================================ admin: staff news
    @app.route("/admin/staff/notices", methods=["GET", "POST"])
    @h.admin_required
    def admin_notices():
        if request.method == "POST":
            f = request.form
            try:
                if f.get("action") == "delete":
                    store.delete_staff_news(h.user_id(), int(f.get("id") or 0))
                    flash("Removed.", "success")
                else:
                    store.post_staff_news(h.user_id(), f.get("title"), f.get("body"), f.get("office_id") or None,
                                          bool(f.get("pinned")), f.get("until_day", ""))
                    flash("Posted. The workers see it on 'My work'.", "success")
            except (ShopError, ValueError) as e:
                flash(str(e), "error")
            return redirect(url_for("admin_notices"))
        branch = store.branch_of(h.user_id())
        offices = [o for o in store.offices() if branch is None or o["id"] in branch]
        return h.admin_page("admin/notices.html", news=store.staff_news_list(h.user_id()), offices=offices,
                            everybody=branch is None)

    # ================================================================ admin: holidays and leave rules
    @app.route("/admin/staff/holidays", methods=["GET", "POST"])
    @h.admin_required
    def admin_holidays():
        if request.method == "POST":
            f = request.form
            try:
                if f.get("action") == "delete":
                    store.delete_holiday(h.user_id(), int(f.get("id") or 0))
                    flash("Removed.", "success")
                else:
                    store.save_holiday(h.user_id(), f.get("country"), f.get("day"), f.get("name"), f.get("kind", "public"),
                                       bool(f.get("by_moon")))
                    flash("Saved.", "success")
            except (ShopError, ValueError) as e:
                flash(str(e), "error")
            return redirect(url_for("admin_holidays", country=f.get("country", ""), year=f.get("year", "")))
        countries = store.holiday_countries()
        w = store.worker_of(h.user_id())
        mine = (w["office_country"] if w else "") or ""
        country = (request.args.get("country") or (mine if mine in countries else (countries[0] if countries else ""))).upper()
        year = request.args.get("year", type=int) or (date.today().year + (1 if date.today().month >= 11 else 0))
        return h.admin_page("admin/holidays.html", rows=store.holidays(country, year), countries=countries, country=country,
                            year=year, kinds=H.HOLIDAY_KINDS, rules=store.leave_rules(), source=H.SOURCES.get(country, ""),
                            may_rules=store.can(h.user_id(), "payroll.manage"), today=date.today().isoformat())

    @app.route("/admin/staff/leave-rules", methods=["POST"])
    @h.admin_required
    def admin_leave_rules():
        f = request.form
        try:
            store.save_leave_rule(h.user_id(), f.get("country"), f.get("min_days"), f.get("unit"), f.get("holiday_rate"),
                                  f.get("note", ""), bool(f.get("checked")))
            flash("Saved.", "success")
        except ShopError as e:
            flash(str(e), "error")
        return redirect(url_for("admin_holidays") + "#rules")

    # ================================================================ admin: office code and network
    @app.route("/admin/offices/<int:office_id>/code")
    @h.admin_required
    def admin_office_code(office_id):
        branch = store.branch_of(h.user_id())
        o = next((x for x in store.offices() if x["id"] == office_id), None)
        if o is None or (branch is not None and office_id not in branch):
            abort(404)
        code = store.office_code(office_id)
        return render_template("admin/office_code.html", o=o, code=code, step=H.CODE_STEP_S,
                               link=h.base_url().rstrip("/") + url_for("staff_clock_code", c=code))

    @app.route("/admin/offices/<int:office_id>/network", methods=["POST"])
    @h.admin_required
    def admin_office_network(office_id):
        try:
            store.set_office_network(h.user_id(), office_id, request.form.get("net_ranges", ""))
            flash("Saved.", "success")
        except ShopError as e:
            flash(str(e), "error")
        return redirect(url_for("admin_offices"))

    # ================================================================ admin: partner webhooks
    @app.route("/admin/partners/<int:partner_id>/webhook", methods=["POST"])
    @h.admin_required
    def admin_partner_webhook(partner_id):
        f = request.form
        try:
            if f.get("action") == "ping":
                store.ping_partner_webhook(h.user_id(), partner_id)
                store.run_jobs(3)
                flash("A test message was put on the queue. See the result in the list below.", "success")
            elif f.get("action") == "resend":
                store.resend_webhook(h.user_id(), int(f.get("delivery") or 0))
                flash("The message is on the queue again.", "success")
            else:
                store.set_partner_webhook(h.user_id(), partner_id, f.get("webhook_url", ""))
                flash("Saved.", "success")
        except (ShopError, ValueError) as e:
            flash(str(e), "error")
        return redirect(url_for("admin_partners") + "#webhooks")

    # ================================================================ admin: courier speed, live feeds
    @app.route("/admin/couriers/speed", methods=["GET", "POST"])
    @h.admin_required
    def admin_courier_speed():
        if request.method == "POST":
            n = store.build_speed_profile()
            flash(f"Counted again: {n} rows." if n else "There are not enough GPS points yet (20 for a town and hour).",
                  "success" if n else "info")
            return redirect(url_for("admin_courier_speed"))
        rows = store.speed_profile()
        towns = {}
        for r in rows:
            towns.setdefault(r["city"], {})[r["hour"]] = r
        return h.admin_page("admin/courier_speed.html", towns=towns, fixed=25)

    @app.route("/admin/tickets/gtfs/poll", methods=["POST"])
    @h.admin_required
    def admin_gtfs_poll():
        out = store.poll_gtfs_rt()
        flash(f"Live feeds read: {out['ok']} of {out['feeds']}." + (" See the reason next to the feed." if out["failed"] else ""),
              "success" if out["ok"] or not out["feeds"] else "error")
        return redirect(url_for("admin_gtfs"))

    # ================================================================ e-invoice
    @app.route("/invoices/<int:invoice_id>/einvoice.json")
    @h.login_required
    def invoice_einvoice(invoice_id):
        inv = store.invoice(invoice_id)
        uid = h.user_id()
        ok = inv is not None and (inv["user_id"] == uid or store.can(uid, "invoices.view"))
        if inv is not None and not ok and inv["seller_id"]:
            shop, abil = store.seller_membership(uid)
            ok = bool(shop) and shop["id"] == inv["seller_id"] and ("owner" in abil or "seller.orders" in abil)
        text = store.einvoice_json(invoice_id) if ok else None
        if text is None:
            abort(404)
        return Response(text, mimetype="application/json", headers={
            "Content-Disposition": f"attachment; filename=einvoice-{inv['number'].replace('/', '-')}.json"})

    @app.route("/admin/taxes/check", methods=["POST"])
    @app.route("/admin/tax-categories/check", methods=["POST"])
    @h.admin_required
    def admin_rate_check():
        f = request.form
        kind = "category" if request.path.startswith("/admin/tax-categories") else "country"
        try:
            store.mark_rate_checked(h.user_id(), kind, f.get("key"), f.get("valid_from", ""), f.get("valid_to", ""),
                                    f.get("note", ""))
            flash("Marked as checked.", "success")
        except (ShopError, ValueError) as e:
            flash(str(e), "error")
        return redirect(url_for("admin_tax_categories" if kind == "category" else "admin_taxes") + "#review")

    # ================================================================ help desk: live messages, e-mail in
    @app.route("/support/<number>/messages.json")
    @h.login_required
    def support_messages_json(number):
        data = store.support_messages_after(number, h.user_id(), request.args.get("after", 0))
        if data is None:
            abort(404)
        me = h.user_id()
        staff = store.can(me, "support.manage")
        for m in data["messages"]:
            m["mine"] = m["staff"] == staff
        return jsonify(data)

    @app.route("/api/inbound-email", methods=["POST"])
    def inbound_email():
        body = request.get_data(cache=False)[:200_000]
        if not store.inbound_email_ok(request.headers.get("X-Yo-Signature", ""), body):
            return jsonify(error="bad signature"), 401
        try:
            p = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return jsonify(error="bad json"), 400
        result, number = store.inbound_email(p.get("message_id"), p.get("from"), p.get("subject"), p.get("text"))
        return jsonify(result=result, ticket=number)
