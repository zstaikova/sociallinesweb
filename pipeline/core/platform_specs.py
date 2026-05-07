"""
Per-platform content requirements.

Each entry defines what a platform expects so transformers and publishers
can produce the right output without guessing.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ImageSpec:
    width: int
    height: int
    max_size_mb: float = 8.0          # max file size
    formats: tuple = ("jpg", "png")   # accepted formats


@dataclass
class VideoSpec:
    width: int
    height: int
    fps: int = 30
    min_duration_s: float = 1.0
    max_duration_s: float = 60.0
    max_size_mb: float = 500.0
    formats: tuple = ("mp4",)
    remotion_composition: str = "MemeVideo"   # which Remotion composition to render


@dataclass
class PlatformSpec:
    id: str
    name: str
    image: Optional[ImageSpec] = None
    video: Optional[VideoSpec] = None
    prefers_video: bool = False       # True = pipeline should render video when possible


# ── Platform specs ────────────────────────────────────────────────────────────

SPECS: dict[str, PlatformSpec] = {
    "facebook": PlatformSpec(
        id="facebook",
        name="Facebook",
        image=ImageSpec(width=1080, height=1080, max_size_mb=4.0),
        video=VideoSpec(
            width=1080, height=1920,
            max_duration_s=240 * 60,  # 4 hours
            remotion_composition="MemeVideo",
        ),
    ),
    "instagram": PlatformSpec(
        id="instagram",
        name="Instagram",
        image=ImageSpec(width=1080, height=1080, max_size_mb=8.0),
        video=VideoSpec(
            width=1080, height=1920,
            min_duration_s=3.0, max_duration_s=90.0,
            remotion_composition="MemeVideo",
        ),
    ),
    "threads": PlatformSpec(
        id="threads",
        name="Threads",
        image=ImageSpec(width=1080, height=1080, max_size_mb=8.0),
    ),
    "x": PlatformSpec(
        id="x",
        name="X (Twitter)",
        image=ImageSpec(width=1280, height=1280, max_size_mb=5.0),
        video=VideoSpec(
            width=1280, height=720,
            max_duration_s=140.0, max_size_mb=512.0,
            remotion_composition="MemeVideo",
        ),
    ),
    "tiktok": PlatformSpec(
        id="tiktok",
        name="TikTok",
        video=VideoSpec(
            width=1080, height=1920,
            min_duration_s=3.0, max_duration_s=600.0,  # 10min with certification
            max_size_mb=500.0,
            remotion_composition="MemeVideo",
        ),
        prefers_video=True,
    ),
    "bluesky": PlatformSpec(
        id="bluesky",
        name="Bluesky",
        image=ImageSpec(width=2000, height=2000, max_size_mb=1.0),
    ),
    "linkedin": PlatformSpec(
        id="linkedin",
        name="LinkedIn",
        image=ImageSpec(width=1200, height=1200, max_size_mb=5.0),
        video=VideoSpec(
            width=1920, height=1080,
            min_duration_s=3.0, max_duration_s=600.0,
            max_size_mb=200.0,
            remotion_composition="MemeVideo",
        ),
    ),
    "pinterest": PlatformSpec(
        id="pinterest",
        name="Pinterest",
        # 2:3 portrait is optimal for Pinterest feed
        image=ImageSpec(width=1000, height=1500, max_size_mb=20.0),
    ),
    "youtube": PlatformSpec(
        id="youtube",
        name="YouTube",
        video=VideoSpec(
            width=1080, height=1920,
            min_duration_s=1.0, max_duration_s=60.0,  # Shorts
            max_size_mb=256.0,
            remotion_composition="MemeVideo",
        ),
        prefers_video=True,
    ),
    "telegram": PlatformSpec(
        id="telegram",
        name="Telegram",
        image=ImageSpec(width=1280, height=1280, max_size_mb=10.0),
    ),
}


def get(platform_id: str) -> Optional[PlatformSpec]:
    return SPECS.get(platform_id)
