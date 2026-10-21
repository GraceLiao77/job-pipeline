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