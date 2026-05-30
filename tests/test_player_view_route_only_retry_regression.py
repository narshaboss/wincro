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


def test_route_only_dir_avoid_uses_sustained_direction_blocks():
    text = _player_view_text()

    assert "def _should_use_route_dir_avoid(_d):" in text
    helper_slice = text[
        text.index("def _should_use_route_dir_avoid(_d):"):
        text.index("# ── 전체테스트/부분실행 맵기반 직행 모드")
    ]
    assert "_ef = edge_fail_counts.get(_dir_key(cx, cy, _d), 0)" in helper_slice
    assert "if _route_only_mode:" in helper_slice
    assert "return _ef >= AVOID_EDGE_FAIL_THRESHOLD" in helper_slice
    assert "return _ef >= EDGE_FAIL_MARK_THRESHOLD" in helper_slice
    assert "if _should_use_route_dir_avoid(_da_d):" in text
    assert "_is_route_only_retry_suppressed(cx, cy, _d, iteration)" in helper_slice


def test_route_only_chokepoint_retry_suppression_survives_direction_none_recovery():
    text = _player_view_text()

    assert "route_only_retry_suppressed_until = {}" in text
    assert "def _suppress_route_only_retry(" in text
    assert "def _is_route_only_retry_suppressed(" in text

    stop_slice = text[
        text.index("def _stop_route_only_chokepoint_retry(_blocked_dir):"):
        text.index("def _apply_route_only_relaxed_result(_route_result, _route_avoid):")
    ]
    build_slice = text[
        text.index("def _build_avoid_set(_goal=None, include_dir_avoid=True):"):
        text.index("def _should_preserve_route_dir_avoid():")
    ]
    normal_none_slice = text[
        text.index("if none_dir_streak >= 3 and use_map and (mapping_on or _stable_shortest_route_mode_active()):"):
        text.index("_t_iter_total = int((time.time() - _t_iter_start) * 1000)", text.index("if none_dir_streak >= 3 and use_map and (mapping_on or _stable_shortest_route_mode_active()):"))
    ]

    assert "_suppress_route_only_retry(cx, cy, _blocked_dir, iteration)" in stop_slice
    assert "_is_route_only_retry_suppressed(cx, cy, _sd, iteration)" in build_slice
    assert "route_only_retry_suppressed_until.clear()" not in normal_none_slice


def test_route_only_stop_diagnostic_captures_suppressed_retry_state():
    text = _player_view_text()

    helper_slice = text[
        text.index("def _remember_route_diagnostic(kind, x, y, tx=None, ty=None, **extra):"):
        text.index("def _rebuild_path_index():")
    ]
    stop_slice = text[
        text.index("def _stop_route_only_chokepoint_retry(_blocked_dir):"):
        text.index("def _apply_route_only_relaxed_result(_route_result, _route_avoid):")
    ]
    fail_start = text.index("# A* 경로 실패")
    fail_slice = text[
        fail_start:
        text.index("_fail_msg =", fail_start)
    ]

    assert '"suppressed_dirs"' in helper_slice
    assert '"edge_fail"' in helper_slice
    assert '"route_only_retry_suppressed"' in stop_slice
    assert "failed_dir=_blocked_dir" in stop_slice
    assert '"path_fail"' in fail_slice
    assert "neighbors=[_d for _nx, _ny, _d in _nb] if _nb else []" in fail_slice


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


def test_route_only_failed_chokepoint_can_nudge_before_retry_stop():
    text = _player_view_text()

    assert "def _pick_chokepoint_nudge_dir(_cx, _cy, _goal_pos, _blocked_dir):" in text
    choke_slice = text[
        text.index("if _route_failed_chokepoint:"):
        text.index("if _local_avoid_mode:")
    ]
    assert "_nudge_dir = _pick_chokepoint_nudge_dir(" in choke_slice
    assert "return _nudge_dir" in choke_slice
    assert choke_slice.index("_nudge_dir = _pick_chokepoint_nudge_dir(") < choke_slice.index("return _stop_route_only_chokepoint_retry(_blocked_primary_dir)")


