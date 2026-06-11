from pathlib import Path

from src.analyzer.automation_models import AutomationRule
from src.database.models import Action
from src.ui.player_view import (
    _build_manual_partial_rules,
    _detach_child_after_parent,
    _find_item_path_by_id,
    _flatten_children_after_parent,
    _manual_partial_start_index,
)


def test_manual_partial_run_enters_monitor_parent_children():
    child = AutomationRule(rule_id="child", action_type="double_click", description="real action")
    parent = AutomationRule(
        rule_id="parent",
        action_type="double_click",
        description="monitor parent",
        is_monitoring_mode=True,
        monitoring_watches=[{"image": "watch.png", "goto_index": 0}],
        children=[child],
    )
    after = AutomationRule(rule_id="after", action_type="click")
    flat_rules = [parent, child, after]

    assert _manual_partial_start_index(flat_rules, parent, 0) == 1


def test_manual_partial_run_keeps_non_monitor_parent_start():
    child = AutomationRule(rule_id="child", action_type="double_click")
    parent = AutomationRule(rule_id="parent", action_type="click", children=[child])
    flat_rules = [parent, child]

    assert _manual_partial_start_index(flat_rules, parent, 0) == 0


def test_manual_partial_run_keeps_leaf_monitor_start():
    monitor = AutomationRule(
        rule_id="monitor",
        action_type="double_click",
        is_monitoring_mode=True,
        monitoring_watches=[{"image": "watch.png", "goto_index": 0}],
    )

    assert _manual_partial_start_index([monitor], monitor, 0) == 0


def test_manual_partial_run_preserves_click_until_children_without_duplicate():
    child = AutomationRule(rule_id="child", action_type="key_press", action_keys=["enter"])
    parent = AutomationRule(
        rule_id="parent",
        action_type="click",
        target_image="target.png",
        click_until_image_disappears=True,
        children=[child],
    )
    after = AutomationRule(rule_id="after", action_type="wait")
    flat_rules = [parent, child, after]

    rules_to_run = _build_manual_partial_rules(flat_rules, 0)

    assert [rule.rule_id for rule in rules_to_run] == ["parent", "after"]
    assert [rule.rule_id for rule in rules_to_run[0].children] == ["child"]


def test_manual_partial_run_still_flattens_normal_parent_children():
    child = AutomationRule(rule_id="child", action_type="key_press", action_keys=["enter"])
    parent = AutomationRule(rule_id="parent", action_type="click", children=[child])
    flat_rules = [parent, child]

    rules_to_run = _build_manual_partial_rules(flat_rules, 0)

    assert [rule.rule_id for rule in rules_to_run] == ["parent", "child"]
    assert rules_to_run[0].children == []


def test_detach_rule_places_child_immediately_after_top_level_parent():
    child = AutomationRule(rule_id="child", action_type="wait", parent_id="parent")
    parent = AutomationRule(rule_id="parent", action_type="click", children=[child])
    after = AutomationRule(rule_id="after", action_type="wait")
    rules = [parent, after]

    assert _detach_child_after_parent(rules, parent, child, "rule_id") is True

    assert [rule.rule_id for rule in rules] == ["parent", "child", "after"]
    assert parent.children == []
    assert child.parent_id is None


def test_detach_rule_places_child_after_nested_parent_at_same_level():
    child = AutomationRule(rule_id="child", action_type="wait", parent_id="parent")
    parent = AutomationRule(rule_id="parent", action_type="click", parent_id="grand", children=[child])
    sibling = AutomationRule(rule_id="sibling", action_type="wait", parent_id="grand")
    grand = AutomationRule(rule_id="grand", action_type="click", children=[parent, sibling])
    rules = [grand]

    assert _detach_child_after_parent(rules, parent, child, "rule_id") is True

    assert [rule.rule_id for rule in grand.children] == ["parent", "child", "sibling"]
    assert parent.children == []
    assert child.parent_id == "grand"


def test_flatten_action_children_places_them_after_parent():
    child1 = Action(action_type="wait", action_id="child1", parent_id="parent")
    child2 = Action(action_type="wait", action_id="child2", parent_id="parent")
    parent = Action(action_type="click", action_id="parent", children=[child1, child2])
    after = Action(action_type="wait", action_id="after")
    actions = [parent, after]

    assert _flatten_children_after_parent(actions, parent, "action_id") == 2

    assert [action.action_id for action in actions] == ["parent", "child1", "child2", "after"]
    assert parent.children == []
    assert child1.parent_id is None
    assert child2.parent_id is None


def test_find_item_path_by_id_returns_full_ancestor_path():
    child = AutomationRule(rule_id="child", action_type="wait", parent_id="parent")
    parent = AutomationRule(rule_id="parent", action_type="click", parent_id="grand", children=[child])
    grand = AutomationRule(rule_id="grand", action_type="click", children=[parent])

    path = _find_item_path_by_id([grand], "child", "rule_id")

    assert [rule.rule_id for rule in path] == ["grand", "parent", "child"]


def test_partial_run_stop_updates_running_badge_without_row_rebuild():
    source = Path("src/ui/player_view.py").read_text(encoding="utf-8", errors="ignore")
    start = source.index("def _clear_current_partial_rule")
    end = source.index("def _make_partial_progress_callback", start)
    body = source[start:end]

    assert "self._active_partial_rule_id = None" in body
    assert "self._update_rule_row_in_place(previous_rule)" in body
    assert "self._refresh_rule_row(previous_rule_id)" not in body
    assert "self._schedule_action_list_refresh()" not in body


def test_partial_run_progress_callback_ignores_late_updates_after_stop():
    source = Path("src/ui/player_view.py").read_text(encoding="utf-8", errors="ignore")
    start = source.index("def _make_partial_progress_callback")
    end = source.index("def _start_partial_run_visual", start)
    body = source[start:end]

    assert "callback_generation" in body
    assert "_partial_run_generation" in body
    assert "not getattr(self, \"_is_running\", False)" in body
