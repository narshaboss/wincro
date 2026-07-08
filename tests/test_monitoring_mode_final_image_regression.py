import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.analyzer.automation_models import AutomationPlan, AutomationRule
from src.player.rule_executor import ExecutionProgress, ExecutionState, RuleExecutor, RuleExecutionResult


def _touch(path: Path) -> str:
    path.write_bytes(b"placeholder")
    return str(path)


def test_monitoring_jump_from_partial_run_can_switch_to_original_plan_target(monkeypatch):
    executor = RuleExecutor()
    target = AutomationRule(rule_id="target", action_type="hotkey", description="다이쇼 시작", wait_after=0)
    monitor = AutomationRule(
        rule_id="monitor",
        action_type="click",
        description="클리어 확인버튼",
        is_monitoring_mode=True,
        monitoring_watches=[{"image": "watch.png", "goto_index": 0}],
        wait_after=0,
    )
    after = AutomationRule(rule_id="after", action_type="hotkey", description="다음 액션", wait_after=0)
    partial_plan = AutomationPlan(
        name="partial",
        initial_rules=[monitor, after],
        monitoring_rules=[],
    )
    partial_plan._original_initial_rules = [target, monitor, after]
    executor._current_plan = partial_plan
    executor._state = ExecutionState.RUNNING_INITIAL
    executor._progress = ExecutionProgress(state=ExecutionState.RUNNING_INITIAL)
    executor._stop_event.clear()
    executor._pause_event.set()
    executed = []

    def fake_monitoring(rule, all_rules, current_index, step_num=""):
        return RuleExecutionResult(
            rule_id=rule.rule_id,
            success=True,
            message="jump",
            monitoring_jump_index=0,
        )

    def fake_execute(rule, *args, **kwargs):
        executed.append(rule.description)
        executor._stop_event.set()
        return RuleExecutionResult(rule_id=rule.rule_id, success=True, message="ok")

    monkeypatch.setattr(executor, "_execute_monitoring_mode", fake_monitoring)
    monkeypatch.setattr(executor, "_execute_rule_with_retry", fake_execute)
    monkeypatch.setattr(executor, "_update_progress", lambda *args, **kwargs: None)
    monkeypatch.setattr(executor, "_wait_for_resume", lambda: False)

    executor._execution_loop()

    assert executed == ["다이쇼 시작"]


def test_monitoring_jump_rule_id_can_continue_from_child_action(monkeypatch):
    executor = RuleExecutor()
    child = AutomationRule(rule_id="child", action_type="hotkey", description="하위 시작", wait_after=0)
    parent = AutomationRule(rule_id="parent", action_type="click", description="상위", children=[child], wait_after=0)
    monitor = AutomationRule(
        rule_id="monitor_child",
        action_type="click",
        description="모니터",
        is_monitoring_mode=True,
        monitoring_watches=[{"image": "watch.png", "goto_index": 1, "goto_rule_id": "child"}],
        wait_after=0,
    )
    partial_plan = AutomationPlan(name="partial", initial_rules=[monitor], monitoring_rules=[])
    partial_plan._original_initial_rules = [parent, monitor]
    executor._current_plan = partial_plan
    executor._state = ExecutionState.RUNNING_INITIAL
    executor._progress = ExecutionProgress(state=ExecutionState.RUNNING_INITIAL)
    executor._stop_event.clear()
    executor._pause_event.set()
    executed = []

    def fake_monitoring(rule, all_rules, current_index, step_num=""):
        return RuleExecutionResult(
            rule_id=rule.rule_id,
            success=True,
            message="jump child",
            monitoring_jump_index=1,
            monitoring_jump_rule_id="child",
        )

    def fake_execute(rule, *args, **kwargs):
        executed.append(rule.description)
        executor._stop_event.set()
        return RuleExecutionResult(rule_id=rule.rule_id, success=True, message="ok")

    monkeypatch.setattr(executor, "_execute_monitoring_mode", fake_monitoring)
    monkeypatch.setattr(executor, "_execute_rule_with_retry", fake_execute)
    monkeypatch.setattr(executor, "_update_progress", lambda *args, **kwargs: None)
    monkeypatch.setattr(executor, "_wait_for_resume", lambda: False)

    executor._execution_loop()

    assert executed == ["하위 시작"]


