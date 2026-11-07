"""
demo_v3.py - demo content of the version 3 modules (Yo Courier, Yo Pay, Rewards, Blog, task boards).

ensure_v3(store) runs after demo_data.seed(): on a new database AND on a database made by version 2
(for example the online one), but only once - the first process writes the key 'v3_demo' into the
settings table and only that process adds the content.

All people are fictional; every email ends with example.com, so nobody is ever contacted.
"""

import json
import random
import secrets
from datetime import datetime, timedelta

from werkzeug.security import generate_password_hash

import places

COURIER_EMAIL, COURIER_PASSWORD = "courier@example.com", "Courier@1234"

# robot couriers (they drive by themselves in the demo): name, email, mobile, vehicle, number, city
ROBOTS = [
    ("Vikram Singh", "vikram.singh@example.com", "9812100001", "motorbike", "HR01AK4521", "Ambala Cantt"),
    ("Aman Saini", "aman.saini@example.com", "9812100002", "bicycle", "", "Ambala Cantt"),
    ("Pooja Rani", "pooja.rani@example.com", "9812100003", "motorbike", "HR01AB7788", "Ambala City"),
    ("Imran Ali", "imran.ali@example.com", "9812100004", "motorbike", "HR02C1290", "Mullana"),
    ("Deepak Yadav", "deepak.yadav@example.com", "9812100005", "van", "HR07T5512", "Kurukshetra"),
    ("Sunil Kumar", "sunil.kumar@example.com", "9812100006", "motorbike", "HR03P8841", "Panchkula"),
    ("Harpreet Kaur", "harpreet.kaur@example.com", "9812100007", "car", "CH01BX3020", "Chandigarh"),
    ("Rahul Verma", "rahul.verma@example.com", "9812100008", "motorbike", "DL3SAB1022", "New Delhi"),
    ("Kiran Rao", "kiran.rao@example.com", "9812100009", "motorbike", "KA01HG7744", "Bengaluru"),
]

