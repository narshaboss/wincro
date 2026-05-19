import ast
from pathlib import Path

from src.player.game_map import GameMap
from src.player.simple_pathfinder import SimplePathfinder


ROOT = Path(__file__).resolve().parents[1]
PLAYER_VIEW = ROOT / "src" / "ui" / "player_view.py"


def _player_view_text():
    return PLAYER_VIEW.read_text(encoding="utf-8-sig")


def _player_view_ast():
    return ast.parse(_player_view_text())


def _line_of(text, needle):
    return text[:text.index(needle)].count("\n") + 1


def _is_not_skip_to_explore_if(node):
    return (
        isinstance(node, ast.If)
        and isinstance(node.test, ast.UnaryOp)
        and isinstance(node.test.op, ast.Not)
        and isinstance(node.test.operand, ast.Name)
        and node.test.operand.id == "_skip_to_explore"
    )


def test_route_only_does_not_expand_local_avoid_to_route_start_segments():
    text = _player_view_text()

    assert "_short_route_local_avoid" not in text
    assert "_segment_has_starts = _segment_has_route_starts(target_idx)" in text
    assert "_local_avoid_mode = not _segment_has_starts" in text


def test_route_only_relax_and_retry_are_guarded_for_failed_chokepoints():
    text = _player_view_text()

    assert "def _should_preserve_route_dir_avoid():" in text
    assert "def _should_use_route_dir_avoid(_d):" in text
    assert "def _is_route_only_failed_chokepoint(_cx, _cy, _dir, _goal_pos):" in text
    helper_slice = text[
        text.index("def _is_route_only_failed_chokepoint(_cx, _cy, _dir, _goal_pos):"):
        text.index("def press_key(direction):")
    ]
    assert "if not _route_only_mode:" not in helper_slice
    assert "_allow_route_dir_relax = not _should_preserve_route_dir_avoid()" in text
    assert "_route_avoid and _dir_avoid" in text
    assert "_route_failed_chokepoint = _is_route_only_failed_chokepoint(" in text
    assert "_preserve_failed_edge = (" in text
    assert "_route_chokepoint_override = _is_route_only_failed_chokepoint(" in text
    assert "if _allow_route_dir_relax or _route_chokepoint_override:" in text


def test_route_only_dir_avoid_uses_only_hard_fail_edges():
    text = _player_view_text()

    assert "def _should_use_route_dir_avoid(_d):" in text
    helper_slice = text[
        text.index("def _should_use_route_dir_avoid(_d):"):
        text.index("# ── 전체테스트/부분실행 맵기반 직행 모드")
    ]
    assert "_ef = edge_fail_counts.get(_dir_key(cx, cy, _d), 0)" in helper_slice
    assert "return _ef >= EDGE_FAIL_MARK_THRESHOLD" in helper_slice
    assert "if _should_use_route_dir_avoid(_da_d):" in text


def test_route_only_local_avoid_requires_goal_rejoin_path():
    text = _player_view_text()

    assert "def _local_avoid_candidate_reaches_goal(_cand_pos):" in text
    assert "avoid_set=_avoid," in text
    assert "if not _local_avoid_candidate_reaches_goal((_nx, _ny)):" in text


def test_route_only_local_avoid_rejoin_does_not_backtrack_through_blocked_edge():
    text = _player_view_text()

    helper_slice = text[
        text.index("def _pick_local_avoid_dir(_cx, _cy, _goal_pos, _blocked_dir):"):
        text.index("def _is_route_only_failed_chokepoint(_cx, _cy, _dir, _goal_pos):")
    ]
    assert "_blocked_next = (_cx + _bdx, _cy + _bdy) if _blocked_dir else None" in helper_slice
    assert "_avoid = {(_cx, _cy)}" in helper_slice
    assert "_avoid.add(_blocked_next)" in helper_slice
    assert "_avoid = {_cand_pos}" not in helper_slice


def test_pathfinder_rejoin_probe_rejects_current_and_blocked_next_backtrack():
    game_map = GameMap(name="local-avoid-rejoin")
    game_map.passable = {(1, 0), (1, 1), (2, 1), (3, 1)}
    pathfinder = SimplePathfinder(game_map)

    old_probe = pathfinder.find_path(
        (1, 0),
        (3, 1),
        allow_unknown=False,
        respect_blocked_edges=True,
        avoid_set={(1, 0)},
    )
    fixed_probe = pathfinder.find_path(
        (1, 0),
        (3, 1),
        allow_unknown=False,
        respect_blocked_edges=True,
        avoid_set={(1, 1), (2, 1)},
    )

    assert old_probe.found
    assert old_probe.path == [(1, 0), (1, 1), (2, 1), (3, 1)]
    assert not fixed_probe.found


def test_route_only_blocked_primary_fallback_is_reachable_after_skip_to_explore():
    text = _player_view_text()
    tree = _player_view_ast()

    fallback_line = _line_of(text, "if _blocked_primary_dir and not _frontier_probe_phase:")
    skip_blocks = [node for node in ast.walk(tree) if _is_not_skip_to_explore_if(node)]
    assert skip_blocks
    assert all(not (node.lineno <= fallback_line <= node.end_lineno) for node in skip_blocks)


