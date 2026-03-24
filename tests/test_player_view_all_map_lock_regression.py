from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAYER_VIEW = ROOT / "src" / "ui" / "player_view.py"


def test_global_map_lock_button_is_present_next_to_clear_all_maps():
    text = PLAYER_VIEW.read_text(encoding="utf-8-sig")

    assert 'text="전체 맵 초기화"' in text
    assert "self._all_map_lock_btn = ctk.CTkButton(" in text
    assert 'text="전체맵 잠금"' in text
    assert "command=self._toggle_all_map_locks" in text


def test_global_map_lock_toggle_updates_all_waypoints_and_button_state():
    text = PLAYER_VIEW.read_text(encoding="utf-8-sig")

    assert "def _toggle_all_map_locks(self):" in text
    assert "cfg['map_locked'] = lock_all" in text
    assert "self._update_all_map_lock_btn()" in text
    assert "🔒 전체 맵 잠금" in text
    assert "🔓 전체 맵 잠금 해제" in text


def test_per_card_and_global_lock_buttons_share_same_visual_helper():
    text = PLAYER_VIEW.read_text(encoding="utf-8-sig")

    assert "def _configure_map_lock_btn(self, btn, locked: bool):" in text
    assert "self._configure_map_lock_btn(cards[idx].get('map_lock_btn'), locked)" in text
    assert "self._configure_map_lock_btn(cards[i].get('map_lock_btn'), lock_all)" in text
