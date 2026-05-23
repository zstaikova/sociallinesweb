import os
import requests

from pipeline.core.base_publisher import BasePublisher
from pipeline.core.content_item import ContentItem

UDEMY_API_BASE = "https://www.udemy.com/instructor-api/v1"


class UdemyPublisher(BasePublisher):
    """
    Read-only connector: verifies bearer token and lists courses.
    Course creation/publishing via the Udemy Instructor API is not supported.
    """

    def __init__(self, credentials: dict = None):
        _c = credentials or {}
        self.bearer_token = (
            _c.get("UDEMY_BEARER_TOKEN")
            or os.environ.get("UDEMY_BEARER_TOKEN", "")
        )

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.bearer_token}",
            "Accept":        "application/json",
        }

    def publish(self, item: ContentItem) -> bool:
        print("  Udemy: course creation via API is not supported — manage courses in your Udemy instructor dashboard")
        return False

    def verify_auth(self) -> bool:
        return self.get_account_info() is not None

    def get_account_info(self) -> "dict | None":
        r = requests.get(
            f"{UDEMY_API_BASE}/taught-courses/courses/",
            headers=self._headers(),
            timeout=10,
        )
        if r.ok:
            data    = r.json()
            count   = data.get("count", 0)
            results = data.get("results", [])
            name    = "Udemy Instructor"
            if results:
                instructors = results[0].get("visible_instructors", [])
                if instructors:
                    name = instructors[0].get("title", name)
            print(f"  Udemy: connected as {name} — {count} course(s)")
            return {"name": name, "id": "udemy"}
        print(f"  Udemy: auth failed {r.status_code} — check bearer token from API Clients page")
        return None
