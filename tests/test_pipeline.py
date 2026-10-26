from job_pipeline import pipeline, store
from job_pipeline.tailor import make_slug


def test_job_key_ignores_company_suffixes_and_punctuation():
    assert pipeline.job_key("Xero Ltd", "Full-Stack Developer") == pipeline.job_key("XERO", "Full Stack Developer")
    assert pipeline.job_key("Acme NZ Limited", "Frontend Engineer") == "acme|frontend engineer"
    assert pipeline.job_key("Acme", "Senior Frontend Engineer") != pipeline.job_key("Acme", "Frontend Engineer")


def test_make_slug_avoids_existing_folders():
    assert make_slug("Orion Health", "Developer", set()) == "orion-health"
    assert make_slug("Orion Health", "Full Stack Dev", {"orion-health"}) == "orion-health-full-stack-dev"
    assert make_slug("Orion Health", "Dev", {"orion-health", "orion-health-dev"}) == "orion-health-dev-2"


def _job(conn):
    return store.insert_job(conn, url="u", source="SEEK", company="Acme", title="Dev", job_key="acme|dev", status="CV Ready")


def test_regenerates_only_when_round_done_and_something_added(env):
    conn = store.connect()
    job = _job(conn)
    a, b = store.add_checklist(conn, job.id, ["Q1?\n→ 若是：x", "Q2?\n→ 若是：y"], generation=1)

    _, regen = pipeline.answer(conn, a, "keep")
    assert regen is False  # Q2 still open
    _, regen = pipeline.answer(conn, b, "drop")
    assert regen is True  # all answered, Q1 adds something

    facts = (env / "workspace" / "facts.md").read_text()
    assert "CONFIRMED: Q1?" in facts and "REJECTED: Q2?" in facts
    assert "→" not in facts


def test_all_dropped_means_no_regeneration(env):
    conn = store.connect()
    job = _job(conn)
    (a,) = store.add_checklist(conn, job.id, ["Q1?"], generation=1)
    _, regen = pipeline.answer(conn, a, "drop")
    assert regen is False


def test_edit_keeps_the_fact_but_replaces_the_suggested_wording(env):
    conn = store.connect()
    job = _job(conn)
    (a,) = store.add_checklist(
        conn, job.id, ["Did you review teammates' PRs?\n→ 若是：Led code review for the team."], generation=1
    )
    _, regen = pipeline.answer(conn, a, "Yes, about 2 PRs a week at WDCC, not leading it")
    facts = (env / "workspace" / "facts.md").read_text()
    assert "CONFIRMED (Grace's wording): Did you review teammates' PRs? — Yes, about 2 PRs a week at WDCC, not leading it" in facts
    assert 'instead of the suggested "Led code review for the team."' in facts
    assert regen is True


def test_changing_an_answer_overrides_the_fact_and_regenerates(env):
    conn = store.connect()
    job = _job(conn)
    (a,) = store.add_checklist(conn, job.id, ["Does it stream Claude output?\n→ 若是：Streamed output."], generation=1)
    pipeline.answer(conn, a, "keep")

    _, regen = pipeline.answer(conn, store.get_item(conn, a.id), "drop")

    assert regen is True  # a changed answer must reach the CV even if it only removes
    lines = (env / "workspace" / "facts.md").read_text().splitlines()
    assert lines[-2].startswith("- CONFIRMED: Does it stream Claude output?")
    assert lines[-1].startswith("- REJECTED: Does it stream Claude output?")  # later entry wins
    assert store.get_item(conn, a.id).answer == "drop"


def test_same_answer_twice_does_nothing(env):
    conn = store.connect()
    job = _job(conn)
    (a,) = store.add_checklist(conn, job.id, ["Q?"], generation=1)
    pipeline.answer(conn, a, "drop")
    before = (env / "workspace" / "facts.md").read_text()
    _, regen = pipeline.answer(conn, store.get_item(conn, a.id), "drop")
    assert regen is False
    assert (env / "workspace" / "facts.md").read_text() == before


def test_cv_messages_map_back_to_their_job(env):
    conn = store.connect()
    job = _job(conn)
    store.remember_cv_message(conn, 555, job.id)
    assert store.job_for_cv_message(conn, 555).id == job.id
    assert store.job_for_cv_message(conn, 999) is None


def test_revision_facts_are_recorded(env):
    from job_pipeline.tailor import record_revision_facts

    job = _job(store.connect())
    record_revision_facts(job, [
        {"claim": "Interview Coach streams Claude feedback", "status": "REJECTED", "note": "it does not stream"},
    ])
    facts = (env / "workspace" / "facts.md").read_text()
    assert "- REJECTED: Interview Coach streams Claude feedback — it does not stream" in facts


def _screen(fit: int, filtered: bool = False):
    from job_pipeline.screen import Screen

    return Screen(company="Acme", title=f"Dev {fit}", location="Auckland", seniority="Intermediate", track="Frontend",
                  filtered=filtered, fit_score=fit, reason="r", cover_letter_needed=False, is_job_ad=True)


def test_low_fit_alert_jobs_are_filtered_but_forwarded_links_are_not(env, monkeypatch):
    from job_pipeline.fetch import Page

    monkeypatch.setenv("MIN_FIT", "3")
    conn = store.connect()
    page = Page("https://www.linkedin.com/jobs/view/1", "LinkedIn", "", "jd")

    monkeypatch.setattr(pipeline, "screen", lambda p: _screen(2))
    assert pipeline.ingest(conn, page, manual=False).job.status == "Filtered"

    monkeypatch.setattr(pipeline, "screen", lambda p: _screen(3))
    assert pipeline.ingest(conn, page, manual=False).job.status == "Inbox"

    monkeypatch.setattr(pipeline, "screen", lambda p: _screen(1))
    assert pipeline.ingest(conn, page, manual=True).job.status == "Approved"  # you chose it yourself


def _fake_notion(monkeypatch):
    calls = []
    monkeypatch.setattr(pipeline.notion, "create_job", lambda job, **kw: calls.append(("create", job.status)) or "page-1")
    monkeypatch.setattr(pipeline.notion, "applied", lambda page_id: calls.append(("applied", page_id)))
    monkeypatch.setattr(pipeline.notion, "upload_cv", lambda page_id, pdfs, out: calls.append(("upload", page_id)))
    return calls


def test_notion_only_hears_about_applied_jobs(env, monkeypatch):
    from job_pipeline.fetch import Page

    calls = _fake_notion(monkeypatch)
    monkeypatch.setattr(pipeline, "screen", lambda p: _screen(5))
    conn = store.connect()
    job = pipeline.ingest(conn, Page("https://www.linkedin.com/jobs/view/7", "LinkedIn", "", "jd"), manual=False).job
    pipeline.skip(conn, job, "Other")
    pipeline.approve(conn, job)
    assert calls == []  # inbox, skip and approve stay local

    pipeline.mark_applied(conn, store.get_job(conn, job.id))
    assert calls[0] == ("create", "Applied")
    assert store.get_job(conn, job.id).notion_page_id == "page-1"
