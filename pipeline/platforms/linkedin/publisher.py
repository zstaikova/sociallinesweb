import os
import requests
from datetime import datetime
from pathlib import Path

from pipeline.core.base_publisher import BasePublisher
from pipeline.core.content_item import ContentItem

API          = "https://api.linkedin.com/rest"
LI_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
HEADERS_BASE = {
    "LinkedIn-Version": "202401",
    "X-Restli-Protocol-Version": "2.0.0",
}
MAX_CAPTION = 3_000


class LinkedInPublisher(BasePublisher):
    def __init__(self, credentials: dict = None, on_token_refresh=None):
        _c = credentials or {}
        self.access_token     = _c.get("LINKEDIN_ACCESS_TOKEN")  or os.environ.get("LINKEDIN_ACCESS_TOKEN", "")
        self.person_urn       = _c.get("LINKEDIN_PERSON_URN")    or os.environ.get("LINKEDIN_PERSON_URN", "")
        self.client_id        = _c.get("LINKEDIN_CLIENT_ID")     or os.environ.get("LINKEDIN_CLIENT_ID", "")
        self.client_secret    = _c.get("LINKEDIN_CLIENT_SECRET") or os.environ.get("LINKEDIN_CLIENT_SECRET", "")
        self.refresh_token    = _c.get("LINKEDIN_REFRESH_TOKEN") or os.environ.get("LINKEDIN_REFRESH_TOKEN", "")
        self.on_token_refresh = on_token_refresh

    def _headers(self):
        return {**HEADERS_BASE, "Authorization": f"Bearer {self.access_token}"}

    # ── Token refresh ────────────────────────────────────────────────────────

    def _refresh(self) -> bool:
        if not self.refresh_token or not self.client_id or not self.client_secret:
            print("  LinkedIn: no refresh token or app credentials — re-authentication required")
            return False
        try:
            r = requests.post(LI_TOKEN_URL, data={
                "grant_type":    "refresh_token",
                "refresh_token": self.refresh_token,
                "client_id":     self.client_id,
                "client_secret": self.client_secret,
            }, timeout=15)
            if r.ok:
                data = r.json()
                self.access_token  = data["access_token"]
                self.refresh_token = data.get("refresh_token", self.refresh_token)
                if self.on_token_refresh:
                    self.on_token_refresh(self.access_token, self.refresh_token)
                print("  LinkedIn: access token refreshed")
                return True
            print(f"  LinkedIn: token refresh failed {r.status_code} {r.text[:200]}")
        except Exception as e:
            print(f"  LinkedIn: token refresh exception: {e}")
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

    def _put(self, url, **kwargs):
        r = requests.put(url, headers={"Authorization": f"Bearer {self.access_token}"}, **kwargs)
        if r.status_code == 401 and self._refresh():
            r = requests.put(url, headers={"Authorization": f"Bearer {self.access_token}"}, **kwargs)
        return r

    # ── Public interface ─────────────────────────────────────────────────────

    def get_account_info(self) -> "dict | None":
        if not self.access_token:
            print("  LinkedIn: no access token")
            return None
        # Try userinfo (needs openid scope); fall back to token-presence check
        r = requests.get("https://api.linkedin.com/v2/userinfo",
                         headers={"Authorization": f"Bearer {self.access_token}"}, timeout=10)
        if r.ok:
            data = r.json()
            return {"name": data.get("name", "LinkedIn"), "id": data.get("sub", self.person_urn)}
        # With Community Management API scopes, userinfo returns 403 — token is still valid
        if r.status_code in (401, 403):
            r2 = requests.get("https://api.linkedin.com/v2/organizationAcls?q=roleAssignee&role=ADMINISTRATOR",
                              headers={**HEADERS_BASE, "Authorization": f"Bearer {self.access_token}"}, timeout=10)
            if r2.ok or r2.status_code == 403:
                name = self.person_urn or "LinkedIn"
                return {"name": name, "id": self.person_urn or "linkedin"}
        print(f"  LinkedIn get_account_info failed: {r.status_code} {r.text[:200]}")
        return None

    def verify_auth(self) -> bool:
        info = self.get_account_info()
        if info:
            print(f"LinkedIn auth OK — {info['name']}")
            return True
        print("LinkedIn auth failed")
        return False

    def publish(self, item: ContentItem) -> bool:
        try:
            text = self._build_caption(item)
            if item.media_path and item.media_path.exists():
                image_urn = self._upload_image(item.media_path)
                if not image_urn:
                    return False
                post_id = self._create_post(text, image_urn=image_urn)
            else:
                post_id = self._create_post(text)

            if not post_id:
                return False

            item.metadata["linkedin_post_id"] = post_id
            item.posted_at = datetime.utcnow()
            return True

        except Exception as e:
            print(f"  LinkedIn publish exception: {e}")
            return False

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _upload_image(self, media_path: Path) -> "str | None":
        init_resp = self._post_req(
            f"{API}/images?action=initializeUpload",
            json={"initializeUploadRequest": {"owner": self.person_urn}},
            timeout=15,
        )
        if not init_resp.ok:
            print(f"  LinkedIn image init failed: {init_resp.status_code} {init_resp.text[:200]}")
            return None

        data       = init_resp.json().get("value", {})
        upload_url = data.get("uploadUrl")
        image_urn  = data.get("image")
        if not upload_url or not image_urn:
            print("  LinkedIn: no upload URL returned")
            return None

        with open(media_path, "rb") as f:
            put_resp = self._put(upload_url, data=f, timeout=60)
        if not put_resp.ok:
            print(f"  LinkedIn image upload failed: {put_resp.status_code} {put_resp.text[:200]}")
            return None
        return image_urn

    def _create_post(self, text: str, image_urn: str = None) -> "str | None":
        body = {
            "author":          self.person_urn,
            "commentary":      text,
            "visibility":      "PUBLIC",
            "distribution":    {"feedDistribution": "MAIN_FEED",
                                "targetEntities": [], "thirdPartyDistributionChannels": []},
            "lifecycleState":  "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }
        if image_urn:
            body["content"] = {"media": {"altText": text[:100], "id": image_urn}}

        resp = self._post_req(f"{API}/posts", json=body, timeout=30)
        if not resp.ok:
            print(f"  LinkedIn post failed: {resp.status_code} {resp.text[:300]}")
            return None
        return resp.headers.get("x-restli-id") or resp.json().get("id")

    def _build_caption(self, item: ContentItem) -> str:
        text = item.caption or ""
        if len(text) > MAX_CAPTION:
            text = text[:MAX_CAPTION - 1] + "…"
        return text