def test_route_only_chokepoint_nudge_can_try_unknown_side_tile():
    text = _player_view_text()

    nudge_slice = text[
        text.index("def _pick_chokepoint_nudge_dir(_cx, _cy, _goal_pos, _blocked_dir):"):
        text.index("def _is_route_only_failed_chokepoint(_cx, _cy, _dir, _goal_pos):")
    ]
    assert "_cand_passable = self._game_map.is_passable(_nx, _ny)" in nudge_slice
    assert "if self._game_map.is_known(_nx, _ny):" in nudge_slice
    assert "if not self._game_map.is_plausible_local_coord(_nx, _ny):" in nudge_slice
    assert "_forward_open = (" in nudge_slice
    assert "0 if _forward_open else 1" in nudge_slice
    assert "0 if _cand_passable else 1" in nudge_slice


def test_route_only_chokepoint_nudge_activates_persistent_detour():
    text = _player_view_text()

    assert "_route_chokepoint_detour = None" in text
    assert "def _activate_route_chokepoint_detour(" in text
    assert "def _get_active_route_chokepoint_detour(" in text
    helper_slice = text[
        text.index("def _apply_route_only_relaxed_result(_route_result, _route_avoid):"):
        text.index("# ── 전체테스트/부분실행 맵기반 직행 모드")
    ]
    route_slice = text[
        text.index("if _route_failed_chokepoint:"):
        text.index("if _local_avoid_mode:")
    ]
    assert "_activate_route_chokepoint_detour(cx, cy, _relaxed_first_dir, _nudge_dir, target_pos)" in helper_slice
    assert "_activate_route_chokepoint_detour(cx, cy, _blocked_primary_dir, _local_dir, target_pos)" in route_slice
    assert "_activate_route_chokepoint_detour(cx, cy, _blocked_primary_dir, _nudge_dir, target_pos)" in route_slice


def test_route_only_chokepoint_detour_uses_phased_forbidden_tiles():
    text = _player_view_text()

    helper_slice = text[
        text.index("def _get_route_chokepoint_detour_forbidden_tiles("):
        text.index("def _is_route_chokepoint_detour_step_allowed(")
    ]
    build_slice = text[
        text.index("def _build_avoid_set(_goal=None, include_dir_avoid=True):"):
        text.index("def _should_preserve_route_dir_avoid():")
    ]
    assert "_active_route_chokepoint_detour = (" in text
    assert "def _get_route_chokepoint_detour_forbidden_tiles(" in text
    assert 'not bool(_detour.get("allow_origin_rejoin"))' in helper_slice
    assert "_current_key == _origin" in helper_slice
    assert "_forbidden.add(_blocked)" in helper_slice
    assert "_get_route_chokepoint_detour_forbidden_tiles(" in build_slice
    assert "_avoid.add(_avoid_pos)" in build_slice


def test_route_only_chokepoint_detour_relaxes_origin_when_astar_is_cut():
    text = _player_view_text()

    helper_slice = text[
        text.index("def _relax_detour_origin_if_path_is_cut("):
        text.index("_route_result = _run_route_only_path(_route_avoid)")
    ]
    none_recovery_slice = text[
        text.index("if direction is None:"):
        text.index("# UI 업데이트", text.index("if direction is None:"))
    ]

    assert 'def _relax_detour_origin_if_path_is_cut(_route_result, _route_avoid):' in text
    assert '_detour["allow_origin_rejoin"] = True' in helper_slice
    assert "_relaxed_result = _run_route_only_path(_relaxed_avoid)" in helper_slice
    assert "유일통로 재합류 허용" in helper_slice
    assert '_detour["allow_origin_rejoin"] = False' in helper_slice
    assert "_relax_detour_origin_if_path_is_cut(_route_result, _route_avoid)" in text
    assert "_clear_route_chokepoint_detour()" in none_recovery_slice


def test_route_only_chokepoint_detour_blocks_cached_path_reentry():
    text = _player_view_text()

    cache_slice = text[
        text.index("# 경로가 있고 현재 위치가 경로 상에 있으면 따라가기"):
        text.index("_skip_to_explore = False")
    ]
    assert "_active_detour_path_blocked = False" in cache_slice
    assert "_get_route_chokepoint_detour_forbidden_tiles(" in cache_slice
    assert "next_pos in _detour_forbidden" in cache_slice
    assert "_active_detour_path_blocked or" in cache_slice
    assert "유일통로 우회경로 캐시차단" in cache_slice


