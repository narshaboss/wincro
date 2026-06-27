from pathlib import Path

import cv2
import numpy as np

from src.ui.image_crop_utils import (
    apply_brush,
    apply_mask_overlay_rgb,
    auto_extract_foreground_mask,
    extract_crop_and_mask,
    fit_image_to_box,
    get_sidecar_mask_path,
    load_sidecar_mask,
    normalize_binary_mask,
)


def test_get_sidecar_mask_path_uses_mask_suffix():
    path = get_sidecar_mask_path(r"C:\tmp\sample_crop.png")
    assert str(path).endswith(r"sample_crop_mask.png")


def test_normalize_binary_mask_resizes_and_binarizes():
    mask = np.array([[0, 200], [255, 10]], dtype=np.uint8)
    normalized = normalize_binary_mask(mask, (4, 4))

    assert normalized.shape == (4, 4)
    assert set(np.unique(normalized)).issubset({0, 255})


def test_load_sidecar_mask_reads_saved_mask(tmp_path: Path):
    image_path = tmp_path / "crop.png"
    image_path.write_bytes(b"placeholder")
    mask_path = tmp_path / "crop_mask.png"
    mask = np.zeros((6, 6), dtype=np.uint8)
    mask[1:5, 1:5] = 255
    cv2.imwrite(str(mask_path), mask)

    loaded = load_sidecar_mask(image_path, (6, 6))

    assert loaded is not None
    assert loaded.shape == (6, 6)
    assert int(loaded[0, 0]) == 0
    assert int(loaded[2, 2]) == 255


def test_extract_crop_and_mask_slices_full_mask():
    image = np.full((8, 8, 3), 120, dtype=np.uint8)
    full_mask = np.full((8, 8), 255, dtype=np.uint8)
    full_mask[2:6, 2:6] = 0

    crop, crop_mask = extract_crop_and_mask(image, (1, 1, 7, 7), full_mask)

    assert crop.shape == (6, 6, 3)
    assert crop_mask.shape == (6, 6)
    assert int(crop_mask[0, 0]) == 255
    assert int(crop_mask[2, 2]) == 0


def test_apply_brush_erases_and_restores_pixels():
    mask = np.full((20, 20), 255, dtype=np.uint8)

    apply_brush(mask, (10, 10), 3, erase=True)
    assert int(mask[10, 10]) == 0

    apply_brush(mask, (10, 10), 3, erase=False)
    assert int(mask[10, 10]) == 255


def test_apply_mask_overlay_rgb_tints_masked_pixels_only():
    image = np.full((4, 4, 3), 100, dtype=np.uint8)
    mask = np.full((4, 4), 255, dtype=np.uint8)
    mask[1:3, 1:3] = 0

    overlay = apply_mask_overlay_rgb(image, mask)

    assert np.array_equal(overlay[0, 0], np.array([100, 100, 100], dtype=np.uint8))
    assert not np.array_equal(overlay[1, 1], np.array([100, 100, 100], dtype=np.uint8))


def test_fit_image_to_box_preserves_aspect_ratio():
    image = np.zeros((100, 200, 3), dtype=np.uint8)

    fitted, scale = fit_image_to_box(image, 50, 50)

    assert fitted.shape[1] == 50
    assert fitted.shape[0] == 25
    assert scale == 0.25


def test_auto_extract_foreground_mask_keeps_center_object():
    image = np.full((60, 60, 3), 40, dtype=np.uint8)
    image[15:45, 20:40] = np.array([220, 220, 220], dtype=np.uint8)

    mask = auto_extract_foreground_mask(image)

    assert mask.shape == (60, 60)
    assert int(mask[30, 30]) == 255


def test_auto_extract_foreground_mask_prefers_colored_ui_text():
    image = np.full((24, 84, 3), np.array([34, 28, 18], dtype=np.uint8), dtype=np.uint8)
    cv2.putText(image, "GET", (4, 17), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (245, 184, 42), 2, cv2.LINE_AA)

    mask = auto_extract_foreground_mask(image)
    kept_ratio = float(np.count_nonzero(mask) / mask.size)

    assert mask.shape == image.shape[:2]
    assert int(mask[0, 0]) == 0
    assert int(mask[12, 12]) == 255
    assert 0.03 < kept_ratio < 0.65
