import os
import requests
from datetime import datetime
from pathlib import Path

from pipeline.core.base_publisher import BasePublisher
from pipeline.core.content_item import ContentItem

THREADS_GRAPH   = "https://graph.threads.net/v1.0"
THREADS_REFRESH = "https://graph.threads.net/refresh_access_token"
FB_GRAPH        = "https://graph.facebook.com/v19.0"
MAX_CAPTION     = 500


class ThreadsPublisher(BasePublisher):
    def __init__(self, credentials: dict = None, on_token_refresh=None):
        _c = credentials or {}
        self.user_id          = _c.get("THREADS_USER_ID")             or os.environ["THREADS_USER_ID"]
        self.access_token     = _c.get("THREADS_ACCESS_TOKEN")        or os.environ["THREADS_ACCESS_TOKEN"]
        self.page_id          = _c.get("FACEBOOK_PAGE_ID")            or os.environ.get("FACEBOOK_PAGE_ID", "")
        self.fb_page_token    = _c.get("FACEBOOK_PAGE_ACCESS_TOKEN")  or os.environ.get("FACEBOOK_PAGE_ACCESS_TOKEN", "")
        self.on_token_refresh = on_token_refresh

    # ── Token refresh ────────────────────────────────────────────────────────

    def _refresh(self) -> bool:
        try:
            r = requests.get(THREADS_REFRESH, params={
                "grant_type":   "th_refresh_token",
                "access_token": self.access_token,
            }, timeout=15)
            if r.ok:
                new_token = r.json().get("access_token", self.access_token)
                self.access_token = new_token
                if self.on_token_refresh:
                    self.on_token_refresh(new_token)
                print("  Threads: access token refreshed")
                return True
            print(f"  Threads: token refresh failed {r.status_code} {r.text[:200]}")
        except Exception as e:
            print(f"  Threads: token refresh exception: {e}")
        return False

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
        r = requests.get(
            f"{THREADS_GRAPH}/{self.user_id}",
            params={"fields": "id,username,name", "access_token": self.access_token},
            timeout=10,
        )
        if r.ok:
            data = r.json()
            name = data.get("username") or data.get("name", "")
            return {"name": f"@{name}", "id": str(data.get("id", ""))}
        return None

    def verify_auth(self) -> bool:
        info = self.get_account_info()
        if info:
            print(f"Threads auth OK — {info['name']} ({info['id']})")
            return True
        print("Threads auth failed")
        return False

    def publish(self, item: ContentItem) -> bool:
        try:
            text = self._build_caption(item)

            if item.media_path and item.media_path.exists():
                image_url = self._stage_image(item.media_path)
                if not image_url:
                    return False
                container_id = self._create_image_container(image_url, text)
            else:
                container_id = self._create_text_container(text)

            if not container_id:
                return False

            post_id = self._publish_container(container_id)
            if not post_id:
                return False

            item.metadata["threads_post_id"] = post_id
            item.posted_at = datetime.utcnow()
            return True

        except Exception as e:
            print(f"  Threads publish exception: {e}")
            return False

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _stage_image(self, media_path: Path) -> "str | None":
        if not self.page_id or not self.fb_page_token:
            print("  Threads: no Facebook credentials for image staging")
            return None
        with open(media_path, "rb") as f:
            resp = requests.post(
                f"{FB_GRAPH}/{self.page_id}/photos",
                data={"published": "false", "access_token": self.fb_page_token},
                files={"source": f},
                timeout=30,
            )
        if not resp.ok:
            print(f"  Threads: image staging failed {resp.status_code} {resp.text[:300]}")
            return None
        photo_id = resp.json().get("id")
        if not photo_id:
            return None
        info = requests.get(
            f"{FB_GRAPH}/{photo_id}",
            params={"fields": "images", "access_token": self.fb_page_token},
            timeout=10,
        )
        if not info.ok:
            return None
        images = info.json().get("images", [])
        if not images:
            return None
        return max(images, key=lambda x: x.get("width", 0))["source"]

    def _create_image_container(self, image_url: str, text: str) -> "str | None":
        resp = self._post(
            f"{THREADS_GRAPH}/{self.user_id}/threads",
            data={"media_type": "IMAGE", "image_url": image_url,
                  "text": text, "access_token": self.access_token},
            timeout=30,
        )
        if not resp.ok:
            print(f"  Threads container failed {resp.status_code} {resp.text[:300]}")
            return None
        return resp.json().get("id")

    def _create_text_container(self, text: str) -> "str | None":
        resp = self._post(
            f"{THREADS_GRAPH}/{self.user_id}/threads",
            data={"media_type": "TEXT", "text": text, "access_token": self.access_token},
            timeout=30,
        )
        if not resp.ok:
            print(f"  Threads text container failed {resp.status_code} {resp.text[:300]}")
            return None
        return resp.json().get("id")

    def _publish_container(self, container_id: str) -> "str | None":
        resp = self._post(
            f"{THREADS_GRAPH}/{self.user_id}/threads_publish",
            data={"creation_id": container_id, "access_token": self.access_token},
            timeout=30,
        )
        if not resp.ok:
            print(f"  Threads publish failed {resp.status_code} {resp.text[:300]}")
            return None
        return resp.json().get("id")

    def _build_caption(self, item: ContentItem) -> str:
        text = item.caption or ""
        if len(text) > MAX_CAPTION:
            text = text[:MAX_CAPTION - 1] + "…"
        return text
