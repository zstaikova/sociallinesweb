import os
from datetime import datetime, timezone
from pathlib import Path

from pipeline.core.base_publisher import BasePublisher
from pipeline.core.content_item import ContentItem

IMAGE_MIMETYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp",
}
MAX_CAPTION = 300


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
            from atproto import client_utils
            client = self._client()

            text = (item.caption or "")[:MAX_CAPTION]
            text_builder = client_utils.TextBuilder()
            text_builder.text(text)

            if item.media_path and item.media_path.exists():
                ext      = item.media_path.suffix.lower()
                mimetype = IMAGE_MIMETYPES.get(ext, "image/jpeg")
                with open(item.media_path, "rb") as f:
                    image_data = f.read()
                upload = client.upload_blob(image_data)
                embed  = {
                    "$type": "app.bsky.embed.images",
                    "images": [{"image": upload.blob, "alt": text[:1000]}],
                }
                post = client.send_post(text_builder, embed=embed)
            else:
                post = client.send_post(text_builder)

            item.metadata["bluesky_post_id"] = post.uri
            item.posted_at = datetime.now(timezone.utc)
            return True

        except Exception as e:
            print(f"  Bluesky publish exception: {e}")
            return False
