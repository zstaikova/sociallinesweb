import os
import time
import requests
from pathlib import Path
from datetime import datetime

from pipeline.core.base_publisher import BasePublisher
from pipeline.core.content_item import ContentItem

API_BASE = "https://open.tiktokapis.com/v2"

# Max chunk size for video upload: 10MB
CHUNK_SIZE = 10 * 1024 * 1024


TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"


class TikTokPublisher(BasePublisher):
    def __init__(self, credentials: dict = None, on_token_refresh=None):
        _c = credentials or {}
        self.client_key    = _c.get("TIKTOK_CLIENT_KEY")    or os.environ["TIKTOK_CLIENT_KEY"]
        self.client_secret = _c.get("TIKTOK_CLIENT_SECRET") or os.environ["TIKTOK_CLIENT_SECRET"]
        self.access_token  = _c.get("TIKTOK_ACCESS_TOKEN")  or os.environ["TIKTOK_ACCESS_TOKEN"]
        self.open_id       = _c.get("TIKTOK_OPEN_ID")       or os.environ["TIKTOK_OPEN_ID"]
        self.refresh_token = _c.get("TIKTOK_REFRESH_TOKEN") or os.environ.get("TIKTOK_REFRESH_TOKEN", "")
        self._on_token_refresh = on_token_refresh

    def _headers(self):
        return {"Authorization": f"Bearer {self.access_token}"}

    def _is_auth_error(self, resp: requests.Response) -> bool:
        if resp.status_code == 401:
            return True
        try:
            code = resp.json().get("error", {}).get("code", "")
            return code in ("access_token_invalid", "access_token_expired")
        except Exception:
            return False

    def _refresh_access_token(self) -> bool:
        """Exchange refresh token for new tokens, update self, and persist via callback."""
        if not self.refresh_token:
            print("  TikTok: no refresh token stored — re-authentication required")
            return False
        try:
            resp = requests.post(
                TOKEN_URL,
                data={
                    "client_key":     self.client_key,
                    "client_secret":  self.client_secret,
                    "grant_type":     "refresh_token",
                    "refresh_token":  self.refresh_token,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=15,
            )
            data = resp.json()
            if not resp.ok or "error" in data:
                print(f"  TikTok token refresh failed: {data}")
                return False
            self.access_token  = data["access_token"]
            self.refresh_token = data.get("refresh_token", self.refresh_token)
            print("  TikTok: access token refreshed silently")
            if self._on_token_refresh:
                self._on_token_refresh(self.access_token, self.refresh_token)
            return True
        except Exception as e:
            print(f"  TikTok token refresh exception: {e}")
            return False

    def _call(self, method: str, url: str, *, json: dict = None,
              params: dict = None, timeout: int = 15) -> requests.Response:
        """Unified API call with automatic silent token refresh on auth errors."""
        def _do():
            headers = {**self._headers(), "Content-Type": "application/json; charset=UTF-8"}
            return getattr(requests, method)(
                url, headers=headers, json=json, params=params, timeout=timeout
            )
        resp = _do()
        if self._is_auth_error(resp) and self._refresh_access_token():
            resp = _do()
        return resp

    def get_account_info(self) -> "dict | None":
        resp = self._call("get", f"{API_BASE}/user/info/",
                          params={"fields": "display_name,avatar_url"}, timeout=10)
        if resp.ok:
            user = resp.json().get("data", {}).get("user", {})
            return {"name": user.get("display_name", ""), "id": self.open_id}
        return None

    def verify_auth(self) -> bool:
        info = self.get_account_info()
        if info:
            print(f"TikTok auth OK — account: {info['name']} ({info['id']})")
            return True
        print("TikTok auth failed")
        return False

    def get_creator_info(self) -> "dict | None":
        """Fetch creator info required before rendering the post form."""
        resp = self._call("post", f"{API_BASE}/post/publish/creator_info/query/", json={})
        if resp.ok:
            return resp.json().get("data", {})
        return None

    def poll_status(self, publish_id: str) -> str:
        """Single status check — returns status string."""
        resp = self._call("post", f"{API_BASE}/post/publish/status/fetch/",
                          json={"publish_id": publish_id})
        if resp.ok:
            return resp.json().get("data", {}).get("status", "UNKNOWN")
        return "ERROR"

    def publish(self, item: ContentItem) -> bool:
        if not item.media_path or not item.media_path.exists():
            print("  No media file to publish")
            return False

        try:
            caption = self._build_caption(item)
            publish_id = self._init_upload(item.media_path, caption, item.metadata)
            if not publish_id:
                return False

            # Poll for completion
            status = self._poll_status(publish_id)
            if status == "PUBLISH_COMPLETE":
                item.metadata["tiktok_publish_id"] = publish_id
                item.posted_at = datetime.utcnow()
                print(f"  TikTok posted. publish_id={publish_id}")
                return True
            else:
                print(f"  TikTok publish ended with status: {status}")
                return False

        except Exception as e:
            print(f"  TikTok publish exception: {e}")
            return False

    def _init_upload(self, media_path: Path, caption: str, meta: dict = None) -> str | None:
        meta = meta or {}
        privacy_level   = meta.get("tiktok_privacy_level", "PUBLIC_TO_EVERYONE")
        disable_comment = not meta.get("tiktok_allow_comment", False)
        disable_duet    = not meta.get("tiktok_allow_duet",    False)
        disable_stitch  = not meta.get("tiktok_allow_stitch",  False)

        post_info = {
            "title":         caption,
            "privacy_level": privacy_level,
            "disable_duet":    disable_duet,
            "disable_comment": disable_comment,
            "disable_stitch":  disable_stitch,
        }

        if meta.get("tiktok_brand_organic") or meta.get("tiktok_branded_content"):
            if meta.get("tiktok_branded_content"):
                post_info["brand_content_toggle"]  = True
                post_info["brand_organic_toggle"]   = bool(meta.get("tiktok_brand_organic"))
            else:
                post_info["brand_organic_toggle"]   = True

        file_size  = media_path.stat().st_size
        chunk_size = min(CHUNK_SIZE, file_size)
        chunk_count = max(1, -(-file_size // chunk_size))

        resp = self._call(
            "post", f"{API_BASE}/post/publish/video/init/",
            json={
                "post_info": post_info,
                "source_info": {
                    "source": "FILE_UPLOAD",
                    "video_size": file_size,
                    "chunk_size": chunk_size,
                    "total_chunk_count": chunk_count,
                },
            },
            timeout=30,
        )

        if not resp.ok:
            print(f"  TikTok init failed: {resp.status_code} {resp.text}")
            return None

        data = resp.json().get("data", {})
        publish_id = data.get("publish_id")
        upload_url = data.get("upload_url")

        if not publish_id or not upload_url:
            print(f"  TikTok init: missing publish_id or upload_url: {resp.text}")
            return None

        # Upload video in chunks
        if not self._upload_chunks(media_path, upload_url, file_size, chunk_size, chunk_count):
            return None

        return publish_id

    def _upload_chunks(self, media_path: Path, upload_url: str, file_size: int, chunk_size: int, chunk_count: int) -> bool:
        with open(media_path, "rb") as f:
            for chunk_index in range(chunk_count):
                start = chunk_index * chunk_size
                end = min(start + chunk_size, file_size) - 1
                chunk = f.read(chunk_size)

                resp = requests.put(
                    upload_url,
                    headers={
                        "Content-Type": "video/mp4",
                        "Content-Range": f"bytes {start}-{end}/{file_size}",
                        "Content-Length": str(len(chunk)),
                    },
                    data=chunk,
                    timeout=120,
                )

                if resp.status_code not in (200, 201, 206):
                    print(f"  TikTok chunk {chunk_index} upload failed: {resp.status_code} {resp.text}")
                    return False

        return True

    def _poll_status(self, publish_id: str, max_attempts: int = 20) -> str:
        for _ in range(max_attempts):
            resp = self._call("post", f"{API_BASE}/post/publish/status/fetch/",
                              json={"publish_id": publish_id})
            if resp.ok:
                status = resp.json().get("data", {}).get("status", "")
                if status in ("PUBLISH_COMPLETE", "FAILED"):
                    return status
            time.sleep(3)
        return "TIMEOUT"

    def _build_caption(self, item: ContentItem) -> str:
        parts = [item.caption]
        if item.tags:
            hashtags = " ".join(f"#{t.replace(' ', '')}" for t in item.tags[:5])
            parts.append(hashtags)
        caption = " ".join(p for p in parts if p)
        return caption[:2200]  # TikTok caption limit
