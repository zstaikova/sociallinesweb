import os
import requests

from pipeline.core.base_publisher import BasePublisher
from pipeline.core.content_item import ContentItem

LS_API_BASE = "https://api.lemonsqueezy.com/v1"


class LemonSqueezyPublisher(BasePublisher):
    """
    Read-only connector: verifies the API key and shows store info.
    Product creation is not available via the Lemon Squeezy API.
    """

    def __init__(self, credentials: dict = None):
        _c = credentials or {}
        self.api_key = (
            _c.get("LEMONSQUEEZY_API_KEY")
            or os.environ.get("LEMONSQUEEZY_API_KEY", "")
        )

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept":        "application/vnd.api+json",
        }

    def publish(self, item: ContentItem) -> bool:
        print("  Lemon Squeezy: product creation via API is not supported — manage products at app.lemonsqueezy.com")
        return False

    def verify_auth(self) -> bool:
        return self.get_account_info() is not None

    def get_account_info(self) -> "dict | None":
        r = requests.get(f"{LS_API_BASE}/me", headers=self._headers(), timeout=10)
        if r.ok:
            data  = r.json().get("data", {})
            attrs = data.get("attributes", {})
            name  = attrs.get("name") or attrs.get("email") or "Lemon Squeezy User"
            uid   = str(data.get("id", "lemonsqueezy"))
            print(f"  Lemon Squeezy: connected as {name}")
            return {"name": name, "id": uid}
        print(f"  Lemon Squeezy: auth failed {r.status_code} — check API key")
        return None
