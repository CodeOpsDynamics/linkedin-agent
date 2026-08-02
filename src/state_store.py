"""
Persistent state for the pipeline. SQLite file lives at data/agent_state.db.

In GitHub Actions, this file needs to persist across runs -- either commit it
back to the repo after each run (simplest, fine for low write-volume like this),
or swap this module out for a hosted SQLite (Turso) / Postgres (Supabase) later
without touching the rest of the pipeline.
"""
import sqlite3
import hashlib
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "agent_state.db"


def get_conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS seen_topics (
            topic_hash TEXT PRIMARY KEY,
            title TEXT,
            source TEXT,
            link TEXT,
            first_seen_at TEXT
        );

        CREATE TABLE IF NOT EXISTS candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic_hash TEXT,
            title TEXT,
            summary TEXT,
            source TEXT,
            link TEXT,
            suggested_type TEXT,       -- 'post' or 'article'
            confidence REAL,
            reasoning TEXT,
            status TEXT DEFAULT 'pending_classification_review',
            confirmed_type TEXT,       -- set once you confirm/override
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id INTEGER,
            draft_type TEXT,
            draft_text TEXT,
            status TEXT DEFAULT 'pending_final_review',  -- pending_final_review, approved, rejected, published
            linkedin_post_urn TEXT,
            created_at TEXT,
            FOREIGN KEY (candidate_id) REFERENCES candidates (id)
        );
        """
    )
    conn.commit()
    conn.close()


def topic_hash(title: str) -> str:
    return hashlib.sha256(title.strip().lower().encode()).hexdigest()[:16]


def is_seen(title: str) -> bool:
    conn = get_conn()
    row = conn.execute(
        "SELECT 1 FROM seen_topics WHERE topic_hash = ?", (topic_hash(title),)
    ).fetchone()
    conn.close()
    return row is not None


def mark_seen(title: str, source: str, link: str):
    conn = get_conn()
    conn.execute(
        "INSERT OR IGNORE INTO seen_topics (topic_hash, title, source, link, first_seen_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (topic_hash(title), title, source, link, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


def add_candidate(title, summary, source, link, suggested_type, confidence, reasoning):
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO candidates (topic_hash, title, summary, source, link, suggested_type, "
        "confidence, reasoning, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            topic_hash(title), title, summary, source, link,
            suggested_type, confidence, reasoning,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()
    candidate_id = cur.lastrowid
    conn.close()
    return candidate_id


def get_pending_candidates():
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM candidates WHERE status = 'pending_classification_review' "
        "ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_candidate(candidate_id: int):
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM candidates WHERE id = ?", (candidate_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def mark_candidate_confirmed(candidate_id: int, confirmed_type: str):
    conn = get_conn()
    conn.execute(
        "UPDATE candidates SET confirmed_type = ?, status = 'confirmed' WHERE id = ?",
        (confirmed_type, candidate_id),
    )
    conn.commit()
    conn.close()


def add_draft(candidate_id: int, draft_type: str, draft_text: str):
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO drafts (candidate_id, draft_type, draft_text, created_at) "
        "VALUES (?, ?, ?, ?)",
        (candidate_id, draft_type, draft_text, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    draft_id = cur.lastrowid
    conn.close()
    return draft_id


def get_draft(draft_id: int):
    conn = get_conn()
    row = conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def mark_draft_published(draft_id: int, post_urn: str):
    conn = get_conn()
    conn.execute(
        "UPDATE drafts SET status = 'published', linkedin_post_urn = ? WHERE id = ?",
        (post_urn, draft_id),
    )
    conn.commit()
    conn.close()
