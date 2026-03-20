import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.player.game_map import GameMap


def _write_legacy_blocked_map(tmp_path):
    path = tmp_path / "legacy_map.json"
    data = {
        "name": "legacy-map",
        "passable": [[9, 3], [9, 4], [10, 4]],
        "blocked": [
            {"value": [10, 3], "Count": 2},
            {"value": [11, 3], "Count": 2},
        ],
        "soft_blocked": {},
        "blocked_edges": [],
        "patrol_points": [],
        "start_pos": [9, 3],
        "end_pos": [10, 4],
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def test_load_accepts_legacy_blocked_dict_format(tmp_path):
    path = _write_legacy_blocked_map(tmp_path)

    gm = GameMap("legacy")
    assert gm.load(str(path)) is True
    assert (10, 3) in gm.blocked
    assert (11, 3) in gm.blocked
    assert len(gm.passable) == 3


def test_load_and_merge_accepts_legacy_blocked_dict_format(tmp_path):
    path = _write_legacy_blocked_map(tmp_path)

    gm = GameMap("legacy")
    gm.passable = {(0, 0)}
    assert gm.load_and_merge(str(path)) is True
    assert (0, 0) in gm.passable
    assert (10, 3) in gm.blocked
    assert (11, 3) in gm.blocked
