from copy import deepcopy
from datetime import datetime
from pathlib import Path
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.analyzer.automation_models import AutomationPlan, AutomationRule
from src.player import rule_executor as runtime
from src.ui import player_view
from src.utils.config import AppConfig, PlayerConfig
from src.utils.json_utils import dump_json_file, load_json_file
from src.utils.rule_identity import repair_legacy_rule_identities, validate_rule_identities


def legacy_data():
    return {
        "plan_id": "identity-regression",
        "name": "identity",
        "initial_rules": [
            {"rule_id": "one", "action_type": "wait"},
            {"rule_id": "two", "action_type": "wait"},
            {"rule_id": "login", "action_type": "wait", "children": [
                {"rule_id": "first-key", "action_type": "hotkey"},
                {"rule_id": "duplicate", "action_type": "hotkey", "action_keys": ["down"]},
            ]},
            {"rule_id": "setup", "action_type": "wait", "children": [
                {"rule_id": "setup-first", "action_type": "wait"},
                {"rule_id": "duplicate", "action_type": "click", "target_image": "target.png"},
                {"rule_id": "duplicate", "action_type": "double_click", "target_image": "other.png"},
            ]},
        ],
        "monitoring_rules": [],
    }


def bare_executor():
    executor = object.__new__(runtime.RuleExecutor)
    executor._config = SimpleNamespace(player=PlayerConfig())
    return executor


def test_legacy_repair_is_deterministic_idempotent_and_does_not_mutate_input():
    source = legacy_data()
    before = deepcopy(source)
    repaired = repair_legacy_rule_identities(source)
    assert source == before
    assert repaired == repair_legacy_rule_identities(source)
    assert repaired == repair_legacy_rule_identities(repaired)
    validate_rule_identities(repaired)
    assert repaired["initial_rules"][2]["children"][1]["rule_id"] == "duplicate"
    expected = deepcopy(repaired)
    for child in expected["initial_rules"][3]["children"][1:]:
        child["rule_id"] = "duplicate"
    assert expected == source


def test_loaded_legacy_plan_logs_keyboard_and_images_at_their_real_steps():
    plan = AutomationPlan.from_dict(legacy_data())
    executor = bare_executor()
    step_map = executor._rule_id_step_map(plan.initial_rules)
    for index, actual in ((1, "4-2"), (2, "4-3")):
        rule = plan.initial_rules[3].children[index]
        assert step_map[rule.rule_id] == actual
        assert executor._rule_step_in_rules(plan.initial_rules, rule) == actual
    key = plan.initial_rules[2].children[1]
    assert step_map[key.rule_id] == "3-2"
    assert key.action_type == "hotkey" and key.action_keys == ["down"]


def test_runtime_does_not_alias_an_unloaded_duplicate_to_the_first_action():
    key = AutomationRule(rule_id="same", action_type="hotkey")
    image = AutomationRule(rule_id="same", action_type="click")
    other = AutomationRule(rule_id="same", action_type="double_click")
    executor = bare_executor()
    assert executor._rule_id_step_map([key, image, other]) == {}
    assert executor._rule_step_in_rules([key, image, other], image) == "2"
    assert executor._rule_step_in_rules([key, image, other], deepcopy(image)) == ""


def test_legacy_duplicate_parent_repairs_only_its_own_child_parent_pointer():
    source = {"plan_id": "parents", "initial_rules": [
        {"rule_id": "parent", "children": [{"rule_id": "a", "parent_id": "parent"}]},
        {"rule_id": "parent", "children": [{"rule_id": "b", "parent_id": "parent"}]},
    ]}
    repaired = repair_legacy_rule_identities(source)
    first, second = repaired["initial_rules"]
    assert first["children"][0]["parent_id"] == "parent"
    assert second["children"][0]["parent_id"] == second["rule_id"] != "parent"
    assert source["initial_rules"][1]["children"][0]["parent_id"] == "parent"


@pytest.mark.parametrize("field", ["action_call_rule_id", "trigger_missing_rewind_rule_id", "goto_rule_id"])
def test_ambiguous_links_are_rejected_instead_of_silently_retargeted(field):
    source = legacy_data()
    source["initial_rules"][0]["monitoring_watches"] = [{field: "duplicate"}]
    before = deepcopy(source)
    with pytest.raises(ValueError, match="Ambiguous action reference"):
        AutomationPlan.from_dict(source)
    assert source == before


def test_unambiguous_call_rewind_and_monitor_links_remain_identical():
    source = legacy_data()
    source["initial_rules"][0].update(action_call_rule_id="setup", trigger_missing_rewind_rule_id="login",
                                     monitoring_watches=[{"goto_rule_id": "setup", "goto_index": 3}])
    repaired = repair_legacy_rule_identities(source)
    assert repaired["initial_rules"][0] == source["initial_rules"][0]


