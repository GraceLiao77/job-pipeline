"""Telegram bot: the only interface you touch day to day.

- Send it a job link → it fetches, screens and (since you picked it) tailors a CV.
- Every morning it sends one card per job waiting for review, with ✅ / ❌ buttons.
- Each checklist question arrives as its own message: tap ✅/❌ or reply to correct it.

Slow work (Chrome fetch, `claude -p`) runs in a worker thread; CV generation is
serialised with a lock so two jobs never run Claude at the same time.
"""

import asyncio
import html
import logging
import re
import threading
from datetime import time
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton as Btn
from telegram import ForceReply, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from . import pipeline, store
from .config import settings
from .claude_cli import Cancelled
from .fetch import FetchFailed, fetch, page_from_pasted
from .store import Job

log = logging.getLogger(__name__)
URL_RE = re.compile(r"https?://\S+")
SKIP_REASONS = {"sen": "Too senior", "stk": "Stack mismatch", "loc": "Location", "visa": "Visa / PR", "oth": "Other"}
SKIP_LABELS = {"sen": "太 senior", "stk": "技术栈不符", "loc": "地点", "visa": "签证/PR", "oth": "其他"}

cv_lock = asyncio.Lock()
# job id → flag the ⏹ button sets; checked while queued and while Claude runs.
cv_cancels: dict[int, threading.Event] = {}


def skip_reasons_keyboard(job_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[Btn(label, callback_data=f"sr:{job_id}:{code}")] for code, label in SKIP_LABELS.items()])


def card_text(job: Job) -> str:
    e = html.escape
    return (
        f"<b>{e(job.title)}</b>\n{e(job.company)} · {e(job.location)}\n"
        f"{job.seniority} · {job.track} · 匹配度 <b>{job.fit_score}/10</b> · {job.source}\n"
        f"{e(job.screen_reason)}\n{e(job.url)}"
    )


def review_keyboard(job: Job) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[Btn("✅ 投", callback_data=f"ap:{job.id}"), Btn("❌ 跳过", callback_data=f"sk:{job.id}")]])


# ---- checklist + CV delivery ---------------------------------------------------


ANSWER_LABELS = {"keep": "✅ 已记为属实", "drop": "❌ 已记为不写"}
# Typed replies to a question message that mean a button, not a correction.
TYPED_ANSWERS = {
    "drop": {"删掉", "删", "删除", "不属实", "不对", "没有", "no", "drop"},
    "keep": {"保留", "属实", "对", "是", "yes", "keep"},
}


def checklist_keyboard(item_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [Btn("✅ 属实，保留", callback_data=f"ck:{item_id}:keep"), Btn("❌ 删掉", callback_data=f"ck:{item_id}:drop")],
            [Btn("✏️ 属实，但要改写法", callback_data=f"ck:{item_id}:edit")],
        ]
    )


def answered_keyboard(item_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[Btn("↩️ 选错了，改答案", callback_data=f"ck:{item_id}:undo")]])


async def send_checklist(ctx: ContextTypes.DEFAULT_TYPE, items: list[store.ChecklistItem]) -> None:
    chat = settings().telegram_chat_id
    for item in items:
        msg = await ctx.bot.send_message(
            chat,
            f"❓ {html.escape(item.question)}",
            parse_mode="HTML",
            reply_markup=checklist_keyboard(item.id),
        )
        with store.connect() as conn:
            store.set_item(conn, item.id, message_id=msg.message_id)


async def make_cv(ctx: ContextTypes.DEFAULT_TYPE, job: Job, *, regenerate: bool = False) -> None:
    chat = settings().telegram_chat_id
    cancel = cv_cancels.setdefault(job.id, threading.Event())
    status = await ctx.bot.send_message(
        chat,
        f"✍️ 正在{'重新' if regenerate else ''}生成 {job.company} 的简历…\n觉得不合适可以随时停下：",
        reply_markup=InlineKeyboardMarkup([[Btn("⏹ 停止，不投了", callback_data=f"st:{job.id}")]]),
    )
    try:
        async with cv_lock:
            try:
                with store.connect() as conn:
                    result, items = await asyncio.to_thread(pipeline.generate_cv, conn, job, cancel)
            except Cancelled:
                await status.edit_text(
                    f"⏹ 已停止，{job.company} 不投了（已记为跳过）。顺手选个原因？",
                    reply_markup=skip_reasons_keyboard(job.id),
                )
                return
            except Exception as e:  # noqa: BLE001
                log.exception("tailor failed")
                await status.edit_reply_markup(None)
                await ctx.bot.send_message(chat, f"❗ {job.company} 简历生成失败：{e}")
                return
    finally:
        cv_cancels.pop(job.id, None)
    await status.edit_reply_markup(None)
    await send_pdfs(ctx, job, result.pdfs)
    note = f"，另有 {len(items)} 个可选确认项 👇（不回答也能直接投，这是保守版）" if items else ""
    ready = await ctx.bot.send_message(
        chat, f"📄 {job.company} 简历已就绪{note}\n想改哪里，直接回复这份 PDF 说就行。\n在电脑上运行 /apply-batch 去填表。"
    )
    with store.connect() as conn:
        store.remember_cv_message(conn, ready.message_id, job.id)
    await send_checklist(ctx, items)


