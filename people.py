"""
people.py - version 7 (mixin class used by Store): what a company needs for its people after version 6.

    contracts       one active contract per worker: kind, first and last day, hours a week, pay (a month or an hour),
                    the currency of the office's country and the days of paid holiday in a year
    holiday balance holiday days are earned month by month (days a year x full months / 12); approved "Annual" time off
                    uses them; "Sick" and "Unpaid" time off do not; unpaid days are taken off the pay
    payroll         a payroll run per country and month makes one payslip per worker: pay of the days of the contract,
                    overtime (1.5 x the hour pay above the planned hours), unpaid days, income tax and social contributions
                    by the EXAMPLE rule of the country (income_tax_rules), net pay. Two different people: one makes the run,
                    another approves it (dual control); then it is paid (test mode: no money moves) and the workers get
                    their payslips
    training        courses with certificates that expire; when the setting "require_training" is on, an ability that needs a
                    course works only while the worker's certificate is valid
    helper offices  a worker can help in another office for some days; that office's manager plans his shifts there
    clock-in place  offices know where they are; a clock-in sends the phone's position and must be near the office
                    (unless the office allows remote work); without a position the clock-in is marked "no location"
    approvals       dual control: payroll runs, and the ability "all" when the setting "dual_control" is on

Amounts are whole units x 100 (kopecks, cents, paise; for Rwanda francs too, to keep one rule).
The tax rules are examples, checked on 2026-11-07, for a person with the ability "tax.manage" to check again:
    RU  NDFL from 2025: 13 % up to 2.4 million roubles a year, 15 % to 5 million, 18 % to 20 million, 20 % to 50 million,
        22 % above; the employer counts it from the start of the year (cumulative); no social contribution of the worker
    RW  PAYE a month: 0 % up to 60,000 RWF, 10 % to 100,000, 20 % to 200,000, 30 % above; RSSB pension 6 % + maternity 0.3 %
    IN  new regime FY 2025-26: standard deduction 75,000; 0 % up to 4 lakh, 5 % to 8, 10 % to 12, 15 % to 16, 20 % to 20,
        25 % to 24, 30 % above; no tax when the taxable income is at most 12 lakh (rebate); 4 % cess; withheld as 1/12
        of the yearly tax (simplified: no marginal relief, no other deductions)
"""

import calendar
import json
from datetime import date, datetime, timedelta

import geo
from core import ShopError, now
from staff import ALL, split

PEOPLE_SCHEMA = """
CREATE TABLE IF NOT EXISTS staff_contracts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, staff_id INTEGER NOT NULL REFERENCES staff(id), kind TEXT NOT NULL,
    start_day TEXT NOT NULL, end_day TEXT NOT NULL DEFAULT '', hours_week INTEGER NOT NULL DEFAULT 40,
    pay_kind TEXT NOT NULL DEFAULT 'month', pay_amount INTEGER NOT NULL, currency TEXT NOT NULL,
    leave_days INTEGER NOT NULL DEFAULT 20, status TEXT NOT NULL DEFAULT 'Active', created_by INTEGER,
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_contract ON staff_contracts(staff_id) WHERE status = 'Active';
CREATE TABLE IF NOT EXISTS income_tax_rules (
    country TEXT PRIMARY KEY, name TEXT NOT NULL, method TEXT NOT NULL, brackets TEXT NOT NULL,
    social_pct REAL NOT NULL DEFAULT 0, deduction INTEGER NOT NULL DEFAULT 0, rebate_upto INTEGER NOT NULL DEFAULT 0,
    cess_pct REAL NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '', updated_by INTEGER, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS payroll_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, country TEXT NOT NULL, month TEXT NOT NULL, currency TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Draft', workers INTEGER NOT NULL DEFAULT 0, total_gross INTEGER NOT NULL DEFAULT 0,
    total_net INTEGER NOT NULL DEFAULT 0, created_by INTEGER NOT NULL, created_at TEXT NOT NULL,
    approved_by INTEGER, approved_at TEXT NOT NULL DEFAULT '', paid_at TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_payroll_month ON payroll_runs(country, month) WHERE status != 'Cancelled';
CREATE TABLE IF NOT EXISTS payslips (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL REFERENCES payroll_runs(id),
    staff_id INTEGER NOT NULL REFERENCES staff(id), contract_id INTEGER, currency TEXT NOT NULL,
    gross INTEGER NOT NULL, tax INTEGER NOT NULL, social INTEGER NOT NULL, net INTEGER NOT NULL,
    worked_min INTEGER NOT NULL DEFAULT 0, planned_min INTEGER NOT NULL DEFAULT 0, overtime_min INTEGER NOT NULL DEFAULT 0,
    unpaid_days INTEGER NOT NULL DEFAULT 0, lines TEXT NOT NULL DEFAULT '[]', UNIQUE (run_id, staff_id)
);
CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, target_id INTEGER NOT NULL, payload TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '', requested_by INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'Waiting',
    decided_by INTEGER, decided_at TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_approval_waiting ON approvals(kind, target_id) WHERE status = 'Waiting';
CREATE TABLE IF NOT EXISTS staff_trainings (
    id INTEGER PRIMARY KEY AUTOINCREMENT, staff_id INTEGER NOT NULL REFERENCES staff(id), course TEXT NOT NULL,
    done_on TEXT NOT NULL, expires_on TEXT NOT NULL, certificate TEXT NOT NULL DEFAULT '', added_by INTEGER,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_training_staff ON staff_trainings(staff_id);
CREATE TABLE IF NOT EXISTS staff_offices (
    id INTEGER PRIMARY KEY AUTOINCREMENT, staff_id INTEGER NOT NULL REFERENCES staff(id),
    office_id INTEGER NOT NULL REFERENCES offices(id), from_day TEXT NOT NULL, to_day TEXT NOT NULL,
    added_by INTEGER, created_at TEXT NOT NULL, UNIQUE (staff_id, office_id, from_day)
);
"""

