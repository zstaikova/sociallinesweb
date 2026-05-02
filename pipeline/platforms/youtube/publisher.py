import os
import requests
from datetime import datetime
from pathlib import Path

from pipeline.core.base_publisher import BasePublisher
from pipeline.core.content_item import ContentItem

OAUTH_REFRESH = "https://oauth2.googleapis.com/token"
CHANNELS_API  = "https://www.googleapis.com/youtube/v3/channels"
UPLOAD_API    = "https://www.googleapis.com/upload/youtube/v3/videos"

_VIDEO_TYPES = {
    ".mp4":  "video/mp4",
    ".mov":  "video/quicktime",
    ".avi":  "video/x-msvideo",
    ".mkv":  "video/x-matroska",
    ".webm": "video/webm",
}


class YouTubePublisher(BasePublisher):
    """
    Publishes videos to YouTube (optimised for Shorts) via YouTube Data API v3.
    Credentials: YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET,
                 YOUTUBE_ACCESS_TOKEN, YOUTUBE_REFRESH_TOKEN
    Set up at: https://console.cloud.google.com → APIs & Services → Credentials
    Enable: YouTube Data API v3
    OAuth type: Desktop app
    """

    def __init__(self, credentials: dict = None, on_token_refresh=None):
        _c = credentials or {}
        self.client_id     = _c.get("YOUTUBE_CLIENT_ID")     or os.environ.get("YOUTUBE_CLIENT_ID", "")
        self.client_secret = _c.get("YOUTUBE_CLIENT_SECRET") or os.environ.get("YOUTUBE_CLIENT_SECRET", "")
        self.access_token  = _c.get("YOUTUBE_ACCESS_TOKEN")  or os.environ.get("YOUTUBE_ACCESS_TOKEN", "")
        self.refresh_token = _c.get("YOUTUBE_REFRESH_TOKEN") or os.environ.get("YOUTUBE_REFRESH_TOKEN", "")
        self.on_token_refresh = on_token_refresh

    # ── Auth ────────────────────────────────────────────────────────────────

    def _refresh(self):
        resp = requests.post(OAUTH_REFRESH, data={
            "grant_type":    "refresh_token",
            "refresh_token": self.refresh_token,
            "client_id":     self.client_id,
            "client_secret": self.client_secret,
        }, timeout=15)
        resp.raise_for_status()
        self.access_token = resp.json()["access_token"]
        if self.on_token_refresh:
            self.on_token_refresh(self.access_token, self.refresh_token)

    def _auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {self.access_token}"}

    def _get(self, url, **kwargs):
        r = requests.get(url, headers=self._auth_headers(), **kwargs)
        if r.status_code == 401:
            self._refresh()
            r = requests.get(url, headers=self._auth_headers(), **kwargs)
        return r

    def _post(self, url, extra_headers: dict = None, **kwargs):
        headers = {**self._auth_headers(), **(extra_headers or {})}
        r = requests.post(url, headers=headers, **kwargs)
        if r.status_code == 401:
            self._refresh()
            headers["Authorization"] = f"Bearer {self.access_token}"
            r = requests.post(url, headers=headers, **kwargs)
        return r

    # ── Public interface ─────────────────────────────────────────────────────

    def get_account_info(self) -> "dict | None":
        r = self._get(CHANNELS_API, params={"part": "snippet", "mine": "true"}, timeout=10)
        if r.ok:
            items = r.json().get("items", [])
            if items:
                return {"name": items[0]["snippet"]["title"], "id": items[0]["id"]}
        return None

    def verify_auth(self) -> bool:
        info = self.get_account_info()
        if info:
            print(f"YouTube auth OK — {info['name']}")
            return True
        print("YouTube auth failed")
        return False

    def publish(self, item: ContentItem) -> bool:
        if not item.media_path or not item.media_path.exists():
            print("  YouTube: no media — skipping")
            return False

        content_type = _VIDEO_TYPES.get(item.media_path.suffix.lower())
        if not content_type:
            print(f"  YouTube: unsupported format {item.media_path.suffix} — skipping")
            return False

        try:
            return self._upload(item, content_type)
        except Exception as e:
            print(f"  YouTube publish exception: {e}")
            return False

    # ── Upload ───────────────────────────────────────────────────────────────

    def _upload(self, item: ContentItem, content_type: str) -> bool:
        # Title: first non-empty line of caption, capped at 92 chars, + #Shorts
        lines = (item.caption or "").strip().split("\n")
        first = next((l.strip() for l in lines if l.strip()), "")
        title = (first[:92] + " #Shorts") if first else "#Shorts"

        description   = (item.caption or "")[:5000]
        privacy       = item.metadata.get("youtube_privacy", "public")
        made_for_kids = bool(item.metadata.get("youtube_made_for_kids", False))
        file_size     = item.media_path.stat().st_size

        body = {
            "snippet": {
                "title":      title,
                "description": description,
                "categoryId": "23",   # Comedy
            },
            "status": {
                "privacyStatus":            privacy,
                "selfDeclaredMadeForKids":  made_for_kids,
            },
        }

        # Step 1 — initiate resumable upload
        init = self._post(
            f"{UPLOAD_API}?uploadType=resumable&part=snippet,status",
            extra_headers={
                "Content-Type":             "application/json",
                "X-Upload-Content-Type":    content_type,
                "X-Upload-Content-Length":  str(file_size),
            },
            json=body,
            timeout=30,
        )

        if not init.ok:
            print(f"  YouTube init failed: {init.status_code} {init.text[:300]}")
            return False

        upload_url = init.headers.get("Location")
        if not upload_url:
            print("  YouTube: no upload URL in response")
            return False

        # Step 2 — upload the file
        with open(item.media_path, "rb") as f:
            video_bytes = f.read()

        up = requests.put(
            upload_url,
            headers={"Content-Type": content_type, "Content-Length": str(file_size)},
            data=video_bytes,
            timeout=300,
        )

        if not up.ok:
            print(f"  YouTube upload failed: {up.status_code} {up.text[:300]}")
            return False

        video_id = up.json().get("id")
        item.metadata["youtube_video_id"] = video_id
        item.posted_at = datetime.utcnow()
        print(f"  YouTube: https://youtube.com/watch?v={video_id}")
        return True
