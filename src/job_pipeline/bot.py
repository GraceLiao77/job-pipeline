"""Telegram bot: the only interface you touch day to day.

- Send it a job link → it fetches, screens and (since you picked it) tailors a CV.
- Every morning it sends one card per job waiting for review, with ✅ / ❌ buttons.
- Each checklist question arrives as its own message: tap ✅/❌ or reply to correct it.

Slow work (Chrome fetch, `claude -p`) runs in a worker thread; CV generation is
serialised with a lock so two jobs never run Claude at the same time.
"""

import asyncio
import logging
import re
from datetime import time
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton as Btn
from telegram import InlineKeyboardMarkup, Update
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
from .fetch import FetchFailed, fetch, page_from_pasted
from .store import Job

log = logging.getLogger(__name__)
URL_RE = re.compile(r"https?://\S+")
SKIP_REASONS = {"sen": "Too senior", "stk": "Stack mismatch", "loc": "Location", "visa": "Visa / PR", "oth": "Other"}
SKIP_LABELS = {"sen": "太 senior", "stk": "技术栈不符", "loc": "地点", "visa": "签证/PR", "oth": "其他"}

cv_lock = asyncio.Lock()


def card_text(job: Job) -> str:
    return (
        f"<b>{job.title}</b>\n{job.company} · {job.location}\n"
        f"{job.seniority} · {job.track} · 匹配度 <b>{job.fit_score}/10</b> · {job.source}\n"
        f"{job.screen_reason}\n{job.url}"
    )


def review_keyboard(job: Job) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[Btn("✅ 投", callback_data=f"ap:{job.id}"), Btn("❌ 跳过", callback_data=f"sk:{job.id}")]])


# ---- checklist + CV delivery ---------------------------------------------------


async def send_checklist(ctx: ContextTypes.DEFAULT_TYPE, items: list[store.ChecklistItem]) -> None:
    chat = settings().telegram_chat_id
    for item in items:
        msg = await ctx.bot.send_message(
            chat,
            f"❓ {item.question}\n\n<i>点按钮，或直接回复这条消息来更正</i>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                [[Btn("✅ 属实，保留", callback_data=f"ck:{item.id}:keep"), Btn("❌ 删掉", callback_data=f"ck:{item.id}:drop")]]
            ),
        )
        with store.connect() as conn:
            store.set_item(conn, item.id, message_id=msg.message_id)


async def make_cv(ctx: ContextTypes.DEFAULT_TYPE, job: Job, *, regenerate: bool = False) -> None:
    chat = settings().telegram_chat_id
    await ctx.bot.send_message(chat, f"✍️ 正在{'重新' if regenerate else ''}生成 {job.company} 的简历…")
    async with cv_lock:
        try:
            with store.connect() as conn:
                result, items = await asyncio.to_thread(pipeline.generate_cv, conn, job)
        except Exception as e:  # noqa: BLE001
            log.exception("tailor failed")
            await ctx.bot.send_message(chat, f"❗ {job.company} 简历生成失败：{e}")
            return
    for pdf in result.pdfs:
        with pdf.open("rb") as fh:
            await ctx.bot.send_document(chat, fh, filename=f"{job.slug}-{pdf.name}")
    note = f"，另有 {len(items)} 个可选确认项 👇（不回答也能直接投，这是保守版）" if items else ""
    await ctx.bot.send_message(chat, f"📄 {job.company} 简历已就绪{note}\n在电脑上运行 /apply-batch 去填表。")
    await send_checklist(ctx, items)


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
    reply_to = msg.reply_to_message
    if reply_to:
        with store.connect() as conn:
            item = store.item_by_message(conn, reply_to.message_id)
        if item:
            await on_answer(update, ctx, item, msg.text.strip())
            return

    url_match = URL_RE.search(msg.text)
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
    with store.connect() as conn:
        job, regenerate = pipeline.answer(conn, item, value)
    label = {"keep": "✅ 已记为属实", "drop": "❌ 已记为不写"}.get(value, f"✏️ 已记录更正：{value}")
    if update.callback_query:
        await update.callback_query.edit_message_reply_markup(None)
        await update.callback_query.message.reply_text(label)
    else:
        await update.message.reply_text(label)
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
            if item and not item.answer:
                await on_answer(update, ctx, item, rest[1])
            return
        job = store.get_job(conn, int(rest[0]))
        if not job:
            return
        if kind == "ap":
            job = pipeline.approve(conn, job)
            await q.edit_message_reply_markup(None)
            await make_cv(ctx, job)
        elif kind == "sk":
            await q.edit_message_reply_markup(
                InlineKeyboardMarkup([[Btn(label, callback_data=f"sr:{job.id}:{code}")] for code, label in SKIP_LABELS.items()])
            )
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
        app.add_handler(MessageHandler(mine & filters.TEXT & ~filters.COMMAND, on_text))
        app.add_handler(CallbackQueryHandler(on_button))
        app.job_queue.run_daily(daily_digest, time(hour=cfg.digest_hour, tzinfo=ZoneInfo("Pacific/Auckland")))
    app.run_polling()
