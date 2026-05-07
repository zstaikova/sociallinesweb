"""
User management store.
Users are stored in data/users.db (SQLite).
Roles: admin | user | reviewer
"""
import sqlite3
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
                    username      TEXT PRIMARY KEY,
                    password_hash TEXT NOT NULL,
                    role          TEXT NOT NULL DEFAULT 'user',
                    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)

    def _ensure_default_admin(self):
        with self._conn() as conn:
            count = conn.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0]
            if count == 0:
                conn.execute(
                    "INSERT OR IGNORE INTO users (username, password_hash, role) VALUES (?,?,?)",
                    ("admin", generate_password_hash("admin"), "admin")
                )

    # ── public API ────────────────────────────────────────────────────────────

    def authenticate(self, username: str, password: str) -> dict | None:
        """Returns user dict if credentials are valid, else None."""
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if row and check_password_hash(row["password_hash"], password):
            return dict(row)
        return None

    def create(self, username: str, password: str, role: str = "user") -> dict:
        if role not in self.ROLES:
            raise ValueError(f"Invalid role: {role}")
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (?,?,?)",
                (username, generate_password_hash(password), role)
            )
        return self.get(username)

    def get(self, username: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        return dict(row) if row else None

    def list_all(self) -> list:
        with self._conn() as conn:
            rows = conn.execute("SELECT username, role, created_at FROM users ORDER BY created_at").fetchall()
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
        """True if this user still has the initial default password."""
        row = self.get(username)
        if not row:
            return False
        return check_password_hash(row["password_hash"], username)
