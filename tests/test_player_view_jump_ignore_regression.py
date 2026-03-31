from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAYER_VIEW = ROOT / "src" / "ui" / "player_view.py"


def test_waypoint_card_includes_jump_ignore_button():
    text = PLAYER_VIEW.read_text(encoding="utf-8-sig")

    assert 'text="점프무시 ON" if _ignore_jump_wait else "점프무시 OFF"' in text
    assert "command=lambda idx=i: self._toggle_jump_ignore(idx)" in text
    assert "'jump_ignore_btn': jump_ignore_btn" in text


def test_jump_ignore_toggle_helpers_exist_and_reindex_updates_binding():
    text = PLAYER_VIEW.read_text(encoding="utf-8-sig")

    assert "def _configure_jump_ignore_btn(self, btn, enabled: bool):" in text
    assert "def _toggle_jump_ignore(self, idx):" in text
    assert "cards[i]['jump_ignore_btn'].configure(command=lambda idx=i: self._toggle_jump_ignore(idx))" in text


def test_runtime_target_tuple_and_actual_jump_wait_respect_ignore_flag():
    text = PLAYER_VIEW.read_text(encoding="utf-8-sig")

    assert "ignore_jump_wait = bool(wp[3].get('ignore_jump_wait'))" in text
    assert "route_walls, ignore_jump_wait))" in text
    assert "_ignore_jump_wait = all_targets[_seg_idx][11]" in text
    assert "if bool(_ignore_jump_wait):" in text
    assert "return False" in text[text.index("def _wait_for_actual_jump(_seg_idx):"):text.index("def _clear_boss_patrol_route_cache():")]
