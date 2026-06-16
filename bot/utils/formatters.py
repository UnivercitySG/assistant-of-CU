"""Formatting helpers for admin replies and the public survey post."""

from __future__ import annotations

from datetime import datetime, timezone

from bot.config import config

# Characters that must be escaped for Telegram legacy Markdown.
_MD_SPECIALS = ("\\", "_", "*", "`", "[")


def escape_md(text: str) -> str:
    """Escape user text for Telegram legacy Markdown (``ParseMode.MARKDOWN``)."""
    for ch in _MD_SPECIALS:
        text = text.replace(ch, "\\" + ch)
    return text


def fmt_offset(minutes: int) -> str:
    """Render a minute offset back into a compact ``1d 2h 30m`` string."""
    parts: list[str] = []
    for unit, size in (("w", 10080), ("d", 1440), ("h", 60), ("m", 1)):
        if minutes >= size:
            value, minutes = divmod(minutes, size)
            parts.append(f"{value}{unit}")
    return " ".join(parts) or "0m"


def fmt_dt(dt: datetime | None) -> str:
    """Render a UTC datetime in the configured local timezone."""
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(config.timezone).strftime("%Y-%m-%d %H:%M %Z")


def render_post(text: str, link: str, deadline: datetime | None) -> str:
    """Build the public survey message exactly as it will appear in the group."""
    lines = [f"📣 *{escape_md(text)}*", "", "📋 Take the survey:", f"👉 {link}"]
    if deadline is not None:
        lines += ["", f"🕒 Deadline: *{fmt_dt(deadline)}*"]
    return "\n".join(lines)


def render_reminder(text: str, link: str, deadline: datetime | None, offset_minutes: int) -> str:
    """Build a reminder message fired before the deadline."""
    lines = [
        "⏰ *Reminder*",
        "",
        f"Please complete the survey: *{escape_md(text)}*",
        f"👉 {link}",
    ]
    if deadline is not None:
        lines += ["", f"Closes in *{fmt_offset(offset_minutes)}* (at {fmt_dt(deadline)})."]
    return "\n".join(lines)
