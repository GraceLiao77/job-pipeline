# Privacy Policy — job-pipeline

_Last updated: 28 October 2026_

job-pipeline is a personal, single-user tool. It runs on its owner's own computer
to help with their job search. It is not offered as a service to anyone else.

## What it accesses

- **Gmail, read-only** (`gmail.readonly`). It searches for job-alert emails from
  job sites and for replies to job applications the owner has sent. It never
  sends, deletes, or changes any email.

## What it does with that data

- Job links and job-ad text are stored in a local SQLite database on the owner's
  computer.
- Job-ad text and short excerpts of application replies are sent to Anthropic's
  Claude to screen jobs and classify replies.
- Records of applications the owner has submitted are saved to the owner's own
  Notion workspace.
- Nothing is sold, shared with third parties for advertising, or used for any
  purpose other than the owner's job search.

## Google API data

job-pipeline's use of information received from Google APIs adheres to the
[Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy),
including the Limited Use requirements.

## Revoking access

Access can be revoked at any time at
[myaccount.google.com/permissions](https://myaccount.google.com/permissions).
Local data can be deleted by removing the project's `data/` folder.

## Contact

Questions: open an issue at
[github.com/GraceLiao77/job-pipeline](https://github.com/GraceLiao77/job-pipeline/issues).
