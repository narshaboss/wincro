from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from src.analyzer.automation_models import AutomationRule
from src.database.db_manager import DatabaseManager
from src.database.models import Action, Sequence
from src.player.rule_executor import RuleExecutor
from src.utils.auto_list import (
    AUTO_LIST_MODE_UNTIL_EXHAUSTED,
    auto_list_config_for_save,
    auto_list_config_from_saved,
    candidate_values,
    classify_colour_state,
    normalize_auto_list_config,
    region_center,
    set_auto_list_item_search_region,
    translate_screen_region,
)


def _solid_bgr(hsv):
    pixel = np.uint8([[hsv]])
    bgr = cv2.cvtColor(pixel, cv2.COLOR_HSV2BGR)[0, 0]
    return np.full((8, 8, 3), bgr, dtype=np.uint8)


def test_colour_classifier_distinguishes_green_red_and_unknown():
    green = classify_colour_state(_solid_bgr((60, 255, 220)))
    red = classify_colour_state(_solid_bgr((0, 255, 220)))
    blank = classify_colour_state(np.zeros((8, 8, 3), dtype=np.uint8))

    assert green.state == "available"
    assert red.state == "unavailable"
    assert blank.state == "unknown"


def test_colour_classifier_gives_red_priority_when_both_are_present():
    crop = _solid_bgr((60, 255, 220))
    crop[:3, :3] = _solid_bgr((0, 255, 220))[0, 0]
    result = classify_colour_state(crop, red_min_pixels=4, green_min_pixels=4)
    assert result.state == "unavailable"
    assert result.red_pixels >= 4
    assert result.green_pixels >= 4


def test_candidate_values_descend_to_minimum():
    assert list(candidate_values(10, 1)) == list(range(10, 0, -1))
    assert list(candidate_values(5, 3)) == [5, 4, 3]


def test_virtual_screen_region_translation_supports_negative_monitor_origin():
    assert translate_screen_region([-100, 20, 50, 80], -1920, 0) == [1820, 20, 1970, 80]


def test_quantity_point_is_migrated_to_region_without_changing_click_center():
    config = normalize_auto_list_config({"quantity_point": [10, 20]})

    assert config["quantity_region"] == [8, 18, 13, 23]
    assert config["quantity_point"] == [10, 20]
    assert region_center(config["quantity_region"]) == [10, 20]


def test_executor_clicks_quantity_region_center_before_typing(monkeypatch):
    clicks = []
    key_events = []
    clipboard = {"value": "사용자 클립보드"}
    field = {"focused": False, "selected": False, "value": "1"}

    def double_click(x, y, duration=0.0):
        clicks.append((x, y, duration))
        field["focused"] = True
        field["selected"] = True
        return True

    def hotkey(*keys):
        key_events.append(keys)
        if keys == ("ctrl", "a") and field["focused"]:
            field["selected"] = True
        elif keys == ("ctrl", "c") and field["focused"]:
            clipboard["value"] = field["value"]
        return True

    def press(key):
        key_events.append(key)
        if field["selected"]:
            field["value"] = ""
            field["selected"] = False
        field["value"] += key
        return True

    controller = SimpleNamespace(
        double_click=double_click,
        hotkey=hotkey,
        press=press,
    )
    import src.player.rule_executor as executor_module
    monkeypatch.setattr(executor_module, "get_input_controller", lambda: controller)
    monkeypatch.setattr(executor_module.pyperclip, "copy", lambda value: clipboard.update(value=value))
    monkeypatch.setattr(executor_module.pyperclip, "paste", lambda: clipboard["value"])

    executor = RuleExecutor()
    monkeypatch.setattr(executor, "_auto_list_wait", lambda _seconds: True)
    monkeypatch.setattr(executor, "_auto_list_capture_input_region", lambda _region: None)
    assert executor._auto_list_input_value([100, 200, 140, 240], 10) is True
    assert clicks == [(120, 220, executor._mouse_duration * 2.0)]
    assert field["value"] == "10"
    assert clipboard["value"] == "사용자 클립보드"
    assert key_events == [
        ("ctrl", "a"),
        ("ctrl", "c"),
        "1",
        "0",
        ("ctrl", "a"),
        ("ctrl", "c"),
    ]


def test_auto_list_quantity_input_retries_until_focus_is_confirmed(monkeypatch):
    clipboard = {"value": "original"}
    field = {"attempt": 0, "focused": False, "selected": False, "value": "1"}
    typed = []

    def double_click(_x, _y, duration=0.0):
        field["attempt"] += 1
        field["focused"] = field["attempt"] >= 2
        field["selected"] = field["focused"]
        return True

    def hotkey(*keys):
        if keys == ("ctrl", "a") and field["focused"]:
            field["selected"] = True
        elif keys == ("ctrl", "c") and field["focused"]:
            clipboard["value"] = field["value"]
        return True

    def press(key):
        typed.append(key)
        if field["selected"]:
            field["value"] = ""
            field["selected"] = False
        field["value"] += key
        return True

    controller = SimpleNamespace(double_click=double_click, hotkey=hotkey, press=press)
    import src.player.rule_executor as executor_module
    monkeypatch.setattr(executor_module, "get_input_controller", lambda: controller)
    monkeypatch.setattr(executor_module.pyperclip, "copy", lambda value: clipboard.update(value=value))
    monkeypatch.setattr(executor_module.pyperclip, "paste", lambda: clipboard["value"])

    executor = RuleExecutor()
    monkeypatch.setattr(executor, "_auto_list_wait", lambda _seconds: True)
    monkeypatch.setattr(executor, "_auto_list_capture_input_region", lambda _region: None)

    assert executor._auto_list_input_value([10, 20, 110, 40], 9) is True
    assert field["attempt"] == 2
    assert typed == ["9"]
    assert field["value"] == "9"
    assert clipboard["value"] == "original"


def test_auto_list_quantity_input_never_types_without_confirmed_focus(monkeypatch):
    clipboard = {"value": "original"}
    typed = []
    controller = SimpleNamespace(
        double_click=lambda *_args, **_kwargs: True,
        hotkey=lambda *_keys: True,
        press=lambda key: typed.append(key) or True,
    )
    import src.player.rule_executor as executor_module
    monkeypatch.setattr(executor_module, "get_input_controller", lambda: controller)
    monkeypatch.setattr(executor_module.pyperclip, "copy", lambda value: clipboard.update(value=value))
    monkeypatch.setattr(executor_module.pyperclip, "paste", lambda: clipboard["value"])

    executor = RuleExecutor()
    monkeypatch.setattr(executor, "_auto_list_wait", lambda _seconds: True)
    monkeypatch.setattr(executor, "_auto_list_capture_input_region", lambda _region: None)

    assert executor._auto_list_input_value([10, 20, 110, 40], 10) is False
    assert typed == []
    assert clipboard["value"] == "original"


