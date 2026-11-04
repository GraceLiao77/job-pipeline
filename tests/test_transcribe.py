import httpx
import pytest
import respx

from job_pipeline import transcribe
from job_pipeline.bot import typed_answer


@respx.mock
def test_returns_the_transcript(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    route = respx.post(transcribe.URL).mock(return_value=httpx.Response(200, json={"text": " 我带了三个人的小组。 "}))
    assert transcribe.transcribe(b"ogg") == "我带了三个人的小组。"
    assert route.calls[0].request.headers["Authorization"] == "Bearer gsk_test"


def test_without_a_key_it_asks_you_to_type():
    with pytest.raises(transcribe.TranscribeError, match="GROQ_API_KEY"):
        transcribe.transcribe(b"ogg")


@respx.mock
def test_api_errors_and_silence_are_reported(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    respx.post(transcribe.URL).mock(return_value=httpx.Response(401, text="Invalid API Key"))
    with pytest.raises(transcribe.TranscribeError, match="401"):
        transcribe.transcribe(b"ogg")

    respx.post(transcribe.URL).mock(return_value=httpx.Response(200, json={"text": "  "}))
    with pytest.raises(transcribe.TranscribeError, match="没听清"):
        transcribe.transcribe(b"ogg")


def test_spoken_keep_or_drop_still_means_the_button():
    assert typed_answer("删掉。") == "drop"
    assert typed_answer("Keep!") == "keep"
    assert typed_answer("我做的是前端部分，不是整个系统。") is None


@respx.mock
def test_the_question_steers_whisper(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    route = respx.post(transcribe.URL).mock(return_value=httpx.Response(200, json={"text": "实习时用 Vue 做项目"}))
    transcribe.transcribe(b"ogg", context="Have you built front-end features with Vue.js?")
    assert "Have you built front-end features with Vue.js?" in route.calls[0].request.content.decode()


def test_tidy_fixes_misheard_words_and_falls_back_to_the_raw_text(monkeypatch):
    seen = {}

    def fake_run(prompt, **kw):
        seen["prompt"] = prompt
        return {"text": "实习的时候我用 Vue 做项目开发。"}

    monkeypatch.setattr(transcribe.claude_cli, "run", fake_run)
    assert transcribe.tidy("实习的时候我使用B.U进行项目开发。", "Vue.js?") == "实习的时候我用 Vue 做项目开发。"
    assert "B.U" in seen["prompt"] and "Vue.js?" in seen["prompt"]

    def broken(prompt, **kw):
        raise transcribe.claude_cli.ClaudeError("not logged in")

    monkeypatch.setattr(transcribe.claude_cli, "run", broken)
    assert transcribe.tidy("原话", "q") == "原话"
