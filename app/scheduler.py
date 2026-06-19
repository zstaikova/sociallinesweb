"""
Scheduled post store (SQLite) + background dispatch runner.
All times are local-time ISO strings (no timezone), matching
what <input type="datetime-local"> produces.
"""
import builtins
import copy
import json
import logging
import sqlite3
import sys
import threading
import uuid
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("socialline")

# ── Thread-local stdout capture ───────────────────────────────────────────────
# contextlib.redirect_stdout patches the global sys.stdout and is not reliable
# in background threads. Instead we patch builtins.print once and use a
# thread-local list to accumulate output per publish call.

_capture = threading.local()
_original_print = builtins.print

def _capturing_print(*args, sep=" ", end="\n", file=None, flush=False):
    lines = getattr(_capture, "lines", None)
    if lines is not None and file is None:
        lines.append(sep.join(str(a) for a in args))
    _original_print(*args, sep=sep, end=end, file=file, flush=flush)

builtins.print = _capturing_print


class ScheduleStore:
    def __init__(self, db_path: Path):
        self.db_path = str(db_path)
        self._init_db()

    # ── internal ────────────────────────────────────────────────────

    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS scheduled_posts (
                    id            TEXT PRIMARY KEY,
                    filename      TEXT NOT NULL,
                    captions      TEXT NOT NULL,
                    platforms     TEXT NOT NULL,
                    platform_opts TEXT NOT NULL,
                    scheduled_at  TEXT NOT NULL,
                    status        TEXT NOT NULL DEFAULT 'pending',
                    result        TEXT,
                    created_at    TEXT NOT NULL,
                    format_map    TEXT,
                    retry_count   INT NOT NULL DEFAULT 0,
                    original_at   TEXT
                )
            """)
            cols = [r[1] for r in conn.execute("PRAGMA table_info(scheduled_posts)").fetchall()]
            if "format_map" not in cols:
                conn.execute("ALTER TABLE scheduled_posts ADD COLUMN format_map TEXT")
            if "retry_count" not in cols:
                conn.execute("ALTER TABLE scheduled_posts ADD COLUMN retry_count INT NOT NULL DEFAULT 0")
            if "original_at" not in cols:
                conn.execute("ALTER TABLE scheduled_posts ADD COLUMN original_at TEXT")

    def _parse(self, row) -> dict:
        d = dict(row)
        d["captions"]      = json.loads(d["captions"])
        d["platforms"]     = json.loads(d["platforms"])
        d["platform_opts"] = json.loads(d["platform_opts"])
        d["format_map"]    = json.loads(d["format_map"]) if d.get("format_map") else {}
        d["retry_count"]   = d.get("retry_count") or 0
        if d["result"]:
            d["result"] = json.loads(d["result"])
        return d

    # ── public API ───────────────────────────────────────────────────

    def add(self, filename: str, captions: dict, platforms: list,
            platform_options: dict, scheduled_at: str,
            format_map: dict = None) -> str:
        """
        scheduled_at: local-time ISO string, e.g. '2026-05-01T14:30'
        format_map: optional dict mapping format name → filename,
                    e.g. {"vertical": "abc_vertical.mp4", "square": "abc_square.mp4"}
        Returns the new post id.
        """
        post_id = str(uuid.uuid4())[:8]
        now = datetime.now().isoformat(timespec="seconds")
        scheduled_at = datetime.fromisoformat(scheduled_at).isoformat(timespec="seconds")
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO scheduled_posts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (post_id, filename, json.dumps(captions), json.dumps(platforms),
                 json.dumps(platform_options), scheduled_at, "pending", None, now,
                 json.dumps(format_map) if format_map else None, 0, None)
            )
        return post_id

    def reschedule_for_retry(self, post_id: str, retry_at: datetime,
                              retry_count: int, platforms: list,
                              partial_result: dict):
        """Re-queue a failed post for automatic retry on specific platforms."""
        with self._conn() as conn:
            # Preserve original_at on first retry
            row = conn.execute("SELECT original_at, scheduled_at FROM scheduled_posts WHERE id=?",
                               (post_id,)).fetchone()
            original_at = (row["original_at"] or row["scheduled_at"]) if row else None
            conn.execute(
                """UPDATE scheduled_posts
                   SET status='pending', scheduled_at=?, retry_count=?,
                       platforms=?, result=?, original_at=?
                   WHERE id=?""",
                (retry_at.isoformat(timespec="seconds"), retry_count,
                 json.dumps(platforms), json.dumps(partial_result),
                 original_at, post_id)
            )

    def list_all(self, status: str = None) -> list:
        with self._conn() as conn:
            if status == "failed":
                # "failed" filter includes partial posts, excludes dismissed
                rows = conn.execute(
                    "SELECT * FROM scheduled_posts WHERE status IN ('failed','partial') ORDER BY scheduled_at",
                ).fetchall()
            elif status:
                rows = conn.execute(
                    "SELECT * FROM scheduled_posts WHERE status=? ORDER BY scheduled_at",
                    (status,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM scheduled_posts ORDER BY scheduled_at"
                ).fetchall()
        return [self._parse(r) for r in rows]

    def get_due(self) -> list:
        """Pending posts whose scheduled_at <= now."""
        now = datetime.now().isoformat(timespec="seconds")
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM scheduled_posts "
                "WHERE status='pending' AND scheduled_at<=? ORDER BY scheduled_at",
                (now,)
            ).fetchall()
        return [self._parse(r) for r in rows]

    def update_status(self, post_id: str, status: str, result: dict = None):
        with self._conn() as conn:
            conn.execute(
                "UPDATE scheduled_posts SET status=?, result=? WHERE id=?",
                (status, json.dumps(result) if result is not None else None, post_id)
            )

    def get_by_id(self, post_id: str) -> "dict | None":
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM scheduled_posts WHERE id=?", (post_id,)
            ).fetchone()
        return self._parse(row) if row else None

    def rename_file(self, old_fn: str, new_fn: str):
        """Update pending scheduled posts that reference old_fn in filename or format_map."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, filename, format_map FROM scheduled_posts WHERE status='pending'"
            ).fetchall()
            for row in rows:
                fm = json.loads(row["format_map"]) if row["format_map"] else {}
                new_fm = {k: (new_fn if v == old_fn else v) for k, v in fm.items()}
                new_filename = new_fn if row["filename"] == old_fn else row["filename"]
                if new_filename != row["filename"] or new_fm != fm:
                    conn.execute(
                        "UPDATE scheduled_posts SET filename=?, format_map=? WHERE id=?",
                        (new_filename, json.dumps(new_fm), row["id"])
                    )

    def delete(self, post_id: str):
        with self._conn() as conn:
            conn.execute("DELETE FROM scheduled_posts WHERE id=?", (post_id,))

    def stats(self) -> dict:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM scheduled_posts GROUP BY status"
            ).fetchall()
        raw = {r["status"]: r["cnt"] for r in rows}
        # Merge partial into failed count for display, exclude dismissed
        raw["failed"] = raw.get("failed", 0) + raw.get("partial", 0)
        raw.pop("dismissed", None)
        return raw


