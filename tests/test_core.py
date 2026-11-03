from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app import recurrence as r
from app.db import DB
from app.parser import parse
from app.tasks import Tasks

TZ = ZoneInfo("Europe/London")
MON = date(2026, 11, 2)  # a Monday


class Clock:
    def __init__(self, dt):
        self.dt = dt

    def __call__(self):
        return self.dt

    def days(self, n):
        self.dt += timedelta(days=n)


@pytest.fixture
def env():
    clock = Clock(datetime(2026, 11, 2, 10, 0, tzinfo=TZ))
    return Tasks(DB(":memory:"), TZ, clock), clock


# ---------- recurrence ----------
def n(rule):
    return r.normalize(rule)


def test_daily_interval():
    rule = n({"type": "daily", "interval": 3})
    assert r.next_after(rule, MON, MON) == MON + timedelta(days=3)
    assert r.first_on_or_after(rule, MON, MON + timedelta(days=1)) == MON + timedelta(days=3)


def test_weekly_days_and_interval():
    rule = n({"type": "weekly", "days": [1, 4]})  # Tue, Fri
    assert r.next_after(rule, MON, MON) == date(2026, 11, 3)
    assert r.next_after(rule, MON, date(2026, 11, 3)) == date(2026, 11, 6)
    biweekly = n({"type": "weekly", "interval": 2, "days": [0]})
    assert r.next_after(biweekly, MON, MON) == date(2026, 11, 16)


def test_monthly_variants():
    assert r.next_after(n({"type": "monthly", "day": 31}), date(2026, 1, 31), date(2026, 1, 31)) == date(2026, 2, 28)
    assert r.next_after(n({"type": "monthly", "day": -1}), MON, MON) == date(2026, 11, 30)
    second_tue = n({"type": "monthly", "nth": 2, "weekday": 1})
    assert r.next_after(second_tue, MON, MON) == date(2026, 11, 10)
    assert r.next_after(second_tue, MON, date(2026, 11, 10)) == date(2026, 12, 8)
    last_fri = n({"type": "monthly", "nth": -1, "weekday": 4})
    assert r.next_after(last_fri, MON, MON) == date(2026, 11, 27)
    quarterly = n({"type": "monthly", "interval": 3, "day": 20})
    assert r.next_after(quarterly, date(2026, 11, 20), date(2026, 11, 20)) == date(2027, 2, 20)


def test_yearly_leap_day():
    rule = n({"type": "yearly", "month": 2, "day": 29})
    assert r.next_after(rule, MON, MON) == date(2027, 2, 28)


def test_bad_rules():
    for bad in ({"type": "nope"}, {"type": "weekly", "days": [9]}, {"type": "monthly", "day": 40}):
        with pytest.raises(r.RuleError):
            r.normalize(bad)


# ---------- parser ----------
@pytest.mark.parametrize("text,title,due,time,rule", [
    ("call dentist tomorrow at 3pm !!", "call dentist", date(2026, 11, 3), "15:00", None),
    ("water plants every 3 days", "water plants", None, None, {"type": "daily", "interval": 3}),
    ("change filter 30 days after done", "change filter", None, None, {"type": "after", "days": 30}),
    ("standup every weekday at 9:30", "standup", None, "09:30", {"type": "weekly", "days": [0, 1, 2, 3, 4]}),
    ("bins every tue and fri at 7pm", "bins", None, "19:00", {"type": "weekly", "interval": 1, "days": [1, 4]}),
    ("review budget every last sunday", "review budget", None, None, {"type": "monthly", "nth": -1, "weekday": 6}),
    ("fix the monday report", "fix the monday report", None, None, None),
    ("meet at 5", "meet", None, "17:00", None),
    ("dentist friday", "dentist", date(2026, 11, 6), None, None),
])
def test_parser(text, title, due, time, rule):
    p = parse(text, MON)
    assert (p.title, p.due_date, p.due_time, p.recurrence) == (title, due, time, rule)


# ---------- task lifecycle ----------
def test_daily_streak_and_break(env):
    tasks, clock = env
    t = tasks.quick_add("meditate daily")
    assert t["due_date"] == "2026-11-02"
    for i in range(3):
        t = tasks.complete(t["id"])
        clock.days(1)
    assert t["streak"] == 3 and t["due_date"] == "2026-11-05"
    clock.days(1)  # now 6th, the 5th was missed
    assert tasks.get(t["id"])["current_streak"] == 0
    t = tasks.complete(t["id"])
    assert t["streak"] == 1 and t["best_streak"] == 3
    assert t["due_date"] == "2026-11-07"  # next occurrence after today, not after the old due date


def test_after_completion(env):
    tasks, clock = env
    t = tasks.quick_add("change filter 30 days after done")
    clock.days(5)
    t = tasks.complete(t["id"])
    assert t["due_date"] == "2026-12-07"


def test_weekly_completed_early_and_skip(env):
    tasks, clock = env
    t = tasks.quick_add("bins every fri")
    assert t["due_date"] == "2026-11-06"
    t = tasks.complete(t["id"])  # done early on Monday
    assert t["due_date"] == "2026-11-13"
    t = tasks.skip(t["id"])
    assert t["due_date"] == "2026-11-20" and t["streak"] == 0


