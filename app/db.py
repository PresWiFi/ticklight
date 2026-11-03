import os
import sqlite3
import threading
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id            INTEGER PRIMARY KEY,
    title         TEXT NOT NULL,
    notes         TEXT NOT NULL DEFAULT '',
    priority      INTEGER NOT NULL DEFAULT 1,      -- 0 low, 1 normal, 2 high
    due_date      TEXT,                            -- YYYY-MM-DD
    due_time      TEXT,                            -- HH:MM
    recurrence    TEXT,                            -- JSON rule, see recurrence.py
    anchor        TEXT,                            -- date recurrence is anchored to
    status        TEXT NOT NULL DEFAULT 'open',    -- open | done | deleted
    streak        INTEGER NOT NULL DEFAULT 0,
    best_streak   INTEGER NOT NULL DEFAULT 0,
    reminded_for  TEXT,                            -- due datetime we already pinged for
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    completed_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_status_due ON tasks(status, due_date);

CREATE TABLE IF NOT EXISTS completions (
    id           INTEGER PRIMARY KEY,
    task_id      INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    done_at      TEXT NOT NULL,
    for_date     TEXT,                             -- the due date this completed
    skipped      INTEGER NOT NULL DEFAULT 0,
    prev_due     TEXT,                             -- state before, for undo
    prev_streak  INTEGER,
    prev_best    INTEGER,
    prev_status  TEXT
);
CREATE INDEX IF NOT EXISTS idx_completions_task ON completions(task_id, id);
CREATE INDEX IF NOT EXISTS idx_completions_done ON completions(done_at);

CREATE TABLE IF NOT EXISTS subtasks (
    id          INTEGER PRIMARY KEY,
    task_id     INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    done        INTEGER NOT NULL DEFAULT 0,
    position    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_subtasks_task ON subtasks(task_id, position);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


class DB:
    def __init__(self, path: str):
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.lock = threading.RLock()

    def _migrate(self) -> None:
        """Bring databases created by older versions up to date."""
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(completions)")}
        if "prev_subtasks" not in cols:
            # JSON {subtask_id: done} so undoing a completion restores the checklist
            self.conn.execute("ALTER TABLE completions ADD COLUMN prev_subtasks TEXT")

    @contextmanager
    def tx(self):
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
                self.conn.execute("COMMIT")
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise

    def query(self, sql: str, args=()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, args).fetchall()

    def one(self, sql: str, args=()) -> sqlite3.Row | None:
        with self.lock:
            return self.conn.execute(sql, args).fetchone()

    def get_meta(self, key: str) -> str | None:
        row = self.one("SELECT value FROM meta WHERE key=?", (key,))
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self.lock:
            self.conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
