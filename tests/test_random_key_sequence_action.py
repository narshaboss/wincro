from unittest.mock import Mock, call, patch

from src.analyzer.automation_models import AutomationRule
from src.database.models import Action, ActionType
from src.player.random_key_sequence import (
    DEFAULT_RANDOM_KEY_STEP_DELAY,
    DEFAULT_RANDOM_KEY_STEP_RANDOM_RANGE,
    clone_default_random_key_sequences,
    execute_random_key_sequence,
    format_random_key_sequences_summary,
    normalize_random_key_sequences,
)
from src.ui.constants import convert_to_monitor_action
from src.ui.monitoring_editor import MonitorActionEditorDialog, MonitoringModeEditor
from src.player.rule_executor import RuleExecutor


def test_random_key_sequence_normalization_and_summary():
    sequences = normalize_random_key_sequences([
        [["pageup"], ["pagedown"], ["left"], ["enter"]],
        [{
            "keys": ["shift", "up"],
            "key_events": [{"event": "down", "key": "shift", "delay": 0}],
            "delay_after": 0.23,
            "delay_after_random": True,
            "delay_after_random_range": 0.07,
        }],
    ])

    assert len(sequences) == 2
    assert sequences[0][0]["keys"] == ["pageup"]
    assert sequences[1][0]["delay_after"] == 0.23
    assert sequences[1][0]["delay_after_random"] is True
    assert sequences[1][0]["delay_after_random_range"] == 0.07
    assert "2개 묶음" in format_random_key_sequences_summary(sequences)


def test_default_random_key_sequences_use_per_key_random_delay_defaults():
    sequences = clone_default_random_key_sequences()

    assert sequences
    for sequence in sequences:
        for step in sequence:
            assert step["delay_after"] == DEFAULT_RANDOM_KEY_STEP_DELAY
            assert step["delay_after_random"] is True
            assert step["delay_after_random_range"] == DEFAULT_RANDOM_KEY_STEP_RANDOM_RANGE


def test_random_key_normalization_preserves_explicit_random_off():
    sequences = normalize_random_key_sequences([[
        {
            "keys": ["enter"],
            "delay_after": 0.8,
            "delay_after_random": False,
            "delay_after_random_range": 0.3,
        }
    ]])

    assert sequences[0][0]["delay_after_random"] is False
    assert sequences[0][0]["delay_after_random_range"] == 0.3


def test_action_model_persists_random_key_sequences():
    sequences = clone_default_random_key_sequences()
    action = Action(
        action_type=ActionType.RANDOM_KEY_SEQUENCE.value,
        random_key_sequences=sequences,
        random_key_step_delay=0.12,
    )

    restored = Action.from_dict(action.to_dict())

    assert restored.action_type == "random_key_sequence"
    assert restored.random_key_sequences == sequences
    assert restored.random_key_step_delay == 0.12


def test_automation_rule_persists_random_key_sequences():
    sequences = clone_default_random_key_sequences()
    rule = AutomationRule(
        action_type="random_key_sequence",
        random_key_sequences=sequences,
        random_key_step_delay=0.12,
    )

    restored = AutomationRule.from_dict(rule.to_dict())

    assert restored.action_type == "random_key_sequence"
    assert restored.random_key_sequences == sequences
    assert restored.random_key_step_delay == 0.12


def test_execute_random_key_sequence_selects_one_group_and_runs_steps():
    input_ctrl = Mock()
    input_ctrl.press.return_value = True
    input_ctrl.hotkey.return_value = True
    sequences = [
        [{"keys": ["pageup"]}, {"keys": ["pagedown"]}],
        [{"keys": ["left", "enter"]}],
    ]

    with patch("src.player.random_key_sequence.random.randrange", return_value=1):
        ok, message, selected_index, selected_label = execute_random_key_sequence(
            input_ctrl,
            sequences,
            step_delay=0,
        )

    assert ok is True
    assert selected_index == 1
    assert "LEFT+ENTER" in selected_label
    assert "랜덤키 완료" in message
    input_ctrl.hotkey.assert_called_once_with("left", "enter")
    input_ctrl.press.assert_not_called()


def test_execute_random_key_sequence_uses_step_delay_after():
    input_ctrl = Mock()
    input_ctrl.press.return_value = True
    sequences = [[
        {"keys": ["pageup"], "delay_after": 0.21},
        {"keys": ["pagedown"], "delay_after": 0.33},
        {"keys": ["enter"]},
    ]]

    with patch("src.player.random_key_sequence.random.randrange", return_value=0), \
         patch("src.player.random_key_sequence.time.sleep") as sleep:
        ok, _message, _selected_index, _selected_label = execute_random_key_sequence(
            input_ctrl,
            sequences,
            step_delay=0.08,
        )

    assert ok is True
    assert input_ctrl.press.call_args_list == [call("pageup"), call("pagedown"), call("enter")]
    assert sleep.call_args_list == [call(0.21), call(0.33)]


def test_execute_random_key_sequence_uses_random_step_delay_range():
    input_ctrl = Mock()
    input_ctrl.press.return_value = True
    sequences = [[
        {"keys": ["pageup"], "delay_after": 0.2, "delay_after_random": True, "delay_after_random_range": 0.05},
        {"keys": ["enter"]},
    ]]

    with patch("src.player.random_key_sequence.random.randrange", return_value=0), \
         patch("src.player.random_key_sequence.random.uniform", return_value=0.03), \
         patch("src.player.random_key_sequence.time.sleep") as sleep:
        ok, _message, _selected_index, _selected_label = execute_random_key_sequence(
            input_ctrl,
            sequences,
            step_delay=0.08,
        )

    assert ok is True
    assert sleep.call_args_list == [call(0.23)]


def test_random_key_action_converts_to_monitor_action():
    sequences = clone_default_random_key_sequences()
    action = Action(
        action_type=ActionType.RANDOM_KEY_SEQUENCE.value,
        random_key_sequences=sequences,
        random_key_step_delay=0.11,
    )

    monitor_action = convert_to_monitor_action(action)

    assert monitor_action["type"] == MonitorActionEditorDialog.RANDOM_KEY_TYPE
    assert monitor_action["random_key_sequences"] == sequences
    assert monitor_action["random_key_step_delay"] == 0.11
    assert MonitorActionEditorDialog.RANDOM_KEY_TYPE in MonitorActionEditorDialog.ACTION_TYPES
    assert MonitoringModeEditor._action_detail(monitor_action)


def test_monitor_random_key_action_executes_selected_sequence():
    input_ctrl = Mock()
    input_ctrl.press.return_value = True
    input_ctrl.hotkey.return_value = True
    executor = RuleExecutor()
    monitor_action = {
        "type": MonitorActionEditorDialog.RANDOM_KEY_TYPE,
        "random_key_sequences": [[{"keys": ["left", "enter"]}]],
        "random_key_step_delay": 0,
    }

    with patch("src.player.rule_executor.get_input_controller", return_value=input_ctrl):
        result = executor._execute_monitor_action(monitor_action)

    assert result
    assert "LEFT+ENTER" in result
    input_ctrl.hotkey.assert_called_once_with("left", "enter")
    input_ctrl.press.assert_not_called()
