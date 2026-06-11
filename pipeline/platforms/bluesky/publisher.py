import io
import os
from datetime import datetime, timezone
from pathlib import Path

from pipeline.core.base_publisher import BasePublisher
from pipeline.core.content_item import ContentItem

IMAGE_MIMETYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp",
}
VIDEO_MIMETYPES = {
    ".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
}
MAX_CAPTION  = 300
BSKY_MAX_IMG = 2_000_000  # Bluesky hard limit: 2 MB per image blob


def _compress_image(path: Path) -> bytes:
    from PIL import Image
    img = Image.open(path).convert("RGB")
    for quality in (85, 70, 55, 40):
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        data = buf.getvalue()
        if len(data) <= BSKY_MAX_IMG:
            return data
    w, h = img.size
    img = img.resize((w // 2, h // 2), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=55, optimize=True)
    return buf.getvalue()


class BlueskyPublisher(BasePublisher):
    def __init__(self, credentials: dict = None):
        _c = credentials or {}
        self.handle       = _c.get("BLUESKY_HANDLE")       or os.environ["BLUESKY_HANDLE"]
        self.app_password = _c.get("BLUESKY_APP_PASSWORD") or os.environ["BLUESKY_APP_PASSWORD"]

    def _client(self):
        from atproto import Client
        client = Client()
        client.login(self.handle, self.app_password)
        return client

    def get_account_info(self) -> "dict | None":
        try:
            client  = self._client()
            profile = client.get_profile(self.handle)
            return {"name": f"@{profile.handle}", "id": profile.did}
        except Exception as e:
            print(f"  Bluesky get_account_info failed: {e}")
            return None

    def verify_auth(self) -> bool:
        info = self.get_account_info()
        if info:
            print(f"Bluesky auth OK — {info['name']} ({info['id']})")
            return True
        print("Bluesky auth failed")
        return False

    def publish(self, item: ContentItem) -> bool:
        try:
            from atproto import client_utils, models
            client = self._client()

            text = (item.caption or "")[:MAX_CAPTION]
            text_builder = client_utils.TextBuilder()
            text_builder.text(text)

            embed = None
            if item.media_path and item.media_path.exists():
                ext = item.media_path.suffix.lower()
                if ext in IMAGE_MIMETYPES:
                    size = item.media_path.stat().st_size
                    if size > BSKY_MAX_IMG:
                        print(f"  Bluesky: image {size // 1024}KB > 2MB — compressing")
                        media_data = _compress_image(item.media_path)
                    else:
                        with open(item.media_path, "rb") as f:
                            media_data = f.read()
                    upload = client.upload_blob(media_data)
                    embed = models.AppBskyEmbedImages.Main(
                        images=[models.AppBskyEmbedImages.Image(
                            image=upload.blob,
                            alt=text[:1000],
                        )]
                    )
                elif ext in VIDEO_MIMETYPES:
                    with open(item.media_path, "rb") as f:
                        media_data = f.read()
                    upload = client.upload_blob(media_data)
                    embed = models.AppBskyEmbedVideo.Main(
                        video=upload.blob,
                        alt=text[:1000],
                    )
                else:
                    print(f"  Bluesky: unsupported media type '{ext}' — posting text only")
            post = client.send_post(text_builder, embed=embed)

            item.metadata["bluesky_post_id"] = post.uri
            item.posted_at = datetime.now(timezone.utc)
            return True

        except Exception as e:
            print(f"  Bluesky publish exception: {e}")
            return False
