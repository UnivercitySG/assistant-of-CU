"""Reminder scheduler.

Uses the application's ``JobQueue`` (APScheduler under the hood). Jobs are named
deterministically per survey, which gives idempotency: rescheduling always
clears prior jobs first, so a survey can never accumulate duplicate reminders.
Jobs are auto-cancelled when a survey is deleted, cancelled or re-sent.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import Application, ContextTypes

from bot.state.storage import Survey
from bot.utils.formatters import escape_md, render_reminder

logger = logging.getLogger(__name__)


def _reminder_job_name(survey_id: int, offset: int) -> str:
    return f"reminder:{survey_id}:{offset}"


def _deadline_job_name(survey_id: int) -> str:
    return f"deadline:{survey_id}"


def cancel_reminders(app: Application, survey_id: int) -> None:
    """Remove every scheduled job belonging to a survey (idempotent)."""
    if app.job_queue is None:
        return
    prefix_rem = f"reminder:{survey_id}:"
    name_dl = _deadline_job_name(survey_id)
    for job in app.job_queue.jobs():
        if job.name == name_dl or (job.name and job.name.startswith(prefix_rem)):
            job.schedule_removal()


def schedule_reminders(app: Application, survey: Survey) -> int:
    """(Re)schedule all future reminders + the deadline notice for a survey.

    Returns the number of jobs scheduled. Past offsets are skipped. Calling this
    repeatedly is safe — existing jobs for the survey are cleared first.
    """
    if app.job_queue is None or survey.id is None:
        return 0

    cancel_reminders(app, survey.id)

    if not survey.is_sent or survey.deadline is None:
        return 0

    now = datetime.now(timezone.utc).timestamp()
    deadline_ts = survey.deadline.timestamp()
    scheduled = 0

    for offset in survey.reminders:
        fire_ts = deadline_ts - offset * 60
        if fire_ts <= now:
            continue
        app.job_queue.run_once(
            _send_reminder,
            when=datetime.fromtimestamp(fire_ts, timezone.utc),
            data={"survey_id": survey.id, "offset": offset},
            name=_reminder_job_name(survey.id, offset),
        )
        scheduled += 1

    if deadline_ts > now:
        app.job_queue.run_once(
            _send_deadline_notice,
            when=survey.deadline,
            data={"survey_id": survey.id},
            name=_deadline_job_name(survey.id),
        )
        scheduled += 1

    logger.info("Scheduled %d job(s) for survey %s", scheduled, survey.id)
    return scheduled


def restore_all(app: Application) -> None:
    """Re-arm reminders for every already-sent survey after a restart."""
    storage = app.bot_data["storage"]
    for survey in storage.all_sent():
        schedule_reminders(app, survey)


async def _load_live_survey(ctx: ContextTypes.DEFAULT_TYPE) -> Survey | None:
    """Reload the survey and confirm it is still sendable; None aborts the job."""
    storage = ctx.application.bot_data["storage"]
    survey_id = ctx.job.data["survey_id"]
    for survey in storage.all_sent():
        if survey.id == survey_id and survey.is_complete and survey.group_chat_id:
            return survey
    return None


async def _send_reminder(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    survey = await _load_live_survey(ctx)
    if survey is None:
        return
    text = render_reminder(survey.text, survey.link, survey.deadline, ctx.job.data["offset"])
    try:
        await ctx.bot.send_message(
            survey.group_chat_id, text, parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True,
        )
    except TelegramError as exc:  # fail-safe: never crash the scheduler
        logger.warning("Reminder for survey %s failed: %s", survey.id, exc)


async def _send_deadline_notice(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    survey = await _load_live_survey(ctx)
    if survey is None:
        return
    text = (
        "⏳ *The survey is closing.*\n\n"
        f"*{escape_md(survey.text)}*\nLast chance 👉 {survey.link}"
    )
    try:
        await ctx.bot.send_message(
            survey.group_chat_id, text, parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True,
        )
    except TelegramError as exc:
        logger.warning("Deadline notice for survey %s failed: %s", survey.id, exc)
