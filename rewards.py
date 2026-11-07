"""
rewards.py - Yo Rewards (mixin class used by Store): a daily quiz, points and personal coupons.

  * Every day there is a quiz of 5 questions - the same questions for everybody who uses the same
    language (chosen from the question bank with the date as the random seed).
    One try per person per day; the answers are checked on the server (the page never gets them).
  * Points: 10 for every right answer, +20 when all 5 are right, +5 for every day of the streak
    (days in a row with at least 3 right answers, at most +25). Also 1 point per ₹100 of a delivered
    order and 20 points for a product review.
  * Points buy personal coupons (one use, 30 days) or Yo Pay money. A coupon goes back to the customer
    when the order is cancelled.
"""

import json
import random
from datetime import date, datetime, timedelta

import world
from core import ShopError, now

QUESTIONS_PER_DAY = 5
QUIZ_MINUTES = 10                 # time to answer after opening the quiz
POINTS_RIGHT, POINTS_ALL, POINTS_STREAK, STREAK_MAX = 10, 20, 5, 25
POINTS_REVIEW = 20
COUPON_DAYS = 30
# key: (points, what you get in paise, minimum order in paise, kind)
REWARDS = {
    "c50": (100, 50_00, 499_00, "coupon"),
    "c150": (250, 150_00, 999_00, "coupon"),
    "c400": (600, 400_00, 1999_00, "coupon"),
    "w50": (200, 50_00, 0, "wallet"),
}

REWARDS_SCHEMA = """
CREATE TABLE IF NOT EXISTS quiz_questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, lang TEXT NOT NULL DEFAULT 'en', question TEXT NOT NULL,
    options TEXT NOT NULL, answer INTEGER NOT NULL, explanation TEXT NOT NULL DEFAULT '',
    topic TEXT NOT NULL DEFAULT 'General', active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quiz_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, day TEXT NOT NULL, lang TEXT NOT NULL,
    question_ids TEXT NOT NULL, answers TEXT NOT NULL DEFAULT '', correct INTEGER NOT NULL DEFAULT 0,
    points INTEGER NOT NULL DEFAULT 0, streak INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL, finished_at TEXT NOT NULL DEFAULT '', UNIQUE (user_id, day)
);
CREATE TABLE IF NOT EXISTS points_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, points INTEGER NOT NULL,
    reason TEXT NOT NULL, ref TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_points_user ON points_ledger(user_id);
CREATE TABLE IF NOT EXISTS user_coupons (
    code TEXT PRIMARY KEY, user_id INTEGER NOT NULL, amount INTEGER NOT NULL, min_order INTEGER NOT NULL,
    points INTEGER NOT NULL, expires_at TEXT NOT NULL, used_order TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);
"""


def short_name(name):
    parts = str(name or "").split()
    return parts[0] + (f" {parts[-1][0]}." if len(parts) > 1 else "") if parts else "?"


