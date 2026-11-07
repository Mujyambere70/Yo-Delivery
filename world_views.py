"""
world_views.py - version 5: the places of the world as JSON for the forms (search a town or a village, the
places near a point), the road route between two places (OpenStreetMap / OSRM with a cache) and the page
"Places we deliver to" (/places).
"""

from flask import abort, jsonify, render_template, request

import geo
import i18n
import world


def place_json(p, lang=None):
    if not p:
        return None
    lang = lang or world.current_lang()
    return {"id": p["id"], "name": p["name"], "label": p["label"], "short": p["short"], "cc": p["cc"],
            "country": p["country_name"], "region": p["region"], "district": p["district"], "area": p["area"],
            "pin": p["pin"] or "", "lat": p["lat"], "lng": p["lng"], "kind": p["kind"],
            "kind_name": i18n.translate_text(geo.KIND_NAMES.get(p["kind"], p["kind"]), lang),
            "approx": bool(p["approx"]), "tz": p["tz"]}


def register(app, store, h):

    @app.route("/places/search")
    def places_search():
        q = request.args.get("q", "")[:80]
        cc = request.args.get("cc", "").upper()[:2] or None
        kinds = [k for k in request.args.get("kinds", "").split(",") if k in geo.KIND_RANK] or None
        try:
            limit = max(1, min(20, int(request.args.get("limit", 10))))
        except ValueError:
            limit = 10
        lang = world.current_lang()
        hits = geo.search(q, cc=cc, limit=limit, lang=lang, kinds=kinds) if len(q.strip()) >= 2 else []
        resp = jsonify(places=[place_json(p, lang) for p in hits])
        resp.headers["Cache-Control"] = "public, max-age=3600"
        return resp

    @app.route("/places/near")
    def places_near():
        try:
            lat, lng = float(request.args["lat"]), float(request.args["lng"])
        except (KeyError, ValueError):
            abort(400)
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            abort(400)
        p = geo.nearest_place(lat, lng)
        towns = geo.nearby(lat, lng, km=30, limit=8, min_rank=3)
        return jsonify(place=place_json(p), towns=[place_json(t) for t in towns])

    @app.route("/places/check-address")
    def places_check_address():
        """Version 6: is the postal code right for the country (and for the place)? For the address forms."""
        import addresscheck
        a = request.args
        r = addresscheck.check(a.get("country", "IN")[:2], a.get("postcode", "")[:12], a.get("place", "")[:40],
                               a.get("city", "")[:80])
        lang = world.current_lang()
        return jsonify(errors=[i18n.translate_text(x, lang) for x in r["errors"]],
                       warnings=[i18n.translate_text(x, lang) for x in r["warnings"]],
                       hint=r["hint"] if r["hint"].startswith("PIN") else i18n.translate_text(r["hint"], lang),
                       example=r["example"])

    @app.route("/places/<place_id>.json")
    def place_info(place_id):
        p = geo.get(place_id[:40])
        if not p:
            abort(404)
        return jsonify(place=place_json(p))

    @app.route("/places/route")
    def places_route():
        """Road distance and the line of the road between two places (for the courier quote and the map)."""
        a, b = geo.get(request.args.get("a", "")[:40]), geo.get(request.args.get("b", "")[:40])
        if not a or not b:
            abort(404)
        r = store.road_route((a["lat"], a["lng"]), (b["lat"], b["lng"]))
        return jsonify(km=r["km"], source=r["source"], line=r["line"], a=place_json(a), b=place_json(b))

    @app.route("/places")
    def places_page():
        """Places we deliver to: search box + the numbers of the data set."""
        stats = geo.stats()
        q = request.args.get("q", "").strip()[:80]
        cc = request.args.get("cc", "").upper()[:2] or None
        hits = geo.search(q, cc=cc, limit=20, lang=world.current_lang()) if len(q) >= 2 else []
        return render_template("places.html", stats=stats, q=q, cc=cc or "", hits=hits, KIND_NAMES=geo.KIND_NAMES,
                               countries=sorted(((c, world.country_name(c, world.current_lang())) for c in geo.countries()),
                                                key=lambda x: x[1]))
