"""Turn a job link into page text.

LinkedIn serves a public "guest" endpoint that plain HTTP can read. SEEK, Indeed
and Prosple sit behind bot checks that block plain HTTP and headless browsers, but
let a real, visible Chrome through — so those go through Playwright driving the
installed Chrome, with the window parked off-screen.

We only pull text here. Working out company, title, seniority etc. is left to the
screening step, which reads text far more reliably than per-site CSS selectors.
"""

import re
import time
from contextlib import contextmanager
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0 Safari/537.36"
)
MAX_CHARS = 20_000
MIN_CHARS = 400

# Most specific element holding the job ad on each site; falls back to <body>.
SELECTORS = {
    "SEEK": '[data-automation="jobAdDetails"]',
    "Indeed": "#jobDescriptionText",
    "Prosple": "main",
}

SOURCE_PATH = {
    'SEEK': r"/job/\d+",
    'LinkedIn': r"/jobs/view/\d+",
}

class FetchFailed(Exception):
    pass


@dataclass
class Page:
    url: str
    source: str
    title: str
    text: str


def detect_source(url: str) -> str:
    # netloc（域名/主机）host 只有 www.seek.co.nz（再 .lower() 变成小写）
    host = urlparse(url).netloc.lower()
    for needle, name in (("seek.", "SEEK"), ("linkedin.", "LinkedIn"), ("indeed.", "Indeed"), ("prosple.", "Prosple")):
        if needle in host:
            return name
    return "Other"


def linkedin_job_id(url: str) -> str | None:
    if m := re.search(r"/jobs/view/(?:[^/]*?-)?(\d{6,})", url):
        return m.group(1)
    ids = parse_qs(urlparse(url).query).get("currentJobId")
    return ids[0] if ids else None


def canonical_url(url: str) -> str:
    """Strip tracking params so the same job always has the same link."""
    source = detect_source(url)
    if source == "LinkedIn" and (jid := linkedin_job_id(url)):
        return f"https://www.linkedin.com/jobs/view/{jid}"
    if source == "SEEK" and (m := re.search(r"/job/(\d+)", url)):
        return f"https://www.seek.co.nz/job/{m.group(1)}"
    if source == "Indeed" and (jk := parse_qs(urlparse(url).query).get("jk")):
        return f"https://nz.indeed.com/viewjob?jk={jk[0]}"
    return url.split("#")[0]


def _clean(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()[:MAX_CHARS]


def _fetch_linkedin(url: str) -> Page:
    jid = linkedin_job_id(url)
    if not jid:
        raise FetchFailed("找不到 LinkedIn job ID")
    for wait in (0, 10, 30):  # 429 = too many requests: wait and try again
        time.sleep(wait)
        resp = httpx.get(
            f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{jid}",
            headers={"User-Agent": UA},
            timeout=20,
            follow_redirects=True,
        )
        if resp.status_code != 429:
            break
    if resp.status_code != 200:
        raise FetchFailed(f"LinkedIn 返回 {resp.status_code}")
    soup = BeautifulSoup(resp.text, "html.parser")
    title = soup.select_one(".topcard__title")
    company = soup.select_one(".topcard__org-name-link, .topcard__flavor")
    location = soup.select_one(".topcard__flavor--bullet")
    body = soup.select_one(".show-more-less-html__markup") or soup
    header = " | ".join(t.get_text(strip=True) for t in (title, company, location) if t)
    return Page(url, "LinkedIn", header, _clean(f"{header}\n\n{body.get_text(chr(10))}"))


# One Chrome window shared by a whole batch (see chrome_session). macOS pulls
# off-screen windows back on screen, so the window is also kept small.
CHROME_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--window-position=-2400,-2400",
    "--window-size=480,360",
]
_shared_browser = None


def _launch_chrome(p):
    # headless Chrome is stopped by the bot check, so the window has to exist
    return p.chromium.launch(channel="chrome", headless=False, args=CHROME_ARGS)


@contextmanager
def chrome_session():
    """Keep one Chrome open for every Chrome fetch inside the `with` block.

    Without it each SEEK/Indeed/Prosple fetch opens and closes its own window,
    which is slow and pops windows up one after another.
    """
    global _shared_browser
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        _shared_browser = _launch_chrome(p)
        try:
            yield
        finally:
            _shared_browser.close()
            _shared_browser = None


def _read_page(browser, url: str, source: str) -> Page:
    page = browser.new_page(user_agent=UA)
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_timeout(6_000)  # let the bot check and client rendering finish
        title = page.title()
        if "just a moment" in title.lower() or "blocked" in title.lower():
            raise FetchFailed(f"{source} 拦截了浏览器")
        el = page.query_selector(SELECTORS.get(source, "body")) or page.query_selector("body")
        text = el.inner_text() if el else ""
    finally:
        page.close()
    return Page(url, source, title, _clean(f"{title}\n\n{text}"))


def _fetch_with_chrome(url: str, source: str) -> Page:
    if _shared_browser is not None:
        return _read_page(_shared_browser, url, source)
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = _launch_chrome(p)
        try:
            return _read_page(browser, url, source)
        finally:
            browser.close()


def fetch(url: str) -> Page:
    url = canonical_url(url)
    source = detect_source(url)
    page = _fetch_linkedin(url) if source == "LinkedIn" else _fetch_with_chrome(url, source)
    if len(page.text) < MIN_CHARS:
        raise FetchFailed("抓到的内容太短，可能需要登录或页面已下线")
    return page


def page_from_pasted(url: str | None, text: str) -> Page:
    """Used when fetching fails and you paste the JD yourself."""
    url = canonical_url(url) if url else ""
    return Page(url, detect_source(url) if url else "Other", "", _clean(text))
