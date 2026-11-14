"""
sitecontent.py - version 8 (mixin class used by Store): the words of the website, edited in the admin panel.

Before version 8 the phone number, the address, the About page, the help answers and the pictures on the first page
were written inside the templates: to change a phone number somebody had to change the code. Now a worker with the
ability "site.manage" edits them on /admin/site and /admin/kb:

    contacts        company name, e-mail, phone, address, opening hours and the social links (footer, /contact)
    pages           About, Delivery, Returns, Terms, Privacy ... written in the small Markdown of the blog,
                    one text per language, with every earlier version kept (and one click to bring it back)
    slides          the big banners of the first page (text, button, picture, from - until)
    help articles   questions and answers of the Help Centre, by topic, with "was this helpful?" counters;
                    the support form shows the best matching articles while the customer writes his question
    staff news      short notices from a manager to the workers of his branch (shown on "My work")

Texts can use a few placeholders that are filled from the shop settings and the contacts, so a changed fee or phone
number is right everywhere: {free_delivery_from} {delivery_fee} {cod_limit} {return_days} {phone} {email} {hours},
and {money:399} writes an amount of rupees in the money of the visitor.
"""

import html
import re
from datetime import date

import world
from blog import render_markdown
from core import ShopError, now

SITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS site_contacts (
    name TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '', updated_by INTEGER, updated_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS site_pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT NOT NULL, lang TEXT NOT NULL DEFAULT 'en', title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '', in_footer INTEGER NOT NULL DEFAULT 1, published INTEGER NOT NULL DEFAULT 1,
    updated_by INTEGER, updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_site_page ON site_pages(slug, lang);
CREATE TABLE IF NOT EXISTS site_page_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, page_id INTEGER NOT NULL REFERENCES site_pages(id), title TEXT NOT NULL,
    body TEXT NOT NULL, saved_by INTEGER, saved_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_site_versions ON site_page_versions(page_id);
CREATE TABLE IF NOT EXISTS site_slides (
    id INTEGER PRIMARY KEY AUTOINCREMENT, sort_no INTEGER NOT NULL DEFAULT 0, label TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL, subtitle TEXT NOT NULL DEFAULT '', button TEXT NOT NULL DEFAULT '',
    link TEXT NOT NULL DEFAULT '/', image TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1,
    from_day TEXT NOT NULL DEFAULT '', to_day TEXT NOT NULL DEFAULT '', updated_by INTEGER,
    updated_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS kb_articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT NOT NULL UNIQUE, topic TEXT NOT NULL DEFAULT 'Orders',
    question TEXT NOT NULL, answer TEXT NOT NULL, words TEXT NOT NULL DEFAULT '', sort_no INTEGER NOT NULL DEFAULT 0,
    published INTEGER NOT NULL DEFAULT 1, opened INTEGER NOT NULL DEFAULT 0, helpful INTEGER NOT NULL DEFAULT 0,
    not_helpful INTEGER NOT NULL DEFAULT 0, updated_by INTEGER, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS staff_news (
    id INTEGER PRIMARY KEY AUTOINCREMENT, office_id INTEGER, title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '',
    pinned INTEGER NOT NULL DEFAULT 0, until_day TEXT NOT NULL DEFAULT '', created_by INTEGER, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS staff_news_seen (
    news_id INTEGER NOT NULL REFERENCES staff_news(id), staff_id INTEGER NOT NULL, seen_at TEXT NOT NULL,
    PRIMARY KEY (news_id, staff_id)
);
"""

# name -> (label, kind, default). The defaults are the fictional demo values of the earlier versions.
CONTACT_FIELDS = {
    "company": ("Company name", "text", "Yo Delivery (demo)"),
    "tagline": ("One line about the company", "text", "Shop, send parcels and travel with one account."),
    "email": ("Support e-mail", "email", "support@yodelivery.example"),
    "phone": ("Support phone", "phone", "1800-000-9696"),
    "whatsapp": ("WhatsApp number", "phone", ""),
    "hours": ("Opening hours", "text", "Every day, 8 AM - 10 PM (India time)"),
    "address": ("Registered office (one line per row)", "lines", "12 Mall Road, Ambala Cantt\nHaryana 133001, India"),
    "sales_email": ("E-mail for sellers and partners", "email", "partners@yodelivery.example"),
    "press_email": ("E-mail for the press", "email", ""),
    "security_email": ("E-mail for security reports", "email", "security@yodelivery.example"),
    "facebook": ("Facebook page", "url", ""),
    "instagram": ("Instagram", "url", ""),
    "x": ("X (Twitter)", "url", ""),
    "youtube": ("YouTube", "url", ""),
    "telegram": ("Telegram channel", "url", ""),
    "linkedin": ("LinkedIn", "url", ""),
    "map_link": ("Link to a map of the office", "url", ""),
    "footer_note": ("Small note at the bottom of every page", "text",
                    "Yo Delivery is a demo e-commerce website made for an academic project. Products, brands, sellers "
                    "and payments are fictional - no real orders are delivered and no real money is charged."),
}
SOCIAL = ("facebook", "instagram", "x", "youtube", "telegram", "linkedin")
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[A-Za-z]{2,24}$")
_PHONE = re.compile(r"^\+?[0-9][0-9 ()\-]{4,22}$")
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,48}$")
RESERVED_SLUGS = {"new", "edit", "contact", "help", "admin"}
KB_TOPICS = ("Orders", "Delivery", "Payments", "Returns", "Account", "Courier", "Tickets", "Selling", "Safety")
PHOTOS = ("photos/courier.jpg", "photos/fashion-wide.jpg", "photos/watches-wide.jpg", "photos/shoes-wide.jpg",
          "photos/tickets.jpg")
KEEP_VERSIONS = 20

DEFAULT_PAGES = [
    ("about", "About Yo Delivery", 1, """Yo Delivery is an online shop and delivery service written in Python with Flask. Customers from any country can shop in seven languages, see prices in their own currency, pay by card in test mode, follow their parcel step by step and return what they don't want. Independent sellers can open their own shop on the marketplace.

It started as a broken PHP website and was rebuilt from the ground up as an academic and portfolio project. Products, brands, sellers and payments are fictional: nothing is really sold, shipped or charged.

## How it is built

- Python 3, Flask, Jinja2 \u00b7 SQLite on a computer, PostgreSQL online
- Interface in English, Russian, French, Spanish, Arabic (right to left), Chinese and Kinyarwanda; money and dates formatted with Babel (Unicode CLDR)
- Card payments with Stripe in test mode, or the built-in test gateway; exchange rates from open.er-api.com
- Order messages by email (Brevo), SMS (Twilio) and Web Push; recommendations from what customers view and buy
- Every important rule is checked by automated tests"""),
    ("delivery", "Delivery information", 1, """## In India

Ambala and nearby (PIN codes starting with 13): next day. Delhi, Haryana, Punjab and Himachal: 2 days. Rest of India: about 4 days. The date for your PIN code is on every product page.

Delivery is free for orders of {free_delivery_from} or more; smaller orders pay {delivery_fee}.

## Abroad

We ship to every country from our warehouse in Ambala, India: about 6 days to nearby countries and about 9 days to the rest of the world. The price is shown in the cart before you pay.

## Follow your parcel

Open [Track a parcel](/track) and type the order or tracking number. You also get a message at every step."""),
    ("returns", "Returns and refunds", 1, """You can return a delivered order within {return_days} days: open the order and press "Return this order", then choose the reason.

- A courier collects the parcel from your address.
- After our check the money goes back the way you paid: card payments to the card, wallet and UPI payments to your Yo Pay wallet.
- A tax credit note is made for every refund.

Questions? Call {phone} ({hours}) or write to {email}."""),
    ("terms", "Terms of use", 1, """This website is a demonstration made for an academic project. By using it you agree to these simple rules.

## Demo only

No real goods are sold, no parcel is really carried and no real money is charged. Card payments run in the test mode of the payment provider.

## Your account

Keep your password to yourself and turn on two-step sign-in if you can. We may block accounts that are used to harm other people or the service.

## Content you write

Reviews, questions, shop pages and stories must be honest and polite. Rude or unsafe messages are held for a moderator.

## Changes

These terms can change. The date of the last change is shown under this text."""),
    ("privacy", "Privacy notice", 1, """We keep only the data we need to run the service, and only for as long as we need it.

- **Account:** name, e-mail, mobile number, country, language and addresses you save.
- **Orders, parcels and tickets:** what you bought or sent, where to and when.
- **ID checks:** a photo of a document is kept only with your consent and is deleted 30 days after the decision.
- **Bank accounts:** only the last 4 digits are stored.

You can download or delete your data at any time on [My data and privacy](/account/data). Write to {email} with any question about your data."""),
    ("careers", "Work with us", 0, """We are a demo company, so there are no real jobs - but this is how hiring would work.

- **Couriers:** apply on [Deliver with Yo](/driver). A manager checks your papers and your first deliveries are followed by a team lead.
- **Sellers:** open a shop on [Sell on Yo Delivery](/sell).
- **Office, hub and support teams:** write to {email}."""),
]

DEFAULT_SLIDES = [
    ("Yo Express", "Delivered in *2 hours* / across Ambala",
     "Groceries, snacks and daily needs at your door. Free delivery over {free_delivery_from}.",
     "Shop Grocery", "/c/grocery", "photos/courier.jpg"),
    ("Fashion Days", "Up to *60% off* / on top styles", "T-shirts, jeans, dresses and hoodies from {money:399}.",
     "Shop Fashion", "/c/fashion", "photos/fashion-wide.jpg"),
    ("Watch Festival", "Premium watches / from *{money:1499}*", "Analog, chronograph and smartwatches. 2-year warranty.",
     "Explore Watches", "/c/watches", "photos/watches-wide.jpg"),
    ("Sneaker Store", "Run further. / Pay *less.*", "Running shoes, sneakers and formals - extra 10% off with code YO10.",
     "Shop Footwear", "/c/footwear", "photos/shoes-wide.jpg"),
]

DEFAULT_ARTICLES = [
    # the ten questions of the Help Centre of versions 1 - 7, word for word: the six other languages know them
    ("Delivery", "How long does delivery take?", "Ambala and nearby (pincodes starting with 13): next day. Delhi, Haryana, Punjab and Himachal: 2 days. Rest of India: about 4 days. You can check the date on every product page with your pincode.", "time days when arrive fast"),
    ("Delivery", "Is delivery free?", "In India delivery is free for orders of {free_delivery_from} or more; smaller orders pay {delivery_fee}. Abroad, shipping is free from {intl_free_from}.", "fee cost shipping price"),
    ("Delivery", "Do you deliver to my country?", "Yes. Yo Delivery ships to every country from its warehouse in Ambala, India: about 6 days to nearby countries and about 9 days to the rest of the world. Import duties of your country, if any, are paid when the parcel arrives.", "abroad international world customs duty"),
    ("Payments", "Can I see prices in my currency and language?", "Yes. Choose your language and currency at the top of every page. Prices are converted from Indian rupees with the day's exchange rate, and you pay by card in your currency.", "currency language convert"),
    ("Payments", "Which payment methods can I use?", "Credit or debit card (test mode), Yo Pay wallet, and in India also UPI and Cash on Delivery (up to {cod_limit}). This is a demo store, so no real money is taken. Test card: 4242 4242 4242 4242.", "pay card upi cash cod wallet"),
    ("Orders", "Will I get messages about my order?", "Yes, at every step: placed, packed, shipped (with the tracking number), out for delivery and delivered. They are on your Notifications page and, when you allow it, by email, SMS or as a notification on your phone.", "notification email sms push"),
    ("Selling", "Can I sell on Yo Delivery?", "Yes. Open \"Sell on Yo Delivery\", apply with your shop details and, after approval, add your products in the Seller Centre.", "seller shop marketplace"),
    ("Orders", "Can I cancel my order?", "Yes, you can cancel from the order details page until the order is shipped. Card payments go back to the card; wallet and UPI payments are refunded to your Yo Pay wallet immediately.", "cancel stop change mind"),
    ("Orders", "How do coupons work?", "Enter the code in the cart. WELCOME100 is for your first order, YO10 gives 10% off (up to {money:200}) above {money:999} and BIGSALE gives 15% off above {money:9999}.", "discount promo code"),
    ("Orders", "Can I write a review?", "Yes, after a product is delivered to you, open the order and click \"Write a review\".", "rating stars feedback"),
    # new in version 8
    ("Delivery", "Where is my order?", "Open \"My orders\" and choose the order: the steps show where it is now. You can also type the tracking number on the page \"Track a parcel\".", "track status late where parcel"),
    ("Returns", "How do I return an order?", "Open the order within {return_days} days after delivery and press \"Return this order\". A courier collects the parcel and the refund follows our check.", "return send back"),
    ("Returns", "When do I get my refund?", "After the returned parcel is checked. Card payments go back to the card; wallet and UPI payments go to your Yo Pay wallet at once.", "refund money back how long"),
    ("Payments", "Where is my tax invoice?", "Open the order and press \"Invoices\". Every seller of the order makes his own invoice, and a credit note is made when you return something.", "invoice bill gst vat receipt"),
    ("Account", "How do I turn on two-step sign-in?", "Open \"My profile\", choose \"Two-step sign-in\", scan the code with an authenticator app and type the 6 digits it shows.", "2fa totp authenticator security password"),
    ("Account", "How do I download or delete my data?", "Open \"My data and privacy\" in your account. You can download everything we keep about you or ask us to delete your account.", "gdpr privacy delete export"),
    ("Courier", "How do I send a parcel?", "Open Yo Courier, write the two addresses and the weight, choose a pick-up time and pay. A courier comes to you and the receiver gets a code to confirm the delivery.", "send book pickup parcel"),
    ("Courier", "My parcel is lost or damaged", "Open the shipment in \"My shipments\" and press \"Report a problem\". Every parcel is covered up to a fixed amount, or up to the value you declared when you bought extra cover.", "claim lost broken compensation"),
    ("Tickets", "What if my trip is late or cancelled?", "You get a message at once. A cancelled trip is refunded automatically; for a long delay you can cancel without a fee.", "delay cancelled refund trip bus train flight"),
    ("Safety", "I found a security problem on the website", "Thank you. Please write to {security_email} and do not share the details in public.", "security bug vulnerability report"),
]

# small words that say nothing; the question words (how, when, where ...) stay: "where is my order" is not "can I cancel my order"
_STOP = set("a an the is are was were do does did i my me you your our we it its to of in on for and or with can "
            "will not no if be have has by at as from this that".split())


def slugify(text):
    s = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")
    return s[:48].strip("-") or "page"


def words_of(text):
    return [w for w in re.findall(r"[a-z0-9]{2,}", str(text or "").lower()) if w not in _STOP]


def _stem(w):
    for end in ("ing", "ed", "es", "s"):
        if len(w) > len(end) + 2 and w.endswith(end):
            w = w[:-len(end)]
            if end in ("ing", "ed") and len(w) > 3 and w[-1] == w[-2] and w[-1] not in "aeiou":
                w = w[:-1]                                # cancelling -> cancell -> cancel
            break
    return w


def slide_title_html(text):
    """Slide title: escaped; *words* are highlighted and " / " starts a new line."""
    t = html.escape(str(text or ""), quote=True)
    t = re.sub(r"\s+/\s+", "<br>", t)                    # before the tags are added: they have a "/" too
    return re.sub(r"\*(.+?)\*", r"<span>\1</span>", t)


def safe_link(link):
    """A link of the website itself (/c/grocery) or an https:// address; anything else becomes "/"."""
    link = str(link or "").strip()
    if re.match(r"^/[A-Za-z0-9/_\-.?=&%#]*$", link) and not link.startswith("//"):
        return link
    if re.match(r"^https://[A-Za-z0-9.\-]+(:\d+)?(/[^\s<>\"']*)?$", link):
        return link
    return "/"


class SiteContentMixin:

    def _site_actor(self, user_id):
        if not self.can(user_id, "site.manage"):
            raise ShopError("You do not have the ability to edit the website's content.")

    # ------------------------------------------------------------------ first start
    def ensure_site_content(self):
        with self.db() as conn:
            t = now()
            if conn.execute("SELECT COUNT(*) FROM site_pages").fetchone()[0] == 0:
                for slug, title, footer, body in DEFAULT_PAGES:
                    conn.execute("INSERT INTO site_pages (slug, lang, title, body, in_footer, published, updated_at) "
                                 "VALUES (?, 'en', ?, ?, ?, 1, ?)", (slug, title, body, footer, t))
            if conn.execute("SELECT COUNT(*) FROM site_slides").fetchone()[0] == 0:
                for i, (label, title, sub, button, link, image) in enumerate(DEFAULT_SLIDES):
                    conn.execute("INSERT INTO site_slides (sort_no, label, title, subtitle, button, link, image, active, "
                                 "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)", (i + 1, label, title, sub, button, link,
                                                                                    image, t))
            if conn.execute("SELECT COUNT(*) FROM kb_articles").fetchone()[0] == 0:
                for i, (topic, q, a, words) in enumerate(DEFAULT_ARTICLES):
                    conn.execute("INSERT INTO kb_articles (slug, topic, question, answer, words, sort_no, published, "
                                 "updated_at) VALUES (?, ?, ?, ?, ?, ?, 1, ?)", (slugify(q), topic, q, a, words, i + 1, t))

    # ------------------------------------------------------------------ contacts
    def contacts(self):
        """Every contact field (saved value or the default) plus "address_lines" and "social"."""
        with self.db() as conn:
            saved = {r["name"]: r["value"] for r in conn.execute("SELECT name, value FROM site_contacts")}
        out = {k: saved.get(k, d[2]) for k, d in CONTACT_FIELDS.items()}
        out["address_lines"] = [x.strip() for x in out["address"].split("\n") if x.strip()]
        out["social"] = [(k, CONTACT_FIELDS[k][0], out[k]) for k in SOCIAL if out[k]]
        return out

    def save_contacts(self, user_id, form):
        """Checks every field; saves only what changed. Returns the labels of the changed fields."""
        self._site_actor(user_id)
        new = {}
        for name, (label, kind, _) in CONTACT_FIELDS.items():
            if name not in form:
                continue
            v = str(form.get(name) or "").replace("\r\n", "\n").strip()
            if kind == "email" and v and not _EMAIL.match(v):
                raise ShopError(f"{label}: please write a valid e-mail address.")
            if kind == "phone" and v and not _PHONE.match(v):
                raise ShopError(f"{label}: use digits, spaces, + ( ) and - only.")
            if kind == "url":
                if v and safe_link(v) != v or v.startswith("/"):
                    raise ShopError(f"{label}: the link must start with https://")
            if kind == "lines":
                v = "\n".join(x.strip()[:80] for x in v.split("\n") if x.strip())[:400]
            else:
                v = re.sub(r"\s+", " ", v)[:300 if name == "footer_note" else 160]
            new[name] = v
        for must in ("company", "email"):
            if must in new and not new[must]:
                raise ShopError(f"{CONTACT_FIELDS[must][0]}: this field cannot be empty.")
        old = self.contacts()
        changed = [n for n, v in new.items() if v != old[n]]
        with self.db() as conn:
            for n in changed:
                if conn.execute("SELECT 1 FROM site_contacts WHERE name = ?", (n,)).fetchone():
                    conn.execute("UPDATE site_contacts SET value = ?, updated_by = ?, updated_at = ? WHERE name = ?",
                                 (new[n], user_id, now(), n))
                else:
                    conn.execute("INSERT INTO site_contacts (name, value, updated_by, updated_at) VALUES (?, ?, ?, ?)",
                                 (n, new[n], user_id, now()))
        if changed:
            self.log(user_id, "Site contacts changed", ", ".join(changed))
        return [CONTACT_FIELDS[n][0] for n in changed]

    def fill_text(self, text, values=None):
        """Put the settings and the contacts into {placeholders}; unknown ones stay as they are."""
        if "{" not in str(text or ""):
            return str(text or "")
        if values is None:
            values = self.text_values()
        text = re.sub(r"\{money:(\d{1,7})\}", lambda m: world.price(int(m.group(1)) * 100), str(text))
        return re.sub(r"\{([a-z_]+)\}", lambda m: str(values.get(m.group(1), m.group(0))), text)

    def text_values(self):
        c, s = self.contacts(), self.settings()
        return {"free_delivery_from": world.price(s["free_delivery_from"]), "delivery_fee": world.price(s["delivery_fee"]),
                "cod_limit": world.price(s["cod_limit"]), "intl_free_from": world.price(s["intl_free_from"]),
                "return_days": s["return_days"], "phone": c["phone"],
                "email": c["email"], "hours": c["hours"], "company": c["company"],
                "security_email": c["security_email"] or c["email"]}

    # ------------------------------------------------------------------ pages
    def site_pages(self, footer_only=False):
        """One row per page (the English text), with the languages it is written in."""
        with self.db() as conn:
            rows = [dict(r) for r in conn.execute("SELECT * FROM site_pages ORDER BY slug, lang")]
        pages = {}
        for r in rows:
            p = pages.setdefault(r["slug"], {"slug": r["slug"], "langs": [], "title": r["title"], "published": 0,
                                             "in_footer": 0, "updated_at": ""})
            p["langs"].append(r["lang"])
            if r["lang"] == "en":
                p.update(title=r["title"], published=r["published"], in_footer=r["in_footer"])
            p["updated_at"] = max(p["updated_at"], r["updated_at"])
        out = sorted(pages.values(), key=lambda p: p["title"].lower())
        return [p for p in out if p["published"] and p["in_footer"]] if footer_only else out

    def site_page(self, slug, lang="en", exact=False, conn=None):
        """The page in this language; without exact=True the English text when there is no translation."""
        def read(c):
            r = c.execute("SELECT * FROM site_pages WHERE slug = ? AND lang = ?", (slug, lang)).fetchone()
            if r is None and not exact and lang != "en":
                r = c.execute("SELECT * FROM site_pages WHERE slug = ? AND lang = 'en'", (slug,)).fetchone()
            return dict(r) if r else None
        if conn is not None:
            return read(conn)
        with self.db() as c:
            return read(c)

    def page_html(self, page):
        return render_markdown(self.fill_text(page["body"])) if page else ""

    def save_site_page(self, user_id, slug, lang, title, body, published=True, in_footer=True):
        """Create or change the text of a page in one language. The old text is kept as a version."""
        self._site_actor(user_id)
        slug, lang = str(slug or "").strip().lower(), str(lang or "en")
        title = re.sub(r"\s+", " ", str(title or "")).strip()[:120]
        body = str(body or "").replace("\r\n", "\n").strip()
        if not _SLUG.match(slug) or slug in RESERVED_SLUGS:
            raise ShopError("The page address can have small letters, digits and '-' (2 to 49 characters).")
        if lang not in world.LANGUAGES:
            raise ShopError("Unknown language.")
        if len(title) < 2:
            raise ShopError("Please write a title.")
        if len(body) < 10:
            raise ShopError("Please write the text of the page (at least 10 characters).")
        if len(body) > 20000:
            raise ShopError("The text is too long (20,000 characters at most).")
        with self.db() as conn:
            old = self.site_page(slug, lang, exact=True, conn=conn)
            if old is None:
                if lang != "en" and self.site_page(slug, "en", exact=True, conn=conn) is None:
                    raise ShopError("Write the English text of a page first.")
                conn.execute("INSERT INTO site_pages (slug, lang, title, body, in_footer, published, updated_by, updated_at) "
                             "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (slug, lang, title, body, 1 if in_footer else 0,
                                                                 1 if published else 0, user_id, now()))
                what = "Site page added"
            else:
                if (old["title"], old["body"]) != (title, body):
                    conn.execute("INSERT INTO site_page_versions (page_id, title, body, saved_by, saved_at) VALUES (?, ?, ?, ?, ?)",
                                 (old["id"], old["title"], old["body"], old["updated_by"], old["updated_at"]))
                    keep = [r["id"] for r in conn.execute("SELECT id FROM site_page_versions WHERE page_id = ? ORDER BY id DESC "
                                                          "LIMIT ?", (old["id"], KEEP_VERSIONS))]
                    if len(keep) == KEEP_VERSIONS:
                        conn.execute("DELETE FROM site_page_versions WHERE page_id = ? AND id < ?", (old["id"], keep[-1]))
                conn.execute("UPDATE site_pages SET title = ?, body = ?, updated_by = ?, updated_at = ? WHERE id = ?",
                             (title, body, user_id, now(), old["id"]))
                what = "Site page changed"
            if lang == "en":                             # shown / in the footer: one switch for all the languages
                conn.execute("UPDATE site_pages SET published = ?, in_footer = ? WHERE slug = ?",
                             (1 if published else 0, 1 if in_footer else 0, slug))
        self.log(user_id, what, f"{slug} ({lang})")
        return slug

    def page_versions(self, slug, lang="en"):
        with self.db() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT v.*, u.name AS saved_by_name FROM site_page_versions v JOIN site_pages p ON p.id = v.page_id "
                "LEFT JOIN users u ON u.id = v.saved_by WHERE p.slug = ? AND p.lang = ? ORDER BY v.id DESC", (slug, lang))]

    def restore_page_version(self, user_id, version_id):
        self._site_actor(user_id)
        with self.db() as conn:
            v = conn.execute("SELECT v.title, v.body, p.slug, p.lang, p.published, p.in_footer FROM site_page_versions v "
                             "JOIN site_pages p ON p.id = v.page_id WHERE v.id = ?", (version_id,)).fetchone()
        if v is None:
            raise ShopError("This version does not exist any more.")
        return self.save_site_page(user_id, v["slug"], v["lang"], v["title"], v["body"], bool(v["published"]),
                                   bool(v["in_footer"]))

    def delete_site_page(self, user_id, slug, lang):
        """Remove one translation - or, for English, the whole page. The About page cannot be removed."""
        self._site_actor(user_id)
        if lang == "en" and slug == "about":
            raise ShopError("The About page cannot be removed. You can change its text.")
        with self.db() as conn:
            ids = [r["id"] for r in conn.execute("SELECT id FROM site_pages WHERE slug = ?" + ("" if lang == "en" else
                                                 " AND lang = ?"), (slug,) if lang == "en" else (slug, lang))]
            for i in ids:
                conn.execute("DELETE FROM site_page_versions WHERE page_id = ?", (i,))
                conn.execute("DELETE FROM site_pages WHERE id = ?", (i,))
        if ids:
            self.log(user_id, "Site page removed", f"{slug} ({lang})")
        return bool(ids)

    # ------------------------------------------------------------------ slides of the first page
    def slides(self, active_only=False, today=None):
        day = (today or date.today()).isoformat()
        with self.db() as conn:
            rows = [dict(r) for r in conn.execute("SELECT * FROM site_slides ORDER BY sort_no, id")]
        for r in rows:
            r["shown"] = bool(r["active"]) and (not r["from_day"] or r["from_day"] <= day) and \
                (not r["to_day"] or r["to_day"] >= day)
            r["title_html"] = slide_title_html(self.fill_text(r["title"]))
        return [r for r in rows if r["shown"]] if active_only else rows

    def save_slide(self, user_id, slide_id, data, image=""):
        self._site_actor(user_id)
        title = re.sub(r"\s+", " ", str(data.get("title") or "")).strip()[:90]
        if len(title) < 3:
            raise ShopError("Please write the big text of the banner.")
        days = []
        for key in ("from_day", "to_day"):
            d = str(data.get(key) or "").strip()
            if d:
                try:
                    d = date.fromisoformat(d).isoformat()
                except ValueError:
                    raise ShopError("Please check the dates of the banner.")
            days.append(d)
        if days[0] and days[1] and days[1] < days[0]:
            raise ShopError("The last day of the banner is before its first day.")
        picture = image or str(data.get("image") or "").strip()
        if picture and picture not in PHOTOS and not picture.startswith("site/"):
            raise ShopError("Please choose one of the pictures of the list or upload a new one.")
        try:
            sort_no = max(0, min(99, int(data.get("sort_no") or 0)))
        except ValueError:
            sort_no = 0
        row = (sort_no, str(data.get("label") or "").strip()[:30], title, str(data.get("subtitle") or "").strip()[:160],
               str(data.get("button") or "").strip()[:30], safe_link(data.get("link")), picture or PHOTOS[0],
               1 if data.get("active") else 0, days[0], days[1], user_id, now())
        with self.db() as conn:
            if slide_id:
                if not conn.execute("SELECT 1 FROM site_slides WHERE id = ?", (slide_id,)).fetchone():
                    raise ShopError("This banner does not exist any more.")
                conn.execute("UPDATE site_slides SET sort_no = ?, label = ?, title = ?, subtitle = ?, button = ?, link = ?, "
                             "image = ?, active = ?, from_day = ?, to_day = ?, updated_by = ?, updated_at = ? WHERE id = ?",
                             row + (slide_id,))
            else:
                if conn.execute("SELECT COUNT(*) FROM site_slides").fetchone()[0] >= 12:
                    raise ShopError("There can be 12 banners at most. Remove one first.")
                conn.execute("INSERT INTO site_slides (sort_no, label, title, subtitle, button, link, image, active, from_day, "
                             "to_day, updated_by, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", row)
        self.log(user_id, "Banner changed" if slide_id else "Banner added", title)

    def delete_slide(self, user_id, slide_id):
        self._site_actor(user_id)
        with self.db() as conn:
            conn.execute("DELETE FROM site_slides WHERE id = ?", (slide_id,))
        self.log(user_id, "Banner removed", str(slide_id))

    # ------------------------------------------------------------------ help articles
    def kb_articles(self, topic="", published_only=True):
        sql, args = "SELECT * FROM kb_articles WHERE 1 = 1", []
        if published_only:
            sql += " AND published = 1"
        if topic:
            sql += " AND topic = ?"
            args.append(topic)
        with self.db() as conn:
            return [dict(r) for r in conn.execute(sql + " ORDER BY sort_no, id", args)]

    def kb_article(self, slug=None, article_id=None):
        with self.db() as conn:
            r = conn.execute("SELECT * FROM kb_articles WHERE " + ("id = ?" if article_id else "slug = ?"),
                             (article_id or slug,)).fetchone()
        return dict(r) if r else None

    def kb_search(self, q, limit=5):
        """The best matching published articles: words of the question count 3, extra search words 2 and words of
        the answer 1; simple endings (-s, -ed, -ing) are ignored. No match -> empty list."""
        want = {_stem(w) for w in words_of(q)}
        if not want:
            return []
        scored = []
        for a in self.kb_articles():
            in_q = {_stem(w) for w in words_of(a["question"])}
            in_w = {_stem(w) for w in words_of(a["words"] + " " + a["topic"])}
            in_a = {_stem(w) for w in words_of(a["answer"])}
            score = sum(3 if w in in_q else 2 if w in in_w else 1 if w in in_a else 0 for w in want)
            if score >= 2:
                scored.append((score, -a["sort_no"], a))
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return [a for _, _, a in scored[:limit]]

    def save_kb_article(self, user_id, article_id, data):
        self._site_actor(user_id)
        q = re.sub(r"\s+", " ", str(data.get("question") or "")).strip()[:160]
        a = str(data.get("answer") or "").replace("\r\n", "\n").strip()
        topic = str(data.get("topic") or "")
        if topic not in KB_TOPICS:
            raise ShopError("Please choose a topic.")
        if len(q) < 8:
            raise ShopError("Please write the question (at least 8 characters).")
        if not 10 <= len(a) <= 4000:
            raise ShopError("Please write the answer (10 to 4,000 characters).")
        try:
            sort_no = max(0, min(999, int(data.get("sort_no") or 0)))
        except ValueError:
            sort_no = 0
        words = " ".join(words_of(data.get("words")))[:200]
        with self.db() as conn:
            if article_id:
                if not conn.execute("SELECT 1 FROM kb_articles WHERE id = ?", (article_id,)).fetchone():
                    raise ShopError("This article does not exist any more.")
                conn.execute("UPDATE kb_articles SET topic = ?, question = ?, answer = ?, words = ?, sort_no = ?, "
                             "published = ?, updated_by = ?, updated_at = ? WHERE id = ?",
                             (topic, q, a, words, sort_no, 1 if data.get("published") else 0, user_id, now(), article_id))
            else:
                slug, n = slugify(q), 1
                while conn.execute("SELECT 1 FROM kb_articles WHERE slug = ?", (slug,)).fetchone():
                    n += 1
                    slug = f"{slugify(q)[:44]}-{n}"
                conn.execute("INSERT INTO kb_articles (slug, topic, question, answer, words, sort_no, published, updated_by, "
                             "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                             (slug, topic, q, a, words, sort_no, 1 if data.get("published") else 0, user_id, now()))
        self.log(user_id, "Help article changed" if article_id else "Help article added", q[:80])

    def delete_kb_article(self, user_id, article_id):
        self._site_actor(user_id)
        with self.db() as conn:
            conn.execute("DELETE FROM kb_articles WHERE id = ?", (article_id,))
        self.log(user_id, "Help article removed", str(article_id))

    def kb_opened(self, article_id):
        with self.db() as conn:
            conn.execute("UPDATE kb_articles SET opened = opened + 1 WHERE id = ?", (article_id,))

    def kb_vote(self, article_id, helpful):
        with self.db() as conn:
            conn.execute("UPDATE kb_articles SET " + ("helpful = helpful + 1" if helpful else "not_helpful = not_helpful + 1")
                         + " WHERE id = ?", (article_id,))

    # ------------------------------------------------------------------ staff news
    def post_staff_news(self, user_id, title, body, office_id=None, pinned=False, until_day=""):
        """A manager writes to the workers of an office of his branch and of the offices under it; only the
        administrator (or a manager of the whole company) writes to everybody (office_id None)."""
        if not self.can(user_id, "staff.manage"):
            raise ShopError("Only managers can post staff news.")
        title = re.sub(r"\s+", " ", str(title or "")).strip()[:100]
        body = str(body or "").replace("\r\n", "\n").strip()[:1500]
        if len(title) < 3:
            raise ShopError("Please write a title.")
        if until_day:
            try:
                until_day = date.fromisoformat(str(until_day)).isoformat()
            except ValueError:
                raise ShopError("Please check the last day of the notice.")
        branch = self.branch_of(user_id)                # None = everything (the administrator)
        office_id = int(office_id) if office_id else None
        if branch is not None:
            if office_id is None:
                office_id = self.worker_of(user_id)["office_id"]
            if office_id not in branch:
                raise ShopError("You can write only to the offices of your own branch.")
        with self.db() as conn:
            cur = conn.execute("INSERT INTO staff_news (office_id, title, body, pinned, until_day, created_by, created_at) "
                               "VALUES (?, ?, ?, ?, ?, ?, ?)", (office_id, title, body, 1 if pinned else 0, until_day or "",
                                                                user_id, now()))
            return cur.lastrowid

    def staff_news_for(self, user_id, limit=8, today=None, mark_seen=False):
        """News for this worker: for everybody, for his office or for an office above it. Each has "is_new"."""
        w = self.worker_of(user_id)
        if not w:
            return []
        day = (today or date.today()).isoformat()
        above = {o["id"] for o in self.office_path(w["office_id"])}
        with self.db() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT n.*, u.name AS author, o.name AS office FROM staff_news n LEFT JOIN users u ON u.id = n.created_by "
                "LEFT JOIN offices o ON o.id = n.office_id ORDER BY n.pinned DESC, n.id DESC LIMIT 200")]
            seen = {r["news_id"] for r in conn.execute("SELECT news_id FROM staff_news_seen WHERE staff_id = ?", (w["id"],))}
            rows = [r for r in rows if (r["office_id"] is None or r["office_id"] in above)
                    and (not r["until_day"] or r["until_day"] >= day)][:limit]
            for r in rows:
                r["is_new"] = r["id"] not in seen
                if mark_seen and r["is_new"]:
                    conn.execute("INSERT INTO staff_news_seen (news_id, staff_id, seen_at) VALUES (?, ?, ?)",
                                 (r["id"], w["id"], now()))
        return rows

    def staff_news_list(self, user_id):
        """What a manager posted or may remove: the news of his branch, with how many workers have seen each."""
        branch = self.branch_of(user_id)
        with self.db() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT n.*, u.name AS author, o.name AS office, (SELECT COUNT(*) FROM staff_news_seen s WHERE "
                "s.news_id = n.id) AS seen_by FROM staff_news n LEFT JOIN users u ON u.id = n.created_by "
                "LEFT JOIN offices o ON o.id = n.office_id ORDER BY n.id DESC LIMIT 200")]
        if branch is not None:
            rows = [r for r in rows if r["office_id"] in branch]
        return rows

    def delete_staff_news(self, user_id, news_id):
        if not self.can(user_id, "staff.manage"):
            raise ShopError("Only managers can remove staff news.")
        if not any(r["id"] == int(news_id) for r in self.staff_news_list(user_id)):
            raise ShopError("You can remove only the news of your own branch.")
        with self.db() as conn:
            conn.execute("DELETE FROM staff_news_seen WHERE news_id = ?", (news_id,))
            conn.execute("DELETE FROM staff_news WHERE id = ?", (news_id,))

    # ------------------------------------------------------------------ colleagues
    def colleagues(self, user_id, q=""):
        """Who works with me: the active workers of my office, the managers above me and - for a manager - his whole
        branch. Only work details (name, position, office); the phone number is for people who may see staff."""
        w = self.worker_of(user_id)
        u = self.get_user(user_id) if user_id else None
        if not u or not (w or u["is_admin"]):
            return []
        full = self.can(user_id, "staff.view")
        with self.db() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT s.id, s.user_id, s.office_id, s.title, s.kind, s.role, s.is_manager, o.name AS office, "
                "o.place AS office_place, u.name AS name, u.email AS email, u.mobile AS mobile FROM staff s "
                "JOIN offices o ON o.id = s.office_id JOIN users u ON u.id = s.user_id WHERE s.status = 'Active' "
                "ORDER BY o.name, s.is_manager DESC, u.name")]
        if full:
            branch = self.branch_of(user_id)
            rows = [r for r in rows if branch is None or r["office_id"] in branch]
        else:
            above = {o["id"] for o in self.office_path(w["office_id"])}
            rows = [r for r in rows if r["office_id"] == w["office_id"] or (r["office_id"] in above and r["is_manager"])]
        if q:
            ql = str(q).lower().strip()
            rows = [r for r in rows if ql in f"{r['name']} {r['title']} {r['office']}".lower()]
        for r in rows:
            if not full:
                r["mobile"] = ""
            r["me"] = r["user_id"] == user_id
        return rows

    def manager_of(self, user_id):
        """The manager of a worker: a manager of his office (not himself), else of the nearest office above."""
        w = self.worker_of(user_id)
        if not w:
            return None
        path = [o["id"] for o in reversed(self.office_path(w["office_id"]))]
        with self.db() as conn:
            for office_id in path:
                r = conn.execute("SELECT u.name, s.title FROM staff s JOIN users u ON u.id = s.user_id WHERE s.office_id = ? "
                                 "AND s.is_manager = 1 AND s.status = 'Active' AND s.user_id != ? ORDER BY s.id LIMIT 1",
                                 (office_id, user_id)).fetchone()
                if r:
                    return {"name": r["name"], "title": r["title"]}
        return None
