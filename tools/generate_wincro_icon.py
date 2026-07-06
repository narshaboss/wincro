"""Generate crisp WinCro application icon assets.

The icon is drawn from vector-like primitives instead of resizing an old bitmap.
This keeps the 16/32px Windows taskbar layers readable.
"""

from __future__ import annotations

import io
import struct
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


ROOT = Path(__file__).resolve().parents[1]
PREVIEW_PATH = ROOT / "icon_preview.png"
ICO_PATH = ROOT / "icon.ico"
ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def _pt(size: int, x: float, y: float) -> tuple[int, int]:
    return int(round(size * x)), int(round(size * y))


def _sharpen(image: Image.Image) -> Image.Image:
    return image.filter(ImageFilter.UnsharpMask(radius=0.8, percent=120, threshold=2))


def _draw_large_icon(size: int) -> Image.Image:
    scale = 4
    canvas_size = size * scale
    img = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    def xy(x: float, y: float) -> tuple[int, int]:
        return _pt(canvas_size, x, y)

    def box(x1: float, y1: float, x2: float, y2: float) -> tuple[int, int, int, int]:
        return (*xy(x1, y1), *xy(x2, y2))

    # Strong, simple silhouette for small Windows taskbar sizes.
    shadow = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.ellipse(box(0.075, 0.085, 0.925, 0.935), fill=(0, 0, 0, 180))
    shadow = shadow.filter(ImageFilter.GaussianBlur(max(2, canvas_size // 80)))
    img.alpha_composite(shadow)

    draw.ellipse(box(0.06, 0.06, 0.94, 0.94), fill="#070604", outline="#F8D879", width=max(3, canvas_size // 58))
    draw.ellipse(box(0.105, 0.105, 0.895, 0.895), outline="#8F5E0A", width=max(2, canvas_size // 95))

    # Blade core.
    blade = [
        xy(0.485, 0.205),
        xy(0.545, 0.205),
        xy(0.555, 0.700),
        xy(0.515, 0.855),
        xy(0.475, 0.700),
    ]
    draw.polygon(blade, fill="#F8D879")
    draw.line([xy(0.515, 0.220), xy(0.515, 0.820)], fill="#8A5B08", width=max(2, canvas_size // 90))
    draw.polygon([xy(0.515, 0.210), xy(0.545, 0.705), xy(0.515, 0.845)], fill="#B97910")

    # Hilt and guard are intentionally bolder than a realistic sword.
    draw.rounded_rectangle(box(0.485, 0.120, 0.545, 0.220), radius=max(3, canvas_size // 55), fill="#FAD66B", outline="#5A3606", width=max(2, canvas_size // 100))
    draw.ellipse(box(0.470, 0.065, 0.560, 0.155), fill="#FFE897", outline="#5A3606", width=max(2, canvas_size // 95))
    draw.ellipse(box(0.492, 0.088, 0.538, 0.134), fill="#A86B0A")

    guard = [
        xy(0.205, 0.310),
        xy(0.420, 0.350),
        xy(0.488, 0.315),
        xy(0.515, 0.365),
        xy(0.542, 0.315),
        xy(0.610, 0.350),
        xy(0.825, 0.310),
        xy(0.640, 0.395),
        xy(0.560, 0.390),
        xy(0.515, 0.440),
        xy(0.470, 0.390),
        xy(0.390, 0.395),
    ]
    draw.polygon(guard, fill="#F8D879")
    draw.line([xy(0.225, 0.318), xy(0.410, 0.365), xy(0.515, 0.405), xy(0.620, 0.365), xy(0.805, 0.318)], fill="#6B4205", width=max(2, canvas_size // 90))

    # A controlled diagonal shine reads well at 64px+ but does not blur 16px.
    shine = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    shine_draw = ImageDraw.Draw(shine)
    shine_draw.polygon([xy(0.505, 0.420), xy(0.550, 0.470), xy(0.530, 0.610), xy(0.485, 0.560)], fill=(255, 255, 235, 70))
    img.alpha_composite(shine)

    return _sharpen(img.resize((size, size), Image.Resampling.LANCZOS))


def _draw_taskbar_icon(size: int) -> Image.Image:
    """Preserve the WinCro sword-in-circle shape for Windows taskbar layers."""
    return _sharpen(_draw_large_icon(256).resize((size, size), Image.Resampling.LANCZOS))


def _draw_icon(size: int) -> Image.Image:
    if size <= 32:
        return _draw_taskbar_icon(size)
    return _draw_large_icon(size)


def _write_multi_layer_ico(path: Path, images: list[Image.Image]) -> None:
    """Write an ICO using exact per-size PNG layers instead of auto-resized layers."""
    png_payloads: list[tuple[int, int, bytes]] = []
    for image in images:
        rgba = image.convert("RGBA")
        buffer = io.BytesIO()
        rgba.save(buffer, format="PNG", optimize=True)
        png_payloads.append((rgba.width, rgba.height, buffer.getvalue()))

    header_size = 6 + 16 * len(png_payloads)
    offset = header_size
    chunks = [struct.pack("<HHH", 0, 1, len(png_payloads))]
    directory_entries = []
    for width, height, payload in png_payloads:
        directory_entries.append(
            struct.pack(
                "<BBBBHHII",
                0 if width >= 256 else width,
                0 if height >= 256 else height,
                0,
                0,
                1,
                32,
                len(payload),
                offset,
            )
        )
        offset += len(payload)
    chunks.extend(directory_entries)
    chunks.extend(payload for _, _, payload in png_payloads)
    path.write_bytes(b"".join(chunks))


def main() -> None:
    preview = _draw_large_icon(256)
    preview.save(PREVIEW_PATH)
    icons = [_draw_icon(size[0]) for size in ICO_SIZES]
    _write_multi_layer_ico(ICO_PATH, icons)
    print(f"wrote {PREVIEW_PATH}")
    print(f"wrote {ICO_PATH}")


if __name__ == "__main__":
    main()
