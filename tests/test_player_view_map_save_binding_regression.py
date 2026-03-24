from pathlib import Path


PLAYER_VIEW = Path(r"C:\Projects\wincro\src\ui\player_view.py")


def test_game_map_is_bound_to_segment_on_runtime_loads():
    text = PLAYER_VIEW.read_text(encoding="utf-8-sig")

    assert "def _bind_game_map_segment(self, game_map_ref, segment_idx: int):" in text
    assert "def _resolve_game_map_segment_idx(self, game_map_ref, fallback_idx: int) -> int:" in text
    assert "self._bind_game_map_segment(self._game_map, 0)" in text
    assert "self._bind_game_map_segment(self._game_map, new_segment_idx)" in text
    assert "self._bind_game_map_segment(fresh_map, segment_idx)" in text


def test_auto_save_prefers_bound_segment_idx_to_current_idx():
    text = PLAYER_VIEW.read_text(encoding="utf-8-sig")

    assert "segment_idx = self._resolve_game_map_segment_idx(game_map_ref, segment_idx)" in text
    assert "segment_idx = self._resolve_game_map_segment_idx(self._game_map, _current_seg_idx)" in text
    assert "[맵핑이상] 저장 세그먼트 보정:" in text
