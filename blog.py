"""
blog.py - Yo Blog & News (mixin class used by Store): deals, seller stories, shopping guides and Yo news.

  * The admin writes and publishes posts; an approved seller can write a story about their shop,
    which waits for the admin's check (status "Review") before it is published.
  * Posts are written in a small, safe Markdown: ## headings, **bold**, *italic*, [links](https://...),
    lists, > quotes and [[product:slug]] to show a product card inside the text.
    All HTML typed by the author is escaped first, so a post cannot contain scripts.
  * Good for search engines (SEO): clean addresses /blog/<slug>, a description and Open Graph tags,
    structured data (schema.org Article), sitemap.xml, robots.txt and an RSS feed.
"""

import html
import math
import re

from core import ShopError, now

POST_CATEGORIES = {"deals": "Deals & offers", "stories": "Seller stories", "guides": "Shopping guides",
                   "news": "Yo news"}
POST_STATUSES = ["Draft", "Review", "Published"]
WORDS_PER_MINUTE = 200

BLOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT NOT NULL UNIQUE, title TEXT NOT NULL, summary TEXT NOT NULL,
    body TEXT NOT NULL, category TEXT NOT NULL, lang TEXT NOT NULL DEFAULT 'en', cover TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '', author_id INTEGER, author_name TEXT NOT NULL, seller_id INTEGER,
    status TEXT NOT NULL DEFAULT 'Draft', views INTEGER NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, published_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_posts_status ON posts(status);
