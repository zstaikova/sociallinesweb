import json
import subprocess
from pathlib import Path

from pipeline.core.base_transformer import BaseTransformer
from pipeline.core.content_item import ContentItem

REMOTION_DIR = Path("D:/remotion")
OUT_DIR = REMOTION_DIR / "out"


class RemotionTransformer(BaseTransformer):
    """
    Renders a MemeVideo MP4 from a ContentItem's caption using Remotion.
    Replaces item.media_path with the rendered video for TikTok publishing.
    """

    def __init__(self, brand_name: str = "@famjammemes", composition: str = "MemeVideo"):
        self.brand_name = brand_name
        self.composition = composition

    def transform(self, item: ContentItem) -> ContentItem:
        lines = self._split_caption(item.caption)
        props = {"brandName": self.brand_name, "lines": lines}

        # Write props to a temp file to avoid shell quoting issues on Windows
        props_file = REMOTION_DIR / f"_props_{item.id}.json"
        out_path = OUT_DIR / f"{item.id}.mp4"
        props_file.write_text(json.dumps(props, ensure_ascii=False))

        try:
            result = subprocess.run(
                f"npx remotion render {self.composition} "
                f"out/{item.id}.mp4 "
                f"--props=./_props_{item.id}.json",
                shell=True,
                cwd=str(REMOTION_DIR),
                capture_output=True,
                text=True,
                timeout=300,
            )
        finally:
            props_file.unlink(missing_ok=True)

        if result.returncode != 0:
            print(f"  Remotion render failed:\n{result.stderr[-1000:]}")
            return item

        item.media_path = out_path
        print(f"  Remotion rendered: {out_path.name}")
        return item

    def _split_caption(self, caption: str, max_lines: int = 3) -> list[str]:
        words = caption.split()
        if not words:
            return [""]

        # Aim for roughly equal lines, max 3
        n = min(max_lines, max(1, (len(words) + 3) // 4))
        chunk = (len(words) + n - 1) // n
        lines = []
        for i in range(n):
            segment = words[i * chunk: (i + 1) * chunk]
            if segment:
                lines.append(" ".join(segment))
        return lines
