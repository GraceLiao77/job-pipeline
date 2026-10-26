"""The job lifecycle, independent of Telegram: ingest → review → tailor → apply.

All state lives in SQLite. Notion only tracks applications: a row is created
when you mark a job Applied (with the CV that was sent). Inbox, skipped and
filtered jobs never reach Notion. A Notion failure is logged, never fatal.
"""

import logging
import re
from dataclasses import dataclass

from . import notion, store
from .config import settings
from .claude_cli import Cancelled
from .fetch import Page, canonical_url
from .screen import screen
from .store import Job
from .tailor import TailorResult, existing_slugs, make_slug, record_answer, record_revision_facts, revise_cv, tailor

log = logging.getLogger(__name__)

COMPANY_NOISE = r"\b(ltd|limited|nz|new zealand|group|inc|pty|co)\b"


def job_key(company: str, title: str) -> str:
    def norm(s: str) -> str:
        s = re.sub(r"[^a-z0-9 ]+", " ", s.lower())
        return " ".join(s.split())

    return f"{norm(re.sub(COMPANY_NOISE, '', company, flags=re.I))}|{norm(title)}"


def _mirror(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception as e:  # noqa: BLE001 — Notion is a mirror, never a blocker
        log.warning("Notion sync failed (%s): %s", fn.__name__, e)
        return None


@dataclass
class Ingested:
    job: Job
    duplicate: bool


def ingest(conn, page: Page, *, manual: bool) -> Ingested:
    """Screen a fetched page and record it.

    manual=True means you forwarded the link yourself, so it skips review and goes
    straight to Approved — unless it hits a hard filter, in which case it waits in
    Inbox so you can decide.
    """
    s = screen(page)
    key = job_key(s.company, s.title)
    if existing := store.find_by_key(conn, key):
        return Ingested(existing, duplicate=True)

    if not s.is_job_ad:
        raise ValueError("这个页面看起来不是单个职位广告")
    if manual:
        status = "Inbox" if s.filtered else "Approved"
    else:
        # Links you forward are never filtered by score; alert jobs below MIN_FIT are.
        status = "Filtered" if s.filtered or s.fit_score < settings().min_fit else "Inbox"

    job = store.insert_job(
        conn,
        url=canonical_url(page.url) if page.url else "",
        source=page.source,
        company=s.company,
        title=s.title,
        location=s.location,
        jd_text=page.text,
        job_key=key,
        status=status,
        fit_score=s.fit_score,
        seniority=s.seniority,
        track=s.track,
        screen_reason=s.reason,
        cover_letter_needed=int(s.cover_letter_needed),
        slug=make_slug(s.company, s.title, existing_slugs()),
    )
    return Ingested(job, duplicate=False)


def record_prefiltered(conn, page: Page, reason: str) -> Job:
    """Store a job the title rules rejected, so its URL is skipped next time."""
    return store.insert_job(
        conn,
        url=canonical_url(page.url),
        source=page.source,
        title=page.title[:200],
        jd_text=page.text,
        job_key=f"prefilter|{canonical_url(page.url)}",
        status="Filtered",
        screen_reason=f"规则预过滤：{reason}",
    )


def approve(conn, job: Job) -> Job:
    return store.update_job(conn, job.id, status="Approved")


def skip(conn, job: Job, reason: str) -> Job:
    # The reason is kept in the log only; Notion tracks applications, not skips.
    log.info("skipped %s (%s): %s", job.company, job.title, reason)
    return store.update_job(conn, job.id, status="Skipped")


def generate_cv(conn, job: Job, cancel=None) -> tuple[TailorResult, list[store.ChecklistItem]]:
    """Tailor, render and record a CV. Raises claude_cli.Cancelled if you press ⏹;
    the job's status is only changed once the CV is actually done."""
    if cancel is not None and cancel.is_set():
        raise Cancelled
    result = tailor(job, cancel)
    generation = store.latest_generation(conn, job.id) + 1
    items = store.add_checklist(conn, job.id, result.questions, generation)
    store.update_job(conn, job.id, status="CV Ready")
    return result, items


def answer(conn, item: store.ChecklistItem, value: str) -> tuple[Job, bool]:
    """Record (or change) a checklist answer. Returns (job, regenerate?).

    First answers regenerate once every item in the round is answered and at least
    one adds something. A *changed* answer regenerates as soon as the round is
    complete — even keep → drop, because the CV may already contain the claim.
    facts.md is append-only; the later line overrides the earlier one.
    """
    job = store.get_job(conn, item.job_id)
    previous = item.answer
    if previous == value:
        return job, False
    store.set_item(conn, item.id, answer=value)
    record_answer(job, item.question, value)
    round_items = store.checklist_for(conn, job.id, item.generation)
    done = all(i.answer for i in round_items)
    if previous is not None:
        return job, done
    adds = any(i.answer != "drop" for i in round_items)
    return job, done and adds


def revise(conn, job: Job, instruction: str) -> tuple[TailorResult, str]:
    """Apply your free-text change request to the job's CV. Returns (result, summary)."""
    result, summary, facts = revise_cv(job, instruction)
    record_revision_facts(job, facts)
    return result, summary


def mark_applied(conn, job: Job) -> Job:
    """Record the application, and only now put it in Notion with the PDFs sent."""
    job = store.update_job(conn, job.id, status="Applied")
    if not job.notion_page_id:
        if page_id := _mirror(notion.create_job, job):
            job = store.update_job(conn, job.id, notion_page_id=page_id)
    if job.notion_page_id:
        _mirror(notion.applied, job.notion_page_id)
        out = settings().career_craft_dir / "workspace" / "output" / job.slug
        pdfs = [p for p in (out / "grace-liao-cv.pdf", out / "grace-liao-cover-letter.pdf") if p.exists()]
        if pdfs:
            _mirror(notion.upload_cv, job.notion_page_id, pdfs, out)
    return job
