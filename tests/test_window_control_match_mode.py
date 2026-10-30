"""Selector-mode regression simulations and temporary-document roundtrips."""

from copy import deepcopy
from dataclasses import replace
from unittest.mock import Mock

import pytest

from src.analyzer.automation_models import AutomationPlan, AutomationRule
from src.database.models import Action, Sequence
from src.utils import window_control as wc
from src.utils.json_utils import dump_json_file, load_json_file
from tests.test_window_control import FakeBackend, window
from tests.test_window_control_persistence import _manager_at, _assert_sequence_saved


PROCESS_CONFIG = {
    "executable_name": "msedge.exe", "title_contains": "", "operation": "maximize",
    "match_mode": "executable",
}


@pytest.fixture(autouse=True)
def no_native_backend(monkeypatch):
    monkeypatch.setattr(wc, "_WindowsBackend", Mock(side_effect=AssertionError("Simulation only")))


def test_old_config_is_not_migrated_or_silently_broadened():
    original = {"executable_name": "msedge.exe", "title_contains": "Original title", "operation": "maximize"}
    assert wc.normalize_window_control_config(original) == original
    assert "match_mode" not in wc.normalize_window_control_config(original)
    backend = FakeBackend([window(executable_name="msedge.exe", title="Event page")])
    assert wc.WindowController(backend).step(original).status == "missing"
    assert not backend.calls
    with pytest.raises(ValueError):
        wc.validate_window_control_config({**original, "title_contains": ""})


@pytest.mark.parametrize("mode", ["", "unknown", "executable_only", None, False, 123])
def test_invalid_mode_never_falls_back_to_executable_matching(mode):
    backend = FakeBackend([window(executable_name="msedge.exe")])
    with pytest.raises(ValueError):
        wc.WindowController(backend).step({**PROCESS_CONFIG, "match_mode": mode})
    assert not backend.calls and backend.snapshots == 0


def test_mode_normalization_has_no_transient_identifiers_or_aliasing():
    raw = {**PROCESS_CONFIG, "match_mode": " EXECUTABLE ", "handle": 42, "process_id": 5}
    before = deepcopy(raw)
    assert wc.normalize_window_control_config(raw) == PROCESS_CONFIG
    assert raw == before
    assert wc.validate_window_control_config(PROCESS_CONFIG) == PROCESS_CONFIG
    assert "제목 무관" in wc.format_window_control_summary(PROCESS_CONFIG)
    with pytest.raises(ValueError):
        wc.validate_window_control_config({**PROCESS_CONFIG, "executable_name": ""})
    with pytest.raises(ValueError):
        wc.validate_window_control_config({**PROCESS_CONFIG, "title_contains": "bad\x00value"})


@pytest.mark.parametrize("caption", ["바람의나라", "대규모 업데이트", "이벤트 안내", "바람의나라 및 다른 페이지"])
def test_executable_mode_accepts_page_title_changes_and_ignores_old_draft(caption):
    backend = FakeBackend([window(executable_name="MSEdge.EXE", title=caption, maximized=True)])
    backend.foreground = 101
    config = {**PROCESS_CONFIG, "title_contains": "Old unrelated homepage title"}
    assert wc.WindowController(backend).step(config).ready
    assert not backend.calls


def test_executable_mode_still_rejects_other_programs_and_duplicate_windows():
    backend = FakeBackend([window(executable_name="chrome.exe", title="바람의나라")])
    controller = wc.WindowController(backend)
    assert controller.step(PROCESS_CONFIG).status == "missing"
    backend.windows = [
        window(executable_name="msedge.exe", title="Home"),
        window(handle=202, executable_name="msedge.exe", title="Event"),
    ]
    backend.foreground = 101
    assert controller.step(PROCESS_CONFIG).status == "ambiguous"
    assert not backend.calls
    backend.windows = [replace(backend.windows[1], maximized=True)]
    backend.foreground = 202
    assert controller.step(PROCESS_CONFIG).ready


def test_redirect_during_snapshot_is_retried_without_losing_process_mode():
    backend = FakeBackend([window(executable_name="msedge.exe", title="Home", maximized=True)])
    backend.foreground = 101

    def redirect(fake):
        if fake.snapshots == 2:
            fake.windows = [replace(fake.windows[0], title="Event page")]

    backend.on_list = redirect
    controller = wc.WindowController(backend)
    assert controller.step(PROCESS_CONFIG).status == "pending"
    assert not backend.calls
    assert controller.step(PROCESS_CONFIG).ready


@pytest.mark.parametrize("mode", ["title", "executable"])
def test_mode_survives_actual_plan_write_reload_and_deepcopy(mode, tmp_path):
    config = {**PROCESS_CONFIG, "match_mode": mode, "title_contains": "Home" if mode == "title" else ""}
    child = AutomationRule(rule_id="window", action_type="window_control", window_control_config=config)
    parent = AutomationRule(rule_id="parent", action_type="hotkey", children=[child])
    plan = AutomationPlan(plan_id="selector-mode", initial_rules=[parent])
    expected = plan.to_dict()
    path = tmp_path / "plan.json"
    dump_json_file(path, expected)
    assert load_json_file(path) == expected
    restored = AutomationPlan.from_dict(load_json_file(path), templates_dir=tmp_path)
    assert restored.to_dict() == expected
    assert restored.initial_rules[0].children[0].window_control_config == config
    clone = deepcopy(restored.initial_rules[0].children[0])
    clone.window_control_config["match_mode"] = "other"
    assert restored.initial_rules[0].children[0].window_control_config == config


def test_executable_mode_survives_sqlite_save_reopen_and_switch_back(tmp_path):
    path = tmp_path / "mode.db"
    db = _manager_at(path, create=True)
    action = Action(action_type="window_control", window_control_config=dict(PROCESS_CONFIG))
    sequence = Sequence(name="Selector mode", actions=[action])
    sequence.id = db.create_sequence(sequence)
    restored = _assert_sequence_saved(path, sequence)
    assert restored.actions[0].window_control_config == PROCESS_CONFIG
    restored.actions[0].window_control_config = {
        "executable_name": "msedge.exe", "title_contains": "Home", "operation": "maximize",
    }
    assert db.update_sequence(restored)
    reloaded = _assert_sequence_saved(path, restored)
    assert "match_mode" not in reloaded.actions[0].window_control_config
    assert reloaded.actions[0].window_control_config["title_contains"] == "Home"
