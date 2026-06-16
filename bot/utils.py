"""Parsing and formatting helpers."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# Accepted deadline formats, interpreted in the configured local timezone.
_DEADLINE_FORMATS = (
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%d.%m.%Y %H:%M",
    "%d/%m/%Y %H:%M",
    "%Y-%m-%d",  # midnight on the given day
)

# Maps a duration unit (and common aliases) to a number of minutes.
_UNIT_MINUTES = {
    "m": 1,
    "min": 1,
    "mins": 1,
    "minute": 1,
    "minutes": 1,
    "h": 60,
    "hr": 60,
    "hrs": 60,
    "hour": 60,
    "hours": 60,
    "d": 60 * 24,
    "day": 60 * 24,
    "days": 60 * 24,
    "w": 60 * 24 * 7,
    "week": 60 * 24 * 7,
    "weeks": 60 * 24 * 7,
}

_DURATION_RE = re.compile(r"^\s*(\d+)\s*([a-zA-Z]+)\s*$")


class ParseError(ValueError):
    """Raised when user input cannot be parsed."""


def parse_deadline(text: str, tz: ZoneInfo) -> datetime:
    """Parse a human-entered deadline into a UTC ``datetime``.

    The input is interpreted in ``tz`` (the bot's configured timezone) and
    converted to UTC for storage.
    """
    text = text.strip()
    for fmt in _DEADLINE_FORMATS:
        try:
            naive = datetime.strptime(text, fmt)
        except ValueError:
            continue
        local = naive.replace(tzinfo=tz)
        return local.astimezone(timezone.utc)
    raise ParseError(
        "I couldn't read that date. Use a format like "
        "`2026-06-20 18:00` or `20.06.2026 18:00`."
    )


def parse_reminder_offsets(text: str) -> list[int]:
    """Parse reminder offsets like ``"1d, 2h, 30m"`` into minutes-before values.

    Returns a sorted (descending) list of unique positive offsets in minutes.
    """
    offsets: set[int] = set()
    for chunk in text.replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        match = _DURATION_RE.match(chunk)
        if not match:
            raise ParseError(
                f"Couldn't read reminder {chunk!r}. Use values like "
                "`1d`, `2h`, `30m` separated by commas."
            )
        amount, unit = int(match.group(1)), match.group(2).lower()
        if unit not in _UNIT_MINUTES:
            raise ParseError(
                f"Unknown time unit {unit!r}. Use m, h, d, or w."
            )
        minutes = amount * _UNIT_MINUTES[unit]
        if minutes <= 0:
            raise ParseError("Reminder offsets must be greater than zero.")
        offsets.add(minutes)
    if not offsets:
        raise ParseError("No reminders provided.")
    return sorted(offsets, reverse=True)


def is_valid_url(text: str) -> bool:
    return bool(re.match(r"^https?://\S+$", text.strip(), re.IGNORECASE))


def format_offset(minutes: int) -> str:
    """Render a minutes value as a compact human string, e.g. ``"1d 2h"``."""
    parts: list[str] = []
    for unit, size in (("w", 60 * 24 * 7), ("d", 60 * 24), ("h", 60), ("m", 1)):
        if minutes >= size:
            value, minutes = divmod(minutes, size)
            parts.append(f"{value}{unit}")
    return " ".join(parts) if parts else "0m"


def format_datetime(dt: datetime, tz: ZoneInfo) -> str:
    """Render a UTC datetime in the configured local timezone."""
    return dt.astimezone(tz).strftime("%Y-%m-%d %H:%M %Z")
