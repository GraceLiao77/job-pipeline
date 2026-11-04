"""Telegram voice note → text, with Whisper hosted on Groq (needs GROQ_API_KEY)."""

import httpx

from .config import settings

URL = "https://api.groq.com/openai/v1/audio/transcriptions"
MODEL = "whisper-large-v3-turbo"
# Whisper copies the script and vocabulary of its prompt: this keeps Chinese in
# simplified characters and spells the tech words the way the CV does.
PROMPT = "以下是普通话的句子，用简体中文。React, TypeScript, Node.js, Next.js, AWS, Claude, Interview Coach."


class TranscribeError(Exception):
    pass


def transcribe(audio: bytes, filename: str = "voice.ogg") -> str:
    key = settings().groq_api_key
    if not key:
        raise TranscribeError("没有设置 GROQ_API_KEY，语音用不了，请先打字回复")
    try:
        resp = httpx.post(
            URL,
            headers={"Authorization": f"Bearer {key}"},
            files={"file": (filename, audio, "audio/ogg")},
            data={"model": MODEL, "prompt": PROMPT, "response_format": "json"},
            timeout=60,
        )
    except httpx.HTTPError as e:
        raise TranscribeError(f"连不上 Groq：{e}") from e
    if resp.status_code != 200:
        raise TranscribeError(f"Groq 返回 {resp.status_code}：{resp.text[:200]}")
    text = resp.json().get("text", "").strip()
    if not text:
        raise TranscribeError("没听清，请再说一次")
    return text
