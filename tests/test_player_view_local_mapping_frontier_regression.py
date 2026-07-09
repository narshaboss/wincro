import json
from pathlib import Path
from types import SimpleNamespace

from src.player.game_map import GameMap
from src.ui.player_view import GameModeDialog


def _make_view(waypoints=None):
    view = GameModeDialog.__new__(GameModeDialog)
    view._config = SimpleNamespace(waypoints=waypoints or [])
    return view


def test_local_mapping_frontier_recomputes_unknown_dirs_for_new_target():
    src = Path(r"C:\Projects\wincro\src\ui\player_view.py").read_text(encoding="utf-8")

    assert "def _frontier_unknown_dirs(_fx, _fy):" in src

    recompute_marker = (
        "if explore_target is not None:\n"
        "                                _et_unknown_dirs = _frontier_unknown_dirs(explore_target[0], explore_target[1])"
    )
    assert recompute_marker in src


def test_local_mapping_frontier_validation_uses_shared_unknown_dir_helper():
    src = Path(r"C:\Projects\wincro\src\ui\player_view.py").read_text(encoding="utf-8")

    assert "_et_unknown_dirs = _frontier_unknown_dirs(et[0], et[1])" in src


def test_local_mapping_frontier_skips_negative_boundary_probe_candidates():
    src = Path(r"C:\Projects\wincro\src\ui\player_view.py").read_text(encoding="utf-8")

    assert "def _mapping_probe_coord_allowed(_x, _y):" in src
    assert "if not _mapping_probe_coord_allowed(nx, ny):\n                                        continue" in src
    assert "if not _mapping_probe_coord_allowed(nx, ny):\n                                    continue" in src
    assert "if not _mapping_probe_coord_allowed(nx, ny):\n                            continue" in src
    assert "if not _mapping_probe_coord_allowed(_fx + _fdx, _fy + _fdy):\n                                        continue" in src
    assert src.count("if not _mapping_probe_coord_allowed(nx, ny):") >= 6


def test_mapping_test_route_ends_are_sequential_not_random_targets():
    src = Path(r"C:\Projects\wincro\src\ui\player_view.py").read_text(encoding="utf-8")
    route_pick_slice = src[
        src.index("_mapping_route_end_progress: dict[int, int] = {}"):
        src.index("def _is_sentinel_boss_goal(tidx, tx, ty):")
    ]

    assert "def _route_ends_for_mapping_sequence(tidx):" in route_pick_slice
    assert "if not _re or _rs:" in route_pick_slice
    assert "_seq_target = _current_mapping_route_end_target(tidx)" in route_pick_slice
    assert "elif _re:\n                tx, ty = _rand.choice(_re)" in route_pick_slice
    assert route_pick_slice.index("_seq_target = _current_mapping_route_end_target(tidx)") < route_pick_slice.index("tx, ty = _rand.choice(_re)")


def test_mapping_test_start_keeps_map_saving_enabled():
    src = Path(r"C:\Projects\wincro\src\ui\player_view.py").read_text(encoding="utf-8")
    start_pos = src.index("def _start_execution(self):")
    start_slice = src[
        start_pos:
        src.index("def _stop_execution(self):", start_pos)
    ]
    single_mapping_slice = src[
        src.index("def _run_single_mapping_test(self, idx: int):"):
        src.index("def _run_single_waypoint(self, idx: int):")
    ]

    assert "self._no_save_mode = (not getattr(self, '_is_mapping_test', False)" in start_slice
    assert "and not getattr(self, '_is_mapping', False)" in start_slice
    assert "and not getattr(self, '_single_waypoint_mode', False))" in start_slice
    assert "self._is_mapping_test = True" in single_mapping_slice
    assert single_mapping_slice.index("self._is_mapping_test = True") < single_mapping_slice.rindex("self.after(300, self._start_execution)")


