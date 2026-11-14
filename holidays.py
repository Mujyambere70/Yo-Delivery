"""
holidays.py - version 8 (mixin class used by Store): public holidays, the leave rules of a country, the year
statement of a worker and two more ways to prove a clock-in.

Public holidays
    One calendar per country (table public_holidays), filled at the first start with the dates of 2027 for Rwanda,
    Russia and India that were checked on the web on 10 November 2026 (see SOURCES). A manager adds the holidays of
    other years and countries on /admin/staff/holidays. Three kinds of day:
        public   a public holiday
        lieu     a day off given in place of a holiday that falls on a weekend
        workday  a Saturday or Sunday that is worked (Russia moves days off by government decree)
    The calendar is used when holiday days are counted, on the shift plan, in payroll (work on a public holiday) and
    in the hub forecast.
Leave rules (examples to check with a lawyer before real use)
    How many days of annual leave the law of a country gives at least, whether they are working or calendar days,
    and how work on a public holiday is paid. A contract with fewer days than the minimum is refused.
Year statement
    Every month of a year for one worker: gross pay, tax, social contributions and net pay, with the totals, the
    hours worked and the holiday days taken (/staff/statement/<year>).
Clock-in proofs
    Version 7 checked the position of the phone. A phone without GPS (or inside a building) can now prove the place
    with the office code - six digits that change every minute on the office screen (HMAC of the office and the
    minute, as in a one-time password) - or by being on the office network (a list of IP ranges of the office).
"""

import hashlib
import hmac
import ipaddress
import json
import time
from datetime import date, timedelta

from core import ShopError, now

HOLIDAY_SCHEMA = """
CREATE TABLE IF NOT EXISTS public_holidays (
    id INTEGER PRIMARY KEY AUTOINCREMENT, country TEXT NOT NULL, day TEXT NOT NULL, name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'public', by_moon INTEGER NOT NULL DEFAULT 0, added_by INTEGER,
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_holiday_day ON public_holidays(country, day);
CREATE TABLE IF NOT EXISTS leave_rules (
    country TEXT PRIMARY KEY, min_days INTEGER NOT NULL, unit TEXT NOT NULL DEFAULT 'working',
    holiday_rate REAL NOT NULL DEFAULT 1, note TEXT NOT NULL DEFAULT '', checked_by INTEGER,
    checked_at TEXT NOT NULL DEFAULT ''
);
"""

