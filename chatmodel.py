"""
chatmodel.py - version 6 (mixin class used by Store): chat moderation that learns from the moderators.

Version 5 held a message when it contained a word of a short list. People write around such lists
("p.a.y m3 d1rectly", "id1ot", "W E S T E R N union"), and a list never knows new words. Version 6 adds two things:

1. Normalisation before the rules: letters and digits that people swap (0 -> o, 1 -> i, 3 -> e, 4 -> a, 5 -> s,
   7 -> t, @ -> a, $ -> s), dots, dashes and spaces between single letters, and letters written many times
   ("stuuupid") are cleaned first, so the word lists of chat.py find them again.
2. A small Naive Bayes model (the classic method of spam filters): it reads the letter groups of 3 and 4
   characters of a message (this works for every language, also Chinese and Arabic) and gives the chance that a
   moderator would hide it. It starts from examples in 7 languages and learns from every decision of a moderator
   (shown = good example, hidden = bad example). A message with a chance of HOLD_P or more waits for a person.
   The model never hides a message by itself: it only sends it to the moderators. /admin/chat shows how well it
   does (checked on examples it did not learn from).
"""

import math
import random
import re
import unicodedata

from core import now

CHATMODEL_SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_examples (
    id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, label TEXT NOT NULL, source TEXT NOT NULL,
    message_id INTEGER UNIQUE, created_at TEXT NOT NULL
);
"""

HOLD_P = 0.9
LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s", "!": "i",
                      "|": "l", "€": "e"})

# starting examples (written for this project): "bad" = a moderator would hide it
SEED_BAD = [
    "pay me directly and I give you a discount", "send the money to my bank account, not on the site",
    "pay outside the app, it is cheaper", "western union me the money", "let's talk on whatsapp, I sell cheaper there",
    "you are an idiot", "stupid seller, worst shop", "give me your card number and the code",
    "send me the OTP you got by sms", "transfer money to my wallet first then I send", "you fool, answer me",
    "оплати напрямую на карту, без сайта", "переведи деньги мне на карту", "ты идиот", "тупой продавец",
    "напиши мне в телеграм, там дешевле", "скинь код из смс",
    "payez-moi directement, c'est moins cher", "envoie l'argent sur mon compte", "espèce d'imbécile",
    "págame directamente fuera de la tienda", "envíame el dinero a mi cuenta", "eres un idiota",
    "ادفع لي مباشرة خارج الموقع", "أرسل المال إلى حسابي", "أنت غبي",
    "直接付款给我，不要在网站上", "把钱转到我的账户", "你是笨蛋",
    "nyishyura ku ruhande, ntunyuze ku rubuga", "ohereza amafaranga kuri konti yanjye", "uri ikigoryi",
]
SEED_OK = [
    "hello, is this still available?", "does it work on an induction stove?", "when will you send my order?",
    "thank you, the parcel arrived today", "can I get it in blue?", "what is the size of the box?",
    "is there a warranty?", "I paid on the site, please confirm", "please pack it well, it is a gift",
    "how many days to Kigali?", "great product, thank you", "can I return it if it does not fit?",
    "здравствуйте, товар есть в наличии?", "когда отправите заказ?", "спасибо, посылка пришла",
    "есть ли гарантия?", "какой размер коробки?",
    "bonjour, est-il encore disponible ?", "merci, le colis est arrivé", "quand envoyez-vous la commande ?",
    "hola, ¿todavía está disponible?", "gracias, llegó el paquete", "¿cuándo envían mi pedido?",
    "مرحبا، هل المنتج متوفر؟", "شكرا، وصل الطرد", "متى ترسل طلبي؟",
    "你好，还有货吗？", "谢谢，包裹到了", "什么时候发货？",
    "muraho, iki gicuruzwa kiracyahari?", "murakoze, ipaki yageze", "muzohereza ryari ibyo natumije?",
]


def normalize(text):
    """Lower case, the same form of every letter, swapped digits back to letters, separators between single letters
    removed and long repeats shortened: "P.a.y  m3 d1rectly!!" -> "pay me directly"."""
    t = unicodedata.normalize("NFKC", str(text or "")).lower()
    t = re.sub(r"(?<=[^\W\d_])[0134578@$!|€](?=[^\W\d_]|\b)", lambda m: m.group(0).translate(LEET), t)
    t = re.sub(r"\b[0134578@$](?=[^\W\d_]{2,})", lambda m: m.group(0).translate(LEET), t)
    t = re.sub(r"\b(?:[^\W\d_][\s.\-_*]){2,}[^\W\d_]\b", lambda m: re.sub(r"[\s.\-_*]", "", m.group(0)), t)
    t = re.sub(r"([^\W\d_])\1{2,}", r"\1\1", t)
    return " ".join(t.split())


def features(text):
    t = " " + normalize(text) + " "
    out = set()
    for n in (3, 4):
        for i in range(len(t) - n + 1):
            g = t[i:i + n]
            if g.strip():
                out.add(g)
    out.update("w:" + w for w in re.findall(r"[^\W\d_]{2,}", t))
    return out


def train_counts(examples):
    """{feature: [bad, ok]}, {"bad": n, "ok": n} from (text, label) pairs."""
    counts, totals = {}, {"bad": 0, "ok": 0}
    for text, label in examples:
        totals[label] += 1
        for f in features(text):
            c = counts.setdefault(f, [0, 0])
            c[0 if label == "bad" else 1] += 1
    return counts, totals


def p_bad(text, counts, totals):
    """The chance (0..1) that the message is a "bad" one (Naive Bayes, Laplace smoothing)."""
    nb, no = totals.get("bad", 0), totals.get("ok", 0)
    if nb == 0 or no == 0:
        return 0.0
    score = math.log(nb / (nb + no)) - math.log(no / (nb + no))
    seen = 0
    for f in features(text):
        b, o = counts.get(f, (0, 0))
        if b + o == 0:
            continue
        seen += 1
        score += math.log((b + 1) / (nb + 2)) - math.log((o + 1) / (no + 2))
    if seen < 3:                                          # too little evidence: say nothing
        return 0.0
    return 1 / (1 + math.exp(-max(-40, min(40, score))))


def evaluate(examples, folds=5, seed=6, threshold=0.5):
    """Cross-validation: train on 4/5 of the examples, test on the other 1/5, five times. A message counts as "held"
    from the given chance on (0.5 here; the shop itself holds only from HOLD_P = 0.9, which gives fewer false alarms)."""
    ex = list(examples)
    if len(ex) < 10 or len({lab for _, lab in ex}) < 2:
        return None
    random.Random(seed).shuffle(ex)
    tp = fp = fn = tn = 0
    for k in range(folds):
        test = ex[k::folds]
        train = [e for i, e in enumerate(ex) if i % folds != k]
        counts, totals = train_counts(train)
        for text, label in test:
            held = p_bad(text, counts, totals) >= threshold
            if held and label == "bad":
                tp += 1
            elif held:
                fp += 1
            elif label == "bad":
                fn += 1
            else:
                tn += 1
    n = tp + fp + fn + tn
    return {"examples": len(ex), "accuracy": round(100 * (tp + tn) / n), "caught": round(100 * tp / max(1, tp + fn)),
            "false_alarms": round(100 * fp / max(1, fp + tn))}


class ChatModelMixin:
    _chat_counts = None

    def ensure_chat_model(self):
        with self.db() as conn:                          # version 7: more starting examples in 7 languages
            if not conn.execute("SELECT 1 FROM chat_examples WHERE source = 'seed7' LIMIT 1").fetchone():
                import chat_seed7
                for label, texts in (("bad", chat_seed7.BAD), ("ok", chat_seed7.OK)):
                    for t in texts:
                        conn.execute("INSERT INTO chat_examples (text, label, source, created_at) VALUES (?, ?, 'seed7', ?)",
                                     (t, label, now()))
            if conn.execute("SELECT 1 FROM chat_examples WHERE source = 'seed' LIMIT 1").fetchone():
                return
            for t in SEED_BAD:
                conn.execute("INSERT INTO chat_examples (text, label, source, created_at) VALUES (?, 'bad', 'seed', ?)",
                             (t, now()))
            for t in SEED_OK:
                conn.execute("INSERT INTO chat_examples (text, label, source, created_at) VALUES (?, 'ok', 'seed', ?)",
                             (t, now()))
        self.train_chat_model()

    def chat_examples(self):
        with self.db() as conn:
            return [(r["text"], r["label"]) for r in conn.execute("SELECT text, label FROM chat_examples ORDER BY id")]

    def train_chat_model(self):
        """Build the counts again from every example (seed + moderator decisions)."""
        counts, totals = train_counts(self.chat_examples())
        self._chat_counts = (counts, totals)
        return {"features": len(counts), **totals}

    def external_moderation(self, text):
        """Version 7: an outside moderation service (a multilingual language model), when MODERATION_URL is set.
        It gets {"input": text} and answers {"flagged": true/false}. Errors and slow answers count as "no answer":
        the message is then judged by the rules and our own model only."""
        url = self.env.get("MODERATION_URL", "")
        if not url.startswith("https://"):
            return None
        import json
        import urllib.request
        req = urllib.request.Request(url, data=json.dumps({"input": text}).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": "YoDelivery/7",
                                              "Authorization": "Bearer " + self.env.get("MODERATION_KEY", "")})
        try:
            with urllib.request.urlopen(req, timeout=2) as r:     # nosec B310 - https checked above
                ans = json.loads(r.read(20000) or b"{}")
        except Exception:                                 # the service is down: the shop goes on
            return None
        res = ans.get("results", [ans])[0] if isinstance(ans, dict) else {}
        return bool(res.get("flagged")) if isinstance(res, dict) and "flagged" in res else None

    def chat_bad_probability(self, text):
        if self._chat_counts is None:
            self.train_chat_model()
        return p_bad(text, *self._chat_counts)

    def learn_chat_decision(self, conn, message_id, text, approve):
        """A moderator decided: the message is a new example (once per message)."""
        conn.execute("INSERT INTO chat_examples (text, label, source, message_id, created_at) VALUES (?, ?, 'moderator', ?, ?) "
                     "ON CONFLICT (message_id) DO UPDATE SET label = excluded.label",
                     (text[:1000], "ok" if approve else "bad", message_id, now()))
        self._chat_counts = None                        # trained again at the next message
        if hasattr(self, "cache_bump"):
            self.cache_bump("chatmodel", conn)

    def chat_model_report(self):
        if hasattr(self, "cached"):                      # the cross-validation is slow: kept 10 minutes
            return self.cached("chatmodel", "report", 600, self._chat_model_report)
        return self._chat_model_report()

    def _chat_model_report(self):
        ex = self.chat_examples()
        with self.db() as conn:
            learned = conn.execute("SELECT COUNT(*) FROM chat_examples WHERE source = 'moderator'").fetchone()[0]
        return {"examples": len(ex), "learned": learned, "bad": sum(1 for _, lab in ex if lab == "bad"),
                "check": evaluate(ex), "hold_p": HOLD_P}
