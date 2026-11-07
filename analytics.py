"""
analytics.py - version 7 (mixin class used by Store): numbers for managers, forecasts for the hubs, A/B tests.

    warehouse     a small data warehouse: one table `fact_daily` (day, country, place, metric, value), rebuilt every
                  night from the shop tables for the last 90 days (orders, revenue, parcels booked and delivered,
                  tickets, new customers, parcels per hub). Managers read fast summaries from it instead of heavy
                  queries on the live tables; a manager sees only the countries and hubs of his branch; CSV export.
    forecast      parcels per day of a hub for the next 14 days with Holt-Winters (exponential smoothing with a trend
                  and a weekly season); the smoothing constants are chosen by the smallest error on the past, and the
                  model is tested on the last 14 days against "same day last week" (MAPE). The forecast gives the
                  workers needed per day (parcels a worker handles in a shift: setting `parcels_per_worker`).
    A/B tests     a visitor is put in group A or B by a hash of the test name and his id (always the same group);
                  for "similar products" A = the learned model + texts and pictures (version 6), B = texts and pictures
                  only. Views and clicks are counted; the two click rates are compared with a two-proportion z-test.
                  With fewer than 100 views per group the page says "not enough data yet".
"""

import csv
import hashlib
import io
import math
from datetime import date, datetime, timedelta

from core import ShopError, now

ANALYTICS_SCHEMA = """
CREATE TABLE IF NOT EXISTS fact_daily (
    day TEXT NOT NULL, country TEXT NOT NULL DEFAULT '', place TEXT NOT NULL DEFAULT '', metric TEXT NOT NULL,
    value INTEGER NOT NULL, source TEXT NOT NULL DEFAULT 'shop', PRIMARY KEY (day, country, place, metric, source)
);
CREATE INDEX IF NOT EXISTS idx_fact_metric ON fact_daily(metric, day);
CREATE TABLE IF NOT EXISTS experiments (
    key TEXT PRIMARY KEY, name TEXT NOT NULL, variants TEXT NOT NULL DEFAULT 'A B', active INTEGER NOT NULL DEFAULT 1,
    note TEXT NOT NULL DEFAULT '', started_at TEXT NOT NULL, stopped_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS exp_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL, unit TEXT NOT NULL, variant TEXT NOT NULL,
    kind TEXT NOT NULL, product_id INTEGER NOT NULL DEFAULT 0, at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_exp_events ON exp_events(key, variant, kind);
"""

METRICS = {"orders": "Orders", "revenue": "Shop revenue", "parcels": "Parcels booked",
           "delivered": "Parcels delivered", "tickets": "Tickets sold", "ticket_revenue": "Ticket revenue",
           "customers": "New customers", "hub_parcels": "Parcels through the hub"}
SEASON = 7
MIN_VIEWS = 100


# ---------------------------------------------------------------- Holt-Winters
def holt_winters(y, alpha, beta, gamma, m=SEASON, h=14):
    """Additive Holt-Winters. Returns (one-step fitted values, forecast of h days)."""
    if len(y) < 2 * m:
        raise ValueError("at least two seasons are needed")
    level = sum(y[:m]) / m
    trend = (sum(y[m:2 * m]) - sum(y[:m])) / (m * m)
    season = [y[i] - level for i in range(m)]
    fitted = []
    for t in range(len(y)):
        s = season[t % m]
        fitted.append(level + trend + s)
        last_level = level
        level = alpha * (y[t] - s) + (1 - alpha) * (level + trend)
        trend = beta * (level - last_level) + (1 - beta) * trend
        season[t % m] = gamma * (y[t] - level) + (1 - gamma) * s
    n = len(y)
    forecast = [max(0.0, level + (k + 1) * trend + season[(n + k) % m]) for k in range(h)]
    return fitted, forecast


def fit_holt_winters(y, h=14):
    """Grid search of the smoothing constants (smallest squared one-step error after the first season)."""
    best = None
    for a in (0.1, 0.2, 0.3, 0.5, 0.7):
        for b in (0.0, 0.05, 0.1):
            for g in (0.1, 0.2, 0.3, 0.5):
                fitted, fc = holt_winters(y, a, b, g, h=h)
                sse = sum((y[t] - fitted[t]) ** 2 for t in range(SEASON, len(y)))
                if best is None or sse < best[0]:
                    best = (sse, (a, b, g), fc)
    return best[1], best[2]


