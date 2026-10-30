"""Headless contracts; load selected AST nodes, never the application modules.

No native window backend, input controller, GUI, network, or real sleep is used.
These tests are intended for the coordinator's remote CI, not local execution.
"""

import __future__
import ast
import builtins
import copy
import hashlib
import json
import logging
import math
import ntpath
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


ROOT = Path(__file__).resolve().parents[1]
PLAYER_VIEW = "src/ui/player_view.py"
EXECUTOR = "src/player/rule_executor.py"
WINDOW_CONFIG = {
    "executable_name": "client.exe",
    "title_contains": "Test client",
    "operation": "maximize",
}
WAITING = [
    pytest.param("missing", "window never found", id="never-found"),
    pytest.param("ambiguous", "multiple matching windows", id="ambiguous"),
    pytest.param("pending", "foreground request denied", id="focus-denied"),
]


def _tree(path):
    return ast.parse((ROOT / path).read_text(encoding="utf-8-sig"))


def _load(path, names, namespace=None, *, owner=None, imports=None):
    """Compile only requested definitions/constants with fail-closed imports."""
    namespace = {} if namespace is None else namespace
    namespace.setdefault("__name__", __name__)
    imports = {} if imports is None else imports

    def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
        if (level, name) in imports:
            return imports[level, name]
        if level == 0 and name in {"copy", "uuid"}:
            return builtins.__import__(name, globals, locals, fromlist, level)
        raise AssertionError(f"Unmocked import in AST harness: {level}:{name}")

    namespace["__builtins__"] = dict(vars(builtins), __import__=safe_import)
    body = _tree(path).body
    if owner is not None:
        body = next(n for n in body if isinstance(n, ast.ClassDef) and n.name == owner).body
    selected, found = [], set()
    for node in body:
        node_names = set()
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            node_names.add(node.name)
        elif isinstance(node, ast.Assign):
            node_names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            node_names.add(node.target.id)
        if node_names.intersection(names):
            selected.append(copy.deepcopy(node))
            found.update(node_names.intersection(names))
    assert found == set(names), f"Missing pending-integration API in {path}: {set(names) - found}"
    code = compile(
        ast.Module(body=selected, type_ignores=[]), str(ROOT / path), "exec",
        flags=__future__.annotations.compiler_flag, dont_inherit=True,
    )
    exec(code, namespace)
    return namespace


class VirtualClock:
    def __init__(self, start=0.0):
        self.now = start
        self.waits = []
        self.stopped = False
        self.paused = False
        self.on_wait = lambda: None

    def wait(self, seconds):
        assert seconds == pytest.approx(0.1), "Cancellation must be polled every 0.1s"
        assert len(self.waits) < 3000, "Unbounded fake wait: stop/pause contract broken"
        self.waits.append(seconds)
        self.now = round(self.now + seconds, 6)
        self.on_wait()
        return self.stopped


class ScriptedController:
    def __init__(self, clock, *results):
        self.clock = clock
        self.results = results
        self.steps = []

    def step(self, config):
        self.steps.append((self.clock.now, copy.deepcopy(config)))
        return self.results[min(len(self.steps) - 1, len(self.results) - 1)]


def _result(status, message=None):
    namespace = _load("src/utils/window_control.py", {"WindowControlResult"}, {"dataclass": dataclass})
    return namespace["WindowControlResult"](status, message or status)


def _runner(clock):
    contract = _load("src/utils/window_control.py", {
        "WINDOW_CONTROL_OPERATIONS", "_INVALID_EXE_CHARACTERS",
        "normalize_window_control_config", "validate_window_control_config",
    }, {"Mapping": Mapping, "ntpath": ntpath, "re": re})
    return _load("src/player/window_control_runtime.py", {"wait_for_window_control"}, {
        "time": SimpleNamespace(monotonic=lambda: clock.now),
        "validate_window_control_config": Mock(wraps=contract["validate_window_control_config"]),
        "WindowController": Mock(side_effect=AssertionError("Never construct a native controller")),
    })


def _run(namespace, clock, controller, **kwargs):
    return namespace["wait_for_window_control"](
        copy.deepcopy(WINDOW_CONFIG), should_stop=lambda: clock.stopped,
        is_paused=lambda: clock.paused, wait=clock.wait, controller=controller, **kwargs,
    )


