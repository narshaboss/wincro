from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAYER_VIEW = ROOT / "src" / "ui" / "player_view.py"


def test_boss_patrol_flap_helpers_exist():
    text = PLAYER_VIEW.read_text(encoding="utf-8-sig")

    assert "_boss_patrol_recent_samples = []" in text
    assert "def _clear_boss_patrol_recent_samples():" in text
    assert "def _record_boss_patrol_sample(_pos, _goal):" in text
    assert "def _detect_boss_patrol_flap():" in text


def test_boss_patrol_records_and_blocks_abab_oscillation():
    text = PLAYER_VIEW.read_text(encoding="utf-8-sig")

    assert "_record_boss_patrol_sample(current_pos_tuple, _move_target)" in text
    assert "_patrol_flap = _detect_boss_patrol_flap()" in text
    assert "_register_dir_block(current_x, current_y, _osc_dir, iteration, ttl=12)" in text
    assert "_clear_boss_patrol_route_cache()" in text
    assert "순찰왕복 차단" in text


def test_boss_patrol_adjacent_goal_repeated_fail_skips_target():
    text = PLAYER_VIEW.read_text(encoding="utf-8-sig")

    assert "_patrol_adjacent_goal_fail = (" in text
    assert "boss_patrol.skip_current_target()" in text
    assert "⚠️ 순찰 인접목표 반복실패 → 스킵" in text


def test_boss_patrol_adjacent_goal_skip_happens_before_wall_threshold_gate():
    text = PLAYER_VIEW.read_text(encoding="utf-8-sig")

    skip_idx = text.index("_patrol_adjacent_goal_fail = (")
    wall_gate_idx = text.index("if (stuck_count >= _wall_threshold or")

    assert skip_idx < wall_gate_idx


def test_boss_patrol_adjacent_goal_fail_uses_last_issued_move_target():
    text = PLAYER_VIEW.read_text(encoding="utf-8-sig")

    assert "_last_move_target = None" in text
    assert "_last_move_mode = None" in text
    assert '_patrolling_route_phase = (_last_move_mode == "patrolling" and not _boss_chasing)' in text
    assert "if _last_move_target is not None:" in text
    assert "_patrol_goal_origin = (int(_last_move_target[0]), int(_last_move_target[1]))" in text
    assert "_last_move_target = _move_target" in text
    assert "_last_move_mode = boss_mode" in text
