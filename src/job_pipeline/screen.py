"""Read a job page and decide: what is it, does it pass the hard filters, how good a fit."""

from dataclasses import dataclass

from . import claude_cli
from .config import settings
from .fetch import Page

SCHEMA = {
    "type": "object",
    "properties": {
        "company": {"type": "string"},
        "title": {"type": "string"},
        "location": {"type": "string", "description": "City/region, plus 'remote'/'hybrid' if stated"},
        "seniority": {"enum": ["Graduate", "Junior", "Intermediate", "Senior", "Unknown"]},
        "track": {"enum": ["Full-stack", "Frontend", "AI", "Other"]},
        "filtered": {"type": "boolean", "description": "true if any hard filter applies"},
        "fit_score": {"type": "integer", "minimum": 1, "maximum": 10},
        "reason": {"type": "string", "description": "One sentence in Chinese: why this score, or which filter hit"},
        "cover_letter_needed": {"type": "boolean", "description": "true only if the ad asks for a cover letter"},
        "is_job_ad": {"type": "boolean", "description": "false if the page is not a single job ad"},
    },
    "required": [
        "company", "title", "location", "seniority", "track",
        "filtered", "fit_score", "reason", "cover_letter_needed", "is_job_ad",
    ],
}


@dataclass
class Screen:
    company: str
    title: str
    location: str
    seniority: str
    track: str
    filtered: bool
    fit_score: int
    reason: str
    cover_letter_needed: bool
    is_job_ad: bool


def screen(page: Page) -> Screen:
    criteria = settings().criteria_file.read_text()
    prompt = (
        "Screen this job ad for the candidate using the criteria below. "
        "Return the structured result only.\n\n"
        f"<criteria>\n{criteria}\n</criteria>\n\n"
        f"<job source=\"{page.source}\" url=\"{page.url}\">\n{page.text}\n</job>"
    )
    out = claude_cli.run(prompt, schema=SCHEMA, model="sonnet", timeout=300)
    return Screen(**out)
