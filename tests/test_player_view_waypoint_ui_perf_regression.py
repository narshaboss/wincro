from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAYER_VIEW = ROOT / "src" / "ui" / "player_view.py"


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def test_waypoint_list_initial_build_is_chunked():
    text = _read_text(PLAYER_VIEW)

    assert "def _schedule_waypoint_parent_build(self):" in text
    assert "def _consume_waypoint_parent_build(self, generation: int, start_idx: int):" in text
    assert "self._schedule_waypoint_parent_build()" in text
    assert "self.after_idle(lambda g=generation, s=end_idx: self._consume_waypoint_parent_build(g, s))" in text


def test_group_expand_and_expand_all_are_batched():
    text = _read_text(PLAYER_VIEW)

    assert "def _expand_group_children_chunk(self, parent_idx: int, children: list, start_idx: int, after_widget, built_any: bool):" in text
    assert "def _expand_all_groups_chunk(self, parents: list, start_idx: int, built_any: bool):" in text
    assert "self._expand_group_children_chunk(parent_idx, list(children), 0, card_data['card'], False)" in text
    assert "self._expand_all_groups_chunk(parents, 0, False)" in text


def test_badge_refresh_is_coalesced():
    text = _read_text(PLAYER_VIEW)

    assert '_wp_badge_refresh_running' in text
    assert '_wp_badge_refresh_pending' in text
    assert 'self._wp_badge_refresh_pending = True' in text
    assert 'self._wp_badge_refresh_running = False' in text


def test_grouped_child_cards_reflow_sections_before_pack():
    text = _read_text(PLAYER_VIEW)

    assert "def _reflow_waypoint_card_sections(self, idx: int):" in text
    assert "grouped_child = self._has_group_parent(idx)" in text
    assert "if grouped_child:" in text
    assert "row3.pack(fill=\"x\", padx=8, pady=(0, 2))" in text
    assert "row4.pack(fill=\"x\", padx=8, pady=(0, 1))" in text
    assert "route_frame.pack(fill=\"x\", padx=8, pady=(2, 2))" in text
    assert "self._reflow_waypoint_card_sections(ci)" in text


def test_group_expand_scrolls_first_child_into_view():
    text = _read_text(PLAYER_VIEW)

    assert "self._main_scroll = main" in text
    assert "def _scroll_widget_into_main_view(self, widget, padding: int = 20):" in text
    assert "canvas = getattr(main_scroll, '_parent_canvas', None)" in text
    assert "self.after_idle(lambda w=first_child['card']: self._scroll_widget_into_main_view(w))" in text
