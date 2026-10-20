"""The job lifecycle, independent of Telegram: ingest → review → tailor → apply.

Every state change goes to SQLite first, then is mirrored to Notion. A Notion
failure is logged and never blocks the pipeline — the row can be re-synced later.
"""

import logging
import re
from dataclasses import dataclass

from . import notion, store
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
        status = "Filtered" if s.filtered else "Inbox"

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
    if page_id := _mirror(notion.create_job, job):
        job = store.update_job(conn, job.id, notion_page_id=page_id)
    return Ingested(job, duplicate=False)


def approve(conn, job: Job) -> Job:
    job = store.update_job(conn, job.id, status="Approved")
    if job.notion_page_id:
        _mirror(notion.set_status, job.notion_page_id, "Approved")
    return job


def skip(conn, job: Job, reason: str) -> Job:
    job = store.update_job(conn, job.id, status="Skipped")
    if job.notion_page_id:
        _mirror(notion.skipped, job.notion_page_id, reason)
    return job


def generate_cv(conn, job: Job, cancel=None) -> tuple[TailorResult, list[store.ChecklistItem]]:
    """Tailor, render and record a CV. Raises claude_cli.Cancelled if you press ⏹;
    the job's status is only changed once the CV is actually done."""
    if cancel is not None and cancel.is_set():
        raise Cancelled
    result = tailor(job, cancel)
    generation = store.latest_generation(conn, job.id) + 1
    items = store.add_checklist(conn, job.id, result.questions, generation)
    job = store.update_job(conn, job.id, status="CV Ready")
    if job.notion_page_id:
        _mirror(notion.set_status, job.notion_page_id, "CV Ready")
        _mirror(notion.upload_cv, job.notion_page_id, result.pdfs, result.output_dir)
        _mirror(notion.checklist, job.notion_page_id, [i.question.split("\n→")[0] for i in items])
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
    if job.notion_page_id:
        _mirror(notion.upload_cv, job.notion_page_id, result.pdfs, result.output_dir)
    return result, summary


def mark_applied(conn, job: Job) -> Job:
    job = store.update_job(conn, job.id, status="Applied")
    if job.notion_page_id:
        _mirror(notion.applied, job.notion_page_id)
    return job
