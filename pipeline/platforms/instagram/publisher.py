import os
import requests
from datetime import datetime
from pathlib import Path

from pipeline.core.base_publisher import BasePublisher
from pipeline.core.content_item import ContentItem

IG_GRAPH      = "https://graph.instagram.com/v21.0"
IG_REFRESH    = "https://graph.instagram.com/refresh_access_token"
FB_GRAPH      = "https://graph.facebook.com/v19.0"
MAX_CAPTION   = 2_200


class InstagramPublisher(BasePublisher):
    def __init__(self, credentials: dict = None, on_token_refresh=None):
        _c = credentials or {}
        self.ig_account_id   = _c.get("INSTAGRAM_ACCOUNT_ID")      or os.environ.get("INSTAGRAM_ACCOUNT_ID", "")
        self.access_token    = _c.get("INSTAGRAM_ACCESS_TOKEN")     or os.environ.get("INSTAGRAM_ACCESS_TOKEN", "")
        self.page_id         = _c.get("FACEBOOK_PAGE_ID")           or os.environ.get("FACEBOOK_PAGE_ID", "")
        self.fb_page_token   = _c.get("FACEBOOK_PAGE_ACCESS_TOKEN") or os.environ.get("FACEBOOK_PAGE_ACCESS_TOKEN", "")
        self.on_token_refresh = on_token_refresh

    # ── Token refresh ────────────────────────────────────────────────────────

    def _refresh(self) -> bool:
        """Extend Instagram long-lived token for another 60 days."""
        try:
            r = requests.get(IG_REFRESH, params={
                "grant_type":   "ig_refreshtoken",
                "access_token": self.access_token,
            }, timeout=15)
            if r.ok:
                new_token = r.json().get("access_token", self.access_token)
                self.access_token = new_token
                if self.on_token_refresh:
                    self.on_token_refresh(new_token)
                print("  Instagram: access token refreshed")
                return True
            print(f"  Instagram: token refresh failed {r.status_code} {r.text[:200]}")
        except Exception as e:
            print(f"  Instagram: token refresh exception: {e}")
        return False

    def _get(self, url, **kwargs):
        r = requests.get(url, **kwargs)
        if r.status_code == 401:
            if self._refresh():
                if "params" in kwargs:
                    kwargs["params"]["access_token"] = self.access_token
                r = requests.get(url, **kwargs)
        return r

    def _post(self, url, **kwargs):
        r = requests.post(url, **kwargs)
        if r.status_code == 401:
            if self._refresh():
                if "data" in kwargs and isinstance(kwargs["data"], dict):
                    kwargs["data"]["access_token"] = self.access_token
                r = requests.post(url, **kwargs)
        return r

    # ── Public interface ─────────────────────────────────────────────────────

    def get_account_info(self) -> "dict | None":
        r = self._get(
            f"{FB_GRAPH}/{self.ig_account_id}",
            params={"fields": "id,username,name", "access_token": self.access_token},
            timeout=10,
        )
        if r.ok:
            data = r.json()
            name = data.get("username") or data.get("name", "")
            return {"name": f"@{name}", "id": str(data.get("id", ""))}
        print(f"  Instagram get_account_info failed: {r.status_code} {r.text[:200]}")
        return None

    def verify_auth(self) -> bool:
        info = self.get_account_info()
        if info:
            print(f"Instagram auth OK — {info['name']} ({info['id']})")
            return True
        print("Instagram auth failed")
        return False

    def publish(self, item: ContentItem) -> bool:
        if not item.media_path or not item.media_path.exists():
            print("  Instagram: no media file")
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

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _stage_image(self, media_path: Path) -> "str | None":
        with open(media_path, "rb") as f:
            resp = self._post(
                f"{FB_GRAPH}/{self.page_id}/photos",
                data={"published": "false", "access_token": self.fb_page_token},
                files={"source": f},
                timeout=30,
            )
        if not resp.ok:
            print(f"  Instagram: image staging failed {resp.status_code} {resp.text[:300]}")
            return None

        photo_id = resp.json().get("id")
        if not photo_id:
            print("  Instagram: no photo ID from staging")
            return None

        info = requests.get(
            f"{FB_GRAPH}/{photo_id}",
            params={"fields": "images", "access_token": self.fb_page_token},
            timeout=10,
        )
        if not info.ok:
            print(f"  Instagram: staged image URL fetch failed: {info.text[:200]}")
            return None

        images = info.json().get("images", [])
        if not images:
            print("  Instagram: no images in staging response")
            return None
        return max(images, key=lambda x: x.get("width", 0))["source"]

    def _create_container(self, image_url: str, caption: str) -> "str | None":
        resp = self._post(
            f"{FB_GRAPH}/{self.ig_account_id}/media",
            data={"image_url": image_url, "caption": caption,
                  "access_token": self.access_token},
            timeout=30,
        )
        if not resp.ok:
            print(f"  Instagram container failed {resp.status_code} {resp.text[:300]}")
            return None
        return resp.json().get("id")

    def _publish_container(self, container_id: str) -> "str | None":
        resp = self._post(
            f"{FB_GRAPH}/{self.ig_account_id}/media_publish",
            data={"creation_id": container_id, "access_token": self.access_token},
            timeout=30,
        )
        if not resp.ok:
            print(f"  Instagram publish failed {resp.status_code} {resp.text[:300]}")
            return None
        return resp.json().get("id")

    def _build_caption(self, item: ContentItem) -> str:
        parts = [item.caption]
        if item.tags:
            parts.append(" ".join(f"#{t.replace(' ', '')}" for t in item.tags[:30]))
        full = "\n\n".join(p for p in parts if p)
        if len(full) > MAX_CAPTION:
            full = full[:MAX_CAPTION - 1] + "…"
        return full