def test_monitoring_route_goto_index_uses_original_plan_not_partial_remainder(tmp_path, monkeypatch, caplog):
    executor = RuleExecutor()
    caplog.set_level(logging.INFO)
    final_image = _touch(tmp_path / "final.png")
    route_image = _touch(tmp_path / "route.png")
    original_first = AutomationRule(
        rule_id="original_first",
        action_type="double_click",
        description="다이쇼 시작",
        wait_after=0,
    )
    partial_first = AutomationRule(
        rule_id="partial_first",
        action_type="hotkey",
        description="특화모드 하위 9번",
        action_keys=["9"],
        wait_after=0,
    )
    monitor = AutomationRule(
        rule_id="monitor",
        action_type="double_click",
        description="클리어 확인버튼",
        target_image=final_image,
        is_monitoring_mode=True,
        monitoring_watches=[{"image": route_image, "goto_index": 0}],
        wait_after=0,
    )
    executor._current_plan = SimpleNamespace(
        initial_rules=[partial_first, monitor],
        _original_initial_rules=[original_first, partial_first, monitor],
    )
    executor._current_step_num = "7"

    def fake_find(image_path, confidence, search_region=None, verify_color=False, verify_brightness=False):
        name = Path(image_path).name
        if name == "route.png":
            return (11, 22, 0.96)
        return None

    monkeypatch.setattr(executor, "_find_image_on_screen", fake_find)
    monkeypatch.setattr(executor._stop_event, "wait", lambda timeout=None: False)

    result = executor._execute_monitoring_mode(monitor, [partial_first, monitor], 1, step_num="7")

    assert result.success is True
    assert result.monitoring_jump_index == 0
    assert result.monitoring_jump_rule_id == "original_first"
    assert "partial_first" not in result.monitoring_jump_rule_id
    assert "rule_id=original_first" in caplog.text
    assert "goto_index=0" in caplog.text
    assert "[모니터링점프상세]" in caplog.text
    assert "monitor_image=route.png" in caplog.text
    assert "target_rule_id=original_first" in caplog.text
    assert "condition_result=none" in caplog.text
    assert "condition_decision=jump" in caplog.text
    assert "현재목록=범위밖" in caplog.text


def test_monitoring_stop_logs_current_wait_and_last_jump_context(tmp_path, monkeypatch, caplog):
    executor = RuleExecutor()
    caplog.set_level(logging.INFO)
    final_image = _touch(tmp_path / "final.png")
    wait_image = _touch(tmp_path / "wait.png")
    rule = AutomationRule(
        rule_id="monitor_wait",
        action_type="click",
        description="wait target",
        target_image=final_image,
        is_monitoring_mode=True,
        monitoring_watches=[{"image": wait_image, "goto_index": 0}],
    )
    executor._last_monitoring_route_detail = {
        "monitor_image": "trigger.png",
        "target_step": "80",
        "target_rule_id": "target_rule",
        "target_name": "target action",
    }
    executor._current_plan = SimpleNamespace(initial_rules=[AutomationRule(rule_id="target_rule", action_type="click")])

    monkeypatch.setattr(executor, "_find_image_on_screen", lambda *args, **kwargs: None)
    monkeypatch.setattr(executor, "_wait_for_resume", lambda: False)
    monkeypatch.setattr(executor._stop_event, "wait", lambda timeout=None: True)

    result = executor._execute_monitoring_mode(rule, [], 0, step_num="8")

    assert result.success is False
    assert result.message == "실행 중지됨"
    assert "[모니터링중단상세]" in caplog.text
    assert "reason=monitoring_wait_stop" in caplog.text
    assert "current_wait=(action=[8] wait target" in caplog.text
    assert "last_jump=(monitor_image=trigger.png" in caplog.text
    assert "target_step=80" in caplog.text


def test_monitoring_mode_stops_when_final_image_is_found(tmp_path, monkeypatch):
    executor = RuleExecutor()
    final_image = _touch(tmp_path / "final.png")
    monitor_image = _touch(tmp_path / "watch.png")
    rule = AutomationRule(
        action_type="click",
        target_image=final_image,
        confidence=0.88,
        is_monitoring_mode=True,
        monitoring_watches=[
            {
                "image": monitor_image,
                "goto_index": 0,
                "monitor_actions": [{"type": "키 입력", "keys": ["enter"]}],
            }
        ],
    )
    searched = []

    def fake_find(image_path, confidence, search_region=None, verify_color=False, verify_brightness=False):
        searched.append((Path(image_path).name, confidence, search_region, verify_color, verify_brightness))
        if Path(image_path).name == "final.png":
            return (10, 20, 0.92)
        return None

    monkeypatch.setattr(executor, "_find_image_on_screen", fake_find)
    monkeypatch.setattr(
        executor,
        "_execute_monitor_action_sequence",
        lambda *args, **kwargs: pytest.fail("monitor actions must not run after final image is found"),
    )

    result = executor._execute_monitoring_mode(rule, [], 0, step_num="1")

    assert result.success is True
    assert result.message == "모니터링 완료 - 최종이미지 발견"
    assert searched == [("final.png", 0.88, None, False, False)]


def test_monitoring_mode_ignores_legacy_base_watch_without_route_target(tmp_path, monkeypatch):
    executor = RuleExecutor()
    final_image = _touch(tmp_path / "final.png")
    monitor_image = _touch(tmp_path / "watch.png")
    rule = AutomationRule(
        action_type="click",
        target_image=final_image,
        is_monitoring_mode=True,
        monitoring_watches=[
            {
                "image": monitor_image,
                "confidence": 0.77,
                "monitor_actions": [{"type": "키 입력", "keys": ["enter"]}],
            }
        ],
    )
    searched = []

    def fake_find(image_path, confidence, search_region=None, verify_color=False, verify_brightness=False):
        searched.append(Path(image_path).name)
        return None

    monkeypatch.setattr(executor, "_find_image_on_screen", fake_find)
    monkeypatch.setattr(
        executor,
        "_execute_monitor_action_sequence",
        lambda *args, **kwargs: pytest.fail("legacy base-watch actions must not run"),
    )
    monkeypatch.setattr(executor._stop_event, "wait", lambda timeout=None: True)

    result = executor._execute_monitoring_mode(rule, [], 0, step_num="2")

    assert result.success is False
    assert result.message == "모니터링 이미지가 설정되지 않음"
    assert searched == []


