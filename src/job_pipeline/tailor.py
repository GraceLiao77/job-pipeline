"""Generate a tailored CV by running career-craft's /auto-tailor skill, then render PDFs."""

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from . import claude_cli
from .config import settings
from .store import Job

TOOLS = ["Read", "Write", "Edit", "Glob", "Grep"]


@dataclass
class TailorResult:
    output_dir: Path
    pdfs: list[Path]
    questions: list[str]


def make_slug(company: str, title: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", company.lower()).strip("-") or "job"
    if base not in taken:
        return base
    role = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40]
    slug, n = f"{base}-{role}", 2
    while slug in taken:
        slug, n = f"{base}-{role}-{n}", n + 1
    return slug


def existing_slugs() -> set[str]:
    out = settings().career_craft_dir / "workspace" / "output"
    return {p.name for p in out.iterdir() if p.is_dir()}


def write_jd(job: Job) -> Path:
    path = settings().career_craft_dir / "workspace" / "job-descriptions" / f"{job.slug}.md"
    path.write_text(
        f"# {job.title} — {job.company}\n\n"
        f"Source: {job.source} · {job.url}\nLocation: {job.location}\n\n---\n\n{job.jd_text}\n"
    )
    return path


def tailor(job: Job) -> TailorResult:
    cc = settings().career_craft_dir
    jd = write_jd(job)
    args = f"{jd.relative_to(cc)} {job.slug}" + (" cover-letter" if job.cover_letter_needed else "")
    claude_cli.run(f"/auto-tailor {args}", cwd=cc, allowed_tools=TOOLS, timeout=900)

    out = cc / "workspace" / "output" / job.slug
    pdfs = []
    for kind, name in (("cv", "grace-liao-cv"), ("letter", "grace-liao-cover-letter")):
        md = out / f"{name}.md"
        if md.exists():
            subprocess.run(
                [sys.executable, "scripts/render.py", kind, str(md), "--pdf"],
                cwd=cc, check=True, capture_output=True,
            )
            pdfs.append(md.with_suffix(".pdf"))
    if not pdfs:
        raise RuntimeError(f"/auto-tailor wrote no CV into {out}")

    checklist = out / "checklist.json"
    items = json.loads(checklist.read_text())["items"] if checklist.exists() else []
    questions = [f"{i['question']}\n→ 若是：{i['would_add']}" for i in items]
    return TailorResult(out, pdfs, questions)


def record_answer(job: Job, question: str, answer: str) -> None:
    """Append your answer to career-craft's facts.md so future CVs reuse it."""
    q, _, suggested = question.partition("\n→ 若是：")
    if answer == "keep":
        line = f"- CONFIRMED: {q} — Yes."
    elif answer == "drop":
        line = f"- REJECTED: {q} — No, do not claim this."
    else:
        line = f"- CONFIRMED (Grace's wording): {q} — {answer}"
        if suggested:
            line += f' Use this detail instead of the suggested "{suggested}".'
    facts = settings().career_craft_dir / "workspace" / "facts.md"
    with facts.open("a") as f:
        f.write(f"{line}  _(from {job.company}, {date.today().isoformat()})_\n")