@pytest.mark.parametrize("status,message", WAITING)
def test_unready_windows_keep_waiting_with_short_cancellation_slices(status, message):
    clock = VirtualClock()
    namespace = _runner(clock)
    controller = ScriptedController(clock, _result(status, message))
    logs = Mock()
    clock.on_wait = lambda: setattr(clock, "stopped", clock.now >= 2.1)

    ok, detail = _run(namespace, clock, controller, on_wait=logs)

    assert ok is False and detail
    assert [at for at, _ in controller.steps] == pytest.approx([0, 0.5, 1, 1.5, 2])
    assert all(config == WINDOW_CONFIG for _, config in controller.steps)
    assert len(clock.waits) == 21
    logs.assert_called_once_with(message)
    namespace["validate_window_control_config"].assert_called_once_with(WINDOW_CONFIG)
    namespace["WindowController"].assert_not_called()


def test_missing_ambiguous_focus_denied_can_recover_to_verified_ready():
    clock = VirtualClock()
    controller = ScriptedController(clock, *[
        _result("missing"), _result("ambiguous"), _result("pending", "focus denied"),
        _result("ready", "verified foreground and size"),
    ])

    assert _run(_runner(clock), clock, controller) == (True, "verified foreground and size")
    assert [at for at, _ in controller.steps] == pytest.approx([0, 0.5, 1, 1.5])


@pytest.mark.parametrize("status", ["unsupported", "invalid_config"])
def test_terminal_controller_result_is_not_retried(status):
    clock = VirtualClock()
    controller = ScriptedController(clock, _result(status, "cannot execute"))
    assert _run(_runner(clock), clock, controller) == (False, "cannot execute")
    assert len(controller.steps) == 1 and not clock.waits


def test_stop_before_first_step_never_touches_windows():
    clock = VirtualClock()
    clock.stopped = True
    controller = ScriptedController(clock, _result("ready"))
    ok, message = _run(_runner(clock), clock, controller)
    assert ok is False and message and not controller.steps and not clock.waits


def test_stop_during_step_wins_over_ready_result():
    clock = VirtualClock()
    controller = Mock()

    def step(config):
        clock.stopped = True
        return _result("ready")

    controller.step.side_effect = step
    ok, message = _run(_runner(clock), clock, controller)
    assert ok is False and message and not clock.waits
    controller.step.assert_called_once_with(WINDOW_CONFIG)


@pytest.mark.parametrize("stop_while_paused", [False, True])
def test_initial_pause_prevents_steps_until_resume_or_stop(stop_while_paused):
    clock = VirtualClock()
    clock.paused = True
    controller = ScriptedController(clock, _result("ready"))

    def tick():
        assert not controller.steps
        if clock.now >= 0.3:
            clock.stopped = stop_while_paused
            clock.paused = stop_while_paused

    clock.on_wait = tick
    ok, message = _run(_runner(clock), clock, controller)
    assert ok is (not stop_while_paused) and message
    assert len(clock.waits) == 3
    assert [at for at, _ in controller.steps] == ([] if stop_while_paused else [0.3])


def test_pause_during_retry_does_not_touch_windows_until_resumed():
    clock = VirtualClock()
    controller = ScriptedController(clock, _result("pending"), _result("ready"))

    def tick():
        clock.paused = 0.2 <= clock.now < 0.8
        if clock.paused:
            assert len(controller.steps) == 1

    clock.on_wait = tick
    assert _run(_runner(clock), clock, controller)[0] is True
    assert [at for at, _ in controller.steps] == pytest.approx([0, 0.8])


@pytest.mark.parametrize("stop_during_pause", [False, True])
def test_pause_arriving_during_ready_step_defers_completion(stop_during_pause):
    clock = VirtualClock()
    steps = []

    def step(config):
        assert not clock.paused, "A paused runner must not manipulate windows"
        steps.append(clock.now)
        if len(steps) == 1:
            clock.paused = True
        return _result("ready", "verified")

    def tick():
        if clock.now >= 0.3:
            clock.paused = False
            clock.stopped = stop_during_pause

    clock.on_wait = tick
    ok, message = _run(_runner(clock), clock, SimpleNamespace(step=step))
    assert clock.now >= 0.3, "Ready must not complete an action while playback is paused"
    assert ok is (not stop_during_pause) and message