QUESTIONS_EN = [
    ("Geography", "What is the capital city of Rwanda?", ["Kigali", "Butare", "Gisenyi", "Kampala"], 0,
     "Kigali is the capital and the largest city of Rwanda."),
    ("Geography", "Which river flows through Krasnoyarsk?", ["Ob", "Yenisei", "Lena", "Volga"], 1,
     "The Yenisei is one of the longest rivers of Siberia."),
    ("Geography", "Chandigarh is the capital of which two Indian states?",
     ["Punjab and Haryana", "Haryana and Himachal Pradesh", "Punjab and Rajasthan", "Delhi and Haryana"], 0,
     "Chandigarh is a union territory and the capital of both Punjab and Haryana."),
    ("Geography", "Which ocean is the largest?", ["Atlantic", "Indian", "Pacific", "Arctic"], 2,
     "The Pacific Ocean is the largest and the deepest ocean."),
    ("Geography", "Which is the largest country in the world by area?", ["Canada", "China", "Russia", "United States"], 2,
     "Russia covers more than 17 million square kilometres."),
    ("Geography", "Lake Baikal, the deepest lake in the world, is in which country?",
     ["Russia", "Mongolia", "Kazakhstan", "China"], 0, "Lake Baikal is in southern Siberia, Russia."),
    ("Geography", "Which Indian city is often called the 'Silicon Valley of India'?",
     ["Bengaluru", "Hyderabad", "Pune", "Chennai"], 0, "Bengaluru is known for its many IT companies."),
    ("Technology", "What does 'HTTP' stand for?",
     ["HyperText Transfer Protocol", "High Transfer Text Process", "Hyperlink Transfer Technology", "Home Tool Transfer Protocol"], 0,
     "HTTP is the protocol browsers use to get web pages."),
    ("Technology", "In Python, which keyword starts a function?", ["func", "def", "function", "define"], 1,
     "def name(arguments): starts a function in Python."),
    ("Technology", "What does SQL stand for?",
     ["Structured Query Language", "Simple Question Language", "Server Quick Logic", "Sequential Query List"], 0,
     "SQL is the language for working with relational databases like PostgreSQL and SQLite."),
    ("Technology", "Who created the Python programming language?",
     ["Guido van Rossum", "Linus Torvalds", "James Gosling", "Dennis Ritchie"], 0,
     "Guido van Rossum released Python in 1991."),
    ("Technology", "How many bits are in one byte?", ["4", "8", "16", "32"], 1, "One byte is 8 bits."),
    ("Technology", "Which HTML tag makes a link?", ["<a>", "<link>", "<href>", "<url>"], 0,
     "<a href=\"...\"> makes a link; <link> connects files like style sheets."),
    ("Technology", "What does 'PWA' mean for a website you can install on a phone?",
     ["Progressive Web App", "Private Web Access", "Public Wireless App", "Portable Web Archive"], 0,
     "A Progressive Web App can be installed and can show notifications."),
    ("Technology", "Which number system do computers use at the lowest level?", ["Decimal", "Binary", "Octal", "Roman"], 1,
     "Computers store everything as 0 and 1 (binary)."),
    ("Money", "In India, what does GST stand for?",
     ["Goods and Services Tax", "General Sales Tax", "Government Service Tax", "Goods Supply Tax"], 0,
     "GST is the tax on goods and services in India."),
    ("Money", "What does EMI mean when you pay in parts?",
     ["Equated Monthly Instalment", "Extra Monthly Interest", "Easy Money Investment", "Early Monthly Income"], 0,
     "An EMI is the same payment every month until the loan is paid."),
    ("Money", "Which currency is used in Rwanda?", ["Rwandan franc", "Rwandan shilling", "Rwandan rand", "Rwandan dollar"], 0,
     "The Rwandan franc (RWF) is the money of Rwanda."),
    ("Money", "Which currency is used in the United Arab Emirates?", ["Dirham", "Riyal", "Dinar", "Pound"], 0,
     "The UAE dirham (AED)."),
    ("Money", "How many paise make one rupee?", ["10", "50", "100", "1000"], 2, "₹1 = 100 paise."),
    ("Shopping", "What does 'COD' mean in online shopping?",
     ["Cash on Delivery", "Card on Demand", "Coupon of the Day", "Cost of Delivery"], 0,
     "You pay when the order arrives at your door."),
    ("Yo", "On Yo Courier, what does the receiver give the courier to get the parcel?",
     ["The 4-digit delivery code", "The sender's password", "A photo of the parcel", "Nothing"], 0,
     "The sender gets the code and shares it with the receiver - the courier must type it."),
    ("Yo", "How many points does every right answer of the Yo daily quiz give?", ["5", "10", "20", "50"], 1,
     "10 points for each right answer, +20 when all 5 are right."),
    ("Yo", "Volume weight = length × width × height ÷ 5000. What is it for a 50 × 40 × 30 cm box?",
     ["6 kg", "12 kg", "24 kg", "60 kg"], 1, "50 × 40 × 30 = 60,000; 60,000 ÷ 5000 = 12 kg."),
    ("Science", "What is the chemical symbol of gold?", ["Au", "Ag", "Gd", "Go"], 0, "Au comes from the Latin word aurum."),
    ("Science", "At sea level, water boils at how many degrees Celsius?", ["90", "100", "110", "120"], 1,
     "100 °C at normal air pressure."),
    ("Science", "Which planet is called the Red Planet?", ["Venus", "Mars", "Jupiter", "Mercury"], 1,
     "Iron oxide (rust) makes the surface of Mars red."),
    ("Science", "Which vitamin does the skin make in sunlight?", ["Vitamin A", "Vitamin B12", "Vitamin C", "Vitamin D"], 3,
     "Sunlight (UVB) helps the skin make vitamin D."),
    ("General", "How many days are there in a leap year?", ["364", "365", "366", "367"], 2, "February has 29 days in a leap year."),
    ("Security", "Which of these passwords is the strongest?", ["password123", "Ravi1990", "T7#qL!m2v9Zp", "12345678"], 2,
     "Long passwords with letters, digits and symbols are the hardest to guess."),
    ("Security", "What does two-factor login (2FA) add to your password?",
     ["A second proof, like a code from an app", "A longer password", "A second email", "Nothing"], 0,
     "Even if someone learns your password, they also need the code."),
]

QUESTIONS_RU = [
    ("География", "Какая река протекает через Красноярск?", ["Обь", "Енисей", "Лена", "Волга"], 1,
     "Енисей — одна из самых длинных рек Сибири."),
    ("География", "Какой город — столица Руанды?", ["Кигали", "Бужумбура", "Кампала", "Найроби"], 0,
     "Кигали — столица и самый большой город Руанды."),
    ("География", "Какое озеро самое глубокое в мире?", ["Байкал", "Ладожское", "Онежское", "Танганьика"], 0,
     "Глубина Байкала — более 1600 метров."),
    ("Технологии", "Сколько бит в одном байте?", ["4", "8", "16", "32"], 1, "Один байт — это 8 бит."),
    ("Технологии", "Какой язык программирования создал Гвидо ван Россум?", ["Java", "Python", "C", "PHP"], 1,
     "Python появился в 1991 году."),
    ("Деньги", "Что означает EMI при покупке в рассрочку?",
     ["Равный ежемесячный платёж", "Дополнительный процент", "Ежемесячный доход", "Бонусные баллы"], 0,
     "EMI (Equated Monthly Instalment) — одинаковый платёж каждый месяц."),
    ("Деньги", "Какая валюта в ОАЭ?", ["Дирхам", "Риал", "Динар", "Фунт"], 0, "Дирхам ОАЭ (AED)."),
    ("Наука", "Какую планету называют Красной планетой?", ["Венера", "Марс", "Юпитер", "Меркурий"], 1,
     "Оксид железа делает поверхность Марса красной."),
    ("Наука", "Какой химический символ у золота?", ["Au", "Ag", "Fe", "Zn"], 0, "Au — от латинского aurum."),
    ("Yo", "Что получатель называет курьеру Yo, чтобы получить посылку?",
     ["4-значный код доставки", "Пароль отправителя", "Номер карты", "Ничего"], 0,
     "Код получает отправитель и передаёт его получателю."),
    ("Общее", "Сколько дней в високосном году?", ["364", "365", "366", "367"], 2, "В феврале високосного года 29 дней."),
    ("Безопасность", "Какой пароль самый надёжный?", ["qwerty123", "Ivan1990", "T7#qL!m2v9Zp", "12345678"], 2,
     "Длинные пароли с буквами, цифрами и знаками труднее всего подобрать."),
]

