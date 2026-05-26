"""
Render job store (SQLite) — tracks video pipeline jobs and outputs.
Same pattern as SourceStore and ScheduleStore.
"""
import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path


class RenderStore:
    def __init__(self, brand_id: str, data_root: Path = None):
        if data_root is None:
            data_root = Path(__file__).resolve().parent.parent / "data" / "brands"
        self.brand_id = brand_id
        self.db_path  = str(data_root / brand_id / "renders.db")
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS render_jobs (
                    id              TEXT PRIMARY KEY,
                    brand_id        TEXT NOT NULL,
                    script_file     TEXT,
                    script_text     TEXT NOT NULL,
                    meta            TEXT,
                    status          TEXT NOT NULL DEFAULT 'pending_render',
                    priority        INTEGER NOT NULL DEFAULT 0,
                    review_required INTEGER NOT NULL DEFAULT 1,
                    reviewed_by     TEXT,
                    reviewed_at     TEXT,
                    reject_reason   TEXT,
                    error           TEXT,
                    created_at      TEXT NOT NULL,
                    updated_at      TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS render_outputs (
                    id          TEXT PRIMARY KEY,
                    job_id      TEXT NOT NULL REFERENCES render_jobs(id),
                    platform    TEXT NOT NULL,
                    dimensions  TEXT NOT NULL,
                    orientation TEXT,
                    engine      TEXT NOT NULL,
                    file_path   TEXT,
                    caption     TEXT,
                    hashtags    TEXT,
                    status      TEXT NOT NULL DEFAULT 'pending',
                    error       TEXT,
                    rendered_at TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS render_captions (
                    id           TEXT PRIMARY KEY,
                    job_id       TEXT NOT NULL REFERENCES render_jobs(id),
                    platform     TEXT NOT NULL,
                    caption      TEXT NOT NULL,
                    hashtags     TEXT,
                    generated_by TEXT NOT NULL DEFAULT 'claude',
                    created_at   TEXT NOT NULL
                )
            """)

    def _parse_job(self, row) -> dict:
        d = dict(row)
        if d.get("meta"):
            d["meta"] = json.loads(d["meta"])
        d["review_required"] = bool(d["review_required"])
        return d

    def _parse_output(self, row) -> dict:
        d = dict(row)
        if d.get("hashtags"):
            d["hashtags"] = json.loads(d["hashtags"])
        return d

    # ── Jobs ──────────────────────────────────────────────────────────────────

    def create_job(self, script_text: str, script_file: str = None,
                   meta: dict = None) -> str:
        job_id = str(uuid.uuid4())[:8]
        now    = datetime.now().isoformat(timespec="seconds")
        meta   = meta or {}
        priority = meta.get("priority", 0)
        review_required = 1 if meta.get("review_required", True) else 0
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO render_jobs
                   (id, brand_id, script_file, script_text, meta, status,
                    priority, review_required, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (job_id, self.brand_id, script_file, script_text,
                 json.dumps(meta), "pending_render",
                 priority, review_required, now, now)
            )
        return job_id

    def get(self, job_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM render_jobs WHERE id=?", (job_id,)
            ).fetchone()
        return self._parse_job(row) if row else None

    def get_next_pending(self) -> dict | None:
        """Oldest highest-priority pending_render job for this brand."""
        with self._conn() as conn:
            row = conn.execute(
                """SELECT * FROM render_jobs
                   WHERE status='pending_render' AND brand_id=?
                   ORDER BY priority ASC, created_at ASC
                   LIMIT 1""",
                (self.brand_id,)
            ).fetchone()
        return self._parse_job(row) if row else None

    def get_pending_review(self) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM render_jobs
                   WHERE status='pending_review' AND brand_id=?
                   ORDER BY created_at""",
                (self.brand_id,)
            ).fetchall()
        return [self._parse_job(r) for r in rows]

    def list_all(self, status: str = None) -> list:
        with self._conn() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM render_jobs WHERE brand_id=? AND status=? ORDER BY created_at DESC",
                    (self.brand_id, status)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM render_jobs WHERE brand_id=? ORDER BY created_at DESC",
                    (self.brand_id,)
                ).fetchall()
        return [self._parse_job(r) for r in rows]

    def update_status(self, job_id: str, status: str, error: str = None):
        now = datetime.now().isoformat(timespec="seconds")
        with self._conn() as conn:
            conn.execute(
                "UPDATE render_jobs SET status=?, error=?, updated_at=? WHERE id=?",
                (status, error, now, job_id)
            )

    def approve_job(self, job_id: str, reviewed_by: str = "user"):
        now = datetime.now().isoformat(timespec="seconds")
        with self._conn() as conn:
            conn.execute(
                """UPDATE render_jobs
                   SET status='approved', reviewed_by=?, reviewed_at=?, updated_at=?
                   WHERE id=?""",
                (reviewed_by, now, now, job_id)
            )

    def reject_job(self, job_id: str, reason: str, reviewed_by: str = "user"):
        now = datetime.now().isoformat(timespec="seconds")
        with self._conn() as conn:
            conn.execute(
                """UPDATE render_jobs
                   SET status='rejected', reject_reason=?, reviewed_by=?,
                       reviewed_at=?, updated_at=?
                   WHERE id=?""",
                (reason, reviewed_by, now, now, job_id)
            )

    # ── Outputs ───────────────────────────────────────────────────────────────

    def add_output(self, job_id: str, platform: str, file_path: str,
                   dimensions: str, engine: str,
                   caption: str = "", hashtags: str = "[]") -> str:
        out_id = str(uuid.uuid4())[:8]
        now    = datetime.now().isoformat(timespec="seconds")

        _ORIENTATION = {
            "1080x1920": "vertical",
            "1080x1080": "square",
            "1920x1080": "horizontal",
            "1280x720":  "horizontal",
        }
        orientation = _ORIENTATION.get(dimensions, "vertical")

        with self._conn() as conn:
            conn.execute(
                """INSERT INTO render_outputs
                   (id, job_id, platform, dimensions, orientation, engine,
                    file_path, caption, hashtags, status, rendered_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (out_id, job_id, platform, dimensions, orientation, engine,
                 file_path, caption, hashtags, "complete", now)
            )
        return out_id

    def get_outputs(self, job_id: str) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM render_outputs WHERE job_id=? ORDER BY platform",
                (job_id,)
            ).fetchall()
        return [self._parse_output(r) for r in rows]

    # ── Captions ──────────────────────────────────────────────────────────────

    def save_caption(self, job_id: str, platform: str,
                     caption: str, hashtags: list) -> str:
        cap_id = str(uuid.uuid4())[:8]
        now    = datetime.now().isoformat(timespec="seconds")
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO render_captions
                   (id, job_id, platform, caption, hashtags, generated_by, created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (cap_id, job_id, platform, caption,
                 json.dumps(hashtags), "claude", now)
            )
        return cap_id

    def get_captions(self, job_id: str) -> dict:
        """Returns {platform: {caption, hashtags}} for a job."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM render_captions WHERE job_id=?", (job_id,)
            ).fetchall()
        result = {}
        for row in rows:
            d = dict(row)
            result[d["platform"]] = {
                "caption":  d["caption"],
                "hashtags": json.loads(d["hashtags"]) if d["hashtags"] else [],
            }
        return result
