import os
import time
import tweepy
from datetime import datetime

from pipeline.core.base_publisher import BasePublisher
from pipeline.core.content_item import ContentItem

_RETRY_STATUSES = {500, 502, 503, 504, 520, 521, 522, 524}
_MAX_RETRIES    = 3
_RETRY_DELAY    = 10  # seconds between retries


class XPublisher(BasePublisher):
    def __init__(self, credentials: dict = None):
        _c = credentials or {}
        self.consumer_key        = _c.get("X_CONSUMER_KEY")        or os.environ["X_CONSUMER_KEY"]
        self.consumer_secret     = _c.get("X_CONSUMER_SECRET")     or os.environ["X_CONSUMER_SECRET"]
        self.access_token        = _c.get("X_ACCESS_TOKEN")        or os.environ["X_ACCESS_TOKEN"]
        self.access_token_secret = _c.get("X_ACCESS_TOKEN_SECRET") or os.environ["X_ACCESS_TOKEN_SECRET"]

        self.client = tweepy.Client(
            consumer_key=self.consumer_key,
            consumer_secret=self.consumer_secret,
            access_token=self.access_token,
            access_token_secret=self.access_token_secret,
        )
        auth = tweepy.OAuth1UserHandler(
            self.consumer_key, self.consumer_secret,
            self.access_token, self.access_token_secret,
        )
        self.api_v1 = tweepy.API(auth)

    def get_account_info(self) -> "dict | None":
        try:
            me = self.client.get_me()
            if me.data:
                return {"name": f"@{me.data.username}", "id": str(me.data.id)}
        except Exception:
            pass
        return None

    def verify_auth(self) -> bool:
        info = self.get_account_info()
        if info:
            print(f"X auth OK — account: {info['name']} ({info['id']})")
            return True
        print("X auth failed")
        return False

    def publish(self, item: ContentItem) -> bool:
        text = self._build_text(item)

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                media_id = None
                if item.media_path and item.media_path.exists():
                    media = self.api_v1.media_upload(filename=str(item.media_path))
                    media_id = media.media_id

                resp = self.client.create_tweet(
                    text=text,
                    media_ids=[media_id] if media_id else None,
                )

                if resp.data:
                    item.metadata["x_post_id"] = resp.data["id"]
                    item.posted_at = datetime.utcnow()
                    return True

                print("  X post failed: no data in response")
                return False

            except Exception as e:
                status = None
                if hasattr(e, 'response') and e.response is not None:
                    status = e.response.status_code
                elif hasattr(e, 'api_codes'):
                    pass

                is_transient = (
                    isinstance(e, tweepy.TwitterServerError)
                    or (status is not None and status in _RETRY_STATUSES)
                )

                print(f"  X publish exception (attempt {attempt}/{_MAX_RETRIES}): {type(e).__name__}: {e}")
                if status:
                    print(f"  X response status: {status}")

                if is_transient and attempt < _MAX_RETRIES:
                    print(f"  Transient error — retrying in {_RETRY_DELAY}s…")
                    time.sleep(_RETRY_DELAY)
                    continue

                return False

        return False

    def _build_text(self, item: ContentItem) -> str:
        # X limit is 280 chars — keep caption short, add a few hashtags
        hashtags = ""
        if item.tags:
            hashtags = " " + " ".join(f"#{t.replace(' ', '')}" for t in item.tags[:3])

        text = item.caption
        combined = f"{text}{hashtags}"

        if len(combined) <= 280:
            return combined

        # Truncate caption to fit hashtags
        max_caption = 280 - len(hashtags) - 1
        return f"{text[:max_caption]}…{hashtags}"