POSTS = [
    {"slug": "yo-delivery-3-super-app", "category": "news", "cover": "images/photos/courier.jpg", "days": 1,
     "title": "Yo Delivery 3.0: one app for shopping, parcels, payments and rewards",
     "summary": "Yo Courier, Yo Pay, Yo Rewards and this blog are now part of Yo Delivery. Here is what you can do.",
     "tags": "Yo Courier, Yo Pay, Yo Rewards, news",
     "body": """Yo Delivery started as a university project in the Ambala Division: an online shop with fast delivery. Today it becomes a small **super app** - one account for many everyday services.

## What is new
- **Yo Courier** - send documents, parcels and groceries. A courier picks them up at your door and you follow the parcel on a live map.
- **Yo Pay** - add money with a card, send money to friends, pay for shopping and parcels, and see every payment in your statement.
- **Yo Rewards** - play a 5-question quiz every day, collect points and turn them into coupons or Yo Pay money.
- **EMI** - pay for bigger orders in 3 to 24 monthly parts. Orders from ₹10,000 get no-cost EMI for 3 and 6 months.
- **Blog & News** - deals, guides and stories of the shops that sell on Yo.

> One account, one wallet, one place for your orders and your parcels.

## For sellers and couriers
Sellers get a **task board** in the Seller Centre: every new order adds a "Pack order" card by itself. Couriers get the **Yo Courier app** with jobs near them; their earning goes to Yo Pay after every delivery.

Try it now: [send your first parcel](/courier) or [play today's quiz](/rewards)."""},
    {"slug": "send-documents-same-day-ambala", "category": "guides", "cover": "images/photos/courier.jpg", "days": 3,
     "title": "How to send documents in the Ambala Division the same day",
     "summary": "University papers, contracts or keys: book a pickup in two minutes and track the courier on the map.",
     "tags": "courier, documents, Ambala, guide",
     "body": """Need to send university documents from Mullana to Ambala Cantt, or a contract from Panchkula to Chandigarh? With **Yo Courier** it takes two minutes.

## 1. Get the price
Open [Yo Courier](/courier), choose the two cities and the weight. Documents up to 0.5 kg get **20% off**. In the same city the price does not depend on the distance.

## 2. Book the pickup
Write the addresses and the phone numbers of the sender and the receiver. Tap **Use my location** if you are at the pickup address - the courier sees the exact point.

## 3. Share the tracking link and the code
After booking you get a **consignment number** (it starts with YC) and a secret **4-digit delivery code**. Send the tracking link and the code to the receiver by WhatsApp.

## 4. Watch the courier
The nearest free courier gets your job automatically. On the live map you see where the courier is. The receiver gives the code, the courier types it - and you get a message that the documents arrived.

*Tip:* choose **Express** when it is urgent - it is about twice as fast."""},
    {"slug": "volume-weight-explained", "category": "guides", "cover": "images/photos/shoes-wide.jpg", "days": 6,
     "title": "Volume weight explained: why a big light box costs more",
     "summary": "Couriers charge for space, not only for kilograms. A simple formula shows the weight you pay for.",
     "tags": "courier, price, weight, guide",
     "body": """A pillow weighs almost nothing, but it fills a whole van. That is why every courier company uses **volume weight**.

## The formula
`volume weight (kg) = length × width × height (cm) ÷ 5000`

You pay for the **bigger** of the two: the real weight or the volume weight.

## An example
A box of 50 × 40 × 30 cm with shoes inside weighs 3 kg.
1. 50 × 40 × 30 = 60,000
2. 60,000 ÷ 5000 = **12 kg**
3. You pay for 12 kg, not 3 kg.

## How to pay less
- Use a box that fits the item - empty space costs money.
- Soft items (clothes) can go in a courier bag instead of a box.
- Several small parcels to the same person? Pack them together.

Yo Courier shows both weights before you book, so there are no surprises."""},
    {"slug": "festive-gadget-deals", "category": "deals", "cover": "images/photos/watches-wide.jpg", "days": 2,
     "title": "Festive deals: smart gadgets that make great gifts",
     "summary": "Earbuds, a fitness watch, a power bank and a speaker - our team's picks for this season.",
     "tags": "deals, gadgets, gifts",
     "body": """Looking for a gift that people really use every day? Our team picked four gadgets with big discounts this week.

## For music lovers
Wireless earbuds with long battery life - perfect for the bus and the gym.

[[product:soundwave-buds]]

## For a healthy routine
Steps, heart rate and sleep on your wrist.

[[product:pulsefit-s2]]

## For long trips
Charge your phone four or five times without a socket.

[[product:powermax-20000]]

## For parties
A small speaker with a big sound.

[[product:boomer-speaker]]

*Tip:* pay with **Yo Pay** and keep your **Yo Rewards** coupons for these deals. Bigger orders can be paid in monthly parts with **EMI**."""},
    {"slug": "how-emi-works", "category": "guides", "cover": "images/photos/fashion-wide.jpg", "days": 8,
     "title": "Pay in parts: how EMI works on Yo",
     "summary": "What an EMI is, how the monthly amount is calculated, and when it costs you nothing extra.",
     "tags": "EMI, Yo Pay, money, guide",
     "body": """EMI means **Equated Monthly Instalment**: you pay the same amount every month until the price and the interest are paid.

## The formula
`E = P × r × (1 + r)^n ÷ ((1 + r)^n − 1)`
- **P** - the price
- **r** - the interest for one month (the yearly rate ÷ 12 ÷ 100)
- **n** - the number of months

## Example
A laptop for ₹60,000 over 12 months at 16% a year: r = 0.01333, and the monthly payment is about **₹5,444**. Over the year you pay about ₹5,300 of interest.

## No-cost EMI
For orders from **₹10,000** you can choose 3 or 6 months **without interest** - the shop pays it for you.

Try the [EMI calculator](/pay/emi) or choose EMI when you pay by card at checkout. The rates on Yo are demo numbers, not an offer of a real bank."""},
    {"slug": "a-day-with-a-yo-courier", "category": "news", "cover": "images/photos/courier.jpg", "days": 5,
     "title": "Meet our couriers: a day on a motorbike in Ambala Cantt",
     "summary": "Vikram starts at nine, takes jobs near him, and gets his earning in Yo Pay after every delivery.",
     "tags": "couriers, Ambala, people",
     "body": """Vikram goes **online** in the Yo Courier app at nine in the morning. The first job comes in a minute: university documents from Sector 9 to the Mall Road.

## How the jobs find him
Yo looks for the **nearest free courier** in the city whose vehicle can carry the parcel. Every open job adds 3 km to the distance, so the work is shared fairly. Couriers can also take open jobs from the list themselves.

## Safe handover
At the door Vikram asks for the **delivery code**. Without the right code the app does not let him finish the job - so parcels go only to the right person.

> "I choose my hours, and my money is in Yo Pay right after the delivery."

## Want to join?
If you have a bicycle, a motorbike, a car or a van, [deliver with Yo](/driver).

*Vikram is one of the demo couriers of this project - he drives on the map by himself so that visitors can see how tracking works.*"""},
    {"slug": "safe-online-shopping-tips", "category": "guides", "cover": "images/photos/fashion-wide.jpg", "days": 10,
     "title": "5 tips to stay safe when you shop and pay online",
     "summary": "Strong passwords, two-factor login, and why Yo never asks for your password by phone.",
     "tags": "security, Yo Pay, tips",
     "body": """Online shopping is easy - and it should also be safe. Five simple habits protect your money.

1. **Use a long password** with letters, digits and symbols, and do not use it on other sites.
2. **Turn on two-factor login** in My Account: after the password you also type a code from an app on your phone.
3. **Check the address** of the site before you sign in. Yo Delivery never asks for your password by phone or by message.
4. **Keep the delivery code secret** until the courier is at your door.
5. **Check your Yo Pay statement** from time to time - every payment is there.

> If something looks strange, change your password and write to our support.

Stay safe and happy shopping!"""},
    {"slug": "daily-quiz-rewards", "category": "news", "cover": "images/photos/watches-wide.jpg", "days": 4,
     "title": "Play the daily quiz and win coupons",
     "summary": "Five questions a day, ten points for each right answer, and a bonus for every day in a row.",
     "tags": "Yo Rewards, quiz, coupons",
     "body": """Every day there are **five new questions** in [Yo Rewards](/rewards): geography, technology, money, science and Yo itself.

## Points
- 10 points for every right answer
- +20 when all five are right
- +5 for every day in a row with at least three right answers (up to +25)
- +1 point for every ₹100 of a delivered order, +20 for a product review

## Rewards
- 100 points - a ₹50 coupon
- 250 points - a ₹150 coupon
- 600 points - a ₹400 coupon
- 200 points - ₹50 in Yo Pay

Coupons are personal, work once and are valid for 30 days. Good luck!"""},
]

