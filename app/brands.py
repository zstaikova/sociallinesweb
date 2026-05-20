"""
Brand store — each brand is an isolated content workspace.
Users own 1-3 brands. Admin can see all brands.

Data layout per brand:
    data/brands/{brand_id}/
        queue/
        posted/
        accounts.enc
        schedule.db
        sources.db
        default_schedule.json
        dedup_log.json
"""
import shutil
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path


BRAND_DATA_ROOT: Path = None   # set by server on startup


class BrandStore:
    MAX_BRANDS_PER_USER = 3

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
                CREATE TABLE IF NOT EXISTS brands (
                    id             TEXT PRIMARY KEY,
                    slug           TEXT UNIQUE NOT NULL,
                    name           TEXT NOT NULL,
                    owner_username TEXT NOT NULL,
                    created_at     TEXT NOT NULL
                )
            """)

    @staticmethod
    def _parse(row) -> dict:
        return dict(row)

    # ── write ────────────────────────────────────────────────────────────────

    def create(self, slug: str, name: str, owner_username: str) -> dict:
        existing = self.list_for_user(owner_username)
        if len(existing) >= self.MAX_BRANDS_PER_USER:
            raise ValueError(
                f"User '{owner_username}' already has {self.MAX_BRANDS_PER_USER} brands (maximum)"
            )
        if self.get_by_slug(slug):
            raise ValueError(f"Brand slug '{slug}' is already taken")
        brand_id = str(uuid.uuid4())[:8]
        now = datetime.now().isoformat(timespec="seconds")
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO brands VALUES (?,?,?,?,?)",
                (brand_id, slug, name, owner_username, now),
            )
        # Create the brand directory
        if BRAND_DATA_ROOT:
            (BRAND_DATA_ROOT / brand_id).mkdir(parents=True, exist_ok=True)
        return self.get(brand_id)

    def update(self, brand_id: str, name: str):
        with self._conn() as conn:
            conn.execute("UPDATE brands SET name=? WHERE id=?", (name, brand_id))
        return self.get(brand_id)

    def delete(self, brand_id: str):
        with self._conn() as conn:
            conn.execute("DELETE FROM brands WHERE id=?", (brand_id,))

    # ── read ─────────────────────────────────────────────────────────────────

    def get(self, brand_id: str) -> "dict | None":
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM brands WHERE id=?", (brand_id,)
            ).fetchone()
        return self._parse(row) if row else None

    def get_by_slug(self, slug: str) -> "dict | None":
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM brands WHERE slug=?", (slug,)
            ).fetchone()
        return self._parse(row) if row else None

    def list_for_user(self, username: str) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM brands WHERE owner_username=? ORDER BY created_at",
                (username,),
            ).fetchall()
        return [self._parse(r) for r in rows]

    def list_all(self) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM brands ORDER BY owner_username, created_at"
            ).fetchall()
        return [self._parse(r) for r in rows]


# ── One-time migration ────────────────────────────────────────────────────────

def migrate_famjam(brand_store: BrandStore, data_dir: Path,
                   famjam_dir: Path, root_accounts_enc: Path):
    """
    Run once on first startup after the refactor.
    Creates the famjam brand owned by zstaikova and moves all existing data into it.
    """
    if brand_store.get_by_slug("famjam"):
        return  # already migrated

    print("  [migration] creating famjam brand …")
    brand = brand_store.create("famjam", "FamJam Memes", "zstaikova")
    bid   = brand["id"]
    bdir  = BRAND_DATA_ROOT / bid
    bdir.mkdir(parents=True, exist_ok=True)

    def _copy(src: Path, dst: Path):
        if not src.exists():
            return
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        print(f"  [migration]   {src.name} → brands/{bid}/{dst.relative_to(bdir)}")

    _copy(famjam_dir / "queue",          bdir / "queue")
    _copy(famjam_dir / "posted",         bdir / "posted")
    _copy(famjam_dir / "schedule.db",    bdir / "schedule.db")
    _copy(famjam_dir / "dedup_log.json", bdir / "dedup_log.json")
    _copy(root_accounts_enc,             bdir / "accounts.enc")
    _copy(data_dir / "sources.db",       bdir / "sources.db")
    _copy(data_dir / "default_schedule.json", bdir / "default_schedule.json")

    # Create empty socialline brand for zstaikova
    if not brand_store.get_by_slug("socialline"):
        brand_store.create("socialline", "Socialline", "zstaikova")
        print("  [migration] created socialline brand for zstaikova")

    print(f"  [migration] done — famjam brand id: {bid}")
