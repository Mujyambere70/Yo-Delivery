"""
courier_views.py - the pages of Yo Courier: book and track parcels (customers) and the courier app (/driver).
"""

from flask import abort, flash, jsonify, redirect, render_template, request, url_for

import courier as C
import customs
import geo
import i18n
import images
import places
import world
from core import ShopError


def register(app, store, h):
    login_required, user_id, act = h.login_required, h.user_id, h.act

    def city_list():
        return sorted(places.CITIES)

    # ======================================================
    # Customers
    # ======================================================

    @app.route("/courier")
    def courier_home():
        start = my_place()
        return render_template("courier/home.html", cities=city_list(), KINDS=C.KINDS, SERVICES=C.SERVICES,
                               RATE_CARD=C.RATE_CARD, ZONE_NAMES=C.ZONE_NAMES, countries=world.countries(world.current_lang()),
                               home=[n for n, c in places.CITIES.items() if c[4] == "home"],
                               india=[n for n, c in places.CITIES.items() if c[4] == "india"], stats=geo.stats(),
                               from_cc=start["from_country"], from_city=start["from_city"],
                               from_place=start["from_place"], to_city="Chandigarh" if start["from_country"] == "IN" else "",
                               to_place="Chandigarh" if start["from_country"] == "IN" else "")

    def my_place():
        """Where the signed-in customer is (default address / country), else the main Yo hub."""
        uid = user_id()
        if uid:
            home = store.default_address(uid)
            me = store.get_user(uid)
            if home:
                cc = home["country"] if "country" in home.keys() else "IN"
                pid = home["place_id"] if "place_id" in home.keys() else ""
                p = C.resolve(home["city"], cc, pid)
                if p:
                    return {"from_country": p["cc"], "from_city": p["name"], "from_place": p["id"]}
            if me and me["country"] and me["country"] != "IN":
                return {"from_country": me["country"], "from_city": "", "from_place": ""}
        return {"from_country": "IN", "from_city": "Ambala Cantt", "from_place": "Ambala Cantt"}

    @app.route("/courier/quote", methods=["POST"])
    def courier_quote_api():
        try:
            q = store.courier_quote(request.form)
        except ShopError as e:
            return jsonify(ok=False, error=str(e))
        tr = i18n.translate_text
        return jsonify(ok=True, price=world.price(q["price"]), subtotal=world.price(q["subtotal"]), tax=world.price(q["tax"]),
                       tax_label=q.get("tax_label", ""),
                       lines=[[tr(label), world.price(v)] for label, v in q["lines"]], zone=tr(q["zone_name"]),
                       weight=f"{q['charge_g'] / 1000:g} kg", distance=q["distance_km"],
                       transit=tr(f"{q['transit']} {q['transit_unit'] if q['transit'] != 1 else q['transit_unit'][:-1]}"),
                       from_label=q["from_label"], to_label=q["to_label"])

    @app.route("/courier/book", methods=["GET", "POST"])
    @login_required
    def courier_book():
        uid = user_id()
        f = request.form if request.method == "POST" else request.args
        if request.method == "POST":
            try:
                form = request.form.to_dict()
                for k in ("customs_desc", "customs_qty", "customs_value"):     # version 6: lines of the customs form
                    form[k] = request.form.getlist(k)
                number = store.book_shipment(uid, form)
                act("Courier", f"Booked {number}")
                flash(f"Pickup booked! Your consignment number is {number}.", "success")
                return redirect(url_for("courier_shipment", number=number))
            except ShopError as e:
                flash(str(e), "error")
        me = store.get_user(uid)
        home = store.default_address(uid)
        sender = dict({"from_name": me["name"], "from_mobile": me["mobile"],
                       "from_address": ", ".join(x for x in (home["line1"], home["line2"]) if x) if home else ""},
                      **my_place())
        if home and "lat" in home.keys() and home["lat"] is not None:
            sender.update(from_lat=f"{home['lat']:.5f}", from_lng=f"{home['lng']:.5f}")
        form = dict(sender, **{k: v for k, v in f.items()})
        quote = None
        try:
            if form.get("weight"):
                quote = store.courier_quote(form)
        except ShopError:
            quote = None
        return render_template("courier/book.html", form=form, cities=city_list(), KINDS=C.KINDS, SERVICES=C.SERVICES,
                               CUSTOMS_REASONS=customs.REASONS,
                               countries=world.countries(world.current_lang()), wallet=me["wallet"], quote=quote)

    @app.route("/courier/shipments")
    @login_required
    def courier_shipments():
        return render_template("courier/list.html", rows=store.my_shipments(user_id()))

    @app.route("/courier/s/<number>")
    @login_required
    def courier_shipment(number):
        try:
            s = store.get_shipment(number, user_id())
        except ShopError:
            abort(404)
        return render_template("courier/shipment.html", s=s, owner=True, customs=store.customs_of(s["id"]),
                               track_url=h.base_url().rstrip("/") + url_for("courier_track", number=s["number"]))

    @app.route("/courier/s/<number>/cancel", methods=["POST"])
    @login_required
    def courier_cancel(number):
        try:
            store.cancel_shipment(user_id(), number)
            act("Courier", f"Cancelled {number}")
            flash("The shipment is cancelled.", "success")
        except ShopError as e:
            flash(str(e), "error")
        return redirect(url_for("courier_shipment", number=number))

    @app.route("/courier/s/<number>/rate", methods=["POST"])
    @login_required
    def courier_rate(number):
        try:
            store.rate_shipment(user_id(), number, request.form.get("stars"), request.form.get("feedback", ""))
            flash("Thank you for rating the courier!", "success")
        except ShopError as e:
            flash(str(e), "error")
        return redirect(url_for("courier_shipment", number=number))

    @app.route("/courier/s/<number>/label")
    @login_required
    def courier_label(number):
        try:
            s = store.get_shipment(number, user_id())
        except ShopError:
            abort(404)
        return render_template("courier/label.html", s=s,
                               track_url=h.base_url().rstrip("/") + url_for("courier_track", number=s["number"]))

    @app.route("/courier/track")
    @app.route("/courier/track/<number>")
    def courier_track(number=None):
        number = (number or request.args.get("number", "")).strip().upper()
        s = None
        if number:
            try:
                s = store.get_shipment(number, public=True)
            except ShopError as e:
                flash(str(e), "error")
            if s and user_id() and store.get_shipment(number, public=True)["user_id"] == user_id():
                return redirect(url_for("courier_shipment", number=number))
        return render_template("courier/track.html", s=s, number=number)

    @app.route("/courier/live/<number>.json")
    def courier_live(number):
        try:
            data = store.live(number, user_id())
        except ShopError:
            return jsonify(ok=False), 404
        if data["last"]:
            data["last"]["when"] = world.format_when(data["last"]["at"])
        return jsonify(ok=True, **data)

    # ======================================================
    # The courier app
    # ======================================================

    def courier_or_none():
        return store.courier_of(user_id()) if user_id() else None

    def approved(view):
        from functools import wraps

        @wraps(view)
        def wrapper(*a, **kw):
            if not user_id():
                flash("Please sign in to open the courier app.", "info")
                return redirect(url_for("login", next=request.full_path))
            c = courier_or_none()
            if c is None:
                return redirect(url_for("driver_apply"))
            if c["status"] != "Approved":
                if request.method == "POST" or request.endpoint != "driver_home":
                    flash("Your courier account is not approved yet.", "info")
                    return redirect(url_for("driver_home"))
            request.courier = c
            return view(*a, **kw)
        return wrapper

    @app.route("/driver")
    @approved
    def driver_home():
        c = request.courier
        mine, waiting, done = store.courier_jobs(c["id"]) if c["status"] == "Approved" else ([], [], [])
        return render_template("driver/home.html", c=c, mine=mine, waiting=waiting, done=done,
                               board=store.board_counts(f"courier:{c['id']}"))

    @app.route("/driver/apply", methods=["GET", "POST"])
    @login_required
    def driver_apply():
        if courier_or_none():
            return redirect(url_for("driver_home"))
        f = request.form if request.method == "POST" else {}
        if request.method == "POST":
            try:
                cid = store.apply_courier(user_id(), request.form)
                photo = request.files.get("photo")
                if photo and photo.filename:
                    try:
                        report = images.save_private_photo(photo, f"private/couriers/{cid}.jpg", store, "courier",
                                                           owner=str(user_id()))
                        with store.db() as conn:
                            conn.execute("UPDATE couriers SET photo = ?, photo_check = ? WHERE id = ?",
                                         (f"private/couriers/{cid}.jpg", str(report["score"]), cid))
                        for p in report["problems"]:
                            flash("Photo check: " + p, "info")
                    except ShopError as e:
                        flash(str(e), "error")
                with store.db() as conn:
                    store.auto_task_admin(conn, f"Check the new courier application #{cid}", f"courier:{cid}",
                                          "/admin/couriers", "high")
                act("Courier", "Applied to deliver with Yo")
                flash("Thank you! We will check your application soon.", "success")
                return redirect(url_for("driver_home"))
            except ShopError as e:
                flash(str(e), "error")
        return render_template("driver/apply.html", f=f, cities=city_list(), VEHICLES=C.VEHICLES,
                               countries=world.countries(world.current_lang()))

    @app.route("/driver/online", methods=["POST"])
    @approved
    def driver_online():
        on = request.form.get("online") == "1"
        try:
            store.set_online(request.courier["id"], on)
            flash("You are online. New jobs near you will come here." if on else "You are offline now.", "success")
        except ShopError as e:
            flash(str(e), "error")
        return redirect(url_for("driver_home"))

    @app.route("/driver/ping", methods=["POST"])
    @approved
    def driver_ping():
        c = request.courier
        try:
            if request.form.get("lat"):
                store.courier_ping(c["id"], request.form.get("lat"), request.form.get("lng"))
            else:                                   # no GPS: only "I am still here"
                with store.db() as conn:
                    conn.execute("UPDATE couriers SET last_seen = ? WHERE id = ?", (h.now(), c["id"]))
        except ShopError as e:
            return jsonify(ok=False, error=str(e)), 400
        mine, waiting, _ = store.courier_jobs(c["id"])
        return jsonify(ok=True, jobs=len(mine), waiting=len(waiting))

    @app.route("/driver/job/<number>")
    @approved
    def driver_job(number):
        c = request.courier
        with store.db() as conn:
            row = conn.execute("SELECT * FROM shipments WHERE number = ?", (number,)).fetchone()
        if row is None:
            abort(404)
        mine = row["courier_id"] == c["id"]
        waiting_here = row["courier_id"] is None and (
            row["status"] == C.BOOKED and row["from_city"] == c["city"] or
            row["status"] == C.AT_HUB and row["to_city"] == c["city"] and row["to_country"] == "IN")
        if not mine and not waiting_here and row["pickup_courier_id"] != c["id"]:
            abort(404)
        job = store._job(row)
        if not mine:                                   # before taking a job the courier sees only the area
            for k in ("from_address", "to_address", "from_mobile", "to_mobile"):
                job[k] = ""
        return render_template("driver/job.html", c=c, j=job, mine=mine, REASONS=(
            "Receiver not available", "Address not found", "Receiver refused the parcel", "Receiver asked for another day"))

    @app.route("/driver/job/<number>/<action>", methods=["POST"])
    @approved
    def driver_action(number, action):
        c, f = request.courier, request.form
        try:
            if action == "take":
                store.take_job(c["id"], number)
                flash("The job is yours. Go to the pickup address.", "success")
            elif action == "pickup":
                store.job_pickup(c["id"], number, cash_collected=f.get("cash") == "1")
                with store.db() as conn:
                    store.auto_task_cash(conn, c["id"])
                flash("Picked up. Drive safely!", "success")
            elif action == "handover":
                store.job_handover(c["id"], number)
                flash("Handed over at the Yo hub. Your earning is in Yo Pay.", "success")
            elif action == "deliver":
                photo_name = ""
                photo = request.files.get("photo")
                store.job_deliver(c["id"], number, f.get("code"), f.get("receiver"), photo_name="")
                if photo and photo.filename:
                    try:
                        photo_name = f"private/pod/{number}.jpg"
                        images.save_private_photo(photo, photo_name, store, "pod", owner=number)
                        with store.db() as conn:
                            conn.execute("UPDATE shipments SET pod_photo = ? WHERE number = ?", (photo_name, number))
                    except ShopError as e:
                        flash(str(e), "error")
                if f.get("signature"):                    # version 7: the receiver's signature
                    try:
                        store.save_pod_signature(c["id"], number, f.get("signature"))
                    except ShopError as e:
                        flash(str(e), "error")
                flash("Delivered! Your earning is in your Yo Pay wallet.", "success")
                act("Courier", f"Delivered {number}")
                return redirect(url_for("driver_home"))
            elif action == "fail":
                store.job_fail(c["id"], number, f.get("reason", ""))
                flash("Saved. The parcel goes to the Yo hub.", "info")
                return redirect(url_for("driver_home"))
            else:
                abort(404)
        except ShopError as e:
            flash(str(e), "error")
        return redirect(url_for("driver_job", number=number))
