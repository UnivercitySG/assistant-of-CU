"""Private-chat, admin-only command and message handlers.

Chat separation and access control are enforced at registration time via
message filters (``ChatType.PRIVATE`` + ``User(admin_ids)``), so every handler
here can assume it was triggered by an admin in a private chat. Anything sent in
a group, or by a non-admin, never reaches these functions.
"""

from __future__ import annotations

import logging

from telegram import BotCommand, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bot.config import config
from bot.services.survey_service import SurveyError, SurveyService
from bot.state.fsm import PROMPTS, SKIP_TOKENS, Step
from bot.state.storage import Survey
from bot.utils.formatters import escape_md, fmt_dt, fmt_offset, render_post
from bot.utils.validators import is_url, parse_deadline, parse_reminders

logger = logging.getLogger(__name__)

HELP_TEXT = (
    "🤖 *Survey broadcasting bot*\n\n"
    "Build a survey here in private, then publish it to the group.\n\n"
    "*Guided setup*\n"
    "• /newsurvey — step-by-step (text → link → deadline → reminders)\n\n"
    "*Direct setters*\n"
    "• /settext `<text>`\n"
    "• /setlink `<url>`\n"
    "• /setdeadline `<2026-06-20 18:00>`\n"
    "• /setreminders `<1d, 2h, 30m>`\n\n"
    "*Review & publish*\n"
    "• /preview — see the exact post\n"
    "• /status — current survey state\n"
    "• /send — publish (asks for /confirm first)\n"
    "• /confirm — confirm sending\n"
    "• /cancel — discard the current survey\n\n"
    "*Extras*\n"
    "• /history — recent surveys\n"
    "• /duplicate — clone the last survey"
)

BOT_COMMANDS = [
    BotCommand("newsurvey", "Start a new survey (guided)"),
    BotCommand("settext", "Set survey text"),
    BotCommand("setlink", "Set survey link"),
    BotCommand("setdeadline", "Set deadline"),
    BotCommand("setreminders", "Set reminder times"),
    BotCommand("preview", "Preview the post"),
    BotCommand("status", "Show current state"),
    BotCommand("send", "Publish to the group"),
    BotCommand("confirm", "Confirm sending"),
    BotCommand("cancel", "Discard current survey"),
    BotCommand("history", "Recent surveys"),
    BotCommand("duplicate", "Clone the last survey"),
    BotCommand("help", "Show help"),
]

_AWAITING = "awaiting_step"   # user_data key: which Step the guided flow expects
_PENDING_SEND = "pending_send"  # user_data key: /send awaiting /confirm


def _service(ctx: ContextTypes.DEFAULT_TYPE) -> SurveyService:
    return ctx.application.bot_data["service"]


async def _reply(update: Update, text: str, *, preview: bool = False) -> None:
    await update.effective_message.reply_text(
        text, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=not preview
    )


def _status_text(survey: Survey | None) -> str:
    if survey is None:
        return "No active survey. Start one with /newsurvey."
    reminders = ", ".join(fmt_offset(o) for o in survey.reminders) or "—"
    lines = [
        "📋 *Current survey*",
        "",
        f"*Step:* {survey.step.label}",
        f"*Text:* {escape_md(survey.text) if survey.text else '—'}",
        f"*Link:* {survey.link or '—'}",
        f"*Deadline:* {fmt_dt(survey.deadline)}",
        f"*Reminders:* {reminders}",
        f"*Published:* {'yes' if survey.is_sent else 'no'}",
    ]
    if not survey.is_complete:
        lines += ["", "⚠️ Needs at least text and a link before /preview or /send."]
    return "\n".join(lines)


# -- basic --------------------------------------------------------------------
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(update, HELP_TEXT)


