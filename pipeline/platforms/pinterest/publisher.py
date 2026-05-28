import os
import requests
from datetime import datetime
from pathlib import Path

from pipeline.core.base_publisher import BasePublisher
from pipeline.core.content_item import ContentItem

API          = "https://api.pinterest.com/v5"
TOKEN_URL    = "https://api.pinterest.com/v5/oauth/token"
MAX_TITLE    = 100
MAX_DESC     = 500


class PinterestPublisher(BasePublisher):
    def __init__(self, credentials: dict = None, on_token_refresh=None):
        _c = credentials or {}
        self.access_token     = _c.get("PINTEREST_ACCESS_TOKEN")  or os.environ["PINTEREST_ACCESS_TOKEN"]
        self.board_id         = _c.get("PINTEREST_BOARD_ID")      or os.environ["PINTEREST_BOARD_ID"]
        self.client_id        = _c.get("PINTEREST_CLIENT_ID")     or os.environ.get("PINTEREST_CLIENT_ID", "")
        self.client_secret    = _c.get("PINTEREST_CLIENT_SECRET") or os.environ.get("PINTEREST_CLIENT_SECRET", "")
        self.refresh_token    = _c.get("PINTEREST_REFRESH_TOKEN") or os.environ.get("PINTEREST_REFRESH_TOKEN", "")
        self.on_token_refresh = on_token_refresh

    def _headers(self):
        return {"Authorization": f"Bearer {self.access_token}"}

    # ── Token refresh ────────────────────────────────────────────────────────

    def _refresh(self) -> bool:
        if not self.refresh_token or not self.client_id or not self.client_secret:
            print("  Pinterest: no refresh token or app credentials — re-authentication required")
            return False
        try:
            r = requests.post(TOKEN_URL,
                data={"grant_type": "refresh_token", "refresh_token": self.refresh_token},
                auth=(self.client_id, self.client_secret),
                timeout=15,
            )
            if r.ok:
                data = r.json()
                self.access_token  = data["access_token"]
                self.refresh_token = data.get("refresh_token", self.refresh_token)
                if self.on_token_refresh:
                    self.on_token_refresh(self.access_token, self.refresh_token)
                print("  Pinterest: access token refreshed")
                return True
            print(f"  Pinterest: token refresh failed {r.status_code} {r.text[:200]}")
        except Exception as e:
            print(f"  Pinterest: token refresh exception: {e}")
        return False

    def _get(self, url, **kwargs):
        r = requests.get(url, headers=self._headers(), **kwargs)
        if r.status_code == 401 and self._refresh():
            r = requests.get(url, headers=self._headers(), **kwargs)
        return r

    def _post_req(self, url, **kwargs):
        r = requests.post(url, headers=self._headers(), **kwargs)
        if r.status_code == 401 and self._refresh():
            r = requests.post(url, headers=self._headers(), **kwargs)
        return r

    # ── Public interface ─────────────────────────────────────────────────────

    def get_account_info(self) -> "dict | None":
        r = self._get(f"{API}/user_account", timeout=10)
        if r.ok:
            data = r.json()
            return {"name": f"@{data.get('username', '')}", "id": data.get("id", self.board_id)}
        return None

    def verify_auth(self) -> bool:
        info = self.get_account_info()
        if info:
            print(f"Pinterest auth OK — {info['name']}")
            return True
        print("Pinterest auth failed")
        return False

    def publish(self, item: ContentItem) -> bool:
        try:
            title       = (item.caption or "")[:MAX_TITLE]
            description = (item.caption or "")[:MAX_DESC]

            if not item.media_path or not item.media_path.exists():
                print("  Pinterest: no image — skipping")
                return False

            media_source = self._upload_image(item.media_path)
            if not media_source:
                return False

            resp = self._post_req(
                f"{API}/pins",
                json={"board_id": self.board_id, "title": title,
                      "description": description, "media_source": media_source},
                timeout=30,
            )
            if not resp.ok:
                print(f"  Pinterest pin failed: {resp.status_code} {resp.text[:300]}")
                return False

            pin_id = resp.json().get("id")
            item.metadata["pinterest_post_id"] = pin_id
            item.posted_at = datetime.utcnow()
            return True

        except Exception as e:
            print(f"  Pinterest publish exception: {e}")
            return False

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _upload_image(self, media_path: Path) -> "dict | None":
        """Upload image via Pinterest's media upload endpoint, return media_source dict."""
        ext = media_path.suffix.lower()
        mime_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                    ".png": "image/png", ".webp": "image/webp"}
        mime = mime_map.get(ext, "image/jpeg")

        # Try multipart upload first (no base64 memory spike)
        try:
            with open(media_path, "rb") as f:
                r = requests.post(
                    f"{API}/media",
                    headers=self._headers(),
                    files={"file": (media_path.name, f, mime)},
                    timeout=60,
                )
            if r.ok:
                media_id = r.json().get("media_id")
                if media_id:
                    return {"source_type": "media_id", "media_id": media_id}
        except Exception:
            pass

        # Fallback: base64 (Pinterest always accepts this)
        import base64
        with open(media_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
        return {"source_type": "image_base64", "content_type": mime, "data": b64}
