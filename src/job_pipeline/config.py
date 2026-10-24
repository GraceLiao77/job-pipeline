"""Settings loaded from .env. Every module reads config through `settings()`."""

import os
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"


@dataclass(frozen=True)
class Settings:
    telegram_token: str
    telegram_chat_id: int | None
    notion_token: str
    notion_data_source_id: str
    career_craft_dir: Path
    digest_hour: int
    min_fit: int  # alert jobs scoring below this are filtered, not sent for review

    @property
    def criteria_file(self) -> Path:
        """Your own criteria.md (git-ignored); falls back to the committed example."""
        own = ROOT / "criteria.md"
        return own if own.exists() else ROOT / "criteria.example.md"

    @property
    def db_path(self) -> Path:
        return DATA_DIR / "state.db"


@cache
def settings() -> Settings:
    load_dotenv(ROOT / ".env")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    return Settings(
        telegram_token=os.environ["TELEGRAM_BOT_TOKEN"],
        telegram_chat_id=int(chat_id) if chat_id else None,
        notion_token=os.getenv("NOTION_TOKEN", ""),
        notion_data_source_id=os.environ["NOTION_DATA_SOURCE_ID"],
        career_craft_dir=Path(os.environ["CAREER_CRAFT_DIR"]),
        digest_hour=int(os.getenv("DIGEST_HOUR", "8")),
        min_fit=int(os.getenv("MIN_FIT", "3")),
    )
