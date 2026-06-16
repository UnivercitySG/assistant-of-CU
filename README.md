# assistant-of-CU — Survey Assistant Telegram Bot

A Telegram bot for running surveys in group chats. **Admins create and manage
surveys privately, one-on-one with the bot**; the bot then posts the survey
link into the chosen group and reminds members before the deadline. **Group
members only view** the available surveys — they can't create or change them.

## How it works

- **Admins work in private chat** with the bot: create a survey, pick which
  group it targets, set the text, link, deadline and reminders — all through a
  guided flow with inline buttons.
- **The bot posts to the group** on the admin's command and sends automatic
  reminders before the deadline (plus a "closing now" notice).
- **Group members do nothing** — the survey message simply appears in the group
  (no command needed), and reminders arrive automatically. Surveys can't be
  created or changed from inside the group.

## Features

- **Multiple groups & surveys.** The bot remembers every group it's added to;
  each group can have several surveys.
- **Private, admin-only management.** Only configured super-admins (by Telegram
  user id) can create or manage surveys.
- **Guided setup.** Text → link → deadline → reminders, with group selection by
  inline keyboard.
- **Automatic reminders** at chosen offsets before the deadline (e.g. 1 day,
  2 hours, 30 minutes) plus a closing notice; ad-hoc reminders via a button.
- **Survives restarts.** Groups, surveys and schedules are stored in SQLite and
  reminders are rebuilt on startup.

## Commands

**In private chat with the bot (admins only):**

| Command | Description |
| --- | --- |
| `/start` | Show help and your Telegram user id |
| `/newsurvey` | Create a survey: pick group → text → link → deadline → reminders |
| `/surveys` | List all surveys with action buttons (send / remind / delete) |
| `/cancel` | Abort the current creation flow |

**In a group:** no commands are needed — the bot posts the survey message and
reminders directly. (`/help` shows short info.)

Each survey message in private chat has inline buttons:
**📤 Send** (post to the group and arm reminders), **⏰ Remind** (send a reminder
now), **🗑 Delete**.

Reminder offsets accept `m` (minutes), `h` (hours), `d` (days), `w` (weeks),
e.g. `1d, 2h, 30m`.

## Setup

1. **Create a bot** with [@BotFather](https://t.me/BotFather) and copy the token.
2. **Add the bot to your group(s).** It registers each group automatically when
   added, so you can pick it as a target later. (Privacy mode can stay on.)
3. **Install dependencies** (Python 3.11+):

   ```bash
   pip install -r requirements.txt
   ```

4. **Configure** by copying `.env.example` to `.env` and filling it in:

   ```bash
   cp .env.example .env
   # edit .env: set BOT_TOKEN and SUPER_ADMIN_IDS (your Telegram user id)
   ```

   To find your user id, start the bot, open a private chat with it and send
   `/start` — it replies with your id.

5. **Run** the bot:

   ```bash
   python run.py
   ```

### Configuration (environment variables)

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `BOT_TOKEN` | yes | — | Token from @BotFather |
| `SUPER_ADMIN_IDS` | **yes** | empty | Comma-separated Telegram user ids allowed to create/manage surveys (without one, nobody can) |
| `DATABASE_PATH` | no | `bot.db` | SQLite file path |
| `TIMEZONE` | no | `UTC` | IANA timezone for interpreting/displaying times (e.g. `Asia/Almaty`) |

## Typical flow

```
1. Add the bot to your group(s).
2. In PRIVATE chat with the bot (as an admin):
   /newsurvey
   → choose the target group from the list
   → "Course feedback — help us improve!"
   → https://forms.gle/your-survey
   → 2026-06-20 18:00
   → 1d, 2h, 30m
3. Tap 📤 Send → the survey message appears in that group automatically and
   reminders are armed.
   ...it reminds the group 1 day, 2 hours and 30 minutes before the deadline,
   then posts a final notice when it closes.
```

## Project layout

```
bot/
├── config.py          # Environment configuration
├── models.py          # Group and Survey dataclasses
├── database.py        # SQLite persistence (groups + surveys)
├── utils.py           # Date/reminder parsing and formatting
├── scheduler.py       # JobQueue reminder & deadline scheduling
├── main.py            # Application bootstrap / polling
└── handlers/
    ├── common.py      # /start, /help, group /surveys view, group tracking
    └── admin.py       # Private-chat creation, group selection, send/remind/delete
run.py                 # Launcher (python run.py)
tests/                 # Unit tests for parsing and persistence
```

## Development

```bash
pip install -r requirements.txt pytest
python -m pytest
```

Built with [python-telegram-bot](https://python-telegram-bot.org/).
