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
    def __init__(self, credentials: dict = None, on_token_refresh=None,
                 on_page_token_refresh=None):
        _c = credentials or {}
        self.ig_account_id        = _c.get("INSTAGRAM_ACCOUNT_ID")      or os.environ.get("INSTAGRAM_ACCOUNT_ID", "")
        self.access_token         = _c.get("INSTAGRAM_ACCESS_TOKEN")     or os.environ.get("INSTAGRAM_ACCESS_TOKEN", "")
        self.page_id              = _c.get("FACEBOOK_PAGE_ID")           or os.environ.get("FACEBOOK_PAGE_ID", "")
        self.fb_page_token        = _c.get("FACEBOOK_PAGE_ACCESS_TOKEN") or os.environ.get("FACEBOOK_PAGE_ACCESS_TOKEN", "")
        self.fb_user_token        = _c.get("FACEBOOK_USER_TOKEN", "")
        self.on_token_refresh     = on_token_refresh
        self.on_page_token_refresh = on_page_token_refresh

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

    def _refresh_page_token(self) -> bool:
        """Regenerate FACEBOOK_PAGE_ACCESS_TOKEN using the stored long-lived user token."""
        if not self.fb_user_token or not self.page_id:
            return False
        try:
            r = requests.get(
                f"{FB_GRAPH}/me/accounts",
                params={"access_token": self.fb_user_token, "fields": "id,name,access_token"},
                timeout=15,
            )
            if not r.ok:
                print(f"  Instagram: page token refresh failed {r.status_code} {r.text[:200]}")
                return False
            for page in r.json().get("data", []):
                if page["id"] == self.page_id:
                    new_token = page["access_token"]
                    self.fb_page_token = new_token
                    if self.on_page_token_refresh:
                        self.on_page_token_refresh(new_token)
                    print(f"  Instagram: FB page token refreshed for page {self.page_id}")
                    return True
            print(f"  Instagram: page {self.page_id} not found in /me/accounts")
        except Exception as e:
            print(f"  Instagram: page token refresh exception: {e}")
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
        elif r.status_code == 400:
            try:
                err_code = r.json().get("error", {}).get("code")
            except Exception:
                err_code = None
            if err_code == 190:
                if self._refresh_page_token():
                    if "data" in kwargs and isinstance(kwargs["data"], dict):
                        d = kwargs["data"]
                        if "access_token" in d:
                            d["access_token"] = self.fb_page_token
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

    # ── Video (Reels) ─────────────────────────────────────────────────────────

    def _publish_reel(self, item: ContentItem) -> bool:
        caption    = self._build_caption(item)
        path       = item.media_path
        upload_tok = self.fb_page_token or self.access_token

        # Try binary resumable upload; fall back to URL-based if blocked.
        result = self._try_resumable_upload(path, caption, upload_tok)
        if result == "ok":
            item.metadata["instagram_reel_method"] = "resumable"
        elif result == "fallback":
            print("  Instagram: binary upload blocked, staging via URL...")
            video_url = self._stage_video_url(path)
            if not video_url:
                return False
            result = self._publish_reel_from_url(video_url, caption, upload_tok)
        if result not in ("ok", None):
            pass
        if result == "ok":
            item.posted_at = datetime.utcnow()
            return True
        return result is True  # shouldn't reach here

    def _try_resumable_upload(self, path: Path, caption: str, upload_tok: str) -> str:
        """Returns 'ok' if published via resumable, 'fallback' if binary upload is blocked."""
        file_size = path.stat().st_size
        resp = self._post(
            f"{FB_GRAPH}/{self.ig_account_id}/media",
            data={"media_type": "REELS", "upload_type": "resumable",
                  "caption": caption, "access_token": upload_tok},
            timeout=30,
        )
        if not resp.ok:
            return "fallback"
        data = resp.json()
        upload_url   = data.get("uri") or data.get("upload_url")
        container_id = data.get("id")
        if not upload_url or not container_id:
            return "fallback"
        try:
            with open(path, "rb") as f:
                ur = requests.post(
                    upload_url,
                    headers={"Authorization": f"OAuth {upload_tok}",
                             "offset": "0", "file_size": str(file_size)},
                    data=f, timeout=180,
                )
        except Exception:
            return "fallback"
        if not ur.ok:
            print(f"  Instagram: binary upload blocked ({ur.status_code}) — falling back to URL upload")
            return "fallback"
        # Binary upload succeeded — wait for processing and publish
        print("  Instagram: video uploaded, waiting for processing...")
        for _ in range(15):
            time.sleep(5)
            sr = requests.get(f"{FB_GRAPH}/{container_id}",
                              params={"fields": "status_code", "access_token": upload_tok},
                              timeout=15)
            sc = sr.json().get("status_code") if sr.ok else None
            if sc == "FINISHED":
                post_id = self._publish_container(container_id, upload_tok)
                if post_id:
                    print(f"  Instagram Reel posted: {post_id}")
                    return "ok"
                return "fallback"
            if sc == "ERROR":
                return "fallback"
        return "fallback"

    def _publish_reel_from_url(self, video_url: str, caption: str, upload_tok: str) -> str:
        """URL-based Reel publish. Returns 'ok' on success, None on failure."""
        resp = self._post(
            f"{FB_GRAPH}/{self.ig_account_id}/media",
            data={"media_type": "REELS", "video_url": video_url,
                  "caption": caption, "access_token": upload_tok},
            timeout=30,
        )
        if not resp.ok:
            print(f"  Instagram: URL container failed {resp.status_code} {resp.text[:300]}")
            return None
        container_id = resp.json().get("id")
        print("  Instagram: waiting for Reel processing (URL upload)...")
        for _ in range(20):
            time.sleep(8)
            sr = requests.get(f"{FB_GRAPH}/{container_id}",
                              params={"fields": "status_code,status", "access_token": upload_tok},
                              timeout=15)
            if sr.ok:
                sc = sr.json().get("status_code")
                if sc == "FINISHED":
                    break
                if sc == "ERROR":
                    print(f"  Instagram: processing error: {sr.json()}")
                    return None
                print(f"  Instagram: processing... ({sc})")
        else:
            print("  Instagram: processing timed out")
            return None
        post_id = self._publish_container(container_id, upload_tok)
        if post_id:
            print(f"  Instagram Reel posted: {post_id}")
            return "ok"
        return None

    def _stage_video_url(self, path: Path) -> "str | None":
        """Upload video to catbox.moe (24 h) and return public URL for Instagram."""
        try:
            with open(path, "rb") as f:
                r = requests.post(
                    "https://litterbox.catbox.moe/resources/internals/api.php",
                    data={"reqtype": "fileupload", "time": "24h"},
                    files={"fileToUpload": (path.name, f, "video/mp4")},
                    timeout=120,
                )
            if r.ok and r.text.startswith("http"):
                print(f"  Instagram: video staged at {r.text.strip()}")
                return r.text.strip()
            print(f"  Instagram: video staging failed {r.status_code} {r.text[:200]}")
        except Exception as e:
            print(f"  Instagram: video staging exception: {e}")
        return None

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
        for attempt in range(2):
            with open(media_path, "rb") as f:
                resp = requests.post(
                    f"{FB_GRAPH}/{self.page_id}/photos",
                    data={"published": "false", "access_token": self.fb_page_token},
                    files={"source": f},
                    timeout=30,
                )
            if resp.ok:
                break
            err_code = resp.json().get("error", {}).get("code") if resp.headers.get("content-type", "").startswith("application/json") else None
            if attempt == 0 and err_code == 190:
                print(f"  Instagram: staging token expired (190) — refreshing and retrying")
                if not self._refresh_page_token():
                    break
                continue
            print(f"  Instagram: image staging failed {resp.status_code} {resp.text[:300]}")
            return None
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

    def _publish_container(self, container_id: str, token: str = None) -> "str | None":
        tok = token or self.access_token
        resp = self._post(
            f"{FB_GRAPH}/{self.ig_account_id}/media_publish",
            data={"creation_id": container_id, "access_token": tok},
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