POSTS_RU = [
    {"slug": "yo-courier-kak-otpravit-dokumenty", "category": "guides", "cover": "images/photos/courier.jpg", "days": 2,
     "lang": "ru", "title": "Yo Courier: как отправить документы за один день",
     "summary": "Цена за минуту, курьер у двери, карта в реальном времени и секретный код доставки.",
     "tags": "курьер, документы",
     "body": """Нужно отправить документы из Муллана в Амбалу или из Панчкулы в Чандигарх? С **Yo Courier** это займёт две минуты.

## Как это работает
1. Откройте [Yo Courier](/courier) и выберите города и вес. Документы до 0,5 кг — **скидка 20%**.
2. Укажите адреса и телефоны отправителя и получателя.
3. Получите **номер отправления** (начинается с YC) и **4-значный код доставки**.
4. Ближайший свободный курьер получает заказ автоматически, а вы видите его на карте.

> Курьер отдаёт посылку только после ввода правильного кода.

Срочно? Выберите **Express** — примерно в два раза быстрее."""},
    {"slug": "ezhednevnaya-viktorina-yo", "category": "news", "cover": "images/photos/watches-wide.jpg", "days": 4,
     "lang": "ru", "title": "Ежедневная викторина Yo: играйте и получайте купоны",
     "summary": "Пять вопросов в день, 10 баллов за правильный ответ и бонус за серию дней.",
     "tags": "Yo Rewards, викторина",
     "body": """Каждый день в [Yo Rewards](/rewards) появляются **пять новых вопросов**.

## Баллы
- 10 баллов за каждый правильный ответ
- +20, если все пять ответов правильные
- +5 за каждый день подряд (до +25)

## Награды
Баллы можно обменять на купоны (₹50, ₹150, ₹400) или на деньги в Yo Pay. Купоны личные и действуют 30 дней.

Удачи!"""},
]

