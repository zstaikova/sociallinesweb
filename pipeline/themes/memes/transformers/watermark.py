from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

from pipeline.core.base_transformer import BaseTransformer
from pipeline.core.content_item import ContentItem

# Platform-safe max dimensions (Facebook, Instagram, Twitter all accept 1080px)
MAX_SIZE = (1080, 1080)


class WatermarkTransformer(BaseTransformer):
    def __init__(
        self,
        text: str = None,
        logo_path: str = None,
        position: str = "bottom-right",  # bottom-right | bottom-left | top-right | top-left
        opacity: int = 200,              # 0-255
        padding: int = 18,
        font_size: int = 28,
    ):
        self.text = text
        self.logo_path = Path(logo_path) if logo_path else None
        self.position = position
        self.opacity = opacity
        self.padding = padding
        self.font_size = font_size

    def transform(self, item: ContentItem) -> ContentItem:
        if not item.media_path or not item.media_path.exists():
            return item

        img = Image.open(item.media_path).convert("RGBA")
        img = self._resize(img)

        if self.logo_path and self.logo_path.exists():
            img = self._apply_logo(img)
        elif self.text:
            img = self._apply_text(img)

        out_path = item.media_path.parent / f"wm_{item.media_path.stem}.jpg"
        img.convert("RGB").save(out_path, "JPEG", quality=92)
        item.media_path = out_path
        return item

    def _resize(self, img: Image.Image) -> Image.Image:
        img.thumbnail(MAX_SIZE, Image.LANCZOS)
        return img

    def _apply_text(self, img: Image.Image) -> Image.Image:
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        try:
            font = ImageFont.truetype("arial.ttf", self.font_size)
        except OSError:
            font = ImageFont.load_default()

        bbox = draw.textbbox((0, 0), self.text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]

        x, y = self._calc_position(img.size, tw, th)

        # Shadow
        draw.text((x + 2, y + 2), self.text, font=font, fill=(0, 0, 0, 160))
        # Text
        draw.text((x, y), self.text, font=font, fill=(255, 255, 255, self.opacity))

        return Image.alpha_composite(img, overlay)

    def _apply_logo(self, img: Image.Image) -> Image.Image:
        logo = Image.open(self.logo_path).convert("RGBA")
        max_logo = min(img.width // 5, 180)
        logo.thumbnail((max_logo, max_logo), Image.LANCZOS)

        r, g, b, a = logo.split()
        a = a.point(lambda p: int(p * self.opacity / 255))
        logo.putalpha(a)

        x, y = self._calc_position(img.size, logo.width, logo.height)
        img.paste(logo, (x, y), logo)
        return img

    def _calc_position(self, img_size, w, h) -> tuple:
        iw, ih = img_size
        p = self.padding
        positions = {
            "bottom-right": (iw - w - p, ih - h - p),
            "bottom-left":  (p, ih - h - p),
            "top-right":    (iw - w - p, p),
            "top-left":     (p, p),
        }
        return positions.get(self.position, positions["bottom-right"])
