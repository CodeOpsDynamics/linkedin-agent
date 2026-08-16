"""
Persistent state for the pipeline -- Turso (hosted libsql) backed.
"""
import os
import hashlib
from datetime import datetime, timezone
import libsql_client

TURSO_DATABASE_URL = os.environ.get("TURSO_DATABASE_URL")
TURSO_AUTH_TOKEN = os.environ.get("TURSO_AUTH_TOKEN")


def get_client():
    if not TURSO_DATABASE_URL or not TURSO_AUTH_TOKEN:
        raise RuntimeError(
            "TURSO_DATABASE_URL / TURSO_AUTH_TOKEN not set."
        )
    url = TURSO_DATABASE_URL.replace("libsql://", "https://", 1)
    return libsql_client.create_client_sync(url=url, auth_token=TURSO_AUTH_TOKEN)


def _row_to_dict(rs, row):
    return {col: val for col, val in zip(rs.columns, row)}


def init_db():
    client = get_client()
    client.execute(
        """
        CREATE TABLE IF NOT EXISTS seen_topics (
            topic_hash TEXT PRIMARY KEY,
            title TEXT,
            source TEXT,
            link TEXT,
            first_seen_at TEXT
        )
        """
    )
    client.execute(
        """
        CREATE TABLE IF NOT EXISTS candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic_hash TEXT,
            title TEXT,
            summary TEXT,
            source TEXT,
            link TEXT,
            suggested_type TEXT,
            confidence REAL,
            reasoning TEXT,
            status TEXT DEFAULT 'pending_classification_review',
            confirmed_type TEXT,
            created_at TEXT
        )
        """
    )
    client.execute(
        """
        CREATE TABLE IF NOT EXISTS drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id INTEGER,
            draft_type TEXT,
            draft_text TEXT,
            status TEXT DEFAULT 'pending_final_review',
            linkedin_post_urn TEXT,
            created_at TEXT,
            FOREIGN KEY (candidate_id) REFERENCES candidates (id)
        )
        """
    )
    client.execute(
        """
        CREATE TABLE IF NOT EXISTS processed_updates (
            update_id INTEGER PRIMARY KEY,
            processed_at TEXT
        )
        """
    )

    # Migration: articles need a title + cover-image brief for the manual
    # publish step (LinkedIn's Articles tab has no API access at all -- see
    # README). ALTER TABLE errors if the column already exists; that's the
    # expected steady-state after the first run, so it's swallowed here.
    for column_def in ("title TEXT", "image_brief TEXT"):
        try:
            client.execute(f"ALTER TABLE drafts ADD COLUMN {column_def}")
        except Exception as e:
            if "duplicate column" not in str(e).lower():
                raise

    client.close()


def topic_hash(title: str) -> str:
    return hashlib.sha256(title.strip().lower().encode()).hexdigest()[:16]


def is_seen(title: str) -> bool:
    client = get_client()
    rs = client.execute("SELECT 1 FROM seen_topics WHERE topic_hash = ?", [topic_hash(title)])
    client.close()
    return len(rs.rows) > 0


def mark_seen(title: str, source: str, link: str):
    client = get_client()
    client.execute(
        "INSERT OR IGNORE INTO seen_topics (topic_hash, title, source, link, first_seen_at) "
        "VALUES (?, ?, ?, ?, ?)",
        [topic_hash(title), title, source, link, datetime.now(timezone.utc).isoformat()],
    )
    client.close()


def add_candidate(title, summary, source, link, suggested_type, confidence, reasoning):
    client = get_client()
    client.execute(
        "INSERT INTO candidates (topic_hash, title, summary, source, link, suggested_type, "
        "confidence, reasoning, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            topic_hash(title), title, summary, source, link,
            suggested_type, confidence, reasoning,
            datetime.now(timezone.utc).isoformat(),
        ],
    )
    rs = client.execute("SELECT last_insert_rowid()")
    candidate_id = rs.rows[0][0]
    client.close()
    return candidate_id


def get_pending_candidates():
    client = get_client()
    rs = client.execute(
        "SELECT * FROM candidates WHERE status = 'pending_classification_review' ORDER BY created_at DESC"
    )
    result = [_row_to_dict(rs, r) for r in rs.rows]
    client.close()
    return result


def get_candidate(candidate_id: int):
    client = get_client()
    rs = client.execute("SELECT * FROM candidates WHERE id = ?", [candidate_id])
    result = _row_to_dict(rs, rs.rows[0]) if rs.rows else None
    client.close()
    return result


