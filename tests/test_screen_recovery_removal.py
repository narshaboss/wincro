"""Removal must preserve old documents and ordinary input execution."""

from datetime import datetime
from pathlib import Path
import json
from types import SimpleNamespace

import pytest

from src.analyzer.automation_models import AutomationPlan, AutomationRule
from src.database.db_manager import DatabaseManager
from src.database.models import Action, Sequence
from src.player.rule_executor import RuleExecutor
from src.ui.player_view import PlayerView, PlanDetailDialog, SequenceDetailDialog
from src.utils.json_utils import dump_json_file, load_json_file


LEGACY = {
    "transition_recovery_policy": "force_on",
    "transition_recovery_enabled": True,
    "transition_verify_mode": "custom_image",
    "transition_verify_image": "missing_confirmation.png",
    "transition_recovery_count": 20,
    "transition_failure_mode": "goto_rule",
    "transition_failure_rule_id": "previous",
}


def assert_no_recovery(value):
    if isinstance(value, dict):
        assert not any(key.startswith(("transition_recovery_", "transition_verify_",
                                       "transition_failure_", "transition_stop_repeats_"))
                       for key in value)
        for child in value.values():
            assert_no_recovery(child)
    elif isinstance(value, list):
        for child in value:
            assert_no_recovery(child)


def test_old_plan_load_save_reload_preserves_actions(tmp_path):
    source = AutomationRule(rule_id="source", action_type="hotkey", action_keys=["enter"],
                            repeat_count=4, repeat_delay=1.2)
    child = AutomationRule(rule_id="child", action_type="hotkey", action_keys=["esc"],
                           parent_id="source", trigger_missing_rewind_rule_id="previous")
    source.children = [child]
    plan = AutomationPlan(name="legacy", initial_rules=[source])
    expected = plan.to_dict()
    legacy = plan.to_dict()
    legacy["transition_recovery_auto_enabled"] = True
    legacy["initial_rules"][0].update(LEGACY)
    legacy["initial_rules"][0]["children"][0].update(LEGACY)
    restored = AutomationPlan.from_dict(legacy, templates_dir=tmp_path)
    path = tmp_path / "plan.json"
    dump_json_file(path, restored.to_dict())
    saved = load_json_file(path)
    assert saved == expected
    assert AutomationPlan.from_dict(saved, templates_dir=tmp_path).to_dict() == expected
    assert_no_recovery(saved)


@pytest.mark.parametrize("existing_column", [False, True])
def test_old_sequence_database_remains_readable_and_savable(tmp_path, existing_column):
    manager = object.__new__(DatabaseManager)
    manager._db_path = tmp_path / "sequence.db"
    manager._wal_initialized = False
    manager._ensure_database()
    action = Action(action_id="source", action_type="hotkey", keys=["enter"], repeat_count=4)
    sequence = Sequence(name="legacy", actions=[action])
    sequence.id = manager.create_sequence(sequence)
    legacy_action = action.to_dict()
    legacy_action.update(LEGACY)
    with manager._get_connection() as conn:
        if existing_column:
            conn.execute("ALTER TABLE sequences ADD COLUMN transition_recovery_auto_enabled INTEGER DEFAULT 0")
            conn.execute("UPDATE sequences SET transition_recovery_auto_enabled = 1")
        conn.execute("UPDATE sequences SET actions = ? WHERE id = ?",
                     (json.dumps([legacy_action]), sequence.id))
    loaded = manager.get_sequence(sequence.id)
    assert loaded.actions[0].to_dict() == action.to_dict()
    assert manager.update_sequence(loaded)
    reloaded = manager.get_sequence(sequence.id)
    assert reloaded.actions[0].to_dict() == action.to_dict()
    assert_no_recovery(reloaded.to_export_dict())
    with manager._get_connection() as conn:
        saved = conn.execute("SELECT actions FROM sequences WHERE id = ?", (sequence.id,)).fetchone()
    assert_no_recovery(json.loads(saved[0]))


@pytest.mark.parametrize("action_type", ["hotkey", "double_click"])
def test_removed_flags_cannot_inject_retries_or_truncate_repeats(monkeypatch, action_type):
    executor = RuleExecutor()
    rule = AutomationRule.from_dict({"rule_id": "source", "action_type": action_type,
                                     "repeat_count": 4, "repeat_delay": 0, **LEGACY})
    # Even an older in-memory object with the removed attributes is inert.
    for name, value in LEGACY.items():
        setattr(rule, name, value)
    plan = AutomationPlan(initial_rules=[rule])
    plan.transition_recovery_auto_enabled = True
    executor._current_plan = plan
    executor._pause_event.set()
    calls = []
    monkeypatch.setattr("src.player.rule_executor.time.sleep", lambda _: None)
    monkeypatch.setattr(executor, "_handle_trigger_gate", lambda *a, **k: None)
    monkeypatch.setattr(executor, "_execute_rule", lambda r, **kw: (
        calls.append(r.rule_id) or executor._make_result(r, True, "sent", datetime.now())))
    monkeypatch.setattr(executor, "_capture_trigger_input_window", lambda: pytest.fail("Unexpected refocus"))
    result = executor._execute_rule_with_retry(rule, step_num="1")
    assert result.success
    assert calls == ["source"] * 4


