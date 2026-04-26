from pathlib import Path

from pipeline.core.base_source import BaseSource
from pipeline.core.content_item import ContentItem

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}


class LocalFolderSource(BaseSource):
    """
    Picks up image files from a local folder.
    Caption defaults to the filename stem (e.g. 'funny_kid_moment.jpg' → 'funny kid moment').
    Processed files are left in place — the content store prevents re-posting.
    """

    def __init__(self, folder: str | Path, caption_from_filename: bool = True):
        self.folder = Path(folder)
        self.caption_from_filename = caption_from_filename

    def fetch(self, limit: int = 10) -> list:
        if not self.folder.exists():
            print(f"  LocalFolderSource: folder not found: {self.folder}")
            return []

        images = [
            p for p in sorted(self.folder.iterdir())
            if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
        ]

        items = []
        for path in images[:limit]:
            caption = path.stem.replace("_", " ").replace("-", " ") if self.caption_from_filename else ""
            item = ContentItem(
                source_url=f"file://{path.resolve()}",
                source_platform="local",
                media_path=path,
                caption=caption,
                tags=["local"],
                metadata={"filename": path.name},
            )
            items.append(item)

        print(f"  LocalFolderSource: found {len(items)} image(s) in {self.folder}")
        return items
