"""
world.py - what makes Yo Delivery an international shop:
interface languages, countries, phone numbers, postal codes, shipping zones and the
currencies prices can be shown in.

Prices are kept in Indian rupees (paise) - the shop's own currency. A customer can see them
in their own currency; the amount is converted with the day's exchange rate (payments.py).
Names of countries in every language come from the Unicode CLDR data (Babel library).
"""

import re
from contextvars import ContextVar
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from babel import Locale
from babel.dates import format_date, format_datetime
from babel.numbers import format_currency, get_territory_currencies

# ---------- interface languages: the six official UN languages + Kinyarwanda ----------
LANGUAGES = {
    "en": "English",
    "ru": "Русский",
    "fr": "Français",
    "es": "Español",
    "ar": "العربية",
    "zh": "中文",
    "rw": "Ikinyarwanda",
}
RTL_LANGUAGES = {"ar"}
BABEL_LOCALE = {"en": "en", "ru": "ru", "fr": "fr", "es": "es", "ar": "ar", "zh": "zh_Hans_CN", "rw": "rw"}

# ---------- currencies prices can be shown and paid in ----------
CURRENCIES = ["INR", "RUB", "USD", "EUR", "GBP", "CNY", "AED", "RWF"]
SHOP_CURRENCY = "INR"
ZERO_DECIMAL = {"RWF"}                       # no cents: amounts are whole francs (also for Stripe)
HOME_COUNTRY = "IN"                          # the warehouse is in Ambala, India


def default_currency(country, lang="en"):
    """Russian interface -> rubles. Otherwise the currency of the country if the shop has it, else US dollars."""
    if lang == "ru":
        return "RUB"
    try:
        local = get_territory_currencies(country)
    except Exception:
        local = []
    for code in local:
        if code in CURRENCIES:
            return code
    return "INR" if country == HOME_COUNTRY else "USD"


def currency_name(code, lang="en"):
    try:
        return Locale.parse(BABEL_LOCALE.get(lang, "en")).currencies.get(code, code)
    except Exception:
        return code


# ---------- countries ----------
DIAL_CODES = {'AF': '93', 'AL': '355', 'DZ': '213', 'AS': '1684', 'AD': '376', 'AO': '244', 'AI': '1264', 'AG': '1268', 'AR': '54', 'AM': '374', 'AW': '297', 'AC': '247', 'AU': '61', 'AT': '43', 'AZ': '994', 'BS': '1242', 'BH': '973', 'BD': '880', 'BB': '1246', 'BY': '375', 'BE': '32', 'BZ': '501', 'BJ': '229', 'BM': '1441', 'BT': '975', 'BO': '591', 'BA': '387', 'BW': '267', 'BR': '55', 'IO': '246', 'VG': '1284', 'BN': '673', 'BG': '359', 'BF': '226', 'BI': '257', 'KH': '855', 'CM': '237', 'CA': '1', 'CV': '238', 'BQ': '599', 'KY': '1345', 'CF': '236', 'TD': '235', 'CL': '56', 'CN': '86', 'CX': '61', 'CC': '61', 'CO': '57', 'KM': '269', 'CG': '242', 'CK': '682', 'CR': '506', 'HR': '385', 'CU': '53', 'CW': '599', 'CY': '357', 'CZ': '420', 'CI': '225', 'CD': '243', 'DK': '45', 'DJ': '253', 'DM': '1767', 'DO': '1809', 'EC': '593', 'EG': '20', 'SV': '503', 'GQ': '240', 'ER': '291', 'EE': '372', 'SZ': '268', 'ET': '251', 'FK': '500', 'FO': '298', 'FJ': '679', 'FI': '358', 'FR': '33', 'GF': '594', 'PF': '689', 'GA': '241', 'GM': '220', 'GE': '995', 'DE': '49', 'GH': '233', 'GI': '350', 'GR': '30', 'GL': '299', 'GD': '1473', 'GP': '590', 'GU': '1671', 'GT': '502', 'GG': '44', 'GN': '224', 'GW': '245', 'GY': '592', 'HT': '509', 'HN': '504', 'HK': '852', 'HU': '36', 'IS': '354', 'IN': '91', 'ID': '62', 'IR': '98', 'IQ': '964', 'IE': '353', 'IM': '44', 'IL': '972', 'IT': '39', 'JM': '1876', 'JP': '81', 'JE': '44', 'JO': '962', 'KZ': '7', 'KE': '254', 'KI': '686', 'XK': '383', 'KW': '965', 'KG': '996', 'LA': '856', 'LV': '371', 'LB': '961', 'LS': '266', 'LR': '231', 'LY': '218', 'LI': '423', 'LT': '370', 'LU': '352', 'MO': '853', 'MG': '261', 'MW': '265', 'MY': '60', 'MV': '960', 'ML': '223', 'MT': '356', 'MH': '692', 'MQ': '596', 'MR': '222', 'MU': '230', 'YT': '262', 'MX': '52', 'FM': '691', 'MD': '373', 'MC': '377', 'MN': '976', 'ME': '382', 'MS': '1664', 'MA': '212', 'MZ': '258', 'MM': '95', 'NA': '264', 'NR': '674', 'NP': '977', 'NL': '31', 'NC': '687', 'NZ': '64', 'NI': '505', 'NE': '227', 'NG': '234', 'NU': '683', 'NF': '672', 'KP': '850', 'MK': '389', 'MP': '1670', 'NO': '47', 'OM': '968', 'PK': '92', 'PW': '680', 'PS': '970', 'PA': '507', 'PG': '675', 'PY': '595', 'PE': '51', 'PH': '63', 'PN': '64', 'PL': '48', 'PT': '351', 'PR': '1787', 'QA': '974', 'RO': '40', 'RU': '7', 'RW': '250', 'RE': '262', 'WS': '685', 'SM': '378', 'SA': '966', 'SN': '221', 'RS': '381', 'SC': '248', 'SL': '232', 'SG': '65', 'SX': '1721', 'SK': '421', 'SI': '386', 'SB': '677', 'SO': '252', 'ZA': '27', 'KR': '82', 'SS': '211', 'ES': '34', 'LK': '94', 'BL': '590', 'SH': '290', 'KN': '1869', 'LC': '1758', 'MF': '590', 'PM': '508', 'VC': '1784', 'SD': '249', 'SR': '597', 'SJ': '47', 'SE': '46', 'CH': '41', 'SY': '963', 'ST': '239', 'TW': '886', 'TJ': '992', 'TZ': '255', 'TH': '66', 'TL': '670', 'TG': '228', 'TK': '690', 'TO': '676', 'TT': '1868', 'TA': '290', 'TN': '216', 'TM': '993', 'TC': '1649', 'TV': '688', 'TR': '90', 'VI': '1340', 'UG': '256', 'UA': '380', 'AE': '971', 'GB': '44', 'US': '1', 'UY': '598', 'UZ': '998', 'VU': '678', 'VA': '39', 'VE': '58', 'VN': '84', 'WF': '681', 'EH': '212', 'YE': '967', 'ZM': '260', 'ZW': '263', 'AX': '358'}