def test_single_mapping_test_starts_from_selected_segment_without_reindexing_targets():
    src = Path(r"C:\Projects\wincro\src\ui\player_view.py").read_text(encoding="utf-8")
    final_idx_slice = src[
        src.index("# 맵핑 모드: 타겟 구간이 final_wp_idx 뒤에 있어도 포함"):
        src.index("# 최종 목표까지의 경유지만 포함")
    ]
    target_init_slice = src[
        src.index("# 맵핑 모드: 해당 구간부터 시작 / 테스트 모드: 처음부터"):
        src.index("try:\n            _ctx_msg = (", src.index("# 맵핑 모드: 해당 구간부터 시작 / 테스트 모드: 처음부터"))
    ]

    assert "elif single_mode and getattr(self, '_is_mapping_test', False):" in final_idx_slice
    assert "final_wp_idx = len(waypoints_raw) - 1" in final_idx_slice
    assert "start_idx = 0" in src
    assert "target_idx = single_idx" in target_init_slice
    assert "_initial_switch_ok = self._switch_segment_map(single_idx)" in target_init_slice
    assert "if (not _initial_switch_ok) and _is_mapping_test:" in target_init_slice
    assert 'self._request_stop_execution(\n                    "initial_segment_switch_failed"' in target_init_slice
    assert target_init_slice.index("target_idx = single_idx") < target_init_slice.index("_initial_switch_ok = self._switch_segment_map(single_idx)")


def test_akgwimun_mapping_plan_has_eleven_sequential_waypoints():
    plan_path = Path(r"C:\Projects\wincro\data\plans\plan_20260708_121550.json")
    data = json.loads(plan_path.read_text(encoding="utf-8-sig"))
    game_mode = data["game_modes"]["rule_584defa4"]
    waypoints = game_mode["waypoints"]

    assert data["name"] == "악귀문 공장"
    assert len(waypoints) == 11
    assert [wp[2] for wp in waypoints] == [f"악귀문{i}굴" for i in range(1, 12)]
    assert game_mode.get("final_waypoint_idx", -1) == -1
    assert all((wp[3].get("route_ends") if len(wp) > 3 and isinstance(wp[3], dict) else []) for wp in waypoints)


def test_mapping_test_route_end_sequence_falls_through_to_next_waypoint_switch():
    src = Path(r"C:\Projects\wincro\src\ui\player_view.py").read_text(encoding="utf-8")
    arrival_slice = src[
        src.index("if _mapping_route_end_sequence_active(target_idx) and _arrived_exact:"):
        src.index("_need_actual_jump = _wait_for_actual_jump(target_idx)")
    ]
    final_route_end_slice = arrival_slice[arrival_slice.index("else:"):]
    transition_slice = src[
        src.index("# 도착 키 입력"):
        src.index("# 테스트 실행 모드: 경유지 도착 알림", src.index("# 도착 키 입력"))
    ]

    assert "self._queue_normal_completion" not in final_route_end_slice
    assert "return" not in final_route_end_slice
    assert "target_idx += 1" in transition_slice
    assert "if target_idx >= len(all_targets):" in transition_slice
    assert "self._switch_segment_map(target_idx, skip_save=_no_save)" in transition_slice
    assert "target_x, target_y = _pick_target(target_idx)" in transition_slice
    assert 'self._append_log(f"▶ 다음: ({tx},{ty}) [{sn}]")' in transition_slice


def test_mapping_test_initial_segment_switch_failure_stops_before_context_logging():
    src = Path(r"C:\Projects\wincro\src\ui\player_view.py").read_text(encoding="utf-8")
    target_init_slice = src[
        src.index("# 맵핑 모드: 해당 구간부터 시작 / 테스트 모드: 처음부터"):
        src.index("try:\n            _ctx_msg = (", src.index("# 맵핑 모드: 해당 구간부터 시작 / 테스트 모드: 처음부터"))
    ]

    stop_pos = target_init_slice.index('self._request_stop_execution(\n                    "initial_segment_switch_failed"')
    assert "_initial_switch_ok = self._switch_segment_map(single_idx)" in target_init_slice
    assert "_initial_switch_ok = self._switch_segment_map(0)" in target_init_slice
    assert "if (not _initial_switch_ok) and _is_mapping_test:" in target_init_slice
    assert target_init_slice.index("if (not _initial_switch_ok) and _is_mapping_test:") < stop_pos
    assert "return" in target_init_slice[stop_pos:]