def mape(actual, pred):
    pairs = [(a, p) for a, p in zip(actual, pred) if a > 0]
    return round(100 * sum(abs(a - p) / a for a, p in pairs) / len(pairs), 1) if pairs else None


def backtest(y, h=14):
    """Holt-Winters against 'same day last week' on the last h days."""
    if len(y) < 2 * SEASON + h:
        return None
    train, test = y[:-h], y[-h:]
    _, fc = fit_holt_winters(train, h)
    naive = [train[-SEASON + (k % SEASON)] for k in range(h)]
    return {"model": mape(test, fc), "naive": mape(test, naive), "days": h}


# ---------------------------------------------------------------- z-test
def two_proportion_z(c1, n1, c2, n2):
    """(z, two-sided p) of the difference of two click rates."""
    if min(n1, n2) == 0:
        return None, None
    p = (c1 + c2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se == 0:
        return 0.0, 1.0
    z = (c1 / n1 - c2 / n2) / se
    return round(z, 3), round(math.erfc(abs(z) / math.sqrt(2)), 4)


def variant_of(key, unit, variants=("A", "B")):
    h = int(hashlib.sha256(f"{key}:{unit}".encode()).hexdigest()[:8], 16)
    return variants[h % len(variants)]


class AnalyticsMixin:

    # ---------------------------------------------------------------- warehouse
    def build_warehouse(self, days=90, today=None):
        """Rebuild the shop facts of the last `days` days (the demo history, source 'demo', is kept)."""
        today = today or date.today()
        first = (today - timedelta(days=days - 1)).isoformat()
        end = (today + timedelta(days=1)).isoformat()
        rows = []
        with self.db() as conn:
            for r in conn.execute("SELECT substr(placed_at, 1, 10) AS d, country, COUNT(*) AS n, SUM(total) AS v "
                                  "FROM orders WHERE placed_at >= ? AND placed_at < ? AND (cancelled_at IS NULL OR cancelled_at = '') "
                                  "GROUP BY substr(placed_at, 1, 10), country", (first, end)):
                rows += [(r["d"], r["country"] or "IN", "", "orders", r["n"]),
                         (r["d"], r["country"] or "IN", "", "revenue", int(r["v"] or 0))]
            for r in conn.execute("SELECT substr(created_at, 1, 10) AS d, from_country AS c, COUNT(*) AS n FROM shipments "
                                  "WHERE created_at >= ? AND created_at < ? AND status != 'Cancelled' "
                                  "GROUP BY substr(created_at, 1, 10), from_country", (first, end)):
                rows.append((r["d"], r["c"] or "IN", "", "parcels", r["n"]))
            for r in conn.execute("SELECT substr(delivered_at, 1, 10) AS d, to_country AS c, COUNT(*) AS n FROM shipments "
                                  "WHERE delivered_at >= ? AND delivered_at < ? GROUP BY substr(delivered_at, 1, 10), "
                                  "to_country", (first, end)):
                rows.append((r["d"], r["c"] or "IN", "", "delivered", r["n"]))
            for r in conn.execute("SELECT substr(created_at, 1, 10) AS d, COUNT(*) AS n, SUM(total) AS v FROM "
                                  "ticket_bookings WHERE created_at >= ? AND created_at < ? AND status IN "
                                  "('Confirmed', 'Completed') GROUP BY substr(created_at, 1, 10)", (first, end)):
                rows += [(r["d"], "", "", "tickets", r["n"]), (r["d"], "", "", "ticket_revenue", int(r["v"] or 0))]
            for r in conn.execute("SELECT substr(created_at, 1, 10) AS d, country, COUNT(*) AS n FROM users WHERE "
                                  "created_at >= ? AND created_at < ? AND is_admin = 0 GROUP BY substr(created_at, 1, 10), "
                                  "country", (first, end)):
                rows.append((r["d"], r["country"] or "IN", "", "customers", r["n"]))
            hubs = [dict(o) for o in conn.execute("SELECT id, place, country FROM offices WHERE kind IN ('hub', 'warehouse') "
                                                  "AND place != '' AND active = 1")]
            for h in hubs:
                for r in conn.execute("SELECT substr(created_at, 1, 10) AS d, COUNT(*) AS n FROM shipments WHERE "
                                      "(from_place = ? OR to_place = ?) AND created_at >= ? AND created_at < ? "
                                      "GROUP BY substr(created_at, 1, 10)", (h["place"], h["place"], first, end)):
                    rows.append((r["d"], h["country"], h["place"], "hub_parcels", r["n"]))
            conn.execute("DELETE FROM fact_daily WHERE source = 'shop' AND day >= ?", (first,))
            for d, c, p, m, v in rows:
                conn.execute("INSERT INTO fact_daily (day, country, place, metric, value, source) VALUES "
                             "(?, ?, ?, ?, ?, 'shop') ON CONFLICT (day, country, place, metric, source) DO UPDATE SET "
                             "value = fact_daily.value + excluded.value", (d, c, p, m, v))
        return len(rows)

    def _branch_scope(self, user_id):
        """(countries, hub places) a viewer may see; (None, None) = everything."""
        u = self.get_user(user_id)
        if u and u["is_admin"]:
            return None, None
        w = self.worker_of(user_id)
        if not w or w["status"] != "Active":
            return set(), set()
        offs = [o for o in self.offices() if o["id"] in self.subtree(w["office_id"])]
        countries = {o["country"] for o in offs if o["country"]}
        if any(o["kind"] == "head" for o in offs):
            return None, None
        return countries, {o["place"] for o in offs if o["place"]}

    def analytics(self, user_id, days=28, today=None):
        today = today or date.today()
        first = (today - timedelta(days=days - 1)).isoformat()
        countries, places = self._branch_scope(user_id)
        with self.db() as conn:
            rows = [dict(r) for r in conn.execute("SELECT * FROM fact_daily WHERE day >= ? AND day <= ? ORDER BY day",
                                                  (first, today.isoformat()))]
        if countries is not None:
            rows = [r for r in rows if (r["metric"] == "hub_parcels" and r["place"] in places)
                    or (r["metric"] != "hub_parcels" and r["country"] in countries)]
        totals, by_day, by_country = {}, {}, {}
        for r in rows:
            totals[r["metric"]] = totals.get(r["metric"], 0) + r["value"]
            by_day.setdefault(r["day"], {}).setdefault(r["metric"], 0)
            by_day[r["day"]][r["metric"]] += r["value"]
            if r["country"]:
                k = (r["country"], r["metric"])
                by_country[k] = by_country.get(k, 0) + r["value"]
        day_list = [(today - timedelta(days=days - 1 - i)).isoformat() for i in range(days)]
        return {"days": day_list, "totals": totals, "by_day": by_day,
                "countries": sorted({c for c, _ in by_country}),
                "by_country": by_country, "scope": "all" if countries is None else ", ".join(sorted(countries)) or "-",
                "demo": any(r["source"] == "demo" for r in rows)}

    def analytics_csv(self, user_id, days=90):
        a = self.analytics(user_id, days)
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["day"] + list(METRICS))
        for d in a["days"]:
            w.writerow([d] + [a["by_day"].get(d, {}).get(m, 0) for m in METRICS])
        return out.getvalue()

    # ---------------------------------------------------------------- forecast
    def hub_series(self, place, days=84, today=None):
        today = today or date.today()
        first = today - timedelta(days=days)
        with self.db() as conn:
            got = {r["day"]: r["v"] for r in conn.execute(
                "SELECT day, SUM(value) AS v FROM fact_daily WHERE metric = 'hub_parcels' AND place = ? AND day >= ? "
                "AND day < ? GROUP BY day", (place, first.isoformat(), today.isoformat()))}
        return [((first + timedelta(days=i)).isoformat(), int(got.get((first + timedelta(days=i)).isoformat(), 0)))
                for i in range(days)]

    def hub_forecast(self, user_id, office_id, h=14, today=None):
        today = today or date.today()
        o = next((x for x in self.offices() if x["id"] == int(office_id)), None)
        if o is None or not o["place"]:
            raise ShopError("Office not found.")
        countries, places = self._branch_scope(user_id)
        if places is not None and o["place"] not in places:
            raise ShopError("This office is not in your branch.")
        series = self.hub_series(o["place"], today=today)
        y = [v for _, v in series]
        while y and y[0] == 0 and len(y) > 2 * SEASON + h:      # skip the empty start
            y.pop(0)
            series.pop(0)
        if sum(1 for v in y if v) < 2 * SEASON:
            return {"office": o, "ready": False, "series": series}
        params, fc = fit_holt_winters([float(v) for v in y], h)
        per_worker = max(1, int(self.setting("parcels_per_worker") or 120))
        days = [(today + timedelta(days=k)).isoformat() for k in range(h)]
        rows = [{"day": d, "parcels": round(v), "workers": math.ceil(round(v) / per_worker) if v > 0 else 0}
                for d, v in zip(days, fc)]
        return {"office": o, "ready": True, "series": series[-28:], "forecast": rows, "params": params,
                "test": backtest([float(v) for v in y], h), "per_worker": per_worker}

    # ---------------------------------------------------------------- A/B tests
    def ensure_experiments(self):
        with self.db() as conn:
            conn.execute("INSERT INTO experiments (key, name, variants, note, started_at) VALUES (?, ?, 'A B', ?, ?) "
                         "ON CONFLICT (key) DO NOTHING",
                         ("similar_products", "Similar products on the product page",
                          "A: learned model + texts and pictures; B: texts and pictures only", now()))

    def experiment(self, key):
        with self.db() as conn:
            r = conn.execute("SELECT * FROM experiments WHERE key = ?", (key,)).fetchone()
        return dict(r) if r else None

    def ab_variant(self, key, unit):
        e = self.experiment(key)
        if not e or not e["active"] or not unit:
            return None
        return variant_of(key, unit, tuple(e["variants"].split()))

    def ab_event(self, key, unit, variant, kind, product_id=0):
        if kind not in ("view", "click", "cart") or not unit or not variant:
            return
        with self.db() as conn:
            conn.execute("INSERT INTO exp_events (key, unit, variant, kind, product_id, at) VALUES (?, ?, ?, ?, ?, ?)",
                         (key, str(unit)[:64], variant[:4], kind, int(product_id or 0), now()))

    def ab_results(self, key):
        e = self.experiment(key)
        if e is None:
            return None
        with self.db() as conn:
            counts = {(r["variant"], r["kind"]): r["n"] for r in conn.execute(
                "SELECT variant, kind, COUNT(*) AS n FROM exp_events WHERE key = ? GROUP BY variant, kind", (key,))}
        vs = e["variants"].split()
        groups = []
        for v in vs:
            views, clicks = counts.get((v, "view"), 0), counts.get((v, "click"), 0)
            groups.append({"variant": v, "views": views, "clicks": clicks, "carts": counts.get((v, "cart"), 0),
                           "rate": round(100 * clicks / views, 2) if views else None})
        z = p = None
        enough = len(groups) == 2 and all(g["views"] >= MIN_VIEWS for g in groups)
        if len(groups) == 2:
            z, p = two_proportion_z(groups[0]["clicks"], groups[0]["views"], groups[1]["clicks"], groups[1]["views"])
        verdict = "Not enough data yet (at least 100 views in each group)."
        if enough and p is not None:
            if p < 0.05:
                best = max(groups, key=lambda g: g["rate"] or 0)
                verdict = f"Group {best['variant']} is better (p = {p})."
            else:
                verdict = f"No clear difference yet (p = {p})."
        return {**e, "groups": groups, "z": z, "p": p, "enough": enough, "verdict": verdict}

    def set_experiment_active(self, user_id, key, active):
        if not self.can(user_id, "model.manage"):
            raise ShopError("You do not have the ability to do this. Ask your manager.")
        with self.db() as conn:
            conn.execute("UPDATE experiments SET active = ?, stopped_at = ? WHERE key = ?",
                         (1 if active else 0, "" if active else now(), key))

    def seed_demo_history(self, today=None):
        """Demo only: 8 weeks of parcels through the demo hubs (source 'demo'), so the forecast has a past.
        A weekly rhythm (busy Monday, quiet Sunday), a slow growth and some noise; marked as demo on the pages."""
        import random
        today = today or date.today()
        rng = random.Random(7)
        week = [1.25, 1.1, 1.0, 1.05, 1.15, 0.8, 0.55]
        with self.db() as conn:
            hubs = [dict(o) for o in conn.execute("SELECT place, country FROM offices WHERE kind = 'hub' AND place != ''")]
            for i, hb in enumerate(hubs):
                base = 140 if hb["country"] == "IN" else 60
                for k in range(56, 0, -1):
                    d = today - timedelta(days=k)
                    v = base * (1 + 0.004 * (56 - k)) * week[d.weekday()] * rng.uniform(0.88, 1.12)
                    conn.execute("INSERT INTO fact_daily (day, country, place, metric, value, source) VALUES "
                                 "(?, ?, ?, 'hub_parcels', ?, 'demo') ON CONFLICT (day, country, place, metric, source) "
                                 "DO NOTHING", (d.isoformat(), hb["country"], hb["place"], int(round(v))))