def test_monitoring_base_watch_no_longer_self_triggers_when_same_as_final_image(tmp_path, monkeypatch):
    executor = RuleExecutor()
    shared_image = _touch(tmp_path / "shared.png")
    rule = AutomationRule(
        action_type="click",
        target_image=shared_image,
        is_monitoring_mode=True,
        monitoring_watches=[
            {
                "image": shared_image,
                "monitor_actions": [{"type": "키 입력", "keys": ["enter"]}],
            }
        ],
    )

    monkeypatch.setattr(executor, "_find_image_on_screen", lambda *args, **kwargs: pytest.fail("legacy base watch must not search"))
    monkeypatch.setattr(
        executor,
        "_execute_monitor_action_sequence",
        lambda *args, **kwargs: pytest.fail("legacy base-watch actions must not run"),
    )

    result = executor._execute_monitoring_mode(rule, [], 0)

    assert result.success is False
    assert result.message == "모니터링 이미지가 설정되지 않음"


def test_monitoring_route_image_requests_main_loop_jump_and_exits_monitoring(tmp_path, monkeypatch):
    executor = RuleExecutor()
    final_image = _touch(tmp_path / "final.png")
    base_image = _touch(tmp_path / "base.png")
    route_image = _touch(tmp_path / "route.png")
    route_target = AutomationRule(rule_id="route_target", action_type="hotkey", description="복구 액션")
    rule = AutomationRule(
        action_type="click",
        target_image=final_image,
        is_monitoring_mode=True,
        monitoring_watches=[
            {
                "image": base_image,
                "monitor_actions": [{"type": "키 입력", "keys": ["enter"]}],
            },
            {
                "image": route_image,
                "goto_index": 0,
            },
        ],
    )
    executor._current_plan = SimpleNamespace(initial_rules=[route_target])
    executor._current_step_num = "7"
    final_checks = 0
    route_runs = []
    monitor_runs = []

    def fake_find(image_path, confidence, search_region=None, verify_color=False, verify_brightness=False):
        nonlocal final_checks
        name = Path(image_path).name
        if name == "final.png":
            final_checks += 1
            return (30, 40, 0.94) if final_checks >= 2 else None
        if name == "route.png":
            return (11, 22, 0.86) if final_checks == 1 else None
        if name == "base.png":
            return (12, 23, 0.9)
        return None

    monkeypatch.setattr(executor, "_find_image_on_screen", fake_find)
    monkeypatch.setattr(executor, "_execute_rule_tree_once", lambda target, step: route_runs.append((target.description, step)) or None)
    monkeypatch.setattr(
        executor,
        "_execute_monitor_action_sequence",
        lambda *args, **kwargs: monitor_runs.append(args) or None,
    )
    monkeypatch.setattr(executor._stop_event, "wait", lambda timeout=None: False)

    result = executor._execute_monitoring_mode(rule, [], 0, step_num="7")

    assert result.success is True
    assert result.monitoring_jump_index == 0
    assert result.monitoring_jump_rule_id == "route_target"
    assert result.message == "모니터링 점프 - 액션 1"
    assert route_runs == []
    assert monitor_runs == []


def test_monitoring_route_runs_watch_actions_before_target_jump(tmp_path, monkeypatch):
    executor = RuleExecutor()
    final_image = _touch(tmp_path / "final.png")
    route_image = _touch(tmp_path / "route.png")
    route_target = AutomationRule(action_type="hotkey", description="점프 액션")
    monitor_action = {"type": "키 입력", "keys": ["esc"]}
    rule = AutomationRule(
        action_type="click",
        target_image=final_image,
        is_monitoring_mode=True,
        monitoring_watches=[
            {
                "image": route_image,
                "goto_index": 0,
                "monitor_actions": [monitor_action],
            }
        ],
    )
    executor._current_plan = SimpleNamespace(initial_rules=[route_target])
    executor._current_step_num = "3"
    final_checks = 0
    calls = []

    def fake_find(image_path, confidence, search_region=None, verify_color=False, verify_brightness=False):
        nonlocal final_checks
        name = Path(image_path).name
        if name == "final.png":
            final_checks += 1
            return (30, 40, 0.94) if final_checks >= 2 else None
        if name == "route.png":
            return (11, 22, 0.86) if final_checks == 1 else None
        return None

    def fake_action_sequence(rule_arg, monitor_actions, confidence, start_time, step_prefix="", **kwargs):
        calls.append(("monitor", list(monitor_actions)))
        return None

    def fake_route(target, step):
        calls.append(("route", target.description, step))
        return None

    monkeypatch.setattr(executor, "_find_image_on_screen", fake_find)
    monkeypatch.setattr(executor, "_execute_monitor_action_sequence", fake_action_sequence)
    monkeypatch.setattr(executor, "_execute_rule_tree_once", fake_route)
    monkeypatch.setattr(executor._stop_event, "wait", lambda timeout=None: False)

    result = executor._execute_monitoring_mode(rule, [], 0, step_num="3")

    assert result.success is True
    assert result.monitoring_jump_index == 0
    assert result.message == "모니터링 점프 - 액션 1"
    assert calls == [
        ("monitor", [monitor_action]),
    ]


