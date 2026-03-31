from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAYER_VIEW = ROOT / "src" / "ui" / "player_view.py"


def test_waypoint_card_includes_single_play_button():
    text = PLAYER_VIEW.read_text(encoding="utf-8-sig")
    card_slice = text[
        text.index("def _build_single_card(self, i: int, wp, pack_card: bool = True):"):
        text.index("def _on_drag_start(self, event, idx):")
    ]

    assert 'command=lambda idx=i: self._run_single_waypoint(idx)' in card_slice
    assert "'single_play_btn': single_play_btn" in card_slice


def test_single_play_button_has_state_update_helper_and_reindex_binding():
    text = PLAYER_VIEW.read_text(encoding="utf-8-sig")

    assert "def _update_card_single_play_btn(self, idx: int, running: bool):" in text
    assert "cards[i]['single_play_btn'].configure(command=lambda idx=i: self._run_single_waypoint(idx))" in text


def test_single_waypoint_run_uses_general_mode_and_restores_button_on_finish():
    text = PLAYER_VIEW.read_text(encoding="utf-8-sig")

    run_slice = text[
        text.index("def _run_single_waypoint(self, idx: int):"):
        text.index("def _ensure_arduino_ready_for_execution(self, context_label: str) -> bool:")
    ]
    stop_slice = text[
        text.index("def _stop_execution(self):"):
        text.index("def _run_loop(self):")
    ]
    arrival_slice = text[
        text.index("def _on_arrival(self):"):
        text.index("def _build_coordinate_ui(self):")
    ]

    assert "self._is_mapping_test = False" in run_slice
    assert "self._update_card_single_play_btn(idx, True)" in run_slice
    assert "self._update_card_single_play_btn(single_idx, False)" in stop_slice
    assert "self._update_card_single_play_btn(single_idx, False)" in arrival_slice


def test_single_waypoint_run_starts_from_selected_segment():
    text = PLAYER_VIEW.read_text(encoding="utf-8-sig")
    loop_slice = text[
        text.index("# 개별 경유지 테스트 모드"):
        text.index("def _pick_target(tidx):")
    ]

    assert "final_wp_idx = single_idx" in loop_slice
    assert "if single_mode:" in loop_slice
    assert "target_idx = single_idx" in loop_slice
    assert "self._switch_segment_map(single_idx)" in loop_slice