SELLER_STORY = {
    "slug": "homeease-kitchen-studio-story", "category": "stories", "cover": "images/products/homeease-cooker-2.jpg", "days": 7,
    "title": "HomeEase Kitchen Studio: from a small shop in Rai Market to customers across India",
    "summary": "Meera Kapoor tells how her family shop in Ambala Cantt started selling online with Yo.",
    "tags": "seller story, kitchen, Ambala",
    "body": """Our shop in Rai Market has sold pressure cookers and pans for many years. Two years ago customers started to ask: *can you send it to my sister in Delhi?*

## Why we joined Yo
We did not have time to build our own website. On Yo we added our products in one evening, and the orders come with the address, the payment and the delivery already arranged.

## What our customers like
A cooker that lasts for years, and a pan that does not stick.

[[product:homeease-cooker]]

[[product:homeease-pan]]

## How we work now
Every morning our **task board** in the Seller Centre shows the orders to pack. We pack them before noon, and Yo Courier picks them up the same day.

> "We are still a small family shop - now with customers in the whole country."

*Meera Kapoor, HomeEase Kitchen Studio*"""}

SELLER_STORY_REVIEW = {
    "category": "stories", "status": "Review", "days": 0,
    "title": "Our new LED lamps: more light, smaller electricity bills",
    "summary": "Why we started selling LED lamps, and how much a family can save in a year.",
    "tags": "LED, lamps, electricity",
    "body": """Many of our customers asked for lamps that use less electricity. We tested several models for three months before we chose one.

## What we checked
- How bright the light is
- How warm the lamp gets
- How long it lasts

[[product:glow-led-lamp]]

We think every home should have good light without big bills."""}


def _ago(days, hours=0, minutes=0):
    return datetime.now() - timedelta(days=days, hours=hours, minutes=minutes)


