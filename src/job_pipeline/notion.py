"""Minimal Notion REST client for the Job Pipeline database.

Uses the 2025-09-03 API, where rows live in a *data source* inside a database.
Plain httpx rather than an SDK keeps every request visible.
"""

from datetime import date
from pathlib import Path

import httpx

from .config import settings

API = "https://api.notion.com/v1"
VERSION = "2025-09-03"


def _client() -> httpx.Client:
    token = settings().notion_token
    if not token:
        raise RuntimeError("NOTION_TOKEN is empty in .env")
    return httpx.Client(
        base_url=API,
        headers={"Authorization": f"Bearer {token}", "Notion-Version": VERSION},
        timeout=30,
    )


def _text(value: str) -> dict:
    return {"rich_text": [{"text": {"content": value[:2000]}}]} if value else {"rich_text": []}


def _select(value: str | None) -> dict:
    return {"select": {"name": value} if value else None}


def job_properties(job, **extra) -> dict:
    """Map a store.Job onto Notion properties. `extra` adds or overrides fields."""
    props = {
        "Role": {"title": [{"text": {"content": job.title or job.url}}]},
        "Company": _text(job.company),
        "URL": {"url": job.url or None},
        "Job Key": _text(job.job_key),
        "Source": _select(job.source),
        "Location": _text(job.location),
        "Seniority": _select(job.seniority),
        "Track": _select(job.track),
        "Fit Score": {"number": job.fit_score},
        "Screen Reason": _text(job.screen_reason),
        "Status": {"status": {"name": job.status}},
        "Cover Letter Needed": {"checkbox": bool(job.cover_letter_needed)},
    }
    props.update(extra)
    return props


def create_job(job) -> str:
    with _client() as c:
        r = c.post(
            "/pages",
            json={
                "parent": {"type": "data_source_id", "data_source_id": settings().notion_data_source_id},
                "properties": job_properties(job),
            },
        )
        r.raise_for_status()
        return r.json()["id"]


def update(page_id: str, properties: dict) -> None:
    with _client() as c:
        c.patch(f"/pages/{page_id}", json={"properties": properties}).raise_for_status()


def set_status(page_id: str, status: str, **extra) -> None:
    update(page_id, {"Status": {"status": {"name": status}}, **extra})


def skipped(page_id: str, reason: str) -> None:
    set_status(page_id, "Skipped", **{"Skip Reason": _select(reason)})


def applied(page_id: str) -> None:
    set_status(page_id, "Applied", **{"Applied Date": {"date": {"start": date.today().isoformat()}}})


def checklist(page_id: str, lines: list[str]) -> None:
    update(page_id, {"Checklist": _text("\n".join(lines))})


def upload_cv(page_id: str, files: list[Path], output_dir: Path) -> None:
    """Upload the PDFs and attach them to the row's CV property."""
    attached = []
    with _client() as c:
        for f in files:
            r = c.post("/file_uploads", json={"filename": f.name, "content_type": "application/pdf"})
            r.raise_for_status()
            upload_id = r.json()["id"]
            with f.open("rb") as fh:
                c.post(
                    f"/file_uploads/{upload_id}/send",
                    files={"file": (f.name, fh, "application/pdf")},
                ).raise_for_status()
            attached.append({"type": "file_upload", "file_upload": {"id": upload_id}, "name": f.name})
    update(page_id, {"CV": {"files": attached}, "Output Dir": _text(str(output_dir))})
