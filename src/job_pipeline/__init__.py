"""job-pipeline CLI.

  job-pipeline bot            run the Telegram bot (long-running)
  job-pipeline ready          list jobs with a CV ready, as JSON (used by /apply-batch)
  job-pipeline applied <id>   mark a job applied (Notion + Telegram)
  job-pipeline cover-letter <id>  write + render a cover letter for a CV-ready job
  job-pipeline fetch <url>    debug: print the text fetched for a job link
  job-pipeline alerts [days]  list job links from job-alert emails in the last N days (default 7)
  job-pipeline ingest-alerts [days]  screen new alert jobs into the Inbox (the bot does this daily at 7:30)
"""

import json
import sys

def main() -> None:
    cmd, args = (sys.argv[1], sys.argv[2:]) if len(sys.argv) > 1 else ("help", [])

    if cmd == "bot":
        from .bot import run

        run()
    elif cmd == "ready":
        from . import store
        from .config import settings

        out = settings().career_craft_dir / "workspace" / "output"
        with store.connect() as conn:
            jobs = store.jobs_with_status(conn, "CV Ready")
        print(json.dumps(
            [
                {
                    "id": j.id,
                    "company": j.company,
                    "title": j.title,
                    "url": j.url,
                    "cv_pdf": str(out / j.slug / "grace-liao-cv.pdf"),
                    "cover_letter_pdf": str(p) if (p := out / j.slug / "grace-liao-cover-letter.pdf").exists() else None,
                    "jd_file": str(settings().career_craft_dir / "workspace" / "job-descriptions" / f"{j.slug}.md"),
                }
                for j in jobs
            ],
            ensure_ascii=False,
            indent=2,
        ))
    elif cmd == "applied" and args:
        from . import pipeline, store

        with store.connect() as conn:
            job = store.get_job(conn, int(args[0]))
            if not job:
                sys.exit(f"no job #{args[0]}")
            pipeline.mark_applied(conn, job)
        print(f"marked #{job.id} {job.company} as Applied")
    elif cmd == "cover-letter" and args:
        from . import notion, store
        from .tailor import write_cover_letter

        with store.connect() as conn:
            job = store.get_job(conn, int(args[0]))
        if not job:
            sys.exit(f"no job #{args[0]}")
        letter = write_cover_letter(job)
        if job.notion_page_id:
            cv = letter.with_name("grace-liao-cv.pdf")
            try:
                notion.upload_cv(job.notion_page_id, [cv, letter.with_suffix(".pdf")], letter.parent)
            except Exception as e:  # noqa: BLE001
                print(f"(Notion sync failed: {e})", file=sys.stderr)
        print(json.dumps({"markdown": str(letter), "pdf": str(letter.with_suffix(".pdf")), "text": letter.read_text()}, ensure_ascii=False))
    elif cmd == "fetch" and args:
        from .fetch import fetch

        page = fetch(args[0])
        print(page.source, page.title, len(page.text), "chars\n")
        print(page.text[:3000])
    elif cmd == "alerts":
        from .gmail import ALERT_QUERY, extract_job_links, fetch_message_html, gmail_service, resolve_redirect, search_alert_ids

        days = args[0] if args else "7"  # 默认查最近 7 天
        service = gmail_service()  # 只登录一次，后面所有请求共用
        ids = search_alert_ids(service, f"{ALERT_QUERY} newer_than:{days}d")
        links: list[str] = []
        for msg_id in ids:
            found = extract_job_links(fetch_message_html(service, msg_id), resolve=resolve_redirect)
            print(f"  {msg_id}: {len(found)} jobs")
            links += found
        unique = list(dict.fromkeys(links))  # 不同邮件里的同一个职位只留一个
        print(f"\n{len(ids)} alert emails in the last {days} days → {len(unique)} unique job links")
        for link in unique:
            print(link)
    elif cmd == "ingest-alerts":
        from . import alerts, store
        from .gmail import gmail_service

        days = int(args[0]) if args else 1
        with store.connect() as conn:
            print(alerts.ingest_alerts(conn, gmail_service(), days))
    else:
        print(__doc__)
