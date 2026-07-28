from pathlib import Path
from types import SimpleNamespace
from time import perf_counter

from src.analyzer.automation_models import AutomationRule
from src.database.models import Action, Sequence
from src.ui.player_view import (
    COMPACT_ACTION_ROW_THRESHOLD,
    PlanDetailDialog,
    SequenceDetailDialog,
    _action_number_label_style,
    _flatten_children_after_parent,
)
from src.ui.theme import COLORS
from src.ui.virtual_scroll import VirtualScrollFrame


ROOT = Path(__file__).resolve().parents[1]
PLAYER_VIEW = ROOT / "src" / "ui" / "player_view.py"


def _read_text() -> str:
    return PLAYER_VIEW.read_text(encoding="utf-8")


def _method_slice(text: str, start: str, end: str) -> str:
    return text[text.index(start):text.index(end, text.index(start))]


def _make_sequence_dialog_stub(sequence: Sequence, collapsed_items: set) -> SequenceDetailDialog:
    dialog = object.__new__(SequenceDetailDialog)
    dialog._sequence = sequence
    dialog._collapsed_items = collapsed_items
    return dialog


def _make_plan_dialog_stub(rules: list[AutomationRule], collapsed_items: set) -> PlanDetailDialog:
    dialog = object.__new__(PlanDetailDialog)
    dialog._plan = SimpleNamespace(initial_rules=rules, monitoring_rules=[])
    dialog._collapsed_items = collapsed_items
    return dialog


class _FakeCanvas:
    def __init__(self):
        self.moved_to = None

    def yview(self):
        return (0.0, 1.0)

    def yview_moveto(self, pos):
        self.moved_to = pos

    def winfo_width(self):
        return 800

    def winfo_height(self):
        return 600

    def configure(self, **_kwargs):
        return None

    def itemconfig(self, *_args, **_kwargs):
        return None


class _FakeWidget:
    def __init__(self):
        self.destroyed = False
        self.y_positions = []
        self.config = {}

    def place_configure(self, **kwargs):
        self.y_positions.append(kwargs.get("y"))

    def place(self, **kwargs):
        self.y_positions.append(kwargs.get("y"))

    def configure(self, **kwargs):
        self.config.update(kwargs)

    def bind(self, *_args, **_kwargs):
        return None

    def winfo_children(self):
        return []

    def destroy(self):
        self.destroyed = True


class _FakeButton:
    def __init__(self):
        self.config = {}

    def configure(self, **kwargs):
        self.config.update(kwargs)


class _FakeAfterQueue:
    def __init__(self):
        self.callbacks = []
        self.next_id = 0

    def after(self, _delay_ms, callback):
        self.next_id += 1
        self.callbacks.append(callback)
        return f"after-{self.next_id}"

    def drain(self, limit: int = 1000):
        count = 0
        while self.callbacks and count < limit:
            callback = self.callbacks.pop(0)
            callback()
            count += 1
        assert not self.callbacks


class _FakeContainer:
    def configure(self, **_kwargs):
        return None


def _make_virtual_scroll_stub(items, visible_widgets):
    frame = object.__new__(VirtualScrollFrame)
    frame._item_height = 10
    frame._buffer_count = 0
    frame._items = list(items)
    frame._visible_widgets = dict(visible_widgets)
    frame._canvas = _FakeCanvas()
    frame._container = _FakeContainer()
    frame._canvas_window = object()
    frame._destroy_callback = None
    frame._render_callback = None
    frame._update_callback = None
    return frame


def test_plan_detail_dialog_collapse_uses_scheduled_refresh():
    text = _read_text()
    method = _method_slice(
        text,
        "def _toggle_item_collapse(self, rule_id: str):",
        "def _move_to_child(self, rule: AutomationRule):",
    )

    assert "self._scrollable.splice_items(index + 1, 0, new_items)" in method
    assert "self._scrollable.splice_items(index + 1, remove_count, [])" in method
    assert 'self._rule_widgets[rule.rule_id]["toggle_btn"] = toggle_btn' in text
    assert "toggle_btn.configure(text=_collapse_toggle_text(rule_id in self._collapsed_items))" in method
    assert "self._refresh_rule_row(rule_id)" not in method
    assert "self._schedule_action_list_refresh()" not in method
    assert "self._refresh_action_list()" not in method


def test_plan_detail_dialog_uses_larger_initial_window_size():
    text = _read_text()
    setup = _method_slice(
        text,
        "class PlanDetailDialog",
        "def _notify_player_partial_run_started",
    )

    assert 'self.geometry("1080x760")' in setup
    assert "self.minsize(980, 700)" in setup
    assert 'self.geometry("950x700")' not in setup


def test_virtual_scroll_destroy_callbacks_prune_stale_widget_maps():
    text = _read_text()
    plan_setup = _method_slice(
        text,
        "class PlanDetailDialog",
        "def _get_flat_rules(self) -> List[AutomationRule]:",
    )
    sequence_setup = _method_slice(
        text,
        "class SequenceDetailDialog",
        "def _get_flat_actions_with_depth(self) -> list:",
    )
    plan_destroy = _method_slice(
        text,
        "def _on_rule_item_destroy(self, item_data: dict, index: int, widget) -> None:",
        "def _create_action_item_virtual(self, parent, rule: AutomationRule, depth: int, index_str: str):",
    )
    sequence_destroy = _method_slice(
        text,
        "def _on_action_item_destroy(self, item_data: dict, index: int, widget) -> None:",
        "def _render_actions_batch(self, actions_list, start_idx, batch_size=5):",
    )
    virtual_text = (ROOT / "src" / "ui" / "virtual_scroll.py").read_text(encoding="utf-8")

    assert "def set_destroy_callback(self, callback):" in virtual_text
    assert "def set_update_callback(self, callback):" in virtual_text
    assert "self._destroy_visible_widget(index, widget)" in virtual_text
    assert "self._scrollable.set_destroy_callback(self._on_rule_item_destroy)" in plan_setup
    assert "self._scrollable.set_update_callback(self._on_rule_item_update)" in plan_setup
    assert "self._scrollable.set_destroy_callback(self._on_action_item_destroy)" in sequence_setup
    assert "self._scrollable.set_update_callback(self._on_action_item_update)" in sequence_setup
    assert "self._rule_widgets.pop(rule_id, None)" in plan_destroy
    assert "self._action_widgets.pop(action_id, None)" in sequence_destroy


def test_plan_and_sequence_detail_reuse_fonts_in_rebuilt_rows():
    text = _read_text()
    plan_text = text[
        text.index("class PlanDetailDialog"):
        text.index("class SequenceDetailDialog")
    ]
    sequence_text = text[
        text.index("class SequenceDetailDialog"):
        text.index("class PlayerView")
    ]

    for class_text, compact_marker, compact_end_marker, row_marker, row_end_marker in (
        (
            plan_text,
            "def _create_compact_rule_item(self, parent, rule: AutomationRule, depth: int = 0, index_str: str = \"1\"):",
            "def _create_action_item(self, parent, rule: AutomationRule, depth: int = 0, index_str: str = \"1\", use_pack: bool = True):",
            "def _create_action_item(self, parent, rule: AutomationRule, depth: int = 0, index_str: str = \"1\", use_pack: bool = True):",
            "def _on_drag_start(self, event, rule: AutomationRule, widget):",
        ),
        (
            sequence_text,
            "def _create_compact_action_item(self, parent, action: Action, depth: int = 0, index_str: str = \"1\", before_widget=None, use_pack: bool = True):",
            "def _create_action_item(self, parent, action: Action, depth: int = 0, index_str: str = \"1\", before_widget=None, use_pack: bool = True):",
            "def _create_action_item(self, parent, action: Action, depth: int = 0, index_str: str = \"1\", before_widget=None, use_pack: bool = True):",
            "def _ensure_action_children_rendered(self, action_id) -> bool:",
        ),
    ):
        init_method = class_text[
            class_text.index("def __init__("):
            class_text.index("def _notify_player_partial_run_started")
            if "def _notify_player_partial_run_started" in class_text
            else class_text.index("def _init_collapsed_items")
        ]
        font_method = class_text[
            class_text.index("def _font(self, size, weight=None):"):
            class_text.index("def _setup_ui(self):")
        ]
        compact_method = class_text[
            class_text.index(compact_marker):
            class_text.index(compact_end_marker, class_text.index(compact_marker))
        ]
        row_method = class_text[
            class_text.index(row_marker):
            class_text.index(row_end_marker, class_text.index(row_marker))
        ]

        assert "self._font_cache = {}" in init_method
        assert 'kwargs = {"family": IOS_FONTS["family"], "size": size}' in font_method
        assert "self._font_cache[key] = cached" in font_method
        assert 'font=self._font(13, "bold")' in compact_method
        assert "font=self._font(12, \"bold\")" in compact_method
        assert "_action_number_label_style(" in compact_method
        assert "font=self._font(*number_font)" in compact_method
        assert 'fg_color=COLORS["accent"]' in compact_method
        assert "font=self._font(13, \"bold\")" in row_method
        assert "font=self._font(12)" in row_method
        assert "_action_number_label_style(" in row_method
        assert "font=self._font(*number_font)" in row_method
        assert 'fg_color=COLORS["accent"]' in row_method


def test_action_number_badge_style_distinguishes_parent_and_child_actions():
    parent_style, parent_font = _action_number_label_style(
        "3",
        0,
        action_color="#2266ff",
        enabled=True,
        compact=True,
    )
    child_style, child_font = _action_number_label_style(
        "3-1",
        1,
        action_color="#2266ff",
        enabled=True,
        compact=True,
    )

    assert parent_style["text"] == "3"
    assert child_style["text"] == "↳ 3-1"
    assert parent_style["fg_color"] != child_style["fg_color"]
    assert parent_style["text_color"] != child_style["text_color"]
    assert child_style["width"] > parent_style["width"]
    assert parent_font[0] > child_font[0]


def test_plan_detail_dialog_action_rows_use_clear_primary_typography():
    text = _read_text()
    plan_text = text[
        text.index("class PlanDetailDialog"):
        text.index("class SequenceDetailDialog")
    ]
    setup_method = plan_text[
        plan_text.index("def _setup_ui(self):"):
        plan_text.index("def _get_flat_rules")
    ]
    compact_method = plan_text[
        plan_text.index("def _create_compact_rule_item"):
        plan_text.index("def _create_action_item(self, parent, rule", plan_text.index("def _create_compact_rule_item"))
    ]

    assert 'text_color=COLORS["text_primary"]' in setup_method
    assert 'font=ctk.CTkFont(size=12, weight="bold")' in setup_method
    assert 'text_color=COLORS["text_secondary"]' not in setup_method
    assert 'font=self._font(12, "bold")' in compact_method
    assert 'font=self._font(13, "bold")' in compact_method
    assert 'text_color=COLORS["text_secondary"]' not in compact_method


def test_action_number_badge_style_is_reapplied_when_virtual_rows_are_reused():
    text = _read_text()
    plan_update = _method_slice(
        text,
        "def _on_rule_item_update(self, item_data: dict, index: int, widget, old_item_data=None) -> None:",
        "def _create_action_item_virtual(self, parent, rule: AutomationRule, depth: int, index_str: str):",
    )
    plan_in_place = _method_slice(
        text,
        "def _update_rule_row_in_place(self, rule: AutomationRule) -> bool:",
        "def _update_rule_parent_summary(self, rule: AutomationRule) -> bool:",
    )
    sequence_update = _method_slice(
        text,
        "def _on_action_item_update(self, item_data: dict, index: int, widget, old_item_data=None) -> None:",
        "def _render_actions_batch(self, actions_list, start_idx, batch_size=5):",
    )
    sequence_in_place = _method_slice(
        text,
        "def _update_compact_action_row(self, action: Action) -> bool:",
        "def _update_action_parent_summary(self, action: Action) -> bool:",
    )

    for method in (plan_update, plan_in_place, sequence_update, sequence_in_place):
        assert "_configure_action_number_label(" in method
        assert "font_factory=getattr(self, \"_font\", None)" in method


def test_virtual_scroll_set_items_reuses_visible_rows_instead_of_full_destroy():
    virtual_text = (ROOT / "src" / "ui" / "virtual_scroll.py").read_text(encoding="utf-8")
    method = _method_slice(
        virtual_text,
        "def set_items(self, items: list, preserve_scroll: bool = False):",
        "def get_items(self):",
    )

    assert "old_visible_by_key" in method
    assert "self._item_identity(item)" in method
    assert "self._item_layout_signature(item)" in method
    assert "widget.place_configure(y=index * self._item_height)" in method
    assert "self._item_index_dirty = True" in method
    assert "self._rebuild_item_index()" not in method
    assert "self._clear_all_widgets()" not in method
    assert "def _item_identity(self, item):" in virtual_text
    assert "def _destroy_visible_widget(self, index, widget, item_data=None):" in virtual_text
    assert "def splice_items(self, start: int, delete_count: int, new_items: list):" in virtual_text
    assert "def _patch_item_index_for_splice(self, old_items, start: int, delete_count: int, new_items: list):" in virtual_text


def test_virtual_scroll_set_items_reuses_matching_visible_widget_instances():
    action_a = SimpleNamespace(action_id="a")
    action_b = SimpleNamespace(action_id="b")
    old_items = [
        {"action": action_a, "depth": 0, "index_str": "1", "parent_id": None},
        {"action": action_b, "depth": 0, "index_str": "2", "parent_id": None},
    ]
    new_items = [
        {"action": action_a, "depth": 0, "index_str": "1", "parent_id": None},
        {"action": action_b, "depth": 0, "index_str": "2", "parent_id": None},
    ]
    widget_a = _FakeWidget()
    widget_b = _FakeWidget()
    frame = _make_virtual_scroll_stub(old_items, {0: widget_a, 1: widget_b})
    rendered = []
    destroyed = []
    frame._render_callback = lambda *_args: rendered.append(_args) or _FakeWidget()
    frame._destroy_callback = lambda item, index, widget: destroyed.append((item, index, widget))

    frame.set_items(new_items, preserve_scroll=True)

    assert frame._visible_widgets == {0: widget_a, 1: widget_b}
    assert widget_a.destroyed is False
    assert widget_b.destroyed is False
    assert rendered == []
    assert destroyed == []
    assert widget_a.y_positions == [0]
    assert widget_b.y_positions == [10]


def test_virtual_scroll_reuses_visible_rows_when_only_index_text_changes():
    action_a = SimpleNamespace(action_id="a")
    old_items = [{"action": action_a, "depth": 0, "index_str": "1", "parent_id": None}]
    new_items = [{"action": action_a, "depth": 0, "index_str": "2", "parent_id": None}]
    widget_a = _FakeWidget()
    frame = _make_virtual_scroll_stub(old_items, {0: widget_a})
    rendered = []
    destroyed = []
    updated = []
    frame._render_callback = lambda *_args: rendered.append(_args) or _FakeWidget()
    frame._destroy_callback = lambda item, index, widget: destroyed.append((item, index, widget))
    frame.set_update_callback(lambda item, index, widget, old_item: updated.append((item, index, widget, old_item)))

    frame.set_items(new_items, preserve_scroll=True)

    assert frame._visible_widgets == {0: widget_a}
    assert widget_a.destroyed is False
    assert rendered == []
    assert destroyed == []
    assert updated == [(new_items[0], 0, widget_a, old_items[0])]


def test_virtual_scroll_keeps_identity_index_for_fast_row_lookup():
    rule = AutomationRule(action_type="click", rule_id="rule_fast")
    action = Action(action_type="click", action_id="action_fast")
    frame = _make_virtual_scroll_stub(
        [
            {"rule": rule, "depth": 0, "index_str": "1"},
            {"action": action, "depth": 0, "index_str": "2"},
        ],
        {},
    )

    assert frame.find_item_index_by_object_id("rule_fast", "AutomationRule") == 0
    assert frame.find_item_index_by_object_id("action_fast", "Action") == 1

    new_rule = AutomationRule(action_type="click", rule_id="new_rule")
    frame.splice_items(1, 0, [{"rule": new_rule, "depth": 0, "index_str": "2"}])

    assert frame.find_item_index_by_object_id("new_rule", "AutomationRule") == 1
    assert frame.find_item_index_by_object(action) == 2


