"""Model-only window-control roundtrips through temporary JSON and SQLite files."""

from contextlib import closing
from copy import deepcopy
import json
import sqlite3

import pytest

from src.analyzer.automation_models import AutomationPlan, AutomationRule
from src.database.db_manager import DatabaseManager
from src.database.models import Action, ActionType, Sequence
from src.utils.json_utils import dump_json_file, load_json_file
from src.utils.window_control import (
    WINDOW_CONTROL_ACTION_TYPE,
    format_window_control_summary,
    validate_window_control_config,
)


MODEL_TYPES = (Action, AutomationRule)
OPERATIONS = ("maximize", "minimize", "foreground")
ORDINARY_TYPES = tuple(
    member.value for member in ActionType if member != ActionType.WINDOW_CONTROL
) + ("game_mode",)
INVALID_CONFIGS = (
    {"executable_name": "notepad.exe", "title_contains": "scratch", "operation": "close"},
    {"executable_name": "notepad.exe", "title_contains": "scratch", "operation": ""},
    {"executable_name": "notepad.exe", "title_contains": "", "operation": "maximize"},
    {"executable_name": "C:\\Windows\\notepad.exe", "title_contains": "scratch", "operation": "maximize"},
    {"executable_name": "", "title_contains": "", "operation": "maximize"},
)


def _config(operation="maximize", title="scratch"):
    return {
        "executable_name": "notepad.exe",
        "title_contains": title,
        "operation": operation,
    }


def _manager_at(path, *, create=False):
    # Never initialize the singleton, whose default path is the user's database.
    manager = object.__new__(DatabaseManager)
    manager._db_path = path
    manager._wal_initialized = False
    if create:
        # _ensure_database also touches the real DATA_DIR; keep setup local here.
        with manager._get_connection() as connection:
            manager._create_tables(connection)
    return manager


def _assert_sequence_saved(path, sequence):
    expected_actions = [action.to_dict() for action in sequence.actions]
    # A separate connection proves the write was committed, not merely applied.
    with closing(sqlite3.connect(path)) as connection:
        row = connection.execute(
            "SELECT actions FROM sequences WHERE id = ?", (sequence.id,)
        ).fetchone()
    assert row is not None
    assert json.loads(row[0]) == expected_actions
    restored = _manager_at(path).get_sequence(sequence.id)
    assert restored is not None
    assert restored.id == sequence.id
    assert restored.name == sequence.name
    assert restored.description == sequence.description
    assert restored.tags == sequence.tags
    assert restored.version == sequence.version
    assert [action.to_dict() for action in restored.actions] == expected_actions
    return restored


def _assert_ordinary_configs_omitted(value):
    if isinstance(value, dict):
        if "action_type" in value and value["action_type"] != WINDOW_CONTROL_ACTION_TYPE:
            assert "window_control_config" not in value
        for child in value.values():
            _assert_ordinary_configs_omitted(child)
    elif isinstance(value, list):
        for child in value:
            _assert_ordinary_configs_omitted(child)


def test_window_control_action_type_matches_shared_utility():
    assert ActionType.WINDOW_CONTROL.value == WINDOW_CONTROL_ACTION_TYPE == "window_control"


@pytest.mark.parametrize("model_type", MODEL_TYPES)
@pytest.mark.parametrize("operation", OPERATIONS)
def test_window_config_and_friendly_summary_roundtrip(model_type, operation, tmp_path):
    config = _config(operation)
    model = model_type(action_type=WINDOW_CONTROL_ACTION_TYPE, window_control_config=config)
    assert model.window_control_config == config
    assert str(model) == format_window_control_summary(config)
    assert "notepad.exe" in str(model)
    saved = model.to_dict()
    assert saved["window_control_config"] == config
    restored = (
        model_type.from_dict(saved, templates_dir=tmp_path)
        if model_type is AutomationRule else model_type.from_dict(saved)
    )
    assert restored.to_dict() == saved
    assert str(restored) == str(model)


@pytest.mark.parametrize("model_type", MODEL_TYPES)
def test_missing_operation_defaults_to_maximize_without_validating_target(model_type):
    model = model_type(
        action_type=WINDOW_CONTROL_ACTION_TYPE,
        window_control_config={"executable_name": "notepad.exe", "title_contains": "scratch"},
    )
    assert model.window_control_config == _config("maximize")
    assert model.to_dict()["window_control_config"] == _config("maximize")
    empty = model_type(action_type=WINDOW_CONTROL_ACTION_TYPE)
    assert empty.window_control_config["operation"] == "maximize"
    assert str(empty) == format_window_control_summary(empty.window_control_config) == "창 제어 설정 필요"
    with pytest.raises(ValueError):
        validate_window_control_config(empty.window_control_config)