def test_mapping_test_route_end_arrival_advances_to_next_coordinate_before_completion():
    src = Path(r"C:\Projects\wincro\src\ui\player_view.py").read_text(encoding="utf-8")
    arrival_slice = src[
        src.index("if _mapping_route_end_sequence_active(target_idx) and _arrived_exact:"):
        src.index("_need_actual_jump = _wait_for_actual_jump(target_idx)")
    ]

    assert "_mapping_route_end_progress[int(target_idx)] = _cur_seq_idx + 1" in arrival_slice
    assert "target_x, target_y = _pick_target(target_idx)" in arrival_slice
    assert "edge_fail_counts.clear()" in arrival_slice
    assert "pathfinder.invalidate_path()" in arrival_slice
    assert "continue" in arrival_slice


def test_mapping_test_route_end_arrival_saves_progress_before_next_coordinate_and_completion():
    src = Path(r"C:\Projects\wincro\src\ui\player_view.py").read_text(encoding="utf-8")
    arrival_slice = src[
        src.index("if _mapping_route_end_sequence_active(target_idx) and _arrived_exact:"):
        src.index("_need_actual_jump = _wait_for_actual_jump(target_idx)")
    ]
    advance_slice = arrival_slice[
        arrival_slice.index("if _cur_seq_idx < len(_seq) - 1:"):
        arrival_slice.index("target_x, target_y = _pick_target(target_idx)")
    ]
    completion_slice = arrival_slice[
        arrival_slice.index("else:"):
        arrival_slice.rindex("_append_log(f\"✅ 도착좌표 순차 맵핑 완료")
    ]

    assert "threading.Thread(" not in advance_slice
    assert "self._auto_save_map(" in advance_slice
    assert "segment_idx=getattr(self, '_current_segment_idx', 0)" in advance_slice
    assert "game_map_ref=self._game_map" in advance_slice
    assert "critical=True" in advance_slice
    assert "threading.Thread(" not in completion_slice
    assert "self._auto_save_map(" in completion_slice
    assert "segment_idx=getattr(self, '_current_segment_idx', 0)" in completion_slice
    assert "game_map_ref=self._game_map" in completion_slice
    assert "critical=True" in completion_slice


def test_mapping_test_route_end_final_save_happens_before_normal_completion_path():
    src = Path(r"C:\Projects\wincro\src\ui\player_view.py").read_text(encoding="utf-8")
    arrival_slice = src[
        src.index("if _mapping_route_end_sequence_active(target_idx) and _arrived_exact:"):
        src.index("_need_actual_jump = _wait_for_actual_jump(target_idx)")
    ]
    final_save_pos = arrival_slice.rindex("self._auto_save_map(")
    final_log_pos = arrival_slice.index("도착좌표 순차 맵핑 완료")

    assert final_save_pos < final_log_pos
    assert "_need_actual_jump" not in arrival_slice
    assert "_queue_normal_completion" not in arrival_slice


def test_mapping_test_route_end_sequence_arrival_checks_current_sequence_target_only():
    src = Path(r"C:\Projects\wincro\src\ui\player_view.py").read_text(encoding="utf-8")
    arrival_check_slice = src[
        src.index("_sequence_route_target = ("):
        src.index("if _mapping_route_end_sequence_active(target_idx) and _arrived_exact:")
    ]

    assert "_current_mapping_route_end_target(target_idx)" in arrival_check_slice
    assert "if _sequence_route_target is not None:" in arrival_check_slice
    assert "_arrived_exact = (current_x == _seq_x and current_y == _seq_y)" in arrival_check_slice
    assert "elif _cur_route_ends:" in arrival_check_slice
    assert arrival_check_slice.index("if _sequence_route_target is not None:") < arrival_check_slice.index("elif _cur_route_ends:")


