import json

from job_pipeline import claude_cli, store, tailor


def _career_craft(cc):
    (cc / ".claude" / "skills" / "auto-tailor").mkdir(parents=True)
    (cc / ".claude" / "skills" / "auto-tailor" / "SKILL.md").write_text("AUTO-TAILOR RULES")
    (cc / "rules").mkdir()
    (cc / "rules" / "truthfulness.md").write_text("NEVER INVENT")
    (cc / "references").mkdir()
    (cc / "references" / "resume-quality-checklist.md").write_text("CHECKLIST")
    (cc / "workspace" / "job-descriptions").mkdir(parents=True)
    (cc / "workspace" / "evidence.md").write_text("EVIDENCE: built a BFF")
    (cc / "workspace" / "facts.md").write_text("- REJECTED: streaming")


def _job(**kw):
    conn = store.connect()
    values = dict(url="u", source="SEEK", company="Acme", title="Dev", job_key="acme|dev", status="Approved",
                  slug="acme", jd_text="JD: React and Node")
    values.update(kw)
    return store.insert_job(conn, **values)


def test_tailor_is_one_tool_free_call_with_everything_inline(env, monkeypatch):
    _career_craft(env)
    calls = []

    def fake_run(prompt, **kw):
        calls.append((prompt, kw))
        return {"cv_markdown": "# Grace Liao\n" + "word " * 900, "cover_letter_markdown": "",
                "checklist": [{"question": "Did you do X?", "would_add": "Did X."}]}

    monkeypatch.setattr(claude_cli, "run", fake_run)
    monkeypatch.setattr(tailor, "render", lambda md, kind: md.with_suffix(".pdf").write_bytes(b"/Type /Page "))
    result = tailor.tailor(_job())

    assert len(calls) == 1
    prompt, kw = calls[0]
    assert kw.get("allowed_tools") is None  # lean mode: no tools, no agent loop
    for part in ("AUTO-TAILOR RULES", "NEVER INVENT", "EVIDENCE: built a BFF", "REJECTED: streaming", "JD: React and Node"):
        assert part in prompt
    out = env / "workspace" / "output" / "acme"
    assert (out / "grace-liao-cv.md").read_text().startswith("# Grace Liao")
    assert not (out / "grace-liao-cover-letter.md").exists()
    assert json.loads((out / "checklist.json").read_text())["items"][0]["question"] == "Did you do X?"
    assert result.questions == ["Did you do X?\n→ 若是：Did X."]


def test_long_cv_is_trimmed_before_rendering(env, monkeypatch):
    _career_craft(env)
    responses = iter([
        {"cv_markdown": "# Grace Liao\n" + "word " * 1300, "cover_letter_markdown": "", "checklist": []},
        {"cv_markdown": "# Grace Liao\n" + "word " * 1000},
    ])
    monkeypatch.setattr(claude_cli, "run", lambda prompt, **kw: next(responses))
    monkeypatch.setattr(tailor, "render", lambda md, kind: md.with_suffix(".pdf").write_bytes(b"/Type /Page "))
    tailor.tailor(_job())
    assert len((env / "workspace" / "output" / "acme" / "grace-liao-cv.md").read_text().split()) < 1100
