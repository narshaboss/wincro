from pathlib import Path

from src.analyzer.automation_models import AutomationRule
from src.database.models import Action
from src.player.rule_executor import RuleExecutor


ROOT = Path(__file__).resolve().parents[1]
PLAYER_VIEW = ROOT / "src" / "ui" / "player_view.py"
RULE_EXECUTOR = ROOT / "src" / "player" / "rule_executor.py"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def test_click_until_image_disappears_persists_for_rule_and_action(tmp_path):
    target = tmp_path / "target.png"
    target.write_bytes(b"fake")

    rule = AutomationRule(
        action_type="click",
        target_image=str(target),
        repeat_count=7,
        repeat_delay=1.2,
        click_until_image_disappears=True,
        click_until_image_disappears_delay=0.15,
    )
    saved_rule = rule.to_dict()

    assert saved_rule["click_until_image_disappears"] is True
    assert saved_rule["click_until_image_disappears_delay"] == 0.15
    restored_rule = AutomationRule.from_dict(saved_rule, templates_dir=tmp_path)
    assert restored_rule.click_until_image_disappears is True
    assert restored_rule.click_until_image_disappears_delay == 0.15
    legacy_rule = AutomationRule.from_dict(
        {
            "action_type": "click",
            "target_image": str(target),
            "repeat_delay": 0.9,
            "click_until_image_disappears": True,
        },
        templates_dir=tmp_path,
    )
    assert legacy_rule.click_until_image_disappears_delay == 0.9

    action = Action.from_dict(
        {
            "action_type": "click",
            "target_image": str(target),
            "repeat_count": 7,
            "repeat_delay": 1.1,
            "click_until_image_disappears": True,
            "click_until_image_disappears_delay": 0.2,
        }
    )

    assert action.click_until_image_disappears is True
    assert action.to_dict()["click_until_image_disappears"] is True
    assert action.click_until_image_disappears_delay == 0.2
    assert action.to_dict()["click_until_image_disappears_delay"] == 0.2
    legacy_action = Action.from_dict(
        {
            "action_type": "click",
            "target_image": str(target),
            "repeat_delay": 0.8,
            "click_until_image_disappears": True,
        }
    )
    assert legacy_action.click_until_image_disappears_delay == 0.8


def test_player_repeat_dialog_exposes_disappear_click_option():
    text = _text(PLAYER_VIEW)

    assert "click_until_delay_entry" in text
    assert "click_until_image_disappears_delay" in text
    assert "전용 반복 대기시간:" in text
    assert "이미지가 사라질 때까지 반복 클릭" in text
    assert "켜면 매번 이미지를 다시 찾아 클릭합니다." in text
    assert 'text="사라짐" if until_disappears' not in text
    assert "_format_repeat_button_text(repeat_count, until_disappears)" in text
    assert 'if until_disappears' in text
    assert 'COLORS["accent_orange"]' in text
    assert 'click_until_image_disappears=getattr(action, "click_until_image_disappears", False)' in text
    assert 'getattr(rule, "target_images", None)' in text


def test_rule_executor_clicks_until_image_disappears_by_researching_target():
    text = _text(RULE_EXECUTOR)

    assert "IMAGE_CLICK_UNTIL_DISAPPEAR_MIN_CLICKS = 5" in text
    assert "IMAGE_CLICK_UNTIL_DISAPPEAR_MAX_SECONDS = 30.0" in text
    assert "IMAGE_CLICK_UNTIL_DISAPPEAR_MISS_CONFIRM = 2" in text
    assert "def _find_rule_image_click_target(" in text
    assert "def _execute_click_until_image_disappears(" in text
    assert "disappear_absent_misses = 0" in text
    assert "이미지 없음 (사라짐 확인)" in text
    assert "def _execute_child_rules_for_repeat_click(" in text
    assert "self._mark_child_rules_handled_by_parent(rule)" in text
    assert "target = self._find_rule_image_click_target(rule, valid_images)" in text
    assert "click_until_image_disappears_delay" in text
    assert 'and (getattr(rule, "target_image", None) or getattr(rule, "target_images", None))' in text


def test_click_until_image_disappears_uses_dedicated_repeat_delay():
    executor = RuleExecutor()
    rule = AutomationRule(
        action_type="click",
        target_image="target.png",
        repeat_delay=9.0,
        repeat_delay_random=True,
        repeat_delay_random_range=5.0,
        click_until_image_disappears=True,
        click_until_image_disappears_delay=0.25,
    )

    assert executor._repeat_delay_for_rule(rule) == 0.25

    class LegacyRule:
        click_until_image_disappears = True
        repeat_delay = 0.75

    assert executor._repeat_delay_for_rule(LegacyRule()) == 0.75


