# Yo Delivery 9 — the Yo super app for the whole world and the team that runs it: shop · courier · tickets · pay · rewards · chat · staff · payroll · invoices · help desk (Python + Flask)

**Designed and developed by Jean Michel Mujyambere** — [github.com/Mujyambere70](https://github.com/Mujyambere70)

Yo Delivery started in 2023 as a university project — *"a delivery service for documents, groceries and
packages in the Ambala Division, with real-time tracking, and orders routed automatically to delivery
drivers"*. Version 2 made it an international online shop and marketplace, version 3 turned it into a small
**super app** (courier service, wallet, rewards, news), version 4 added **Yo Tickets** (bus, train and flight
tickets with seat maps, QR e-tickets and a conductor app), and **version 5 opens it to the whole world**: every town
and village (all of Russia and India, every village of Rwanda), 154 home currencies, couriers and tickets anywhere.
**Version 6 adds the people who run it**: an office tree, workers and managers, abilities given by the
administrator, shifts, attendance, tasks and an activity log — and works through the future scope of version 5.
**Version 7 works through the limitations and future scope of version 6**: contracts, holidays and payroll with
dual control, tax invoices and e-invoices, signed payment callbacks, a courier partner API, time windows and
signatures for couriers, live delays, a live check for ID photos, analytics, forecasts and A/B tests, a help desk,
parcel claims and a safety check of orders. **Version 8** makes it easy to use (a "My work" button, a menu with search,
site content edited without code) and **version 9** finishes the 21 steps found in the review of version 8: translated
help texts, page drafts, one search that forgives typos, shift swaps, help-desk files and stars, "buy again", seller
insights, a status page, limits per person and a nightly backup that is restored to prove it works.
One account and one wallet for everything.

**Live demo:** https://yo-delivery.fly.dev

> Yo Delivery is a **demo project for academic use**. Products, brands, sellers, couriers, transport companies
> and customers are fictional, card payments run in **test mode** (no real money), nothing is really delivered
> and no real tickets are sold.
>
> Yo Delivery — **учебный демо-проект**. Товары, продавцы, курьеры, перевозчики и покупатели вымышлены, оплата
> картой работает в **тестовом режиме** (без реальных денег), ничего не доставляется, настоящие билеты не продаются.

---

## What is new in version 9: the 21 steps after the review of version 8 / Что нового в версии 9

| | English | Русский |
|---|---|---|
| **Content** | Help articles, banners and contact lines in the other 6 languages (`/admin/site/translate/...`). Page **drafts** with a publishing time, **pictures** inside pages (`![words](/img/site/...)`) and **preview as a customer**. | Перевод статей справки, баннеров и контактов; **черновики** страниц с временем публикации, **картинки** и **предпросмотр**. |
| **Search** | `/find`: one box for products, help articles, pages and (signed in) own orders, parcels and questions; small **typing mistakes are corrected** ("sheos" → "shoes"). `/admin/site/searches`: what people searched and did not find. | `/find`: один поиск с **исправлением опечаток**; список запросов без результата. |
| **Workers** | Number tiles of one's own job on *My work*; **shift swap** (colleague says yes, manager approves); **first-days checklist** (3 steps checked by the system); the clock-in code screen lists who clocked in (first name only). | Плитки с цифрами своей работы; **обмен сменами**; **чек-лист первых дней**; список отметившихся на экране кода. |
| **Help desk** | "**is typing**" on both sides, **files** (JPG, PNG, PDF ≤ 2 MB, checked by their first bytes, private), **stars** for an answer (summary for the team). | «**печатает…**», **файлы**, **звёзды** за ответ. |
| **Customers** | Saved **usual way to pay** (chosen at the checkout), **Buy again**, **change the address** of an order before it is shipped (same country). | Обычный способ оплаты, «Купить снова», смена адреса до отправки. |
| **Sellers** | `/seller/insights`: best products, return reasons, low stock, products not sold. `/seller/import`: many products from one **CSV** (formula cells refused, products off until approved). | Аналитика продавца; **импорт CSV**. |
| **Maps & partners** | The ticket map shows the **operator's live bus** (GTFS-Realtime) instead of the timetable place; the parcel map tells the age of the courier's GPS point. `/partners/api#check`: **webhook signature sandbox**. | Живое положение автобуса на карте билета; проверка подписи вебхука. |
| **Quality** | `tools/release_check.py` (crawler of 11 users + browser click test, exit code for CI); `/status`; **limits per person** in the database (search, sign-in, uploads) with `/admin/monitor/limits`; **nightly backup restored** into an empty database and compared table by table (`/admin/backups#restore-test`). | Проверка перед выпуском, страница состояния, лимиты, ночная копия с тестом восстановления. |
| **Fixed** | Quick find of an **order number** crashed (orders have no status column) — found by the new tests. | Исправлена ошибка быстрого поиска по номеру заказа. |
| **Checks (v9)** | 362 tests on SQLite and PostgreSQL; `tools/release_check.py`: 7,730 pages as 11 users, 16 browser checks, 0 problems; axe-core: 60 pages + the open menu, 0 problems (the restore form of the Backups page got joined labels); bandit: 0 medium/high. | 362 теста; проверка выпуска: 0 проблем; доступность: 60 страниц, 0 проблем. |
| **Fixed** | The **delivery code** on the parcel page was drawn 64 px high (the v8 clock-in screen reused the class `code-box`); a search with two corrected words showed nothing when no product had both words. | Исправлены размер кода доставки и поиск из двух слов. |

**Try version 9:** open `/find?q=sheos`, `/status` and `/partners/api#check` without signing in. As `demo@example.com`: a question in
*Support* (files, stars, "is typing" when an agent writes), *Your Account → My usual way to pay*, an order → *Buy again*.
As `seller@example.com`: *Insights* and *My products → add from CSV*. As `admin@example.com`: *Site content → Translate*,
*Searches with no result*, *Shift swaps*, *Rate limits*, *Backups → Test the newest backup now*.

## What is new in version 8: easy to use, content without code / Что нового в версии 8

| | English | Русский |
|---|---|---|
| **My work button** | Every worker sees his **position** in his profile and at the top of every page. After sign-in one orange button opens his own tool (hub worker → scanner, support → help desk, finance → payroll). `/work` | У каждого сотрудника видна **должность** в профиле и вверху страницы. После входа одна оранжевая кнопка открывает его рабочий инструмент. |
| **☰ menu** | The "three lines" button opens a menu with the profile, a **search of services and pages** (also order / parcel numbers) and all categories. Works without JavaScript too (`/menu`). | Кнопка «три линии» открывает меню: профиль, **поиск сервисов и страниц**, все категории. |
| **Categories** | All categories in one scrolling row in the header, with arrows. | Все категории в одной прокручиваемой строке в шапке. |
| **Admin menu** | Grouped (Shop, Courier, Money, People, Content …), with a filter box and a quick find (`/admin/find`). | Меню админ-панели по группам, с фильтром и быстрым поиском. |
| **Site content** | `/admin/site`: **contacts**, **pages** (About, Terms, Privacy, Returns … with earlier versions and one text per language), **banners** of the first page. `/admin/kb`: help articles. New ability `site.manage`. New pages `/contact`, `/page/<slug>`. | `/admin/site`: **контакты**, **страницы** (О нас, Условия … с версиями и языками), **баннеры**. `/admin/kb`: статьи справки. |
| **People** | Staff news, colleagues list, public holidays (Rwanda, Russia, India 2027) and leave rules per country, year statement, clock-in with an **office code** (changes every minute, QR) or the office network. | Новости для сотрудников, коллеги, праздничные дни и правила отпусков, годовая справка, отметка прихода по **коду офиса** или сети офиса. |
| **Money** | India's e-invoice number (IRN = SHA-256) + JSON + QR on every tax invoice — **demo: not registered**. Tax rates can be marked "checked" by a second person with valid-from/until days. | Номер e-invoice (IRN) + JSON + QR — **демо, без регистрации**. Ставки можно отметить «проверено» вторым человеком. |
| **Partners** | Signed **webhooks** to courier partners with retries (1, 4, 9, 16, 25 min); private addresses refused; made-up addresses never called. | Подписанные **вебхуки** партнёрам с повторами. |
| **Live data** | Courier arrival times from the real GPS speed by town and hour; GTFS-Realtime vehicles and alerts with a background poll; help-desk messages appear live; e-mails from customers (`/api/inbound-email`, signed); analytics rebuilt every hour; forecast marks holidays. | Время прибытия по реальной скорости курьеров; транспорт и объявления GTFS-Realtime; сообщения поддержки появляются сразу; e-mail от клиентов; аналитика каждый час. |
| **Checks** | 326 tests on SQLite and PostgreSQL; axe-core: 49 pages + the open menu, 0 problems; bandit: 0 medium/high; `/.well-known/security.txt`. | 325 тестов; доступность: 49 страниц, 0 проблем. |

## What is new in version 7: the limits of version 6, solved / Что нового в версии 7

| Group | What it does |
|---|---|
| **People** | **Contracts** (permanent, fixed term, part time, internship; the pay is never written to the log), **time off** holiday / sick / unpaid with a **holiday balance** earned month by month, **payroll** of a country and month with **payslips** (example income-tax rules: Russia НДФЛ counted from 1 January, Rwanda PAYE + 6.3 % social contributions, India new regime), **dual control** (the maker of a payroll cannot approve it; with the setting on, the ability "all" also needs a second person), **training certificates** (an ability can require a valid course), **helper offices** (a worker helps another office on chosen days and its manager plans his shifts), **clock-in with the phone position** (300 m from the office, or "remote" where allowed). Worker page: *My work → My contract, holidays and payslips* |
| **Real money** | **Tax invoices** for every order: one per seller and one from Yo for the delivery, GST **by kind of goods** (clothes and shoes 5 % up to ₹2,500 a piece, else 18 %; books 0 %; examples from 22 Sept 2025), numbers **without gaps** per issuer and financial year, **credit notes** for refunds, **bill of supply** for sellers without GSTIN, **GSTIN** (mod-36) and **INN** checks, **UBL 2.1 e-invoice** XML. **Signed payment callbacks** (`/api/payments/<provider>/callback`, HMAC-SHA256, 5-minute window, each event once) and **reconciliation** of a provider's CSV statement with our books. Still **test mode** |
| **Partners & couriers** | **Courier partner API** (`/partners/api`, OpenAPI 3.0): a partner reads its parcels and sends signed tracking events; **delivery time windows** chosen by the sender and an **expected time** for every stop of the route plan (late stops marked); **turn-by-turn** directions (OSRM, kept for offline use); the receiver **signs on the courier's phone**; **HS codes** on customs forms and **electronic customs data** (JSON) for parcels abroad; `/.well-known/assetlinks.json` for an Android wrapper app |
| **Places & travel** | **Postal-code directory**: 145,364 codes of 11 countries (RU, BY, DE, FR, GB, US, ES, IT, TR, ZA, KE; GeoNames, CC BY 4.0) — "660049 belongs to Krasnoyarsk"; **GTFS-Realtime**: upload a file or give an https address; delays and cancellations reach the trips of the day and the travellers (`tools/make_gtfs_rt.py` makes a sample file) |
| **Trust** | Chat model with **277 examples in 7 languages** (was 64), optional **outside moderation service** (`MODERATION_URL`, `MODERATION_KEY`; the shop goes on when it is down), **live check** of the ID check: three photos after two random instructions ("closer", "left", "right") |
| **Shop & data** | **Data warehouse** (`fact_daily`, rebuilt every night) with **analytics** of the own branch and CSV, **hub forecast** for 14 days (Holt-Winters with a weekly season, checked against "same day last week") with the workers needed, **A/B test** of the similar products (hash groups, two-proportion z-test, 100 views each) |
| **Help & safety (new)** | **Help desk** (`/support`): questions with a promised answer time (24 h, urgent 4 h), a queue for the support team sorted by it; **parcel claims** for lost or damaged parcels (declared value, or ₹2,000 without insurance), paid once to Yo Pay; **safety check** of new orders (points for a new account, big amount, many orders in an hour, big cash order, failed payments, a shared phone; 70+ waits for a person) |
| **Access & scale** | **Daily jobs claimed once** for several machines (`claim_period`), optional **Redis** cache layer (`REDIS_URL`), **privacy register** with keeping times applied every day and a log of data requests, **security headers** (Permissions-Policy, COOP, HSTS on https), **"Skip to the content"** link and focus ring, `tools/security_check.py` (bandit + pip-audit), keyboard check in `tools/a11y_check.py` (0 axe problems on 32 pages) |

8 new abilities (45 in all): `payroll.manage`, `invoices.view`, `payments.manage`, `partners.manage`, `support.manage`,
`risk.review`, `analytics.view`, `privacy.manage`. About 220 new texts in Russian, French, Spanish, Arabic, Chinese and Kinyarwanda.

## What is new in version 6: the team, and the future scope of version 5 / Что нового в версии 6

**Staff / Сотрудники** — *"I need workers, and the administrator must be able to add or remove them and give them the ability to do anything."*

| Part | What it does |
|---|---|
| **Office tree** (`/admin/offices`) | Head office → country → region → city / hub / warehouse / support centre / transport company / team. A manager's **branch** = his office and every office under it. Demo: India (Haryana & Punjab region, Ambala hub, Chandigarh support centre), Rwanda (Kigali hub, Gakenke office), Russia (Krasnoyarsk office and warehouse) |
| **Workers and managers** (`/admin/staff`) | Add a worker (existing account, or a new one with a one-time link to choose a password), move, **suspend**, **remove** — access ends at the next click. Managers do this only inside their branch and never for themselves |
| **Abilities** | 37 abilities in version 6, 45 since version 7, 46 since version 8 (`site.manage`) (e.g. `orders.manage`, `hub.scan`, `tickets.check`, `staff.abilities`, `tax.manage`, `photos.review`) and 13 ready-made roles. **Every admin page checks its ability** (GET = view, POST = manage). A manager gives only abilities he has himself; only the administrator gives **"all"** |
| **Staff activity log** (`/admin/staff/log`) | Every change of offices, workers, abilities, shifts, tasks and **every POST on an admin page** (path + field *names*, never values); filters by person, action, office, days, kind |
| **Shifts & attendance** (`/admin/staff/rota`, `/attendance`) | Shift plan in the **local time of each office** (repeat for several days), clock in / out, **on time / late (10 min) / absent / on leave**, hours per office, time off asked and decided |
| **Tasks & performance** (`/admin/staff/tasks`, `/performance`) | Managers give tasks (due day, priority); workers start / finish them on *My work* (`/staff`); overdue tasks; tasks done and on time, hours, late days per worker |
| **Other** | **Two-step sign-in required** for workers (setting), **staff ID card** with a signed QR code anybody can check, **hub scans** (in / sorted / out / problem), **seller employees** with chosen rights (products, orders, stories, board, payouts) |

**Future scope of version 5, done / Что сделано из планов версии 5**

| Group | What it does |
|---|---|
| **Payments & couriers** | **Customs declaration** (CN 22 / CN 23 style, forbidden goods, estimated duties by country rule), **mobile money** M-Pesa / MTN MoMo / SBP / UPI (**test mode**), **VAT of each country** on courier prices (examples, e.g. Rwanda VAT 18 %, Russia НДС 22 % from 2026, editable with `tax.manage`), **bank payouts** for sellers (IBAN mod 97, IFSC, Russian BIK + account key; only bank + last 4 digits + HMAC are stored; test mode), **route plan** for couriers (pickup before drop, nearest neighbour + 2-opt) |
| **Maps, places & travel** | **Postal codes of 186 countries** (postcode-validator data, MIT) + Indian PIN check against India Post (warns "133207 belongs to Ambala"), **GTFS timetable import** (sample feed of a fictional Rwandan bus company), **buses and trains with one change**, **trips through up to 5 towns** |
| **Trust & smart shop** | **ID check** with consent (document + selfie, LBP face score, ICAO 9303 MRZ check digits, a person decides, photos deleted after 30 days), **chat model that learns** (Naive Bayes on letter groups, catches "id1ot", "p.a.y"; learns from moderators), **translation drafts** (glossary + shop memory, or LibreTranslate / DeepL when a key is set), **similar products from texts and pictures** (TF-IDF + colour/edge histograms) |
| **Apps, access & scale** | **Job queue** in the database (several processes, retries), **monitoring** (`/admin/monitor`, `/healthz`, alerts), **shared cache** (memory + database), **installable courier app** with an offline copy of the route, **accessibility audit** with axe-core: 0 WCAG 2.1 A/AA problems on 18 pages (`tools/a11y_check.py`; the orange text was made darker for contrast) |

## What is new in version 5: the whole world / Что нового в версии 5

| Part | What it does |
|---|---|
| **485,665 places** (`geo.py`, `/places`) | Every town of the world with 1,000+ people (GeoNames), **every settlement of Russia** (151,212 more villages, khutors and stanitsas with real coordinates and Russian names; search also in Russian letters: *Красноярск*, *Хилово*), **every village, cell and sector of Rwanda** (14,839 villages, e.g. Mugunga, Gakenke district), **154,874 Indian post offices with their PIN codes**, 3,214 airports. A read-only SQLite file built from `data/geo/places.tsv.gz` (in the Docker image at build time); prefix search with "name, district" and PIN search; the time zone of every place |
| **Home currencies** | 154 currencies (Babel / Unicode CLDR); the default comes from the account country or the browser language (`rw-RW` → RWF); a short menu + "all currencies" list; rates every hour; zero-decimal currencies (RWF, JPY…); cards are charged in the visitor's currency where gateways support it, otherwise in USD. **Times are shown in the visitor's own time zone** |
| **Courier anywhere** | Pickup and delivery in any town or village: place search with autocomplete in the forms, a **pin on the door** (Leaflet + OpenStreetMap), road distance from **OSRM** (OpenStreetMap routing) kept in a cache table (straight line × 1.3 without internet), zones inside every country (local / nearby / whole country, abroad near / world), Indian GST only inside India. A courier also serves the villages up to 15 km around their town. Demo robot couriers appear in a new place when a parcel is booked there |
| **Real street map** | The live tracking map shows OpenStreetMap streets and the road the courier drives (the drawn map is kept as a fallback) |
| **Tickets worldwide** | Buses in every country (and between neighbours: Kigali → Kampala), trains where there are trains (not in Rwanda), flights between all airports with the nearest airport for villages; **connections with one change** (two linked bookings, paid and cancelled together), **round trips** (no convenience fee on the way back), **waiting lists** for full trains (confirmed automatically when a seat comes back; all the money back at chart time) |
| **Sellers** | Product names and descriptions **in every language** (the seller writes them; the original stays for orders), **chat between customers and sellers** with moderation (contacts and links hidden, rude words and "pay me directly" held, rate limit, report button, admin queue), **automatic daily payouts** to the seller's Yo Pay wallet |
| **Smarter checks** | **Face detection** on courier photos with OpenCV's trained Haar cascade, QR scanning in **every browser** (jsQR when there is no BarcodeDetector), the daily quiz in **all 7 languages**, **learned recommendations** (matrix factorization / ALS with NumPy, used only when it beats "most popular" in a hit-rate test) |
| **Privacy & safety** | **Download my data** (JSON) and **delete my account**; at most 5 wrong passwords in 15 minutes per account |

## What is new in version 4: Yo Tickets / Что нового в версии 4

| Part | What it does |
|---|---|
| **Search** (`/tickets`) | Bus, train or flight; from / to (33 cities of India + 12 big cities abroad), date (up to 120 days), 1–6 passengers. No airport in the town? The **nearest airport** is used (Ambala → Chandigarh IXC) with a note; a town without a railway station gets the nearest station's name |
| **Timetables** | The services of a route are made the first time it is searched — **always the same for the same route** (a SHA-256 hash of the route seeds the generator) — and saved, so the admin can switch a service off. Long-distance trains run on some days only. **Times are local**: IST in India, the city's own time zone abroad (Python `zoneinfo`) |
| **Seat maps** | Bus 2+2 seater (40) or 2+1 sleeper with lower / upper deck (30); train coaches SL / 3A / 2A with lower, middle, upper and side berths, CC / EC chair cars; plane 3 business rows + 24 economy rows with **seat fees** (front row, extra legroom, window/aisle) |
| **Fares** | Per km by class; bus and flight fares go up to +30 % as the trip fills (+ last-minute flights); train fares fixed; GST 0 / 5 / 12 %; airport fees; convenience fee; every line in whole rupees |
| **Seat hold** | Chosen seats are kept for **10 minutes**. `UNIQUE(trip_id, seat)` + `INSERT … ON CONFLICT DO NOTHING`: **the same seat can never be sold twice**, even at the same moment; expired holds give the seats back |
| **Payment** | Yo Pay (ledger line `ticket`) or test card, **EMI** by card for bigger amounts. Not enough money? The seats stay held while you add money |
| **E-ticket** | 6-character **PNR**, QR code with an **HMAC-signed link**; anybody can check it is valid, but the names are shown only to the conductor; printable; trip status and a **live map** (position from the timetable) |
| **Cancel & refund** | By mode and hours before departure (bus 10 / 25 / 50 / 100 %, train 5 / 25 / 50 / 100 %, flight 15 / 30 / 60 / 100 %), convenience fee not refunded, instant refund to Yo Pay. **Operator cancels a trip → everybody gets all the money back** |
| **Conductor app** (`/conductor`) | Staff of a transport company: trips of today and tomorrow, passenger list, **scan the QR code** with the phone camera (BarcodeDetector) or type the PNR; boarding from 3 hours before departure; each ticket checked in once; other companies' tickets refused |
| **Messages & points** | Ticket confirmed, reminder 3 hours before, delay, cancellation; **Yo Rewards points** after the trip (1 point per ₹100) |
| **Admin** | Bookings and stats, booking details, **trips: delay or cancel** (with automatic refunds and messages), services on/off, conductors |
| **SEO** | Route pages such as `/tickets/bus/ambala-cantt-to-new-delhi` with timetable, canonical link and sitemap entries |

## Version 3 / Версия 3

| Module | What it does |
|---|---|
| **Yo Courier** (`/courier`) | Send **documents, parcels and groceries**: instant price (first 0.5 kg + every next 0.5 kg + km, **volume weight** L×W×H÷5000, Express ×1.6, documents −20 %, fragile handling, insurance 2 %, GST 18 % in India), pickup in 33 cities (Ambala Division, the Tricity and big cities of India) and delivery to every country. **Consignment number** (YC…), printable **label with QR code**, **live map** (own SVG map, no paid map service), share link, cancel before pickup, rating |
| **Automatic routing to couriers** | The nearest **online** courier in the city whose vehicle can carry the weight, with the fewest open jobs, gets the job (score = km + 3 km per open job); couriers can also take open jobs — **only one can get it** (conditional UPDATE). A courier who does not pick up in time loses the job |
| **Courier app** (`/driver`) | Apply (vehicle, city, photo) → admin approves → go online; the phone sends its **GPS position**; job cards; pickup (cash confirmation), hand-over at the Yo hub, **proof of delivery: 4-digit delivery code** (5 wrong codes lock the job) + receiver's name + photo; failed attempt → Yo hub → second attempt → return. **The earning goes to Yo Pay** after every job (70 % same city, 25 % + 25 % between cities) |
| **Yo network** | Between cities: pickup courier → Yo hub → in transit → destination hub → delivery courier; abroad and in cities without Yo couriers a partner company finishes the delivery. **Demo robot couriers** drive along the route by themselves so a visitor can watch the live map |
| **Yo Pay** (`/pay`) | A real **ledger** (`wallet_txns`): every balance change writes a line in the same transaction, and a payment can never make a balance negative. **Add money** with a test card, **send money** to another Yo user (password like a UPI PIN, limits per transfer and per day), statement + **CSV**, the admin's ledger check |
| **EMI** | EMI calculator (`/pay/emi`, formula E = P·r·(1+r)ⁿ / ((1+r)ⁿ−1)), "EMI from ₹…/month" on product pages, **EMI plan at checkout** for card payments, **no-cost EMI** (3 and 6 months from ₹10,000) |
| **Yo Rewards** (`/rewards`) | **Daily quiz** of 5 questions (the same for everyone in one language, answers checked only on the server, 10 minutes, one try a day), points (+20 for all right, streak bonus, points for delivered orders and reviews), **leaderboard**, rewards: **personal one-time coupons** (30 days, work at checkout, come back if the order is cancelled) or Yo Pay money. Question bank in English and Russian, managed by the admin |
| **Blog & News** (`/blog`) | Deals, seller stories, shopping guides and Yo news in several languages. **Safe Markdown** (HTML escaped, only https/relative links, `[[product:slug]]` product cards). Sellers write stories → admin checks → published. **SEO**: meta description, canonical, Open Graph, **schema.org BlogPosting + BreadcrumbList**, `sitemap.xml`, `robots.txt`, **RSS feed** |
| **Task boards** (from TaskFlow) | Kanban board (To do / Doing / Done, drag and drop) for every **seller**, every **courier** and the **admin team**. Yo adds cards by itself: "Pack order …" for each new order, "Restock …" at low stock, "Give ₹… cash to the Yo hub", new seller/courier applications and stories to check |
| **Automatic photo checks** | Every product picture, courier photo and delivery photo is analysed with Pillow: size, **sharpness** (Laplacian detail ratio), light, white background, and a **dHash fingerprint** that finds the same photo uploaded by another account (fraud check) |
| **Admin** | New pages: Shipments (assign a courier), Couriers (approve, pause, cash received, photo check), Yo Pay ledger, Rewards & Quiz, Blog & News, Photo checks, Team task board |
| **Deployment** | **Fly.io + Neon PostgreSQL**, Dockerfile with **Gunicorn**, `min_machines_running = 0`, health check; the new demo content is added **once**, also to an existing version 2 database |

## Version 2 (still included) / Версия 2
- 7 languages (English, Русский, Français, Español, العربية right-to-left, 中文, Ikinyarwanda) and 8 currencies with daily exchange rates
- Card payments with Stripe Checkout in test mode (signed webhooks), messages by email (Brevo), SMS (Twilio) and Web Push
- Multi-seller marketplace (Seller Centre, commission, payouts), recommendations, returns and refunds, two-factor login
- PostgreSQL online (Neon) and SQLite on a computer, backups

## How to run on your computer / Как запустить

```bash
pip install -r requirements.txt
python app.py
```
Open **http://127.0.0.1:5000**. The database `yo_delivery.db` (SQLite) is created on the first run
with 42 products, customers from 7 countries, a seller, couriers, shipments, blog posts and the quiz.

### Demo accounts / Демо-аккаунты

| Who | Login | Password |
|---|---|---|
| Customer in India (English) | `demo@example.com` | `Demo@1234` |
| Customer in Russia (Русский, RUB) | `anna.smirnova@example.com` | `Shop@2026` |
| Customer in Rwanda (Ikinyarwanda, RWF; also an address in the village of Mugunga) | `amani.uwase@example.com` | `Shop@2026` |
| Seller "HomeEase Kitchen Studio" | `seller@example.com` | `Seller@1234` |
| **Courier** Manish (Ambala Cantt, try the courier app) | `courier@example.com` | `Courier@1234` |
| **Conductor** Gurpreet Kaur (Saffron Roadways buses) | `conductor@example.com` | `Conductor@1234` |
| Admin (Jean Michel Mujyambere) | `admin@example.com` | `Admin@1234` |
| **Regional manager** Rohit Malhotra (Haryana & Punjab: Ambala hub + Chandigarh support) | `rohit.malhotra@example.com` | `Staff@2026` |
| **Hub worker** Sunil Kumar (Ambala hub) | `sunil.kumar@example.com` | `Staff@2026` |
| **Country manager** Olga Smirnova (Russia, Русский) | `olga.smirnova@example.com` | `Staff@2026` |

Other demo customers (same password `Shop@2026`): camille.laurent@example.com (France),
li.wei@example.com (China), omar.almansoori@example.com (UAE), lucia.fernandez@example.com (Spain).
Other demo workers (password `Staff@2026`): anita.verma, kavya.iyer, neha.gill, arjun.mehta, claudine.uwimana,
eric.habimana, jeanbosco.niyonzima, diane.mukamana, dmitry.volkov, irina.petrova (all `@example.com`).
All demo addresses end with `example.com`, so **no email or SMS is ever sent to them**.

**Try Yo Courier:** sign in as the demo customer → *Courier* → *Book a pickup* (e.g. from Ambala Cantt to
Ambala City). A demo courier takes the job at once; open the shipment and watch the live map — the parcel is
delivered in about 4 minutes. To be the courier yourself: sign in as `courier@example.com` in another browser,
*Go online*, then book a pickup in Ambala Cantt — real couriers get jobs before the demo robots.

**Try Yo Tickets:** *Tickets* → bus from Ambala Cantt to New Delhi → choose a seat → pay with Yo Pay → open the
e-ticket. The demo customer already has trips (done, cancelled and upcoming). To check a ticket in: book a
**Saffron Roadways** bus that leaves within 3 hours, sign in as `conductor@example.com` in another browser and type
the PNR (or scan the QR code with a phone). As admin, *Tickets → Trips* lets you delay or cancel a trip.

**Try version 5:** *Courier* → *Book a pickup* from **Kigali** to **Mugunga** (type the first letters and choose
from the list) — a robot courier appears in Kigali. *Tickets* → flight **Krasnoyarsk → Kigali** (a connection with one
change), train **Chandigarh → New Delhi** tomorrow (one class is full: join the waiting list), or a bus with a return
date. Open **HomeEase Pressure Cooker** → *Ask the seller*; switch the language to Русский to see the seller's own text.
*Account → My data & privacy* downloads everything about you. The admin sees *Chat moderation* and *Recommendations*.

**Try version 6:** sign in as `rohit.malhotra@example.com` — *My work* shows his team and a time-off request to decide;
*Staff* shows only his branch (Ambala hub, Chandigarh support centre); try to give a worker an ability he does not
have himself. Sign in as `sunil.kumar@example.com` — clock in, finish his task, open the staff ID card and the hub scan;
`/admin/orders` answers "Ask your manager". As admin: *Offices*, *Staff*, *Shifts & attendance*, *Staff tasks*,
*Activity log*, *Tax rules*, *Timetables (GTFS)*, *ID checks*, *Monitoring*. As the customer: book a parcel Kigali →
Mugunga (VAT 18 %), a parcel to Rwanda (customs form), *Yo Pay → Mobile money*, a bus Ambala Cantt → Mumbai (one
change) or *Tickets → Plan a multi-city trip*.

**Try version 7:** sign in as `eric.habimana@example.com` (Staff@2026) — *My work → My contract, holidays and
payslips* shows his contract, holiday balance, a missing course and the payslip of the paid Rwanda payroll. Sign in
as admin — *Payroll* has an India payroll made by `kavya.iyer@example.com` that waits for **you** (the second person);
*Approvals*, *Staff → a worker → Contract, training*, *Invoices*, *Tax by kind of goods*, *Reconcile payments*,
*Courier partners*, *Support desk*, *Parcel claims*, *Safety checks* (Pavel Orlov's order, 85 points), *Analytics*,
*Hub forecast*, *A/B tests*, *Privacy*. As the customer `demo@example.com`: an order → *Tax invoices* (print or
UBL XML), *Help → Ask our support team*, a delivered parcel → *Lost or damaged? Claim*, *Courier → Book a pickup*
with "When is the receiver at home?". As the courier: the route plan shows the expected time of each stop.

**Test cards:** `4242 4242 4242 4242` (success), `5555 5555 5555 4444` (success),
`4000 0000 0000 0002` (declined), `4000 0000 0000 9995` (not enough money). Any future date, any CVC.

**Tests / Тесты:** `python -m unittest discover -s tests` — **362 tests** (87 of version 2 + 46 of version 3 + 30 of version 4 + 32 of version 5 + 32 of version 6 + 43 of version 7 + 56 of version 8 + 36 of version 9).
The same tests on PostgreSQL: `TEST_DATABASE_URL=postgresql://user@host/dbname python -m unittest discover -s tests`

**Before every release / Перед каждым выпуском (v9):** `python tools/release_check.py` — signs in as 11 kinds of users, opens
every page they can reach (about 7,700 requests) and clicks through the main ways of the shop in Chromium. Exit code 0 = nothing
found. `--crawl-only` works without a browser.
(the test database is emptied — use a separate one).

---

## Put it online: Fly.io + Neon (PostgreSQL) / Размещение в интернете

1. **Neon** (neon.tech, free) — create a project and copy the connection string
   `postgresql://...neon.tech/neondb?sslmode=require` (keep it secret, never paste it into a chat).
2. Install `flyctl`, sign in (`fly auth login`), then in this folder:
   ```bash
   fly launch --no-deploy          # keeps the existing fly.toml (app "yo-delivery"; choose another name if taken)
   fly secrets set DATABASE_URL="postgresql://...neon.tech/neondb?sslmode=require" SECRET_KEY="<a long random text>"
   fly deploy
   ```
3. Open `https://yo-delivery.fly.dev`. The first start fills an empty database (about a minute); a database of
   an earlier version keeps all its data and only gets the new demo content once. Sign in as admin and **change the
   admin password** (My Account).
4. *Optional secrets* — **Stripe test mode** (`STRIPE_SECRET_KEY=sk_test_...`, `STRIPE_WEBHOOK_SECRET`,
   webhook `https://<your-site>/payments/stripe/webhook`), **email** (`BREVO_API_KEY`, `ALERT_EMAIL_FROM`),
   **SMS** (`TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM`), **daily backup by email** (`BACKUP_EMAIL`),
   **translation drafts by a service** (`TRANSLATE_URL` + `TRANSLATE_API_KEY` for LibreTranslate, or `DEEPL_API_KEY`).
   *Version 7 (all optional):* **signed payment callbacks** — one secret per provider, `MPESA_WEBHOOK_SECRET`,
   `MOMO_WEBHOOK_SECRET`, `SBP_WEBHOOK_SECRET`, `UPI_WEBHOOK_SECRET` (cards keep `STRIPE_WEBHOOK_SECRET`; without a
   secret the callback answers 503); **shared cache** `REDIS_URL` (and `pip install redis`); **outside moderation**
   `MODERATION_URL` + `MODERATION_KEY`; **Android wrapper app** `ANDROID_APP_ID` + `ANDROID_APP_SHA256`.
   Example: `fly secrets set MOMO_WEBHOOK_SECRET="<long random text>"`.
   *Version 8 (optional):* `INBOUND_EMAIL_SECRET` — the secret that signs e-mails sent to `/api/inbound-email` by an e-mail provider
   (without it the endpoint answers 401). Set it with `fly secrets set`, never in the code.
   Several Gunicorn workers or machines are fine since version 6: background work goes through the job queue (since version 7 daily jobs are claimed once for all machines).
5. The machine sleeps when nobody visits (`min_machines_running = 0`) and wakes up on the next visit.
   `YO_FAST_TRACKING = "1"` in `fly.toml` keeps the demo speed; set `"0"` for real-time orders and couriers.

Keys and passwords are **only** set with `fly secrets set` — never in the code or on GitHub.
If a password or database URL was ever shown in a chat or a screenshot, reset it in Neon and set the new one.

---

## Shop features / Возможности магазина

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
Hashed passwords, CSRF token in every form and in the JSON calls, parameterized SQL, auto-escaped HTML,
safe Markdown in the blog, safe redirects, two-factor login (TOTP), password-reset links stored only as hashes
and valid 30 minutes, Stripe webhooks checked with HMAC-SHA256, live payment keys refused, sellers see no
customer names or addresses. **Staff (v6):** every admin page checks the worker's ability; managers give only
abilities they have; every change is logged (field names, never values); two-step sign-in can be required for workers.
**Version 7:** payment callbacks and partner events need an HMAC-SHA256 signature (constant-time compare,
5-minute window, each event once); a partner's secret is shown once; a payroll and (optionally) the ability "all"
need a second person; contract pay is never logged; the signature of a receiver is private; security headers
(Permissions-Policy, Cross-Origin-Opener-Policy, HSTS on https); bandit finds no medium or high problem.
**ID checks (v6):** only with consent, seen only with `photos.review`, nobody decides his own, photos deleted after
30 days. **Bank accounts (v6):** only the bank, the last 4 digits and an HMAC fingerprint are stored. **Courier privacy:** the public tracking page and map show only city centres —
no addresses, phone numbers or the delivery code; couriers see the addresses only after taking a job;
courier and delivery photos are never public (`/img/private/...` is blocked, only admins see them).
Every stock / wallet / payment / job change happens in one database transaction, and "once only" actions
(taking a job, using a coupon, finishing the quiz, paying a courier, paying a ticket) use conditional UPDATEs,
and a seat is protected by a UNIQUE key. **Tickets:** the QR code holds an HMAC signature (the key is made once and
kept in the database), the public check shows no names, and only the staff of the right company can check in.

## Structure / Структура
```
yo_delivery_py/
├── app.py            Flask routes: shop, account, checkout, payments, seller centre, admin
├── courier_views.py  Yo Courier pages and the courier app (/courier, /driver)
├── tickets_views.py  Yo Tickets pages, the conductor app (/tickets, /conductor) and its admin pages
├── pay_views.py      Yo Pay pages, EMI calculator        rewards_views.py   Yo Rewards and the daily quiz
├── blog_views.py     Blog, seller stories, sitemap.xml, robots.txt, RSS
├── board_views.py    Task boards (seller, courier, admin)  admin_v3.py   admin pages of the new modules
├── store.py          Shop logic (users, products, cart, orders, reviews, coupons, settings)
├── courier.py        Prices, booking, routing, courier jobs, proof of delivery, network, demo couriers, live map
├── tickets.py        Routes, timetables, seat maps, fares, seat holds, payment, refunds, QR e-tickets, conductor
├── places.py         The 33 cities with coordinates, distances (haversine × road factor), route points
├── geo.py            485,665 places of the world (search, nearest, airports, time zones), OSRM road routes
├── worldwide.py      Road route cache, product texts in other languages, my data, login limit, seller payouts
├── chat.py           Customer–seller chat with moderation       learn.py  learned recommendations (ALS, NumPy)
├── world_views.py, v5_views.py   Pages of version 5 (/places, /messages, /account/data, admin chat & model)
├── quiz_world.py     The quiz in French, Spanish, Arabic, Chinese and Kinyarwanda
├── staff.py          (v6) Offices, workers, abilities, roles, staff log, time off, staff card, hub scans, seller team
├── workforce.py      (v6) Shift plan, attendance, hours, staff tasks, performance
├── staff_views.py, workforce_views.py, v6_views.py, v6b_views.py   Pages of version 6 (/staff, /admin/staff…, /hub)
├── customs.py, mobilemoney.py, taxes.py, bankpay.py, routeplan.py   (v6) Money and couriers
├── addresscheck.py, gtfs.py   (v6) Postal codes and GTFS timetables (data/geo/postcodes.json, data/gtfs/)
├── chatmodel.py, translate.py, contentrec.py, idcheck.py   (v6) Learning chat model, drafts, similar products, ID check
├── jobs.py, cache.py (v6) Job queue, monitoring, alerts, shared cache (+ Redis layer and atomic daily claim, v7)
├── people.py         (v7) Contracts, time off and balance, payroll and payslips, approvals (dual control), training, helper offices, clock-in place
├── invoices.py, providers.py   (v7) Tax invoices, GSTIN / INN checks, credit notes, UBL XML; signed callbacks, reconciliation
├── partners.py, courierplus.py, gtfsrt.py   (v7) Courier partner API; signature, customs data, app links; GTFS-Realtime
├── analytics.py, helpdesk.py, privacy.py, chat_seed7.py   (v7) Warehouse, forecast, A/B test; help desk, claims, safety check; privacy register; chat examples
├── v7_views.py, v7b_views.py   Pages of version 7 (/staff/me, /invoices, /support, /api/partner, /admin/payroll …)
├── services.py, sitecontent.py   (v8) List of services, first page of a worker, menu, quick find; contacts, pages, banners, help articles, staff news
├── holidays.py, einvoice.py, webhooks.py, v8extra.py   (v8) Holidays, leave rules, office code; IRN and rate review; partner webhooks; speed profile, live data, live help desk
├── v8_views.py, demo_v8.py   Pages and demo content of version 8 (/menu, /work, /contact, /admin/site, /admin/kb …)
├── v9.py, v9_views.py, demo_v9.py   (v9) Translations, drafts, search with typos, swaps, onboarding, help-desk files and stars, buy again, seller insights and CSV, live maps, signature sandbox, status, limits, restore test
├── data/geo/         places.tsv.gz, airports.tsv.gz, regions.tsv.gz, countries.json (+ tools/make_places.py, tools/add_russia.py)
├── data/models/      haarcascade_frontalface_default.xml (OpenCV face model, Intel licence)
├── yopay.py          Wallet ledger, top-up, transfers, EMI     rewards.py   quiz, points, personal coupons
├── blog.py           Posts and the safe Markdown            taskboard.py  Kanban tasks and automatic tasks
├── photocheck.py     Automatic photo checks (sharpness, light, size, background, dHash duplicates)
├── payments.py, notices.py, sellers.py, recommend.py, aftersales.py, maintenance.py   (version 2)
├── world.py, i18n.py Languages, currencies, countries; translations/*.json (ru, fr, es, ar, zh, rw)
├── db.py             One database layer for SQLite and PostgreSQL
├── catalog.py, demo_data.py, demo_v3.py … demo_v7.py   Starting products and demo data (added once)
├── templates/        Jinja2 pages (shop, courier/, driver/, tickets/, pay/, rewards/, blog/, board/, seller/, admin/)
├── static/           style.css, superapp.css, tickets.css, world.css, admin.css, yo-map.js, places.js, courier.js,
│                     tickets.js, vendor/jsQR.js, pictures, PWA
├── tools/            collect_strings.py (missing translations), build_catalogs.py (+ tools/tr/*.tsv), a11y_check.py (axe + keyboard),
│                     security_check.py (bandit, pip-audit), make_gtfs_rt.py, add_postcodes.py (postal directory),
│                     release_check.py (v9: crawler of 11 users + browser click test before every release)
├── Dockerfile, fly.toml   Fly.io deployment with Gunicorn
└── tests/            362 tests (SQLite and PostgreSQL)
```

## Database (116 tables + the read-only places file)
Version 2: users, addresses, products, categories, cart, wishlist, orders, order_items, order_events, reviews,
coupons, settings, media, activity, sellers, payouts, views, notifications, push_subscriptions, password_resets,
payments, returns, fx_rates, backups.
**Version 3:** `couriers`, `shipments`, `shipment_events`, `courier_track`, `wallet_txns`, `posts`,
`quiz_questions`, `quiz_attempts`, `points_ledger`, `user_coupons`, `board_tasks`, `photo_checks`.
**Version 4:** `ticket_operators`, `ticket_services`, `ticket_trips`, `ticket_bookings`, `ticket_passengers`,
`ticket_seats` (UNIQUE trip + seat), `ticket_staff`.
**Version 5:** `route_cache`, `product_texts`, `chat_threads`, `chat_messages`, `rec_factors`, `login_failures`
(+ new columns: places of shipments, couriers and addresses; waiting list, connection and return of ticket bookings;
automatic payouts of sellers). The places of the world live in a separate read-only SQLite file (`places`, `airports`).
**Version 6:** `offices`, `staff`, `staff_log`, `staff_shifts`, `staff_leave`, `staff_rota`, `staff_tasks`, `seller_team`,
`jobs`, `request_stats`, `alerts`, `cache_entries`, `customs_rules`, `customs_declarations`, `mobile_payments`,
`tax_rules`, `bank_accounts`, `bank_transfers`, `gtfs_feeds`, `chat_examples`, `content_similar`, `id_checks`
(+ new columns: `sellers.payout_to`, `users.id_verified`, `shipments.tax_label`, GTFS source and dates of ticket services).
**Version 7:** `staff_contracts`, `income_tax_rules`, `payroll_runs`, `payslips`, `approvals`, `staff_trainings`, `staff_offices`,
`tax_category_rates`, `invoice_counters`, `invoices`, `payment_events`, `reconciliations`, `partners`, `partner_events`,
`fact_daily`, `experiments`, `exp_events`, `support_tickets`, `support_messages`, `parcel_claims`, `privacy_requests`
(+ new columns: kind of time off, place of offices, clock-in position, seller tax id, partner / time window / signature of
shipments, live URL of GTFS feeds, live check of ID checks, risk score of orders). The places file also gets a `postcodes` table.

**Place data:** GeoNames (CC BY 4.0), the administrative units of Rwanda (npm `rwanda`), the post offices of India (npm `indian-pincodes`, India Post open data), OurAirports (public domain), "Settlements of Russia: population and geographic coordinates" (Ministry of Health of the Russian Federation, processed by data-in.ru / ANO TsPUR; copy github.com/NickyX3/russia_settlements; CC BY-NC-SA 4.0). Regions that GeoNames (ISO 3166) lists under another country keep that country.
**Postal-code forms:** postcode-validator (MIT, `data/geo/postcodes-LICENSE.txt`). **Postal-code directory (v7):**
GeoNames postal codes, CC BY 4.0 — "Contains data from GeoNames (www.geonames.org)"; copy from
github.com/zauberware/postal-codes-json-xml-csv (`data/geo/postcode_dir-LICENSE.txt`). **Sample GTFS feed:** made for this
project (fictional "Sample Highland Coaches").

**Version 8:** `site_contacts`, `site_pages`, `site_page_versions`, `site_slides`, `kb_articles`, `staff_news`, `staff_news_seen`,
`public_holidays`, `leave_rules`, `webhook_deliveries`, `speed_profile`, `gtfs_alerts`, `gtfs_vehicles`, `inbound_emails`.
**Version 9:** `content_texts`, `search_log`, `shift_swaps`, `onboarding_done`, `support_files`, `support_typing`, `support_ratings`,
`rate_hits`, `rate_blocks`, `backup_checks` (+ new columns: draft title, text and publishing time of pages, `users.pay_pref`).

**Try version 8:** sign in as `neha.gill@example.com` (Staff@2026) — you land on *My work*; press the orange button, open ☰ and
search "claim". Sign in as `admin@example.com` — *Site content* (change the phone number, edit the About page), *Help articles*,
*Public holidays*, *Offices → Clock-in code screen*, *Courier partners → Webhooks*.

## Limitations / Ограничения
- **Content:** translations of help texts, banners and contacts are typed by the staff (no translation service); a page picture has no automatic resizing for phones beyond 1200 px and no gallery; a draft has one scheduled time, no review by a second person.
- **Search:** typo correction works word by word on the shop's own words (difflib, no meaning, no synonyms, Latin letters only); the search log keeps words for 90 days without the name of the person.
- **Workers:** a swap moves one whole shift (no part shifts, no automatic check of working-time law); the onboarding list is the same for every job.
- **Help desk:** "is typing" and new messages are a poll every 2.5–5 seconds, not a WebSocket; files are kept in the database (2 MB each, 10 per question) and are not scanned by an antivirus.
- **Customers:** the usual way to pay is a choice only — no card is stored (tokens of a real provider are needed for one-click card payments); a new address must be in the same country.
- **Sellers:** CSV import adds products only (no update of existing ones, no pictures from the file).
- **Maps:** the live bus needs a real GTFS-Realtime feed of the operator (the demo uses a made-up one); the parcel map shows the last GPS point, not a prediction.
- **Quality:** the status page checks the parts of this one machine; limits count per account or network address and can be passed by many addresses; the restore test uses SQLite even when the shop runs on PostgreSQL.
- **Money stays in test mode on purpose:** card payments, the e-invoice (not registered at India's IRP), partner webhooks and payroll are demos; tax and leave rules are examples.
- **Scale & law:** one free machine; not tested by real workers, accountants or screen-reader users; Russian users' data is not kept in Russia; no outside security audit.
- The list of Russian settlements is licensed CC BY-NC-SA 4.0 (non-commercial use only).
- Translations were prepared by the developer and should be checked by native speakers; the admin panel stays in English on purpose.

## Future scope (version 10)
- A WebSocket help desk with replies by e-mail and an antivirus check of files; a help search that understands meaning.
- Card tokens from a certified provider for true one-click payment; real registration of e-invoices (sandbox first), Russian EDO.
- The first real courier partner and a real GTFS-Realtime feed; a prediction of the arrival time on the map; a native courier app.
- Search with synonyms and Cyrillic / Devanagari typo correction; CSV update of existing products and pictures by link.
- Working-time rules per country for swaps; a second-person review of page drafts; a picture gallery.
- Status page and limits shared by several machines (Redis), restore test on PostgreSQL, a separate analytics database, a database in Russia, an outside security audit.

The Kinyarwanda translation was prepared by the developer and should be reviewed by a native speaker
before real use.

© 2026 Jean Michel Mujyambere. Built as an academic and portfolio project.
