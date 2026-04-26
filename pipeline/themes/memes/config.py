from pipeline.themes.memes.sources.reddit_source import RedditSource
from pipeline.themes.memes.transformers.watermark import WatermarkTransformer


def create_source(subreddits=None, sort="top", time_filter="week", min_score=100, on_candidate=None):
    return RedditSource(
        subreddits=subreddits or ["memes", "dankmemes"],
        sort=sort,
        time_filter=time_filter,
        min_score=min_score,
        on_candidate=on_candidate,
    )


def create_shared_transformers(watermark_text=None, watermark_logo=None):
    if watermark_text or watermark_logo:
        return [WatermarkTransformer(text=watermark_text, logo_path=watermark_logo)]
    return []
