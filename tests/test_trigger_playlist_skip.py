from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import importlib

import pytest

from src.analyzer.automation_models import AutomationRule


RULE_EXECUTOR_MODULE = importlib.import_module("src.player.rule_executor")
PLAYER_VIEW = Path(r"C:\Projects\wincro\src\ui\player_view.py")
MAIN_WINDOW = Path(r"C:\Projects\wincro\src\ui\main_window.py")


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
    )

    payload = rule.to_dict()

    assert payload["stop_playlist_on_trigger_missing"] is True
    assert AutomationRule.from_dict(payload).stop_playlist_on_trigger_missing is True


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

    assert "트리거 미감지 시 현재 재생목록 종료" in player_view_src
    assert "stop_playlist_on_trigger_missing" in player_view_src
    assert "PLAYLIST_SKIP_TRIGGER_MISSING" in main_window_src
    assert "def _mini_on_playlist_skip(self, message: str):" in main_window_src
    assert "self._run_sequence_plan(next_index)" in main_window_src