def test_monitoring_route_image_click_reuses_detected_location(tmp_path, monkeypatch):
    executor = RuleExecutor()
    final_image = _touch(tmp_path / "final.png")
    route_image = _touch(tmp_path / "route.png")
    route_target = AutomationRule(rule_id="target", action_type="hotkey", description="점프 액션")
    rule = AutomationRule(
        action_type="click",
        target_image=final_image,
        is_monitoring_mode=True,
        monitoring_watches=[
            {
                "image": route_image,
                "goto_index": 0,
                "pre_jump_recheck": False,
                "monitor_actions": [
                    {
                        "type": "이미지 클릭",
                        "image": route_image,
                        "click_type": "double_click",
                        "wait_after": 0,
                    }
                ],
            }
        ],
    )
    executor._current_plan = SimpleNamespace(initial_rules=[route_target])
    route_searches = 0
    clicked = []

    def fake_find(image_path, confidence, search_region=None, verify_color=False, verify_brightness=False):
        nonlocal route_searches
        name = Path(image_path).name
        if name == "final.png":
            return None
        if name == "route.png":
            route_searches += 1
            return (111, 222, 0.99) if route_searches == 1 else None
        return None

    class FakeInputController:
        def move_to(self, x, y, duration=0):
            clicked.append(("move", x, y))
            return True

        def double_click(self):
            clicked.append(("double_click",))
            return True

    monkeypatch.setattr(executor, "_find_image_on_screen", fake_find)
    monkeypatch.setattr("src.player.rule_executor.get_input_controller", lambda: FakeInputController())
    monkeypatch.setattr(executor._stop_event, "wait", lambda timeout=None: False)

    result = executor._execute_monitoring_mode(rule, [], 0, step_num="8")

    assert result.success is True
    assert result.monitoring_jump_index == 0
    assert result.monitoring_jump_rule_id == "target"
    assert route_searches == 1
    assert clicked == [("move", 111, 222), ("double_click",)]


def test_monitor_action_image_click_waits_like_normal_image_action(tmp_path, monkeypatch):
    executor = RuleExecutor()
    image_path = _touch(tmp_path / "target.png")
    searches = 0
    clicked = []

    def fake_find(image_path_arg, confidence, search_region=None, verify_color=False, verify_brightness=False):
        nonlocal searches
        searches += 1
        return (44, 55, 0.91) if searches >= 3 else None

    class FakeInputController:
        def move_to(self, x, y, duration=0):
            clicked.append(("move", x, y))
            return True

        def click(self):
            clicked.append(("click",))
            return True

    monkeypatch.setattr(executor, "_find_image_on_screen", fake_find)
    monkeypatch.setattr("src.player.rule_executor.get_input_controller", lambda: FakeInputController())
    monkeypatch.setattr(executor._stop_event, "wait", lambda timeout=None: False)
    monkeypatch.setattr(executor, "_wait_for_resume", lambda: False)
    monkeypatch.setattr(executor, "_check_user_intervention", lambda: False)

    result = executor._execute_monitor_action(
        {
            "type": "이미지 클릭",
            "image": image_path,
            "click_type": "click",
            "wait_after": 0,
        },
        confidence=0.8,
    )

    assert result == "이미지 클릭: target.png"
    assert searches == 3
    assert clicked == [("move", 44, 55), ("click",)]


def test_monitor_action_image_click_skip_on_not_found_returns_success_message(tmp_path, monkeypatch):
    executor = RuleExecutor()
    image_path = _touch(tmp_path / "missing.png")
    searches = []

    def fake_find(image_path_arg, confidence, search_region=None, verify_color=False, verify_brightness=False):
        searches.append(Path(image_path_arg).name)
        return None

    monkeypatch.setattr(executor, "_find_image_on_screen", fake_find)
    monkeypatch.setattr(executor._stop_event, "wait", lambda timeout=None: False)
    monkeypatch.setattr(executor, "_wait_for_resume", lambda: False)
    monkeypatch.setattr(executor, "_check_user_intervention", lambda: False)

    result = executor._execute_monitor_action(
        {
            "type": "이미지 클릭",
            "image": image_path,
            "click_type": "click",
            "skip_on_not_found": True,
            "wait_after": 0,
        },
        confidence=0.8,
    )

    assert result.startswith("스킵됨")
    assert searches == ["missing.png"]


def test_monitoring_route_jump_disabled_runs_actions_without_target_jump(tmp_path, monkeypatch):
    executor = RuleExecutor()
    final_image = _touch(tmp_path / "final.png")
    route_image = _touch(tmp_path / "route.png")
    route_target = AutomationRule(action_type="hotkey", description="disabled jump target")
    monitor_action = {"type": "키 입력", "keys": ["enter"]}
    rule = AutomationRule(
        action_type="click",
        target_image=final_image,
        is_monitoring_mode=True,
        monitoring_watches=[
            {
                "image": route_image,
                "goto_index": 0,
                "jump_enabled": False,
                "monitor_actions": [monitor_action],
            }
        ],
    )
    executor._current_plan = SimpleNamespace(initial_rules=[route_target])
    final_checks = 0
    monitor_runs = []

    def fake_find(image_path, confidence, search_region=None, verify_color=False, verify_brightness=False):
        nonlocal final_checks
        name = Path(image_path).name
        if name == "final.png":
            final_checks += 1
            return (30, 40, 0.94) if final_checks >= 2 else None
        if name == "route.png":
            return (11, 22, 0.86)
        return None

    monkeypatch.setattr(executor, "_find_image_on_screen", fake_find)
    monkeypatch.setattr(executor, "_execute_monitor_action_sequence", lambda *args, **kwargs: monitor_runs.append(args) or None)
    monkeypatch.setattr(executor._stop_event, "wait", lambda timeout=None: False)

    result = executor._execute_monitoring_mode(rule, [], 0, step_num="6")

    assert result.success is True
    assert result.monitoring_jump_index == -1
    assert result.message == "모니터링 완료 - 최종이미지 발견"
    assert len(monitor_runs) == 1
    assert monitor_runs[0][1] == [monitor_action]


