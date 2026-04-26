import os
import tweepy
from datetime import datetime

from pipeline.core.base_publisher import BasePublisher
from pipeline.core.content_item import ContentItem


class XPublisher(BasePublisher):
    def __init__(self):
        self.consumer_key        = os.environ["X_CONSUMER_KEY"]
        self.consumer_secret     = os.environ["X_CONSUMER_SECRET"]
        self.access_token        = os.environ["X_ACCESS_TOKEN"]
        self.access_token_secret = os.environ["X_ACCESS_TOKEN_SECRET"]

        # v2 client for posting tweets
        self.client = tweepy.Client(
            consumer_key=self.consumer_key,
            consumer_secret=self.consumer_secret,
            access_token=self.access_token,
            access_token_secret=self.access_token_secret,
        )

        # v1.1 API for media uploads (v2 doesn't support media upload directly)
        auth = tweepy.OAuth1UserHandler(
            self.consumer_key, self.consumer_secret,
            self.access_token, self.access_token_secret,
        )
        self.api_v1 = tweepy.API(auth)

    def verify_auth(self) -> bool:
        try:
            me = self.client.get_me()
            if me.data:
                print(f"X auth OK — account: @{me.data.username} ({me.data.id})")
                return True
            print("X auth failed: no user data returned")
            return False
        except Exception as e:
            print(f"X auth failed: {e}")
            return False

    def publish(self, item: ContentItem) -> bool:
        try:
            text = self._build_text(item)
            media_id = None

            if item.media_path and item.media_path.exists():
                media = self.api_v1.media_upload(filename=str(item.media_path))
                media_id = media.media_id

            resp = self.client.create_tweet(
                text=text,
                media_ids=[media_id] if media_id else None,
            )

            if resp.data:
                item.metadata["x_tweet_id"] = resp.data["id"]
                item.posted_at = datetime.utcnow()
                return True

            print("  X post failed: no data in response")
            return False

        except Exception as e:
            print(f"  X publish exception: {type(e).__name__}: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"  X response status: {e.response.status_code}")
                print(f"  X response body: {e.response.text}")
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