def test_wait_reporting_cancellation_stops_even_before_stop_callback_changes():
    clock = VirtualClock()
    controller = ScriptedController(clock, _result("missing"))
    waits = Mock(return_value=True)
    ok, message = _runner(clock)["wait_for_window_control"](
        WINDOW_CONFIG, should_stop=lambda: False, is_paused=lambda: False,
        wait=waits, controller=controller,
    )
    assert ok is False and message and len(controller.steps) == 1
    waits.assert_called_once_with(0.1)


@pytest.mark.parametrize("bad_config", [
    {}, {**WINDOW_CONFIG, "operation": "launch"},
    {**WINDOW_CONFIG, "executable_name": "C:/client.exe"},
])
def test_invalid_config_fails_before_controller_access(bad_config):
    clock = VirtualClock()
    controller = Mock()
    ok, message = _runner(clock)["wait_for_window_control"](
        bad_config, should_stop=lambda: False, is_paused=lambda: False,
        wait=clock.wait, controller=controller,
    )
    assert ok is False and message and not clock.waits
    controller.step.assert_not_called()


def _executor(clock, runner):
    names = {"_execute_fixed_action", "_execute_window_control", "_make_result", "_update_progress"}
    namespace = _load(EXECUTOR, names, {
        "datetime": datetime, "RuleExecutionResult": SimpleNamespace,
        "WINDOW_CONTROL_ACTION_TYPE": "window_control", "AUTO_LIST_ACTION_TYPE": "auto_list",
        "AUTO_LIST_VALUE_INPUT_ACTION_TYPE": "auto_list_value_input",
        "ACTION_CALL_ACTION_TYPE": "action_call", "logger": Mock(),
        "get_input_controller": Mock(side_effect=AssertionError("Unexpected input-controller access")),
        "wait_for_window_control": runner,
    }, owner="RuleExecutor")
    host = type("ExecutorHarness", (), {name: namespace[name] for name in names})()
    host._stop_event = SimpleNamespace(is_set=lambda: clock.stopped, wait=clock.wait)
    host._pause_event = SimpleNamespace(is_set=lambda: not clock.paused)
    host._step_prefix = "[1] "
    host._on_progress = Mock()
    host._progress = SimpleNamespace(message="window control", current_action_name="window control")
    host._state = "running"
    return host, namespace


def _rule(action_type="window_control"):
    return SimpleNamespace(rule_id="window-rule", action_type=action_type,
                           window_control_config=copy.deepcopy(WINDOW_CONFIG))


def test_fixed_dispatch_routes_window_control_before_any_other_action_or_input():
    clock = VirtualClock()
    host, namespace = _executor(clock, Mock())
    sentinel = object()
    host._execute_window_control = Mock(return_value=sentinel)
    rule, started = _rule(), datetime.now()
    assert host._execute_fixed_action(rule, started) is sentinel
    host._execute_window_control.assert_called_once_with(rule, started)
    namespace["get_input_controller"].assert_not_called()
    host._on_progress.assert_not_called()


def test_fixed_dispatch_respects_stop_before_window_control():
    clock = VirtualClock()
    clock.stopped = True
    host, namespace = _executor(clock, Mock())
    host._execute_window_control = Mock()
    result = host._execute_fixed_action(_rule(), datetime.now())
    assert result.success is False
    host._execute_window_control.assert_not_called()
    namespace["get_input_controller"].assert_not_called()


@pytest.mark.parametrize("action_type,method", [
    ("auto_list", "_execute_auto_list"),
    ("auto_list_value_input", "_execute_auto_list_value_input"),
    ("action_call", "_execute_action_call"),
])
def test_unrelated_fixed_dispatch_still_uses_its_original_handler(action_type, method):
    host, namespace = _executor(VirtualClock(), Mock())
    expected = object()
    handler = Mock(return_value=expected)
    setattr(host, method, handler)
    host._execute_window_control = Mock(side_effect=AssertionError("Wrong dispatch"))
    rule, started = _rule(action_type), datetime.now()
    assert host._execute_fixed_action(rule, started) is expected
    handler.assert_called_once_with(rule, started)
    host._execute_window_control.assert_not_called()
    namespace["wait_for_window_control"].assert_not_called()


