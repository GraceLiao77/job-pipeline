"""Tailor a CV with career-craft's /auto-tailor rules, then render PDFs.

Every Claude call here is a single lean call: the pipeline reads the skill, rules,
evidence and JD itself and puts them in the prompt, and Claude returns the files as
JSON. Letting Claude browse the files itself took ~34 turns and ~1.1–1.6M tokens per
CV; one call with everything inline is a small fraction of that.
"""

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

MAX_PAGES = 2
TRIM_ATTEMPTS = 2
MAX_WORDS = 1100  # two-page CVs have been 900–1,100 words

RULE_FILES = [
    "rules/truthfulness.md",
    "rules/tailoring.md",
    "rules/editorial-style.md",
    "rules/ats-and-layout.md",
    "references/resume-quality-checklist.md",
]
LETTER_EXAMPLE = "workspace/output/zuru/grace-liao-cover-letter.md"

TAILOR_SCHEMA = {
    "type": "object",
    "properties": {
        "cv_markdown": {"type": "string", "description": "The complete grace-liao-cv.md"},
        "cover_letter_markdown": {"type": "string", "description": "The complete letter, or empty if not requested"},
        "checklist": {
            "type": "array",
            "maxItems": 5,
            "items": {
                "type": "object",
                "properties": {"question": {"type": "string"}, "would_add": {"type": "string"}},
                "required": ["question", "would_add"],
            },
        },
    },
    "required": ["cv_markdown", "cover_letter_markdown", "checklist"],
}
CV_SCHEMA = {"type": "object", "properties": {"cv_markdown": {"type": "string"}}, "required": ["cv_markdown"]}
LETTER_SCHEMA = {
    "type": "object",
    "properties": {"cover_letter_markdown": {"type": "string"}},
    "required": ["cover_letter_markdown"],
}


def _files(*paths: str) -> str:
    """Inline career-craft files as <file> blocks; missing optional files are skipped."""
    cc = settings().career_craft_dir
    parts = []
    for rel in paths:
        f = cc / rel
        if f.exists():
            parts.append(f'<file path="{rel}">\n{f.read_text()}\n</file>')
    return "\n\n".join(parts)


def _evidence() -> str:
    return _files("workspace/evidence.md", "workspace/facts.md")


def _jd(job: Job) -> str:
    return f"<file path=\"workspace/job-descriptions/{job.slug}.md\">\n{write_jd(job).read_text()}\n</file>"


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


def tailor(job: Job, cancel=None) -> TailorResult:
    cc = settings().career_craft_dir
    out = cc / "workspace" / "output" / job.slug
    out.mkdir(parents=True, exist_ok=True)
    letter_note = (
        f"A cover letter IS requested; copy the layout of this example:\n{_files(LETTER_EXAMPLE)}"
        if job.cover_letter_needed else "No cover letter is requested: return an empty cover_letter_markdown."
    )
    prompt = (
        "Run the /auto-tailor skill below in single-shot mode. All files it mentions are "
        "provided inline, so you have no tools: instead of reading files, use the blocks below; "
        "instead of writing files, return their contents in the JSON fields (cv_markdown, "
        "cover_letter_markdown, checklist items). Ignore steps about wc, rendering or stopping.\n\n"
        f"Output slug: {job.slug}. Today's date: {date.today().strftime('%-d %B %Y')}.\n{letter_note}\n\n"
        f"{_files('.claude/skills/auto-tailor/SKILL.md')}\n\n{_files(*RULE_FILES)}\n\n{_evidence()}\n\n{_jd(job)}"
    )
    result = claude_cli.run(prompt, schema=TAILOR_SCHEMA, timeout=600, cancel=cancel)

    cv = out / "grace-liao-cv.md"
    cv.write_text(result["cv_markdown"].strip() + "\n")
    (out / "checklist.json").write_text(json.dumps({"items": result["checklist"]}, ensure_ascii=False, indent=2))
    if len(cv.read_text().split()) > MAX_WORDS:
        trim(cv, None, cancel, job)
    finish_cv(cv, cancel, job)
    pdfs = [cv.with_suffix(".pdf")]
    if letter_md := result["cover_letter_markdown"].strip():
        letter = out / "grace-liao-cover-letter.md"
        letter.write_text(letter_md + "\n")
        render(letter, "letter")
        pdfs.append(letter.with_suffix(".pdf"))
    questions = [f"{i['question']}\n→ 若是：{i['would_add']}" for i in result["checklist"]]
    return TailorResult(out, pdfs, questions)


REVISE_SCHEMA = {
    "type": "object",
    "properties": {
        "cv_markdown": {"type": "string", "description": "The complete revised CV"},
        "summary": {"type": "string", "description": "One sentence in Chinese: what you changed"},
        "facts": {
            "type": "array",
            "description": "Facts about Grace the instruction reveals (e.g. a claim is untrue). Empty for pure wording/emphasis changes.",
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string"},
                    "status": {"enum": ["CONFIRMED", "REJECTED"]},
                    "note": {"type": "string"},
                },
                "required": ["claim", "status", "note"],
            },
        },
    },
    "required": ["cv_markdown", "summary", "facts"],
}