def test_virtual_scroll_splice_patches_index_without_full_rebuild():
    virtual_text = (ROOT / "src" / "ui" / "virtual_scroll.py").read_text(encoding="utf-8")
    method = _method_slice(
        virtual_text,
        "def splice_items(self, start: int, delete_count: int, new_items: list):",
        "def _clear_all_widgets(self):",
    )

    assert "self._patch_item_index_for_splice(None, start, delete_count, new_items)" in method
    assert "self._items[start:end] = new_items" in method
    assert "old_visible_items = {" in method
    assert "old_items[:start] + new_items + old_items[end:]" not in method
    assert "self._rebuild_item_index()" not in method

    patch_method = _method_slice(
        virtual_text,
        "def _patch_item_index_for_splice(self, old_items, start: int, delete_count: int, new_items: list):",
        "def _patch_item_index_for_replace",
    )
    assert "self._item_index_dirty = True" in patch_method
    assert "for identity, index in self._item_index_by_identity.items():" not in patch_method


def test_virtual_scroll_large_splice_defers_identity_index_rebuild():
    actions = [SimpleNamespace(action_id=f"a_{idx}") for idx in range(60000)]
    items = [{"action": action, "depth": 0, "index_str": str(idx + 1)} for idx, action in enumerate(actions)]
    frame = _make_virtual_scroll_stub(items, {})
    frame._item_index_by_identity = {
        frame._item_identity(item): index
        for index, item in enumerate(items)
    }
    frame._item_index_dirty = False
    new_action = SimpleNamespace(action_id="inserted")
    new_item = {"action": new_action, "depth": 0, "index_str": "1"}

    started = perf_counter()
    frame.splice_items(1, 0, [new_item])
    elapsed = perf_counter() - started

    assert frame.get_items()[1] is new_item
    assert frame._item_index_dirty is True
    assert elapsed < 0.05
    assert frame.find_item_index_by_object(new_action) == 1
    assert frame._item_index_dirty is False


def test_virtual_scroll_replace_range_reuses_moved_visible_rows():
    first = AutomationRule(action_type="click", rule_id="first")
    child = AutomationRule(action_type="click", rule_id="child", parent_id="first")
    second = AutomationRule(action_type="click", rule_id="second")
    items = [
        {"rule": first, "depth": 0, "index_str": "1", "parent_id": None},
        {"rule": child, "depth": 1, "index_str": "1.1", "parent_id": "first"},
        {"rule": second, "depth": 0, "index_str": "2", "parent_id": None},
    ]
    first_widget = _FakeWidget()
    second_widget = _FakeWidget()
    frame = _make_virtual_scroll_stub(items, {0: first_widget, 2: second_widget})
    updates = []
    frame._update_callback = lambda item, index, widget, old_item: updates.append(
        (item["rule"].rule_id, index, widget, old_item["rule"].rule_id)
    )

    frame.replace_items_range(
        0,
        3,
        [
            {"rule": second, "depth": 0, "index_str": "1", "parent_id": None},
            {"rule": first, "depth": 0, "index_str": "2", "parent_id": None},
            {"rule": child, "depth": 1, "index_str": "2.1", "parent_id": "first"},
        ],
    )

    assert frame.get_items()[0]["rule"] is second
    assert frame.get_items()[1]["rule"] is first
    assert frame.find_item_index_by_object_id("second", "AutomationRule") == 0
    assert frame.find_item_index_by_object_id("first", "AutomationRule") == 1
    assert frame._visible_widgets[0] is second_widget
    assert frame._visible_widgets[1] is first_widget
    assert not first_widget.destroyed
    assert not second_widget.destroyed
    assert second_widget.y_positions[-1] == 0
    assert first_widget.y_positions[-1] == 10
    assert ("second", 0, second_widget, "second") in updates
    assert ("first", 1, first_widget, "first") in updates


def test_virtual_scroll_equal_length_replace_patches_only_replaced_identities():
    virtual_text = (ROOT / "src" / "ui" / "virtual_scroll.py").read_text(encoding="utf-8")
    method = _method_slice(
        virtual_text,
        "def _patch_item_index_for_replace(self, start: int, delete_count: int, new_items: list, old_identities=None):",
        "def find_item_index_by_object(self, obj) -> int:",
    )

    assert "if delta == 0 and old_identities is not None:" in method
    fast_path = method[
        method.index("if delta == 0 and old_identities is not None:"):
        method.index("next_index = {}", method.index("if delta == 0 and old_identities is not None:"))
    ]
    assert "for identity, index in self._item_index_by_identity.items():" not in fast_path


def test_virtual_scroll_metadata_update_preserves_visible_widgets():
    action = Action(action_type="click", action_id="action")
    old_item = {"action": action, "depth": 0, "index_str": "2"}
    new_item = {"action": action, "depth": 0, "index_str": "1"}
    widget = _FakeWidget()
    frame = _make_virtual_scroll_stub([old_item], {0: widget})
    updates = []
    frame._update_callback = lambda item, index, row, old: updates.append(
        (item["index_str"], index, row, old["index_str"])
    )

    assert frame.update_items_metadata([new_item]) is True

    assert frame.get_items()[0]["index_str"] == "1"
    assert frame._visible_widgets[0] is widget
    assert widget.destroyed is False
    assert updates == [("1", 0, widget, "2")]


def test_virtual_scroll_large_set_items_keeps_visible_widgets_without_bulk_render():
    actions = [SimpleNamespace(action_id=f"a_{idx}") for idx in range(5000)]
    old_items = [{"action": action, "depth": 0, "index_str": str(idx + 1)} for idx, action in enumerate(actions)]
    new_items = [{"action": action, "depth": 0, "index_str": str(idx + 1)} for idx, action in enumerate(actions)]
    visible_widgets = {idx: _FakeWidget() for idx in range(12)}
    frame = _make_virtual_scroll_stub(old_items, visible_widgets)
    frame._canvas.yview = lambda: (0.0, 1.0)
    frame._canvas.winfo_height = lambda: 110
    rendered = []
    destroyed = []
    frame._render_callback = lambda *_args: rendered.append(_args) or _FakeWidget()
    frame._destroy_callback = lambda item, index, widget: destroyed.append((item, index, widget))

    started = perf_counter()
    frame.set_items(new_items, preserve_scroll=True)
    elapsed = perf_counter() - started

    assert frame._visible_widgets == visible_widgets
    assert rendered == []
    assert destroyed == []
    assert elapsed < 0.25


def test_virtual_scroll_large_set_items_defers_identity_index_rebuild():
    actions = [SimpleNamespace(action_id=f"a_{idx}") for idx in range(60000)]
    old_items = [{"action": action, "depth": 0, "index_str": str(idx + 1)} for idx, action in enumerate(actions)]
    new_items = [{"action": action, "depth": 0, "index_str": str(idx + 1)} for idx, action in enumerate(actions)]
    frame = _make_virtual_scroll_stub(old_items, {})
    frame._item_index_by_identity = {
        frame._item_identity(item): index
        for index, item in enumerate(old_items)
    }
    frame._item_index_dirty = False
    frame._canvas.winfo_height = lambda: 110

    started = perf_counter()
    frame.set_items(new_items, preserve_scroll=True)
    elapsed = perf_counter() - started

    assert frame._item_index_dirty is True
    assert frame._item_index_by_identity == {}
    assert elapsed < 0.05
    assert frame.find_item_index_by_object(actions[-1]) == len(actions) - 1
    assert frame._item_index_dirty is False


def test_detail_dialog_parent_lookup_uses_lazy_cache_instead_of_recursive_scan():
    text = _read_text()
    plan_method = _method_slice(
        text,
        "def _find_parent_rule(self, target: AutomationRule) -> Optional[AutomationRule]:",
        "def _find_parent_in_tree(self, rule: AutomationRule, target: AutomationRule)",
    )
    sequence_start = text.index("class SequenceDetailDialog")
    sequence_text = text[sequence_start:]
    sequence_method = _method_slice(
        sequence_text,
        "def _find_parent_action(self, target: Action) -> Optional[Action]:",
        "def _find_parent_in_tree_action(self, action: Action, target: Action)",
    )

    assert 'exact_key = ("object", id(target))' in plan_method
    assert 'return cache.get(("id", target_id))' in plan_method
    assert "_find_parent_in_tree" not in plan_method
    assert 'exact_key = ("object", id(target))' in sequence_method
    assert 'return cache.get(("id", target_id))' in sequence_method
    assert "_find_parent_in_tree_action" not in sequence_method


def test_parent_lookup_cache_is_invalidated_after_attach_operations():
    plan_parent = AutomationRule(action_type="click", rule_id="plan_parent")
    plan_child = AutomationRule(action_type="key_press", rule_id="plan_child")
    plan_dialog = _make_plan_dialog_stub([plan_parent, plan_child], {"plan_parent"})
    plan_dialog._scrollable = _make_virtual_scroll_stub(
        [
            {"rule": plan_parent, "depth": 0, "index_str": "1", "parent_id": None},
            {"rule": plan_child, "depth": 0, "index_str": "2", "parent_id": None},
        ],
        {},
    )
    assert plan_dialog._find_parent_rule(plan_child) is None
    plan_dialog._move_rule_to_target(plan_child, plan_parent)
    assert plan_dialog._find_parent_rule(plan_child) is plan_parent

    sequence_parent = Action(action_type="click", action_id="sequence_parent")
    sequence_child = Action(action_type="key_press", action_id="sequence_child")
    sequence_dialog = _make_sequence_dialog_stub(Sequence(name="seq", actions=[sequence_parent, sequence_child]), {"sequence_parent"})
    sequence_dialog._scrollable = _make_virtual_scroll_stub(
        [
            {"action": sequence_parent, "depth": 0, "index_str": "1"},
            {"action": sequence_child, "depth": 0, "index_str": "2"},
        ],
        {},
    )
    sequence_dialog._batch_render_id = None
    assert sequence_dialog._find_parent_action(sequence_child) is None
    sequence_dialog._move_action_to_target(sequence_child, sequence_parent)
    assert sequence_dialog._find_parent_action(sequence_child) is sequence_parent


def test_plan_delete_duplicate_rule_id_removes_exact_child(monkeypatch):
    import tkinter.messagebox as messagebox

    parent_a = AutomationRule(action_type="click", rule_id="parent_a")
    parent_b = AutomationRule(action_type="click", rule_id="parent_b")
    child_a = AutomationRule(action_type="key_press", rule_id="dup_child", parent_id="parent_a")
    child_b = AutomationRule(action_type="type", rule_id="dup_child", parent_id="parent_b")
    parent_a.children.append(child_a)
    parent_b.children.append(child_b)
    dialog = _make_plan_dialog_stub([parent_a, parent_b], set())
    dialog._selected_rule = None
    dialog._modified = False
    refreshed = []
    dialog._invalidate_rule_tree_cache = lambda: None
    dialog._refresh_after_rule_deleted = lambda deleted, parent: refreshed.append((deleted, parent))
    monkeypatch.setattr(messagebox, "askyesno", lambda *_args, **_kwargs: True)

    dialog._delete_rule(child_b)

    assert parent_a.children == [child_a]
    assert parent_b.children == []
    assert refreshed == [(child_b, parent_b)]
    assert dialog._modified is True


def test_sequence_delete_duplicate_action_id_removes_exact_child(monkeypatch):
    import tkinter.messagebox as messagebox

    parent_a = Action(action_type="click", action_id="parent_a")
    parent_b = Action(action_type="click", action_id="parent_b")
    child_a = Action(action_type="key_press", action_id="dup_child", parent_id="parent_a")
    child_b = Action(action_type="type", action_id="dup_child", parent_id="parent_b")
    parent_a.children.append(child_a)
    parent_b.children.append(child_b)
    dialog = _make_sequence_dialog_stub(Sequence(name="seq", actions=[parent_a, parent_b]), set())
    dialog._selected_action = None
    dialog._modified = False
    refreshed = []
    dialog._invalidate_action_tree_cache = lambda: None
    dialog._refresh_after_action_deleted = lambda deleted, parent: refreshed.append((deleted, parent))
    monkeypatch.setattr(messagebox, "askyesno", lambda *_args, **_kwargs: True)

    dialog._delete_action(child_b)

    assert parent_a.children == [child_a]
    assert parent_b.children == []
    assert refreshed == [(child_b, parent_b)]
    assert dialog._modified is True


def test_dialog_refresh_keeps_widget_maps_for_reused_virtual_rows():
    text = _read_text()
    plan_method = _method_slice(
        text,
        "def _refresh_action_list(self, preserve_scroll: bool = True):",
        "def _schedule_action_list_refresh(self, delay_ms: int = 16, preserve_scroll: bool = True):",
    )
    sequence_start = text.index("class SequenceDetailDialog")
    sequence_text = text[sequence_start:]
    sequence_method = _method_slice(
        sequence_text,
        "def _refresh_action_list(self, preserve_scroll: bool = True):",
        "def _schedule_action_list_refresh(self, delay_ms: int = 16, preserve_scroll: bool = True):",
    )

    assert "self._rule_widgets = {}" not in plan_method
    assert "self._action_widgets = {}" not in sequence_method
    assert ".set_items(" in plan_method
    assert ".set_items(" in sequence_method


def test_sequence_detail_collapse_uses_virtual_splice_instead_of_full_refresh():
    text = _read_text()
    sequence_start = text.index("class SequenceDetailDialog")
    sequence_text = text[sequence_start:]
    method = _method_slice(
        sequence_text,
        "def _toggle_item_collapse(self, action_id: str):",
        "def _on_drag_start(self, event, action: Action, widget):",
    )

    assert "self._scrollable.splice_items(index + 1, 0, new_items)" in method
    assert "self._scrollable.splice_items(index + 1, remove_count, [])" in method
    assert 'self._action_widgets[action.action_id]["toggle_btn"] = toggle_btn' in sequence_text
    assert "toggle_btn.configure(text=_collapse_toggle_text(action_id in self._collapsed_items))" in method
    assert "self._refresh_action_row(action)" not in method
    assert "self._schedule_action_list_refresh()" not in method
    assert "self._refresh_action_list()" not in method


def test_plan_visible_sibling_swap_reorders_blocks_without_reflatten():
    first = AutomationRule(action_type="click", rule_id="first")
    child = AutomationRule(action_type="click", rule_id="child", parent_id="first")
    second = AutomationRule(action_type="click", rule_id="second")
    first.children.append(child)
    items = [
        {"rule": first, "depth": 0, "index_str": "1", "parent_id": None},
        {"rule": child, "depth": 1, "index_str": "1.1", "parent_id": "first"},
        {"rule": second, "depth": 0, "index_str": "2", "parent_id": None},
    ]
    dialog = _make_plan_dialog_stub([second, first], set())
    dialog._scrollable = _make_virtual_scroll_stub(items, {})

    assert dialog._apply_visible_rule_sibling_swap(first, second) is True

    updated = dialog._scrollable.get_items()
    assert [item["rule"].rule_id for item in updated] == ["second", "first", "child"]
    assert [item["index_str"] for item in updated] == ["1", "2", "2.1"]