def _names(lang):
    """Country names in a language; Kinyarwanda has few in CLDR, so French names fill the gaps."""
    names = dict(Locale.parse("en").territories)
    if lang == "rw":
        names.update(Locale.parse("fr").territories)
    names.update(Locale.parse(BABEL_LOCALE.get(lang, "en")).territories)
    return names


def countries(lang="en"):
    """[(code, name)] of every country, sorted by the name in the chosen language."""
    names = _names(lang)
    return sorted(((c, names.get(c, c)) for c in DIAL_CODES), key=lambda r: r[1])


def country_name(code, lang="en"):
    return _names(lang).get(code, code)


# ---------- shipping zones (days from the Ambala warehouse; the shop's own service promise) ----------
SOUTH_ASIA = {"IN"}
NEAR = {"NP", "BT", "BD", "LK", "MV", "AE", "OM", "QA", "BH", "KW", "SA", "SG", "MY", "TH"}


def shipping_zone(country):
    if country in SOUTH_ASIA:
        return "domestic"
    return "near" if country in NEAR else "world"


def delivery_days(country, postcode=""):
    """Rough delivery time. India: by the first digits of the PIN code; abroad: by zone."""
    if country == "IN":
        if postcode.startswith("13"):
            return 1          # Ambala / nearby
        if postcode[:2] in ("11", "12", "14", "15", "16", "17"):
            return 2          # Delhi, Haryana, Punjab, Himachal
        return 4
    return 6 if shipping_zone(country) == "near" else 9


# ---------- phone numbers and postal codes ----------
POSTCODE_RULES = {
    "IN": (r"[1-9]\d{5}", "6 digits"),
    "RU": (r"\d{6}", "6 digits"),
    "US": (r"\d{5}(-\d{4})?", "5 digits"),
    "CN": (r"\d{6}", "6 digits"),
    "FR": (r"\d{5}", "5 digits"),
    "DE": (r"\d{5}", "5 digits"),
    "ES": (r"\d{5}", "5 digits"),
}


# Countries where post codes are not used for addresses - the field may stay empty there.
NO_POSTCODE = {"AE", "RW", "BI", "UG", "TZ", "KE", "GH", "QA", "HK", "IE", "AO", "CD", "ZW", "MW"}