def test_route_only_chokepoint_detour_forces_forward_when_astar_cannot_rejoin():
    text = _player_view_text()

    helper_slice = text[
        text.index("def _get_route_chokepoint_detour_forced_dir("):
        text.index("def _clear_step_watchdog():")
    ]
    route_start = text.index("_route_result, _route_avoid = _apply_route_only_relaxed_result(")
    route_end = text.index("if _route_result.found and _route_result.directions:", route_start)
    route_slice = text[route_start:route_end]
    assert "def _get_route_chokepoint_detour_forced_dir(" in text
    assert "_detour[\"forced_steps\"] = _forced_steps + 1" in helper_slice
    assert "_next_axis_dist > _cur_axis_dist" in helper_slice
    assert "_is_route_chokepoint_detour_step_allowed(_next, _goal_key, _current)" in helper_slice
    assert "_forced_detour_dir = _get_route_chokepoint_detour_forced_dir(cx, cy, target_pos)" in route_slice
    assert "return _forced_detour_dir" in route_slice


def test_route_only_chokepoint_detour_prefers_side_forward_over_blocked_reentry():
    text = _player_view_text()

    helper_slice = text[
        text.index("def _route_result_reenters_detour_blocked("):
        text.index("def _get_route_chokepoint_detour_forced_dir(")
    ]
    route_start = text.index("_route_result, _route_avoid = _apply_route_only_relaxed_result(")
    route_end = text.index("if _route_result.found and _route_result.directions:", route_start)
    route_slice = text[route_start:route_end]
    route_order_slice = text[route_start:route_end + len("if _route_result.found and _route_result.directions:")]

    assert "def _route_result_reenters_detour_blocked(" in text
    assert "_next == _blocked" in helper_slice
    assert "_next == _origin" in helper_slice
    assert "_route_result_reenters_detour_blocked(" in route_slice
    assert "_forced_detour_dir = _get_route_chokepoint_detour_forced_dir(cx, cy, target_pos)" in route_slice
    assert route_order_slice.index("_route_result_reenters_detour_blocked(") < route_order_slice.index("if _route_result.found and _route_result.directions:")


def test_route_only_chokepoint_detour_can_probe_stale_blocked_side_forward_tile():
    text = _player_view_text()

    helper_slice = text[
        text.index("def _get_route_chokepoint_detour_forced_dir("):
        text.index("def _clear_step_watchdog():")
    ]

    assert "_allow_blocked_probe = False" in helper_slice
    assert "_next not in {_origin, _blocked}" in helper_slice
    assert "self._game_map.is_blocked(_next[0], _next[1])" in helper_slice
    assert "self._game_map.is_soft_blocked(_next[0], _next[1])" in helper_slice
    assert 'label=_probe_label' in helper_slice


def test_jolbon_side_detour_rejoins_when_blocked_gate_is_relaxed():
    map_path = next((ROOT / "data" / "maps").glob("9b87b454_15_*3*_map.json"))
    game_map = GameMap(name="jolbon-route-detour")
    assert game_map.load(str(map_path))
    pathfinder = SimplePathfinder(game_map)

    hard_avoid = {(12, 8), (13, 8)}
    upper_blocked = pathfinder.find_path(
        (12, 7),
        (16, 8),
        allow_unknown=True,
        max_iterations=20000,
        unknown_cost=3,
        allow_soft_blocked=True,
        respect_blocked_edges=True,
        avoid_set=hard_avoid,
    )
    assert not upper_blocked.found

    upper_rejoin = pathfinder.find_path(
        (12, 7),
        (16, 8),
        allow_unknown=True,
        max_iterations=20000,
        unknown_cost=3,
        allow_soft_blocked=True,
        respect_blocked_edges=True,
        avoid_set={(12, 8)},
    )
    assert upper_rejoin.found
    assert upper_rejoin.path[:3] == [(12, 7), (13, 7), (13, 8)]
    assert upper_rejoin.directions[:2] == ["right", "down"]

    lower_rejoin = pathfinder.find_path(
        (12, 9),
        (16, 8),
        allow_unknown=True,
        max_iterations=20000,
        unknown_cost=3,
        allow_soft_blocked=True,
        respect_blocked_edges=True,
        avoid_set={(12, 8)},
    )
    assert lower_rejoin.found
    assert lower_rejoin.path[:3] == [(12, 9), (13, 9), (13, 8)]
    assert lower_rejoin.directions[:2] == ["right", "up"]


