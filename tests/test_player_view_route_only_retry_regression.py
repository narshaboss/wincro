from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAYER_VIEW = ROOT / "src" / "ui" / "player_view.py"


def test_route_only_does_not_expand_local_avoid_to_route_start_segments():
    text = PLAYER_VIEW.read_text(encoding="utf-8-sig")

    assert "_short_route_local_avoid" not in text
    assert "_segment_has_starts = _segment_has_route_starts(target_idx)" in text
    assert "_local_avoid_mode = not _segment_has_starts" in text


def test_route_only_relax_and_retry_are_guarded_for_failed_chokepoints():
    text = PLAYER_VIEW.read_text(encoding="utf-8-sig")

    assert "def _should_preserve_route_dir_avoid():" in text
    assert "def _is_route_only_failed_chokepoint(_cx, _cy, _dir, _goal_pos):" in text
    helper_slice = text[
        text.index("def _is_route_only_failed_chokepoint(_cx, _cy, _dir, _goal_pos):"):
        text.index("def press_key(direction):")
    ]
    assert "if not _route_only_mode:" not in helper_slice
    assert "_allow_route_dir_relax = not _should_preserve_route_dir_avoid()" in text
    assert "_route_avoid and _dir_avoid and _allow_route_dir_relax" in text
    assert "_route_failed_chokepoint = _is_route_only_failed_chokepoint(" in text
    assert "_preserve_failed_edge = (" in text


def test_route_only_local_avoid_requires_goal_rejoin_path():
    text = PLAYER_VIEW.read_text(encoding="utf-8-sig")

    assert "def _local_avoid_candidate_reaches_goal(_cand_pos):" in text
    assert "avoid_set=_avoid," in text
    assert "if not _local_avoid_candidate_reaches_goal((_nx, _ny)):" in text


def test_route_only_failed_chokepoint_preserves_corridor_axis_when_no_side_path():
    text = PLAYER_VIEW.read_text(encoding="utf-8-sig")

    choke_slice = text[
        text.index("if _route_failed_chokepoint:"):
        text.index("if _local_avoid_mode:")
    ]
    assert "🧭 유일통로 유지 직진" in text
    assert "return _blocked_primary_dir" in choke_slice


def test_stable_waypoint_goal_detour_is_disabled_for_route_chokepoints():
    text = PLAYER_VIEW.read_text(encoding="utf-8-sig")

    assert "if (_stable_waypoint_phase and _edge_fail >= 2 and" in text
    assert "portal_grace <= 0 and" in text
    assert "not _route_chokepoint):" in text


def test_active_goal_detour_is_cleared_when_anchor_is_no_longer_reachable():
    text = PLAYER_VIEW.read_text(encoding="utf-8-sig")

    assert "_detour_probe = pathfinder.find_path(" in text
    assert "current_pos," in text
    assert "_active_goal_detour," in text
    assert "_detour_probe_avoid = {target_pos}" in text
    assert "_clear_temporary_goal_detour(target_pos)" in text


def test_route_only_clears_stale_blocked_edges_when_edge_relaxed_probe_succeeds():
    text = PLAYER_VIEW.read_text(encoding="utf-8-sig")

    assert "def _clear_runtime_blocked_edges_at(_cx, _cy):" in text
    assert "respect_blocked_edges=False," in text
    assert "_edge_relaxed_probe = pathfinder.find_path(" in text
    assert "_cleared_edge_count = _clear_runtime_blocked_edges_at(cx, cy)" in text
    assert "🧭 경로복구: (" in text