def test_auto_list_quantity_selection_detects_dark_active_background():
    before = np.full((20, 100, 3), 240, dtype=np.uint8)
    after = before.copy()
    after[3:17, 72:92] = 10

    assert RuleExecutor._auto_list_input_selection_visible(before, after) is True
    assert RuleExecutor._auto_list_input_selection_visible(before, before.copy()) is False


def test_auto_list_quantity_input_uses_visual_focus_when_copy_is_unsupported(monkeypatch):
    typed = []
    controller = SimpleNamespace(
        double_click=lambda *_args, **_kwargs: True,
        hotkey=lambda *keys: False if keys == ("ctrl", "c") else True,
        press=lambda key: typed.append(key) or True,
    )
    import src.player.rule_executor as executor_module
    monkeypatch.setattr(executor_module, "get_input_controller", lambda: controller)

    before = np.full((20, 100, 3), 240, dtype=np.uint8)
    selected = before.copy()
    selected[3:17, 72:92] = 10
    captures = iter([before, selected, selected])

    executor = RuleExecutor()
    monkeypatch.setattr(executor, "_auto_list_wait", lambda _seconds: True)
    monkeypatch.setattr(executor, "_auto_list_capture_input_region", lambda _region: next(captures))

    assert executor._auto_list_input_value([10, 20, 110, 40], 10) is True
    assert typed == ["1", "0"]


def test_auto_list_config_normalizes_item_values():
    config = normalize_auto_list_config(
        {
            "max_value": 10,
            "min_value": 1,
            "items": [{"image": "sample.png", "target_count": "3", "confidence": 2}],
        }
    )
    assert config["items"][0]["target_count"] == 3
    assert config["items"][0]["confidence"] == 1.0
    assert config["items"][0]["enabled"] is True


def test_auto_list_legacy_single_item_region_migrates_to_shared_region():
    config = normalize_auto_list_config(
        {
            "items": [
                {"image": "one.png", "search_region": [10, 20, 110, 220]},
                {"image": "two.png", "search_region": None},
                {"image": "three.png"},
            ]
        }
    )

    assert config["item_search_region"] == [10, 20, 110, 220]
    assert [item["search_region"] for item in config["items"]] == [
        [10, 20, 110, 220],
        [10, 20, 110, 220],
        [10, 20, 110, 220],
    ]


def test_auto_list_shared_region_set_and_clear_updates_every_item():
    config = {"items": [{"image": "one.png"}, {"image": "two.png"}]}

    assert set_auto_list_item_search_region(config, [1, 2, 30, 40]) == [1, 2, 30, 40]
    assert all(item["search_region"] == [1, 2, 30, 40] for item in config["items"])

    assert set_auto_list_item_search_region(config, None) is None
    assert config["item_search_region"] is None
    assert all(item["search_region"] is None for item in config["items"])


def test_auto_list_config_normalizes_exhaustion_safety_options():
    config = normalize_auto_list_config(
        {
            "processing_mode": AUTO_LIST_MODE_UNTIL_EXHAUSTED,
            "reselect_each_cycle": True,
            "max_cycles_per_item": "25",
            "max_runtime_per_item": "900",
        }
    )
    assert config["processing_mode"] == AUTO_LIST_MODE_UNTIL_EXHAUSTED
    assert config["reselect_each_cycle"] is True
    assert config["max_cycles_per_item"] == 25
    assert config["max_runtime_per_item"] == 900.0


def test_auto_list_item_timeout_preserves_subsecond_value_across_save_reload(tmp_path):
    config = normalize_auto_list_config({"item_timeout": "0.5"})
    saved = auto_list_config_for_save(config)
    restored = auto_list_config_from_saved(saved, tmp_path)

    assert config["item_timeout"] == 0.5
    assert saved["item_timeout"] == 0.5
    assert restored["item_timeout"] == 0.5


