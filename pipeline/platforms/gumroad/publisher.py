import os
import requests

from pipeline.core.base_publisher import BasePublisher
from pipeline.core.content_item import ContentItem

GUMROAD_API_BASE = "https://api.gumroad.com/v2"


class GumroadPublisher(BasePublisher):
    """
    Read-only connector: verifies access token and shows account info.
    Product creation via the Gumroad API is not supported.
    """

    def __init__(self, credentials: dict = None):
        _c = credentials or {}
        self.access_token = (
            _c.get("GUMROAD_ACCESS_TOKEN")
            or os.environ.get("GUMROAD_ACCESS_TOKEN", "")
        )

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.access_token}"}

    def publish(self, item: ContentItem) -> bool:
        print("  Gumroad: product creation via API is not supported — manage products at app.gumroad.com")
        return False

    def verify_auth(self) -> bool:
        return self.get_account_info() is not None

    def get_account_info(self) -> "dict | None":
        r = requests.get(f"{GUMROAD_API_BASE}/user", headers=self._headers(), timeout=10)
        if r.ok and r.json().get("success"):
            user = r.json().get("user", {})
            name = user.get("name") or user.get("email") or "Gumroad User"
            uid  = str(user.get("id") or "gumroad")
            print(f"  Gumroad: connected as {name}")
            return {"name": name, "id": uid}
        print(f"  Gumroad: auth failed {r.status_code} — check access token")
        return None