def mark_candidate_confirmed(candidate_id: int, confirmed_type: str):
    client = get_client()
    client.execute(
        "UPDATE candidates SET confirmed_type = ?, status = 'confirmed' WHERE id = ?",
        [confirmed_type, candidate_id],
    )
    client.close()


def mark_candidate_skipped(candidate_id: int):
    client = get_client()
    client.execute("UPDATE candidates SET status = 'skipped' WHERE id = ?", [candidate_id])
    client.close()


def mark_candidate_published(candidate_id: int):
    client = get_client()
    client.execute("UPDATE candidates SET status = 'published' WHERE id = ?", [candidate_id])
    client.close()


def mark_candidate_delivered_manual(candidate_id: int):
    """Terminal state for articles: LinkedIn's Articles tab can't be reached
    via API, so this marks that the ready-to-paste package was handed to
    Himanshu -- not that it's actually live yet. Prevents the same
    candidate from being re-drafted/re-queued."""
    client = get_client()
    client.execute("UPDATE candidates SET status = 'delivered_manual' WHERE id = ?", [candidate_id])
    client.close()


def add_draft(candidate_id: int, draft_type: str, draft_text: str, title: str = None, image_brief: str = None):
    client = get_client()
    client.execute(
        "INSERT INTO drafts (candidate_id, draft_type, draft_text, title, image_brief, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [candidate_id, draft_type, draft_text, title, image_brief, datetime.now(timezone.utc).isoformat()],
    )
    rs = client.execute("SELECT last_insert_rowid()")
    draft_id = rs.rows[0][0]
    client.close()
    return draft_id


def get_draft(draft_id: int):
    client = get_client()
    rs = client.execute("SELECT * FROM drafts WHERE id = ?", [draft_id])
    result = _row_to_dict(rs, rs.rows[0]) if rs.rows else None
    client.close()
    return result


def mark_draft_published(draft_id: int, post_urn: str):
    client = get_client()
    client.execute(
        "UPDATE drafts SET status = 'published', linkedin_post_urn = ? WHERE id = ?",
        [post_urn, draft_id],
    )
    client.close()


def mark_draft_rejected(draft_id: int):
    client = get_client()
    client.execute("UPDATE drafts SET status = 'rejected' WHERE id = ?", [draft_id])
    client.close()


def mark_draft_delivered_manual(draft_id: int):
    """Terminal state for articles -- see mark_candidate_delivered_manual."""
    client = get_client()
    client.execute("UPDATE drafts SET status = 'delivered_manual' WHERE id = ?", [draft_id])
    client.close()


def try_mark_update_processed(update_id: int) -> bool:
    """True = first time seeing this update_id, False = duplicate."""
    client = get_client()
    client.execute(
        "INSERT OR IGNORE INTO processed_updates (update_id, processed_at) VALUES (?, ?)",
        [update_id, datetime.now(timezone.utc).isoformat()],
    )
    rs = client.execute("SELECT changes()")
    inserted = rs.rows[0][0] == 1
    client.close()
    return inserted


def queue_draft(draft_id: int):
    client = get_client()
    client.execute(
        "UPDATE drafts SET status = 'queued' WHERE id = ?", [draft_id]
    )
    client.close()


def get_latest_actionable_draft():
    """Most recent draft still awaiting a publish/discard decision (status
    'pending_final_review') -- lets /publish, /publishnow, /discard work
    without the draft_id when there's an obvious 'the thing I just drafted'
    to act on, instead of forcing a copy-paste of the id every time."""
    client = get_client()
    rs = client.execute(
        "SELECT * FROM drafts WHERE status = 'pending_final_review' "
        "ORDER BY created_at DESC LIMIT 1"
    )
    result = _row_to_dict(rs, rs.rows[0]) if rs.rows else None
    client.close()
    return result


def get_next_queued_draft(draft_type: str = None):
    """Oldest queued draft, if any -- used by the daily scheduled-publish jobs.
    Pass draft_type='post' or 'article' to pull only that slot's queue;
    omit it to pull the oldest queued draft regardless of type."""
    client = get_client()
    if draft_type:
        rs = client.execute(
            "SELECT * FROM drafts WHERE status = 'queued' AND draft_type = ? "
            "ORDER BY created_at ASC LIMIT 1",
            [draft_type],
        )
    else:
        rs = client.execute(
            "SELECT * FROM drafts WHERE status = 'queued' ORDER BY created_at ASC LIMIT 1"
        )
    result = _row_to_dict(rs, rs.rows[0]) if rs.rows else None
    client.close()
    return result
