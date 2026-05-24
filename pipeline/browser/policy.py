"""
Per-platform posting policy and rate-limit enforcement for browser connectors.

Policy state is persisted to data/browser_policy.json (unencrypted — no secrets here).
"""
import json
import threading
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
POLICY_FILE = ROOT / "data" / "browser_policy.json"

# Per-platform limits: (max_posts, window_days)
_DEFAULTS: dict[str, dict] = {
    "medium":         {"max_posts": 3, "window_days": 7,  "min_gap_hours": 0},
    "quora":          {"max_posts": 1, "window_days": 1,  "min_gap_hours": 0},
    "tpt":            {"max_posts": 5, "window_days": 7,  "min_gap_hours": 0},
    "substack_notes": {"max_posts": 3, "window_days": 1,  "min_gap_hours": 2},
}


class PolicyViolation(Exception):
    pass


class SafePolicy:
    """
    Thread-safe rate-limit tracker.  Call check_and_record() before posting;
    it raises PolicyViolation if the platform is over its limit or killed.
    """

    def __init__(self, policy_file: Path = POLICY_FILE):
        self._file = policy_file
        self._lock = threading.Lock()
        self._state: dict = self._load()

    # ── persistence ────────────────────────────────────────────────────────

    def _load(self) -> dict:
        if self._file.exists():
            try:
                return json.loads(self._file.read_text())
            except Exception:
                pass
        return {}

    def _save(self):
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._file.write_text(json.dumps(self._state, indent=2))

    # ── helpers ─────────────────────────────────────────────────────────────

    def _platform_state(self, platform: str) -> dict:
        if platform not in self._state:
            self._state[platform] = {"posts": [], "killed": False}
        return self._state[platform]

    def _window_posts(self, posts: list[str], window_days: int) -> list[str]:
        cutoff = (datetime.utcnow() - timedelta(days=window_days)).isoformat()
        return [p for p in posts if p >= cutoff]

    # ── public API ──────────────────────────────────────────────────────────

    def check_and_record(self, platform: str):
        """
        Check rate limit then record a new post timestamp.
        Raises PolicyViolation with a human-readable message if not allowed.
        Must be called immediately before posting.
        """
        cfg = _DEFAULTS.get(platform, {"max_posts": 10, "window_days": 1, "min_gap_hours": 0})
        with self._lock:
            st = self._platform_state(platform)

            if st["killed"]:
                raise PolicyViolation(f"{platform}: posting is disabled (kill switch active)")

            recent = self._window_posts(st["posts"], cfg["window_days"])
            if len(recent) >= cfg["max_posts"]:
                raise PolicyViolation(
                    f"{platform}: limit reached ({cfg['max_posts']} posts "
                    f"per {cfg['window_days']}d). Last post: {recent[-1]}"
                )

            if cfg["min_gap_hours"] and recent:
                last = datetime.fromisoformat(recent[-1])
                gap = datetime.utcnow() - last
                required = timedelta(hours=cfg["min_gap_hours"])
                if gap < required:
                    wait = int((required - gap).total_seconds() / 60)
                    raise PolicyViolation(
                        f"{platform}: minimum gap not met. Try again in {wait} minutes."
                    )

            st["posts"] = recent + [datetime.utcnow().isoformat()]
            self._save()

    def kill(self, platform: str):
        with self._lock:
            self._platform_state(platform)["killed"] = True
            self._save()

    def unkill(self, platform: str):
        with self._lock:
            self._platform_state(platform)["killed"] = False
            self._save()

    def status(self, platform: str) -> dict:
        cfg = _DEFAULTS.get(platform, {"max_posts": 10, "window_days": 1, "min_gap_hours": 0})
        with self._lock:
            st = self._platform_state(platform)
            recent = self._window_posts(st["posts"], cfg["window_days"])
            return {
                "platform":   platform,
                "killed":     st["killed"],
                "posts_used": len(recent),
                "posts_max":  cfg["max_posts"],
                "window_days": cfg["window_days"],
                "recent":     recent,
            }

    def all_status(self) -> list[dict]:
        return [self.status(p) for p in _DEFAULTS]
