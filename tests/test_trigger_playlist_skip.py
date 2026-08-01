from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch
import importlib
import json

import pytest

from src.analyzer.automation_models import AutomationPlan, AutomationRule


RULE_EXECUTOR_MODULE = importlib.import_module("src.player.rule_executor")
PLAYER_VIEW = Path(r"C:\Projects\wincro\src\ui\player_view.py")
MAIN_WINDOW = Path(r"C:\Projects\wincro\src\ui\main_window.py")
RULE_EXECUTOR = Path(r"C:\Projects\wincro\src\player\rule_executor.py")


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


def test_trigger_playlist_skip_round_trip():
    rule = AutomationRule(
        action_type="click",
        trigger_image="trigger_sample.png",
        trigger_search_region=[10, 20, 310, 420],
        stop_playlist_on_trigger_missing=True,
        trigger_missing_keys=["esc"],
        trigger_missing_key_sequences=[["esc"], ["enter"]],
        trigger_missing_key_sequence_settings=[
            {
                "repeat_count": 2,
                "repeat_delay": 0.2,
                "repeat_delay_random": False,
                "repeat_delay_random_range": 0.1,
            },
            {
                "repeat_count": 5,
                "repeat_delay": 0.8,
                "repeat_delay_random": True,
                "repeat_delay_random_range": 0.25,
            },
        ],
        trigger_missing_key_repeat_count=3,
        trigger_missing_key_repeat_delay=0.25,
        trigger_missing_key_repeat_delay_random=True,
        trigger_missing_key_repeat_delay_random_range=0.1,
        rewind_previous_on_trigger_missing=True,
        trigger_missing_rewind_count=2,
        trigger_missing_rewind_delay=0.4,
        trigger_missing_rewind_delay_random=True,
        trigger_missing_rewind_delay_random_range=0.2,
        trigger_missing_rewind_keys=["enter"],
        trigger_missing_rewind_key_sequences=[["enter"], ["shift", "up"]],
        trigger_missing_rewind_key_sequence_settings=[
            {
                "repeat_count": 3,
                "repeat_delay": 0.15,
                "repeat_delay_random": True,
                "repeat_delay_random_range": 0.05,
            },
            {
                "repeat_count": 1,
                "repeat_delay": 0.6,
                "repeat_delay_random": False,
                "repeat_delay_random_range": 0.2,
            },
        ],
        trigger_missing_rewind_key_repeat_count=2,
        trigger_missing_rewind_key_repeat_delay=0.15,
        trigger_missing_rewind_key_repeat_delay_random=True,
        trigger_missing_rewind_key_repeat_delay_random_range=0.05,
        trigger_missing_rewind_rule_id="rule_target",
    )

    payload = rule.to_dict()

    assert payload["stop_playlist_on_trigger_missing"] is True
    assert payload["trigger_search_region"] == [10, 20, 310, 420]
    assert payload["trigger_missing_keys"] == ["esc"]
    assert payload["trigger_missing_key_sequences"] == [["esc"], ["enter"]]
    assert payload["trigger_missing_key_sequence_settings"] == [
        {
            "repeat_count": 2,
            "repeat_delay": 0.2,
            "repeat_delay_random": False,
            "repeat_delay_random_range": 0.1,
        },
        {
            "repeat_count": 5,
            "repeat_delay": 0.8,
            "repeat_delay_random": True,
            "repeat_delay_random_range": 0.25,
        },
    ]
    assert payload["trigger_missing_key_repeat_count"] == 3
    assert payload["trigger_missing_key_repeat_delay"] == 0.25
    assert payload["trigger_missing_key_repeat_delay_random"] is True
    assert payload["trigger_missing_key_repeat_delay_random_range"] == 0.1
    assert payload["rewind_previous_on_trigger_missing"] is True
    assert payload["trigger_missing_rewind_count"] == 2
    assert payload["trigger_missing_rewind_delay"] == 0.4
    assert payload["trigger_missing_rewind_delay_random"] is True
    assert payload["trigger_missing_rewind_delay_random_range"] == 0.2
    assert payload["trigger_missing_rewind_keys"] == ["enter"]
    assert payload["trigger_missing_rewind_key_sequences"] == [["enter"], ["shift", "up"]]
    assert payload["trigger_missing_rewind_key_sequence_settings"][0]["repeat_count"] == 3
    assert payload["trigger_missing_rewind_key_sequence_settings"][1]["repeat_delay"] == 0.6
    assert payload["trigger_missing_rewind_key_repeat_count"] == 2
    assert payload["trigger_missing_rewind_key_repeat_delay"] == 0.15
    assert payload["trigger_missing_rewind_key_repeat_delay_random"] is True
    assert payload["trigger_missing_rewind_key_repeat_delay_random_range"] == 0.05
    assert payload["trigger_missing_rewind_rule_id"] == "rule_target"
    restored = AutomationRule.from_dict(payload)
    assert restored.stop_playlist_on_trigger_missing is True
    assert restored.trigger_search_region == [10, 20, 310, 420]
    assert restored.trigger_missing_keys == ["esc"]
    assert restored.trigger_missing_key_sequences == [["esc"], ["enter"]]
    assert restored.trigger_missing_key_sequence_settings == payload["trigger_missing_key_sequence_settings"]
    assert restored.trigger_missing_key_repeat_count == 3
    assert restored.trigger_missing_key_repeat_delay == 0.25
    assert restored.trigger_missing_key_repeat_delay_random is True
    assert restored.trigger_missing_key_repeat_delay_random_range == 0.1
    assert restored.rewind_previous_on_trigger_missing is True
    assert restored.trigger_missing_rewind_count == 2
    assert restored.trigger_missing_rewind_delay == 0.4
    assert restored.trigger_missing_rewind_delay_random is True
    assert restored.trigger_missing_rewind_delay_random_range == 0.2
    assert restored.trigger_missing_rewind_keys == ["enter"]
    assert restored.trigger_missing_rewind_key_sequences == [["enter"], ["shift", "up"]]
    assert (
        restored.trigger_missing_rewind_key_sequence_settings
        == payload["trigger_missing_rewind_key_sequence_settings"]
    )
    assert restored.trigger_missing_rewind_key_repeat_count == 2
    assert restored.trigger_missing_rewind_key_repeat_delay == 0.15
    assert restored.trigger_missing_rewind_key_repeat_delay_random is True
    assert restored.trigger_missing_rewind_key_repeat_delay_random_range == 0.05
    assert restored.trigger_missing_rewind_rule_id == "rule_target"


