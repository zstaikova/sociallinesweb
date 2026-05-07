import os
import base64
import requests
from datetime import datetime
from pathlib import Path

from pipeline.core.base_publisher import BasePublisher
from pipeline.core.content_item import ContentItem

API = "https://api.pinterest.com/v5"


class PinterestPublisher(BasePublisher):
    """
    Publishes image pins to Pinterest via the Pinterest API v5.
    Credentials: PINTEREST_ACCESS_TOKEN, PINTEREST_BOARD_ID
    Get token via Pinterest OAuth — scope: boards:read,pins:write
    Board ID format: {username}/{board_name} or numeric ID
    """

    def __init__(self, credentials: dict = None):
        _c = credentials or {}
        self.access_token = _c.get("PINTEREST_ACCESS_TOKEN") or os.environ["PINTEREST_ACCESS_TOKEN"]
        self.board_id     = _c.get("PINTEREST_BOARD_ID")     or os.environ["PINTEREST_BOARD_ID"]

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    def get_account_info(self) -> "dict | None":
        resp = requests.get(f"{API}/user_account", headers=self._headers(), timeout=10)
        if resp.ok:
            data = resp.json()
            return {"name": f"@{data.get('username', '')}", "id": data.get("id", self.board_id)}
        return None

    def verify_auth(self) -> bool:
        info = self.get_account_info()
        if info:
            print(f"Pinterest auth OK — {info['name']}")
            return True
        print("Pinterest auth failed")
        return False

    def publish(self, item: ContentItem) -> bool:
        try:
            title       = (item.caption or "")[:100]
            description = (item.caption or "")[:500]

            if item.media_path and item.media_path.exists():
                media_source = self._build_image_source(item.media_path)
            else:
                print("  Pinterest: no image — skipping")
                return False

            body = {
                "board_id": self.board_id,
                "title": title,
                "description": description,
                "media_source": media_source,
            }

            resp = requests.post(
                f"{API}/pins",
                headers=self._headers(),
                json=body,
                timeout=30,
            )

            if not resp.ok:
                print(f"  Pinterest pin failed: {resp.status_code} {resp.text}")
                return False

            pin_id = resp.json().get("id")
            item.metadata["pinterest_pin_id"] = pin_id
            item.posted_at = datetime.utcnow()
            return True

        except Exception as e:
            print(f"  Pinterest publish exception: {e}")
            return False

    def _build_image_source(self, media_path) -> dict:
        ext = Path(media_path).suffix.lower()
        content_type_map = {
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".png": "image/png", ".webp": "image/webp",
        }
        content_type = content_type_map.get(ext, "image/jpeg")
        with open(media_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
        return {
            "source_type": "image_base64",
            "content_type": content_type,
            "data": b64,
        }
