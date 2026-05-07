"""
Source configuration store (SQLite) + pull engine.

Source types:
  reddit   — pull from a subreddit
  google   — theme/keyword search via Google Custom Search API
  url      — single image/video/article URL
  folder   — watch a local folder
  website  — scrape images from a URL
  article  — fetch + summarise an article URL into a caption
"""
import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path


# ── Store ─────────────────────────────────────────────────────────────────────

class SourceStore:
    def __init__(self, db_path: Path):
        self.db_path = str(db_path)
        self._init_db()

    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sources (
                    id          TEXT PRIMARY KEY,
                    name        TEXT NOT NULL,
                    type        TEXT NOT NULL,
                    config      TEXT NOT NULL DEFAULT '{}',
                    enabled     INTEGER NOT NULL DEFAULT 1,
                    schedule    TEXT,
                    last_pulled TEXT,
                    last_count  INTEGER DEFAULT 0,
                    created_at  TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pull_log (
                    id          TEXT PRIMARY KEY,
                    source_id   TEXT NOT NULL,
                    pulled_at   TEXT NOT NULL,
                    count       INTEGER NOT NULL DEFAULT 0,
                    status      TEXT NOT NULL DEFAULT 'ok',
                    error       TEXT
                )
            """)

    # ── CRUD ──────────────────────────────────────────────────────────────────

    def add(self, name: str, type_: str, config: dict,
            schedule: str = None) -> dict:
        sid = str(uuid.uuid4())[:8]
        now = datetime.now().isoformat(timespec="seconds")
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO sources VALUES (?,?,?,?,?,?,?,?,?)",
                (sid, name, type_, json.dumps(config), 1, schedule, None, 0, now)
            )
        return self.get(sid)

    def get(self, source_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM sources WHERE id=?", (source_id,)
            ).fetchone()
        return self._parse(row) if row else None

    def list_all(self, enabled_only=False) -> list:
        with self._conn() as conn:
            if enabled_only:
                rows = conn.execute(
                    "SELECT * FROM sources WHERE enabled=1 ORDER BY created_at"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM sources ORDER BY created_at"
                ).fetchall()
        return [self._parse(r) for r in rows]

    def update(self, source_id: str, **fields):
        allowed = {"name", "config", "enabled", "schedule",
                   "last_pulled", "last_count"}
        sets, vals = [], []
        for k, v in fields.items():
            if k not in allowed:
                continue
            sets.append(f"{k}=?")
            vals.append(json.dumps(v) if k == "config" else v)
        if not sets:
            return
        vals.append(source_id)
        with self._conn() as conn:
            conn.execute(
                f"UPDATE sources SET {', '.join(sets)} WHERE id=?", vals
            )

    def delete(self, source_id: str):
        with self._conn() as conn:
            conn.execute("DELETE FROM sources WHERE id=?", (source_id,))
            conn.execute("DELETE FROM pull_log WHERE source_id=?", (source_id,))

    def log_pull(self, source_id: str, count: int,
                 status: str = "ok", error: str = None):
        lid = str(uuid.uuid4())[:8]
        now = datetime.now().isoformat(timespec="seconds")
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO pull_log VALUES (?,?,?,?,?,?)",
                (lid, source_id, now, count, status, error)
            )
        self.update(source_id, last_pulled=now, last_count=count)

    def recent_log(self, source_id: str, limit=5) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM pull_log WHERE source_id=? "
                "ORDER BY pulled_at DESC LIMIT ?",
                (source_id, limit)
            ).fetchall()
        return [dict(r) for r in rows]

    def _parse(self, row) -> dict:
        d = dict(row)
        d["config"] = json.loads(d["config"])
        d["enabled"] = bool(d["enabled"])
        return d


# ── Pull engine ───────────────────────────────────────────────────────────────

class PullEngine:
    """
    Pulls content from a source into the queue directory.
    Returns list of filenames added.
    """

    DEDUP_DAYS = 30  # how long before a post can be reused

    def __init__(self, queue_dir: Path, env_file: Path):
        self.queue_dir  = queue_dir
        self.env_file   = env_file
        self._dedup_file = queue_dir.parent / "dedup_log.json"
        queue_dir.mkdir(parents=True, exist_ok=True)

    # ── Dedup helpers ─────────────────────────────────────────────────────────

    def _load_dedup(self) -> dict:
        if self._dedup_file.exists():
            try:
                return json.loads(self._dedup_file.read_text())
            except Exception:
                pass
        return {}

    def _save_dedup(self, log: dict):
        self._dedup_file.write_text(json.dumps(log, indent=2))

    def _dedup_seen(self, log: dict, key: str) -> bool:
        """Return True if key was seen within DEDUP_DAYS."""
        from datetime import timedelta
        if key not in log:
            return False
        try:
            pulled_at = datetime.fromisoformat(log[key])
            return (datetime.now() - pulled_at).days < self.DEDUP_DAYS
        except Exception:
            return False

    def _dedup_add(self, log: dict, key: str):
        log[key] = datetime.now().isoformat(timespec="seconds")

    def _dedup_clean(self, log: dict):
        """Remove entries older than DEDUP_DAYS."""
        cutoff = datetime.now().timestamp() - self.DEDUP_DAYS * 86400
        return {
            k: v for k, v in log.items()
            if datetime.fromisoformat(v).timestamp() > cutoff
        }

    def _queue_hashes(self) -> set:
        """MD5 hashes of all files currently in queue."""
        import hashlib
        hashes = set()
        for f in self.queue_dir.iterdir():
            if f.is_file() and not f.suffix == ".json" and not f.suffix == ".txt":
                try:
                    hashes.add(hashlib.md5(f.read_bytes()).hexdigest())
                except Exception:
                    pass
        return hashes

    def pull(self, source: dict) -> list[str]:
        """Dispatch to the right puller. Returns list of filenames added."""
        t = source["type"]
        cfg = source["config"]
        if t == "reddit":
            return self._pull_reddit(cfg)
        if t == "google":
            return self._pull_google(cfg)
        if t == "url":
            return self._pull_url(cfg)
        if t == "article":
            return self._pull_article(cfg)
        if t == "folder":
            return self._pull_folder(cfg)
        if t == "website":
            return self._pull_website(cfg)
        raise ValueError(f"Unknown source type: {t}")

    # ── Reddit ────────────────────────────────────────────────────────────────

    def _pull_reddit(self, cfg: dict) -> list[str]:
        import praw, requests, os
        from dotenv import load_dotenv
        load_dotenv(self.env_file, override=True)

        reddit = praw.Reddit(
            client_id=cfg.get("client_id") or os.environ["REDDIT_CLIENT_ID"],
            client_secret=cfg.get("client_secret") or os.environ["REDDIT_CLIENT_SECRET"],
            user_agent="socialline/1.0",
        )
        sub      = cfg.get("subreddit", "memes")
        sort     = cfg.get("sort", "hot")
        limit    = int(cfg.get("limit", 10))
        min_score = int(cfg.get("min_score", 0))
        time_filter = cfg.get("time_filter", "day")

        subreddit = reddit.subreddit(sub)
        if sort == "hot":
            posts = subreddit.hot(limit=limit * 3)
        elif sort == "new":
            posts = subreddit.new(limit=limit * 3)
        else:  # top
            posts = subreddit.top(time_filter=time_filter, limit=limit * 3)

        # Keyword filters
        raw_allowlist = cfg.get("allowlist", "")
        raw_blocklist = cfg.get("blocklist", "")
        skip_nsfw     = cfg.get("skip_nsfw", True)

        allowlist = [w.strip().lower() for w in raw_allowlist.split(",") if w.strip()] if raw_allowlist else []
        blocklist = [w.strip().lower() for w in raw_blocklist.split(",") if w.strip()] if raw_blocklist else []

        dedup_log     = self._load_dedup()
        dedup_log     = self._dedup_clean(dedup_log)
        queue_hashes  = self._queue_hashes()

        IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4"}
        added = []
        for post in posts:
            if len(added) >= limit:
                break
            if post.score < min_score:
                continue
            # NSFW filter
            if skip_nsfw and post.over_18:
                continue
            # Dedup — skip if pulled within DEDUP_DAYS
            if self._dedup_seen(dedup_log, post.id):
                continue
            title_lower = post.title.lower()
            # Blocklist — skip if any blocked word found in title
            if blocklist and any(w in title_lower for w in blocklist):
                continue
            # Allowlist — skip if none of the allowed words found in title
            if allowlist and not any(w in title_lower for w in allowlist):
                continue
            url = post.url
            ext = Path(url.split("?")[0]).suffix.lower()
            if ext not in IMAGE_EXT:
                continue
            filename = self._download(url, post.title, queue_hashes=queue_hashes)
            if filename:
                self._dedup_add(dedup_log, post.id)
                added.append(filename)

        self._save_dedup(dedup_log)
        return added

    # ── Google Custom Search ──────────────────────────────────────────────────

    def _pull_google(self, cfg: dict) -> list[str]:
        import requests, os
        from dotenv import load_dotenv
        load_dotenv(self.env_file, override=True)

        api_key = cfg.get("api_key") or os.environ.get("GOOGLE_SEARCH_API_KEY", "")
        cx      = cfg.get("cx") or os.environ.get("GOOGLE_SEARCH_CX", "")
        query   = cfg.get("query", "")
        limit   = int(cfg.get("limit", 10))
        search_type = cfg.get("search_type", "image")  # image or web
        date_restrict = cfg.get("date_restrict", "")   # e.g. d7 = last 7 days

        if not api_key or not cx or not query:
            raise ValueError("Google Search requires api_key, cx, and query")

        params = {
            "key":        api_key,
            "cx":         cx,
            "q":          query,
            "num":        min(limit, 10),  # Google max per request = 10
        }
        if search_type == "image":
            params["searchType"] = "image"
        if date_restrict:
            params["dateRestrict"] = date_restrict

        r = requests.get(
            "https://www.googleapis.com/customsearch/v1",
            params=params, timeout=15
        )
        r.raise_for_status()
        items = r.json().get("items", [])

        added = []
        for item in items:
            if search_type == "image":
                url = item.get("link", "")
                title = item.get("title", "image")
                filename = self._download(url, title)
                if filename:
                    added.append(filename)
            else:
                # Web result — treat as article
                url   = item.get("link", "")
                title = item.get("title", "")
                snippet = item.get("snippet", "")
                # Try to get image from page image
                img = (item.get("pagemap", {})
                           .get("cse_image", [{}])[0]
                           .get("src", ""))
                filename = None
                if img:
                    filename = self._download(img, title)
                if filename:
                    # Store article context as a sidecar for caption generation
                    sidecar = self.queue_dir / (Path(filename).stem + ".article.json")
                    sidecar.write_text(json.dumps({
                        "url": url, "title": title, "snippet": snippet
                    }))
                    added.append(filename)
        return added

    # ── Single URL ────────────────────────────────────────────────────────────

    def _pull_url(self, cfg: dict) -> list[str]:
        url   = cfg.get("url", "")
        title = cfg.get("title", "")
        if not url:
            raise ValueError("url is required")
        filename = self._download(url, title or url)
        return [filename] if filename else []

    # ── Article ───────────────────────────────────────────────────────────────

    def _pull_article(self, cfg: dict) -> list[str]:
        import requests
        from bs4 import BeautifulSoup

        url = cfg.get("url", "")
        if not url:
            raise ValueError("url is required")

        r = requests.get(url, timeout=15,
                         headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        # Extract title
        title = (soup.find("meta", property="og:title") or {}).get("content", "")
        if not title:
            title_tag = soup.find("title")
            title = title_tag.get_text(strip=True) if title_tag else "article"

        # Extract main image
        img_url = (soup.find("meta", property="og:image") or {}).get("content", "")

        # Extract article text (paragraphs)
        paragraphs = [p.get_text(strip=True) for p in soup.find_all("p") if len(p.get_text(strip=True)) > 60]
        article_text = "\n".join(paragraphs[:20])  # first 20 paragraphs

        # Summarise with DeepSeek
        style       = cfg.get("style", "bullets")
        max_bullets = int(cfg.get("max_bullets", 5))
        summary = self._summarise_article(title, article_text, url,
                                          style=style, max_bullets=max_bullets)

        added = []
        if img_url:
            filename = self._download(img_url, title)
            if filename:
                # Write summary as the caption sidecar
                sidecar = self.queue_dir / (Path(filename).stem + ".caption.txt")
                sidecar.write_text(summary)
                added.append(filename)
        return added

    def _summarise_article(self, title: str, text: str, url: str,
                           style: str = "bullets", max_bullets: int = 5) -> str:
        import requests as req, os
        from dotenv import load_dotenv
        load_dotenv(self.env_file, override=True)

        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        if not api_key:
            return title

        if style == "bullets":
            prompt = f"""Summarise this article into {max_bullets} concise bullet points.

Title: {title}
URL: {url}
Content: {text[:3000]}

Rules:
- Start each bullet with •
- One bullet per line
- Lead with the most important point
- Keep each bullet under 100 characters
- No preamble, no title, no extra text — bullets only"""
        else:
            prompt = f"""Summarise this article into 1-2 punchy sentences for social media.
Title: {title}
URL: {url}
Content: {text[:3000]}

Return only the caption text, nothing else."""

        r = req.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"},
            json={"model": "deepseek-chat",
                  "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": 400},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()

    # ── Local folder ──────────────────────────────────────────────────────────

    def _pull_folder(self, cfg: dict) -> list[str]:
        import shutil
        folder = Path(cfg.get("path", ""))
        if not folder.exists():
            raise ValueError(f"Folder not found: {folder}")

        EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".mov"}
        added = []
        for f in sorted(folder.iterdir()):
            if f.is_file() and f.suffix.lower() in EXTS:
                dest = self.queue_dir / f.name
                if not dest.exists():
                    shutil.copy2(f, dest)
                    added.append(f.name)
        return added

    # ── Website scrape ────────────────────────────────────────────────────────

    def _pull_website(self, cfg: dict) -> list[str]:
        import requests
        from bs4 import BeautifulSoup
        from urllib.parse import urljoin

        url      = cfg.get("url", "")
        selector = cfg.get("selector", "img")
        limit    = int(cfg.get("limit", 10))

        r = requests.get(url, timeout=15,
                         headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4"}
        added = []
        for el in soup.select(selector):
            if len(added) >= limit:
                break
            src = el.get("src") or el.get("href") or ""
            if not src:
                continue
            src = urljoin(url, src)
            ext = Path(src.split("?")[0]).suffix.lower()
            if ext not in EXTS:
                continue
            filename = self._download(src, el.get("alt", ""))
            if filename:
                added.append(filename)
        return added

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _download(self, url: str, title: str = "", queue_hashes: set = None) -> str | None:
        """Download a file to queue_dir. Returns filename or None."""
        import requests, re, hashlib
        try:
            r = requests.get(url, timeout=20,
                             headers={"User-Agent": "Mozilla/5.0"},
                             stream=True)
            r.raise_for_status()
            # Determine extension from content-type or URL
            ct  = r.headers.get("content-type", "")
            ext = _ext_from_content_type(ct) or Path(url.split("?")[0]).suffix.lower()
            if not ext or ext not in {".jpg", ".jpeg", ".png", ".gif",
                                      ".webp", ".mp4", ".mov"}:
                return None

            data = b"".join(r.iter_content(65536))

            # Content hash dedup — skip if identical file already in queue
            content_hash = hashlib.md5(data).hexdigest()
            if queue_hashes and content_hash in queue_hashes:
                return None
            if queue_hashes is not None:
                queue_hashes.add(content_hash)

            # Build filename from title or URL hash
            if title:
                slug = re.sub(r"[^\w\s-]", "", title.lower())
                slug = re.sub(r"[\s-]+", "_", slug).strip("_")[:60]
            else:
                slug = content_hash[:12]

            dest = self.queue_dir / f"{slug}{ext}"
            counter = 1
            while dest.exists():
                dest = self.queue_dir / f"{slug}_{counter}{ext}"
                counter += 1

            dest.write_bytes(data)
            return dest.name
        except Exception:
            return None


def _ext_from_content_type(ct: str) -> str:
    mapping = {
        "image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif",
        "image/webp": ".webp", "video/mp4": ".mp4", "video/quicktime": ".mov",
    }
    for k, v in mapping.items():
        if k in ct:
            return v
    return ""