"""

_INLINE = [
    (re.compile(r"`([^`]+)`"), r"<code>\1</code>"),
    (re.compile(r"\*\*(.+?)\*\*"), r"<strong>\1</strong>"),
    (re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])"), r"<em>\1</em>"),
]
_LINK = re.compile(r"\[([^\]]+)\]\(((?:https://|/)[^\s)]*)\)")
_PRODUCT = re.compile(r"^\[\[product:([a-z0-9-]+)\]\]$")


def slugify(text):
    s = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return s[:70].strip("-") or "post"


def _inline(text):
    text = html.escape(text, quote=True)

    def link(m):
        label, url = m.group(1), html.unescape(m.group(2))
        outside = url.startswith("https://")
        return (f'<a href="{html.escape(url, quote=True)}"' + (' rel="nofollow noopener" target="_blank"' if outside else "")
                + f">{label}</a>")
    text = _LINK.sub(link, text)
    for rx, rep in _INLINE:
        text = rx.sub(rep, text)
    return text


def render_markdown(body, product_card=None):
    """Turn the small Markdown of a post into HTML. product_card(slug) returns HTML for [[product:slug]]."""
    out, para, items, kind = [], [], [], None

    def flush():
        nonlocal para, items, kind
        if para:
            out.append("<p>" + " ".join(_inline(x) for x in para) + "</p>")
        if items:
            tag = "ol" if kind == "ol" else "ul"
            out.append(f"<{tag}>" + "".join(f"<li>{_inline(x)}</li>" for x in items) + f"</{tag}>")
        para, items, kind = [], [], None

    for raw in str(body or "").replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if not line:
            flush()
            continue
        m = re.match(r"^(#{2,3})\s+(.+)$", line)
        if m:
            flush()
            level = len(m.group(1))
            out.append(f"<h{level}>{_inline(m.group(2))}</h{level}>")
            continue
        m = _PRODUCT.match(line)
        if m:
            flush()
            card = product_card(m.group(1)) if product_card else ""
            if card:
                out.append(card)
            continue
        m = re.match(r"^[-*]\s+(.+)$", line) or re.match(r"^\d+[.)]\s+(.+)$", line)
        if m:
            new = "ol" if line[0].isdigit() else "ul"
            if para or (items and kind != new):
                flush()
            kind = new
            items.append(m.group(1))
            continue
        if line.startswith(">"):
            flush()
            out.append(f"<blockquote>{_inline(line.lstrip('> ').strip())}</blockquote>")
            continue
        if items:
            flush()
        para.append(line)
    flush()
    return "\n".join(out)


def plain_text(body):
    """Text without Markdown marks (for the summary and the reading time)."""
    t = re.sub(r"\[\[product:[^\]]+\]\]", "", str(body or ""))
    t = _LINK.sub(r"\1", t)
    t = re.sub(r"[#*`>]+", "", t)
    return " ".join(t.split())


def reading_minutes(body):
    return max(1, math.ceil(len(plain_text(body).split()) / WORDS_PER_MINUTE))


class BlogMixin:

    def posts(self, category="", lang="", q="", status="Published", limit=24, offset=0, seller_id=None):
        sql, args = "SELECT * FROM posts WHERE 1 = 1", []
        if status:
            sql += " AND status = ?"
            args.append(status)
        if category:
            sql += " AND category = ?"
            args.append(category)
        if lang:
            sql += " AND lang = ?"
            args.append(lang)
        if seller_id is not None:
            sql += " AND seller_id = ?"
            args.append(seller_id)
        if q:
            sql += " AND (title LIKE ? OR summary LIKE ? OR tags LIKE ?)"
            args += [f"%{q}%"] * 3
        order = "published_at DESC, id DESC" if status == "Published" else "updated_at DESC, id DESC"
        with self.db() as conn:
            rows = [dict(r) for r in conn.execute(f"{sql} ORDER BY {order} LIMIT ? OFFSET ?", args + [limit, offset])]
        for r in rows:
            r["minutes"] = reading_minutes(r["body"])
            r["category_name"] = POST_CATEGORIES.get(r["category"], r["category"])
        return rows

    def blog_feed(self, lang="en", category="", q="", limit=24):
        """Posts for a visitor: in their language first, then the English ones."""
        mine = self.posts(category, lang, q, limit=limit) if lang != "en" else []
        other = [p for p in self.posts(category, "", q, limit=limit * 2) if p["lang"] in ("en",) and p not in mine]
        return (mine + other)[:limit]

    def get_post(self, slug, count_view=False, any_status=False):
        with self.db() as conn:
            r = conn.execute("SELECT * FROM posts WHERE slug = ?", (slug,)).fetchone()
            if r is None or (not any_status and r["status"] != "Published"):
                raise ShopError("Post not found.")
            if count_view:
                conn.execute("UPDATE posts SET views = views + 1 WHERE id = ?", (r["id"],))
        p = dict(r)
        p["minutes"] = reading_minutes(p["body"])
        p["category_name"] = POST_CATEGORIES.get(p["category"], p["category"])
        p["tag_list"] = [t for t in (x.strip() for x in p["tags"].split(",")) if t]
        return p

    def related_posts(self, post, limit=3):
        """Other posts in the same language: the same category first."""
        same = [p for p in self.posts(post["category"], post["lang"], limit=limit + 1) if p["id"] != post["id"]]
        if len(same) < limit:
            same += [p for p in self.posts(lang=post["lang"], limit=limit + 4) if p["id"] != post["id"] and p not in same]
        return same[:limit]

    def save_post(self, f, author_id, author_name, post_id=None, seller_id=None):
        """Create or change a post. A seller's post always goes to 'Review' (the admin publishes it)."""
        title = " ".join(str(f.get("title", "")).split())[:120]
        body = str(f.get("body", "")).replace("\r\n", "\n").strip()[:30_000]
        summary = " ".join(str(f.get("summary", "")).split())[:300] or plain_text(body)[:200]
        category = f.get("category", "")
        lang = f.get("lang", "en")
        import world
        if len(title) < 8:
            raise ShopError("Please write a title (at least 8 characters).")
        if len(plain_text(body)) < 80:
            raise ShopError("The post is too short - please write at least a few sentences.")
        if category not in POST_CATEGORIES:
            raise ShopError("Please choose a category.")
        if seller_id is not None and category != "stories":
            raise ShopError("Sellers can write seller stories only.")
        if lang not in world.LANGUAGES:
            raise ShopError("Please choose the language of the post.")
        tags = ", ".join(t for t in (" ".join(x.split())[:30] for x in str(f.get("tags", "")).split(",")) if t)[:200]
        cover = str(f.get("cover", ""))[:120]
        if cover and not re.fullmatch(r"(images|blog)/[a-z0-9/_.-]+\.(jpg|png|webp)", cover):
            cover = ""
        status = f.get("status", "Draft")
        if seller_id is not None:
            status = "Review" if status != "Draft" else "Draft"
        if status not in POST_STATUSES:
            status = "Draft"
        when = now()
        with self.db() as conn:
            if post_id:
                old = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
                if old is None or (seller_id is not None and old["seller_id"] != seller_id):
                    raise ShopError("Post not found.")
                if seller_id is not None and old["status"] == "Published":
                    status = "Review"                         # a changed story is checked again
                published = old["published_at"] or (when if status == "Published" else "")
                conn.execute("UPDATE posts SET title = ?, summary = ?, body = ?, category = ?, lang = ?, tags = ?, "
                             "cover = CASE WHEN ? = '' THEN cover ELSE ? END, status = ?, updated_at = ?, "
                             "published_at = ? WHERE id = ?",
                             (title, summary, body, category, lang, tags, cover, cover, status, when, published, post_id))
                return old["slug"]
            slug, base, n = slugify(f.get("slug") or title), slugify(f.get("slug") or title), 2
            while conn.execute("SELECT 1 FROM posts WHERE slug = ?", (slug,)).fetchone():
                slug, n = f"{base}-{n}", n + 1
            conn.execute("INSERT INTO posts (slug, title, summary, body, category, lang, cover, tags, author_id, "
                         "author_name, seller_id, status, created_at, updated_at, published_at) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (slug, title, summary, body, category, lang, cover, tags, author_id, author_name, seller_id,
                          status, f.get("when") or when, f.get("when") or when,
                          (f.get("when") or when) if status == "Published" else ""))
            return slug

    def set_post_status(self, post_id, status, note=""):
        if status not in POST_STATUSES:
            raise ShopError("Unknown status.")
        with self.db() as conn:
            p = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
            if p is None:
                raise ShopError("Post not found.")
            conn.execute("UPDATE posts SET status = ?, note = ?, published_at = CASE WHEN ? = 'Published' AND "
                         "published_at = '' THEN ? ELSE published_at END WHERE id = ?",
                         (status, " ".join(str(note).split())[:200], status, now(), post_id))
            if p["seller_id"] and p["author_id"] and status != p["status"]:
                text = ("Your story is published on the Yo Blog." if status == "Published" else
                        "Your story needs changes before it can be published." + (f" {note}" if note else ""))
                self.notify(conn, p["author_id"], "Your story: {0}".replace("{0}", p["title"][:60]), text,
                            url=f"/blog/{p['slug']}" if status == "Published" else "/seller/stories")

    def delete_post(self, post_id, seller_id=None):
        with self.db() as conn:
            p = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
            if p is None or (seller_id is not None and p["seller_id"] != seller_id):
                raise ShopError("Post not found.")
            conn.execute("DELETE FROM posts WHERE id = ?", (post_id,))
            return p["cover"]

    def post_by_id(self, post_id):
        with self.db() as conn:
            r = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
        return dict(r) if r else None
