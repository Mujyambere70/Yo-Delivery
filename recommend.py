"""
recommend.py - product recommendations from what customers viewed and bought (mixin class used by Store).

Three simple, explainable methods (no black box):

  1. "Frequently bought together"  - products that were in the same orders as this product
                                      (co-occurrence count over all orders that were not cancelled).
  2. "Customers who viewed this also viewed" - products seen by the same visitors as this product.
  3. "Recommended for you" - a taste profile of one visitor: every category and brand gets points
        viewed product = 1, wishlist = 2, bought = 3
     then each product the visitor has not bought gets
        score = category points + brand points (x0.5) + rating / 5 (popular tie-breaker)
     New visitors (no history yet) get the best-rated popular products.

A visitor is the browser (a random id in the session); after sign-in the user id is used too,
so the history follows the customer on every device.
"""

from collections import Counter, defaultdict

from core import now

VIEW_POINTS, WISH_POINTS, BUY_POINTS = 1, 2, 3


class RecommendMixin:

    def record_view(self, visitor, user_id, product_id):
        with self.db() as conn:
            last = conn.execute("SELECT product_id FROM views WHERE visitor = ? ORDER BY id DESC LIMIT 1",
                                (visitor,)).fetchone()
            if last and last[0] == product_id:          # reloading the page is not a new view
                return
            conn.execute("INSERT INTO views (visitor, user_id, product_id, at) VALUES (?, ?, ?, ?)",
                         (visitor, user_id, product_id, now()))

    def link_visitor(self, visitor, user_id):
        """After sign-in, the views made before signing in belong to the customer too."""
        with self.db() as conn:
            conn.execute("UPDATE views SET user_id = ? WHERE visitor = ? AND user_id IS NULL", (user_id, visitor))

    def recently_viewed(self, visitor, user_id=None, limit=10):
        with self.db() as conn:
            if user_id:
                rows = conn.execute("SELECT product_id, MAX(id) AS last FROM views WHERE user_id = ? OR visitor = ? "
                                    "GROUP BY product_id ORDER BY last DESC LIMIT ?", (user_id, visitor, limit))
            else:
                rows = conn.execute("SELECT product_id, MAX(id) AS last FROM views WHERE visitor = ? "
                                    "GROUP BY product_id ORDER BY last DESC LIMIT ?", (visitor, limit))
            ids = [r[0] for r in rows]
        return self.products(ids=ids)

    def bought_together(self, product_id, limit=6):
        with self.db() as conn:
            rows = conn.execute(
                "SELECT b.product_id, COUNT(*) AS n FROM order_items a "
                "JOIN order_items b ON b.order_id = a.order_id AND b.product_id != a.product_id "
                "JOIN orders o ON o.id = a.order_id "
                "WHERE a.product_id = ? AND o.cancelled_at = '' GROUP BY b.product_id ORDER BY n DESC, b.product_id "
                "LIMIT ?", (product_id, limit)).fetchall()
        return self.products(ids=[r[0] for r in rows])

    def also_viewed(self, product_id, limit=8):
        with self.db() as conn:
            rows = conn.execute(
                "SELECT b.product_id, COUNT(DISTINCT b.visitor) AS n FROM views a "
                "JOIN views b ON b.visitor = a.visitor AND b.product_id != a.product_id "
                "WHERE a.product_id = ? GROUP BY b.product_id ORDER BY n DESC, b.product_id LIMIT ?",
                (product_id, limit)).fetchall()
        return self.products(ids=[r[0] for r in rows])

    def taste(self, visitor, user_id=None):
        """Points per category and per brand, and the products already bought."""
        cats, brands, bought = Counter(), Counter(), set()
        with self.db() as conn:
            if user_id:
                viewed = conn.execute("SELECT product_id FROM views WHERE user_id = ? OR visitor = ?",
                                      (user_id, visitor)).fetchall()
                wished = conn.execute("SELECT product_id FROM wishlist WHERE user_id = ?", (user_id,)).fetchall()
                bought = {r[0] for r in conn.execute(
                    "SELECT i.product_id FROM order_items i JOIN orders o ON o.id = i.order_id "
                    "WHERE o.user_id = ? AND o.cancelled_at = ''", (user_id,))}
            else:
                viewed = conn.execute("SELECT product_id FROM views WHERE visitor = ?", (visitor,)).fetchall()
                wished = []
            info = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT id, category, brand FROM products")}
        for pid, points in ([(r[0], VIEW_POINTS) for r in viewed] + [(r[0], WISH_POINTS) for r in wished] +
                            [(pid, BUY_POINTS) for pid in bought]):
            if pid in info:
                cats[info[pid][0]] += points
                brands[info[pid][1]] += points
        return cats, brands, bought

    def recommended(self, visitor, user_id=None, limit=10, exclude=()):
        try:                                     # version 5: the learned model first (learn.py), when it is in use
            learned = self.learned_for(visitor, user_id, limit, exclude)
        except Exception:
            learned = None
        if learned:
            return learned
        cats, brands, bought = self.taste(visitor, user_id)
        items = self.products(order="popular")
        if not cats:
            return sorted(items, key=lambda p: (-p.rating, -p.rating_count))[:limit]
        scored = defaultdict(float)
        for p in items:
            if p.id in bought or p.id in exclude or p.stock <= 0:
                continue
            scored[p.id] = cats.get(p.category, 0) + 0.5 * brands.get(p.brand, 0) + p.rating / 5
        best = sorted(scored, key=lambda pid: -scored[pid])[:limit]
        return self.products(ids=best)
