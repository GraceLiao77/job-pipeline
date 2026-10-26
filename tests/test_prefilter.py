from job_pipeline.prefilter import quick_reject


def test_senior_roles_are_rejected():
    assert quick_reject("Senior Full Stack Engineer | Xero | Auckland") is not None
    assert quick_reject("Staff Frontend Engineer | OKX | Remote") is not None


def test_normal_roles_go_to_claude():
    assert quick_reject("Full Stack Developer | Xero | Auckland") is None


def test_singapore_is_rejected():
    assert quick_reject("Frontend Engineer | OCBC | Singapore") is not None


def test_lead_is_a_whole_word_only():
    assert quick_reject("Tech Lead | Halter | Auckland") is not None
    assert quick_reject("Software Developer | Leading Edge Ltd | Auckland") is None   # 公司名里的 Leading 不算


def test_case_does_not_matter():
    assert quick_reject("SENIOR developer | Acme | Auckland") is not None