"""Task operations. Everything that changes data goes through here, so the web
UI, the API and the Telegram bot all behave the same way."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

from . import parser, recurrence
from .db import DB


class NotFound(Exception):
    pass


FIELDS = ("title", "notes", "priority", "due_date", "due_time", "recurrence")


def _d(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


def _valid_time(t: str | None) -> str | None:
    if not t:
        return None
    hh, mm = t.split(":")[:2]
    h, m = int(hh), int(mm)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError("bad time")
    return f"{h:02d}:{m:02d}"


class Tasks:
    def __init__(self, db: DB, tz: ZoneInfo, clock: Callable[[], datetime] | None = None):
        self.db = db
        self.tz = tz
        self._clock = clock
        self.version = 0
        self.listeners: list[Callable[[], None]] = []

    # ---------- helpers ----------
    def now(self) -> datetime:
        return self._clock() if self._clock else datetime.now(self.tz)

    def today(self) -> date:
        return self.now().date()

    def _stamp(self) -> str:
        return self.now().isoformat(timespec="seconds")

    def _changed(self) -> None:
        self.version += 1
        for fn in self.listeners:
            fn()

    def _row(self, task_id: int) -> dict:
        row = self.db.one("SELECT * FROM tasks WHERE id=?", (task_id,))
        if not row:
            raise NotFound(f"task {task_id}")
        return self._decorate(dict(row))

    def get(self, task_id: int) -> dict:
        return self._row(task_id)

    def _grace_end(self, t: dict) -> date | None:
        """The day a recurring task's current occurrence counts as missed."""
        rule, due = t["recurrence"], _d(t["due_date"])
        if not rule or not due:
            return None
        if rule["type"] == "after":
            return due + timedelta(days=rule["days"])
        return recurrence.next_after(rule, _d(t["anchor"]) or due, due)

    def _decorate(self, t: dict) -> dict:
        if isinstance(t.get("recurrence"), str):
            t["recurrence"] = json.loads(t["recurrence"])
        t["recurrence_text"] = recurrence.describe(t["recurrence"])
        streak = t["streak"]
        grace = self._grace_end(t)
        if grace and self.today() >= grace:
            streak = 0  # missed an occurrence, streak is broken
        t["current_streak"] = streak
        t["subtasks"] = self.subtasks(t["id"])
        t["subtasks_done"] = sum(1 for st in t["subtasks"] if st["done"])
        return t

    def _first_due(self, rule: dict | None, due: date | None, due_time: str | None) -> tuple[date | None, date | None]:
        """Return (due_date, anchor) for a new or re-scheduled task."""
        now = self.now()
        today = now.date()
        time_passed = bool(due_time) and due_time <= now.strftime("%H:%M")
        if not rule:
            if due is None and due_time:
                due = today + timedelta(days=1) if time_passed else today
            return due, None
        anchor = due or today
        if rule["type"] == "after":
            return anchor, anchor
        first = recurrence.first_on_or_after(rule, anchor, anchor)
        if due is None and first == today and time_passed:
            first = recurrence.next_after(rule, anchor, today)
        return first, anchor

    # ---------- create / edit ----------
    def create(self, title: str, notes: str = "", priority: int = 1,
               due_date: str | date | None = None, due_time: str | None = None,
               recurrence_rule: dict | None = None) -> dict:
        title = (title or "").strip()
        if not title:
            raise ValueError("title is required")
        rule = recurrence.normalize(recurrence_rule)
        due = _d(due_date) if isinstance(due_date, str) else due_date
        due_time = _valid_time(due_time)
        due, anchor = self._first_due(rule, due, due_time)
        stamp = self._stamp()
        with self.db.tx() as c:
            cur = c.execute(
                "INSERT INTO tasks(title, notes, priority, due_date, due_time, recurrence, anchor,"
                " created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (title, notes or "", max(0, min(2, int(priority))),
                 due.isoformat() if due else None, due_time,
                 json.dumps(rule) if rule else None,
                 anchor.isoformat() if anchor else None, stamp, stamp))
        self._changed()
        return self.get(cur.lastrowid)

    def quick_add(self, text: str) -> dict:
        p = parser.parse(text, self.today())
        task = self.create(p.title, priority=p.priority, due_date=p.due_date,
                           due_time=p.due_time, recurrence_rule=p.recurrence)
        task["parsed_summary"] = p.summary()
        return task

    def update(self, task_id: int, changes: dict) -> dict:
        t = self._row(task_id)
        new = {k: changes[k] for k in FIELDS if k in changes}
        if "title" in new:
            new["title"] = (new["title"] or "").strip()
            if not new["title"]:
                raise ValueError("title is required")
        if "priority" in new:
            new["priority"] = max(0, min(2, int(new["priority"])))
        if "due_time" in new:
            new["due_time"] = _valid_time(new["due_time"])
        if "due_date" in new:
            new["due_date"] = _d(new["due_date"]).isoformat() if new["due_date"] else None
        schedule_changed = any(k in new for k in ("due_date", "recurrence", "due_time"))
        if "recurrence" in new or "due_date" in new:
            rule = recurrence.normalize(new.get("recurrence", t["recurrence"]))
            due_in = new.get("due_date", t["due_date"])
            due, anchor = self._first_due(rule, _d(due_in), new.get("due_time", t["due_time"]))
            new["recurrence"] = json.dumps(rule) if rule else None
            new["due_date"] = due.isoformat() if due else None
            new["anchor"] = anchor.isoformat() if anchor else None
        if schedule_changed:
            new["reminded_for"] = None
        if not new:
            return t
        new["updated_at"] = self._stamp()
        cols = ", ".join(f"{k}=?" for k in new)
        with self.db.tx() as c:
            c.execute(f"UPDATE tasks SET {cols} WHERE id=?", (*new.values(), task_id))
        self._changed()
        return self.get(task_id)

    # ---------- complete / skip / undo ----------
    def complete(self, task_id: int, skipped: bool = False) -> dict:
        t = self._row(task_id)
        if t["status"] != "open":
            return t
        today, stamp = self.today(), self._stamp()
        rule, due = t["recurrence"], _d(t["due_date"])
        snapshot = json.dumps({st["id"]: st["done"] for st in t["subtasks"]})
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO completions(task_id, done_at, for_date, skipped, prev_due,"
                " prev_streak, prev_best, prev_status, prev_subtasks) VALUES(?,?,?,?,?,?,?,?,?)",
                (task_id, stamp, t["due_date"], int(skipped), t["due_date"],
                 t["streak"], t["best_streak"], t["status"], snapshot))
            if not rule:
                if skipped:
                    raise ValueError("only recurring tasks can be skipped")
                c.execute("UPDATE tasks SET status='done', completed_at=?, updated_at=? WHERE id=?",
                          (stamp, stamp, task_id))
            else:
                if skipped:
                    streak = 0
                else:
                    grace = self._grace_end(t)
                    streak = t["streak"] + 1 if grace is None or today < grace else 1
                best = max(t["best_streak"], streak)
                if rule["type"] == "after":
                    nxt = today + timedelta(days=rule["days"])
                else:
                    anchor = _d(t["anchor"]) or due or today
                    nxt = recurrence.next_after(rule, anchor, max(due or today, today))
                c.execute(
                    "UPDATE tasks SET due_date=?, streak=?, best_streak=?, reminded_for=NULL,"
                    " completed_at=?, updated_at=? WHERE id=?",
                    (nxt.isoformat(), streak, best, stamp, stamp, task_id))
                # Each occurrence starts with a fresh checklist
                c.execute("UPDATE subtasks SET done=0 WHERE task_id=?", (task_id,))
        self._changed()
        return self.get(task_id)

    def skip(self, task_id: int) -> dict:
        return self.complete(task_id, skipped=True)

    def undo_complete(self, task_id: int) -> dict:
        last = self.db.one(
            "SELECT * FROM completions WHERE task_id=? ORDER BY id DESC LIMIT 1", (task_id,))
        if not last:
            raise ValueError("nothing to undo")
        with self.db.tx() as c:
            c.execute(
                "UPDATE tasks SET status=?, due_date=?, streak=?, best_streak=?,"
                " completed_at=NULL, updated_at=? WHERE id=?",
                (last["prev_status"], last["prev_due"], last["prev_streak"],
                 last["prev_best"], self._stamp(), task_id))
            for sid, done in json.loads(last["prev_subtasks"] or "{}").items():
                c.execute("UPDATE subtasks SET done=? WHERE id=? AND task_id=?",
                          (int(done), int(sid), task_id))
            c.execute("DELETE FROM completions WHERE id=?", (last["id"],))
        self._changed()
        return self.get(task_id)

    def delete(self, task_id: int) -> dict:
        t = self._row(task_id)
        with self.db.tx() as c:
            c.execute("UPDATE tasks SET status='deleted', updated_at=? WHERE id=?",
                      (self._stamp(), task_id))
        self._changed()
        t["status"] = "deleted"
        return t

    def restore(self, task_id: int) -> dict:
        self._row(task_id)
        with self.db.tx() as c:
            c.execute("UPDATE tasks SET status='open', updated_at=? WHERE id=?",
                      (self._stamp(), task_id))
        self._changed()
        return self.get(task_id)

    # ---------- subtasks ----------
    def subtasks(self, task_id: int) -> list[dict]:
        rows = self.db.query(
            "SELECT id, title, done FROM subtasks WHERE task_id=? ORDER BY position, id",
            (task_id,))
        return [{"id": r["id"], "title": r["title"], "done": bool(r["done"])} for r in rows]

    def _sub_row(self, sub_id: int):
        row = self.db.one("SELECT * FROM subtasks WHERE id=?", (sub_id,))
        if not row:
            raise NotFound(f"subtask {sub_id}")
        return row

    def add_subtasks(self, task_id: int, titles: list[str] | str) -> dict:
        """Add one or more checklist items to an existing task."""
        self._row(task_id)
        if isinstance(titles, str):
            titles = [titles]
        titles = [t.strip() for t in titles if t and t.strip()]
        if not titles:
            raise ValueError("subtask title is required")
        stamp = self._stamp()
        with self.db.tx() as c:
            pos = c.execute("SELECT COALESCE(MAX(position), 0) FROM subtasks WHERE task_id=?",
                            (task_id,)).fetchone()[0]
            for i, title in enumerate(titles, 1):
                c.execute("INSERT INTO subtasks(task_id, title, position, created_at)"
                          " VALUES(?,?,?,?)", (task_id, title, pos + i, stamp))
        self._changed()
        return self.get(task_id)

    def update_subtask(self, sub_id: int, title: str | None = None,
                       done: bool | None = None) -> dict:
        """Rename and/or tick a subtask. done=None with no title toggles it."""
        row = self._sub_row(sub_id)
        if title is not None and not title.strip():
            raise ValueError("subtask title is required")
        if done is None and title is None:
            done = not row["done"]
        with self.db.tx() as c:
            if title is not None:
                c.execute("UPDATE subtasks SET title=? WHERE id=?", (title.strip(), sub_id))
            if done is not None:
                c.execute("UPDATE subtasks SET done=? WHERE id=?", (int(done), sub_id))
        self._changed()
        return self.get(row["task_id"])

    def toggle_subtask(self, sub_id: int) -> dict:
        return self.update_subtask(sub_id)

    def delete_subtask(self, sub_id: int) -> dict:
        row = self._sub_row(sub_id)
        with self.db.tx() as c:
            c.execute("DELETE FROM subtasks WHERE id=?", (sub_id,))
        self._changed()
        return self.get(row["task_id"])

    # ---------- queries ----------
    def open_tasks(self) -> list[dict]:
        rows = self.db.query(
            "SELECT * FROM tasks WHERE status='open' ORDER BY"
            " due_date IS NULL, due_date, due_time IS NULL, due_time, priority DESC, id")
        return [self._decorate(dict(r)) for r in rows]

    def board(self, upcoming_days: int = 7) -> dict:
        today = self.today()
        horizon = today + timedelta(days=upcoming_days)
        out = {"overdue": [], "today": [], "upcoming": [], "later": [], "anytime": []}
        for t in self.open_tasks():
            d = _d(t["due_date"])
            if d is None:
                out["anytime"].append(t)
            elif d < today:
                out["overdue"].append(t)
            elif d == today:
                out["today"].append(t)
            elif d <= horizon:
                out["upcoming"].append(t)
            else:
                out["later"].append(t)
        for key in ("overdue", "today", "anytime"):
            out[key].sort(key=lambda t: (-t["priority"], t["due_date"] or "",
                                         t["due_time"] or "99", t["id"]))
        out["done_today"] = self.done_on(today)
        now = self.now()
        out["now"] = now.isoformat(timespec="seconds")
        out["today_date"] = today.isoformat()
        out["version"] = self.version
        return out

    def done_on(self, day: date) -> list[dict]:
        rows = self.db.query(
            "SELECT c.id AS completion_id, c.skipped, c.done_at, t.* FROM completions c"
            " JOIN tasks t ON t.id=c.task_id WHERE c.done_at >= ? AND c.done_at < ?"
            " AND t.status != 'deleted' ORDER BY c.done_at DESC",
            (day.isoformat(), (day + timedelta(days=1)).isoformat()))
        return [self._decorate(dict(r)) for r in rows]

    def history(self, task_id: int, limit: int = 60) -> list[dict]:
        rows = self.db.query(
            "SELECT done_at, for_date, skipped FROM completions WHERE task_id=?"
            " ORDER BY id DESC LIMIT ?", (task_id, limit))
        return [dict(r) for r in rows]

    # ---------- reminders ----------
    def due_reminders(self, lead_minutes: int = 0, max_late_hours: int = 6) -> list[dict]:
        """Open timed tasks whose reminder time has arrived and haven't been pinged."""
        now = self.now().replace(tzinfo=None)
        out = []
        for t in self.open_tasks():
            if not (t["due_date"] and t["due_time"]):
                continue
            key = f"{t['due_date']}T{t['due_time']}"
            if t["reminded_for"] == key:
                continue
            due_dt = datetime.fromisoformat(key)
            if due_dt - timedelta(minutes=lead_minutes) <= now <= due_dt + timedelta(hours=max_late_hours):
                out.append(t)
        return out

    def mark_reminded(self, task_id: int, key: str) -> None:
        with self.db.tx() as c:
            c.execute("UPDATE tasks SET reminded_for=? WHERE id=?", (key, task_id))
