"""
learn.py - version 5: recommendations learned from the data (mixin class used by Store).

The model: matrix factorization for implicit feedback (alternating least squares, Hu, Koren & Volinsky 2008),
written with NumPy only.
    * the data: every customer (or browser) x every product; a view = 1, wishlist = 2, bought = 3 (added up)
    * confidence c = 1 + 20 * value; preference p = 1 if value > 0 else 0
    * each customer and each product gets a vector of 16 numbers ("factors"); the model finds vectors so that
      customer . product is close to p, trusting cells with a big c more (regularization lambda = 0.1)
    * 12 rounds: fix the products and solve every customer exactly, then fix the customers and solve every product
Quality check before use: for every customer with 3+ products one product is hidden, the model is trained without
it, and we count how often it is in the top 10 ("hit rate @10"). The model replaces the simple rules of
recommend.py only when it is better than "most popular" - otherwise the shop keeps the rules.
It runs once a day (daily tasks) when there is enough data: 30+ customers and 200+ actions.
"""

import json

from core import now

LEARN_SCHEMA = """
CREATE TABLE IF NOT EXISTS rec_factors (
    kind TEXT NOT NULL, key TEXT NOT NULL, vec TEXT NOT NULL, PRIMARY KEY (kind, key)
);
"""
FACTORS, ROUNDS, REG, ALPHA = 16, 12, 0.1, 20.0
MIN_USERS, MIN_ACTIONS = 30, 200
VIEW, WISH, BUY = 1.0, 2.0, 3.0


def als(matrix, factors=FACTORS, rounds=ROUNDS, reg=REG, alpha=ALPHA, seed=7):
    """matrix: users x items (numpy, values >= 0). Returns (user vectors, item vectors)."""
    import numpy as np
    rng = np.random.default_rng(seed)
    n_users, n_items = matrix.shape
    U = rng.normal(0, 0.1, (n_users, factors))
    V = rng.normal(0, 0.1, (n_items, factors))
    P = (matrix > 0).astype(float)
    C = 1.0 + alpha * matrix
    eye = reg * np.eye(factors)
    for _ in range(rounds):
        for X, Y, Pm, Cm in ((U, V, P, C), (V, U, P.T, C.T)):
            YtY = Y.T @ Y
            for i in range(X.shape[0]):
                c = Cm[i]
                nz = c > 1.0
                if not nz.any():
                    X[i] = 0
                    continue
                Yn = Y[nz]
                A = YtY + (Yn.T * (c[nz] - 1.0)) @ Yn + eye
                b = (Yn.T * c[nz]) @ Pm[i][nz]
                X[i] = np.linalg.solve(A, b)
    return U, V


def hit_rate(matrix, k=10, seed=11):
    """Hide one product of every customer with 3+ products; how often is it in the model's top k? (and popular)"""
    import numpy as np
    rng = np.random.default_rng(seed)
    train = matrix.copy()
    tests = []
    for u in range(matrix.shape[0]):
        items = np.flatnonzero(matrix[u])
        if len(items) >= 3:
            i = rng.choice(items)
            train[u, i] = 0
            tests.append((u, i))
    if not tests:
        return 0.0, 0.0, 0
    U, V = als(train)
    popular = np.argsort(-(train > 0).sum(axis=0))
    hits = pop_hits = 0
    for u, i in tests:
        scores = U[u] @ V.T
        scores[train[u] > 0] = -1e9                       # do not recommend what they already have
        hits += i in np.argsort(-scores)[:k]
        pop = [x for x in popular if train[u, x] == 0][:k]
        pop_hits += i in pop
    return hits / len(tests), pop_hits / len(tests), len(tests)