def test_undo_restores_state(env):
    tasks, _ = env
    t = tasks.quick_add("stretch daily")
    tasks.complete(t["id"])
    t = tasks.undo_complete(t["id"])
    assert t["due_date"] == "2026-11-02" and t["streak"] == 0
    one = tasks.quick_add("buy milk")
    assert tasks.complete(one["id"])["status"] == "done"
    assert tasks.undo_complete(one["id"])["status"] == "open"


def test_time_already_passed_rolls_forward(env):
    tasks, _ = env  # it's 10:00
    assert tasks.quick_add("call at 9am")["due_date"] == "2026-11-03"
    assert tasks.quick_add("call at 11am")["due_date"] == "2026-11-02"
    assert tasks.quick_add("pills daily at 8am")["due_date"] == "2026-11-03"


def test_board_sections(env):
    tasks, clock = env
    tasks.quick_add("a today")
    tasks.quick_add("b tomorrow")
    tasks.quick_add("c")
    tasks.quick_add("d in 3 weeks")
    old = tasks.quick_add("e today")
    clock.days(2)
    b = tasks.board(7)
    assert [t["title"] for t in b["overdue"]] == ["a", "e", "b"]
    assert [len(b[k]) for k in ("today", "upcoming", "later", "anytime")] == [0, 0, 1, 1]
    tasks.complete(old["id"])
    assert len(tasks.board(7)["done_today"]) == 1


def test_reminders(env):
    tasks, clock = env
    t = tasks.quick_add("call at 10:30")
    assert tasks.due_reminders() == []
    clock.dt = clock.dt.replace(minute=30)
    due = tasks.due_reminders()
    assert [x["id"] for x in due] == [t["id"]]
    tasks.mark_reminded(t["id"], "2026-11-02T10:30")
    assert tasks.due_reminders() == []
    assert [x["id"] for x in tasks.due_reminders(lead_minutes=0)] == []


def test_update_reschedules(env):
    tasks, _ = env
    t = tasks.quick_add("thing")
    t = tasks.update(t["id"], {"recurrence": {"type": "weekly", "days": [3]}})
    assert t["due_date"] == "2026-11-05" and t["recurrence_text"] == "weekly on Thu"
    t = tasks.update(t["id"], {"recurrence": None, "due_date": None})
    assert t["due_date"] is None and t["recurrence"] is None


# ---------- subtasks ----------
def test_subtasks_crud(env):
    tasks, _ = env
    t = tasks.quick_add("paint fence")
    t = tasks.add_subtasks(t["id"], ["buy paint", " ", "sand"])
    assert [s["title"] for s in t["subtasks"]] == ["buy paint", "sand"]
    assert t["subtasks_done"] == 0
    sid = t["subtasks"][0]["id"]
    t = tasks.toggle_subtask(sid)
    assert t["subtasks"][0]["done"] and t["subtasks_done"] == 1
    t = tasks.update_subtask(sid, title="buy green paint")
    assert t["subtasks"][0] == {"id": sid, "title": "buy green paint", "done": True}
    t = tasks.add_subtasks(t["id"], "brushes")
    assert [s["title"] for s in t["subtasks"]][-1] == "brushes"
    t = tasks.delete_subtask(sid)
    assert len(t["subtasks"]) == 2
    with pytest.raises(ValueError):
        tasks.add_subtasks(t["id"], ["", "  "])


def test_recurring_checklist_resets_and_undo_restores(env):
    tasks, _ = env
    t = tasks.quick_add("weekly review every fri")
    t = tasks.add_subtasks(t["id"], ["inbox zero", "plan week"])
    tasks.toggle_subtask(t["subtasks"][0]["id"])
    t = tasks.complete(t["id"])
    assert t["subtasks_done"] == 0  # fresh checklist for next occurrence
    t = tasks.undo_complete(t["id"])
    assert [s["done"] for s in t["subtasks"]] == [True, False]


def test_one_off_keeps_checklist_and_board_includes_it(env):
    tasks, _ = env
    t = tasks.quick_add("pack today")
    t = tasks.add_subtasks(t["id"], ["passport"])
    tasks.toggle_subtask(t["subtasks"][0]["id"])
    b = tasks.board(7)
    assert b["today"][0]["subtasks"][0]["done"]
    t = tasks.complete(t["id"])
    assert t["subtasks_done"] == 1


def test_migration_adds_column(tmp_path):
    import sqlite3
    path = str(tmp_path / "old.db")
    old = sqlite3.connect(path)
    old.executescript("CREATE TABLE completions (id INTEGER PRIMARY KEY, task_id INTEGER, done_at TEXT,"
                      " for_date TEXT, skipped INTEGER, prev_due TEXT, prev_streak INTEGER,"
                      " prev_best INTEGER, prev_status TEXT);")
    old.close()
    db = DB(path)
    cols = {r["name"] for r in db.query("PRAGMA table_info(completions)")}
    assert "prev_subtasks" in cols
