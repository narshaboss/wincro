from pathlib import Path


def test_stop_execution_captures_segment_and_map_ref_for_async_save():
    text = Path("src/ui/player_view.py").read_text(encoding="utf-8-sig")

    assert "_save_segment_idx = mapping_seg if mapping_seg >= 0 else getattr(self, '_current_segment_idx', 0)" in text
    assert "_save_map_ref = getattr(self, '_game_map', None)" in text
    assert "self._auto_save_map(segment_idx=_save_segment_idx, game_map_ref=_save_map_ref)" in text


def test_stop_mapping_captures_segment_and_map_ref_for_async_save():
    text = Path("src/ui/player_view.py").read_text(encoding="utf-8-sig")

    assert "mapping_seg = getattr(self, '_current_segment_idx', 0)" in text
    assert "_save_map_ref = getattr(self, '_game_map', None)" in text
    assert "self._auto_save_map(\n                        segment_idx=mapping_seg,\n                        game_map_ref=_save_map_ref,\n                        critical=True,\n                    )" in text