HOLIDAY_KINDS = {"public": "Public holiday", "lieu": "Day off in place of a holiday", "workday": "Weekend day that is worked"}
SOURCES = {
    "RW": "Official public holidays of Rwanda for 2027 (checked on 10 Nov 2026); Eid dates can move by a day (moon).",
    "RU": "Government decree No. 1187 of 17.09.2026 on moving days off in 2027; Labour Code art. 112.",
    "IN": "The three national holidays of India; every state adds its own holidays.",
}
# country, day, name, kind, by moon
SEED_HOLIDAYS = [
    ("RW", "2026-12-25", "Christmas Day", "public", 0), ("RW", "2026-12-26", "Boxing Day", "public", 0),
    ("RW", "2027-01-01", "New Year's Day", "public", 0), ("RW", "2027-01-02", "Day after New Year's Day", "public", 0),
    ("RW", "2027-02-01", "National Heroes' Day", "public", 0), ("RW", "2027-03-09", "Eid al-Fitr", "public", 1),
    ("RW", "2027-03-26", "Good Friday", "public", 0), ("RW", "2027-03-29", "Easter Monday", "public", 0),
    ("RW", "2027-04-07", "Genocide against the Tutsi Memorial Day", "public", 0),
    ("RW", "2027-05-01", "Labour Day", "public", 0), ("RW", "2027-05-16", "Eid al-Adha", "public", 1),
    ("RW", "2027-07-01", "Independence Day", "public", 0), ("RW", "2027-07-04", "Liberation Day", "public", 0),
    ("RW", "2027-07-05", "Liberation Day (day off)", "lieu", 0), ("RW", "2027-08-06", "Umuganura (harvest day)", "public", 0),
    ("RW", "2027-08-15", "Assumption Day", "public", 0), ("RW", "2027-08-16", "Assumption Day (day off)", "lieu", 0),
    ("RW", "2027-12-25", "Christmas Day", "public", 0), ("RW", "2027-12-26", "Boxing Day", "public", 0),
    ("RU", "2026-12-31", "Day off before the New Year", "lieu", 0),
    *[("RU", f"2027-01-0{d}", "New Year holidays" if d != 7 else "Orthodox Christmas", "public", 0) for d in range(1, 9)],
    ("RU", "2027-02-20", "Working Saturday (day off moved to 22 February)", "workday", 0),
    ("RU", "2027-02-22", "Day off (moved from 20 February)", "lieu", 0),
    ("RU", "2027-02-23", "Defender of the Fatherland Day", "public", 0),
    ("RU", "2027-03-08", "International Women's Day", "public", 0),
    ("RU", "2027-05-01", "Spring and Labour Day", "public", 0), ("RU", "2027-05-03", "Spring and Labour Day (day off)", "lieu", 0),
    ("RU", "2027-05-09", "Victory Day", "public", 0), ("RU", "2027-05-10", "Victory Day (day off)", "lieu", 0),
    ("RU", "2027-06-12", "Russia Day", "public", 0), ("RU", "2027-06-14", "Russia Day (day off)", "lieu", 0),
    ("RU", "2027-11-04", "Unity Day", "public", 0), ("RU", "2027-11-05", "Day off (moved from 2 January)", "lieu", 0),
    ("RU", "2027-12-31", "Day off (moved from 3 January)", "lieu", 0),
    ("IN", "2027-01-26", "Republic Day", "public", 0), ("IN", "2027-08-15", "Independence Day", "public", 0),
    ("IN", "2027-10-02", "Gandhi Jayanti", "public", 0),
]
# country, least days of annual leave, unit, pay rate for work on a public holiday, note
SEED_LEAVE_RULES = [
    ("RW", 18, "working", 1, "Law No. 66/2018 regulating labour in Rwanda: 18 working days a year, +1 day for every 3 years "
                             "of service up to 21. Pay for holiday work: not set here."),
    ("RU", 28, "calendar", 2, "Labour Code of the Russian Federation, art. 115 and 120: 28 calendar days, public holidays "
                              "inside the leave are not counted; art. 153: work on a holiday is paid at least double."),
    ("IN", 15, "working", 2, "Factories Act 1948, s. 79: 1 day for every 20 days worked (about 15 a year). Holiday pay is "
                             "set by each state; double wages in several states."),
]
CODE_STEP_S = 60
PROOFS = {"gps": "Position of the phone", "code": "Office code", "network": "Office network", "": "-"}


def _day(text):
    try:
        return date.fromisoformat(str(text)[:10])
    except ValueError:
        raise ShopError("Please check the date.")


