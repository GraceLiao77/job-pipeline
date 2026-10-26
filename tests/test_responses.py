from job_pipeline import responses, store


def test_company_keywords_drop_generic_words():
    assert responses.company_keywords("Museum of New Zealand Te Papa Tongarewa") == ["Te Papa", "Tongarewa"]
    assert responses.company_keywords("Tribe Group (for Tribe Group's client)") == ["Tribe"]
    assert responses.company_keywords("Serko") == ["Serko"]
    assert responses.company_keywords("Crimson Education") == ["Crimson"]


def test_query_mentions_applied_companies_and_skips_alert_senders():
    q = responses.build_query(["Serko", "Crimson Education"], days=3)
    assert "newer_than:3d" in q and "Serko" in q and "Crimson" in q
    assert "-from:jobalerts-noreply@linkedin.com" in q
    assert "category:" not in q  # real replies land in Promotions too
    assert responses.build_query([], days=3) is None  # nothing applied → nothing to check


def _applied(conn, company, title="Dev"):
    return store.insert_job(conn, url=f"u-{company}", source="SEEK", company=company, title=title,
                            job_key=company, status="Applied", slug=company.lower())


def test_interview_and_rejection_update_status_once(env, monkeypatch):
    conn = store.connect()
    serko = _applied(conn, "Serko")
    tribe = _applied(conn, "Tribe Group")
    emails = {"e1": "serko invite", "e2": "tribe no", "e3": "crimson ack"}
    verdicts = {
        "serko invite": {"job_id": serko.id, "kind": "interview", "summary": "约面试"},
        "tribe no": {"job_id": tribe.id, "kind": "rejection", "summary": "拒信"},
        "crimson ack": {"job_id": None, "kind": "acknowledgement", "summary": "确认收到"},
    }
    monkeypatch.setattr(responses, "search_alert_ids", lambda service, q: list(emails))
    monkeypatch.setattr(responses, "read_email", lambda service, msg_id: {"from": "x", "subject": emails[msg_id], "date": "", "text": emails[msg_id]})
    monkeypatch.setattr(responses, "classify", lambda email, jobs: verdicts[email["text"]])
    synced = []
    monkeypatch.setattr(responses.pipeline, "set_outcome", lambda conn, job, status: synced.append((job.company, status)) or store.update_job(conn, job.id, status=status))

    updates = responses.check_responses(conn, service=None, days=3)

    assert [(u.job.company, u.kind) for u in updates] == [("Serko", "interview"), ("Tribe Group", "rejection")]
    assert synced == [("Serko", "Interview"), ("Tribe Group", "Rejected")]
    assert responses.check_responses(conn, service=None, days=3) == []  # same emails: nothing new


def test_action_required_notifies_without_changing_status(env, monkeypatch):
    conn = store.connect()
    tribe = _applied(conn, "Tribe Group")
    monkeypatch.setattr(responses, "search_alert_ids", lambda service, q: ["e1"])
    monkeypatch.setattr(responses, "read_email", lambda service, msg_id: {"from": "", "subject": "", "date": "", "text": ""})
    monkeypatch.setattr(responses, "classify", lambda email, jobs: {"job_id": tribe.id, "kind": "action_required", "summary": "补答问题"})
    updates = responses.check_responses(conn, service=None, days=3)
    assert updates[0].kind == "action_required"
    assert store.get_job(conn, tribe.id).status == "Applied"
