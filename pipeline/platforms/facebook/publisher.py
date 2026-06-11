import io
import os
import time
import requests
from datetime import datetime
from pathlib import Path
from PIL import Image

from pipeline.core.base_publisher import BasePublisher
from pipeline.core.content_item import ContentItem

GRAPH_URL    = "https://graph.facebook.com/v19.0"
MAX_BYTES    = 4 * 1024 * 1024   # Facebook /photos multipart limit
MAX_CAPTION  = 2_000
VIDEO_EXTS   = {".mp4", ".mov", ".avi", ".mkv", ".webm"}


def _compress_image(path: Path) -> tuple[bytes, str]:
    img = Image.open(path).convert("RGB")
    for quality in (85, 70, 55, 40):
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        data = buf.getvalue()
        if len(data) <= MAX_BYTES:
            return data, "image/jpeg"
    w, h = img.size
    img = img.resize((w // 2, h // 2), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=55, optimize=True)
    return buf.getvalue(), "image/jpeg"


class FacebookPublisher(BasePublisher):
    def __init__(self, credentials: dict = None):
        _c = credentials or {}
        self.page_id      = _c.get("FACEBOOK_PAGE_ID")           or os.environ.get("FACEBOOK_PAGE_ID", "")
        self.access_token = _c.get("FACEBOOK_PAGE_ACCESS_TOKEN") or os.environ.get("FACEBOOK_PAGE_ACCESS_TOKEN", "")

    def get_account_info(self) -> "dict | None":
        resp = requests.get(
            f"{GRAPH_URL}/{self.page_id}",
            params={"fields": "name,id", "access_token": self.access_token},
            timeout=10,
        )
        if resp.ok:
            data = resp.json()
            return {"name": data.get("name", ""), "id": str(data.get("id", ""))}
        try:
            err = resp.json().get("error", {})
            msg = err.get("message", "") or err.get("type", "")
            if msg:
                raise RuntimeError(f"Facebook API error: {msg}")
        except RuntimeError:
            raise
        except Exception:
            pass
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
        if item.media_path.suffix.lower() in VIDEO_EXTS:
            return self._publish_video(item)
        return self._publish_image(item)

    def _publish_video(self, item: ContentItem) -> bool:
        caption = self._build_caption(item)
        path    = item.media_path
        for attempt in range(2):
            try:
                with open(path, "rb") as f:
                    resp = requests.post(
                        f"{GRAPH_URL}/{self.page_id}/videos",
                        data={"description": caption, "access_token": self.access_token},
                        files={"source": (path.name, f, "video/mp4")},
                        timeout=180,
                    )
                if resp.ok:
                    post_id = resp.json().get("id")
                    item.metadata["facebook_post_id"] = post_id
                    item.posted_at = datetime.utcnow()
                    print(f"  Facebook video posted: {post_id}")
                    return True
                err = resp.json().get("error", {}) if "application/json" in resp.headers.get("content-type", "") else {}
                if err.get("code") in (1, 2) and attempt == 0:
                    print(f"  Facebook: transient error — retrying in 5s")
                    time.sleep(5)
                    continue
                print(f"  Facebook video error: {resp.status_code} {resp.text[:300]}")
                return False
            except Exception as e:
                print(f"  Facebook video exception: {e}")
                if attempt == 0:
                    time.sleep(3)
                    continue
                return False
        return False

    def _publish_image(self, item: ContentItem) -> bool:
        caption = self._build_caption(item)
        path    = item.media_path
        size    = path.stat().st_size
        for attempt in range(2):
            try:
                if size > MAX_BYTES:
                    print(f"  Facebook: image {size // 1024}KB > 4MB limit — compressing")
                    img_bytes, mime = _compress_image(path)
                    files = {"source": (path.name, img_bytes, mime)}
                else:
                    files = {"source": open(path, "rb")}

                resp = requests.post(
                    f"{GRAPH_URL}/{self.page_id}/photos",
                    data={"caption": caption, "access_token": self.access_token},
                    files=files,
                    timeout=60,
                )
                if hasattr(files["source"], "close"):
                    files["source"].close()

                if resp.ok:
                    data   = resp.json()
                    post_id = data.get("post_id") or data.get("id")
                    item.metadata["facebook_post_id"] = post_id
                    item.posted_at = datetime.utcnow()
                    return True

                err = resp.json().get("error", {}) if "application/json" in resp.headers.get("content-type", "") else {}
                if err.get("code") in (1, 2) and attempt == 0:
                    print(f"  Facebook: transient error (code {err.get('code')}) — retrying in 5s")
                    time.sleep(5)
                    continue
                print(f"  Facebook API error: {resp.status_code} {resp.text}")
                return False
            except Exception as e:
                print(f"  Facebook publish exception: {e}")
                if attempt == 0:
                    time.sleep(3)
                    continue
                return False
        return False

    def _build_caption(self, item: ContentItem) -> str:
        parts = [item.caption]
        if item.tags:
            hashtags = " ".join(f"#{t.replace(' ', '')}" for t in item.tags[:10])
            parts.append(hashtags)
        full = "\n\n".join(p for p in parts if p)
        if len(full) > MAX_CAPTION:
            full = full[:MAX_CAPTION - 1] + "…"
        return full