def test_missing_next_image_waits_without_reclicking(monkeypatch):
    executor = RuleExecutor()
    executor._pause_event.set()
    rule = AutomationRule.from_dict({"action_type": "click", "repeat_count": 1, **LEGACY})
    target = AutomationRule(action_type="click", target_image="next.png")
    calls, scans = [], []
    monkeypatch.setattr("src.player.rule_executor.time.sleep", lambda _: None)
    monkeypatch.setattr(executor, "_handle_trigger_gate", lambda *a, **k: None)
    monkeypatch.setattr(executor, "_execute_rule", lambda r, **kw: (
        calls.append(r.rule_id) or executor._make_result(r, True, "sent", datetime.now())))
    def scan(*args, **kwargs):
        scans.append(1)
        if len(scans) == 3:
            executor._stop_event.set()
        return None
    monkeypatch.setattr(executor, "_find_image_on_screen", scan)
    result = executor._execute_rule_with_retry(rule, next_target_images=["next.png"], next_rule=target)
    assert not result.success
    assert len(calls) == 1
    assert len(scans) == 3


def test_sequence_conversion_keeps_existing_image_verification():
    action = Action.from_dict({"action_id": "image", "action_type": "click",
                              "verify_image_color": True, "verify_image_brightness": True,
                              "search_region": [1, 2, 40, 50], **LEGACY})
    view = object.__new__(PlayerView)
    rule = view._sequence_action_to_playback_rule(action)
    assert rule.verify_image_color and rule.verify_image_brightness
    assert_no_recovery(rule.to_dict())


def test_screen_recovery_ui_and_engine_removed():
    root = Path(__file__).resolve().parents[1]
    for relative in ("src/ui/player_view.py", "src/ui/main_window.py"):
        source = (root / relative).read_text(encoding="utf-8-sig")
        assert "화면복구" not in source
        assert "transition_recovery" not in source
    assert not hasattr(RuleExecutor, "_recover_transition")
    assert not (root / "src/utils/transition_recovery_policy.py").exists()


def test_plan_save_handler_writes_then_reloads_exact_document(tmp_path, monkeypatch):
    from src.ui import player_view
    plan = AutomationPlan.from_dict({"name": "before", "plan_id": "audit",
        "initial_rules": [{"rule_id": "key", "action_type": "hotkey",
                           "action_keys": ["enter"], "repeat_count": 4, **LEGACY}]},
        templates_dir=tmp_path)
    dialog = SimpleNamespace(_plan=plan, _name_entry=SimpleNamespace(get=lambda: "saved"), _modified=True)
    monkeypatch.setattr(player_view, "PLANS_DIR", tmp_path)
    monkeypatch.setattr(player_view, "get_db",
                        lambda: SimpleNamespace(get_recording_by_plan_id=lambda _: None))
    assert PlanDetailDialog._save_plan(dialog, show_message=False)
    assert dialog._modified is False
    disk = load_json_file(tmp_path / "audit.json")
    assert disk == plan.to_dict()
    assert AutomationPlan.from_dict(disk, templates_dir=tmp_path).to_dict() == disk
    assert_no_recovery(disk)


def test_sequence_save_handler_writes_then_reloads_exact_actions(tmp_path):
    manager = object.__new__(DatabaseManager)
    manager._db_path = tmp_path / "save.db"
    manager._wal_initialized = False
    manager._ensure_database()
    sequence = Sequence(name="audit", actions=[Action.from_dict({
        "action_type": "hotkey", "keys": ["enter"], "repeat_count": 4, **LEGACY})])
    sequence.id = manager.create_sequence(sequence)
    sequence.actions[0].repeat_count = 3
    dialog = SimpleNamespace(_sequence=sequence, _db=manager, _modified=True)
    assert SequenceDetailDialog._save_sequence_with_options(dialog, show_message=False)
    reloaded = manager.get_sequence(sequence.id)
    assert [a.to_dict() for a in reloaded.actions] == [a.to_dict() for a in sequence.actions]
    assert_no_recovery(reloaded.to_export_dict())
