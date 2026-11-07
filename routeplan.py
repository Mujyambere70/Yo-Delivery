"""
routeplan.py - version 6: the order of the stops when a courier has several parcels at the same time.

A stop is a pickup (at the sender) or a drop (at the receiver, or at the Yo hub for parcels to another town).
The drop of a parcel can only come after its pickup. The plan starts at the courier's position, always goes to
the nearest stop that is allowed now (nearest neighbour), and then improves the order with 2-opt moves (turn
around a piece of the route when that makes it shorter and keeps every pickup before its drop).
Distances are straight lines x 1.3 (the usual road factor), so the plan needs no internet; the map shows it.

Version 7: delivery time windows. A receiver can choose when he is at home (9-12, 12-15, 15-18, 18-21). The plan
then also counts the time: 25 km/h between stops and 5 minutes at each stop, waiting when it is too early. The
nearest stop is not taken when that would make another stop late; 2-opt keeps a change only when it makes no stop
later (fewer late minutes first, then fewer km). With ROUTING_URL (an OSRM server) the km can be real road km.
"""

import places
import courier as C

ROAD = 1.3


def _km(a, b):
    return places.haversine_km(a, b) * ROAD


def route_length(start, stops):
    pos, total = start, 0.0
    for s in stops:
        total += _km(pos, s["point"])
        pos = s["point"]
    return total


def _valid(order):
    seen = set()
    for s in order:
        if s["kind"] == "drop" and s["needs"] and s["needs"] not in seen:
            return False
        seen.add(s["id"])
    return True


SPEED_KMH = 25
STOP_MIN = 5
WINDOWS = {"": None, "09-12": (540, 720), "12-15": (720, 900), "15-18": (900, 1080), "18-21": (1080, 1260)}


def timeline(start, order, t0):
    """[(arrival minute, minutes late), ...] and the total late minutes, starting at minute t0 of the day."""
    pos, t, late, out = start, float(t0), 0.0, []
    for s in order:
        t += _km(pos, s["point"]) / SPEED_KMH * 60
        w = s.get("window")
        if w and t < w[0]:
            t = float(w[0])                                # too early: wait until the window opens
        lt = max(0.0, t - w[1]) if w else 0.0
        out.append((t, lt))
        late += lt
        t += STOP_MIN
        pos = s["point"]
    return out, late


def _cost(start, order, t0):
    late = timeline(start, order, t0)[1] if t0 is not None else 0.0
    return round(late, 1), route_length(start, order)


def plan(start, stops, t0=None):
    """start: (lat, lng); stops: dicts with id, kind ('pickup'/'drop'), needs (id of the pickup or None), point,
    and (version 7) an optional window (first minute, last minute). t0: the minute of the day now (None: no times)."""
    todo, order, pos, done, t = list(stops), [], start, set(), float(t0 or 0)
    while todo:
        ready = [s for s in todo if s["kind"] == "pickup" or not s["needs"] or s["needs"] in done]
        nxt = min(ready, key=lambda s: _km(pos, s["point"]))
        if t0 is not None:                                 # would the nearest stop make a window stop late?
            urgent = []
            for s in ready:
                w = s.get("window")
                if s is nxt or not w:
                    continue
                via = t + (_km(pos, nxt["point"]) + _km(nxt["point"], s["point"])) / SPEED_KMH * 60 + STOP_MIN
                direct = t + _km(pos, s["point"]) / SPEED_KMH * 60
                if via > w[1] >= direct:
                    urgent.append(s)
            if urgent:
                nxt = min(urgent, key=lambda s: s["window"][1])
        t += _km(pos, nxt["point"]) / SPEED_KMH * 60
        if nxt.get("window") and t < nxt["window"][0]:
            t = float(nxt["window"][0])
        t += STOP_MIN
        order.append(nxt)
        done.add(nxt["id"])
        todo.remove(nxt)
        pos = nxt["point"]
    better = True
    while better:                                         # 2-opt with the pickup-before-drop rule (and the windows)
        better = False
        best = _cost(start, order, t0)
        for i in range(len(order) - 1):
            for j in range(i + 1, len(order)):
                cand = order[:i] + order[i:j + 1][::-1] + order[j + 1:]
                if not _valid(cand):
                    continue
                c = _cost(start, cand, t0)
                if c[0] < best[0] or (c[0] == best[0] and c[1] < best[1] - 0.01):
                    order, best, better = cand, c, True
    return order


