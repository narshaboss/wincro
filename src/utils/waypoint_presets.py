from __future__ import annotations

from copy import deepcopy
import hashlib
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
            confidence = item.get("confidence")
            try:
                if confidence is not None:
                    normalized["confidence"] = float(confidence)
            except Exception:
                pass
            region = item.get("region")
            if isinstance(region, (list, tuple)) and len(region) == 4:
                try:
                    normalized["region"] = [int(v) for v in region]
                except Exception:
                    pass
            ocr_text = str(item.get("ocr_text", "")).strip()
            if ocr_text:
                normalized["ocr_text"] = ocr_text
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


def get_image_preset(kind: str, *, name: str | None = None, path: str | None = None) -> dict[str, Any] | None:
    key = _image_preset_key(kind)
    preset_name = str(name or "").strip()
    preset_path = str(path or "").strip()
    items = deepcopy(load_waypoint_presets()[key])
    for item in items:
        if preset_name and str(item.get("name", "")).strip() == preset_name:
            return item
        if preset_path and str(item.get("path", "")).strip() == preset_path:
            return item
    if preset_path:
        _matched = _find_image_preset_by_fingerprint(items, preset_path)
        if _matched is not None:
            return _matched
    return None


def _hash_file(path: str) -> str:
    _path = Path(str(path or "").strip())
    if not _path.exists() or not _path.is_file():
        return ""
    _h = hashlib.sha256()
    with _path.open("rb") as _f:
        for _chunk in iter(lambda: _f.read(65536), b""):
            if not _chunk:
                break
            _h.update(_chunk)
    return _h.hexdigest()


def _find_image_preset_by_fingerprint(items: list[dict[str, Any]], preset_path: str) -> dict[str, Any] | None:
    try:
        _target_hash = _hash_file(preset_path)
    except Exception:
        _target_hash = ""
    if not _target_hash:
        return None
    for item in items:
        try:
            if _hash_file(str(item.get("path", "")).strip()) == _target_hash:
                return item
        except Exception:
            continue
    return None


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


def upsert_image_preset(
    kind: str,
    name: str,
    path: str,
    *,
    confidence: float | None = None,
    region: list[int] | None = None,
    ocr_text: str | None = None,
) -> None:
    preset_name = str(name).strip()
    preset_path = str(path).strip()
    if not preset_name:
        raise ValueError("preset name is required")
    if not preset_path:
        raise ValueError("image path is required")
    key = _image_preset_key(kind)
    data = load_waypoint_presets()
    existing = next((dict(item) for item in data[key] if item.get("name") == preset_name), None)
    items = [item for item in data[key] if item.get("name") != preset_name]
    payload: dict[str, Any] = {"name": preset_name, "path": preset_path}
    try:
        if confidence is not None:
            payload["confidence"] = float(confidence)
        elif (existing or {}).get("confidence") is not None:
            payload["confidence"] = float(existing["confidence"])
    except Exception:
        pass
    if isinstance(region, (list, tuple)) and len(region) == 4:
        payload["region"] = [int(v) for v in region]
    elif isinstance((existing or {}).get("region"), (list, tuple)) and len((existing or {}).get("region", [])) == 4:
        payload["region"] = [int(v) for v in existing["region"]]
    _ocr_text = str(ocr_text or "").strip()
    if _ocr_text:
        payload["ocr_text"] = _ocr_text
    elif str((existing or {}).get("ocr_text", "")).strip():
        payload["ocr_text"] = str(existing["ocr_text"]).strip()
    items.insert(0, payload)
    data[key] = items
    save_waypoint_presets(data)


def set_image_preset_confidence(kind: str, *, name: str | None = None, path: str | None = None, confidence: float | None = None) -> None:
    key = _image_preset_key(kind)
    preset_name = str(name or "").strip()
    preset_path = str(path or "").strip()
    if not preset_name and not preset_path:
        raise ValueError("preset name or path is required")
    data = load_waypoint_presets()
    updated = False
    items: list[dict[str, Any]] = []
    for item in data[key]:
        matches = False
        if preset_name and str(item.get("name", "")).strip() == preset_name:
            matches = True
        if preset_path and str(item.get("path", "")).strip() == preset_path:
            matches = True
        if matches:
            new_item = dict(item)
            try:
                if confidence is not None:
                    new_item["confidence"] = float(confidence)
                else:
                    new_item.pop("confidence", None)
            except Exception:
                new_item.pop("confidence", None)
            items.append(new_item)
            updated = True
        else:
            items.append(item)
    if not updated:
        raise ValueError("image preset not found")
    data[key] = items
    save_waypoint_presets(data)


def set_image_preset_region(kind: str, *, name: str | None = None, path: str | None = None, region: list[int] | None = None) -> None:
    key = _image_preset_key(kind)
    preset_name = str(name or "").strip()
    preset_path = str(path or "").strip()
    if not preset_name and not preset_path:
        raise ValueError("preset name or path is required")
    data = load_waypoint_presets()
    updated = False
    items: list[dict[str, Any]] = []
    for item in data[key]:
        matches = False
        if preset_name and str(item.get("name", "")).strip() == preset_name:
            matches = True
        if preset_path and str(item.get("path", "")).strip() == preset_path:
            matches = True
        if matches:
            new_item = dict(item)
            if isinstance(region, (list, tuple)) and len(region) == 4:
                new_item["region"] = [int(v) for v in region]
            else:
                new_item.pop("region", None)
            items.append(new_item)
            updated = True
        else:
            items.append(item)
    if not updated:
        raise ValueError("image preset not found")
    data[key] = items
    save_waypoint_presets(data)


def set_image_preset_ocr_text(kind: str, *, name: str | None = None, path: str | None = None, ocr_text: str | None = None) -> None:
    key = _image_preset_key(kind)
    preset_name = str(name or "").strip()
    preset_path = str(path or "").strip()
    if not preset_name and not preset_path:
        raise ValueError("preset name or path is required")
    data = load_waypoint_presets()
    updated = False
    items: list[dict[str, Any]] = []
    for item in data[key]:
        matches = False
        if preset_name and str(item.get("name", "")).strip() == preset_name:
            matches = True
        if preset_path and str(item.get("path", "")).strip() == preset_path:
            matches = True
        if matches:
            new_item = dict(item)
            _ocr_text = str(ocr_text or "").strip()
            if _ocr_text:
                new_item["ocr_text"] = _ocr_text
            else:
                new_item.pop("ocr_text", None)
            items.append(new_item)
            updated = True
        else:
            items.append(item)
    if not updated:
        raise ValueError("image preset not found")
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
