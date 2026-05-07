import os
from datetime import datetime, timezone
from pathlib import Path

from pipeline.core.base_publisher import BasePublisher
from pipeline.core.content_item import ContentItem

IMAGE_MIMETYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp",
}


class BlueskyPublisher(BasePublisher):
    """
    Publishes image posts to Bluesky via the AT Protocol.
    Credentials: BLUESKY_HANDLE, BLUESKY_APP_PASSWORD
    Get an app password at: https://bsky.app/settings/app-passwords
    """

    def __init__(self, credentials: dict = None):
        _c = credentials or {}
        self.handle       = _c.get("BLUESKY_HANDLE")        or os.environ["BLUESKY_HANDLE"]
        self.app_password = _c.get("BLUESKY_APP_PASSWORD")  or os.environ["BLUESKY_APP_PASSWORD"]

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
        except Exception:
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

            text_builder = client_utils.TextBuilder()
            text_builder.text(item.caption or "")

            if item.media_path and item.media_path.exists():
                ext      = Path(item.media_path).suffix.lower()
                mimetype = IMAGE_MIMETYPES.get(ext, "image/jpeg")
                with open(item.media_path, "rb") as f:
                    image_data = f.read()
                upload = client.upload_blob(image_data)
                images = [{"image": upload.blob, "alt": item.caption or ""}]
                embed  = {"$type": "app.bsky.embed.images", "images": images}
                post   = client.send_post(text_builder, embed=embed)
            else:
                post = client.send_post(text_builder)

            item.metadata["bluesky_post_uri"] = post.uri
            item.posted_at = datetime.now(timezone.utc)
            return True

        except Exception as e:
            print(f"  Bluesky publish exception: {e}")
            return False