def test_monitoring_route_condition_blocks_target_jump_until_condition_disappears(tmp_path, monkeypatch, caplog):
    executor = RuleExecutor()
    caplog.set_level(logging.INFO)
    final_image = _touch(tmp_path / "final.png")
    route_image = _touch(tmp_path / "route.png")
    condition_image = _touch(tmp_path / "condition.png")
    route_target = AutomationRule(action_type="hotkey", description="점프 액션")
    rule = AutomationRule(
        action_type="click",
        target_image=final_image,
        is_monitoring_mode=True,
        monitoring_watches=[
            {
                "image": route_image,
                "goto_index": 0,
                "monitor_actions": [{"type": "키 입력", "keys": ["esc"]}],
                "condition_image": condition_image,
                "condition_confidence": 0.81,
            }
        ],
    )
    executor._current_plan = SimpleNamespace(initial_rules=[route_target])
    executor._current_step_num = "4"
    final_checks = 0
    route_runs = []
    monitor_runs = []

    def fake_find(image_path, confidence, search_region=None, verify_color=False, verify_brightness=False):
        nonlocal final_checks
        name = Path(image_path).name
        if name == "final.png":
            final_checks += 1
            return (30, 40, 0.94) if final_checks >= 2 else None
        if name == "route.png":
            return (11, 22, 0.86) if final_checks == 1 else None
        if name == "condition.png":
            return (20, 20, 0.9)
        return None

    monkeypatch.setattr(executor, "_find_image_on_screen", fake_find)
    monkeypatch.setattr(executor, "_execute_monitor_action_sequence", lambda *args, **kwargs: monitor_runs.append(args) or None)
    monkeypatch.setattr(executor, "_execute_rule_tree_once", lambda target, step: route_runs.append((target.description, step)) or None)
    monkeypatch.setattr(executor._stop_event, "wait", lambda timeout=None: False)

    result = executor._execute_monitoring_mode(rule, [], 0, step_num="4")

    assert result.success is True
    assert result.message == "모니터링 완료 - 최종이미지 발견"
    assert len(monitor_runs) == 1
    assert route_runs == []
    assert "condition_image=condition.png" in caplog.text
    assert "condition_result=visible" in caplog.text
    assert "condition_matched=90%" in caplog.text
    assert "condition_threshold=81%" in caplog.text
    assert "condition_decision=wait" in caplog.text


def test_monitoring_route_can_jump_when_condition_image_is_visible(tmp_path, monkeypatch, caplog):
    executor = RuleExecutor()
    caplog.set_level(logging.INFO)
    final_image = _touch(tmp_path / "final.png")
    route_image = _touch(tmp_path / "route.png")
    condition_image = _touch(tmp_path / "condition.png")
    route_target = AutomationRule(action_type="hotkey", description="조건 점프 액션")
    monitor_action = {"type": "키 입력", "keys": ["esc"]}
    rule = AutomationRule(
        action_type="click",
        target_image=final_image,
        is_monitoring_mode=True,
        monitoring_watches=[
            {
                "image": route_image,
                "goto_index": 0,
                "pre_jump_recheck": False,
                "monitor_actions": [monitor_action],
                "condition_image": condition_image,
                "condition_confidence": 0.81,
                "condition_jump_when_visible": True,
                "condition_verify_image_color": True,
                "condition_verify_image_brightness": True,
            }
        ],
    )
    executor._current_plan = SimpleNamespace(initial_rules=[route_target])
    checks = []
    monitor_runs = []

    def fake_find(image_path, confidence, search_region=None, verify_color=False, verify_brightness=False):
        name = Path(image_path).name
        checks.append((name, verify_color, verify_brightness))
        if name == "final.png":
            return None
        if name == "route.png":
            return (11, 22, 0.86)
        if name == "condition.png":
            return (20, 20, 0.9)
        return None

    monkeypatch.setattr(executor, "_find_image_on_screen", fake_find)
    monkeypatch.setattr(executor, "_execute_monitor_action_sequence", lambda *args, **kwargs: monitor_runs.append(args) or None)

    result = executor._execute_monitoring_mode(rule, [], 0, step_num="5")

    assert result.success is True
    assert result.monitoring_jump_index == 0
    assert result.message == "모니터링 점프 - 액션 1"
    assert checks == [
        ("final.png", False, False),
        ("route.png", False, False),
        ("condition.png", True, True),
    ]
    assert len(monitor_runs) == 1
    assert monitor_runs[0][1] == [monitor_action]
    assert "condition_image=condition.png" in caplog.text
    assert "condition_result=visible" in caplog.text
    assert "condition_matched=90%" in caplog.text
    assert "condition_threshold=81%" in caplog.text
    assert "condition_decision=jump" in caplog.text


