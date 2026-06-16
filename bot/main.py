"""Application bootstrap: build the bot, register handlers, start polling."""

from __future__ import annotations

import logging

from telegram import BotCommand
from telegram.ext import Application, ContextTypes

from .config import Config
from .database import Database
from .handlers import register_handlers
from .scheduler import reschedule_all

logger = logging.getLogger(__name__)

_COMMANDS = [
    BotCommand("newsurvey", "Guided survey setup"),
    BotCommand("settext", "Set the survey message text"),
    BotCommand("setlink", "Set the survey link"),
    BotCommand("setdeadline", "Set the deadline"),
    BotCommand("setreminders", "Set reminder offsets"),
    BotCommand("send", "Post the survey to the group"),
    BotCommand("remind", "Send a reminder now"),
    BotCommand("status", "Show the current survey"),
    BotCommand("delete", "Delete the survey"),
    BotCommand("help", "Show help"),
]


async def _post_init(application: Application) -> None:
    """Run once after the event loop starts: register commands and jobs."""
    await application.bot.set_my_commands(_COMMANDS)
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
    logger.info("Starting polling…")
    # allowed_updates includes my_chat_member so we learn when we're added
    # to a group.
    application.run_polling(allowed_updates=["message", "my_chat_member"])


if __name__ == "__main__":
    main()