def test_legacy_trigger_missing_keys_migrate_to_single_sequence():
    restored = AutomationRule.from_dict({
        "action_type": "click",
        "trigger_missing_keys": ["esc"],
        "trigger_missing_rewind_keys": ["shift", "up"],
    })

    assert restored.trigger_missing_key_sequences == [["esc"]]
    assert restored.trigger_missing_rewind_key_sequences == [["shift", "up"]]


def test_incomplete_per_key_trigger_settings_fall_back_to_legacy_globals():
    restored = AutomationRule.from_dict({
        "action_type": "click",
        "trigger_missing_rewind_key_sequences": [["esc"], ["enter"]],
        "trigger_missing_rewind_key_sequence_settings": [
            {"repeat_count": 7, "repeat_delay": 0.1}
        ],
        "trigger_missing_rewind_key_repeat_count": 2,
        "trigger_missing_rewind_key_repeat_delay": 0.4,
    })

    assert restored.trigger_missing_rewind_key_sequence_settings == []
    assert restored.trigger_missing_rewind_key_repeat_count == 2
    assert restored.trigger_missing_rewind_key_repeat_delay == 0.4


def test_trigger_search_region_is_normalized_and_round_trips():
    restored = AutomationRule.from_dict({
        "action_type": "click",
        "trigger_search_region": [310, 420, 10, 20],
    })

    assert restored.trigger_search_region == [10, 20, 310, 420]
    assert restored.to_dict()["trigger_search_region"] == [10, 20, 310, 420]


def test_explicit_trigger_region_takes_priority_over_legacy_coordinates():
    executor = _make_executor()
    rule = AutomationRule(
        action_type="click",
        trigger_x=500,
        trigger_y=600,
        trigger_search_region=[10, 20, 310, 420],
    )

    assert executor._trigger_search_region_for_rule(rule) == [10, 20, 310, 420]


def test_legacy_trigger_coordinates_still_use_compatible_radius():
    executor = _make_executor()
    rule = AutomationRule(action_type="click", trigger_x=500, trigger_y=600)

    assert executor._trigger_search_region_for_rule(rule) == [280, 380, 720, 820]