@pytest.mark.parametrize("success", [True, False])
def test_executor_forwards_wait_contract_and_logs_without_reporting_progress(success):
    clock = VirtualClock()

    def runner(config, *, should_stop, is_paused, wait, on_wait=None, controller=None):
        assert config == WINDOW_CONFIG and controller is None
        assert not should_stop() and not is_paused()
        clock.paused = True
        assert is_paused()
        clock.paused = False
        assert not is_paused()
        for _ in range(3):
            wait(0.1)
            on_wait("still waiting")
        clock.stopped = True
        assert should_stop()
        return success, "runner result"

    host, namespace = _executor(clock, runner)
    result = host._execute_fixed_action(_rule(), datetime.now())
    assert result.success is success and result.message == "runner result"
    assert result.rule_id == "window-rule"
    assert namespace["logger"].info.call_count == 3
    namespace["get_input_controller"].assert_not_called()
    host._on_progress.assert_not_called()


@pytest.mark.parametrize("success", [True, False])
def test_action_player_uses_shared_runner_without_input_or_image_lookup(success):
    state = SimpleNamespace(PLAYING="playing", STOPPED="stopped", PAUSED="paused")
    namespace = _load("src/player/action_player.py", {"_execute_action"}, {
        "PlayerState": state, "logger": Mock(),
        "Path": Mock(side_effect=AssertionError("No image lookup for a window action")),
        "get_input_controller": Mock(side_effect=AssertionError("No input for a window action")),
        "pyautogui": SimpleNamespace(FailSafeException=type("FakeFailSafe", (Exception,), {})),
    }, owner="ActionPlayer")
    host = SimpleNamespace(
        _state=state.PLAYING, _emergency_stop=SimpleNamespace(is_triggered=False),
        _wait_for_legacy_stop=Mock(return_value=False), _on_progress=Mock(),
        _update_progress=Mock(side_effect=AssertionError("Polling is not progress")),
        _find_all_images_on_screen=Mock(side_effect=AssertionError("Unexpected image search")),
    )

    def runner(config, *, should_stop, is_paused, wait, on_wait=None, controller=None):
        assert config == WINDOW_CONFIG and controller is None
        assert not should_stop() and not is_paused()
        host._state = state.PAUSED
        assert is_paused()
        host._state = state.STOPPED
        assert should_stop()
        host._state = state.PLAYING
        host._emergency_stop.is_triggered = True
        assert should_stop()
        host._emergency_stop.is_triggered = False
        wait(0.1)
        on_wait("waiting for window")
        return success, "not ready"

    namespace["wait_for_window_control"] = Mock(side_effect=runner)
    action = _action(wait_before=0, wait_after=0, x=None, y=None, target_image="must-not-open.png")
    result = namespace["_execute_action"](host, action, 1.0)
    assert result == ((True, "") if success else (False, "not ready"))
    namespace["wait_for_window_control"].assert_called_once()
    namespace["get_input_controller"].assert_not_called()
    namespace["Path"].assert_not_called()
    host._find_all_images_on_screen.assert_not_called()
    host._on_progress.assert_not_called()
    host._update_progress.assert_not_called()
    host._wait_for_legacy_stop.assert_called_once_with(0.1)
    namespace["logger"].info.assert_called_once()


def _converter():
    name = "_sequence_action_to_playback_rule"
    namespace = _load(PLAYER_VIEW, {name}, {
        "AutomationRule": SimpleNamespace, "copy": copy, "WINDOW_CONTROL_ACTION_TYPE": "window_control",
    }, owner="PlayerView")
    return type("PlayerViewHarness", (), {name: namespace[name]})()


def _action(action_type="window_control", action_id="window-action", **kwargs):
    fields = dict(action_type=action_type, action_id=action_id, description="Test action", children=[],
                  window_control_config=copy.deepcopy(WINDOW_CONFIG))
    fields.update(kwargs)
    return SimpleNamespace(**fields)


