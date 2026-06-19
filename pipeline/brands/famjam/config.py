from pipeline.core.pipeline import Pipeline, PlatformConfig
from pipeline.core.content_store import ContentStore
from pipeline.core.accounts import AccountStore
from pipeline.themes.memes.config import create_source, create_shared_transformers

BRAND_NAME = "@famjammemes"


def _make_refresh_cb(acct: AccountStore, platform: str,
                     access_key: str, refresh_key: str = None):
    """Return an on_token_refresh callback that persists new tokens to AccountStore."""
    def _cb(new_access, new_refresh=None):
        partial = {access_key: new_access}
        if refresh_key and new_refresh:
            partial[refresh_key] = new_refresh
        acct.update_credentials(platform, partial)
    return _cb


def create_pipeline(platforms: list, store: ContentStore = None,
                    account_store: AccountStore = None, **kwargs) -> Pipeline:
    store = store or ContentStore()
    acct  = account_store or AccountStore()

    source = create_source(
        on_candidate=store.log_fetch,
        subreddits=kwargs.get("subreddits") or [
            "wholesomememes",
            "daddit",
            "Mommit",
            "kidsarefuckingstupid",
            "KidsLogic",
            "Parenting",
        ],
        sort=kwargs.get("sort", "top"),
        time_filter=kwargs.get("time_filter", "week"),
        min_score=kwargs.get("min_score", 100),
    )

    shared_transformers = create_shared_transformers(
        watermark_text=kwargs.get("watermark_text"),
        watermark_logo=kwargs.get("watermark_logo"),
    )

    platform_configs = []

    def _creds(p):
        return acct.get_credentials(p)

    def _has(p):
        return bool(_creds(p))

    if "facebook" in platforms and _has("facebook"):
        from pipeline.platforms.facebook.publisher import FacebookPublisher
        platform_configs.append(PlatformConfig(
            publisher=FacebookPublisher(
                _creds("facebook"),
                on_token_refresh=_make_refresh_cb(
                    acct, "facebook", "FACEBOOK_PAGE_ACCESS_TOKEN"))))

    if "instagram" in platforms and _has("instagram"):
        from pipeline.platforms.instagram.publisher import InstagramPublisher
        platform_configs.append(PlatformConfig(
            publisher=InstagramPublisher(
                _creds("instagram"),
                on_token_refresh=_make_refresh_cb(
                    acct, "instagram", "INSTAGRAM_ACCESS_TOKEN"),
                on_page_token_refresh=_make_refresh_cb(
                    acct, "instagram", "FACEBOOK_PAGE_ACCESS_TOKEN"))))

    if "x" in platforms and _has("x"):
        from pipeline.platforms.x.publisher import XPublisher
        platform_configs.append(PlatformConfig(publisher=XPublisher(_creds("x"))))

    if "tiktok" in platforms and _has("tiktok"):
        from pipeline.platforms.tiktok.publisher import TikTokPublisher
        from pipeline.platforms.tiktok.remotion_transformer import from_spec as remotion_for
        platform_configs.append(PlatformConfig(
            publisher=TikTokPublisher(
                _creds("tiktok"),
                on_token_refresh=_make_refresh_cb(
                    acct, "tiktok", "TIKTOK_ACCESS_TOKEN", "TIKTOK_REFRESH_TOKEN")),
            transformers=[remotion_for("tiktok", brand_name=BRAND_NAME)],
        ))

    if "threads" in platforms and _has("threads"):
        from pipeline.platforms.threads.publisher import ThreadsPublisher
        platform_configs.append(PlatformConfig(
            publisher=ThreadsPublisher(
                _creds("threads"),
                on_token_refresh=_make_refresh_cb(
                    acct, "threads", "THREADS_ACCESS_TOKEN"))))

    if "bluesky" in platforms and _has("bluesky"):
        from pipeline.platforms.bluesky.publisher import BlueskyPublisher
        platform_configs.append(PlatformConfig(
            publisher=BlueskyPublisher(_creds("bluesky"))))

    if "linkedin" in platforms and _has("linkedin"):
        from pipeline.platforms.linkedin.publisher import LinkedInPublisher
        platform_configs.append(PlatformConfig(
            publisher=LinkedInPublisher(
                _creds("linkedin"),
                on_token_refresh=_make_refresh_cb(
                    acct, "linkedin", "LINKEDIN_ACCESS_TOKEN", "LINKEDIN_REFRESH_TOKEN"))))

    if "pinterest" in platforms and _has("pinterest"):
        from pipeline.platforms.pinterest.publisher import PinterestPublisher
        platform_configs.append(PlatformConfig(
            publisher=PinterestPublisher(
                _creds("pinterest"),
                on_token_refresh=_make_refresh_cb(
                    acct, "pinterest", "PINTEREST_ACCESS_TOKEN", "PINTEREST_REFRESH_TOKEN"))))

    if "telegram" in platforms and _has("telegram"):
        from pipeline.platforms.telegram.publisher import TelegramPublisher
        platform_configs.append(PlatformConfig(
            publisher=TelegramPublisher(_creds("telegram"))))

    if "youtube" in platforms and _has("youtube"):
        from pipeline.platforms.youtube.publisher import YouTubePublisher
        from pipeline.platforms.tiktok.remotion_transformer import from_spec as remotion_for
        platform_configs.append(PlatformConfig(
            publisher=YouTubePublisher(
                _creds("youtube"),
                on_token_refresh=_make_refresh_cb(
                    acct, "youtube", "YOUTUBE_ACCESS_TOKEN", "YOUTUBE_REFRESH_TOKEN")),
            transformers=[remotion_for("youtube", brand_name=BRAND_NAME)],
        ))

    if "substack" in platforms and _has("substack"):
        from pipeline.platforms.substack.publisher import SubstackPublisher
        platform_configs.append(PlatformConfig(
            publisher=SubstackPublisher(_creds("substack"))))

    if "etsy" in platforms and _has("etsy"):
        from pipeline.platforms.etsy.publisher import EtsyPublisher
        platform_configs.append(PlatformConfig(
            publisher=EtsyPublisher(_creds("etsy"))))

    return Pipeline(source, shared_transformers, platform_configs, store)
