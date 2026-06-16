# assistant-of-CU — Survey Assistant Telegram Bot

A Telegram bot that attaches to group chats and helps an admin run surveys:
post a survey link to the group, set a deadline, and automatically remind
members to complete it before time runs out.

## Features

- **Attaches to group chats.** Add the bot to any group; it greets the group
  and is ready to use.
- **Admin-managed.** Only Telegram group administrators (or configured
  super-admins) can manage surveys.
- **Editable content.** Set/edit the message text, the survey link, the
  deadline, and the reminder schedule at any time.
- **Posts survey links** to the group on demand.
- **Automatic reminders.** Schedules reminders at chosen offsets before the
  deadline (e.g. 1 day, 2 hours, 30 minutes before) plus a "closing now"
  notice at the deadline. Send an ad-hoc reminder any time with `/remind`.
- **Survives restarts.** Surveys and their schedules are stored in SQLite and
  reminders are rebuilt on startup.

## Commands

Run these inside the group (admins only):

| Command | Description |
| --- | --- |
| `/newsurvey` | Guided setup: text → link → deadline → reminders |
| `/settext <text>` | Set the survey message text |
| `/setlink <url>` | Set the survey link |
| `/setdeadline <when>` | e.g. `2026-06-20 18:00` or `20.06.2026 18:00` |
| `/setreminders <list>` | e.g. `1d, 2h, 30m` before the deadline |
| `/send` | Post the survey to the group and arm reminders |
| `/remind` | Send a reminder to the group immediately |
| `/status` | Show the current survey configuration |
| `/delete` | Delete the survey and cancel reminders |
| `/cancel` | Abort the guided setup |
| `/help` | Show help (works anywhere) |

Reminder offsets accept `m` (minutes), `h` (hours), `d` (days), `w` (weeks).

## Setup

1. **Create a bot** with [@BotFather](https://t.me/BotFather) and copy the token.
2. **Disable privacy mode** in @BotFather (`/setprivacy` → *Disable*) so the bot
   can read group commands. Then add the bot to your group.
3. **Install dependencies** (Python 3.11+):

   ```bash
   pip install -r requirements.txt
   ```

4. **Configure** by copying `.env.example` to `.env` and filling it in:

   ```bash
   cp .env.example .env
   # edit .env: set BOT_TOKEN, optionally SUPER_ADMIN_IDS, TIMEZONE
   ```

5. **Run** the bot:

   ```bash
   python run.py
   ```

### Configuration (environment variables)

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `BOT_TOKEN` | yes | — | Token from @BotFather |
| `SUPER_ADMIN_IDS` | no | empty | Comma-separated user IDs allowed to manage surveys in any group |
| `DATABASE_PATH` | no | `bot.db` | SQLite file path |
| `TIMEZONE` | no | `UTC` | IANA timezone for interpreting/displaying times (e.g. `Asia/Almaty`) |

## Typical flow

```
1. Add the bot to your group.
2. /newsurvey
   → "Course feedback — help us improve!"
   → https://forms.gle/your-survey
   → 2026-06-20 18:00
   → 1d, 2h, 30m
3. /send        # posts the link to the group, arms reminders
   ...the bot reminds the group 1 day, 2 hours and 30 minutes before,
   then posts a final notice at the deadline.
```

## Project layout

```
bot/
├── config.py          # Environment configuration
├── models.py          # Survey dataclass
├── database.py        # SQLite persistence (one survey per chat)
├── utils.py           # Date/reminder parsing and formatting
├── scheduler.py       # JobQueue reminder & deadline scheduling
├── main.py            # Application bootstrap / polling
└── handlers/
    ├── common.py      # /start, /help, admin guards, group-join greeting
    └── admin.py       # Survey setup, setters, /send, /remind, /status, /delete
run.py                 # Launcher (python run.py)
tests/                 # Unit tests for parsing and persistence
```

## Development

```bash
pip install -r requirements.txt pytest
python -m pytest
```

Built with [python-telegram-bot](https://python-telegram-bot.org/).