def revise_cv(job: Job, instruction: str) -> tuple[TailorResult, str, list[dict]]:
    """Edit an existing tailored CV per Grace's free-text instruction, then re-render."""
    cc = settings().career_craft_dir
    out = cc / "workspace" / "output" / job.slug
    cv = out / "grace-liao-cv.md"
    prompt = (
        f"Grace reviewed her tailored CV (below) and asks for this change:\n\n<instruction>\n{instruction}\n</instruction>\n\n"
        "Return the full revised CV in cv_markdown. Follow the truthfulness rules: you may remove, reorder or "
        "reword; you may only add content supported by evidence.md or facts.md. Remove every mention of "
        "anything Grace says is untrue (bullets, skills list, profile). Keep the exact Markdown conventions "
        f"and stay under {MAX_WORDS} words. If the instruction tells you something about Grace's real "
        "experience, return it in `facts`.\n\n"
        f'<file path="grace-liao-cv.md">\n{cv.read_text()}\n</file>\n\n{_files("rules/truthfulness.md")}\n\n{_evidence()}'
    )
    out_json = claude_cli.run(prompt, schema=REVISE_SCHEMA, timeout=600)
    cv.write_text(out_json["cv_markdown"].strip() + "\n")
    finish_cv(cv, None, job)
    pdfs = [cv.with_suffix(".pdf")] + ([p] if (p := out / "grace-liao-cover-letter.pdf").exists() else [])
    return TailorResult(out, pdfs, []), out_json["summary"], out_json["facts"]


def write_cover_letter(job: Job) -> Path:
    """Write and render a cover letter for a job whose CV already exists.

    Used when an application form turns out to offer a cover letter even though the
    JD did not ask for one. The tailored CV is the evidence, so the two agree.
    """
    cc = settings().career_craft_dir
    out = cc / "workspace" / "output" / job.slug
    letter = out / "grace-liao-cover-letter.md"
    result = claude_cli.run(
        "Write a cover letter for the job below, following step 4 of the auto-tailor skill (structure and "
        f"rules, 250–350 words, dated {date.today().strftime('%-d %B %Y')}) and the layout of the example "
        "letter. Use only facts in the tailored CV, evidence.md and facts.md; respect REJECTED entries. "
        "Return it in cover_letter_markdown.\n\n"
        f"{_files('.claude/skills/auto-tailor/SKILL.md', LETTER_EXAMPLE)}\n\n"
        f'<file path="grace-liao-cv.md">\n{(out / "grace-liao-cv.md").read_text()}\n</file>\n\n{_evidence()}\n\n{_jd(job)}',
        schema=LETTER_SCHEMA, timeout=600,
    )
    letter.write_text(result["cover_letter_markdown"].strip() + "\n")
    render(letter, "letter")
    return letter


def record_revision_facts(job: Job, facts: list[dict]) -> None:
    if not facts:
        return
    path = settings().career_craft_dir / "workspace" / "facts.md"
    with path.open("a") as f:
        for fact in facts:
            f.write(f"- {fact['status']}: {fact['claim']} — {fact['note']}  _(CV revision, {job.company}, {date.today().isoformat()})_\n")


def finish_cv(cv: Path, cancel=None, job: Job | None = None) -> None:
    """Render to PDF, trimming until it fits on MAX_PAGES."""
    render(cv, "cv")
    for _ in range(TRIM_ATTEMPTS):
        pages = page_count(cv.with_suffix(".pdf"))
        if pages <= MAX_PAGES:
            return
        trim(cv, pages, cancel, job)
        render(cv, "cv")


def render(md: Path, kind: str) -> None:
    subprocess.run(
        [sys.executable, "scripts/render.py", kind, str(md), "--pdf"],
        cwd=settings().career_craft_dir, check=True, capture_output=True,
    )


def page_count(pdf: Path) -> int:
    # Chrome writes one "/Type /Page" object per page (plus one "/Type /Pages" tree).
    return len(re.findall(rb"/Type\s*/Page[^s]", pdf.read_bytes()))


def trim(cv: Path, pages: int | None, cancel=None, job: Job | None = None) -> None:
    """Cut a CV that is too long (by word count, or rendered past two pages)."""
    words = len(cv.read_text().split())
    why = f"renders to {pages} A4 pages" if pages else f"is {words} words"
    jd = _jd(job) if job else ""
    result = claude_cli.run(
        f"This CV {why}; it must fit on {MAX_PAGES} pages, about 1,000 words. Return the full shortened CV "
        "in cv_markdown: drop the bullets and sub-projects least relevant to the job description, then "
        "tighten wording. Keep the exact Markdown conventions, Work Rights, contact details and Education. "
        f"Do not add anything new.\n\n<file path=\"grace-liao-cv.md\">\n{cv.read_text()}\n</file>\n\n{jd}",
        schema=CV_SCHEMA, timeout=600, cancel=cancel,
    )
    cv.write_text(result["cv_markdown"].strip() + "\n")


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
