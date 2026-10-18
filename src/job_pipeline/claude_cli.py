"""Run Claude Code headlessly (`claude -p`) on the Pro subscription.

No API key is involved: the CLI uses the logged-in Claude Code account, so usage
counts against the Pro limits rather than paid API credit.
"""

import json
import subprocess
from pathlib import Path

HEADLESS_NOTE = (
    "You are running non-interactively inside an automation pipeline. Nobody will "
    "read a chat reply, so do not ask questions, do not add English-correction notes "
    "or other conversational extras. Do the task and stop."
)


class ClaudeError(Exception):
    pass


def run(
    prompt: str,
    *,
    schema: dict | None = None,
    cwd: Path | None = None,
    model: str | None = None,
    allowed_tools: list[str] | None = None,
    timeout: int = 600,
) -> dict | str:
    """Return `structured_output` when a schema is given, otherwise the result text."""
    cmd = ["claude", "-p", prompt, "--output-format", "json", "--append-system-prompt", HEADLESS_NOTE]
    if schema:
        cmd += ["--json-schema", json.dumps(schema)]
    if model:
        cmd += ["--model", model]
    if allowed_tools:
        cmd += ["--allowedTools", ",".join(allowed_tools), "--permission-mode", "acceptEdits"]

    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
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
