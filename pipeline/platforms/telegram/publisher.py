import os
import requests
from datetime import datetime
from pathlib import Path

from pipeline.core.base_publisher import BasePublisher
from pipeline.core.content_item import ContentItem

API = "https://api.telegram.org"

_VIDEO_EXT = {".mp4", ".mov", ".avi", ".mkv", ".webm"}


class TelegramPublisher(BasePublisher):
    """
    Posts photos and videos to a Telegram channel via Bot API.
    No OAuth or app review required.

    Credentials:
      TELEGRAM_BOT_TOKEN  — from @BotFather (/newbot)
      TELEGRAM_CHAT_ID    — @channelname or numeric ID (-100123456789)

    Setup:
      1. Message @BotFather on Telegram → /newbot → copy the token
      2. Add the bot as an admin to your channel
      3. Get the channel ID by forwarding a message to @userinfobot
    """

    def __init__(self, credentials: dict = None):
        _c = credentials or {}
        self.token   = _c.get("TELEGRAM_BOT_TOKEN") or os.environ["TELEGRAM_BOT_TOKEN"]
        self.chat_id = _c.get("TELEGRAM_CHAT_ID")   or os.environ["TELEGRAM_CHAT_ID"]

    def _url(self, method: str) -> str:
        return f"{API}/bot{self.token}/{method}"

    def get_account_info(self) -> "dict | None":
        r = requests.get(self._url("getMe"), timeout=10)
        if r.ok:
            d = r.json().get("result", {})
            return {"name": f"@{d.get('username', 'bot')}", "id": self.chat_id}
        return None

    def verify_auth(self) -> bool:
        info = self.get_account_info()
        if info:
            print(f"Telegram auth OK — {info['name']} → {self.chat_id}")
            return True
        print("Telegram auth failed — check bot token")
        return False

    def publish(self, item: ContentItem) -> bool:
        caption = (item.caption or "")[:1024]

        if not item.media_path or not item.media_path.exists():
            return self._send_message(caption)

        ext = item.media_path.suffix.lower()
        try:
            if ext in _VIDEO_EXT:
                ok = self._send_video(item.media_path, caption)
            else:
                ok = self._send_photo(item.media_path, caption)
        except Exception as e:
            print(f"  Telegram publish exception: {e}")
            return False

        if ok:
            item.posted_at = datetime.utcnow()
        return ok

    def _send_photo(self, path: Path, caption: str) -> bool:
        with open(path, "rb") as f:
            r = requests.post(self._url("sendPhoto"), data={
                "chat_id":    self.chat_id,
                "caption":    caption,
                "parse_mode": "HTML",
            }, files={"photo": f}, timeout=60)
        if not r.ok:
            print(f"  Telegram sendPhoto failed: {r.status_code} {r.text[:200]}")
            return False
        return True

    def _send_video(self, path: Path, caption: str) -> bool:
        with open(path, "rb") as f:
            r = requests.post(self._url("sendVideo"), data={
                "chat_id":            self.chat_id,
                "caption":            caption,
                "parse_mode":         "HTML",
                "supports_streaming": "true",
            }, files={"video": f}, timeout=300)
        if not r.ok:
            print(f"  Telegram sendVideo failed: {r.status_code} {r.text[:200]}")
            return False
        return True

    def _send_message(self, text: str) -> bool:
        r = requests.post(self._url("sendMessage"), json={
            "chat_id":    self.chat_id,
            "text":       text,
            "parse_mode": "HTML",
        }, timeout=30)
        if not r.ok:
            print(f"  Telegram sendMessage failed: {r.status_code} {r.text[:200]}")
            return False
        return True
