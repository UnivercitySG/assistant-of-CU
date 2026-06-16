"""Application factory: builds the bot, wires dependencies and starts polling."""

from __future__ import annotations

import logging

from telegram.ext import Application

from bot.config import config
from bot.handlers import register_handlers
from bot.handlers.private import BOT_COMMANDS
from bot.scheduler.reminders import restore_all
from bot.services.survey_service import SurveyService
from bot.state.storage import Storage

logger = logging.getLogger(__name__)


async def _post_init(app: Application) -> None:
    await app.bot.set_my_commands(BOT_COMMANDS)
    restore_all(app)  # re-arm reminders for already-sent surveys
    logger.info("Bot ready. Admins=%s group=%s", config.admin_ids, config.group_chat_id)


def build_application() -> Application:
    config.validate()

    storage = Storage(config.database_path)
    app = (
        Application.builder()
        .token(config.bot_token)
        .post_init(_post_init)
        .build()
    )

    app.bot_data["storage"] = storage
    app.bot_data["service"] = SurveyService(app, storage)

    register_handlers(app)
    return app


def run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if config.group_chat_id is None:
        logger.warning("GROUP_CHAT_ID is not set — /send will be rejected until configured.")

    app = build_application()
    logger.info("Starting polling…")
    # Only private messages + the bot's own membership changes are needed.
    app.run_polling(allowed_updates=["message", "my_chat_member"])
