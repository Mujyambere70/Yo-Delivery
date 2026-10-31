# Yo Delivery — international online shop & marketplace (Python + Flask)

**Designed and developed by Jean Michel Mujyambere** — [github.com/Mujyambere70](https://github.com/Mujyambere70)

An e-commerce website and delivery service for customers in **every country**: 7 languages,
prices in 8 currencies, card payments (test mode), order tracking with email / SMS / phone
messages, returns and refunds, product recommendations, and a **multi-seller marketplace**
where independent sellers run their own shops.

> Yo Delivery is a **demo project for academic use**. Products, brands, sellers and customers are
> fictional, card payments run in **test mode** (no real money) and nothing is really delivered.
>
> Yo Delivery — **учебный демо-проект**. Товары, бренды, продавцы и покупатели вымышлены, оплата
> картой работает в **тестовом режиме** (без реальных денег), ничего не доставляется.

---

## What is new in version 2 / Что нового в версии 2

| Was (v1 limitation) | Now (v2) |
|---|---|
| English only | **7 languages**: English, Русский, Français, Español, العربية (right-to-left), 中文, Ikinyarwanda. Money, dates and 240+ country names are formatted with Babel (Unicode CLDR). Order messages arrive in the customer's language |
| India only | **Every country**: phone numbers with country codes, postal codes per country (or none, e.g. Rwanda, UAE), international shipping zones and fees, couriers *Yo Express* (India) and *Yo Global Express* (world), export invoices |
| Rupees only | Prices shown and paid in **INR, RUB, USD, EUR, GBP, CNY, AED, RWF** with the day's exchange rate (open.er-api.com, refreshed daily). The order remembers the amount and rate |
| Simulated payments | **Card payments**: Stripe Checkout in test mode (only `sk_test_` keys; live keys are refused) with signed webhooks, or the built-in test gateway with the Stripe test cards. Unpaid card orders are cancelled after 30 minutes and the stock returns |
| No email / SMS | Messages at every step (placed, packed, shipped with tracking number, out for delivery, delivered, returns, refunds): **in the site, by email (Brevo or SMTP), SMS (Twilio) and Web Push** on the phone. Each message is sent once only |
| SQLite only, data lost after restart | **PostgreSQL** online (`DATABASE_URL`, e.g. Neon free plan), SQLite on a computer. Uploaded pictures and the login key are stored **in the database**, so nothing is lost when the server restarts. Daily automatic backup (optionally emailed), manual backup / restore |
| Single seller | **Multi-seller marketplace**: sellers apply, the admin approves; sellers add products (checked by the admin), see their sales and earnings; commission per item; payouts recorded by the admin; public shop page per seller |
| No recommendations | **"Recommended for you"**, "Frequently bought together", "Customers who viewed this item also viewed", recently viewed — from what customers view, save and buy |
| Delivery date only by PIN code | Delivery date by country (and by PIN code in India), public parcel tracking page |
| — | **Returns & refunds** (to the card or Yo Pay wallet), **password reset by email**, **two-factor login** (authenticator app), installable app (PWA) |

---

## How to run on your computer / Как запустить

```bash
pip install -r requirements.txt
python app.py
```
Open **http://127.0.0.1:5000**. The database `yo_delivery.db` (SQLite) is created on the first run
with 42 products, customers from 7 countries, a seller and orders.

### Demo accounts / Демо-аккаунты

| Who | Login | Password |
|---|---|---|
| Customer in India (English) | `demo@example.com` | `Demo@1234` |
| Customer in Russia (Русский, RUB) | `anna.smirnova@example.com` | `Shop@2026` |
| Customer in Rwanda (Ikinyarwanda, RWF) | `amani.uwase@example.com` | `Shop@2026` |
| Seller "HomeEase Kitchen Studio" | `seller@example.com` | `Seller@1234` |
| Admin (Jean Michel Mujyambere) | `admin@example.com` | `Admin@1234` |

Other demo customers (same password `Shop@2026`): camille.laurent@example.com (France),
li.wei@example.com (China), omar.almansoori@example.com (UAE), lucia.fernandez@example.com (Spain).
All demo addresses end with `example.com`, so **no email or SMS is ever sent to them**.

**Test cards:** `4242 4242 4242 4242` (success), `5555 5555 5555 4444` (success),
`4000 0000 0000 0002` (declined), `4000 0000 0000 9995` (not enough money). Any future date, any CVC.

**Tests / Тесты:** `python -m unittest discover tests` — 87 tests.
The same tests on PostgreSQL: `TEST_DATABASE_URL=postgresql://user@host/dbname python -m unittest discover tests`
(the test database is emptied — use a separate one).

---

## Put it online: GitHub + Neon + Render / Размещение в интернете

1. **GitHub** — create a repository `yo-delivery` and upload this folder
   (`git init`, `git add .`, `git commit -m "Yo Delivery v2"`, `git push`). The `.gitignore` keeps the
   database file and secrets out of GitHub.
2. **Neon** (neon.tech, free) — create a project → copy the connection string
   `postgresql://...neon.tech/neondb?sslmode=require`.
3. **Render** (render.com, free) — *New → Blueprint* → choose the repository. Render reads `render.yaml`.
   Paste the Neon string into **DATABASE_URL**. Leave the optional keys empty for now → *Apply*.
4. Open `https://yo-delivery-michel.onrender.com` — the first start fills the database (about 1 minute).
   Sign in as admin and **change the admin password** (My Account).
5. *Optional* — **Stripe test mode** (dashboard.stripe.com, switch "Test mode" on):
   *Developers → API keys* → copy the **Secret key `sk_test_...`** into `STRIPE_SECRET_KEY`.
   *Developers → Webhooks → Add endpoint* `https://<your-site>/payments/stripe/webhook`, event
   `checkout.session.completed` → copy the **Signing secret `whsec_...`** into `STRIPE_WEBHOOK_SECRET`.
   Without a key, the built-in test gateway is used.
6. *Optional* — **email** (Brevo: `BREVO_API_KEY`, `ALERT_EMAIL_FROM`), **SMS** (Twilio:
   `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM`), **daily backup by email** (`BACKUP_EMAIL`).
   The admin page *Shop Settings → System status* shows what is switched on.

Keys and passwords are **only** put in Render's *Environment* page — never in the code or on GitHub.

---

## Features / Возможности

| Area | What you can do |
|---|---|
| Languages & money | Language and currency menus on every page; the choice is saved in the account. Arabic pages are right-to-left |
| Home | Banner slider, categories, offers, Deals of the Day, **Recommended for you**, recently viewed |
| Search & categories | Search, filters (category, brand, price, rating, discount), sorting |
| Product page | Gallery, price in your currency, offers, delivery date for your country, specifications, reviews, **frequently bought together**, **customers also viewed**, seller's shop link |
| Cart & checkout | Guest cart, coupons, free-delivery bar, addresses in any country, payment by card (all countries), Yo Pay wallet, UPI and Cash on Delivery (India) |
| Orders | Tracking timeline, courier and tracking number, public tracking page, cancel before shipping, **return within 7 days** with refund, invoice (export invoices are zero-rated) |
| Account | Profile, language & currency, addresses, wishlist, notifications page, email/SMS settings, phone notifications, **two-factor login**, password change, **forgot password** |
| Sell on Yo Delivery | Apply → admin approves → **Seller Centre**: dashboard, products (with pictures), sales & payouts, shop settings, public shop page |
| Admin panel | Dashboard, orders, products (approve sellers' products), categories, customers, **sellers & payouts**, **returns**, reviews, coupons, activity log, database viewer, **backups**, shop settings & system status |

## Security
Hashed passwords, CSRF token in every form, parameterized SQL, auto-escaped HTML, safe redirects,
two-factor login (TOTP), password-reset links stored only as hashes and valid 30 minutes,
Stripe webhooks checked with HMAC-SHA256, live payment keys refused, sellers see no customer names
or addresses, every stock / wallet / payment change in one database transaction.

## Structure / Структура
```
yo_delivery_py/
├── app.py           Flask routes: shop, account, checkout, payments, seller centre, admin
├── store.py         Shop logic (users, products, cart, orders, reviews, coupons, settings)
├── payments.py      Exchange rates, Stripe test mode, built-in test gateway, card refunds
├── notices.py       Messages (site, email, SMS, push), password reset, two-factor login
├── sellers.py       Marketplace: applications, seller products, sales, commission, payouts
├── recommend.py     Recommendations from views, wishlists and orders
├── aftersales.py    Returns and refunds
├── maintenance.py   Daily jobs, backups, pictures stored in the database
├── world.py         Languages, currencies, countries, phone / postal rules, shipping zones
├── i18n.py          Page translation; translations/*.json (ru, fr, es, ar, zh, rw)
├── db.py            One database layer for SQLite and PostgreSQL
├── notify.py, push.py, otp.py   Email/SMS sending, Web Push, authenticator codes
├── catalog.py, demo_data.py     Starting products and demo data
├── templates/       Jinja2 pages (shop, seller/, admin/)
├── static/          style.css, admin.css, pictures, PWA files
└── tests/           87 tests (SQLite and PostgreSQL)
```

The Kinyarwanda translation was prepared by the developer and should be reviewed by a native speaker
before real use.

© 2026 Jean Michel Mujyambere. Built as an academic and portfolio project.
