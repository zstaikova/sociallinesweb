"""
Encrypted multi-account credential store.

Each brand has its own accounts.enc file, encrypted with a key derived from
a master secret + brand_id via HKDF. If ACCOUNT_MASTER_KEY is set in the
environment, per-brand keys are used (production). Otherwise falls back to
a global keyring/file key (development).
"""
import base64
import json
import os
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ACCOUNTS_FILE = ROOT / "accounts.enc"
KEY_FILE      = ROOT / ".accounts.key"
SERVICE_NAME  = "socialline-accounts"


# ── Key management ──────────────────────────────────────────────────────────

def _derive_brand_key(master_secret: bytes, brand_id: str) -> bytes:
    """HKDF-derive a per-brand Fernet key from the master secret."""
    from cryptography.hazmat.primitives.hashes import SHA256
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.backends import default_backend
    hkdf = HKDF(
        algorithm=SHA256(),
        length=32,
        salt=brand_id.encode(),
        info=b"socialline-account-store-v1",
        backend=default_backend(),
    )
    return base64.urlsafe_b64encode(hkdf.derive(master_secret))


def _global_fernet():
    """Fallback: single global key from keyring or key file (dev mode)."""
    from cryptography.fernet import Fernet
    try:
        import keyring
        key = keyring.get_password(SERVICE_NAME, "fernet_key")
        if key:
            return Fernet(key.encode())
        key = Fernet.generate_key()
        keyring.set_password(SERVICE_NAME, "fernet_key", key.decode())
        return Fernet(key)
    except Exception:
        pass

    if KEY_FILE.exists():
        return Fernet(KEY_FILE.read_bytes().strip())

    key = Fernet.generate_key()
    KEY_FILE.write_bytes(key)
    try:
        KEY_FILE.chmod(0o600)
    except Exception:
        pass
    return Fernet(key)


# ── Credential key lists per platform ───────────────────────────────────────
# Only per-user tokens go here — app-level CLIENT_ID/SECRET stay in server .env

PLATFORM_ENV_KEYS: dict[str, list[str]] = {
    "facebook":  ["FACEBOOK_PAGE_ID", "FACEBOOK_PAGE_ACCESS_TOKEN"],
    "instagram": ["INSTAGRAM_ACCOUNT_ID", "INSTAGRAM_ACCESS_TOKEN",
                  "FACEBOOK_PAGE_ID", "FACEBOOK_PAGE_ACCESS_TOKEN"],
    "threads":   ["THREADS_USER_ID", "THREADS_ACCESS_TOKEN",
                  "FACEBOOK_PAGE_ID", "FACEBOOK_PAGE_ACCESS_TOKEN"],
    "x":         ["X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET"],
    "tiktok":    ["TIKTOK_ACCESS_TOKEN", "TIKTOK_OPEN_ID", "TIKTOK_REFRESH_TOKEN",
                  "TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET"],
    "bluesky":   ["BLUESKY_HANDLE", "BLUESKY_APP_PASSWORD"],
    "linkedin":  ["LINKEDIN_ACCESS_TOKEN", "LINKEDIN_PERSON_URN", "LINKEDIN_REFRESH_TOKEN"],
    "pinterest": ["PINTEREST_ACCESS_TOKEN", "PINTEREST_BOARD_ID", "PINTEREST_REFRESH_TOKEN"],
    "reddit":    ["REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET"],
    "youtube":   ["YOUTUBE_ACCESS_TOKEN", "YOUTUBE_REFRESH_TOKEN"],
    "telegram":  ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"],
    "substack":  ["SUBSTACK_SESSION_TOKEN", "SUBSTACK_PUBLICATION"],
    "etsy":      ["ETSY_ACCESS_TOKEN", "ETSY_SHOP_ID",
                  "ETSY_DEFAULT_PRICE", "ETSY_DEFAULT_TAXONOMY_ID"],
    "lemonsqueezy": ["LEMONSQUEEZY_API_KEY"],
    "gumroad":      ["GUMROAD_ACCESS_TOKEN"],
    "teachable":    ["TEACHABLE_API_KEY"],
    "udemy":        ["UDEMY_BEARER_TOKEN"],
}


# ── Account model ────────────────────────────────────────────────────────────

