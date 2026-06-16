"""Shared guards, group tracking, and public commands (/start, /help, /surveys)."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.constants import ChatType, ParseMode
from telegram.ext import (
    Application,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from ..database import Database

logger = logging.getLogger(__name__)

GROUP_TYPES = {ChatType.GROUP, ChatType.SUPERGROUP}

HELP_ADMIN = (
    "🤖 <b>Бот опросов — личный кабинет администратора</b>\n\n"
    "Здесь вы создаёте опросы и отправляете их в группы. В самой группе "
    "участники видят только список доступных опросов.\n\n"
    "<b>Команды (в личке):</b>\n"
    "• /newsurvey — создать опрос (выбор группы → текст → ссылка → дедлайн → напоминания)\n"
    "• /surveys — список всех опросов с кнопками (отправить, напомнить, удалить)\n"
    "• /cancel — отменить создание\n\n"
    "Бот сам опубликует ссылку в выбранной группе и будет напоминать о дедлайне.\n"
    "Время указывается в часовом поясе, заданном в настройках бота."
)

HELP_GROUP = (
    "🤖 <b>Бот опросов</b>\n\n"
    "Я сам публикую здесь опросы и напоминаю о дедлайнах.\n"
    "Создание и настройка опросов — в личке с ботом (для администраторов)."
)


def _db(context: ContextTypes.DEFAULT_TYPE) -> Database:
    return context.application.bot_data["db"]


def is_super_admin(user_id: int | None, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """True if the user is one of the configured super-admins."""
    if user_id is None:
        return False
    return user_id in context.application.bot_data["config"].super_admin_ids


def _record_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Remember a group chat (and its current title) the bot can see."""
    chat = update.effective_chat
    if chat is not None and chat.type in GROUP_TYPES:
        _db(context).upsert_group(chat.id, chat.title or str(chat.id))


# -- public commands -------------------------------------------------------


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    user = update.effective_user
    if chat is not None and chat.type in GROUP_TYPES:
        _record_group(update, context)
        await update.effective_message.reply_text(HELP_GROUP, parse_mode=ParseMode.HTML)
        return

    # Private chat: show help plus the user's id so they can be whitelisted.
    extra = ""
    if user is not None:
        recognised = "✅ у вас есть доступ" if is_super_admin(user.id, context) \
            else "🔒 нет доступа — добавьте этот ID в SUPER_ADMIN_IDS"
        extra = f"\n\nВаш Telegram ID: <code>{user.id}</code> ({recognised})."
    await update.effective_message.reply_text(
        HELP_ADMIN + extra, parse_mode=ParseMode.HTML
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start(update, context)


# -- group membership tracking ---------------------------------------------


async def on_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """React when the bot itself is added to or removed from a group."""
    result = update.my_chat_member
    if result is None or result.chat.type not in GROUP_TYPES:
        return

    new_status = result.new_chat_member.status
    old_status = result.old_chat_member.status
    was_member = old_status in ("member", "administrator", "creator")
    is_member = new_status in ("member", "administrator")

    if is_member and not was_member:
        _db(context).upsert_group(result.chat.id, result.chat.title or str(result.chat.id))
        await context.bot.send_message(
            chat_id=result.chat.id,
            text=(
                "👋 Спасибо, что добавили меня! Я буду публиковать здесь опросы и "
                "напоминать о дедлайнах. Создание опросов — в личке с ботом."
            ),
        )
    elif not is_member and was_member:
        _db(context).remove_group(result.chat.id)


async def _passive_group_record(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Keep group titles fresh by recording any group we see a message in."""
    _record_group(update, context)


def register_common_handlers(application: Application) -> None:
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(
        ChatMemberHandler(on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER)
    )
    # Passive recorder in a separate handler group so it never blocks others.
    application.add_handler(
        MessageHandler(filters.ChatType.GROUPS, _passive_group_record), group=1
    )
