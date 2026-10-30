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


def test_paused_wait_reporting_cancellation_stops_without_a_controller_step():
    clock = VirtualClock()
    waits, controller = Mock(return_value=True), Mock()
    ok, message = _runner(clock)["wait_for_window_control"](
        WINDOW_CONFIG, should_stop=lambda: False, is_paused=lambda: True,
        wait=waits, controller=controller,
    )
    assert ok is False and message
    waits.assert_called_once_with(0.1)
    controller.step.assert_not_called()


def test_runner_passes_validated_normalized_config_to_controller():
    clock = VirtualClock()
    controller = ScriptedController(clock, _result("ready"))
    config = {"executable_name": " client.exe ", "title_contains": " Test client ", "operation": " MAXIMIZE "}
    assert _runner(clock)["wait_for_window_control"](
        config, should_stop=lambda: False, is_paused=lambda: False,
        wait=clock.wait, controller=controller,
    )[0] is True
    assert controller.steps == [(0.0, WINDOW_CONFIG)]
    assert config["operation"] == " MAXIMIZE ", "Validation must not rewrite the source model"


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


def test_window_target_images_are_excluded_without_skipping_window_action_in_lookahead():
    names = {"_target_images_for_rule", "_next_runtime_rule"}
    namespace = _load(EXECUTOR, names, {"WINDOW_CONTROL_ACTION_TYPE": "window_control"}, owner="RuleExecutor")
    host = type("LookaheadHarness", (), {name: namespace[name] for name in names})()
    host._should_skip_runtime_action = lambda rule: getattr(rule, "skip", False)
    window = _rule()
    window.target_image = "stray-primary.png"
    window.target_images = ["stray-multi.png"]
    following = SimpleNamespace(action_type="click", target_image="following.png")
    skipped = SimpleNamespace(action_type="click", skip=True)
    assert host._next_runtime_rule([skipped, window, following], 0) is window
    assert host._target_images_for_rule(window) == []
    assert window.target_image == "stray-primary.png" and window.target_images == ["stray-multi.png"]


@pytest.mark.parametrize("action_type", ["click", "double_click", "wait_for_image"])
def test_ordinary_target_images_keep_primary_order_and_deduplication(action_type):
    namespace = _load(EXECUTOR, {"_target_images_for_rule"},
                      {"WINDOW_CONTROL_ACTION_TYPE": "window_control"}, owner="RuleExecutor")
    helper = namespace["_target_images_for_rule"].__func__
    rule = SimpleNamespace(action_type=action_type, target_image=Path("primary.png"),
                           target_images=["primary.png", "alternate.png", "", None, "alternate.png"])
    before = copy.deepcopy(rule)
    assert helper(rule) == ["primary.png", "alternate.png"]
    assert rule == before


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
    main_window = SimpleNamespace(**main, get_config=lambda: None)
    main_window.MainWindow = type("MainWindowHarness", (), {name: main[name] for name in method_names})

    def config():
        return SimpleNamespace(system=SimpleNamespace(pc_number=""), notification=SimpleNamespace(
            discord_enabled=False, discord_webhook_url="", discord_notify_on_stuck=True,
            discord_stuck_seconds=180, discord_cooldown_seconds=300,
        ))

    main_window.get_config = config
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


