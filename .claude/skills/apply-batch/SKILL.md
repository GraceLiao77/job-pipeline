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

1. Run `uv run job-pipeline ready` in this repo. It prints JSON: id, company, title,
   url, cv_pdf, cover_letter_pdf, jd_file. If an id was passed, do only that job.
   If the list is empty, say so and stop.
2. Read `profile.md` (standard answers). If it is missing, stop and ask Grace to
   copy `profile.example.md` to `profile.md` and fill it in.
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
3. Upload `cv_pdf` wherever a resume/CV is requested, and `cover_letter_pdf` when a
   cover letter is requested and one exists. If a cover letter is required but none
   exists, tell Grace instead of uploading the CV twice.
4. Fill every field you can from `profile.md`:
   - Fields whose answer is `ASK` or `TODO` (always expected salary) — leave blank.
   - Yes/no questions with no clear answer in the profile — leave unanswered.
   - Free-text questions ("Why this role?", "Tell us about…") — draft an answer
     from the jd_file and the tailored CV markdown next to `cv_pdf`. Use only facts
     in those files and `profile.md`; keep within any word limit. Never invent.
5. Go through multi-page forms with Next/Continue, but stop on the final review
   page before the Submit button.
6. Report to Grace in one short message, then wait:
   - fields left blank for Grace (always including salary),
   - each free-text answer you drafted (so Grace can check it),
   - anything odd (screening questions about visa or PR, assessments, etc.).
7. When Grace says it is submitted ("done", "sent", "投了"), run
   `uv run job-pipeline applied <id>`, confirm, and open the next job. If Grace says
   to skip it, leave it as CV Ready and move on.

At the end, summarise: submitted, skipped, and still pending.
