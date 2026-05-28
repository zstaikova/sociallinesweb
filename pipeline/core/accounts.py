"""
Encrypted multi-account credential store.

Accounts are saved to accounts.enc (Fernet-encrypted JSON) in the project root.
The encryption key lives in Windows Credential Manager via keyring; if keyring is
unavailable it falls back to a .accounts.key file (chmod 600).
"""
import json
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ACCOUNTS_FILE = ROOT / "accounts.enc"
KEY_FILE      = ROOT / ".accounts.key"
SERVICE_NAME  = "socialline-accounts"


# ── Key management ──────────────────────────────────────────────────────────

def _get_or_create_key() -> bytes:
    try:
        import keyring
        key = keyring.get_password(SERVICE_NAME, "fernet_key")
        if key:
            return key.encode()
        from cryptography.fernet import Fernet
        key = Fernet.generate_key()
        keyring.set_password(SERVICE_NAME, "fernet_key", key.decode())
        return key
    except Exception:
        pass  # keyring unavailable — fall back to key file

    if KEY_FILE.exists():
        return KEY_FILE.read_bytes().strip()

    from cryptography.fernet import Fernet
    key = Fernet.generate_key()
    KEY_FILE.write_bytes(key)
    try:
        KEY_FILE.chmod(0o600)
    except Exception:
        pass
    return key


def _fernet():
    from cryptography.fernet import Fernet
    return Fernet(_get_or_create_key())


# ── Credential key lists per platform (for snapshot-from-env) ───────────────

PLATFORM_ENV_KEYS: dict[str, list[str]] = {
    "facebook":  ["FACEBOOK_PAGE_ID", "FACEBOOK_PAGE_ACCESS_TOKEN",
                  "FACEBOOK_APP_ID", "FACEBOOK_APP_SECRET"],
    "instagram": ["INSTAGRAM_ACCOUNT_ID", "INSTAGRAM_ACCESS_TOKEN",
                  "INSTAGRAM_APP_ID", "INSTAGRAM_APP_SECRET",
                  "FACEBOOK_PAGE_ID", "FACEBOOK_PAGE_ACCESS_TOKEN"],
    "threads":   ["THREADS_USER_ID", "THREADS_ACCESS_TOKEN",
                  "THREADS_APP_ID", "THREADS_APP_SECRET",
                  "FACEBOOK_PAGE_ID", "FACEBOOK_PAGE_ACCESS_TOKEN"],
    "x":         ["X_CONSUMER_KEY", "X_CONSUMER_SECRET",
                  "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET"],
    "tiktok":    ["TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET",
                  "TIKTOK_ACCESS_TOKEN", "TIKTOK_OPEN_ID"],
    "bluesky":   ["BLUESKY_HANDLE", "BLUESKY_APP_PASSWORD"],
    "linkedin":  ["LINKEDIN_ACCESS_TOKEN", "LINKEDIN_PERSON_URN",
                  "LINKEDIN_CLIENT_ID", "LINKEDIN_CLIENT_SECRET",
                  "LINKEDIN_REFRESH_TOKEN"],
    "pinterest": ["PINTEREST_ACCESS_TOKEN", "PINTEREST_BOARD_ID",
                  "PINTEREST_CLIENT_ID", "PINTEREST_CLIENT_SECRET",
                  "PINTEREST_REFRESH_TOKEN"],
    "reddit":    ["REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET"],
    "youtube":   ["YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET",
                  "YOUTUBE_ACCESS_TOKEN", "YOUTUBE_REFRESH_TOKEN"],
    "telegram":  ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"],
    # Content platforms
    "substack":     ["SUBSTACK_SESSION_TOKEN", "SUBSTACK_PUBLICATION"],
    # Selling platforms
    "etsy":         ["ETSY_API_KEY", "ETSY_ACCESS_TOKEN", "ETSY_SHOP_ID",
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
    def __init__(self, accounts_file: Path = None):
        self._file = accounts_file or ACCOUNTS_FILE
        self._accounts: list[Account] = []
        self._load()

    # -- persistence --

    def _load(self):
        if not self._file.exists():
            self._accounts = []
            return
        try:
            encrypted = self._file.read_bytes()
            data = json.loads(_fernet().decrypt(encrypted))
            self._accounts = [Account(a) for a in data.get("accounts", [])]
        except Exception as e:
            print(f"Warning: could not load {self._file.name}: {e}")
            self._accounts = []

    def _save(self):
        self._file.parent.mkdir(parents=True, exist_ok=True)
        data = {"accounts": [a.to_dict() for a in self._accounts]}
        encrypted = _fernet().encrypt(json.dumps(data).encode())
        self._file.write_bytes(encrypted)

    # -- write operations --

    def add(self, platform: str, account_name: str, account_id: str,
            credentials: dict) -> Account:
        """Add or update an account. Returns the Account."""
        existing = next(
            (a for a in self._accounts
             if a.platform == platform and a.account_id == account_id),
            None,
        )
        if existing:
            existing.account_name = account_name
            existing.credentials  = credentials
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

    def get_credentials(self, platform: str) -> dict:
        """Returns active account credentials, or {} if none stored."""
        account = self.get_active(platform)
        return account.credentials if account else {}

    def update_credentials(self, platform: str, partial: dict):
        """Merge `partial` into the active account's credentials and save."""
        account = self.get_active(platform)
        if account:
            account.credentials = {**account.credentials, **partial}
            self._save()

    def snapshot_from_env(self, platform: str) -> dict:
        """Read current env vars for this platform into a credentials dict."""
        import os
        keys = PLATFORM_ENV_KEYS.get(platform, [])
        return {k: os.environ[k] for k in keys if os.environ.get(k)}