# ── Helpers ──────────────────────────────────────────────────────────────────

def _is_retriable(detail: str) -> bool:
    """True for transient errors that are worth auto-retrying."""
    if not detail:
        return False
    d = str(detail)
    # Facebook/Instagram intermittent OAuthException 190 (page-level API instability)
    if '"code":190' in d or ("OAuthException" in d and "190" in d):
        return True
    # HTTP server errors
    if any(code in d for code in ['"500"', '"502"', '"503"', '"504"', ':500', ':502', ':503', ':504']):
        return True
    # Network / timeout
    if any(w in d.lower() for w in ['timeout', 'connection error', 'temporarily unavailable']):
        return True
    return False


def _make_logger(brand_dir: Path):
    """Return a log function that writes to brand posting.log and stdout."""
    log_file = brand_dir / "posting.log"

    def log(msg: str):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"{ts} {msg}"
        print(f"  {line}")
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

    return log


# ── Background runner ────────────────────────────────────────────────────────

def _run_due_posts(store: ScheduleStore, queue_dir: Path, accounts_file: Path = None,
                   brand_id: str = None):
    """Called every 30 s by the background thread to fire due posts."""
    import sys
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))

    due = store.get_due()
    if not due:
        return

    for post in due:
        _publish_scheduled(store, post, queue_dir, root, accounts_file=accounts_file,
                           brand_id=brand_id)


