from pathlib import Path


def test_stop_execution_captures_segment_and_map_ref_for_async_save():
    text = Path("src/ui/player_view.py").read_text(encoding="utf-8-sig")

    assert "_save_segment_idx = mapping_seg if mapping_seg >= 0 else getattr(self, '_current_segment_idx', 0)" in text
    assert "_save_map_ref = getattr(self, '_game_map', None)" in text
    assert "was_mapping_test = getattr(self, '_is_mapping_test', False)" in text
    assert "was_mapping_session = bool(was_mapping or was_mapping_test)" in text
    assert "mapping_session=was_mapping_session" in text
    assert 'reason="execution-stop"' in text


def test_stop_mapping_captures_segment_and_map_ref_for_async_save():
    text = Path("src/ui/player_view.py").read_text(encoding="utf-8-sig")

    assert "mapping_seg = getattr(self, '_current_segment_idx', 0)" in text
    assert "_save_map_ref = getattr(self, '_game_map', None)" in text
    assert "mapping_session=True" in text
    assert 'reason="mapping-stop"' in text