def test_mapping_test_route_end_sequence_portal_checks_current_sequence_target_only():
    src = Path(r"C:\Projects\wincro\src\ui\player_view.py").read_text(encoding="utf-8")
    portal_slice = src[
        src.index("_seq_portal_target = ("):
        src.index("portal_threshold = max", src.index("_seq_portal_target = ("))
    ]

    assert "_current_mapping_route_end_target(target_idx)" in portal_slice
    assert "if _seq_portal_target is not None:" in portal_slice
    assert "was_near_target = abs(prev_x - _seq_px) + abs(prev_y - _seq_py) <= 1" in portal_slice
    assert "elif _portal_re:" in portal_slice
    assert portal_slice.index("if _seq_portal_target is not None:") < portal_slice.index("elif _portal_re:")


def test_mapping_test_route_end_sequence_distance_and_recheck_use_current_target_only():
    src = Path(r"C:\Projects\wincro\src\ui\player_view.py").read_text(encoding="utf-8")
    distance_slice = src[
        src.index("_seq_dist_target = ("):
        src.index("is_final = (target_idx == len(all_targets) - 1)", src.index("_seq_dist_target = ("))
    ]
    recheck_slice = src[
        src.index("if _seq_dist_target is not None:", src.index("_cx, _cy = int(check_x), int(check_y)")):
        src.index("if _check_arrived:", src.index("_cx, _cy = int(check_x), int(check_y)"))
    ]

    assert "_current_mapping_route_end_target(target_idx)" in distance_slice
    assert "if _seq_dist_target is not None:" in distance_slice
    assert "dist = abs(_seq_dist_target[0] - current_x) + abs(_seq_dist_target[1] - current_y)" in distance_slice
    assert "elif _cur_re_dist:" in distance_slice
    assert "_check_arrived = (_cx, _cy) == _seq_dist_target" in recheck_slice
    assert "elif _cur_re_dist:" in recheck_slice


def test_mapping_test_route_end_sequence_disables_local_frontier_phase():
    src = Path(r"C:\Projects\wincro\src\ui\player_view.py").read_text(encoding="utf-8")
    mode_slice = src[
        src.index("def _compute_mapping_modes(seg_idx: int):"):
        src.index("# 맵핑테스트:", src.index("def _compute_mapping_modes(seg_idx: int):"))
    ]

    assert "(not _mapping_route_end_sequence_active(seg_idx))" in mode_slice
    assert "_full = (not _seg_locked)" in mode_slice
    assert "_local = (" in mode_slice


def test_mapping_test_no_start_direct_route_does_not_reenter_local_reprobe():
    src = Path(r"C:\Projects\wincro\src\ui\player_view.py").read_text(encoding="utf-8")
    reprobe_slice = src[
        src.index("def _restart_local_reprobe("):
        src.index("def find_path_direction", src.index("def _restart_local_reprobe("))
    ]

    guard = "if (_is_mapping_test and not _mt_has_starts) or _mt_has_starts or _segment_map_locked or _segment_requires_full_completion:"
    assert guard in reprobe_slice
    assert reprobe_slice.index(guard) < reprobe_slice.index("_local_explore_phase = True")
    assert "_phase2_reprobe_requested = False" in reprobe_slice
    assert "_phase2_reprobe_reason = \"\"" in reprobe_slice


def test_mapping_test_does_not_show_waypoint_arrival_notifications():
    src = Path(r"C:\Projects\wincro\src\ui\player_view.py").read_text(encoding="utf-8")

    assert "and not _is_mapping_test" in src[
        src.index("# 테스트 실행 모드: 경유지 도착 알림"):
        src.index("stuck_count = 0", src.index("# 테스트 실행 모드: 경유지 도착 알림"))
    ]
    assert "and not _is_mapping_test" in src[
        src.index("# 테스트 실행 모드: 포탈 도착 알림"):
        src.index("stuck_count = 0", src.index("# 테스트 실행 모드: 포탈 도착 알림"))
    ]


