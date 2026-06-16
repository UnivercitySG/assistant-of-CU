"""Data structures shared across the bot."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class Group:
    """A group chat the bot has been added to."""

    chat_id: int
    title: str = ""


@dataclass
class Survey:
    """A survey targeted at a single group chat.

    A group may have several surveys. Times are stored and handled internally
    as timezone-aware UTC ``datetime`` objects.
    """

    group_id: int
    title: str = ""
    link: str = ""
    deadline: datetime | None = None
    # Minutes *before* the deadline at which to send a reminder, e.g. [1440, 60].
    reminder_offsets: list[int] = field(default_factory=list)
    created_by: int | None = None
    created_at: datetime | None = None
    # Whether the survey link has already been posted to the group.
    is_sent: bool = False
    # Database id, assigned on first save.
    id: int | None = None

    @property
    def is_complete(self) -> bool:
        """True once the survey has the minimum fields needed to be sent."""
        return bool(self.title and self.link)

    def deadline_passed(self, now: datetime | None = None) -> bool:
        if self.deadline is None:
            return False
        now = now or datetime.now(timezone.utc)
        return self.deadline <= now

    @property
    def is_active(self) -> bool:
        """Sent, complete, and not past its deadline — visible in the group."""
        return self.is_sent and self.is_complete and not self.deadline_passed()