@pytest.mark.parametrize("model_type", MODEL_TYPES)
def test_normalization_saves_only_persistent_strings_without_mutating_input(model_type):
    raw = {
        "executable_name": "  notepad.exe  ",
        "title_contains": "  scratch  ",
        "operation": " MINIMIZE ",
        "hwnd": 123,
        "pid": 456,
    }
    original = deepcopy(raw)
    model = model_type(action_type=WINDOW_CONTROL_ACTION_TYPE, window_control_config=raw)
    assert model.window_control_config == _config("minimize")
    assert raw == original
    model.window_control_config = raw
    assert model.to_dict()["window_control_config"] == _config("minimize")
    assert raw == original


@pytest.mark.parametrize("model_type", MODEL_TYPES)
def test_default_config_dictionaries_are_not_shared(model_type):
    first = model_type(action_type="wait")
    second = model_type(action_type="wait")
    first.window_control_config["operation"] = "minimize"
    assert second.window_control_config == {}


@pytest.mark.parametrize("model_type", MODEL_TYPES)
@pytest.mark.parametrize("action_type", ORDINARY_TYPES)
def test_ordinary_action_ignores_and_omits_window_config(model_type, action_type, tmp_path):
    model = model_type(action_type=action_type, window_control_config=_config("minimize"))
    assert model.window_control_config == {}
    expected = deepcopy(model.to_dict())
    assert "window_control_config" not in expected
    # An editor can change the type/config after construction; stale data must not leak.
    model.window_control_config = _config("foreground")
    assert model.to_dict() == expected
    legacy = deepcopy(expected)
    legacy["window_control_config"] = _config("minimize")
    restored = (
        model_type.from_dict(legacy, templates_dir=tmp_path)
        if model_type is AutomationRule else model_type.from_dict(legacy)
    )
    assert restored.window_control_config == {}
    assert restored.to_dict() == expected


@pytest.mark.parametrize("model_type", MODEL_TYPES)
def test_nested_children_and_deepcopy_preserve_independent_configs(model_type, tmp_path):
    child = model_type(
        action_type=WINDOW_CONTROL_ACTION_TYPE, window_control_config=_config("minimize")
    )
    grandchild = model_type(
        action_type=WINDOW_CONTROL_ACTION_TYPE, window_control_config=_config("foreground")
    )
    child.children = [grandchild]
    root = model_type(action_type="wait", children=[child])
    expected = deepcopy(root.to_dict())
    clone = deepcopy(root)
    assert clone.to_dict() == expected
    clone.children[0].window_control_config["operation"] = "maximize"
    clone.children[0].children[0].window_control_config["title_contains"] = "clone only"
    assert root.to_dict() == expected
    restored = (
        model_type.from_dict(expected, templates_dir=tmp_path)
        if model_type is AutomationRule else model_type.from_dict(expected)
    )
    assert restored.to_dict() == expected
    snapshot = restored.to_dict()
    restored.children[0].window_control_config["title_contains"] = "edited"
    assert snapshot == expected
    assert root.to_dict() == expected
    _assert_ordinary_configs_omitted(snapshot)


def test_full_plan_create_edit_save_reload_preserves_all_rules_and_settings(tmp_path):
    grandchild = AutomationRule(
        rule_id="grandchild", parent_id="child", action_type=WINDOW_CONTROL_ACTION_TYPE,
        window_control_config=_config("foreground"), enabled=False,
    )
    child = AutomationRule(
        rule_id="child", parent_id="window", action_type=WINDOW_CONTROL_ACTION_TYPE,
        window_control_config=_config("minimize"), children=[grandchild], repeat_count=3,
        repeat_delay=1.25, repeat_delay_random=True, repeat_delay_random_range=0.15,
    )
    window = AutomationRule(
        rule_id="window", action_type=WINDOW_CONTROL_ACTION_TYPE,
        window_control_config=_config(), children=[child], wait_after=2.0,
    )
    ordinary = AutomationRule(rule_id="ordinary", action_type="hotkey", action_keys=["enter"])
    monitoring = AutomationRule(
        rule_id="monitor", rule_type="monitor", action_type=WINDOW_CONTROL_ACTION_TYPE,
        window_control_config=_config("foreground", title="monitor target"), timeout=12.5,
    )
    plan = AutomationPlan(
        plan_id="plan_window_control_test", name="Window persistence", description="All settings",
        created_at="2026-10-30T12:00:00", initial_rules=[ordinary, window],
        monitoring_rules=[monitoring], user_verified=True, total_repeat_count=5,
    )
    path = tmp_path / "full_plan.json"
    for edited in (False, True):
        if edited:
            window.window_control_config = _config("minimize", title="edited target")
            child.window_control_config["operation"] = "foreground"
            grandchild.window_control_config["operation"] = "maximize"
            monitoring.window_control_config["title_contains"] = "edited monitor"
            ordinary.action_keys = ["esc"]
            plan.modified = True
            plan.total_repeat_count = 7
        expected = deepcopy(plan.to_dict())
        dump_json_file(path, expected)
        saved = load_json_file(path)
        assert saved == expected
        restored = AutomationPlan.from_dict(saved, templates_dir=tmp_path)
        assert restored.to_dict() == expected
        assert restored.initial_rules[1].children[0].children[0].window_control_config == (
            grandchild.window_control_config
        )
        _assert_ordinary_configs_omitted(saved)


