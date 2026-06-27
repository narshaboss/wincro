from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

import cv2
import numpy as np


MaskArray = np.ndarray
CropCoords = Tuple[int, int, int, int]


def get_sidecar_mask_path(image_path: str | Path) -> Path:
    path = Path(image_path)
    return path.parent / f"{path.stem}_mask{path.suffix}"


def normalize_binary_mask(mask: Optional[np.ndarray], shape: tuple[int, int]) -> MaskArray:
    height, width = shape
    if mask is None:
        return np.full((height, width), 255, dtype=np.uint8)

    if mask.ndim == 3:
        mask = cv2.cvtColor(mask, cv2.COLOR_BGR2GRAY)

    if mask.shape[:2] != (height, width):
        mask = cv2.resize(mask, (width, height), interpolation=cv2.INTER_NEAREST)

    normalized = np.where(mask > 127, 255, 0).astype(np.uint8)
    return normalized


def load_sidecar_mask(image_path: str | Path, shape: tuple[int, int]) -> Optional[MaskArray]:
    mask_path = get_sidecar_mask_path(image_path)
    if not mask_path.exists():
        return None

    img_array = np.fromfile(str(mask_path), np.uint8)
    mask = cv2.imdecode(img_array, cv2.IMREAD_GRAYSCALE)
    if mask is None:
        return None
    return normalize_binary_mask(mask, shape)


def extract_crop_and_mask(
    image_rgb: np.ndarray,
    coords: CropCoords,
    full_mask: Optional[np.ndarray] = None,
) -> tuple[np.ndarray, MaskArray]:
    x1, y1, x2, y2 = coords
    cropped = image_rgb[y1:y2, x1:x2].copy()
    if cropped.size == 0:
        return cropped, np.zeros((0, 0), dtype=np.uint8)

    if full_mask is None:
        crop_mask = np.full(cropped.shape[:2], 255, dtype=np.uint8)
    else:
        normalized = normalize_binary_mask(full_mask, image_rgb.shape[:2])
        crop_mask = normalized[y1:y2, x1:x2].copy()

    return cropped, crop_mask


def apply_brush(mask: np.ndarray, center: tuple[int, int], radius: int, *, erase: bool) -> np.ndarray:
    if mask.size == 0:
        return mask
    cx, cy = center
    cv2.circle(mask, (int(cx), int(cy)), max(1, int(radius)), 0 if erase else 255, thickness=-1)
    return mask


def apply_mask_overlay_rgb(
    image_rgb: np.ndarray,
    mask: Optional[np.ndarray],
    *,
    tint_rgb: tuple[int, int, int] = (255, 96, 96),
    alpha: float = 0.60,
) -> np.ndarray:
    if image_rgb is None or image_rgb.size == 0:
        return image_rgb

    normalized = normalize_binary_mask(mask, image_rgb.shape[:2]) if mask is not None else np.full(image_rgb.shape[:2], 255, dtype=np.uint8)
    overlay = image_rgb.astype(np.float32).copy()
    masked_out = normalized == 0
    if np.any(masked_out):
        tint = np.array(tint_rgb, dtype=np.float32)
        overlay[masked_out] = overlay[masked_out] * (1.0 - alpha) + tint * alpha
    return np.clip(overlay, 0, 255).astype(np.uint8)


def fit_image_to_box(image_rgb: np.ndarray, max_width: int, max_height: int) -> tuple[np.ndarray, float]:
    if image_rgb is None or image_rgb.size == 0:
        return image_rgb, 1.0

    height, width = image_rgb.shape[:2]
    if width <= 0 or height <= 0:
        return image_rgb, 1.0

    scale = min(max_width / width, max_height / height)
    new_width = max(1, int(width * scale))
    new_height = max(1, int(height * scale))
    fitted = cv2.resize(image_rgb, (new_width, new_height), interpolation=cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR)
    return fitted, scale


def _is_usable_foreground_mask(mask: np.ndarray, shape: tuple[int, int], *, max_ratio: float = 0.80) -> bool:
    if mask is None or mask.size == 0:
        return False
    height, width = shape
    total = max(1, int(height * width))
    kept = int(np.count_nonzero(mask))
    ratio = kept / total
    return kept >= max(8, int(total * 0.015)) and ratio <= max_ratio


def _extract_colored_text_mask(image_rgb: np.ndarray) -> Optional[MaskArray]:
    """Extract bright, saturated UI text/icons before falling back to GrabCut."""
    height, width = image_rgb.shape[:2]
    hsv = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2HSV)
    saturation = hsv[:, :, 1]
    value = hsv[:, :, 2]

    # Most game reward/notice text is bright colored foreground on a changing dark background.
    mask = np.where((saturation > 45) & (value > 115), 255, 0).astype(np.uint8)
    kernel = np.ones((2, 2), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=1)

    if _is_usable_foreground_mask(mask, (height, width), max_ratio=0.65):
        return mask
    return None


def auto_extract_foreground_mask(image_rgb: np.ndarray) -> MaskArray:
    if image_rgb is None or image_rgb.size == 0:
        return np.zeros((0, 0), dtype=np.uint8)

    height, width = image_rgb.shape[:2]
    if width < 3 or height < 3:
        return np.full((height, width), 255, dtype=np.uint8)

    colored_text_mask = _extract_colored_text_mask(image_rgb)
    if colored_text_mask is not None:
        return colored_text_mask

    image_bgr = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR)
    gc_mask = np.full((height, width), cv2.GC_PR_FGD, dtype=np.uint8)

    border_x = max(1, int(round(width * 0.08)))
    border_y = max(1, int(round(height * 0.08)))
    gc_mask[:border_y, :] = cv2.GC_BGD
    gc_mask[-border_y:, :] = cv2.GC_BGD
    gc_mask[:, :border_x] = cv2.GC_BGD
    gc_mask[:, -border_x:] = cv2.GC_BGD

    inner_x = max(1, int(round(width * 0.18)))
    inner_y = max(1, int(round(height * 0.18)))
    gc_mask[inner_y:height - inner_y, inner_x:width - inner_x] = cv2.GC_PR_FGD

    bg_model = np.zeros((1, 65), np.float64)
    fg_model = np.zeros((1, 65), np.float64)
    rect = (
        border_x,
        border_y,
        max(1, width - border_x * 2),
        max(1, height - border_y * 2),
    )

    try:
        cv2.grabCut(image_bgr, gc_mask, rect, bg_model, fg_model, 4, cv2.GC_INIT_WITH_MASK)
    except cv2.error:
        return np.full((height, width), 255, dtype=np.uint8)

    result_mask = np.where(
        (gc_mask == cv2.GC_FGD) | (gc_mask == cv2.GC_PR_FGD),
        255,
        0,
    ).astype(np.uint8)

    kernel = np.ones((3, 3), np.uint8)
    result_mask = cv2.morphologyEx(result_mask, cv2.MORPH_OPEN, kernel, iterations=1)
    result_mask = cv2.morphologyEx(result_mask, cv2.MORPH_CLOSE, kernel, iterations=1)

    kept = int(np.count_nonzero(result_mask))
    if kept == 0 or kept < int(height * width * 0.05):
        return np.full((height, width), 255, dtype=np.uint8)

    return result_mask
