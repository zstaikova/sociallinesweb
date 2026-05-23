import os
import requests

from pipeline.core.base_publisher import BasePublisher
from pipeline.core.content_item import ContentItem

TEACHABLE_API_BASE = "https://developers.teachable.com/v1"


class TeachablePublisher(BasePublisher):
    """
    Read-only connector: verifies API key and lists courses.
    Course creation via the Teachable API is not supported.
    """

    def __init__(self, credentials: dict = None):
        _c = credentials or {}
        self.api_key = (
            _c.get("TEACHABLE_API_KEY")
            or os.environ.get("TEACHABLE_API_KEY", "")
        )

    def _headers(self) -> dict:
        return {"apiKey": self.api_key}

    def publish(self, item: ContentItem) -> bool:
        print("  Teachable: course creation via API is not supported — manage courses in your dashboard")
        return False

    def verify_auth(self) -> bool:
        return self.get_account_info() is not None

    def get_account_info(self) -> "dict | None":
        r = requests.get(
            f"{TEACHABLE_API_BASE}/courses",
            headers=self._headers(),
            timeout=10,
        )
        if r.ok:
            courses = r.json().get("courses", [])
            count   = len(courses)
            school  = courses[0].get("school_name") if courses else None
            name    = school or "Teachable School"
            print(f"  Teachable: connected — {count} course(s)")
            return {"name": name, "id": "teachable"}
        print(f"  Teachable: auth failed {r.status_code} — check API key (requires Pro plan)")
        return None
