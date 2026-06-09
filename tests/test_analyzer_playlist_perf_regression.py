from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ANALYZER_VIEW = ROOT / "src" / "ui" / "analyzer_view.py"


def _read_text() -> str:
    return ANALYZER_VIEW.read_text(encoding="utf-8")


def _method_slice(text: str, start: str, end: str) -> str:
    return text[text.index(start):text.index(end, text.index(start))]


def test_playlist_dialog_item_collapse_does_not_rebuild_full_action_list():
    text = _read_text()
    method = _method_slice(
        text,
        "def _toggle_item_collapse(self, rule_id: str):",
        "def _refresh_action_list(self):",
    )

    assert "self._refresh_action_list()" not in method
    assert "self._apply_rule_collapse_state(rule_id)" in method
    assert "self._action_widgets.get(rule_id)" in method


def test_playlist_dialog_uses_lazy_child_containers_for_fast_expand():
    text = _read_text()

    assert "self._action_widgets = {}" in text
    assert "self._collapsible_rule_ids = set()" in text
    assert '"children_rendered": False' in text
    assert '"children_container"' in text
    assert "def _ensure_children_rendered(self, rule_id: str) -> None:" in text
    assert "container.pack_forget()" in text
    assert 'container.pack(fill="x")' in text
    assert "self._render_rules(" in _method_slice(
        text,
        "def _ensure_children_rendered(self, rule_id: str) -> None:",
        "def _apply_rule_collapse_state(self, rule_id: str) -> None:",
    )


def test_playlist_dialog_refresh_rebuilds_collapsible_rule_index():
    text = _read_text()
    refresh_method = _method_slice(
        text,
        "def _refresh_action_list(self):",
        "def _render_rules(self, parent, rules, depth=0, prefix: str = \"\"):",
    )

    assert "self._action_widgets = {}" in refresh_method
    assert "self._collapsible_rule_ids.clear()" in refresh_method
    assert refresh_method.index("self._collapsible_rule_ids.clear()") < refresh_method.index("self._render_rules(")


def test_image_crop_dialog_arrow_keys_move_crop_selection():
    text = _read_text()
    key_method = _method_slice(
        text,
        "def _on_crop_arrow_key(self, event):",
        "def _move_crop_selection(self, dx: int, dy: int) -> bool:",
    )
    move_method = _method_slice(
        text,
        "def _move_crop_selection(self, dx: int, dy: int) -> bool:",
        "def _setup_ui(self):",
    )
    set_method = _method_slice(
        text,
        "def _set_crop_selection(self, coords: tuple[int, int, int, int], *, refresh_mask: bool = True):",
        "def _refresh_preview(self):",
    )
    save_method = _method_slice(
        text,
        "def _save_crop(self):",
        "def _delete_image(self):",
    )

    assert "self._bind_crop_keyboard_controls()" in text
    assert "def _focus_crop_canvas(self):" in text
    assert "takefocus=1" in text
    assert "self.after(100, self._focus_crop_canvas)" in text
    assert "선택 후 방향키=1px 이동, Shift+방향키=10px 이동" in text
    assert 'self._canvas.bind("<Left>", self._on_crop_arrow_key)' in text
    assert 'self._canvas.bind("<Shift-Right>", self._on_crop_arrow_key)' in text
    assert 'step = 10 if (getattr(event, "state", 0) & 0x0001) else 1' in key_method
    assert '"Left": (-step, 0)' in key_method
    assert '"Down": (0, step)' in key_method
    assert "self._move_crop_selection(dx, dy)" in key_method
    assert "self._set_crop_selection(new_coords, refresh_mask=False)" in move_method
    assert "new_x1 = max(0, min(max_x1, x1 + int(dx)))" in move_method
    assert "new_y1 = max(0, min(max_y1, y1 + int(dy)))" in move_method
    assert "self._crop_mask_needs_refresh = True" in set_method
    assert "def _ensure_current_crop_mask(self, *, refresh_view: bool = False):" in set_method
    assert "self._ensure_current_crop_mask()" in save_method