def _publish_scheduled(store: ScheduleStore, post: dict, queue_dir: Path, root: Path,
                       accounts_file: Path = None, brand_id: str = None):
    """Publish one scheduled post and update its status."""
    from pipeline.core.content_item import ContentItem
    from pipeline.core.content_store import ContentStore
    from pipeline.core.accounts import AccountStore
    from pipeline.brands.famjam.config import create_pipeline

    filename         = post["filename"]
    captions         = post["captions"]
    platforms        = post["platforms"]
    platform_options = post["platform_opts"]
    format_map       = post.get("format_map") or {}

    # Platform → format routing
    _PLATFORM_FORMAT = {
        "tiktok": "vertical", "instagram_reel": "vertical", "youtube_short": "vertical",
        "facebook": "square", "instagram_post": "square",
        "linkedin": "horizontal",
        "twitter": "wide", "x": "wide",
    }

    image_path = queue_dir / filename
    if not image_path.exists():
        store.update_status(post["id"], "failed", {"error": "File not found"})
        return

    try:
        content_store = ContentStore(queue_dir.parent / "content.db")
        acct_store    = AccountStore(accounts_file, brand_id=brand_id) if accounts_file else AccountStore()

        # Pre-flight: record which platforms have no credentials so the result is explicit
        no_creds = [p for p in platforms if not acct_store.get_credentials(p)]

        # Pre-flight token health check for Facebook/Instagram
        import requests as _req
        for _plat in ("facebook", "instagram"):
            if _plat not in platforms:
                continue
            _creds = acct_store.get_credentials(_plat)
            if not _creds:
                continue
            _tok = _creds.get("FACEBOOK_PAGE_ACCESS_TOKEN") or _creds.get("INSTAGRAM_ACCESS_TOKEN", "")
            _pid = _creds.get("FACEBOOK_PAGE_ID") or _creds.get("INSTAGRAM_ACCOUNT_ID", "")
            if _tok and _pid:
                _r = _req.get(f"https://graph.facebook.com/v19.0/{_pid}",
                              params={"fields": "id", "access_token": _tok}, timeout=8)
                if not _r.ok:
                    _err = _r.json().get("error", {})
                    logger.warning("PRE-FLIGHT %s token invalid (code %s): %s",
                                   _plat, _err.get("code"), _err.get("message", _r.text[:200]))

        pipeline = create_pipeline(platforms=platforms, store=content_store,
                                   account_store=acct_store)

        if not pipeline.platforms:
            missing = ", ".join(no_creds) if no_creds else "all"
            store.update_status(post["id"], "failed",
                                {"error": f"No credentials loaded for: {missing}"})
            return

        item = ContentItem(
            source_url=f"file://{image_path.resolve()}",
            source_platform="local",
            media_path=image_path,
            caption=next((v for v in captions.values() if v), image_path.stem),
            tags=[],
        )

        if not content_store.exists(item.id):
            content_store.save(item)

        for transformer in pipeline.shared_transformers:
            item = transformer.transform(item)

        # Seed results: platforms with no credentials get an immediate skip entry
        results = {p: {"status": "skipped", "detail": "No credentials"} for p in no_creds}

        for platform_config in pipeline.platforms:
            pname = platform_config.name
            try:
                # Use format-specific file if available
                fmt = _PLATFORM_FORMAT.get(pname)
                fmt_filename = format_map.get(fmt) if fmt else None
                fmt_path = queue_dir / fmt_filename if fmt_filename else image_path
                if not fmt_path.exists():
                    fmt_path = image_path  # fallback to primary

                platform_item = copy.deepcopy(item)
                platform_item.source_url = f"file://{fmt_path.resolve()}"
                platform_item.media_path = fmt_path
                platform_item.caption = captions.get(pname) or item.caption
                if pname in platform_options:
                    platform_item.metadata.update(platform_options[pname])

                for transformer in platform_config.transformers:
                    platform_item = transformer.transform(platform_item)

                _capture.lines = []
                try:
                    success = platform_config.publisher.publish(platform_item)
                finally:
                    output = "\n".join(_capture.lines).strip()
                    _capture.lines = None

                if success:
                    post_id = (platform_item.metadata.get(f"{pname}_post_id")
                               or platform_item.metadata.get(f"{pname}_publish_id"))
                    content_store.mark_posted(item.id, pname, post_id)
                    results[pname] = {"status": "posted", "post_id": post_id}
                else:
                    content_store.mark_failed(item.id, pname)
                    results[pname] = {"status": "failed", "detail": output or "unknown"}
            except Exception as e:
                results[pname] = {"status": "error", "detail": str(e)}

        posted  = sum(1 for r in results.values() if r.get("status") == "posted")
        failed  = sum(1 for r in results.values() if r.get("status") in ("failed", "error"))
        if posted == 0:
            final_status = "failed"
        elif failed == 0:
            final_status = "published"
        else:
            final_status = "partial"

        log = _make_logger(queue_dir.parent)
        log(f"[post] {post['id']} {filename} → {final_status} "
            f"(posted={posted} failed={failed} retry={post.get('retry_count',0)})")
        for pname, r in results.items():
            if isinstance(r, dict):
                s = r.get("status", "?")
                detail = r.get("post_id") or r.get("detail") or r.get("error") or ""
                log(f"  {pname}: {s}" + (f" — {str(detail)[:120]}" if detail else ""))

        # Auto-retry on transient failures
        _MAX_AUTO_RETRIES = 3
        _RETRY_HOURS      = 1
        retry_count = post.get("retry_count", 0)
        if final_status in ("failed", "partial") and retry_count < _MAX_AUTO_RETRIES:
            failed_platforms = [
                p for p, r in results.items()
                if isinstance(r, dict) and r.get("status") in ("failed", "error")
                and _is_retriable(r.get("detail", "") or r.get("error", ""))
            ]
            if failed_platforms:
                from datetime import timedelta
                retry_at = datetime.now() + timedelta(hours=_RETRY_HOURS)
                store.reschedule_for_retry(post["id"], retry_at,
                                           retry_count + 1, failed_platforms, results)
                log(f"  ↺ retry {retry_count+1}/{_MAX_AUTO_RETRIES} scheduled for "
                    f"{retry_at.strftime('%Y-%m-%d %H:%M')} — platforms: {', '.join(failed_platforms)}")
                return  # don't mark failed yet

        store.update_status(post["id"], final_status, results)
        if final_status in ("failed", "partial") and retry_count >= _MAX_AUTO_RETRIES:
            log(f"  ✗ gave up after {retry_count} auto-retries")

        # Move files to posted folder on full success
        if final_status == "published":
            posted_dir = queue_dir.parent / "posted"
            posted_dir.mkdir(exist_ok=True)
            files_to_move = {filename} | set(format_map.values())
            for fname in files_to_move:
                src = queue_dir / fname
                if src.exists():
                    try:
                        src.rename(posted_dir / fname)
                    except Exception:
                        pass

    except Exception as e:
        store.update_status(post["id"], "failed", {"error": str(e)})


