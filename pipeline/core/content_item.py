from dataclasses import dataclass, field
from pathlib import Path
from enum import Enum
from datetime import datetime
from typing import Optional
import hashlib


class ContentStatus(str, Enum):
    DISCOVERED = "discovered"
    READY = "ready"
    POSTED = "posted"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass
class ContentItem:
    source_url: str
    source_platform: str        # "reddit", "imgflip", etc.
    media_path: Optional[Path] = None
    caption: str = ""
    tags: list = field(default_factory=list)
    attribution: str = ""
    status: ContentStatus = ContentStatus.DISCOVERED
    metadata: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)
    posted_at: Optional[datetime] = None

    @property
    def id(self) -> str:
        return hashlib.md5(self.source_url.encode()).hexdigest()
