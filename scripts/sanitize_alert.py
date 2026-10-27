"""Turn a real job-alert .eml into a fixture that is safe to commit.

    uv run python scripts/sanitize_alert.py tests/fixtures/raw/<file>.eml tests/fixtures/<name>.eml

Real alert emails carry personal data: your name and address, and tracking links
whose tokens identify your account (Indeed's H4sI… tokens hold an accountId; SEEK's
tracker paths are per-recipient). This keeps only what the parser needs:

- one text/html part, with the From/Subject headers the tests don't depend on
- job links reduced to what identifies the job (LinkedIn path, Indeed `jk`)
- every SEEK tracker link replaced by a numbered fake; the real job URL it pointed
  to is resolved once now and saved in `<name>.redirects.json`, so tests can use
  a fake resolver and never touch the network
- every other link stripped of its query string
"""

import email
import email.policy
import json
import re
import sys
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from job_pipeline.gmail import TRACKER_HOSTS, extract_html_from_bytes, resolve_redirect

PERSONAL = [
    (re.compile(r"[\w.+-]+@gmail\.com", re.I), "candidate@example.com"),
    (re.compile(r"\bGrace\b"), "Candidate"),
    (re.compile(r"\bLiao\b"), "Person"),
]


def clean_url(url: str, redirects: dict, counter: list) -> str:
    url = url.replace("&amp;", "&")
    p = urlparse(url)
    host = p.netloc.lower()
    if host in TRACKER_HOSTS:
        counter[0] += 1
        fake = f"https://{host}/uni/ss/c/FAKE-{counter[0]}"
        redirects[fake] = resolve_redirect(url)
        return fake
    if "indeed." in host:
        jk = parse_qs(p.query).get("jk")
        if host.startswith("cts."):
            return "https://cts.indeed.com/v3/FAKE"
        query = urlencode({"jk": jk[0]}) if jk else ""
        return urlunparse((p.scheme, p.netloc, p.path, "", query, ""))
    return urlunparse((p.scheme, p.netloc, p.path, "", "", ""))


def main(src: str, dst: str) -> None:
    raw = Path(src).read_bytes()
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    html = extract_html_from_bytes(raw)

    redirects: dict[str, str | None] = {}
    counter = [0]
    html = re.sub(r'href="([^"]+)"', lambda m: f'href="{clean_url(m.group(1), redirects, counter)}"', html)
    html = re.sub(r'src="[^"]*"', 'src=""', html)  # tracking pixels and images
    html = re.sub(r"url\([^)]*\)", "url()", html)  # CSS pixels: Indeed hides one in background-image
    for pattern, repl in PERSONAL:
        html = pattern.sub(repl, html)

    out = EmailMessage()
    out["From"] = msg["From"]
    out["Subject"] = msg["Subject"]
    out["To"] = "candidate@example.com"
    out.set_content(html, subtype="html")
    Path(dst).write_bytes(bytes(out))
    if redirects:
        Path(dst).with_suffix(".redirects.json").write_text(json.dumps(redirects, indent=2))
    print(f"wrote {dst} ({len(redirects)} SEEK trackers resolved)")


if __name__ == "__main__":
    main(*sys.argv[1:3])
