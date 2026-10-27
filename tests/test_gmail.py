from job_pipeline.gmail import extract_job_links

def test_extracts_seek_job_links():
    html = '<a href="https://www.seek.co.nz/job/95140987?ref=alert">Full Stack Dev</a>'
    assert extract_job_links(html) == ["https://www.seek.co.nz/job/95140987"]

def test_ignores_non_job_links():
    html = '''<a href="https://www.seek.co.nz/job/111">Dev</a>
              <a href="https://www.seek.co.nz/unsubscribe?x=1">Unsubscribe</a>'''
    assert extract_job_links(html) == ["https://www.seek.co.nz/job/111"]

def test_deduplicates_same_job():
    html = '<a href="https://www.seek.co.nz/job/111?a=1">x</a><a href="https://www.seek.co.nz/job/111?b=2">y</a>'
    assert extract_job_links(html) == ["https://www.seek.co.nz/job/111"]

def test_extracts_linkedin_job_link_and_ignores_others():
    html = '''
        <a href="https://www.linkedin.com/comm/widgets/promo?lipi=urn%3Ali%3Apage%3Aemail_email_job_alert_digest_01%3"></a>
        <a href="https://www.linkedin.com/help/linkedin/answer/4788?lang=en&amp;lipi=urn%3Ali%3Apage%3Aemail_email_jo"></a>
        <a href="https://www.linkedin.com/comm/jobs/alerts?lipi=urn%3Ali%3Apage%3Aemail_email_job_alert_digest_01%3Bi"></a>
        <a href="https://www.linkedin.com/comm/jobs/view/4471344184/?trackingId=1212&amp"></a>
    '''
    assert extract_job_links(html) == ['https://www.linkedin.com/jobs/view/4471344184']

def test_extracts_linkedin_job_link_without_comm():
    html = '<a href="https://www.linkedin.com/jobs/view/4471344184/?refId=abc">Dev</a>'
    assert extract_job_links(html) == ["https://www.linkedin.com/jobs/view/4471344184"]

def test_ignores_links_from_unknown_sites():
    html = '''<a href="https://apps.apple.com/app/indeed/id309735670">App Store</a>
              <a href="https://www.seek.co.nz/job/111">Dev</a>'''
    assert extract_job_links(html) == ["https://www.seek.co.nz/job/111"]
    

def test_resolves_seek_tracking_links():
    html = '<a href="https://url.s.seek.co.nz/uni/ss/c/abc">Dev</a>'
    fake_resolve = lambda url: "https://nz.seek.com/job/95417639?ref=alert"
    assert extract_job_links(html, resolve=fake_resolve) == ["https://www.seek.co.nz/job/95417639"]


def test_tracking_link_that_goes_nowhere_is_skipped():
    html = '<a href="https://url.s.seek.co.nz/uni/ss/c/abc">Unsubscribe</a>'
    assert extract_job_links(html, resolve=lambda url: None) == []

def test_extracts_indeed_job_links():
    html = '<a href="https://nz.indeed.com/rc/clk/dl?jk=abc123def4567890&amp;from=ja&amp;tk=FAKE">Dev</a>'
    assert extract_job_links(html) == ["https://nz.indeed.com/viewjob?jk=abc123def4567890"]


def test_ignores_indeed_search_and_legal_links():
    html = '''<a href="https://nz.indeed.com/jobs?q=react&amp;l=Auckland">More jobs</a>
              <a href="https://nz.indeed.com/legal?hl=en">Terms</a>'''
    assert extract_job_links(html) == []

def test_indeed_job_key_found_even_when_not_first():
    html = '<a href="https://nz.indeed.com/rc/clk/dl?from=ja&amp;jk=abc123def4567890">Dev</a>'
    assert extract_job_links(html) == ["https://nz.indeed.com/viewjob?jk=abc123def4567890"]