def test_sequence_visible_sibling_swap_reorders_blocks_without_reflatten():
    first = Action(action_type="click", action_id="first")
    child = Action(action_type="click", action_id="child", parent_id="first")
    second = Action(action_type="click", action_id="second")
    first.children.append(child)
    items = [
        {"action": first, "depth": 0, "index_str": "1"},
        {"action": child, "depth": 1, "index_str": "1-1"},
        {"action": second, "depth": 0, "index_str": "2"},
    ]
    dialog = _make_sequence_dialog_stub(Sequence(name="seq", actions=[second, first]), set())
    dialog._scrollable = _make_virtual_scroll_stub(items, {})

    assert dialog._apply_visible_action_sibling_swap(first, second) is True

    updated = dialog._scrollable.get_items()
    assert [item["action"].action_id for item in updated] == ["second", "first", "child"]
    assert [item["index_str"] for item in updated] == ["1", "2", "2-1"]


def test_visible_sibling_swap_uses_range_replace_instead_of_full_set_items():
    text = _read_text()
    plan_method = _method_slice(
        text,
        "def _apply_visible_rule_sibling_swap(self, rule_a: AutomationRule, rule_b: AutomationRule) -> bool:",
        "def _build_visible_rule_child_items(self, parent_rule: AutomationRule, parent_depth: int, parent_index_str: str) -> list:",
    )
    sequence_start = text.index("class SequenceDetailDialog")
    sequence_text = text[sequence_start:]
    sequence_method = _method_slice(
        sequence_text,
        "def _apply_visible_action_sibling_swap(self, action_a: Action, action_b: Action) -> bool:",
        "def _build_visible_action_child_items(self, parent_action: Action, parent_depth: int, parent_index_str: str) -> list:",
    )

    assert "self._scrollable.replace_items_range(idx_a, end_b - idx_a, block_b + block_a)" in plan_method
    assert "self._scrollable.set_items(" not in plan_method
    assert "self._scrollable.replace_items_range(idx_a, end_b - idx_a, block_b + block_a)" in sequence_method
    assert "self._scrollable.set_items(" not in sequence_method


def test_hidden_rule_sibling_swap_under_collapsed_parent_skips_full_resync():
    parent = AutomationRule(action_type="click", rule_id="parent")
    first = AutomationRule(action_type="click", rule_id="first", parent_id="parent")
    second = AutomationRule(action_type="click", rule_id="second", parent_id="parent")
    parent.children = [second, first]
    dialog = _make_plan_dialog_stub([parent], {parent.rule_id})
    dialog._rule_widgets = {}
    dialog._scrollable = _make_virtual_scroll_stub(
        [{"rule": parent, "depth": 0, "index_str": "1", "parent_id": None}],
        {},
    )

    assert dialog._apply_visible_rule_sibling_swap(first, second) is True
    assert [item["rule"].rule_id for item in dialog._scrollable.get_items()] == ["parent"]


def test_hidden_action_sibling_swap_under_collapsed_parent_skips_full_resync():
    parent = Action(action_type="click", action_id="parent")
    first = Action(action_type="click", action_id="first", parent_id="parent")
    second = Action(action_type="click", action_id="second", parent_id="parent")
    parent.children = [second, first]
    dialog = _make_sequence_dialog_stub(Sequence(name="seq", actions=[parent]), {parent.action_id})
    dialog._action_widgets = {}
    dialog._compact_action_rows = False
    dialog._scrollable = _make_virtual_scroll_stub(
        [{"action": parent, "depth": 0, "index_str": "1"}],
        {},
    )

    assert dialog._apply_visible_action_sibling_swap(first, second) is True
    assert [item["action"].action_id for item in dialog._scrollable.get_items()] == ["parent"]


def test_rule_metadata_refresh_reindexes_large_lists_in_chunks():
    rules = [AutomationRule(action_type="click", rule_id=f"rule_{idx}") for idx in range(2001)]
    dialog = _make_plan_dialog_stub(rules, set())
    dialog._scrollable = _make_virtual_scroll_stub(
        [{"rule": rule, "depth": 0, "index_str": "stale", "parent_id": None} for rule in rules],
        {},
    )
    dialog._rule_metadata_refresh_job = None
    dialog._rule_metadata_refresh_generation = 0
    queue = _FakeAfterQueue()
    dialog.after = queue.after

    dialog._schedule_full_rule_metadata_refresh(delay_ms=0)
    queue.drain()

    items = dialog._scrollable.get_items()
    assert queue.next_id > 2
    assert dialog._rule_metadata_refresh_job is None
    assert items[0]["index_str"] == "1"
    assert items[799]["index_str"] == "800"
    assert items[800]["index_str"] == "801"
    assert items[-1]["index_str"] == "2001"


def test_action_metadata_refresh_reindexes_large_lists_in_chunks():
    actions = [Action(action_type="click", action_id=f"action_{idx}") for idx in range(2001)]
    dialog = _make_sequence_dialog_stub(Sequence(name="seq", actions=actions), set())
    dialog._scrollable = _make_virtual_scroll_stub(
        [{"action": action, "depth": 0, "index_str": "stale"} for action in actions],
        {},
    )
    dialog._action_metadata_refresh_job = None
    dialog._action_metadata_refresh_generation = 0
    queue = _FakeAfterQueue()
    dialog.after = queue.after

    dialog._schedule_full_action_metadata_refresh(delay_ms=0)
    queue.drain()

    items = dialog._scrollable.get_items()
    assert queue.next_id > 2
    assert dialog._action_metadata_refresh_job is None
    assert items[0]["index_str"] == "1"
    assert items[799]["index_str"] == "800"
    assert items[800]["index_str"] == "801"
    assert items[-1]["index_str"] == "2001"


def test_full_metadata_refresh_uses_chunked_visible_updates_not_full_metadata_replace():
    text = _read_text()
    plan_method = _method_slice(
        text,
        "def _schedule_full_rule_metadata_refresh(self, delay_ms: int = 120) -> None:",
        "def _refresh_rule_numbering_after_patch(self) -> None:",
    )
    sequence_start = text.index("class SequenceDetailDialog")
    sequence_text = text[sequence_start:]
    sequence_method = _method_slice(
        sequence_text,
        "def _schedule_full_action_metadata_refresh(self, delay_ms: int = 120) -> None:",
        "def _refresh_action_numbering_after_patch(self) -> None:",
    )

    assert "chunk_size = 800" in plan_method
    assert "self.after(1, lambda: _process_chunk(end))" in plan_method
    assert "scrollable.update_visible_items_metadata" in plan_method
    assert "scrollable.update_items_metadata" not in plan_method
    assert "chunk_size = 800" in sequence_method
    assert "self.after(1, lambda: _process_chunk(end))" in sequence_method
    assert "scrollable.update_visible_items_metadata" in sequence_method
    assert "scrollable.update_items_metadata" not in sequence_method


def test_plan_add_child_splices_only_new_visible_rule():
    parent = AutomationRule(action_type="click", rule_id="parent")
    child = AutomationRule(action_type="click", rule_id="child", parent_id="parent")
    parent.children.append(child)
    dialog = _make_plan_dialog_stub([parent], set())
    dialog._scrollable = _make_virtual_scroll_stub(
        [{"rule": parent, "depth": 0, "index_str": "1", "parent_id": None}],
        {},
    )
    scheduled = []
    refreshed = []
    dialog._schedule_action_list_refresh = lambda *args, **kwargs: scheduled.append((args, kwargs))
    dialog._refresh_rule_row = lambda rule_id: refreshed.append(rule_id) or True

    dialog._refresh_after_rule_added(parent, child)

    updated = dialog._scrollable.get_items()
    assert scheduled == []
    assert refreshed == ["parent"]
    assert [item["rule"].rule_id for item in updated] == ["parent", "child"]
    assert [item["index_str"] for item in updated] == ["1", "1.1"]


def test_sequence_add_child_splices_only_new_visible_action():
    parent = Action(action_type="click", action_id="parent")
    child = Action(action_type="click", action_id="child", parent_id="parent")
    parent.children.append(child)
    dialog = _make_sequence_dialog_stub(Sequence(name="seq", actions=[parent]), set())
    dialog._scrollable = _make_virtual_scroll_stub(
        [{"action": parent, "depth": 0, "index_str": "1"}],
        {},
    )
    scheduled = []
    refreshed = []
    dialog._schedule_action_list_refresh = lambda *args, **kwargs: scheduled.append((args, kwargs))
    dialog._refresh_action_row = lambda action: refreshed.append(action.action_id) or True

    dialog._refresh_after_action_added(parent, child)

    updated = dialog._scrollable.get_items()
    assert scheduled == []
    assert refreshed == ["parent"]
    assert [item["action"].action_id for item in updated] == ["parent", "child"]
    assert [item["index_str"] for item in updated] == ["1", "1-1"]


def test_plan_first_child_requires_parent_row_rebuild_for_collapse_button():
    parent = AutomationRule(action_type="click", rule_id="parent")
    parent.children.append(AutomationRule(action_type="key_press", rule_id="child", parent_id="parent"))
    dialog = _make_plan_dialog_stub([parent], set())
    dialog._compact_rule_rows = True
    dialog._rule_widgets = {
        parent.rule_id: {
            "widget": _FakeWidget(),
            "rule": parent,
        }
    }
    updated = []
    dialog._update_rule_row_in_place = lambda rule: updated.append(rule.rule_id) or True

    assert dialog._update_rule_parent_summary(parent) is False
    assert updated == []


def test_sequence_first_child_requires_parent_row_rebuild_for_collapse_button():
    parent = Action(action_type="click", action_id="parent")
    parent.children.append(Action(action_type="key_press", action_id="child", parent_id="parent"))
    dialog = _make_sequence_dialog_stub(Sequence(name="seq", actions=[parent]), set())
    dialog._compact_action_rows = True
    dialog._action_widgets = {
        parent.action_id: {
            "widget": _FakeWidget(),
            "action": parent,
        }
    }
    updated = []
    dialog._update_compact_action_row = lambda action: updated.append(action.action_id) or True

    assert dialog._update_action_parent_summary(parent) is False
    assert updated == []


def test_plan_add_child_updates_existing_parent_summary_without_row_rebuild():
    parent = AutomationRule(action_type="click", rule_id="parent")
    existing = AutomationRule(action_type="click", rule_id="existing", parent_id="parent")
    child = AutomationRule(action_type="click", rule_id="child", parent_id="parent")
    parent.children.extend([existing, child])
    dialog = _make_plan_dialog_stub([parent], set())
    dialog._selected_rule = None
    dialog._active_partial_rule_id = None
    dialog._running_rule_id = None
    dialog._scrollable = _make_virtual_scroll_stub(
        [
            {"rule": parent, "depth": 0, "index_str": "1", "parent_id": None},
            {"rule": existing, "depth": 1, "index_str": "1.1", "parent_id": "parent"},
        ],
        {},
    )
    child_count = _FakeWidget()
    toggle = _FakeWidget()
    dialog._rule_widgets = {
        parent.rule_id: {
            "widget": _FakeWidget(),
            "type_label": _FakeWidget(),
            "name_label": _FakeWidget(),
            "detail_label": _FakeWidget(),
            "number_label": _FakeWidget(),
            "child_count_label": child_count,
            "toggle_btn": toggle,
        }
    }
    refreshed = []
    dialog._refresh_rule_row = lambda rule_id: refreshed.append(rule_id) or True

    dialog._refresh_after_rule_added(parent, child)

    assert refreshed == []
    assert child_count.config["text"] == "  (2개 하위)"
    assert toggle.config["text"] == "▲"


def test_sequence_add_child_updates_existing_parent_summary_without_row_rebuild():
    parent = Action(action_type="click", action_id="parent")
    existing = Action(action_type="click", action_id="existing", parent_id="parent")
    child = Action(action_type="click", action_id="child", parent_id="parent")
    parent.children.extend([existing, child])
    dialog = _make_sequence_dialog_stub(Sequence(name="seq", actions=[parent]), set())
    dialog._compact_action_rows = False
    dialog._selected_action = None
    dialog._scrollable = _make_virtual_scroll_stub(
        [
            {"action": parent, "depth": 0, "index_str": "1"},
            {"action": existing, "depth": 1, "index_str": "1-1"},
        ],
        {},
    )
    child_count = _FakeWidget()
    toggle = _FakeWidget()
    dialog._action_widgets = {
        parent.action_id: {
            "widget": _FakeWidget(),
            "type_label": _FakeWidget(),
            "name_label": _FakeWidget(),
            "detail_label": _FakeWidget(),
            "number_label": _FakeWidget(),
            "child_count_label": child_count,
            "toggle_btn": toggle,
        }
    }
    refreshed = []
    dialog._refresh_action_row = lambda action: refreshed.append(action.action_id) or True

    dialog._refresh_after_action_added(parent, child)

    assert refreshed == []
    assert child_count.config["text"] == "  (2개 하위)"
    assert toggle.config["text"] == "▲"


def test_hidden_parent_child_changes_do_not_schedule_full_refresh():
    visible = AutomationRule(action_type="click", rule_id="visible")
    hidden_parent = AutomationRule(action_type="click", rule_id="hidden_parent")
    hidden_child = AutomationRule(action_type="key_press", rule_id="hidden_child", parent_id="hidden_parent")
    hidden_parent.children.append(hidden_child)
    plan_dialog = _make_plan_dialog_stub([visible, hidden_parent], {hidden_parent.rule_id})
    plan_dialog._scrollable = _make_virtual_scroll_stub(
        [{"rule": visible, "depth": 0, "index_str": "1", "parent_id": None}],
        {},
    )
    plan_scheduled = []
    plan_refreshed = []
    plan_dialog._schedule_action_list_refresh = lambda *args, **kwargs: plan_scheduled.append((args, kwargs))
    plan_dialog._refresh_rule_row = lambda rule_id: plan_refreshed.append(rule_id) or False

    added_child = AutomationRule(action_type="type", rule_id="added_child", parent_id="hidden_parent")
    hidden_parent.children.append(added_child)
    plan_dialog._refresh_after_rule_added(hidden_parent, added_child)
    plan_dialog._refresh_after_rule_deleted(hidden_child, hidden_parent)

    assert plan_scheduled == []
    assert plan_refreshed == [hidden_parent.rule_id]

    visible_action = Action(action_type="click", action_id="visible")
    hidden_action_parent = Action(action_type="click", action_id="hidden_parent")
    hidden_action_child = Action(action_type="key_press", action_id="hidden_child", parent_id="hidden_parent")
    hidden_action_parent.children.append(hidden_action_child)
    sequence_dialog = _make_sequence_dialog_stub(
        Sequence(name="hidden", actions=[visible_action, hidden_action_parent]),
        {hidden_action_parent.action_id},
    )
    sequence_dialog._scrollable = _make_virtual_scroll_stub(
        [{"action": visible_action, "depth": 0, "index_str": "1"}],
        {},
    )
    sequence_scheduled = []
    sequence_refreshed = []
    sequence_dialog._schedule_action_list_refresh = lambda *args, **kwargs: sequence_scheduled.append((args, kwargs))
    sequence_dialog._refresh_action_row = lambda action: sequence_refreshed.append(action.action_id) or False

    added_action = Action(action_type="type", action_id="added_action", parent_id="hidden_parent")
    hidden_action_parent.children.append(added_action)
    sequence_dialog._refresh_after_action_added(hidden_action_parent, added_action)
    sequence_dialog._refresh_after_action_deleted(hidden_action_child, hidden_action_parent)

    assert sequence_scheduled == []
    assert sequence_refreshed == [hidden_action_parent.action_id]


