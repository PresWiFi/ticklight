"""Recurrence rules.

A rule is a small dict stored as JSON on the task:

    {"type": "daily",   "interval": 1}
    {"type": "weekly",  "interval": 1, "days": [0, 3]}       # Mon=0 .. Sun=6
    {"type": "monthly", "interval": 1, "day": 15}            # day -1 = last day
    {"type": "monthly", "interval": 1, "nth": 2, "weekday": 1}  # 2nd Tuesday, nth -1 = last
    {"type": "yearly",  "month": 3, "day": 1}
    {"type": "after",   "days": 3}                           # N days after completion

Calendar rules are anchored to the task's `anchor` date (its first due date),
so "every 2 weeks" keeps a stable rhythm no matter when you tick it off.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta

WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
ORDINALS = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 5: "5th", -1: "last"}


class RuleError(ValueError):
    pass


def normalize(rule: dict | None) -> dict | None:
    """Validate a rule and fill defaults. Raises RuleError on bad input."""
    if not rule:
        return None
    t = rule.get("type")
    out: dict = {"type": t}
    try:
        if t in ("daily", "weekly", "monthly"):
            out["interval"] = max(1, int(rule.get("interval", 1)))
        if t == "daily":
            pass
        elif t == "weekly":
            days = sorted({int(d) for d in rule.get("days") or []})
            if any(d < 0 or d > 6 for d in days):
                raise RuleError("weekdays must be 0-6")
            out["days"] = days  # empty -> same weekday as anchor
        elif t == "monthly":
            if rule.get("nth") is not None:
                nth, wd = int(rule["nth"]), int(rule["weekday"])
                if nth not in ORDINALS or not 0 <= wd <= 6:
                    raise RuleError("bad nth/weekday")
                out["nth"], out["weekday"] = nth, wd
            elif rule.get("day") is not None:
                day = int(rule["day"])
                if not (1 <= day <= 31 or day == -1):
                    raise RuleError("day must be 1-31 or -1")
                out["day"] = day
            # neither -> same day-of-month as anchor
        elif t == "yearly":
            if rule.get("month") is not None:
                out["month"] = int(rule["month"])
                out["day"] = int(rule.get("day", 1))
                if not 1 <= out["month"] <= 12 or not 1 <= out["day"] <= 31:
                    raise RuleError("bad month/day")
        elif t == "after":
            out["days"] = max(1, int(rule.get("days", 1)))
        else:
            raise RuleError(f"unknown recurrence type: {t!r}")
    except (TypeError, ValueError, KeyError) as e:
        if isinstance(e, RuleError):
            raise
        raise RuleError(str(e)) from e
    return out


def _clamp_day(y: int, m: int, d: int) -> date:
    last = calendar.monthrange(y, m)[1]
    return date(y, m, last if d == -1 else min(d, last))


def _nth_weekday(y: int, m: int, nth: int, weekday: int) -> date | None:
    if nth == -1:
        last = date(y, m, calendar.monthrange(y, m)[1])
        return last - timedelta(days=(last.weekday() - weekday) % 7)
    first = date(y, m, 1)
    d = first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (nth - 1))
    return d if d.month == m else None  # e.g. no 5th Tuesday this month


def _add_months(y: int, m: int, n: int) -> tuple[int, int]:
    idx = y * 12 + (m - 1) + n
    return idx // 12, idx % 12 + 1


def first_on_or_after(rule: dict, anchor: date, start: date) -> date:
    """First calendar occurrence >= start (and >= anchor). Not used for 'after'."""
    start = max(start, anchor)
    t = rule["type"]
    if t == "daily":
        n = rule["interval"]
        offset = (start - anchor).days
        return anchor + timedelta(days=-(-offset // n) * n)
    if t == "weekly":
        n = rule["interval"]
        days = rule["days"] or [anchor.weekday()]
        anchor_week = anchor - timedelta(days=anchor.weekday())
        d = start
        for _ in range(7 * n + 7):
            weeks = (d - anchor_week).days // 7
            if weeks % n == 0 and d.weekday() in days:
                return d
            d += timedelta(days=1)
        raise RuleError("no weekly occurrence found")
    if t == "monthly":
        n = rule["interval"]
        y, m = start.year, start.month
        for _ in range(12 * n + 24):
            months = (y - anchor.year) * 12 + (m - anchor.month)
            if months % n == 0:
                if "nth" in rule:
                    cand = _nth_weekday(y, m, rule["nth"], rule["weekday"])
                else:
                    cand = _clamp_day(y, m, rule.get("day", anchor.day))
                if cand and cand >= start:
                    return cand
            y, m = _add_months(y, m, 1)
        raise RuleError("no monthly occurrence found")
    if t == "yearly":
        month = rule.get("month", anchor.month)
        day = rule.get("day", anchor.day)
        for y in range(start.year, start.year + 3):
            cand = _clamp_day(y, month, day)
            if cand >= start:
                return cand
    raise RuleError(f"cannot compute occurrence for {t!r}")


def next_after(rule: dict, anchor: date, after: date) -> date:
    """Next occurrence strictly after `after`.

    For 'after' rules, `after` is the completion date.
    """
    if rule["type"] == "after":
        return after + timedelta(days=rule["days"])
    return first_on_or_after(rule, anchor, after + timedelta(days=1))


def describe(rule: dict | None) -> str:
    if not rule:
        return ""
    t = rule["type"]
    n = rule.get("interval", 1)
    if t == "daily":
        return "daily" if n == 1 else f"every {n} days"
    if t == "weekly":
        days = rule.get("days") or []
        every = "weekly" if n == 1 else f"every {n} weeks"
        if days == [0, 1, 2, 3, 4] and n == 1:
            return "weekdays"
        if days == [5, 6] and n == 1:
            return "weekends"
        if days:
            return f"{every} on {', '.join(WEEKDAY_NAMES[d] for d in days)}"
        return every
    if t == "monthly":
        every = "monthly" if n == 1 else f"every {n} months"
        if "nth" in rule:
            return f"{every} on the {ORDINALS[rule['nth']]} {WEEKDAY_NAMES[rule['weekday']]}"
        if rule.get("day") == -1:
            return f"{every} on the last day"
        if "day" in rule:
            return f"{every} on the {_ordinal(rule['day'])}"
        return every
    if t == "yearly":
        if "month" in rule:
            return f"yearly on {calendar.month_abbr[rule['month']]} {rule['day']}"
        return "yearly"
    if t == "after":
        d = rule["days"]
        return f"{d} day{'s' if d != 1 else ''} after done"
    return t


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"
