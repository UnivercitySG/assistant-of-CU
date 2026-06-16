"""SQLite-backed survey storage.

A survey row holds both draft state (FSM step, fields being filled) and, once
published, its delivery metadata. Each admin has at most one active draft;
published surveys are retained as history.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone

from bot.state.fsm import Step

STATUS_DRAFT = "draft"
STATUS_SENT = "sent"


@dataclass
class Survey:
    admin_id: int
    id: int | None = None
    text: str = ""
    link: str = ""
    deadline: datetime | None = None
    reminders: list[int] = field(default_factory=list)  # minutes before deadline
    step: Step = Step.TEXT
    status: str = STATUS_DRAFT
    group_chat_id: int | None = None
    message_id: int | None = None
    created_at: datetime | None = None
    sent_at: datetime | None = None

    @property
    def is_complete(self) -> bool:
        """A survey can be previewed/sent once it has text and a link."""
        return bool(self.text and self.link)

    @property
    def is_sent(self) -> bool:
        return self.status == STATUS_SENT


def _to_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _from_iso(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class Storage:
    def __init__(self, path: str):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS surveys(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER NOT NULL,
                text TEXT NOT NULL DEFAULT '',
                link TEXT NOT NULL DEFAULT '',
                deadline TEXT,
                reminders TEXT NOT NULL DEFAULT '',
                step TEXT NOT NULL DEFAULT 'TEXT',
                status TEXT NOT NULL DEFAULT 'draft',
                group_chat_id INTEGER,
                message_id INTEGER,
                created_at TEXT,
                sent_at TEXT)"""
        )
        self._conn.commit()

    # -- mapping ----------------------------------------------------------
    @staticmethod
    def _row_to_survey(row: sqlite3.Row) -> Survey:
        reminders = [int(x) for x in row["reminders"].split(",") if x.strip()]
        try:
            step = Step[row["step"]]
        except KeyError:
            step = Step.TEXT
        return Survey(
            admin_id=row["admin_id"],
            id=row["id"],
            text=row["text"],
            link=row["link"],
            deadline=_from_iso(row["deadline"]),
            reminders=reminders,
            step=step,
            status=row["status"],
            group_chat_id=row["group_chat_id"],
            message_id=row["message_id"],
            created_at=_from_iso(row["created_at"]),
            sent_at=_from_iso(row["sent_at"]),
        )

    # -- queries ----------------------------------------------------------
    def get_draft(self, admin_id: int) -> Survey | None:
        row = self._conn.execute(
            "SELECT * FROM surveys WHERE admin_id=? AND status=? ORDER BY id DESC LIMIT 1",
            (admin_id, STATUS_DRAFT),
        ).fetchone()
        return self._row_to_survey(row) if row else None

    def get_last_sent(self, admin_id: int) -> Survey | None:
        row = self._conn.execute(
            "SELECT * FROM surveys WHERE admin_id=? AND status=? ORDER BY id DESC LIMIT 1",
            (admin_id, STATUS_SENT),
        ).fetchone()
        return self._row_to_survey(row) if row else None

    def history(self, admin_id: int, limit: int = 5) -> list[Survey]:
        rows = self._conn.execute(
            "SELECT * FROM surveys WHERE admin_id=? AND status=? ORDER BY id DESC LIMIT ?",
            (admin_id, STATUS_SENT, limit),
        ).fetchall()
        return [self._row_to_survey(r) for r in rows]

    def all_sent(self) -> list[Survey]:
        rows = self._conn.execute(
            "SELECT * FROM surveys WHERE status=?", (STATUS_SENT,)
        ).fetchall()
        return [self._row_to_survey(r) for r in rows]

    # -- mutations --------------------------------------------------------
    def save(self, survey: Survey) -> Survey:
        if survey.created_at is None:
            survey.created_at = datetime.now(timezone.utc)
        values = (
            survey.admin_id,
            survey.text,
            survey.link,
            _to_iso(survey.deadline),
            ",".join(map(str, survey.reminders)),
            survey.step.name,
            survey.status,
            survey.group_chat_id,
            survey.message_id,
            _to_iso(survey.created_at),
            _to_iso(survey.sent_at),
        )
        if survey.id is None:
            cur = self._conn.execute(
                """INSERT INTO surveys
                   (admin_id, text, link, deadline, reminders, step, status,
                    group_chat_id, message_id, created_at, sent_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                values,
            )
            survey.id = cur.lastrowid
        else:
            self._conn.execute(
                """UPDATE surveys SET admin_id=?, text=?, link=?, deadline=?, reminders=?,
                   step=?, status=?, group_chat_id=?, message_id=?, created_at=?, sent_at=?
                   WHERE id=?""",
                values + (survey.id,),
            )
        self._conn.commit()
        return survey

    def delete_draft(self, admin_id: int) -> bool:
        cur = self._conn.execute(
            "DELETE FROM surveys WHERE admin_id=? AND status=?", (admin_id, STATUS_DRAFT)
        )
        self._conn.commit()
        return cur.rowcount > 0