def test_plan_delete_visible_block_reindexes_remaining_rules_without_full_refresh():
    first = AutomationRule(action_type="click", rule_id="first")
    child = AutomationRule(action_type="click", rule_id="child", parent_id="first")
    second = AutomationRule(action_type="click", rule_id="second")
    first.children.append(child)
    dialog = _make_plan_dialog_stub([second], set())
    dialog._scrollable = _make_virtual_scroll_stub(
        [
            {"rule": first, "depth": 0, "index_str": "1", "parent_id": None},
            {"rule": child, "depth": 1, "index_str": "1.1", "parent_id": "first"},
            {"rule": second, "depth": 0, "index_str": "2", "parent_id": None},
        ],
        {},
    )
    scheduled = []
    dialog._schedule_action_list_refresh = lambda *args, **kwargs: scheduled.append((args, kwargs))

    dialog._refresh_after_rule_deleted(first, None)

    updated = dialog._scrollable.get_items()
    assert scheduled == []
    assert [item["rule"].rule_id for item in updated] == ["second"]
    assert [item["index_str"] for item in updated] == ["1"]


def test_sequence_delete_visible_block_reindexes_remaining_actions_without_full_refresh():
    first = Action(action_type="click", action_id="first")
    child = Action(action_type="click", action_id="child", parent_id="first")
    second = Action(action_type="click", action_id="second")
    first.children.append(child)
    dialog = _make_sequence_dialog_stub(Sequence(name="seq", actions=[second]), set())
    dialog._scrollable = _make_virtual_scroll_stub(
        [
            {"action": first, "depth": 0, "index_str": "1"},
            {"action": child, "depth": 1, "index_str": "1-1"},
            {"action": second, "depth": 0, "index_str": "2"},
        ],
        {},
    )
    scheduled = []
    dialog._schedule_action_list_refresh = lambda *args, **kwargs: scheduled.append((args, kwargs))

    dialog._refresh_after_action_deleted(first, None)

    updated = dialog._scrollable.get_items()
    assert scheduled == []
    assert [item["action"].action_id for item in updated] == ["second"]
    assert [item["index_str"] for item in updated] == ["1"]


def test_delete_visible_block_uses_virtual_index_cache_instead_of_linear_scan():
    text = _read_text()
    plan_method = _method_slice(
        text,
        "def _refresh_after_rule_deleted(self, deleted_rule: AutomationRule, parent_rule: Optional[AutomationRule]) -> None:",
        "def _build_visible_rule_subtree_items(",
    )
    sequence_start = text.index("class SequenceDetailDialog")
    sequence_text = text[sequence_start:]
    sequence_method = _method_slice(
        sequence_text,
        "def _refresh_after_action_deleted(self, deleted_action: Action, parent_action: Optional[Action]) -> None:",
        "def _build_visible_action_subtree_items(",
    )

    assert "deleted_index = self._find_visible_rule_item_index_by_object(deleted_rule)" in plan_method
    assert "self._scrollable.splice_items(deleted_index, remove_count, [])" in plan_method
    assert "self._refresh_rule_numbering_after_patch()" in plan_method
    assert "self._scrollable.set_items(" not in plan_method
    assert "for index, item in enumerate(items):" not in plan_method
    assert "deleted_index = self._find_visible_action_item_index_by_object(deleted_action)" in sequence_method
    assert "self._scrollable.splice_items(deleted_index, remove_count, [])" in sequence_method
    assert "self._refresh_action_numbering_after_patch()" in sequence_method
    assert "self._scrollable.set_items(" not in sequence_method
    assert "for index, item in enumerate(items):" not in sequence_method


def test_partial_rule_index_uses_virtual_scroll_cache_before_flattening():
    current = AutomationRule(action_type="click", rule_id="current")
    other = AutomationRule(action_type="click", rule_id="other")
    dialog = _make_plan_dialog_stub([current, other], set())
    dialog._scrollable = _make_virtual_scroll_stub(
        [
            {"rule": current, "depth": 0, "index_str": "1", "parent_id": None},
            {"rule": other, "depth": 0, "index_str": "2", "parent_id": None},
        ],
        {},
    )
    dialog._get_flat_rules_with_depth = lambda: (_ for _ in ()).throw(AssertionError("flat scan should not run"))

    index, item = dialog._visible_rule_index("other")

    assert index == 1
    assert item["rule"] is other


def test_sequence_image_editor_updates_row_and_thumbnail_before_row_rebuild():
    text = _read_text()
    sequence_start = text.index("class SequenceDetailDialog")
    sequence_text = text[sequence_start:]
    method = _method_slice(
        sequence_text,
        "def _open_image_editor(self, image_path: str, action: Action):",
        "def _save_sequence(self):",
    )

    assert "self._update_compact_action_row(changed_action)" in method
    assert "self._refresh_action_thumbnail(changed_action)" in method
    assert "if not updated and not thumb_updated:" in method
    assert "self._refresh_action_row(changed_action)" in method
    assert "self._schedule_action_list_refresh()" not in method


def test_plan_detail_dialog_toggle_enabled_refreshes_single_row_without_popup_save():
    text = _read_text()
    method = _method_slice(
        text,
        "def _toggle_rule_enabled(self, rule: AutomationRule):",
        "def _toggle_skip_mode(self, rule: AutomationRule):",
    )

    assert "self._save_plan(show_message=False)" in method
    assert "self._refresh_rule_row(rule.rule_id)" in method
    assert "self._refresh_action_list()" not in method


def test_plan_nonstructural_update_changes_labels_without_row_recreate():
    rule = AutomationRule(
        action_type="click",
        rule_id="rule",
        description="old",
        action_x=10,
        action_y=20,
    )
    dialog = _make_plan_dialog_stub([rule], set())
    dialog._selected_rule = None
    dialog._active_partial_rule_id = None
    dialog._running_rule_id = None
    row = _FakeWidget()
    type_label = _FakeWidget()
    name_label = _FakeWidget()
    detail_label = _FakeWidget()
    number_label = _FakeWidget()
    run_btn = _FakeWidget()
    repeat_btn = _FakeWidget()
    delay_btn = _FakeWidget()
    skip_btn = _FakeWidget()
    dialog._rule_widgets = {
        rule.rule_id: {
            "widget": row,
            "type_label": type_label,
            "name_label": name_label,
            "detail_label": detail_label,
            "number_label": number_label,
            "run_btn": run_btn,
            "repeat_btn": repeat_btn,
            "delay_btn": delay_btn,
            "skip_btn": skip_btn,
        }
    }
    rule.action_type = "double_click"
    rule.description = "changed"
    rule.target_image = "sample.png"
    rule.alternate_mouse_route = True

    assert dialog._update_rule_row_in_place(rule) is True

    assert type_label.config["text"] == "더블 클릭"
    assert name_label.config["text"] == " - changed"
    assert "이동경로 변경" in detail_label.config["text"]
    assert repeat_btn.config["text"] == "↻ 1회"
    assert delay_btn.config["text"].startswith("⏱ ")
    assert "초" in delay_btn.config["text"]
    assert delay_btn.config["width"] >= 82
    assert run_btn.config["state"] == "normal"
    assert row.destroyed is False


def test_plan_trigger_button_updates_in_place_after_trigger_save():
    rule = AutomationRule(action_type="click", rule_id="rule")
    dialog = _make_plan_dialog_stub([rule], set())
    trigger_btn = _FakeWidget()
    dialog._rule_widgets = {
        rule.rule_id: {
            "rule": rule,
            "trigger_btn": trigger_btn,
        }
    }

    rule.trigger_image = "trigger.png"
    dialog._update_rule_buttons(rule)

    assert trigger_btn.config["fg_color"] == COLORS["success"]
    assert trigger_btn.config["hover_color"] == COLORS["green_hover"]
    assert trigger_btn.config["text_color"] == COLORS["text_on_accent"]


def test_plan_trigger_buttons_are_registered_for_compact_and_full_rows():
    text = _read_text()
    compact_method = _method_slice(
        text,
        "def _create_compact_rule_item(self, parent, rule: AutomationRule, depth: int = 0, index_str: str = \"1\"):",
        "def _create_action_item(self, parent, rule: AutomationRule, depth: int = 0, index_str: str = \"1\", use_pack: bool = True):",
    )
    full_method = _method_slice(
        text,
        "def _create_action_item(self, parent, rule: AutomationRule, depth: int = 0, index_str: str = \"1\", use_pack: bool = True):",
        "def _on_drag_start(self, event, rule: AutomationRule, widget):",
    )
    update_method = _method_slice(
        text,
        "def _update_rule_buttons(self, rule: AutomationRule):",
        "def _update_rule_row_in_place(self, rule: AutomationRule) -> bool:",
    )

    assert 'self._rule_widgets[rule.rule_id]["trigger_btn"] = trigger_btn' in compact_method
    assert 'self._rule_widgets[rule.rule_id]["trigger_btn"] = trigger_btn' in full_method
    assert 'if "trigger_btn" in widgets:' in update_method
    assert 'has_trigger = bool(getattr(rule, "trigger_image", None))' in update_method


def test_plan_compact_row_label_truncates_long_action_names():
    rule = AutomationRule(
        action_type="click",
        rule_id="rule",
        description="long-action-name-" * 30,
        action_x=10,
        action_y=20,
    )
    dialog = _make_plan_dialog_stub([rule], set())

    label = dialog._compact_rule_label_text(rule)

    assert len(label) <= 96
    assert "..." in label


def test_plan_partial_run_marks_current_row_and_truncates_status_text():
    rule = AutomationRule(
        action_type="click",
        rule_id="rule",
        description="current-partial-action-" * 20,
    )
    dialog = _make_plan_dialog_stub([rule], set())
    dialog._is_running = True
    dialog._selected_rule = None
    dialog._partial_status_label = _FakeWidget()
    row = _FakeWidget()
    badge = _FakeWidget()
    dialog._rule_widgets = {
        rule.rule_id: {
            "widget": row,
            "rule": rule,
            "running_badge": badge,
        }
    }
    dialog.winfo_exists = lambda: True
    dialog._expand_ancestors_for_rule = lambda _rule_id: False
    dialog._visible_rule_index = lambda _rule_id: (0, {"rule": rule, "index_str": "1"})
    dialog._player_view = None
    dialog._scrollable = None

    dialog._set_current_partial_rule(rule.rule_id, "partial run started")

    assert dialog._partial_status_label.config["text"].startswith("현재 실행: 1 - ")
    assert len(dialog._partial_status_label.config["text"]) <= 92
    assert row.config["border_width"] == 0
    assert row.config["fg_color"] == COLORS["accent_pink"]
    assert badge.config["text"] == ""
    assert badge.config["width"] == 0


def test_plan_dialog_modal_edits_refresh_only_changed_rule_row():
    text = _read_text()
    trigger_method = _method_slice(
        text,
        "def _edit_trigger_image(self, rule: AutomationRule):",
        "def _edit_monitoring_mode(self, rule: AutomationRule):",
    )
    monitoring_method = _method_slice(
        text,
        "def _edit_monitoring_mode(self, rule: AutomationRule):",
        "def _detach_rule(self, rule: AutomationRule):",
    )
    monitor_delete_method = _method_slice(
        text,
        "def _delete_monitor_action(self, rule: AutomationRule, watch_idx: int, action_idx: int):",
        "def _delete_rule(self, rule: AutomationRule):",
    )

    assert 'result = {"saved": False}' in trigger_method
    assert 'if result["saved"]:' in trigger_method
    assert "if not self._save_plan(show_message=False):" in trigger_method
    assert 'messagebox.showerror("저장 실패", "트리거 설정 저장에 실패했습니다. 로그를 확인하세요.")' in trigger_method
    assert "self._update_rule_buttons(rule)" in trigger_method
    assert "if not self._update_rule_row_in_place(rule):" in trigger_method
    assert "self._refresh_rule_row(rule.rule_id)" in trigger_method
    assert "def apply_editor_save() -> bool:" in monitoring_method
    assert "on_save=apply_editor_save" in monitoring_method
    assert "if not self._save_plan(show_message=False):" in monitoring_method
    assert "self._update_rule_buttons(rule)" in monitoring_method
    assert 'if editor.was_saved and not save_applied["value"]:' in monitoring_method
    assert "if not self._update_rule_row_in_place(rule):" in monitoring_method
    assert "self._refresh_rule_row(rule.rule_id)" in monitoring_method
    assert "if not self._update_rule_row_in_place(rule):" in monitor_delete_method
    assert "self._refresh_rule_row(rule.rule_id)" in monitor_delete_method
    assert "self._refresh_action_list()" not in trigger_method
    assert "self._refresh_action_list()" not in monitoring_method
    assert "self._schedule_action_list_refresh()" not in trigger_method
    assert "self._schedule_action_list_refresh()" not in monitoring_method
    assert "self._schedule_action_list_refresh()" not in monitor_delete_method


def test_plan_detail_dialog_common_edits_are_coalesced_refreshes():
    text = _read_text()
    sync_method_pairs = [
        ("def _move_rule_up(self, rule: AutomationRule):", "def _move_rule_down(self, rule: AutomationRule):"),
        ("def _move_rule_down(self, rule: AutomationRule):", "def _edit_wait_time(self, rule: AutomationRule):"),
    ]
    in_place_method_pairs = [
        ("def _randomize_all_delays(self):", "def _toggle_all_children(self):"),
    ]
    bulk_sync_method_pairs = [
        ("def _toggle_all_children(self):", "def _toggle_all_collapse(self):"),
    ]

    for start, end in sync_method_pairs:
        method = _method_slice(text, start, end)
        assert "self._sync_visible_rules_from_model()" in method
        assert "self._schedule_action_list_refresh()" not in method

    for start, end in in_place_method_pairs:
        method = _method_slice(text, start, end)
        assert "self._update_visible_rule_rows_in_place()" in method
        assert "self._sync_visible_rules_from_model()" not in method
        assert "self._refresh_action_list()" not in method
        assert "self._schedule_action_list_refresh()" not in method

    wait_method = _method_slice(
        text,
        "def _edit_wait_time(self, rule: AutomationRule):",
        "def _edit_repeat_count(self, rule: AutomationRule):",
    )
    assert "if not self._update_rule_row_in_place(rule):" in wait_method
    assert "self._schedule_action_list_refresh()" not in wait_method

    for start, end in bulk_sync_method_pairs:
        method = _method_slice(text, start, end)
        assert "self._sync_visible_rules_from_model()" in method
        assert "self._refresh_action_list()" not in method
        assert "self._schedule_action_list_refresh()" not in method

    synced_method_pairs = [
        ("def _move_to_child(self, rule: AutomationRule):", "def _move_to_parent(self, rule: AutomationRule):"),
        ("def _move_to_parent(self, rule: AutomationRule):", "def _find_parent_rule(self, target: AutomationRule)"),
    ]
    for start, end in synced_method_pairs:
        method = _method_slice(text, start, end)
        assert "self._sync_visible_rules_from_model()" in method
        assert (
            "self._apply_visible_rule_attach(rule, prev_rule, None)" in method
            or "self._apply_visible_rule_detach(parent_rule, rule)" in method
        )
        assert "self._refresh_action_list()" not in method


def test_plan_detail_dialog_save_plan_supports_quiet_ui_save():
    text = _read_text()
    method = _method_slice(
        text,
        "def _save_plan(self, show_message: bool = True) -> bool:",
        "def _delete_monitor_action(self, rule: AutomationRule, watch_idx: int, action_idx: int):",
    )

    assert "if show_message:" in method
    assert "messagebox.showinfo" in method