def test_monitoring_pre_jump_recheck_allows_jump_when_route_image_disappears(tmp_path, monkeypatch, caplog):
    executor = RuleExecutor()
    caplog.set_level(logging.INFO)
    final_image = _touch(tmp_path / "final.png")
    route_image = _touch(tmp_path / "route.png")
    route_target = AutomationRule(action_type="hotkey", description="target")
    rule = AutomationRule(
        action_type="click",
        target_image=final_image,
        is_monitoring_mode=True,
        monitoring_watches=[
            {
                "image": route_image,
                "goto_index": 0,
                "pre_jump_recheck": True,
            }
        ],
    )
    executor._current_plan = SimpleNamespace(initial_rules=[route_target])
    route_checks = 0

    def fake_find(image_path, confidence, search_region=None, verify_color=False, verify_brightness=False):
        nonlocal route_checks
        name = Path(image_path).name
        if name == "final.png":
            return None
        if name == "route.png":
            route_checks += 1
            return (11, 22, 0.91) if route_checks == 1 else None
        return None

    monkeypatch.setattr(executor, "_find_image_on_screen", fake_find)

    result = executor._execute_monitoring_mode(rule, [], 0, step_num="6")

    assert result.success is True
    assert result.monitoring_jump_index == 0
    assert route_checks == 2
    assert "점프전 재확인 통과" in caplog.text
    assert "pre_jump_recheck_result=not_found" in caplog.text
    assert "pre_jump_recheck_decision=jump" in caplog.text


def test_monitoring_pre_jump_recheck_repeats_actions_when_route_image_still_visible(tmp_path, monkeypatch, caplog):
    executor = RuleExecutor()
    caplog.set_level(logging.INFO)
    final_image = _touch(tmp_path / "final.png")
    route_image = _touch(tmp_path / "route.png")
    route_target = AutomationRule(action_type="hotkey", description="target")
    rule = AutomationRule(
        action_type="click",
        target_image=final_image,
        is_monitoring_mode=True,
        monitoring_watches=[
            {
                "image": route_image,
                "goto_index": 0,
                "pre_jump_recheck": True,
            }
        ],
    )
    executor._current_plan = SimpleNamespace(initial_rules=[route_target])
    checks = []

    def fake_find(image_path, confidence, search_region=None, verify_color=False, verify_brightness=False):
        name = Path(image_path).name
        checks.append(name)
        if name == "final.png":
            return None
        if name == "route.png":
            return (11, 22, 0.91)
        return None

    monkeypatch.setattr(executor, "_find_image_on_screen", fake_find)
    monkeypatch.setattr(executor._stop_event, "wait", lambda timeout=None: True)

    result = executor._execute_monitoring_mode(rule, [], 0, step_num="6")

    assert result.success is False
    assert checks == ["final.png", "route.png", "route.png"]
    assert "점프전 재확인 감지" in caplog.text
    assert "pre_jump_recheck_result=visible" in caplog.text
    assert "pre_jump_recheck_decision=repeat_actions" in caplog.text


def test_monitoring_pre_jump_recheck_runs_actions_again_then_jumps_when_image_disappears(tmp_path, monkeypatch, caplog):
    executor = RuleExecutor()
    caplog.set_level(logging.INFO)
    final_image = _touch(tmp_path / "final.png")
    route_image = _touch(tmp_path / "route.png")
    route_target = AutomationRule(action_type="hotkey", description="target")
    monitor_action = {"type": "hotkey", "key": "esc"}
    rule = AutomationRule(
        action_type="click",
        target_image=final_image,
        is_monitoring_mode=True,
        monitoring_watches=[
            {
                "image": route_image,
                "goto_index": 0,
                "pre_jump_recheck": True,
                "monitor_actions": [monitor_action],
            }
        ],
    )
    executor._current_plan = SimpleNamespace(initial_rules=[route_target])
    route_checks = 0
    monitor_runs = []

    def fake_find(image_path, confidence, search_region=None, verify_color=False, verify_brightness=False):
        nonlocal route_checks
        name = Path(image_path).name
        if name == "final.png":
            return None
        if name == "route.png":
            route_checks += 1
            if route_checks in {1, 2, 3}:
                return (11, 22, 0.91)
            return None
        return None

    def fake_monitor_actions(rule_arg, actions, confidence, start_time, step_prefix="", **kwargs):
        monitor_runs.append(list(actions))
        return None

    monkeypatch.setattr(executor, "_find_image_on_screen", fake_find)
    monkeypatch.setattr(executor, "_execute_monitor_action_sequence", fake_monitor_actions)
    monkeypatch.setattr(executor._stop_event, "wait", lambda timeout=None: False)

    result = executor._execute_monitoring_mode(rule, [], 0, step_num="6")

    assert result.success is True
    assert result.monitoring_jump_index == 0
    assert route_checks == 4
    assert monitor_runs == [[monitor_action], [monitor_action]]
    assert "점프전 재확인 감지" in caplog.text
    assert "점프전 재확인 통과" in caplog.text
    assert "pre_jump_recheck_decision=jump" in caplog.text


