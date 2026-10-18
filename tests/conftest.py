import pytest

from job_pipeline import config


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    """Point settings at a throwaway DB and career-craft dir for every test."""
    cc = tmp_path / "career-craft"
    (cc / "workspace" / "output").mkdir(parents=True)
    (cc / "workspace" / "facts.md").write_text("# Verified facts\n")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    monkeypatch.setenv("NOTION_TOKEN", "")
    monkeypatch.setenv("NOTION_DATA_SOURCE_ID", "ds")
    monkeypatch.setenv("CAREER_CRAFT_DIR", str(cc))
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "load_dotenv", lambda *a, **k: None)
    config.settings.cache_clear()
    yield cc
    config.settings.cache_clear()
