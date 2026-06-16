"""Telegram-бот для опросов в группах: публикует ссылку на опрос,
ставит дедлайн и напоминает участникам пройти его до конца срока."""

from __future__ import annotations

import logging
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from telegram import BotCommand, Update
from telegram.constants import ChatType, ParseMode
from telegram.ext import (
    Application,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger(__name__)

# ── Настройки из окружения ────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
DATABASE_PATH = os.environ.get("DATABASE_PATH", "bot.db").strip() or "bot.db"
TIMEZONE = ZoneInfo(os.environ.get("TIMEZONE", "UTC").strip() or "UTC")
SUPER_ADMIN_IDS = {
    int(x) for x in os.environ.get("SUPER_ADMIN_IDS", "").replace(";", ",").split(",")
    if x.strip().isdigit()
}
GROUP_TYPES = {ChatType.GROUP, ChatType.SUPERGROUP}
TEXT, LINK, DEADLINE, REMINDERS = range(4)  # состояния /newsurvey
_SKIP = {"skip", "none", "-", "пропустить"}

_UNIT_MIN = {"m": 1, "min": 1, "мин": 1, "h": 60, "ч": 60, "hour": 60,
             "d": 1440, "д": 1440, "day": 1440, "w": 10080, "нед": 10080}
_DATE_FORMATS = ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%d.%m.%Y %H:%M",
                 "%d/%m/%Y %H:%M", "%Y-%m-%d")


# ── Модель опроса ─────────────────────────────────────────────────────────
@dataclass
class Survey:
    chat_id: int
    title: str = ""
    link: str = ""
    deadline: datetime | None = None
    reminder_offsets: list[int] = field(default_factory=list)  # минут до дедлайна
    is_sent: bool = False

    @property
    def is_complete(self) -> bool:
        return bool(self.title and self.link)


# ── База данных (SQLite) ──────────────────────────────────────────────────
class DB:
    def __init__(self, path: str):
        self.c = sqlite3.connect(path, check_same_thread=False)
        self.c.row_factory = sqlite3.Row
        self.c.execute("""CREATE TABLE IF NOT EXISTS surveys(
            chat_id INTEGER PRIMARY KEY, title TEXT DEFAULT '', link TEXT DEFAULT '',
            deadline TEXT, reminder_offsets TEXT DEFAULT '', is_sent INTEGER DEFAULT 0)""")
        self.c.commit()

    @staticmethod
    def _row(r) -> Survey:
        d = datetime.fromisoformat(r["deadline"]) if r["deadline"] else None
        offs = [int(x) for x in r["reminder_offsets"].split(",") if x.strip()]
        return Survey(r["chat_id"], r["title"], r["link"], d, offs, bool(r["is_sent"]))

    def get(self, chat_id: int) -> Survey | None:
        r = self.c.execute("SELECT * FROM surveys WHERE chat_id=?", (chat_id,)).fetchone()
        return self._row(r) if r else None

    def all(self) -> list[Survey]:
        return [self._row(r) for r in self.c.execute("SELECT * FROM surveys").fetchall()]

    def save(self, s: Survey):
        self.c.execute("""INSERT INTO surveys VALUES(?,?,?,?,?,?)
            ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title, link=excluded.link,
            deadline=excluded.deadline, reminder_offsets=excluded.reminder_offsets,
            is_sent=excluded.is_sent""",
            (s.chat_id, s.title, s.link,
             s.deadline.astimezone(timezone.utc).isoformat() if s.deadline else None,
             ",".join(map(str, s.reminder_offsets)), int(s.is_sent)))
        self.c.commit()

    def delete(self, chat_id: int) -> bool:
        cur = self.c.execute("DELETE FROM surveys WHERE chat_id=?", (chat_id,))
        self.c.commit()
        return cur.rowcount > 0