def _review_rule(rule_id="window", action_type="window_control", **overrides):
    fields = dict(
        rule_id=rule_id, action_type=action_type, rule_type="monitor", description=rule_id,
        target_image=None, target_images=[], action_keys=[], action_text=None,
        trigger_image="stale-trigger.png" if action_type == "window_control" else None,
        stop_playlist_on_trigger_missing=True, rewind_previous_on_trigger_missing=True,
        trigger_missing_keys=["enter"], trigger_missing_rewind_keys=["escape"],
        window_control_config=dict(WINDOW_CONFIG), is_monitoring_mode=False,
        monitoring_watches=[], children=[], enabled=True, wait_after=0,
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _review_executor(clock, runner):
    host, namespace = _executor(clock, runner)
    names = {"_execute_rule", "_handle_trigger_gate", "_execute_rule_tree_once", "_execution_loop", "_safe_int"}
    namespace.update({
        "_CYAN": "", "_GREEN": "", "_YELLOW": "", "_RED": "", "_RESET": "",
        "ExecutionState": SimpleNamespace(
            RUNNING_INITIAL="running", MONITORING="monitoring", COMPLETED="completed", FAILED="failed",
        ),
        "Path": Mock(side_effect=AssertionError("Window control must not inspect image files")),
        "block_automation_input": Mock(side_effect=AssertionError("Unexpected input-safety mutation")),
    })
    _load(EXECUTOR, names, namespace, owner="RuleExecutor")
    for name in names:
        setattr(host, name, namespace[name].__get__(host))
    host._current_step_num = "previous-step"
    host._results = []
    host._on_rule_executed = Mock()
    host._on_error = Mock()
    host._should_skip_runtime_action = lambda rule: False
    host._is_runtime_action_enabled = lambda rule: getattr(rule, "enabled", True)
    host._rule_repeats_child_actions = lambda rule: False
    host._wait_after_rule_result = Mock(return_value=None)
    for name in (
        "_execute_monitoring_mode", "_wait_for_trigger", "_capture_trigger_input_window",
        "_execute_trigger_missing_keys", "_prepare_for_click_after_trigger", "_wait_for_image",
        "_find_image_on_screen", "_click_at",
    ):
        setattr(host, name, Mock(side_effect=AssertionError(f"Unexpected route: {name}")))

    dispatched = []

    def execute_once(rule, *args, **kwargs):
        dispatched.append(rule.rule_id)
        gate = host._handle_trigger_gate(rule, datetime.now(), kwargs.get("step_num", ""))
        if gate is not None:
            return gate
        if rule.action_type == "window_control":
            return host._execute_rule(rule, kwargs.get("step_num", ""))
        return host._make_result(rule, True, "ordinary child completed", datetime.now())

    host._execute_rule_with_retry = Mock(side_effect=execute_once)
    return host, namespace, dispatched


def test_window_trigger_gate_returns_before_file_lookup_or_pretrigger_input():
    host, namespace, _ = _review_executor(VirtualClock(), Mock())
    rule = _review_rule(is_monitoring_mode=True, monitoring_watches=[{"image": "stale-watch.png"}])
    before = copy.deepcopy(rule)

    assert host._handle_trigger_gate(rule, datetime.now(), "2-1", can_rewind_previous=True) is None

    assert rule == before
    namespace["Path"].assert_not_called()
    namespace["get_input_controller"].assert_not_called()
    namespace["wait_for_window_control"].assert_not_called()
    host._on_progress.assert_not_called()
    for name in ("_wait_for_trigger", "_capture_trigger_input_window", "_execute_trigger_missing_keys",
                 "_prepare_for_click_after_trigger"):
        getattr(host, name).assert_not_called()


@pytest.mark.parametrize("rule_type", ["monitor", "wait_for_image", "wait_for_disappear", "hotkey", "unknown"])
def test_window_action_dispatch_precedes_rule_type_and_stale_monitor_flags(rule_type):
    runner = Mock(return_value=(True, "verified window"))
    host, namespace, _ = _review_executor(VirtualClock(), runner)
    rule = _review_rule(rule_type=rule_type, is_monitoring_mode=True,
                        monitoring_watches=[{"image": "stale-watch.png"}])
    result = host._execute_rule(rule, "3-2")
    assert result.success is True and result.message == "verified window"
    assert result.rule_id == rule.rule_id
    runner.assert_called_once()
    assert runner.call_args.args == (WINDOW_CONFIG,)
    namespace["get_input_controller"].assert_not_called()
    namespace["Path"].assert_not_called()
    for name in ("_execute_monitoring_mode", "_wait_for_image", "_find_image_on_screen", "_click_at"):
        getattr(host, name).assert_not_called()


@pytest.mark.parametrize("entrypoint", ["tree", "loop"])
@pytest.mark.parametrize("monitor_flag,watches", [
    (True, []), (False, [{"image": "stale-watch.png"}]), (True, [{"image": "stale-watch.png"}]),
])
@pytest.mark.parametrize("ready", [True, False])
def test_window_monitor_flags_do_not_hide_controller_or_mark_progress_as_monitoring(
    entrypoint, monitor_flag, watches, ready,
):
    clock = VirtualClock()
    controller = ScriptedController(clock, _result("ready" if ready else "unsupported", "controller result"))
    runner = _runner(clock)

    def run(config, **kwargs):
        return runner["wait_for_window_control"](config, controller=controller, **kwargs)

    host, namespace, dispatched = _review_executor(clock, run)
    child = _review_rule("following-child", "key_press", rule_type="fixed_sequence")
    window = _review_rule(is_monitoring_mode=monitor_flag, monitoring_watches=copy.deepcopy(watches), children=[child])
    progress = []
    host._on_progress = Mock(side_effect=lambda p: progress.append(
        (p.current_rule, p.current_action_is_monitoring)))

    if entrypoint == "tree":
        parent = _review_rule("parent", "click", children=[window])
        result = host._execute_rule_tree_once(parent, "4")
        assert (result is None) is ready
        assert host._current_step_num == "previous-step"
        expected = ["parent", "window"] + (["following-child"] if ready else [])
    else:
        def flatten(rules, prefix=""):
            result = []
            for index, rule in enumerate(rules, 1):
                step = f"{prefix}-{index}" if prefix else str(index)
                result.append((rule, step))
                result.extend(flatten(rule.children, step))
            return result

        host._current_plan = SimpleNamespace(name="window route", initial_rules=[window], monitoring_rules=[])
        host._flatten_rules_with_step = flatten
        host._rule_id_step_map = lambda rules: {rule.rule_id: step for rule, step in flatten(rules)}
        host._format_step_alias = lambda runtime, original: original or runtime
        host._wait_for_resume = Mock(return_value=False)
        host._trigger_missing_rewind_attempts = {}
        host._next_runtime_rule = lambda rules, index: rules[index] if index < len(rules) else None
        host._target_images_for_rule = lambda rule: []
        host._progress.initial_completed = 0
        host._default_wait = 0
        host._input_reset_completed = True
        host._end_shared_image_scan = Mock()
        host._reset_input_state = Mock(side_effect=AssertionError("Unexpected native input reset"))
        host._notify_complete = Mock()
        host._execution_loop()
        host._notify_complete.assert_called_once()
        assert host._notify_complete.call_args.args[0] is ready, host._notify_complete.call_args
        if ready:
            host._on_error.assert_not_called()
        else:
            assert "controller result" in host._notify_complete.call_args.args[1]
        host._end_shared_image_scan.assert_called_once_with()
        host._reset_input_state.assert_not_called()
        expected = ["window"] + (["following-child"] if ready else [])

    assert dispatched == expected
    assert [result.rule_id for result in host._results] == expected
    assert len(controller.steps) == 1 and controller.steps[0][1] == WINDOW_CONFIG
    assert ("window", False) in progress
    assert all(not is_monitoring for _, is_monitoring in progress)
    host._execute_monitoring_mode.assert_not_called()
    host._execute_trigger_missing_keys.assert_not_called()
    host._capture_trigger_input_window.assert_not_called()
    namespace["get_input_controller"].assert_not_called()
    assert not clock.waits


def _legacy_selector():
    namespace = _load("src/player/action_player.py", {"_get_enabled_actions"}, {
        "is_runtime_action_enabled": Mock(side_effect=lambda action, config: getattr(action, "enabled", True)),
        "should_skip_pumpkin_action": Mock(side_effect=lambda action, config: getattr(action, "runtime_skip", False)),
    }, owner="ActionPlayer")
    host = SimpleNamespace(_config=SimpleNamespace(player=object()))
    host._get_enabled_actions = namespace["_get_enabled_actions"].__get__(host)
    return host, namespace


def test_legacy_without_window_control_preserves_top_level_only_selection():
    host, namespace = _legacy_selector()
    child = _action("key_press", "nested-key")
    parent = _action("click", "parent", children=[child])
    disabled = _action("click", "disabled", enabled=False)
    skipped = _action("click", "skipped", runtime_skip=True)
    sequence = SimpleNamespace(actions=[parent, disabled, skipped])
    before = copy.deepcopy(sequence)
    assert host._get_enabled_actions(sequence) == [parent]
    assert sequence == before
    assert all(c.args[1] is host._config.player for c in namespace["is_runtime_action_enabled"].call_args_list)


def test_legacy_selection_keeps_existing_lightweight_actions_without_action_type():
    host, _ = _legacy_selector()
    enabled = SimpleNamespace(description="ordinary", enabled=True)
    disabled = SimpleNamespace(description="disabled", enabled=False)
    skipped = SimpleNamespace(description="skipped", enabled=True, runtime_skip=True)
    assert host._get_enabled_actions(SimpleNamespace(actions=[enabled, disabled, skipped])) == [enabled]


def test_legacy_window_presence_recursively_collects_all_children_in_depth_first_order():
    host, _ = _legacy_selector()
    window_child = _action("key_press", "window-child")
    window = _action(children=[window_child])
    branch = _action("click", "branch", children=[window])
    root = _action("click", "root", children=[branch])
    other_child = _action("hotkey", "other-child")
    other = _action("click", "other", children=[other_child])
    sequence = SimpleNamespace(actions=[root, other])
    before = copy.deepcopy(sequence)
    selected = host._get_enabled_actions(sequence)
    assert selected == [root, branch, window, window_child, other, other_child]
    assert all(actual is expected for actual, expected in zip(
        selected, [root, branch, window, window_child, other, other_child]))
    assert sequence == before


def test_legacy_recursive_selection_filters_disabled_branches_and_deduplicates_shared_nodes():
    host, _ = _legacy_selector()
    window = _action()
    hidden = _action("key_press", "hidden")
    disabled_parent = _action("click", "disabled", enabled=False, children=[hidden])
    kept_child = _action("key_press", "kept-child")
    skip_child = _action("key_press", "skip-child")
    skipped_parent = _action("click", "skipped", runtime_skip=True, children=[skip_child])
    root = _action("click", "root", children=[window, kept_child])
    sequence = SimpleNamespace(actions=[root, disabled_parent, skipped_parent, kept_child, window])
    selected = host._get_enabled_actions(sequence)
    assert selected == [root, window, kept_child, skip_child]
    assert len({id(action) for action in selected}) == 4


def test_disabled_window_presence_enables_recursion_without_executing_its_disabled_branch():
    host, _ = _legacy_selector()
    child = _action("key_press", "ordinary-child")
    parent = _action("click", "ordinary-parent", children=[child])
    hidden = _action("key_press", "hidden-child")
    disabled_window = _action(enabled=False, children=[hidden])
    assert host._get_enabled_actions(SimpleNamespace(actions=[parent, disabled_window])) == [parent, child]


@pytest.mark.parametrize("failed_type", ["window_control", "click"])
def test_legacy_window_failure_finalizes_false_before_any_child_or_following_input(failed_type):
    host, _ = _legacy_selector()
    state = SimpleNamespace(STOPPED="stopped", PAUSED="paused", COMPLETED="completed", FAILED="failed")
    namespace = _load("src/player/action_player.py", {"_play_loop"}, {
        "time": SimpleNamespace(time=lambda: 10.0), "PlayerState": state, "logger": Mock(),
        "effective_action_repeat_count": lambda action, config: 3 if action.action_type == "window_control" else 1,
    }, owner="ActionPlayer")
    nested = _action("key_press", "nested-input")
    first = _action(failed_type, "failed", children=[nested], repeat_delay=0)
    following = _action("key_press", "following-input")
    sequence = SimpleNamespace(actions=[first, following])
    inputs = Mock()

    def execute(action, speed):
        if action is first:
            return False, "window unavailable"
        inputs(action.action_id)
        return True, ""

    host._state = "running"
    host._emergency_stop = SimpleNamespace(is_triggered=False)
    host._progress = SimpleNamespace()
    host._execution_logger = Mock()
    host._execution_log = SimpleNamespace()
    host._is_execution_generation_active = Mock(return_value=True)
    host._on_action_start = Mock()
    host._on_action_complete = Mock()
    host._update_progress = Mock()
    host._execute_action = Mock(side_effect=execute)
    host._finalize_execution = Mock()
    host._reset_input_state = Mock(side_effect=AssertionError("A failed window must not start another repeat"))
    host._wait_for_legacy_stop = Mock(side_effect=AssertionError("Unexpected wait after failed window"))

    namespace["_play_loop"](host, sequence, repeat_count=2 if failed_type == "window_control" else 1,
                            speed_multiplier=1.0, generation=47)

    host._finalize_execution.assert_called_once()
    assert host._finalize_execution.call_args.kwargs == {"generation": 47}
    host._reset_input_state.assert_not_called()
    host._wait_for_legacy_stop.assert_not_called()
    if failed_type == "window_control":
        host._execute_action.assert_called_once_with(first, 1.0)
        host._finalize_execution.assert_called_once_with(False, "window unavailable", generation=47)
        host._on_action_complete.assert_called_once_with(0, first, False)
        inputs.assert_not_called()
    else:
        assert [c.args[0] for c in host._execute_action.call_args_list] == [first, following]
        inputs.assert_called_once_with("following-input")
        assert host._finalize_execution.call_args.args[0] is True, "Keep ordinary legacy failure policy unchanged"
        assert host._execution_log.failed_steps == 1 and host._execution_log.completed_steps == 1
