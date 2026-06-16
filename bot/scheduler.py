"""Scheduling of survey reminders and deadline notices via the JobQueue.

Jobs are named deterministically per survey so they can be located and replaced
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


def _reminder_job_name(survey_id: int, offset: int) -> str:
    return f"rem:{survey_id}:{offset}"


def _deadline_job_name(survey_id: int) -> str:
    return f"dl:{survey_id}"


def _remove_jobs_by_prefix(application: Application, prefix: str) -> None:
    for job in application.job_queue.jobs():
        if job.name and job.name.startswith(prefix):
            job.schedule_removal()


def clear_survey_jobs(application: Application, survey_id: int) -> None:
    """Remove every reminder/deadline job belonging to a survey."""
    _remove_jobs_by_prefix(application, f"rem:{survey_id}:")
    _remove_jobs_by_prefix(application, f"dl:{survey_id}")


def schedule_survey(application: Application, survey: Survey) -> None:
    """(Re)schedule all future jobs for a survey.

    Reminders and the deadline notice are only scheduled when they fall in the
    future and the survey has been sent and has a deadline.
    """
    if survey.id is None:
        return
    clear_survey_jobs(application, survey.id)

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
            data={"survey_id": survey.id, "offset": offset},
            name=_reminder_job_name(survey.id, offset),
        )

    if survey.deadline > now:
        job_queue.run_once(
            _send_deadline_notice,
            when=survey.deadline,
            data={"survey_id": survey.id},
            name=_deadline_job_name(survey.id),
        )

    logger.info(
        "Scheduled jobs for survey %s -> group %s (deadline %s, %d reminders)",
        survey.id,
        survey.group_id,
        survey.deadline.isoformat(),
        len(survey.reminder_offsets),
    )


def reschedule_all(application: Application) -> None:
    """Rebuild jobs for every stored survey. Call once on startup."""
    db: Database = application.bot_data["db"]
    for survey in db.list_all_surveys():
        schedule_survey(application, survey)


# -- job callbacks ---------------------------------------------------------


async def _send_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    survey_id = context.job.data["survey_id"]
    offset = context.job.data["offset"]
    db: Database = context.application.bot_data["db"]
    tz = context.application.bot_data["config"].timezone

    survey = db.get_survey(survey_id)
    if survey is None or not survey.is_complete or survey.deadline is None:
        return
    if survey.deadline_passed():
        return

    text = (
        f"⏰ <b>Напоминание</b>\n\n"
        f"Пройдите, пожалуйста, опрос: <b>{survey.title}</b>\n"
        f"👉 {survey.link}\n\n"
        f"Закроется через <b>{format_offset(offset)}</b> "
        f"({format_datetime(survey.deadline, tz)})."
    )
    try:
        await context.bot.send_message(
            chat_id=survey.group_id, text=text, parse_mode=ParseMode.HTML
        )
    except Exception:  # noqa: BLE001 - keep the queue alive on send failures.
        logger.exception("Failed to send reminder for survey %s", survey_id)


async def _send_deadline_notice(context: ContextTypes.DEFAULT_TYPE) -> None:
    survey_id = context.job.data["survey_id"]
    db: Database = context.application.bot_data["db"]

    survey = db.get_survey(survey_id)
    if survey is None or not survey.is_complete:
        return

    text = (
        f"⏳ <b>Опрос закрывается.</b>\n\n"
        f"<b>{survey.title}</b>\n"
        f"Последний шанс ответить 👉 {survey.link}"
    )
    try:
        await context.bot.send_message(
            chat_id=survey.group_id, text=text, parse_mode=ParseMode.HTML
        )
    except Exception:  # noqa: BLE001
        logger.exception("Failed to send deadline notice for survey %s", survey_id)
