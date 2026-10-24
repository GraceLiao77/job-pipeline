import httpx
import respx

from job_pipeline.gmail import resolve_redirect

TRACKER = "https://url.s.seek.co.nz/uni/ss/c/abc"


@respx.mock
def test_returns_the_address_on_the_note():
    respx.head(TRACKER).mock(
        return_value=httpx.Response(302, headers={"Location": "https://nz.seek.com/job/123"})
    )
    assert resolve_redirect(TRACKER) == "https://nz.seek.com/job/123"


@respx.mock
def test_returns_none_when_there_is_no_note():
    respx.head(TRACKER).mock(return_value=httpx.Response(200))
    assert resolve_redirect(TRACKER) is None
