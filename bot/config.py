"""Configuration loaded once from environment variables.

A single ``Config`` instance (``config``) is imported everywhere. Loading and
validation happen at import time so the process fails fast on misconfiguration.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

try:  # optional: load a local .env during development
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover - dotenv is a convenience, not required
    pass


def _parse_ids(raw: str) -> set[int]:
    return {int(p) for p in raw.replace(";", ",").split(",") if p.strip().lstrip("-").isdigit()}


@dataclass(frozen=True)
class Config:
    bot_token: str
    admin_ids: set[int] = field(default_factory=set)
    group_chat_id: int | None = None
    timezone: ZoneInfo = ZoneInfo("UTC")
    database_path: str = "bot.db"
    send_cooldown_seconds: int = 15

    def is_admin(self, user_id: int | None) -> bool:
        return user_id is not None and user_id in self.admin_ids

    def validate(self) -> None:
        """Raise ``RuntimeError`` if the process cannot run at all."""
        if not self.bot_token:
            raise RuntimeError(
                "BOT_TOKEN is not set. Create a bot via @BotFather and export BOT_TOKEN."
            )
        if not self.admin_ids:
            raise RuntimeError(
                "No admins configured. Set ADMIN_ID (or ADMIN_IDS) to your Telegram user id."
            )


def _load() -> Config:
    tz_name = (os.environ.get("TIMEZONE", "UTC").strip() or "UTC")
    try:
        tz = ZoneInfo(tz_name)
    except Exception:  # pragma: no cover - bad tz falls back to UTC
        tz = ZoneInfo("UTC")

    group_raw = os.environ.get("GROUP_CHAT_ID", "").strip()
    group_id = int(group_raw) if group_raw.lstrip("-").isdigit() else None

    cooldown_raw = os.environ.get("SEND_COOLDOWN_SECONDS", "15").strip()
    cooldown = int(cooldown_raw) if cooldown_raw.isdigit() else 15

    return Config(
        bot_token=os.environ.get("BOT_TOKEN", "").strip(),
        admin_ids=_parse_ids(os.environ.get("ADMIN_IDS", "") or os.environ.get("ADMIN_ID", "")),
        group_chat_id=group_id,
        timezone=tz,
        database_path=os.environ.get("DATABASE_PATH", "bot.db").strip() or "bot.db",
        send_cooldown_seconds=cooldown,
    )


config = _load()
