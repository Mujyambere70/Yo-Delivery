"""
Yo Delivery - international online shopping & delivery website (Python + Flask).
Designed and developed by Jean Michel Mujyambere (github.com/Mujyambere70).

Run:   python app.py      then open http://127.0.0.1:5000
Demo:  demo@example.com  /  Demo@1234        Admin: admin@example.com / Admin@1234
       seller@example.com / Seller@1234 (seller centre)

Database: SQLite file yo_delivery.db on your computer, or PostgreSQL when DATABASE_URL is set
(needed online - free hosts like Render delete local files at every restart).
"""

import csv
import io
import os
import secrets
import time
from datetime import datetime
from functools import wraps
from types import SimpleNamespace

from flask import (Flask, Response, abort, flash, g, jsonify, redirect, render_template, request, send_from_directory,
                   session, url_for)

import admin_v3
import tickets_views
import world_views
import v5_views
import staff as staff_rules
import staff_views
import workforce_views
import addresscheck
import v6b_views
import v7_views
import v7b_views
import v6_views
import blog_views
import board_views
import courier_views
import demo_data
import i18n
import images
import otp
import jinja2
import pay_views
import rewards_views
import world
import yopay
from core import now
from aftersales import RETURN_REASONS
from sellers import SELLER_STATUSES
from store import (ACTIONS, CATEGORY_NAMES, STATES, TRACK_STEPS, ShopError, Store, delivery_date_text,
                   delivery_days, rupees, to_dt)

GST = {"electronics": 18, "fashion": 12, "footwear": 12, "watches": 18, "grocery": 5, "home": 18, "books": 0}
LANG_CURRENCY = {"en": "INR", "ru": "RUB", "fr": "EUR", "es": "EUR", "ar": "AED", "zh": "CNY", "rw": "RWF"}
DEVELOPER = {"name": "Jean Michel Mujyambere", "github": "https://github.com/Mujyambere70",
             "linkedin": "https://linkedin.com/in/jeanmichel", "email": "mujyamberejean9@gmail.com"}


