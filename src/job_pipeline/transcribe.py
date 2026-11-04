"""Telegram voice note → text, with Whisper hosted on Groq (needs GROQ_API_KEY).

Two steps: Whisper turns the audio into raw text, then `tidy` fixes misheard tech
words and drops filler, using the question you were answering as context.
"""

import logging

import httpx

from . import claude_cli
from .config import settings

log = logging.getLogger(__name__)

URL = "https://api.groq.com/openai/v1/audio/transcriptions"
MODEL = "whisper-large-v3-turbo"
# Whisper copies the script and vocabulary of its prompt: this keeps Chinese in
# simplified characters and spells the tech words the way the CV does.
PROMPT = (
    "以下是普通话的句子，用简体中文，英文技术词保留英文。"
    "React, Vue.js, TypeScript, JavaScript, jQuery, Node.js, Next.js, PostgreSQL, MySQL, AWS, Claude, "
    "TikTok, 新东方 New Oriental, Interview Coach."
)

TIDY_SCHEMA = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
}


class TranscribeError(Exception):
    pass


def transcribe(audio: bytes, context: str = "", filename: str = "voice.ogg") -> str:
    """`context` (the question being answered) steers Whisper towards its words."""
    key = settings().groq_api_key
    if not key:
        raise TranscribeError("没有设置 GROQ_API_KEY，语音用不了，请先打字回复")
    # Whisper only reads the last ~224 tokens of the prompt; keep the question short.
    prompt = f"{PROMPT} {context[:300]}".strip()
    try:
        resp = httpx.post(
            URL,
            headers={"Authorization": f"Bearer {key}"},
            files={"file": (filename, audio, "audio/ogg")},
            data={"model": MODEL, "prompt": prompt, "response_format": "json"},
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


def tidy(raw: str, context: str) -> str:
    """Fix misheard words and filler; never add facts. Falls back to `raw`."""
    prompt = (
        "Below is a speech-to-text transcript of a short spoken answer (usually Mandarin with English "
        "tech words) to the question shown. Speech recognition often mishears English terms, e.g. "
        "'B.U' for 'Vue', '加瓦' for 'Java'. Clean it up:\n"
        "- fix words that were clearly misheard, using the question for context;\n"
        "- remove filler (嗯, 就是, 然后, 那个) and repeated words; add punctuation;\n"
        "- keep the speaker's language (Chinese stays simplified Chinese, tech terms in English);\n"
        "- keep every fact, number and name exactly; do not add, guess or strengthen anything.\n"
        "Return the cleaned answer in `text`.\n\n"
        f"<question>\n{context}\n</question>\n\n<transcript>\n{raw}\n</transcript>"
    )
    try:
        out = claude_cli.run(prompt, schema=TIDY_SCHEMA, model="haiku", timeout=60)
    except Exception as e:  # noqa: BLE001 — the raw transcript still works
        log.warning("tidying the transcript failed: %s", e)
        return raw
    return out.get("text", "").strip() or raw