def test_trigger_rewind_target_survives_plan_json_disk_round_trip(tmp_path):
    target = AutomationRule(rule_id="target", action_type="hotkey")
    source = AutomationRule(
        rule_id="source",
        action_type="click",
        rewind_previous_on_trigger_missing=True,
        trigger_missing_rewind_rule_id="target",
        trigger_missing_rewind_key_sequences=[["esc"], ["enter"]],
        trigger_missing_rewind_key_sequence_settings=[
            {"repeat_count": 2, "repeat_delay": 0.1},
            {"repeat_count": 4, "repeat_delay": 0.7},
        ],
        trigger_search_region=[12, 34, 456, 678],
    )
    plan = AutomationPlan(name="disk roundtrip", initial_rules=[target, source])
    plan_path = tmp_path / "plan.json"

    plan_path.write_text(
        json.dumps(plan.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    restored = AutomationPlan.from_dict(
        json.loads(plan_path.read_text(encoding="utf-8"))
    )

    restored_source = restored.initial_rules[1]
    assert restored_source.rewind_previous_on_trigger_missing is True
    assert restored_source.trigger_missing_rewind_rule_id == "target"
    assert restored_source.trigger_missing_rewind_key_sequences == [["esc"], ["enter"]]
    assert restored_source.trigger_missing_rewind_key_sequence_settings == [
        {
            "repeat_count": 2,
            "repeat_delay": 0.1,
            "repeat_delay_random": False,
            "repeat_delay_random_range": 0.3,
        },
        {
            "repeat_count": 4,
            "repeat_delay": 0.7,
            "repeat_delay_random": False,
            "repeat_delay_random_range": 0.3,
        },
    ]
    assert restored_source.trigger_search_region == [12, 34, 456, 678]


def test_trigger_missing_returns_playlist_skip_result(tmp_path):
    trigger = tmp_path / "trigger.png"
    trigger.write_bytes(b"not a real png but path exists")
    executor = _make_executor()
    rule = AutomationRule(
        action_type="click",
        trigger_image=str(trigger),
        wait_after=0,
        stop_playlist_on_trigger_missing=True,
    )

    with patch.object(RULE_EXECUTOR_MODULE.time, "sleep", return_value=None), patch.object(
        executor, "_wait_for_trigger", return_value=None
    ) as wait_for_trigger:
        result = executor._execute_rule_with_retry(rule, step_num="1")

    assert result.success is True
    assert result.skip_current_playlist is True
    assert result.message.startswith(RULE_EXECUTOR_MODULE.PLAYLIST_SKIP_TRIGGER_MISSING)
    assert wait_for_trigger.call_args.kwargs["timeout"] == pytest.approx(
        RULE_EXECUTOR_MODULE.PLAYLIST_SKIP_TRIGGER_TIMEOUT_SECONDS
    )


def test_trigger_wait_receives_explicit_trigger_search_region(tmp_path):
    trigger = tmp_path / "trigger.png"
    trigger.write_bytes(b"not a real png but path exists")
    executor = _make_executor()
    rule = AutomationRule(
        action_type="click",
        trigger_image=str(trigger),
        trigger_search_region=[11, 22, 333, 444],
        wait_after=0,
        stop_playlist_on_trigger_missing=True,
    )

    with patch.object(
        executor,
        "_wait_for_trigger",
        return_value=None,
    ) as wait_for_trigger:
        result = executor._execute_rule_with_retry(rule, step_num="1")

    assert result.success is True
    assert wait_for_trigger.call_args.kwargs["search_region"] == [11, 22, 333, 444]


def test_playlist_skip_runs_multiple_exit_keys_in_saved_order(tmp_path):
    trigger = tmp_path / "trigger.png"
    trigger.write_bytes(b"not a real png but path exists")
    executor = _make_executor()
    rule = AutomationRule(
        action_type="click",
        trigger_image=str(trigger),
        stop_playlist_on_trigger_missing=True,
        trigger_missing_key_sequences=[["esc"], ["enter"], ["ctrl", "a"]],
        trigger_missing_key_repeat_count=1,
        trigger_missing_key_repeat_delay=0,
    )
    input_ctrl = Mock()
    input_ctrl.press.return_value = True
    input_ctrl.hotkey.return_value = True

    with patch.object(RULE_EXECUTOR_MODULE, "get_input_controller", return_value=input_ctrl), patch.object(
        executor, "_wait_for_trigger", return_value=None
    ):
        result = executor._execute_rule_with_retry(rule, step_num="3")

    assert result.success is True
    assert result.skip_current_playlist is True
    assert input_ctrl.method_calls == [
        call.release_all(),
        call.press("esc"),
        call.press("enter"),
        call.hotkey("ctrl", "a"),
    ]


def test_trigger_missing_runs_configured_key_before_playlist_skip(tmp_path):
    trigger = tmp_path / "trigger.png"
    trigger.write_bytes(b"not a real png but path exists")
    executor = _make_executor()
    rule = AutomationRule(
        action_type="click",
        trigger_image=str(trigger),
        wait_after=0,
        stop_playlist_on_trigger_missing=True,
        trigger_missing_keys=["esc"],
        trigger_missing_key_repeat_count=3,
        trigger_missing_key_repeat_delay=0,
    )
    input_ctrl = SimpleNamespace(press=Mock(return_value=True), hotkey=Mock(return_value=True))

    with patch.object(RULE_EXECUTOR_MODULE, "get_input_controller", return_value=input_ctrl), patch.object(
        RULE_EXECUTOR_MODULE.time, "sleep", return_value=None
    ), patch.object(executor, "_wait_for_trigger", return_value=None):
        result = executor._execute_rule_with_retry(rule, step_num="1")

    assert result.success is True
    assert result.skip_current_playlist is True
    assert input_ctrl.press.call_count == 3
    input_ctrl.press.assert_called_with("esc")
    input_ctrl.hotkey.assert_not_called()


def test_trigger_missing_key_repeat_delay_uses_random_range_before_playlist_skip(tmp_path):
    trigger = tmp_path / "trigger.png"
    trigger.write_bytes(b"not a real png but path exists")
    executor = _make_executor()
    rule = AutomationRule(
        action_type="click",
        trigger_image=str(trigger),
        wait_after=0,
        stop_playlist_on_trigger_missing=True,
        trigger_missing_keys=["esc"],
        trigger_missing_key_repeat_count=2,
        trigger_missing_key_repeat_delay=0.2,
        trigger_missing_key_repeat_delay_random=True,
        trigger_missing_key_repeat_delay_random_range=0.1,
    )
    input_ctrl = SimpleNamespace(press=Mock(return_value=True), hotkey=Mock(return_value=True))

    with patch.object(RULE_EXECUTOR_MODULE, "get_input_controller", return_value=input_ctrl), patch.object(
        RULE_EXECUTOR_MODULE.random, "uniform", return_value=0.05
    ), patch.object(executor._stop_event, "wait", return_value=False) as wait, patch.object(
        executor, "_wait_for_trigger", return_value=None
    ):
        result = executor._execute_rule_with_retry(rule, step_num="1")

    assert result.success is True
    assert result.skip_current_playlist is True
    assert input_ctrl.press.call_count == 2
    wait.assert_called_once_with(pytest.approx(0.25))


def test_trigger_missing_runs_configured_hotkey_before_playlist_skip(tmp_path):
    trigger = tmp_path / "trigger.png"
    trigger.write_bytes(b"not a real png but path exists")
    executor = _make_executor()
    rule = AutomationRule(
        action_type="click",
        trigger_image=str(trigger),
        wait_after=0,
        stop_playlist_on_trigger_missing=True,
        trigger_missing_keys=["shift", "up"],
    )
    input_ctrl = SimpleNamespace(press=Mock(return_value=True), hotkey=Mock(return_value=True))

    with patch.object(RULE_EXECUTOR_MODULE, "get_input_controller", return_value=input_ctrl), patch.object(
        RULE_EXECUTOR_MODULE.time, "sleep", return_value=None
    ), patch.object(executor, "_wait_for_trigger", return_value=None):
        result = executor._execute_rule_with_retry(rule, step_num="1")

    assert result.success is True
    assert result.skip_current_playlist is True
    input_ctrl.press.assert_not_called()
    input_ctrl.hotkey.assert_called_once_with("shift", "up")


def test_trigger_missing_key_failure_stops_before_playlist_skip(tmp_path):
    trigger = tmp_path / "trigger.png"
    trigger.write_bytes(b"not a real png but path exists")
    executor = _make_executor()
    rule = AutomationRule(
        action_type="click",
        trigger_image=str(trigger),
        wait_after=0,
        stop_playlist_on_trigger_missing=True,
        trigger_missing_keys=["esc"],
    )
    input_ctrl = SimpleNamespace(press=Mock(return_value=False), hotkey=Mock(return_value=True))

    with patch.object(RULE_EXECUTOR_MODULE, "get_input_controller", return_value=input_ctrl), patch.object(
        executor, "_wait_for_trigger", return_value=None
    ):
        result = executor._execute_rule_with_retry(rule, step_num="1")

    assert result.success is False
    assert result.skip_current_playlist is False
    assert "키입력 실패" in result.message
    input_ctrl.press.assert_called_once_with("esc")


def test_trigger_missing_can_request_previous_action_rewind(tmp_path):
    trigger = tmp_path / "trigger.png"
    trigger.write_bytes(b"not a real png but path exists")
    executor = _make_executor()
    rule = AutomationRule(
        action_type="click",
        trigger_image=str(trigger),
        rewind_previous_on_trigger_missing=True,
        trigger_missing_rewind_count=2,
        trigger_missing_rewind_delay=0.4,
        trigger_missing_rewind_delay_random=True,
        trigger_missing_rewind_delay_random_range=0.1,
    )

    with patch.object(RULE_EXECUTOR_MODULE.random, "uniform", return_value=0.05), patch.object(
        executor, "_wait_for_trigger", return_value=None
    ) as wait_for_trigger:
        result = executor._execute_rule_with_retry(rule, step_num="2", can_rewind_previous=True)

    assert result.success is True
    assert result.rewind_previous_action is True
    assert result.skip_current_playlist is False
    assert result.rewind_delay == pytest.approx(0.45)
    assert "이전 액션" in result.message
    assert wait_for_trigger.call_args.kwargs["timeout"] == pytest.approx(
        RULE_EXECUTOR_MODULE.PLAYLIST_SKIP_TRIGGER_TIMEOUT_SECONDS
    )


def test_trigger_missing_can_request_configured_action_rewind(tmp_path):
    trigger = tmp_path / "trigger.png"
    trigger.write_bytes(b"not a real png but path exists")
    executor = _make_executor()
    rule = AutomationRule(
        rule_id="source",
        action_type="click",
        trigger_image=str(trigger),
        rewind_previous_on_trigger_missing=True,
        trigger_missing_rewind_rule_id="target",
        trigger_missing_rewind_count=1,
        trigger_missing_rewind_delay=0,
    )

    with patch.object(executor, "_wait_for_trigger", return_value=None):
        result = executor._execute_rule_with_retry(
            rule,
            step_num="4",
            can_rewind_previous=False,
        )

    assert result.success is True
    assert result.rewind_previous_action is True
    assert result.rewind_target_rule_id == "target"
    assert "지정 액션" in result.message


def test_configured_trigger_rewind_jumps_by_rule_id_in_full_plan(monkeypatch):
    executor = _make_executor()
    target = AutomationRule(rule_id="target", action_type="hotkey", description="target", wait_after=0)
    middle = AutomationRule(rule_id="middle", action_type="hotkey", description="middle", wait_after=0)
    source = AutomationRule(rule_id="source", action_type="click", description="source", wait_after=0)
    after = AutomationRule(rule_id="after", action_type="hotkey", description="after", wait_after=0)
    plan = AutomationPlan(name="rewind", initial_rules=[target, middle, source, after])
    executor._current_plan = plan
    executor._state = RULE_EXECUTOR_MODULE.ExecutionState.RUNNING_INITIAL
    executor._progress = RULE_EXECUTOR_MODULE.ExecutionProgress(
        state=RULE_EXECUTOR_MODULE.ExecutionState.RUNNING_INITIAL
    )
    executor._pause_event.set()
    executor._stop_event.clear()
    executed = []
    source_runs = 0

    def fake_execute(rule, *args, **kwargs):
        nonlocal source_runs
        executed.append(rule.rule_id)
        if rule.rule_id == "source":
            source_runs += 1
            if source_runs == 1:
                return RULE_EXECUTOR_MODULE.RuleExecutionResult(
                    rule_id=rule.rule_id,
                    success=True,
                    message="rewind",
                    rewind_previous_action=True,
                    rewind_target_rule_id="target",
                )
        return RULE_EXECUTOR_MODULE.RuleExecutionResult(rule_id=rule.rule_id, success=True, message="ok")

    monkeypatch.setattr(executor, "_execute_rule_with_retry", fake_execute)
    monkeypatch.setattr(executor, "_wait_for_resume", lambda: False)
    monkeypatch.setattr(executor, "_update_progress", lambda *args, **kwargs: None)

    executor._execution_loop()

    assert executed == ["target", "middle", "source", "target", "middle", "source", "after"]
    assert executor.state is RULE_EXECUTOR_MODULE.ExecutionState.COMPLETED


def test_configured_trigger_rewind_from_partial_run_restores_original_plan(monkeypatch):
    executor = _make_executor()
    target = AutomationRule(rule_id="target", action_type="hotkey", description="target", wait_after=0)
    middle = AutomationRule(rule_id="middle", action_type="hotkey", description="middle", wait_after=0)
    source = AutomationRule(rule_id="source", action_type="click", description="source", wait_after=0)
    after = AutomationRule(rule_id="after", action_type="hotkey", description="after", wait_after=0)
    plan = AutomationPlan(name="partial rewind", initial_rules=[source, after])
    plan._original_initial_rules = [target, middle, source, after]
    executor._current_plan = plan
    executor._state = RULE_EXECUTOR_MODULE.ExecutionState.RUNNING_INITIAL
    executor._progress = RULE_EXECUTOR_MODULE.ExecutionProgress(
        state=RULE_EXECUTOR_MODULE.ExecutionState.RUNNING_INITIAL
    )
    executor._pause_event.set()
    executor._stop_event.clear()
    executed = []
    source_runs = 0

    def fake_execute(rule, *args, **kwargs):
        nonlocal source_runs
        executed.append(rule.rule_id)
        if rule.rule_id == "source":
            source_runs += 1
            if source_runs == 1:
                return RULE_EXECUTOR_MODULE.RuleExecutionResult(
                    rule_id=rule.rule_id,
                    success=True,
                    message="rewind",
                    rewind_previous_action=True,
                    rewind_target_rule_id="target",
                )
        return RULE_EXECUTOR_MODULE.RuleExecutionResult(rule_id=rule.rule_id, success=True, message="ok")

    monkeypatch.setattr(executor, "_execute_rule_with_retry", fake_execute)
    monkeypatch.setattr(executor, "_wait_for_resume", lambda: False)
    monkeypatch.setattr(executor, "_update_progress", lambda *args, **kwargs: None)

    executor._execution_loop()

    assert executed == ["source", "target", "middle", "source", "after"]
    assert executor.state is RULE_EXECUTOR_MODULE.ExecutionState.COMPLETED


def test_configured_trigger_rewind_rejects_forward_target(monkeypatch):
    executor = _make_executor()
    source = AutomationRule(rule_id="source", action_type="click", description="source", wait_after=0)
    after = AutomationRule(rule_id="after", action_type="hotkey", description="after", wait_after=0)
    plan = AutomationPlan(name="invalid rewind", initial_rules=[source, after])
    executor._current_plan = plan
    executor._state = RULE_EXECUTOR_MODULE.ExecutionState.RUNNING_INITIAL
    executor._progress = RULE_EXECUTOR_MODULE.ExecutionProgress(
        state=RULE_EXECUTOR_MODULE.ExecutionState.RUNNING_INITIAL
    )
    executor._pause_event.set()
    executor._stop_event.clear()
    executed = []
    completed = []

    def fake_execute(rule, *args, **kwargs):
        executed.append(rule.rule_id)
        return RULE_EXECUTOR_MODULE.RuleExecutionResult(
            rule_id=rule.rule_id,
            success=True,
            message="rewind",
            rewind_previous_action=True,
            rewind_target_rule_id="after",
        )

    monkeypatch.setattr(executor, "_execute_rule_with_retry", fake_execute)
    monkeypatch.setattr(executor, "_wait_for_resume", lambda: False)
    monkeypatch.setattr(executor, "_update_progress", lambda *args, **kwargs: None)
    executor.set_callbacks(on_complete=lambda success, message: completed.append((success, message)))

    executor._execution_loop()

    assert executed == ["source"]
    assert executor.state is RULE_EXECUTOR_MODULE.ExecutionState.FAILED
    assert completed and completed[-1][0] is False
    assert "현재 액션보다 앞에 있지 않습니다" in completed[-1][1]


def test_trigger_missing_runs_configured_hotkey_before_previous_action_rewind(tmp_path):
    trigger = tmp_path / "trigger.png"
    trigger.write_bytes(b"not a real png but path exists")
    executor = _make_executor()
    rule = AutomationRule(
        action_type="click",
        trigger_image=str(trigger),
        rewind_previous_on_trigger_missing=True,
        trigger_missing_rewind_count=1,
        trigger_missing_rewind_delay=0,
        trigger_missing_rewind_keys=["shift", "up"],
        trigger_missing_rewind_key_repeat_count=2,
        trigger_missing_rewind_key_repeat_delay=0,
    )
    input_ctrl = SimpleNamespace(press=Mock(return_value=True), hotkey=Mock(return_value=True))

    with patch.object(RULE_EXECUTOR_MODULE, "get_input_controller", return_value=input_ctrl), patch.object(
        RULE_EXECUTOR_MODULE.time, "sleep", return_value=None
    ), patch.object(executor, "_wait_for_trigger", return_value=None):
        result = executor._execute_rule_with_retry(rule, step_num="2", can_rewind_previous=True)

    assert result.success is True
    assert result.rewind_previous_action is True
    input_ctrl.press.assert_not_called()
    assert input_ctrl.hotkey.call_count == 2
    input_ctrl.hotkey.assert_called_with("shift", "up")


def test_trigger_missing_restores_target_window_and_clears_pressed_keys_before_rewind_input(
    tmp_path,
):
    trigger = tmp_path / "trigger.png"
    trigger.write_bytes(b"not a real png but path exists")
    executor = _make_executor()
    rule = AutomationRule(
        action_type="click",
        trigger_image=str(trigger),
        rewind_previous_on_trigger_missing=True,
        trigger_missing_rewind_count=1,
        trigger_missing_rewind_delay=0,
        trigger_missing_rewind_key_sequences=[["esc"]],
    )
    input_ctrl = Mock()
    input_ctrl.press.return_value = True

    with patch.object(
        RULE_EXECUTOR_MODULE,
        "get_input_controller",
        return_value=input_ctrl,
    ), patch.object(
        executor,
        "_capture_trigger_input_window",
        return_value=4455,
    ), patch.object(
        executor,
        "_restore_trigger_input_window",
        return_value=True,
    ) as restore_window, patch.object(
        executor,
        "_wait_for_trigger",
        return_value=None,
    ):
        result = executor._execute_rule_with_retry(
            rule,
            step_num="2",
            can_rewind_previous=True,
        )

    assert result.success is True
    assert result.rewind_previous_action is True
    restore_window.assert_called_once_with(4455)
    assert input_ctrl.method_calls == [call.release_all(), call.press("esc")]


def test_trigger_missing_runs_multiple_key_inputs_in_saved_order(tmp_path):
    trigger = tmp_path / "trigger.png"
    trigger.write_bytes(b"not a real png but path exists")
    executor = _make_executor()
    rule = AutomationRule(
        action_type="click",
        trigger_image=str(trigger),
        rewind_previous_on_trigger_missing=True,
        trigger_missing_rewind_count=1,
        trigger_missing_rewind_delay=0,
        trigger_missing_rewind_key_sequences=[["esc"], ["shift", "up"], ["enter"]],
        trigger_missing_rewind_key_repeat_count=2,
        trigger_missing_rewind_key_repeat_delay=0,
    )
    input_ctrl = Mock()
    input_ctrl.press.return_value = True
    input_ctrl.hotkey.return_value = True

    with patch.object(RULE_EXECUTOR_MODULE, "get_input_controller", return_value=input_ctrl), patch.object(
        executor, "_wait_for_trigger", return_value=None
    ):
        result = executor._execute_rule_with_retry(rule, step_num="2", can_rewind_previous=True)

    assert result.success is True
    assert result.rewind_previous_action is True
    assert input_ctrl.method_calls == [
        call.release_all(),
        call.press("esc"),
        call.hotkey("shift", "up"),
        call.press("enter"),
        call.press("esc"),
        call.hotkey("shift", "up"),
        call.press("enter"),
    ]


def test_trigger_missing_key_repeat_delay_runs_between_each_key_and_list_cycle(tmp_path):
    trigger = tmp_path / "trigger.png"
    trigger.write_bytes(b"not a real png but path exists")
    executor = _make_executor()
    rule = AutomationRule(
        action_type="click",
        trigger_image=str(trigger),
        rewind_previous_on_trigger_missing=True,
        trigger_missing_rewind_count=1,
        trigger_missing_rewind_delay=0,
        trigger_missing_rewind_key_sequences=[["esc"], ["enter"]],
        trigger_missing_rewind_key_repeat_count=2,
        trigger_missing_rewind_key_repeat_delay=0.35,
    )
    input_ctrl = Mock()
    input_ctrl.press.return_value = True

    with patch.object(
        RULE_EXECUTOR_MODULE,
        "get_input_controller",
        return_value=input_ctrl,
    ), patch.object(
        executor._stop_event,
        "wait",
        return_value=False,
    ) as wait, patch.object(
        executor,
        "_wait_for_trigger",
        return_value=None,
    ):
        result = executor._execute_rule_with_retry(
            rule,
            step_num="2",
            can_rewind_previous=True,
        )

    assert result.success is True
    assert input_ctrl.method_calls == [
        call.release_all(),
        call.press("esc"),
        call.press("enter"),
        call.press("esc"),
        call.press("enter"),
    ]
    assert wait.call_args_list == [call(0.35), call(0.35), call(0.35)]


def test_trigger_missing_rewind_uses_each_keys_own_repeat_and_delay(tmp_path):
    trigger = tmp_path / "trigger.png"
    trigger.write_bytes(b"not a real png but path exists")
    executor = _make_executor()
    rule = AutomationRule(
        action_type="click",
        trigger_image=str(trigger),
        rewind_previous_on_trigger_missing=True,
        trigger_missing_rewind_count=1,
        trigger_missing_rewind_delay=0,
        trigger_missing_rewind_key_sequences=[["esc"], ["enter"]],
        trigger_missing_rewind_key_sequence_settings=[
            {
                "repeat_count": 2,
                "repeat_delay": 0.1,
                "repeat_delay_random": False,
                "repeat_delay_random_range": 0.0,
            },
            {
                "repeat_count": 3,
                "repeat_delay": 0.7,
                "repeat_delay_random": False,
                "repeat_delay_random_range": 0.0,
            },
        ],
        # Explicit per-key settings must take priority over these legacy values.
        trigger_missing_rewind_key_repeat_count=9,
        trigger_missing_rewind_key_repeat_delay=5.0,
    )
    input_ctrl = Mock()
    input_ctrl.press.return_value = True

    with patch.object(
        RULE_EXECUTOR_MODULE,
        "get_input_controller",
        return_value=input_ctrl,
    ), patch.object(
        executor._stop_event,
        "wait",
        return_value=False,
    ) as wait, patch.object(
        executor,
        "_wait_for_trigger",
        return_value=None,
    ):
        result = executor._execute_rule_with_retry(
            rule,
            step_num="2",
            can_rewind_previous=True,
        )

    assert result.success is True
    assert result.rewind_previous_action is True
    assert input_ctrl.method_calls == [
        call.release_all(),
        call.press("esc"),
        call.press("esc"),
        call.press("enter"),
        call.press("enter"),
        call.press("enter"),
    ]
    assert wait.call_args_list == [call(0.1), call(0.1), call(0.7), call(0.7)]


def test_trigger_missing_per_key_random_delay_is_independent(tmp_path):
    trigger = tmp_path / "trigger.png"
    trigger.write_bytes(b"not a real png but path exists")
    executor = _make_executor()
    rule = AutomationRule(
        action_type="click",
        trigger_image=str(trigger),
        rewind_previous_on_trigger_missing=True,
        trigger_missing_rewind_count=1,
        trigger_missing_rewind_delay=0,
        trigger_missing_rewind_key_sequences=[["esc"], ["enter"]],
        trigger_missing_rewind_key_sequence_settings=[
            {
                "repeat_count": 1,
                "repeat_delay": 0.2,
                "repeat_delay_random": True,
                "repeat_delay_random_range": 0.1,
            },
            {
                "repeat_count": 1,
                "repeat_delay": 9.0,
                "repeat_delay_random": False,
                "repeat_delay_random_range": 0.0,
            },
        ],
    )
    input_ctrl = Mock()
    input_ctrl.press.return_value = True

    with patch.object(
        RULE_EXECUTOR_MODULE,
        "get_input_controller",
        return_value=input_ctrl,
    ), patch.object(
        RULE_EXECUTOR_MODULE.random,
        "uniform",
        return_value=0.05,
    ) as random_uniform, patch.object(
        executor._stop_event,
        "wait",
        return_value=False,
    ) as wait, patch.object(
        executor,
        "_wait_for_trigger",
        return_value=None,
    ):
        result = executor._execute_rule_with_retry(
            rule,
            step_num="2",
            can_rewind_previous=True,
        )

    assert result.success is True
    random_uniform.assert_called_once_with(-0.1, 0.1)
    wait.assert_called_once_with(pytest.approx(0.25))


def test_trigger_missing_rewind_key_repeat_delay_uses_random_range(tmp_path):
    trigger = tmp_path / "trigger.png"
    trigger.write_bytes(b"not a real png but path exists")
    executor = _make_executor()
    rule = AutomationRule(
        action_type="click",
        trigger_image=str(trigger),
        rewind_previous_on_trigger_missing=True,
        trigger_missing_rewind_count=1,
        trigger_missing_rewind_delay=0,
        trigger_missing_rewind_keys=["esc"],
        trigger_missing_rewind_key_repeat_count=2,
        trigger_missing_rewind_key_repeat_delay=0.2,
        trigger_missing_rewind_key_repeat_delay_random=True,
        trigger_missing_rewind_key_repeat_delay_random_range=0.1,
    )
    input_ctrl = SimpleNamespace(press=Mock(return_value=True), hotkey=Mock(return_value=True))

    with patch.object(RULE_EXECUTOR_MODULE, "get_input_controller", return_value=input_ctrl), patch.object(
        RULE_EXECUTOR_MODULE.random, "uniform", return_value=0.05
    ), patch.object(executor._stop_event, "wait", return_value=False) as wait, patch.object(
        executor, "_wait_for_trigger", return_value=None
    ):
        result = executor._execute_rule_with_retry(rule, step_num="2", can_rewind_previous=True)

    assert result.success is True
    assert result.rewind_previous_action is True
    assert input_ctrl.press.call_count == 2
    wait.assert_called_once_with(pytest.approx(0.25))


def test_trigger_missing_key_failure_stops_before_previous_action_rewind(tmp_path):
    trigger = tmp_path / "trigger.png"
    trigger.write_bytes(b"not a real png but path exists")
    executor = _make_executor()
    rule = AutomationRule(
        action_type="click",
        trigger_image=str(trigger),
        rewind_previous_on_trigger_missing=True,
        trigger_missing_rewind_count=1,
        trigger_missing_rewind_delay=0,
        trigger_missing_rewind_keys=["esc"],
    )
    input_ctrl = SimpleNamespace(press=Mock(return_value=False), hotkey=Mock(return_value=True))

    with patch.object(RULE_EXECUTOR_MODULE, "get_input_controller", return_value=input_ctrl), patch.object(
        executor, "_wait_for_trigger", return_value=None
    ):
        result = executor._execute_rule_with_retry(rule, step_num="2", can_rewind_previous=True)

    assert result.success is False
    assert result.rewind_previous_action is False
    assert "키입력 실패" in result.message
    input_ctrl.press.assert_called_once_with("esc")


def test_trigger_missing_previous_action_rewind_is_limited(tmp_path):
    trigger = tmp_path / "trigger.png"
    trigger.write_bytes(b"not a real png but path exists")
    executor = _make_executor()
    rule = AutomationRule(
        action_type="click",
        trigger_image=str(trigger),
        rewind_previous_on_trigger_missing=True,
        trigger_missing_rewind_count=1,
        trigger_missing_rewind_delay=0,
    )

    with patch.object(executor, "_wait_for_trigger", return_value=None):
        first = executor._execute_rule_with_retry(rule, step_num="2", can_rewind_previous=True)
        second = executor._execute_rule_with_retry(rule, step_num="2", can_rewind_previous=True)

    assert first.success is True
    assert first.rewind_previous_action is True
    assert second.success is False
    assert second.rewind_previous_action is False


def test_trigger_missing_without_playlist_skip_still_fails(tmp_path):
    trigger = tmp_path / "trigger.png"
    trigger.write_bytes(b"not a real png but path exists")
    executor = _make_executor()
    rule = AutomationRule(
        action_type="click",
        trigger_image=str(trigger),
        stop_playlist_on_trigger_missing=False,
    )

    with patch.object(RULE_EXECUTOR_MODULE.time, "sleep", return_value=None), patch.object(
        executor, "_wait_for_trigger", return_value=None
    ):
        result = executor._execute_rule_with_retry(rule, step_num="1")

    assert result.success is False
    assert result.skip_current_playlist is False


def test_trigger_file_missing_is_configuration_error(tmp_path):
    executor = _make_executor()
    rule = AutomationRule(
        action_type="click",
        trigger_image=str(tmp_path / "missing.png"),
        stop_playlist_on_trigger_missing=True,
    )

    result = executor._execute_rule_with_retry(rule, step_num="1")

    assert result.success is False
    assert result.skip_current_playlist is False
    assert "트리거 이미지 파일 없음" in result.message


def test_trigger_playlist_skip_ui_and_sequence_hooks_exist():
    player_view_src = PLAYER_VIEW.read_text(encoding="utf-8")
    main_window_src = MAIN_WINDOW.read_text(encoding="utf-8")
    trigger_editor_src = player_view_src[
        player_view_src.index("def _edit_trigger_image"):
        player_view_src.index("def _edit_monitoring_mode")
    ]

    assert "트리거 미감지 시 현재 재생목록 종료" in player_view_src
    assert "stop_playlist_on_trigger_missing" in player_view_src
    assert "trigger_missing_keys" in player_view_src
    assert "trigger_missing_key_sequences" in player_view_src
    assert "trigger_missing_key_repeat_count" in player_view_src
    assert "trigger_missing_key_repeat_delay" in player_view_src
    assert "trigger_missing_key_repeat_delay_random" in player_view_src
    assert "rewind_previous_on_trigger_missing" in player_view_src
    assert "trigger_missing_rewind_count" in player_view_src
    assert "trigger_missing_rewind_keys" in player_view_src
    assert "trigger_missing_rewind_key_sequences" in player_view_src
    assert "trigger_missing_key_sequence_settings" in player_view_src
    assert "trigger_missing_rewind_key_sequence_settings" in player_view_src
    assert "trigger_missing_rewind_key_repeat_count" in player_view_src
    assert "재생목록 종료 전 키입력" in trigger_editor_src
    assert "지정 액션 복귀 전 키입력" in trigger_editor_src
    assert "키별 반복횟수" in trigger_editor_src
    assert "키별 반복대기시간" in trigger_editor_src
    assert "각 키의 반복횟수·반복대기시간·랜덤시간을 개별 적용" in trigger_editor_src
    assert "종료 전 키 반복횟수" not in trigger_editor_src
    assert "복귀 전 키 반복횟수" not in trigger_editor_src
    assert trigger_editor_src.count("apply_trigger_key_state_to_rule(") == 5
    assert "트리거 미감지 시 지정 액션으로 돌아가기" in player_view_src
    assert "trigger_missing_rewind_rule_id" in trigger_editor_src
    assert "playlist_skip_options_frame.pack_forget()" in trigger_editor_src
    assert "rewind_options_frame.pack_forget()" in trigger_editor_src
    assert "▼ 옵션 펼치기" in trigger_editor_src
    assert "▲ 옵션 접기" in trigger_editor_src
    assert "+ 키 추가" in trigger_editor_src
    assert "전체 해제" in trigger_editor_src
    assert 'hierarchy_label = "◆ 상위" if not parent_step else "↳ 하위"' in player_view_src
    assert "◆ 상위는 독립 액션" not in trigger_editor_src
    assert "트리거 검색범위 적용 방식" in trigger_editor_src
    assert 'build_preset_row("a", "A영역", COLORS["accent_blue"])' in trigger_editor_src
    assert 'build_preset_row("b", "B영역", COLORS["accent_orange"])' in trigger_editor_src
    assert "자유영역" in trigger_editor_src
    assert "trigger_search_region" in trigger_editor_src
    assert "trigger_x_entry" not in trigger_editor_src
    assert "trigger_y_entry" not in trigger_editor_src
    assert "고급 옵션" not in trigger_editor_src
    assert "PLAYLIST_SKIP_TRIGGER_MISSING" in main_window_src
    assert "def _mini_on_playlist_skip(self, message: str):" in main_window_src
    assert "self._run_sequence_plan(next_index, playback_generation=playback_generation)" in main_window_src


def test_next_screen_wait_uses_stop_event_wait_before_skip_log():
    rule_executor_src = RULE_EXECUTOR.read_text(encoding="utf-8")
    next_screen_wait_src = rule_executor_src[
        rule_executor_src.index("# 클릭 동작이고 다음 타겟 이미지가 있으면 확인"):
        rule_executor_src.index("# 클릭이 아니거나 다음 이미지가 없으면 바로 성공")
    ]

    assert "self._stop_event.wait(check_interval)" in next_screen_wait_src
    assert "time.sleep(check_interval)" not in next_screen_wait_src
    assert "if self._stop_event.is_set():" in next_screen_wait_src
    assert "⏭ 다음 화면 스킵" in next_screen_wait_src


def test_game_mode_trigger_gate_receives_source_rule_in_all_playback_paths():
    player_view_src = PLAYER_VIEW.read_text(encoding="utf-8")
    main_window_src = MAIN_WINDOW.read_text(encoding="utf-8")

    assert "source_previous_rule=None, trigger_rewind_attempts=None" in player_view_src
    assert "self._source_rule = source_rule" in player_view_src
    assert "self._source_previous_rule = source_previous_rule" in player_view_src
    assert "self._trigger_rewind_attempts = trigger_rewind_attempts" in player_view_src
    assert "def _handle_source_trigger_gate(self) -> bool:" in player_view_src
    assert "can_rewind_previous=bool(getattr(self, \"_source_previous_rule\", None))" in player_view_src
    assert "self._rewind_previous_action = True" in player_view_src
    assert "if not self._handle_source_trigger_gate():" in player_view_src
    assert player_view_src.count("source_rule=source_rule") >= 2
    assert "source_previous_rule=source_previous_rule" in player_view_src
    assert "source_rule=source_rule" in main_window_src
    assert "source_previous_rule=source_previous_rule" in main_window_src
    assert "rewind_previous_action = bool(getattr(gm, '_rewind_previous_action', False))" in main_window_src
    assert "트리거 미감지 → 지정 액션 재시도 중" in player_view_src
    assert "트리거 미감지 → 지정 액션 재시도 중" in main_window_src
    assert "_rewind_target_rule_id" in player_view_src
    assert "rewind_target_rule_id=rewind_target_rule_id" in player_view_src
    assert "rewind_target_rule_id=rewind_target_rule_id" in main_window_src
    assert "self._rewind_delay = 0.0" in player_view_src
    assert "self._rewind_delay = max(0.0, float(getattr(result, \"rewind_delay\", 0.0) or 0.0))" in player_view_src
    assert "rewind_delay = float(getattr(gm, \"_rewind_delay\", 0.0) or 0.0)" in player_view_src
    assert "rewind_delay = float(getattr(gm, \"_rewind_delay\", 0.0) or 0.0)" in main_window_src
    assert "rewind_delay=rewind_delay" in player_view_src
    assert "rewind_delay=rewind_delay" in main_window_src


def test_game_mode_trigger_missing_skip_propagates_to_playlist_handlers():
    player_view_src = PLAYER_VIEW.read_text(encoding="utf-8")
    main_window_src = MAIN_WINDOW.read_text(encoding="utf-8")

    assert "self._skip_current_playlist = True" in player_view_src
    assert "skip_current_playlist = bool(getattr(gm, '_skip_current_playlist', False))" in player_view_src
    assert "skip_current_playlist = bool(getattr(gm, \"_skip_current_playlist\", False))" in player_view_src
    assert "skip_current_playlist: bool = False" in player_view_src
    assert "message = error_msg or PLAYLIST_SKIP_TRIGGER_MISSING" in player_view_src

    assert "skip_current_playlist = bool(getattr(gm, '_skip_current_playlist', False))" in main_window_src
    assert "skip_current_playlist: bool = False" in main_window_src
    assert "self._mini_on_playlist_skip(message)" in main_window_src