def test_click_until_image_disappears_repeats_only_child_actions(tmp_path, monkeypatch):
    target = tmp_path / "target.png"
    target.write_bytes(b"fake")

    parent = AutomationRule(
        action_type="click",
        target_image=str(target),
        repeat_count=5,
        click_until_image_disappears=True,
    )
    child = AutomationRule(action_type="key_press", action_keys=["enter"], wait_after=0, description="child-enter")
    sibling = AutomationRule(action_type="wait", action_text="0", description="sibling")
    parent.children = [child]

    executor = RuleExecutor()
    executed_rule_ids = []
    executor.set_callbacks(on_rule_executed=lambda result: executed_rule_ids.append(result.rule_id))

    found_target = {
        "x": 10,
        "y": 20,
        "confidence": 1.0,
        "image": str(target),
        "method": "test",
        "locations": [(10, 20, 1.0)],
    }
    search_results = iter([found_target, found_target, None, None])
    monkeypatch.setattr(executor, "_find_rule_image_click_target", lambda rule, images: next(search_results))

    click_calls = []
    execution_order = []
    key_calls = []

    class FakeInputController:
        def press(self, key):
            key_calls.append(key)

    def fake_click(rule, action_type, click_x, click_y, start_time, *, image_click=False):
        click_calls.append((rule.rule_id, action_type, click_x, click_y, image_click))
        execution_order.append("click")
        return executor._make_result(rule, True, "click 완료", start_time)

    monkeypatch.setattr(executor, "_execute_click_at", fake_click)
    monkeypatch.setattr("src.player.rule_executor.get_input_controller", lambda: FakeInputController())
    original_execute_rule_tree_once = executor._execute_rule_tree_once

    def record_child_order(rule, step_num):
        result = original_execute_rule_tree_once(rule, step_num)
        if rule.rule_id == child.rule_id:
            execution_order.append("child")
        return result

    monkeypatch.setattr(executor, "_execute_rule_tree_once", record_child_order)

    result = executor._execute_rule_with_retry(parent, max_retries=1, step_num="1")

    assert result.success is True
    assert click_calls == [(parent.rule_id, "click", 10, 20, True)] * 2
    assert executed_rule_ids == [child.rule_id, child.rule_id]
    assert key_calls == ["enter", "enter"]
    assert execution_order == ["click", "child", "click", "child"]
    assert child.rule_id in executor._child_rules_executed_with_parent
    assert sibling.rule_id not in executor._child_rules_executed_with_parent


def test_click_until_image_disappears_guard_limit_does_not_stop_playlist(tmp_path, monkeypatch):
    target = tmp_path / "target.png"
    target.write_bytes(b"fake")

    parent = AutomationRule(
        action_type="double_click",
        target_image=str(target),
        repeat_count=1,
        repeat_delay=0,
        click_until_image_disappears=True,
    )
    child = AutomationRule(action_type="hotkey", action_keys=["enter"], wait_after=0, description="child-enter")
    parent.children = [child]

    executor = RuleExecutor()
    found_target = {
        "x": 10,
        "y": 20,
        "confidence": 1.0,
        "image": str(target),
        "method": "test",
        "locations": [(10, 20, 1.0)],
    }
    monkeypatch.setattr(executor, "_find_rule_image_click_target", lambda rule, images: found_target)
    monkeypatch.setattr(executor, "_repeat_delay_for_rule", lambda rule: 0)

    click_calls = []
    child_calls = []

    def fake_click(rule, action_type, click_x, click_y, start_time, *, image_click=False):
        click_calls.append((rule.rule_id, action_type, click_x, click_y, image_click))
        return executor._make_result(rule, True, "click 완료", start_time)

    monkeypatch.setattr(executor, "_execute_click_at", fake_click)
    monkeypatch.setattr(executor, "_execute_child_rules_for_repeat_click", lambda rule, start_time: child_calls.append(rule.rule_id) or None)

    result = executor._execute_rule_with_retry(parent, max_retries=1, step_num="1")

    assert result.success is True
    assert "한도 도달 후 진행" in result.message
    assert len(click_calls) == 5
    assert child_calls == [parent.rule_id] * 5
    assert child.rule_id in executor._child_rules_executed_with_parent