def test_sequence_detail_dialog_common_edits_are_coalesced_refreshes():
    text = _read_text()
    sync_method_pairs = [
        ("def _move_action_up(self, action: Action):", "def _move_action_down(self, action: Action):"),
        ("def _move_action_down(self, action: Action):", "def _edit_wait_time_action(self, action: Action):"),
    ]
    in_place_method_pairs = [
        ("def _randomize_all_delays(self):", "def _toggle_all_children(self):"),
    ]
    bulk_sync_method_pairs = [
        ("def _toggle_all_children(self):", "def _toggle_all_collapse(self):"),
        ("def _toggle_all_collapse(self):", "def _collect_parent_action_ids(self, action: Action):"),
    ]

    sequence_start = text.index("class SequenceDetailDialog")
    sequence_text = text[sequence_start:]
    for start, end in sync_method_pairs:
        method = _method_slice(sequence_text, start, end)
        assert "self._sync_visible_actions_from_model()" in method
        assert "self._schedule_action_list_refresh()" not in method

    for start, end in in_place_method_pairs:
        method = _method_slice(sequence_text, start, end)
        assert "self._update_visible_action_rows_in_place()" in method
        assert "self._sync_visible_actions_from_model()" not in method
        assert "self._refresh_action_list()" not in method
        assert "self._schedule_action_list_refresh()" not in method

    wait_method = _method_slice(
        sequence_text,
        "def _edit_wait_time_action(self, action: Action):",
        "def _edit_repeat_count_action(self, action: Action):",
    )
    assert "if not self._update_compact_action_row(action):" in wait_method
    assert "self._schedule_action_list_refresh()" not in wait_method

    for start, end in bulk_sync_method_pairs:
        method = _method_slice(sequence_text, start, end)
        assert "self._sync_visible_actions_from_model()" in method
        assert "self._refresh_action_list()" not in method
        assert "self._schedule_action_list_refresh()" not in method

    drag_method = _method_slice(
        sequence_text,
        "def _move_action_to_target(self, dragged: Action, target: Action):",
        "def _is_ancestor_action(self, potential_ancestor: Action, target: Action)",
    )
    assert "self._sync_visible_actions_from_model()" in drag_method
    assert "self._refresh_action_list()" not in drag_method


def test_delete_uses_local_remove_refresh_helpers():
    text = _read_text()
    plan_method = _method_slice(
        text,
        "def _delete_rule(self, rule: AutomationRule):",
        "def _move_rule_up(self, rule: AutomationRule):",
    )
    sequence_start = text.index("class SequenceDetailDialog")
    sequence_text = text[sequence_start:]
    sequence_method = _method_slice(
        sequence_text,
        "def _delete_action(self, action: Action):",
        "def _move_action_up(self, action: Action):",
    )

    assert "self._refresh_after_rule_deleted(rule, parent)" in plan_method
    assert "self._refresh_after_action_deleted(action, parent)" in sequence_method
    assert "self._schedule_action_list_refresh()" not in plan_method
    assert "self._schedule_action_list_refresh()" not in sequence_method


def test_sequence_detail_dialog_uses_virtual_scroll_items():
    text = _read_text()
    sequence_start = text.index("class SequenceDetailDialog")
    sequence_text = text[sequence_start:]
    setup_method = _method_slice(
        sequence_text,
        "def _setup_ui(self):",
        "def _get_flat_actions_with_depth(self) -> list:",
    )
    init_method = _method_slice(
        sequence_text,
        "def __init__(self, parent, sequence: Sequence, db):",
        "def _init_collapsed_items(self):",
    )
    refresh_method = _method_slice(
        sequence_text,
        "def _refresh_action_list(self, preserve_scroll: bool = True):",
        "def _schedule_action_list_refresh(self, delay_ms: int = 16, preserve_scroll: bool = True):",
    )
    render_method = _method_slice(
        sequence_text,
        "def _render_action_item(self, parent, item_data: dict, index: int):",
        "def _render_actions_batch(self, actions_list, start_idx, batch_size=5):",
    )
    legacy_batch_method = _method_slice(
        sequence_text,
        "def _render_actions_batch(self, actions_list, start_idx, batch_size=5):",
        "def _update_action_buttons(self, action: Action):",
    )

    assert "self._scrollable = VirtualScrollFrame(" in setup_method
    assert "COMPACT_ACTION_ROW_THRESHOLD = 80" in text
    assert "self._compact_action_rows = True" in init_method
    assert "item_height=76" in setup_method
    assert "buffer_count=2" in setup_method
    assert "self._scrollable.set_render_callback(self._render_action_item)" in setup_method
    assert "self._scrollable.set_destroy_callback(self._on_action_item_destroy)" in setup_method
    assert "self._scrollable.set_items(self._get_flat_actions_with_depth(), preserve_scroll=preserve_scroll)" in refresh_method
    assert "winfo_children()" not in refresh_method
    assert "self._create_compact_action_item" in render_method
    assert "use_pack=False" in render_method
    assert "self._create_action_item(" not in render_method
    assert 'if isinstance(getattr(self, "_scrollable", None), VirtualScrollFrame):' in legacy_batch_method


def test_sequence_detail_uses_compact_rows_for_every_playlist_size():
    text = _read_text()
    sequence_start = text.index("class SequenceDetailDialog")
    sequence_text = text[sequence_start:]
    init_method = _method_slice(
        sequence_text,
        "def __init__(self, parent, sequence: Sequence, db):",
        "def _init_collapsed_items(self):",
    )
    setup_method = _method_slice(
        sequence_text,
        "def _setup_ui(self):",
        "def _get_flat_actions_with_depth(self) -> list:",
    )
    render_method = _method_slice(
        sequence_text,
        "def _render_action_item(self, parent, item_data: dict, index: int):",
        "def _render_actions_batch(self, actions_list, start_idx, batch_size=5):",
    )

    assert "self._compact_action_rows = True" in init_method
    assert "item_height=76" in setup_method
    assert "buffer_count=2" in setup_method
    assert "return self._create_compact_action_item(parent, action, depth=depth, index_str=index_str, use_pack=False)" in render_method
    assert "return self._create_action_item(" not in render_method


def test_plan_detail_uses_compact_rows_for_every_plan_size():
    text = _read_text()
    init_method = _method_slice(
        text,
        "def __init__(self, parent, plan: AutomationPlan):",
        "def _notify_player_partial_run_started(self, mode: str = \"부분실행\") -> None:",
    )
    setup_method = _method_slice(
        text,
        "def _setup_ui(self):",
        "def _get_flat_rules(self) -> List[AutomationRule]:",
    )
    render_method = _method_slice(
        text,
        "def _render_rule_item(self, parent, item_data: dict, index: int):",
        "def _on_rule_item_destroy(self, item_data: dict, index: int, widget) -> None:",
    )

    assert "self._total_rule_count = self._count_rule_tree(self._plan.initial_rules)" in init_method
    assert "self._compact_rule_rows = True" in init_method
    assert "item_height=76" in setup_method
    assert "buffer_count=2" in setup_method
    assert "return self._create_compact_rule_item(parent, rule, depth, index_str)" in render_method
    assert "return self._create_action_item_virtual(" not in render_method
    assert "def _create_compact_rule_item(" in text
    assert "def _show_rule_context_menu(" in text


def test_plan_detail_count_and_flatten_ignore_existing_child_cycles():
    parent = AutomationRule(action_type="click", rule_id="parent")
    child = AutomationRule(action_type="key_press", rule_id="child", parent_id="parent")
    parent.children.append(child)
    child.children.append(parent)
    dialog = _make_plan_dialog_stub([parent], set())

    assert dialog._count_rule_tree(dialog._plan.initial_rules) == 2
    flat = dialog._get_flat_rules_with_depth()

    assert [item["rule"].rule_id for item in flat] == ["parent", "child"]
    assert [item["depth"] for item in flat] == [0, 1]


def test_plan_compact_row_update_changes_labels_without_rebuild():
    rule = AutomationRule(
        action_type="click",
        rule_id="rule",
        description="old",
        action_x=10,
        action_y=20,
    )
    dialog = _make_plan_dialog_stub([rule], set())
    dialog._compact_rule_rows = True
    dialog._selected_rule = None
    dialog._active_partial_rule_id = None
    dialog._running_rule_id = None
    row = _FakeWidget()
    name_label = _FakeWidget()
    number_label = _FakeWidget()
    run_btn = _FakeWidget()
    repeat_btn = _FakeWidget()
    delay_btn = _FakeWidget()
    skip_btn = _FakeWidget()
    dialog._rule_widgets = {
        rule.rule_id: {
            "widget": row,
            "rule": rule,
            "name_label": name_label,
            "number_label": number_label,
            "run_btn": run_btn,
            "repeat_btn": repeat_btn,
            "delay_btn": delay_btn,
            "skip_btn": skip_btn,
        }
    }

    rule.description = "changed"
    rule.target_image = "sample.png"
    rule.alternate_mouse_route = True

    assert dialog._update_rule_row_in_place(rule) is True

    assert "changed" in name_label.config["text"]
    assert "이동경로 변경" in name_label.config["text"]
    assert repeat_btn.config["text"] == "↻ 1회"
    assert delay_btn.config["text"].startswith("⏱ ")
    assert "초" in delay_btn.config["text"]
    assert delay_btn.config["width"] >= 82
    assert row.destroyed is False


def test_sequence_detail_compact_row_mode_is_reserved_for_large_action_trees():
    text = _read_text()
    sequence_start = text.index("class SequenceDetailDialog")
    sequence_text = text[sequence_start:]
    compact_method = _method_slice(
        sequence_text,
        "def _create_compact_action_item(self, parent, action: Action, depth: int = 0, index_str: str = \"1\", before_widget=None, use_pack: bool = True):",
        "def _create_action_item(self, parent, action: Action, depth: int = 0, index_str: str = \"1\", before_widget=None, use_pack: bool = True):",
    )

    assert "큰 재생목록용 경량 카드" in compact_method
    assert "self._display_thumbnail(thumb, action, size=ACTION_COMPACT_THUMB_IMAGE_SIZE)" in compact_method
    assert '"thumb_frame"] = thumb' in compact_method
    assert '"thumb_size"] = ACTION_COMPACT_THUMB_IMAGE_SIZE' in compact_method
    assert 'text=_collapse_toggle_text(is_collapsed)' in compact_method
    assert "_action_number_label_style(" in compact_method
    assert "font=self._font(*number_font)" in compact_method
    assert 'number.pack(side="left", padx=(0, 2), pady=8)' in compact_method
    assert "self._toggle_skip_mode_action" in compact_method
    assert "self._edit_repeat_count_action" in compact_method
    assert "self._edit_wait_time_action" in compact_method
    assert "self._delete_action" in compact_method
    assert "self._show_action_context_menu" in compact_method
    assert "self._compact_action_label_text(action)" in compact_method
    assert '"name_label"] = name_label' in compact_method


def test_sequence_detail_dialog_collapsed_children_are_lazy_rendered():
    text = _read_text()
    sequence_start = text.index("class SequenceDetailDialog")
    sequence_text = text[sequence_start:]
    create_method = _method_slice(
        sequence_text,
        "def _create_action_item(self, parent, action: Action, depth: int = 0, index_str: str = \"1\", before_widget=None, use_pack: bool = True):",
        "def _ensure_action_children_rendered(self, action_id) -> bool:",
    )
    ensure_method = _method_slice(
        sequence_text,
        "def _ensure_action_children_rendered(self, action_id) -> bool:",
        "def _display_thumbnail(self, parent, action: Action, size: int = 60):",
    )
    toggle_method = _method_slice(
        sequence_text,
        "def _toggle_item_collapse(self, action_id: str):",
        "def _on_drag_start(self, event, action: Action, widget):",
    )

    assert '"children_rendered"] = False' in create_method
    assert "if not is_collapsed:" in create_method
    assert "self._ensure_action_children_rendered(action.action_id)" in create_method
    assert "for child_idx, child in enumerate(getattr(action, \"children\", []) or [], 1):" in ensure_method
    assert "self._create_compact_action_item(children_container, child, depth + 1, index_str=child_index_str)" in ensure_method
    assert "self._create_action_item(children_container" not in ensure_method
    assert "self._ensure_action_children_rendered(action_id)" in toggle_method


def test_sequence_detail_dialog_property_edits_refresh_single_row():
    text = _read_text()
    sequence_start = text.index("class SequenceDetailDialog")
    sequence_text = text[sequence_start:]

    create_method = _method_slice(
        sequence_text,
        "def _create_action_item(self, parent, action: Action, depth: int = 0, index_str: str = \"1\", before_widget=None, use_pack: bool = True):",
        "def _ensure_action_children_rendered(self, action_id) -> bool:",
    )
    row_refresh_method = _method_slice(
        sequence_text,
        "def _refresh_action_row(self, action: Optional[Action]) -> bool:",
        "def _display_thumbnail(self, parent, action: Action, size: int = 60):",
    )
    compact_update_method = _method_slice(
        sequence_text,
        "def _update_compact_action_row(self, action: Action) -> bool:",
        "def _show_action_context_menu(self, event, action: Action, depth: int):",
    )

    assert '"before"] = before_widget' in create_method
    assert "self._drop_action_widget_mappings(action)" in row_refresh_method
    assert "wrapper.destroy()" in row_refresh_method
    assert "before_widget=before_widget" in row_refresh_method
    assert "self._create_compact_action_item(parent, action, depth=depth, index_str=index_str, before_widget=before_widget)" in row_refresh_method
    assert "self._create_action_item(parent, action" not in row_refresh_method
    assert "self._update_action_buttons(action)" in compact_update_method
    assert "name_label.configure(" in compact_update_method
    assert '"type_label"] = type_lbl' in create_method
    assert '"name_label"] = name_lbl' in create_method
    assert '"detail_label"] = detail_lbl' in create_method
    assert "type_label.configure(" in compact_update_method
    assert "detail_label.configure(" in compact_update_method

    single_row_pairs = [
        ("def _edit_action_name(self, action: Action):", "def _change_action_click_type(self, action: Action, new_type: str):"),
        ("def _change_action_click_type(self, action: Action, new_type: str):", "def _toggle_action_alternate_mouse_route(self, action: Action):"),
        ("def _toggle_action_alternate_mouse_route(self, action: Action):", "def _detach_action(self, action: Action):"),
        ("def _toggle_action_enabled(self, action: Action):", "def _copy_action(self, action: Action):"),
    ]
    for start, end in single_row_pairs:
        method = _method_slice(sequence_text, start, end)
        assert "if not self._update_compact_action_row(action):" in method
        assert "self._refresh_action_row(action)" in method
        assert "self._schedule_action_list_refresh()" not in method


def test_sequence_noncompact_property_update_changes_labels_without_row_recreate():
    action = Action(
        action_type="click",
        action_id="action",
        description="old",
        x=10,
        y=20,
    )
    dialog = _make_sequence_dialog_stub(Sequence(name="seq", actions=[action]), set())
    dialog._compact_action_rows = False
    dialog._selected_action = None
    row = _FakeWidget()
    type_label = _FakeWidget()
    name_label = _FakeWidget()
    detail_label = _FakeWidget()
    number_label = _FakeWidget()
    repeat_btn = _FakeWidget()
    delay_btn = _FakeWidget()
    skip_btn = _FakeWidget()
    dialog._action_widgets = {
        action.action_id: {
            "widget": row,
            "type_label": type_label,
            "name_label": name_label,
            "detail_label": detail_label,
            "number_label": number_label,
            "repeat_btn": repeat_btn,
            "delay_btn": delay_btn,
            "skip_btn": skip_btn,
        }
    }
    action.action_type = "double_click"
    action.description = "changed"
    action.alternate_mouse_route = True
    action.target_image = "sample.png"

    assert dialog._update_compact_action_row(action) is True

    assert type_label.config["text"] == "더블 클릭"
    assert name_label.config["text"] == " - changed"
    assert "이동경로 변경" in detail_label.config["text"]
    assert repeat_btn.config["text"] == "↻ 1회"
    assert delay_btn.config["text"].startswith("⏱ ")
    assert "초" in delay_btn.config["text"]
    assert delay_btn.config["width"] >= 82
    assert row.destroyed is False


