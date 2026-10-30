"""Ordered fallback simulations and real temporary JSON/SQLite persistence."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.analyzer.automation_models import AutomationPlan, AutomationRule
from src.database.models import Action, Sequence
from src.player import window_control_runtime as runtime
from src.utils import window_control as wc
from src.utils.json_utils import dump_json_file, load_json_file
from tests.test_window_control import FakeBackend, window
from tests.test_window_control_persistence import _assert_sequence_saved, _manager_at
from tests.test_window_control_runtime import VirtualClock


FIRST = {"executable_name": "msedge.exe", "title_contains": "", "match_mode": "executable"}
SECOND = {"executable_name": "mudrun_tauri.exe", "title_contains": "Mudfish"}
SINGLE = {"executable_name": "single.exe", "title_contains": "single", "operation": "maximize"}
MULTI = {**SINGLE, "multi_enabled": True, "targets": [FIRST, SECOND]}


@pytest.fixture(autouse=True)
def no_native_access(monkeypatch):
    monkeypatch.setattr(wc, "_WindowsBackend", Mock(side_effect=AssertionError("Simulation only")))
    monkeypatch.setattr(runtime, "WindowController", Mock(side_effect=AssertionError("Inject the controller")))


class Candidates:
    def __init__(self, clock, statuses):
        self.clock, self.statuses, self.calls = clock, statuses, []
        self.on_step = lambda config: None

    def step(self, config):
        self.calls.append((self.clock.now, deepcopy(config)))
        self.on_step(config)
        value = self.statuses[config["executable_name"]]
        status = value(self.clock.now) if callable(value) else value
        return wc.WindowControlResult(status, status)


def run(monkeypatch, config, statuses, *, clock=None, on_step=None, **kwargs):
    clock = clock or VirtualClock()
    controller = Candidates(clock, statuses)
    if on_step is not None:
        controller.on_step = on_step
    monkeypatch.setattr(runtime, "time", SimpleNamespace(monotonic=lambda: clock.now))
    result = runtime.wait_for_window_control(
        deepcopy(config), should_stop=lambda: clock.stopped, is_paused=lambda: clock.paused,
        wait=clock.wait, controller=controller, **kwargs,
    )
    return result, clock, controller


def names(controller):
    return [config["executable_name"] for _, config in controller.calls]


def test_normalization_detaches_candidates_and_retains_only_persistent_selector_fields():
    raw = deepcopy(MULTI)
    raw["targets"][0].update(handle=123, process_id=7, operation="close", targets=[{}])
    before = deepcopy(raw)
    normalized = wc.validate_window_control_config(raw)
    assert normalized == MULTI and raw == before
    normalized["targets"][0]["executable_name"] = "changed.exe"
    assert raw == before
    assert "멀티 2개" in wc.format_window_control_summary(MULTI)
    assert wc.format_window_control_summary(MULTI) == "최대화 | 멀티 2개"
    assert wc.normalize_window_control_config(SINGLE) == SINGLE


@pytest.mark.parametrize("enabled", [None, 0, 1, "true", [], {}])
def test_invalid_multi_toggle_never_enables_or_silently_disables_it(enabled):
    backend = FakeBackend()
    with pytest.raises(ValueError):
        wc.WindowController(backend).step({**MULTI, "multi_enabled": enabled})
    assert backend.snapshots == 0 and not backend.calls


@pytest.mark.parametrize("targets", [None, {}, "app.exe", [None], ["app.exe"], [], [FIRST] * 21])
def test_invalid_candidate_container_fails_before_any_backend_access(targets):
    backend = FakeBackend()
    with pytest.raises(ValueError):
        wc.WindowController(backend).step({**MULTI, "targets": targets})
    assert backend.snapshots == 0 and not backend.calls


@pytest.mark.parametrize("bad", [
    {"executable_name": ""}, {"executable_name": "C:/edge.exe"},
    {"executable_name": "bad.exe", "title_contains": ""},
    {**FIRST, "match_mode": "unknown"}, {**FIRST, "title_contains": "bad\x00title"},
])
def test_every_candidate_is_validated_even_if_an_earlier_one_would_match(bad):
    backend = FakeBackend([window(executable_name="msedge.exe", maximized=True)])
    with pytest.raises(ValueError, match="후보 2"):
        wc.WindowController(backend).step({**MULTI, "targets": [FIRST, bad]})
    assert backend.snapshots == 0 and not backend.calls


def test_multi_only_validates_active_selectors_but_off_restores_the_original_single_target():
    active = {**MULTI, "executable_name": "", "title_contains": ""}
    assert wc.validate_window_control_config(active)["targets"] == MULTI["targets"]
    with pytest.raises(ValueError):
        wc.validate_window_control_config({**active, "multi_enabled": False})
    disabled = {**MULTI, "multi_enabled": False, "targets": [{"executable_name": "", "title_contains": ""}]}
    assert wc.validate_window_control_config(disabled) == disabled
    assert wc.format_window_control_summary(disabled) == wc.format_window_control_summary(SINGLE)
    backend = FakeBackend([window(executable_name="single.exe", title="single", maximized=True)])
    backend.foreground = 101
    assert wc.WindowController(backend).step(disabled).ready
    assert not backend.calls


@pytest.mark.parametrize("operation", ["maximize", "minimize", "foreground"])
def test_missing_first_candidate_immediately_probes_second_without_wait(monkeypatch, operation):
    result, clock, controller = run(monkeypatch, {**MULTI, "operation": operation}, {
        "msedge.exe": "missing", "mudrun_tauri.exe": "ready",
    }, skip_on_not_found=True, skip_timeout=0)
    assert result[0] is True and "후보 2/2" in result[1] and "스킵됨" not in result[1]
    assert names(controller) == ["msedge.exe", "mudrun_tauri.exe"]
    assert all(config["operation"] == operation for _, config in controller.calls)
    assert clock.now == 0 and not clock.waits


def test_if_both_exist_only_the_first_is_controlled():
    first = window(executable_name="msedge.exe")
    second = window(handle=202, process_id=43, executable_name="mudrun_tauri.exe", title="Mudfish")
    backend = FakeBackend([first, second])
    backend.foreground, backend.apply_show = 101, True
    result = wc.WindowController(backend).step(MULTI)
    assert result.ready and result.target_index == 0
    assert backend.calls == [("maximize", 101)]
    assert backend.windows[1] == second


def test_real_controller_falls_back_and_verifies_only_the_existing_second_window():
    backend = FakeBackend([window(executable_name="mudrun_tauri.exe", title="Mudfish")])
    backend.foreground, backend.apply_show = 101, True
    result = wc.WindowController(backend).step(MULTI)
    assert result.ready and result.target_index == 1
    assert backend.calls == [("maximize", 101)]


def test_duplicate_first_candidate_windows_are_not_absence_or_permission_to_touch_second():
    backend = FakeBackend([
        window(executable_name="msedge.exe"), window(handle=102, executable_name="msedge.exe"),
        window(handle=202, executable_name="mudrun_tauri.exe", title="Mudfish"),
    ])
    result = wc.WindowController(backend).step(MULTI)
    assert result.status == "ambiguous" and result.target_index == 0 and not backend.calls


@pytest.mark.parametrize("first_status", ["ambiguous", "pending", "unsupported"])
def test_existing_but_unready_first_candidate_never_falls_back_or_skips(monkeypatch, first_status):
    clock = VirtualClock()
    clock.on_wait = lambda: setattr(clock, "stopped", clock.now >= 1.1)
    result, _, controller = run(monkeypatch, MULTI, {
        "msedge.exe": first_status, "mudrun_tauri.exe": "ready",
    }, clock=clock, skip_on_not_found=True, skip_timeout=0)
    assert result[0] is False and "스킵됨" not in result[1]
    assert set(names(controller)) == {"msedge.exe"}


@pytest.mark.parametrize("timeout,finished", [(0, 0), (1.5, 1.5), (0.55, 1.0)])
def test_skip_checks_all_candidates_with_one_shared_deadline(monkeypatch, timeout, finished):
    result, clock, controller = run(monkeypatch, MULTI, {
        "msedge.exe": "missing", "mudrun_tauri.exe": "missing",
    }, skip_on_not_found=True, skip_timeout=timeout)
    assert result[0] is True and "스킵됨" in result[1]
    assert clock.now == finished
    assert names(controller) == ["msedge.exe", "mudrun_tauri.exe"] * (round(finished / 0.5) + 1)
    assert [at for at, _ in controller.calls][::2] == [at for at, _ in controller.calls][1::2]


def test_skip_off_keeps_searching_the_whole_list_until_stop(monkeypatch):
    clock = VirtualClock()
    clock.on_wait = lambda: setattr(clock, "stopped", clock.now >= 1.1)
    result, _, controller = run(monkeypatch, MULTI, {
        "msedge.exe": "missing", "mudrun_tauri.exe": "missing",
    }, clock=clock, skip_on_not_found=False)
    assert result == (False, "실행 중지됨")
    assert names(controller) == ["msedge.exe", "mudrun_tauri.exe"] * 3


def test_second_candidate_arriving_before_deadline_completes_without_skip(monkeypatch):
    result, clock, controller = run(monkeypatch, MULTI, {
        "msedge.exe": "missing", "mudrun_tauri.exe": lambda at: "ready" if at >= 1 else "missing",
    }, skip_on_not_found=True, skip_timeout=1.5)
    assert result[0] is True and "후보 2/2" in result[1] and "스킵됨" not in result[1]
    assert clock.now == 1 and len(controller.calls) == 6


def test_selected_second_target_is_not_stolen_by_a_newly_appearing_first_target(monkeypatch):
    result, clock, controller = run(monkeypatch, MULTI, {
        "msedge.exe": lambda at: "missing" if at == 0 else "ready",
        "mudrun_tauri.exe": lambda at: "pending" if at == 0 else "ready",
    })
    assert result[0] is True and "후보 2/2" in result[1] and clock.now == 0.5
    assert names(controller) == ["msedge.exe", "mudrun_tauri.exe", "mudrun_tauri.exe"]


def test_a_closed_selected_target_can_immediately_fall_back_again(monkeypatch):
    result, clock, controller = run(monkeypatch, MULTI, {
        "msedge.exe": lambda at: "missing" if at == 0 else "ready",
        "mudrun_tauri.exe": lambda at: "pending" if at == 0 else "missing",
    })
    assert result[0] is True and "후보 1/2" in result[1] and clock.now == 0.5
    assert names(controller) == ["msedge.exe", "mudrun_tauri.exe", "mudrun_tauri.exe", "msedge.exe"]


def test_stop_between_candidates_does_not_touch_the_next_one(monkeypatch):
    clock = VirtualClock()
    result, _, controller = run(monkeypatch, MULTI, {
        "msedge.exe": "missing", "mudrun_tauri.exe": "ready",
    }, clock=clock, on_step=lambda _: setattr(clock, "stopped", True))
    assert result == (False, "실행 중지됨") and names(controller) == ["msedge.exe"]


def test_pause_between_candidates_never_skips_a_partially_scanned_list(monkeypatch):
    clock = VirtualClock()
    clock.on_wait = lambda: setattr(clock, "paused", clock.now < 0.3)
    calls = []
    def pause_once(config):
        calls.append(config)
        if len(calls) == 1:
            clock.paused = True
    result, _, controller = run(monkeypatch, MULTI, {
        "msedge.exe": "missing", "mudrun_tauri.exe": "ready",
    }, clock=clock, on_step=pause_once, skip_on_not_found=True, skip_timeout=0)
    assert result[0] is True and "스킵됨" not in result[1] and clock.now == 0.3
    assert names(controller) == ["msedge.exe", "msedge.exe", "mudrun_tauri.exe"]


def test_disabled_multi_keeps_the_existing_single_runtime_path(monkeypatch):
    config = {**MULTI, "multi_enabled": False}
    result, clock, controller = run(monkeypatch, config, {"single.exe": "ready"})
    assert result == (True, "ready") and names(controller) == ["single.exe"]
    assert controller.calls[0][1] == config and not clock.waits


def test_disabled_multi_skips_only_a_missing_single_target_even_if_a_dormant_candidate_exists(monkeypatch):
    result, clock, controller = run(monkeypatch, {**MULTI, "multi_enabled": False}, {
        "single.exe": "missing", "msedge.exe": "ready", "mudrun_tauri.exe": "ready",
    }, skip_on_not_found=True, skip_timeout=0)
    assert result[0] is True and "스킵됨" in result[1]
    assert names(controller) == ["single.exe"] and not clock.waits


def test_existing_candidate_resets_the_shared_absence_timer(monkeypatch):
    result, clock, controller = run(monkeypatch, MULTI, {
        "msedge.exe": "missing",
        "mudrun_tauri.exe": lambda at: "pending" if at == 0.5 else "missing",
    }, skip_on_not_found=True, skip_timeout=1)
    assert result[0] is True and "스킵됨" in result[1]
    assert clock.now == 2.0
    assert any(at == 0.5 and value["executable_name"] == "mudrun_tauri.exe" for at, value in controller.calls)


def test_pause_does_not_consume_the_shared_missing_deadline(monkeypatch):
    clock = VirtualClock()
    paused = [False]
    def pause_and_resume():
        if not paused[0] and clock.now >= 0.2:
            paused[0], clock.paused = True, True
        if paused[0] and clock.now >= 0.7:
            clock.paused = False
    clock.on_wait = pause_and_resume
    result, _, controller = run(monkeypatch, MULTI, {
        "msedge.exe": "missing", "mudrun_tauri.exe": "missing",
    }, clock=clock, skip_on_not_found=True, skip_timeout=0.7)
    assert result[0] is True and "스킵됨" in result[1] and clock.now >= 1.2
    assert not any(0.2 <= at < 0.7 for at, _ in controller.calls)


@pytest.mark.parametrize("enabled", [False, True])
def test_multi_json_and_real_sqlite_reopen_preserve_order_and_disabled_drafts(tmp_path, enabled):
    config = {**deepcopy(MULTI), "multi_enabled": enabled}
    child = AutomationRule(rule_id="child", action_type="window_control", window_control_config=config)
    parent = AutomationRule(rule_id="parent", action_type="hotkey", children=[child])
    plan = AutomationPlan(plan_id="multi", initial_rules=[parent])
    path = tmp_path / "plan.json"
    dump_json_file(path, plan.to_dict())
    restored = AutomationPlan.from_dict(load_json_file(path), templates_dir=tmp_path)
    assert restored.to_dict() == plan.to_dict()
    assert restored.initial_rules[0].children[0].window_control_config == config
    clone = deepcopy(child)
    clone.window_control_config["targets"].reverse()
    assert child.window_control_config == config
    exported = child.to_dict()
    exported["window_control_config"]["targets"][0]["executable_name"] = "changed.exe"
    assert child.window_control_config == config
    db_path = tmp_path / "multi.db"
    db = _manager_at(db_path, create=True)
    action = Action(action_type="window_control", window_control_config=config)
    sequence = Sequence(name="multi", actions=[Action(action_type="hotkey", children=[action])])
    sequence.id = db.create_sequence(sequence)
    reopened = _assert_sequence_saved(db_path, sequence)
    assert reopened.actions[0].children[0].window_control_config == config
    action.window_control_config["targets"].reverse()
    assert db.update_sequence(sequence)
    reopened = _assert_sequence_saved(db_path, sequence)
    assert reopened.actions[0].children[0].window_control_config == action.window_control_config
