from job_pipeline.fetch import canonical_url, detect_source, linkedin_job_id


def test_detect_source():
    assert detect_source("https://www.seek.co.nz/job/95140987?ref=x") == "SEEK"
    assert detect_source("https://nz.linkedin.com/jobs/view/123456789") == "LinkedIn"
    assert detect_source("https://nz.indeed.com/viewjob?jk=abc") == "Indeed"
    assert detect_source("https://nz.prosple.com/graduate-employers/x") == "Prosple"
    assert detect_source("https://careers.example.com/1") == "Other"


def test_linkedin_job_id_from_view_and_search_urls():
    assert linkedin_job_id("https://www.linkedin.com/jobs/view/4455707620/?trk=abc") == "4455707620"
    assert linkedin_job_id("https://www.linkedin.com/jobs/view/frontend-developer-at-acme-4455707620") == "4455707620"
    assert linkedin_job_id("https://www.linkedin.com/jobs/search/?currentJobId=4455707620&keywords=x") == "4455707620"


def test_canonical_url_strips_tracking():
    assert canonical_url("https://www.seek.co.nz/job/95140987?type=standout&ref=search#sol") == "https://www.seek.co.nz/job/95140987"
    assert canonical_url("https://nz.indeed.com/viewjob?jk=abc123&from=serp") == "https://nz.indeed.com/viewjob?jk=abc123"
    assert canonical_url("https://www.linkedin.com/jobs/search/?currentJobId=42424242") == "https://www.linkedin.com/jobs/view/42424242"
