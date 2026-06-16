"""SQLite persistence layer for surveys.

The bot keeps one active survey per group chat. The data layer is intentionally
small and synchronous: SQLite calls are fast and the bot's load is light, so we
avoid the complexity of an async driver.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone

from .models import Survey

_SCHEMA = """
CREATE TABLE IF NOT EXISTS surveys (
    chat_id          INTEGER PRIMARY KEY,
    title            TEXT    NOT NULL DEFAULT '',
    link             TEXT    NOT NULL DEFAULT '',
    deadline         TEXT,
    reminder_offsets TEXT    NOT NULL DEFAULT '',
    created_by       INTEGER,
    created_at       TEXT,
    is_sent          INTEGER NOT NULL DEFAULT 0
);
"""


def _to_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat()


def _from_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _offsets_to_str(offsets: list[int]) -> str:
    return ",".join(str(int(o)) for o in offsets)


def _offsets_from_str(value: str | None) -> list[int]:
    if not value:
        return []
    return [int(part) for part in value.split(",") if part.strip()]


class Database:
    """Thin, thread-safe wrapper around a SQLite connection."""

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

    # -- mapping helpers ---------------------------------------------------

    @staticmethod
    def _row_to_survey(row: sqlite3.Row) -> Survey:
        return Survey(
            chat_id=row["chat_id"],
            title=row["title"],
            link=row["link"],
            deadline=_from_iso(row["deadline"]),
            reminder_offsets=_offsets_from_str(row["reminder_offsets"]),
            created_by=row["created_by"],
            created_at=_from_iso(row["created_at"]),
            is_sent=bool(row["is_sent"]),
        )

    # -- queries -----------------------------------------------------------

    def get_survey(self, chat_id: int) -> Survey | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM surveys WHERE chat_id = ?", (chat_id,)
            ).fetchone()
        return self._row_to_survey(row) if row else None

    def list_surveys(self) -> list[Survey]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM surveys").fetchall()
        return [self._row_to_survey(row) for row in rows]

    def save_survey(self, survey: Survey) -> None:
        """Insert or replace the survey for ``survey.chat_id``."""
        if survey.created_at is None:
            survey.created_at = datetime.now(timezone.utc)
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO surveys
                    (chat_id, title, link, deadline, reminder_offsets,
                     created_by, created_at, is_sent)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    title=excluded.title,
                    link=excluded.link,
                    deadline=excluded.deadline,
                    reminder_offsets=excluded.reminder_offsets,
                    created_by=excluded.created_by,
                    is_sent=excluded.is_sent
                """,
                (
                    survey.chat_id,
                    survey.title,
                    survey.link,
                    _to_iso(survey.deadline),
                    _offsets_to_str(survey.reminder_offsets),
                    survey.created_by,
                    _to_iso(survey.created_at),
                    int(survey.is_sent),
                ),
            )
            self._conn.commit()

    def delete_survey(self, chat_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM surveys WHERE chat_id = ?", (chat_id,)
            )
            self._conn.commit()
            return cur.rowcount > 0