def start_scheduler(get_stores_fn, interval: int = 30):
    """
    Start the background scheduler thread. Safe to call multiple times.

    get_stores_fn: callable returning list of (sched_store, queue_dir, accounts_file, brand_id)
                   Called on every tick so new brands are picked up automatically.
    """
    def _loop():
        import time
        while True:
            try:
                for sched_store, queue_dir, accounts_file, brand_id in get_stores_fn():
                    _run_due_posts(sched_store, queue_dir, accounts_file=accounts_file,
                                   brand_id=brand_id)
            except Exception:
                pass
            time.sleep(interval)

    t = threading.Thread(target=_loop, daemon=True, name="scheduler")
    t.start()
    return t


def publish_now(store: ScheduleStore, post_id: str, queue_dir: Path,
                accounts_file: Path = None, brand_id: str = None,
                platforms: list = None) -> "dict | None":
    """Re-run a post synchronously and return the updated result dict, or None if not found.

    platforms: if given, override the post's platform list (for partial retries).
    """
    import sys
    post = store.get_by_id(post_id)
    if not post:
        return None
    if platforms:
        post = dict(post)
        post["platforms"] = platforms
    store.update_status(post_id, "pending", None)
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    _publish_scheduled(store, post, queue_dir, root,
                       accounts_file=accounts_file, brand_id=brand_id)
    updated = store.get_by_id(post_id)
    return updated


# ── Auto-pull runner ─────────────────────────────────────────────────────────

_SCHEDULE_INTERVALS = {
    "hourly": 3600,
    "6h":     6 * 3600,
    "daily":  24 * 3600,
    "weekly": 7 * 24 * 3600,
}


