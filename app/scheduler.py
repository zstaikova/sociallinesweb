"""
Scheduled post store (SQLite) + background dispatch runner.
All times are local-time ISO strings (no timezone), matching
what <input type="datetime-local"> produces.
"""
import copy
import io
import json
import sqlite3
import threading
import uuid
import contextlib
from datetime import datetime
from pathlib import Path


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
                    created_at    TEXT NOT NULL
                )
            """)

    def _parse(self, row) -> dict:
        d = dict(row)
        d["captions"]      = json.loads(d["captions"])
        d["platforms"]     = json.loads(d["platforms"])
        d["platform_opts"] = json.loads(d["platform_opts"])
        if d["result"]:
            d["result"] = json.loads(d["result"])
        return d

    # ── public API ───────────────────────────────────────────────────

    def add(self, filename: str, captions: dict, platforms: list,
            platform_options: dict, scheduled_at: str) -> str:
        """
        scheduled_at: local-time ISO string, e.g. '2026-05-01T14:30'
        Returns the new post id.
        """
        post_id = str(uuid.uuid4())[:8]
        now = datetime.now().isoformat(timespec="seconds")
        # Normalise to seconds precision
        scheduled_at = datetime.fromisoformat(scheduled_at).isoformat(timespec="seconds")
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO scheduled_posts VALUES (?,?,?,?,?,?,?,?,?)",
                (post_id, filename, json.dumps(captions), json.dumps(platforms),
                 json.dumps(platform_options), scheduled_at, "pending", None, now)
            )
        return post_id

    def list_all(self, status: str = None) -> list:
        with self._conn() as conn:
            if status:
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

    def delete(self, post_id: str):
        with self._conn() as conn:
            conn.execute("DELETE FROM scheduled_posts WHERE id=?", (post_id,))


# ── Background runner ────────────────────────────────────────────────────────

def _run_due_posts(store: ScheduleStore, queue_dir: Path):
    """Called every 30 s by the background thread to fire due posts."""
    import sys
    root = queue_dir.parent.parent  # famjammemes/../ = project root
    sys.path.insert(0, str(root))

    due = store.get_due()
    if not due:
        return

    for post in due:
        _publish_scheduled(store, post, queue_dir, root)


def _publish_scheduled(store: ScheduleStore, post: dict, queue_dir: Path, root: Path):
    """Publish one scheduled post and update its status."""
    from pipeline.core.content_item import ContentItem
    from pipeline.core.content_store import ContentStore
    from pipeline.brands.famjam.config import create_pipeline

    filename  = post["filename"]
    captions  = post["captions"]
    platforms = post["platforms"]
    platform_options = post["platform_opts"]

    image_path = queue_dir / filename
    if not image_path.exists():
        store.update_status(post["id"], "failed", {"error": "File not found"})
        return

    try:
        content_store = ContentStore()
        pipeline      = create_pipeline(platforms=platforms, store=content_store)

        item = ContentItem(
            source_url=f"file://{image_path.resolve()}",
            source_platform="local",
            media_path=image_path,
            caption=captions.get("facebook", image_path.stem),
            tags=["scheduled"],
        )

        if not content_store.exists(item.id):
            content_store.save(item)

        for transformer in pipeline.shared_transformers:
            item = transformer.transform(item)

        results = {}
        for platform_config in pipeline.platforms:
            pname = platform_config.name
            try:
                platform_item = copy.deepcopy(item)
                platform_item.caption = captions.get(pname, item.caption)
                if pname in platform_options:
                    platform_item.metadata.update(platform_options[pname])

                for transformer in platform_config.transformers:
                    platform_item = transformer.transform(platform_item)

                captured = io.StringIO()
                with contextlib.redirect_stdout(captured):
                    success = platform_config.publisher.publish(platform_item)
                output = captured.getvalue().strip()

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

        all_ok = all(r.get("status") == "posted" for r in results.values())
        store.update_status(post["id"], "published" if all_ok else "failed", results)

    except Exception as e:
        store.update_status(post["id"], "failed", {"error": str(e)})


def start_scheduler(store: ScheduleStore, queue_dir: Path, interval: int = 30):
    """Start the background scheduler thread. Safe to call multiple times."""
    def _loop():
        import time
        while True:
            try:
                _run_due_posts(store, queue_dir)
            except Exception:
                pass
            time.sleep(interval)

    t = threading.Thread(target=_loop, daemon=True, name="scheduler")
    t.start()
    return t


# ── Auto-pull runner ─────────────────────────────────────────────────────────

_SCHEDULE_INTERVALS = {
    "hourly": 3600,
    "6h":     6 * 3600,
    "daily":  24 * 3600,
    "weekly": 7 * 24 * 3600,
}


def start_source_puller(source_store, pull_engine, sched_store: ScheduleStore,
                        queue_dir: Path, load_default_sched,
                        interval: int = 60):
    """
    Background thread that fires source pulls on their configured schedule,
    then auto-fills posting slots if on_upload trigger is enabled.
    Checks every `interval` seconds (default 60).
    """
    def _loop():
        import time
        while True:
            try:
                _run_due_pulls(source_store, pull_engine, sched_store,
                               queue_dir, load_default_sched)
            except Exception:
                pass
            time.sleep(interval)

    t = threading.Thread(target=_loop, daemon=True, name="source-puller")
    t.start()
    return t


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