async def send_pdfs(ctx: ContextTypes.DEFAULT_TYPE, job: Job, pdfs) -> None:
    chat = settings().telegram_chat_id
    for pdf in pdfs:
        with pdf.open("rb") as fh:
            sent = await ctx.bot.send_document(chat, fh, filename=f"{job.slug}-{pdf.name}")
        with store.connect() as conn:
            store.remember_cv_message(conn, sent.message_id, job.id)


async def revise_cv(ctx: ContextTypes.DEFAULT_TYPE, job: Job, instruction: str) -> None:
    chat = settings().telegram_chat_id
    await ctx.bot.send_message(chat, f"✍️ 正在按你的意见修改 {job.company} 的简历…")
    async with cv_lock:
        try:
            with store.connect() as conn:
                result, summary = await asyncio.to_thread(pipeline.revise, conn, job, instruction)
        except Exception as e:  # noqa: BLE001
            log.exception("revise failed")
            await ctx.bot.send_message(chat, f"❗ 修改失败：{e}")
            return
    await send_pdfs(ctx, job, result.pdfs)
    await ctx.bot.send_message(chat, f"✅ 已修改：{summary}\n还要改就继续回复这份 PDF。")


# ---- handlers -----------------------------------------------------------------


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    configured = settings().telegram_chat_id
    if configured is None:
        await update.message.reply_text(
            f"你的 chat ID 是 {update.effective_chat.id}\n把它填进 .env 的 TELEGRAM_CHAT_ID，然后重启 bot。"
        )
    elif update.effective_chat.id == configured:
        await update.message.reply_text("👋 把职位链接发给我就行。每天早上会推送待审核的岗位。")


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    url_match = URL_RE.search(msg.text)

    # A checklist correction: a reply to the question (or to the ✏️ prompt), or the
    # next plain message after tapping ✏️.
    reply_to = msg.reply_to_message
    item_id = ctx.chat_data.get("edit_prompts", {}).get(reply_to.message_id) if reply_to else None
    if item_id is None and not reply_to and not url_match:
        item_id = ctx.chat_data.get("editing_item")
    with store.connect() as conn:
        item = store.get_item(conn, item_id) if item_id else None
        if item is None and reply_to:
            item = store.item_by_message(conn, reply_to.message_id)
        cv_job = store.job_for_cv_message(conn, reply_to.message_id) if reply_to and not item else None
    if item:
        ctx.chat_data.pop("editing_item", None)
        text = msg.text.strip()
        typed = next((k for k, words in TYPED_ANSWERS.items() if text.lower() in words), None)
        await on_answer(update, ctx, item, typed or text)
        return
    if cv_job:
        await revise_cv(ctx, cv_job, msg.text.strip())
        return

    pending_url = ctx.chat_data.pop("pending_url", None)

    if url_match and len(msg.text) < 300:
        url = url_match.group(0)
        await msg.reply_text("🔎 抓取 JD 中…")
        try:
            page = await asyncio.to_thread(fetch, url)
        except FetchFailed as e:
            ctx.chat_data["pending_url"] = url
            await msg.reply_text(f"抓不到（{e}）。请把 JD 全文直接粘贴发给我。")
            return
    elif len(msg.text) >= 300:
        page = page_from_pasted(url_match.group(0) if url_match else pending_url, msg.text)
    else:
        await msg.reply_text("发职位链接给我，或者直接粘贴 JD 全文。")
        return

    await msg.reply_text("🧐 预筛中…")
    try:
        with store.connect() as conn:
            res = await asyncio.to_thread(pipeline.ingest, conn, page, manual=True)
    except Exception as e:  # noqa: BLE001
        log.exception("ingest failed")
        await msg.reply_text(f"❗ 预筛失败：{e}")
        return

    job = res.job
    if res.duplicate:
        await msg.reply_text(f"这个岗位已经有了（状态：{job.status}）。\n{job.company} · {job.title}")
    elif job.status == "Inbox":
        await msg.reply_text("⚠️ 命中了过滤规则，要不要仍然投？\n\n" + card_text(job), parse_mode="HTML", reply_markup=review_keyboard(job))
        with store.connect() as conn:
            store.update_job(conn, job.id, card_sent=1)
    else:
        await msg.reply_text(card_text(job), parse_mode="HTML")
        await make_cv(ctx, job)


