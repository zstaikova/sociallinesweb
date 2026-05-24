"""
Fernet-encrypted Playwright storage_state persistence.

Sessions live at data/browser_sessions/{platform}.session.enc — same Fernet
key as accounts.enc so we don't need a second secret.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SESSIONS_DIR = ROOT / "data" / "browser_sessions"


def _fernet():
    from pipeline.core.accounts import _fernet as accounts_fernet
    return accounts_fernet()


class BrowserSessionStore:
    def __init__(self, sessions_dir: Path = SESSIONS_DIR):
        self._dir = sessions_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, platform: str) -> Path:
        return self._dir / f"{platform}.session.enc"

    def save(self, platform: str, storage_state: dict):
        data = json.dumps(storage_state).encode()
        encrypted = _fernet().encrypt(data)
        self._path(platform).write_bytes(encrypted)

    def load(self, platform: str) -> dict | None:
        p = self._path(platform)
        if not p.exists():
            return None
        try:
            encrypted = p.read_bytes()
            return json.loads(_fernet().decrypt(encrypted))
        except Exception:
            return None

    def exists(self, platform: str) -> bool:
        return self._path(platform).exists()

    def delete(self, platform: str):
        p = self._path(platform)
        if p.exists():
            p.unlink()