CONTRACT_KINDS = {"permanent": "Permanent", "fixed": "Fixed term", "part": "Part time", "intern": "Internship"}
LEAVE_KINDS = ("Annual", "Sick", "Unpaid")
CURRENCY_OF = {"IN": "INR", "RW": "RWF", "RU": "RUB", "KE": "KES", "UG": "UGX", "TZ": "TZS", "KZ": "KZT", "AE": "AED",
               "GB": "GBP", "FR": "EUR", "DE": "EUR", "ZA": "ZAR", "NG": "NGN", "CN": "CNY", "US": "USD"}
OVERTIME_RATE = 1.5                     # example rule: an overtime hour is paid 1.5 x the hour pay
GEOFENCE_M = 300

# course -> (name, abilities that need it, months the certificate is valid)
COURSES = {
    "hub_safety": ("Safety at the hub and the warehouse", ["hub.scan"], 24),
    "dangerous_goods": ("Dangerous goods in parcels", ["customs.manage"], 24),
    "data_protection": ("Data protection and privacy", ["photos.review", "customers.manage", "database.view",
                                                         "privacy.manage", "support.manage"], 12),
    "money_fraud": ("Money handling and fraud", ["payroll.manage", "payments.manage", "sellers.manage",
                                                 "risk.review"], 24),
    "road_safety": ("Road safety for couriers", [], 24),
    "first_aid": ("First aid", [], 36),
}
COURSE_FOR = {a: c for c, (_, abilities, _m) in COURSES.items() for a in abilities}

# country, name, method, brackets [(upper limit in whole units or None, %)], social %, deduction, rebate up to, cess %, note
EXAMPLE_INCOME_TAX = [
    ("RU", "НДФЛ", "year_cumulative", [(2_400_000, 13), (5_000_000, 15), (20_000_000, 18), (50_000_000, 20), (None, 22)],
     0, 0, 0, 0, "Tax Code of the Russian Federation, art. 224 (progressive scale from 2025); counted from 1 January"),
    ("RW", "PAYE", "month", [(60_000, 0), (100_000, 10), (200_000, 20), (None, 30)],
     6.3, 0, 0, 0, "Rwanda Revenue Authority PAYE bands; RSSB pension 6 % + maternity 0.3 % of the worker"),
    ("IN", "Income tax (TDS)", "year_projected",
     [(400_000, 0), (800_000, 5), (1_200_000, 10), (1_600_000, 15), (2_000_000, 20), (2_400_000, 25), (None, 30)],
     0, 75_000, 1_200_000, 4, "New regime FY 2025-26: standard deduction, rebate u/s 87A, 4 % cess (no marginal relief)"),
]


