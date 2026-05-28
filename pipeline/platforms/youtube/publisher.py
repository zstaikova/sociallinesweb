import os
import requests
from datetime import datetime
from pathlib import Path

from pipeline.core.base_publisher import BasePublisher
from pipeline.core.content_item import ContentItem

OAUTH_REFRESH  = "https://oauth2.googleapis.com/token"
CHANNELS_API   = "https://www.googleapis.com/youtube/v3/channels"
UPLOAD_API     = "https://www.googleapis.com/upload/youtube/v3/videos"
CHUNK_SIZE     = 8 * 1024 * 1024   # 8 MB streaming chunks

_VIDEO_TYPES = {
    ".mp4":  "video/mp4",
    ".mov":  "video/quicktime",
    ".avi":  "video/x-msvideo",
    ".mkv":  "video/x-matroska",
    ".webm": "video/webm",
}

_CATEGORY_IDS = {
    "comedy":      "23",
    "education":   "27",
    "entertainment": "24",
    "gaming":      "20",
    "howto":       "26",
    "music":       "10",
    "news":        "25",
    "people":      "22",
    "science":     "28",
    "sports":      "17",
    "tech":        "28",
    "travel":      "19",
}


class YouTubePublisher(BasePublisher):
    def __init__(self, credentials: dict = None, on_token_refresh=None):
        _c = credentials or {}
        self.client_id        = _c.get("YOUTUBE_CLIENT_ID")     or os.environ.get("YOUTUBE_CLIENT_ID", "")
        self.client_secret    = _c.get("YOUTUBE_CLIENT_SECRET") or os.environ.get("YOUTUBE_CLIENT_SECRET", "")
        self.access_token     = _c.get("YOUTUBE_ACCESS_TOKEN")  or os.environ.get("YOUTUBE_ACCESS_TOKEN", "")
        self.refresh_token    = _c.get("YOUTUBE_REFRESH_TOKEN") or os.environ.get("YOUTUBE_REFRESH_TOKEN", "")
        self.on_token_refresh = on_token_refresh

    # ── Auth ─────────────────────────────────────────────────────────────────

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
            print(f"  YouTube: no channel found: {r.json()}")
            return None
        print(f"  YouTube get_account_info failed: {r.status_code} {r.text[:300]}")
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
        lines     = (item.caption or "").strip().split("\n")
        first     = next((l.strip() for l in lines if l.strip()), "")
        title     = (first[:92] + " #Shorts") if first else "#Shorts"
        description = (item.caption or "")[:5000]
        privacy   = item.metadata.get("youtube_privacy", "public")
        made_for_kids = bool(item.metadata.get("youtube_made_for_kids", False))
        file_size = item.media_path.stat().st_size

        # Category: from metadata, or map from content tag, or default Education
        cat_key   = str(item.metadata.get("youtube_category", "")).lower()
        category  = _CATEGORY_IDS.get(cat_key) or item.metadata.get("youtube_category_id", "27")

        body = {
            "snippet": {"title": title, "description": description, "categoryId": category},
            "status":  {"privacyStatus": privacy, "selfDeclaredMadeForKids": made_for_kids},
        }

        init = self._post(
            f"{UPLOAD_API}?uploadType=resumable&part=snippet,status",
            extra_headers={
                "Content-Type":            "application/json",
                "X-Upload-Content-Type":   content_type,
                "X-Upload-Content-Length": str(file_size),
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

        # Stream upload in chunks (avoid loading full file into memory)
        video_id = self._stream_upload(upload_url, item.media_path, content_type, file_size)
        if not video_id:
            return False

        item.metadata["youtube_post_id"] = video_id
        item.posted_at = datetime.utcnow()
        print(f"  YouTube: https://youtube.com/watch?v={video_id}")
        return True

    def _stream_upload(self, upload_url: str, media_path: Path,
                       content_type: str, file_size: int) -> "str | None":
        offset = 0
        with open(media_path, "rb") as f:
            while offset < file_size:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                end = offset + len(chunk) - 1
                r = requests.put(
                    upload_url,
                    headers={
                        "Content-Type":   content_type,
                        "Content-Length": str(len(chunk)),
                        "Content-Range":  f"bytes {offset}-{end}/{file_size}",
                    },
                    data=chunk,
                    timeout=120,
                )
                if r.status_code in (200, 201):
                    return r.json().get("id")
                if r.status_code == 308:
                    range_hdr = r.headers.get("Range", "")
                    if range_hdr:
                        offset = int(range_hdr.split("-")[1]) + 1
                    else:
                        offset += len(chunk)
                else:
                    print(f"  YouTube upload chunk failed: {r.status_code} {r.text[:300]}")
                    return None
        return None