class RewardsMixin:

    # ===== points =====

    def points(self, user_id):
        with self.db() as conn:
            return conn.execute("SELECT COALESCE(SUM(points), 0) FROM points_ledger WHERE user_id = ?",
                                (user_id,)).fetchone()[0]

    def add_points(self, conn, user_id, points, reason, ref="", once=False, when=None):
        """once=True: only if this user has no line with the same reason and ref yet (e.g. one order)."""
        if once and conn.execute("SELECT 1 FROM points_ledger WHERE user_id = ? AND reason = ? AND ref = ?",
                                 (user_id, reason, ref)).fetchone():
            return False
        conn.execute("INSERT INTO points_ledger (user_id, points, reason, ref, created_at) VALUES (?, ?, ?, ?, ?)",
                     (user_id, points, reason, ref, when or now()))
        return True

    def points_history(self, user_id, limit=50):
        with self.db() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM points_ledger WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, limit))]

    # ===== the daily quiz =====

    def quiz_language(self, lang):
        with self.db() as conn:
            n = conn.execute("SELECT COUNT(*) FROM quiz_questions WHERE lang = ? AND active = 1", (lang,)).fetchone()[0]
        return lang if n >= QUESTIONS_PER_DAY else "en"

    def todays_questions(self, lang, day=None):
        day = day or date.today().isoformat()
        with self.db() as conn:
            ids = [r[0] for r in conn.execute("SELECT id FROM quiz_questions WHERE lang = ? AND active = 1 ORDER BY id",
                                              (lang,))]
        if len(ids) < QUESTIONS_PER_DAY:
            raise ShopError("The quiz is not ready yet. Please come back later.")
        return random.Random(f"yo-quiz-{lang}-{day}").sample(ids, QUESTIONS_PER_DAY)

    def quiz_today(self, user_id, lang, day=None):
        """The attempt of today (or None) and the questions without their answers."""
        day = day or date.today().isoformat()
        with self.db() as conn:
            a = conn.execute("SELECT * FROM quiz_attempts WHERE user_id = ? AND day = ?", (user_id, day)).fetchone()
        return dict(a) if a else None

    def start_quiz(self, user_id, lang, day=None, when=None):
        day = day or date.today().isoformat()
        lang = self.quiz_language(lang)
        ids = self.todays_questions(lang, day)
        with self.db() as conn:
            conn.execute("INSERT INTO quiz_attempts (user_id, day, lang, question_ids, started_at) VALUES (?, ?, ?, ?, ?) "
                         "ON CONFLICT (user_id, day) DO NOTHING", (user_id, day, lang, json.dumps(ids), when or now()))
            a = dict(conn.execute("SELECT * FROM quiz_attempts WHERE user_id = ? AND day = ?", (user_id, day)).fetchone())
        return a, self._questions(json.loads(a["question_ids"]), with_answers=bool(a["finished_at"]))

    def _questions(self, ids, with_answers=False):
        with self.db() as conn:
            rows = {r["id"]: dict(r) for r in conn.execute(
                f"SELECT * FROM quiz_questions WHERE id IN ({', '.join('?' * len(ids))})", ids)}
        out = []
        for i in ids:
            q = rows.get(i)
            if q is None:
                continue
            q["options"] = json.loads(q["options"])
            if not with_answers:
                q.pop("answer"), q.pop("explanation")
            out.append(q)
        return out

    def finish_quiz(self, user_id, answers, day=None, when=None):
        """answers: {question_id: option index}. Returns the result with the right answers."""
        day = day or date.today().isoformat()
        when = when or now()
        with self.db() as conn:
            a = conn.execute("SELECT * FROM quiz_attempts WHERE user_id = ? AND day = ?", (user_id, day)).fetchone()
            if a is None:
                raise ShopError("Please open today's quiz first.")
            if a["finished_at"]:
                raise ShopError("You have already played today. Come back tomorrow!")
            started = datetime.strptime(a["started_at"], "%Y-%m-%d %H:%M:%S")
            late = datetime.strptime(when, "%Y-%m-%d %H:%M:%S") - started > timedelta(minutes=QUIZ_MINUTES)
            ids = json.loads(a["question_ids"])
            qs = {q["id"]: q for q in self._questions(ids, with_answers=True)}
            given, correct = {}, 0
            for qid in ids:
                try:
                    pick = int(answers.get(str(qid), answers.get(qid, -1)))
                except (TypeError, ValueError):
                    pick = -1
                given[str(qid)] = pick
                if not late and pick == qs[qid]["answer"]:
                    correct += 1
            yesterday = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
            prev = conn.execute("SELECT streak, correct FROM quiz_attempts WHERE user_id = ? AND day = ? AND finished_at <> ''",
                                (user_id, yesterday)).fetchone()
            streak = (prev["streak"] + 1 if prev and prev["correct"] >= 3 else 1) if correct >= 3 else 0
            points = correct * POINTS_RIGHT + (POINTS_ALL if correct == QUESTIONS_PER_DAY else 0)
            if streak > 1:
                points += min(STREAK_MAX, POINTS_STREAK * (streak - 1))
            if conn.execute("UPDATE quiz_attempts SET answers = ?, correct = ?, points = ?, streak = ?, finished_at = ? "
                            "WHERE id = ? AND finished_at = ''",
                            (json.dumps(given), correct, points, streak, when, a["id"])).rowcount == 0:
                raise ShopError("You have already played today. Come back tomorrow!")
            if points:
                self.add_points(conn, user_id, points, "Daily quiz", day, when=when)
        return {"correct": correct, "points": points, "streak": streak, "late": late, "given": given,
                "questions": [qs[i] for i in ids]}

    def quiz_result(self, user_id, day=None):
        a = self.quiz_today(user_id, None, day)
        if not a or not a["finished_at"]:
            return None
        ids = json.loads(a["question_ids"])
        return {"correct": a["correct"], "points": a["points"], "streak": a["streak"], "late": False,
                "given": json.loads(a["answers"]), "questions": self._questions(ids, with_answers=True)}

    def leaderboard(self, days=7, limit=10):
        since = (date.today() - timedelta(days=days - 1)).isoformat()
        with self.db() as conn:
            rows = conn.execute("SELECT u.name, SUM(a.points) AS points, SUM(a.correct) AS correct, COUNT(*) AS days "
                                "FROM quiz_attempts a JOIN users u ON u.id = a.user_id WHERE a.day >= ? AND "
                                "a.finished_at <> '' GROUP BY a.user_id, u.name ORDER BY SUM(a.points) DESC, u.name "
                                "LIMIT ?", (since, limit)).fetchall()
        return [{"name": short_name(r["name"]), "points": r["points"], "correct": r["correct"], "days": r["days"]}
                for r in rows]

    # ===== spending points =====

    def redeem(self, user_id, key, when=None):
        if key not in REWARDS:
            raise ShopError("Please choose a reward.")
        cost, value, minimum, kind = REWARDS[key]
        when = when or now()
        with self.db() as conn:
            if conn.is_postgres:                 # one redeem at a time for this user (no double spending)
                conn.execute("SELECT id FROM users WHERE id = ? FOR UPDATE", (user_id,))
            have = conn.execute("SELECT COALESCE(SUM(points), 0) FROM points_ledger WHERE user_id = ?",
                                (user_id,)).fetchone()[0]
            if have < cost:
                raise ShopError(f"You need {cost} points for this reward (you have {have}).")
            if kind == "wallet":
                ref = "RWD" + datetime.now().strftime("%y%m%d") + "".join(random.choices("0123456789", k=6))
                self.add_points(conn, user_id, -cost, "Reward: Yo Pay money", ref, when=when)
                self.wallet_move(conn, user_id, value, "reward", ref, f"{cost} Yo Rewards points", when=when)
                return {"kind": "wallet", "amount": value}
            code = "YO" + "".join(random.choices("ABCDEFGHJKLMNPQRSTUVWXYZ23456789", k=6))
            expires = (datetime.strptime(when, "%Y-%m-%d %H:%M:%S") + timedelta(days=COUPON_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
            self.add_points(conn, user_id, -cost, "Reward: coupon", code, when=when)
            conn.execute("INSERT INTO user_coupons (code, user_id, amount, min_order, points, expires_at, created_at) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?)", (code, user_id, value, minimum, cost, expires, when))
            return {"kind": "coupon", "code": code, "amount": value, "min_order": minimum, "expires_at": expires}

    def my_coupons(self, user_id):
        with self.db() as conn:
            rows = [dict(r) for r in conn.execute("SELECT * FROM user_coupons WHERE user_id = ? ORDER BY created_at DESC",
                                                  (user_id,))]
        t = now()
        for r in rows:
            r["state"] = "Used" if r["used_order"] else ("Expired" if r["expires_at"] < t else "Active")
        return rows

    def personal_coupon(self, code, user_id, items_total):
        """Value of a personal reward coupon, or None when the code is not a personal coupon."""
        with self.db() as conn:
            c = conn.execute("SELECT * FROM user_coupons WHERE code = ?", (code,)).fetchone()
        if c is None:
            return None
        if user_id is None or c["user_id"] != user_id:
            raise ShopError("This coupon belongs to another account.")
        if c["used_order"]:
            raise ShopError("This coupon was already used.")
        if c["expires_at"] < now():
            raise ShopError("This coupon has expired.")
        if items_total < c["min_order"]:
            raise ShopError(f"Add items worth {world.price(c['min_order'] - items_total)} more to use {code}.")
        return c["amount"]

    def use_personal_coupon(self, conn, code, user_id, order_number):
        changed = conn.execute("UPDATE user_coupons SET used_order = ? WHERE code = ? AND user_id = ? AND used_order = ''",
                               (order_number, code, user_id)).rowcount
        exists = conn.execute("SELECT 1 FROM user_coupons WHERE code = ?", (code,)).fetchone()
        if exists and not changed:
            raise ShopError("This coupon was already used.")

    def give_back_coupon(self, conn, order_number):
        conn.execute("UPDATE user_coupons SET used_order = '' WHERE used_order = ?", (order_number,))

    # ===== question bank (admin) =====

    def quiz_questions(self, lang=""):
        with self.db() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM quiz_questions " + ("WHERE lang = ? " if lang else "") + "ORDER BY lang, id",
                ((lang,) if lang else ()))]
        for r in rows:
            r["options"] = json.loads(r["options"])
        return rows

    def save_question(self, f, question_id=None):
        text = " ".join(str(f.get("question", "")).split())[:300]
        options = [" ".join(str(f.get(f"option{i}", "")).split())[:120] for i in range(4)]
        explanation = " ".join(str(f.get("explanation", "")).split())[:400]
        topic = " ".join(str(f.get("topic", "General")).split())[:40] or "General"
        lang = f.get("lang", "en")
        try:
            answer = int(f.get("answer", -1))
        except (TypeError, ValueError):
            answer = -1
        if len(text) < 10:
            raise ShopError("Please write the question (at least 10 characters).")
        if any(not o for o in options) or len({o.lower() for o in options}) < 4:
            raise ShopError("Please write 4 different answers.")
        if not 0 <= answer <= 3:
            raise ShopError("Please mark the right answer.")
        if lang not in world.LANGUAGES:
            raise ShopError("Please choose the language.")
        with self.db() as conn:
            if question_id:
                conn.execute("UPDATE quiz_questions SET lang = ?, question = ?, options = ?, answer = ?, explanation = ?, "
                             "topic = ? WHERE id = ?",
                             (lang, text, json.dumps(options, ensure_ascii=False), answer, explanation, topic, question_id))
                return question_id
            cur = conn.execute("INSERT INTO quiz_questions (lang, question, options, answer, explanation, topic, created_at) "
                               "VALUES (?, ?, ?, ?, ?, ?, ?)",
                               (lang, text, json.dumps(options, ensure_ascii=False), answer, explanation, topic, now()))
            return cur.lastrowid

    def toggle_question(self, question_id):
        with self.db() as conn:
            conn.execute("UPDATE quiz_questions SET active = 1 - active WHERE id = ?", (question_id,))

    def rewards_stats(self):
        with self.db() as conn:
            r = conn.execute("SELECT COUNT(*), COALESCE(SUM(correct), 0), COALESCE(SUM(points), 0) FROM quiz_attempts "
                             "WHERE finished_at <> ''").fetchone()
            c = conn.execute("SELECT COUNT(*), COALESCE(SUM(CASE WHEN used_order <> '' THEN 1 ELSE 0 END), 0) "
                             "FROM user_coupons").fetchone()
        return {"attempts": r[0], "correct": r[1], "points": r[2], "coupons": c[0], "coupons_used": c[1]}
