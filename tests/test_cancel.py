import threading
import time

import pytest

from job_pipeline import claude_cli, pipeline, store


def test_a_running_subprocess_stops_soon_after_cancel():
    cancel = threading.Event()
    threading.Timer(0.3, cancel.set).start()
    start = time.monotonic()
    with pytest.raises(claude_cli.Cancelled):
        claude_cli.run_process(["sleep", "30"], cwd=None, timeout=60, cancel=cancel)
    assert time.monotonic() - start < 5


def test_without_cancel_the_process_output_is_returned():
    proc = claude_cli.run_process(["echo", "hi"], cwd=None, timeout=10, cancel=threading.Event())
    assert proc.stdout.strip() == "hi"


def test_cancelled_before_start_leaves_the_job_untouched(env, monkeypatch):
    conn = store.connect()
    job = store.insert_job(conn, url="u", source="SEEK", company="Acme", title="Dev", job_key="acme|dev", status="Approved")
    monkeypatch.setattr(pipeline, "tailor", lambda *a, **k: pytest.fail("should not tailor"))
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(claude_cli.Cancelled):
        pipeline.generate_cv(conn, job, cancel=cancel)
    assert store.get_job(conn, job.id).status == "Approved"
