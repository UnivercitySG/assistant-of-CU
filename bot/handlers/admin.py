"""Private-chat survey management for super-admins.

Admins create surveys here, pick a target group, and the bot posts the link and
reminders into that group. Group members only ever *view* surveys (handled in
``common.py``).
"""

from __future__ import annotations

import functools
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from ..database import Database
from ..models import Survey
from ..scheduler import clear_survey_jobs, schedule_survey
from ..utils import (
    ParseError,
    format_datetime,
    format_offset,
    is_valid_url,
    parse_deadline,
    parse_reminder_offsets,
)
from .common import is_super_admin

logger = logging.getLogger(__name__)

# Conversation states.
SELECT_GROUP, TEXT, LINK, DEADLINE, REMINDERS = range(5)

_SKIP_WORDS = {"skip", "none", "-", "пропустить", "нет"}


def _db(context: ContextTypes.DEFAULT_TYPE) -> Database:
    return context.application.bot_data["db"]


def _tz(context: ContextTypes.DEFAULT_TYPE):
    return context.application.bot_data["config"].timezone


def admin_dm_only(handler):
    """Restrict a handler to super-admins in a private chat."""

    @functools.wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_super_admin(update.effective_user.id if update.effective_user else None, context):
            target = update.effective_message or (
                update.callback_query.message if update.callback_query else None
            )
            if target is not None:
                await target.reply_text(
                    "🔒 Управление опросами доступно только администраторам. "
                    "Напишите /start, чтобы узнать свой ID."
                )
            return ConversationHandler.END
        return await handler(update, context)

    return wrapper


def _group_label(context: ContextTypes.DEFAULT_TYPE, group_id: int) -> str:
    group = _db(context).get_group(group_id)
    return group.title if group and group.title else str(group_id)


def _survey_summary(survey: Survey, context: ContextTypes.DEFAULT_TYPE) -> str:
    tz = _tz(context)
    rem = ", ".join(format_offset(o) for o in survey.reminder_offsets) or "—"
    return (
        f"📋 <b>Опрос #{survey.id}</b>\n"
        f"<b>Группа:</b> {_group_label(context, survey.group_id)}\n"
        f"<b>Текст:</b> {survey.title or '—'}\n"
        f"<b>Ссылка:</b> {survey.link or '—'}\n"
        f"<b>Дедлайн:</b> "
        f"{format_datetime(survey.deadline, tz) if survey.deadline else '—'}\n"
        f"<b>Напоминания:</b> {rem}\n"
        f"<b>Опубликован:</b> {'да' if survey.is_sent else 'нет'}"
    )


def _survey_buttons(survey: Survey) -> InlineKeyboardMarkup:
    row = []
    if not survey.is_sent:
        row.append(InlineKeyboardButton("📤 Отправить", callback_data=f"send:{survey.id}"))
    else:
        row.append(InlineKeyboardButton("⏰ Напомнить", callback_data=f"remind:{survey.id}"))
    row.append(InlineKeyboardButton("🗑 Удалить", callback_data=f"del:{survey.id}"))
    return InlineKeyboardMarkup([row])


# -- guided creation -------------------------------------------------------


