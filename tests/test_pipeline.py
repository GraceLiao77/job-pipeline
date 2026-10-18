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