# ── Парсинг и форматирование ──────────────────────────────────────────────
def parse_deadline(text: str) -> datetime:
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text.strip(), fmt).replace(tzinfo=TIMEZONE)\
                .astimezone(timezone.utc)
        except ValueError:
            continue
    raise ValueError("Не понял дату. Пример: `2026-06-20 18:00` или `20.06.2026 18:00`.")


def parse_reminders(text: str) -> list[int]:
    out: set[int] = set()
    for chunk in text.replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        m = re.match(r"^(\d+)\s*([a-zA-Zа-яА-Я]+)$", chunk)
        if not m or m.group(2).lower() not in _UNIT_MIN:
            raise ValueError(f"Не понял «{chunk}». Используй `1d`, `2h`, `30m`.")
        out.add(int(m.group(1)) * _UNIT_MIN[m.group(2).lower()])
    if not out:
        raise ValueError("Напоминания не заданы.")
    return sorted(out, reverse=True)


def is_url(text: str) -> bool:
    return bool(re.match(r"^https?://\S+$", text.strip(), re.I))


def fmt_offset(m: int) -> str:
    parts = []
    for unit, size in (("w", 10080), ("d", 1440), ("h", 60), ("m", 1)):
        if m >= size:
            v, m = divmod(m, size)
            parts.append(f"{v}{unit}")
    return " ".join(parts) or "0m"


def fmt_dt(dt: datetime) -> str:
    return dt.astimezone(TIMEZONE).strftime("%Y-%m-%d %H:%M %Z")


# ── Планировщик напоминаний ───────────────────────────────────────────────
def clear_jobs(app: Application, chat_id: int):
    for job in app.job_queue.jobs():
        if job.name and job.name.startswith((f"rem:{chat_id}:", f"dl:{chat_id}")):
            job.schedule_removal()


def schedule(app: Application, s: Survey):
    clear_jobs(app, s.chat_id)
    if not s.deadline or not s.is_sent:
        return
    now = datetime.now(timezone.utc).timestamp()
    for off in s.reminder_offsets:
        fire = s.deadline.timestamp() - off * 60
        if fire > now:
            app.job_queue.run_once(_remind_job, datetime.fromtimestamp(fire, timezone.utc),
                                   data={"chat_id": s.chat_id, "off": off},
                                   name=f"rem:{s.chat_id}:{off}")
    if s.deadline.timestamp() > now:
        app.job_queue.run_once(_deadline_job, s.deadline,
                               data={"chat_id": s.chat_id}, name=f"dl:{s.chat_id}")


async def _remind_job(ctx: ContextTypes.DEFAULT_TYPE):
    s = DB_INSTANCE.get(ctx.job.data["chat_id"])
    if not s or not s.is_complete or not s.deadline:
        return
    await ctx.bot.send_message(s.chat_id, parse_mode=ParseMode.HTML, text=(
        f"⏰ <b>Напоминание</b>\n\nПройдите опрос: <b>{s.title}</b>\n👉 {s.link}\n\n"
        f"Закроется через <b>{fmt_offset(ctx.job.data['off'])}</b> ({fmt_dt(s.deadline)})."))


async def _deadline_job(ctx: ContextTypes.DEFAULT_TYPE):
    s = DB_INSTANCE.get(ctx.job.data["chat_id"])
    if s and s.is_complete:
        await ctx.bot.send_message(s.chat_id, parse_mode=ParseMode.HTML, text=(
            f"⏳ <b>Опрос закрывается.</b>\n\n<b>{s.title}</b>\nПоследний шанс 👉 {s.link}"))


# ── Проверка прав администратора ──────────────────────────────────────────
async def is_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> bool:
    user, chat = update.effective_user, update.effective_chat
    if not user or not chat:
        return False
    if user.id in SUPER_ADMIN_IDS:
        return True
    if chat.type not in GROUP_TYPES:
        return False
    try:
        member = await chat.get_member(user.id)
    except Exception:
        return False
    return member.status in ("administrator", "creator")