def test_sequence_conversion_preserves_window_config_in_nested_children():
    nested = _action(action_id="nested", window_control_config={**WINDOW_CONFIG, "operation": "minimize"})
    normal_child = _action("click", "normal", x=17, y=29, children=[nested], window_control_config={})
    source = _action(children=[normal_child])
    before = copy.deepcopy(source)
    rule = _converter()._sequence_action_to_playback_rule(source)

    assert rule is not None and rule.rule_type == "fixed_sequence"
    assert rule.window_control_config == WINDOW_CONFIG
    child = rule.children[0]
    assert (child.action_type, child.action_x, child.action_y) == ("click", 17, 29)
    assert child.parent_id == rule.rule_id
    assert child.children[0].parent_id == child.rule_id
    assert child.children[0].window_control_config == nested.window_control_config
    rule.window_control_config["title_contains"] = "changed"
    child.children[0].window_control_config["operation"] = "foreground"
    assert source == before, "Playback conversion must not alias editable window settings"


@pytest.mark.parametrize("action_type,rule_type", [
    ("click", "fixed_sequence"), ("key_press", "fixed_sequence"),
    ("type", "type_text"), ("hotkey", "hotkey"), ("wait_for_image", "wait_for_image"),
])
def test_window_conversion_does_not_change_unrelated_action_fields(action_type, rule_type):
    action = _action(action_type, x=31, y=47, text="original text", keys=["ctrl", "a"],
                     window_control_config={}, wait_after=0.7, repeat_count=3, target_image="target.png")
    rule = _converter()._sequence_action_to_playback_rule(action)
    assert (rule.action_type, rule.rule_type, rule.rule_id) == (action_type, rule_type, action.action_id)
    assert (rule.action_x, rule.action_y, rule.action_text) == (31, 47, "original text")
    assert rule.action_keys == ["ctrl", "a"] and rule.action_keys is not action.keys
    assert (rule.wait_after, rule.repeat_count, rule.target_image) == (0.7, 3, "target.png")
    assert not getattr(rule, "window_control_config", {})


class InlineThread:
    """Run the real notifier worker deterministically, without starting a thread."""
    def __init__(self, *, target, **kwargs):
        self.target = target

    def start(self):
        self.target()


def _watchdog(monkeypatch, clock, http_status=200):
    """Reuse the existing delivery regression's context/receipt, not its GUI imports."""
    notifier_names = {
        "DISCORD_WEBHOOK_PREFIXES", "DiscordAlert", "DiscordSendResult",
        "is_valid_discord_webhook_url", "default_pc_name", "_trim", "_format_fields",
        "build_discord_payload", "_safe_error_detail", "_retry_delay",
        "send_discord_webhook", "is_retryable_discord_result", "send_discord_alert_async",
    }
    requests = []
    notifier = _load("src/utils/discord_notifier.py", notifier_names, {
        "dataclass": dataclass, "json": json, "math": math, "re": re, "urllib": urllib,
        "threading": SimpleNamespace(Thread=InlineThread), "_SEND_SLOTS": threading.BoundedSemaphore(4),
        "time": SimpleNamespace(sleep=Mock()),
        "logger": logging.getLogger("window-control-notifier-test"),
    })
    delivery = _load("src/utils/notification_delivery.py", {
        "DeliveryTicket", "_DeliveryEntry", "DiscordDeliveryState",
    }, {
        **notifier, "dataclass": dataclass, "hashlib": hashlib, "threading": threading,
        "time": SimpleNamespace(monotonic=lambda: clock.now),
    })
    method_names = {
        "_mini_notification_config", "_mini_notification_enabled", "_mini_send_discord_alert",
        "_mini_is_current_playback_generation", "_mini_check_notification_watchdog",
        "_mini_record_notification_progress", "_mini_format_notification_progress",
        "_mini_build_notification_snapshot", "_mini_record_game_mode_notification_activity",
    }
    main = _load("src/ui/main_window.py", method_names, {
        **notifier, "DiscordDeliveryState": delivery["DiscordDeliveryState"],
        "time": SimpleNamespace(monotonic=lambda: clock.now), "re": re,
        "truncate_ui_text": lambda text, limit: text[:limit],
        "logger": logging.getLogger("window-control-watchdog-test"),
    }, owner="MainWindow")
    main_window = SimpleNamespace(**main)
    main_window.MainWindow = type("MainWindowHarness", (), {name: main[name] for name in method_names})

    def config():
        return SimpleNamespace(system=SimpleNamespace(pc_number=""), notification=SimpleNamespace(
            discord_enabled=False, discord_webhook_url="", discord_notify_on_stuck=True,
            discord_stuck_seconds=180, discord_cooldown_seconds=300,
        ))

    previous = _load("tests/test_discord_delivery_regression.py", {"URL", "RECEIPT", "Response", "context"}, {
        "json": json, "SimpleNamespace": SimpleNamespace, "AppConfig": config,
        "DiscordDeliveryState": delivery["DiscordDeliveryState"], "main_window": main_window,
    })
    now = [clock.now]
    ctx, _ = previous["context"](monkeypatch, now)
    main["get_config"] = main_window.get_config
    for name in ("_mini_format_notification_progress", "_mini_build_notification_snapshot",
                 "_mini_record_game_mode_notification_activity"):
        setattr(ctx, name, getattr(main_window.MainWindow, name).__get__(ctx))
    ctx._mini_notification_last_progress_at = clock.now
    ctx._mini_build_stuck_diagnostic_fields = lambda elapsed, threshold: (("action", "window_control waiting"),)
    ctx._mini_start_notification_watchdog = Mock()

    def http(request, timeout):
        requests.append((clock.now, request))
        return previous["Response"](http_status, None if http_status == 200 else b"")

    # Functions keep their original globals, so wire the HTTP boundary there.
    notifier["_open_discord_request"] = http
    return SimpleNamespace(ctx=ctx, main=main, now=now, requests=requests)


