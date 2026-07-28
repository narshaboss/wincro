from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from src.analyzer.automation_models import AutomationRule
from src.database.models import Action
from src.player.rule_executor import RuleExecutor
from src.ui.player_view import PlayerView, _action_call_options


def test_action_call_fields_survive_rule_and_action_reload(tmp_path):
    rule = AutomationRule(
        rule_id="call_rule",
        action_type="action_call",
        action_call_rule_id="target_rule",
        action_call_include_children=False,
    )
    restored_rule = AutomationRule.from_dict(rule.to_dict(), templates_dir=tmp_path)

    action = Action(
        action_type="action_call",
        action_id="call_action",
        action_call_rule_id="target_action",
        action_call_include_children=False,
    )
    restored_action = Action.from_dict(action.to_dict())

    assert restored_rule.action_call_rule_id == "target_rule"
    assert restored_rule.action_call_include_children is False
    assert restored_action.action_call_rule_id == "target_action"
    assert restored_action.action_call_include_children is False


def test_action_call_executes_target_setup_children_and_prunes_caller_branch(monkeypatch):
    caller = AutomationRule(
        rule_id="call_return_to_craft",
        action_type="action_call",
        action_call_rule_id="craft_start",
        action_call_include_children=True,
        wait_after=0.0,
    )
    setup_one = AutomationRule(
        rule_id="craft_menu",
        action_type="click",
        description="장비 제작 아이콘",
        wait_after=0.0,
    )
    setup_two = AutomationRule(
        rule_id="craft_available",
        action_type="click",
        description="제작 가능",
        wait_after=0.0,
    )
    auto_list = AutomationRule(
        rule_id="craft_auto_list",
        action_type="auto_list",
        description="자동 목록 처리",
        children=[caller],
        wait_after=0.0,
    )
    target = AutomationRule(
        rule_id="craft_start",
        action_type="click",
        description="장비제작 시작",
        children=[setup_one, setup_two, auto_list],
        wait_after=0.0,
    )
    executor = RuleExecutor()
    executor._current_plan = SimpleNamespace(
        _original_initial_rules=[target],
        initial_rules=[caller],
        monitoring_rules=[],
    )
    executor._current_step_num = "3-3-10"
    executor._auto_list_current_value = 10
    executor._auto_list_current_item = {"name": "환웅천검 1각"}
    executed = []

    def execute_once(active_rule, *args, **kwargs):
        executed.append(active_rule.rule_id)
        return executor._make_result(active_rule, True, "완료", datetime.now())

    monkeypatch.setattr(executor, "_execute_rule_with_retry", execute_once)

    result = executor._execute_action_call(caller, datetime.now())

    assert result.success is True
    assert executed == ["craft_start", "craft_menu", "craft_available"]
    assert auto_list in target.children
    assert caller in auto_list.children
    assert executor._action_call_stack == []
    assert executor._auto_list_current_value == 10
    assert executor._auto_list_current_item == {"name": "환웅천검 1각"}
    assert "호출 후 복귀 완료" in result.message


def test_action_call_can_exclude_all_target_children(monkeypatch):
    child = AutomationRule(rule_id="child", action_type="click", wait_after=0.0)
    target = AutomationRule(
        rule_id="target",
        action_type="click",
        children=[child],
        wait_after=0.0,
    )
    caller = AutomationRule(
        rule_id="caller",
        action_type="action_call",
        action_call_rule_id="target",
        action_call_include_children=False,
        wait_after=0.0,
    )
    executor = RuleExecutor()
    executor._current_plan = SimpleNamespace(
        _original_initial_rules=[target, caller],
        initial_rules=[caller],
        monitoring_rules=[],
    )
    executed = []

    def execute_once(active_rule, *args, **kwargs):
        executed.append(active_rule.rule_id)
        return executor._make_result(active_rule, True, "완료", datetime.now())

    monkeypatch.setattr(executor, "_execute_rule_with_retry", execute_once)

    result = executor._execute_action_call(caller, datetime.now())

    assert result.success is True
    assert executed == ["target"]


def test_action_call_rejects_missing_self_auto_list_and_cycles():
    executor = RuleExecutor()
    auto_list = AutomationRule(rule_id="auto", action_type="auto_list")
    caller = AutomationRule(
        rule_id="caller",
        action_type="action_call",
        action_call_rule_id="missing",
    )
    executor._current_plan = SimpleNamespace(
        _original_initial_rules=[auto_list, caller],
        initial_rules=[caller],
        monitoring_rules=[],
    )

    missing = executor._execute_action_call(caller, datetime.now())
    caller.action_call_rule_id = caller.rule_id
    self_call = executor._execute_action_call(caller, datetime.now())
    caller.action_call_rule_id = auto_list.rule_id
    auto_call = executor._execute_action_call(caller, datetime.now())
    executor._action_call_stack = ["auto"]
    cycle = executor._execute_action_call(caller, datetime.now())

    assert missing.success is False and "찾을 수 없습니다" in missing.message
    assert self_call.success is False and "자기 자신" in self_call.message
    assert auto_call.success is False and "자동 목록" in auto_call.message
    assert cycle.success is False and "순환" in cycle.message


def test_action_call_choice_list_uses_full_numbers_and_blocks_recursive_targets():
    caller_child = Action(action_type="click", action_id="caller_child", description="호출자 하위")
    caller = Action(
        action_type="action_call",
        action_id="caller",
        description="액션 호출",
        children=[caller_child],
    )
    auto_list = Action(
        action_type="auto_list",
        action_id="auto",
        description="자동 목록",
        children=[caller],
    )
    target = Action(
        action_type="click",
        action_id="target",
        description="장비제작 시작",
        children=[Action(action_type="click", action_id="setup", description="제작 가능"), auto_list],
    )

    options = _action_call_options([target], "action_id", caller_id="caller")
    labels_by_id = {item_id: label for label, item_id in options}

    assert labels_by_id["target"] == "[1] 장비제작 시작"
    assert labels_by_id["setup"] == "[1-1] 제작 가능"
    assert "auto" not in labels_by_id
    assert "caller" not in labels_by_id
    assert "caller_child" not in labels_by_id


def test_sequence_playback_conversion_preserves_action_call_target():
    target = Action(action_type="click", action_id="target", description="장비제작 시작")
    caller = Action(
        action_type="action_call",
        action_id="caller",
        description="액션 호출",
        action_call_rule_id=target.action_id,
        action_call_include_children=False,
    )
    player_view = object.__new__(PlayerView)

    converted = player_view._sequence_action_to_playback_rule(caller)

    assert converted is not None
    assert converted.action_type == "action_call"
    assert converted.action_call_rule_id == "target"
    assert converted.action_call_include_children is False


def test_editors_expose_action_call_button_instead_of_value_input_button():
    source = Path("src/ui/player_view.py").read_text(encoding="utf-8")

    assert source.count("command=self._add_action_call_action") == 2
    assert "command=self._add_auto_list_value_input_action" not in source
