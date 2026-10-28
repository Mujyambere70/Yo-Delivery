"""
Yo Delivery - online shopping & delivery website (Python + Flask).

Run:   python app.py      then open http://127.0.0.1:5000
Demo:  demo@example.com  /  Demo@1234
"""

import csv
import io
import os
import secrets
from functools import wraps

from flask import (Flask, Response, abort, flash, redirect, render_template, request, session, url_for)

import demo_data
import images
from store import (ACTIONS, CATEGORY_NAMES, STATES, TRACK_STEPS, ShopError, Store, delivery_date_text,
                   delivery_days, rupees, to_dt, PINCODE_RE)

GST = {"electronics": 18, "fashion": 12, "footwear": 12, "watches": 18, "grocery": 5, "home": 18, "books": 0}


def create_app(db_path=None, seed=True):
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024        # uploads up to 12 MB

    store = Store(db_path or os.path.join(app.root_path, "yo_delivery.db"))
    if seed:
        demo_data.seed(store)
    app.store = store

    # ---------------- template helpers ----------------

    app.jinja_env.filters["rupees"] = rupees

    @app.template_filter("nicedate")
    def nicedate(value, fmt="%d %b %Y"):
        return to_dt(value).strftime(fmt) if value else ""

    def csrf_token():
        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_hex(16)
        return session["csrf_token"]

    app.jinja_env.globals.update(csrf_token=csrf_token, TRACK_STEPS=TRACK_STEPS, GST=GST,
                                 delivery_date=delivery_date_text)

    def user_id():
        return session.get("uid")

    def get_cart():
        if user_id():
            return store.get_cart(user_id())
        return {int(k): v for k, v in session.get("cart", {}).items()}

    def save_cart(cart):
        if user_id():
            store.save_cart(user_id(), cart)
        else:
            session["cart"] = {str(k): v for k, v in cart.items()}

    @app.context_processor
    def header_data():
        uid = user_id()
        user = store.get_user(uid) if uid else None
        if uid and user is None:          # account removed
            session.clear()
        address = store.default_address(uid) if user else None
        settings = store.settings()
        return {"user": user, "cart_count": sum(get_cart().values()),
                "wish_ids": store.wishlist_ids(uid) if user else set(), "deliver_to": address,
                # these come from the database, so the admin can change them
                "CATEGORIES": [(c["slug"], c["name"], c["image"]) for c in store.categories()],
                "COUPONS": {c["code"]: (c["description"],) for c in store.coupons(active_only=True)},
                "FREE_DELIVERY_FROM": settings["free_delivery_from"], "SETTINGS": settings}

    # ---------------- security ----------------

    @app.before_request
    def check_csrf():
        if user_id():                       # a blocked account is signed out immediately
            u = store.get_user(user_id())
            if u is None or u["blocked"]:
                session.clear()
                flash("Your account has been blocked. Please contact customer support.", "error")
                return redirect(url_for("index"))
        if request.method == "POST":
            sent = request.form.get("csrf_token", "")
            if not sent or not secrets.compare_digest(sent, session.get("csrf_token", "")):
                abort(400)

    @app.after_request
    def headers(resp):
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        return resp

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
            if not u or not u["is_admin"]:
                abort(403)
            return view(*a, **kw)
        return wrapper

    def safe_next(default="index"):
        nxt = request.values.get("next", "")
        return nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for(default)

    # ======================================================
    # Shopping pages
    # ======================================================

    @app.route("/")
    def index():
        recent = store.products(ids=session.get("recent", [])[:8]) if session.get("recent") else []
        return render_template(
            "index.html",
            deals=store.products(order="deals", limit=8),
            electronics=store.products(category="electronics", limit=8),
            fashion=store.products(category="fashion", limit=8) + store.products(category="footwear", limit=4),
            grocery=store.products(category="grocery", limit=8),
            home_books=store.products(category="home") + store.products(category="books"),
            new_arrivals=store.products(order="new", limit=10),
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
        return render_template("listing.html", results=results, q=q, category=cat,
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
        if not p.active and not (user_id() and store.get_user(user_id())["is_admin"]):
            abort(404)                      # hidden by the admin
        recent = [x for x in session.get("recent", []) if x != p.id]
        session["recent"] = ([p.id] + recent)[:10]

        pin = request.args.get("pin", "").strip()
        if not pin and user_id() and store.default_address(user_id()):
            pin = store.default_address(user_id())["pincode"]
        delivery = None
        if pin:
            if PINCODE_RE.match(pin):
                delivery = {"pin": pin, "date": delivery_date_text(pin), "days": delivery_days(pin)}
            else:
                delivery = {"pin": pin, "error": "Please enter a valid 6-digit pincode."}
        similar = [x for x in store.products(category=p.category) if x.id != p.id][:8]
        return render_template("product.html", p=p, reviews=store.reviews(p.id), breakdown=store.rating_breakdown(p),
                               delivery=delivery, similar=similar,
                               can_review=user_id() and store.can_review(user_id(), p.id))

    # ======================================================
    # Cart
    # ======================================================

    @app.route("/cart")
    def cart():
        items = store.cart_items(get_cart())
        summary = store.price_summary(items, session.get("coupon", ""), user_id())
        if session.get("coupon") and summary["coupon_error"]:
            flash(summary["coupon_error"], "error")
            session.pop("coupon", None)
        return render_template("cart.html", items=items, s=summary)

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
            return redirect(url_for("cart"))
        items = store.cart_items(get_cart())
        s = store.price_summary(items, code, user_id())
        if s["coupon_error"]:
            flash(s["coupon_error"], "error")
        else:
            session["coupon"] = code
            flash(f"Coupon {code} applied! You saved {rupees(s['coupon_discount'])}.", "success")
        return redirect(url_for(request.form.get("back", "cart") if request.form.get("back") == "checkout" else "cart"))

    # ======================================================
    # Checkout & orders
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
            try:
                number = store.place_order(uid, request.form.get("address_id"), request.form.get("payment", ""),
                                           session.get("coupon", ""), request.form.get("upi_id", ""))
            except ShopError as e:
                flash(str(e), "error")
                return redirect(url_for("checkout"))
            session.pop("coupon", None)
            o = store.get_order(uid, number)
            act("Order placed", f"{number} - {len(o['items'])} item(s) - {o['payment_method']} - {rupees(o['total'])}")
            return redirect(url_for("order_success", number=number))
        addresses = store.addresses(uid)
        summary = store.price_summary(items, session.get("coupon", ""), uid)
        return render_template("checkout.html", items=items, s=summary, addresses=addresses, states=STATES,
                               wallet=store.get_user(uid)["wallet"])

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
        reviewable = {it["product_id"] for it in o["items"] if store.can_review(user_id(), it["product_id"])}
        return render_template("order.html", o=o, steps=store.tracking(o), reviewable=reviewable)

    @app.route("/orders/<number>/cancel", methods=["POST"])
    @login_required
    def cancel(number):
        try:
            o = store.cancel_order(user_id(), number)
            act("Order cancelled", f"{number} - {rupees(o['total'])}")
            extra = " The amount was refunded to your Yo Pay wallet." if o["payment_method"] != "Cash on Delivery" else ""
            flash("Your order was cancelled." + extra, "success")
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
        for it in o["items"]:
            cat = store.get_product(pid=it["product_id"]).category
            rate = GST.get(cat, 18)
            gross = it["price"] * it["qty"]
            taxable = round(gross * 100 / (100 + rate))
            lines.append({**it, "rate": rate, "gross": gross, "taxable": taxable, "tax": gross - taxable})
        return render_template("invoice.html", o=o, lines=lines, user=store.get_user(user_id()))

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
        form = request.form if request.method == "POST" else {}
        if request.method == "POST":
            try:
                if request.form.get("password") != request.form.get("password2"):
                    raise ShopError("The two passwords do not match.")
                uid = store.register(form.get("name", ""), form.get("email", ""), form.get("mobile", ""),
                                     form.get("password", ""))
            except ShopError as e:
                flash(str(e), "error")
                return render_template("signup.html", form=form), 400
            start_session(uid)
            act("Signed up", "New account with email " + form.get("email", "").strip().lower())
            flash("Welcome to Yo Delivery! ₹2,000 demo balance was added to your Yo Pay wallet.", "success")
            return redirect(safe_next())
        return render_template("signup.html", form=form)

    def start_session(uid):
        guest_cart = {int(k): v for k, v in session.get("cart", {}).items()}
        recent, token = session.get("recent", []), session.get("csrf_token")
        session.clear()
        session.update(uid=uid, recent=recent, csrf_token=token)
        if guest_cart:                                  # move guest cart into the account
            cart = store.get_cart(uid)
            for pid, q in guest_cart.items():
                try:
                    cart = store.cart_set(cart, pid, cart.get(pid, 0) + q)
                except ShopError:
                    pass
            store.save_cart(uid, cart)

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if user_id():
            return redirect(url_for("index"))
        if request.method == "POST":
            try:
                u = store.login(request.form.get("login", ""), request.form.get("password", ""))
            except ShopError as e:
                act("Login failed", "Wrong password or unknown account: " + request.form.get("login", "")[:60], uid=0)
                flash(str(e), "error")
                return render_template("login.html", login=request.form.get("login", "")), 401
            start_session(u["id"])
            act("Logged in", "Admin" if u.get("is_admin") else "")
            if u.get("is_admin") and not request.values.get("next"):
                flash(f"Welcome, {u['name'].split()[0]}! You are signed in as admin.", "success")
                return redirect(url_for("admin_dashboard"))
            flash(f"Welcome back, {u['name'].split()[0]}!", "success")
            return redirect(safe_next())
        return render_template("login.html", login="")

    @app.route("/logout")
    def logout():
        if user_id():
            act("Logged out")
        session.clear()
        flash("You have been signed out.", "success")
        return redirect(url_for("index"))

    @app.route("/account", methods=["GET", "POST"])
    @login_required
    def account():
        if request.method == "POST":
            f = request.form
            try:
                if f.get("action") == "password":
                    if f.get("new_password") != f.get("new_password2"):
                        raise ShopError("The two new passwords do not match.")
                    store.change_password(user_id(), f.get("old_password", ""), f.get("new_password", ""))
                    act("Password changed")
                    flash("Your password was changed.", "success")
                else:
                    store.update_user(user_id(), f.get("name", ""), f.get("email", ""), f.get("mobile", ""))
                    act("Profile updated")
                    flash("Your profile was updated.", "success")
            except ShopError as e:
                flash(str(e), "error")
            return redirect(url_for("account"))
        return render_template("account.html", orders_count=len(store.orders(user_id())))

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
                    store.add_address(user_id(), f)
                    act("Address added", f"{f.get('city', '')} {f.get('pincode', '')}")
                    flash("Address saved.", "success")
            except ShopError as e:
                flash(str(e), "error")
                if f.get("next"):
                    return redirect(safe_next())
                return render_template("addresses.html", addresses=store.addresses(user_id()), states=STATES,
                                       form=f), 400
            return redirect(safe_next("addresses") if f.get("next") else url_for("addresses"))
        return render_template("addresses.html", addresses=store.addresses(user_id()), states=STATES, form={})

    # ======================================================
    # Admin panel  (sign in as admin@example.com / Admin@1234)
    # ======================================================

    @app.route("/admin")
    @admin_required
    def admin_dashboard():
        stats = store.admin_stats()
        return render_template("admin/dashboard.html", st=stats, recent_orders=store.all_orders(limit=8),
                               recent_activity=store.activity(limit=12),
                               low_stock=[p for p in store.products(order="popular") if p.stock <= 10])

    @app.route("/admin/orders")
    @admin_required
    def admin_orders():
        status, q = request.args.get("status", ""), request.args.get("q", "").strip()
        return render_template("admin/orders.html", orders=store.all_orders(status, q), status=status, q=q)

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
                    store.set_order_status(number, status, request.form.get("note", ""))
                    act("Admin: orders", f"Order {number} -> {status}")
                    flash(f"Order status changed to '{status}'. The customer sees it on the tracking page.", "success")
                else:
                    store.cancel_order(o["user_id"], number)
                    act("Admin: orders", f"Admin cancelled order {number}")
                    flash("Order cancelled by admin. Stock returned" +
                          (" and amount refunded to the customer's wallet." if o["payment_method"] != "Cash on Delivery"
                           else "."), "success")
            except ShopError as e:
                flash(str(e), "error")
            return redirect(url_for("admin_order", number=number))
        current = TRACK_STEPS.index(o["status"]) if o["status"] in TRACK_STEPS else len(TRACK_STEPS)
        return render_template("admin/order.html", o=o, steps=store.tracking(o), next_steps=TRACK_STEPS[current + 1:],
                               activity=store.activity(q=number, limit=20))

    @app.route("/admin/customers")
    @admin_required
    def admin_customers():
        q = request.args.get("q", "").strip()
        return render_template("admin/customers.html", customers=store.all_customers(q), q=q)

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
                    bal = store.adjust_wallet(uid, f.get("amount"))
                    act("Admin: customers", f"Wallet of {c['name']} changed by ₹{f.get('amount')} "
                        f"({f.get('reason', '').strip()[:60] or 'no reason'}) -> {rupees(bal)}")
                    flash(f"Wallet updated. New balance: {rupees(bal)}.", "success")
                elif action == "delete":
                    store.delete_user(uid, user_id())
                    act("Admin: customers", f"Deleted account {c['name']} ({c['email']})")
                    flash(f"Account of {c['name']} deleted.", "success")
                    return redirect(url_for("admin_customers"))
            except ShopError as e:
                flash(str(e), "error")
            return redirect(url_for("admin_customer", uid=uid))
        return render_template("admin/customer.html", c=c, orders=store.orders(uid), addresses=store.addresses(uid),
                               activity=store.activity(user_id=uid, limit=50),
                               cart=store.cart_items(store.get_cart(uid)), wishlist=store.wishlist(uid))

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
        if q:
            products = [p for p in products if q.lower() in (p.name + " " + p.brand).lower()]
        with store.db() as conn:
            sold = {r[0]: r[1] for r in conn.execute(
                "SELECT i.product_id, SUM(i.qty) FROM order_items i JOIN orders o ON o.id = i.order_id "
                "WHERE o.cancelled_at = '' GROUP BY i.product_id")}
        return render_template("admin/products.html", products=products, category=cat, show=show, q=q, sold=sold,
                               categories=store.categories())

    def product_form(product=None):
        f = request.form if request.method == "POST" else None
        if f is not None:
            try:
                files = request.files.getlist("images")
                if product is None and not any(x.filename for x in files):
                    raise ShopError("Please upload at least one product picture (JPG or PNG).")
                slug = store.save_product(f, product.id if product else None)
                try:
                    count = images.save_product_images(files, slug, app.static_folder)
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
            f = {"name": product.name, "brand": product.brand, "category": product.category,
                 "price": "%g" % (product.price / 100), "mrp": "%g" % (product.mrp / 100), "stock": product.stock,
                 "about": "\n".join(product.about), "specs": "\n".join(f"{k}: {v}" for k, v in product.specs.items()),
                 "tags": product.tags, "active": "yes" if product.active else "no"}
        return render_template("admin/product_form.html", p=product, form=f or {"active": "yes"},
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
            elif action == "delete":
                store.delete_product(pid)
                if p.art == "upload":
                    images.delete_product_images(p.slug, app.static_folder)
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
                        store.save_category(f.get("name", ""), slug,
                                            images.save_category_image(pic, slug, app.static_folder))
                    act("Admin: products", ("Renamed" if f.get("slug") else "Added") + f" category {f.get('name')}")
                    flash(f"Category '{f.get('name')}' saved.", "success")
            except ShopError as e:
                flash(str(e), "error")
            return redirect(url_for("admin_categories"))
        return render_template("admin/categories.html", categories=store.categories())

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
        return render_template("admin/coupons.html", coupons=store.coupons())

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
        return render_template("admin/reviews.html", reviews=store.all_reviews(q, rating if rating.isdigit() else None),
                               q=q, rating=rating)

    @app.route("/admin/settings", methods=["GET", "POST"])
    @admin_required
    def admin_settings():
        if request.method == "POST":
            try:
                store.update_settings(request.form)
                act("Admin: settings", "Changed shop settings")
                flash("Settings saved. The shop uses them immediately.", "success")
            except ShopError as e:
                flash(str(e), "error")
            return redirect(url_for("admin_settings"))
        return render_template("admin/settings.html", st=store.settings())

    @app.route("/admin/account", methods=["GET", "POST"])
    @admin_required
    def admin_account():
        if request.method == "POST":
            f = request.form
            try:
                if f.get("action") == "password":
                    if f.get("new_password") != f.get("new_password2"):
                        raise ShopError("The two new passwords do not match.")
                    store.change_password(user_id(), f.get("old_password", ""), f.get("new_password", ""))
                    act("Password changed", "Admin changed own password")
                    flash("Your password was changed. Use the new password next time you sign in.", "success")
                else:
                    store.update_user(user_id(), f.get("name", ""), f.get("email", ""), f.get("mobile", ""))
                    act("Profile updated", "Admin updated own name / email / mobile")
                    flash("Your details were saved.", "success")
            except ShopError as e:
                flash(str(e), "error")
            return redirect(url_for("admin_account"))
        return render_template("admin/account.html")

    @app.route("/admin/activity")
    @admin_required
    def admin_activity():
        action, q = request.args.get("action", ""), request.args.get("q", "").strip()
        return render_template("admin/activity.html", rows=store.activity(action, q, limit=500), action=action, q=q,
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
        return render_template("admin/database.html", tables=tables, table=table, cols=cols, rows=rows,
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
        return Response("\ufeff" + out.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename=yo_delivery_{table}.csv"})

    @app.errorhandler(403)
    def forbidden(e):
        return render_template("error.html", code=403, title="Admins only",
                               message="This page is only for store administrators."), 403

    @app.route("/help")
    def help_page():
        return render_template("help.html")

    # ---------------- errors ----------------

    @app.errorhandler(404)
    def not_found(e):
        return render_template("error.html", code=404, title="Page not found",
                               message="Sorry, we couldn't find that page."), 404

    @app.errorhandler(400)
    def bad_request(e):
        return render_template("error.html", code=400, title="Please try again",
                               message="This form has expired. Go back, reload the page and try again."), 400

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")
