from pathlib import Path

import cv2
import numpy as np

from src.analyzer.template_matcher import TemplateMatcher


def _write_png(path: Path, image: np.ndarray) -> None:
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    path.write_bytes(encoded.tobytes())


def _make_directional_template() -> tuple[np.ndarray, np.ndarray]:
    template = np.zeros((18, 28, 3), dtype=np.uint8)
    mask = np.zeros((18, 28), dtype=np.uint8)
    points = np.array([[3, 8], [14, 8], [14, 5], [24, 9], [14, 13], [14, 10], [3, 10]], dtype=np.int32)
    cv2.fillConvexPoly(template, points, (240, 240, 240))
    cv2.fillConvexPoly(mask, points, 255)
    return template, mask


def test_match_binary_can_match_horizontal_flip(tmp_path: Path):
    template, mask = _make_directional_template()
    template_path = tmp_path / "boss_crop.png"
    mask_path = tmp_path / "boss_crop_mask.png"
    _write_png(template_path, template)
    _write_png(mask_path, mask)

    screen = np.zeros((80, 80, 3), dtype=np.uint8)
    flipped = cv2.flip(template, 1)
    screen[24:42, 35:63] = flipped

    matcher = TemplateMatcher()
    no_flip = matcher.match_binary(screen, str(template_path), threshold=0.85, allow_flip=False)
    with_flip = matcher.match_binary(screen, str(template_path), threshold=0.85, allow_flip=True)

    assert with_flip.found
    assert with_flip.confidence > no_flip.confidence
    assert abs(with_flip.center_x - 49) <= 1
    assert abs(with_flip.center_y - 33) <= 1


def test_match_with_mask_can_match_horizontal_flip(tmp_path: Path):
    template, mask = _make_directional_template()
    template_path = tmp_path / "boss_crop.png"
    mask_path = tmp_path / "boss_crop_mask.png"
    _write_png(template_path, template)
    _write_png(mask_path, mask)

    screen = np.zeros((80, 80, 3), dtype=np.uint8)
    flipped = cv2.flip(template, 1)
    screen[20:38, 30:58] = flipped

    matcher = TemplateMatcher()
    no_flip = matcher.match(screen, str(template_path), threshold=0.9, use_mask=True, allow_flip=False)
    with_flip = matcher.match(screen, str(template_path), threshold=0.9, use_mask=True, allow_flip=True)

    assert with_flip.found
    assert with_flip.confidence > no_flip.confidence
    assert abs(with_flip.center_x - 44) <= 1
    assert abs(with_flip.center_y - 29) <= 1


def test_match_binary_rejects_degenerate_tiny_template_without_mask(tmp_path: Path):
    template = np.zeros((15, 4, 3), dtype=np.uint8)
    gradient = np.array([69, 100, 90, 82, 75, 70, 65, 65, 65, 65, 65, 65, 68, 82, 101], dtype=np.uint8)
    for x in range(template.shape[1]):
        template[:, x] = gradient[:, None]
    template[:, 0] = np.clip(template[:, 0] - 3, 0, 255)

    template_path = tmp_path / "char_crop.png"
    _write_png(template_path, template)

    screen = np.random.randint(0, 256, (200, 200, 3), dtype=np.uint8)

    matcher = TemplateMatcher()
    inspection = matcher.inspect_template_localization(str(template_path))
    result = matcher.match_binary(screen, str(template_path), threshold=0.7, allow_flip=True)

    assert inspection["valid"] is False
    assert inspection["reason"] == "degenerate_tiny_template"
    assert result.found is False
    assert result.confidence == 0.0


def test_match_binary_small_masked_template_still_matches(tmp_path: Path):
    template = np.zeros((15, 6, 3), dtype=np.uint8)
    mask = np.zeros((15, 6), dtype=np.uint8)
    pts = np.array([[1, 2], [4, 1], [4, 13], [1, 12]], dtype=np.int32)
    cv2.fillConvexPoly(template, pts, (230, 230, 230))
    cv2.fillConvexPoly(mask, pts, 255)

    template_path = tmp_path / "char_crop.png"
    mask_path = tmp_path / "char_crop_mask.png"
    _write_png(template_path, template)
    _write_png(mask_path, mask)

    screen = np.zeros((60, 60, 3), dtype=np.uint8)
    screen[20:35, 28:34] = template

    matcher = TemplateMatcher()
    inspection = matcher.inspect_template_localization(str(template_path))
    result = matcher.match_binary(screen, str(template_path), threshold=0.7, allow_flip=False)

    assert inspection["valid"] is True
    assert result.found is True
    assert result.confidence >= 0.7
