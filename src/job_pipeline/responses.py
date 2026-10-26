"""Spot replies to applications in Gmail: interview, action required, rejection.

Only emails that mention a company you applied to are read, alert and newsletter
senders are excluded, and each email is classified once by a lean Claude call.
Interview → status Interview; rejection → Rejected; action required (pre-screening
questions, assessments) only notifies, because the application is still open.
"""

import email
import email.policy
import logging
import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

from . import claude_cli, pipeline, store
from .gmail import fetch_message_raw, search_alert_ids
from .store import Job

log = logging.getLogger(__name__)

# Senders that mention company names without being replies to you.
NOISE_SENDERS = [
    "jobmail@s.seek.co.nz",
    "jobalerts-noreply@linkedin.com",
    "updates-noreply@linkedin.com",
    "donotreply@jobalert.indeed.com",
    "noreply@glassdoor.com",
    "noreply@medium.com",
]
GENERIC_WORDS = {
    "group", "ltd", "limited", "nz", "new", "zealand", "the", "of", "museum", "education",
    "inc", "co", "company", "for", "client", "s", "services", "technology", "technologies",
    "global", "international", "solutions", "and",
}
OPEN_STATUSES = ("Applied", "Interview")
STATUS_FOR = {"interview": "Interview", "rejection": "Rejected", "offer": "Offer"}

CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "job_id": {"type": ["integer", "null"], "description": "id of the application this email is about, or null"},
        "kind": {"enum": ["interview", "action_required", "rejection", "offer", "acknowledgement", "other"]},
        "summary": {"type": "string", "description": "One short sentence in Chinese: what the email says / asks"},
    },
    "required": ["job_id", "kind", "summary"],
}


@dataclass
class Update:
    job: Job
    kind: str
    summary: str
    subject: str


def company_keywords(company: str) -> list[str]:
    """Distinctive words to find a company's emails: 'Tribe Group (for …)' → ['Tribe']."""
    name = company.split("(")[0]
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'’]+", name) if w.lower().strip("'’") not in GENERIC_WORDS]
    # Keep two-word Māori / proper names together when the name is long (Te Papa).
    if len(words) >= 2 and words[0].lower() == "te":
        return [f"{words[0]} {words[1]}"] + words[2:]
    return words or [name.strip()]


def build_query(companies: list[str], days: int) -> str | None:
    keywords = sorted({k for c in companies for k in company_keywords(c)})
    if not keywords:
        return None
    terms = " OR ".join(f'"{k}"' if " " in k else k for k in keywords)
    noise = " ".join(f"-from:{s}" for s in NOISE_SENDERS)
    # No category filters: Gmail filed Tribe's pre-screening request under Promotions.
    return f"newer_than:{days}d ({terms}) {noise}"


def read_email(service, msg_id: str) -> dict:
    msg = email.message_from_bytes(fetch_message_raw(service, msg_id), policy=email.policy.default)
    part = msg.get_body(preferencelist=("plain", "html"))
    body = part.get_content() if part else ""
    if part is not None and part.get_content_type() == "text/html":
        body = BeautifulSoup(body, "html.parser").get_text("\n")
    body = re.sub(r"\n\s*\n+", "\n\n", body).strip()
    return {"from": str(msg["From"]), "subject": str(msg["Subject"]), "date": str(msg["Date"]), "text": body[:6000]}


def classify(mail: dict, jobs: list[Job]) -> dict:
    applications = "\n".join(f"- id {j.id}: {j.title} at {j.company}" for j in jobs)
    prompt = (
        "Is this email a reply to one of Grace's job applications? Pick the application it is about "
        "(job_id, or null if none) and the kind:\n"
        "- interview: invites her to an interview, call or meeting, or asks for her availability\n"
        "- action_required: asks her to do something to progress (answer questions, assessment, test, documents)\n"
        "- rejection: she is not progressing\n"
        "- offer: a job offer\n"
        "- acknowledgement: only confirms the application was received or viewed\n"
        "- other: anything else (marketing, newsletters, job alerts, unrelated)\n\n"
        f"Her applications:\n{applications}\n\n"
        f"<email>\nFrom: {mail['from']}\nSubject: {mail['subject']}\nDate: {mail['date']}\n\n{mail['text']}\n</email>"
    )
    return claude_cli.run(prompt, schema=CLASSIFY_SCHEMA, model="sonnet", timeout=180)


def check_responses(conn, service, days: int = 3, dry_run: bool = False) -> list[Update]:
    jobs = [j for status in OPEN_STATUSES for j in store.jobs_with_status(conn, status)]
    query = build_query([j.company for j in jobs], days)
    if not query:
        return []
    by_id = {j.id: j for j in jobs}
    updates: list[Update] = []
    for msg_id in search_alert_ids(service, query):
        key = f"reply:{msg_id}"
        if store.email_seen(conn, key):
            continue
        mail = read_email(service, msg_id)
        verdict = classify(mail, jobs)
        job = by_id.get(verdict["job_id"])
        log.info("reply %s → %s %s", mail["subject"][:60], verdict["kind"], job.company if job else "-")
        if job and verdict["kind"] in ("interview", "action_required", "rejection", "offer"):
            if not dry_run and (status := STATUS_FOR.get(verdict["kind"])) and job.status != status:
                job = pipeline.set_outcome(conn, job, status)
                by_id[job.id] = job
            updates.append(Update(job, verdict["kind"], verdict["summary"], mail["subject"]))
        if not dry_run:
            store.mark_email_seen(conn, key)
    return updates
