import os
import re
import requests
import praw
from pathlib import Path

from pipeline.core.base_source import BaseSource
from pipeline.core.content_item import ContentItem

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}

# Words/phrases that make a post off-brand for a family audience.
# Checked against the post title (case-insensitive, whole-word where marked with \b).
BLOCKLIST = [
    # Profanity
    r"\bfuck\b", r"\bfucking\b", r"\bfucked\b", r"\bfucker\b",
    r"\bshit\b", r"\bshitty\b", r"\bshitpost\b",
    r"\bbitch\b", r"\bass\b", r"\basshole\b",
    r"\bdamn\b", r"\bcrap\b", r"\bpiss\b",
    r"\bdick\b", r"\bcock\b", r"\bpussy\b", r"\bboobs?\b",
    # Violence / dark themes
    r"\bkill\b", r"\bkilled\b", r"\bdead\b", r"\bdeath\b",
    r"\bsuicid", r"\bself.harm\b", r"\bshoot\b", r"\bgun\b",
    r"\bdrug\b", r"\bweed\b", r"\bstoned\b", r"\bhigh\b",
    r"\bferal\b",
    r"\bdrunk\b", r"\bwhiskey\b", r"\bwhisky\b", r"\bvodka\b",
    r"\bbeer\b", r"\bbooze\b", r"\balcohol\b",
    # Sexual
    r"\bsex\b", r"\bporn\b", r"\bnude\b", r"\bnaked\b", r"\bhorny\b",
    r"\bhentai\b", r"\bnsfw\b",
    # Politics / divisive
    r"\bpolitics\b", r"\bpolitical\b", r"\btrump\b", r"\bbiden\b",
    r"\belon\b", r"\bmusk\b", r"\bgop\b",
    r"\belection\b", r"\brepublican\b", r"\bdemocrat\b",
    r"\bprotest\b", r"\bwar\b", r"\bweapon\b",
    # Hate
    r"\bracist\b", r"\bracism\b", r"\bnigger\b", r"\bfaggot\b",
]

_BLOCKLIST_RE = re.compile("|".join(BLOCKLIST), re.IGNORECASE)


class RedditSource(BaseSource):
    def __init__(
        self,
        subreddits: list = None,
        sort: str = "hot",          # hot | top | new | rising
        time_filter: str = "day",   # hour | day | week | month | year (only for sort=top)
        min_score: int = 100,
        download_dir: Path = None,
        blocklist: list = None,     # extra patterns to add on top of the default BLOCKLIST
        on_candidate=None,          # callable(post_id, subreddit, title, score, url, outcome, block_match)
    ):
        self.reddit = praw.Reddit(
            client_id=os.environ["REDDIT_CLIENT_ID"],
            client_secret=os.environ["REDDIT_CLIENT_SECRET"],
            user_agent=os.getenv("REDDIT_USER_AGENT", "socialline/1.0"),
        )
        self.subreddits = subreddits or ["memes", "dankmemes"]
        self.sort = sort
        self.time_filter = time_filter
        self.min_score = min_score
        self.download_dir = Path(download_dir or "downloads/reddit")
        self.download_dir.mkdir(parents=True, exist_ok=True)

        if blocklist:
            combined = BLOCKLIST + blocklist
            self._blocklist_re = re.compile("|".join(combined), re.IGNORECASE)
        else:
            self._blocklist_re = _BLOCKLIST_RE
        self.on_candidate = on_candidate

    def fetch(self, limit: int = 10) -> list:
        items = []
        per_sub = max(1, limit // len(self.subreddits))

        for sub_name in self.subreddits:
            subreddit = self.reddit.subreddit(sub_name)

            if self.sort == "top":
                posts = subreddit.top(time_filter=self.time_filter, limit=per_sub * 3)
            elif self.sort == "hot":
                posts = subreddit.hot(limit=per_sub * 3)
            elif self.sort == "new":
                posts = subreddit.new(limit=per_sub * 3)
            elif self.sort == "rising":
                posts = subreddit.rising(limit=per_sub * 3)
            else:
                posts = subreddit.hot(limit=per_sub * 3)

            count = 0
            for post in posts:
                if count >= per_sub:
                    break

                def _log(outcome, block_match=None):
                    if self.on_candidate:
                        self.on_candidate(
                            post.id, sub_name, post.title, post.score,
                            post.url, outcome, block_match,
                        )

                if post.score < self.min_score:
                    _log("low_score")
                    continue
                if not self._is_image_url(post.url):
                    _log("no_image")
                    continue
                if post.over_18:
                    _log("nsfw")
                    continue

                block_match = self._block_match(post.title)
                if block_match:
                    print(f"  Blocked ({block_match}): {post.title[:80]}")
                    _log("blocked", block_match)
                    continue

                item = ContentItem(
                    source_url=post.url,
                    source_platform="reddit",
                    caption=post.title,
                    tags=[sub_name],
                    attribution=f"u/{post.author.name if post.author else 'unknown'} on r/{post.subreddit.display_name}",
                    metadata={
                        "score": post.score,
                        "post_id": post.id,
                        "post_url": f"https://reddit.com{post.permalink}",
                        "subreddit": post.subreddit.display_name,
                    },
                )

                media_path = self._download(post.url, item.id)
                if media_path:
                    item.media_path = media_path
                    items.append(item)
                    _log("accepted")
                    count += 1
                else:
                    _log("download_failed")

        return items[:limit]

    def _block_match(self, title: str) -> str | None:
        m = self._blocklist_re.search(title)
        return m.group(0) if m else None

    def _is_image_url(self, url: str) -> bool:
        path = url.split("?")[0].lower()
        return any(path.endswith(ext) for ext in IMAGE_EXTENSIONS)

    def _download(self, url: str, item_id: str) -> Path | None:
        try:
            ext = Path(url.split("?")[0]).suffix or ".jpg"
            if ext.lower() not in IMAGE_EXTENSIONS:
                ext = ".jpg"
            dest = self.download_dir / f"{item_id}{ext}"
            if dest.exists():
                return dest
            resp = requests.get(url, timeout=15, headers={"User-Agent": "socialline/1.0"})
            resp.raise_for_status()
            dest.write_bytes(resp.content)
            return dest
        except Exception as e:
            print(f"  Download failed {url}: {e}")
            return None
