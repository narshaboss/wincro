from pathlib import Path


def test_boss_timing_counter_initialized_each_iteration():
    src = Path(r"C:\Projects\wincro\src\ui\player_view.py").read_text(encoding="utf-8")

    assert "_t_iter_start = time.time()\n                # 보스/순찰 보조값은 일부 분기에서만 채워지므로 반복 시작마다 안전한 기본값으로 초기화한다.\n                _t_boss_ms = 0\n                _move_target = None" in src
    assert "_t_path_ms = max(0, int((time.time() - _t_iter_start) * 1000) - _t_ocr_ms - _t_boss_ms)" in src
    assert '_arm_step_watchdog(f"boss-{boss_mode}", (current_x, current_y), direction, _move_target)' in src
