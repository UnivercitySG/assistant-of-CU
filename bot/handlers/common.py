"""Shared guards, helpers and the public-facing commands (/start, /help)."""

from __future__ import annotations

import functools
import logging
from typing import Awaitable, Callable

from telegram import Chat, Update
from telegram.constants import ChatType, ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

logger = logging.getLogger(__name__)

GROUP_TYPES = {ChatType.GROUP, ChatType.SUPERGROUP}

HELP_TEXT = (
    "🤖 <b>Survey Assistant</b>\n\n"
    "Add me to a group, then an admin can set up a survey and I'll post the "
    "link and remind everyone before the deadline.\n\n"
    "<b>Admin commands (in the group):</b>\n"
    "• /newsurvey — guided setup (text → link → deadline → reminders)\n"
    "• /settext &lt;text&gt; — set the survey message text\n"
    "• /setlink &lt;url&gt; — set the survey link\n"
    "• /setdeadline &lt;when&gt; — e.g. <code>2026-06-20 18:00</code>\n"
    "• /setreminders &lt;list&gt; — e.g. <code>1d, 2h, 30m</code> before the deadline\n"
    "• /send — post the survey to the group now and arm reminders\n"
    "• /remind — send a reminder to the group immediately\n"
    "• /status — show the current survey configuration\n"
    "• /delete — remove the current survey and cancel reminders\n"
    "• /cancel — abort the guided setup\n\n"
    "Times are interpreted in the bot's configured timezone."
)


async def is_user_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """True if the acting user may manage surveys in this chat.

    A user qualifies if they are a configured super-admin or a Telegram
    administrator/creator of the current group.
    """
    user = update.effective_user
    chat = update.effective_chat
    if user is None or chat is None:
        return False

    config = context.application.bot_data["config"]
    if user.id in config.super_admin_ids:
        return True

    if chat.type not in GROUP_TYPES:
        return False

    try:
        member = await chat.get_member(user.id)
    except Exception:  # noqa: BLE001
        logger.warning("Could not fetch membership for user %s", user.id)
        return False
    return member.status in ("administrator", "creator")


def admin_command(
    handler: Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[object]]
):
    """Decorator: restrict a command to group admins used inside a group."""

    @functools.wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat = update.effective_chat
        if chat is None or chat.type not in GROUP_TYPES:
            await update.effective_message.reply_text(
                "This command only works inside a group chat. Add me to a "
                "group and try again."
            )
            return None
        if not await is_user_admin(update, context):
            await update.effective_message.reply_text(
                "🔒 Only group administrators can manage surveys."
            )
            return None
        return await handler(update, context)

    return wrapper


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(HELP_TEXT, parse_mode=ParseMode.HTML)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(HELP_TEXT, parse_mode=ParseMode.HTML)


async def on_added_to_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Greet the group when the bot itself is added as a member."""
    result = update.my_chat_member
    if result is None:
        return
    new_status = result.new_chat_member.status
    was_member = result.old_chat_member.status in ("member", "administrator", "creator")
    is_member = new_status in ("member", "administrator")
    if is_member and not was_member and result.chat.type in GROUP_TYPES:
        await context.bot.send_message(
            chat_id=result.chat.id,
            text=(
                "👋 Thanks for adding me! An admin can run /newsurvey to set up "
                "a survey, or /help to see everything I can do."
            ),
        )


def register_common_handlers(application: Application) -> None:
    from telegram.ext import ChatMemberHandler

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(
        ChatMemberHandler(on_added_to_group, ChatMemberHandler.MY_CHAT_MEMBER)
    )
