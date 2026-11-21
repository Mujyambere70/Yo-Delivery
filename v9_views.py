"""
v9_views.py - version 9 pages:

    /find                               one search: products, help, pages and numbers (forgives typing mistakes)
    /status                             is everything working?
    /admin/site/searches                what people searched for and did not find
    /admin/site/translate/<kind>/<ref>  help articles, banners and contacts in the other languages
    /admin/site/pages/<slug>/draft, /preview, /admin/site/upload      drafts, preview and pictures of pages
    /staff/swap, /admin/staff/swaps     swapping a shift;  /staff/onboarding   first-days checklist
    /support/<n>/typing, /file, /rate   "is typing", files and stars at the help desk
    /account/pay-pref, /orders/<n>/again, /orders/<n>/address          customers
    /seller/insights, /seller/import    sellers
    /partners/api/check                 does my webhook signature fit?
    /admin/monitor/limits               who was stopped by a limit;  /admin/backups/check   restore test now
"""

from flask import Response, abort, flash, redirect, render_template, request, url_for

import images
import providers
import v9 as V9
import world
from core import ShopError


def register(app, store, h):

    def ip():
        return request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip() or "?"

    def who():
        return f"u{h.user_id()}" if h.user_id() else ip()

    app.jinja_env.globals.update(role_numbers=store.role_numbers, my_swaps=lambda: store.swaps(h.user_id()),
                                 onboarding_of=store.onboarding_of, swap_colleagues=lambda: store.swap_colleagues(h.user_id()), recent_clock_ins=store.recent_clock_ins,
                                 support_files=lambda n: store.support_files(n, h.user_id()) or [],
                                 support_rating=store.support_rating, pay_pref=lambda: store.pay_pref(h.user_id()),
                                 my_addresses=lambda: store.addresses(h.user_id()) if h.user_id() else [],
                                 backup_checks=store.backup_checks, rating_summary=store.support_rating_summary)

    SAMPLE_SECRET, SAMPLE_BODY = "whsec_sandbox_only", '{"event": "shipment.delivered", "number": "YC0000000001"}'

    def sample_signature():
        """A fresh example for the sandbox form of the partner page (a made-up secret that opens nothing)."""
        return {"secret": SAMPLE_SECRET, "body": SAMPLE_BODY, "header": providers.sign(SAMPLE_SECRET, SAMPLE_BODY.encode())}
    app.jinja_env.globals["sample_signature"] = sample_signature

    @app.before_request
    def limits():
        """Version 9: a person (or an address) can try to sign in 10 times in 10 minutes."""
        if request.endpoint == "login" and request.method == "POST" and not store.rate_ok("login", ip()):
            return render_template("too_many.html", what="sign-in tries", wait="10 minutes"), 429
        return None

    # ================================================================ search and status
    @app.route("/find")
    def find_page():
        q = request.args.get("q", "").strip()[:80]
        if q and not store.rate_ok("search", who()):
            return render_template("too_many.html", what="searches", wait="one minute"), 429
        res = store.find_everything(h.user_id(), q, world.current_lang())
        try:
            res["products"] = store.localize_products(res["products"])
        except Exception:
            pass
        for r in res["found"]:
            try:
                r["url"] = url_for(r["endpoint"], **r.get("args", {}))
            except Exception:
                r["url"] = ""
        return render_template("find.html", r=res, q=q)

    @app.route("/status")
    def status_page():
        parts = store.system_status()
        return render_template("status.html", parts=parts, all_ok=all(p["ok"] for p in parts)), 200

    @app.route("/admin/site/searches")
    @h.admin_required
    def admin_search_misses():
        return h.admin_page("admin/searches.html", s=store.search_misses())

    # ================================================================ content: translations, drafts, pictures
    @app.route("/admin/site/translate/<kind>/<ref>", methods=["GET", "POST"])
    @h.admin_required
    def admin_translate(kind, ref):
        if kind not in V9.TEXT_KINDS:
            abort(404)
        lang = request.values.get("lang", "ru")
        if lang not in world.LANGUAGES or lang == "en":
            lang = "ru"
        if kind == "kb":
            src = store.kb_article(article_id=int(ref)) if ref.isdigit() else None
        elif kind == "slide":
            src = next((s for s in store.slides() if str(s["id"]) == ref), None)
        else:
            src, ref = store.contacts(), "site"
        if src is None:
            abort(404)
        if request.method == "POST":
            try:
                store.save_content_texts(h.user_id(), kind, ref, lang, request.form)
                flash("Saved. Visitors who read in this language see your text.", "success")
            except ShopError as e:
                flash(str(e), "error")
            return redirect(url_for("admin_translate", kind=kind, ref=ref, lang=lang))
        have = store.content_texts(kind, ref)
        return h.admin_page("admin/translate.html", kind=kind, ref=ref, lang=lang, src=src, fields=V9.TEXT_KINDS[kind],
                            have=have, LANGS=world.LANGUAGES, done={l for l, _ in have})

    @app.route("/admin/site/pages/<slug>/draft", methods=["POST"])
    @h.admin_required
    def admin_page_draft(slug):
        f = request.form
        lang = f.get("lang", "en")
        try:
            if f.get("action") == "publish":
                store.publish_page_draft(h.user_id(), slug, lang)
                flash("The draft is published. The text before it was kept as a version.", "success")
            else:
                store.save_page_draft(h.user_id(), slug, lang, f.get("title"), f.get("body"), f.get("publish_at", ""))
                flash("Draft saved. Visitors still see the published text.", "success")
        except ShopError as e:
            flash(str(e), "error")
        return redirect(url_for("admin_site_page", slug=slug, lang=lang) + "#draft")

    @app.route("/admin/site/pages/<slug>/preview")
    @h.admin_required
    def admin_page_preview(slug):
        lang = request.args.get("lang", "en")
        page = store.site_page(slug, lang, exact=True)
        if page is None:
            abort(404)
        if page.get("draft_body"):
            page = dict(page, title=page["draft_title"], body=page["draft_body"])
        return render_template("site_page.html", page=page, body=store.page_html(page), translated=True, preview=True)

    @app.route("/admin/site/upload", methods=["POST"])
    @h.admin_required
    def admin_site_upload():
        back = request.form.get("back") or url_for("admin_site")
        if not back.startswith("/admin/site"):
            back = url_for("admin_site")
        try:
            if not store.rate_ok("upload", who()):
                raise ShopError("Too many uploads. Please wait a few minutes.")
            file = request.files.get("picture")
            if not file or not file.filename:
                raise ShopError("Please choose a picture.")
            name = images.save_page_picture(file, store)
            flash(f"Picture saved. Put this line where it should stand in the text:  ![what the picture shows](/img/{name})", "success")
        except ShopError as e:
            flash(str(e), "error")
        return redirect(back)

    # ================================================================ workers
    @app.route("/staff/swap", methods=["POST"])
    @h.login_required
    def staff_swap():
        f = request.form
        try:
            if f.get("action") == "offer":
                store.offer_swap(h.user_id(), int(f.get("rota_id") or 0), int(f.get("to_staff") or 0))
                flash("Asked. Your colleague answers first, then your manager decides.", "success")
            else:
                store.answer_swap(h.user_id(), int(f.get("swap_id") or 0), f.get("action") == "yes")
                flash("Your answer is saved.", "success")
        except (ShopError, ValueError) as e:
            flash(str(e) if isinstance(e, ShopError) else "Please choose a shift and a colleague.", "error")
        return redirect(url_for("staff_home") + "#swaps")

    @app.route("/admin/staff/swaps", methods=["GET", "POST"])
    @h.admin_required
    def admin_swaps():
        if request.method == "POST":
            try:
                store.decide_swap(h.user_id(), int(request.form.get("swap_id") or 0), request.form.get("action") == "approve")
                flash("Decided.", "success")
            except (ShopError, ValueError) as e:
                flash(str(e), "error")
            return redirect(url_for("admin_swaps"))
        branch = store.branch_of(h.user_id())
        rows = store.swaps(open_only=False)
        return h.admin_page("admin/swaps.html", rows=[r for r in rows if branch is None or r["office_id"] in branch])

    @app.route("/staff/onboarding", methods=["POST"])
    @h.login_required
    def staff_onboarding():
        w = store.worker_of(h.user_id())
        try:
            sid = int(request.form.get("staff_id") or (w["id"] if w else 0))
            store.tick_onboarding(h.user_id(), sid, request.form.get("item", ""), request.form.get("done", "1") == "1")
        except (ShopError, ValueError) as e:
            flash(str(e), "error")
        return redirect(request.form.get("back") if str(request.form.get("back", "")).startswith("/") else url_for("staff_home") + "#first-days")

    # ================================================================ help desk
    @app.route("/support/<number>/typing", methods=["POST"])
    @h.login_required
    def support_typing(number):
        store.support_typing(number, h.user_id())
        return ("", 204)

    @app.route("/support/<number>/file", methods=["POST"])
    @h.login_required
    def support_file_add(number):
        staff = store.can(h.user_id(), "support.manage")
        try:
            if not store.rate_ok("upload", who()):
                raise ShopError("Too many uploads. Please wait a few minutes.")
            file = request.files.get("file")
            if not file or not file.filename:
                raise ShopError("Please choose a file.")
            store.add_support_file(h.user_id(), number, file.filename, file.mimetype, file.read(V9.MAX_FILE + 1))
            flash("The file was added.", "success")
        except ShopError as e:
            flash(str(e), "error")
        t = store.support_ticket(number)
        mine = t is not None and t["user_id"] == h.user_id()
        return redirect(url_for("support_ticket_page", number=number) if mine or not staff
                        else url_for("admin_support_ticket", number=number))

    @app.route("/support/<number>/file/<int:file_id>")
    @h.login_required
    def support_file_get(number, file_id):
        got = store.support_file(number, h.user_id(), file_id)
        if got is None:
            abort(404)
        name, mime, blob = got
        return Response(blob, mimetype=mime, headers={"Content-Disposition": f'inline; filename="{name}"',
                                                      "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"})

    @app.route("/support/<number>/rate", methods=["POST"])
    @h.login_required
    def support_rate(number):
        try:
            store.rate_support(h.user_id(), number, request.form.get("stars"), request.form.get("note", ""))
            flash("Thank you for your stars.", "success")
        except ShopError as e:
            flash(str(e), "error")
        return redirect(url_for("support_ticket_page", number=number))

    # ================================================================ customers
    @app.route("/account/pay-pref", methods=["POST"])
    @h.login_required
    def account_pay_pref():
        try:
            store.set_pay_pref(h.user_id(), request.form.get("method", ""))
            flash("Saved. This way to pay is chosen for you at the checkout.", "success")
        except ShopError as e:
            flash(str(e), "error")
        return redirect(url_for("account") + "#pay")

    @app.route("/orders/<number>/again", methods=["POST"])
    @h.login_required
    def order_again(number):
        try:
            added, missing = store.reorder(h.user_id(), number)
            if added:
                flash("The products are in your cart." + (" Not available now: " + ", ".join(m[:40] for m in missing[:3]) + "."
                                                           if missing else ""), "success")
                return redirect(url_for("cart"))
            flash("None of these products can be bought now.", "error")
        except ShopError as e:
            flash(str(e), "error")
        return redirect(url_for("order", number=number))

    @app.route("/orders/<number>/address", methods=["POST"])
    @h.login_required
    def order_address(number):
        try:
            store.change_order_address(h.user_id(), number, int(request.form.get("address_id") or 0))
            flash("The address of this order was changed.", "success")
        except (ShopError, ValueError) as e:
            flash(str(e) if isinstance(e, ShopError) else "Please choose an address.", "error")
        return redirect(url_for("order", number=number))

    # ================================================================ sellers
    @app.route("/seller/insights")
    @h.seller_required
    def seller_insights():
        shop = request.shop
        return render_template("seller/insights.html", shop=shop, i=store.seller_insights(shop["id"]))

    @app.route("/seller/import", methods=["GET", "POST"])
    @h.seller_required
    def seller_import():
        shop = request.shop
        result = None
        if request.method == "POST":
            try:
                if not store.rate_ok("upload", who()):
                    raise ShopError("Too many uploads. Please wait a few minutes.")
                file = request.files.get("file")
                raw = file.read(600_000) if file and file.filename else b""
                if not raw:
                    raise ShopError("Please choose a CSV file.")
                result = store.import_products_csv(shop["id"], raw.decode("utf-8", "replace"))
                flash(f"{result['added']} products were added. They are switched off until you add a picture and we approve them.",
                      "success" if result["added"] else "info")
            except ShopError as e:
                flash(str(e), "error")
        return render_template("seller/import.html", shop=shop, result=result, columns=V9.CSV_COLUMNS, max_rows=V9.CSV_MAX)

    # ================================================================ partners, limits, backups
    @app.route("/partners/api/check", methods=["POST"])
    def partner_check():
        if not store.rate_ok("search", who()):
            return render_template("too_many.html", what="checks", wait="one minute"), 429
        f = request.form
        res = store.check_signature(f.get("secret", "").strip(), f.get("header", "").strip(), f.get("body", ""))
        return render_template("partner_check.html", res=res, header=f.get("header", ""), body=f.get("body", ""))

    @app.route("/admin/monitor/limits")
    @h.admin_required
    def admin_limits():
        return h.admin_page("admin/limits.html", r=store.rate_report())

    @app.route("/admin/backups/check", methods=["POST"])
    @h.admin_required
    def admin_backup_check():
        try:
            bid = int(request.form.get("id") or 0) or store.save_backup("auto", "restore test")[0]
            res = store.check_backup(bid)
            flash(f"Restore test passed: {res['tables']} tables, {res['rows']} rows." if res["ok"]
                  else f"Restore test FAILED: {res['note']}", "success" if res["ok"] else "error")
        except (ShopError, ValueError) as e:
            flash(str(e), "error")
        return redirect(url_for("admin_backups"))
