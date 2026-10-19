"""Local SQLite state.

Notion is the record you look at; this database is the bot's working memory:
full JD text (too long for Notion properties), which Telegram message belongs to
which job or checklist item, and whether the morning card has been sent.
"""

import sqlite3
from dataclasses import dataclass, fields
from datetime import datetime, timezone

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    url TEXT NOT NULL,
    source TEXT NOT NULL,
    company TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',
    jd_text TEXT NOT NULL DEFAULT '',
    job_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    fit_score INTEGER,
    seniority TEXT NOT NULL DEFAULT 'Unknown',
    track TEXT NOT NULL DEFAULT 'Other',
    screen_reason TEXT NOT NULL DEFAULT '',
    cover_letter_needed INTEGER NOT NULL DEFAULT 0,
    slug TEXT NOT NULL DEFAULT '',
    notion_page_id TEXT NOT NULL DEFAULT '',
    card_sent INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS checklist (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    question TEXT NOT NULL,
    message_id INTEGER,
    answer TEXT,
    generation INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS cv_messages (
    message_id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id)
);
"""


@dataclass
class Job:
    id: int
    url: str
    source: str
    company: str
    title: str
    location: str
    jd_text: str
    job_key: str
    status: str
    fit_score: int | None
    seniority: str
    track: str
    screen_reason: str
    cover_letter_needed: int
    slug: str
    notion_page_id: str
    card_sent: int
    created_at: str


@dataclass
class ChecklistItem:
    id: int
    job_id: int
    question: str
    message_id: int | None
    answer: str | None
    generation: int


def connect() -> sqlite3.Connection:
    path = settings().db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    # Worker threads (asyncio.to_thread) reuse connections opened on the event loop.
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _job(row: sqlite3.Row | None) -> Job | None:
    return Job(**dict(row)) if row else None


def find_by_key(conn: sqlite3.Connection, job_key: str) -> Job | None:
    return _job(conn.execute("SELECT * FROM jobs WHERE job_key = ?", (job_key,)).fetchone())


def get_job(conn: sqlite3.Connection, job_id: int) -> Job | None:
    return _job(conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone())


def jobs_with_status(conn: sqlite3.Connection, status: str) -> list[Job]:
    rows = conn.execute("SELECT * FROM jobs WHERE status = ? ORDER BY fit_score DESC", (status,))
    return [Job(**dict(r)) for r in rows]


def insert_job(conn: sqlite3.Connection, **values) -> Job:
    values.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    cols = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    cur = conn.execute(f"INSERT INTO jobs ({cols}) VALUES ({marks})", tuple(values.values()))
    conn.commit()
    return get_job(conn, cur.lastrowid)


def update_job(conn: sqlite3.Connection, job_id: int, **values) -> Job:
    allowed = {f.name for f in fields(Job)} - {"id"}
    assert set(values) <= allowed, set(values) - allowed
    sets = ", ".join(f"{k} = ?" for k in values)
    conn.execute(f"UPDATE jobs SET {sets} WHERE id = ?", (*values.values(), job_id))
    conn.commit()
    return get_job(conn, job_id)


def add_checklist(conn: sqlite3.Connection, job_id: int, questions: list[str], generation: int) -> list[ChecklistItem]:
    for q in questions:
        conn.execute(
            "INSERT INTO checklist (job_id, question, generation) VALUES (?, ?, ?)",
            (job_id, q, generation),
        )
    conn.commit()
    return checklist_for(conn, job_id, generation)


def checklist_for(conn: sqlite3.Connection, job_id: int, generation: int) -> list[ChecklistItem]:
    rows = conn.execute(
        "SELECT * FROM checklist WHERE job_id = ? AND generation = ? ORDER BY id", (job_id, generation)
    )
    return [ChecklistItem(**dict(r)) for r in rows]


def latest_generation(conn: sqlite3.Connection, job_id: int) -> int:
    row = conn.execute("SELECT MAX(generation) FROM checklist WHERE job_id = ?", (job_id,)).fetchone()
    return row[0] or 0


def get_item(conn: sqlite3.Connection, item_id: int) -> ChecklistItem | None:
    row = conn.execute("SELECT * FROM checklist WHERE id = ?", (item_id,)).fetchone()
    return ChecklistItem(**dict(row)) if row else None


def item_by_message(conn: sqlite3.Connection, message_id: int) -> ChecklistItem | None:
    row = conn.execute("SELECT * FROM checklist WHERE message_id = ?", (message_id,)).fetchone()
    return ChecklistItem(**dict(row)) if row else None


def set_item(conn: sqlite3.Connection, item_id: int, **values) -> None:
    sets = ", ".join(f"{k} = ?" for k in values)
    conn.execute(f"UPDATE checklist SET {sets} WHERE id = ?", (*values.values(), item_id))
    conn.commit()


def remember_cv_message(conn: sqlite3.Connection, message_id: int, job_id: int) -> None:
    """Remember which job a sent CV belongs to, so replying to it can revise that CV."""
    conn.execute("INSERT OR REPLACE INTO cv_messages VALUES (?, ?)", (message_id, job_id))
    conn.commit()


def job_for_cv_message(conn: sqlite3.Connection, message_id: int) -> Job | None:
    row = conn.execute("SELECT job_id FROM cv_messages WHERE message_id = ?", (message_id,)).fetchone()
    return get_job(conn, row[0]) if row else None