# -- guided flow --------------------------------------------------------------
async def cmd_newsurvey(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    _service(ctx).start_new(update.effective_user.id)
    ctx.user_data[_AWAITING] = Step.TEXT
    ctx.user_data.pop(_PENDING_SEND, None)
    await _reply(update, "Let's build a survey ✍️\n\n" + PROMPTS[Step.TEXT])


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Route a plain message to the current guided step (no-op if not in a flow)."""
    awaiting = ctx.user_data.get(_AWAITING)
    if awaiting is None:
        await _reply(update, "Use /newsurvey to start, or /help to see commands.")
        return

    text = update.effective_message.text.strip()
    admin_id = update.effective_user.id
    service = _service(ctx)

    try:
        if awaiting == Step.TEXT:
            service.set_text(admin_id, text)
            ctx.user_data[_AWAITING] = Step.LINK
            await _reply(update, PROMPTS[Step.LINK])

        elif awaiting == Step.LINK:
            service.set_link(admin_id, text, valid=is_url(text))
            ctx.user_data[_AWAITING] = Step.DEADLINE
            await _reply(update, PROMPTS[Step.DEADLINE])

        elif awaiting == Step.DEADLINE:
            if text.lower() in SKIP_TOKENS:
                service.clear_deadline(admin_id)
                await _finish_flow(update, ctx)
            else:
                service.set_deadline(admin_id, parse_deadline(text))
                ctx.user_data[_AWAITING] = Step.REMINDERS
                await _reply(update, PROMPTS[Step.REMINDERS])

        elif awaiting == Step.REMINDERS:
            if text.lower() in SKIP_TOKENS:
                service.clear_reminders(admin_id)
            else:
                service.set_reminders(admin_id, parse_reminders(text))
            await _finish_flow(update, ctx)

    except (SurveyError, ValueError) as exc:
        # Stay on the same step so the admin can retry.
        await _reply(update, f"⚠️ {exc}")


async def _finish_flow(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    ctx.user_data.pop(_AWAITING, None)
    draft = _service(ctx).get_draft(update.effective_user.id)
    await _reply(
        update,
        _status_text(draft) + "\n\nSaved ✅ Use /preview to review or /send to publish.",
    )


# -- direct setters -----------------------------------------------------------
async def cmd_settext(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    text = " ".join(ctx.args).strip()
    if not text:
        await _reply(update, "Usage: /settext <text>")
        return
    try:
        _service(ctx).set_text(update.effective_user.id, text)
    except SurveyError as exc:
        await _reply(update, f"⚠️ {exc}")
        return
    await _reply(update, "✅ Text updated.")


async def cmd_setlink(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    link = " ".join(ctx.args).strip()
    if not link:
        await _reply(update, "Usage: /setlink <http(s)://...>")
        return
    try:
        _service(ctx).set_link(update.effective_user.id, link, valid=is_url(link))
    except SurveyError as exc:
        await _reply(update, f"⚠️ {exc}")
        return
    await _reply(update, "✅ Link updated.")


async def cmd_setdeadline(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    raw = " ".join(ctx.args).strip()
    if not raw:
        await _reply(update, "Usage: /setdeadline 2026-06-20 18:00")
        return
    try:
        deadline = parse_deadline(raw)
        survey = _service(ctx).set_deadline(update.effective_user.id, deadline)
    except (SurveyError, ValueError) as exc:
        await _reply(update, f"⚠️ {exc}")
        return
    await _reply(update, f"✅ Deadline set: {fmt_dt(survey.deadline)}.")


async def cmd_setreminders(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    raw = " ".join(ctx.args).strip()
    if not raw:
        await _reply(update, "Usage: /setreminders 1d, 2h, 30m")
        return
    try:
        offsets = parse_reminders(raw)
        _service(ctx).set_reminders(update.effective_user.id, offsets)
    except (SurveyError, ValueError) as exc:
        await _reply(update, f"⚠️ {exc}")
        return
    await _reply(update, "✅ Reminders set: " + ", ".join(fmt_offset(o) for o in offsets) + ".")


# -- review -------------------------------------------------------------------
async def cmd_preview(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    draft = _service(ctx).get_draft(update.effective_user.id)
    if draft is None or not draft.is_complete:
        await _reply(update, "Nothing to preview yet — set at least text and a link.")
        return
    post = render_post(draft.text, draft.link, draft.deadline)
    await _reply(update, "👇 *Preview — this is exactly what the group will see:*", preview=False)
    await _reply(update, post, preview=True)


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    draft = _service(ctx).get_draft(update.effective_user.id)
    await _reply(update, _status_text(draft))


# -- publish ------------------------------------------------------------------
async def cmd_send(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        draft = _service(ctx).check_can_send(update.effective_user.id)
    except SurveyError as exc:
        await _reply(update, f"⚠️ {exc}")
        return
    ctx.user_data[_PENDING_SEND] = True
    target = config.group_chat_id
    await _reply(
        update,
        f"About to publish to group `{target}`:\n\n"
        + render_post(draft.text, draft.link, draft.deadline)
        + "\n\nAre you sure? Send /confirm to publish or /cancel to abort.",
        preview=True,
    )


async def cmd_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not ctx.user_data.get(_PENDING_SEND):
        await _reply(update, "Nothing to confirm. Use /send first.")
        return
    admin_id = update.effective_user.id
    service = _service(ctx)
    try:
        draft = service.check_can_send(admin_id)
    except SurveyError as exc:
        ctx.user_data.pop(_PENDING_SEND, None)
        await _reply(update, f"⚠️ {exc}")
        return

    post = render_post(draft.text, draft.link, draft.deadline)
    try:
        message = await ctx.bot.send_message(
            config.group_chat_id, post, parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True,
        )
    except TelegramError as exc:
        logger.warning("Publish failed for admin %s: %s", admin_id, exc)
        await _reply(update, f"❌ Could not publish to the group: {exc}")
        return

    saved = service.mark_sent(draft, message.message_id)
    ctx.user_data.pop(_PENDING_SEND, None)
    ctx.user_data.pop(_AWAITING, None)

    note = "📤 Published to the group."
    if saved.deadline is not None:
        active = sum(1 for o in saved.reminders) if saved.reminders else 0
        note += f" Closes {fmt_dt(saved.deadline)}."
        if active:
            note += f" {active} reminder(s) scheduled."
    await _reply(update, note)


async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    ctx.user_data.pop(_AWAITING, None)
    had_pending = ctx.user_data.pop(_PENDING_SEND, None)
    cancelled = _service(ctx).cancel(update.effective_user.id)
    if cancelled:
        await _reply(update, "🗑️ Survey discarded, reminders cancelled.")
    elif had_pending:
        await _reply(update, "Send aborted.")
    else:
        await _reply(update, "Nothing to cancel.")


# -- extras -------------------------------------------------------------------
async def cmd_history(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    surveys = _service(ctx).history(update.effective_user.id, limit=5)
    if not surveys:
        await _reply(update, "No surveys published yet.")
        return
    lines = ["🗂️ *Recent surveys*", ""]
    for s in surveys:
        when = fmt_dt(s.sent_at)
        lines.append(f"• *{escape_md(s.text[:60])}* — {when}\n  {s.link}")
    await _reply(update, "\n".join(lines))


async def cmd_duplicate(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        draft = _service(ctx).duplicate_last(update.effective_user.id)
    except SurveyError as exc:
        await _reply(update, f"⚠️ {exc}")
        return
    ctx.user_data.pop(_AWAITING, None)
    await _reply(update, _status_text(draft) + "\n\nCloned ✅ Edit fields or /send.")


# -- registration -------------------------------------------------------------
def register_handlers(app: Application) -> None:
    """Attach all handlers, gated to private chats and whitelisted admins."""
    guard = filters.ChatType.PRIVATE & filters.User(user_id=list(config.admin_ids))

    commands = [
        ("start", cmd_start),
        ("help", cmd_start),
        ("newsurvey", cmd_newsurvey),
        ("settext", cmd_settext),
        ("setlink", cmd_setlink),
        ("setdeadline", cmd_setdeadline),
        ("setreminders", cmd_setreminders),
        ("preview", cmd_preview),
        ("status", cmd_status),
        ("send", cmd_send),
        ("confirm", cmd_confirm),
        ("cancel", cmd_cancel),
        ("history", cmd_history),
        ("duplicate", cmd_duplicate),
    ]
    for name, handler in commands:
        app.add_handler(CommandHandler(name, handler, filters=guard))

    app.add_handler(MessageHandler(guard & filters.TEXT & ~filters.COMMAND, on_text))
