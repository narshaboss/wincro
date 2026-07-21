import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.player.game_map import GameMap
from src.ui.player_view import GameModeDialog


def _make_view_with_waypoints(waypoints):
    view = GameModeDialog.__new__(GameModeDialog)
    view._config = SimpleNamespace(
        waypoints=waypoints,
        name="test-mode",
        engine_profile="wongak_legacy_v1",
    )
    return view


def _build_placeholder_like_map():
    gm = GameMap()
    passable = {
        (0, 0), (1, 0), (2, 0),
        (0, 1), (1, 1), (2, 1),
        (0, 2), (1, 2), (2, 2),
        (0, 3), (1, 3), (2, 3),
    }
    blocked = {
        (1, -1), (2, -1),
        (3, 0), (3, 1), (3, 2), (3, 3),
        (0, 4), (1, 4), (2, 4),
        (-1, 1), (-1, 2), (-1, 3),
    }
    gm.passable = set(passable)
    gm.blocked = set(blocked)
    gm.soft_blocked = {}
    return gm


def test_placeholder_segment_completion_ignores_placeholder_unknown_edges():
    waypoints = [
        (0, 0, "boss-cave", {
            "arrival_keys": [{"key": "enter"}],
            "route_ends": [],
            "target_image": "boss.png",
        })
    ]
    view = _make_view_with_waypoints(waypoints)
    gm = _build_placeholder_like_map()
    gm.blocked.update({(-1, 0), (0, -1)})

    assert gm.is_fully_explored() is True
    assert view._is_segment_map_complete(gm, 0) is True


def test_placeholder_segment_keeps_negative_origin_unknown_incomplete():
    waypoints = [
        (0, 0, "boss-cave", {
            "arrival_keys": [{"key": "enter"}],
            "route_ends": [],
            "target_image": "boss.png",
        })
    ]
    view = _make_view_with_waypoints(waypoints)
    gm = _build_placeholder_like_map()

    assert view._is_segment_map_complete(gm, 0) is False


def test_zero_zero_image_segment_without_arrival_keys_is_treated_as_placeholder():
    waypoints = [
        (0, 0, "boss-cave", {
            "route_starts": [{"x": 26, "y": 21}],
            "route_ends": [],
            "character_image": "char.png",
            "target_image": "boss.png",
        })
    ]
    view = _make_view_with_waypoints(waypoints)

    assert view._should_persist_segment_end(0) is False


def test_non_placeholder_segment_keeps_unknown_edges_incomplete():
    waypoints = [
        (5, 5, "normal-cave", {
            "arrival_keys": [],
            "route_ends": [(5, 5)],
            "target_image": "",
        })
    ]
    view = _make_view_with_waypoints(waypoints)
    gm = _build_placeholder_like_map()

    assert gm.is_fully_explored() is False
    assert view._is_segment_map_complete(gm, 0) is False


def test_placeholder_segment_still_requires_non_placeholder_unknowns():
    waypoints = [
        (0, 0, "boss-cave", {
            "arrival_keys": [{"key": "enter"}],
            "route_ends": [],
            "target_image": "boss.png",
        })
    ]
    view = _make_view_with_waypoints(waypoints)
    gm = _build_placeholder_like_map()
    # Add a non-placeholder unknown neighbor and ensure completion stays False.
    gm.blocked.discard((3, 2))

    assert view._is_segment_map_complete(gm, 0) is False


def test_placeholder_segment_keeps_interior_unknown_from_origin_incomplete():
    waypoints = [
        (1, 1, "boss-cave", {
            "arrival_keys": [{"key": "enter"}],
            "route_ends": [],
            "target_image": "boss.png",
        })
    ]
    view = _make_view_with_waypoints(waypoints)
    gm = GameMap()
    gm.passable = {
        (0, 1), (1, 1), (2, 1),
        (0, 2), (1, 2), (2, 2),
    }
    gm.blocked = {
        (-1, 1), (3, 1),
        (-1, 2), (3, 2),
        (0, 3), (1, 3), (2, 3),
        (0, 0), (2, 0),
    }
    gm.soft_blocked = {}

    # Placeholder origin (1,1) still has an interior unknown at (1,0),
    # so completion must stay False.
    assert view._is_segment_map_complete(gm, 0) is False


def test_no_start_segment_uses_transient_local_map_path():
    waypoints = [
        (31, 30, "group-root", {
            "route_starts": [],
            "route_ends": [(31, 30)],
            "skip_initial_map_copy": True,
        }),
        (13, 22, "cave1", {
            "route_starts": [(9, 3)],
            "route_ends": [(13, 22)],
            "skip_initial_map_copy": True,
        }),
    ]
    view = _make_view_with_waypoints(waypoints)
    view._config_rule_id = "rule_testabcd"

    assert view._uses_transient_local_map(0) is True
    assert view._uses_transient_local_map(1) is False
    assert view._get_segment_map_name(0).endswith("_local_map.json")
    assert view._get_segment_map_name(1).endswith("_map.json")
    assert not view._get_segment_map_name(1).endswith("_local_map.json")


def test_equivalent_rule_map_fallback_is_removed():
    assert not hasattr(GameModeDialog, "_get_equivalent_rule_prefixes")


def test_verify_saved_map_file_accepts_valid_saved_map(tmp_path):
    view = _make_view_with_waypoints([])
    gm = GameMap("save-check")
    gm.passable = {(1, 1), (1, 2), (2, 2)}
    gm.blocked = {(2, 1)}
    path = tmp_path / "map.json"
    gm.save(str(path))

    assert view._verify_saved_map_file(str(path), expected_passable=3) is True


def test_verify_saved_map_file_rejects_passable_mismatch(tmp_path):
    view = _make_view_with_waypoints([])
    gm = GameMap("save-check")
    gm.passable = {(1, 1), (1, 2), (2, 2)}
    gm.blocked = {(2, 1)}
    path = tmp_path / "map.json"
    gm.save(str(path))

    assert view._verify_saved_map_file(str(path), expected_passable=99) is False
