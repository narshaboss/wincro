from pathlib import Path


def test_stop_execution_captures_segment_and_map_ref_for_async_save():
    text = Path("src/ui/player_view.py").read_text(encoding="utf-8-sig")

    assert "was_mapping_test = getattr(self, '_is_mapping_test', False)" in text
    assert "_stop_preserve_learned_blocks = bool(" in text
    assert "and self._should_persist_learned_local_blocks(mapping_seg)" in text
    assert "_save_segment_idx = mapping_seg if mapping_seg >= 0 else getattr(self, '_current_segment_idx', 0)" in text
    assert "_save_map_ref = getattr(self, '_game_map', None)" in text
    assert "_save_preserve_learned_blocks = _stop_preserve_learned_blocks" in text
    assert "setattr(_save_map_ref, \"preserve_learned_blocked\", True)" in text
    assert (
        "self._auto_save_map(\n"
        "                        segment_idx=_save_segment_idx,\n"
        "                        game_map_ref=_save_map_ref,\n"
        "                        critical=bool(_save_preserve_learned_blocks),\n"
        "                    )"
    ) in text


def test_stop_mapping_captures_segment_and_map_ref_for_async_save():
    text = Path("src/ui/player_view.py").read_text(encoding="utf-8-sig")

    assert "mapping_seg = getattr(self, '_current_segment_idx', 0)" in text
    assert "_save_map_ref = getattr(self, '_game_map', None)" in text
    assert "self._auto_save_map(\n                        segment_idx=mapping_seg,\n                        game_map_ref=_save_map_ref,\n                        critical=True,\n                    )" in text
