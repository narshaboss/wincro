"""Mocked cleanup policy tests. No real mouse/keyboard/device calls."""

import ctypes
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.utils import input_controller as ic
from src.utils.input_controller import _get_pressed_mouse_buttons as read_pressed_buttons


@pytest.fixture
def cleanup(monkeypatch):
    controller = ic.InputController()
    mouse_up, key_up = Mock(), Mock()
    held = set()
    monkeypatch.setattr(ic, "_get_arduino", lambda: None)
    monkeypatch.setattr(ic, "_get_pressed_mouse_buttons", lambda: set(held))
    monkeypatch.setattr(ic.pyautogui, "mouseUp", mouse_up)
    monkeypatch.setattr(ic.pyautogui, "keyUp", key_up)
    monkeypatch.setattr(ic, "_input_block_event", threading.Event())
    return SimpleNamespace(controller=controller, mouse_up=mouse_up, key_up=key_up, held=held)


@pytest.mark.parametrize("arduino_connected", [False, True])
def test_idle_start_repeat_and_stop_never_emit_mouse_up(cleanup, monkeypatch, arduino_connected):
    release = Mock(return_value=True)
    monkeypatch.setattr(ic, "_get_arduino", lambda: SimpleNamespace(
        is_connected=arduino_connected, release_all=release,
    ))
    for _ in range(12):
        assert cleanup.controller.release_all() is True
    cleanup.mouse_up.assert_not_called()
    assert cleanup.key_up.call_count >= 12
    assert release.call_count == (12 if arduino_connected else 0)


@pytest.mark.parametrize("button", ["left", "right", "middle"])
def test_untracked_but_currently_held_button_is_recovered_once(cleanup, button):
    cleanup.held.add(button)
    cleanup.mouse_up.side_effect = lambda **_kwargs: cleanup.held.discard(button)
    assert cleanup.controller.release_all() is True
    assert cleanup.controller.release_all() is True
    cleanup.mouse_up.assert_called_once_with(button=button, _pause=False)


@pytest.mark.parametrize("button", ["left", "right", "middle", "x1"])
def test_tracked_button_is_released_even_if_os_cannot_observe_it(cleanup, button):
    cleanup.controller._track_button(button, True)
    assert cleanup.controller.release_all() is True
    assert cleanup.controller.release_all() is True
    cleanup.mouse_up.assert_called_once_with(button=button, _pause=False)
    assert cleanup.controller._pressed_buttons == set()


def test_mouse_release_failure_keeps_tracking_for_retry(cleanup):
    cleanup.controller._track_button("right", True)
    cleanup.mouse_up.side_effect = OSError("simulated release failure")
    assert cleanup.controller.release_all() is False
    assert cleanup.controller._pressed_buttons == {"right"}
    cleanup.mouse_up.side_effect = None
    assert cleanup.controller.release_all() is True
    assert cleanup.controller._pressed_buttons == set()
    assert cleanup.mouse_up.call_count == 2


def test_hid_cleanup_failure_is_not_hidden_by_idle_mouse_policy(cleanup, monkeypatch):
    release = Mock(return_value=False)
    monkeypatch.setattr(ic, "_get_arduino", lambda: SimpleNamespace(is_connected=True, release_all=release))
    monkeypatch.setattr(ic.time, "sleep", lambda _seconds: None)
    assert cleanup.controller.release_all() is False
    assert release.call_count == 2
    cleanup.mouse_up.assert_not_called()


def test_async_state_uses_current_down_bit_not_pressed_since_last_query(monkeypatch):
    states = {0x01: 0x8001, 0x02: 0x0001, 0x04: -32768}
    reader = Mock(side_effect=lambda key: states[key])
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(user32=SimpleNamespace(GetAsyncKeyState=reader)), raising=False)
    assert read_pressed_buttons() == {"left", "middle"}
    assert reader.call_count == 3


def test_state_query_failure_falls_back_to_tracked_releases(cleanup, monkeypatch):
    reader = Mock(side_effect=OSError("simulated inaccessible input desktop"))
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(user32=SimpleNamespace(GetAsyncKeyState=reader)), raising=False)
    monkeypatch.setattr(ic, "_get_pressed_mouse_buttons", read_pressed_buttons)
    cleanup.controller._track_button("left", True)
    assert cleanup.controller.release_all() is True
    cleanup.mouse_up.assert_called_once_with(button="left", _pause=False)


def test_real_executor_boundaries_use_policy_without_phantom_mouse_events(cleanup, monkeypatch):
    from src.analyzer.automation_models import AutomationPlan
    from src.player import rule_executor

    monkeypatch.setattr(rule_executor, "get_input_controller", lambda: cleanup.controller)
    plan = AutomationPlan(name="isolated-input-cleanup", initial_rules=[], monitoring_rules=[])
    plan.total_repeat_count = 3
    executor = rule_executor.RuleExecutor()
    completed = []
    executor.set_callbacks(on_complete=lambda success, _message: completed.append(success))
    assert executor.execute_plan(plan) is True
    executor._execution_thread.join(timeout=3)
    assert not executor._execution_thread.is_alive()
    assert completed == [True]
    cleanup.mouse_up.assert_not_called()
    assert cleanup.key_up.call_count > 0
