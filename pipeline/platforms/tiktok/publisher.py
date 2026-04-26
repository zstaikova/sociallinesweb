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


class TikTokPublisher(BasePublisher):
    def __init__(self):
        self.client_key    = os.environ["TIKTOK_CLIENT_KEY"]
        self.client_secret = os.environ["TIKTOK_CLIENT_SECRET"]
        self.access_token  = os.environ["TIKTOK_ACCESS_TOKEN"]
        self.open_id       = os.environ["TIKTOK_OPEN_ID"]

    def _headers(self):
        return {"Authorization": f"Bearer {self.access_token}"}

    def verify_auth(self) -> bool:
        resp = requests.get(
            f"{API_BASE}/user/info/",
            params={"fields": "display_name,avatar_url"},
            headers=self._headers(),
            timeout=10,
        )
        if resp.ok:
            user = resp.json().get("data", {}).get("user", {})
            print(f"TikTok auth OK — account: {user.get('display_name')} ({self.open_id})")
            return True
        print(f"TikTok auth failed: {resp.text}")
        return False

    def publish(self, item: ContentItem) -> bool:
        if not item.media_path or not item.media_path.exists():
            print("  No media file to publish")
            return False

        try:
            caption = self._build_caption(item)
            publish_id = self._init_upload(item.media_path, caption)
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

    def _init_upload(self, media_path: Path, caption: str) -> str | None:
        file_size = media_path.stat().st_size
        chunk_size = min(CHUNK_SIZE, file_size)
        chunk_count = max(1, -(-file_size // chunk_size))  # ceiling division

        resp = requests.post(
            f"{API_BASE}/post/publish/video/init/",
            headers={**self._headers(), "Content-Type": "application/json; charset=UTF-8"},
            json={
                "post_info": {
                    "title": caption,
                    "privacy_level": "SELF_ONLY",  # sandbox safe — change to PUBLIC_TO_EVERYONE after approval
                    "disable_duet": False,
                    "disable_comment": False,
                    "disable_stitch": False,
                },
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
            resp = requests.post(
                f"{API_BASE}/post/publish/status/fetch/",
                headers={**self._headers(), "Content-Type": "application/json; charset=UTF-8"},
                json={"publish_id": publish_id},
                timeout=15,
            )
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
