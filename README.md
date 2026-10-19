# job-pipeline

Automates the boring half of job hunting while keeping the two decisions that
matter human: **which jobs to apply for**, and **pressing Submit**.

```
job link ──► fetch JD ──► screen (claude -p) ──► Telegram ✅/❌ ──► tailored CV (career-craft /auto-tailor)
                                                                        │
                Notion "Job Pipeline" ◄── you click Submit ◄── /apply-batch fills the form in Chrome
```

| Step | How |
| --- | --- |
| Fetch | LinkedIn via its public guest endpoint (httpx); SEEK, Indeed, Prosple via Playwright driving real Chrome (they block plain HTTP and headless browsers) |
| Screen | `claude -p` with a JSON schema, rules in [`criteria.md`](criteria.md) |
| Review | Telegram bot — morning card per job; links you forward skip review. Checklist answers can be changed (↩️), and replying to a CV PDF with a change request revises it |
| Tailor | career-craft `/auto-tailor`: verified evidence only, conservative by default; uncertain claims come back as a Telegram checklist, answers saved to `career-craft/workspace/facts.md` |
| Apply | `/apply-batch` skill in Claude Code: uploads the PDF, fills from `profile.md`, leaves salary blank, **stops before Submit** |
| Track | Notion database, mirrored from local SQLite (`data/state.db`) |

Runs locally on the Claude Code Pro login — no API key.

## Setup

```bash
uv sync
cp .env.example .env            # fill in tokens (see comments)
cp profile.example.md profile.md
uv run job-pipeline bot         # send /start to the bot to get your chat ID
```

Run at login (after `.env` is complete):

```bash
cp ops/com.graceliao.job-pipeline.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.graceliao.job-pipeline.plist
tail -f data/bot.log
```

## CLI

```bash
uv run job-pipeline bot            # Telegram bot
uv run job-pipeline ready          # CV-ready jobs as JSON (used by /apply-batch)
uv run job-pipeline applied <id>   # mark applied
uv run job-pipeline fetch <url>    # debug a fetch
uv run pytest
```

## Roadmap

- **v2** — Gmail API: read job-alert emails into the Inbox; detect interview/rejection replies.
- **v3** — move the bot and Gmail polling to Fly.io; add Singapore.
