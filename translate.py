"""
translate.py - version 6: drafts of product texts in other languages, for the seller to check.

Two ways, the first that works is used:
    1. a translation service, when the server has one: TRANSLATE_URL (any LibreTranslate-compatible server, with
       TRANSLATE_API_KEY if it needs one) or DEEPL_API_KEY (DeepL API). The text goes to that service, so a shop must
       tell its sellers which one it uses.
    2. without a service (like this demo): the shop's own translation memory - lines that sellers already
       translated themselves (product_texts, line by line), the translated words of the shop's pages, and a small
       glossary of shop words. Lines and words that the memory does not know stay in English in [brackets].
A draft is never saved by itself: the seller reads it, corrects it and presses Save.
"""

import json
import re
import urllib.parse
import urllib.request

import i18n

TIMEOUT = 8

# a small glossary of shop words (checked by me; the translations of the shop's pages are used as well)
GLOSSARY = {
    "ru": {"stainless steel": "нержавеющая сталь", "warranty": "гарантия", "years": "лет", "year": "год",
           "minutes": "минут", "litre": "л", "litres": "л", "liter": "л", "black": "чёрный", "white": "белый",
           "blue": "синий", "red": "красный", "green": "зелёный", "cotton": "хлопок", "size": "размер",
           "weight": "вес", "battery": "аккумулятор", "hours": "часов", "waterproof": "водонепроницаемый",
           "automatic": "автоматический", "safety": "безопасность", "kettle": "чайник", "cooker": "плита",
           "shoes": "обувь", "shirt": "рубашка", "phone": "телефон", "charger": "зарядное устройство",
           "free": "бесплатно", "new": "новый", "and": "и", "with": "с", "for": "для", "in": "в"},
    "fr": {"stainless steel": "acier inoxydable", "warranty": "garantie", "years": "ans", "year": "an",
           "minutes": "minutes", "black": "noir", "white": "blanc", "blue": "bleu", "red": "rouge", "green": "vert",
           "cotton": "coton", "size": "taille", "weight": "poids", "battery": "batterie", "hours": "heures",
           "waterproof": "étanche", "automatic": "automatique", "safety": "sécurité", "kettle": "bouilloire",
           "shoes": "chaussures", "shirt": "chemise", "phone": "téléphone", "charger": "chargeur", "free": "gratuit",
           "new": "nouveau", "and": "et", "with": "avec", "for": "pour", "in": "en"},
    "es": {"stainless steel": "acero inoxidable", "warranty": "garantía", "years": "años", "year": "año",
           "minutes": "minutos", "black": "negro", "white": "blanco", "blue": "azul", "red": "rojo", "green": "verde",
           "cotton": "algodón", "size": "talla", "weight": "peso", "battery": "batería", "hours": "horas",
           "waterproof": "impermeable", "automatic": "automático", "safety": "seguridad", "kettle": "hervidor",
           "shoes": "zapatos", "shirt": "camisa", "phone": "teléfono", "charger": "cargador", "free": "gratis",
           "new": "nuevo", "and": "y", "with": "con", "for": "para", "in": "en"},
    "ar": {"stainless steel": "فولاذ مقاوم للصدأ", "warranty": "ضمان", "years": "سنوات", "year": "سنة",
           "minutes": "دقائق", "black": "أسود", "white": "أبيض", "blue": "أزرق", "red": "أحمر", "green": "أخضر",
           "cotton": "قطن", "size": "مقاس", "weight": "وزن", "battery": "بطارية", "hours": "ساعات",
           "waterproof": "مقاوم للماء", "automatic": "تلقائي", "safety": "أمان", "kettle": "غلاية", "shoes": "أحذية",
           "shirt": "قميص", "phone": "هاتف", "charger": "شاحن", "free": "مجاني", "new": "جديد", "and": "و",
           "with": "مع", "for": "لـ"},
    "zh": {"stainless steel": "不锈钢", "warranty": "保修", "years": "年", "year": "年", "minutes": "分钟",
           "black": "黑色", "white": "白色", "blue": "蓝色", "red": "红色", "green": "绿色", "cotton": "棉",
           "size": "尺寸", "weight": "重量", "battery": "电池", "hours": "小时", "waterproof": "防水",
           "automatic": "自动", "safety": "安全", "kettle": "水壶", "shoes": "鞋", "shirt": "衬衫", "phone": "手机",
           "charger": "充电器", "free": "免费", "new": "新", "and": "和", "with": "带", "for": "适用于"},
    "rw": {"warranty": "garanti", "years": "imyaka", "year": "umwaka", "minutes": "iminota", "black": "umukara",
           "white": "umweru", "blue": "ubururu", "red": "umutuku", "green": "icyatsi", "size": "ingano",
           "weight": "uburemere", "battery": "batiri", "hours": "amasaha", "automatic": "byikora",
           "safety": "umutekano", "shoes": "inkweto", "shirt": "ishati", "phone": "telefoni", "free": "ubuntu",
           "new": "gishya", "and": "na", "with": "na", "for": "kuri"},
}