def test_special_mode_ids_and_configurations_are_not_renamed():
    source = legacy_data()
    source["game_modes"] = {"duplicate": {"engine_profile": "wongak_legacy_v1"}}
    before = deepcopy(source)
    with pytest.raises(ValueError, match="Duplicate special-mode"):
        repair_legacy_rule_identities(source)
    assert source == before


def test_missing_special_mode_id_is_not_repaired_without_map_ownership():
    with pytest.raises(ValueError, match="Missing special-mode action IDs"):
        repair_legacy_rule_identities({"initial_rules": [{"action_type": "game_mode"}]})


def test_missing_ids_are_repaired_without_colliding_with_other_sections():
    source = {"plan_id": "missing", "initial_rules": [{"rule_id": ""}, {"rule_id": ""}],
              "monitoring_rules": [{"rule_id": "existing"}, {"rule_id": "existing"}]}
    repaired = repair_legacy_rule_identities(source)
    validate_rule_identities(repaired)
    assert repaired == repair_legacy_rule_identities(source)


def test_new_repair_id_cannot_overwrite_an_existing_reserved_id():
    source = legacy_data()
    first = repair_legacy_rule_identities(source)
    reserved = first["initial_rules"][3]["children"][1]["rule_id"]
    source["monitoring_rules"] = [{"rule_id": reserved}]
    repaired = repair_legacy_rule_identities(source)
    validate_rule_identities(repaired)
    assert repaired["monitoring_rules"][0]["rule_id"] == reserved
    assert repaired["initial_rules"][3]["children"][1]["rule_id"] != reserved


def test_repaired_plan_save_button_persists_and_reloads_exact_actions(tmp_path, monkeypatch):
    plan = AutomationPlan.from_dict(legacy_data(), templates_dir=tmp_path)
    dialog = SimpleNamespace(_plan=plan, _name_entry=SimpleNamespace(get=lambda: "identity"), _modified=True)
    monkeypatch.setattr(player_view, "PLANS_DIR", tmp_path)
    monkeypatch.setattr(player_view, "get_db", lambda: SimpleNamespace(get_recording_by_plan_id=lambda _: None))
    assert player_view.PlanDetailDialog._save_plan(dialog, show_message=False) is True
    saved = load_json_file(tmp_path / f"{plan.plan_id}.json")
    assert saved == plan.to_dict()
    assert AutomationPlan.from_dict(saved, templates_dir=tmp_path).to_dict() == saved
    validate_rule_identities(saved)


def test_duplicate_introduced_before_save_cannot_replace_previous_document(tmp_path, monkeypatch):
    plan = AutomationPlan.from_dict(legacy_data(), templates_dir=tmp_path)
    destination = tmp_path / f"{plan.plan_id}.json"
    dump_json_file(destination, plan.to_dict())
    before = destination.read_bytes()
    plan.initial_rules.append(AutomationRule(rule_id="one"))
    dialog = SimpleNamespace(_plan=plan, _name_entry=SimpleNamespace(get=lambda: "identity"), _modified=True)
    monkeypatch.setattr(player_view, "PLANS_DIR", tmp_path)
    assert player_view.PlanDetailDialog._save_plan(dialog, show_message=False) is False
    assert destination.read_bytes() == before
    assert dialog._modified is True


def test_full_execution_loop_uses_repaired_steps_and_preserves_action_order(monkeypatch):
    monkeypatch.setattr(runtime, "get_config", lambda: AppConfig())
    executor = runtime.RuleExecutor()
    executor._current_plan = AutomationPlan.from_dict(legacy_data())
    executed = []
    def execute(rule, *args, **kwargs):
        executed.append((executor._current_step_num, rule.action_type))
        return runtime.RuleExecutionResult(rule_id=rule.rule_id, success=True, message="ok")
    monkeypatch.setattr(executor, "_execute_rule_with_retry", execute)
    monkeypatch.setattr(executor, "_reset_input_state", lambda _: True)
    monkeypatch.setattr(executor, "_update_progress", lambda *_: None)
    executor._execution_loop()
    assert executor.state == runtime.ExecutionState.COMPLETED
    assert executed == [("1", "wait"), ("2", "wait"), ("3", "wait"), ("3-1", "hotkey"),
                        ("3-2", "hotkey"), ("4", "wait"), ("4-1", "wait"),
                        ("4-2", "click"), ("4-3", "double_click")]


def test_all_shipped_plans_have_unique_ids_before_any_runtime_repair():
    root = Path(__file__).resolve().parents[1]
    for path in sorted((root / "data/plans").glob("*.json")):
        validate_rule_identities(load_json_file(path))


