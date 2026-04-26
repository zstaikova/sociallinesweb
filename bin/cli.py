#!/usr/bin/env python3
"""
socialline — multi-platform content pipeline
"""
import sys
from pathlib import Path

# Allow running from any directory
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import argparse
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")


def cmd_run(args):
    from pipeline.core.content_store import ContentStore
    from pipeline.core.content_item import ContentItem
    from pipeline.brands.famjam.config import create_pipeline

    store = ContentStore()

    # --- Local folder source ---
    if args.source == "folder":
        if not args.folder:
            print("--folder is required when --source folder")
            sys.exit(1)
        from pipeline.themes.memes.sources.local_source import LocalFolderSource
        source = LocalFolderSource(folder=args.folder)
        pipeline = create_pipeline(platforms=args.platforms, store=store,
                                   watermark_text=args.watermark_text,
                                   watermark_logo=args.watermark_logo)
        pipeline.source = source
        pipeline.run(limit=args.limit, dry_run=args.dry_run)
        return

    # --- Web source ---
    if args.source == "web":
        if not args.urls:
            print("--urls is required when --source web")
            sys.exit(1)
        from pipeline.themes.memes.sources.web_source import WebImageSource
        source = WebImageSource(urls=args.urls, caption=args.caption)
        pipeline = create_pipeline(platforms=args.platforms, store=store,
                                   watermark_text=args.watermark_text,
                                   watermark_logo=args.watermark_logo)
        pipeline.source = source
        pipeline.run(limit=args.limit, dry_run=args.dry_run)
        return

    if args.media:
        # Direct media post — bypass Reddit source
        from pipeline.core.pipeline import PlatformConfig
        media_path = Path(args.media)
        if not media_path.exists():
            print(f"Media file not found: {args.media}")
            sys.exit(1)

        item = ContentItem(
            source_url=f"file://{media_path.resolve()}",
            source_platform="local",
            media_path=media_path,
            caption=args.caption or media_path.stem,
            tags=args.tags or [],
        )

        pipeline = create_pipeline(
            platforms=args.platforms,
            store=store,
        )

        for platform in pipeline.platforms:
            print(f"  Publishing to {platform.name}: {item.caption!r}")
            if args.dry_run:
                print(f"  [dry-run] Would post.")
                continue
            platform_item = __import__("copy").deepcopy(item)
            for transformer in platform.transformers:
                platform_item = transformer.transform(platform_item)
            success = platform.publisher.publish(platform_item)
            print("  Posted." if success else "  Failed.")
        return

    pipeline = create_pipeline(
        platforms=args.platforms,
        store=store,
        subreddits=args.subreddits,
        sort=args.sort,
        time_filter=args.time_filter,
        min_score=args.min_score,
        watermark_text=args.watermark_text,
        watermark_logo=args.watermark_logo,
    )
    pipeline.run(limit=args.limit, dry_run=args.dry_run)


def cmd_auth(args):
    if args.platform == "facebook":
        from pipeline.platforms.facebook.publisher import FacebookPublisher
        sys.exit(0 if FacebookPublisher().verify_auth() else 1)

    elif args.platform == "instagram":
        from pipeline.platforms.instagram.publisher import InstagramPublisher
        sys.exit(0 if InstagramPublisher().verify_auth() else 1)

    elif args.platform == "x":
        from pipeline.platforms.x.publisher import XPublisher
        sys.exit(0 if XPublisher().verify_auth() else 1)

    elif args.platform == "tiktok":
        from pipeline.platforms.tiktok.publisher import TikTokPublisher
        sys.exit(0 if TikTokPublisher().verify_auth() else 1)

    elif args.platform == "reddit":
        import praw, os
        reddit = praw.Reddit(
            client_id=os.environ["REDDIT_CLIENT_ID"],
            client_secret=os.environ["REDDIT_CLIENT_SECRET"],
            user_agent=os.getenv("REDDIT_USER_AGENT", "socialline/1.0"),
        )
        try:
            list(reddit.subreddit("memes").hot(limit=1))
            print("Reddit auth OK — read-only access confirmed")
        except Exception as e:
            print(f"Reddit auth failed: {e}")
            sys.exit(1)


