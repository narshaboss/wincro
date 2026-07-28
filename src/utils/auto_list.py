"""Shared data and colour classification for the automatic list action."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional

import cv2
import numpy as np


AUTO_LIST_ACTION_TYPE = "auto_list"
AUTO_LIST_VALUE_INPUT_ACTION_TYPE = "auto_list_value_input"
AUTO_LIST_MAX_ITEMS = 50
AUTO_LIST_EXTRACTION_BATCH_LIMIT = 10
AUTO_LIST_MIN_ITEM_TIMEOUT = 0.1
AUTO_LIST_MODE_TARGET = "target"
AUTO_LIST_MODE_UNTIL_EXHAUSTED = "until_exhausted"

DEFAULT_AUTO_LIST_CONFIG: Dict[str, Any] = {
    "items": [],
    "item_search_region": None,
    "quantity_region": None,
    "quantity_point": None,
    "status_region": None,
    "processing_mode": AUTO_LIST_MODE_TARGET,
    "reselect_each_cycle": True,
    "max_value": 10,
    "min_value": 1,
    "render_wait": 0.4,
    "after_process_wait": 1.0,
    "item_timeout": 30.0,
    "max_cycles_per_item": 500,
    "max_runtime_per_item": 7200.0,
    "skip_missing_item": False,
    "sample_count": 3,
    "sample_interval": 0.12,
    "unknown_retries": 5,
    "red_min_pixels": 4,
    "green_min_pixels": 4,
}


@dataclass(frozen=True)
class ColourStateResult:
    state: str
    red_pixels: int
    green_pixels: int
    coloured_pixels: int

    @property
    def is_available(self) -> bool:
        return self.state == "available"

    @property
    def is_unavailable(self) -> bool:
        return self.state == "unavailable"


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _bounded_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _normalise_point(value: Any) -> Optional[list[int]]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    try:
        return [int(value[0]), int(value[1])]
    except (TypeError, ValueError):
        return None


def _normalise_region(value: Any) -> Optional[list[int]]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        x1, y1, x2, y2 = (int(part) for part in value)
    except (TypeError, ValueError):
        return None
    if x2 <= x1 or y2 <= y1:
        return None
    return [x1, y1, x2, y2]


def region_center(region: Any) -> Optional[list[int]]:
    """Return the integer center of a validated screen region."""
    normalized = _normalise_region(region)
    if normalized is None:
        return None
    x1, y1, x2, y2 = normalized
    return [(x1 + x2) // 2, (y1 + y2) // 2]


def normalize_auto_list_config(config: Any) -> Dict[str, Any]:
    """Return a validated copy while preserving forward-compatible keys."""
    raw = copy.deepcopy(config) if isinstance(config, dict) else {}
    result = copy.deepcopy(DEFAULT_AUTO_LIST_CONFIG)
    result.update(raw)

    max_value = _bounded_int(result.get("max_value"), 10, 1, 9999)
    min_value = _bounded_int(result.get("min_value"), 1, 1, max_value)
    processing_mode = str(result.get("processing_mode") or AUTO_LIST_MODE_TARGET)
    if processing_mode not in {AUTO_LIST_MODE_TARGET, AUTO_LIST_MODE_UNTIL_EXHAUSTED}:
        processing_mode = AUTO_LIST_MODE_TARGET
    quantity_point = _normalise_point(result.get("quantity_point"))
    quantity_region = _normalise_region(result.get("quantity_region"))
    if quantity_region is None and quantity_point is not None:
        x, y = quantity_point
        quantity_region = [x - 2, y - 2, x + 3, y + 3]
    if quantity_region is not None:
        quantity_point = region_center(quantity_region)
    result.update(
        {
            "max_value": max_value,
            "min_value": min_value,
            "quantity_region": quantity_region,
            # Retained for backward compatibility with older exported plans.
            "quantity_point": quantity_point,
            "status_region": _normalise_region(result.get("status_region")),
            "processing_mode": processing_mode,
            "reselect_each_cycle": bool(result.get("reselect_each_cycle", True)),
            "render_wait": _bounded_float(result.get("render_wait"), 0.4, 0.05, 30.0),
            "after_process_wait": _bounded_float(result.get("after_process_wait"), 1.0, 0.0, 300.0),
            "item_timeout": _bounded_float(
                result.get("item_timeout"),
                30.0,
                AUTO_LIST_MIN_ITEM_TIMEOUT,
                3600.0,
            ),
            "max_cycles_per_item": _bounded_int(result.get("max_cycles_per_item"), 500, 1, 100000),
            "max_runtime_per_item": _bounded_float(
                result.get("max_runtime_per_item"),
                7200.0,
                10.0,
                86400.0,
            ),
            "skip_missing_item": bool(result.get("skip_missing_item", False)),
            "sample_count": _bounded_int(result.get("sample_count"), 3, 1, 9),
            "sample_interval": _bounded_float(result.get("sample_interval"), 0.12, 0.02, 2.0),
            "unknown_retries": _bounded_int(result.get("unknown_retries"), 5, 1, 20),
            "red_min_pixels": _bounded_int(result.get("red_min_pixels"), 4, 1, 10000),
            "green_min_pixels": _bounded_int(result.get("green_min_pixels"), 4, 1, 10000),
        }
    )

    items = []
    for index, item in enumerate(result.get("items") or []):
        if not isinstance(item, dict) or len(items) >= AUTO_LIST_MAX_ITEMS:
            continue
        image = str(item.get("image") or "").strip()
        name = str(item.get("name") or (Path(image).stem if image else f"항목 {index + 1}")).strip()
        items.append(
            {
                **copy.deepcopy(item),
                "name": name or f"항목 {index + 1}",
                "image": image,
                "target_count": _bounded_int(item.get("target_count"), 1, 1, 999999),
                "confidence": _bounded_float(item.get("confidence"), 0.8, 0.3, 1.0),
                "search_region": _normalise_region(item.get("search_region")),
                "enabled": bool(item.get("enabled", True)),
            }
        )
    shared_item_region = _normalise_region(result.get("item_search_region"))
    if shared_item_region is None:
        shared_item_region = next(
            (copy.deepcopy(item["search_region"]) for item in items if item.get("search_region")),
            None,
        )
    result["item_search_region"] = shared_item_region
    if shared_item_region is not None:
        for item in items:
            item["search_region"] = copy.deepcopy(shared_item_region)
    result["items"] = items
    return result


def set_auto_list_item_search_region(config: Dict[str, Any], region: Any) -> Optional[list[int]]:
    """Apply one shared search region to every registered automatic-list item."""
    normalized = _normalise_region(region)
    config["item_search_region"] = copy.deepcopy(normalized)
    for item in config.get("items") or []:
        if isinstance(item, dict):
            item["search_region"] = copy.deepcopy(normalized)
    return normalized


def auto_list_config_for_save(config: Any) -> Dict[str, Any]:
    result = normalize_auto_list_config(config)
    for item in result["items"]:
        if item.get("image"):
            item["image"] = Path(item["image"]).name
    return result


def auto_list_config_from_saved(config: Any, templates_dir: Path) -> Dict[str, Any]:
    result = normalize_auto_list_config(config)
    for item in result["items"]:
        image = str(item.get("image") or "").strip()
        if image and not Path(image).is_absolute():
            item["image"] = str(templates_dir / image)
    return result


def candidate_values(desired: int, minimum: int = 1) -> Iterable[int]:
    """Yield the largest valid value first so the first success is maximal."""
    desired = max(1, int(desired))
    minimum = max(1, min(int(minimum), desired))
    return range(desired, minimum - 1, -1)


def crop_bgr_region(frame_bgr: np.ndarray, region: Any) -> Optional[np.ndarray]:
    normalised = _normalise_region(region)
    if frame_bgr is None or normalised is None or frame_bgr.ndim != 3:
        return None
    x1, y1, x2, y2 = normalised
    height, width = frame_bgr.shape[:2]
    x1, x2 = max(0, x1), min(width, x2)
    y1, y2 = max(0, y1), min(height, y2)
    if x2 <= x1 or y2 <= y1:
        return None
    return frame_bgr[y1:y2, x1:x2]


def translate_screen_region(region: Any, origin_x: int = 0, origin_y: int = 0) -> Optional[list[int]]:
    """Translate absolute desktop coordinates to a captured virtual-screen frame."""
    normalised = _normalise_region(region)
    if normalised is None:
        return None
    x1, y1, x2, y2 = normalised
    return [x1 - int(origin_x), y1 - int(origin_y), x2 - int(origin_x), y2 - int(origin_y)]


def classify_colour_state(
    crop_bgr: np.ndarray,
    *,
    red_min_pixels: int = 4,
    green_min_pixels: int = 4,
) -> ColourStateResult:
    """Classify a status crop. Any meaningful red wins over green."""
    if crop_bgr is None or crop_bgr.size == 0 or crop_bgr.ndim != 3:
        return ColourStateResult("unknown", 0, 0, 0)

    hsv = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2HSV)
    red_low = cv2.inRange(hsv, np.array([0, 80, 60]), np.array([12, 255, 255]))
    red_high = cv2.inRange(hsv, np.array([168, 80, 60]), np.array([179, 255, 255]))
    red_mask = cv2.bitwise_or(red_low, red_high)
    green_mask = cv2.inRange(hsv, np.array([35, 65, 55]), np.array([95, 255, 255]))

    red_pixels = int(cv2.countNonZero(red_mask))
    green_pixels = int(cv2.countNonZero(green_mask))
    coloured_pixels = red_pixels + green_pixels
    if red_pixels >= max(1, int(red_min_pixels)):
        state = "unavailable"
    elif green_pixels >= max(1, int(green_min_pixels)):
        state = "available"
    else:
        state = "unknown"
    return ColourStateResult(state, red_pixels, green_pixels, coloured_pixels)


def majority_colour_state(results: Iterable[ColourStateResult]) -> ColourStateResult:
    samples = list(results)
    if not samples:
        return ColourStateResult("unknown", 0, 0, 0)
    unavailable = sum(sample.is_unavailable for sample in samples)
    available = sum(sample.is_available for sample in samples)
    threshold = len(samples) // 2 + 1
    if unavailable >= threshold:
        state = "unavailable"
    elif available >= threshold:
        state = "available"
    else:
        state = "unknown"
    return ColourStateResult(
        state,
        max(sample.red_pixels for sample in samples),
        max(sample.green_pixels for sample in samples),
        max(sample.coloured_pixels for sample in samples),
    )
