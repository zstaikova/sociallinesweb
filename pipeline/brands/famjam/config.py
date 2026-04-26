from pipeline.core.pipeline import Pipeline, PlatformConfig
from pipeline.core.content_store import ContentStore
from pipeline.themes.memes.config import create_source, create_shared_transformers

BRAND_NAME = "@famjammemes"


def create_pipeline(platforms: list, store: ContentStore = None, **kwargs) -> Pipeline:
    store = store or ContentStore()

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
        sort=kwargs.get("sort", "top"),  # X disabled — 402 on free tier, re-enable when resolved
        time_filter=kwargs.get("time_filter", "week"),
        min_score=kwargs.get("min_score", 100),
    )

    shared_transformers = create_shared_transformers(
        watermark_text=kwargs.get("watermark_text"),
        watermark_logo=kwargs.get("watermark_logo"),
    )

    platform_configs = []

    if "facebook" in platforms:
        from pipeline.platforms.facebook.publisher import FacebookPublisher
        platform_configs.append(PlatformConfig(publisher=FacebookPublisher()))

    if "instagram" in platforms:
        from pipeline.platforms.instagram.publisher import InstagramPublisher
        platform_configs.append(PlatformConfig(publisher=InstagramPublisher()))

    if "x" in platforms:
        from pipeline.platforms.x.publisher import XPublisher
        platform_configs.append(PlatformConfig(publisher=XPublisher()))

    if "tiktok" in platforms:
        from pipeline.platforms.tiktok.publisher import TikTokPublisher
        from pipeline.platforms.tiktok.remotion_transformer import RemotionTransformer
        platform_configs.append(PlatformConfig(
            publisher=TikTokPublisher(),
            transformers=[RemotionTransformer(brand_name=BRAND_NAME)],
        ))

    return Pipeline(source, shared_transformers, platform_configs, store)
