import json
import shutil
import subprocess
from pathlib import Path

from pipeline.core.base_transformer import BaseTransformer
from pipeline.core.content_item import ContentItem

REMOTION_DIR = Path("D:/remotion")
PUBLIC_DIR   = REMOTION_DIR / "public"
OUT_DIR      = REMOTION_DIR / "out"

# Image formats Remotion can display natively in <Img>
SUPPORTED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}


class RemotionTransformer(BaseTransformer):
    """
    Renders a Remotion video from a ContentItem.

    Copies the source image into remotion/public/ temporarily so
    Remotion's staticFile() can load it, then renders the composition
    and replaces item.media_path with the resulting MP4.
    """

    def __init__(
        self,
        brand_name: str = "@famjammemes",
        composition: str = "MemeVideo",
        width: int = 1080,
        height: int = 1920,
    ):
        self.brand_name  = brand_name
        self.composition = composition
        self.width       = width
        self.height      = height

    def transform(self, item: ContentItem) -> ContentItem:
        src = item.media_path
        if not src or not src.exists():
            print(f"  Remotion: no source image — skipping render")
            return item

        ext = src.suffix.lower()
        if ext not in SUPPORTED_IMAGE_EXTS:
            print(f"  Remotion: unsupported source format {ext} — skipping render")
            return item

        PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
        OUT_DIR.mkdir(parents=True, exist_ok=True)

        # Copy source image into remotion/public/ with a stable temp name
        tmp_name = f"_meme_{item.id}{ext}"
        tmp_path = PUBLIC_DIR / tmp_name
        shutil.copy2(src, tmp_path)

        lines      = self._split_caption(item.caption)
        out_path   = OUT_DIR / f"{item.id}.mp4"
        props_file = REMOTION_DIR / f"_props_{item.id}.json"

        props = {
            "brandName": self.brand_name,
            "lines":     lines,
            "imagePath": tmp_name,
        }
        props_file.write_text(json.dumps(props, ensure_ascii=False))

        try:
            result = subprocess.run(
                f"npx remotion render {self.composition} "
                f"out/{item.id}.mp4 "
                f"--props=./_props_{item.id}.json "
                f"--width={self.width} --height={self.height}",
                shell=True,
                cwd=str(REMOTION_DIR),
                capture_output=True,
                text=True,
                timeout=300,
            )
        finally:
            props_file.unlink(missing_ok=True)
            tmp_path.unlink(missing_ok=True)

        if result.returncode != 0:
            print(f"  Remotion render failed:\n{result.stderr[-1000:]}")
            return item

        item.media_path = out_path
        print(f"  Remotion rendered: {out_path.name} ({self.width}x{self.height})")
        return item

    def _split_caption(self, caption: str, max_lines: int = 3) -> list[str]:
        words = caption.split()
        if not words:
            return [""]
        n = min(max_lines, max(1, (len(words) + 3) // 4))
        chunk = (len(words) + n - 1) // n
        lines = []
        for i in range(n):
            segment = words[i * chunk: (i + 1) * chunk]
            if segment:
                lines.append(" ".join(segment))
        return lines


def from_spec(platform_id: str, brand_name: str = "@famjammemes") -> "RemotionTransformer":
    """Create a RemotionTransformer from a platform's VideoSpec."""
    from pipeline.core.platform_specs import get as get_spec
    spec = get_spec(platform_id)
    if not spec or not spec.video:
        raise ValueError(f"No video spec defined for platform '{platform_id}'")
    v = spec.video
    return RemotionTransformer(
        brand_name=brand_name,
        composition=v.remotion_composition,
        width=v.width,
        height=v.height,
    )
