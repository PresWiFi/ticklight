"""Telegram bot: quick add, list, complete, plus morning summary and reminders."""

from __future__ import annotations

import asyncio
import html
import logging
import re
from datetime import date, datetime, timedelta

from telegram import InlineKeyboardButton as Btn
from telegram import InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes,
                          MessageHandler, filters)

from .config import Settings
from .tasks import NotFound, Tasks

log = logging.getLogger("ticklight.bot")

HELP = """<b>Ticklight</b>
Just send a message to add a task. Examples:
• <code>call dentist tomorrow at 3pm !!</code>
• <code>water plants every 3 days</code>
• <code>change filter 30 days after done</code>
• <code>standup every weekday at 9:30</code>
• <code>pay rent monthly on the 1st</code>
• <code>bins every tue and fri at 7pm</code>
• <code>review budget every last sunday</code>

<code>!!</code> = high priority, <code>!low</code> = low priority.

<b>Many at once</b>: send several lines, one task per line. Start a line
with <code>+</code> (or indent it) to make it a subtask of the line above.

<b>Subtasks</b>: reply to any message about a single task to add
checklist items to it (one per line), or use <code>/sub 12 buy paint; tape</code>.
Tap /12 on a task to see and tick its checklist.

/today – overdue + today (tap to complete)
/list – everything open
/done 12 · /skip 12 · /del 12 · /undo 12
/id – show your Telegram user id"""


def esc(s: str) -> str:
    return html.escape(s or "", quote=False)


def fmt_when(t: dict, today: date) -> str:
    bits = []
    if t["due_date"]:
        d = date.fromisoformat(t["due_date"])
        delta = (d - today).days
        if delta == 0:
            bits.append("today")
        elif delta == 1:
            bits.append("tomorrow")
        elif delta < 0:
            bits.append(f"⚠️ {-delta}d overdue")
        elif delta < 7:
            bits.append(d.strftime("%a"))
        else:
            bits.append(d.strftime("%d %b"))
    if t["due_time"]:
        bits.append(t["due_time"])
    if t["recurrence_text"]:
        bits.append("🔁 " + t["recurrence_text"])
    if t.get("current_streak", 0) >= 2:
        bits.append(f"🔥{t['current_streak']}")
    if t.get("subtasks"):
        bits.append(f"☑ {t['subtasks_done']}/{len(t['subtasks'])}")
    return " · ".join(bits)


def split_items(text: str) -> list[str]:
    """Subtask titles from a message: one per line, or separated by ';'."""
    items = []
    for line in text.splitlines():
        items += [x.strip(" -•*\t") for x in line.split(";")]
    return [x for x in items if x]


def fmt_task(t: dict, today: date) -> str:
    mark = "❗" if t["priority"] == 2 else ("▫️" if t["priority"] == 0 else "•")
    when = fmt_when(t, today)
    return f"{mark} <b>{esc(t['title'])}</b>{'  <i>' + esc(when) + '</i>' if when else ''}  /{t['id']}"


def task_buttons(tasks: list[dict], scope: str) -> InlineKeyboardMarkup:
    rows = [[Btn(f"✅ {t['title'][:40]}", callback_data=f"done:{t['id']}:{scope}")]
            for t in tasks[:30]]
    rows.append([Btn("🔄 Refresh", callback_data=f"refresh:0:{scope}")])
    return InlineKeyboardMarkup(rows)


