import copy
from dataclasses import dataclass, field

from .content_item import ContentStatus
from .content_store import ContentStore


@dataclass
class PlatformConfig:
    publisher: object  # BasePublisher
    transformers: list = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.publisher.__class__.__name__.replace("Publisher", "").lower()


class Pipeline:
    def __init__(self, source, shared_transformers: list, platforms: list, store: ContentStore):
        self.source = source
        self.shared_transformers = shared_transformers
        self.platforms = platforms  # list[PlatformConfig]
        self.store = store

    def run(self, limit: int = 10, dry_run: bool = False):
        print(f"Fetching up to {limit} items from {self.source.__class__.__name__}...")
        items = self.source.fetch(limit=limit)
        print(f"Found {len(items)} items")

        platform_names = [p.name for p in self.platforms]
        new_items = [
            i for i in items
            if not all(self.store.already_posted_to(i.id, p) for p in platform_names)
        ]
        print(f"{len(new_items)} not yet posted to all platforms")

        for item in new_items:
            if not self.store.exists(item.id):
                self.store.save(item)

            # Apply shared transforms once (e.g. watermark for image platforms)
            for transformer in self.shared_transformers:
                item = transformer.transform(item)

            if dry_run:
                print(f"[dry-run] Would post: {item.caption[:60]!r}")
                continue

            for platform in self.platforms:
                if self.store.already_posted_to(item.id, platform.name):
                    print(f"  Skipping {platform.name} — already posted")
                    continue

                # Each platform gets its own copy so transforms don't bleed across
                platform_item = copy.deepcopy(item)
                for transformer in platform.transformers:
                    platform_item = transformer.transform(platform_item)

                print(f"  Publishing to {platform.name}: {platform_item.caption[:60]!r}")
                success = platform.publisher.publish(platform_item)

                if success:
                    post_id = platform_item.metadata.get(f"{platform.name}_post_id")
                    self.store.mark_posted(item.id, platform.name, post_id)
                    print(f"  Posted. post_id={post_id}")
                else:
                    self.store.mark_failed(item.id, platform.name)
                    print(f"  Failed.")

        stats = self.store.stats()
        print(f"\nStore: {stats['total']} total | {stats['posted']} posted | {stats['failed']} failed")
