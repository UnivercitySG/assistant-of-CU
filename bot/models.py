"""Data structures shared across the bot."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class Survey:
    """A survey configured for a single group chat.

    Each group chat has at most one active survey at a time. Times are stored
    and handled internally as timezone-aware UTC ``datetime`` objects.
    """

    chat_id: int
    title: str = ""
    link: str = ""
    deadline: datetime | None = None
    # Minutes *before* the deadline at which to send a reminder, e.g. [1440, 60].
    reminder_offsets: list[int] = None  # type: ignore[assignment]
    created_by: int | None = None
    created_at: datetime | None = None
    # Whether the survey link has already been posted to the chat.
    is_sent: bool = False

    def __post_init__(self) -> None:
        if self.reminder_offsets is None:
            self.reminder_offsets = []

    @property
    def is_complete(self) -> bool:
        """True once the survey has the minimum fields needed to be sent."""
        return bool(self.title and self.link)

    def deadline_passed(self, now: datetime | None = None) -> bool:
        if self.deadline is None:
            return False
        now = now or datetime.now(timezone.utc)
        return self.deadline <= now
