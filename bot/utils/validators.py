"""Strict input validation: URLs, deadlines and reminder offsets.

Each parser either returns a normalised value or raises ``ValueError`` with a
human-friendly, admin-facing message.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from bot.config import config

# Accepted absolute datetime layouts (interpreted in the configured timezone).
_DATE_FORMATS = (
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M",
    "%d.%m.%Y %H:%M",
    "%d/%m/%Y %H:%M",
    "%Y-%m-%d",
)

# Reminder unit -> minutes. Supports the spec formats 1d / 2h / 30m (plus weeks).
_UNIT_MINUTES = {"m": 1, "min": 1, "h": 60, "hour": 60, "d": 1440, "day": 1440, "w": 10080}

_URL_RE = re.compile(r"^https?://[^\s/$.?#].[^\s]*$", re.IGNORECASE)
_REMINDER_RE = re.compile(r"^(\d+)\s*([a-zA-Z]+)$")


def is_url(text: str) -> bool:
    """Return True for a syntactically valid http(s) URL."""
    return bool(_URL_RE.match(text.strip()))


def parse_deadline(text: str) -> datetime:
    """Parse an absolute deadline into a timezone-aware UTC datetime."""
    raw = text.strip()
    if not raw:
        raise ValueError("Empty deadline.")
    for fmt in _DATE_FORMATS:
        try:
            naive = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        return naive.replace(tzinfo=config.timezone).astimezone(timezone.utc)
    raise ValueError(
        "Could not read that date. Examples: `2026-06-20 18:00` or `20.06.2026 18:00`."
    )


def parse_reminders(text: str) -> list[int]:
    """Parse a list like ``1d, 2h, 30m`` into sorted, de-duplicated minute offsets.

    The result is sorted descending (earliest reminder first) so the scheduler
    fires them in chronological order before the deadline.
    """
    offsets: set[int] = set()
    for chunk in text.replace(";", ",").split(","):
        token = chunk.strip().lower()
        if not token:
            continue
        match = _REMINDER_RE.match(token)
        if not match or match.group(2) not in _UNIT_MINUTES:
            raise ValueError(f"Could not read `{chunk.strip()}`. Use formats like `1d`, `2h`, `30m`.")
        value = int(match.group(1))
        if value <= 0:
            raise ValueError(f"Reminder `{chunk.strip()}` must be greater than zero.")
        offsets.add(value * _UNIT_MINUTES[match.group(2)])
    if not offsets:
        raise ValueError("No reminders provided. Example: `1d, 2h, 30m`.")
    return sorted(offsets, reverse=True)
