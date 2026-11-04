"""Daily job-alert ingestion: Gmail alert emails → job links → screened Inbox jobs.

Each email is processed once (remembered in `seen_emails`), and a link already in
the jobs table is skipped before fetching, so nothing is fetched or screened twice.
A link the site's bot check blocks MAX_BLOCKS times is given up on and reported
once, instead of keeping its email unread and being retried every day.
"""

import logging
import time
from dataclasses import dataclass, field

from . import pipeline, store
from .fetch import Blocked, chrome_session, fetch
from .prefilter import quick_reject
from .gmail import ALERT_QUERY, extract_job_links, fetch_message_html, resolve_redirect, search_alert_ids

log = logging.getLogger(__name__)
PAUSE_SECONDS = 2
MAX_BLOCKS = 2


@dataclass
class Summary:
    emails: int = 0      # new alert emails read
    links: int = 0       # job links found in them
    known: int = 0       # links already in the jobs table
    new_jobs: int = 0    # jobs added (Inbox or Filtered)
    inbox: int = 0       # of those, waiting for your review
    filtered: int = 0    # of those, dropped (by the title rules or by criteria.md)
    prefiltered: int = 0 # of the filtered, dropped by title rules without asking Claude
    failed: int = 0      # links that could not be fetched or screened
    blocked: list[str] = field(default_factory=list)  # given up on after MAX_BLOCKS bot checks


def ingest_alerts(conn, service, days: int = 1) -> Summary:
    with chrome_session():  # one Chrome window for the whole batch
        return _ingest(conn, service, days)


def _ingest(conn, service, days: int) -> Summary:
    summary = Summary()
    for msg_id in search_alert_ids(service, f"{ALERT_QUERY} newer_than:{days}d"):
        if store.email_seen(conn, msg_id):
            continue
        summary.emails += 1
        failed_before = summary.failed
        for url in extract_job_links(fetch_message_html(service, msg_id), resolve=resolve_redirect):
            summary.links += 1
            if store.find_by_url(conn, url) or store.block_attempts(conn, url) >= MAX_BLOCKS:
                summary.known += 1  # a given-up link was already reported
                continue
            time.sleep(PAUSE_SECONDS)  # LinkedIn answers 429 when hit back-to-back
            try:
                page = fetch(url)
                if reason := quick_reject(page.title):  # obvious from the title: don't spend Claude on it
                    pipeline.record_prefiltered(conn, page, reason)
                    summary.new_jobs += 1
                    summary.filtered += 1
                    summary.prefiltered += 1
                    continue
                result = pipeline.ingest(conn, page, manual=False)
            except Blocked as e:
                log.warning("alert link blocked %s: %s", url, e)
                if store.record_block(conn, url) >= MAX_BLOCKS:
                    summary.blocked.append(url)  # stop retrying; you get the link instead
                else:
                    summary.failed += 1
                continue
            except Exception as e:  # noqa: BLE001 — one bad link must not stop the rest
                log.warning("alert link failed %s: %s", url, e)
                summary.failed += 1
                continue
            if result.duplicate:
                summary.known += 1
            else:
                summary.new_jobs += 1
                if result.job.status == "Inbox":
                    summary.inbox += 1
                else:
                    summary.filtered += 1
        # Only when every link worked: a crash or a failed link means the email is read
        # again next run, and the links that did work are then skipped as known.
        if summary.failed == failed_before:
            store.mark_email_seen(conn, msg_id)
    return summary