def test_auto_hunt_legacy_repair_preserves_settings_without_freezing_editable_ids():
    root = Path(__file__).resolve().parents[1]
    saved = load_json_file(root / "data/plans/plan_20260118_174859.json")
    legacy = deepcopy(saved)
    for parent, child, old_id in ((1, 4, "rule_0006"), (3, 1, "rule_0003"),
                                  (3, 2, "rule_0003"), (6, 8, "rule_0006")):
        legacy["initial_rules"][parent]["children"][child]["rule_id"] = old_id
    repaired = repair_legacy_rule_identities(legacy)
    validate_rule_identities(repaired)
    assert repair_legacy_rule_identities(legacy) == repaired

    def without_identity(value):
        if isinstance(value, dict):
            return {key: without_identity(item) for key, item in value.items()
                    if key not in {"rule_id", "parent_id"}}
        if isinstance(value, list):
            return [without_identity(item) for item in value]
        return value

    assert without_identity(repaired) == without_identity(saved)
    assert legacy != repaired, "Legacy duplicate IDs must actually be repaired"


def test_keyboard_never_searches_image_and_image_wait_reports_its_real_step(tmp_path, monkeypatch):
    data = legacy_data()
    (tmp_path / "target.png").touch()
    plan = AutomationPlan.from_dict(data, templates_dir=tmp_path)
    executor = bare_executor()
    executor._stop_event = threading.Event()
    executor._make_result = lambda rule, success, message, _: SimpleNamespace(success=success, message=message)
    executor._wait_for_resume = lambda: False
    executor._check_user_intervention = lambda: False
    key = plan.initial_rules[2].children[1]
    image = plan.initial_rules[3].children[1]
    steps = executor._rule_id_step_map(plan.initial_rules)
    controller = SimpleNamespace(hotkey=Mock(return_value=True))
    monkeypatch.setattr(runtime, "get_input_controller", lambda: controller)
    matcher = Mock(side_effect=AssertionError("Keyboard must not enter image search"))
    monkeypatch.setattr(executor, "_find_rule_image_click_target", matcher)
    executor._current_step_num = steps[key.rule_id]
    assert executor._execute_fixed_action(key, datetime.now()).success
    controller.hotkey.assert_called_once_with("down")
    matcher.assert_not_called()

    messages = []
    executor._current_step_num = steps[image.rule_id]
    monkeypatch.setattr(executor, "_find_rule_image_click_target", lambda *_: None)
    monkeypatch.setattr(runtime.time, "sleep", lambda _: executor._stop_event.set())
    monkeypatch.setattr(runtime.logger, "info", messages.append)
    executor._execute_fixed_action(image, datetime.now())
    waits = [message for message in messages if "타겟 이미지 대기" in message]
    assert len(waits) == 1 and "[4-2]" in waits[0] and "[3-2]" not in waits[0]


def test_runtime_progress_to_discord_uses_real_image_action_number(monkeypatch):
    from src.ui.main_window import MainWindow
    from src.utils.discord_notifier import DiscordAlert, build_discord_payload
    monkeypatch.setattr(runtime, "get_config", lambda: AppConfig())
    executor = runtime.RuleExecutor()
    executor._current_plan = AutomationPlan.from_dict(legacy_data())
    context = SimpleNamespace(_mini_active_plan=executor._current_plan)
    snapshots = []
    def progress(message):
        if executor.progress.current_action_number == "4-2":
            snapshots.append(MainWindow._mini_build_notification_snapshot(context, executor.progress, message))
    monkeypatch.setattr(executor, "_update_progress", progress)
    monkeypatch.setattr(executor, "_reset_input_state", lambda _: True)
    monkeypatch.setattr(executor, "_execute_rule_with_retry", lambda rule, *args, **kwargs:
                        runtime.RuleExecutionResult(rule_id=rule.rule_id, success=True, message="ok"))
    executor._execution_loop()
    assert snapshots and snapshots[0]["action_number"] == "4-2"
    context._mini_notification_last_progress_snapshot = snapshots[0]
    context._mini_infer_stuck_reason = lambda snapshot, elapsed: MainWindow._mini_infer_stuck_reason(context, snapshot, elapsed)
    fields = MainWindow._mini_build_stuck_diagnostic_fields(context, 180, 180)
    assert dict(fields)["현재 액션"] == "[4-2] click"
    content = build_discord_payload(DiscordAlert(title="stuck", pc_number="03", fields=fields))["content"]
    assert content.splitlines()[0] == "PC번호: 03번"
    assert "[4-2] click" in content and "[3-2]" not in content
