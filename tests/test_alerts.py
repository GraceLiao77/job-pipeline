import contextlib

from job_pipeline import alerts, store
from job_pipeline.fetch import FetchFailed, Page


def _fake_gmail(monkeypatch, emails: dict[str, list[str]]):
    """emails: message id → job links found in that email."""
    monkeypatch.setattr(alerts, "PAUSE_SECONDS", 0)
    monkeypatch.setattr(alerts, "chrome_session", contextlib.nullcontext)  # no real browser in tests
    monkeypatch.setattr(alerts, "search_alert_ids", lambda service, q: list(emails))
    monkeypatch.setattr(alerts, "fetch_message_html", lambda service, msg_id: msg_id)
    monkeypatch.setattr(alerts, "extract_job_links", lambda html, resolve=None: emails[html])


def _fake_ingest(monkeypatch, calls: list[str], fail: set[str] = frozenset()):
    def fetch(url):
        if url in fail:
            raise FetchFailed("blocked")
        return Page(url, "LinkedIn", "", "jd text")

    def ingest(conn, page, manual):
        assert manual is False  # alert jobs go through review
        calls.append(page.url)
        job = store.insert_job(conn, url=page.url, source="LinkedIn", company=page.url, title="Dev",
                               job_key=page.url, status="Inbox")
        return type("R", (), {"job": job, "duplicate": False})()

    monkeypatch.setattr(alerts, "fetch", fetch)
    monkeypatch.setattr(alerts.pipeline, "ingest", ingest)


def test_new_emails_are_ingested_and_marked_seen(env, monkeypatch):
    conn = store.connect()
    calls: list[str] = []
    _fake_gmail(monkeypatch, {"m1": ["https://www.linkedin.com/jobs/view/1"], "m2": ["https://www.linkedin.com/jobs/view/2"]})
    _fake_ingest(monkeypatch, calls)

    summary = alerts.ingest_alerts(conn, service=None, days=1)

    assert calls == ["https://www.linkedin.com/jobs/view/1", "https://www.linkedin.com/jobs/view/2"]
    assert summary.emails == 2 and summary.new_jobs == 2
    assert store.email_seen(conn, "m1") and store.email_seen(conn, "m2")


def test_seen_emails_and_known_jobs_are_skipped(env, monkeypatch):
    conn = store.connect()
    store.mark_email_seen(conn, "old")
    store.insert_job(conn, url="https://www.linkedin.com/jobs/view/1", source="LinkedIn", job_key="k1", status="Applied")
    calls: list[str] = []
    _fake_gmail(monkeypatch, {
        "old": ["https://www.linkedin.com/jobs/view/9"],
        "new": ["https://www.linkedin.com/jobs/view/1", "https://www.linkedin.com/jobs/view/2"],
    })
    _fake_ingest(monkeypatch, calls)

    summary = alerts.ingest_alerts(conn, service=None, days=1)

    assert calls == ["https://www.linkedin.com/jobs/view/2"]  # no refetch, no Claude call for job 1
    assert summary.emails == 1 and summary.known == 1


def test_one_bad_link_does_not_stop_the_rest(env, monkeypatch):
    conn = store.connect()
    calls: list[str] = []
    bad = "https://www.linkedin.com/jobs/view/1"
    _fake_gmail(monkeypatch, {"m1": [bad, "https://www.linkedin.com/jobs/view/2"]})
    _fake_ingest(monkeypatch, calls, fail={bad})

    summary = alerts.ingest_alerts(conn, service=None, days=1)

    assert calls == ["https://www.linkedin.com/jobs/view/2"]
    assert summary.failed == 1
    # Not marked seen, so the failed link is retried next run (job 2 is then skipped as known).
    assert not store.email_seen(conn, "m1")
