import os
import requests
from datetime import datetime

from pipeline.core.base_publisher import BasePublisher
from pipeline.core.content_item import ContentItem

GRAPH_URL = "https://graph.facebook.com/v19.0"


class FacebookPublisher(BasePublisher):
    def __init__(self, credentials: dict = None):
        _c = credentials or {}
        self.page_id      = _c.get("FACEBOOK_PAGE_ID")           or os.environ["FACEBOOK_PAGE_ID"]
        self.access_token = _c.get("FACEBOOK_PAGE_ACCESS_TOKEN") or os.environ["FACEBOOK_PAGE_ACCESS_TOKEN"]

    def get_account_info(self) -> "dict | None":
        resp = requests.get(
            f"{GRAPH_URL}/{self.page_id}",
            params={"fields": "name,id", "access_token": self.access_token},
            timeout=10,
        )
        if resp.ok:
            data = resp.json()
            return {"name": data.get("name", ""), "id": str(data.get("id", ""))}
        return None

    def verify_auth(self) -> bool:
        info = self.get_account_info()
        if info:
            print(f"Facebook auth OK — page: {info['name']} ({info['id']})")
            return True
        print("Facebook auth failed")
        return False

    def publish(self, item: ContentItem) -> bool:
        if not item.media_path or not item.media_path.exists():
            print("  No media file to publish")
            return False

        caption = self._build_caption(item)

        try:
            with open(item.media_path, "rb") as f:
                resp = requests.post(
                    f"{GRAPH_URL}/{self.page_id}/photos",
                    data={
                        "caption": caption,
                        "access_token": self.access_token,
                    },
                    files={"source": f},
                    timeout=30,
                )

            if resp.ok:
                data = resp.json()
                post_id = data.get("post_id") or data.get("id")
                item.metadata["facebook_post_id"] = post_id
                item.posted_at = datetime.utcnow()
                return True
            else:
                print(f"  Facebook API error: {resp.status_code} {resp.text}")
                return False

        except Exception as e:
            print(f"  Facebook publish exception: {e}")
            return False

    def _build_caption(self, item: ContentItem) -> str:
        parts = [item.caption]
        if item.tags:
            hashtags = " ".join(f"#{t.replace(' ', '')}" for t in item.tags[:10])
            parts.append(hashtags)
        return "\n\n".join(p for p in parts if p)