def test_ordinary_full_plan_disk_roundtrip_does_not_add_window_fields(tmp_path):
    plan = AutomationPlan(
        plan_id="ordinary_plan", created_at="2026-10-30T12:00:00",
        initial_rules=[AutomationRule(
            rule_id="parent", action_type="hotkey", action_keys=["enter"],
            children=[AutomationRule(rule_id="child", parent_id="parent", action_type="wait")],
        )],
        monitoring_rules=[AutomationRule(rule_id="monitor", action_type="click", action_x=42)],
    )
    expected = deepcopy(plan.to_dict())
    legacy = deepcopy(expected)
    legacy["initial_rules"][0]["window_control_config"] = _config("minimize")
    restored = AutomationPlan.from_dict(legacy, templates_dir=tmp_path)
    path = tmp_path / "ordinary_plan.json"
    dump_json_file(path, restored.to_dict())
    saved = load_json_file(path)
    assert saved == expected
    assert AutomationPlan.from_dict(saved, templates_dir=tmp_path).to_dict() == expected
    _assert_ordinary_configs_omitted(saved)


def test_sequence_create_edit_commit_reload_preserves_nested_actions(tmp_path):
    grandchild = Action(
        action_id="grandchild", parent_id="child", action_type=WINDOW_CONTROL_ACTION_TYPE,
        window_control_config=_config("foreground"), enabled=False,
    )
    child = Action(
        action_id="child", parent_id="window", action_type=WINDOW_CONTROL_ACTION_TYPE,
        window_control_config=_config("minimize"), children=[grandchild], repeat_count=3,
        repeat_delay=1.25, wait_after=2.0,
    )
    window = Action(
        action_id="window", action_type=WINDOW_CONTROL_ACTION_TYPE,
        window_control_config=_config(), children=[child], wait_before=0.75,
    )
    ordinary = Action(action_id="ordinary", action_type="hotkey", keys=["enter"])
    sequence = Sequence(
        name="Window sequence", description="Nested settings", actions=[ordinary, window],
        tags=["window", "persistence"], version="1.1",
    )
    path = tmp_path / "sequence.db"
    manager = _manager_at(path, create=True)
    sequence.id = manager.create_sequence(sequence)
    restored = _assert_sequence_saved(path, sequence)
    _assert_ordinary_configs_omitted(restored.to_export_dict())
    restored.actions[0].keys = ["esc"]
    restored.actions[1].window_control_config = _config("minimize", title="edited target")
    restored.actions[1].children[0].window_control_config["operation"] = "foreground"
    restored.actions[1].children[0].children[0].window_control_config["operation"] = "maximize"
    restored.description = "Edited settings"
    restored.tags.append("edited")
    assert manager.update_sequence(restored)
    reloaded = _assert_sequence_saved(path, restored)
    _assert_ordinary_configs_omitted(reloaded.to_export_dict())


@pytest.mark.parametrize("config", INVALID_CONFIGS)
def test_invalid_config_survives_full_plan_and_sqlite_for_runtime_validation(config, tmp_path):
    rule = AutomationRule(
        rule_id="invalid", action_type=WINDOW_CONTROL_ACTION_TYPE,
        window_control_config=deepcopy(config),
    )
    action = Action(
        action_id="invalid", action_type=WINDOW_CONTROL_ACTION_TYPE,
        window_control_config=deepcopy(config),
    )
    for model in (rule, action):
        assert model.window_control_config == config
        assert model.to_dict()["window_control_config"] == config
        assert str(model) == format_window_control_summary(model.window_control_config) == "창 제어 설정 필요"
        with pytest.raises(ValueError):
            validate_window_control_config(model.window_control_config)
    plan = AutomationPlan(plan_id="invalid_plan", initial_rules=[rule])
    expected = deepcopy(plan.to_dict())
    path = tmp_path / "invalid_plan.json"
    dump_json_file(path, expected)
    saved = load_json_file(path)
    assert saved == expected
    restored_plan = AutomationPlan.from_dict(saved, templates_dir=tmp_path)
    assert restored_plan.to_dict() == expected
    assert restored_plan.initial_rules[0].window_control_config == config
    with pytest.raises(ValueError):
        validate_window_control_config(restored_plan.initial_rules[0].window_control_config)
    db_path = tmp_path / "invalid_sequence.db"
    manager = _manager_at(db_path, create=True)
    sequence = Sequence(name="Invalid config retained", actions=[action])
    sequence.id = manager.create_sequence(sequence)
    restored_sequence = _assert_sequence_saved(db_path, sequence)
    assert restored_sequence.actions[0].window_control_config == config
    assert manager.update_sequence(restored_sequence)
    reloaded = _assert_sequence_saved(db_path, restored_sequence)
    assert reloaded.actions[0].window_control_config == config
    with pytest.raises(ValueError):
        validate_window_control_config(reloaded.actions[0].window_control_config)
