"""
blog_views.py - Yo Blog & News pages, seller stories, and the files search engines read
(sitemap.xml, robots.txt, the RSS feed).
"""

from datetime import datetime
from xml.sax.saxutils import escape

from flask import Response, abort, flash, redirect, render_template, request, url_for
from markupsafe import Markup

import blog as B
import images
import world
from core import ShopError


def register(app, store, h):
    login_required, user_id, act = h.login_required, h.user_id, h.act

    def product_card(slug):
        try:
            p = store.get_product(slug=slug)
        except ShopError:
            return ""
        if not p.active:
            return ""
        return render_template("blog/_product.html", p=p)

    def html_of(post):
        return Markup(B.render_markdown(post["body"], product_card))   # nosec B704 - render_markdown escapes the text

    @app.route("/blog")
    @app.route("/blog/c/<category>")
    def blog_home(category=""):
        if category and category not in B.POST_CATEGORIES:
            abort(404)
        q = " ".join(request.args.get("q", "").split())[:60]
        posts = store.blog_feed(world.current_lang(), category, q, limit=30)
        return render_template("blog/home.html", posts=posts, category=category, q=q, CATS=B.POST_CATEGORIES)

    @app.route("/blog/<slug>")
    def blog_post(slug):
        try:
            p = store.get_post(slug, count_view=True)
        except ShopError:
            abort(404)
        canonical = h.base_url().rstrip("/") + url_for("blog_post", slug=slug)
        return render_template("blog/post.html", p=p, body=html_of(p), related=store.related_posts(p),
                               canonical=canonical, CATS=B.POST_CATEGORIES)

    @app.route("/blog/feed.xml")
    def blog_feed():
        base = h.base_url().rstrip("/")
        items = []
        for p in store.posts(limit=30):
            when = datetime.strptime(p["published_at"] or p["created_at"], "%Y-%m-%d %H:%M:%S")
            link = base + url_for("blog_post", slug=p["slug"])
            items.append(f"<item><title>{escape(p['title'])}</title><link>{link}</link><guid>{link}</guid>"
                         f"<pubDate>{when.strftime('%a, %d %b %Y %H:%M:%S +0530')}</pubDate>"
                         f"<category>{escape(p['category_name'])}</category>"
                         f"<description>{escape(p['summary'])}</description></item>")
        xml = ('<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>Yo Delivery Blog &amp; News</title>'
               f"<link>{base}/blog</link><description>Deals, seller stories and shopping guides from Yo Delivery"
               "</description><language>en</language>" + "".join(items) + "</channel></rss>")
        return Response(xml, mimetype="application/rss+xml")

    @app.route("/sitemap.xml")
    def sitemap():
        base = h.base_url().rstrip("/")
        urls = [(url_for("index"), "daily"), (url_for("courier_home"), "weekly"), (url_for("blog_home"), "daily"),
                (url_for("rewards_home"), "daily"), (url_for("pay_emi"), "monthly"), (url_for("help_page"), "monthly"),
                (url_for("about"), "monthly"), (url_for("sell"), "monthly"), (url_for("track"), "monthly")]
        urls += [(url_for("category", category=c["slug"]), "daily") for c in store.categories()]
        urls += [(url_for("product", slug=p.slug), "weekly") for p in store.products(limit=1000)]
        urls += [(url_for("blog_post", slug=p["slug"]), "monthly") for p in store.posts(limit=1000)]
        with store.db() as conn:
            shops = [r[0] for r in conn.execute("SELECT slug FROM sellers WHERE status = 'Approved'")]
        urls += [(url_for("storefront", slug=s), "weekly") for s in shops] if "storefront" in app.view_functions else []
        if "tickets_home" in app.view_functions:              # version 4: Yo Tickets and its route pages
            import tickets as TK
            urls.append((url_for("tickets_home"), "daily"))
            urls += [(url_for("tickets_route", mode=m, route=f"{TK.slug(a)}-to-{TK.slug(b)}"), "weekly")
                     for m, a, b in TK.POPULAR_ROUTES]
        body = "".join(f"<url><loc>{escape(base + u)}</loc><changefreq>{f}</changefreq></url>" for u, f in urls)
        return Response('<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                        + body + "</urlset>", mimetype="application/xml")

    @app.route("/robots.txt")
    def robots():
        base = h.base_url().rstrip("/")
        text = ("User-agent: *\nDisallow: /admin\nDisallow: /account\nDisallow: /cart\nDisallow: /checkout\n"
                "Disallow: /driver\nDisallow: /pay\nDisallow: /seller\nDisallow: /courier/s/\nDisallow: /conductor\n"
                "Disallow: /tickets/b/\nDisallow: /tickets/v/\nDisallow: /tickets/trip/\n"
                f"Sitemap: {base}/sitemap.xml\n")
        return Response(text, mimetype="text/plain")

    # ======================================================
    # Seller stories (Seller Centre)
    # ======================================================

    @app.route("/seller/stories")
    @h.seller_required
    def seller_stories():
        shop = request.shop
        return render_template("seller/stories.html", shop=shop, posts=store.posts(status="", seller_id=shop["id"], limit=50))

    @app.route("/seller/stories/new", methods=["GET", "POST"])
    @app.route("/seller/stories/<int:post_id>/edit", methods=["GET", "POST"])
    @h.seller_required
    def seller_story_edit(post_id=None):
        shop = request.shop
        post = store.post_by_id(post_id) if post_id else None
        if post_id and (post is None or post["seller_id"] != shop["id"]):
            abort(404)
        f = request.form if request.method == "POST" else (post or {"category": "stories", "lang": world.current_lang()})
        if request.method == "POST":
            data = dict(request.form, category="stories")
            try:
                cover = request.files.get("cover_file")
                slug = store.save_post(data, user_id(), store.get_user(user_id())["name"], post_id, seller_id=shop["id"])
                if cover and cover.filename:
                    name = images.save_blog_cover(cover, slug, store)
                    with store.db() as conn:
                        conn.execute("UPDATE posts SET cover = ? WHERE slug = ?", (name, slug))
                act("Seller", f"Story saved: {slug}")
                if request.form.get("status") != "Draft":
                    with store.db() as conn:
                        store.auto_task_admin(conn, f"Check the seller story '{data.get('title', '')[:50]}'",
                                              "post:" + slug, "/admin/blog", "normal")
                flash("Your story was sent to Yo for a check." if request.form.get("status") != "Draft"
                      else "Draft saved.", "success")
                return redirect(url_for("seller_stories"))
            except ShopError as e:
                flash(str(e), "error")
        return render_template("seller/story_form.html", shop=shop, f=f, post=post, LANGUAGES=world.LANGUAGES)

    @app.route("/seller/stories/<int:post_id>/delete", methods=["POST"])
    @h.seller_required
    def seller_story_delete(post_id):
        try:
            cover = store.delete_post(post_id, seller_id=request.shop["id"])
            if cover.startswith("blog/"):
                store.delete_media(cover)
            flash("The story was deleted.", "success")
        except ShopError as e:
            flash(str(e), "error")
        return redirect(url_for("seller_stories"))