def test_route_only_failed_chokepoint_uses_bounded_retry_and_escape():
    text = _player_view_text()

    assert "ROUTE_ONLY_CHOKE_ESCAPE_THRESHOLD = 6" in text
    assert "def _stop_route_only_chokepoint_retry(_blocked_dir):" in text
    assert "⚠️ 유일통로 반복실패 → 첫칸 재시도 중단" in text
    choke_slice = text[
        text.index("if _route_failed_chokepoint:"):
        text.index("if _local_avoid_mode:")
    ]
    assert "if _blocked_edge_fail >= ROUTE_ONLY_CHOKE_ESCAPE_THRESHOLD:" in choke_slice
    assert "return _stop_route_only_chokepoint_retry(_blocked_primary_dir)" in choke_slice
    assert "_route_relaxed_dir_override = _blocked_primary_dir" in choke_slice
    assert "return _blocked_primary_dir" in choke_slice


def test_route_only_relaxed_path_chokepoint_override_stops_after_threshold():
    text = _player_view_text()

    helper_slice = text[
        text.index("def _apply_route_only_relaxed_result(_route_result, _route_avoid):"):
        text.index("# ── 전체테스트/부분실행 맵기반 직행 모드")
    ]
    assert "_relaxed_edge_fail = edge_fail_counts.get(_dir_key(cx, cy, _relaxed_first_dir), 0)" in helper_slice
    assert "_stop_chokepoint_retry = (" in helper_slice
    assert "_relaxed_edge_fail >= ROUTE_ONLY_CHOKE_ESCAPE_THRESHOLD" in helper_slice
    assert "_stop_route_only_chokepoint_retry(_relaxed_first_dir)" in helper_slice


def test_route_only_can_use_local_avoid_even_when_segment_has_starts():
    text = _player_view_text()

    route_slice = text[
        text.index("if _blocked_primary_dir and not _frontier_probe_phase:"):
        text.index("elif _strict_route_mode and _blocked_primary_dir:")
    ]
    assert "if _blocked_edge_fail < AVOID_EDGE_FAIL_THRESHOLD:" in route_slice
    assert "_route_local_dir = _pick_local_avoid_dir(cx, cy, target_pos, _blocked_primary_dir)" in route_slice
    assert "🧭 경로막힘 국소회피" in text


def test_stable_waypoint_goal_detour_is_disabled_for_route_chokepoints():
    text = _player_view_text()

    assert "AVOID_EDGE_FAIL_THRESHOLD = 2" in text
    assert "if (_stable_waypoint_phase and _edge_fail >= AVOID_EDGE_FAIL_THRESHOLD and" in text
    assert "portal_grace <= 0 and" in text
    assert "not _route_chokepoint):" in text


def test_active_goal_detour_is_cleared_when_anchor_is_no_longer_reachable():
    text = _player_view_text()

    assert "_detour_probe = pathfinder.find_path(" in text
    assert "current_pos," in text
    assert "_active_goal_detour," in text
    assert "_detour_probe_avoid = {target_pos}" in text
    assert "_clear_temporary_goal_detour(target_pos)" in text


def test_route_only_clears_stale_blocked_edges_when_edge_relaxed_probe_succeeds():
    text = _player_view_text()

    assert "def _clear_runtime_blocked_edges_at(_cx, _cy):" in text
    assert "def _clear_runtime_blocked_edges_for_path(_path, _directions):" in text
    assert "respect_blocked_edges=False," in text
    assert "_edge_relaxed_probe = pathfinder.find_path(" in text
    assert "_cleared_edge_count = _clear_runtime_blocked_edges_for_path(" in text
    assert "_edge_relaxed_probe.path, _edge_relaxed_probe.directions" in text
    assert "🧭 경로복구: (" in text


def test_route_only_relaxed_path_can_use_its_first_blocked_direction():
    text = _player_view_text()

    assert "_route_relaxed_dir_override = None" in text
    assert "if _route_only_mode and _route_relaxed_dir_override == _d:" in text
    assert "_route_relaxed_dir_override = _relaxed_first_dir" in text
    assert text.index("_route_relaxed_dir_override = None") < text.index("def _can_take_path_dir(_d):")


def test_route_only_can_force_runtime_reload_when_locked_no_start_segment_current_pos_is_unknown():
    text = _player_view_text()

    assert "def _should_force_runtime_reload_for_unknown_route_position(self, game_map_ref, segment_idx: int, current_pos) -> bool:" in text
    assert "if self._get_segment_route_start_points(segment_idx):" in text
    assert "if not self._is_segment_map_locked(segment_idx):" in text
    assert "return not game_map_ref.is_known(cx, cy)" in text
    assert "_current_pos_unknown = self._should_force_runtime_reload_for_unknown_route_position(" in text
    assert "_needs_runtime_reload = (" in text
    assert "_current_pos_unknown or" in text