def _d(text):
    try:
        return datetime.strptime(str(text), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise ShopError("Please choose a day.")


def _month(text):
    try:
        return datetime.strptime(str(text), "%Y-%m").date()
    except (TypeError, ValueError):
        raise ShopError("Please choose a month like 2026-10.")


def _units(text, label="the pay"):
    try:
        v = round(float(str(text).replace(",", "").replace(" ", "")) * 100)
    except (TypeError, ValueError):
        raise ShopError(f"Please write {label} as a number.")
    if v <= 0:
        raise ShopError(f"Please write {label} as a number.")
    return int(v)


def bracket_tax(income, brackets):
    """Tax of an income (whole units) with progressive brackets [(upper limit or None, %), ...]."""
    tax, low = 0.0, 0
    for upto, pct in brackets:
        top = income if upto is None else min(income, upto)
        if top > low:
            tax += (top - low) * pct / 100
        if upto is None or income <= upto:
            break
        low = upto
    return tax


def add_months(d, n):
    m = d.month - 1 + n
    y = d.year + m // 12
    m = m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def km_between(a, b):
    return geo.haversine_km(a, b)


class PeopleMixin:

    # ================================================================ tax rules
    def ensure_income_tax_rules(self):
        with self.db() as conn:
            for c, name, method, br, social, ded, rebate, cess, note in EXAMPLE_INCOME_TAX:
                conn.execute("INSERT INTO income_tax_rules (country, name, method, brackets, social_pct, deduction, "
                             "rebate_upto, cess_pct, note, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                             "ON CONFLICT (country) DO NOTHING",
                             (c, name, method, json.dumps(br), social, ded, rebate, cess, note, now()))

    def income_tax_rules(self):
        with self.db() as conn:
            rows = [dict(r) for r in conn.execute("SELECT * FROM income_tax_rules ORDER BY country")]
        for r in rows:
            r["bracket_list"] = json.loads(r["brackets"])
        return rows

    def income_tax_rule(self, country, conn=None):
        def read(c):
            r = c.execute("SELECT * FROM income_tax_rules WHERE country = ?", (country,)).fetchone()
            if r is None:
                return None
            r = dict(r)
            r["bracket_list"] = json.loads(r["brackets"])
            return r
        if conn is not None:
            return read(conn)
        with self.db() as c:
            return read(c)

    @staticmethod
    def month_tax(rule, gross, ytd_gross=0, ytd_tax=0):
        """(income tax, social contribution) of one month's gross pay (x 100) by the rule of the country."""
        if rule is None:
            return 0, 0
        br = [(u, p) for u, p in rule["bracket_list"]]
        g = gross / 100
        if rule["method"] == "month":
            tax = bracket_tax(g, br)
        elif rule["method"] == "year_cumulative":            # Russia: tax of the year so far minus tax already taken
            tax = bracket_tax((ytd_gross + gross) / 100, br) - ytd_tax / 100
        else:                                                # India: one twelfth of the projected yearly tax
            taxable = max(0.0, g * 12 - rule["deduction"])
            yearly = 0.0 if taxable <= rule["rebate_upto"] else bracket_tax(taxable, br)
            tax = yearly * (1 + rule["cess_pct"] / 100) / 12
        social = g * rule["social_pct"] / 100
        return max(0, round(tax * 100)), max(0, round(social * 100))

    # ================================================================ contracts
    def save_contract(self, actor_id, staff_id, data):
        kind = data.get("kind", "")
        if kind not in CONTRACT_KINDS:
            raise ShopError("Please choose the kind of contract.")
        start = _d(data.get("start_day"))
        end = _d(data["end_day"]) if data.get("end_day") else None
        if kind in ("fixed", "intern") and end is None:
            raise ShopError("A fixed-term contract needs its last day.")
        if end and end < start:
            raise ShopError("The last day is before the first day.")
        pay_kind = data.get("pay_kind", "month")
        if pay_kind not in ("month", "hour"):
            raise ShopError("Please choose a pay a month or an hour.")
        amount = _units(data.get("pay_amount"))
        try:
            hours = int(data.get("hours_week") or 40)
            leave = int(data.get("leave_days") if data.get("leave_days") not in (None, "") else 20)
        except ValueError:
            raise ShopError("Please write the hours and the holiday days as numbers.")
        if not 1 <= hours <= 60 or not 0 <= leave <= 60:
            raise ShopError("Hours a week: 1 to 60; holiday days a year: 0 to 60.")
        with self.db() as conn:
            t = self._target(conn, actor_id, staff_id, "staff.manage")
            currency = CURRENCY_OF.get(t["office_country"], "INR")
            conn.execute("UPDATE staff_contracts SET status = 'Ended' WHERE staff_id = ? AND status = 'Active'", (staff_id,))
            cur = conn.execute("INSERT INTO staff_contracts (staff_id, kind, start_day, end_day, hours_week, pay_kind, "
                               "pay_amount, currency, leave_days, created_by, created_at) VALUES "
                               "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                               (staff_id, kind, start.isoformat(), end.isoformat() if end else "", hours, pay_kind,
                                amount, currency, leave, actor_id, now()))
            self._staff_log(conn, actor_id, staff_id, t["office_id"], "Contract saved",
                            f"{CONTRACT_KINDS[kind]} from {start}{f' to {end}' if end else ''}")    # no pay in the log
            self.notify(conn, t["user_id"], "Your contract was saved", "Open 'My work' to see it.", url="/staff/me")
            return cur.lastrowid

    def contract_of(self, staff_id, conn=None):
        def read(c):
            r = c.execute("SELECT * FROM staff_contracts WHERE staff_id = ? AND status = 'Active'", (staff_id,)).fetchone()
            return dict(r) if r else None
        if conn is not None:
            return read(conn)
        with self.db() as c:
            return read(c)

    def contracts_ending(self, user_id, within_days=45):
        """Active contracts of my branch that end soon (for the manager)."""
        until = (date.today() + timedelta(days=within_days)).isoformat()
        ids = {w["id"]: w for w in self.workers(user_id)}
        with self.db() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM staff_contracts WHERE status = 'Active' AND end_day != '' AND end_day <= ? ORDER BY end_day",
                (until,))]
        return [{**r, "worker": ids[r["staff_id"]]} for r in rows if r["staff_id"] in ids]

    # ================================================================ holiday balance
    def leave_balance(self, staff_id, year=None, today=None):
        today = today or date.today()
        year = year or today.year
        with self.db() as conn:
            c = self.contract_of(staff_id, conn)
            used_rows = conn.execute("SELECT from_day, to_day FROM staff_leave WHERE staff_id = ? AND status IN "
                                     "('Approved', 'Waiting') AND kind = 'Annual' AND to_day >= ? AND from_day <= ?",
                                     (staff_id, f"{year}-01-01", f"{year}-12-31")).fetchall()
        if c is None:
            return None
        start = max(_d(c["start_day"]), date(year, 1, 1))
        end_of_counting = min(today, date(year, 12, 31))
        months = 0
        if end_of_counting >= start:
            months = (end_of_counting.year - start.year) * 12 + end_of_counting.month - start.month + 1
        earned = round(c["leave_days"] * min(months, 12) / 12 * 2) / 2            # to half days
        used = 0
        for r in used_rows:
            a, b = max(_d(r["from_day"]), date(year, 1, 1)), min(_d(r["to_day"]), date(year, 12, 31))
            used += sum(1 for i in range((b - a).days + 1) if (a + timedelta(days=i)).weekday() < 5)
        return {"year": year, "per_year": c["leave_days"], "earned": earned, "used": used, "left": earned - used}

    def request_leave_kind(self, user_id, from_day, to_day, kind="Annual", reason=""):
        """Version 7: time off of a kind; annual leave needs enough holiday days."""
        if kind not in LEAVE_KINDS:
            raise ShopError("Please choose the kind of time off.")
        w = self.worker_of(user_id)
        if w and kind == "Annual":
            bal = self.leave_balance(w["id"], _d(from_day).year)
            if bal is not None:
                a, b = _d(from_day), _d(to_day)
                need = sum(1 for i in range((b - a).days + 1) if (a + timedelta(days=i)).weekday() < 5) if b >= a else 0
                if need > bal["left"]:
                    raise ShopError(f"You have {bal['left']:g} holiday days left this year; this asks for {need}.")
        leave_id = self.request_leave(user_id, from_day, to_day, reason)
        with self.db() as conn:
            conn.execute("UPDATE staff_leave SET kind = ? WHERE id = ?", (kind, leave_id))
        return leave_id

    # ================================================================ payroll
    def _payroll_actor(self, conn, actor_id):
        return self._actor(conn, actor_id, "payroll.manage")

    def create_payroll(self, actor_id, country, month):
        m = _month(month)
        first = m.replace(day=1)
        last = first.replace(day=calendar.monthrange(first.year, first.month)[1])
        if first > date.today():
            raise ShopError("A payroll can be made only for this month or a month before.")
        country = str(country or "").upper()
        with self.db() as conn:
            self._payroll_actor(conn, actor_id)
            rule = self.income_tax_rule(country, conn)
            staff = [dict(r) for r in conn.execute(
                "SELECT s.*, u.name FROM staff s JOIN offices o ON o.id = s.office_id JOIN users u ON u.id = s.user_id "
                "WHERE o.country = ? AND s.status != 'Removed'", (country,))]
            contracts = {}
            for s in staff:
                c = conn.execute("SELECT * FROM staff_contracts WHERE staff_id = ? AND status IN ('Active', 'Ended') "
                                 "AND start_day <= ? AND (end_day = '' OR end_day >= ?) ORDER BY id DESC LIMIT 1",
                                 (s["id"], last.isoformat(), first.isoformat())).fetchone()
                if c:
                    contracts[s["id"]] = dict(c)
            if not contracts:
                raise ShopError("No worker of this country has a contract in this month.")
            currency = CURRENCY_OF.get(country, "INR")
            if conn.execute("SELECT 1 FROM payroll_runs WHERE country = ? AND month = ? AND status != 'Cancelled'",
                            (country, first.strftime("%Y-%m"))).fetchone():
                raise ShopError("There is a payroll for this country and month already.")
            cur = conn.execute("INSERT INTO payroll_runs (country, month, currency, created_by, created_at) "
                               "VALUES (?, ?, ?, ?, ?)", (country, first.strftime("%Y-%m"), currency, actor_id, now()))
            run_id = cur.lastrowid
            total_g = total_n = 0
            for s in staff:
                c = contracts.get(s["id"])
                if not c:
                    continue
                slip = self._payslip(conn, s, c, first, last, rule)
                conn.execute("INSERT INTO payslips (run_id, staff_id, contract_id, currency, gross, tax, social, net, "
                             "worked_min, planned_min, overtime_min, unpaid_days, lines) VALUES "
                             "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                             (run_id, s["id"], c["id"], c["currency"], slip["gross"], slip["tax"], slip["social"],
                              slip["net"], slip["worked_min"], slip["planned_min"], slip["overtime_min"],
                              slip["unpaid_days"], json.dumps(slip["lines"])))
                total_g += slip["gross"]
                total_n += slip["net"]
            conn.execute("UPDATE payroll_runs SET workers = ?, total_gross = ?, total_net = ? WHERE id = ?",
                         (len(contracts), total_g, total_n, run_id))
            conn.execute("INSERT INTO approvals (kind, target_id, summary, requested_by, created_at) VALUES "
                         "('payroll', ?, ?, ?, ?)", (run_id, f"Payroll {country} {first:%Y-%m}: {len(contracts)} workers",
                                                     actor_id, now()))
            self._staff_log(conn, actor_id, None, None, "Payroll made", f"{country} {first:%Y-%m}, {len(contracts)} workers")
            return run_id

    def _payslip(self, conn, s, c, first, last, rule):
        days_in_month = (last - first).days + 1
        a = max(first, _d(c["start_day"]))
        b = min(last, _d(c["end_day"])) if c["end_day"] else last
        contract_days = max(0, (b - a).days + 1)
        worked = conn.execute("SELECT COALESCE(SUM(minutes), 0) FROM staff_shifts WHERE staff_id = ? AND start_at >= ? "
                              "AND start_at < ?", (s["id"], first.isoformat(),
                                                   (last + timedelta(days=1)).isoformat())).fetchone()[0]
        planned = 0
        for r in conn.execute("SELECT start_hm, end_hm FROM staff_rota WHERE staff_id = ? AND day >= ? AND day <= ?",
                              (s["id"], first.isoformat(), last.isoformat())):
            planned += self._plan_minutes(r)
        unpaid = 0
        for r in conn.execute("SELECT from_day, to_day FROM staff_leave WHERE staff_id = ? AND status = 'Approved' AND "
                              "kind = 'Unpaid' AND from_day <= ? AND to_day >= ?",
                              (s["id"], last.isoformat(), first.isoformat())):
            x, y = max(_d(r["from_day"]), a), min(_d(r["to_day"]), b)
            unpaid += max(0, (y - x).days + 1)
        lines = []
        if c["pay_kind"] == "month":
            base = round(c["pay_amount"] * contract_days / days_in_month)
            lines.append(["Salary", f"{contract_days}/{days_in_month} days", base])
            hour_pay = c["pay_amount"] / (c["hours_week"] * 52 / 12 * 60)          # per minute
            overtime = max(0, int(worked) - planned) if planned else 0
            ot = round(overtime * hour_pay * OVERTIME_RATE)
            if ot:
                lines.append(["Overtime x1.5", f"{overtime // 60} h {overtime % 60} min", ot])
            deduct = round(c["pay_amount"] * unpaid / days_in_month)
            if deduct:
                lines.append(["Unpaid time off", f"{unpaid} days", -deduct])
            gross = max(0, base + ot - deduct)
        else:
            overtime = 0
            gross = round(c["pay_amount"] * int(worked) / 60)
            lines.append(["Hours worked", f"{int(worked) // 60} h {int(worked) % 60} min", gross])
        year_start = f"{first.year}-01"
        prev = conn.execute("SELECT COALESCE(SUM(p.gross), 0), COALESCE(SUM(p.tax), 0) FROM payslips p JOIN payroll_runs r "
                            "ON r.id = p.run_id WHERE p.staff_id = ? AND r.status IN ('Approved', 'Paid') AND r.month >= ? "
                            "AND r.month < ?", (s["id"], year_start, first.strftime("%Y-%m"))).fetchone()
        tax, social = self.month_tax(rule, gross, prev[0], prev[1])
        if rule:
            lines.append([rule["name"], "example rule", -tax])
            if social:
                lines.append(["Social contributions", f"{rule['social_pct']:g} %", -social])
        return {"gross": gross, "tax": tax, "social": social, "net": gross - tax - social, "worked_min": int(worked),
                "planned_min": planned, "overtime_min": overtime, "unpaid_days": unpaid, "lines": lines}

    def payroll_runs(self):
        with self.db() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT r.*, u.name AS made_by, a.name AS approved_by_name FROM payroll_runs r "
                "JOIN users u ON u.id = r.created_by LEFT JOIN users a ON a.id = r.approved_by ORDER BY r.id DESC")]

    def payroll_run(self, run_id):
        with self.db() as conn:
            r = conn.execute("SELECT * FROM payroll_runs WHERE id = ?", (run_id,)).fetchone()
            if r is None:
                return None
            slips = [dict(x) for x in conn.execute(
                "SELECT p.*, u.name, o.name AS office FROM payslips p JOIN staff s ON s.id = p.staff_id "
                "JOIN users u ON u.id = s.user_id JOIN offices o ON o.id = s.office_id WHERE p.run_id = ? ORDER BY u.name",
                (run_id,))]
        for s in slips:
            s["line_list"] = json.loads(s["lines"])
        return {**dict(r), "slips": slips}

    def approve_payroll(self, actor_id, run_id):
        """Dual control: the person who made the run cannot approve it."""
        with self.db() as conn:
            self._payroll_actor(conn, actor_id)
            r = conn.execute("SELECT * FROM payroll_runs WHERE id = ?", (run_id,)).fetchone()
            if r is None:
                raise ShopError("Payroll not found.")
            if r["created_by"] == actor_id:
                raise ShopError("Another person must approve this payroll (dual control).")
            if not conn.execute("UPDATE payroll_runs SET status = 'Approved', approved_by = ?, approved_at = ? "
                                "WHERE id = ? AND status = 'Draft'", (actor_id, now(), run_id)).rowcount:
                raise ShopError("This payroll was decided already.")
            conn.execute("UPDATE approvals SET status = 'Approved', decided_by = ?, decided_at = ? WHERE kind = 'payroll' "
                         "AND target_id = ? AND status = 'Waiting'", (actor_id, now(), run_id))
            self._staff_log(conn, actor_id, None, None, "Payroll approved", f"{r['country']} {r['month']}")

    def cancel_payroll(self, actor_id, run_id):
        with self.db() as conn:
            self._payroll_actor(conn, actor_id)
            if not conn.execute("UPDATE payroll_runs SET status = 'Cancelled' WHERE id = ? AND status IN ('Draft', 'Approved')",
                                (run_id,)).rowcount:
                raise ShopError("Only a payroll that is not paid can be cancelled.")
            conn.execute("UPDATE approvals SET status = 'Refused', decided_by = ?, decided_at = ? WHERE kind = 'payroll' "
                         "AND target_id = ? AND status = 'Waiting'", (actor_id, now(), run_id))
            self._staff_log(conn, actor_id, None, None, "Payroll cancelled", str(run_id))

    def pay_payroll(self, actor_id, run_id):
        """Test mode: the run is marked paid once and every worker gets his payslip; no money moves."""
        with self.db() as conn:
            self._payroll_actor(conn, actor_id)
            r = conn.execute("SELECT * FROM payroll_runs WHERE id = ?", (run_id,)).fetchone()
            if r is None:
                raise ShopError("Payroll not found.")
            if not conn.execute("UPDATE payroll_runs SET status = 'Paid', paid_at = ? WHERE id = ? AND status = 'Approved'",
                                (now(), run_id)).rowcount:
                raise ShopError("Only an approved payroll can be paid (once).")
            for s in conn.execute("SELECT s.user_id FROM payslips p JOIN staff s ON s.id = p.staff_id WHERE p.run_id = ?",
                                  (run_id,)).fetchall():
                self.notify(conn, s["user_id"], "Your payslip is ready", f"Payslip {r['month']}", url="/staff/payslips")
            self._staff_log(conn, actor_id, None, None, "Payroll paid (test mode)", f"{r['country']} {r['month']}")

    def my_payslips(self, user_id):
        w = self.worker_of(user_id)
        if not w:
            return []
        with self.db() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT p.*, r.month, r.country, r.paid_at FROM payslips p JOIN payroll_runs r ON r.id = p.run_id "
                "WHERE p.staff_id = ? AND r.status = 'Paid' ORDER BY r.month DESC", (w["id"],))]
        for r in rows:
            r["line_list"] = json.loads(r["lines"])
        return rows

    # ================================================================ approvals (dual control)
    def approvals(self, status="Waiting"):
        with self.db() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT a.*, u.name AS requested_name, d.name AS decided_name FROM approvals a "
                "JOIN users u ON u.id = a.requested_by LEFT JOIN users d ON d.id = a.decided_by "
                "WHERE (? = '' OR a.status = ?) ORDER BY a.id DESC LIMIT 100", (status, status))]

    def decide_approval(self, actor_id, approval_id, approve, note=""):
        with self.db() as conn:
            a = conn.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
        if a is None:
            raise ShopError("Approval not found.")
        if a["requested_by"] == actor_id:
            raise ShopError("Another person must decide this (dual control).")
        if a["kind"] == "payroll":
            return self.approve_payroll(actor_id, a["target_id"]) if approve else self.cancel_payroll(actor_id, a["target_id"])
        if a["kind"] == "grant_all":
            if not (self.get_user(actor_id) or {}).get("is_admin") and ALL not in self.abilities_of(actor_id):
                raise ShopError("Only the administrator or a person with the ability to do everything can decide this.")
            with self.db() as conn:
                if not conn.execute("UPDATE approvals SET status = ?, decided_by = ?, decided_at = ?, note = ? WHERE id = ? "
                                    "AND status = 'Waiting'", ("Approved" if approve else "Refused", actor_id, now(),
                                                               " ".join(str(note).split())[:200], approval_id)).rowcount:
                    raise ShopError("This was decided already.")
                if approve:
                    t = conn.execute("SELECT * FROM staff WHERE id = ?", (a["target_id"],)).fetchone()
                    if t and t["status"] != "Removed":
                        conn.execute("UPDATE staff SET abilities = ?, updated_at = ? WHERE id = ?",
                                     (" ".join(sorted(split(t["abilities"]) | {ALL})), now(), t["id"]))
                        self._staff_log(conn, actor_id, t["id"], t["office_id"], "Worker changed",
                                        "+ all (second person approved)")
                        self.notify(conn, t["user_id"], "Your abilities changed",
                                    "Open 'My work' to see what you can do now.", url="/staff")
            return None
        raise ShopError("Unknown approval.")

    def update_worker(self, actor_id, staff_id, data):
        """Version 7: with dual control on, the ability "all" waits for a second person."""
        if str(self.setting("dual_control") or "0") == "1" and "abilities" in data:
            wanted = split(" ".join(data.get("abilities") or []))
            with self.db() as conn:
                t = conn.execute("SELECT abilities FROM staff WHERE id = ?", (staff_id,)).fetchone()
            if t is not None and ALL in wanted and ALL not in split(t["abilities"]):
                data = dict(data)
                data["abilities"] = sorted(wanted - {ALL})
                super().update_worker(actor_id, staff_id, data)
                with self.db() as conn:
                    if conn.execute("SELECT 1 FROM approvals WHERE kind = 'grant_all' AND target_id = ? AND "
                                    "status = 'Waiting'", (staff_id,)).fetchone():
                        raise ShopError("This change is waiting for a second person already.")
                    conn.execute("INSERT INTO approvals (kind, target_id, summary, requested_by, created_at) VALUES "
                                 "('grant_all', ?, 'Give the ability to do everything', ?, ?)",
                                 (staff_id, actor_id, now()))
                    self._staff_log(conn, actor_id, staff_id, None, "Approval asked", "ability 'all' (dual control)")
                return "waiting"
        return super().update_worker(actor_id, staff_id, data)

    def set_dual_control(self, actor_id, on):
        u = self.get_user(actor_id)
        if not u or not u["is_admin"]:
            raise ShopError("Only the administrator can change this.")
        if on:
            with self.db() as conn:
                n = conn.execute("SELECT COUNT(*) FROM staff WHERE status = 'Active' AND (' ' || abilities || ' ') "
                                 "LIKE '% all %'").fetchone()[0]
            if not n:
                raise ShopError("Turn this on only when a second person has the ability to do everything.")
        with self.db() as conn:
            conn.execute("INSERT INTO settings (key, value) VALUES ('dual_control', ?) ON CONFLICT (key) DO UPDATE "
                         "SET value = excluded.value", ("1" if on else "0",))

    # ================================================================ training
    def add_training(self, actor_id, staff_id, course, done_on, certificate=""):
        if course not in COURSES:
            raise ShopError("Please choose a course.")
        done = _d(done_on)
        if done > date.today():
            raise ShopError("The course cannot be done in the future.")
        with self.db() as conn:
            t = self._target(conn, actor_id, staff_id, "staff.manage")
            expires = add_months(done, COURSES[course][2])
            conn.execute("INSERT INTO staff_trainings (staff_id, course, done_on, expires_on, certificate, added_by, "
                         "created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (staff_id, course, done.isoformat(), expires.isoformat(),
                          " ".join(str(certificate).split())[:40], actor_id, now()))
            self._staff_log(conn, actor_id, staff_id, t["office_id"], "Training added", COURSES[course][0])

    def trainings(self, staff_id, conn=None):
        def read(c):
            rows = [dict(r) for r in c.execute("SELECT * FROM staff_trainings WHERE staff_id = ? ORDER BY done_on DESC",
                                               (staff_id,))]
            today = date.today().isoformat()
            for r in rows:
                r["name"] = COURSES.get(r["course"], (r["course"],))[0]
                r["valid"] = r["expires_on"] >= today
            return rows
        if conn is not None:
            return read(conn)
        with self.db() as c:
            return read(c)

    def missing_training(self, staff_id, abilities, conn=None):
        """Courses that the abilities of a worker need and that he has not (or no longer) done."""
        have = {t["course"] for t in self.trainings(staff_id, conn) if t["valid"]}
        need = {COURSE_FOR[a] for a in abilities if a in COURSE_FOR}
        if ALL in abilities:
            need = set()                                     # "all" is trusted by the administrator
        return sorted(need - have)

    def training_blocks(self, user_id, ability):
        """True when the setting "require_training" is on and this ability needs a course the worker lacks."""
        if ability not in COURSE_FOR or str(self.setting("require_training") or "0") != "1":
            return False
        w = self.worker_of(user_id)
        if not w:
            return False
        return COURSE_FOR[ability] in self.missing_training(w["id"], {ability})

    def can(self, user_id, ability):
        ok = super().can(user_id, ability)
        if ok and self.training_blocks(user_id, ability):
            return False
        return ok

    def training_report(self, user_id):
        """Workers of my branch with missing or soon expiring certificates."""
        soon = (date.today() + timedelta(days=60)).isoformat()
        out = []
        with self.db() as conn:
            for w in self.workers(user_id):
                if w["status"] != "Active":
                    continue
                tr = self.trainings(w["id"], conn)
                missing = self.missing_training(w["id"], split(w["abilities"]), conn)
                expiring = [t for t in tr if t["valid"] and t["expires_on"] <= soon]
                if missing or expiring:
                    out.append({"worker": w, "missing": [COURSES[c][0] for c in missing], "expiring": expiring})
        return out

    # ================================================================ helper offices
    def assign_helper_office(self, actor_id, staff_id, office_id, from_day, to_day):
        a, b = _d(from_day), _d(to_day)
        if b < a:
            raise ShopError("The last day is before the first day.")
        if (b - a).days > 92:
            raise ShopError("A worker can help in another office for at most 3 months at a time.")
        with self.db() as conn:
            t = self._target(conn, actor_id, staff_id, "staff.manage")
            o = conn.execute("SELECT * FROM offices WHERE id = ? AND active = 1", (int(office_id or 0),)).fetchone()
            if o is None:
                raise ShopError("Office not found.")
            if o["id"] == t["office_id"]:
                raise ShopError("This is the worker's own office.")
            conn.execute("INSERT INTO staff_offices (staff_id, office_id, from_day, to_day, added_by, created_at) VALUES "
                         "(?, ?, ?, ?, ?, ?) ON CONFLICT (staff_id, office_id, from_day) DO UPDATE SET to_day = excluded.to_day",
                         (staff_id, o["id"], a.isoformat(), b.isoformat(), actor_id, now()))
            self._staff_log(conn, actor_id, staff_id, o["id"], "Helps in another office", f"{o['name']}: {a} - {b}")
            self.notify(conn, t["user_id"], "You help in another office", f"{o['name']}: {a} - {b}", url="/staff")

    def helper_offices(self, staff_id):
        with self.db() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT h.*, o.name AS office FROM staff_offices h JOIN offices o ON o.id = h.office_id "
                "WHERE h.staff_id = ? AND h.to_day >= ? ORDER BY h.from_day", (staff_id, date.today().isoformat()))]

    def helpers_in(self, user_id, office_id=None):
        """Workers of other branches who help in the offices of my branch (now or soon)."""
        u = self.get_user(user_id)
        if u and u["is_admin"]:
            branch = None
        else:
            w = self.worker_of(user_id)
            if not w or w["status"] != "Active":
                return []
            branch = self.subtree(w["office_id"])
        since = (date.today() - timedelta(days=31)).isoformat()
        with self.db() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT h.office_id AS help_office_id, o.name AS help_office, h.from_day, h.to_day, s.*, u.name, u.email, "
                "ho.name AS office FROM staff_offices h JOIN staff s ON s.id = h.staff_id JOIN users u ON u.id = s.user_id "
                "JOIN offices o ON o.id = h.office_id JOIN offices ho ON ho.id = s.office_id "
                "WHERE h.to_day >= ? AND s.status = 'Active'", (since,))]
        out = []
        for r in rows:
            if branch is not None and r["help_office_id"] not in branch:
                continue
            if office_id and r["help_office_id"] != int(office_id):
                continue
            out.append(r)
        return out

    def _helper_allowed(self, conn, actor_id, staff_id, office_id, days):
        """May this actor plan shifts of a helper in this office on these days?"""
        admin, actor, branch = self._actor(conn, actor_id, "shifts.manage")
        if not admin and int(office_id) not in branch:
            return False
        for d in days:
            if not conn.execute("SELECT 1 FROM staff_offices WHERE staff_id = ? AND office_id = ? AND from_day <= ? AND "
                                "to_day >= ?", (staff_id, office_id, d, d)).fetchone():
                raise ShopError("The worker does not help in this office on {0}.".replace("{0}", d))
        return True

    # ================================================================ offices: where they are
    def ensure_office_locations(self):
        with self.db() as conn:
            for o in conn.execute("SELECT id, place FROM offices WHERE place != '' AND lat IS NULL").fetchall():
                p = geo.get(o["place"])
                if p:
                    conn.execute("UPDATE offices SET lat = ?, lng = ? WHERE id = ?", (p["lat"], p["lng"], o["id"]))

    def set_office_location(self, actor_id, office_id, lat, lng, geofence_m=GEOFENCE_M, remote_ok=False):
        try:
            lat, lng, fence = float(lat), float(lng), int(geofence_m or GEOFENCE_M)
        except (TypeError, ValueError):
            raise ShopError("Please give the position as numbers.")
        if not (-90 <= lat <= 90 and -180 <= lng <= 180) or not 50 <= fence <= 5000:
            raise ShopError("Please check the position and the distance (50 to 5,000 m).")
        with self.db() as conn:
            admin, actor, branch = self._actor(conn, actor_id, "offices.manage")
            if not admin and int(office_id) not in branch:
                raise ShopError("You can change only the offices under your own office.")
            conn.execute("UPDATE offices SET lat = ?, lng = ?, geofence_m = ?, remote_ok = ? WHERE id = ?",
                         (lat, lng, fence, 1 if remote_ok else 0, office_id))
            self._staff_log(conn, actor_id, None, office_id, "Office place changed", f"{fence} m")

    def clock_in_at(self, user_id, lat=None, lng=None, accuracy=None):
        """Version 7: clock in with the position of the phone (checked against the office)."""
        w = self.worker_of(user_id)
        if not w or w["status"] != "Active":
            raise ShopError("Only active workers can clock in.")
        with self.db() as conn:
            o = conn.execute("SELECT lat, lng, geofence_m, remote_ok FROM offices WHERE id = ?", (w["office_id"],)).fetchone()
            today = date.today().isoformat()
            helper = conn.execute("SELECT o.lat, o.lng, o.geofence_m, o.remote_ok FROM staff_offices h JOIN offices o "
                                  "ON o.id = h.office_id WHERE h.staff_id = ? AND h.from_day <= ? AND h.to_day >= ?",
                                  (w["id"], today, today)).fetchall()
        flag, dist = "", None
        places = [p for p in [o, *helper] if p and p["lat"] is not None]
        if places:
            try:
                here = (float(lat), float(lng))
            except (TypeError, ValueError):
                here = None
            if here is None:
                flag = "no location"
            else:
                acc = min(float(accuracy or 0), 200)
                dists = [(km_between(here, (p["lat"], p["lng"])) * 1000, p) for p in places]
                dist, p = min(dists, key=lambda x: x[0])
                if dist > p["geofence_m"] + acc:
                    if not any(q["remote_ok"] for _, q in dists):
                        raise ShopError("You are {0} from your office. Please clock in at the office."
                                        .replace("{0}", f"{dist / 1000:.1f} km" if dist >= 1000 else f"{dist:.0f} m"))
                    flag = "remote"
        self.clock_in(user_id)
        with self.db() as conn:
            conn.execute("UPDATE staff_shifts SET in_lat = ?, in_lng = ?, in_dist_m = ?, in_flag = ? WHERE staff_id = ? "
                         "AND end_at = ''", (lat if flag != "no location" else None, lng if flag != "no location" else None,
                                             int(dist) if dist is not None else None, flag, w["id"]))
        return flag
