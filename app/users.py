"""
User management store.
Users are stored in data/users.db (SQLite).
Roles: admin | user | reviewer
"""
import sqlite3
import uuid
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

DB_PATH: Path = None  # set by server on startup


class UserStore:
    ROLES = ("admin", "user", "reviewer")

    def __init__(self, db_path: Path):
        self.db_path = str(db_path)
        self._init_db()
        self._ensure_default_admin()

    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id            TEXT PRIMARY KEY,
                    username      TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    role          TEXT NOT NULL DEFAULT 'user',
                    plan          TEXT NOT NULL DEFAULT 'free',
                    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id         TEXT PRIMARY KEY,
                    user_id    TEXT,
                    username   TEXT,
                    brand_id   TEXT,
                    action     TEXT NOT NULL,
                    platform   TEXT,
                    detail     TEXT,
                    ip         TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
            # Migrations for existing databases
            cols = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
            if "id" not in cols:
                conn.execute("ALTER TABLE users ADD COLUMN id TEXT")
                conn.execute("UPDATE users SET id = lower(hex(randomblob(8))) WHERE id IS NULL")
            if "plan" not in cols:
                conn.execute("ALTER TABLE users ADD COLUMN plan TEXT NOT NULL DEFAULT 'free'")

    def _ensure_default_admin(self):
        with self._conn() as conn:
            count = conn.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0]
            if count == 0:
                conn.execute(
                    "INSERT OR IGNORE INTO users (id, username, password_hash, role) VALUES (?,?,?,?)",
                    (str(uuid.uuid4())[:8], "admin", generate_password_hash("admin"), "admin")
                )

    # ── public API ────────────────────────────────────────────────────────────

    def authenticate(self, username: str, password: str) -> "dict | None":
        """Constant-time check to prevent username enumeration."""
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        # Always hash-check to prevent timing-based enumeration
        dummy = generate_password_hash("__dummy__")
        test  = row["password_hash"] if row else dummy
        if check_password_hash(test, password) and row:
            return dict(row)
        return None

    def create(self, username: str, password: str, role: str = "user") -> dict:
        if role not in self.ROLES:
            raise ValueError(f"Invalid role: {role}")
        uid = str(uuid.uuid4())[:8]
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO users (id, username, password_hash, role) VALUES (?,?,?,?)",
                (uid, username, generate_password_hash(password), role)
            )
        return self.get(username)

    def get(self, username: str) -> "dict | None":
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        return dict(row) if row else None

    def get_by_id(self, user_id: str) -> "dict | None":
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None

    def list_all(self) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, username, role, plan, created_at FROM users ORDER BY created_at"
            ).fetchall()
        return [dict(r) for r in rows]

    def update_password(self, username: str, new_password: str):
        with self._conn() as conn:
            conn.execute(
                "UPDATE users SET password_hash=? WHERE username=?",
                (generate_password_hash(new_password), username)
            )

    def update_role(self, username: str, role: str):
        if role not in self.ROLES:
            raise ValueError(f"Invalid role: {role}")
        with self._conn() as conn:
            conn.execute("UPDATE users SET role=? WHERE username=?", (role, username))

    def delete(self, username: str):
        with self._conn() as conn:
            conn.execute("DELETE FROM users WHERE username=?", (username,))

    def is_default_password(self, username: str) -> bool:
        row = self.get(username)
        if not row:
            return False
        return check_password_hash(row["password_hash"], username)

    # ── audit log ─────────────────────────────────────────────────────────────

    def log(self, action: str, username: str = None, user_id: str = None,
            brand_id: str = None, platform: str = None, detail: str = None,
            ip: str = None):
        """Append-only audit entry. Never raises."""
        try:
            with self._conn() as conn:
                conn.execute(
                    """INSERT INTO audit_log
                       (id, user_id, username, brand_id, action, platform, detail, ip)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (str(uuid.uuid4())[:8], user_id, username, brand_id,
                     action, platform, str(detail)[:500] if detail else None, ip)
                )
        except Exception:
            pass

    def recent_audit(self, limit: int = 100) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM audit_log ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]