def test_detail_dialog_thumbnails_use_bounded_worker_queue():
    text = _read_text()
    plan_method = _method_slice(
        text,
        "def _display_thumbnail(self, parent, rule: AutomationRule, size: int = 60):",
        "def _collect_all_image_rules(self) -> list:",
    )
    sequence_start = text.index("class SequenceDetailDialog")
    sequence_text = text[sequence_start:]
    sequence_method = _method_slice(
        sequence_text,
        "def _display_thumbnail(self, parent, action: Action, size: int = 60):",
        "def _collect_all_image_actions(self) -> list:",
    )

    assert "from .analyzer_view import (" in text
    assert "ImageCropDialog" in text
    assert "submit_thumbnail_task" in text
    assert "submit_thumbnail_task(_load_thumb)" in plan_method
    assert "submit_thumbnail_task(_load_thumb)" in sequence_method
    assert "threading.Thread(target=_load_thumb" not in plan_method
    assert "threading.Thread(target=_load_thumb" not in sequence_method


def test_player_image_editor_callbacks_use_current_navigated_rule_or_action():
    text = _read_text()
    plan_method = _method_slice(
        text,
        "def _open_image_editor(self, image_path: str, rule: AutomationRule):",
        "def _mark_modified(self):",
    )
    sequence_start = text.index("class SequenceDetailDialog")
    sequence_text = text[sequence_start:]
    sequence_method = _method_slice(
        sequence_text,
        "def _open_image_editor(self, image_path: str, action: Action):",
        "def _save_sequence(self):",
    )

    assert "def on_crop_complete(new_path: str, target_rule=None, old_path=None):" in plan_method
    assert "def on_delete(target_rule=None, old_path=None):" in plan_method
    assert "def on_change(new_path: str, target_rule=None, old_path=None):" in plan_method
    assert "def on_search_radius_change(target_rule=None):" in plan_method
    assert "changed_rule_ids.add(getattr(target, \"rule_id\", rule.rule_id))" in plan_method
    assert "self._update_rule_row_in_place(changed_rule)" in plan_method
    assert "self._refresh_rule_thumbnail(changed_rule)" in plan_method
    assert "self._schedule_action_list_refresh()" not in plan_method

    assert "def on_crop_complete(new_path: str, target_action=None, old_path=None):" in sequence_method
    assert "def on_delete(target_action=None, old_path=None):" in sequence_method
    assert "def on_change(new_path: str, target_action=None, old_path=None):" in sequence_method
    assert "def on_search_radius_change(target_action=None):" in sequence_method
    assert "changed_actions.append(target)" in sequence_method
    assert "self._update_compact_action_row(changed_action)" in sequence_method
    assert "self._refresh_action_thumbnail(changed_action)" in sequence_method
    assert "if not updated and not thumb_updated:" in sequence_method
    assert "if not refreshed:" not in sequence_method

    repeat_method = _method_slice(
        sequence_text,
        "def _edit_repeat_count_action(self, action: Action):",
        "def _edit_action_name(self, action: Action):",
    )
    assert "if not self._update_compact_action_row(action) and not self._refresh_action_row(action):" in repeat_method
    assert "self._update_action_buttons(action)" in repeat_method


def test_image_rule_navigation_collection_is_cached_and_invalidated():
    first = AutomationRule(action_type="click", rule_id="first", target_image="first.png")
    parent = AutomationRule(action_type="click", rule_id="parent")
    child = AutomationRule(action_type="click", rule_id="child", target_image="child.png", parent_id="parent")
    parent.children = [child]
    dialog = _make_plan_dialog_stub([first, parent], set())

    assert [rule.rule_id for rule in dialog._collect_all_image_rules()] == ["first", "child"]

    new_child = AutomationRule(action_type="click", rule_id="new_child", target_image="new.png", parent_id="parent")
    parent.children.append(new_child)
    assert [rule.rule_id for rule in dialog._collect_all_image_rules()] == ["first", "child"]

    dialog._invalidate_rule_tree_cache()
    assert [rule.rule_id for rule in dialog._collect_all_image_rules()] == ["first", "child", "new_child"]


def test_image_action_navigation_collection_is_cached_and_invalidated():
    first = Action(action_type="click", action_id="first", target_image="first.png")
    parent = Action(action_type="click", action_id="parent")
    child = Action(action_type="click", action_id="child", target_image="child.png", parent_id="parent")
    parent.children = [child]
    dialog = _make_sequence_dialog_stub(Sequence(name="seq", actions=[first, parent]), set())

    assert [action.action_id for action in dialog._collect_all_image_actions()] == ["first", "child"]

    new_child = Action(action_type="click", action_id="new_child", target_image="new.png", parent_id="parent")
    parent.children.append(new_child)
    assert [action.action_id for action in dialog._collect_all_image_actions()] == ["first", "child"]

    dialog._invalidate_action_tree_cache()
    assert [action.action_id for action in dialog._collect_all_image_actions()] == ["first", "child", "new_child"]


def test_image_editor_navigation_cache_is_invalidated_by_image_mutations():
    text = _read_text()
    plan_collect = _method_slice(
        text,
        "def _collect_all_image_rules(self) -> list:",
        "def _open_image_editor(self, image_path: str, rule: AutomationRule):",
    )
    plan_open = _method_slice(
        text,
        "def _open_image_editor(self, image_path: str, rule: AutomationRule):",
        "def _mark_modified(self):",
    )
    plan_invalidate = _method_slice(
        text,
        "def _invalidate_rule_tree_cache(self) -> None:",
        "def _get_rule_parent_cache(self) -> dict:",
    )
    sequence_start = text.index("class SequenceDetailDialog")
    sequence_text = text[sequence_start:]
    sequence_collect = _method_slice(
        sequence_text,
        "def _collect_all_image_actions(self) -> list:",
        "def _open_image_editor(self, image_path: str, action: Action):",
    )
    sequence_open = _method_slice(
        sequence_text,
        "def _open_image_editor(self, image_path: str, action: Action):",
        "def _save_sequence(self):",
    )
    sequence_invalidate = _method_slice(
        sequence_text,
        "def _invalidate_action_tree_cache(self) -> None:",
        "def _get_action_parent_cache(self) -> dict:",
    )

    assert "cached = getattr(self, \"_image_rule_cache\", None)" in plan_collect
    assert "self._image_rule_cache = tuple(result)" in plan_collect
    assert plan_open.count("self._invalidate_rule_image_cache()") >= 3
    assert "self._invalidate_rule_image_cache()" in plan_invalidate

    assert "cached = getattr(self, \"_image_action_cache\", None)" in sequence_collect
    assert "self._image_action_cache = tuple(result)" in sequence_collect
    assert sequence_open.count("self._invalidate_action_image_cache()") >= 3
    assert "self._invalidate_action_image_cache()" in sequence_invalidate


def test_player_image_editor_refreshes_thumbnails_without_rebuilding_visible_rows():
    text = _read_text()
    plan_create_method = _method_slice(
        text,
        "def _create_action_item(self, parent, rule: AutomationRule",
        "def _display_thumbnail(self, parent, rule: AutomationRule, size: int = 60):",
    )
    plan_compact_method = _method_slice(
        text,
        "def _create_compact_rule_item(self, parent, rule: AutomationRule",
        "def _create_action_item(self, parent, rule: AutomationRule",
    )
    plan_thumbnail_method = _method_slice(
        text,
        "def _refresh_rule_thumbnail(self, rule: AutomationRule) -> bool:",
        "def _create_action_item(self, parent, rule: AutomationRule",
    )
    sequence_start = text.index("class SequenceDetailDialog")
    sequence_text = text[sequence_start:]
    sequence_create_method = _method_slice(
        sequence_text,
        "def _create_action_item(self, parent, action: Action",
        "def _ensure_action_children_rendered(self, action_id) -> bool:",
    )
    sequence_compact_method = _method_slice(
        sequence_text,
        "def _create_compact_action_item(self, parent, action: Action",
        "def _create_action_item(self, parent, action: Action",
    )
    sequence_thumbnail_method = _method_slice(
        sequence_text,
        "def _refresh_action_thumbnail(self, action: Action) -> bool:",
        "def _show_action_context_menu(self, event, action: Action, depth: int):",
    )

    assert 'self._rule_widgets[rule.rule_id]["thumb_frame"] = thumb' in plan_create_method
    assert 'self._rule_widgets[rule.rule_id]["thumb_frame"] = thumb' in plan_compact_method
    assert 'self._rule_widgets[rule.rule_id]["thumb_size"] = ACTION_COMPACT_THUMB_IMAGE_SIZE' in plan_compact_method
    assert "self._display_thumbnail(thumb, rule, size=ACTION_COMPACT_THUMB_IMAGE_SIZE)" in plan_compact_method
    assert 'thumb.pack(side="left", padx=(0, 4), pady=6)' in plan_compact_method
    assert plan_compact_method.index('self._rule_widgets[rule.rule_id]["thumb_frame"] = thumb') < plan_compact_method.index("name_label = ctk.CTkLabel(")
    assert "for child in thumb_frame.winfo_children():" in plan_thumbnail_method
    assert "self._display_thumbnail(thumb_frame, rule, size=widget_data.get(\"thumb_size\", 60))" in plan_thumbnail_method
    assert 'self._action_widgets[action.action_id]["thumb_frame"] = thumb' in sequence_create_method
    assert 'self._action_widgets[action.action_id]["thumb_frame"] = thumb' in sequence_compact_method
    assert 'self._action_widgets[action.action_id]["thumb_size"] = ACTION_COMPACT_THUMB_IMAGE_SIZE' in sequence_compact_method
    assert "self._display_thumbnail(thumb, action, size=ACTION_COMPACT_THUMB_IMAGE_SIZE)" in sequence_compact_method
    assert 'thumb.pack(side="left", padx=(0, 4), pady=6)' in sequence_compact_method
    assert sequence_compact_method.index('self._action_widgets[action.action_id]["thumb_frame"] = thumb') < sequence_compact_method.index("name_label = ctk.CTkLabel(")
    assert "for child in thumb_frame.winfo_children():" in sequence_thumbnail_method
    assert "self._display_thumbnail(thumb_frame, action, size=widget_data.get(\"thumb_size\", 60))" in sequence_thumbnail_method
    assert "self._refresh_action_row(action)" not in sequence_thumbnail_method


def test_trigger_image_confidence_slider_supports_left_right_arrow_adjustment():
    text = _read_text()
    method = _method_slice(
        text,
        "def _edit_trigger_image(self, rule: AutomationRule):",
        "def _edit_monitoring_mode(self, rule: AutomationRule):",
    )

    assert "def adjust_confidence(delta: int):" in method
    assert "conf_var.set(max(30, min(95, current + delta)))" in method
    assert 'if key == "Left":' in method
    assert "adjust_confidence(-1)" in method
    assert 'if key == "Right":' in method
    assert "adjust_confidence(1)" in method
    assert 'conf_slider.bind("<Left>", on_conf_key)' in method
    assert 'conf_slider.bind("<Right>", on_conf_key)' in method
    assert 'dialog.bind("<Left>", on_conf_key)' in method
    assert 'dialog.bind("<Right>", on_conf_key)' in method
    assert "dialog.after(100, conf_slider.focus_set)" in method


def test_sequence_detail_large_collapsed_tree_flatten_is_top_level_only_and_fast():
    top_level = []
    collapsed = set()
    for parent_idx in range(1000):
        parent = Action(action_type="click", action_id=f"parent_{parent_idx}")
        parent.children = [
            Action(
                action_type="key_press",
                action_id=f"child_{parent_idx}_{child_idx}",
                parent_id=parent.action_id,
            )
            for child_idx in range(20)
        ]
        top_level.append(parent)
        collapsed.add(parent.action_id)

    dialog = _make_sequence_dialog_stub(
        Sequence(name="large", actions=top_level),
        collapsed_items=collapsed,
    )

    started = perf_counter()
    flat = dialog._get_flat_actions_with_depth()
    elapsed = perf_counter() - started

    assert len(flat) == len(top_level)
    assert all(item["depth"] == 0 for item in flat)
    assert all(item["action"].action_id.startswith("parent_") for item in flat)
    assert elapsed < 0.1


def test_sequence_detail_counts_nested_actions_for_compact_threshold():
    parent = Action(action_type="click", action_id="parent")
    parent.children = [
        Action(action_type="key_press", action_id=f"child_{idx}", parent_id=parent.action_id)
        for idx in range(250)
    ]
    dialog = _make_sequence_dialog_stub(Sequence(name="count", actions=[parent]), set())

    assert dialog._count_action_tree([parent]) == 251


def test_sequence_detail_expanded_tree_flatten_keeps_stable_child_indices():
    parent = Action(action_type="click", action_id="parent")
    parent.children = [
        Action(action_type="key_press", action_id=f"child_{idx}", parent_id=parent.action_id)
        for idx in range(3)
    ]
    dialog = _make_sequence_dialog_stub(
        Sequence(name="expanded", actions=[parent]),
        collapsed_items=set(),
    )

    flat = dialog._get_flat_actions_with_depth()

    assert [item["action"].action_id for item in flat] == ["parent", "child_0", "child_1", "child_2"]
    assert [item["depth"] for item in flat] == [0, 1, 1, 1]
    assert [item["index_str"] for item in flat] == ["1", "1-1", "1-2", "1-3"]


def test_sequence_detail_repairs_legacy_flat_parent_links_before_flattening():
    parent = Action(action_type="click", action_id="parent")
    child = Action(action_type="key_press", action_id="child", parent_id="parent")
    sibling = Action(action_type="type", action_id="sibling")
    dialog = _make_sequence_dialog_stub(Sequence(name="legacy", actions=[parent, child, sibling]), set())

    dialog._repair_flat_action_hierarchy()
    flat = dialog._get_flat_actions_with_depth()

    assert dialog._sequence.actions == [parent, sibling]
    assert parent.children == [child]
    assert [item["action"].action_id for item in flat] == ["parent", "child", "sibling"]
    assert [item["depth"] for item in flat] == [0, 1, 0]
    assert [item["index_str"] for item in flat] == ["1", "1-1", "2"]


def test_sequence_detail_keeps_orphaned_legacy_parent_links_visible():
    orphan = Action(action_type="click", action_id="orphan", parent_id="missing_parent")
    sibling = Action(action_type="type", action_id="sibling")
    dialog = _make_sequence_dialog_stub(Sequence(name="orphaned", actions=[orphan, sibling]), set())

    dialog._repair_flat_action_hierarchy()
    flat = dialog._get_flat_actions_with_depth()

    assert [item["action"].action_id for item in flat] == ["orphan", "sibling"]
    assert [item["depth"] for item in flat] == [0, 0]


def test_sequence_detail_repaired_legacy_children_start_collapsed():
    parent = Action(action_type="click", action_id="parent")
    child = Action(action_type="key_press", action_id="child", parent_id="parent")
    dialog = _make_sequence_dialog_stub(Sequence(name="legacy", actions=[parent, child]), set())

    dialog._repair_flat_action_hierarchy()
    dialog._init_collapsed_items()
    flat = dialog._get_flat_actions_with_depth()

    assert parent.action_id in dialog._collapsed_items
    assert [item["action"].action_id for item in flat] == ["parent"]


def test_sequence_detail_repair_handles_parent_id_cycles_without_hanging():
    first = Action(action_type="click", action_id="first", parent_id="second")
    second = Action(action_type="key_press", action_id="second", parent_id="first")
    dialog = _make_sequence_dialog_stub(Sequence(name="cycle", actions=[first, second]), set())

    started = perf_counter()
    dialog._repair_flat_action_hierarchy()
    flat = dialog._get_flat_actions_with_depth()
    elapsed = perf_counter() - started

    assert elapsed < 0.05
    assert first.children == []
    assert second.children == []
    assert [item["action"].action_id for item in flat] == ["first", "second"]


def test_sequence_detail_count_and_flatten_ignore_existing_child_cycles():
    parent = Action(action_type="click", action_id="parent")
    child = Action(action_type="key_press", action_id="child", parent_id="parent")
    parent.children.append(child)
    child.children.append(parent)
    dialog = _make_sequence_dialog_stub(Sequence(name="child_cycle", actions=[parent]), set())

    assert dialog._count_action_tree(dialog._sequence.actions) == 2
    flat = dialog._get_flat_actions_with_depth()

    assert [item["action"].action_id for item in flat] == ["parent", "child"]
    assert [item["depth"] for item in flat] == [0, 1]


