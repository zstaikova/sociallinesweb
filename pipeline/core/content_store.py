import sqlite3
import json
from pathlib import Path
from datetime import datetime
from .content_item import ContentItem, ContentStatus


DB_PATH = Path("socialline.db")


class ContentStore:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS content_items (
                    id TEXT PRIMARY KEY,
                    source_url TEXT NOT NULL,
                    source_platform TEXT NOT NULL,
                    media_path TEXT,
                    caption TEXT DEFAULT '',
                    tags TEXT DEFAULT '[]',
                    attribution TEXT DEFAULT '',
                    status TEXT DEFAULT 'discovered',
                    metadata TEXT DEFAULT '{}',
                    created_at TEXT,
                    posted_at TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS post_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    platform_post_id TEXT,
                    posted_at TEXT,
                    success INTEGER DEFAULT 1
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS fetch_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_at TEXT NOT NULL,
                    post_id TEXT NOT NULL,
                    subreddit TEXT NOT NULL,
                    title TEXT,
                    score INTEGER,
                    url TEXT,
                    outcome TEXT NOT NULL,
                    block_match TEXT
                )
            """)

    def exists(self, item_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM content_items WHERE id = ?", (item_id,)
            ).fetchone()
            return row is not None

    def save(self, item: ContentItem):
        with self._connect() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO content_items
                (id, source_url, source_platform, media_path, caption, tags,
                 attribution, status, metadata, created_at, posted_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item.id,
                item.source_url,
                item.source_platform,
                str(item.media_path) if item.media_path else None,
                item.caption,
                json.dumps(item.tags),
                item.attribution,
                item.status.value,
                json.dumps(item.metadata),
                item.created_at.isoformat(),
                item.posted_at.isoformat() if item.posted_at else None,
            ))

    def mark_posted(self, item_id: str, platform: str, platform_post_id: str = None):
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                "UPDATE content_items SET status = ?, posted_at = ? WHERE id = ?",
                (ContentStatus.POSTED.value, now, item_id)
            )
            conn.execute("""
                INSERT INTO post_history (content_id, platform, platform_post_id, posted_at)
                VALUES (?, ?, ?, ?)
            """, (item_id, platform, platform_post_id, now))

    def mark_failed(self, item_id: str, platform: str):
        with self._connect() as conn:
            conn.execute(
                "UPDATE content_items SET status = ? WHERE id = ?",
                (ContentStatus.FAILED.value, item_id)
            )
            conn.execute("""
                INSERT INTO post_history (content_id, platform, posted_at, success)
                VALUES (?, ?, ?, 0)
            """, (item_id, platform, datetime.utcnow().isoformat()))

    def already_posted_to(self, item_id: str, platform: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM post_history WHERE content_id = ? AND platform = ? AND success = 1",
                (item_id, platform)
            ).fetchone()
            return row is not None

    def log_fetch(self, post_id: str, subreddit: str, title: str, score: int,
                  url: str, outcome: str, block_match: str = None):
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO fetch_log (run_at, post_id, subreddit, title, score, url, outcome, block_match)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (datetime.utcnow().isoformat(), post_id, subreddit, title, score, url, outcome, block_match))

    def fetch_log_summary(self, limit: int = 100) -> dict:
        with self._connect() as conn:
            # outcome breakdown
            outcomes = conn.execute("""
                SELECT outcome, COUNT(*) as n FROM fetch_log GROUP BY outcome ORDER BY n DESC
            """).fetchall()

            # per-subreddit breakdown
            by_sub = conn.execute("""
                SELECT subreddit, outcome, COUNT(*) as n
                FROM fetch_log
                GROUP BY subreddit, outcome
                ORDER BY subreddit, n DESC
            """).fetchall()

            # recent blocked titles
            blocked = conn.execute("""
                SELECT subreddit, title, score, block_match, run_at
                FROM fetch_log WHERE outcome = 'blocked'
                ORDER BY run_at DESC LIMIT ?
            """, (limit,)).fetchall()

            # recent accepted titles
            accepted = conn.execute("""
                SELECT subreddit, title, score, run_at
                FROM fetch_log WHERE outcome = 'accepted'
                ORDER BY run_at DESC LIMIT ?
            """, (limit,)).fetchall()

        return {
            "outcomes": [dict(r) for r in outcomes],
            "by_subreddit": [dict(r) for r in by_sub],
            "blocked": [dict(r) for r in blocked],
            "accepted": [dict(r) for r in accepted],
        }

    def stats(self) -> dict:
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM content_items").fetchone()[0]
            posted = conn.execute(
                "SELECT COUNT(*) FROM content_items WHERE status = 'posted'"
            ).fetchone()[0]
            failed = conn.execute(
                "SELECT COUNT(*) FROM content_items WHERE status = 'failed'"
            ).fetchone()[0]
        return {"total": total, "posted": posted, "failed": failed}
