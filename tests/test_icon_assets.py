from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
ICON_PREVIEW = ROOT / "icon_preview.png"
ICON_ICO = ROOT / "icon.ico"
VIDEO_PLAY_SVG = ROOT / "src" / "ui" / "assets" / "bootstrap_play_circle_fill.svg"
VIDEO_PLAY_PNG = ROOT / "src" / "ui" / "assets" / "bootstrap_play_circle_fill.png"


def _visible_bounds_ratio(image: Image.Image) -> float:
    rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A")
    bounds = alpha.getbbox()
    if bounds is None:
        return 0.0
    width = bounds[2] - bounds[0]
    height = bounds[3] - bounds[1]
    return min(width / rgba.width, height / rgba.height)


def test_wincro_icon_assets_are_high_resolution_and_multisize():
    assert ICON_PREVIEW.exists()
    assert ICON_ICO.exists()

    preview = Image.open(ICON_PREVIEW)
    assert preview.size == (256, 256)
    assert preview.mode in {"RGBA", "LA", "P"}
    assert _visible_bounds_ratio(preview) >= 0.82

    ico = Image.open(ICON_ICO)
    assert hasattr(ico, "ico")
    sizes = set(ico.ico.sizes())
    assert {(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)} <= sizes


def test_wincro_icon_stays_readable_at_taskbar_sizes():
    preview = Image.open(ICON_PREVIEW).convert("RGBA")
    ico = Image.open(ICON_ICO)
    for size in (16, 24, 32):
        sample = preview.resize((size, size), Image.Resampling.LANCZOS)
        assert _visible_bounds_ratio(sample) >= 0.78
        ico_layer = ico.ico.getimage((size, size)).convert("RGBA")
        assert ico_layer.size == (size, size)
        assert _visible_bounds_ratio(ico_layer) >= 0.78


def test_video_play_overlay_uses_packaged_bootstrap_icon_asset():
    assert VIDEO_PLAY_SVG.exists()
    assert VIDEO_PLAY_PNG.exists()

    svg = VIDEO_PLAY_SVG.read_text(encoding="utf-8")
    assert "Bootstrap Icons: play-circle-fill" in svg
    assert "License: MIT" in svg
    assert "M16 8A8 8" in svg

    png = Image.open(VIDEO_PLAY_PNG).convert("RGBA")
    assert png.size == (256, 256)
    assert _visible_bounds_ratio(png) >= 0.78

    spec = (ROOT / "WinCro.spec").read_text(encoding="utf-8-sig")
    assert "('src/ui/assets', 'src/ui/assets')" in spec
    assert "('icon.ico', '.')" in spec
    assert "('icon_preview.png', '.')" in spec
