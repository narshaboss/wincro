from types import SimpleNamespace
from unittest.mock import Mock, patch
import importlib

from src.analyzer.automation_models import AutomationRule


RULE_EXECUTOR_MODULE = importlib.import_module("src.player.rule_executor")


def _make_executor():
    cfg = SimpleNamespace(
        player=SimpleNamespace(
            default_wait_ms=800,
            mouse_move_duration=0.4,
            typing_interval=0.1,
        )
    )
    with patch.object(RULE_EXECUTOR_MODULE, "get_config", return_value=cfg):
        return RULE_EXECUTOR_MODULE.RuleExecutor()


def test_rule_executor_key_press_failure_is_reported():
    executor = _make_executor()
    rule = AutomationRule(action_type="key_press", action_keys=["enter"])
    input_ctrl = SimpleNamespace(press=Mock(return_value=False), hotkey=Mock(return_value=True))

    with patch.object(RULE_EXECUTOR_MODULE, "get_input_controller", return_value=input_ctrl):
        result = executor._execute_rule(rule, step_num="1")

    assert result.success is False
    assert result.message == "키 입력 전송 실패"
    input_ctrl.press.assert_called_once_with("enter")
    input_ctrl.hotkey.assert_not_called()


def test_rule_executor_key_press_repeat_attempts_each_enter():
    executor = _make_executor()
    rule = AutomationRule(action_type="key_press", action_keys=["enter"], repeat_count=4, repeat_delay=0)
    input_ctrl = SimpleNamespace(press=Mock(return_value=True), hotkey=Mock(return_value=True))

    with patch.object(RULE_EXECUTOR_MODULE, "get_input_controller", return_value=input_ctrl):
        result = executor._execute_rule_with_retry(rule, step_num="1")

    assert result.success is True
    assert input_ctrl.press.call_count == 4
    input_ctrl.hotkey.assert_not_called()