def _fmt(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _uid(store, email):
    with store.db() as conn:
        r = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    return r[0] if r else None


def ensure_v3(store):
    """Add the version 3 demo content once (new database or a database of version 2)."""
    with store.db() as conn:                       # only the first process that writes the key adds the content
        claimed = conn.execute("INSERT INTO settings (key, value) VALUES ('v3_demo', ?) ON CONFLICT (key) DO NOTHING",
                               (_fmt(datetime.now()),)).rowcount
    if not claimed:
        return False
    rng = random.Random(33)
    _quiz_bank(store)
    robots = _couriers(store)
    _shipments(store, rng)
    _posts(store)
    _wallets_and_rewards(store, rng)
    _boards(store)
    _photo_checks(store, robots)
    return True


def _quiz_bank(store):
    with store.db() as conn:
        if conn.execute("SELECT 1 FROM quiz_questions").fetchone():
            return
    for lang, bank in (("en", QUESTIONS_EN), ("ru", QUESTIONS_RU)):
        for topic, q, options, answer, why in bank:
            store.save_question({"lang": lang, "topic": topic, "question": q, "explanation": why, "answer": answer,
                                 **{f"option{i}": o for i, o in enumerate(options)}})


def _couriers(store):
    ids = {}
    locked = generate_password_hash(secrets.token_urlsafe(24))     # robots never sign in (one hash for all: faster)
    for i, (name, email, mobile, vehicle, number, city) in enumerate(ROBOTS):
        uid = _uid(store, email)
        if not uid:
            with store.db() as conn:
                uid = conn.execute("INSERT INTO users (name, email, mobile, password, wallet, created_at, country, lang, "
                                   "currency) VALUES (?, ?, ?, ?, 0, ?, 'IN', 'en', 'INR')",
                                   (name, email, mobile, locked, _fmt(_ago(40 - i)))).lastrowid
        if store.courier_of(uid):
            ids[email] = store.courier_of(uid)["id"]
            continue
        cid = store.apply_courier(uid, {"vehicle": vehicle, "city": city, "vehicle_no": number, "agree": "1"})
        lat, lng = places.near_centre(city, name)
        with store.db() as conn:
            conn.execute("UPDATE couriers SET status = 'Approved', demo = 1, online = 1, lat = ?, lng = ?, last_seen = ?, "
                         "created_at = ?, decided_at = ? WHERE id = ?",
                         (lat, lng, _fmt(datetime.now()), _fmt(_ago(39 - i)), _fmt(_ago(38 - i)), cid))
        ids[email] = cid
    # a courier account a visitor can try (moves only when you use it); starts offline
    uid = _uid(store, COURIER_EMAIL) or store.register("Manish Kumar", COURIER_EMAIL, "9812100010", COURIER_PASSWORD,
                                                       wallet=0, when=_fmt(_ago(20)))
    if not store.courier_of(uid):
        cid = store.apply_courier(uid, {"vehicle": "motorbike", "city": "Ambala Cantt", "vehicle_no": "HR01AM2468",
                                        "agree": "1"})
        store.decide_courier(cid, "Approved")
        ids[COURIER_EMAIL] = cid
    # an application waiting for the admin
    uid = _uid(store, "ritu.devi@example.com") or store.register("Ritu Devi", "ritu.devi@example.com", "9812100011",
                                                                 "Shop@2026", wallet=0, when=_fmt(_ago(1)))
    if not store.courier_of(uid):
        ids["ritu"] = store.apply_courier(uid, {"vehicle": "bicycle", "city": "Yamunanagar", "agree": "1"})
    return ids


def _book_and_run(store, uid, f, start, rating=None, feedback=""):
    """Book a shipment in the past and let the demo couriers finish it (time moves in steps)."""
    number = store.book_shipment(uid, f, when=_fmt(start))
    with store.db() as conn:
        sid = conn.execute("SELECT id FROM shipments WHERE number = ?", (number,)).fetchone()[0]
    t = start
    for _ in range(40):
        t += timedelta(seconds=45)
        store.courier_tick(at=t, only=sid)
        if store.get_shipment(number, uid)["status"] == "Delivered":
            break
    if rating:
        try:
            store.rate_shipment(uid, number, rating, feedback)
        except Exception:
            pass
    return number


def _shipments(store, rng):
    ravi = _uid(store, "demo@example.com")
    neha = _uid(store, "neha.verma@example.com")
    sana = _uid(store, "sana.joseph@example.com")
    if not ravi:
        return
    with store.db() as conn:                       # enough demo money for the history below
        if conn.execute("SELECT wallet FROM users WHERE id = ?", (ravi,)).fetchone()[0] < 3000_00:
            store.wallet_move(conn, ravi, 3000_00, "admin", "", "Demo money", when=_fmt(_ago(25)))
    base = {"from_name": "Ravi Kumar", "from_mobile": "9876543210", "from_address": "House 214, Sector 9",
            "from_city": "Ambala Cantt", "payment": "wallet", "service": "standard", "to_country": "IN"}
    _book_and_run(store, ravi, dict(base, kind="document", weight="0.4", contents="University certificates",
                                    to_city="Mullana", to_name="Admission Office MMDU", to_mobile="9812000301",
                                    to_address="MMICT & BM Block, MMDU Campus"), _ago(21, 3), 5, "Fast and polite!")
    _book_and_run(store, ravi, dict(base, kind="parcel", weight="2.2", length="30", width="20", height="15",
                                    contents="Books and a winter jacket", to_city="Bengaluru", to_name="Sana Joseph",
                                    to_mobile="9812000005", to_address="12, 4th Cross, Indiranagar",
                                    service="express", declared="3500", insured="1"), _ago(12, 5), 4)
    _book_and_run(store, ravi, dict(base, kind="grocery", weight="4.5", contents="Fresh vegetables and fruit",
                                    to_city="Ambala Cantt", to_name="Kamla Devi", to_mobile="9812000302",
                                    to_address="88 Mall Road", service="express", payment="cash"), _ago(3, 6), 5)
    _book_and_run(store, ravi, dict(base, kind="document", weight="0.3", contents="Letter and photos",
                                    to_country="RW", to_city="Kigali", to_name="Amani Uwase", to_mobile="+250788555014",
                                    to_address="KG 11 Ave, house 27"), _ago(15, 2))
    if neha:
        _book_and_run(store, neha, {"from_name": "Neha Verma", "from_mobile": "9812000003", "from_address": "Karol Bagh",
                                    "from_city": "New Delhi", "payment": "wallet", "service": "standard",
                                    "to_country": "IN", "kind": "parcel", "weight": "1.2", "contents": "A gift box",
                                    "to_city": "New Delhi", "to_name": "Ritika Verma", "to_mobile": "9812000303",
                                    "to_address": "Lajpat Nagar II"}, _ago(6, 4), 5)
    if sana:
        _book_and_run(store, sana, {"from_name": "Sana Joseph", "from_mobile": "9812000005",
                                    "from_address": "12, 4th Cross, Indiranagar", "from_city": "Bengaluru",
                                    "payment": "wallet", "service": "express", "to_country": "IN", "kind": "document",
                                    "weight": "0.2", "contents": "Signed contract", "to_city": "Bengaluru",
                                    "to_name": "Office of K. Rao", "to_mobile": "9812000304",
                                    "to_address": "MG Road, 3rd floor"}, _ago(2, 3), 4)


def _posts(store):
    admin = store.get_user(_uid(store, "admin@example.com") or 1)
    with store.db() as conn:
        if conn.execute("SELECT 1 FROM posts").fetchone():
            return
    for p in POSTS + POSTS_RU:
        store.save_post(dict(p, status="Published", lang=p.get("lang", "en"), when=_fmt(_ago(p["days"], 2))),
                        admin["id"], admin["name"])
    seller_uid = _uid(store, "seller@example.com")
    shop = store.seller_of(seller_uid) if seller_uid else None
    if shop:
        slug = store.save_post(dict(SELLER_STORY, status="Review", lang="en", when=_fmt(_ago(SELLER_STORY["days"], 4))),
                               seller_uid, "Meera Kapoor", seller_id=shop["id"])
        with store.db() as conn:
            pid = conn.execute("SELECT id FROM posts WHERE slug = ?", (slug,)).fetchone()[0]
        store.set_post_status(pid, "Published")
        with store.db() as conn:
            conn.execute("UPDATE posts SET published_at = ?, views = 37 WHERE id = ?", (_fmt(_ago(6, 22)), pid))
        store.save_post(dict(SELLER_STORY_REVIEW, lang="en"), seller_uid, "Meera Kapoor", seller_id=shop["id"])
    with store.db() as conn:                        # a few views, so the list looks lived-in
        for i, p in enumerate(POSTS + POSTS_RU):
            conn.execute("UPDATE posts SET views = ? WHERE slug = ?", (120 - i * 11, p["slug"]))


def _wallets_and_rewards(store, rng):
    ravi = _uid(store, "demo@example.com")
    if not ravi:
        return
    with store.db() as conn:
        # Yo Pay: a top-up by test card and money sent to a friend
        store.wallet_move(conn, ravi, 2000_00, "topup", "TOP" + _ago(9).strftime("%y%m%d") + "00004242",
                          "Card ending 4242 (test mode)", when=_fmt(_ago(9, 1)))
        neha = _uid(store, "neha.verma@example.com")
        if neha:
            ref = "TRF" + _ago(4).strftime("%y%m%d") + "00001111"
            store.wallet_move(conn, ravi, -350_00, "transfer_out", ref, "To Neha Verma - Lunch money", when=_fmt(_ago(4, 2)))
            store.wallet_move(conn, neha, 350_00, "transfer_in", ref, "From Ravi Kumar - Lunch money", when=_fmt(_ago(4, 2)))
    # quiz history for the leaderboard (the last 6 days, not today: the visitor can play today)
    players = [ravi] + [u for u in (_uid(store, e) for e in ("priya.sharma@example.com", "arjun.mehta@example.com",
                                                                "neha.verma@example.com", "anna.smirnova@example.com",
                                                                "amani.uwase@example.com")) if u]
    for uid in players:
        for d in range(6, 0, -1):
            if rng.random() < (0.9 if uid == ravi else 0.55):
                day = _ago(d).date().isoformat()
                lang = "ru" if uid == _uid(store, "anna.smirnova@example.com") else "en"
                started = _ago(d, rng.randint(1, 10))
                store.start_quiz(uid, lang, day=day, when=_fmt(started))
                attempt = store.quiz_today(uid, None, day)
                qs = store._questions(json.loads(attempt["question_ids"]), with_answers=True)
                answers = {str(q["id"]): (q["answer"] if rng.random() < 0.75 else (q["answer"] + 1) % 4) for q in qs}
                store.finish_quiz(uid, answers, day=day, when=_fmt(started + timedelta(minutes=3)))
    # Ravi already used some points for a coupon
    if store.points(ravi) >= 100:
        store.redeem(ravi, "c50", when=_fmt(_ago(2, 5)))


def _boards(store):
    seller_uid = _uid(store, "seller@example.com")
    shop = store.seller_of(seller_uid) if seller_uid else None
    if shop:
        b = f"seller:{shop['id']}"
        store.add_task(b, {"title": "Take new photos of the cast iron pan", "priority": "normal",
                           "due": (_ago(-2)).date().isoformat(), "notes": "White background, daylight."}, by="Meera")
        store.add_task(b, {"title": "Answer the question about the cooker size", "priority": "high", "status": "doing"},
                       by="Meera")
        store.add_task(b, {"title": "Send the GST invoice of October to the accountant", "status": "done"}, by="Meera")
    store.add_task("admin", {"title": "Write a Diwali deals post for the blog", "priority": "normal",
                             "due": (_ago(-3)).date().isoformat(), "link": "/admin/blog"}, by="Jean")
    store.add_task("admin", {"title": "Check the courier application of Ritu Devi (Yamunanagar)", "priority": "high",
                             "link": "/admin/couriers", "ref": "courier:ritu"}, by="Yo", auto=True)
    store.add_task("admin", {"title": "Check the seller story about LED lamps", "priority": "normal",
                             "link": "/admin/blog?status=Review", "ref": "post:led"}, by="Yo", auto=True)
    store.add_task("admin", {"title": "Add 10 more quiz questions in French", "status": "doing", "link": "/admin/rewards"},
                   by="Jean")
    store.add_task("admin", {"title": "Approve the new demo couriers", "status": "done"}, by="Jean")
    manish = store.courier_of(_uid(store, COURIER_EMAIL))
    if manish:
        b = f"courier:{manish['id']}"
        store.add_task(b, {"title": "Service the motorbike (oil change)", "due": (_ago(-5)).date().isoformat()}, by="Manish")
        store.add_task(b, {"title": "Buy a rain cover for the delivery bag", "priority": "low"}, by="Manish")


def _photo_checks(store, couriers):
    """Run the automatic photo check on a few product pictures and two courier photos (one is a copy)."""
    import os
    from PIL import Image, ImageOps

    import photocheck
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "images")
    for slug in ("homeease-cooker", "homeease-kettle", "homeease-pan", "glow-led-lamp", "soundwave-buds", "book-python"):
        path = os.path.join(here, "products", f"{slug}-1.jpg")
        if os.path.exists(path):
            with Image.open(path) as img:
                store.save_photo_check(f"products/{slug}-1.jpg", "product", photocheck.analyse(img), owner=slug)
    path = os.path.join(here, "photos", "courier.jpg")
    if not os.path.exists(path):
        return
    import io
    with Image.open(path) as img:
        face = ImageOps.fit(img.convert("RGB"), (600, 600))
    for key, owner_email in (("manish", COURIER_EMAIL), ("ritu", "ritu.devi@example.com")):
        cid = couriers.get(COURIER_EMAIL) if key == "manish" else couriers.get("ritu")
        if not cid:
            continue
        pic = face if key == "manish" else face.resize((420, 420))       # the same photo, smaller: a copy
        buf = io.BytesIO()
        pic.save(buf, "JPEG", quality=85)
        name = f"private/couriers/{cid}.jpg"
        store.save_media(name, "image/jpeg", buf.getvalue())
        report = store.save_photo_check(name, "courier", photocheck.analyse(pic, "courier"),
                                        owner=str(_uid(store, owner_email)))
        with store.db() as conn:
            conn.execute("UPDATE couriers SET photo = ?, photo_check = ? WHERE id = ?", (name, str(report["score"]), cid))
