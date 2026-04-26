import os
import requests
from datetime import datetime

from pipeline.core.base_publisher import BasePublisher
from pipeline.core.content_item import ContentItem

IG_GRAPH  = "https://graph.instagram.com/v21.0"
FB_GRAPH  = "https://graph.facebook.com/v19.0"


class InstagramPublisher(BasePublisher):
    def __init__(self, ig_account_id: str = None, access_token: str = None):
        self.ig_account_id = ig_account_id or os.environ["INSTAGRAM_ACCOUNT_ID"]
        self.access_token  = access_token or os.environ["INSTAGRAM_ACCESS_TOKEN"]
        # Facebook Page used only for staging images on CDN
        self.page_id         = os.environ["FACEBOOK_PAGE_ID"]
        self.fb_page_token   = os.environ["FACEBOOK_PAGE_ACCESS_TOKEN"]

    def verify_auth(self) -> bool:
        resp = requests.get(
            f"{IG_GRAPH}/{self.ig_account_id}",
            params={
                "fields": "id,username,name",
                "access_token": self.access_token,
            },
            timeout=10,
        )
        if resp.ok:
            data = resp.json()
            handle = data.get("username") or data.get("name")
            print(f"Instagram auth OK — account: @{handle} ({data.get('id')})")
            return True
        print(f"Instagram auth failed: {resp.text}")
        return False

    def publish(self, item: ContentItem) -> bool:
        if not item.media_path or not item.media_path.exists():
            print("  No media file to publish")
            return False

        caption = self._build_caption(item)

        try:
            image_url = self._stage_image(item.media_path)
            if not image_url:
                return False

            container_id = self._create_container(image_url, caption)
            if not container_id:
                return False

            post_id = self._publish_container(container_id)
            if not post_id:
                return False

            item.metadata["instagram_post_id"] = post_id
            item.posted_at = datetime.utcnow()
            return True

        except Exception as e:
            print(f"  Instagram publish exception: {e}")
            return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _stage_image(self, media_path) -> str | None:
        """
        Upload image to Facebook Page as unpublished photo to get a
        publicly-accessible CDN URL (Instagram API requires a public URL).
        """
        with open(media_path, "rb") as f:
            resp = requests.post(
                f"{FB_GRAPH}/{self.page_id}/photos",
                data={
                    "published": "false",
                    "access_token": self.fb_page_token,
                },
                files={"source": f},
                timeout=30,
            )

        if not resp.ok:
            print(f"  Failed to stage image on Facebook CDN: {resp.status_code} {resp.text}")
            return None

        photo_id = resp.json().get("id")
        if not photo_id:
            print("  No photo ID returned from staging upload")
            return None

        info = requests.get(
            f"{FB_GRAPH}/{photo_id}",
            params={"fields": "images", "access_token": self.fb_page_token},
            timeout=10,
        )
        if not info.ok:
            print(f"  Could not retrieve staged image URL: {info.text}")
            return None

        images = info.json().get("images", [])
        if not images:
            print("  No image URLs in staged photo response")
            return None

        largest = max(images, key=lambda x: x.get("width", 0))
        return largest["source"]

    def _create_container(self, image_url: str, caption: str) -> str | None:
        resp = requests.post(
            f"{IG_GRAPH}/{self.ig_account_id}/media",
            data={
                "image_url": image_url,
                "caption": caption,
                "access_token": self.access_token,
            },
            timeout=30,
        )
        if not resp.ok:
            print(f"  Instagram container creation failed: {resp.status_code} {resp.text}")
            return None
        return resp.json().get("id")

    def _publish_container(self, container_id: str) -> str | None:
        resp = requests.post(
            f"{IG_GRAPH}/{self.ig_account_id}/media_publish",
            data={
                "creation_id": container_id,
                "access_token": self.access_token,
            },
            timeout=30,
        )
        if not resp.ok:
            print(f"  Instagram publish failed: {resp.status_code} {resp.text}")
            return None
        return resp.json().get("id")

    def _build_caption(self, item: ContentItem) -> str:
        parts = [item.caption]
        if item.tags:
            hashtags = " ".join(f"#{t.replace(' ', '')}" for t in item.tags[:30])
            parts.append(hashtags)
        return "\n\n".join(p for p in parts if p)