@admin_dm_only
async def newsurvey(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    groups = _db(context).list_groups()
    if not groups:
        await update.effective_message.reply_text(
            "Сначала добавьте меня в группу — тогда я смогу отправлять туда опросы. "
            "После добавления вернитесь сюда и снова наберите /newsurvey."
        )
        return ConversationHandler.END

    context.user_data["draft"] = None
    keyboard = [
        [InlineKeyboardButton(g.title or str(g.chat_id), callback_data=f"g:{g.chat_id}")]
        for g in groups
    ]
    await update.effective_message.reply_text(
        "Для какой группы создаём опрос?",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return SELECT_GROUP


async def picked_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    group_id = int(query.data.split(":", 1)[1])
    context.user_data["draft"] = Survey(
        group_id=group_id, created_by=update.effective_user.id
    )
    await query.edit_message_text(
        f"Группа: <b>{_group_label(context, group_id)}</b>\n\n"
        "Шаг 1/4 — пришлите <b>текст</b> сообщения для участников.",
        parse_mode=ParseMode.HTML,
    )
    return TEXT


async def conv_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["draft"].title = update.effective_message.text.strip()
    await update.effective_message.reply_text(
        "Шаг 2/4 — пришлите <b>ссылку</b> на опрос (http:// или https://).",
        parse_mode=ParseMode.HTML,
    )
    return LINK


async def conv_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    link = update.effective_message.text.strip()
    if not is_valid_url(link):
        await update.effective_message.reply_text(
            "Это не похоже на ссылку. Нужен адрес, начинающийся с http:// или https://."
        )
        return LINK
    context.user_data["draft"].link = link
    await update.effective_message.reply_text(
        "Шаг 3/4 — пришлите <b>дедлайн</b> (напр. <code>2026-06-20 18:00</code>) "
        "или <code>skip</code>, если без срока.",
        parse_mode=ParseMode.HTML,
    )
    return DEADLINE


async def conv_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.effective_message.text.strip()
    draft: Survey = context.user_data["draft"]
    if text.lower() in _SKIP_WORDS:
        draft.deadline = None
        return await _finish(update, context)
    try:
        draft.deadline = parse_deadline(text, _tz(context))
    except ParseError as exc:
        await update.effective_message.reply_text(str(exc), parse_mode=ParseMode.MARKDOWN)
        return DEADLINE
    await update.effective_message.reply_text(
        "Шаг 4/4 — когда напоминать до дедлайна? Напр. <code>1d, 2h, 30m</code>, "
        "или <code>skip</code>.",
        parse_mode=ParseMode.HTML,
    )
    return REMINDERS


async def conv_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.effective_message.text.strip()
    draft: Survey = context.user_data["draft"]
    if text.lower() in _SKIP_WORDS:
        draft.reminder_offsets = []
    else:
        try:
            draft.reminder_offsets = parse_reminder_offsets(text)
        except ParseError as exc:
            await update.effective_message.reply_text(
                str(exc), parse_mode=ParseMode.MARKDOWN
            )
            return REMINDERS
    return await _finish(update, context)


async def _finish(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    draft: Survey = context.user_data.pop("draft")
    survey = _db(context).save_survey(draft)
    await update.effective_message.reply_text(
        _survey_summary(survey, context)
        + "\n\nСохранено ✅ Нажмите «Отправить», чтобы опубликовать в группе.",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
        reply_markup=_survey_buttons(survey),
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("draft", None)
    await update.effective_message.reply_text("Создание отменено.")
    return ConversationHandler.END


# -- listing & actions -----------------------------------------------------


@admin_dm_only
async def list_surveys_dm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    surveys = _db(context).list_all_surveys()
    if not surveys:
        await update.effective_message.reply_text(
            "Опросов пока нет. Создайте первый командой /newsurvey."
        )
        return
    await update.effective_message.reply_text(f"Всего опросов: {len(surveys)}")
    for survey in surveys:
        await update.effective_message.reply_text(
            _survey_summary(survey, context),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=_survey_buttons(survey),
        )


def _survey_message(survey: Survey) -> str:
    return (
        f"📣 <b>{survey.title}</b>\n\n"
        f"Пройдите, пожалуйста, опрос:\n👉 {survey.link}"
    )


@admin_dm_only
async def on_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    action, raw_id = query.data.split(":", 1)
    survey_id = int(raw_id)
    survey = _db(context).get_survey(survey_id)
    if survey is None:
        await query.answer("Опрос не найден.", show_alert=True)
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:  # noqa: BLE001
            pass
        return

    if action == "send":
        await _action_send(update, context, survey)
    elif action == "remind":
        await _action_remind(update, context, survey)
    elif action == "del":
        await _action_delete(update, context, survey)


async def _action_send(update, context, survey: Survey) -> None:
    query = update.callback_query
    if not survey.is_complete:
        await query.answer("Нужны текст и ссылка.", show_alert=True)
        return
    try:
        await context.bot.send_message(
            chat_id=survey.group_id,
            text=_survey_message(survey),
            parse_mode=ParseMode.HTML,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Failed to post survey %s", survey.id)
        await query.answer(
            "Не удалось отправить. Бот всё ещё в группе?", show_alert=True
        )
        return

    survey.is_sent = True
    _db(context).save_survey(survey)
    schedule_survey(context.application, survey)
    await query.answer("Опубликовано ✅")
    note = "📤 Опрос опубликован в группе."
    if survey.deadline:
        note += f" Напоминания включены; закроется {format_datetime(survey.deadline, _tz(context))}."
    await query.edit_message_text(
        _survey_summary(survey, context) + f"\n\n{note}",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
        reply_markup=_survey_buttons(survey),
    )


async def _action_remind(update, context, survey: Survey) -> None:
    query = update.callback_query
    if not survey.is_complete:
        await query.answer("Опрос не готов.", show_alert=True)
        return
    tail = ""
    if survey.deadline:
        tail = f"\n\nДедлайн: {format_datetime(survey.deadline, _tz(context))}."
    try:
        await context.bot.send_message(
            chat_id=survey.group_id,
            text=(
                f"⏰ <b>Напоминание</b>\n\nПройдите опрос: <b>{survey.title}</b>\n"
                f"👉 {survey.link}{tail}"
            ),
            parse_mode=ParseMode.HTML,
        )
        await query.answer("Напоминание отправлено ✅")
    except Exception:  # noqa: BLE001
        await query.answer("Не удалось отправить.", show_alert=True)


async def _action_delete(update, context, survey: Survey) -> None:
    query = update.callback_query
    clear_survey_jobs(context.application, survey.id)
    _db(context).delete_survey(survey.id)
    await query.answer("Удалено 🗑")
    await query.edit_message_text(
        f"🗑 Опрос #{survey.id} удалён, напоминания отменены."
    )


def register_admin_handlers(application: Application) -> None:
    conv = ConversationHandler(
        entry_points=[
            CommandHandler("newsurvey", newsurvey, filters=filters.ChatType.PRIVATE)
        ],
        states={
            SELECT_GROUP: [CallbackQueryHandler(picked_group, pattern=r"^g:-?\d+$")],
            TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, conv_text)],
            LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, conv_link)],
            DEADLINE: [MessageHandler(filters.TEXT & ~filters.COMMAND, conv_deadline)],
            REMINDERS: [MessageHandler(filters.TEXT & ~filters.COMMAND, conv_reminders)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_chat=True,
        per_user=True,
    )
    application.add_handler(conv)
    application.add_handler(
        CommandHandler("surveys", list_surveys_dm, filters=filters.ChatType.PRIVATE)
    )
    application.add_handler(
        CommandHandler("cancel", cancel, filters=filters.ChatType.PRIVATE)
    )
    application.add_handler(
        CallbackQueryHandler(on_action, pattern=r"^(send|remind|del):\d+$")
    )