def test_auto_list_item_search_uses_saved_subsecond_timeout(monkeypatch):
    executor = RuleExecutor()
    waits = []
    item = {"image": "missing.png", "confidence": 0.8, "search_region": None}

    monkeypatch.setattr(executor, "_wait_for_resume", lambda: False)
    monkeypatch.setattr(executor, "_find_image_on_screen", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(executor, "_auto_list_wait", lambda interval: waits.append(interval) or True)
    monkeypatch.setattr("src.player.rule_executor.time.monotonic", lambda: 0.0)

    assert executor._auto_list_wait_for_item(item, 0.5) is None
    assert sum(waits) == pytest.approx(0.5)


def test_automation_rule_saves_auto_list_images_portably(tmp_path):
    image = tmp_path / "item.png"
    image.write_bytes(b"image")
    rule = AutomationRule(
        rule_id="rule_auto",
        action_type="auto_list",
        auto_list_config={"items": [{"image": str(image), "name": "항목"}]},
    )
    saved = rule.to_dict()
    assert saved["auto_list_config"]["items"][0]["image"] == "item.png"

    restored = AutomationRule.from_dict(saved, templates_dir=tmp_path)
    assert Path(restored.auto_list_config["items"][0]["image"]) == image


def test_auto_list_exhaustion_mode_and_value_input_child_survive_reload(tmp_path):
    image = tmp_path / "item.png"
    image.write_bytes(b"image")
    child = AutomationRule(
        rule_id="value_child",
        action_type="auto_list_value_input",
        description="현재 처리수량 입력",
        search_region=[100, 200, 160, 240],
    )
    rule = AutomationRule(
        rule_id="parent",
        action_type="auto_list",
        children=[child],
        auto_list_config={
            "processing_mode": AUTO_LIST_MODE_UNTIL_EXHAUSTED,
            "reselect_each_cycle": True,
            "items": [{"image": str(image), "name": "1성"}],
        },
    )

    restored = AutomationRule.from_dict(rule.to_dict(), templates_dir=tmp_path)
    assert restored.auto_list_config["processing_mode"] == AUTO_LIST_MODE_UNTIL_EXHAUSTED
    assert restored.auto_list_config["reselect_each_cycle"] is True
    assert restored.children[0].action_type == "auto_list_value_input"
    assert restored.children[0].search_region == [100, 200, 160, 240]


def test_auto_list_quantity_repeat_settings_survive_rule_and_action_reload(tmp_path):
    target = tmp_path / "target.png"
    check = tmp_path / "check.png"
    target.write_bytes(b"target")
    check.write_bytes(b"check")
    rule = AutomationRule(
        rule_id="select_items",
        action_type="click",
        target_image=str(target),
        repeat_from_auto_list_quantity=True,
        auto_list_repeat_confirm_image=str(check),
        auto_list_repeat_confirm_region=[100, 200, 300, 500],
        auto_list_repeat_confirm_confidence=0.93,
    )

    saved_rule = rule.to_dict()
    restored_rule = AutomationRule.from_dict(saved_rule, templates_dir=tmp_path)

    assert saved_rule["auto_list_repeat_confirm_image"] == "check.png"
    assert restored_rule.repeat_from_auto_list_quantity is True
    assert restored_rule.auto_list_repeat_confirm_image == str(check)
    assert restored_rule.auto_list_repeat_confirm_region == [100, 200, 300, 500]
    assert restored_rule.auto_list_repeat_confirm_confidence == 0.93

    action = Action(
        action_type="click",
        target_image=str(target),
        repeat_from_auto_list_quantity=True,
        auto_list_repeat_confirm_image=str(check),
        auto_list_repeat_confirm_region=[100, 200, 300, 500],
        auto_list_repeat_confirm_confidence=0.93,
    )
    restored_action = Action.from_dict(action.to_dict())
    assert restored_action.repeat_from_auto_list_quantity is True
    assert restored_action.auto_list_repeat_confirm_image == str(check)
    assert restored_action.auto_list_repeat_confirm_region == [100, 200, 300, 500]
    assert restored_action.auto_list_repeat_confirm_confidence == 0.93


def test_non_auto_list_rules_do_not_serialize_auto_list_defaults():
    rule = AutomationRule(rule_id="normal", action_type="hotkey", action_keys=["enter"])
    action = Action(action_type="hotkey", keys=["enter"])

    assert rule.auto_list_config == {}
    assert action.auto_list_config == {}
    assert "auto_list_config" not in rule.to_dict()
    assert "auto_list_config" not in action.to_dict()


def test_action_saves_auto_list_images_portably(monkeypatch, tmp_path):
    image = tmp_path / "item.png"
    image.write_bytes(b"image")
    action = Action(
        action_type="auto_list",
        auto_list_config={"items": [{"image": str(image), "name": "항목"}]},
    )
    saved = action.to_dict()
    assert saved["auto_list_config"]["items"][0]["image"] == "item.png"

    import src.utils.config as config_module

    monkeypatch.setattr(config_module, "DATA_DIR", tmp_path.parent)
    restored = Action.from_dict(saved)
    assert Path(restored.auto_list_config["items"][0]["image"]).name == "item.png"


def test_sequence_database_round_trip_preserves_auto_list_and_dynamic_value_child(tmp_path):
    image = tmp_path / "item.png"
    image.write_bytes(b"image")
    child = Action(
        action_type="auto_list_value_input",
        description="현재 처리수량 입력",
        search_region=[100, 200, 160, 240],
    )
    parent = Action(
        action_type="auto_list",
        description="자동 목록 처리",
        children=[child],
        auto_list_config={
            "processing_mode": AUTO_LIST_MODE_UNTIL_EXHAUSTED,
            "quantity_region": [10, 20, 100, 40],
            "status_region": [10, 50, 100, 70],
            "items": [{"image": str(image), "name": "1성"}],
        },
    )
    sequence = Sequence(name="자동 목록 저장 검증", actions=[parent])
    manager = DatabaseManager()
    original_path = manager._db_path
    original_wal = manager._wal_initialized
    try:
        manager._db_path = tmp_path / "round_trip.db"
        manager._wal_initialized = False
        manager._ensure_database()
        sequence.id = manager.create_sequence(sequence)
        loaded = manager.get_sequence(sequence.id)
    finally:
        manager._db_path = original_path
        manager._wal_initialized = original_wal

    assert loaded is not None
    loaded_parent = loaded.actions[0]
    assert loaded_parent.auto_list_config["processing_mode"] == AUTO_LIST_MODE_UNTIL_EXHAUSTED
    assert loaded_parent.children[0].action_type == "auto_list_value_input"
    assert loaded_parent.children[0].search_region == [100, 200, 160, 240]


def test_executor_descends_value_and_runs_children_for_each_accepted_batch(monkeypatch, tmp_path):
    image = tmp_path / "item.png"
    image.write_bytes(b"image")
    child = AutomationRule(rule_id="child", action_type="hotkey", action_keys=["enter"])
    rule = AutomationRule(
        rule_id="parent",
        action_type="auto_list",
        children=[child],
        auto_list_config={
            "items": [{"image": str(image), "name": "제작", "target_count": 3}],
            "quantity_region": [8, 18, 13, 23],
            "status_region": [0, 0, 10, 10],
            "max_value": 3,
            "min_value": 1,
            "render_wait": 0.05,
            "after_process_wait": 0.0,
        },
    )
    executor = RuleExecutor()
    entered = []
    child_runs = []
    states = iter(
        [
            SimpleNamespace(state="unavailable", is_available=False, red_pixels=10, green_pixels=0),
            SimpleNamespace(state="available", is_available=True, red_pixels=0, green_pixels=10),
            SimpleNamespace(state="available", is_available=True, red_pixels=0, green_pixels=10),
        ]
    )

    monkeypatch.setattr(executor, "_auto_list_wait_for_item", lambda *_: (20, 30, 0.99))
    monkeypatch.setattr(executor, "_execute_click_at", lambda *_args, **_kwargs: SimpleNamespace(success=True))
    monkeypatch.setattr(executor, "_auto_list_wait", lambda *_: True)
    monkeypatch.setattr(executor, "_auto_list_input_value", lambda _point, value: entered.append(value) or True)
    monkeypatch.setattr(executor, "_auto_list_colour_state", lambda _config: next(states))
    monkeypatch.setattr(executor, "_execute_child_rules_for_auto_list", lambda *_: child_runs.append("run") or None)

    result = executor._execute_fixed_action(rule, datetime.now())

    assert result.success is True
    assert entered == [3, 2, 1]
    assert child_runs == ["run", "run"]
    assert child.rule_id in executor._child_rules_executed_with_parent


def test_executor_reselects_and_processes_until_minimum_value_is_unavailable(monkeypatch, tmp_path):
    image = tmp_path / "item.png"
    image.write_bytes(b"image")
    child = AutomationRule(rule_id="child", action_type="hotkey", action_keys=["enter"])
    rule = AutomationRule(
        rule_id="parent",
        action_type="auto_list",
        children=[child],
        auto_list_config={
            "processing_mode": AUTO_LIST_MODE_UNTIL_EXHAUSTED,
            "items": [{"image": str(image), "name": "1성"}],
            "quantity_region": [8, 18, 13, 23],
            "status_region": [0, 0, 10, 10],
            "max_value": 3,
            "min_value": 1,
            "render_wait": 0.05,
            "after_process_wait": 0.0,
            "max_cycles_per_item": 10,
        },
    )
    executor = RuleExecutor()
    entered = []
    accepted_values = []
    find_calls = []
    states = iter(
        [
            SimpleNamespace(state="available", is_available=True, red_pixels=0, green_pixels=10),
            SimpleNamespace(state="unavailable", is_available=False, red_pixels=10, green_pixels=0),
            SimpleNamespace(state="available", is_available=True, red_pixels=0, green_pixels=10),
            SimpleNamespace(state="unavailable", is_available=False, red_pixels=10, green_pixels=0),
            SimpleNamespace(state="unavailable", is_available=False, red_pixels=10, green_pixels=0),
            SimpleNamespace(state="unavailable", is_available=False, red_pixels=10, green_pixels=0),
        ]
    )

    monkeypatch.setattr(
        executor,
        "_auto_list_wait_for_item",
        lambda *_: find_calls.append("find") or (20, 30, 0.99),
    )
    monkeypatch.setattr(executor, "_execute_click_at", lambda *_args, **_kwargs: SimpleNamespace(success=True))
    monkeypatch.setattr(executor, "_auto_list_wait", lambda *_: True)
    monkeypatch.setattr(executor, "_auto_list_input_value", lambda _region, value: entered.append(value) or True)
    monkeypatch.setattr(executor, "_auto_list_colour_state", lambda _config: next(states))
    monkeypatch.setattr(
        executor,
        "_execute_child_rules_for_auto_list",
        lambda _rule, _started, value, _item: accepted_values.append(value) or None,
    )

    result = executor._execute_fixed_action(rule, datetime.now())

    assert result.success is True
    assert entered == [3, 3, 2, 3, 2, 1]
    assert accepted_values == [3, 2]
    assert len(find_calls) == 3


def test_exhaustion_mode_treats_processed_item_disappearance_as_completion(monkeypatch, tmp_path):
    image = tmp_path / "item.png"
    image.write_bytes(b"image")
    child = AutomationRule(rule_id="child", action_type="hotkey", action_keys=["enter"])
    rule = AutomationRule(
        rule_id="parent",
        action_type="auto_list",
        children=[child],
        auto_list_config={
            "processing_mode": AUTO_LIST_MODE_UNTIL_EXHAUSTED,
            "items": [{"image": str(image), "name": "1번"}],
            "quantity_region": [8, 18, 13, 23],
            "status_region": [0, 0, 10, 10],
            "max_value": 10,
            "min_value": 1,
            "item_timeout": 1.0,
            "render_wait": 0.05,
            "after_process_wait": 0.0,
        },
    )
    executor = RuleExecutor()
    find_results = iter([(20, 30, 0.99), None])
    accepted = []

    monkeypatch.setattr(executor, "_auto_list_wait_for_item", lambda *_: next(find_results))
    monkeypatch.setattr(executor, "_execute_click_at", lambda *_args, **_kwargs: SimpleNamespace(success=True))
    monkeypatch.setattr(executor, "_auto_list_wait", lambda *_: True)
    monkeypatch.setattr(executor, "_auto_list_input_value", lambda *_: True)
    monkeypatch.setattr(
        executor,
        "_auto_list_colour_state",
        lambda _config: SimpleNamespace(
            state="available",
            is_available=True,
            red_pixels=0,
            green_pixels=10,
        ),
    )
    monkeypatch.setattr(
        executor,
        "_execute_child_rules_for_auto_list",
        lambda _rule, _started, value, item: accepted.append((value, item["name"])) or None,
    )

    result = executor._execute_fixed_action(rule, datetime.now())

    assert result.success is True
    assert accepted == [(10, "1번")]
    assert "총 10" in result.message


def test_auto_list_quickly_skips_missing_items_and_continues_in_order(monkeypatch, tmp_path):
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    child = AutomationRule(rule_id="child", action_type="hotkey", action_keys=["enter"])
    rule = AutomationRule(
        rule_id="parent",
        action_type="auto_list",
        children=[child],
        auto_list_config={
            "processing_mode": AUTO_LIST_MODE_UNTIL_EXHAUSTED,
            "items": [
                {"image": str(first), "name": "1번"},
                {"image": str(second), "name": "4번"},
            ],
            "quantity_region": [8, 18, 13, 23],
            "status_region": [0, 0, 10, 10],
            "max_value": 1,
            "min_value": 1,
            "item_timeout": 1.0,
            "skip_missing_item": True,
            "render_wait": 0.05,
            "after_process_wait": 0.0,
        },
    )
    executor = RuleExecutor()
    searched = []

    def find_item(item, timeout):
        searched.append((item["name"], timeout))
        return None if item["name"] == "1번" else (20, 30, 0.99)

    monkeypatch.setattr(executor, "_auto_list_wait_for_item", find_item)
    monkeypatch.setattr(executor, "_execute_click_at", lambda *_args, **_kwargs: SimpleNamespace(success=True))
    monkeypatch.setattr(executor, "_auto_list_wait", lambda *_: True)
    monkeypatch.setattr(executor, "_auto_list_input_value", lambda *_: True)
    monkeypatch.setattr(
        executor,
        "_auto_list_colour_state",
        lambda _config: SimpleNamespace(
            state="unavailable",
            is_available=False,
            red_pixels=10,
            green_pixels=0,
        ),
    )

    result = executor._execute_fixed_action(rule, datetime.now())

    assert result.success is True
    assert searched == [("1번", 1.0), ("4번", 1.0)]


def test_current_auto_list_value_input_requires_context_and_uses_configured_region(monkeypatch):
    executor = RuleExecutor()
    rule = AutomationRule(
        rule_id="value",
        action_type="auto_list_value_input",
        search_region=[100, 200, 160, 240],
    )
    entered = []
    monkeypatch.setattr(
        executor,
        "_auto_list_input_value",
        lambda region, value: entered.append((region, value)) or True,
    )

    missing = executor._execute_fixed_action(rule, datetime.now())
    executor._auto_list_current_value = 8
    success = executor._execute_fixed_action(rule, datetime.now())

    assert missing.success is False
    assert success.success is True
    assert entered == [([100, 200, 160, 240], 8)]


def test_auto_list_quantity_repeat_selects_only_unchecked_rows(monkeypatch, tmp_path):
    target = tmp_path / "target.png"
    check = tmp_path / "check.png"
    target.write_bytes(b"target")
    check.write_bytes(b"check")
    rule = AutomationRule(
        rule_id="select_items",
        action_type="click",
        target_image=str(target),
        search_region=[0, 0, 200, 100],
        repeat_from_auto_list_quantity=True,
        auto_list_repeat_confirm_image=str(check),
        auto_list_repeat_confirm_region=[0, 0, 300, 100],
        auto_list_repeat_confirm_confidence=0.9,
        repeat_delay=0,
        wait_after=0,
    )
    executor = RuleExecutor()
    executor._auto_list_current_value = 3
    clicked = []

    def find_all(image_path, *_args, **_kwargs):
        if str(image_path) == str(check):
            return [(180, y, 0.99) for _, y in clicked]
        return [(20, 10, 0.99), (20, 30, 0.99), (20, 50, 0.99)]

    def click_at(active_rule, _kind, x, y, started, **_kwargs):
        clicked.append((x, y))
        return executor._make_result(active_rule, True, "클릭 완료", started)

    monkeypatch.setattr(executor, "_find_all_images_on_screen", find_all)
    monkeypatch.setattr(executor, "_execute_click_at", click_at)

    result = executor._execute_rule_with_retry(rule, max_retries=3)

    assert result.success is True
    assert clicked == [(20, 10), (20, 30), (20, 50)]
    assert "3/3" in result.message


def test_auto_list_quantity_repeat_skips_rows_that_are_already_checked(monkeypatch, tmp_path):
    target = tmp_path / "target.png"
    check = tmp_path / "check.png"
    target.write_bytes(b"target")
    check.write_bytes(b"check")
    rule = AutomationRule(
        rule_id="select_items",
        action_type="click",
        target_image=str(target),
        repeat_from_auto_list_quantity=True,
        auto_list_repeat_confirm_image=str(check),
        auto_list_repeat_confirm_region=[0, 0, 300, 100],
        repeat_delay=0,
        wait_after=0,
    )
    executor = RuleExecutor()
    executor._auto_list_current_value = 3
    checked_rows = [10]
    clicked = []

    def find_all(image_path, *_args, **_kwargs):
        if str(image_path) == str(check):
            return [(180, y, 0.99) for y in checked_rows]
        return [(20, 10, 0.99), (20, 30, 0.99), (20, 50, 0.99)]

    def click_at(active_rule, _kind, x, y, started, **_kwargs):
        clicked.append((x, y))
        checked_rows.append(y)
        return executor._make_result(active_rule, True, "클릭 완료", started)

    monkeypatch.setattr(executor, "_find_all_images_on_screen", find_all)
    monkeypatch.setattr(executor, "_execute_click_at", click_at)

    result = executor._execute_rule_with_retry(rule)

    assert result.success is True
    assert clicked == [(20, 30), (20, 50)]
    assert checked_rows == [10, 30, 50]


def test_auto_list_children_receive_current_item_context_and_restore_it(monkeypatch, tmp_path):
    image = tmp_path / "current_item.png"
    image.write_bytes(b"item")
    child = AutomationRule(rule_id="child", action_type="hotkey", action_keys=["enter"])
    parent = AutomationRule(
        rule_id="parent",
        action_type="auto_list",
        children=[child],
        auto_list_config={"items": [{"image": str(image), "name": "등록 항목"}]},
    )
    executor = RuleExecutor()
    executor._auto_list_current_value = 99
    executor._auto_list_current_item = {"name": "previous"}
    executor._auto_list_registered_items = [{"name": "previous registered"}]
    observed = []

    def execute_child(active_child, step_num):
        observed.append(
            (
                active_child.rule_id,
                step_num,
                executor._auto_list_current_value,
                dict(executor._auto_list_current_item or {}),
                [dict(item) for item in executor._auto_list_registered_items],
            )
        )
        return None

    monkeypatch.setattr(executor, "_execute_rule_tree_once", execute_child)

    result = executor._execute_child_rules_for_auto_list(
        parent,
        datetime.now(),
        7,
        {"image": str(image), "name": "환웅천검 1각", "confidence": 0.88},
    )

    assert result is None
    assert observed == [
        (
            "child",
            "목록-1",
            7,
            {"image": str(image), "name": "환웅천검 1각", "confidence": 0.88},
            [
                {
                    "image": str(image),
                    "name": "등록 항목",
                    "target_count": 1,
                    "confidence": 0.8,
                    "search_region": None,
                    "enabled": True,
                }
            ],
        )
    ]
    assert executor._auto_list_current_value == 99
    assert executor._auto_list_current_item == {"name": "previous"}
    assert executor._auto_list_registered_items == [{"name": "previous registered"}]


def test_auto_list_quantity_repeat_selects_all_registered_rows_only(monkeypatch, tmp_path):
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    unrelated = tmp_path / "unrelated.png"
    check = tmp_path / "check.png"
    for path in (first, second, unrelated, check):
        path.write_bytes(path.stem.encode("ascii"))
    rule = AutomationRule(
        rule_id="select_registered",
        action_type="click",
        target_image=str(unrelated),
        search_region=[0, 0, 200, 120],
        repeat_from_auto_list_quantity=True,
        auto_list_repeat_confirm_image=str(check),
        auto_list_repeat_confirm_region=[150, 0, 220, 120],
        repeat_delay=0,
        wait_after=0,
    )
    executor = RuleExecutor()
    executor._auto_list_current_value = 1
    executor._auto_list_current_item = {"image": str(first), "name": "1번"}
    executor._auto_list_registered_items = [
        {"image": str(first), "name": "1번", "confidence": 0.8},
        {"image": str(second), "name": "2번", "confidence": 0.9},
    ]
    clicked = []
    searched_targets = []

    def find_all(image_path, *_args, **_kwargs):
        path = str(image_path)
        if path == str(check):
            return [(180, y, 0.99) for _x, y in clicked]
        searched_targets.append(path)
        if path == str(first):
            return [(20, 10, 0.99), (20, 50, 0.98)]
        if path == str(second):
            return [(20, 30, 0.99)]
        if path == str(unrelated):
            raise AssertionError("등록되지 않은 이미지를 추출 대상으로 검색하면 안 됩니다")
        return []

    def click_at(active_rule, _kind, x, y, started, **_kwargs):
        clicked.append((x, y))
        return executor._make_result(active_rule, True, "클릭 완료", started)

    monkeypatch.setattr(executor, "_find_all_images_on_screen", find_all)
    monkeypatch.setattr(executor, "_execute_click_at", click_at)

    result = executor._execute_rule_with_retry(rule)

    assert result.success is True
    assert clicked == [(20, 10), (20, 30), (20, 50)]
    assert set(searched_targets) == {str(first), str(second)}
    assert str(unrelated) not in searched_targets
    assert "3/3" in result.message


def test_auto_list_quantity_repeat_prefers_each_registered_item_region(monkeypatch, tmp_path):
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    check = tmp_path / "check.png"
    for path in (first, second, check):
        path.write_bytes(path.stem.encode("ascii"))

    shared_region = [10, 20, 210, 320]
    rule = AutomationRule(
        rule_id="select_registered_in_list_only",
        action_type="click",
        target_image=str(first),
        search_region=[0, 0, 1920, 1080],
        repeat_from_auto_list_quantity=True,
        auto_list_repeat_confirm_image=str(check),
        auto_list_repeat_confirm_region=[150, 20, 220, 320],
        repeat_delay=0,
        wait_after=0,
    )
    executor = RuleExecutor()
    executor._auto_list_current_value = 2
    executor._auto_list_current_item = {
        "image": str(first),
        "name": "first",
        "confidence": 0.8,
        "search_region": shared_region,
    }
    executor._auto_list_registered_items = [
        {
            "image": str(first),
            "name": "first",
            "confidence": 0.8,
            "search_region": shared_region,
        },
        {
            "image": str(second),
            "name": "second",
            "confidence": 0.8,
            "search_region": shared_region,
        },
    ]
    clicked = []
    searched_regions = []

    def find_all(image_path, *_args, **kwargs):
        if str(image_path) == str(check):
            return [(180, y, 0.99) for _x, y in clicked]
        searched_regions.append(kwargs.get("search_region"))
        if str(image_path) == str(first):
            return [(20, 40, 0.99)]
        if str(image_path) == str(second):
            return [(20, 60, 0.99)]
        return []

    def click_at(active_rule, _kind, x, y, started, **_kwargs):
        clicked.append((x, y))
        return executor._make_result(active_rule, True, "click complete", started)

    monkeypatch.setattr(executor, "_find_all_images_on_screen", find_all)
    monkeypatch.setattr(executor, "_execute_click_at", click_at)

    result = executor._execute_rule_with_retry(rule)

    assert result.success is True
    assert clicked == [(20, 40), (20, 60)]
    assert searched_regions == [shared_region, shared_region]


def test_auto_list_quantity_repeat_never_falls_back_to_child_action_region(monkeypatch, tmp_path):
    registered = tmp_path / "registered.png"
    check = tmp_path / "check.png"
    registered.write_bytes(b"registered")
    check.write_bytes(b"check")

    rule = AutomationRule(
        rule_id="registered_range_isolated",
        action_type="click",
        target_image=str(registered),
        action_x=900,
        action_y=700,
        search_region=[700, 600, 1000, 800],
        search_radius=123,
        repeat_from_auto_list_quantity=True,
        auto_list_repeat_confirm_image=str(check),
        auto_list_repeat_confirm_region=[150, 0, 220, 120],
        repeat_delay=0,
        wait_after=0,
    )
    executor = RuleExecutor()
    executor._auto_list_current_value = 1
    executor._auto_list_registered_items = [
        {
            "image": str(registered),
            "name": "registered",
            "confidence": 0.8,
            "search_region": None,
        }
    ]
    clicked = []
    searches = []

    def find_all(image_path, *_args, **kwargs):
        if str(image_path) == str(check):
            return [(180, y, 0.99) for _x, y in clicked]
        searches.append((kwargs.get("search_region"), kwargs.get("search_radius")))
        return [(20, 40, 0.99)]

    def click_at(active_rule, _kind, x, y, started, **_kwargs):
        clicked.append((x, y))
        return executor._make_result(active_rule, True, "click complete", started)

    monkeypatch.setattr(executor, "_find_all_images_on_screen", find_all)
    monkeypatch.setattr(executor, "_execute_click_at", click_at)

    result = executor._execute_rule_with_retry(rule)

    assert result.success is True
    assert clicked == [(20, 40)]
    assert searches == [(None, 0)]


def test_auto_list_quantity_repeat_skips_generic_next_screen_infinite_wait(monkeypatch, tmp_path):
    target = tmp_path / "target.png"
    next_target = tmp_path / "next.png"
    target.write_bytes(b"target")
    next_target.write_bytes(b"next")
    rule = AutomationRule(
        rule_id="selector",
        action_type="click",
        target_image=str(target),
        repeat_from_auto_list_quantity=True,
    )
    next_rule = AutomationRule(
        rule_id="next-action",
        action_type="click",
        target_image=str(next_target),
    )
    executor = RuleExecutor()
    executor._auto_list_current_value = 1
    executor._auto_list_registered_items = [
        {"image": str(target), "name": "target", "confidence": 0.8}
    ]

    monkeypatch.setattr(
        executor,
        "_execute_auto_list_quantity_image_clicks",
        lambda active_rule, started, *_args, **_kwargs: executor._make_result(
            active_rule,
            True,
            "registered rows selected",
            started,
        ),
    )
    monkeypatch.setattr(
        executor,
        "_find_image_on_screen",
        lambda *_args, **_kwargs: pytest.fail("specialized selector must not enter generic next-screen wait"),
    )

    result = executor._execute_rule_with_retry(
        rule,
        next_target_images=[str(next_target)],
        next_rule=next_rule,
    )

    assert result.success is True
    assert result.message == "registered rows selected"


def test_auto_list_quantity_repeat_fails_when_registered_template_is_missing(tmp_path):
    target = tmp_path / "target.png"
    check = tmp_path / "check.png"
    target.write_bytes(b"target")
    check.write_bytes(b"check")
    missing = tmp_path / "missing_registered.png"
    rule = AutomationRule(
        rule_id="select_registered",
        action_type="click",
        target_image=str(target),
        repeat_from_auto_list_quantity=True,
        auto_list_repeat_confirm_image=str(check),
        auto_list_repeat_confirm_region=[0, 0, 200, 100],
    )
    executor = RuleExecutor()
    executor._auto_list_current_value = 1
    executor._auto_list_current_item = {"image": str(target), "name": "현재 항목"}
    executor._auto_list_registered_items = [
        {"image": str(target), "name": "정상 항목", "confidence": 0.8},
        {"image": str(missing), "name": "누락 항목", "confidence": 0.8},
    ]

    result = executor._execute_rule_with_retry(rule)

    assert result.success is False
    assert "자동 목록 추출 이미지 파일 없음" in result.message
    assert "누락 항목" in result.message


def test_partial_run_selection_recovers_nested_auto_list_parent_context(monkeypatch, tmp_path):
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    check = tmp_path / "check.png"
    for path in (first, second, check):
        path.write_bytes(path.stem.encode("ascii"))

    selector = AutomationRule(
        rule_id="partial-selector",
        action_type="click",
        description="등록 항목 선택",
        target_image=str(first),
        search_region=[0, 0, 200, 120],
        repeat_from_auto_list_quantity=True,
        auto_list_repeat_confirm_image=str(check),
        auto_list_repeat_confirm_region=[150, 0, 220, 120],
        repeat_delay=0,
        wait_after=0,
    )
    wrapper = AutomationRule(
        rule_id="nested-wrapper",
        action_type="wait",
        children=[selector],
    )
    parent = AutomationRule(
        rule_id="auto-list-parent",
        action_type="auto_list",
        children=[wrapper],
        auto_list_config={
            "items": [
                {"image": str(first), "name": "첫 번째", "confidence": 0.8},
                {"image": str(second), "name": "두 번째", "confidence": 0.8},
            ]
        },
    )
    partial_selector = AutomationRule.from_dict(selector.to_dict(), templates_dir=tmp_path)
    executor = RuleExecutor()
    executor._current_plan = SimpleNamespace(_original_initial_rules=[parent])
    clicked = []

    def find_all(image_path, *_args, **_kwargs):
        if str(image_path) == str(check):
            return [(180, y, 0.99) for _x, y in clicked]
        if str(image_path) == str(first):
            return [(20, 10, 0.99)]
        if str(image_path) == str(second):
            return [(20, 30, 0.99)]
        return []

    def click_at(active_rule, _kind, x, y, started, **_kwargs):
        clicked.append((x, y))
        return executor._make_result(active_rule, True, "클릭 완료", started)

    monkeypatch.setattr(executor, "_find_all_images_on_screen", find_all)
    monkeypatch.setattr(executor, "_execute_click_at", click_at)

    result = executor._execute_rule_with_retry(partial_selector)

    assert result.success is True
    assert clicked == [(20, 10), (20, 30)]
    assert executor._auto_list_current_value is None
    assert executor._auto_list_registered_items == []


def test_partial_run_selection_recovers_context_from_runtime_plan_when_original_tree_is_absent(
    monkeypatch,
    tmp_path,
):
    target = tmp_path / "target.png"
    check = tmp_path / "check.png"
    target.write_bytes(b"target")
    check.write_bytes(b"check")
    shared_region = [10, 20, 210, 320]
    selector = AutomationRule(
        rule_id="partial-selector-runtime-tree",
        action_type="click",
        target_image=str(target),
        search_region=[700, 600, 1000, 800],
        repeat_from_auto_list_quantity=True,
        auto_list_repeat_confirm_image=str(check),
        auto_list_repeat_confirm_region=[150, 20, 220, 320],
        repeat_delay=0,
        wait_after=0,
    )
    parent = AutomationRule(
        rule_id="auto-list-parent-runtime-tree",
        action_type="auto_list",
        children=[selector],
        auto_list_config={
            "item_search_region": shared_region,
            "items": [
                {
                    "image": str(target),
                    "name": "registered",
                    "confidence": 0.8,
                    "search_region": shared_region,
                }
            ],
        },
    )
    partial_selector = AutomationRule.from_dict(selector.to_dict(), templates_dir=tmp_path)
    executor = RuleExecutor()
    executor._current_plan = SimpleNamespace(initial_rules=[parent])
    clicked = []
    searched_regions = []

    def find_all(image_path, *_args, **kwargs):
        if str(image_path) == str(check):
            return [(180, y, 0.99) for _x, y in clicked]
        searched_regions.append(kwargs.get("search_region"))
        return [(20, 40, 0.99)]

    def click_at(active_rule, _kind, x, y, started, **_kwargs):
        clicked.append((x, y))
        return executor._make_result(active_rule, True, "click complete", started)

    monkeypatch.setattr(executor, "_find_all_images_on_screen", find_all)
    monkeypatch.setattr(executor, "_execute_click_at", click_at)

    result = executor._execute_rule_with_retry(partial_selector)

    assert result.success is True
    assert clicked == [(20, 40)]
    assert searched_regions == [shared_region]


def test_auto_list_quantity_repeat_caps_each_extraction_batch_at_ten(monkeypatch, tmp_path):
    target = tmp_path / "target.png"
    check = tmp_path / "check.png"
    target.write_bytes(b"target")
    check.write_bytes(b"check")
    rule = AutomationRule(
        rule_id="select_registered_batch",
        action_type="click",
        target_image=str(target),
        search_region=[0, 0, 200, 300],
        repeat_from_auto_list_quantity=True,
        auto_list_repeat_confirm_image=str(check),
        auto_list_repeat_confirm_region=[150, 0, 220, 300],
        repeat_delay=0,
        wait_after=0,
    )
    executor = RuleExecutor()
    executor._auto_list_current_value = 10
    executor._auto_list_current_item = {"image": str(target), "name": "현재 항목"}
    executor._auto_list_registered_items = [
        {"image": str(target), "name": "등록 항목", "confidence": 0.8}
    ]
    clicked = []
    target_rows = [(20, 10 + (index * 18), 0.99) for index in range(12)]

    def find_all(image_path, *_args, **_kwargs):
        if str(image_path) == str(check):
            return [(180, y, 0.99) for _x, y in clicked]
        return target_rows

    def click_at(active_rule, _kind, x, y, started, **_kwargs):
        clicked.append((x, y))
        return executor._make_result(active_rule, True, "클릭 완료", started)

    monkeypatch.setattr(executor, "_find_all_images_on_screen", find_all)
    monkeypatch.setattr(executor, "_execute_click_at", click_at)

    result = executor._execute_rule_with_retry(rule)

    assert result.success is True
    assert clicked == [(x, y) for x, y, _score in target_rows[:10]]
    assert "10/10" in result.message


def test_auto_list_quantity_repeat_uses_current_item_and_ignores_other_row_checks(
    monkeypatch,
    tmp_path,
):
    import src.player.rule_executor as executor_module

    child_target = tmp_path / "configured_child_target.png"
    current_item = tmp_path / "current_auto_list_item.png"
    check = tmp_path / "check.png"
    child_target.write_bytes(b"child")
    current_item.write_bytes(b"item")
    check.write_bytes(b"check")
    rule = AutomationRule(
        rule_id="select_items",
        action_type="click",
        target_image=str(child_target),
        search_region=[0, 0, 200, 100],
        repeat_from_auto_list_quantity=True,
        auto_list_repeat_confirm_image=str(check),
        auto_list_repeat_confirm_region=[150, 0, 220, 100],
        repeat_delay=0,
        wait_after=0,
    )
    executor = RuleExecutor()
    executor._auto_list_current_value = 1
    executor._auto_list_current_item = {
        "image": str(current_item),
        "name": "현재 항목",
        "confidence": 0.87,
    }
    clicked = []
    searched = []

    def find_all(image_path, confidence, *_args, **_kwargs):
        searched.append((str(image_path), confidence))
        if str(image_path) == str(check):
            # 바로 다음 행의 체크는 현재 행 체크로 계산하면 안 된다.
            checks = [(180, 30, 0.99)]
            if clicked:
                checks.append((180, 10, 0.99))
            return checks
        if str(image_path) == str(current_item):
            return [(20, 10, 0.99)]
        if str(image_path) == str(child_target):
            raise AssertionError("하위 액션 이미지를 현재 자동 목록 항목보다 우선하면 안 됩니다")
        return []

    def click_at(active_rule, _kind, x, y, started, **_kwargs):
        clicked.append((x, y))
        return executor._make_result(active_rule, True, "클릭 완료", started)

    monkeypatch.setattr(executor, "_find_all_images_on_screen", find_all)
    monkeypatch.setattr(executor, "_execute_click_at", click_at)
    monkeypatch.setattr(
        executor_module,
        "_get_cached_template",
        lambda image_path: (
            np.zeros((9, 11), dtype=np.uint8),
            9,
            11,
        )
        if str(image_path) == str(check)
        else (np.zeros((21, 70), dtype=np.uint8), 21, 70),
    )

    result = executor._execute_rule_with_retry(rule)

    assert result.success is True
    assert clicked == [(20, 10)]
    assert any(path == str(current_item) and confidence == 0.87 for path, confidence in searched)
    assert all(path != str(child_target) for path, _confidence in searched)
    assert "1/1" in result.message


def test_auto_list_quantity_repeat_requires_check_on_clicked_row(monkeypatch, tmp_path):
    target = tmp_path / "target.png"
    check = tmp_path / "check.png"
    target.write_bytes(b"target")
    check.write_bytes(b"check")
    rule = AutomationRule(
        rule_id="select_items",
        action_type="click",
        target_image=str(target),
        repeat_from_auto_list_quantity=True,
        auto_list_repeat_confirm_image=str(check),
        auto_list_repeat_confirm_region=[0, 0, 300, 100],
        timeout=0.01,
        repeat_delay=0,
        wait_after=0,
    )
    executor = RuleExecutor()
    executor._auto_list_current_value = 1
    clicked = []

    def find_all(image_path, *_args, **_kwargs):
        if str(image_path) == str(check):
            return [] if not clicked else [(180, 80, 0.99)]
        return [(20, 10, 0.99)]

    def click_at(active_rule, _kind, x, y, started, **_kwargs):
        clicked.append((x, y))
        return executor._make_result(active_rule, True, "클릭 완료", started)

    monkeypatch.setattr(executor, "_find_all_images_on_screen", find_all)
    monkeypatch.setattr(executor, "_execute_click_at", click_at)

    result = executor._execute_rule_with_retry(rule)

    assert result.success is False
    assert clicked == [(20, 10)]
    assert "선택 체크가 증가하지 않았습니다" in result.message


def test_auto_list_quantity_repeat_rejects_execution_without_auto_list_context(tmp_path):
    target = tmp_path / "target.png"
    check = tmp_path / "check.png"
    target.write_bytes(b"target")
    check.write_bytes(b"check")
    rule = AutomationRule(
        rule_id="select_items",
        action_type="click",
        target_image=str(target),
        repeat_from_auto_list_quantity=True,
        auto_list_repeat_confirm_image=str(check),
        auto_list_repeat_confirm_region=[0, 0, 300, 100],
    )

    result = RuleExecutor()._execute_rule_with_retry(rule)

    assert result.success is False
    assert "자동 목록 처리의 하위 액션" in result.message


def test_auto_list_quantity_repeat_ui_requires_check_image_and_region():
    source = Path("src/ui/player_view.py").read_text(encoding="utf-8")
    assert 'text="자동 목록 등록 항목 전부 선택"' in source
    assert 'return "↻ 등록항목"' in source
    assert 'text="체크 이미지"' in source
    assert 'text="확인 범위"' in source
    assert 'confidence_row = ctk.CTkFrame(card, fg_color="transparent")' in source
    assert 'text="체크 이미지 인식률"' in source
    assert 'messagebox.showerror("오류", "선택 체크 이미지를 설정하세요"' in source
    assert 'messagebox.showerror("오류", "선택 체크 확인 범위를 설정하세요"' in source


def test_auto_list_repeat_region_selector_restores_dialog_after_selection():
    source = Path("src/ui/player_view.py").read_text(encoding="utf-8", errors="ignore")
    start = source.index("def _build_auto_list_quantity_repeat_controls")
    end = source.index("def _manual_partial_start_index", start)
    body = source[start:end]

    assert "def on_select(x1, y1, x2, y2):" in body
    assert 'confirm_region["value"] = [int(x1), int(y1), int(x2), int(y2)]' in body
    assert "finally:\n                restore_dialog()" in body
    assert "dialog.after(100, launch_selector)" in body
    assert "restore_dialog()\n                logger.exception" in body


def test_auto_list_ui_button_is_below_random_key_button():
    source = Path("src/ui/player_view.py").read_text(encoding="utf-8")
    plan_random = source.index('text="🎲 랜덤키입력"')
    plan_auto = source.index('text="자동 목록 처리"', plan_random)
    assert plan_auto > plan_random
    assert "command=self._add_auto_list_action" in source[plan_auto:plan_auto + 500]


def test_auto_list_dialog_save_immediately_persists_parent_editor():
    source = Path("src/ui/player_view.py").read_text(encoding="utf-8")
    plan_start = source.index("    def _add_auto_list_action(self):")
    plan_end = source.index("    def _add_mouse_action(self):", plan_start)
    sequence_start = source.index("    def _add_auto_list_action(self):", plan_end)
    sequence_end = source.index("    def _add_mouse_action(self):", sequence_start)

    plan_block = source[plan_start:plan_end]
    sequence_block = source[sequence_start:sequence_end]
    assert plan_block.count("self._save_plan(show_message=False)") == 6
    assert sequence_block.count("self._save_sequence_silent()") == 6
