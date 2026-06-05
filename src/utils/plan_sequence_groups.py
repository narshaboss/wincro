"""
Helpers for grouped auto-run playlist settings.

The old auto-run setting is a flat pair of lists.  Group support keeps the
runtime path simple by resolving the selected group back into those two lists
right before playback.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import uuid4

from .json_utils import dump_json_file, load_json_file


DEFAULT_GROUP_NAME = "기본 그룹"
MAX_REPEAT_COUNT = 9999


def _group_id() -> str:
    return f"group_{uuid4().hex[:12]}"


def normalize_repeat_count(value: Any, default: int = 1) -> int:
    try:
        count = int(value)
    except (TypeError, ValueError):
        count = default
    if count < 1:
        return 1
    if count > MAX_REPEAT_COUNT:
        return MAX_REPEAT_COUNT
    return count


def normalize_plan_path(path: Any) -> str:
    return str(path or "").strip()


def same_plan_path(left: Any, right: Any) -> bool:
    left_s = normalize_plan_path(left)
    right_s = normalize_plan_path(right)
    if not left_s or not right_s:
        return False
    try:
        if Path(left_s).resolve() == Path(right_s).resolve():
            return True
    except Exception:
        pass
    return Path(left_s).name.lower() == Path(right_s).name.lower()


def make_plan_sequence_entry(plan_path: str, repeat_count: int = 1) -> dict:
    return {
        "plan_path": normalize_plan_path(plan_path),
        "repeat_count": normalize_repeat_count(repeat_count),
    }


def make_plan_sequence_group(
    name: str = DEFAULT_GROUP_NAME,
    entries: list[dict] | None = None,
    group_id: str = "",
    repeat_count: int = 1,
) -> dict:
    normalized_entries = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        path = normalize_plan_path(entry.get("plan_path", ""))
        if not path:
            continue
        normalized_entries.append(make_plan_sequence_entry(path, entry.get("repeat_count", 1)))
    return {
        "group_id": group_id or _group_id(),
        "name": (name or DEFAULT_GROUP_NAME).strip() or DEFAULT_GROUP_NAME,
        "repeat_count": normalize_repeat_count(repeat_count),
        "entries": normalized_entries,
    }


def _legacy_entries(player_config: Any) -> list[dict]:
    paths = list(getattr(player_config, "plan_sequence", []) or [])
    repeats = list(getattr(player_config, "plan_sequence_repeats", []) or [])
    entries: list[dict] = []
    for index, plan_path in enumerate(paths):
        path = normalize_plan_path(plan_path)
        if not path:
            continue
        repeat = repeats[index] if index < len(repeats) else 1
        entries.append(make_plan_sequence_entry(path, repeat))
    return entries


def normalize_plan_sequence_groups(player_config: Any, mutate: bool = True) -> list[dict]:
    raw_groups = getattr(player_config, "plan_sequence_groups", None)
    groups: list[dict] = []

    if isinstance(raw_groups, list):
        for raw in raw_groups:
            if not isinstance(raw, dict):
                continue
            entries = []
            for raw_entry in raw.get("entries", []) or []:
                if not isinstance(raw_entry, dict):
                    continue
                path = normalize_plan_path(raw_entry.get("plan_path", ""))
                if not path:
                    continue
                entries.append(make_plan_sequence_entry(path, raw_entry.get("repeat_count", 1)))
            groups.append(
                make_plan_sequence_group(
                    raw.get("name", DEFAULT_GROUP_NAME),
                    entries,
                    raw.get("group_id", ""),
                    raw.get("repeat_count", 1),
                )
            )

    if not groups:
        groups = [make_plan_sequence_group(DEFAULT_GROUP_NAME, _legacy_entries(player_config))]

    active_id = str(getattr(player_config, "active_plan_sequence_group_id", "") or "")
    group_ids = {group["group_id"] for group in groups}
    if active_id not in group_ids:
        active_id = groups[0]["group_id"] if groups else ""

    if mutate:
        player_config.plan_sequence_groups = deepcopy(groups)
        player_config.active_plan_sequence_group_id = active_id

    return groups


def get_active_plan_sequence_group(player_config: Any) -> dict | None:
    groups = normalize_plan_sequence_groups(player_config, mutate=True)
    active_id = str(getattr(player_config, "active_plan_sequence_group_id", "") or "")
    for group in groups:
        if group.get("group_id") == active_id:
            return group
    return groups[0] if groups else None


def get_active_plan_sequence(player_config: Any) -> tuple[list[str], list[int], dict | None]:
    group = get_active_plan_sequence_group(player_config)
    if not group:
        return [], [], None

    paths: list[str] = []
    repeats: list[int] = []
    normalized_entries = []
    for entry in group.get("entries", []) or []:
        path = normalize_plan_path(entry.get("plan_path", ""))
        if path:
            normalized_entries.append((path, normalize_repeat_count(entry.get("repeat_count", 1))))

    group_repeat = normalize_repeat_count(group.get("repeat_count", 1))
    for _ in range(group_repeat):
        for path, repeat_count in normalized_entries:
            paths.append(path)
            repeats.append(repeat_count)
    return paths, repeats, group


def mirror_active_group_to_legacy(player_config: Any) -> None:
    paths, repeats, _group = get_active_plan_sequence(player_config)
    player_config.plan_sequence = list(paths)
    player_config.plan_sequence_repeats = list(repeats)


def set_active_plan_sequence_group(player_config: Any, group_id: str) -> bool:
    groups = normalize_plan_sequence_groups(player_config, mutate=True)
    for group in groups:
        if group.get("group_id") == group_id:
            player_config.active_plan_sequence_group_id = group_id
            mirror_active_group_to_legacy(player_config)
            return True
    return False


def add_or_update_group_entry(group: dict, plan_path: str, repeat_count: int = 1) -> None:
    path = normalize_plan_path(plan_path)
    if not path:
        return
    repeat = normalize_repeat_count(repeat_count)
    entries = group.setdefault("entries", [])
    for entry in entries:
        if same_plan_path(entry.get("plan_path", ""), path):
            entry["plan_path"] = path
            entry["repeat_count"] = repeat
            return
    entries.append(make_plan_sequence_entry(path, repeat))


def sync_plan_repeat_in_groups(player_config: Any, plan_path: str, repeat_count: int, active_only: bool = True) -> bool:
    repeat = normalize_repeat_count(repeat_count)
    changed = False
    groups = normalize_plan_sequence_groups(player_config, mutate=True)
    active_id = str(getattr(player_config, "active_plan_sequence_group_id", "") or "")
    target_groups = [group for group in groups if group.get("group_id") == active_id] if active_only else groups
    if not target_groups:
        target_groups = groups
    for group in target_groups:
        for entry in group.get("entries", []) or []:
            if same_plan_path(entry.get("plan_path", ""), plan_path):
                entry["repeat_count"] = repeat
                changed = True
    if changed:
        player_config.plan_sequence_groups = groups
        mirror_active_group_to_legacy(player_config)
    return changed


def read_plan_repeat_count(plan_path: str, default: int = 1) -> int:
    try:
        data = load_json_file(plan_path)
        return normalize_repeat_count(data.get("total_repeat_count", default), default=default)
    except Exception:
        return normalize_repeat_count(default)


def write_plan_repeat_count(plan_path: str, repeat_count: int) -> bool:
    path = Path(plan_path)
    if not path.exists():
        return False
    data = load_json_file(path)
    data["total_repeat_count"] = normalize_repeat_count(repeat_count)
    dump_json_file(path, data, ensure_ascii=False, indent=2)
    return True
