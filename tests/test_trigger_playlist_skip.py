from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import importlib

import pytest

from src.analyzer.automation_models import AutomationRule


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
        stop_playlist_on_trigger_missing=True,
        trigger_missing_keys=["esc"],
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
        trigger_missing_rewind_key_repeat_count=2,
        trigger_missing_rewind_key_repeat_delay=0.15,
        trigger_missing_rewind_key_repeat_delay_random=True,
        trigger_missing_rewind_key_repeat_delay_random_range=0.05,
    )

    payload = rule.to_dict()

    assert payload["stop_playlist_on_trigger_missing"] is True
    assert payload["trigger_missing_keys"] == ["esc"]
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
    assert payload["trigger_missing_rewind_key_repeat_count"] == 2
    assert payload["trigger_missing_rewind_key_repeat_delay"] == 0.15
    assert payload["trigger_missing_rewind_key_repeat_delay_random"] is True
    assert payload["trigger_missing_rewind_key_repeat_delay_random_range"] == 0.05
    restored = AutomationRule.from_dict(payload)
    assert restored.stop_playlist_on_trigger_missing is True
    assert restored.trigger_missing_keys == ["esc"]
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
    assert restored.trigger_missing_rewind_key_repeat_count == 2
    assert restored.trigger_missing_rewind_key_repeat_delay == 0.15
    assert restored.trigger_missing_rewind_key_repeat_delay_random is True
    assert restored.trigger_missing_rewind_key_repeat_delay_random_range == 0.05


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
    assert "trigger_missing_key_repeat_count" in player_view_src
    assert "trigger_missing_key_repeat_delay" in player_view_src
    assert "trigger_missing_key_repeat_delay_random" in player_view_src
    assert "rewind_previous_on_trigger_missing" in player_view_src
    assert "trigger_missing_rewind_count" in player_view_src
    assert "trigger_missing_rewind_keys" in player_view_src
    assert "trigger_missing_rewind_key_repeat_count" in player_view_src
    assert trigger_editor_src.count("종료 전 키입력") >= 2
    assert "트리거 미감지 시 전 액션으로 돌아가기" in player_view_src
    assert "playlist_skip_options_frame.pack_forget()" in trigger_editor_src
    assert "rewind_options_frame.pack_forget()" in trigger_editor_src
    assert "▼ 옵션 펼치기" in trigger_editor_src
    assert "▲ 옵션 접기" in trigger_editor_src
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
    assert "트리거 미감지 → 전 액션 재시도 중" in player_view_src
    assert "트리거 미감지 → 전 액션 재시도 중" in main_window_src
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
