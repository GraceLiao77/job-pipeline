"""job-pipeline CLI.

  job-pipeline bot            run the Telegram bot (long-running)
  job-pipeline ready          list jobs with a CV ready, as JSON (used by /apply-batch)
  job-pipeline applied <id>   mark a job applied (Notion + Telegram)
  job-pipeline fetch <url>    debug: print the text fetched for a job link
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
    elif cmd == "fetch" and args:
        from .fetch import fetch

        page = fetch(args[0])
        print(page.source, page.title, len(page.text), "chars\n")
        print(page.text[:3000])
    else:
        print(__doc__)