async def on_answer(update: Update, ctx: ContextTypes.DEFAULT_TYPE, item: store.ChecklistItem, value: str) -> None:
    changed = item.answer is not None and item.answer != value
    with store.connect() as conn:
        job, regenerate = pipeline.answer(conn, item, value)
    label = ANSWER_LABELS.get(value, f"✏️ 已记录更正：{html.escape(value)}")
    if changed:
        label = f"🔁 已改答案 → {label}"
    if update.callback_query:
        await update.callback_query.edit_message_text(
            f"❓ {html.escape(item.question)}\n\n<b>{label}</b>", parse_mode="HTML", reply_markup=answered_keyboard(item.id)
        )
    else:
        await update.message.reply_text(label)
        if item.message_id:
            try:
                await ctx.bot.edit_message_reply_markup(
                    settings().telegram_chat_id, item.message_id, reply_markup=answered_keyboard(item.id)
                )
            except Exception:  # noqa: BLE001 — message too old or unchanged; not important
                pass
    if regenerate:
        await make_cv(ctx, job, regenerate=True)


async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    if update.effective_chat.id != settings().telegram_chat_id:
        return
    kind, *rest = q.data.split(":")
    with store.connect() as conn:
        if kind == "ck":
            item = store.get_item(conn, int(rest[0]))
            if not item:
                return
            if rest[1] == "undo":
                await q.edit_message_text(
                    f"❓ {html.escape(item.question)}\n\n<i>当前：{ANSWER_LABELS.get(item.answer, '✏️ ' + html.escape(str(item.answer)))}，重新选：</i>",
                    parse_mode="HTML",
                    reply_markup=checklist_keyboard(item.id),
                )
            elif rest[1] == "edit":
                await q.edit_message_reply_markup(None)
                prompt = await q.message.reply_text(
                    "✏️ 请写出准确的说法（比如具体做了什么、真实的数字），下一版简历会按你的写：",
                    reply_markup=ForceReply(input_field_placeholder="准确的说法…"),
                )
                ctx.chat_data.setdefault("edit_prompts", {})[prompt.message_id] = item.id
                ctx.chat_data["editing_item"] = item.id
            else:
                await on_answer(update, ctx, item, rest[1])
            return
        job = store.get_job(conn, int(rest[0]))
        if not job:
            return
        if kind == "st":
            if event := cv_cancels.get(job.id):
                event.set()
            # Mark skipped right away; if generation had just finished, this still wins.
            pipeline.skip(conn, job, "Other")
            await q.edit_message_reply_markup(None)
            await q.message.reply_text("⏹ 正在停止…")
        elif kind == "ap":
            job = pipeline.approve(conn, job)
            await q.edit_message_reply_markup(None)
            await make_cv(ctx, job)
        elif kind == "sk":
            await q.edit_message_reply_markup(skip_reasons_keyboard(job.id))
        elif kind == "sr":
            pipeline.skip(conn, job, SKIP_REASONS[rest[1]])
            await q.edit_message_reply_markup(None)
            await q.message.reply_text(f"已跳过：{SKIP_LABELS[rest[1]]}")