def create_app(db_path=None, seed=True, notifier=None, driver=None, env=None, housekeeping=True):
    app = Flask(__name__)
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024        # pictures and backup files
    env = os.environ if env is None else env

    target = db_path or env.get("DATABASE_URL") or os.path.join(app.root_path, "yo_delivery.db")
    store = Store(target, driver=driver, notifier=notifier, env=env)
    # the key that signs login cookies must stay the same after a restart and for every worker
    app.config["SECRET_KEY"] = env.get("SECRET_KEY") or store.secret_key()
    if seed:
        demo_data.seed(store)
    app.store = store
    app.config["HOUSEKEEPING"] = housekeeping
    rate_cache = {"at": 0, "rates": {}}

    # ---------------- template helpers ----------------

    def rates():
        if time.time() - rate_cache["at"] > 600:              # read the rates at most every 10 minutes
            rate_cache.update(at=time.time(), rates=store.rates())
        return rate_cache["rates"]

    def order_money(amount, o):
        """An amount of an order, in the currency the customer paid in (the rate of the order day)."""
        cur = o.get("currency") or "INR"
        if cur == "INR":
            return world.format_money(amount, "INR")
        return world.format_money(world.convert(amount, cur, {cur: o.get("fx_rate") or 1, "INR": 1.0}), cur)

    # Filters that depend on the visitor (language, currency) get the template context, so that Jinja does not
    # compute them once at compile time for constant values like {{ 10000|rupees }} and reuse them for everyone.
    def live(fn):
        return jinja2.pass_context(lambda _ctx, *args: fn(*args))
    app.jinja_env.filters["rupees"] = live(lambda paise: world.price(paise))      # shop prices: visitor's currency
    app.jinja_env.filters["inr"] = live(lambda paise: world.format_money(paise, "INR"))  # back office: rupees
    app.jinja_env.filters["omoney"] = live(order_money)
    app.jinja_env.filters["money"] = live(lambda amount, cur="INR": world.format_money(amount, cur))
    app.jinja_env.filters["tr"] = live(lambda text: i18n.translate_text(text))
    app.jinja_env.filters["country"] = live(lambda code: world.country_name(code, world.current_lang()))

    @app.template_filter("nicedate")
    def nicedate(value, fmt="%d %b %Y"):
        if not value:
            return ""
        return world.format_when(value, with_time="%H" in fmt or "%I" in fmt)
    app.jinja_env.filters["nicedate"] = live(nicedate)

    def localtime(value, fmt="%d %b %Y, %H:%M"):
        """A time that is already local (a departure in the time of its city): never converted."""
        if not value:
            return ""
        return world.format_when(value, with_time="%H" in fmt or "%I" in fmt, convert=False)
    app.jinja_env.filters["localtime"] = live(localtime)

    def coupon_text(c):
        """Coupon description in the visitor's currency, built from the coupon's numbers
        (the page translator turns the English sentence into the visitor's language)."""
        low = world.price(c["min_order"] * 100)
        if c["kind"] == "flat":
            text = f"{world.price(c['amount'] * 100)} off on orders above {low}"
        else:
            text = f"{c['amount']}% off up to {world.price(c['max_off'] * 100)} on orders above {low}"
        return text + (", first order only" if c["first_order_only"] else "")

    def csrf_token():
        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_hex(16)
        return session["csrf_token"]

    def picture(name):
        """Address of a picture: uploaded ones live in the database, built-in ones in static/images."""
        name = str(name or "")
        return url_for("img", name=name[7:] if name.startswith("images/") else name)

    app.jinja_env.globals.update(csrf_token=csrf_token, TRACK_STEPS=TRACK_STEPS, GST=GST, picture=picture,
                                 delivery_date=delivery_date_text, LANGUAGES=world.LANGUAGES,
                                 CURRENCIES=world.CURRENCIES, MAIN_CURRENCIES=world.MAIN_CURRENCIES,
                                 DEVELOPER=DEVELOPER,
                                 currency_name=world.currency_name, now_year=datetime.now().year)

    def user_id():
        return session.get("uid")

    def L(items):
        """Product names and descriptions in the visitor's language when the seller wrote them (version 5)."""
        try:
            return store.localize_products(list(items))
        except Exception:
            return list(items)

    def visitor():
        if "visitor" not in session:
            session["visitor"] = secrets.token_hex(8)
        return session["visitor"]

    def get_cart():
        if user_id():
            return store.get_cart(user_id())
        return {int(k): v for k, v in session.get("cart", {}).items()}

    def save_cart(cart):
        if user_id():
            store.save_cart(user_id(), cart)
        else:
            session["cart"] = {str(k): v for k, v in cart.items()}

    def is_back_office():
        return (request.path.startswith("/admin")) and request.endpoint != "static"

    @app.context_processor
    def header_data():
        uid = user_id()
        user = store.get_user(uid) if uid else None
        if uid and user is None:          # account removed
            session.clear()
        address = store.default_address(uid) if user else None
        settings = store.settings()
        lang = world.current_lang()
        return {"user": user, "cart_count": sum(get_cart().values()),
                "wish_ids": store.wishlist_ids(uid) if user else set(), "deliver_to": address,
                # these come from the database, so the admin can change them
                "CATEGORIES": [(c["slug"], c["name"], c["image"]) for c in store.categories()],
                "COUPONS": {c["code"]: (coupon_text(c),) for c in store.coupons(active_only=True)},
                "RATES": world.current_rates(),
                "FREE_DELIVERY_FROM": settings["free_delivery_from"], "SETTINGS": settings,
                "LANG": lang, "RTL": lang in world.RTL_LANGUAGES, "CURRENCY": world.current_currency(),
                "my_shop": store.seller_membership(uid)[0] if user else None,
                "my_courier": store.courier_of(uid) if user else None,
                "my_staff": store.staff_of(uid) if user else None,
                "my_work": store.worker_of(uid) if user else None,
                "can": (lambda ability: store.can(uid, ability)) if user else (lambda ability: False),
                "unseen": store.unseen_count(uid) if user else 0}

    # ---------------- language, currency, housekeeping, security ----------------

    @app.before_request
    def language_and_currency():
        """Language: chosen by the visitor, else the customer's saved one, else the browser's, else English.
        Currency: chosen, else saved, else the usual currency of the language. The back office is English + ₹."""
        if is_back_office():
            world.set_context(lang="en", currency="INR", rates={})
            return
        u = store.get_user(user_id()) if user_id() else None
        lang = session.get("lang") or (u["lang"] if u and u["lang"] != "en" else None)
        if lang not in world.LANGUAGES:
            lang = request.accept_languages.best_match(list(world.LANGUAGES)) or "en"
        region = world.country_from_languages(request.headers.get("Accept-Language", ""))
        cur = (session.get("currency") or (u["currency"] if u and u["currency"] else None)
               or (world.default_currency(u["country"], lang) if u else None)          # the home currency
               or (world.default_currency(region, lang) if region else LANG_CURRENCY[lang]))
        today = rates()
        if cur != "INR" and not today.get(cur):           # no exchange rate yet: show and charge rupees, honestly
            cur = "INR"
        world.set_context(lang=lang, currency=cur, rates=today, tz=request.cookies.get("tz", ""))

    @app.after_request
    def translate(resp):
        lang = world.current_lang()
        if lang != "en" and resp.mimetype == "text/html" and not resp.direct_passthrough:
            page = i18n.translate_html(resp.get_data(as_text=True), lang)
            if lang in world.RTL_LANGUAGES:
                page = page.replace('<html lang="en">', f'<html lang="{lang}" dir="rtl">', 1)
            else:
                page = page.replace('<html lang="en">', f'<html lang="{lang}">', 1)
            resp.set_data(page)
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "same-origin"
        # version 7 (security check): the browser features the pages may use, and HTTPS only once it is used
        resp.headers["Permissions-Policy"] = "camera=(self), geolocation=(self), microphone=(), payment=(self)"
        resp.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        if request.is_secure or request.headers.get("X-Forwarded-Proto") == "https":
            resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return resp

    @app.before_request
    def jobs():
        if app.config["HOUSEKEEPING"] and request.endpoint not in ("static", "img"):
            try:
                store.housekeeping()
            except Exception as e:                      # never block the shop because of this
                app.logger.warning("housekeeping failed: %s", e)

    # version 6: the numbers of every request (monitoring page, alerts)
    @app.before_request
    def start_timer():
        g.t0 = time.time()

    @app.after_request
    def count_request(resp):
        if request.endpoint not in ("static", "img", "health") and hasattr(g, "t0"):
            store.record_request((time.time() - g.t0) * 1000, resp.status_code)
        return resp

    @app.teardown_request
    def count_crash(exc):
        if exc is not None and hasattr(g, "t0"):
            store.record_request((time.time() - g.t0) * 1000, 500)

    @app.route("/healthz")
    def health():
        """For Fly.io and uptime services: 200 when the database and the places file answer."""
        h = store.health()
        return jsonify(h), (200 if h["ok"] else 503)

    @app.before_request
    def check_csrf():
        if user_id():                       # a blocked account is signed out immediately
            u = store.get_user(user_id())
            if u is None or u["blocked"]:
                session.clear()
                flash("Your account has been blocked. Please contact customer support.", "error")
                return redirect(url_for("index"))
        if request.method == "POST" and request.endpoint not in ("stripe_webhook", "push_subscribe", "payment_callback",
                                                                 "partner_events_api"):
            sent = request.form.get("csrf_token", "")
            if not sent or not secrets.compare_digest(sent, session.get("csrf_token", "")):
                abort(400)

    def login_required(view):
        @wraps(view)
        def wrapper(*a, **kw):
            if not user_id():
                flash("Please sign in to continue.", "info")
                return redirect(url_for("login", next=request.full_path))
            return view(*a, **kw)
        return wrapper

    def act(action, details="", uid=None):
        """Write one line to the activity log (seen by the admin)."""
        store.log(uid if uid is not None else user_id(), action, details,
                  request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip())

    def admin_required(view):
        @wraps(view)
        def wrapper(*a, **kw):
            if not user_id():
                flash("Please sign in with an admin account.", "info")
                return redirect(url_for("login", next=request.full_path))
            u = store.get_user(user_id())
            if not u:
                abort(403)
            if not u["is_admin"]:
                if store.setting("staff_need_2fa") and not u["totp_enabled"] and not request.path.startswith(
                        "/admin/account"):            # version 6: the shop can ask every worker for 2-step sign-in
                    flash("Please turn on two-step sign-in (an app code) before you open the admin pages.", "info")
                    return redirect(url_for("admin_account"))
                # version 6: workers open the admin pages their abilities allow (staff.py)
                need = staff_rules.ability_for_path(request.path, request.method)
                if need is None or not store.can(u["id"], need):
                    if request.path.rstrip("/") == "/admin" and store.worker_of(u["id"]):
                        return redirect(url_for("staff_home"))
                    abort(403)
            resp = view(*a, **kw)
            if request.method == "POST":
                try:
                    store.log_staff_action(u["id"], request.path, sorted(k for k in request.form if k != "csrf_token"),
                                           getattr(resp, "status_code", 200))
                except Exception:                      # the log must never break the page
                    pass
            return resp
        return wrapper

    def seller_required(view):
        @wraps(view)
        def wrapper(*a, **kw):
            if not user_id():
                flash("Please sign in to open your Seller Centre.", "info")
                return redirect(url_for("login", next=request.full_path))
            shop, abilities = store.seller_membership(user_id())
            if not shop:
                return redirect(url_for("sell"))
            need = staff_rules.seller_ability_for_path(request.path)   # employees of a shop (version 6)
            if need and need not in abilities:
                flash("The owner of the shop has not given you this. Ask him to tick it on the Team page.", "info")
                return redirect(url_for("seller_dashboard"))
            request.shop_abilities = abilities
            if shop["status"] != "Approved" and request.endpoint not in ("seller_dashboard",):
                flash("Your shop is not approved, so this page is not available yet.", "info")
                return redirect(url_for("seller_dashboard"))
            request.shop = shop
            return view(*a, **kw)
        return wrapper

    def safe_next(default="index"):
        nxt = request.values.get("next", "")
        return nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for(default)

    def base_url():
        return request.url_root if request.url_root.endswith("/") else request.url_root + "/"

    # ======================================================
    # Language, currency, pictures, app files
    # ======================================================

    @app.route("/lang/<code>")
    def set_language(code):
        if code in world.LANGUAGES:
            session["lang"] = code
            session.pop("currency", None) if code == "ru" else None     # Russian -> rubles
            if code == "ru":
                session["currency"] = "RUB"
            if user_id():
                store.set_preferences(user_id(), lang=code, currency="RUB" if code == "ru" else None)
        return redirect(safe_next())

    @app.route("/currency", defaults={"code": None})
    @app.route("/currency/<code>")
    def set_currency(code):
        code = (code or request.args.get("code", "")).upper()
        if code in world.CURRENCIES:
            session["currency"] = code
            if user_id():
                store.set_preferences(user_id(), currency=code)
        return redirect(safe_next())

    @app.route("/img/<path:name>")
    def img(name):
        if name.startswith("private/") or ".." in name:       # courier and delivery photos: admins only
            abort(404)
        found = store.media(name)
        if found:
            mime, blob, when = found
            etag = f'"{abs(hash((name, when)))}"'
            if request.headers.get("If-None-Match") == etag:
                return Response(status=304)
            return Response(blob, mimetype=mime, headers={"Cache-Control": "public, max-age=3600", "ETag": etag})
        return send_from_directory(os.path.join(app.static_folder, "images"), name, max_age=86400)

    @app.route("/sw.js")
    def service_worker():
        return send_from_directory(app.static_folder, "sw.js", mimetype="application/javascript", max_age=0)

    # ======================================================
    # Shopping pages
    # ======================================================

    @app.route("/")
    def index():
        recent = store.recently_viewed(visitor(), user_id(), 8)
        return render_template(
            "index.html",
            deals=L(store.products(order="deals", limit=8)),
            electronics=L(store.products(category="electronics", limit=8)),
            fashion=L(store.products(category="fashion", limit=8) + store.products(category="footwear", limit=4)),
            grocery=L(store.products(category="grocery", limit=8)),
            home_books=L(store.products(category="home") + store.products(category="books")),
            new_arrivals=L(store.products(order="new", limit=10)),
            for_you=L(store.recommended(visitor(), user_id(), 10)) if recent or user_id() else [],
            recent=recent)

    def listing(category=""):
        a = request.args
        q = a.get("q", "").strip()[:80]

        def num(key):
            try:
                return float(a.get(key)) if a.get(key) else None
            except ValueError:
                return None
        brands = a.getlist("brand")
        sort = a.get("sort", "relevance")
        results = store.search(q=q, category=category or a.get("category", ""), brands=brands,
                               min_price=num("min"), max_price=num("max"), min_rating=num("rating") or 0,
                               min_discount=num("discount") or 0, sort=sort)
        cat = category or a.get("category", "")
        return render_template("listing.html", results=L(results), q=q, category=cat,
                               category_name=CATEGORY_NAMES.get(cat, ""),
                               brands=store.brands(cat), chosen_brands=brands, sort=sort, args=a)

    @app.route("/search")
    def search():
        return listing()

    @app.route("/c/<category>")
    def category(category):
        if category not in CATEGORY_NAMES:
            abort(404)
        return listing(category)

    @app.route("/p/<slug>")
    def product(slug):
        try:
            p = store.get_product(slug=slug)
        except ShopError:
            abort(404)
        u = store.get_user(user_id()) if user_id() else None
        shop = store.seller_of(user_id()) if u else None
        own = shop and p.seller_id == shop["id"]
        if (not p.active or not p.approved) and not (u and u["is_admin"]) and not own:
            abort(404)                      # hidden, or a seller's product waiting for approval
        store.record_view(visitor(), user_id(), p.id)

        address = store.default_address(user_id()) if user_id() else None
        country = request.args.get("country") or (address["country"] if address else "IN")
        country = country if country in world.DIAL_CODES else "IN"
        pin = request.args.get("pin", "").strip()
        if not pin and address and address["country"] == country:
            pin = address["pincode"]
        delivery = None
        if country != "IN":
            delivery = {"country": country, "date": delivery_date_text("", country=country),
                        "days": delivery_days("", country), "fee": store.shipping_fee(p.price, country)}
        elif pin:
            if world.clean_postcode("IN", pin):
                delivery = {"pin": pin, "country": "IN", "date": delivery_date_text(pin), "days": delivery_days(pin)}
            else:
                delivery = {"pin": pin, "country": "IN", "error": "Please enter a valid 6-digit pincode."}
        # version 7: an A/B test - group A sees the mix of version 6, group B only the text + picture neighbours
        ab_unit = str(user_id() or visitor())
        ab = store.ab_variant("similar_products", ab_unit)
        if request.args.get("rec") in ("A", "B"):             # the visitor came here by clicking a similar product
            store.ab_event("similar_products", ab_unit, request.args["rec"], "click", p.id)
        picked = store.content_similar(p.id, 8) if ab == "B" else store.similar_products(p.id, 8)
        similar = L(picked or [x for x in store.products(category=p.category) if x.id != p.id][:8])
        if ab and picked:
            store.ab_event("similar_products", ab_unit, ab, "view", p.id)
        sold_by = store.shop_names().get(p.seller_id) if p.seller_id else None
        L([p])
        return render_template("product.html", p=p, reviews=store.reviews(p.id), breakdown=store.rating_breakdown(p),
                               delivery=delivery, similar=similar, country=country,
                               countries=world.countries(world.current_lang()),
                               together=L(store.bought_together(p.id, 4)), also_viewed=L(store.also_viewed(p.id, 8)),
                               sold_by=sold_by, can_review=user_id() and store.can_review(user_id(), p.id),
                               learned=L(store.learned_similar(p.id, 8)), ab=ab if picked else None)

    @app.route("/s/<slug>")
    def storefront(slug):
        try:
            shop = store.seller(slug=slug)
        except ShopError:
            abort(404)
        if shop["status"] != "Approved":
            abort(404)
        items = [p for p in store.seller_products(shop["id"]) if p.active and p.approved]
        return render_template("storefront.html", shop=shop, items=items)

    # ======================================================
    # Cart
    # ======================================================

    @app.route("/cart")
    def cart():
        items = store.cart_items(get_cart())
        address = store.default_address(user_id()) if user_id() else None
        summary = store.price_summary(items, session.get("coupon", ""), user_id(), address["country"] if address else "IN")
        if session.get("coupon") and summary["coupon_error"]:
            flash(summary["coupon_error"], "error")
            session.pop("coupon", None)
        suggestions = store.recommended(visitor(), user_id(), 8, exclude={p.id for p, _ in items}) if items else []
        return render_template("cart.html", items=items, s=summary, suggestions=suggestions)

    @app.route("/cart/add", methods=["POST"])
    def cart_add():
        pid = request.form.get("product_id", type=int)
        try:
            p = store.get_product(pid=pid)
            cart = get_cart()
            cart = store.cart_set(cart, pid, cart.get(pid, 0) + request.form.get("qty", 1, type=int))
            save_cart(cart)
        except ShopError as e:
            flash(str(e), "error")
            return redirect(safe_next())
        act("Added to cart", f"{p.name[:70]} (qty {request.form.get('qty', 1)})")
        if request.form.get("buy_now"):
            return redirect(url_for("checkout"))
        flash(f"Added to cart: {p.name[:60]}", "success")
        return redirect(safe_next("cart"))

    @app.route("/cart/update", methods=["POST"])
    def cart_update():
        try:
            save_cart(store.cart_set(get_cart(), request.form.get("product_id", type=int),
                                     request.form.get("qty", 0, type=int)))
        except ShopError as e:
            flash(str(e), "error")
        return redirect(url_for("cart"))

    @app.route("/cart/save-for-later", methods=["POST"])
    @login_required
    def save_for_later():
        pid = request.form.get("product_id", type=int)
        cart = get_cart()
        cart.pop(pid, None)
        save_cart(cart)
        if pid not in store.wishlist_ids(user_id()):
            store.toggle_wishlist(user_id(), pid)
        flash("Moved to your wishlist.", "success")
        return redirect(url_for("cart"))

    @app.route("/cart/coupon", methods=["POST"])
    def cart_coupon():
        code = request.form.get("code", "").strip().upper()
        if request.form.get("remove") or not code:
            session.pop("coupon", None)
            return redirect(url_for("checkout") if request.form.get("back") == "checkout" else url_for("cart"))
        items = store.cart_items(get_cart())
        s = store.price_summary(items, code, user_id())
        if s["coupon_error"]:
            flash(s["coupon_error"], "error")
        else:
            session["coupon"] = code
            flash(f"Coupon {code} applied! You saved {world.price(s['coupon_discount'])}.", "success")
        return redirect(url_for("checkout") if request.form.get("back") == "checkout" else url_for("cart"))

    # ======================================================
    # Checkout, payment & orders
    # ======================================================

    @app.route("/checkout", methods=["GET", "POST"])
    @login_required
    def checkout():
        uid = user_id()
        items = store.cart_items(get_cart())
        if not items:
            flash("Your cart is empty.", "info")
            return redirect(url_for("cart"))
        if request.method == "POST":
            payment = request.form.get("payment", "")
            try:
                emi = request.form.get("emi", "0")
                number = store.place_order(uid, request.form.get("address_id"), payment, session.get("coupon", ""),
                                           request.form.get("upi_id", ""), currency=world.current_currency(),
                                           rates=rates(), emi_months=int(emi) if emi.isdigit() else 0)
            except ShopError as e:
                flash(str(e), "error")
                return redirect(url_for("checkout", address=request.form.get("address_id", "")))
            session.pop("coupon", None)
            o = store.get_order(uid, number)
            act("Order placed", f"{number} - {len(o['items'])} item(s) - {o['payment_method']} - {rupees(o['total'])}")
            if payment == "card":
                return redirect(url_for("pay_order", number=number))
            return redirect(url_for("order_success", number=number))
        addresses = store.addresses(uid)
        chosen = next((a for a in addresses if str(a["id"]) == request.args.get("address")), addresses[0] if addresses else None)
        country = chosen["country"] if chosen else "IN"
        summary = store.price_summary(items, session.get("coupon", ""), uid, country)
        return render_template("checkout.html", items=items, s=summary, addresses=addresses, chosen=chosen,
                               states=STATES, wallet=store.get_user(uid)["wallet"], country=country,
                               countries=world.countries(world.current_lang()), dial=world.DIAL_CODES,
                               gateway=store.gateway(), emi_plans=yopay.emi_plans(summary["total"]))

    @app.route("/pay/<number>", methods=["GET", "POST"])
    @login_required
    def pay_order(number):
        """Start paying a card order: Stripe test mode when configured, else the built-in test gateway."""
        try:
            o = store.get_order(user_id(), number)
        except ShopError:
            abort(404)
        if o["payment_status"] == "Paid" or o["payment_method"] != "Card" or o["cancelled_at"]:
            return redirect(url_for("order", number=number))
        if store.gateway() == "stripe":
            try:
                return redirect(store.start_card_payment(user_id(), number, base_url()))
            except ShopError as e:
                flash(str(e), "error")
                return redirect(url_for("order", number=number))
        error = None
        if request.method == "POST":
            f = request.form
            try:
                ok, message = store.pay_with_test_card(user_id(), number, f.get("card", ""), f.get("expiry", ""),
                                                       f.get("cvc", ""), f.get("name", ""))
                if ok:
                    act("Payment", f"{number} paid by test card")
                    return redirect(url_for("order_success", number=number))
                error = message
                act("Payment", f"{number} card declined")
            except ShopError as e:
                error = str(e)
        return render_template("pay.html", o=o, error=error), (402 if error else 200)

    @app.route("/pay/<number>/return")
    @login_required
    def stripe_return(number):
        try:
            store.get_order(user_id(), number)
            paid = store.finish_stripe(number, request.args.get("session_id", ""))
        except ShopError as e:
            flash(str(e), "error")
            return redirect(url_for("order", number=number))
        if paid:
            act("Payment", f"{number} paid via Stripe (test mode)")
            return redirect(url_for("order_success", number=number))
        flash("The payment is not finished yet. You can try again.", "info")
        return redirect(url_for("order", number=number))

    @app.route("/payments/stripe/webhook", methods=["POST"])
    def stripe_webhook():
        try:
            store.stripe_webhook(request.get_data(), request.headers.get("Stripe-Signature", ""))
        except ShopError:
            return jsonify({"ok": False}), 400
        return jsonify({"ok": True})

    @app.route("/order/<number>/success")
    @login_required
    def order_success(number):
        try:
            o = store.get_order(user_id(), number)
        except ShopError:
            abort(404)
        return render_template("order_success.html", o=o)

    @app.route("/orders")
    @login_required
    def orders():
        return render_template("orders.html", orders=store.orders(user_id()))

    @app.route("/orders/<number>")
    @login_required
    def order(number):
        try:
            o = store.get_order(user_id(), number)
        except ShopError:
            abort(404)
        if request.args.get("payment") == "cancelled":
            flash("The payment was cancelled. You can try again within 30 minutes.", "info")
        reviewable = {it["product_id"] for it in o["items"] if store.can_review(user_id(), it["product_id"])}
        can_return, last_day = store.return_window(o)
        return render_template("order.html", o=o, steps=store.tracking(o), reviewable=reviewable,
                               ret=store.order_return(o["id"]), can_return=can_return, last_day=last_day,
                               reasons=RETURN_REASONS)

    @app.route("/orders/<number>/cancel", methods=["POST"])
    @login_required
    def cancel(number):
        try:
            o = store.cancel_order(user_id(), number)
            act("Order cancelled", f"{number} - {rupees(o['total'])}")
            if o["payment_method"] == "Card" and o["payment_status"] == "Paid":
                extra = " The money goes back to your card."
            elif o["payment_method"] in ("Yo Pay Wallet", "UPI"):
                extra = " The amount was refunded to your Yo Pay wallet."
            else:
                extra = ""
            flash("Your order was cancelled." + extra, "success")
        except ShopError as e:
            flash(str(e), "error")
        return redirect(url_for("order", number=number))

    @app.route("/orders/<number>/return", methods=["POST"])
    @login_required
    def request_return(number):
        try:
            store.request_return(user_id(), number, request.form.get("reason", ""), request.form.get("details", ""))
            act("Return requested", f"{number} - {request.form.get('reason', '')}")
            flash("Your return request was sent. We will tell you when it is checked.", "success")
        except ShopError as e:
            flash(str(e), "error")
        return redirect(url_for("order", number=number))

    @app.route("/orders/<number>/invoice")
    @login_required
    def invoice(number):
        try:
            o = store.get_order(user_id(), number)
        except ShopError:
            abort(404)
        lines = []
        export = o["international"]
        for it in o["items"]:
            cat = store.get_product(pid=it["product_id"]).category
            rate = 0 if export else GST.get(cat, 18)            # exports are zero-rated for GST
            gross = it["price"] * it["qty"]
            taxable = round(gross * 100 / (100 + rate))
            lines.append({**it, "rate": rate, "gross": gross, "taxable": taxable, "tax": gross - taxable})
        return render_template("invoice.html", o=o, lines=lines, user=store.get_user(user_id()), export=export)

    @app.route("/track", methods=["GET", "POST"])
    def track():
        """Track a parcel without signing in: order number + email, or the tracking number."""
        found, form = None, request.values
        if form.get("number") or form.get("awb"):
            with store.db() as conn:
                if form.get("awb"):
                    row = conn.execute("SELECT number, user_id FROM orders WHERE awb = ?",
                                       (form.get("awb", "").strip().upper(),)).fetchone()
                else:
                    row = conn.execute("SELECT o.number, o.user_id FROM orders o JOIN users u ON u.id = o.user_id "
                                       "WHERE o.number = ? AND u.email = ?", (form.get("number", "").strip().upper(),
                                                                              form.get("email", "").strip().lower())
                                       ).fetchone()
            if row:
                o = store.get_order(row["user_id"], row["number"])
                found = {"o": o, "steps": store.tracking(o)}
            else:
                flash("No parcel found. Please check the numbers (and the email of the order).", "error")
        return render_template("track.html", found=found, form=form)

    # ======================================================
    # Reviews & wishlist
    # ======================================================

    @app.route("/p/<slug>/review", methods=["GET", "POST"])
    @login_required
    def review(slug):
        try:
            p = store.get_product(slug=slug)
        except ShopError:
            abort(404)
        if not store.can_review(user_id(), p.id):
            flash("You can review this product after it is delivered to you.", "info")
            return redirect(url_for("product", slug=slug))
        form = request.form if request.method == "POST" else {}
        if request.method == "POST":
            try:
                store.add_review(user_id(), p.id, form.get("rating"), form.get("title", ""), form.get("body", ""))
            except ShopError as e:
                flash(str(e), "error")
                return render_template("review.html", p=p, form=form), 400
            act("Review posted", f"{form.get('rating')} stars - {p.name[:60]}")
            flash("Thank you! Your review was published.", "success")
            return redirect(url_for("product", slug=slug) + "#reviews")
        return render_template("review.html", p=p, form=form)

    @app.route("/wishlist")
    @login_required
    def wishlist():
        return render_template("wishlist.html", items=store.wishlist(user_id()))

    @app.route("/wishlist/toggle", methods=["POST"])
    def wishlist_toggle():
        if not user_id():
            flash("Please sign in to save items to your wishlist.", "info")
            return redirect(url_for("login", next=request.form.get("next", "/")))
        try:
            added = store.toggle_wishlist(user_id(), request.form.get("product_id", type=int))
            flash("Saved to your wishlist." if added else "Removed from your wishlist.", "success")
        except ShopError as e:
            flash(str(e), "error")
        return redirect(safe_next("wishlist"))

    # ======================================================
    # Account
    # ======================================================

    @app.route("/signup", methods=["GET", "POST"])
    def signup():
        lang = world.current_lang()
        form = request.form if request.method == "POST" else {"country": "RU" if lang == "ru" else "IN"}
        if request.method == "POST":
            try:
                if request.form.get("password") != request.form.get("password2"):
                    raise ShopError("The two passwords do not match.")
                uid = store.register(form.get("name", ""), form.get("email", ""), form.get("mobile", ""),
                                     form.get("password", ""), country=form.get("country", "IN"), lang=lang)
            except ShopError as e:
                flash(str(e), "error")
                return render_template("signup.html", form=form, countries=world.countries(lang),
                                       dial=world.DIAL_CODES), 400
            start_session(uid)
            act("Signed up", "New account with email " + form.get("email", "").strip().lower())
            flash(f"Welcome to Yo Delivery! {world.price(store.setting('signup_wallet'))} demo balance was added "
                  f"to your Yo Pay wallet.", "success")
            return redirect(safe_next())
        return render_template("signup.html", form=form, countries=world.countries(lang), dial=world.DIAL_CODES)

    def start_session(uid):
        guest_cart = {int(k): v for k, v in session.get("cart", {}).items()}
        keep = {k: session[k] for k in ("visitor", "csrf_token", "lang", "currency") if k in session}
        session.clear()
        session.update(uid=uid, **keep)
        store.link_visitor(visitor(), uid)
        if "lang" in keep or "currency" in keep:          # what was chosen before signing in is remembered
            store.set_preferences(uid, keep.get("lang"), keep.get("currency"))
        if guest_cart:                                  # move guest cart into the account
            cart = store.get_cart(uid)
            for pid, q in guest_cart.items():
                try:
                    cart = store.cart_set(cart, pid, cart.get(pid, 0) + q)
                except ShopError:
                    pass
            store.save_cart(uid, cart)

    def after_login(u):
        start_session(u["id"])
        act("Logged in", "Admin" if u.get("is_admin") else "")
        if u.get("is_admin") and not request.values.get("next"):
            flash(f"Welcome, {u['name'].split()[0]}! You are signed in as admin.", "success")
            return redirect(url_for("admin_dashboard"))
        w = store.worker_of(u["id"])
        if w and w["status"] == "Active" and not request.values.get("next"):
            flash(f"Welcome, {u['name'].split()[0]}! Here is your work for today.", "success")
            return redirect(url_for("staff_home"))
        flash(f"Welcome back, {u['name'].split()[0]}!", "success")
        return redirect(safe_next())

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if user_id():
            return redirect(url_for("index"))
        if request.method == "POST":
            who, ip = request.form.get("login", ""), (request.access_route or [request.remote_addr or ""])[0]
            try:
                store.login_allowed(who, ip)            # version 5: at most 5 wrong passwords in 15 minutes
            except ShopError as e:
                flash(str(e), "error")
                return render_template("login.html", login=who), 429
            try:
                u = store.login(who, request.form.get("password", ""))
            except ShopError as e:
                store.login_failed(who, ip)
                act("Login failed", "Wrong password or unknown account: " + who[:60], uid=0)
                flash(str(e), "error")
                return render_template("login.html", login=who), 401
            store.login_ok(who)
            if u.get("totp_enabled"):                    # second step: code from the authenticator app
                session.update(pending_uid=u["id"], pending_at=time.time(), pending_tries=0,
                               pending_next=request.values.get("next", ""))
                return redirect(url_for("login_verify"))
            return after_login(u)
        return render_template("login.html", login="")

    @app.route("/login/verify", methods=["GET", "POST"])
    def login_verify():
        uid = session.get("pending_uid")
        if not uid or time.time() - session.get("pending_at", 0) > 300:
            session.pop("pending_uid", None)
            flash("Please sign in again.", "info")
            return redirect(url_for("login"))
        if request.method == "POST":
            try:
                store.verify_2fa(uid, request.form.get("code", ""))
            except ShopError as e:
                session["pending_tries"] = session.get("pending_tries", 0) + 1
                if session["pending_tries"] >= 5:
                    session.pop("pending_uid", None)
                    flash("Too many wrong codes. Please sign in again.", "error")
                    return redirect(url_for("login"))
                flash(str(e), "error")
                return render_template("verify.html"), 401
            nxt = session.get("pending_next", "")
            for k in ("pending_uid", "pending_at", "pending_tries", "pending_next"):
                session.pop(k, None)
            resp = after_login(store.get_user(uid))
            return redirect(nxt) if nxt.startswith("/") and not nxt.startswith("//") else resp
        return render_template("verify.html")

    @app.route("/forgot", methods=["GET", "POST"])
    def forgot():
        if request.method == "POST":
            try:
                sent = store.start_password_reset(request.form.get("email", ""), base_url())
            except Exception:
                sent = False
            if sent:
                act("Password reset", "Reset link sent to " + request.form.get("email", "").strip().lower()[:60], uid=0)
            # the same answer for every email, so nobody can find out which emails have accounts
            flash("If this email belongs to an account, we sent a link to choose a new password. It works for 30 "
                  "minutes.", "info")
            return redirect(url_for("login"))
        return render_template("forgot.html", email_ready=bool(store.notifier.email_provider()))

    @app.route("/reset/<token>", methods=["GET", "POST"])
    def reset(token):
        if request.method == "POST":
            try:
                if request.form.get("password") != request.form.get("password2"):
                    raise ShopError("The two passwords do not match.")
                uid = store.reset_password(token, request.form.get("password", ""))
            except ShopError as e:
                flash(str(e), "error")
                return render_template("reset.html", ok=store.reset_token_ok(token)), 400
            act("Password reset", "New password set with an email link", uid=uid)
            flash("Your new password is saved. Please sign in.", "success")
            return redirect(url_for("login"))
        return render_template("reset.html", ok=store.reset_token_ok(token))

    @app.route("/logout")
    def logout():
        if user_id():
            act("Logged out")
        keep = {k: session[k] for k in ("lang", "currency") if k in session}
        session.clear()
        session.update(keep)
        flash("You have been signed out.", "success")
        return redirect(url_for("index"))

    def totp_context(prefix):
        secret = session.get(prefix + "totp")
        u = store.get_user(user_id())
        return {"new_secret": secret if secret and not u["totp_enabled"] else None,
                "new_secret_grouped": otp.grouped(secret) if secret else "",
                "otp_uri": otp.uri(secret, u["email"], "Yo Delivery") if secret else ""}

    def handle_security_form(f):
        """2FA, notification and preference actions shared by My Account and the admin account page."""
        action = f.get("action")
        if action == "2fa_start":
            session["new_totp"] = otp.new_secret()
            return "#two-factor"
        if action == "2fa_confirm":
            store.enable_2fa(user_id(), session.get("new_totp", ""), f.get("code", ""))
            session.pop("new_totp", None)
            flash("Two-factor login is ON. From now on you also need the code from your app to sign in.", "success")
            return "#two-factor"
        if action == "2fa_disable":
            store.disable_2fa(user_id(), f.get("password", ""), f.get("code", ""))
            flash("Two-factor login is OFF.", "success")
            return "#two-factor"
        if action == "notify":
            store.set_notify_prefs(user_id(), f.get("email"), f.get("sms"))
            flash("Notification settings saved.", "success")
            return "#notify"
        if action == "prefs":
            lang, cur = f.get("lang"), f.get("currency")
            store.set_preferences(user_id(), lang, cur)
            if lang in world.LANGUAGES:
                session["lang"] = lang
            if cur in world.CURRENCIES:
                session["currency"] = cur
            flash("Language and currency saved.", "success")
            return "#prefs"
        if action == "push_remove":
            store.remove_push_subscription(user_id(), f.get("sub_id", type=int))
            flash("This device will no longer get notifications.", "success")
            return "#notify"
        return None

    @app.route("/account", methods=["GET", "POST"])
    @login_required
    def account():
        if request.method == "POST":
            f = request.form
            try:
                anchor = handle_security_form(f)
                if anchor is not None:
                    return redirect(url_for("account") + anchor)
                if f.get("action") == "password":
                    if f.get("new_password") != f.get("new_password2"):
                        raise ShopError("The two new passwords do not match.")
                    store.change_password(user_id(), f.get("old_password", ""), f.get("new_password", ""))
                    act("Password changed")
                    flash("Your password was changed.", "success")
                else:
                    store.update_user(user_id(), f.get("name", ""), f.get("email", ""), f.get("mobile", ""),
                                      f.get("country"))
                    act("Profile updated")
                    flash("Your profile was updated.", "success")
            except ShopError as e:
                flash(str(e), "error")
            return redirect(url_for("account"))
        return render_template("account.html", orders_count=len(store.orders(user_id())),
                               countries=world.countries(world.current_lang()), dial=world.DIAL_CODES,
                               devices=store.push_subscriptions(user_id()), providers=store.notifier.status(),
                               **totp_context("new_"))

    @app.route("/account/notifications")
    @login_required
    def notifications():
        rows = store.notifications(user_id())
        store.mark_seen(user_id())
        return render_template("notifications.html", rows=rows)

    @app.route("/push/key")
    @login_required
    def push_key():
        return jsonify({"key": store.vapid_keys()["public"]})

    @app.route("/push/subscribe", methods=["POST"])
    @login_required
    def push_subscribe():
        data = request.get_json(silent=True) or {}
        if not secrets.compare_digest(str(data.get("csrf", "")), session.get("csrf_token", "")):
            return jsonify({"ok": False}), 400
        try:
            store.add_push_subscription(user_id(), data.get("subscription") or {}, data.get("device", ""))
        except ShopError as e:
            return jsonify({"ok": False, "error": i18n.translate_text(str(e))}), 400
        return jsonify({"ok": True})

    @app.route("/account/addresses", methods=["GET", "POST"])
    @login_required
    def addresses():
        if request.method == "POST":
            f = request.form
            try:
                if f.get("action") == "delete":
                    store.delete_address(user_id(), f.get("address_id", type=int))
                    flash("Address removed.", "success")
                elif f.get("action") == "default":
                    store.set_default_address(user_id(), f.get("address_id", type=int))
                    flash("Default address changed.", "success")
                else:
                    aid = store.add_address(user_id(), f)
                    act("Address added", f"{f.get('city', '')} {f.get('pincode', '')} {f.get('country', 'IN')}")
                    flash("Address saved.", "success")
                    for warning in addresscheck.check(f.get("country", "IN"), f.get("pincode", ""), f.get("place_id", ""),
                                                      f.get("city", ""))["warnings"]:      # version 6: look again
                        flash(warning, "info")
                    if f.get("next", "").startswith("/checkout"):
                        return redirect(url_for("checkout", address=aid))
            except ShopError as e:
                flash(str(e), "error")
                if f.get("next"):
                    return redirect(safe_next())
                return render_template("addresses.html", addresses=store.addresses(user_id()), states=STATES,
                                       form=f, countries=world.countries(world.current_lang()),
                                       dial=world.DIAL_CODES), 400
            return redirect(safe_next("addresses") if f.get("next") else url_for("addresses"))
        u = store.get_user(user_id())
        return render_template("addresses.html", addresses=store.addresses(user_id()), states=STATES,
                               form={"country": u["country"]}, countries=world.countries(world.current_lang()),
                               dial=world.DIAL_CODES)

    # ======================================================
    # Sell on Yo Delivery (seller centre)
    # ======================================================

    @app.route("/sell", methods=["GET", "POST"])
    def sell():
        shop = store.seller_of(user_id()) if user_id() else None
        if request.method == "POST":
            if not user_id():
                return redirect(url_for("login", next=url_for("sell")))
            try:
                store.apply_seller(user_id(), request.form)
                act("Seller", "Applied to sell: " + request.form.get("shop_name", "")[:60])
                flash("Thank you! Your seller application was sent. We will tell you when it is checked.", "success")
                return redirect(url_for("seller_dashboard"))
            except ShopError as e:
                flash(str(e), "error")
                return render_template("sell.html", shop=shop, form=request.form,
                                       countries=world.countries(world.current_lang())), 400
        u = store.get_user(user_id()) if user_id() else None
        return render_template("sell.html", shop=shop, form={"country": u["country"] if u else "IN"},
                               countries=world.countries(world.current_lang()))

    @app.route("/seller")
    @seller_required
    def seller_dashboard():
        shop = request.shop
        sales = store.seller_sales(shop["id"], 10) if shop["status"] == "Approved" else []
        return render_template("seller/dashboard.html", shop=shop, sales=sales, bal=store.seller_balance(shop["id"]),
                               products=store.seller_products(shop["id"]), payouts=store.payouts(shop["id"]))

    @app.route("/seller/products")
    @seller_required
    def seller_products():
        return render_template("seller/products.html", shop=request.shop,
                               products=store.seller_products(request.shop["id"]))

    def seller_product_form(product=None):
        shop = request.shop
        f = request.form if request.method == "POST" else None
        if f is not None:
            try:
                files = request.files.getlist("images")
                if product is None and not any(x.filename for x in files):
                    raise ShopError("Please upload at least one product picture (JPG or PNG).")
                slug = store.save_seller_product(shop["id"], f, product.id if product else None)
                try:
                    images.save_product_images(files, slug, store)
                except ShopError:
                    if product is None:
                        store.delete_product(store.get_product(slug=slug).id)
                    raise
                p = store.get_product(slug=slug)
                act("Seller", ("Updated " if product else "Added ") + p.name[:60] + " (waits for approval)")
                flash("Saved. Our team checks the product before it appears in the shop.", "success")
                return redirect(url_for("seller_products"))
            except ShopError as e:
                flash(str(e), "error")
        if f is None and product is not None:
            f = product_fields(product)
        return render_template("seller/product_form.html", shop=shop, p=product, form=f or {"active": "yes"},
                               categories=store.categories()), (400 if request.method == "POST" else 200)

    @app.route("/seller/products/new", methods=["GET", "POST"])
    @seller_required
    def seller_product_new():
        return seller_product_form()

    @app.route("/seller/products/<int:pid>/edit", methods=["GET", "POST"])
    @seller_required
    def seller_product_edit(pid):
        try:
            p = store.get_product(pid=pid)
        except ShopError:
            abort(404)
        if p.seller_id != request.shop["id"]:
            abort(404)
        return seller_product_form(p)

    @app.route("/seller/products/<int:pid>/<action>", methods=["POST"])
    @seller_required
    def seller_product_action(pid, action):
        try:
            p = store.get_product(pid=pid)
        except ShopError:
            abort(404)
        if p.seller_id != request.shop["id"] or action not in ("hide", "show", "stock"):
            abort(404)
        try:
            if action == "stock":
                store.admin_update_product(pid, p.price / 100, p.mrp / 100, request.form.get("stock"))
                flash("Stock updated.", "success")
            else:
                store.set_product_active(pid, action == "show")
                flash("The product is now " + ("visible in the shop." if action == "show" else "hidden."), "success")
        except ShopError as e:
            flash(str(e), "error")
        return redirect(url_for("seller_products"))

    @app.route("/seller/orders")
    @seller_required
    def seller_orders():
        return render_template("seller/orders.html", shop=request.shop, sales=store.seller_sales(request.shop["id"]),
                               bal=store.seller_balance(request.shop["id"]), payouts=store.payouts(request.shop["id"]))

    @app.route("/seller/settings", methods=["GET", "POST"])
    @seller_required
    def seller_settings():
        if request.method == "POST":
            try:
                store.update_shop(request.shop["id"], request.form)
                flash("Shop details saved.", "success")
            except ShopError as e:
                flash(str(e), "error")
            return redirect(url_for("seller_settings"))
        return render_template("seller/settings.html", shop=request.shop)

    # ======================================================
    # Admin panel  (sign in as admin@example.com / Admin@1234)
    # ======================================================

    def admin_page(template, **kw):
        with store.db() as conn:
            counts = {"sellers": conn.execute("SELECT COUNT(*) FROM sellers WHERE status = 'Pending'").fetchone()[0],
                      "products": conn.execute("SELECT COUNT(*) FROM products WHERE approved = 0").fetchone()[0],
                      "returns": conn.execute("SELECT COUNT(*) FROM returns WHERE status = 'Requested'").fetchone()[0],
                      "couriers": conn.execute("SELECT COUNT(*) FROM couriers WHERE status = 'Pending'").fetchone()[0],
                      "posts": conn.execute("SELECT COUNT(*) FROM posts WHERE status = 'Review'").fetchone()[0],
                      "shipments": conn.execute("SELECT COUNT(*) FROM shipments WHERE status IN ('Booked', "
                                                "'At destination hub') AND courier_id IS NULL").fetchone()[0],
                      "board": conn.execute("SELECT COUNT(*) FROM board_tasks WHERE board = 'admin' AND "
                                            "status <> 'done'").fetchone()[0],
                      "tickets": conn.execute("SELECT COUNT(*) FROM ticket_bookings WHERE status = 'Held'").fetchone()[0],
                      "chat": conn.execute("SELECT COUNT(*) FROM chat_messages WHERE status = 'Held'").fetchone()[0]}
        counts["leave"] = sum(1 for r in store.leave_list(user_id()) if r["status"] == "Waiting")
        counts["alerts"] = store.open_alerts()
        counts["staff_tasks"] = sum(1 for t in store.task_list(user_id()) if t["overdue"])
        counts["id_checks"] = len(store.id_checks("Waiting"))
        with store.db() as conn:                          # version 7
            counts["support"] = conn.execute("SELECT COUNT(*) FROM support_tickets WHERE status = 'Open'").fetchone()[0]
            counts["claims"] = conn.execute("SELECT COUNT(*) FROM parcel_claims WHERE status = 'Waiting'").fetchone()[0]
            counts["risk"] = conn.execute("SELECT COUNT(*) FROM orders WHERE risk_status = 'Review'").fetchone()[0]
            counts["approvals"] = conn.execute("SELECT COUNT(*) FROM approvals WHERE status = 'Waiting'").fetchone()[0]
        return render_template(template, counts=counts, **kw)

    @app.route("/admin")
    @admin_required
    def admin_dashboard():
        stats = store.admin_stats()
        return admin_page("admin/dashboard.html", st=stats, recent_orders=store.all_orders(limit=8),
                          recent_activity=store.activity(limit=12),
                          low_stock=[p for p in store.products(order="popular") if p.stock <= 10])

    @app.route("/admin/orders")
    @admin_required
    def admin_orders():
        status, q = request.args.get("status", ""), request.args.get("q", "").strip()
        return admin_page("admin/orders.html", orders=store.all_orders(status, q), status=status, q=q)

    @app.route("/admin/orders/<number>", methods=["GET", "POST"])
    @admin_required
    def admin_order(number):
        try:
            o = store.admin_order(number)
        except ShopError:
            abort(404)
        if request.method == "POST":
            try:
                if request.form.get("action") == "status":
                    status = request.form.get("status", "")
                    if o["payment_status"] in ("Pending", "Failed"):
                        raise ShopError("This order is waiting for payment.")
                    store.set_order_status(number, status, request.form.get("note", ""))
                    act("Admin: orders", f"Order {number} -> {status}")
                    flash(f"Order status changed to '{status}'. The customer sees it on the tracking page and gets "
                          f"a message.", "success")
                else:
                    store.cancel_order(o["user_id"], number)
                    act("Admin: orders", f"Admin cancelled order {number}")
                    flash("Order cancelled by admin. Stock returned and the payment refunded.", "success")
            except ShopError as e:
                flash(str(e), "error")
            return redirect(url_for("admin_order", number=number))
        current = TRACK_STEPS.index(o["status"]) if o["status"] in TRACK_STEPS else len(TRACK_STEPS)
        return admin_page("admin/order.html", o=o, steps=store.tracking(o), next_steps=TRACK_STEPS[current + 1:],
                          activity=store.activity(q=number, limit=20), payments=store.payments_of(number),
                          ret=store.order_return(o["id"]), shops=store.shop_names())

    @app.route("/admin/customers")
    @admin_required
    def admin_customers():
        q = request.args.get("q", "").strip()
        return admin_page("admin/customers.html", customers=store.all_customers(q), q=q)

    @app.route("/admin/customers/<int:uid>", methods=["GET", "POST"])
    @admin_required
    def admin_customer(uid):
        c = next((x for x in store.all_customers() if x["id"] == uid), None)
        if c is None:
            abort(404)
        if request.method == "POST":
            f, action = request.form, request.form.get("action")
            try:
                if action == "details":
                    store.admin_update_user(uid, f, user_id())
                    act("Admin: customers", f"Updated {f.get('name')} ({f.get('email')})"
                        + (" - BLOCKED" if f.get("blocked") == "yes" else "") + (" - admin" if f.get("is_admin") == "yes" else ""))
                    flash("Customer details saved.", "success")
                elif action == "password":
                    store.admin_set_password(uid, f.get("new_password", ""))
                    act("Admin: customers", f"Reset password of {c['name']}")
                    flash("New password set. Tell the customer the new password.", "success")
                elif action == "wallet":
                    bal = store.adjust_wallet(uid, f.get("amount"), f.get("reason", "").strip()[:80])
                    act("Admin: customers", f"Wallet of {c['name']} changed by ₹{f.get('amount')} "
                        f"({f.get('reason', '').strip()[:60] or 'no reason'}) -> {rupees(bal)}")
                    flash(f"Wallet updated. New balance: {rupees(bal)}.", "success")
                elif action == "reset2fa":
                    store.reset_2fa(uid)
                    act("Admin: customers", f"Turned off two-factor login of {c['name']}")
                    flash("Two-factor login turned off for this account.", "success")
                elif action == "delete":
                    store.delete_user(uid, user_id())
                    act("Admin: customers", f"Deleted account {c['name']} ({c['email']})")
                    flash(f"Account of {c['name']} deleted.", "success")
                    return redirect(url_for("admin_customers"))
            except ShopError as e:
                flash(str(e), "error")
            return redirect(url_for("admin_customer", uid=uid))
        full = store.get_user(uid)
        return admin_page("admin/customer.html", c=c, full=full, orders=store.orders(uid),
                          addresses=store.addresses(uid), activity=store.activity(user_id=uid, limit=50),
                          cart=store.cart_items(store.get_cart(uid)), wishlist=store.wishlist(uid),
                          countries=world.countries("en"), shop=store.seller_of(uid))

    @app.route("/admin/products", methods=["GET", "POST"])
    @admin_required
    def admin_products():
        if request.method == "POST":                    # quick edit of price / MRP / stock
            f = request.form
            try:
                p = store.admin_update_product(f.get("product_id", type=int), f.get("price"), f.get("mrp"), f.get("stock"))
                act("Admin: products", f"{p.name[:50]}: price ₹{f.get('price')}, MRP ₹{f.get('mrp')}, stock {f.get('stock')}")
                flash(f"Saved: {p.name[:60]}", "success")
            except ShopError as e:
                flash(str(e), "error")
            return redirect(url_for("admin_products", category=f.get("category", ""), show=f.get("show", "")) +
                            f"#p{f.get('product_id', '')}")
        cat, show, q = request.args.get("category", ""), request.args.get("show", ""), request.args.get("q", "").strip()
        products = store.products(category=cat or None, active_only=False, order="new")
        if show == "hidden":
            products = [p for p in products if not p.active]
        elif show == "low":
            products = [p for p in products if p.stock <= 10]
        elif show == "waiting":
            products = [p for p in products if not p.approved]
        elif show == "sellers":
            products = [p for p in products if p.seller_id]
        if q:
            products = [p for p in products if q.lower() in (p.name + " " + p.brand).lower()]
        with store.db() as conn:
            sold = {r[0]: r[1] for r in conn.execute(
                "SELECT i.product_id, SUM(i.qty) FROM order_items i JOIN orders o ON o.id = i.order_id "
                "WHERE o.cancelled_at = '' GROUP BY i.product_id")}
        return admin_page("admin/products.html", products=products, category=cat, show=show, q=q, sold=sold,
                          categories=store.categories(), shops=store.shop_names())

    def product_fields(product):
        return {"name": product.name, "brand": product.brand, "category": product.category,
                "price": "%g" % (product.price / 100), "mrp": "%g" % (product.mrp / 100), "stock": product.stock,
                "about": "\n".join(product.about), "specs": "\n".join(f"{k}: {v}" for k, v in product.specs.items()),
                "tags": product.tags, "active": "yes" if product.active else "no"}

    def product_form(product=None):
        f = request.form if request.method == "POST" else None
        if f is not None:
            try:
                files = request.files.getlist("images")
                if product is None and not any(x.filename for x in files):
                    raise ShopError("Please upload at least one product picture (JPG or PNG).")
                slug = store.save_product(f, product.id if product else None)
                try:
                    count = images.save_product_images(files, slug, store)
                except ShopError:
                    if product is None:                 # do not keep a new product without pictures
                        store.delete_product(store.get_product(slug=slug).id)
                    raise
                p = store.get_product(slug=slug)
                act("Admin: products", ("Updated " if product else "Added new product ") + p.name[:60]
                    + (f" ({count} new picture(s))" if count else ""))
                flash(("Product updated: " if product else "New product added: ") + p.name[:60], "success")
                return redirect(url_for("admin_products") + f"#p{p.id}")
            except ShopError as e:
                flash(str(e), "error")
        if f is None and product is not None:
            f = product_fields(product)
        return admin_page("admin/product_form.html", p=product, form=f or {"active": "yes"},
                          categories=store.categories()), (400 if request.method == "POST" else 200)

    @app.route("/admin/products/new", methods=["GET", "POST"])
    @admin_required
    def admin_product_new():
        return product_form()

    @app.route("/admin/products/<int:pid>/edit", methods=["GET", "POST"])
    @admin_required
    def admin_product_edit(pid):
        try:
            return product_form(store.get_product(pid=pid))
        except ShopError:
            abort(404)

    @app.route("/admin/products/<int:pid>/<action>", methods=["POST"])
    @admin_required
    def admin_product_action(pid, action):
        try:
            p = store.get_product(pid=pid)
            if action in ("hide", "show"):
                store.set_product_active(pid, action == "show")
                act("Admin: products", ("Showed " if action == "show" else "Hid ") + p.name[:60])
                flash(f"{p.name[:50]} is now {'visible in the shop' if action == 'show' else 'hidden from the shop'}.",
                      "success")
            elif action in ("approve", "reject"):
                store.approve_product(pid, action == "approve")
                act("Admin: products", ("Approved " if action == "approve" else "Did not approve ") + p.name[:60])
                flash(f"{p.name[:50]}: " + ("approved and visible in the shop." if action == "approve"
                                            else "not approved; the seller was told."), "success")
            elif action == "delete":
                store.delete_product(pid)
                if p.art == "upload":
                    images.delete_product_images(p.slug, store)
                act("Admin: products", "Deleted " + p.name[:60])
                flash(f"Deleted: {p.name[:60]}", "success")
            else:
                abort(404)
        except ShopError as e:
            flash(str(e), "error")
        return redirect(safe_next("admin_products"))

    @app.route("/admin/categories", methods=["GET", "POST"])
    @admin_required
    def admin_categories():
        if request.method == "POST":
            f = request.form
            try:
                if f.get("action") == "delete":
                    store.delete_category(f.get("slug"))
                    act("Admin: products", f"Deleted category {f.get('slug')}")
                    flash("Category deleted.", "success")
                else:
                    slug = store.save_category(f.get("name", ""), f.get("slug") or None)
                    pic = request.files.get("image")
                    if pic and pic.filename:
                        store.save_category(f.get("name", ""), slug, images.save_category_image(pic, slug, store))
                    act("Admin: products", ("Renamed" if f.get("slug") else "Added") + f" category {f.get('name')}")
                    flash(f"Category '{f.get('name')}' saved.", "success")
            except ShopError as e:
                flash(str(e), "error")
            return redirect(url_for("admin_categories"))
        return admin_page("admin/categories.html", categories=store.categories())

    @app.route("/admin/coupons", methods=["GET", "POST"])
    @admin_required
    def admin_coupons():
        if request.method == "POST":
            f, action = request.form, request.form.get("action")
            try:
                if action == "toggle":
                    store.toggle_coupon(f.get("code"))
                    act("Admin: settings", f"Switched coupon {f.get('code')} on/off")
                elif action == "delete":
                    store.delete_coupon(f.get("code"))
                    act("Admin: settings", f"Deleted coupon {f.get('code')}")
                    flash(f"Coupon {f.get('code')} deleted.", "success")
                else:
                    code = store.save_coupon(f)
                    act("Admin: settings", f"Saved coupon {code}")
                    flash(f"Coupon {code} saved. Customers can use it now.", "success")
            except ShopError as e:
                flash(str(e), "error")
            return redirect(url_for("admin_coupons"))
        return admin_page("admin/coupons.html", coupons=store.coupons())

    @app.route("/admin/reviews", methods=["GET", "POST"])
    @admin_required
    def admin_reviews():
        if request.method == "POST":
            try:
                r = store.delete_review(request.form.get("review_id", type=int))
                act("Admin: products", f"Deleted review '{r['title']}' by {r['author']}")
                flash("Review deleted and product rating updated.", "success")
            except ShopError as e:
                flash(str(e), "error")
            return redirect(url_for("admin_reviews", q=request.form.get("q", ""), rating=request.form.get("rating", "")))
        q, rating = request.args.get("q", "").strip(), request.args.get("rating", "")
        return admin_page("admin/reviews.html", reviews=store.all_reviews(q, rating if rating.isdigit() else None),
                          q=q, rating=rating)

    @app.route("/admin/sellers", methods=["GET", "POST"])
    @admin_required
    def admin_sellers():
        if request.method == "POST":
            f = request.form
            sid = f.get("seller_id", type=int)
            try:
                if f.get("action") == "payout":
                    amount = store.record_payout(sid, f.get("amount"), f.get("note"), store.get_user(user_id())["name"])
                    act("Admin: sellers", f"Payout {rupees(amount)} to seller #{sid}")
                    flash(f"Payout of {rupees(amount)} recorded. The seller was told.", "success")
                else:
                    s = store.decide_seller(sid, f.get("status"), f.get("reason"), f.get("commission"))
                    act("Admin: sellers", f"{s['shop_name']} -> {f.get('status')}")
                    flash(f"{s['shop_name']}: {f.get('status')}. The seller was told.", "success")
            except ShopError as e:
                flash(str(e), "error")
            return redirect(url_for("admin_sellers", status=request.args.get("status", "")))
        status = request.args.get("status", "")
        return admin_page("admin/sellers.html", sellers=store.sellers(status), status=status, STATUSES=SELLER_STATUSES,
                          waiting=store.products_waiting())

    @app.route("/admin/returns", methods=["GET", "POST"])
    @admin_required
    def admin_returns():
        if request.method == "POST":
            f = request.form
            try:
                r = store.decide_return(f.get("return_id", type=int), f.get("action") == "approve",
                                        store.get_user(user_id())["name"], f.get("note", ""))
                act("Admin: orders", f"Return #{r['id']} " + ("refunded" if f.get("action") == "approve" else "rejected"))
                flash("Return refunded: stock is back and the customer was paid back." if f.get("action") == "approve"
                      else "Return rejected. The customer was told why.", "success")
            except ShopError as e:
                flash(str(e), "error")
            return redirect(url_for("admin_returns", status=request.args.get("status", "Requested")))
        status = request.args.get("status", "Requested")
        return admin_page("admin/returns.html", rows=store.returns(status), status=status)

    @app.route("/admin/settings", methods=["GET", "POST"])
    @admin_required
    def admin_settings():
        if request.method == "POST":
            try:
                if request.form.get("action") == "fx":
                    n = store.refresh_fx_rates()
                    act("Admin: settings", f"Downloaded today's exchange rates ({n} currencies)")
                    flash(f"Exchange rates updated ({n} currencies).", "success")
                    rate_cache["at"] = 0
                else:
                    store.update_settings(request.form)
                    act("Admin: settings", "Changed shop settings")
                    flash("Settings saved. The shop uses them immediately.", "success")
            except ShopError as e:
                flash(str(e), "error")
            except Exception as e:
                flash(f"Could not download the rates: {str(e)[:80]}", "error")
            return redirect(url_for("admin_settings"))
        return admin_page("admin/settings.html", st=store.settings(), fx=store.fx_rates(), gateway=store.gateway(),
                          providers=store.notifier.status(), database=store.database.describe(),
                          webhook=bool(env.get("STRIPE_WEBHOOK_SECRET")), secret_from_env=bool(env.get("SECRET_KEY")))

    @app.route("/admin/backups", methods=["GET", "POST"])
    @admin_required
    def admin_backups():
        if request.method == "POST":
            f = request.form
            if f.get("action") == "restore":
                file = request.files.get("file")
                me = store.get_user(user_id())
                try:
                    if f.get("confirm") != "RESTORE":
                        raise ShopError("Type RESTORE in capital letters to confirm.")
                    if not file or not file.filename:
                        raise ShopError("Choose a backup file (.json.gz).")
                    store.login(me["email"], f.get("password", ""))
                    data = store.unpack(file.read())
                    store.save_backup("before restore", me["name"])
                    counts = store.import_data(data)
                    act("Admin: settings", f"Restored the database from a backup ({counts.get('orders', 0)} orders)",
                        uid=user_id() if store.get_user(user_id()) else 0)
                    flash(f"Database restored: {counts.get('users', 0)} users, {counts.get('orders', 0)} orders. "
                          f"A copy of the old data was saved first.", "success")
                    if not store.get_user(user_id()):
                        session.clear()
                        return redirect(url_for("login"))
                except ShopError as e:
                    flash("Password is wrong." if "Incorrect" in str(e) else str(e), "error")
            else:
                bid, _ = store.save_backup("manual", store.get_user(user_id())["name"])
                act("Admin: settings", f"Made backup #{bid}")
                flash(f"Backup #{bid} created. Download it and keep it safe on your computer.", "success")
            return redirect(url_for("admin_backups"))
        return admin_page("admin/backups.html", rows=store.backups(), database=store.database.describe(),
                          backup_email=env.get("BACKUP_EMAIL", ""), providers=store.notifier.status())

    @app.route("/admin/backups/<int:bid>.json.gz")
    @app.route("/admin/backups/now.json.gz", defaults={"bid": None})
    @admin_required
    def admin_backup_download(bid):
        try:
            blob = store.backup_blob(bid) if bid else store.pack(store.export_data())
        except ShopError:
            abort(404)
        act("Admin update", f"Downloaded backup {bid or 'now'}")
        name = f"yo_delivery_backup_{bid or datetime.now().strftime('%Y%m%d_%H%M')}.json.gz"
        return Response(blob, mimetype="application/gzip", headers={"Content-Disposition": f"attachment; filename={name}"})

    @app.route("/admin/account", methods=["GET", "POST"])
    @admin_required
    def admin_account():
        if request.method == "POST":
            f = request.form
            try:
                anchor = handle_security_form(f)
                if anchor is not None:
                    return redirect(url_for("admin_account") + anchor)
                if f.get("action") == "password":
                    if f.get("new_password") != f.get("new_password2"):
                        raise ShopError("The two new passwords do not match.")
                    store.change_password(user_id(), f.get("old_password", ""), f.get("new_password", ""))
                    act("Password changed", "Admin changed own password")
                    flash("Your password was changed. Use the new password next time you sign in.", "success")
                else:
                    store.update_user(user_id(), f.get("name", ""), f.get("email", ""), f.get("mobile", ""),
                                      f.get("country"))
                    act("Profile updated", "Admin updated own name / email / mobile")
                    flash("Your details were saved.", "success")
            except ShopError as e:
                flash(str(e), "error")
            return redirect(url_for("admin_account"))
        return admin_page("admin/account.html", **totp_context("new_"))

    @app.route("/admin/activity")
    @admin_required
    def admin_activity():
        action, q = request.args.get("action", ""), request.args.get("q", "").strip()
        return admin_page("admin/activity.html", rows=store.activity(action, q, limit=500), action=action, q=q,
                          actions=ACTIONS)

    @app.route("/admin/database")
    @app.route("/admin/database/<table>")
    @admin_required
    def admin_database(table=None):
        tables = store.tables()
        table = table or "orders"
        try:
            cols, rows = store.table_rows(table, limit=200)
        except ShopError:
            abort(404)
        return admin_page("admin/database.html", tables=tables, table=table, cols=cols, rows=rows,
                          total=dict(tables)[table])

    @app.route("/admin/database/<table>.csv")
    @admin_required
    def admin_table_csv(table):
        try:
            cols, rows = store.table_rows(table, limit=100000)
        except ShopError:
            abort(404)
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(cols)
        writer.writerows(rows)
        act("Admin update", f"Exported table {table} to CSV")
        return Response("﻿" + out.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename=yo_delivery_{table}.csv"})

    # ======================================================
    # Information pages
    # ======================================================

    @app.route("/help")
    def help_page():
        return render_template("help.html")

    @app.route("/about")
    def about():
        return render_template("about.html")

    @app.errorhandler(403)
    def forbidden(e):
        if user_id() and store.worker_of(user_id()):
            return render_template("error.html", code=403, title="Not allowed",
                                   message="You do not have the ability to open this page or to do this. "
                                           "Ask your manager."), 403
        return render_template("error.html", code=403, title="Admins only",
                               message="This page is only for store administrators."), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template("error.html", code=404, title="Page not found",
                               message="Sorry, we couldn't find that page."), 404

    @app.errorhandler(400)
    def bad_request(e):
        return render_template("error.html", code=400, title="Please try again",
                               message="This form has expired. Go back, reload the page and try again."), 400

    @app.errorhandler(413)
    def too_big(e):
        return render_template("error.html", code=413, title="File too large",
                               message="The file is too large. Pictures can be at most 12 MB."), 413

    # ======================================================
    # Version 3 - the Yo super app: Courier, Yo Pay, Rewards, Blog, task boards
    # ======================================================
    helpers = SimpleNamespace(login_required=login_required, admin_required=admin_required,
                              seller_required=seller_required, act=act, user_id=user_id, safe_next=safe_next,
                              base_url=base_url, admin_page=admin_page, now=now, visitor=visitor)
    for module in (courier_views, pay_views, rewards_views, blog_views, board_views, admin_v3, tickets_views,
                   world_views, v5_views, staff_views, v6_views, workforce_views, v6b_views, v7_views, v7b_views):
        module.register(app, store, helpers)

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")