def cmd_fetchlog(args):
    from pipeline.core.content_store import ContentStore
    summary = ContentStore().fetch_log_summary(limit=args.limit)

    print("\n--- Outcome breakdown ---")
    for row in summary["outcomes"]:
        print(f"  {row['outcome']:<20} {row['n']}")

    print("\n--- Per subreddit ---")
    current_sub = None
    for row in summary["by_subreddit"]:
        if row["subreddit"] != current_sub:
            current_sub = row["subreddit"]
            print(f"  r/{current_sub}")
        print(f"    {row['outcome']:<18} {row['n']}")

    if args.show == "blocked" or args.show == "all":
        print(f"\n--- Last {args.limit} blocked ---")
        for row in summary["blocked"]:
            print(f"  [{row['subreddit']}] score={row['score']}  match={row['block_match']!r}")
            print(f"    {row['title']}")

    if args.show == "accepted" or args.show == "all":
        print(f"\n--- Last {args.limit} accepted ---")
        for row in summary["accepted"]:
            print(f"  [{row['subreddit']}] score={row['score']}")
            print(f"    {row['title']}")


def cmd_stats(args):
    from pipeline.core.content_store import ContentStore
    store = ContentStore()
    stats = store.stats()
    print(f"Content store: {stats['total']} total | {stats['posted']} posted | {stats['failed']} failed")


def main():
    parser = argparse.ArgumentParser(
        prog="socialline",
        description="Multi-platform content pipeline",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # --- run ---
    run = sub.add_parser("run", help="Fetch, transform, and publish content")
    run.add_argument("--theme", default="memes", choices=["memes"])
    run.add_argument("--source", default="reddit", choices=["reddit", "folder", "web"],
                     help="Content source (default: reddit)")
    run.add_argument("--folder", default=str(ROOT / "famjammemes" / "queue"),
                     help="Local image folder (--source folder)")
    run.add_argument("--urls", nargs="+", default=None,
                     help="Image or page URLs (--source web)")
    run.add_argument("--platforms", nargs="+", default=["facebook", "instagram", "tiktok"],
                     choices=["facebook", "instagram", "x", "tiktok"], metavar="PLATFORM")
    run.add_argument("--subreddits", nargs="+", default=None)
    run.add_argument("--sort", default="top", choices=["hot", "top", "new", "rising"])
    run.add_argument("--time-filter", dest="time_filter", default="week",
                     choices=["hour", "day", "week", "month", "year"])
    run.add_argument("--min-score", dest="min_score", type=int, default=100)
    run.add_argument("--limit", type=int, default=5)
    run.add_argument("--watermark-text", dest="watermark_text", default=None)
    run.add_argument("--watermark-logo", dest="watermark_logo", default=None)
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--media", default=None, help="Path to local media file (skips Reddit source)")
    run.add_argument("--caption", default=None)
    run.add_argument("--tags", nargs="+", default=None)
    run.set_defaults(func=cmd_run)

    # --- auth ---
    auth = sub.add_parser("auth", help="Verify credentials for a platform")
    auth.add_argument("platform", choices=["facebook", "instagram", "x", "tiktok", "reddit"])
    auth.set_defaults(func=cmd_auth)

    # --- stats ---
    stats = sub.add_parser("stats", help="Show content store statistics")
    stats.set_defaults(func=cmd_stats)

    # --- fetchlog ---
    fetchlog = sub.add_parser("fetchlog", help="Show fetch log — what was seen, accepted, blocked")
    fetchlog.add_argument("--show", default="blocked", choices=["blocked", "accepted", "all"],
                          help="Which posts to list (default: blocked)")
    fetchlog.add_argument("--limit", type=int, default=50)
    fetchlog.set_defaults(func=cmd_fetchlog)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
