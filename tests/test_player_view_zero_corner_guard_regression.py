from pathlib import Path


def test_zero_corner_placeholder_completion_guard_present():
    source = Path(r"C:\Projects\wincro\src\ui\player_view.py").read_text(encoding="utf-8")

    assert "_current_segment_placeholder_target() == (0, 0)" in source
    assert 'reason=f"{phase}:zero-corner-boundary"' in source
    assert "explore_target = (0, 0)" in source
    assert "_should_mark_current_segment_target_tile(wall_x, wall_y)" in source
    assert "_should_mark_current_segment_target_tile(_mark_wall[0], _mark_wall[1])" in source
    assert "def _pick_zero_corner_probe_direction(cx, cy):" in source
    assert "_zero_corner_dir = _pick_zero_corner_probe_direction(current_x, current_y)" in source
    assert "def _allow_placeholder_target_wall_promotion(tx, ty, edge_fail=0, from_pos=None):" in source
    assert '"""placeholder target은 알려진 인접 접근이 모두 반복 실패한 경우에만 벽 승격한다."""' in source
    assert "_zero_corner_tile_blocked = self._game_map.is_blocked(0, 0)" in source
    assert "if self._game_map.is_blocked(0, 0):" in source
    assert "_allow_placeholder_wall = _allow_placeholder_target_wall_promotion(" in source
    assert "_can_mark_wall_tile = _should_mark_current_segment_target_tile(wall_x, wall_y) or _allow_placeholder_wall" in source
    assert "not _segment_target_is_placeholder(_pfx, _pfy)" in source


def test_zero_corner_probe_uses_axis_rules_not_anchor_pathfinder():
    source = Path(r"C:\Projects\wincro\src\ui\player_view.py").read_text(encoding="utf-8")

    assert 'if cx == 0 and cy > 0:' in source
    assert 'return "up"' in source
    assert 'if cx > 0 and cy == 0:' in source
    assert 'return "left"' in source
    assert "for _ax, _ay in ((0, 0), (1, 0), (0, 1), (1, 1)):" not in source


def test_zero_corner_probe_can_switch_approach_after_repeated_failures():
    source = Path(r"C:\Projects\wincro\src\ui\player_view.py").read_text(encoding="utf-8")

    assert '_up_fail = edge_fail_counts.get(_dir_key(0, 1, "up"), 0)' in source
    assert '_left_fail = edge_fail_counts.get(_dir_key(1, 0, "left"), 0)' in source
    assert 'if _up_fail >= EDGE_FAIL_MARK_THRESHOLD and _left_fail < EDGE_FAIL_MARK_THRESHOLD:' in source
    assert 'if (cx, cy) == (0, 1) and self._game_map.is_passable(1, 1):' in source
    assert 'return "right"' in source
    assert 'if (cx, cy) == (1, 1) and self._game_map.is_passable(1, 0):' in source
    assert 'if (cx, cy) == (1, 0):' in source


def test_placeholder_target_wall_promotion_requires_all_known_adjacent_failures():
    source = Path(r"C:\Projects\\wincro\\src\\ui\\player_view.py").read_text(encoding="utf-8")

    assert "_adj_fail_dirs = []" in source
    assert "if not self._game_map.is_passable(_ax, _ay):" in source
    assert "_adj_fail_dirs.append(edge_fail_counts.get(_dir_key(_ax, _ay, _nd), 0))" in source
    assert "return all(_ef >= EDGE_FAIL_MARK_THRESHOLD for _ef in _adj_fail_dirs)" in source


def test_placeholder_target_stale_blocked_is_sanitized_only_for_partial_maps():
    source = Path(r"C:\Projects\wincro\src\ui\player_view.py").read_text(encoding="utf-8")

    assert "def _sanitize_segment_placeholder_target_tile(self, game_map_ref, segment_idx: int):" in source
    assert "if game_map_ref.is_passable(nx, ny):" in source
    assert "elif not game_map_ref.is_known(nx, ny):" in source
    assert "if passable_neighbors and unknown_neighbors:" in source
    assert 'game_map_ref.blocked.discard((phx, phy))' in source


def test_mapping_test_disables_sentinel_zero_goal_ocr_correction():
    source = Path(r"C:\Projects\wincro\src\ui\player_view.py").read_text(encoding="utf-8")

    assert "def _is_sentinel_boss_goal(tidx, tx, ty):" in source
    assert "if _is_mapping_test:" in source
    assert "return False" in source
