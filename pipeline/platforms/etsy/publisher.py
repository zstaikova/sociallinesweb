import os
import requests
from datetime import datetime
from pathlib import Path

from pipeline.core.base_publisher import BasePublisher
from pipeline.core.content_item import ContentItem

ETSY_API_BASE = "https://openapi.etsy.com/v3/application"


class EtsyPublisher(BasePublisher):
    def __init__(self, credentials: dict = None):
        _c = credentials or {}
        self.api_key      = _c.get("ETSY_API_KEY")      or os.environ.get("ETSY_API_KEY", "")
        self.access_token = _c.get("ETSY_ACCESS_TOKEN") or os.environ.get("ETSY_ACCESS_TOKEN", "")
        self.shop_id      = _c.get("ETSY_SHOP_ID")      or os.environ.get("ETSY_SHOP_ID", "")
        self.default_price       = float(_c.get("ETSY_DEFAULT_PRICE") or os.environ.get("ETSY_DEFAULT_PRICE", "9.99"))
        self.default_taxonomy_id = int(_c.get("ETSY_DEFAULT_TAXONOMY_ID") or os.environ.get("ETSY_DEFAULT_TAXONOMY_ID", "2078"))
        self.default_quantity    = int(_c.get("ETSY_DEFAULT_QUANTITY") or os.environ.get("ETSY_DEFAULT_QUANTITY", "1"))

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "x-api-key":     self.api_key,
        }

    def _upload_image(self, listing_id: str, path: Path) -> bool:
        try:
            with open(path, "rb") as f:
                resp = requests.post(
                    f"{ETSY_API_BASE}/shops/{self.shop_id}/listings/{listing_id}/images",
                    headers={
                        "Authorization": f"Bearer {self.access_token}",
                        "x-api-key":     self.api_key,
                    },
                    files={"image": (path.name, f, "image/jpeg")},
                    timeout=60,
                )
            return resp.ok
        except Exception as e:
            print(f"  Etsy: image upload error — {e}")
            return False

    def publish(self, item: ContentItem) -> bool:
        caption = (item.caption or "").strip()
        title   = caption.split("\n")[0].strip() or item.media_path.stem.replace("_", " ")
        title   = title[:140]  # Etsy max title length

        r = requests.post(
            f"{ETSY_API_BASE}/shops/{self.shop_id}/listings",
            headers={**self._headers(), "Content-Type": "application/json"},
            json={
                "quantity":    item.metadata.get("etsy_quantity", self.default_quantity),
                "title":       title,
                "description": caption or title,
                "price":       item.metadata.get("etsy_price", self.default_price),
                "who_made":    "i_did",
                "when_made":   item.metadata.get("etsy_when_made", "2020_2024"),
                "taxonomy_id": item.metadata.get("etsy_taxonomy_id", self.default_taxonomy_id),
            },
            timeout=30,
        )
        if not r.ok:
            print(f"  Etsy: listing creation failed {r.status_code} — {r.text[:200]}")
            return False

        listing_id = str(r.json().get("listing_id", ""))
        if listing_id and item.media_path and item.media_path.exists():
            if not self._upload_image(listing_id, item.media_path):
                print(f"  Etsy: image upload failed for listing {listing_id} (listing created as draft)")

        item.metadata["etsy_post_id"] = listing_id
        item.posted_at = datetime.now().isoformat()
        print(f"  Etsy: created draft listing '{title}' (id={listing_id})")
        return True

    def verify_auth(self) -> bool:
        return self.get_account_info() is not None

    def get_account_info(self) -> "dict | None":
        r = requests.get(
            f"{ETSY_API_BASE}/shops/{self.shop_id}",
            headers=self._headers(),
            timeout=10,
        )
        if r.ok:
            d = r.json()
            name = d.get("shop_name") or f"Etsy Shop {self.shop_id}"
            uid  = str(d.get("shop_id") or self.shop_id)
            print(f"  Etsy: connected to shop '{name}'")
            return {"name": name, "id": uid}
        print(f"  Etsy: auth failed {r.status_code} — check API key, access token, and shop ID")
        return None