def _service(env, text, target):
    """A real translation service, when the shop has one (None otherwise or when it fails)."""
    if not text.strip():
        return ""
    try:
        if env.get("DEEPL_API_KEY"):
            data = urllib.parse.urlencode({"text": text, "target_lang": target.upper()[:2], "source_lang": "EN"}).encode()
            host = "api-free.deepl.com" if env["DEEPL_API_KEY"].endswith(":fx") else "api.deepl.com"
            req = urllib.request.Request(f"https://{host}/v2/translate", data=data,
                                         headers={"Authorization": "DeepL-Auth-Key " + env["DEEPL_API_KEY"]})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:  # nosec B310 - a fixed https address
                return json.loads(r.read())["translations"][0]["text"]
        if env.get("TRANSLATE_URL", "").startswith(("https://", "http://")):    # only web addresses (no file:)
            body = {"q": text, "source": "en", "target": target, "format": "text"}
            if env.get("TRANSLATE_API_KEY"):
                body["api_key"] = env["TRANSLATE_API_KEY"]
            req = urllib.request.Request(env["TRANSLATE_URL"].rstrip("/") + "/translate", data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:  # nosec B310 - http(s) checked above
                return json.loads(r.read())["translatedText"]
    except Exception:                                  # no service, no network, a wrong key: use the memory
        return None
    return None


def _words(line, lang, catalog):
    """Word by word with the glossary (longest phrases first) and the shop's own page translations."""
    out, known, total = line, 0, 0
    phrases = dict(GLOSSARY.get(lang, {}))
    for en, tr in sorted(phrases.items(), key=lambda kv: -len(kv[0])):
        pat = re.compile(rf"\b{re.escape(en)}\b", re.I)
        if pat.search(out):
            out = pat.sub("\x00" + tr + "\x01", out)
    parts = re.split(r"(\x00[^\x01]*\x01|\d+(?:[.,]\d+)?\s?(?:%|kg|g|cm|mm|l|ml|w|v|mah)?\b|[^\w\x00\x01]+)", out, flags=re.I)
    res = []
    for p in parts:
        if not p:
            continue
        if p.startswith("\x00"):
            res.append(p[1:-1])
            known += 1
            total += 1
        elif re.fullmatch(r"[A-Z][a-z]+[A-Z]\w*|[A-Z]{2,}\w*", p):     # brand names (HomeEase) and codes (USB) stay
            res.append(p)
        elif re.fullmatch(r"[A-Za-z][A-Za-z'-]*", p):
            total += 1
            tr = catalog.get(p) or catalog.get(p.capitalize())
            if tr:
                res.append(tr)
                known += 1
            else:
                res.append(f"[{p}]")
        else:
            res.append(p)
    return "".join(res), known, total


def draft(text, lang, memory, env=None):
    """{"text", "from": "service" | "memory", "known": share 0..100} for one text (several lines)."""
    env = env or {}
    got = _service(env, text, lang)
    if got:
        return {"text": got, "from": "service", "known": 100}
    catalog = {k: v for k, v in i18n.catalog(lang)[0].items() if " " not in k}
    lines, known, total = [], 0, 0
    for line in str(text or "").split("\n"):
        key = " ".join(line.split()).lower()
        if key in memory:
            lines.append(memory[key])
            known += 1
            total += 1
            continue
        out, k, t = _words(line, lang, catalog)
        lines.append(out)
        known += k
        total += max(t, 0)
    return {"text": "\n".join(lines), "from": "memory", "known": round(100 * known / total) if total else 100}


class TranslateMixin:
    def translation_memory(self, lang):
        """{english line (lower case): the seller's translation} from every product text of the shop."""
        mem = {}
        with self.db() as conn:
            rows = conn.execute("SELECT p.name AS en_name, p.about AS en_about, t.name, t.about FROM product_texts t "
                                "JOIN products p ON p.id = t.product_id WHERE t.lang = ?", (lang,)).fetchall()
        for r in rows:
            mem[" ".join(r["en_name"].split()).lower()] = r["name"]
            en_lines, tr_lines = str(r["en_about"] or "").split("\n"), str(r["about"] or "").split("\n")
            if len(en_lines) == len(tr_lines):
                for a, b in zip(en_lines, tr_lines):
                    if a.strip() and b.strip():
                        mem[" ".join(a.split()).lower()] = b.strip()
        return mem

    def translation_drafts(self, product, langs):
        """{lang: {"name", "about", "from", "known"}} for the languages asked."""
        out = {}
        about = product.about if isinstance(product.about, str) else "\n".join(product.about or [])
        for lang in langs:
            mem = self.translation_memory(lang)
            n = draft(product.name, lang, mem, self.env)
            a = draft(about, lang, mem, self.env)
            out[lang] = {"name": n["text"], "about": a["text"], "from": a["from"] if a["text"] else n["from"],
                         "known": min(n["known"], a["known"]) if about else n["known"]}
        return out