def clean_postcode(country, value):
    """Return the post code as it is stored ("" where countries do not use them), or None if it is not valid."""
    value = " ".join(str(value or "").upper().split())
    if country in NO_POSTCODE and not value:
        return ""
    rule = POSTCODE_RULES.get(country)
    if rule:
        return value if re.fullmatch(rule[0], value) else None
    return value if re.fullmatch(r"[A-Z0-9][A-Z0-9 -]{1,9}", value) else None


def clean_mobile(country, value):
    """Return the number as it is stored, or None if it is not valid.
    India keeps the 10-digit form (as before); every other country is stored as +<code><number>."""
    digits = re.sub(r"[\s().-]", "", str(value or ""))
    dial = DIAL_CODES.get(country, "")
    if digits.startswith("+"):
        digits = digits[1:]
        if dial and digits.startswith(dial):
            digits = digits[len(dial):]
        elif dial:
            return None
    if not digits.isdigit():
        return None
    if country == "IN":
        digits = digits[-10:] if len(digits) == 12 and digits.startswith("91") else digits
        return digits if re.fullmatch(r"[6-9]\d{9}", digits) else None
    digits = digits.lstrip("0") if country not in ("IT",) else digits
    if country == "RU" and not re.fullmatch(r"9\d{9}", digits):
        return None
    if not 6 <= len(digits) <= 14 or len(dial + digits) > 15:
        return None
    return f"+{dial}{digits}"



# ---------- language and currency of the current page ----------
_lang = ContextVar("lang", default="en")
_currency = ContextVar("currency", default=SHOP_CURRENCY)
_rates = ContextVar("rates", default={})


def set_context(lang=None, currency=None, rates=None):
    if lang:
        _lang.set(lang if lang in LANGUAGES else "en")
    if currency:
        _currency.set(currency if currency in CURRENCIES else SHOP_CURRENCY)
    if rates is not None:
        _rates.set(rates)


def current_lang():
    return _lang.get()


def current_rates():
    return _rates.get() or {}


def current_currency():
    return _currency.get()


def babel_locale(lang=None):
    lang = lang or _lang.get()
    return "en_IN" if lang == "en" else BABEL_LOCALE.get(lang, "en")


def convert(paise, currency, rates):
    """Shop amount in paise -> amount in the smallest unit of `currency` (or None without a rate).
    rates = {currency: units per 1 US dollar}."""
    if currency == SHOP_CURRENCY:
        return int(paise)
    if currency not in rates or SHOP_CURRENCY not in rates:
        return None
    value = Decimal(int(paise)) * Decimal(str(rates[currency])) / Decimal(str(rates[SHOP_CURRENCY]))
    if currency in ZERO_DECIMAL:                       # whole francs, kept x100 like every other amount
        return int((value / 100).to_integral_value(ROUND_HALF_UP)) * 100
    return int(value.to_integral_value(ROUND_HALF_UP))


def format_money(amount, currency=None, lang=None, whole=None):
    """Amount in the smallest unit -> text in the style of the language.
    1499900 INR (English) -> '₹14,999'; 1234567 RUB (Russian) -> '12 345,67 ₽'."""
    currency = currency or SHOP_CURRENCY
    value = Decimal(int(amount)) / 100
    if whole is None:
        whole = value == value.to_integral_value() or currency in ZERO_DECIMAL
    try:
        return format_currency(value, currency, locale=babel_locale(lang),
                               format=None, currency_digits=not whole, decimal_quantization=not whole) \
            if not whole else format_currency(value.to_integral_value(ROUND_HALF_UP), currency,
                                              locale=babel_locale(lang), currency_digits=False,
                                              format=_whole_pattern(lang))
    except Exception:
        return f"{value:,.2f} {currency}"


def _whole_pattern(lang):
    """The language's currency pattern without decimals (₹1,499 instead of ₹1,499.00)."""
    pattern = Locale.parse(babel_locale(lang)).currency_formats["standard"].pattern
    pattern = pattern.split(";")[0]
    return re.sub(r"\.0+", "", pattern)


def price(paise):
    """A shop price shown in the visitor's currency (falls back to rupees when no rate is known)."""
    cur = _currency.get()
    local = convert(paise, cur, _rates.get())
    if local is None:
        return format_money(paise, SHOP_CURRENCY)
    return format_money(local, cur)


def format_when(value, with_time=True, lang=None):
    """'2026-10-31 01:48:00' -> '31 Oct 2026, 01:48 AM' (English) or '31 окт. 2026 г., 01:48' (Russian)."""
    lang = lang or _lang.get()
    d = value if isinstance(value, datetime) else datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S")
    if lang == "en":
        return d.strftime("%d %b %Y, %I:%M %p" if with_time else "%d %b %Y")
    loc = babel_locale(lang)
    return format_datetime(d, "d MMM y, HH:mm", locale=loc) if with_time else format_date(d, "medium", locale=loc)
