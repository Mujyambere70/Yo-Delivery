# Yo Delivery — online shopping & delivery website (Python + Flask)

A complete e-commerce website like the big online marketplaces: product catalog, search,
filters, cart, coupons, checkout, order tracking, invoices, reviews, wishlist and user accounts.

> Yo Delivery is a **demo project for academic use**. Products, brands and sellers are fictional,
> payments are simulated (no real money) and nothing is really delivered.

## How to run / Как запустить

```bash
pip install -r requirements.txt      # Flask + Pillow (Pillow is used for uploaded product pictures)
python app.py
```
Open **http://127.0.0.1:5000**. The database `yo_delivery.db` is created on the first run
with 42 products and a demo customer.

**Demo login:** `demo@example.com` / `Demo@1234` (or mobile `9876543210`)
The demo account already has 2 addresses, past orders (delivered + cancelled), a wishlist,
items in the cart and ₹3,201 Yo Pay wallet balance.

To start again with fresh data: stop the app, delete `yo_delivery.db`, run `python app.py`.

**Tests:** `python -m unittest discover -s tests -v` (49 tests)

## Features / Возможности

| Area | What you can do |
|---|---|
| Home | Banner slider, categories, offers, Deals of the Day with countdown, product shelves, recently viewed |
| Search & categories | Search by name/brand, filter by category, brand, price, rating, discount; sort by popularity, price, rating, discount, newest |
| Product page | 3-picture gallery, price / MRP / % off, offers, pincode delivery-date check, stock status, specifications, rating bars, customer reviews, similar products |
| Cart | Works without login (moves into your account when you sign in), quantity +/−, remove, save for later, free-delivery progress bar, coupons |
| Checkout | Choose or add delivery address, order summary, pay by **UPI** (simulated), **Yo Pay wallet** or **Cash on Delivery** |
| Orders | Order list, tracking timeline (Placed → Packed → Shipped → Out for delivery → Delivered), cancel before shipping (wallet refund), printable tax invoice with GST, buy it again |
| Reviews | Customers can review a product only after it was delivered to them (one review each) |
| Account | Profile, change password, saved addresses (default / delete), wishlist, wallet balance |

**Coupons:** `WELCOME100` (₹100 off first order above ₹499), `YO10` (10% up to ₹200 above ₹999),
`BIGSALE` (15% up to ₹2,000 above ₹9,999). Free delivery from ₹499, otherwise ₹40.

**Order tracking speed:** in demo mode an order moves to the next step every few minutes
(delivered after ~8 minutes) so you can show it live. For realistic days, run with
`YO_FAST_TRACKING=0`.

## Admin panel / Панель администратора

Sign in with **`admin@example.com` / `Admin@1234`** - you go straight to **http://127.0.0.1:5000/admin**
(the admin account is created automatically, also in an existing database).
Change this password on **My Account** after the first login.

| Page | What the admin can do |
|---|---|
| Dashboard | Total sales, orders, average order, customers, low stock, 14-day sales chart, top products, sales by category, payment methods, latest orders, live activity |
| Orders | See every order, search / filter by status, **change delivery status** (Packed → Shipped → Out for delivery → Delivered, with a note), cancel an order (stock returned, wallet refunded) |
| Products | **Add new products with 1-3 pictures** (JPG/PNG/WEBP, saved as 800×800 JPG), edit everything (name, brand, category, price, MRP, stock, description, specifications, search words, pictures), quick-edit price/stock, **hide/show** in the shop, delete (only never-ordered products) |
| Categories | Add new categories (with picture), rename, change picture, delete empty ones - the shop menu updates at once |
| Customers | Edit name / email / mobile, **block / unblock**, make admin, **reset password**, add or remove wallet money, delete accounts without orders; see addresses, cart, wishlist, orders and full history |
| Reviews | Read all reviews, search / filter by stars, delete a review (product rating is recalculated) |
| Coupons | Create flat (₹) or percent (%) coupons with minimum order, maximum discount and "first order only"; turn on/off, delete |
| Activity Log | Every sign-up, login, failed login, logout, add-to-cart, order, cancellation, review and **every admin change**, with time and IP |
| Database | Read-only view of every table + **Download CSV** for Excel (passwords hidden) |
| Shop Settings | Free-delivery limit, delivery fee, Cash on Delivery limit, welcome wallet bonus, **announcement bar** shown on every shop page |
| My Account | Change **your own name, email, mobile and password** |

Safety rules: an admin cannot block himself or remove his own admin rights; products that were ordered can only be
hidden (orders keep their history); order status can only move forward; every admin change is written to the Activity Log.
Normal customers get "Admins only" (403) if they try to open `/admin`.

### Opening the database file directly
The data is in `yo_delivery.db` (SQLite) in the project folder. To browse it outside the website, install the free
**DB Browser for SQLite** (sqlitebrowser.org) -> *Open Database* -> choose `yo_delivery.db` -> tab *Browse Data*.
Money is stored in paise (₹1 = 100), passwords only as secure hashes.

## Security
Hashed passwords (werkzeug), CSRF token in every form, parameterized SQL (no SQL injection),
auto-escaped HTML (no XSS), safe redirects after login, customers can only see their own orders,
stock / wallet / cart changes in one database transaction.

## Structure / Структура
```
yo_delivery_py/
├── app.py          Flask routes (pages, cart, checkout, orders, account)
├── store.py        Shop logic + SQLite database (users, products, cart, orders, reviews)
├── catalog.py      The first 42 products, categories and coupons (copied into the database on first run)
├── images.py       Saves pictures uploaded by the admin (Pillow)
├── demo_data.py    Demo customer with addresses, orders, wishlist, cart
├── templates/      HTML pages (Jinja2); base.html = header, menu, footer
├── static/style.css
├── static/images/  products/ (3 JPG per product), categories/, photos/ (banners), logo.png
├── templates/admin/ admin panel pages, static/admin.css
└── tests/          test_store.py (unit), test_app.py (integration), test_admin.py (admin panel)
```