def test_reverse_l_detour_rejoins_through_origin_when_required():
    game_map = GameMap(name="reverse-l-origin-rejoin")
    corridor = {
        (19, 15),
        (19, 14), (18, 14), (17, 14), (16, 14), (15, 14), (14, 14), (13, 14),
        (13, 15), (13, 16), (13, 17), (13, 18), (13, 19), (13, 20), (13, 21), (13, 22),
    }
    game_map.passable = set(corridor)
    pathfinder = SimplePathfinder(game_map)

    blocked_origin = pathfinder.find_path(
        (19, 15),
        (13, 22),
        allow_unknown=False,
        allow_soft_blocked=True,
        respect_blocked_edges=True,
        avoid_set={(19, 14)},
    )
    rejoin_origin = pathfinder.find_path(
        (19, 15),
        (13, 22),
        allow_unknown=False,
        allow_soft_blocked=True,
        respect_blocked_edges=True,
        avoid_set=None,
    )

    assert not blocked_origin.found
    assert rejoin_origin.found
    assert rejoin_origin.path[:3] == [(19, 15), (19, 14), (18, 14)]
    assert rejoin_origin.directions[:2] == ["up", "left"]


def test_route_only_chokepoint_detour_activation_log_is_not_throttled():
    text = _player_view_text()

    activate_slice = text[
        text.index("def _activate_route_chokepoint_detour("):
        text.index("def _clear_step_watchdog():")
    ]
    assert "유일통로 임시우회 고정" in activate_slice
    assert "iteration % 10" not in activate_slice


def test_route_only_relaxed_chokepoint_tries_nudge_before_stop():
    text = _player_view_text()

    helper_slice = text[
        text.index("def _apply_route_only_relaxed_result(_route_result, _route_avoid):"):
        text.index("# ── 전체테스트/부분실행 맵기반 직행 모드")
    ]
    assert "_route_direct_dir_override = None" in text
    assert "nonlocal _route_relaxed_dir_override, _route_direct_dir_override" in helper_slice
    assert "_nudge_dir = _local_dir or _pick_chokepoint_nudge_dir(" in helper_slice
    assert "_route_direct_dir_override = _nudge_dir" in helper_slice
    assert helper_slice.index("_nudge_dir = _local_dir or _pick_chokepoint_nudge_dir(") < helper_slice.index("_stop_route_only_chokepoint_retry(_relaxed_first_dir)")
    route_slice = text[
        text.index("_route_result, _route_avoid = _apply_route_only_relaxed_result("):
        text.index("if not (_route_result.found and _route_result.directions):")
    ]
    assert "if _route_direct_dir_override:" in route_slice
    assert "return _route_direct_dir_override" in route_slice


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


def test_route_only_relaxed_path_nonchokepoint_stops_after_sustained_failures():
    text = _player_view_text()

    helper_slice = text[
        text.index("def _apply_route_only_relaxed_result(_route_result, _route_avoid):"):
        text.index("# ── 전체테스트/부분실행 맵기반 직행 모드")
    ]
    assert "ROUTE_ONLY_NONCHOKE_RELAX_LIMIT = EDGE_FAIL_MARK_THRESHOLD + AVOID_EDGE_FAIL_THRESHOLD" in text
    assert "_stop_nonchoke_relax = (" in helper_slice
    assert "not _route_chokepoint_override" in helper_slice
    assert "_relaxed_edge_fail >= ROUTE_ONLY_NONCHOKE_RELAX_LIMIT" in helper_slice
    assert "경로회피 완화중단" in helper_slice
    assert "경로막힘 장기정체 우회" in helper_slice
    assert helper_slice.index("if _stop_nonchoke_relax:") < helper_slice.index("elif _stop_chokepoint_retry:")


def test_route_only_can_use_local_avoid_even_when_segment_has_starts():
    text = _player_view_text()

    route_slice = text[
        text.index("if _blocked_primary_dir and not _frontier_probe_phase:"):
        text.index("elif _strict_route_mode and _blocked_primary_dir:")
    ]
    assert "if _blocked_edge_fail < AVOID_EDGE_FAIL_THRESHOLD:" in route_slice
    assert "_route_local_dir = _pick_local_avoid_dir(cx, cy, target_pos, _blocked_primary_dir)" in route_slice
    assert "🧭 경로막힘 국소회피" in text


def test_route_only_local_avoid_activates_persistent_bypass_detour():
    text = _player_view_text()

    route_slice = text[
        text.index("_route_local_dir = _pick_local_avoid_dir(cx, cy, target_pos, _blocked_primary_dir)"):
        text.index("if _local_avoid_mode:")
    ]
    forbidden_slice = text[
        text.index("def _get_route_chokepoint_detour_forbidden_tiles("):
        text.index("def _is_route_chokepoint_detour_step_allowed(")
    ]
    assert "_activate_route_chokepoint_detour(" in route_slice
    assert "keep_blocked_avoid=True" in route_slice
    assert '"keep_blocked_avoid": bool(keep_blocked_avoid),' in text
    assert 'bool(_detour.get("keep_blocked_avoid")) or _current_key == _origin' in forbidden_slice
    assert "경로막힘 임시우회 고정" in text