def test_sequence_detail_repairs_large_legacy_flat_playlist_without_ui_block():
    parent = Action(action_type="click", action_id="parent")
    actions = [parent]
    actions.extend(
        Action(action_type="key_press", action_id=f"child_{idx}", parent_id="parent")
        for idx in range(2500)
    )
    dialog = _make_sequence_dialog_stub(Sequence(name="large_legacy", actions=actions), {"parent"})

    started = perf_counter()
    dialog._repair_flat_action_hierarchy()
    flat = dialog._get_flat_actions_with_depth()
    elapsed = perf_counter() - started

    assert elapsed < 0.25
    assert len(parent.children) == 2500
    assert dialog._sequence.actions == [parent]
    assert [item["action"].action_id for item in flat] == ["parent"]


def test_plan_child_attach_expands_target_parent_and_preserves_visible_child():
    parent = AutomationRule(action_type="click", rule_id="parent")
    child = AutomationRule(action_type="key_press", rule_id="child")
    dialog = _make_plan_dialog_stub([parent, child], {"parent"})
    dialog._modified = False
    dialog._schedule_action_list_refresh = lambda *args, **kwargs: None

    dialog._move_rule_to_target(child, parent)

    assert parent.rule_id not in dialog._collapsed_items
    assert child in parent.children
    assert child not in dialog._plan.initial_rules
    flat = dialog._get_flat_rules_with_depth()
    assert [item["rule"].rule_id for item in flat] == ["parent", "child"]
    assert [item["depth"] for item in flat] == [0, 1]


def test_sequence_child_attach_expands_target_parent_and_preserves_visible_child():
    parent = Action(action_type="click", action_id="parent")
    child = Action(action_type="key_press", action_id="child")
    dialog = _make_sequence_dialog_stub(Sequence(name="seq", actions=[parent, child]), {"parent"})
    dialog._modified = False
    dialog._schedule_action_list_refresh = lambda *args, **kwargs: None

    dialog._move_action_to_target(child, parent)

    assert parent.action_id not in dialog._collapsed_items
    assert child in parent.children
    assert child not in dialog._sequence.actions
    flat = dialog._get_flat_actions_with_depth()
    assert [item["action"].action_id for item in flat] == ["parent", "child"]
    assert [item["depth"] for item in flat] == [0, 1]


def test_plan_drag_attach_syncs_visible_items_without_scheduled_full_refresh():
    parent = AutomationRule(action_type="click", rule_id="parent")
    child = AutomationRule(action_type="key_press", rule_id="child")
    dialog = _make_plan_dialog_stub([parent, child], {"parent"})
    dialog._scrollable = _make_virtual_scroll_stub(
        [
            {"rule": parent, "depth": 0, "index_str": "1", "parent_id": None},
            {"rule": child, "depth": 0, "index_str": "2", "parent_id": None},
        ],
        {},
    )
    scheduled = []
    synced = []
    dialog._schedule_action_list_refresh = lambda *args, **kwargs: scheduled.append((args, kwargs))
    dialog._sync_visible_rules_from_model = lambda *args, **kwargs: synced.append((args, kwargs)) or True

    dialog._move_rule_to_target(child, parent)

    updated = dialog._scrollable.get_items()
    assert scheduled == []
    assert synced == []
    assert parent.rule_id not in dialog._collapsed_items
    assert [item["rule"].rule_id for item in updated] == ["parent", "child"]
    assert [item["depth"] for item in updated] == [0, 1]
    assert [item["index_str"] for item in updated] == ["1", "1.1"]


def test_sequence_drag_attach_syncs_visible_items_without_scheduled_full_refresh():
    parent = Action(action_type="click", action_id="parent")
    child = Action(action_type="key_press", action_id="child")
    dialog = _make_sequence_dialog_stub(Sequence(name="seq", actions=[parent, child]), {"parent"})
    dialog._scrollable = _make_virtual_scroll_stub(
        [
            {"action": parent, "depth": 0, "index_str": "1"},
            {"action": child, "depth": 0, "index_str": "2"},
        ],
        {},
    )
    dialog._batch_render_id = None
    scheduled = []
    synced = []
    dialog._schedule_action_list_refresh = lambda *args, **kwargs: scheduled.append((args, kwargs))
    dialog._sync_visible_actions_from_model = lambda *args, **kwargs: synced.append((args, kwargs)) or True

    dialog._move_action_to_target(child, parent)

    updated = dialog._scrollable.get_items()
    assert scheduled == []
    assert synced == []
    assert parent.action_id not in dialog._collapsed_items
    assert [item["action"].action_id for item in updated] == ["parent", "child"]
    assert [item["depth"] for item in updated] == [0, 1]
    assert [item["index_str"] for item in updated] == ["1", "1-1"]


def test_detach_uses_visible_sync_helpers_instead_of_full_refresh():
    text = _read_text()
    plan_method = _method_slice(
        text,
        "def _detach_rule(self, rule: AutomationRule):",
        "def _setup_copied_game_mode_maps(self, src_rule_id: str, config):",
    )
    sequence_start = text.index("class SequenceDetailDialog")
    sequence_text = text[sequence_start:]
    sequence_method = _method_slice(
        sequence_text,
        "def _detach_action(self, action: Action):",
        "def _toggle_action_enabled(self, action: Action):",
    )

    assert "self._apply_visible_rule_detach(parent, rule)" in plan_method
    assert "self._sync_visible_rules_from_model()" in plan_method
    assert "self._apply_visible_action_detach(parent, action)" in sequence_method
    assert "self._sync_visible_actions_from_model()" in sequence_method
    assert "self._refresh_action_list()" not in plan_method
    assert "self._refresh_action_list()" not in sequence_method


def test_plan_detach_patches_visible_items_without_full_sync():
    parent = AutomationRule(action_type="click", rule_id="parent")
    child = AutomationRule(action_type="key_press", rule_id="child", parent_id="parent")
    sibling = AutomationRule(action_type="type", rule_id="sibling")
    parent.children.append(child)
    dialog = _make_plan_dialog_stub([parent, sibling], set())
    dialog._scrollable = _make_virtual_scroll_stub(
        [
            {"rule": parent, "depth": 0, "index_str": "1", "parent_id": None},
            {"rule": child, "depth": 1, "index_str": "1.1", "parent_id": "parent"},
            {"rule": sibling, "depth": 0, "index_str": "2", "parent_id": None},
        ],
        {},
    )
    synced = []
    dialog._sync_visible_rules_from_model = lambda *args, **kwargs: synced.append((args, kwargs)) or True

    dialog._detach_rule(child)

    updated = dialog._scrollable.get_items()
    assert synced == []
    assert child not in parent.children
    assert dialog._plan.initial_rules == [parent, child, sibling]
    assert [item["rule"].rule_id for item in updated] == ["parent", "child", "sibling"]
    assert [item["depth"] for item in updated] == [0, 0, 0]
    assert [item["index_str"] for item in updated] == ["1", "2", "3"]


def test_sequence_detach_patches_visible_items_without_full_sync():
    parent = Action(action_type="click", action_id="parent")
    child = Action(action_type="key_press", action_id="child", parent_id="parent")
    sibling = Action(action_type="type", action_id="sibling")
    parent.children.append(child)
    dialog = _make_sequence_dialog_stub(Sequence(name="seq", actions=[parent, sibling]), set())
    dialog._scrollable = _make_virtual_scroll_stub(
        [
            {"action": parent, "depth": 0, "index_str": "1"},
            {"action": child, "depth": 1, "index_str": "1-1"},
            {"action": sibling, "depth": 0, "index_str": "2"},
        ],
        {},
    )
    synced = []
    dialog._sync_visible_actions_from_model = lambda *args, **kwargs: synced.append((args, kwargs)) or True

    dialog._detach_action(child)

    updated = dialog._scrollable.get_items()
    assert synced == []
    assert child not in parent.children
    assert dialog._sequence.actions == [parent, child, sibling]
    assert [item["action"].action_id for item in updated] == ["parent", "child", "sibling"]
    assert [item["depth"] for item in updated] == [0, 0, 0]
    assert [item["index_str"] for item in updated] == ["1", "2", "3"]


def test_plan_flatten_children_patches_visible_items_without_full_sync():
    parent = AutomationRule(action_type="click", rule_id="parent")
    child = AutomationRule(action_type="key_press", rule_id="child", parent_id="parent")
    grandchild = AutomationRule(action_type="type", rule_id="grandchild", parent_id="child")
    sibling = AutomationRule(action_type="click", rule_id="sibling")
    child.children.append(grandchild)
    parent.children.append(child)
    dialog = _make_plan_dialog_stub([parent, sibling], set())
    dialog._scrollable = _make_virtual_scroll_stub(
        [
            {"rule": parent, "depth": 0, "index_str": "1", "parent_id": None},
            {"rule": child, "depth": 1, "index_str": "1.1", "parent_id": "parent"},
            {"rule": grandchild, "depth": 2, "index_str": "1.1.1", "parent_id": "child"},
            {"rule": sibling, "depth": 0, "index_str": "2", "parent_id": None},
        ],
        {},
    )
    synced = []
    dialog._sync_visible_rules_from_model = lambda *args, **kwargs: synced.append((args, kwargs)) or True
    children = list(parent.children)
    assert _flatten_children_after_parent(dialog._plan.initial_rules, parent, "rule_id") == 1

    assert dialog._apply_visible_rule_flatten_children(parent, children) is True

    updated = dialog._scrollable.get_items()
    assert synced == []
    assert parent.children == []
    assert dialog._plan.initial_rules == [parent, child, sibling]
    assert [item["rule"].rule_id for item in updated] == ["parent", "child", "grandchild", "sibling"]
    assert [item["depth"] for item in updated] == [0, 0, 1, 0]
    assert [item["index_str"] for item in updated] == ["1", "2", "2.1", "3"]


def test_sequence_flatten_children_patches_visible_items_without_full_sync():
    parent = Action(action_type="click", action_id="parent")
    child = Action(action_type="key_press", action_id="child", parent_id="parent")
    grandchild = Action(action_type="type", action_id="grandchild", parent_id="child")
    sibling = Action(action_type="click", action_id="sibling")
    child.children.append(grandchild)
    parent.children.append(child)
    dialog = _make_sequence_dialog_stub(Sequence(name="seq", actions=[parent, sibling]), set())
    dialog._scrollable = _make_virtual_scroll_stub(
        [
            {"action": parent, "depth": 0, "index_str": "1"},
            {"action": child, "depth": 1, "index_str": "1-1"},
            {"action": grandchild, "depth": 2, "index_str": "1-1-1"},
            {"action": sibling, "depth": 0, "index_str": "2"},
        ],
        {},
    )
    synced = []
    dialog._sync_visible_actions_from_model = lambda *args, **kwargs: synced.append((args, kwargs)) or True
    children = list(parent.children)
    assert _flatten_children_after_parent(dialog._sequence.actions, parent, "action_id") == 1

    assert dialog._apply_visible_action_flatten_children(parent, children) is True

    updated = dialog._scrollable.get_items()
    assert synced == []
    assert parent.children == []
    assert dialog._sequence.actions == [parent, child, sibling]
    assert [item["action"].action_id for item in updated] == ["parent", "child", "grandchild", "sibling"]
    assert [item["depth"] for item in updated] == [0, 0, 1, 0]
    assert [item["index_str"] for item in updated] == ["1", "2", "2-1", "3"]


def test_plan_add_button_uses_selected_rule_as_parent():
    parent = AutomationRule(action_type="click", rule_id="parent")
    child = AutomationRule(action_type="key_press", rule_id="child")
    dialog = _make_plan_dialog_stub([parent], {"parent"})
    dialog._selected_rule = parent

    attached_parent = dialog._add_rule_to_current_parent(child)

    assert attached_parent is parent
    assert child.parent_id == parent.rule_id
    assert child in parent.children
    assert child not in dialog._plan.initial_rules
    assert parent.rule_id not in dialog._collapsed_items
    flat = dialog._get_flat_rules_with_depth()
    assert [item["rule"].rule_id for item in flat] == ["parent", "child"]
    assert [item["depth"] for item in flat] == [0, 1]


def test_plan_action_click_keeps_selected_parent_visible_for_child_add():
    parent = AutomationRule(action_type="click", rule_id="parent")
    other = AutomationRule(action_type="key_press", rule_id="other")
    dialog = _make_plan_dialog_stub([parent, other], set())
    parent_row = _FakeWidget()
    other_row = _FakeWidget()
    dialog._selected_rule = None
    dialog._active_partial_rule_id = None
    dialog._rule_widgets = {
        parent.rule_id: {"widget": parent_row, "rule": parent},
        other.rule_id: {"widget": other_row, "rule": other},
    }

    dialog._select_rule(parent)
    dialog._select_rule(parent)
    dialog._select_rule(other)

    assert dialog._selected_rule is other
    assert parent_row.config["fg_color"] == COLORS["bg_glass"]
    assert other_row.config["fg_color"] == COLORS["selection_green"]


def test_plan_drag_start_selects_action_without_overwriting_selection_color():
    parent = AutomationRule(action_type="click", rule_id="parent")
    dialog = _make_plan_dialog_stub([parent], set())
    row = _FakeWidget()
    dialog._selected_rule = None
    dialog._active_partial_rule_id = None
    dialog._rule_widgets = {
        parent.rule_id: {"widget": row, "rule": parent},
    }
    dialog._drag_data = {"rule": None, "widget": None, "start_y": 0}
    event = SimpleNamespace(y_root=100)

    dialog._on_drag_start(event, parent, row)

    assert dialog._selected_rule is parent
    assert row.config["fg_color"] == COLORS["selection_green"]


def test_plan_add_button_falls_back_to_top_level_when_selection_is_stale():
    current = AutomationRule(action_type="click", rule_id="current")
    stale = AutomationRule(action_type="click", rule_id="stale")
    child = AutomationRule(action_type="key_press", rule_id="child")
    dialog = _make_plan_dialog_stub([current], set())
    dialog._selected_rule = stale

    attached_parent = dialog._add_rule_to_current_parent(child)

    assert attached_parent is None
    assert dialog._selected_rule is None
    assert child.parent_id is None
    assert child in dialog._plan.initial_rules
    assert not stale.children


def test_plan_add_button_resolves_selected_parent_by_id_after_refresh():
    current = AutomationRule(action_type="click", rule_id="parent")
    selected_snapshot = AutomationRule(action_type="click", rule_id="parent")
    child = AutomationRule(action_type="key_press", rule_id="child")
    dialog = _make_plan_dialog_stub([current], {"parent"})
    dialog._selected_rule = selected_snapshot

    attached_parent = dialog._add_rule_to_current_parent(child)

    assert attached_parent is current
    assert dialog._selected_rule is current
    assert child.parent_id == current.rule_id
    assert child in current.children
    assert child not in dialog._plan.initial_rules
    assert current.rule_id not in dialog._collapsed_items


def test_sequence_add_button_uses_selected_action_as_parent():
    parent = Action(action_type="click", action_id="parent")
    child = Action(action_type="key_press", action_id="child")
    dialog = _make_sequence_dialog_stub(Sequence(name="seq", actions=[parent]), {"parent"})
    dialog._selected_action = parent

    attached_parent = dialog._add_action_to_current_parent(child)

    assert attached_parent is parent
    assert child.parent_id == parent.action_id
    assert child in parent.children
    assert child not in dialog._sequence.actions
    assert parent.action_id not in dialog._collapsed_items
    flat = dialog._get_flat_actions_with_depth()
    assert [item["action"].action_id for item in flat] == ["parent", "child"]
    assert [item["depth"] for item in flat] == [0, 1]