class Account:
    def __init__(self, data: dict):
        self.id           = data["id"]
        self.platform     = data["platform"]
        self.account_name = data["account_name"]
        self.account_id   = data.get("account_id", "")
        self.credentials  = data.get("credentials", {})
        self.active       = data.get("active", False)
        self.created_at   = data.get("created_at", datetime.utcnow().isoformat())

    def to_dict(self) -> dict:
        return {
            "id":           self.id,
            "platform":     self.platform,
            "account_name": self.account_name,
            "account_id":   self.account_id,
            "credentials":  self.credentials,
            "active":       self.active,
            "created_at":   self.created_at,
        }

    def public_dict(self) -> dict:
        """Safe representation without credentials, for the frontend."""
        d = self.to_dict()
        d["has_credentials"] = bool(self.credentials)
        d.pop("credentials")
        return d


# ── Store ────────────────────────────────────────────────────────────────────

class AccountStore:
    def __init__(self, accounts_file: Path = None, brand_id: str = None):
        self._file          = accounts_file or ACCOUNTS_FILE
        self._brand_id      = brand_id
        self._accounts: list[Account] = []
        self.load_error: str | None = None
        self._load()

    def _fernet(self):
        from cryptography.fernet import Fernet
        master = os.environ.get("ACCOUNT_MASTER_KEY", "")
        if master and self._brand_id:
            return Fernet(_derive_brand_key(master.encode(), self._brand_id))
        return _global_fernet()

    def _load(self):
        if not self._file.exists():
            self._accounts = []
            return
        try:
            encrypted = self._file.read_bytes()
            data = json.loads(self._fernet().decrypt(encrypted))
            self._accounts = [Account(a) for a in data.get("accounts", [])]
            self.load_error = None
        except Exception as e:
            self.load_error = str(e)
            print(f"Warning: could not load {self._file.name}: {e}")
            self._accounts = []

    def _save(self):
        self._file.parent.mkdir(parents=True, exist_ok=True)
        data = {"accounts": [a.to_dict() for a in self._accounts]}
        encrypted = self._fernet().encrypt(json.dumps(data).encode())
        self._file.write_bytes(encrypted)

    # -- write operations --

    def add(self, platform: str, account_name: str, account_id: str,
            credentials: dict) -> Account:
        existing = next(
            (a for a in self._accounts
             if a.platform == platform and a.account_id == account_id),
            None,
        )
        if existing:
            existing.account_name = account_name
            existing.credentials  = credentials
            existing.created_at   = datetime.utcnow().isoformat()
            self._save()
            return existing

        first = not any(a for a in self._accounts if a.platform == platform)
        account = Account({
            "id":           str(uuid.uuid4()),
            "platform":     platform,
            "account_name": account_name,
            "account_id":   account_id,
            "credentials":  credentials,
            "active":       first,
            "created_at":   datetime.utcnow().isoformat(),
        })
        self._accounts.append(account)
        self._save()
        return account

    def set_active(self, account_id: str) -> bool:
        account = next((a for a in self._accounts if a.id == account_id), None)
        if not account:
            return False
        for a in self._accounts:
            if a.platform == account.platform:
                a.active = (a.id == account_id)
        self._save()
        return True

    def remove(self, account_id: str) -> bool:
        removed = next((a for a in self._accounts if a.id == account_id), None)
        if not removed:
            return False
        self._accounts = [a for a in self._accounts if a.id != account_id]
        if removed.active:
            remaining = [a for a in self._accounts if a.platform == removed.platform]
            if remaining:
                remaining[0].active = True
        self._save()
        return True

    # -- read operations --

    def list_all(self, platform: str = None) -> list[Account]:
        if platform:
            return [a for a in self._accounts if a.platform == platform]
        return list(self._accounts)

    def get_active(self, platform: str) -> "Account | None":
        accounts = [a for a in self._accounts if a.platform == platform]
        return next((a for a in accounts if a.active), accounts[0] if accounts else None)

    # Platforms that share credentials with another platform
    _PLATFORM_ALIASES = {
        "youtube_short":   "youtube",
        "instagram_reel":  "instagram",
        "instagram_post":  "instagram",
    }

    def get_credentials(self, platform: str) -> dict:
        account = self.get_active(platform)
        if not account:
            alias = self._PLATFORM_ALIASES.get(platform)
            if alias:
                account = self.get_active(alias)
        return account.credentials if account else {}

    def update_credentials(self, platform: str, partial: dict):
        """Merge partial into the active account's credentials and save."""
        account = self.get_active(platform)
        if account:
            account.credentials = {**account.credentials, **partial}
            self._save()

    def snapshot_from_env(self, platform: str) -> dict:
        """Read current env vars for this platform into a credentials dict."""
        keys = PLATFORM_ENV_KEYS.get(platform, [])
        return {k: os.environ[k] for k in keys if os.environ.get(k)}