def start_source_puller(get_resources_fn, interval: int = 60):
    """
    Background thread that fires source pulls on their configured schedule,
    then auto-fills posting slots if on_upload trigger is enabled.

    get_resources_fn: callable returning list of
        (source_store, pull_engine, sched_store, queue_dir, sched_cfg_file)
    Checked every `interval` seconds (default 60).
    """
    def _loop():
        import time
        while True:
            try:
                for source_store, pull_engine, sched_store, queue_dir, sched_cfg_file in get_resources_fn():
                    def _load_sched(f=sched_cfg_file):
                        return _load_sched_file(f)
                    _run_due_pulls(source_store, pull_engine, sched_store,
                                   queue_dir, _load_sched)
            except Exception:
                pass
            time.sleep(interval)

    t = threading.Thread(target=_loop, daemon=True, name="source-puller")
    t.start()
    return t


def _load_sched_file(path: Path) -> dict:
    """Load default_schedule.json from a brand's directory."""
    _defaults = {
        "enabled": False,
        "days_of_week": list(range(7)),
        "times": [],
        "days_ahead": 7,
        "platforms": [],
        "trigger": "manual",
    }
    try:
        if path.exists():
            return {**_defaults, **__import__("json").loads(path.read_text())}
    except Exception:
        pass
    return _defaults


def _run_due_pulls(source_store, pull_engine, sched_store: ScheduleStore,
                   queue_dir: Path, load_default_sched):
    from datetime import datetime, timedelta

    now = datetime.now()
    sources = source_store.list_all()

    for source in sources:
        if not source.get("enabled"):
            continue
        schedule = source.get("schedule")
        if not schedule or schedule not in _SCHEDULE_INTERVALS:
            continue

        interval_s = _SCHEDULE_INTERVALS[schedule]
        last_pulled = source.get("last_pulled")

        if last_pulled:
            try:
                last_dt = datetime.fromisoformat(last_pulled)
            except ValueError:
                last_dt = datetime.min
        else:
            last_dt = datetime.min

        if (now - last_dt).total_seconds() < interval_s:
            continue

        # Skip if queue already has enough items
        queue_count = len(list(queue_dir.glob("*"))) if queue_dir.exists() else 0
        if queue_count >= 10:
            continue

        # Due — pull it
        try:
            added = pull_engine.pull(source)
            source_store.log_pull(source["id"], len(added))
            print(f"  [auto-pull] {source['name']}: {len(added)} new item(s)")
        except Exception as e:
            print(f"  [auto-pull] {source['name']} failed: {e}")
            continue

        if not added:
            continue

        # Auto-schedule new items if on_upload trigger is set
        cfg = load_default_sched()
        if not (cfg.get("enabled") and cfg.get("trigger") == "on_upload" and cfg.get("platforms")):
            continue

        times       = cfg.get("times", [])
        days_of_week = cfg.get("days_of_week", list(range(7)))
        days_ahead  = int(cfg.get("days_ahead", 7))
        platforms   = cfg.get("platforms", [])
        if not times:
            continue

        # Build free slots
        slots = []
        for day_offset in range(days_ahead + 1):
            day = now.date() + timedelta(days=day_offset)
            if day.weekday() not in days_of_week:
                continue
            from datetime import datetime as dt2
            for t in times:
                h, m = map(int, t.split(":"))
                slot_dt = dt2(day.year, day.month, day.day, h, m)
                if slot_dt > now:
                    slots.append(slot_dt)

        occupied = {p["scheduled_at"][:16] for p in sched_store.list_all(status="pending")}
        free_slots = [s for s in slots if s.strftime("%Y-%m-%dT%H:%M") not in occupied]

        existing_scheduled = {p["filename"] for p in sched_store.list_all(status="pending")}
        new_items = [f for f in added if f not in existing_scheduled]

        for slot_dt, filename in zip(free_slots, new_items):
            from pathlib import Path as _Path
            captions = {p: _Path(filename).stem.replace("_", " ") for p in platforms}
            sched_store.add(filename, captions, platforms, {},
                            slot_dt.isoformat(timespec="minutes"))
            print(f"  [auto-schedule] {filename} → {slot_dt.strftime('%Y-%m-%d %H:%M')}")
