"""Natural-language quick add.

Examples:
    call dentist tomorrow at 3pm !!
    water plants every 3 days
    change water filter 30 days after done
    standup every weekday at 9:30
    pay rent monthly on the 1st
    bins every tue and fri at 7pm
    review budget every 2nd sunday
    renew passport on 2027-03-01 !low

Priority: "!!" / "!high" -> high, "!low" -> low.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from . import recurrence

DAYS = {
    "mon": 0, "monday": 0, "tue": 1, "tues": 1, "tuesday": 1,
    "wed": 2, "weds": 2, "wednesday": 2, "thu": 3, "thur": 3, "thurs": 3,
    "thursday": 3, "fri": 4, "friday": 4, "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}
MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
NTH = {"1st": 1, "first": 1, "2nd": 2, "second": 2, "3rd": 3, "third": 3,
       "4th": 4, "fourth": 4, "5th": 5, "fifth": 5, "last": -1}

DAY_RE = "|".join(sorted(DAYS, key=len, reverse=True))
MONTH_RE = "|".join(sorted(MONTHS, key=len, reverse=True))
NTH_RE = "|".join(sorted(NTH, key=len, reverse=True))
DAYLIST_RE = rf"(?:{DAY_RE})(?:\s*(?:,|and|&|\+)?\s*(?:{DAY_RE}))*"
UNIT_RE = r"(?P<u>day|week|month|year)s?"
DOM_RE = r"(\d{1,2})(?:st|nd|rd|th)"


@dataclass
class Parsed:
    title: str
    priority: int = 1  # 0 low, 1 normal, 2 high
    due_date: date | None = None
    due_time: str | None = None  # "HH:MM"
    recurrence: dict | None = None
    matched: list[str] = field(default_factory=list)

    def summary(self) -> str:
        bits = []
        if self.due_date:
            bits.append(self.due_date.strftime("%a %d %b"))
        if self.due_time:
            bits.append(self.due_time)
        if self.recurrence:
            bits.append("🔁 " + recurrence.describe(self.recurrence))
        if self.priority == 2:
            bits.append("❗high")
        elif self.priority == 0:
            bits.append("low")
        return " · ".join(bits)


def _days_in(text: str) -> list[int]:
    return sorted({DAYS[w.lower()] for w in re.findall(rf"\b({DAY_RE})\b", text, re.I)})


def _dom(s: str) -> int:
    return int(re.match(r"\d+", s).group())


def parse(text: str, today: date) -> Parsed:
    s = " " + text.strip() + " "
    p = Parsed(title="")

    def take(pattern: str, fn) -> bool:
        nonlocal s
        m = re.search(pattern, s, re.I)
        if not m:
            return False
        if fn(m) is False:
            return False
        p.matched.append(m.group().strip())
        s = s[: m.start()] + " " + s[m.end():]
        return True

    # ---- priority ----
    def prio_high(m):
        p.priority = 2

    def prio_low(m):
        p.priority = 0

    take(r"(?<!\S)(?:!!|!high|!h|!urgent)(?!\S)", prio_high) or \
        take(r"(?<!\S)(?:!low|!l)(?!\S)", prio_low)

    # ---- time ----
    def set_time(m):
        h = int(m.group("h"))
        mi = int(m.group("m") or 0)
        ap = (m.groupdict().get("ap") or "").lower()
        if ap:
            if not 1 <= h <= 12:
                return False
            h = h % 12 + (12 if ap.startswith("p") else 0)
        elif 1 <= h <= 6 and not m.group("m"):
            h += 12  # "at 5" almost always means 5pm
        if h > 23 or mi > 59:
            return False
        p.due_time = f"{h:02d}:{mi:02d}"

    (take(r"\s(?:at\s+|@\s*)?(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>am|pm|a\.m\.|p\.m\.)(?=\s)", set_time)
     or take(r"\s(?:at\s+|@\s*)?(?P<h>\d{1,2}):(?P<m>\d{2})(?=\s)", set_time)
     or take(r"\s(?:at|@)\s*(?P<h>\d{1,2})(?P<m>)(?P<ap>)(?=\s)", set_time))

    def noon(m):
        p.due_time = "12:00"

    def tonight(m):
        p.due_date = today
        p.due_time = p.due_time or "19:00"

    if not p.due_time:
        take(r"\s(?:at\s+)?(?:noon|midday)(?=\s)", noon)

    # ---- recurrence (most specific first) ----
    def r_after(m):
        n = int(m.group("n") or 1)
        mult = {"day": 1, "week": 7, "month": 30, "year": 365}[m.group("u").lower()]
        p.recurrence = {"type": "after", "days": n * mult}

    def r_weekdays(m):
        p.recurrence = {"type": "weekly", "days": [0, 1, 2, 3, 4]}

    def r_weekends(m):
        p.recurrence = {"type": "weekly", "days": [5, 6]}

    def r_nth_weekday(m):
        p.recurrence = {"type": "monthly", "nth": NTH[m.group("nth").lower()],
                        "weekday": DAYS[m.group("d").lower()]}

    def r_month_day(m):
        day = -1 if m.group("last") else _dom(m.group("dom"))
        n = int(m.groupdict().get("n") or 1)
        p.recurrence = {"type": "monthly", "interval": n, "day": day}

    def r_every_days(m):
        n = int(m.group("n") or 1)
        days = _days_in(m.group("days"))
        p.recurrence = {"type": "weekly", "interval": n, "days": days}

    def r_weekly_on(m):
        n = 2 if m.group("w").lower() == "fortnightly" else 1
        p.recurrence = {"type": "weekly", "interval": n, "days": _days_in(m.group("days"))}

    def r_yearly_on(m):
        mon = MONTHS[(m.group("mon1") or m.group("mon2")).lower()]
        day = int(m.group("d1") or m.group("d2"))
        p.recurrence = {"type": "yearly", "month": mon, "day": day}

    def r_every_n(m):
        n = int(m.group("n") or 1)
        u = m.group("u").lower()
        p.recurrence = {"type": {"day": "daily", "week": "weekly", "month": "monthly",
                                 "year": "yearly"}[u], "interval": n}
        if u == "year":
            p.recurrence.pop("interval")

    def r_simple(m):
        w = m.group("w").lower()
        p.recurrence = {"type": {"daily": "daily", "weekly": "weekly", "monthly": "monthly",
                                 "yearly": "yearly", "annually": "yearly",
                                 "fortnightly": "weekly"}[w]}
        if w == "fortnightly":
            p.recurrence["interval"] = 2

    N = r"(?:(?P<n>\d+)\s+)?"
    (take(rf"\s(?:every\s+)?{N}(?P<u>day|week|month|year)s?\s+after\s+(?:done|completion|completed|complete|finished|i\s+do\s+it)(?=\s)", r_after)
     or take(r"\s(?:every\s+)?weekdays?(?=\s)", r_weekdays)
     or take(r"\s(?:every\s+)?weekends?(?=\s)", r_weekends)
     or take(rf"\s(?:every|each|on\s+the)\s+(?P<nth>{NTH_RE})\s+(?P<d>{DAY_RE})(?:\s+of\s+(?:every|each|the)\s+month)?(?=\s)", r_nth_weekday)
     or take(rf"\s(?:every|each)\s+{N}months?\s+on\s+the\s+(?:(?P<last>last\s+day)|(?P<dom>{DOM_RE}))(?=\s)", r_month_day)
     or take(rf"\smonthly\s+on\s+the\s+(?:(?P<last>last\s+day)|(?P<dom>{DOM_RE}))(?=\s)", r_month_day)
     or take(rf"\s(?:on\s+the\s+)?(?:(?P<last>last\s+day)|(?P<dom>{DOM_RE}))\s+of\s+(?:every|each|the)\s+month(?=\s)", r_month_day)
     or take(rf"\s(?:every|each)\s+(?:(?P<last>last\s+day)|(?P<dom>{DOM_RE}))(?:\s+of\s+the\s+month)?(?=\s)", r_month_day)
     or take(rf"\s(?:every|each)\s+(?:(?P<n>\d+)\s+weeks?\s+on\s+|week\s+on\s+)?(?P<days>{DAYLIST_RE})(?=\s)", r_every_days)
     or take(rf"\s(?P<w>weekly|fortnightly)\s+on\s+(?P<days>{DAYLIST_RE})(?=\s)", r_weekly_on)
     or take(rf"\s(?:every|each)\s+year\s+on\s+(?:(?P<mon1>{MONTH_RE})\s+(?P<d1>\d{{1,2}})(?:st|nd|rd|th)?|(?P<d2>\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?(?P<mon2>{MONTH_RE}))(?=\s)", r_yearly_on)
     or take(rf"\s(?:yearly|annually)\s+on\s+(?:(?P<mon1>{MONTH_RE})\s+(?P<d1>\d{{1,2}})(?:st|nd|rd|th)?|(?P<d2>\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?(?P<mon2>{MONTH_RE}))(?=\s)", r_yearly_on)
     or take(rf"\s(?:every|each)\s+{N}{UNIT_RE}(?=\s)", r_every_n)
     or take(r"\s(?P<w>daily|weekly|monthly|yearly|annually|fortnightly)(?=\s)", r_simple))

    # ---- dates ----
    def d_today(m):
        p.due_date = today

    def d_tomorrow(m):
        p.due_date = today + timedelta(days=1)

    def d_in(m):
        n = int(m.group("n")) if m.group("n").isdigit() else 1
        u = m.group("u").lower()
        if u == "month":
            y, mo = divmod(today.month - 1 + n, 12)
            p.due_date = recurrence._clamp_day(today.year + y, mo + 1, today.day)
        else:
            p.due_date = today + timedelta(days=n * {"day": 1, "week": 7, "year": 365}[u])

    def d_weekday(m):
        wd = DAYS[m.group("d").lower()]
        delta = (wd - today.weekday()) % 7
        if m.group("next"):
            delta = delta or 7
            if delta < 7 and m.group("next").lower().startswith("next week"):
                delta += 7
        p.due_date = today + timedelta(days=delta)

    def d_iso(m):
        p.due_date = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))

    def d_month_day(m):
        mon = MONTHS[(m.group("mon1") or m.group("mon2")).lower()]
        day = int(m.group("d1") or m.group("d2"))
        y = today.year
        try:
            d = date(y, mon, day)
            if d < today:
                d = date(y + 1, mon, day)
        except ValueError:
            return False
        p.due_date = d

    def d_dom(m):
        day = _dom(m.group("dom"))
        y, mo = today.year, today.month
        for _ in range(3):
            try:
                d = date(y, mo, day)
                if d >= today:
                    p.due_date = d
                    return
            except ValueError:
                pass
            y, mo = (y + 1, 1) if mo == 12 else (y, mo + 1)
        return False

    def d_next_unit(m):
        u = m.group("u").lower()
        if u == "week":
            p.due_date = today + timedelta(days=7 - today.weekday())  # next Monday
        elif u == "month":
            y, mo = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
            p.due_date = date(y, mo, 1)

    take(r"\s(?:tonight)(?=\s)", tonight) \
        or take(r"\s(?:today|tod)(?=\s)", d_today) \
        or take(r"\s(?:tomorrow|tmrw|tmr|tomo)(?=\s)", d_tomorrow) \
        or take(r"\s(?:on\s+)?(\d{4})-(\d{1,2})-(\d{1,2})(?=\s)", d_iso) \
        or take(r"\sin\s+(?P<n>\d+|a|an|one)\s+(?P<u>day|week|month|year)s?(?=\s)", d_in) \
        or take(rf"\s(?:on\s+|(?P<next>next\s+week\s+|next\s+|this\s+))(?P<d>{DAY_RE})(?=\s)", d_weekday) \
        or take(rf"\s(?P<next>)(?P<d>{DAY_RE})\s*$", d_weekday) \
        or take(r"\snext\s+(?P<u>week|month)(?=\s)", d_next_unit) \
        or take(rf"\s(?:on\s+)?(?:(?P<mon1>{MONTH_RE})\s+(?P<d1>\d{{1,2}})(?:st|nd|rd|th)?|(?P<d2>\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?(?P<mon2>{MONTH_RE}))(?=\s)", d_month_day) \
        or take(rf"\son\s+the\s+(?P<dom>{DOM_RE})(?=\s)", d_dom)

    p.title = re.sub(r"\s+", " ", s).strip(" ,-")
    if not p.title:
        p.title = text.strip()
    return p



BULLET_RE = re.compile(r"^(?:[-•*+]|\d+[.)]|\[[ xX]?\])\s*")
MAX_BULK = 200


def split_bulk(text: str) -> list[tuple[str, list[str]]]:
    """Split a block of text into (task line, [subtask lines]).

    One task per line. A line that is indented, or starts with "+", becomes a
    subtask of the task above it. List markers ("-", "*", "•", "1.", "[ ]")
    are stripped, so pasted lists and markdown checklists work as-is.
    """
    items: list[tuple[str, list[str]]] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        is_sub = bool(items) and (raw[:1] in (" ", "\t") or stripped.startswith("+"))
        clean = stripped
        for _ in range(2):  # e.g. "- [ ] buy milk"
            clean = BULLET_RE.sub("", clean, count=1)
        clean = clean.strip()
        if not clean:
            continue
        if is_sub:
            items[-1][1].append(clean)
        else:
            items.append((clean, []))
    return items
