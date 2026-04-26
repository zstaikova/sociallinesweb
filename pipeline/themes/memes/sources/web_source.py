import hashlib
import requests
from pathlib import Path
from urllib.parse import urljoin, urlparse

from pipeline.core.base_source import BaseSource
from pipeline.core.content_item import ContentItem

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; socialline/1.0)"}


class WebImageSource(BaseSource):
    """
    Fetches images from a list of URLs.
    Each URL can be:
      - A direct image URL  → downloaded as-is
      - A webpage URL       → scraped for <img> tags, images downloaded
    Caption defaults to the URL's filename stem or the page title if scraped.
    Requires: requests, beautifulsoup4
    """

    def __init__(
        self,
        urls: list[str],
        caption: str = None,        # override caption for all items
        download_dir: Path = None,
    ):
        self.urls = urls
        self.caption_override = caption
        self.download_dir = Path(download_dir or "downloads/web")
        self.download_dir.mkdir(parents=True, exist_ok=True)

    def fetch(self, limit: int = 10) -> list:
        items = []
        for url in self.urls:
            if len(items) >= limit:
                break
            if self._is_image_url(url):
                item = self._item_from_image_url(url)
                if item:
                    items.append(item)
            else:
                scraped = self._scrape_page(url, limit - len(items))
                items.extend(scraped)
        return items[:limit]

    def _is_image_url(self, url: str) -> bool:
        path = url.split("?")[0].lower()
        return any(path.endswith(ext) for ext in IMAGE_EXTENSIONS)

    def _item_from_image_url(self, url: str, caption: str = None) -> ContentItem | None:
        dest = self._download(url)
        if not dest:
            return None
        cap = self.caption_override or caption or Path(url.split("?")[0]).stem.replace("_", " ").replace("-", " ")
        return ContentItem(
            source_url=url,
            source_platform="web",
            media_path=dest,
            caption=cap,
            tags=["web"],
            metadata={"source_url": url},
        )

    def _scrape_page(self, page_url: str, limit: int) -> list:
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            print("  WebImageSource: beautifulsoup4 not installed — run: pip install beautifulsoup4")
            return []

        try:
            resp = requests.get(page_url, timeout=15, headers=HEADERS)
            resp.raise_for_status()
        except Exception as e:
            print(f"  WebImageSource: failed to fetch page {page_url}: {e}")
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        page_title = soup.title.string.strip() if soup.title else ""

        items = []
        for img in soup.find_all("img"):
            if len(items) >= limit:
                break
            src = img.get("src") or img.get("data-src")
            if not src:
                continue
            src = urljoin(page_url, src)
            if not self._is_image_url(src):
                continue
            alt = img.get("alt", "").strip() or page_title
            caption = self.caption_override or alt
            item = self._item_from_image_url(src, caption)
            if item:
                items.append(item)

        print(f"  WebImageSource: scraped {len(items)} image(s) from {page_url}")
        return items

    def _download(self, url: str) -> Path | None:
        try:
            url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
            ext = Path(url.split("?")[0]).suffix.lower() or ".jpg"
            if ext not in IMAGE_EXTENSIONS:
                ext = ".jpg"
            dest = self.download_dir / f"{url_hash}{ext}"
            if dest.exists():
                return dest
            resp = requests.get(url, timeout=15, headers=HEADERS)
            resp.raise_for_status()
            dest.write_bytes(resp.content)
            return dest
        except Exception as e:
            print(f"  WebImageSource: download failed {url}: {e}")
            return None