def admin_only(handler):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        chat = update.effective_chat
        if not chat or chat.type not in GROUP_TYPES:
            await update.effective_message.reply_text("Команда работает только в группе.")
            return ConversationHandler.END
        if not await is_admin(update, ctx):
            await update.effective_message.reply_text("🔒 Только администраторы группы.")
            return ConversationHandler.END
        return await handler(update, ctx)
    wrapper.__name__ = handler.__name__
    return wrapper


# ── Тексты и помощники ────────────────────────────────────────────────────
HELP = (
    "🤖 <b>Бот опросов</b>\n\nДобавьте меня в группу, и админ сможет настроить опрос — "
    "я опубликую ссылку и напомню всем о дедлайне.\n\n"
    "<b>Команды (в группе, для админов):</b>\n"
    "• /newsurvey — пошаговая настройка\n"
    "• /settext &lt;текст&gt; — текст опроса\n"
    "• /setlink &lt;url&gt; — ссылка\n"
    "• /setdeadline &lt;дата&gt; — напр. <code>2026-06-20 18:00</code>\n"
    "• /setreminders &lt;список&gt; — напр. <code>1d, 2h, 30m</code>\n"
    "• /send — опубликовать опрос и включить напоминания\n"
    "• /remind — напомнить сейчас\n"
    "• /status — текущие настройки\n"
    "• /delete — удалить опрос\n"
    "• /cancel — отменить настройку")


def status_text(s: Survey) -> str:
    rem = ", ".join(fmt_offset(o) for o in s.reminder_offsets) or "—"
    return (f"📋 <b>Текущий опрос</b>\n\n<b>Текст:</b> {s.title or '—'}\n"
            f"<b>Ссылка:</b> {s.link or '—'}\n"
            f"<b>Дедлайн:</b> {fmt_dt(s.deadline) if s.deadline else '—'}\n"
            f"<b>Напоминания:</b> {rem}\n"
            f"<b>Опубликован:</b> {'да' if s.is_sent else 'нет'}"
            + ("" if s.is_complete else "\n\n⚠️ Нужны текст и ссылка перед /send."))


def get_or_create(chat_id: int) -> Survey:
    return DB_INSTANCE.get(chat_id) or Survey(chat_id=chat_id)


# ── Команды ───────────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(HELP, parse_mode=ParseMode.HTML)


