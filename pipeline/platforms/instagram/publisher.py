import os
import time
import requests
from datetime import datetime
from pathlib import Path

from pipeline.core.base_publisher import BasePublisher
from pipeline.core.content_item import ContentItem

IG_GRAPH      = "https://graph.instagram.com/v21.0"
IG_REFRESH    = "https://graph.instagram.com/refresh_access_token"
FB_GRAPH      = "https://graph.facebook.com/v19.0"
MAX_CAPTION   = 2_200
VIDEO_EXTS    = {".mp4", ".mov", ".avi", ".mkv", ".webm"}


class InstagramPublisher(BasePublisher):
    def __init__(self, credentials: dict = None, on_token_refresh=None):
        _c = credentials or {}
        self.ig_account_id    = _c.get("INSTAGRAM_ACCOUNT_ID")      or os.environ.get("INSTAGRAM_ACCOUNT_ID", "")
        self.access_token     = _c.get("INSTAGRAM_ACCESS_TOKEN")     or os.environ.get("INSTAGRAM_ACCESS_TOKEN", "")
        self.page_id          = _c.get("FACEBOOK_PAGE_ID")           or os.environ.get("FACEBOOK_PAGE_ID", "")
        self.fb_page_token    = _c.get("FACEBOOK_PAGE_ACCESS_TOKEN") or os.environ.get("FACEBOOK_PAGE_ACCESS_TOKEN", "")
        self.on_token_refresh = on_token_refresh

    # ── Token refresh ────────────────────────────────────────────────────────

    def _refresh(self) -> bool:
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
        if item.media_path.suffix.lower() in VIDEO_EXTS:
            return self._publish_reel(item)
        return self._publish_image(item)

    # ── Video (Reels) — resumable upload ────────────────────────────────────

    def _publish_reel(self, item: ContentItem) -> bool:
        caption   = self._build_caption(item)
        path      = item.media_path
        file_size = path.stat().st_size

        # Step 1: initialise resumable upload session
        resp = self._post(
            f"{FB_GRAPH}/{self.ig_account_id}/media",
            data={
                "media_type":  "REELS",
                "upload_type": "resumable",
                "caption":     caption,
                "access_token": self.access_token,
            },
            timeout=30,
        )
        if not resp.ok:
            print(f"  Instagram: Reels init failed {resp.status_code} {resp.text[:300]}")
            return False

        data        = resp.json()
        upload_url  = data.get("upload_url") or data.get("uri")
        container_id = data.get("id")
        if not upload_url or not container_id:
            print(f"  Instagram: missing upload_url or container id: {data}")
            return False

        # Step 2: upload video bytes
        try:
            with open(path, "rb") as f:
                upload_resp = requests.post(
                    upload_url,
                    headers={
                        "Authorization":  f"OAuth {self.access_token}",
                        "offset":         "0",
                        "file_size":      str(file_size),
                    },
                    data=f,
                    timeout=180,
                )
        except Exception as e:
            print(f"  Instagram: video upload exception: {e}")
            return False

        if not upload_resp.ok:
            print(f"  Instagram: video upload failed {upload_resp.status_code} {upload_resp.text[:300]}")
            return False

        # Step 3: wait for processing
        print(f"  Instagram: video uploaded, waiting for processing...")
        for _ in range(15):
            time.sleep(5)
            status_resp = requests.get(
                f"{FB_GRAPH}/{container_id}",
                params={"fields": "status_code,status", "access_token": self.access_token},
                timeout=15,
            )
            if status_resp.ok:
                status_code = status_resp.json().get("status_code")
                if status_code == "FINISHED":
                    break
                if status_code == "ERROR":
                    print(f"  Instagram: video processing error: {status_resp.json()}")
                    return False
                print(f"  Instagram: processing... ({status_code})")
        else:
            print("  Instagram: video processing timed out")
            return False

        # Step 4: publish
        post_id = self._publish_container(container_id)
        if post_id:
            item.metadata["instagram_post_id"] = post_id
            item.posted_at = datetime.utcnow()
            print(f"  Instagram Reel posted: {post_id}")
            return True
        return False

    # ── Image ────────────────────────────────────────────────────────────────

    def _publish_image(self, item: ContentItem) -> bool:
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
