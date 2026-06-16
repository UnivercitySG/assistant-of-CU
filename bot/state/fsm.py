"""Survey state machine.

Flow (strict order):  TEXT -> LINK -> DEADLINE -> REMINDERS -> READY

DEADLINE and REMINDERS are optional and may be skipped, but a step may never be
filled before the ones that precede it (no step jumps).
"""

from __future__ import annotations

from enum import IntEnum


class Step(IntEnum):
    TEXT = 0
    LINK = 1
    DEADLINE = 2
    REMINDERS = 3
    READY = 4

    @property
    def label(self) -> str:
        return self.name


# Prompt shown when the bot is waiting for input at a given step.
PROMPTS = {
    Step.TEXT: "Step 1/4 — send the *survey text* (the message participants will read).",
    Step.LINK: "Step 2/4 — send the *survey link* (must start with http:// or https://).",
    Step.DEADLINE: (
        "Step 3/4 — send the *deadline* (e.g. `2026-06-20 18:00`) or send `skip`."
    ),
    Step.REMINDERS: (
        "Step 4/4 — send *reminder times* before the deadline (e.g. `1d, 2h, 30m`) "
        "or send `skip`."
    ),
}

# Tokens that mean "leave this optional step empty".
SKIP_TOKENS = {"skip", "none", "-"}