def test_monitoring_route_priority_follows_user_order_not_target_index(tmp_path, monkeypatch):
    executor = RuleExecutor()
    final_image = _touch(tmp_path / "final.png")
    first_route_image = _touch(tmp_path / "first_route.png")
    second_route_image = _touch(tmp_path / "second_route.png")
    route_targets = [
        AutomationRule(action_type="hotkey", description="낮은 번호 액션"),
        AutomationRule(action_type="hotkey", description="높은 번호 액션"),
    ]
    rule = AutomationRule(
        action_type="click",
        target_image=final_image,
        is_monitoring_mode=True,
        monitoring_watches=[
            {
                "image": first_route_image,
                "goto_index": 1,
                "pre_jump_recheck": False,
            },
            {
                "image": second_route_image,
                "goto_index": 0,
                "pre_jump_recheck": False,
            },
        ],
    )
    executor._current_plan = SimpleNamespace(initial_rules=route_targets)
    executor._current_step_num = "9"
    route_runs = []
    checks = []

    def fake_find(image_path, confidence, search_region=None, verify_color=False, verify_brightness=False):
        name = Path(image_path).name
        checks.append(name)
        if name == "final.png":
            return None
        return (10, 10, 0.9)

    monkeypatch.setattr(executor, "_find_image_on_screen", fake_find)
    monkeypatch.setattr(
        executor,
        "_execute_rule_tree_once",
        lambda target, step: route_runs.append((target.description, step)) or executor._stop_event.set() or None,
    )
    monkeypatch.setattr(executor._stop_event, "wait", lambda timeout=None: executor._stop_event.is_set())

    result = executor._execute_monitoring_mode(rule, [], 0, step_num="9")

    assert result.success is True
    assert result.monitoring_jump_index == 1
    assert result.message == "모니터링 점프 - 액션 2"
    assert route_runs == []
    assert checks[:2] == ["final.png", "first_route.png"]


def test_monitoring_route_supports_multiple_images_with_priority(tmp_path, monkeypatch):
    executor = RuleExecutor()
    final_image = _touch(tmp_path / "final.png")
    slow_image = _touch(tmp_path / "slow.png")
    fast_image = _touch(tmp_path / "fast.png")
    legacy_image = _touch(tmp_path / "legacy.png")
    route_target = AutomationRule(action_type="hotkey", description="multi target")
    monitor_action = {"type": "키 입력", "keys": ["esc"]}
    rule = AutomationRule(
        action_type="click",
        target_image=final_image,
        is_monitoring_mode=True,
        monitoring_watches=[
            {
                "image": legacy_image,
                "images": [
                    {"image": slow_image, "priority": 5},
                    {"image": fast_image, "priority": 1},
                ],
                "goto_index": 0,
                "pre_jump_recheck": False,
                "monitor_actions": [monitor_action],
            }
        ],
    )
    executor._current_plan = SimpleNamespace(initial_rules=[route_target])
    checks = []
    monitor_runs = []

    def fake_find(image_path, confidence, search_region=None, verify_color=False, verify_brightness=False):
        name = Path(image_path).name
        checks.append(name)
        if name == "final.png":
            return None
        if name == "fast.png":
            return (11, 22, 0.91)
        return None

    monkeypatch.setattr(executor, "_find_image_on_screen", fake_find)
    monkeypatch.setattr(executor, "_execute_monitor_action_sequence", lambda *args, **kwargs: monitor_runs.append(args) or None)

    result = executor._execute_monitoring_mode(rule, [], 0, step_num="10")

    assert result.success is True
    assert result.monitoring_jump_index == 0
    assert checks[:2] == ["final.png", "fast.png"]
    assert "slow.png" not in checks
    assert "legacy.png" not in checks
    assert len(monitor_runs) == 1
    assert monitor_runs[0][1] == [monitor_action]


def test_monitoring_watch_order_has_priority_over_inner_image_priority(tmp_path, monkeypatch):
    executor = RuleExecutor()
    final_image = _touch(tmp_path / "final.png")
    first_watch_image = _touch(tmp_path / "first_watch.png")
    second_watch_image = _touch(tmp_path / "second_watch.png")
    targets = [
        AutomationRule(action_type="hotkey", description="first target"),
        AutomationRule(action_type="hotkey", description="second target"),
    ]
    rule = AutomationRule(
        action_type="click",
        target_image=final_image,
        is_monitoring_mode=True,
        monitoring_watches=[
            {
                "images": [{"image": first_watch_image, "priority": 9}],
                "goto_index": 0,
                "pre_jump_recheck": False,
            },
            {
                "images": [{"image": second_watch_image, "priority": 1}],
                "goto_index": 1,
                "pre_jump_recheck": False,
            },
        ],
    )
    executor._current_plan = SimpleNamespace(initial_rules=targets)
    checks = []

    def fake_find(image_path, confidence, search_region=None, verify_color=False, verify_brightness=False):
        name = Path(image_path).name
        checks.append(name)
        if name == "final.png":
            return None
        return (11, 22, 0.91)

    monkeypatch.setattr(executor, "_find_image_on_screen", fake_find)

    result = executor._execute_monitoring_mode(rule, [], 0, step_num="10")

    assert result.success is True
    assert result.monitoring_jump_index == 0
    assert checks[:2] == ["final.png", "first_watch.png"]
    assert "second_watch.png" not in checks