@pytest.mark.parametrize("status,message", WAITING)
def test_waiting_window_reaches_180_second_watchdog_and_mocked_http(monkeypatch, status, message):
    clock = VirtualClock(start=1000.0)
    watchdog = _watchdog(monkeypatch, clock)
    runner = _runner(clock)
    controller = ScriptedController(clock, _result(status, message))

    def run(config, **kwargs):
        return runner["wait_for_window_control"](config, controller=controller, **kwargs)

    host, namespace = _executor(clock, run)
    host._on_progress = Mock(side_effect=lambda progress:
                             watchdog.main["_mini_record_notification_progress"](watchdog.ctx, progress))

    def tick():
        watchdog.now[0] = clock.now
        elapsed = round(clock.now - 1000, 6)
        if elapsed < 180:
            assert not watchdog.requests
        if elapsed in {30, 60, 90, 120, 150, 179.9, 180, 180.1}:
            watchdog.main["_mini_check_notification_watchdog"](watchdog.ctx, 1)
        if elapsed >= 180.1:
            clock.stopped = True

    clock.on_wait = tick
    result = host._execute_fixed_action(_rule(), datetime.now())

    assert result.success is False and len(controller.steps) == 361
    host._on_progress.assert_not_called()
    namespace["get_input_controller"].assert_not_called()
    assert namespace["logger"].info.call_count > 1, "Waiting must still produce diagnostic logs"
    assert watchdog.ctx._mini_notification_last_progress_at == 1000
    assert len(watchdog.requests) == 1
    sent_at, request = watchdog.requests[0]
    assert sent_at == 1180
    assert urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)["wait"] == ["true"]
    payload = json.loads(request.data.decode("utf-8"))
    assert request.method == "POST" and "window_control waiting" in payload["content"]
    assert payload["content"].splitlines()[0] == "PC\ubc88\ud638: 7\ubc88"
    assert watchdog.ctx._mini_notification_last_sent_at == {"stuck": 1180}
    assert watchdog.ctx._is_running and not watchdog.ctx._mini_stop_requested


def test_window_watchdog_does_not_treat_empty_http_response_as_delivery(monkeypatch):
    clock = VirtualClock(start=1180)
    watchdog = _watchdog(monkeypatch, clock, http_status=204)
    watchdog.ctx._mini_notification_last_progress_at = 1000
    watchdog.main["_mini_check_notification_watchdog"](watchdog.ctx, 1)
    assert len(watchdog.requests) == 3
    assert not watchdog.ctx._mini_notification_last_sent_at
    assert watchdog.ctx._is_running
