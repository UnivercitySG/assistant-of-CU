"""Application bootstrap: build the bot, register handlers, start polling."""

from __future__ import annotations

import logging

from telegram import BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats
from telegram.ext import Application, ContextTypes

from .config import Config
from .database import Database
from .handlers import register_handlers
from .scheduler import reschedule_all

logger = logging.getLogger(__name__)

# Commands shown in the menu for admins in private chat.
_PRIVATE_COMMANDS = [
    BotCommand("newsurvey", "Создать опрос"),
    BotCommand("surveys", "Список опросов"),
    BotCommand("cancel", "Отменить создание"),
    BotCommand("help", "Помощь"),
]

# Groups only ever see informational help — surveys are posted automatically.
_GROUP_COMMANDS = [BotCommand("help", "Помощь")]


async def _post_init(application: Application) -> None:
    """Run once after the event loop starts: register commands and jobs."""
    await application.bot.set_my_commands(
        _PRIVATE_COMMANDS, scope=BotCommandScopeAllPrivateChats()
    )
    await application.bot.set_my_commands(
        _GROUP_COMMANDS, scope=BotCommandScopeAllGroupChats()
    )
    reschedule_all(application)
    logger.info("Bot ready. Reminder jobs restored from the database.")


async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled error while processing update", exc_info=context.error)


def build_application(config: Config | None = None) -> Application:
    """Construct a fully wired :class:`Application` (without running it)."""
    config = config or Config.from_env()

    application = (
        Application.builder()
        .token(config.bot_token)
        .post_init(_post_init)
        .build()
    )

    application.bot_data["config"] = config
    application.bot_data["db"] = Database(config.database_path)

    register_handlers(application)
    application.add_error_handler(_on_error)
    return application


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    # Quiet the noisy HTTP client logger.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    application = build_application()
    if not application.bot_data["config"].super_admin_ids:
        logger.warning(
            "SUPER_ADMIN_IDS is empty — nobody can create surveys yet. "
            "Message the bot with /start to get your Telegram ID, then add it "
            "to SUPER_ADMIN_IDS and restart."
        )
    logger.info("Starting polling…")
    # allowed_updates includes my_chat_member (so we learn when we're added to
    # a group) and callback_query (for the inline action buttons).
    application.run_polling(
        allowed_updates=["message", "callback_query", "my_chat_member"]
    )


if __name__ == "__main__":
    main()
