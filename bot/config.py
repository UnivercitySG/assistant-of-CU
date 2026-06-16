"""Runtime configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv is optional in production deployments.
    pass


def _parse_ids(raw: str) -> set[int]:
    ids: set[int] = set()
    for chunk in raw.replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            ids.add(int(chunk))
        except ValueError:
            raise ValueError(f"Invalid user id in SUPER_ADMIN_IDS: {chunk!r}")
    return ids


@dataclass(frozen=True)
class Config:
    """Validated application configuration."""

    bot_token: str
    super_admin_ids: set[int] = field(default_factory=set)
    database_path: str = "bot.db"
    timezone_name: str = "UTC"

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)

    @classmethod
    def from_env(cls) -> "Config":
        token = os.environ.get("BOT_TOKEN", "").strip()
        if not token:
            raise RuntimeError(
                "BOT_TOKEN is not set. Create a bot with @BotFather and put the "
                "token in your environment or a .env file (see .env.example)."
            )

        tz_name = os.environ.get("TIMEZONE", "UTC").strip() or "UTC"
        try:
            ZoneInfo(tz_name)
        except ZoneInfoNotFoundError:
            raise RuntimeError(
                f"Unknown TIMEZONE {tz_name!r}. Use an IANA name like 'Asia/Almaty'."
            )

        return cls(
            bot_token=token,
            super_admin_ids=_parse_ids(os.environ.get("SUPER_ADMIN_IDS", "")),
            database_path=os.environ.get("DATABASE_PATH", "bot.db").strip() or "bot.db",
            timezone_name=tz_name,
        )
