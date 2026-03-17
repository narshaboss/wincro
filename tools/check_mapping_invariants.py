import glob
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(r'C:\Projects\wincro')
PLAN_PATH = ROOT / 'data' / 'plans' / 'plan_20260205_000742.json'
MAP_DIR = ROOT / 'data' / 'maps'

sys.path.insert(0, str(ROOT))

from src.player.game_map import GameMap
from src.ui.player_view import GameModeDialog


def compute_unknown_origins(game_map):
    passable = set(game_map.passable)
    blocked = set(game_map.blocked)
    soft = set(game_map.soft_blocked)
    origins = []
    for x, y in sorted(passable):
        unknown_dirs = []
        for dx, dy, name in ((0, -1, 'up'), (0, 1, 'down'), (-1, 0, 'left'), (1, 0, 'right')):
            n = (x + dx, y + dy)
            if n in passable or n in blocked or n in soft:
                continue
            unknown_dirs.append(name)
        if unknown_dirs:
            origins.append(((x, y), tuple(unknown_dirs)))
    return origins


def expected_complete(game_map, placeholder):
    if len(game_map.passable) < 10:
        return False
    origins = compute_unknown_origins(game_map)
    if placeholder is None:
        return len(origins) == 0
    for origin, _dirs in origins:
        if origin != placeholder:
            return False
    return True


def should_end_be_empty(meta, wp):
    arrival_keys = meta.get('arrival_keys', []) or []
    route_ends = meta.get('route_ends', []) or []
    has_boss_image = bool(meta.get('target_image'))
    try:
        wp_x = int(wp[0])
        wp_y = int(wp[1])
    except Exception:
        wp_x = wp_y = None
    return bool(arrival_keys and not route_ends and (has_boss_image or (wp_x == 0 and wp_y == 0)))


def main():
    plan = json.loads(PLAN_PATH.read_text(encoding='utf-8'))
    failures = []
    checked = 0

    for mode_key, mode in sorted(plan.get('game_modes', {}).items()):
        if not mode_key.startswith('rule_'):
            continue
        prefix = mode_key[len('rule_'):]
        waypoints = mode.get('waypoints', []) or []
        view = GameModeDialog.__new__(GameModeDialog)
        view._config = SimpleNamespace(waypoints=waypoints, name=mode.get('name', mode_key))
        view._config_rule_id = mode_key

        for idx, wp in enumerate(waypoints):
            map_path = Path(view._get_segment_map_name(idx))
            if not map_path.exists():
                continue
            checked += 1
            gm = GameMap()
            try:
                gm.load(str(map_path))
            except Exception as exc:
                failures.append(f'{map_path.name}: load failed: {exc}')
                continue

            meta = wp[3] if len(wp) >= 4 and isinstance(wp[3], dict) else {}
            placeholder = view._get_segment_placeholder_target(idx)
            actual = view._is_segment_map_complete(gm, idx)
            unknown_origins = compute_unknown_origins(gm)
            expect = expected_complete(gm, placeholder)
            if actual != expect:
                failures.append(
                    f'{map_path.name}: complete mismatch actual={actual} expected={expect} '
                    f'placeholder={placeholder} unknown_origins={len(unknown_origins)}'
                )

            if should_end_be_empty(meta, wp) and gm.end_pos is not None:
                failures.append(f'{map_path.name}: placeholder segment persisted end_pos={gm.end_pos}')

    print(f'checked_maps={checked}')
    if failures:
        print('FAILURES:')
        for item in failures:
            print(item)
        return 1
    print('ALL_MAPPING_INVARIANTS_OK')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