def test_local_bypass_avoid_prevents_side_tile_from_returning_to_origin():
    game_map = GameMap(name="local-bypass-rejoin")
    game_map.passable = {
        (11, 21),
        (11, 22),
        (12, 21),
        (12, 22),
        (13, 21),
        (13, 22),
    }
    pathfinder = SimplePathfinder(game_map)

    backtrack = pathfinder.find_path((11, 21), (13, 22), allow_unknown=False)
    bypass = pathfinder.find_path(
        (11, 21),
        (13, 22),
        allow_unknown=False,
        avoid_set={(11, 22), (12, 22)},
    )

    assert backtrack.found
    assert backtrack.directions[0] == "down"
    assert bypass.found
    assert bypass.directions[:2] == ["right", "right"]
    assert (11, 22) not in bypass.path
    assert (12, 22) not in bypass.path[:-1]


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


def test_route_only_stale_edge_recovery_preserves_freshly_marked_edges():
    text = _player_view_text()

    helper_slice = text[
        text.index("runtime_blocked_edge_guard_until = {}"):
        text.index("def _is_dir_blocked(x, y, d, now_iter):")
    ]
    clear_slice = text[
        text.index("def _clear_runtime_blocked_edges_for_path(_path, _directions):"):
        text.index("def press_key(direction):")
    ]
    mark_slice = text[
        text.index("if _edge_mode:"):
        text.index("if _ui_update_ok:", text.index("if _edge_mode:"))
    ]
    success_slice = text[
        text.index("if moved:"):
        text.index("# 왔던 방향의 반대를 explored_from에 기록")
    ]

    assert "FRESH_BLOCKED_EDGE_GUARD_TTL = 36" in text
    assert "def _mark_fresh_blocked_edge(x, y, d, now_iter):" in helper_slice
    assert "def _is_fresh_blocked_edge(x, y, d, now_iter):" in helper_slice
    assert "if _is_fresh_blocked_edge(_px, _py, _dir, iteration):" in clear_slice
    assert "_mark_fresh_blocked_edge(prev_x, prev_y, last_dir, iteration)" in mark_slice
    assert "_clear_fresh_blocked_edge(prev_x, prev_y, last_dir)" in success_slice


def test_route_only_relaxed_path_can_use_its_first_blocked_direction():
    text = _player_view_text()

    assert "_route_relaxed_dir_override = None" in text
    assert "if _route_only_mode and _route_relaxed_dir_override == _d:" in text
    assert "_route_relaxed_dir_override = _relaxed_first_dir" in text
    assert text.index("_route_relaxed_dir_override = None") < text.index("def _can_take_path_dir(_d):")


def test_route_only_stagnation_guard_defers_pending_chokepoint_move_failure():
    text = _player_view_text()

    guard_slice = text[
        text.index("if _guard_stagnation_iterations > max_stagnation_iterations:"):
        text.index("if _tick_step_watchdog(coord=(current_x, current_y), coord_failed=False):")
    ]

    assert "_pending_route_fail_count = edge_fail_counts.get(_pending_edge_key, 0) + 1" in guard_slice
    assert "_pending_route_fail_count <= ROUTE_ONLY_CHOKE_ESCAPE_THRESHOLD" in guard_slice
    assert '"stagnation_guard_deferred"' in guard_slice
    assert "_guard_stagnation_iterations = max(0, max_stagnation_iterations - 1)" in guard_slice
    assert "if not _defer_stagnation_stop:" in guard_slice
    assert "break" in guard_slice


def test_route_only_stagnation_guard_waits_on_recent_dynamic_gate_failure():
    text = _player_view_text()

    guard_slice = text[
        text.index("if _guard_stagnation_iterations > max_stagnation_iterations:"):
        text.index("if _tick_step_watchdog(coord=(current_x, current_y), coord_failed=False):")
    ]

    assert "_recent_dynamic_gate_snapshot = getattr(self, \"_last_route_diagnostic_snapshot\", None)" in guard_slice
    assert '_diag_kind in {"move_fail", "path_fail", "dynamic_chokepoint_wait", "stagnation_guard_deferred"}' in guard_slice
    assert "_diag_edge_count >= ROUTE_ONLY_CHOKE_ESCAPE_THRESHOLD" in guard_slice
    assert "_guard_stagnation_iterations = max(0, max_stagnation_iterations - 25)" in guard_slice
    assert '"dynamic_chokepoint_wait"' in guard_slice
    assert "유일통로 장애물 대기" in guard_slice


