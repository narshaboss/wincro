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
    route_target = AutomationRule(action_type="hotkey", description="복구 액션")
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

    def fake_action_sequence(rule_arg, monitor_actions, confidence, start_time, step_prefix=""):
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


def test_monitoring_route_condition_blocks_target_jump_until_condition_disappears(tmp_path, monkeypatch):
    executor = RuleExecutor()
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


def test_monitoring_route_can_jump_when_condition_image_is_visible(tmp_path, monkeypatch):
    executor = RuleExecutor()
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
            },
            {
                "image": second_route_image,
                "goto_index": 0,
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