def test_sequence_action_click_keeps_selected_parent_visible_for_child_add():
    parent = Action(action_type="click", action_id="parent")
    other = Action(action_type="key_press", action_id="other")
    dialog = _make_sequence_dialog_stub(Sequence(name="seq", actions=[parent, other]), set())
    parent_row = _FakeWidget()
    other_row = _FakeWidget()
    dialog._selected_action = None
    dialog._action_widgets = {
        parent.action_id: {"widget": parent_row, "action": parent},
        other.action_id: {"widget": other_row, "action": other},
    }

    dialog._select_action(parent)
    dialog._select_action(parent)
    dialog._select_action(other)

    assert dialog._selected_action is other
    assert parent_row.config["fg_color"] == COLORS["bg_glass"]
    assert other_row.config["fg_color"] == COLORS["selection_green"]


def test_sequence_drag_start_selects_action_without_overwriting_selection_color():
    parent = Action(action_type="click", action_id="parent")
    dialog = _make_sequence_dialog_stub(Sequence(name="seq", actions=[parent]), set())
    row = _FakeWidget()
    dialog._selected_action = None
    dialog._action_widgets = {
        parent.action_id: {"widget": row, "action": parent},
    }
    dialog._drag_data = {"action": None, "widget": None, "start_y": 0}
    event = SimpleNamespace(y_root=100)

    dialog._on_drag_start(event, parent, row)

    assert dialog._selected_action is parent
    assert row.config["fg_color"] == COLORS["selection_green"]


def test_sequence_add_button_falls_back_to_top_level_when_selection_is_stale():
    current = Action(action_type="click", action_id="current")
    stale = Action(action_type="click", action_id="stale")
    child = Action(action_type="key_press", action_id="child")
    dialog = _make_sequence_dialog_stub(Sequence(name="seq", actions=[current]), set())
    dialog._selected_action = stale

    attached_parent = dialog._add_action_to_current_parent(child)

    assert attached_parent is None
    assert dialog._selected_action is None
    assert child.parent_id is None
    assert child in dialog._sequence.actions
    assert not stale.children


def test_sequence_add_button_resolves_selected_parent_by_id_after_refresh():
    current = Action(action_type="click", action_id="parent")
    selected_snapshot = Action(action_type="click", action_id="parent")
    child = Action(action_type="key_press", action_id="child")
    dialog = _make_sequence_dialog_stub(Sequence(name="seq", actions=[current]), {"parent"})
    dialog._selected_action = selected_snapshot

    attached_parent = dialog._add_action_to_current_parent(child)

    assert attached_parent is current
    assert dialog._selected_action is current
    assert child.parent_id == current.action_id
    assert child in current.children
    assert child not in dialog._sequence.actions
    assert current.action_id not in dialog._collapsed_items


def test_action_add_buttons_route_through_selected_parent_helpers():
    text = _read_text()
    assert text.count("self._add_rule_to_current_parent(new_rule)") == 9
    assert text.count("self._add_action_to_current_parent(new_action)") == 9
    assert text.count("self._refresh_after_rule_added(parent_rule, new_rule)") == 9
    assert text.count("self._refresh_after_action_added(parent_action, new_action)") == 9


def test_paste_uses_local_insert_refresh_helpers():
    text = _read_text()
    paste_rule_method = _method_slice(
        text,
        "def _paste_rule(self, target_rule: AutomationRule):",
        "def _paste_rule_top(self):",
    )
    paste_rule_top_method = _method_slice(
        text,
        "def _paste_rule_top(self):",
        "def _paste_as_monitor_action(self, rule_index: int, watch_index: int):",
    )
    sequence_start = text.index("class SequenceDetailDialog")
    sequence_text = text[sequence_start:]
    paste_action_method = _method_slice(
        sequence_text,
        "def _paste_action(self, target_action: Action):",
        "def _paste_action_top(self):",
    )
    paste_action_top_method = _method_slice(
        sequence_text,
        "def _paste_action_top(self):",
        "def _paste_as_monitoring_watch_action(self, action: Action):",
    )

    assert "self._refresh_after_rule_added(target_rule, new_rule)" in paste_rule_method
    assert "self._refresh_after_rule_added(None, new_rule)" in paste_rule_top_method
    assert "self._refresh_after_action_added(target_action, new_action)" in paste_action_method
    assert "self._refresh_after_action_added(None, new_action)" in paste_action_top_method
    assert "self._schedule_action_list_refresh()" not in paste_rule_method
    assert "self._schedule_action_list_refresh()" not in paste_action_method


def test_paste_under_parent_does_not_collapse_target_parent():
    text = _read_text()
    paste_rule_method = _method_slice(
        text,
        "def _paste_rule(self, target_rule: AutomationRule):",
        "def _paste_rule_top(self):",
    )
    paste_action_method = _method_slice(
        text,
        "def _paste_action(self, target_action: Action):",
        "def _paste_action_top(self):",
    )

    assert "self._collapsed_items.discard(target_rule.rule_id)" in paste_rule_method
    assert "self._collapsed_items.add(target_rule.rule_id)" not in paste_rule_method
    assert "self._collapsed_items.discard(target_action.action_id)" in paste_action_method


def test_monitor_paste_refreshes_only_changed_row_before_falling_back():
    text = _read_text()
    plan_direct_method = _method_slice(
        text,
        "def _paste_as_monitor_action(self, rule_index: int, watch_index: int):",
        "def _paste_as_monitoring_watch(self, rule: AutomationRule):",
    )
    plan_watch_method = _method_slice(
        text,
        "def _paste_as_monitoring_watch(self, rule: AutomationRule):",
        "def _add_rule_to_current_parent(self, new_rule: AutomationRule) -> Optional[AutomationRule]:",
    )
    sequence_start = text.index("class SequenceDetailDialog")
    sequence_text = text[sequence_start:]
    sequence_watch_method = _method_slice(
        sequence_text,
        "def _paste_as_monitoring_watch_action(self, action: Action):",
        "def _paste_as_monitor_action_seq(self, action_index: int, watch_index: int):",
    )
    sequence_direct_method = _method_slice(
        sequence_text,
        "def _paste_as_monitor_action_seq(self, action_index: int, watch_index: int):",
        "def _add_action_to_current_parent(self, new_action: Action) -> Optional[Action]:",
    )

    assert "if not self._update_rule_row_in_place(rule):" in plan_direct_method
    assert "if not self._update_rule_row_in_place(rule):" in plan_watch_method
    assert "self._refresh_rule_row(rule.rule_id)" in plan_direct_method
    assert "self._refresh_rule_row(rule.rule_id)" in plan_watch_method
    assert "if not self._update_compact_action_row(action):" in sequence_watch_method
    assert "if not self._update_compact_action_row(action):" in sequence_direct_method
    assert "self._refresh_action_row(action)" in sequence_watch_method
    assert "self._refresh_action_row(action)" in sequence_direct_method
    assert "self._refresh_action_list()" not in plan_direct_method
    assert "self._refresh_action_list()" not in plan_watch_method
    assert "self._refresh_action_list()" not in sequence_watch_method
    assert "self._refresh_action_list()" not in sequence_direct_method


def test_sequence_detail_destroy_callback_removes_only_matching_visible_mapping():
    action = Action(action_type="click", action_id="visible")
    other = Action(action_type="click", action_id="other")
    visible_widget = object()
    stale_widget = object()
    dialog = _make_sequence_dialog_stub(Sequence(name="destroy", actions=[action, other]), set())
    dialog._action_widgets = {
        action.action_id: {"wrapper": visible_widget},
        other.action_id: {"wrapper": stale_widget},
    }

    dialog._on_action_item_destroy({"action": action}, 0, object())
    assert action.action_id in dialog._action_widgets

    dialog._on_action_item_destroy({"action": action}, 0, visible_widget)
    assert action.action_id not in dialog._action_widgets
    assert other.action_id in dialog._action_widgets


def test_plan_toggle_all_collapse_marks_only_top_level_parents_and_lazy_collapses_children():
    parent = AutomationRule(action_type="click", rule_id="parent")
    child = AutomationRule(action_type="click", rule_id="child", parent_id=parent.rule_id)
    grandchild = AutomationRule(action_type="click", rule_id="grandchild", parent_id=child.rule_id)
    child.children.append(grandchild)
    parent.children.append(child)

    dialog = _make_plan_dialog_stub([parent], set())
    dialog._all_collapsed = False
    dialog._collapse_btn = _FakeButton()
    synced = []
    dialog._sync_visible_rules_from_model = lambda *args, **kwargs: synced.append((args, kwargs)) or True

    dialog._toggle_all_collapse()

    assert dialog._collapsed_items == {parent.rule_id}
    assert synced
    assert dialog._collapse_btn.config["text"] == "모두 펼치기"

    dialog._collapsed_items.discard(parent.rule_id)
    flat = dialog._get_flat_rules_with_depth()

    assert [item["rule"].rule_id for item in flat] == ["parent", "child"]
    assert child.rule_id in dialog._collapsed_items


def test_sequence_toggle_all_collapse_marks_only_top_level_parents_and_lazy_collapses_children():
    parent = Action(action_type="click", action_id="parent")
    child = Action(action_type="click", action_id="child", parent_id=parent.action_id)
    grandchild = Action(action_type="click", action_id="grandchild", parent_id=child.action_id)
    child.children.append(grandchild)
    parent.children.append(child)

    dialog = _make_sequence_dialog_stub(Sequence(name="seq", actions=[parent]), set())
    dialog._all_collapsed = False
    dialog._collapse_btn = _FakeButton()
    synced = []
    dialog._sync_visible_actions_from_model = lambda *args, **kwargs: synced.append((args, kwargs)) or True

    dialog._toggle_all_collapse()

    assert dialog._collapsed_items == {parent.action_id}
    assert synced
    assert dialog._collapse_btn.config["text"] == "모두 펼치기"

    dialog._collapsed_items.discard(parent.action_id)
    flat = dialog._get_flat_actions_with_depth()

    assert [item["action"].action_id for item in flat] == ["parent", "child"]
    assert child.action_id in dialog._collapsed_items


def test_plan_toggle_all_collapse_uses_visible_root_filter_without_full_sync():
    parent = AutomationRule(action_type="click", rule_id="parent")
    child = AutomationRule(action_type="click", rule_id="child", parent_id=parent.rule_id)
    grandchild = AutomationRule(action_type="click", rule_id="grandchild", parent_id=child.rule_id)
    sibling = AutomationRule(action_type="click", rule_id="sibling")
    child.children.append(grandchild)
    parent.children.append(child)
    dialog = _make_plan_dialog_stub([parent, sibling], set())
    dialog._all_collapsed = False
    dialog._collapse_btn = _FakeButton()
    dialog._scrollable = _make_virtual_scroll_stub(
        [
            {"rule": parent, "depth": 0, "index_str": "1", "parent_id": None},
            {"rule": child, "depth": 1, "index_str": "1.1", "parent_id": "parent"},
            {"rule": grandchild, "depth": 2, "index_str": "1.1.1", "parent_id": "child"},
            {"rule": sibling, "depth": 0, "index_str": "2", "parent_id": None},
        ],
        {},
    )
    synced = []
    dialog._sync_visible_rules_from_model = lambda *args, **kwargs: synced.append((args, kwargs)) or True

    dialog._toggle_all_collapse()

    updated = dialog._scrollable.get_items()
    assert synced == []
    assert [item["rule"].rule_id for item in updated] == ["parent", "sibling"]
    assert [item["index_str"] for item in updated] == ["1", "2"]


def test_sequence_toggle_all_collapse_uses_visible_root_filter_without_full_sync():
    parent = Action(action_type="click", action_id="parent")
    child = Action(action_type="click", action_id="child", parent_id=parent.action_id)
    grandchild = Action(action_type="click", action_id="grandchild", parent_id=child.action_id)
    sibling = Action(action_type="click", action_id="sibling")
    child.children.append(grandchild)
    parent.children.append(child)
    dialog = _make_sequence_dialog_stub(Sequence(name="seq", actions=[parent, sibling]), set())
    dialog._all_collapsed = False
    dialog._collapse_btn = _FakeButton()
    dialog._scrollable = _make_virtual_scroll_stub(
        [
            {"action": parent, "depth": 0, "index_str": "1"},
            {"action": child, "depth": 1, "index_str": "1-1"},
            {"action": grandchild, "depth": 2, "index_str": "1-1-1"},
            {"action": sibling, "depth": 0, "index_str": "2"},
        ],
        {},
    )
    synced = []
    dialog._sync_visible_actions_from_model = lambda *args, **kwargs: synced.append((args, kwargs)) or True

    dialog._toggle_all_collapse()

    updated = dialog._scrollable.get_items()
    assert synced == []
    assert [item["action"].action_id for item in updated] == ["parent", "sibling"]
    assert [item["index_str"] for item in updated] == ["1", "2"]


def test_toggle_all_collapse_builds_roots_from_model_not_expanded_visible_list():
    text = _read_text()
    plan_method = _method_slice(
        text,
        "def _apply_visible_rule_collapse_to_roots(self) -> bool:",
        "def _apply_visible_rule_sibling_swap(self, rule_a: AutomationRule, rule_b: AutomationRule) -> bool:",
    )
    sequence_start = text.index("class SequenceDetailDialog")
    sequence_text = text[sequence_start:]
    sequence_method = _method_slice(
        sequence_text,
        "def _apply_visible_action_collapse_to_roots(self) -> bool:",
        "def _apply_visible_action_sibling_swap(self, action_a: Action, action_b: Action) -> bool:",
    )

    assert 'getattr(self._plan, "initial_rules", [])' in plan_method
    assert "for item in items" not in plan_method
    assert "self._scrollable.get_items()" not in plan_method
    assert 'getattr(self._sequence, "actions", [])' in sequence_method
    assert "for item in items" not in sequence_method
    assert "self._scrollable.get_items()" not in sequence_method


def test_plan_toggle_all_collapse_large_tree_is_top_level_only_and_fast():
    roots = []
    for parent_idx in range(1000):
        parent = AutomationRule(action_type="click", rule_id=f"parent_{parent_idx}")
        parent.children = [
            AutomationRule(
                action_type="key_press",
                rule_id=f"child_{parent_idx}_{child_idx}",
                parent_id=parent.rule_id,
            )
            for child_idx in range(20)
        ]
        roots.append(parent)

    dialog = _make_plan_dialog_stub(roots, set())
    dialog._all_collapsed = False
    dialog._collapse_btn = _FakeButton()
    dialog._sync_visible_rules_from_model = lambda *args, **kwargs: True

    started = perf_counter()
    dialog._toggle_all_collapse()
    elapsed = perf_counter() - started

    assert len(dialog._collapsed_items) == len(roots)
    assert all(rule.rule_id in dialog._collapsed_items for rule in roots)
    assert elapsed < 0.05


def test_sequence_toggle_all_collapse_large_tree_is_top_level_only_and_fast():
    roots = []
    for parent_idx in range(1000):
        parent = Action(action_type="click", action_id=f"parent_{parent_idx}")
        parent.children = [
            Action(
                action_type="key_press",
                action_id=f"child_{parent_idx}_{child_idx}",
                parent_id=parent.action_id,
            )
            for child_idx in range(20)
        ]
        roots.append(parent)

    dialog = _make_sequence_dialog_stub(Sequence(name="large", actions=roots), set())
    dialog._all_collapsed = False
    dialog._collapse_btn = _FakeButton()
    dialog._sync_visible_actions_from_model = lambda *args, **kwargs: True

    started = perf_counter()
    dialog._toggle_all_collapse()
    elapsed = perf_counter() - started

    assert len(dialog._collapsed_items) == len(roots)
    assert all(action.action_id in dialog._collapsed_items for action in roots)
    assert elapsed < 0.05