class Bot:
    def __init__(self, settings: Settings, tasks: Tasks):
        self.s = settings
        self.tasks = tasks
        self.app: Application | None = None
        self._loop_task: asyncio.Task | None = None
        self._snoozed: dict[int, datetime] = {}

    # ---------- lifecycle ----------
    async def start(self) -> None:
        self.app = Application.builder().token(self.s.telegram_token).build()
        a = self.app
        a.add_handler(CommandHandler(["start", "help"], self.cmd_help))
        a.add_handler(CommandHandler("id", self.cmd_id))
        a.add_handler(CommandHandler("add", self.cmd_add))
        a.add_handler(CommandHandler("today", self.cmd_today))
        a.add_handler(CommandHandler(["list", "all"], self.cmd_list))
        a.add_handler(CommandHandler("done", self.cmd_action))
        a.add_handler(CommandHandler("skip", self.cmd_action))
        a.add_handler(CommandHandler(["del", "delete"], self.cmd_action))
        a.add_handler(CommandHandler("undo", self.cmd_action))
        a.add_handler(CommandHandler(["sub", "subtask"], self.cmd_sub))
        a.add_handler(MessageHandler(filters.Regex(r"^/\d+(@\w+)?$"), self.cmd_show))
        a.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_text))
        a.add_handler(CallbackQueryHandler(self.on_button))
        await a.initialize()
        await a.bot.set_my_commands([
            ("today", "Overdue + today"), ("list", "All open tasks"),
            ("add", "Add a task"), ("sub", "Add subtasks: /sub 12 a; b"),
            ("help", "How to add tasks"),
        ])
        await a.start()
        await a.updater.start_polling(drop_pending_updates=False)
        self._loop_task = asyncio.create_task(self._notify_loop())
        log.info("Telegram bot started")

    async def stop(self) -> None:
        if self._loop_task:
            self._loop_task.cancel()
        if self.app:
            if self.app.updater and self.app.updater.running:
                await self.app.updater.stop()
            if self.app.running:
                await self.app.stop()
            await self.app.shutdown()

    def allowed(self, update: Update) -> bool:
        u = update.effective_user
        return bool(u and u.id in self.s.telegram_users)

    async def _deny(self, update: Update) -> None:
        uid = update.effective_user.id if update.effective_user else "?"
        log.warning("Ignoring message from unauthorised user %s", uid)
        if update.effective_message:
            await update.effective_message.reply_text(
                f"Not authorised. Your user id is {uid}.\n"
                "Add it to TELEGRAM_ALLOWED_USERS and restart the service.")

    # ---------- commands ----------
    async def cmd_id(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await update.effective_message.reply_text(f"Your user id: {update.effective_user.id}")

    async def cmd_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.allowed(update):
            return await self._deny(update)
        await update.effective_message.reply_html(HELP)

    async def cmd_add(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.allowed(update):
            return await self._deny(update)
        text = " ".join(ctx.args or [])
        if not text:
            return await update.effective_message.reply_text("Usage: /add call mum tomorrow 6pm")
        await self._add(update, text)

    async def on_text(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.allowed(update):
            return await self._deny(update)
        msg = update.effective_message
        tid = self._replied_task_id(msg)
        if tid:
            return await self._add_subs(update, tid, split_items(msg.text))
        # Several lines = several tasks (indented or "+" lines are subtasks)
        if len([ln for ln in msg.text.splitlines() if ln.strip()]) > 1:
            try:
                added = self.tasks.bulk_add(msg.text)
            except ValueError as e:
                return await msg.reply_text(str(e))
            today = self.tasks.today()
            body = "\n".join(fmt_task(t, today) for t in added)
            return await msg.reply_html(f"Added {len(added)} task{'s' if len(added) != 1 else ''}:\n{body}")
        if msg.text.strip():
            await self._add(update, msg.text.strip(" -•*\t"))

    def _replied_task_id(self, msg) -> int | None:
        """If this message replies to a bot message about exactly one task, its id."""
        r = msg.reply_to_message
        if not r or not r.from_user or not r.from_user.is_bot:
            return None
        ids = set(re.findall(r"(?<![\w/])/(\d+)\b", r.text or ""))
        return int(ids.pop()) if len(ids) == 1 else None

    async def cmd_sub(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.allowed(update):
            return await self._deny(update)
        # Keep newlines: take everything after "/sub 12"
        m = re.match(r"/\w+(?:@\w+)?\s+/?(\d+)\s+(.+)", update.effective_message.text, re.S)
        if not m:
            return await update.effective_message.reply_text(
                "Usage: /sub 12 buy paint; masking tape\n(or reply to a task message)")
        await self._add_subs(update, int(m.group(1)), split_items(m.group(2)))

    async def _add_subs(self, update: Update, tid: int, items: list[str]) -> None:
        try:
            t = self.tasks.add_subtasks(tid, items)
        except NotFound:
            return await update.effective_message.reply_text("No such task.")
        except ValueError as e:
            return await update.effective_message.reply_text(str(e))
        await update.effective_message.reply_html(
            self._detail(t), reply_markup=self._detail_kb(t))

    async def _add(self, update: Update, text: str) -> None:
        t = self.tasks.quick_add(text)
        when = t.pop("parsed_summary", "") or "no date"
        kb = InlineKeyboardMarkup([[
            Btn("↩️ Undo", callback_data=f"del:{t['id']}:x"),
            Btn("✅ Done", callback_data=f"done:{t['id']}:x"),
        ]])
        await update.effective_message.reply_html(
            f"➕ <b>{esc(t['title'])}</b>  /{t['id']}\n<i>{esc(when)}</i>\n"
            "<i>Reply to this message to add subtasks.</i>", reply_markup=kb)

    def _today_view(self) -> tuple[str, InlineKeyboardMarkup]:
        b = self.tasks.board(self.s.upcoming_days)
        today = self.tasks.today()
        items = b["overdue"] + b["today"]
        if not items:
            text = "🎉 Nothing due today."
            if b["done_today"]:
                text += f" ({len(b['done_today'])} done)"
        else:
            parts = []
            if b["overdue"]:
                parts.append("<b>Overdue</b>\n" + "\n".join(fmt_task(t, today) for t in b["overdue"]))
            if b["today"]:
                parts.append("<b>Today</b>\n" + "\n".join(fmt_task(t, today) for t in b["today"]))
            if b["done_today"]:
                parts.append(f"✅ {len(b['done_today'])} done today")
            text = "\n\n".join(parts)
        return text, task_buttons(items, "today")

    def _list_view(self) -> tuple[str, InlineKeyboardMarkup]:
        tasks = self.tasks.open_tasks()
        today = self.tasks.today()
        if not tasks:
            return "No open tasks.", task_buttons([], "list")
        text = "\n".join(fmt_task(t, today) for t in tasks[:80])
        if len(tasks) > 80:
            text += f"\n…and {len(tasks) - 80} more"
        return text, task_buttons(tasks, "list")

    async def cmd_today(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.allowed(update):
            return await self._deny(update)
        text, kb = self._today_view()
        await update.effective_message.reply_html(text, reply_markup=kb)

    async def cmd_list(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.allowed(update):
            return await self._deny(update)
        text, kb = self._list_view()
        await update.effective_message.reply_html(text, reply_markup=kb)

    async def cmd_show(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Tapping /12 in a list shows the task with action buttons."""
        if not self.allowed(update):
            return await self._deny(update)
        tid = int(update.effective_message.text[1:].split("@")[0])
        try:
            t = self.tasks.get(tid)
        except NotFound:
            return await update.effective_message.reply_text("No such task.")
        await update.effective_message.reply_html(
            self._detail(t), reply_markup=self._detail_kb(t))

    def _detail(self, t: dict) -> str:
        lines = [fmt_task(t, self.tasks.today())]
        if t["notes"]:
            lines.append(esc(t["notes"]))
        if t["recurrence"]:
            lines.append(f"Streak: {t['current_streak']} (best {t['best_streak']})")
        if t["subtasks"]:
            lines.append("")
            lines += [f"{'☑' if st['done'] else '☐'} {esc(st['title'])}" for st in t["subtasks"]]
        lines.append("\n<i>Reply to add subtasks.</i>")
        if t["status"] != "open":
            lines.append(f"<i>{t['status']}</i>")
        return "\n".join(lines)

    def _detail_kb(self, t: dict) -> InlineKeyboardMarkup:
        tid = t["id"]
        row = [Btn("✅ Done", callback_data=f"done:{tid}:x")]
        if t["recurrence"]:
            row.append(Btn("⏭ Skip", callback_data=f"skip:{tid}:x"))
        row.append(Btn("🗑 Delete", callback_data=f"del:{tid}:x"))
        rows = [[Btn(f"{'☑' if st['done'] else '☐'} {st['title'][:45]}",
                     callback_data=f"st:{st['id']}:{tid}")] for st in t["subtasks"][:40]]
        return InlineKeyboardMarkup(rows + [row])

    async def cmd_action(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.allowed(update):
            return await self._deny(update)
        cmd = update.effective_message.text.split()[0][1:].split("@")[0]
        cmd = {"delete": "del"}.get(cmd, cmd)
        if not ctx.args or not ctx.args[0].lstrip("/").isdigit():
            return await update.effective_message.reply_text(f"Usage: /{cmd} <task id>")
        msg = self._apply(cmd, int(ctx.args[0].lstrip("/")))
        await update.effective_message.reply_html(msg)

    def _apply(self, action: str, tid: int) -> str:
        try:
            if action == "done":
                t = self.tasks.complete(tid)
                if t["recurrence"]:
                    nd = date.fromisoformat(t["due_date"]).strftime("%a %d %b")
                    streak = f" 🔥{t['streak']}" if t["streak"] >= 2 else ""
                    return f"✅ <b>{esc(t['title'])}</b>{streak}\nNext: {nd}"
                return f"✅ <b>{esc(t['title'])}</b>"
            if action == "skip":
                t = self.tasks.skip(tid)
                nd = date.fromisoformat(t["due_date"]).strftime("%a %d %b")
                return f"⏭ Skipped <b>{esc(t['title'])}</b>. Next: {nd}"
            if action == "del":
                t = self.tasks.delete(tid)
                return f"🗑 Deleted <b>{esc(t['title'])}</b>"
            if action == "undo":
                t = self.tasks.undo_complete(tid)
                return f"↩️ Undid completion of <b>{esc(t['title'])}</b>"
            if action == "restore":
                t = self.tasks.restore(tid)
                return f"↩️ Restored <b>{esc(t['title'])}</b>"
        except NotFound:
            return "No such task."
        except ValueError as e:
            return f"Can't do that: {esc(str(e))}"
        return "Unknown action."

    # ---------- buttons ----------
    async def on_button(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        q = update.callback_query
        if not self.allowed(update):
            return await q.answer("Not authorised")
        action, tid, scope = (q.data.split(":") + ["", ""])[:3]
        tid = int(tid or 0)
        if action == "st":  # toggle a subtask, redraw the task detail
            try:
                t = self.tasks.toggle_subtask(tid)
            except NotFound:
                return await q.answer("That subtask no longer exists")
            await q.answer()
            try:
                await q.edit_message_text(self._detail(t), parse_mode=ParseMode.HTML,
                                          reply_markup=self._detail_kb(t))
            except Exception as e:
                log.debug("edit failed: %s", e)
            return
        if action == "snooze":
            self._snoozed[tid] = self.tasks.now() + timedelta(hours=1)
            self.tasks.mark_reminded(tid, None)  # make it eligible again after the snooze
            await q.answer("Snoozed for 1 hour")
            return await q.edit_message_reply_markup(None)
        if action != "refresh":
            msg = self._apply(action, tid)
            await q.answer(html.unescape(msg.replace("<b>", "").replace("</b>", ""))[:190])
        else:
            await q.answer()
        try:
            if scope == "today":
                text, kb = self._today_view()
                await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
            elif scope == "list":
                text, kb = self._list_view()
                await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
            else:
                undo = {"done": "undo", "skip": "undo", "del": "restore"}.get(action)
                kb = InlineKeyboardMarkup([[Btn("↩️ Undo", callback_data=f"{undo}:{tid}:x")]]) \
                    if undo else None
                await q.edit_message_text(msg, parse_mode=ParseMode.HTML, reply_markup=kb)
        except Exception as e:  # "message is not modified" etc.
            log.debug("edit failed: %s", e)

    # ---------- proactive messages ----------
    async def send_all(self, text: str, kb: InlineKeyboardMarkup | None = None) -> None:
        for uid in self.s.telegram_users:
            try:
                await self.app.bot.send_message(uid, text, parse_mode=ParseMode.HTML,
                                                reply_markup=kb)
            except Exception as e:
                log.warning("send to %s failed: %s", uid, e)

    async def _notify_loop(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("notify loop error")
            await asyncio.sleep(30)

    async def tick(self) -> None:
        now = self.tasks.now()
        # Morning summary, once per day, within 3 hours of the configured time.
        if self.s.summary_time:
            hh, mm = map(int, self.s.summary_time.split(":"))
            at = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            today = now.date().isoformat()
            if at <= now < at + timedelta(hours=3) and self.tasks.db.get_meta("last_summary") != today:
                self.tasks.db.set_meta("last_summary", today)
                text, kb = self._today_view()
                await self.send_all("☀️ <b>Good morning!</b>\n\n" + text, kb)

        # Due-time reminders
        for t in self.tasks.due_reminders(self.s.reminder_lead_minutes):
            snooze_until = self._snoozed.get(t["id"])
            if snooze_until and now < snooze_until:
                continue
            self._snoozed.pop(t["id"], None)
            key = f"{t['due_date']}T{t['due_time']}"
            self.tasks.mark_reminded(t["id"], key)
            row = [Btn("✅ Done", callback_data=f"done:{t['id']}:x")]
            if t["recurrence"]:
                row.append(Btn("⏭ Skip", callback_data=f"skip:{t['id']}:x"))
            row.append(Btn("💤 1h", callback_data=f"snooze:{t['id']}:x"))
            await self.send_all(f"⏰ <b>{esc(t['title'])}</b> — due {t['due_time']}  /{t['id']}",
                                InlineKeyboardMarkup([row]))
