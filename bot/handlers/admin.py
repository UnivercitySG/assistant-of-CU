"""Admin commands for configuring, sending and reminding about surveys."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
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
from .common import admin_command, is_user_admin

logger = logging.getLogger(__name__)

# Conversation states for the guided /newsurvey flow.
TEXT, LINK, DEADLINE, REMINDERS = range(4)

_SKIP_WORDS = {"skip", "none", "-"}


def _db(context: ContextTypes.DEFAULT_TYPE) -> Database:
    return context.application.bot_data["db"]


def _tz(context: ContextTypes.DEFAULT_TYPE):
    return context.application.bot_data["config"].timezone


def _get_or_create(context: ContextTypes.DEFAULT_TYPE, update: Update) -> Survey:
    db = _db(context)
    chat_id = update.effective_chat.id
    survey = db.get_survey(chat_id)
    if survey is None:
        survey = Survey(chat_id=chat_id, created_by=update.effective_user.id)
    return survey


def _format_status(survey: Survey, context: ContextTypes.DEFAULT_TYPE) -> str:
    tz = _tz(context)
    lines = ["📋 <b>Current survey</b>", ""]
    lines.append(f"<b>Text:</b> {survey.title or '—'}")
    lines.append(f"<b>Link:</b> {survey.link or '—'}")
    lines.append(
        "<b>Deadline:</b> "
        + (format_datetime(survey.deadline, tz) if survey.deadline else "—")
    )
    if survey.reminder_offsets:
        reminders = ", ".join(format_offset(o) for o in survey.reminder_offsets)
        lines.append(f"<b>Reminders:</b> {reminders} before deadline")
    else:
        lines.append("<b>Reminders:</b> —")
    lines.append(f"<b>Posted to group:</b> {'yes' if survey.is_sent else 'no'}")
    if not survey.is_complete:
        lines.append("\n⚠️ Add at least text and a link before /send.")
    return "\n".join(lines)


# -- guided setup conversation --------------------------------------------


@admin_command
async def newsurvey(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    survey = _get_or_create(context, update)
    context.user_data["draft"] = survey
    await update.effective_message.reply_text(
        "Let's set up a survey. ✍️\n\n"
        "Step 1/4 — Send the <b>message text</b> members will see "
        "(e.g. a short call to action).",
        parse_mode=ParseMode.HTML,
    )
    return TEXT


async def _conv_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["draft"].title = update.effective_message.text.strip()
    await update.effective_message.reply_text(
        "Step 2/4 — Send the <b>survey link</b> (must start with http:// or https://).",
        parse_mode=ParseMode.HTML,
    )
    return LINK


async def _conv_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    link = update.effective_message.text.strip()
    if not is_valid_url(link):
        await update.effective_message.reply_text(
            "That doesn't look like a URL. Please send a link starting with "
            "http:// or https://."
        )
        return LINK
    context.user_data["draft"].link = link
    await update.effective_message.reply_text(
        "Step 3/4 — Send the <b>deadline</b> (e.g. <code>2026-06-20 18:00</code>), "
        "or send <code>skip</code> for no deadline.",
        parse_mode=ParseMode.HTML,
    )
    return DEADLINE


async def _conv_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.effective_message.text.strip()
    draft: Survey = context.user_data["draft"]
    if text.lower() in _SKIP_WORDS:
        draft.deadline = None
        await _finish_setup(update, context)
        return ConversationHandler.END
    try:
        draft.deadline = parse_deadline(text, _tz(context))
    except ParseError as exc:
        await update.effective_message.reply_text(str(exc), parse_mode=ParseMode.MARKDOWN)
        return DEADLINE
    await update.effective_message.reply_text(
        "Step 4/4 — When should I send <b>reminders</b> before the deadline?\n"
        "e.g. <code>1d, 2h, 30m</code>, or <code>skip</code> for none.",
        parse_mode=ParseMode.HTML,
    )
    return REMINDERS


async def _conv_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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
    await _finish_setup(update, context)
    return ConversationHandler.END


async def _finish_setup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    draft: Survey = context.user_data.pop("draft")
    _db(context).save_survey(draft)
    # If the survey was already live, refresh its scheduled jobs.
    if draft.is_sent:
        schedule_survey(context.application, draft)
    await update.effective_message.reply_text(
        _format_status(draft, context)
        + "\n\nSaved ✅ Use /send to post it to the group.",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("draft", None)
    await update.effective_message.reply_text("Setup cancelled.")
    return ConversationHandler.END


# -- direct setter commands ------------------------------------------------


@admin_command
async def set_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = " ".join(context.args).strip()
    if not text:
        await update.effective_message.reply_text("Usage: /settext <message text>")
        return
    survey = _get_or_create(context, update)
    survey.title = text
    _db(context).save_survey(survey)
    await update.effective_message.reply_text("✅ Survey text updated.")


@admin_command
async def set_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    link = " ".join(context.args).strip()
    if not is_valid_url(link):
        await update.effective_message.reply_text(
            "Usage: /setlink <url> (must start with http:// or https://)"
        )
        return
    survey = _get_or_create(context, update)
    survey.link = link
    _db(context).save_survey(survey)
    await update.effective_message.reply_text("✅ Survey link updated.")


@admin_command
async def set_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    raw = " ".join(context.args).strip()
    if not raw:
        await update.effective_message.reply_text(
            "Usage: /setdeadline <when>  e.g. /setdeadline 2026-06-20 18:00"
        )
        return
    try:
        deadline = parse_deadline(raw, _tz(context))
    except ParseError as exc:
        await update.effective_message.reply_text(str(exc), parse_mode=ParseMode.MARKDOWN)
        return
    survey = _get_or_create(context, update)
    survey.deadline = deadline
    _db(context).save_survey(survey)
    if survey.is_sent:
        schedule_survey(context.application, survey)
    await update.effective_message.reply_text(
        f"✅ Deadline set to {format_datetime(deadline, _tz(context))}."
    )


@admin_command
async def set_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    raw = " ".join(context.args).strip()
    if not raw:
        await update.effective_message.reply_text(
            "Usage: /setreminders <list>  e.g. /setreminders 1d, 2h, 30m"
        )
        return
    try:
        offsets = parse_reminder_offsets(raw)
    except ParseError as exc:
        await update.effective_message.reply_text(str(exc), parse_mode=ParseMode.MARKDOWN)
        return
    survey = _get_or_create(context, update)
    survey.reminder_offsets = offsets
    _db(context).save_survey(survey)
    if survey.is_sent:
        schedule_survey(context.application, survey)
    pretty = ", ".join(format_offset(o) for o in offsets)
    await update.effective_message.reply_text(
        f"✅ Reminders set: {pretty} before the deadline."
    )


# -- actions ---------------------------------------------------------------


def _survey_message(survey: Survey) -> str:
    return (
        f"📣 <b>{survey.title}</b>\n\n"
        f"Please take a moment to complete our survey:\n"
        f"👉 {survey.link}"
    )


@admin_command
async def send(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    survey = _db(context).get_survey(update.effective_chat.id)
    if survey is None or not survey.is_complete:
        await update.effective_message.reply_text(
            "Nothing to send yet. Set at least the text and link first "
            "(/newsurvey or /settext + /setlink)."
        )
        return

    await context.bot.send_message(
        chat_id=survey.chat_id,
        text=_survey_message(survey),
        parse_mode=ParseMode.HTML,
    )
    survey.is_sent = True
    _db(context).save_survey(survey)
    schedule_survey(context.application, survey)

    note = "📤 Survey posted to the group."
    if survey.deadline:
        note += f" Reminders armed; closes {format_datetime(survey.deadline, _tz(context))}."
    await update.effective_message.reply_text(note)


@admin_command
async def remind_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    survey = _db(context).get_survey(update.effective_chat.id)
    if survey is None or not survey.is_complete:
        await update.effective_message.reply_text(
            "No complete survey to remind about yet."
        )
        return
    tail = ""
    if survey.deadline:
        tail = f"\n\nDeadline: {format_datetime(survey.deadline, _tz(context))}."
    await context.bot.send_message(
        chat_id=survey.chat_id,
        text=(
            f"⏰ <b>Reminder</b>\n\nPlease complete the survey: "
            f"<b>{survey.title}</b>\n👉 {survey.link}{tail}"
        ),
        parse_mode=ParseMode.HTML,
    )


@admin_command
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    survey = _db(context).get_survey(update.effective_chat.id)
    if survey is None:
        await update.effective_message.reply_text(
            "No survey configured here yet. Run /newsurvey to create one."
        )
        return
    await update.effective_message.reply_text(
        _format_status(survey, context),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


@admin_command
async def delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    clear_survey_jobs(context.application, chat_id)
    if _db(context).delete_survey(chat_id):
        await update.effective_message.reply_text(
            "🗑️ Survey deleted and reminders cancelled."
        )
    else:
        await update.effective_message.reply_text("There was no survey to delete.")


def register_admin_handlers(application: Application) -> None:
    conv = ConversationHandler(
        entry_points=[CommandHandler("newsurvey", newsurvey)],
        states={
            TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, _conv_text)],
            LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, _conv_link)],
            DEADLINE: [MessageHandler(filters.TEXT & ~filters.COMMAND, _conv_deadline)],
            REMINDERS: [MessageHandler(filters.TEXT & ~filters.COMMAND, _conv_reminders)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_chat=True,
        per_user=True,
    )
    application.add_handler(conv)

    application.add_handler(CommandHandler("settext", set_text))
    application.add_handler(CommandHandler("setlink", set_link))
    application.add_handler(CommandHandler("setdeadline", set_deadline))
    application.add_handler(CommandHandler("setreminders", set_reminders))
    application.add_handler(CommandHandler("send", send))
    application.add_handler(CommandHandler("remind", remind_now))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("delete", delete))
    application.add_handler(CommandHandler("cancel", cancel))
