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
