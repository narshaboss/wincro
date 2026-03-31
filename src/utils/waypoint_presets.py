from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from .config import DATA_DIR
from .json_utils import dump_json_file, load_json_file


PRESET_FILE = DATA_DIR / "waypoint_presets.json"

ARRIVAL_KEY_PRESETS = "arrival_key_presets"
BOSS_IMAGE_PRESETS = "boss_image_presets"
CHARACTER_IMAGE_PRESETS = "character_image_presets"
ITEM_IMAGE_PRESETS = "item_image_presets"


def _default_presets() -> dict[str, list[dict[str, Any]]]:
    return {
        ARRIVAL_KEY_PRESETS: [],
        BOSS_IMAGE_PRESETS: [],
        CHARACTER_IMAGE_PRESETS: [],
        ITEM_IMAGE_PRESETS: [],
    }


def _copy_keys(keys: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [dict(item) for item in (keys or []) if isinstance(item, dict)]


def _sanitize_named_items(items: Any, *, require_path: bool = False, require_keys: bool = False) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if not isinstance(items, list):
        return result
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        normalized: dict[str, Any] = {"name": name}
        if require_path:
            path = str(item.get("path", "")).strip()
            if not path:
                continue
            normalized["path"] = path
        if require_keys:
            keys = _copy_keys(item.get("keys", []))
            if not keys:
                continue
            normalized["keys"] = keys
        result.append(normalized)
    return result


def load_waypoint_presets() -> dict[str, list[dict[str, Any]]]:
    data = _default_presets()
    if not PRESET_FILE.exists():
        return data
    try:
        raw = load_json_file(PRESET_FILE)
    except Exception:
        return data
    if not isinstance(raw, dict):
        return data
    data[ARRIVAL_KEY_PRESETS] = _sanitize_named_items(raw.get(ARRIVAL_KEY_PRESETS), require_keys=True)
    data[BOSS_IMAGE_PRESETS] = _sanitize_named_items(raw.get(BOSS_IMAGE_PRESETS), require_path=True)
    data[CHARACTER_IMAGE_PRESETS] = _sanitize_named_items(raw.get(CHARACTER_IMAGE_PRESETS), require_path=True)
    data[ITEM_IMAGE_PRESETS] = _sanitize_named_items(raw.get(ITEM_IMAGE_PRESETS), require_path=True)
    return data


def save_waypoint_presets(data: dict[str, list[dict[str, Any]]]) -> None:
    payload = {
        ARRIVAL_KEY_PRESETS: _sanitize_named_items(data.get(ARRIVAL_KEY_PRESETS), require_keys=True),
        BOSS_IMAGE_PRESETS: _sanitize_named_items(data.get(BOSS_IMAGE_PRESETS), require_path=True),
        CHARACTER_IMAGE_PRESETS: _sanitize_named_items(data.get(CHARACTER_IMAGE_PRESETS), require_path=True),
        ITEM_IMAGE_PRESETS: _sanitize_named_items(data.get(ITEM_IMAGE_PRESETS), require_path=True),
    }
    PRESET_FILE.parent.mkdir(parents=True, exist_ok=True)
    dump_json_file(PRESET_FILE, payload, ensure_ascii=False, indent=2)


def _image_preset_key(kind: str) -> str:
    if kind == "boss":
        return BOSS_IMAGE_PRESETS
    if kind == "character":
        return CHARACTER_IMAGE_PRESETS
    if kind == "item":
        return ITEM_IMAGE_PRESETS
    raise ValueError(f"unknown image preset kind: {kind}")


def list_arrival_key_presets() -> list[dict[str, Any]]:
    return deepcopy(load_waypoint_presets()[ARRIVAL_KEY_PRESETS])


def list_image_presets(kind: str, *, existing_only: bool = True) -> list[dict[str, Any]]:
    key = _image_preset_key(kind)
    items = deepcopy(load_waypoint_presets()[key])
    if not existing_only:
        return items
    result: list[dict[str, Any]] = []
    for item in items:
        try:
            if Path(item["path"]).exists():
                result.append(item)
        except Exception:
            continue
    return result


def upsert_arrival_key_preset(name: str, keys: list[dict[str, Any]]) -> None:
    preset_name = str(name).strip()
    if not preset_name:
        raise ValueError("preset name is required")
    copied_keys = _copy_keys(keys)
    if not copied_keys:
        raise ValueError("arrival key preset requires at least one key")
    data = load_waypoint_presets()
    items = [item for item in data[ARRIVAL_KEY_PRESETS] if item.get("name") != preset_name]
    items.append({"name": preset_name, "keys": copied_keys})
    data[ARRIVAL_KEY_PRESETS] = items
    save_waypoint_presets(data)


def remove_arrival_key_preset(name: str) -> None:
    preset_name = str(name).strip()
    if not preset_name:
        raise ValueError("preset name is required")
    data = load_waypoint_presets()
    data[ARRIVAL_KEY_PRESETS] = [item for item in data[ARRIVAL_KEY_PRESETS] if item.get("name") != preset_name]
    save_waypoint_presets(data)


def upsert_image_preset(kind: str, name: str, path: str) -> None:
    preset_name = str(name).strip()
    preset_path = str(path).strip()
    if not preset_name:
        raise ValueError("preset name is required")
    if not preset_path:
        raise ValueError("image path is required")
    key = _image_preset_key(kind)
    data = load_waypoint_presets()
    items = [item for item in data[key] if item.get("name") != preset_name]
    items.insert(0, {"name": preset_name, "path": preset_path})
    data[key] = items
    save_waypoint_presets(data)


def remove_image_preset(kind: str, name: str) -> None:
    preset_name = str(name).strip()
    if not preset_name:
        raise ValueError("preset name is required")
    key = _image_preset_key(kind)
    data = load_waypoint_presets()
    data[key] = [item for item in data[key] if item.get("name") != preset_name]
    save_waypoint_presets(data)
