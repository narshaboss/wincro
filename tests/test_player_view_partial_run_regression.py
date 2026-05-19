from src.analyzer.automation_models import AutomationRule
from src.ui.player_view import _manual_partial_start_index


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