def test_monitoring_multi_image_uses_image_specific_options(tmp_path, monkeypatch):
    executor = RuleExecutor()
    final_image = _touch(tmp_path / "final.png")
    route_image = _touch(tmp_path / "route.png")
    route_target = AutomationRule(action_type="hotkey", description="option target")
    rule = AutomationRule(
        action_type="click",
        target_image=final_image,
        confidence=0.65,
        is_monitoring_mode=True,
        monitoring_watches=[
            {
                "images": [
                    {
                        "image": route_image,
                        "priority": 1,
                        "confidence": 0.97,
                        "search_region": [1, 2, 30, 40],
                        "verify_image_color": True,
                        "verify_image_brightness": True,
                    }
                ],
                "confidence": 0.72,
                "search_region": [9, 9, 99, 99],
                "goto_index": 0,
                "pre_jump_recheck": False,
            }
        ],
    )
    executor._current_plan = SimpleNamespace(initial_rules=[route_target])
    calls = []

    def fake_find(image_path, confidence, search_region=None, verify_color=False, verify_brightness=False):
        name = Path(image_path).name
        calls.append((name, confidence, search_region, verify_color, verify_brightness))
        if name == "route.png":
            return (11, 22, 0.99)
        return None

    monkeypatch.setattr(executor, "_find_image_on_screen", fake_find)

    result = executor._execute_monitoring_mode(rule, [], 0, step_num="11")

    assert result.success is True
    assert result.monitoring_jump_index == 0
    assert calls == [
        ("final.png", 0.65, None, False, False),
        ("route.png", 0.97, [1, 2, 30, 40], True, True),
    ]


def test_monitoring_route_can_jump_to_child_action_by_rule_id(tmp_path, monkeypatch):
    executor = RuleExecutor()
    final_image = _touch(tmp_path / "final.png")
    route_image = _touch(tmp_path / "route.png")
    child_target = AutomationRule(rule_id="child_target", action_type="hotkey", description="child target")
    parent_target = AutomationRule(rule_id="parent_target", action_type="click", description="parent", children=[child_target])
    rule = AutomationRule(
        action_type="click",
        target_image=final_image,
        is_monitoring_mode=True,
        monitoring_watches=[
            {
                "image": route_image,
                "goto_index": 1,
                "goto_rule_id": "child_target",
                "pre_jump_recheck": False,
            }
        ],
    )
    executor._current_plan = SimpleNamespace(initial_rules=[parent_target])

    def fake_find(image_path, confidence, search_region=None, verify_color=False, verify_brightness=False):
        if Path(image_path).name == "route.png":
            return (11, 22, 0.9)
        return None

    monkeypatch.setattr(executor, "_find_image_on_screen", fake_find)

    result = executor._execute_monitoring_mode(rule, [], 0, step_num="12")

    assert result.success is True
    assert result.monitoring_jump_index == 1
    assert result.monitoring_jump_rule_id == "child_target"


def test_monitoring_route_limits_multi_images_to_ten(tmp_path):
    executor = RuleExecutor()
    image_paths = [_touch(tmp_path / f"candidate_{idx}.png") for idx in range(12)]
    watch = {
        "images": [
            {"image": image_path, "priority": idx + 1}
            for idx, image_path in enumerate(image_paths)
        ]
    }

    normalized = executor._normalise_monitoring_watch_images(watch)

    assert len(normalized) == 10
    assert [Path(item["image"]).name for item in normalized] == [f"candidate_{idx}.png" for idx in range(10)]


def test_monitoring_multi_images_are_saved_and_restored_relative(tmp_path):
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    first = _touch(templates_dir / "first.png")
    second = _touch(templates_dir / "second.png")
    rule = AutomationRule(
        action_type="click",
        monitoring_watches=[
            {
                "image": first,
                "images": [
                    {
                        "image": first,
                        "priority": 1,
                        "confidence": 0.91,
                        "search_region": [1, 2, 3, 4],
                        "verify_image_color": True,
                    },
                    {"image": second, "priority": 2, "verify_image_brightness": True},
                ],
                "goto_index": 0,
                "goto_rule_id": "target_rule",
            }
        ],
    )

    saved = rule.to_dict()
    restored = AutomationRule.from_dict(saved, templates_dir=templates_dir)

    assert saved["monitoring_watches"][0]["image"] == "first.png"
    assert saved["monitoring_watches"][0]["images"] == [
        {
            "image": "first.png",
            "priority": 1,
            "confidence": 0.91,
            "search_region": [1, 2, 3, 4],
            "verify_image_color": True,
        },
        {"image": "second.png", "priority": 2, "verify_image_brightness": True},
    ]
    assert saved["monitoring_watches"][0]["goto_rule_id"] == "target_rule"
    restored_images = restored.monitoring_watches[0]["images"]
    assert restored_images[0]["image"] == str(templates_dir / "first.png")
    assert restored_images[1]["image"] == str(templates_dir / "second.png")
    assert restored_images[0]["search_region"] == [1, 2, 3, 4]
    assert restored_images[0]["verify_image_color"] is True
    assert restored_images[1]["verify_image_brightness"] is True
