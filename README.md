# Survey Broadcasting Bot

A production-ready Telegram bot for creating surveys in **private chat** and
broadcasting them to a Telegram **group**, with scheduled reminders before a
deadline.

## Architecture rules

- **Strict chat separation.** The bot is controlled *only* in private chat. In
  any group/supergroup it is output-only — it never accepts commands there.
- **Admin-only, by user id.** Only the user ids in `ADMIN_ID`/`ADMIN_IDS` can use
  the bot. Everyone else is ignored silently (handlers are gated by message
  filters, so non-admin/group updates never reach the logic).
- **Strict FSM.** A survey is built in order: `TEXT → LINK → DEADLINE →
  REMINDERS → READY`. Steps cannot be jumped; deadline/reminders are optional
  and can be `skip`ped.
- **Persistent state.** Drafts and sent surveys live in SQLite, so state and
  reminders survive restarts.
- **Safe publishing.** `/send` requires an explicit `/confirm`, is rate-limited,
  and validates all inputs (URL, datetime, reminder format).

## Project layout

```
bot/
  config.py            # env-driven configuration
  app.py               # application factory + polling
  handlers/            # Telegram commands (private chat only)
  services/            # business logic / FSM enforcement
  state/               # FSM definition + SQLite storage
  scheduler/           # idempotent reminder jobs
  utils/               # validators + formatters
main.py                # entry point
```

## Commands

| Command | Purpose |
| --- | --- |
| `/newsurvey` | Guided, step-by-step survey creation |
| `/settext <text>` | Set the survey text |
| `/setlink <url>` | Set the survey link (http/https) |
| `/setdeadline <datetime>` | e.g. `2026-06-20 18:00` |
| `/setreminders <list>` | e.g. `1d, 2h, 30m` |
| `/preview` | Show the post exactly as the group will see it |
| `/status` | Current survey state |
| `/send` → `/confirm` | Publish to the group (confirmation + rate limit) |
| `/cancel` | Discard the current survey and its reminders |
| `/history` | Recent published surveys |
| `/duplicate` | Clone the last survey into a new draft |

## Reminders

Offsets use `1d` / `2h` / `30m` syntax and fire that long **before** the
deadline (plus a final "closing" notice at the deadline). Jobs are named per
survey, so rescheduling is idempotent (no duplicates) and cancelling a survey
removes its jobs. On startup, reminders for already-sent surveys are restored.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env        # then edit values
python main.py              # python 3.9+
```

### Configuration (`.env`)

| Variable | Required | Notes |
| --- | --- | --- |
| `BOT_TOKEN` | yes | From [@BotFather](https://t.me/BotFather) |
| `ADMIN_ID` / `ADMIN_IDS` | yes | Whitelisted Telegram user id(s) |
| `GROUP_CHAT_ID` | for `/send` | Target group chat id (negative number) |
| `TIMEZONE` | no | IANA tz, default `UTC` |
| `DATABASE_PATH` | no | default `bot.db` |
| `SEND_COOLDOWN_SECONDS` | no | default `15` |

> **Security note:** never commit your real `.env`. It is git-ignored here. If a
> token was ever committed, rotate it via @BotFather.