async def on_join(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    r = update.my_chat_member
    if not r:
        return
    was = r.old_chat_member.status in ("member", "administrator", "creator")
    now = r.new_chat_member.status in ("member", "administrator")
    if now and not was and r.chat.type in GROUP_TYPES:
        await ctx.bot.send_message(r.chat.id,
            "👋 Спасибо, что добавили меня! Админ может запустить /newsurvey или /help.")


# --- пошаговая настройка ---
@admin_only
async def newsurvey(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["draft"] = get_or_create(update.effective_chat.id)
    await update.effective_message.reply_text(
        "Настроим опрос ✍️\n\nШаг 1/4 — пришлите <b>текст</b> сообщения для участников.",
        parse_mode=ParseMode.HTML)
    return TEXT


async def step_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["draft"].title = update.effective_message.text.strip()
    await update.effective_message.reply_text(
        "Шаг 2/4 — пришлите <b>ссылку</b> на опрос (http:// или https://).",
        parse_mode=ParseMode.HTML)
    return LINK


async def step_link(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    link = update.effective_message.text.strip()
    if not is_url(link):
        await update.effective_message.reply_text("Это не похоже на ссылку. Нужен http(s)://")
        return LINK
    ctx.user_data["draft"].link = link
    await update.effective_message.reply_text(
        "Шаг 3/4 — пришлите <b>дедлайн</b> (напр. <code>2026-06-20 18:00</code>) "
        "или <code>skip</code>.", parse_mode=ParseMode.HTML)
    return DEADLINE


async def step_deadline(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    txt = update.effective_message.text.strip()
    if txt.lower() in _SKIP:
        ctx.user_data["draft"].deadline = None
        return await finish(update, ctx)
    try:
        ctx.user_data["draft"].deadline = parse_deadline(txt)
    except ValueError as e:
        await update.effective_message.reply_text(str(e), parse_mode=ParseMode.MARKDOWN)
        return DEADLINE
    await update.effective_message.reply_text(
        "Шаг 4/4 — когда напоминать до дедлайна? Напр. <code>1d, 2h, 30m</code> "
        "или <code>skip</code>.", parse_mode=ParseMode.HTML)
    return REMINDERS


async def step_reminders(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    txt = update.effective_message.text.strip()
    if txt.lower() in _SKIP:
        ctx.user_data["draft"].reminder_offsets = []
    else:
        try:
            ctx.user_data["draft"].reminder_offsets = parse_reminders(txt)
        except ValueError as e:
            await update.effective_message.reply_text(str(e), parse_mode=ParseMode.MARKDOWN)
            return REMINDERS
    return await finish(update, ctx)


async def finish(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    s = ctx.user_data.pop("draft")
    DB_INSTANCE.save(s)
    if s.is_sent:
        schedule(ctx.application, s)
    await update.effective_message.reply_text(
        status_text(s) + "\n\nСохранено ✅ Используйте /send, чтобы опубликовать.",
        parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    return ConversationHandler.END


async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data.pop("draft", None)
    await update.effective_message.reply_text("Настройка отменена.")
    return ConversationHandler.END


# --- прямые сеттеры ---
@admin_only
async def set_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = " ".join(ctx.args).strip()
    if not text:
        await update.effective_message.reply_text("Использование: /settext <текст>")
        return
    s = get_or_create(update.effective_chat.id)
    s.title = text
    DB_INSTANCE.save(s)
    await update.effective_message.reply_text("✅ Текст обновлён.")


@admin_only
async def set_link(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    link = " ".join(ctx.args).strip()
    if not is_url(link):
        await update.effective_message.reply_text("Использование: /setlink <http(s)://...>")
        return
    s = get_or_create(update.effective_chat.id)
    s.link = link
    DB_INSTANCE.save(s)
    await update.effective_message.reply_text("✅ Ссылка обновлена.")


@admin_only
async def set_deadline(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = " ".join(ctx.args).strip()
    try:
        d = parse_deadline(raw)
    except ValueError as e:
        await update.effective_message.reply_text(
            str(e) if raw else "Использование: /setdeadline 2026-06-20 18:00",
            parse_mode=ParseMode.MARKDOWN)
        return
    s = get_or_create(update.effective_chat.id)
    s.deadline = d
    DB_INSTANCE.save(s)
    if s.is_sent:
        schedule(ctx.application, s)
    await update.effective_message.reply_text(f"✅ Дедлайн: {fmt_dt(d)}.")


@admin_only
async def set_reminders(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = " ".join(ctx.args).strip()
    try:
        offs = parse_reminders(raw)
    except ValueError as e:
        await update.effective_message.reply_text(
            str(e) if raw else "Использование: /setreminders 1d, 2h, 30m",
            parse_mode=ParseMode.MARKDOWN)
        return
    s = get_or_create(update.effective_chat.id)
    s.reminder_offsets = offs
    DB_INSTANCE.save(s)
    if s.is_sent:
        schedule(ctx.application, s)
    await update.effective_message.reply_text(
        "✅ Напоминания: " + ", ".join(fmt_offset(o) for o in offs) + " до дедлайна.")


# --- действия ---
@admin_only
async def send(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    s = DB_INSTANCE.get(update.effective_chat.id)
    if not s or not s.is_complete:
        await update.effective_message.reply_text(
            "Нечего отправлять. Сначала задайте текст и ссылку (/newsurvey).")
        return
    await ctx.bot.send_message(s.chat_id, parse_mode=ParseMode.HTML, text=(
        f"📣 <b>{s.title}</b>\n\nПройдите, пожалуйста, опрос:\n👉 {s.link}"))
    s.is_sent = True
    DB_INSTANCE.save(s)
    schedule(ctx.application, s)
    note = "📤 Опрос опубликован."
    if s.deadline:
        note += f" Напоминания включены; закроется {fmt_dt(s.deadline)}."
    await update.effective_message.reply_text(note)


@admin_only
async def remind_now(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    s = DB_INSTANCE.get(update.effective_chat.id)
    if not s or not s.is_complete:
        await update.effective_message.reply_text("Готового опроса пока нет.")
        return
    tail = f"\n\nДедлайн: {fmt_dt(s.deadline)}." if s.deadline else ""
    await ctx.bot.send_message(s.chat_id, parse_mode=ParseMode.HTML, text=(
        f"⏰ <b>Напоминание</b>\n\nПройдите опрос: <b>{s.title}</b>\n👉 {s.link}{tail}"))


@admin_only
async def status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    s = DB_INSTANCE.get(update.effective_chat.id)
    if not s:
        await update.effective_message.reply_text("Опрос ещё не настроен. Запустите /newsurvey.")
        return
    await update.effective_message.reply_text(
        status_text(s), parse_mode=ParseMode.HTML, disable_web_page_preview=True)


@admin_only
async def delete(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    clear_jobs(ctx.application, chat_id)
    msg = "🗑️ Опрос удалён, напоминания отменены." if DB_INSTANCE.delete(chat_id) \
        else "Удалять нечего."
    await update.effective_message.reply_text(msg)


# ── Запуск ────────────────────────────────────────────────────────────────
DB_INSTANCE: DB = None  # type: ignore

COMMANDS = [
    BotCommand("newsurvey", "Пошаговая настройка опроса"),
    BotCommand("settext", "Текст опроса"), BotCommand("setlink", "Ссылка"),
    BotCommand("setdeadline", "Дедлайн"), BotCommand("setreminders", "Напоминания"),
    BotCommand("send", "Опубликовать опрос"), BotCommand("remind", "Напомнить сейчас"),
    BotCommand("status", "Текущие настройки"), BotCommand("delete", "Удалить опрос"),
    BotCommand("help", "Помощь"),
]


async def _post_init(app: Application):
    await app.bot.set_my_commands(COMMANDS)
    for s in DB_INSTANCE.all():
        schedule(app, s)
    logger.info("Бот готов. Напоминания восстановлены из базы.")


def main():
    global DB_INSTANCE
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не задан. Создайте бота у @BotFather и укажите токен.")

    DB_INSTANCE = DB(DATABASE_PATH)
    app = Application.builder().token(BOT_TOKEN).post_init(_post_init).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("newsurvey", newsurvey)],
        states={
            TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, step_text)],
            LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, step_link)],
            DEADLINE: [MessageHandler(filters.TEXT & ~filters.COMMAND, step_deadline)],
            REMINDERS: [MessageHandler(filters.TEXT & ~filters.COMMAND, step_reminders)],
        },
        fallbacks=[CommandHandler("cancel", cancel)], per_chat=True, per_user=True)
    app.add_handler(conv)
    for name, fn in [("start", cmd_start), ("help", cmd_start), ("settext", set_text),
                     ("setlink", set_link), ("setdeadline", set_deadline),
                     ("setreminders", set_reminders), ("send", send),
                     ("remind", remind_now), ("status", status), ("delete", delete),
                     ("cancel", cancel)]:
        app.add_handler(CommandHandler(name, fn))
    app.add_handler(ChatMemberHandler(on_join, ChatMemberHandler.MY_CHAT_MEMBER))

    logger.info("Запуск polling…")
    app.run_polling(allowed_updates=["message", "my_chat_member"])


if __name__ == "__main__":
    main()