def test_mapping_test_local_completion_keeps_current_sequence_target():
    src = Path(r"C:\Projects\wincro\src\ui\player_view.py").read_text(encoding="utf-8")
    completion_slice = src[
        src.index("if _mapping_route_end_sequence_active(target_idx):", src.index("# 도착좌표 passable 등록 (A* 도달 가능하도록)")):
        src.index("if _ui_update_ok:", src.index("# 도착좌표 passable 등록 (A* 도달 가능하도록)"))
    ]

    assert "_seq_target = _current_mapping_route_end_target(target_idx)" in completion_slice
    assert "target_x, target_y = _seq_target" in completion_slice
    assert "else:\n                                        for _mte_x, _mte_y in _cur_re:" in completion_slice


def test_local_mapping_probe_rejects_negative_boundary_for_nonnegative_maps():
    view = _make_view([
        (44, 15, "normal-cave", {"route_ends": [{"x": 44, "y": 15}]})
    ])
    gm = GameMap("nonnegative")
    gm.passable = {(0, 11), (1, 11), (0, 12)}

    assert view._mapping_probe_coord_allowed_for_map(
        gm,
        current_pos=(0, 11),
        target_pos=(44, 15),
        candidate_pos=(-1, 11),
        segment_idx=0,
    ) is False
    assert view._mapping_probe_coord_allowed_for_map(
        gm,
        current_pos=(0, 11),
        target_pos=(44, 15),
        candidate_pos=(0, 10),
        segment_idx=0,
    ) is True


def test_local_mapping_probe_keeps_zero_zero_placeholder_negative_boundary():
    view = _make_view([
        (0, 0, "boss-cave", {"arrival_keys": [{"key": "enter"}], "target_image": "boss.png"})
    ])
    gm = GameMap("placeholder")
    gm.passable = {(0, 0), (0, 1), (1, 0)}

    assert view._mapping_probe_coord_allowed_for_map(
        gm,
        current_pos=(0, 0),
        target_pos=(0, 0),
        candidate_pos=(-1, 0),
        segment_idx=0,
    ) is True


def test_local_mapping_probe_keeps_existing_negative_coordinate_maps():
    view = _make_view([
        (-5, 2, "negative-cave", {"route_ends": [{"x": -5, "y": 2}]})
    ])
    gm = GameMap("negative")
    gm.passable = {(-1, 2), (0, 2)}

    assert view._mapping_probe_coord_allowed_for_map(
        gm,
        current_pos=(0, 2),
        target_pos=(-5, 2),
        candidate_pos=(-1, 1),
        segment_idx=0,
    ) is True


def test_local_mapping_frontier_direct_probe_does_not_reuse_cleared_target():
    src = Path(r"C:\Projects\wincro\src\ui\player_view.py").read_text(encoding="utf-8")
    marker = "if explore_target:\n                                _probing_unknown_from_frontier = False"
    start = src.index(marker)
    frontier_slice = src[start:src.index("if explore_target is not None and explore_target_tries >= 15:", start)]

    assert "_probing_unknown_from_frontier = True" in frontier_slice
    assert "explore_target = None" in frontier_slice
    assert "if _probing_unknown_from_frontier or explore_target is None:" in frontier_slice
    assert frontier_slice.index("if _probing_unknown_from_frontier or explore_target is None:") < frontier_slice.index("_cur_dist = abs(current_x - explore_target[0])")


def test_execution_start_clears_stale_runtime_coordinate_snapshots():
    src = Path(r"C:\Projects\wincro\src\ui\player_view.py").read_text(encoding="utf-8")
    start_slice = src[
        src.index("def _start_execution(self):"):
        src.index("self._stop_event.clear()", src.index("def _start_execution(self):"))
    ]

    assert "self._last_runtime_coord_snapshot = None" in start_slice
    assert "self._last_ocr_coord_snapshot = None" in start_slice
    assert "self._last_valid_runtime_coord_snapshot = None" in start_slice