class HolidaysMixin:

    # ------------------------------------------------------------------ calendar
    def ensure_holidays(self):
        with self.db() as conn:
            if conn.execute("SELECT COUNT(*) FROM public_holidays").fetchone()[0] == 0:
                for country, day, name, kind, moon in SEED_HOLIDAYS:
                    conn.execute("INSERT INTO public_holidays (country, day, name, kind, by_moon, created_at) "
                                 "VALUES (?, ?, ?, ?, ?, ?)", (country, day, name, kind, moon, now()))
            if conn.execute("SELECT COUNT(*) FROM leave_rules").fetchone()[0] == 0:
                for country, days, unit, rate, note in SEED_LEAVE_RULES:
                    conn.execute("INSERT INTO leave_rules (country, min_days, unit, holiday_rate, note) VALUES (?, ?, ?, ?, ?)",
                                 (country, days, unit, rate, note))

    def holidays(self, country="", year=None):
        sql, args = "SELECT * FROM public_holidays WHERE 1 = 1", []
        if country:
            sql += " AND country = ?"
            args.append(country.upper())
        if year:
            sql += " AND day >= ? AND day <= ?"
            args += [f"{int(year)}-01-01", f"{int(year)}-12-31"]
        with self.db() as conn:
            return [dict(r) for r in conn.execute(sql + " ORDER BY country, day", args)]

    def holiday_map(self, country, first, last, conn=None):
        """{day text: row} of one country between two dates."""
        def read(c):
            return {r["day"]: dict(r) for r in c.execute(
                "SELECT day, name, kind, by_moon FROM public_holidays WHERE country = ? AND day >= ? AND day <= ?",
                (str(country or "").upper(), first.isoformat(), last.isoformat()))}
        if conn is not None:
            return read(conn)
        with self.db() as c:
            return read(c)

    def holiday_countries(self):
        with self.db() as conn:
            return [r["country"] for r in conn.execute("SELECT DISTINCT country FROM public_holidays ORDER BY country")]

    def save_holiday(self, actor_id, country, day, name, kind="public", by_moon=False):
        """A manager adds a day for the country of his own office; the administrator for every country."""
        country = str(country or "").strip().upper()
        name = " ".join(str(name or "").split())[:80]
        d = _day(day)
        if len(country) != 2 or not country.isalpha():
            raise ShopError("Please write the country as 2 letters, for example RW.")
        if kind not in HOLIDAY_KINDS:
            raise ShopError("Please choose the kind of day.")
        if len(name) < 3:
            raise ShopError("Please write the name of the holiday.")
        if kind == "workday" and d.weekday() < 5:
            raise ShopError("Only a Saturday or a Sunday can be marked as a worked weekend day.")
        self._holiday_actor(actor_id, country)
        with self.db() as conn:
            if conn.execute("SELECT 1 FROM public_holidays WHERE country = ? AND day = ?", (country, d.isoformat())).fetchone():
                conn.execute("UPDATE public_holidays SET name = ?, kind = ?, by_moon = ?, added_by = ? WHERE country = ? "
                             "AND day = ?", (name, kind, 1 if by_moon else 0, actor_id, country, d.isoformat()))
            else:
                conn.execute("INSERT INTO public_holidays (country, day, name, kind, by_moon, added_by, created_at) "
                             "VALUES (?, ?, ?, ?, ?, ?, ?)", (country, d.isoformat(), name, kind, 1 if by_moon else 0,
                                                              actor_id, now()))
            self._staff_log(conn, actor_id, None, None, "Public holiday saved", f"{country} {d.isoformat()} {name}")

    def delete_holiday(self, actor_id, holiday_id):
        with self.db() as conn:
            r = conn.execute("SELECT country, day, name FROM public_holidays WHERE id = ?", (holiday_id,)).fetchone()
        if r is None:
            return
        self._holiday_actor(actor_id, r["country"])
        with self.db() as conn:
            conn.execute("DELETE FROM public_holidays WHERE id = ?", (holiday_id,))
            self._staff_log(conn, actor_id, None, None, "Public holiday removed", f"{r['country']} {r['day']} {r['name']}")

    def _holiday_actor(self, actor_id, country):
        u = self.get_user(actor_id) if actor_id else None
        if u and u["is_admin"]:
            return
        w = self.worker_of(actor_id)
        if not w or not self.can(actor_id, "staff.manage"):
            raise ShopError("Only managers can change the holiday calendar.")
        if (w["office_country"] or "").upper() != country:
            raise ShopError("You can change only the calendar of the country of your own office.")

    # ------------------------------------------------------------------ counting days
    def is_working_day(self, country, d, hol=None):
        hol = self.holiday_map(country, d, d) if hol is None else hol
        h = hol.get(d.isoformat())
        if h:
            return h["kind"] == "workday"
        return d.weekday() < 5

    def working_days(self, country, first, last):
        if last < first:
            return 0
        hol = self.holiday_map(country, first, last)
        return sum(1 for i in range((last - first).days + 1) if self.is_working_day(country, first + timedelta(days=i), hol))

    def leave_days_count(self, country, first, last):
        """Holiday days used by time off from first to last, by the rule of the country: working days (Monday to
        Friday without public holidays) or calendar days without public holidays."""
        if last < first:
            return 0
        rule = self.leave_rule(country)
        if rule and rule["unit"] == "calendar":
            hol = self.holiday_map(country, first, last)
            return sum(1 for i in range((last - first).days + 1)
                       if (hol.get((first + timedelta(days=i)).isoformat()) or {}).get("kind") != "public")
        return self.working_days(country, first, last)

    def country_of_staff(self, staff_id, conn=None):
        def read(c):
            r = c.execute("SELECT o.country FROM staff s JOIN offices o ON o.id = s.office_id WHERE s.id = ?",
                          (staff_id,)).fetchone()
            return (r["country"] or "").upper() if r else ""
        if conn is not None:
            return read(conn)
        with self.db() as c:
            return read(c)

    # ------------------------------------------------------------------ leave rules
    def leave_rules(self):
        with self.db() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT l.*, u.name AS checked_name FROM leave_rules l LEFT JOIN users u ON u.id = l.checked_by "
                "ORDER BY l.country")]

    def leave_rule(self, country, conn=None):
        def read(c):
            r = c.execute("SELECT * FROM leave_rules WHERE country = ?", (str(country or "").upper(),)).fetchone()
            return dict(r) if r else None
        if conn is not None:
            return read(conn)
        with self.db() as c:
            return read(c)

    def save_leave_rule(self, actor_id, country, min_days, unit, holiday_rate, note="", checked=False):
        u = self.get_user(actor_id) if actor_id else None
        if not (u and u["is_admin"]) and not self.can(actor_id, "payroll.manage"):
            raise ShopError("Only the administrator or a payroll manager can change the leave rules.")
        country = str(country or "").strip().upper()
        try:
            min_days, rate = int(min_days), round(float(holiday_rate), 2)
        except (TypeError, ValueError):
            raise ShopError("Please check the numbers.")
        if len(country) != 2 or not country.isalpha() or unit not in ("working", "calendar"):
            raise ShopError("Please check the country and the kind of days.")
        if not 0 <= min_days <= 60 or not 1 <= rate <= 4:
            raise ShopError("Days: 0 to 60. Pay rate for holiday work: 1 to 4.")
        note = " ".join(str(note or "").split())[:300]
        with self.db() as conn:
            row = (min_days, unit, rate, note, actor_id if checked else None, now() if checked else "", country)
            if conn.execute("SELECT 1 FROM leave_rules WHERE country = ?", (country,)).fetchone():
                conn.execute("UPDATE leave_rules SET min_days = ?, unit = ?, holiday_rate = ?, note = ?, checked_by = ?, "
                             "checked_at = ? WHERE country = ?", row)
            else:
                conn.execute("INSERT INTO leave_rules (min_days, unit, holiday_rate, note, checked_by, checked_at, country) "
                             "VALUES (?, ?, ?, ?, ?, ?, ?)", row)
            self._staff_log(conn, actor_id, None, None, "Leave rule saved", f"{country}: {min_days} {unit} days, x{rate:g}")

    def check_contract_leave(self, staff_id, leave_days, conn=None):
        """Raise when a contract gives fewer holiday days than the rule of the worker's country."""
        country = self.country_of_staff(staff_id, conn)
        rule = self.leave_rule(country, conn)
        if rule and float(leave_days) < rule["min_days"]:
            raise ShopError(f"The contract gives {float(leave_days):g} holiday days, but the rule for {country} is at "
                            f"least {rule['min_days']} {rule['unit']} days a year.")

    # ------------------------------------------------------------------ payroll: work on a public holiday
    def holiday_work_line(self, conn, s, c, first, last):
        """["Work on public holidays", "5 h 30 min x2", extra money] or None. Only the extra part is added: the
        normal pay of these hours is already in the salary or in the hours worked."""
        country = self.country_of_staff(s["id"], conn)
        rule = self.leave_rule(country, conn)
        if not rule or rule["holiday_rate"] <= 1:
            return None
        days = [d for d, h in self.holiday_map(country, first, last, conn).items() if h["kind"] == "public"]
        if not days:
            return None
        minutes = 0
        for r in conn.execute("SELECT start_at, minutes FROM staff_shifts WHERE staff_id = ? AND start_at >= ? AND "
                              "start_at < ? AND minutes > 0", (s["id"], first.isoformat(),
                                                               (last + timedelta(days=1)).isoformat())):
            if r["start_at"][:10] in days:
                minutes += r["minutes"]
        if not minutes:
            return None
        per_min = c["pay_amount"] / (c["hours_week"] * 52 / 12 * 60) if c["pay_kind"] == "month" else c["pay_amount"] / 60
        extra = round(minutes * per_min * (rule["holiday_rate"] - 1))
        return ["Work on public holidays", f"{minutes // 60} h {minutes % 60:02d} min x{rule['holiday_rate']:g}", extra]

    # ------------------------------------------------------------------ year statement
    def year_statement(self, user_id, year):
        """The paid payslips of one year for the worker himself."""
        w = self.worker_of(user_id)
        if not w:
            return None
        year = int(year)
        with self.db() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT p.gross, p.tax, p.social, p.net, p.worked_min, p.overtime_min, p.unpaid_days, r.month, r.country, "
                "r.currency, r.paid_at FROM payslips p JOIN payroll_runs r ON r.id = p.run_id WHERE p.staff_id = ? AND "
                "r.status = 'Paid' AND r.month >= ? AND r.month <= ? ORDER BY r.month", (w["id"], f"{year}-01", f"{year}-12"))]
            leave = conn.execute("SELECT from_day, to_day, kind FROM staff_leave WHERE staff_id = ? AND status = 'Approved' "
                                 "AND to_day >= ? AND from_day <= ?", (w["id"], f"{year}-01-01", f"{year}-12-31")).fetchall()
            contract = self.contract_of(w["id"], conn)
        country = (w["office_country"] or "").upper()
        taken = {}
        for r in leave:
            a, b = max(_day(r["from_day"]), date(year, 1, 1)), min(_day(r["to_day"]), date(year, 12, 31))
            kind = r["kind"] or "Annual"
            taken[kind] = taken.get(kind, 0) + (self.leave_days_count(country, a, b) if kind == "Annual"
                                                else max(0, (b - a).days + 1))
        total = {k: sum(r[k] for r in rows) for k in ("gross", "tax", "social", "net", "worked_min", "overtime_min")}
        return {"year": year, "worker": w, "months": rows, "total": total, "leave": taken,
                "currency": rows[0]["currency"] if rows else (contract or {}).get("currency", ""),
                "years": self.statement_years(w["id"])}

    def statement_years(self, staff_id):
        with self.db() as conn:
            return sorted({int(r["month"][:4]) for r in conn.execute(
                "SELECT r.month FROM payslips p JOIN payroll_runs r ON r.id = p.run_id WHERE p.staff_id = ? AND "
                "r.status = 'Paid'", (staff_id,))}, reverse=True)

    # ------------------------------------------------------------------ clock-in: office code and office network
    def office_code(self, office_id, at=None, step=0):
        """Six digits for this office and this minute (HMAC-SHA256, like a one-time password)."""
        counter = int((at if at is not None else time.time()) // CODE_STEP_S) + step
        mac = hmac.new((self._staff_key() + ":office-code").encode(), f"{int(office_id)}:{counter}".encode(),
                       hashlib.sha256).digest()
        return f"{int.from_bytes(mac[:4], 'big') % 1_000_000:06d}"

    def office_code_ok(self, office_id, code, at=None):
        """The code of this minute or of the minute before (the worker needs time to type it)."""
        code = "".join(ch for ch in str(code or "") if ch.isdigit())
        if len(code) != 6:
            return False
        return any(hmac.compare_digest(self.office_code(office_id, at, step), code) for step in (0, -1))

    def set_office_network(self, actor_id, office_id, ranges):
        """The IP ranges of the office network, e.g. "203.0.113.0/24, 2001:db8:12::/48" (empty = no network check)."""
        nets = []
        for part in str(ranges or "").replace(";", ",").split(","):
            part = part.strip()
            if not part:
                continue
            try:
                net = ipaddress.ip_network(part, strict=False)
            except ValueError:
                raise ShopError(f"'{part[:40]}' is not a network such as 203.0.113.0/24.")
            if net.num_addresses > 65536 and net.version == 4 or net.version == 6 and net.prefixlen < 32:
                raise ShopError("The network is too large: use /16 or smaller (IPv4), /32 or smaller (IPv6).")
            nets.append(str(net))
        if len(nets) > 8:
            raise ShopError("An office can have 8 networks at most.")
        with self.db() as conn:
            admin, actor, branch = self._actor(conn, actor_id, "offices.manage")
            if not admin and int(office_id) not in branch:
                raise ShopError("You can change only the offices under your own office.")
            conn.execute("UPDATE offices SET net_ranges = ? WHERE id = ?", (", ".join(nets), office_id))
            self._staff_log(conn, actor_id, None, office_id, "Office network changed", f"{len(nets)} ranges")

    @staticmethod
    def ip_in_ranges(ip, ranges):
        try:
            addr = ipaddress.ip_address(str(ip or "").strip())
        except ValueError:
            return False
        for part in str(ranges or "").split(","):
            try:
                if addr in ipaddress.ip_network(part.strip(), strict=False):
                    return True
            except ValueError:
                continue
        return False

    def clock_in_proved(self, user_id, lat=None, lng=None, accuracy=None, code="", ip=""):
        """Clock in with the best proof there is: the office code, the office network, else the position of the
        phone (version 7). Returns (flag, proof); flag is "", "remote" or "no location"."""
        w = self.worker_of(user_id)
        if not w or w["status"] != "Active":
            raise ShopError("Only active workers can clock in.")
        with self.db() as conn:
            o = conn.execute("SELECT id, net_ranges FROM offices WHERE id = ?", (w["office_id"],)).fetchone()
            today = date.today().isoformat()
            helper = conn.execute("SELECT o.id, o.net_ranges FROM staff_offices h JOIN offices o ON o.id = h.office_id "
                                  "WHERE h.staff_id = ? AND h.from_day <= ? AND h.to_day >= ?", (w["id"], today, today)).fetchall()
        places = [o, *helper]
        proof = ""
        if str(code or "").strip():
            if not any(self.office_code_ok(p["id"], code) for p in places):
                raise ShopError("This office code is not right or it is too old. Please read the code on the office screen again.")
            proof = "code"
        elif any(self.ip_in_ranges(ip, p["net_ranges"]) for p in places if p["net_ranges"]):
            proof = "network"
        if proof:
            self.clock_in(user_id)
            flag = ""
        else:
            flag = self.clock_in_at(user_id, lat, lng, accuracy)
            proof = "gps" if flag != "no location" else ""
        with self.db() as conn:
            conn.execute("UPDATE staff_shifts SET in_proof = ?" + (", in_flag = ''" if proof in ("code", "network") else "")
                         + " WHERE staff_id = ? AND end_at = ''", (proof, w["id"]))
        return flag, proof

    # ------------------------------------------------------------------ forecast: what a holiday does to a hub
    def holiday_factor(self, series, country):
        """series = [(day text, parcels)]. How busy public holidays were compared with the same weekday on normal
        days (1.0 = no difference). None when the history has fewer than 2 holidays."""
        if not series:
            return None
        first, last = _day(series[0][0]), _day(series[-1][0])
        hol = {d for d, h in self.holiday_map(country, first, last).items() if h["kind"] in ("public", "lieu")}
        by_wd, on_hol = {}, []
        for day, n in series:
            wd = _day(day).weekday()
            if day in hol:
                on_hol.append((wd, n))
            else:
                by_wd.setdefault(wd, []).append(n)
        ratios = [n / (sum(by_wd[wd]) / len(by_wd[wd])) for wd, n in on_hol if by_wd.get(wd) and sum(by_wd[wd]) > 0]
        if len(ratios) < 2:
            return None
        return round(max(0.0, min(2.0, sum(ratios) / len(ratios))), 2)


def as_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
