"""Run Claude Code headlessly (`claude -p`) on the Pro subscription.

No API key is involved: the CLI uses the logged-in Claude Code account, so usage
counts against the Pro limits rather than paid API credit.
"""

import json
import subprocess
import threading
from pathlib import Path

HEADLESS_NOTE = (
    "You are running non-interactively inside an automation pipeline. Nobody will "
    "read a chat reply, so do not ask questions, do not add English-correction notes "
    "or other conversational extras. Do the task and stop."
)


class ClaudeError(Exception):
    pass


class Cancelled(Exception):
    """You pressed ⏹ while this run was queued or in progress."""


def run_process(cmd: list[str], *, cwd: Path | None, timeout: int, cancel: threading.Event | None):
    """subprocess.run, except it can be stopped early by setting `cancel`."""
    if cancel is not None and cancel.is_set():
        raise Cancelled
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    # communicate() in a thread so this one can watch the cancel flag.
    result: dict = {}
    reader = threading.Thread(target=lambda: result.update(zip(("stdout", "stderr"), proc.communicate())))
    reader.start()
    waited = 0.0
    while reader.is_alive():
        reader.join(0.2)
        waited += 0.2
        if (cancel is not None and cancel.is_set()) or waited > timeout:
            proc.terminate()
            try:
                proc.wait(5)
            except subprocess.TimeoutExpired:
                proc.kill()
            reader.join()
            if waited > timeout:
                raise subprocess.TimeoutExpired(cmd, timeout)
            raise Cancelled
    return subprocess.CompletedProcess(cmd, proc.returncode, result.get("stdout", ""), result.get("stderr", ""))


def run(
    prompt: str,
    *,
    schema: dict | None = None,
    cwd: Path | None = None,
    model: str | None = None,
    allowed_tools: list[str] | None = None,
    timeout: int = 600,
    cancel: threading.Event | None = None,
) -> dict | str:
    """Return `structured_output` when a schema is given, otherwise the result text."""
    cmd = ["claude", "-p", prompt, "--output-format", "json", "--append-system-prompt", HEADLESS_NOTE]
    if schema:
        cmd += ["--json-schema", json.dumps(schema)]
    if model:
        cmd += ["--model", model]
    if allowed_tools:
        cmd += ["--allowedTools", ",".join(allowed_tools), "--permission-mode", "acceptEdits"]

    proc = run_process(cmd, cwd=cwd, timeout=timeout, cancel=cancel)
    try:
        out = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise ClaudeError(f"claude exited {proc.returncode}: {proc.stderr.strip()[:500]}")
    if out.get("is_error"):
        raise ClaudeError(str(out.get("result") or out.get("subtype"))[:500])
    if schema:
        if out.get("structured_output") is None:
            raise ClaudeError("no structured output returned")
        return out["structured_output"]
    return out.get("result", "")
