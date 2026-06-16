"""Scheduling of survey reminders and deadline notices via the JobQueue.

Jobs are named deterministically per chat so they can be located and replaced
whenever a survey changes, and rebuilt from the database on startup.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from telegram.constants import ParseMode
from telegram.ext import Application, ContextTypes

from .database import Database
from .models import Survey
from .utils import format_datetime, format_offset

logger = logging.getLogger(__name__)


def _reminder_job_name(chat_id: int, offset: int) -> str:
    return f"reminder:{chat_id}:{offset}"


def _deadline_job_name(chat_id: int) -> str:
    return f"deadline:{chat_id}"


def _remove_jobs_by_prefix(application: Application, prefix: str) -> None:
    for job in application.job_queue.jobs():
        if job.name and job.name.startswith(prefix):
            job.schedule_removal()


def clear_survey_jobs(application: Application, chat_id: int) -> None:
    """Remove every reminder/deadline job belonging to ``chat_id``."""
    _remove_jobs_by_prefix(application, f"reminder:{chat_id}:")
    _remove_jobs_by_prefix(application, f"deadline:{chat_id}")


def schedule_survey(application: Application, survey: Survey) -> None:
    """(Re)schedule all future jobs for a survey.

    Reminders and the deadline notice are only scheduled when they fall in the
    future and the survey has actually been sent and has a deadline.
    """
    clear_survey_jobs(application, survey.chat_id)

    if survey.deadline is None or not survey.is_sent:
        return

    now = datetime.now(timezone.utc)
    job_queue = application.job_queue

    for offset in survey.reminder_offsets:
        fire_at = survey.deadline.timestamp() - offset * 60
        if fire_at <= now.timestamp():
            continue  # Reminder time already passed.
        job_queue.run_once(
            _send_reminder,
            when=datetime.fromtimestamp(fire_at, tz=timezone.utc),
            data={"chat_id": survey.chat_id, "offset": offset},
            name=_reminder_job_name(survey.chat_id, offset),
        )

    if survey.deadline > now:
        job_queue.run_once(
            _send_deadline_notice,
            when=survey.deadline,
            data={"chat_id": survey.chat_id},
            name=_deadline_job_name(survey.chat_id),
        )

    logger.info(
        "Scheduled jobs for chat %s (deadline %s, %d reminders)",
        survey.chat_id,
        survey.deadline.isoformat(),
        len(survey.reminder_offsets),
    )


def reschedule_all(application: Application) -> None:
    """Rebuild jobs for every stored survey. Call once on startup."""
    db: Database = application.bot_data["db"]
    for survey in db.list_surveys():
        schedule_survey(application, survey)


# -- job callbacks ---------------------------------------------------------


async def _send_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = context.job.data["chat_id"]
    offset = context.job.data["offset"]
    db: Database = context.application.bot_data["db"]
    tz = context.application.bot_data["config"].timezone

    survey = db.get_survey(chat_id)
    if survey is None or not survey.is_complete or survey.deadline is None:
        return
    if survey.deadline_passed():
        return

    text = (
        f"⏰ <b>Reminder</b>\n\n"
        f"Please complete the survey: <b>{survey.title}</b>\n"
        f"👉 {survey.link}\n\n"
        f"Closes in <b>{format_offset(offset)}</b> "
        f"({format_datetime(survey.deadline, tz)})."
    )
    try:
        await context.bot.send_message(
            chat_id=chat_id, text=text, parse_mode=ParseMode.HTML
        )
    except Exception:  # noqa: BLE001 - keep the queue alive on send failures.
        logger.exception("Failed to send reminder to chat %s", chat_id)


async def _send_deadline_notice(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = context.job.data["chat_id"]
    db: Database = context.application.bot_data["db"]

    survey = db.get_survey(chat_id)
    if survey is None or not survey.is_complete:
        return

    text = (
        f"⏳ <b>The survey is closing now.</b>\n\n"
        f"<b>{survey.title}</b>\n"
        f"Last chance to respond 👉 {survey.link}"
    )
    try:
        await context.bot.send_message(
            chat_id=chat_id, text=text, parse_mode=ParseMode.HTML
        )
    except Exception:  # noqa: BLE001
        logger.exception("Failed to send deadline notice to chat %s", chat_id)