async def daily_digest(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat = settings().telegram_chat_id
    with store.connect() as conn:
        jobs = [j for j in store.jobs_with_status(conn, "Inbox") if not j.card_sent]
        for job in jobs:
            await ctx.bot.send_message(chat, card_text(job), parse_mode="HTML", reply_markup=review_keyboard(job))
            store.update_job(conn, job.id, card_sent=1)
        ready = store.jobs_with_status(conn, "CV Ready")
    if ready:
        await ctx.bot.send_message(chat, f"📌 {len(ready)} 份简历已就绪还没投，记得运行 /apply-batch。")


alert_lock = asyncio.Lock()


def summary_text(s) -> str:
    return (
        f"📬 读了 {s.emails} 封新的 job alert，找到 {s.links} 个职位链接\n"
        f"新职位 {s.new_jobs} 个：{s.inbox} 个待你审核，{s.filtered} 个被过滤（其中 {s.prefiltered} 个看标题就过滤，没用 Claude）\n"
        f"已处理过的 {s.known} 个跳过" + (f"，{s.failed} 个抓取/预筛失败" if s.failed else "")
    )


async def check_alerts(ctx: ContextTypes.DEFAULT_TYPE, days: int = 2) -> None:
    """Read new alert emails and screen their jobs. Cards go out with the 8:00 digest."""
    from . import alerts
    from .gmail import gmail_service

    chat = settings().telegram_chat_id
    if alert_lock.locked():
        return
    async with alert_lock:
        try:
            with store.connect() as conn:
                summary = await asyncio.to_thread(lambda: alerts.ingest_alerts(conn, gmail_service(), days))
        except Exception as e:  # noqa: BLE001
            log.exception("alert ingestion failed")
            await ctx.bot.send_message(chat, f"❗ 读取 job alert 失败：{e}")
            return
    if summary.emails:
        await ctx.bot.send_message(chat, summary_text(summary))


async def scheduled_check(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await check_alerts(ctx)
    # If the check ran late (Mac was asleep at 7:30), don't wait until tomorrow's digest.
    from datetime import datetime

    if datetime.now(ZoneInfo("Pacific/Auckland")).hour >= settings().digest_hour:
        await daily_digest(ctx)


async def cmd_check(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("📬 正在读取 job alert 邮件，每个新职位约 20 秒…")
    await check_alerts(ctx)
    await daily_digest(ctx)


REPLY_TEMPLATES = {
    "interview": "🎉 {company} 发来面试邀请！\n{summary}\n（Notion 已改为 Interview）",
    "action_required": "📝 {company} 需要你行动：\n{summary}",
    "rejection": "📭 {company}：{summary}\n（Notion 已改为 Rejected）",
    "offer": "🏆 {company} 发来 offer！\n{summary}",
}
reply_lock = asyncio.Lock()


async def check_replies(ctx: ContextTypes.DEFAULT_TYPE, days: int = 3, quiet: bool = True) -> None:
    """Read Gmail for replies to applications and tell you about the ones that matter."""
    from . import responses
    from .gmail import gmail_service

    chat = settings().telegram_chat_id
    if reply_lock.locked():
        return
    async with reply_lock:
        try:
            with store.connect() as conn:
                updates = await asyncio.to_thread(lambda: responses.check_responses(conn, gmail_service(), days))
        except Exception as e:  # noqa: BLE001
            log.exception("reply check failed")
            await ctx.bot.send_message(chat, f"❗ 检查回复邮件失败：{e}")
            return
    for u in updates:
        text = REPLY_TEMPLATES[u.kind].format(company=u.job.company, summary=u.summary)
        await ctx.bot.send_message(chat, f"{text}\n\n✉️ {u.subject}")
    if not updates and not quiet:
        await ctx.bot.send_message(chat, "📭 没有新的面试 / 拒信 / 待处理邮件。")


async def scheduled_replies(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    from datetime import datetime

    if 8 <= datetime.now(ZoneInfo("Pacific/Auckland")).hour <= 20:  # daytime only
        await check_replies(ctx)


async def cmd_replies(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("✉️ 正在检查回复邮件…")
    await check_replies(ctx, quiet=False)


async def cmd_ready(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    with store.connect() as conn:
        jobs = store.jobs_with_status(conn, "CV Ready")
    lines = [f"#{j.id} {j.company} — {j.title}" for j in jobs] or ["没有待投递的岗位"]
    await update.message.reply_text("\n".join(lines))


def run() -> None:
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    cfg = settings()
    app = Application.builder().token(cfg.telegram_token).build()
    app.add_handler(CommandHandler("start", start))
    if cfg.telegram_chat_id is None:
        log.warning("TELEGRAM_CHAT_ID not set — only /start works until you add it")
    else:
        mine = filters.Chat(cfg.telegram_chat_id)
        app.add_handler(CommandHandler("ready", cmd_ready, filters=mine))
        app.add_handler(CommandHandler("check", cmd_check, filters=mine))
        app.add_handler(CommandHandler("replies", cmd_replies, filters=mine))
        app.add_handler(MessageHandler(mine & filters.TEXT & ~filters.COMMAND, on_text))
        app.add_handler(CallbackQueryHandler(on_button))
        nz = ZoneInfo("Pacific/Auckland")
        # Run even if the Mac was asleep at the scheduled time (within 6 hours), once.
        late_ok = {"misfire_grace_time": 6 * 3600, "coalesce": True}
        app.job_queue.run_daily(scheduled_check, time(hour=cfg.digest_hour - 1, minute=30, tzinfo=nz), job_kwargs=late_ok)
        app.job_queue.run_daily(daily_digest, time(hour=cfg.digest_hour, tzinfo=nz), job_kwargs=late_ok)
        # Replies: every 2 hours, daytime only (checked inside), first run 1 minute after start.
        app.job_queue.run_repeating(scheduled_replies, interval=2 * 3600, first=60)
    app.run_polling()
