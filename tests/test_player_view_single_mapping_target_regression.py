from pathlib import Path


def test_single_mapping_test_uses_selected_target_idx():
    source = Path(r"C:\Projects\wincro\src\ui\player_view.py").read_text(encoding="utf-8")

    assert "if 0 <= mapping_seg < len(all_targets):" in source
    assert "target_idx = mapping_seg" in source
    assert "start_idx = 0" in source
    assert "start_idx = single_idx if single_mode else 0" not in source
    assert "elif single_mode and getattr(self, '_is_mapping_test', False):" in source
    assert "final_wp_idx = len(waypoints_raw) - 1" in source
    assert "elif single_mode:" in source
    assert "final_wp_idx = single_idx" in source
    assert "self._switch_segment_map(0)" in source
    assert "경유지 {idx+1}부터 맵핑테스트 시작 (ESC로 중지)" in source
