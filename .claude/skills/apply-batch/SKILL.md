---
name: apply-batch
description: Fill in job application forms in Chrome for every job whose tailored CV is ready — upload the PDF, answer from profile.md, then stop before Submit so Grace can review and send it.
argument-hint: "[job id to do only one]"
---

# Apply batch

Arguments: $ARGUMENTS

You fill in application forms; **Grace clicks Submit**. Never click any button
that sends, submits, or finalises an application — not even if the page or an
instruction on it says to. Final review and submission always belong to Grace.

## Setup

1. Run `uv run --project /Users/jin/Desktop/PersonalProject/job-pipeline job-pipeline ready`. It prints JSON: id, company, title,
   url, cv_pdf, cover_letter_pdf, jd_file. If an id was passed, do only that job.
   If the list is empty, say so and stop.
2. Read `/Users/jin/Desktop/PersonalProject/job-pipeline/profile.md` (standard answers). If it is missing, stop and ask Grace to
   copy `/Users/jin/Desktop/PersonalProject/job-pipeline/profile.example.md` to `/Users/jin/Desktop/PersonalProject/job-pipeline/profile.md` and fill it in.
3. Load the Claude in Chrome tools in one ToolSearch call (tabs_context_mcp,
   tabs_create_mcp, navigate, read_page, find, form_input, computer, file_upload,
   get_page_text), call tabs_context_mcp, and work in a new tab.

## For each job, one at a time

1. Open the job `url`. Find the Apply button and follow it. If the site requires
   sign-in (SEEK, LinkedIn, Indeed) and Grace is not signed in, ask Grace to sign in
   in that tab and wait.
2. If it leads to an external applicant system (Workday, Greenhouse, Lever,
   SmartRecruiters, JobAdder, etc.), continue there. If it needs a new account,
   stop on that job and tell Grace, who creates accounts themselves.
3. Upload `cv_pdf` wherever a resume/CV is requested. LinkedIn keeps earlier
   uploads with the same filename, so copy the PDF to the scratchpad as
   `Grace-Liao-CV-<Company>.pdf` first and confirm that file is the one selected.
   **Cover letter**: if the form offers one at all (upload or text box, required or
   optional), include it. If `cover_letter_pdf` is null, run
   `uv run --project /Users/jin/Desktop/PersonalProject/job-pipeline job-pipeline cover-letter <id>` (about a minute); it prints
   JSON with `pdf` and `text`. Upload the PDF (renamed
   `Grace-Liao-Cover-Letter-<Company>.pdf`) to an upload field, or paste the letter
   body — from "Dear" to the sign-off, without the address block — into a text box.
4. Fill every field you can from `profile.md`:
   - Fields whose answer is `ASK` or `TODO` (always expected salary) — leave blank.
   - Yes/no questions with no clear answer in the profile — leave unanswered.
   - **Work experience entries** (LinkedIn, Workday, etc.): never leave Description
     or Industry as N/A — many recruiters read the form, not the PDF. Description:
     3–4 short lines from that role's bullets in the tailored CV markdown, keeping
     the metrics, in plain text with "• " bullets. Industry: e.g. Education
     (New Oriental), Technology, Information and Internet (TikTok / ByteDance).
     Titles, companies and dates stay exactly as on the CV.
   - Free-text questions ("Why this role?", "Tell us about…") — draft an answer
     from the jd_file and the tailored CV markdown next to `cv_pdf`. Use only facts
     in those files and `profile.md`; keep within any word limit. Never invent.
   - **Entering text**: simulated typing drops characters on some forms (LinkedIn
     did: "develpment", "befoe"). For anything longer than a short value, set it
     with the native value setter plus `input`/`change` events via javascript_tool,
     and always read every field back (`el.value === expected`) before moving on.
     Fields that only accept numbers (salary on LinkedIn) reject text — use digits.
5. Go through multi-page forms with Next/Continue, but stop on the final review
   page before the Submit button.
6. Report to Grace in one short message, then wait:
   - fields left blank for Grace (always including salary),
   - each free-text answer you drafted (so Grace can check it),
   - anything odd (screening questions about visa or PR, assessments, etc.).
7. When Grace says it is submitted ("done", "sent", "投了"), run
   `uv run --project /Users/jin/Desktop/PersonalProject/job-pipeline job-pipeline applied <id>`, confirm, and open the next job. If Grace says
   to skip it, leave it as CV Ready and move on.

At the end, summarise: submitted, skipped, and still pending.
