from pipeline.core.pipeline import Pipeline, PlatformConfig
from pipeline.core.content_store import ContentStore
from pipeline.core.accounts import AccountStore
from pipeline.themes.memes.config import create_source, create_shared_transformers

BRAND_NAME = "@famjammemes"


def create_pipeline(platforms: list, store: ContentStore = None,
                    account_store: AccountStore = None, **kwargs) -> Pipeline:
    store = store or ContentStore()
    acct  = account_store or AccountStore()  # fallback to root accounts.enc for CLI use

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

    def _has_creds(p):
        return bool(_creds(p))

    if "facebook" in platforms and _has_creds("facebook"):
        from pipeline.platforms.facebook.publisher import FacebookPublisher
        platform_configs.append(PlatformConfig(publisher=FacebookPublisher(_creds("facebook"))))

    if "instagram" in platforms and _has_creds("instagram"):
        from pipeline.platforms.instagram.publisher import InstagramPublisher
        platform_configs.append(PlatformConfig(publisher=InstagramPublisher(_creds("instagram"))))

    if "x" in platforms and _has_creds("x"):
        from pipeline.platforms.x.publisher import XPublisher
        platform_configs.append(PlatformConfig(publisher=XPublisher(_creds("x"))))

    if "tiktok" in platforms and _has_creds("tiktok"):
        from pipeline.platforms.tiktok.publisher import TikTokPublisher
        from pipeline.platforms.tiktok.remotion_transformer import from_spec as remotion_for
        _tiktok_creds = _creds("tiktok")

        def _tiktok_on_refresh(new_access_token, new_refresh_token):
            account = acct.get_active("tiktok")
            if account:
                updated = {**account.credentials,
                           "TIKTOK_ACCESS_TOKEN":  new_access_token,
                           "TIKTOK_REFRESH_TOKEN": new_refresh_token}
                acct.add("tiktok", account.account_name, account.account_id, updated)

        platform_configs.append(PlatformConfig(
            publisher=TikTokPublisher(_tiktok_creds, on_token_refresh=_tiktok_on_refresh),
            transformers=[remotion_for("tiktok", brand_name=BRAND_NAME)],
        ))

    if "threads" in platforms and _has_creds("threads"):
        from pipeline.platforms.threads.publisher import ThreadsPublisher
        platform_configs.append(PlatformConfig(publisher=ThreadsPublisher(_creds("threads"))))

    if "bluesky" in platforms and _has_creds("bluesky"):
        from pipeline.platforms.bluesky.publisher import BlueskyPublisher
        platform_configs.append(PlatformConfig(publisher=BlueskyPublisher(_creds("bluesky"))))

    if "linkedin" in platforms and _has_creds("linkedin"):
        from pipeline.platforms.linkedin.publisher import LinkedInPublisher
        platform_configs.append(PlatformConfig(publisher=LinkedInPublisher(_creds("linkedin"))))

    if "pinterest" in platforms and _has_creds("pinterest"):
        from pipeline.platforms.pinterest.publisher import PinterestPublisher
        platform_configs.append(PlatformConfig(publisher=PinterestPublisher(_creds("pinterest"))))

    if "telegram" in platforms and _has_creds("telegram"):
        from pipeline.platforms.telegram.publisher import TelegramPublisher
        platform_configs.append(PlatformConfig(publisher=TelegramPublisher(_creds("telegram"))))

    if "youtube" in platforms and _has_creds("youtube"):
        from pipeline.platforms.youtube.publisher import YouTubePublisher
        from pipeline.platforms.tiktok.remotion_transformer import from_spec as remotion_for
        _youtube_creds = _creds("youtube")

        def _youtube_on_refresh(new_access_token, new_refresh_token=None):
            account = acct.get_active("youtube")
            if account:
                updated = {**account.credentials, "YOUTUBE_ACCESS_TOKEN": new_access_token}
                if new_refresh_token:
                    updated["YOUTUBE_REFRESH_TOKEN"] = new_refresh_token
                acct.add("youtube", account.account_name, account.account_id, updated)

        platform_configs.append(PlatformConfig(
            publisher=YouTubePublisher(_youtube_creds, on_token_refresh=_youtube_on_refresh),
            transformers=[remotion_for("youtube", brand_name=BRAND_NAME)],
        ))

    return Pipeline(source, shared_transformers, platform_configs, store)
