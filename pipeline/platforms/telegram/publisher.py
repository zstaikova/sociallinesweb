import os
import requests
from datetime import datetime
from pathlib import Path

from pipeline.core.base_publisher import BasePublisher
from pipeline.core.content_item import ContentItem

API       = "https://api.telegram.org"
MAX_CAPTION = 1_024
_VIDEO_EXT  = {".mp4", ".mov", ".avi", ".mkv", ".webm"}


class TelegramPublisher(BasePublisher):
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
        caption = (item.caption or "")[:MAX_CAPTION]

        if not item.media_path or not item.media_path.exists():
            return self._send_message(caption, item)

        ext = item.media_path.suffix.lower()
        try:
            if ext in _VIDEO_EXT:
                ok, msg_id = self._send_video(item.media_path, caption)
            else:
                ok, msg_id = self._send_photo(item.media_path, caption)
        except Exception as e:
            print(f"  Telegram publish exception: {e}")
            return False

        if ok:
            if msg_id:
                item.metadata["telegram_post_id"] = str(msg_id)
            item.posted_at = datetime.utcnow()
        return ok

    def _send_photo(self, path: Path, caption: str) -> "tuple[bool, int|None]":
        with open(path, "rb") as f:
            r = requests.post(self._url("sendPhoto"), data={
                "chat_id": self.chat_id,
                "caption": caption,
            }, files={"photo": f}, timeout=60)
        if not r.ok:
            print(f"  Telegram sendPhoto failed: {r.status_code} {r.text[:300]}")
            return False, None
        msg_id = r.json().get("result", {}).get("message_id")
        return True, msg_id

    def _send_video(self, path: Path, caption: str) -> "tuple[bool, int|None]":
        with open(path, "rb") as f:
            r = requests.post(self._url("sendVideo"), data={
                "chat_id":            self.chat_id,
                "caption":            caption,
                "supports_streaming": "true",
            }, files={"video": f}, timeout=300)
        if not r.ok:
            print(f"  Telegram sendVideo failed: {r.status_code} {r.text[:300]}")
            return False, None
        msg_id = r.json().get("result", {}).get("message_id")
        return True, msg_id

    def _send_message(self, text: str, item: ContentItem) -> bool:
        r = requests.post(self._url("sendMessage"), json={
            "chat_id": self.chat_id,
            "text":    text or "(no caption)",
        }, timeout=30)
        if not r.ok:
            print(f"  Telegram sendMessage failed: {r.status_code} {r.text[:300]}")
            return False
        msg_id = r.json().get("result", {}).get("message_id")
        if msg_id:
            item.metadata["telegram_post_id"] = str(msg_id)
        item.posted_at = datetime.utcnow()
        return True