def test_route_only_path_fail_preserves_dynamic_gate_context_for_stagnation_guard():
    text = _player_view_text()

    path_fail_slice = text[
        text.index("if not (_route_result.found and _route_result.directions):"):
        text.index("_fail_msg = f\"⚠️ 최단경로 실패", text.index("if not (_route_result.found and _route_result.directions):"))
    ]
    guard_slice = text[
        text.index("if _guard_stagnation_iterations > max_stagnation_iterations:"):
        text.index("if _tick_step_watchdog(coord=(current_x, current_y), coord_failed=False):")
    ]

    assert "_path_fail_failed_to = None" in path_fail_slice
    assert "_path_fail_failed_dir = None" in path_fail_slice
    assert "_path_fail_edge_count = 0" in path_fail_slice
    assert "_direction_between((cx, cy), (_apx, _apy))" in path_fail_slice
    assert "_remember_dynamic_chokepoint_gate(" in path_fail_slice
    assert 'source="path_fail"' in path_fail_slice
    assert "failed_to=_path_fail_failed_to" in path_fail_slice
    assert "failed_edge_count=_path_fail_edge_count if _path_fail_failed_to is not None else None" in path_fail_slice
    assert '_diag_kind in {"move_fail", "path_fail", "dynamic_chokepoint_wait", "stagnation_guard_deferred"}' in guard_slice
    assert '_diag_avoid = _recent_dynamic_gate_snapshot.get("avoid") or []' in guard_slice
    assert "_diag_failed_to = tuple(_diag_avoid_items[0])" in guard_slice


def test_route_only_dynamic_chokepoint_gate_centralizes_move_path_and_stop_guard():
    text = _player_view_text()

    helper_slice = text[
        text.index("_dynamic_chokepoint_gate = None"):
        text.index("def _collect_route_direction_state(")
    ]
    move_fail_slice = text[
        text.index("if ((_stable_waypoint_phase or _patrolling_route_phase) and"):
        text.index("if _route_chokepoint and _ui_update_ok", text.index("if ((_stable_waypoint_phase or _patrolling_route_phase) and"))
    ]
    guard_slice = text[
        text.index("if _guard_stagnation_iterations > max_stagnation_iterations:"):
        text.index("if _tick_step_watchdog(coord=(current_x, current_y), coord_failed=False):")
    ]
    success_slice = text[
        text.index("if moved:"):
        text.index("# 왔던 방향의 반대를 explored_from에 기록", text.index("if moved:"))
    ]

    assert "DYNAMIC_CHOKEPOINT_GATE_TTL = 240" in text
    assert "def _remember_dynamic_chokepoint_gate(" in helper_slice
    assert "def _get_dynamic_chokepoint_gate(" in helper_slice
    assert "blocked" in helper_slice and "goal" in helper_slice
    assert "_remember_dynamic_chokepoint_gate(" in move_fail_slice
    assert 'source="move_fail"' in move_fail_slice
    assert "_active_dynamic_gate = _get_dynamic_chokepoint_gate((target_x, target_y))" in guard_slice
    assert "dynamic_gate=_active_dynamic_gate" in guard_slice
    assert guard_slice.index("_active_dynamic_gate = _get_dynamic_chokepoint_gate") < guard_slice.index("_recent_dynamic_gate_snapshot")
    assert "_active_gate = _get_dynamic_chokepoint_gate((target_x, target_y))" in success_slice
    assert "_clear_dynamic_chokepoint_gate(" in success_slice


def test_route_only_can_force_runtime_reload_when_locked_no_start_segment_current_pos_is_unknown():
    text = _player_view_text()

    assert "def _should_force_runtime_reload_for_unknown_route_position(self, game_map_ref, segment_idx: int, current_pos) -> bool:" in text
    assert "if self._get_segment_route_start_points(segment_idx):" in text
    assert "if not self._is_segment_map_locked(segment_idx):" in text
    assert "return not game_map_ref.is_known(cx, cy)" in text
    assert "_current_pos_unknown = self._should_force_runtime_reload_for_unknown_route_position(" in text
    assert "_needs_runtime_reload = (" in text
    assert "_current_pos_unknown or" in text
