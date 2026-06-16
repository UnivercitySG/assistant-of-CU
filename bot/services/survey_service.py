"""Survey business logic.

Handlers stay thin: they parse Telegram updates and delegate every state change
to this service, which enforces the FSM order, persists state and drives the
reminder scheduler. Invalid operations raise ``SurveyError`` with a message
suitable for showing to the admin.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from telegram.ext import Application

from bot.config import config
from bot.scheduler.reminders import cancel_reminders, schedule_reminders
from bot.state.fsm import Step
from bot.state.storage import STATUS_SENT, Storage, Survey


class SurveyError(Exception):
    """Domain error whose message is safe to show to the admin."""


class SurveyService:
    def __init__(self, app: Application, storage: Storage):
        self._app = app
        self._storage = storage
        self._last_send: dict[int, float] = {}  # admin_id -> monotonic seconds

    # -- draft lifecycle --------------------------------------------------
    def start_new(self, admin_id: int) -> Survey:
        """Discard any existing draft and begin a fresh one at step TEXT."""
        self._storage.delete_draft(admin_id)
        survey = Survey(admin_id=admin_id, step=Step.TEXT)
        return self._storage.save(survey)

    def get_draft(self, admin_id: int) -> Survey | None:
        return self._storage.get_draft(admin_id)

    def require_draft(self, admin_id: int) -> Survey:
        draft = self._storage.get_draft(admin_id)
        if draft is None:
            raise SurveyError("No active survey. Start one with /newsurvey.")
        return draft

    def cancel(self, admin_id: int) -> bool:
        """Drop the current draft and any of its scheduled jobs."""
        draft = self._storage.get_draft(admin_id)
        if draft is None:
            return False
        if draft.id is not None:
            cancel_reminders(self._app, draft.id)
        return self._storage.delete_draft(admin_id)

    # -- field setters (each enforces FSM order) --------------------------
    def set_text(self, admin_id: int, text: str) -> Survey:
        text = text.strip()
        if not text:
            raise SurveyError("Survey text cannot be empty.")
        draft = self._storage.get_draft(admin_id) or Survey(admin_id=admin_id)
        draft.text = text
        draft.step = max(draft.step, Step.LINK)
        return self._storage.save(draft)

    def set_link(self, admin_id: int, link: str, *, valid: bool) -> Survey:
        if not valid:
            raise SurveyError("That is not a valid link. It must start with http:// or https://.")
        draft = self._storage.get_draft(admin_id)
        if draft is None or not draft.text:
            raise SurveyError("Set the survey text first (/settext or /newsurvey).")
        draft.link = link.strip()
        draft.step = max(draft.step, Step.DEADLINE)
        return self._storage.save(draft)

    def set_deadline(self, admin_id: int, deadline: datetime) -> Survey:
        draft = self._storage.get_draft(admin_id)
        if draft is None or not draft.is_complete:
            raise SurveyError("Set the text and link first before the deadline.")
        draft.deadline = deadline
        draft.step = max(draft.step, Step.REMINDERS)
        self._reschedule_if_sent(draft)
        return self._storage.save(draft)

    def clear_deadline(self, admin_id: int) -> Survey:
        """Skip the deadline step (also clears reminders, which need a deadline)."""
        draft = self._storage.get_draft(admin_id)
        if draft is None or not draft.is_complete:
            raise SurveyError("Set the text and link first.")
        draft.deadline = None
        draft.reminders = []
        draft.step = max(draft.step, Step.READY)
        return self._storage.save(draft)

    def set_reminders(self, admin_id: int, offsets: list[int]) -> Survey:
        draft = self._storage.get_draft(admin_id)
        if draft is None or not draft.is_complete:
            raise SurveyError("Set the text and link first.")
        if draft.deadline is None:
            raise SurveyError("Set a deadline before reminders (reminders fire before it).")
        draft.reminders = offsets
        draft.step = Step.READY
        self._reschedule_if_sent(draft)
        return self._storage.save(draft)

    def clear_reminders(self, admin_id: int) -> Survey:
        draft = self._storage.get_draft(admin_id)
        if draft is None:
            raise SurveyError("No active survey. Start one with /newsurvey.")
        draft.reminders = []
        draft.step = Step.READY
        self._reschedule_if_sent(draft)
        return self._storage.save(draft)

    # -- publishing -------------------------------------------------------
    def check_can_send(self, admin_id: int) -> Survey:
        """Validate that the current draft may be published; raise otherwise."""
        if config.group_chat_id is None:
            raise SurveyError(
                "No target group configured. Set GROUP_CHAT_ID to the group's chat id."
            )
        draft = self.require_draft(admin_id)
        if not draft.is_complete:
            raise SurveyError("Survey is incomplete — it needs at least text and a link.")
        remaining = self._cooldown_remaining(admin_id)
        if remaining > 0:
            raise SurveyError(f"Please wait {remaining}s before sending again (rate limit).")
        return draft

    def mark_sent(self, draft: Survey, message_id: int) -> Survey:
        """Promote a draft to a sent survey and (re)arm its reminders."""
        draft.status = STATUS_SENT
        draft.group_chat_id = config.group_chat_id
        draft.message_id = message_id
        draft.sent_at = datetime.now(timezone.utc)
        draft.step = Step.READY
        saved = self._storage.save(draft)
        self._last_send[draft.admin_id] = time.monotonic()
        schedule_reminders(self._app, saved)
        return saved

    # -- history / duplicate ---------------------------------------------
    def history(self, admin_id: int, limit: int = 5) -> list[Survey]:
        return self._storage.history(admin_id, limit)

    def duplicate_last(self, admin_id: int) -> Survey:
        """Clone the most recent sent survey into a fresh draft."""
        source = self._storage.get_last_sent(admin_id)
        if source is None:
            raise SurveyError("No previous survey to duplicate.")
        self._storage.delete_draft(admin_id)
        clone = Survey(
            admin_id=admin_id,
            text=source.text,
            link=source.link,
            deadline=source.deadline,
            reminders=list(source.reminders),
            step=Step.READY,
        )
        return self._storage.save(clone)

    # -- internals --------------------------------------------------------
    def _cooldown_remaining(self, admin_id: int) -> int:
        last = self._last_send.get(admin_id)
        if last is None:
            return 0
        elapsed = time.monotonic() - last
        return max(0, int(config.send_cooldown_seconds - elapsed))

    def _reschedule_if_sent(self, survey: Survey) -> None:
        if survey.is_sent:
            schedule_reminders(self._app, survey)