class RoutePlanMixin:
    def courier_route_plan(self, courier_id):
        """The stops of all the jobs of a courier in the best order, with the km of each leg."""
        mine, _, _ = self.courier_jobs(courier_id)
        with self.db() as conn:
            c = conn.execute("SELECT * FROM couriers WHERE id = ?", (courier_id,)).fetchone()
        home = C.place_pos(c["place"] or c["city"]) if c else None
        stops = []
        for j in mine:
            src = (j["from_lat"], j["from_lng"])
            dst = (j["to_lat"], j["to_lng"])
            hub = home or src
            to_hub = j["leg"] == "pickup"
            drop_point, drop_name = (hub, "Yo hub") if to_hub else (dst, j["to_address"])
            drop_city = j["from_city"] if to_hub else j["to_city"]
            window = WINDOWS.get(j.get("time_window") or "") if not to_hub else None
            if j["next"] == "pickup":
                stops.append({"id": j["number"] + ":p", "kind": "pickup", "needs": None, "point": src,
                              "number": j["number"], "address": j["from_address"], "city": j["from_city"],
                              "who": j["from_name"]})
                stops.append({"id": j["number"] + ":d", "kind": "drop", "needs": j["number"] + ":p", "point": drop_point,
                              "number": j["number"], "address": drop_name, "city": drop_city,
                              "who": "Yo hub" if to_hub else j["to_name"], "window": window})
            else:
                stops.append({"id": j["number"] + ":d", "kind": "drop", "needs": None, "point": drop_point,
                              "number": j["number"], "address": drop_name, "city": drop_city,
                              "who": "Yo hub" if to_hub else j["to_name"], "window": window})
        if not stops:
            return {"stops": [], "km": 0, "plain_km": 0, "saved_km": 0, "start": None}
        start = (c["lat"], c["lng"]) if c and c["lat"] is not None else stops[0]["point"]
        t0 = None
        if any(s.get("window") for s in stops):
            local = self.local_now(c["place"] or c["city"]) if c else None
            t0 = (local.hour * 60 + local.minute) if local else None
        order = plan(start, stops, t0)
        km_plain = route_length(start, sorted(stops, key=lambda s: (s["number"], s["kind"] != "pickup")))
        pos, total = start, 0.0
        times = timeline(start, order, t0)[0] if t0 is not None else [(None, 0)] * len(order)
        for n, (s, (eta, late)) in enumerate(zip(order, times), 1):
            leg = _km(pos, s["point"])
            total += leg
            s.update(n=n, leg_km=round(leg, 1), total_km=round(total, 1),
                     eta=f"{int(eta) // 60 % 24:02d}:{int(eta) % 60:02d}" if eta is not None else "",
                     late_min=int(late), window_text=(f"{s['window'][0] // 60:02d}:00-{s['window'][1] // 60:02d}:00"
                                                      if s.get("window") else ""))
            pos = s["point"]
        return {"stops": order, "km": round(total, 1), "plain_km": round(km_plain, 1),
                "saved_km": round(max(0.0, km_plain - total), 1), "start": start,
                "late": sum(1 for s in order if s["late_min"] > 0)}

    def local_now(self, place_id):
        """The time now in the time zone of a place (None when not known)."""
        from datetime import datetime
        from zoneinfo import ZoneInfo
        import geo
        p = geo.get(place_id) if place_id else None
        tz = (p or {}).get("tz") or ("Asia/Kolkata" if place_id and not p else None)
        try:
            return datetime.now(ZoneInfo(tz)) if tz else None
        except Exception:                                  # an unknown zone name
            return None
