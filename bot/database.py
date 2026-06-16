"""SQLite persistence layer for groups and surveys.

A group may have several surveys. The data layer is intentionally small and
synchronous: SQLite calls are fast and the bot's load is light, so we avoid the
complexity of an async driver.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone

from .models import Group, Survey

_SCHEMA = """
CREATE TABLE IF NOT EXISTS groups (
    chat_id  INTEGER PRIMARY KEY,
    title    TEXT NOT NULL DEFAULT '',
    added_at TEXT
);

CREATE TABLE IF NOT EXISTS surveys (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id         INTEGER NOT NULL,
    title            TEXT    NOT NULL DEFAULT '',
    link             TEXT    NOT NULL DEFAULT '',
    deadline         TEXT,
    reminder_offsets TEXT    NOT NULL DEFAULT '',
    created_by       INTEGER,
    created_at       TEXT,
    is_sent          INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_surveys_group ON surveys (group_id);
"""


def _to_iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat() if dt else None


def _from_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _offsets_to_str(offsets: list[int]) -> str:
    return ",".join(str(int(o)) for o in offsets)


def _offsets_from_str(value: str | None) -> list[int]:
    if not value:
        return []
    return [int(part) for part in value.split(",") if part.strip()]


class Database:
    """Thread-safe wrapper around a SQLite connection."""

    def __init__(self, path: str) -> None:
        # check_same_thread=False because python-telegram-bot may invoke
        # handlers from worker threads; a lock serialises access.
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- groups ------------------------------------------------------------

    def upsert_group(self, chat_id: int, title: str) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO groups (chat_id, title, added_at)
                VALUES (?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title
                """,
                (chat_id, title, datetime.now(timezone.utc).isoformat()),
            )
            self._conn.commit()

    def remove_group(self, chat_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM groups WHERE chat_id = ?", (chat_id,))
            self._conn.commit()

    def list_groups(self) -> list[Group]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT chat_id, title FROM groups ORDER BY title"
            ).fetchall()
        return [Group(chat_id=r["chat_id"], title=r["title"]) for r in rows]

    def get_group(self, chat_id: int) -> Group | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT chat_id, title FROM groups WHERE chat_id = ?", (chat_id,)
            ).fetchone()
        return Group(chat_id=row["chat_id"], title=row["title"]) if row else None

    # -- survey mapping ----------------------------------------------------

    @staticmethod
    def _row_to_survey(row: sqlite3.Row) -> Survey:
        return Survey(
            id=row["id"],
            group_id=row["group_id"],
            title=row["title"],
            link=row["link"],
            deadline=_from_iso(row["deadline"]),
            reminder_offsets=_offsets_from_str(row["reminder_offsets"]),
            created_by=row["created_by"],
            created_at=_from_iso(row["created_at"]),
            is_sent=bool(row["is_sent"]),
        )

    # -- survey queries ----------------------------------------------------

    def get_survey(self, survey_id: int) -> Survey | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM surveys WHERE id = ?", (survey_id,)
            ).fetchone()
        return self._row_to_survey(row) if row else None

    def list_all_surveys(self) -> list[Survey]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM surveys ORDER BY id").fetchall()
        return [self._row_to_survey(r) for r in rows]

    def list_surveys_for_group(self, group_id: int) -> list[Survey]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM surveys WHERE group_id = ? ORDER BY id", (group_id,)
            ).fetchall()
        return [self._row_to_survey(r) for r in rows]

    def save_survey(self, survey: Survey) -> Survey:
        """Insert a new survey or update an existing one (by ``survey.id``)."""
        if survey.created_at is None:
            survey.created_at = datetime.now(timezone.utc)
        with self._lock:
            if survey.id is None:
                cur = self._conn.execute(
                    """
                    INSERT INTO surveys
                        (group_id, title, link, deadline, reminder_offsets,
                         created_by, created_at, is_sent)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        survey.group_id,
                        survey.title,
                        survey.link,
                        _to_iso(survey.deadline),
                        _offsets_to_str(survey.reminder_offsets),
                        survey.created_by,
                        _to_iso(survey.created_at),
                        int(survey.is_sent),
                    ),
                )
                survey.id = cur.lastrowid
            else:
                self._conn.execute(
                    """
                    UPDATE surveys SET
                        group_id=?, title=?, link=?, deadline=?, reminder_offsets=?,
                        created_by=?, is_sent=?
                    WHERE id=?
                    """,
                    (
                        survey.group_id,
                        survey.title,
                        survey.link,
                        _to_iso(survey.deadline),
                        _offsets_to_str(survey.reminder_offsets),
                        survey.created_by,
                        int(survey.is_sent),
                        survey.id,
                    ),
                )
            self._conn.commit()
        return survey

    def delete_survey(self, survey_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM surveys WHERE id = ?", (survey_id,)
            )
            self._conn.commit()
            return cur.rowcount > 0
