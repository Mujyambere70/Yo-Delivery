"""
contentrec.py - version 6 (mixin class used by Store): "similar products" from the TEXT and the PICTURE of each
product, so a new product (that nobody has bought or viewed yet) is recommended too.

    text     TF-IDF of the words of the name, the description, the brand and the category (words that many
             products have count less), cosine similarity
    picture  the colours of the main picture (a histogram of hue, saturation and value, 8 x 4 x 4) and its shapes
             (a histogram of edge directions in a 4 x 4 grid), cosine similarity
    together 0.7 x text + 0.3 x picture (only text when a picture is missing); the same category adds 0.1
The 12 closest products of each product are saved in content_similar (made again every day by the job queue and
after the seller changes a product). The learned model (learn.py, from what people buy and view) and this one
work together: "Similar products" on the product page uses these scores; when the learned model is ready and
knows the product, its neighbours are mixed in.
"""

import io
import math
import os
import re
from collections import Counter

from core import now

CONTENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS content_similar (
    product_id INTEGER NOT NULL, other_id INTEGER NOT NULL, score REAL NOT NULL, text_score REAL NOT NULL,
    image_score REAL NOT NULL, made_at TEXT NOT NULL, PRIMARY KEY (product_id, other_id)
);
"""

TOP = 12
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
STOP = {"the", "and", "for", "with", "of", "a", "an", "in", "on", "to", "is", "it", "by", "or", "your", "you", "at",
        "this", "that", "from", "up", "all", "new"}


def tokens(text):
    return [w for w in re.findall(r"[a-z0-9]+", str(text or "").lower()) if len(w) > 1 and w not in STOP]


def tfidf(docs):
    """{id: {word: weight}} with weights normalised to length 1."""
    df = Counter()
    for words in docs.values():
        df.update(set(words))
    n = len(docs)
    out = {}
    for pid, words in docs.items():
        tf = Counter(words)
        v = {w: (1 + math.log(c)) * math.log((1 + n) / (1 + df[w])) for w, c in tf.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        out[pid] = {w: x / norm for w, x in v.items()}
    return out


def cosine_sparse(a, b):
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(w, 0.0) for w, x in a.items())


def image_vector(data):
    """Colour + shape histogram of a picture (bytes) as a NumPy vector of length 1, or None."""
    try:
        import numpy as np
        from PIL import Image
        img = Image.open(io.BytesIO(data)).convert("RGB")
        img.thumbnail((128, 128))
        hsv = np.asarray(img.convert("HSV"), dtype=np.float32) / 255.0
        h = np.minimum((hsv[..., 0] * 8).astype(int), 7)
        s = np.minimum((hsv[..., 1] * 4).astype(int), 3)
        v = np.minimum((hsv[..., 2] * 4).astype(int), 3)
        colour = np.bincount((h * 16 + s * 4 + v).ravel(), minlength=128).astype(np.float32)
        gray = np.asarray(img.convert("L").resize((64, 64)), dtype=np.float32)
        gy, gx = np.gradient(gray)
        mag = np.hypot(gx, gy)
        ang = (np.arctan2(gy, gx) % math.pi) / math.pi * 9
        bins = np.minimum(ang.astype(int), 8)
        shape = []
        for i in range(4):
            for j in range(4):
                cell = (slice(i * 16, i * 16 + 16), slice(j * 16, j * 16 + 16))
                shape.append(np.bincount(bins[cell].ravel(), weights=mag[cell].ravel(), minlength=9))
        shape = np.concatenate(shape).astype(np.float32)
        colour /= (np.linalg.norm(colour) or 1.0)
        shape /= (np.linalg.norm(shape) or 1.0)
        vec = np.concatenate([colour, shape * 0.8])
        return vec / (np.linalg.norm(vec) or 1.0)
    except Exception:
        return None


class ContentRecMixin:
    def build_content_similar(self):
        """Compute the closest products of every active product and save them. Returns the numbers."""
        prods = self.products()
        if not prods:
            return {"products": 0}
        docs, images = {}, {}
        for p in prods:
            about = p.about if isinstance(p.about, str) else " ".join(p.about or [])
            docs[p.id] = tokens(f"{p.name} {p.name} {about} {getattr(p, 'brand', '')} {p.category}")
            found = self.media(f"products/{p.slug}-1.jpg")          # uploaded by a seller (in the database)
            data = found[1] if found else None
            if data is None:                                          # the pictures that come with the shop
                path = os.path.join(STATIC, "images", "products", f"{p.slug}-1.jpg")
                if os.path.exists(path):
                    with open(path, "rb") as f:
                        data = f.read()
            vec = image_vector(data) if data else None
            if vec is not None:
                images[p.id] = vec
        text = tfidf(docs)
        cat = {p.id: p.category for p in prods}
        rows = []
        for a in prods:
            scores = []
            for b in prods:
                if a.id == b.id:
                    continue
                ts = cosine_sparse(text[a.id], text[b.id])
                im = float(images[a.id] @ images[b.id]) if a.id in images and b.id in images else None
                score = (0.7 * ts + 0.3 * im) if im is not None else ts
                score += 0.1 if cat[a.id] == cat[b.id] else 0.0
                scores.append((score, b.id, ts, im or 0.0))
            scores.sort(reverse=True)
            rows.extend((a.id, b, round(s, 4), round(t, 4), round(i, 4)) for s, b, t, i in scores[:TOP])
        stamp = now()
        with self.db() as conn:
            conn.execute("DELETE FROM content_similar")
            for r in rows:
                conn.execute("INSERT INTO content_similar (product_id, other_id, score, text_score, image_score, made_at) "
                             "VALUES (?, ?, ?, ?, ?, ?)", (*r, stamp))
        return {"products": len(prods), "with_picture": len(images), "pairs": len(rows)}

    def content_similar(self, product_id, limit=8):
        with self.db() as conn:
            ids = [r[0] for r in conn.execute("SELECT other_id FROM content_similar WHERE product_id = ? "
                                              "ORDER BY score DESC LIMIT ?", (product_id, limit * 2))]
        if not ids:
            return []
        found = {p.id: p for p in self.products(ids=ids)}
        return [found[i] for i in ids if i in found][:limit]

    def similar_products(self, product_id, limit=8):
        """Text + picture neighbours, mixed with the learned neighbours when the learned model knows the product."""
        content = self.content_similar(product_id, limit)
        learned = self.learned_similar(product_id, limit) if hasattr(self, "learned_similar") else []
        out, seen = [], {product_id}
        for pair in zip(content, learned + [None] * len(content)):
            for p in pair:
                if p is not None and p.id not in seen:
                    seen.add(p.id)
                    out.append(p)
        for p in content + learned:
            if p.id not in seen:
                seen.add(p.id)
                out.append(p)
        return out[:limit]

    def content_report(self):
        with self.db() as conn:
            r = conn.execute("SELECT COUNT(DISTINCT product_id), COUNT(*), MAX(made_at), "
                             "SUM(CASE WHEN image_score > 0 THEN 1 ELSE 0 END) FROM content_similar").fetchone()
        return {"products": r[0] or 0, "pairs": r[1] or 0, "made_at": r[2] or "", "with_picture_pairs": r[3] or 0}
