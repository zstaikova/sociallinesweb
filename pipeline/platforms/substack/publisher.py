import json
import os
import re
import requests
from datetime import datetime
from pathlib import Path

from pipeline.core.base_publisher import BasePublisher
from pipeline.core.content_item import ContentItem

_GLOBAL_ME = "https://substack.com/api/v1/me"


def _parse_publication(raw: str) -> str:
    """Extract the publication subdomain from any URL format the user might paste.

    Handles:
      cognifylearn
      cognifylearn.substack.com
      https://cognifylearn.substack.com
      https://cognifylearn.substack.com/
      https://substack.com/@cognifylearn
    """
    raw = raw.strip().rstrip("/")
    # Full substack.com URL with @ handle → extract the handle
    m = re.search(r"substack\.com/@([A-Za-z0-9_-]+)", raw)
    if m:
        return m.group(1).lower()
    # Any URL containing a subdomain like cognifylearn.substack.com
    m = re.search(r"([A-Za-z0-9_-]+)\.substack\.com", raw)
    if m:
        return m.group(1).lower()
    # Plain subdomain already
    return raw.lower()


def _text_to_tiptap(text: str) -> str:
    """Convert plain text (newline-separated) to a Tiptap/ProseMirror JSON string."""
    paragraphs = []
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped:
            paragraphs.append({
                "type": "paragraph",
                "content": [{"type": "text", "text": stripped}],
            })
    if not paragraphs:
        paragraphs = [{"type": "paragraph"}]
    return json.dumps({"type": "doc", "content": paragraphs})


class SubstackPublisher(BasePublisher):
    def __init__(self, credentials: dict = None):
        _c = credentials or {}
        self.session_token = (
            _c.get("SUBSTACK_SESSION_TOKEN")
            or _c.get("SUBSTACK_SID")
            or os.environ.get("SUBSTACK_SESSION_TOKEN", "")
            or os.environ.get("SUBSTACK_SID", "")
        )
        self.publication = _parse_publication(
            _c.get("SUBSTACK_PUBLICATION") or os.environ.get("SUBSTACK_PUBLICATION", "")
        )
        # Cookie value from DevTools is URL-encoded — decode it for the header
        from urllib.parse import unquote
        raw_token = (
            _c.get("SUBSTACK_SESSION_TOKEN")
            or _c.get("SUBSTACK_SID")
            or os.environ.get("SUBSTACK_SESSION_TOKEN", "")
            or os.environ.get("SUBSTACK_SID", "")
        )
        self.session_token = unquote(raw_token)

    def _base(self) -> str:
        return f"https://{self.publication}.substack.com/api/v1"

    def _headers(self) -> dict:
        return {
            "Cookie": f"substack.sid={self.session_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (compatible; Socialline/1.0)",
        }

    def _upload_image(self, path: Path) -> "str | None":
        try:
            with open(path, "rb") as f:
                resp = requests.post(
                    f"{self._base()}/image",
                    headers={
                        "Cookie": f"substack.sid={self.session_token}",
                        "User-Agent": "Mozilla/5.0",
                    },
                    files={"image": (path.name, f, "image/jpeg")},
                    timeout=30,
                )
            if resp.ok:
                return resp.json().get("url")
        except Exception as e:
            print(f"  Substack: image upload failed — {e}")
        return None

    def publish(self, item: ContentItem) -> bool:
        text = (item.caption or "").strip()
        lines = text.split("\n", 1)
        title = lines[0].strip() or item.media_path.stem.replace("_", " ")
        body  = lines[1].strip() if len(lines) > 1 else ""

        cover_url = None
        if item.media_path and item.media_path.exists():
            cover_url = self._upload_image(item.media_path)

        draft_data: dict = {
            "type": "newsletter",
            "draft_title": title,
            "draft_body": _text_to_tiptap(body or title),
            "audience": "everyone",
        }
        if cover_url:
            draft_data["cover_image"] = cover_url

        r = requests.post(
            f"{self._base()}/drafts",
            headers=self._headers(),
            json=draft_data,
            timeout=30,
        )
        if not r.ok:
            print(f"  Substack: draft creation failed {r.status_code} — {r.text[:200]}")
            return False

        draft_id = r.json().get("id")
        pub = requests.post(
            f"{self._base()}/drafts/{draft_id}/publish",
            headers=self._headers(),
            json={"send_email": True, "audience": "everyone"},
            timeout=30,
        )
        if pub.ok:
            item.metadata["substack_post_id"] = str(draft_id)
            item.posted_at = datetime.now().isoformat()
            print(f"  Substack: published '{title}'")
            return True
        print(f"  Substack: publish failed {pub.status_code} — {pub.text[:200]}")
        return False

    def verify_auth(self) -> bool:
        return self.get_account_info() is not None

    def get_account_info(self) -> "dict | None":
        last_error = ""
        for url in [f"{self._base()}/me", _GLOBAL_ME]:
            try:
                r = requests.get(url, headers=self._headers(), timeout=10)
                if r.ok:
                    d = r.json()
                    name = d.get("name") or d.get("handle") or self.publication
                    uid  = str(d.get("id") or self.publication)
                    return {"name": name, "id": uid}
                last_error = f"HTTP {r.status_code} from {url}: {r.text[:200]}"
            except Exception as e:
                last_error = str(e)
        raise RuntimeError(f"Substack auth failed — {last_error}")
