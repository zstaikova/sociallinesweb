import os
import requests
from datetime import datetime

from pipeline.core.base_publisher import BasePublisher
from pipeline.core.content_item import ContentItem

API = "https://api.linkedin.com/rest"
HEADERS_BASE = {
    "LinkedIn-Version": "202401",
    "X-Restli-Protocol-Version": "2.0.0",
}


class LinkedInPublisher(BasePublisher):
    """
    Publishes image posts to LinkedIn via the Posts API.
    Credentials: LINKEDIN_ACCESS_TOKEN, LINKEDIN_PERSON_URN
    LINKEDIN_PERSON_URN format: urn:li:person:{id}
    Get token via LinkedIn OAuth — scope: w_member_social
    """

    def __init__(self, credentials: dict = None):
        _c = credentials or {}
        self.access_token = _c.get("LINKEDIN_ACCESS_TOKEN") or os.environ["LINKEDIN_ACCESS_TOKEN"]
        self.person_urn   = _c.get("LINKEDIN_PERSON_URN")   or os.environ["LINKEDIN_PERSON_URN"]

    def _headers(self):
        return {**HEADERS_BASE, "Authorization": f"Bearer {self.access_token}"}

    def get_account_info(self) -> "dict | None":
        resp = requests.get(
            "https://api.linkedin.com/v2/userinfo",
            headers={"Authorization": f"Bearer {self.access_token}"},
            timeout=10,
        )
        if resp.ok:
            data = resp.json()
            return {"name": data.get("name", data.get("sub", "")), "id": self.person_urn}
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
            if item.media_path and item.media_path.exists():
                image_urn = self._upload_image(item.media_path)
                if not image_urn:
                    return False
                post_id = self._create_post(item.caption or "", image_urn=image_urn)
            else:
                post_id = self._create_post(item.caption or "")

            if not post_id:
                return False

            item.metadata["linkedin_post_id"] = post_id
            item.posted_at = datetime.utcnow()
            return True

        except Exception as e:
            print(f"  LinkedIn publish exception: {e}")
            return False

    def _upload_image(self, media_path) -> str | None:
        # Step 1: Initialize upload
        init_resp = requests.post(
            f"{API}/images?action=initializeUpload",
            headers=self._headers(),
            json={"initializeUploadRequest": {"owner": self.person_urn}},
            timeout=15,
        )
        if not init_resp.ok:
            print(f"  LinkedIn image init failed: {init_resp.status_code} {init_resp.text}")
            return None

        data       = init_resp.json().get("value", {})
        upload_url = data.get("uploadUrl")
        image_urn  = data.get("image")

        if not upload_url or not image_urn:
            print("  LinkedIn: no upload URL returned")
            return None

        # Step 2: Upload binary
        with open(media_path, "rb") as f:
            put_resp = requests.put(
                upload_url,
                data=f,
                headers={"Authorization": f"Bearer {self.access_token}"},
                timeout=60,
            )
        if not put_resp.ok:
            print(f"  LinkedIn image upload failed: {put_resp.status_code}")
            return None

        return image_urn

    def _create_post(self, text: str, image_urn: str = None) -> str | None:
        body = {
            "author": self.person_urn,
            "commentary": text,
            "visibility": "PUBLIC",
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "targetEntities": [],
                "thirdPartyDistributionChannels": [],
            },
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }

        if image_urn:
            body["content"] = {
                "media": {
                    "altText": text[:100],
                    "id": image_urn,
                }
            }

        resp = requests.post(
            f"{API}/posts",
            headers=self._headers(),
            json=body,
            timeout=30,
        )
        if not resp.ok:
            print(f"  LinkedIn post failed: {resp.status_code} {resp.text}")
            return None

        return resp.headers.get("x-restli-id") or resp.json().get("id")