class LearnMixin:

    def _interactions(self):
        """{customer key: {product id: value}} from views, wishlists and orders."""
        data = {}
        with self.db() as conn:
            for r in conn.execute("SELECT COALESCE(CAST(user_id AS TEXT), 'v:' || visitor) AS who, product_id "
                                  "FROM views"):
                d = data.setdefault(r[0], {})
                d[r[1]] = max(d.get(r[1], 0), VIEW)
            for r in conn.execute("SELECT CAST(user_id AS TEXT), product_id FROM wishlist"):
                d = data.setdefault(r[0], {})
                d[r[1]] = max(d.get(r[1], 0), WISH)
            for r in conn.execute("SELECT CAST(o.user_id AS TEXT), i.product_id FROM order_items i JOIN orders o "
                                  "ON o.id = i.order_id WHERE o.cancelled_at = ''"):
                d = data.setdefault(r[0], {})
                d[r[1]] = BUY
        return data

    def learn_recommendations(self, force=False):
        """Train, check and (if it is better than "popular") keep the model. Returns its report."""
        try:
            import numpy as np
        except ImportError:
            return {"used": False, "reason": "NumPy is not installed"}
        data = self._interactions()
        actions = sum(len(v) for v in data.values())
        report = {"trained_at": now(), "users": len(data), "actions": actions, "used": False}
        if not force and (len(data) < MIN_USERS or actions < MIN_ACTIONS):
            report["reason"] = f"Not enough data yet ({MIN_USERS}+ customers and {MIN_ACTIONS}+ actions needed)"
            self._save_rec_report(report)
            return report
        users = sorted(data)
        items = sorted({p for d in data.values() for p in d})
        col = {p: j for j, p in enumerate(items)}
        m = np.zeros((len(users), len(items)))
        for i, u in enumerate(users):
            for p, v in data[u].items():
                m[i, col[p]] = v
        model_hit, pop_hit, tested = hit_rate(m)
        report.update(products=len(items), tested=tested, hit_rate=round(model_hit, 3), popular_hit_rate=round(pop_hit, 3))
        U, V = als(m)
        report["used"] = tested >= 5 and model_hit > pop_hit
        if not report["used"]:
            report["reason"] = "The model is not better than the most popular products yet"
        with self.db() as conn:
            conn.execute("DELETE FROM rec_factors")
            if report["used"]:
                for i, u in enumerate(users):
                    conn.execute("INSERT INTO rec_factors (kind, key, vec) VALUES ('u', ?, ?)",
                                 (u, json.dumps([round(float(x), 5) for x in U[i]])))
                for j, p in enumerate(items):
                    conn.execute("INSERT INTO rec_factors (kind, key, vec) VALUES ('i', ?, ?)",
                                 (str(p), json.dumps([round(float(x), 5) for x in V[j]])))
        self._save_rec_report(report)
        if hasattr(self, "cache_bump"):
            self.cache_bump("model")              # version 6: every process reads the new neighbours
        return report

    def _save_rec_report(self, report):
        with self.db() as conn:
            conn.execute("INSERT INTO settings (key, value) VALUES ('rec_model', ?) "
                         "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (json.dumps(report),))

    def rec_model_report(self):
        with self.db() as conn:
            r = conn.execute("SELECT value FROM settings WHERE key = 'rec_model'").fetchone()
        return json.loads(r[0]) if r else None

    def _vectors(self, kind):
        with self.db() as conn:
            return {r[0]: json.loads(r[1]) for r in conn.execute("SELECT key, vec FROM rec_factors WHERE kind = ?",
                                                                 (kind,))}

    def learned_for(self, visitor, user_id=None, limit=10, exclude=()):
        """Products for one customer from the model, or None when the model has no vector for them."""
        import numpy as np
        with self.db() as conn:
            row = None
            for key in ([str(user_id)] if user_id else []) + [f"v:{visitor}"]:
                row = conn.execute("SELECT vec FROM rec_factors WHERE kind = 'u' AND key = ?", (key,)).fetchone()
                if row:
                    break
        if not row:
            return None
        u = np.array(json.loads(row[0]))
        items = self._vectors("i")
        if not items:
            return None
        cats, brands, bought = self.taste(visitor, user_id)
        skip = set(bought) | set(exclude)
        scores = sorted(((float(u @ np.array(v)), int(p)) for p, v in items.items() if int(p) not in skip),
                        reverse=True)
        ids = [p for _, p in scores[:limit * 2]]
        return [p for p in self.products(ids=ids) if p.stock > 0][:limit]

    def learned_similar(self, product_id, limit=8):
        """Products close to this one in the model (cosine of the product vectors)."""
        try:
            import numpy as np
        except ImportError:
            return []
        def neighbours():
            items = self._vectors("i")
            me = items.get(str(product_id))
            if not me:
                return []
            a = np.array(me)
            na = np.linalg.norm(a) or 1.0
            scores = []
            for p, v in items.items():
                if p == str(product_id):
                    continue
                b = np.array(v)
                scores.append((float(a @ b / (na * (np.linalg.norm(b) or 1.0))), int(p)))
            scores.sort(reverse=True)
            return [p for s, p in scores[:limit] if s > 0.2]
        ids = self.cached("model", f"sim:{product_id}:{limit}", 3600, neighbours) if hasattr(self, "cached") \
            else neighbours()                         # version 6: kept an hour, shared by all server processes
        if not ids:
            return []
        found = {p.id: p for p in self.products(ids=ids)}
        return [found[i] for i in ids if i in found]
