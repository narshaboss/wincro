from datetime import datetime
import json
from pathlib import Path
from types import SimpleNamespace

from src.analyzer.automation_models import AutomationPlan, AutomationRule
from src.database.db_manager import DatabaseManager
from src.database.models import Action, Sequence
from src.player.rule_executor import RuleExecutor
from src.ui.player_view import PlayerView, _transition_next_confirmation_item
from src.utils.transition_recovery_policy import (
    TRANSITION_POLICY_AUTO,
    TRANSITION_POLICY_FORCE_OFF,
    TRANSITION_POLICY_FORCE_ON,
    build_transition_recovery_context_exclusions,
    evaluate_auto_transition_recovery,
    flatten_enabled_transition_items,
    transition_recovery_policy_for,
    transition_target_images,
)


ROOT = Path(__file__).resolve().parents[1]
PLAYER_VIEW_SOURCE = ROOT / "src" / "ui" / "player_view.py"
MAIN_WINDOW_SOURCE = ROOT / "src" / "ui" / "main_window.py"
AUTO_HUNT_PLAN = ROOT / "data" / "plans" / "plan_20260118_174859.json"


def _rule(**overrides):
    values = {
        "rule_id": "source",
        "action_type": "double_click",
        "description": "source action",
        "target_image": "source.png",
        "repeat_count": 1,
        "repeat_delay": 0.0,
        "wait_after": 0.0,
        "transition_recovery_enabled": True,
        "transition_verify_mode": "next_action",
        "transition_verify_timeout": 0.5,
        "transition_recovery_mode": "refocus_retry",
        "transition_recovery_count": 3,
        "transition_recovery_delay": 0.0,
        "transition_failure_mode": "alert_wait",
    }
    values.update(overrides)
    return AutomationRule(**values)


def _next_rule(**overrides):
    values = {
        "rule_id": "expected",
        "action_type": "click",
        "description": "expected screen",
        "target_image": "expected.png",
        "confidence": 0.91,
        "search_region": [10, 20, 210, 220],
        "verify_image_color": True,
        "verify_image_brightness": True,
    }
    values.update(overrides)
    return AutomationRule(**values)


def _success(executor, rule, message="input sent"):
    return executor._make_result(rule, True, message, datetime.now())


def _prepare_executor(monkeypatch):
    executor = RuleExecutor()
    monkeypatch.setattr("src.player.rule_executor.time.sleep", lambda _seconds: None)
    monkeypatch.setattr(executor, "_capture_trigger_input_window", lambda: 4242)
    monkeypatch.setattr(executor, "_restore_trigger_input_window", lambda _hwnd: True)
    return executor


def test_transition_settings_survive_plan_json_and_sequence_db_reload(tmp_path):
    verify_image = tmp_path / "verify.png"
    verify_image.write_bytes(b"image")
    rule = _rule(
        transition_verify_mode="custom_image",
        transition_verify_image=str(verify_image),
        transition_verify_region=[1, 2, 101, 102],
        transition_verify_confidence=0.93,
        transition_verify_color=True,
        transition_verify_brightness=True,
        transition_recovery_mode="actions_retry",
        transition_recovery_count=3,
        transition_recovery_delay=1.25,
        transition_recovery_delay_random=True,
        transition_recovery_delay_random_range=0.4,
        transition_recovery_rule_ids=["recover-key", "recover-click"],
        transition_failure_mode="goto_rule",
        transition_failure_rule_id="previous",
    )
    plan = AutomationPlan(
        name="transition",
        initial_rules=[rule],
        transition_recovery_auto_enabled=True,
    )
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan.to_dict(), ensure_ascii=False), encoding="utf-8")
    loaded_plan = AutomationPlan.from_dict(
        json.loads(plan_path.read_text(encoding="utf-8")),
        templates_dir=tmp_path,
    )
    restored_rule = loaded_plan.initial_rules[0]
    assert restored_rule.transition_recovery_enabled is True
    assert restored_rule.transition_recovery_policy == TRANSITION_POLICY_FORCE_ON
    assert loaded_plan.transition_recovery_auto_enabled is True
    assert restored_rule.transition_verify_image == str(verify_image.resolve())
    assert restored_rule.transition_verify_region == [1, 2, 101, 102]
    assert restored_rule.transition_verify_confidence == 0.93
    assert restored_rule.transition_verify_color is True
    assert restored_rule.transition_verify_brightness is True
    assert restored_rule.transition_recovery_rule_ids == ["recover-key", "recover-click"]
    assert restored_rule.transition_failure_rule_id == "previous"

    action = Action.from_dict(rule.to_dict())
    action.action_id = "source-action"
    sequence = Sequence(
        name="transition",
        actions=[action],
        transition_recovery_auto_enabled=True,
    )
    manager = DatabaseManager()
    original_path = manager._db_path
    original_wal = manager._wal_initialized
    try:
        manager._db_path = tmp_path / "transition.db"
        manager._wal_initialized = False
        manager._ensure_database()
        sequence.id = manager.create_sequence(sequence)
        action.transition_recovery_count = 5
        assert manager.update_sequence(sequence) is True
        loaded_sequence = manager.get_sequence(sequence.id)
    finally:
        manager._db_path = original_path
        manager._wal_initialized = original_wal

    assert loaded_sequence is not None
    restored_action = loaded_sequence.actions[0]
    assert restored_action.transition_recovery_enabled is True
    assert restored_action.transition_recovery_policy == TRANSITION_POLICY_FORCE_ON
    assert restored_action.transition_recovery_count == 5
    assert restored_action.transition_recovery_rule_ids == ["recover-key", "recover-click"]
    assert loaded_sequence.transition_recovery_auto_enabled is True


def test_legacy_transition_flag_migrates_to_explicit_policy():
    legacy_on = AutomationRule.from_dict(
        {
            "rule_id": "legacy-on",
            "action_type": "click",
            "transition_recovery_enabled": True,
        }
    )
    legacy_off = AutomationRule.from_dict(
        {
            "rule_id": "legacy-off",
            "action_type": "click",
            "transition_recovery_enabled": False,
        }
    )
    explicit_off = AutomationRule(
        rule_id="explicit-off",
        action_type="click",
        transition_recovery_policy=TRANSITION_POLICY_FORCE_OFF,
        transition_recovery_enabled=True,
    )

    assert legacy_on.transition_recovery_policy == TRANSITION_POLICY_FORCE_ON
    assert legacy_on.transition_recovery_enabled is True
    assert legacy_off.transition_recovery_policy == TRANSITION_POLICY_AUTO
    assert legacy_off.transition_recovery_enabled is False
    assert explicit_off.transition_recovery_policy == TRANSITION_POLICY_FORCE_OFF
    assert explicit_off.transition_recovery_enabled is False


def test_plan_auto_policy_only_enables_safe_compatible_actions():
    source = AutomationRule(
        rule_id="auto-source",
        action_type="hotkey",
        action_keys=["enter"],
        transition_recovery_policy=TRANSITION_POLICY_AUTO,
    )
    expected = _next_rule()
    executor = RuleExecutor()

    executor._current_plan = SimpleNamespace(transition_recovery_auto_enabled=False)
    assert executor._transition_confirmation_spec(
        source,
        expected,
        ["expected.png"],
    ) is None

    executor._current_plan = SimpleNamespace(transition_recovery_auto_enabled=True)
    spec = executor._transition_confirmation_spec(
        source,
        expected,
        ["expected.png"],
    )
    assert spec is not None
    assert spec.images == ("expected.png",)


def test_force_policy_overrides_plan_auto_switch():
    executor = RuleExecutor()
    expected = _next_rule()
    executor._current_plan = SimpleNamespace(transition_recovery_auto_enabled=True)
    force_off = _rule(
        transition_recovery_policy=TRANSITION_POLICY_FORCE_OFF,
        transition_recovery_enabled=False,
    )
    assert executor._transition_confirmation_spec(
        force_off,
        expected,
        ["expected.png"],
    ) is None

    executor._current_plan = SimpleNamespace(transition_recovery_auto_enabled=False)
    force_on = _rule(
        transition_recovery_policy=TRANSITION_POLICY_FORCE_ON,
        transition_recovery_enabled=True,
    )
    assert executor._transition_confirmation_spec(
        force_on,
        expected,
        ["expected.png"],
    ) is not None

    unsafe_next = _next_rule(click_until_image_disappears=True)
    assert executor._transition_confirmation_spec(
        force_on,
        unsafe_next,
        ["expected.png"],
    ) is None


def test_auto_policy_excludes_ambiguous_or_unbounded_flows():
    expected = _next_rule()
    cases = [
        (
            AutomationRule(action_type="game_mode"),
            expected,
            "source_type",
        ),
        (
            AutomationRule(action_type="click", is_monitoring_mode=True),
            expected,
            "source_monitoring",
        ),
        (
            AutomationRule(action_type="click", click_until_image_disappears=True),
            expected,
            "source_until_disappears",
        ),
        (
            AutomationRule(action_type="click", repeat_from_auto_list_quantity=True),
            expected,
            "source_auto_list_repeat",
        ),
        (
            AutomationRule(action_type="click", skip_on_not_found=True),
            expected,
            "source_optional",
        ),
        (
            AutomationRule(action_type="click"),
            _next_rule(skip_on_not_found=True),
            "next_optional",
        ),
        (
            AutomationRule(action_type="click"),
            _next_rule(target_image=None),
            "next_image_missing",
        ),
        (
            AutomationRule(action_type="click"),
            _next_rule(click_until_image_disappears=True),
            "next_absence_success",
        ),
    ]
    for source, next_item, reason_code in cases:
        result = evaluate_auto_transition_recovery(
            source,
            next_item,
            transition_target_images(next_item),
        )
        assert result.eligible is False
        assert result.reason_code == reason_code


def test_structural_parent_context_blocks_auto_and_force_on_recovery():
    child = AutomationRule(
        rule_id="monitor-child",
        action_type="click",
        target_image="source.png",
        transition_recovery_policy=TRANSITION_POLICY_FORCE_ON,
    )
    expected = _next_rule(rule_id="monitor-next")
    parent = AutomationRule(
        rule_id="monitor-parent",
        action_type="click",
        is_monitoring_mode=True,
        children=[child, expected],
    )
    context_exclusions = build_transition_recovery_context_exclusions([parent])
    context = context_exclusions[id(child)]

    eligibility = evaluate_auto_transition_recovery(
        child,
        expected,
        transition_target_images(expected),
        context,
    )
    assert eligibility.eligible is False
    assert eligibility.reason_code == "ancestor_monitoring"

    executor = RuleExecutor()
    executor._current_plan = SimpleNamespace(transition_recovery_auto_enabled=True)
    executor._transition_recovery_context_exclusions = context_exclusions
    assert executor._transition_confirmation_spec(
        child,
        expected,
        transition_target_images(expected),
    ) is None


def test_next_action_confirmation_uses_actual_image_settings():
    executor = RuleExecutor()
    next_rule = _next_rule(target_images=["alternate.png"])
    spec = executor._transition_confirmation_spec(
        _rule(),
        next_rule,
        ["expected.png", "alternate.png"],
    )

    assert spec is not None
    assert spec.images == ("expected.png", "alternate.png")
    assert spec.confidence == 0.91
    assert spec.search_region == [10, 20, 210, 220]
    assert spec.verify_color is True
    assert spec.verify_brightness is True


def test_next_confirmation_ui_helper_matches_enabled_runtime_tree():
    hidden = _next_rule(rule_id="hidden", enabled=False)
    child = _next_rule(rule_id="child")
    source = _rule(children=[hidden, child])

    assert _transition_next_confirmation_item(
        [source],
        source.rule_id,
        "rule_id",
    ) is child


def test_unbounded_click_modes_cannot_own_transition_recovery():
    assert RuleExecutor._transition_recovery_supported(
        _rule(click_until_image_disappears=True)
    ) is False
    assert RuleExecutor._transition_recovery_supported(
        _rule(repeat_from_auto_list_quantity=True)
    ) is False


def test_transition_success_stops_remaining_repeats(monkeypatch):
    executor = _prepare_executor(monkeypatch)
    rule = _rule(repeat_count=4)
    calls = []
    monkeypatch.setattr(
        executor,
        "_execute_rule",
        lambda active_rule, **_kwargs: calls.append(active_rule.rule_id) or _success(executor, active_rule),
    )
    monkeypatch.setattr(
        executor,
        "_wait_for_transition_confirmation",
        lambda *_args, **_kwargs: {
            "status": "found",
            "image": "expected.png",
            "score": 0.99,
            "waited": 0.1,
        },
    )

    result = executor._execute_rule_with_retry(
        rule,
        ["expected.png"],
        next_rule=_next_rule(),
        step_num="6-2",
    )

    assert result.success is True
    assert calls == ["source"]


def test_transition_recovers_after_one_refocused_retry(monkeypatch):
    executor = _prepare_executor(monkeypatch)
    calls = []
    focus_calls = []
    outcomes = iter(
        [
            {"status": "timeout", "waited": 0.5},
            {"status": "found", "image": "expected.png", "score": 0.97, "waited": 0.1},
        ]
    )
    monkeypatch.setattr(
        executor,
        "_execute_rule",
        lambda active_rule, **_kwargs: calls.append(active_rule.rule_id) or _success(executor, active_rule),
    )
    monkeypatch.setattr(executor, "_scan_transition_confirmation", lambda _spec: None)
    monkeypatch.setattr(
        executor,
        "_restore_trigger_input_window",
        lambda hwnd: focus_calls.append(hwnd) or True,
    )
    monkeypatch.setattr(
        executor,
        "_wait_for_transition_confirmation",
        lambda *_args, **_kwargs: next(outcomes),
    )

    result = executor._execute_rule_with_retry(
        _rule(),
        ["expected.png"],
        next_rule=_next_rule(),
        step_num="6-2",
    )

    assert result.success is True
    assert calls == ["source", "source"]
    assert focus_calls == [4242]


def test_three_failed_recoveries_alert_once_then_resume_when_screen_appears(monkeypatch):
    executor = _prepare_executor(monkeypatch)
    calls = []
    alerts = []
    outcomes = iter(
        [
            {"status": "timeout", "waited": 0.5},
            {"status": "timeout", "waited": 0.5},
            {"status": "timeout", "waited": 0.5},
            {"status": "timeout", "waited": 0.5},
            {"status": "found", "image": "expected.png", "score": 0.96, "waited": 1.0},
        ]
    )
    executor.set_callbacks(on_transition_recovery_alert=lambda details: alerts.append(details))
    monkeypatch.setattr(
        executor,
        "_execute_rule",
        lambda active_rule, **_kwargs: calls.append(active_rule.rule_id) or _success(executor, active_rule),
    )
    monkeypatch.setattr(executor, "_scan_transition_confirmation", lambda _spec: None)
    monkeypatch.setattr(
        executor,
        "_wait_for_transition_confirmation",
        lambda *_args, **_kwargs: next(outcomes),
    )

    result = executor._execute_rule_with_retry(
        _rule(),
        ["expected.png"],
        next_rule=_next_rule(),
        step_num="6-2",
    )

    assert result.success is True
    assert calls == ["source", "source", "source", "source"]
    assert len(alerts) == 1
    assert alerts[0]["step_num"] == "6-2"
    assert alerts[0]["attempts"] == 3
    assert alerts[0]["expected_images"] == ["expected.png"]


def test_transition_alert_wait_stops_promptly_on_stop_event(monkeypatch):
    executor = _prepare_executor(monkeypatch)
    alerts = []
    wait_calls = {"count": 0}
    executor.set_callbacks(on_transition_recovery_alert=lambda details: alerts.append(details))
    monkeypatch.setattr(executor, "_execute_rule", lambda active_rule, **_kwargs: _success(executor, active_rule))
    monkeypatch.setattr(executor, "_scan_transition_confirmation", lambda _spec: None)

    def wait(*_args, **_kwargs):
        wait_calls["count"] += 1
        if wait_calls["count"] >= 3:
            executor._stop_event.set()
            return {"status": "stopped", "waited": 0.0}
        return {"status": "timeout", "waited": 0.5}

    monkeypatch.setattr(executor, "_wait_for_transition_confirmation", wait)
    result = executor._execute_rule_with_retry(
        _rule(transition_recovery_count=1),
        ["expected.png"],
        next_rule=_next_rule(),
        step_num="6-2",
    )

    assert result.success is False
    assert result.message == "실행 중지됨"
    assert len(alerts) == 1


def test_key_action_uses_same_transition_recovery(monkeypatch):
    executor = _prepare_executor(monkeypatch)
    calls = []
    outcomes = iter(
        [
            {"status": "timeout", "waited": 0.5},
            {"status": "found", "image": "expected.png", "score": 0.95, "waited": 0.1},
        ]
    )
    monkeypatch.setattr(
        executor,
        "_execute_rule",
        lambda active_rule, **_kwargs: calls.append(active_rule.action_type) or _success(executor, active_rule),
    )
    monkeypatch.setattr(executor, "_scan_transition_confirmation", lambda _spec: None)
    monkeypatch.setattr(
        executor,
        "_wait_for_transition_confirmation",
        lambda *_args, **_kwargs: next(outcomes),
    )

    result = executor._execute_rule_with_retry(
        _rule(action_type="hotkey", target_image=None, action_keys=["enter"]),
        ["expected.png"],
        next_rule=_next_rule(),
        step_num="2",
    )

    assert result.success is True
    assert calls == ["hotkey", "hotkey"]


def test_recovery_action_bundle_executes_in_saved_order(monkeypatch):
    executor = RuleExecutor()
    key = AutomationRule(rule_id="recover-key", action_type="hotkey", repeat_count=2)
    click = AutomationRule(rule_id="recover-click", action_type="click", target_image="recover.png")
    owner = _rule(
        transition_recovery_mode="actions_retry",
        transition_recovery_rule_ids=["recover-key", "recover-click"],
    )
    executor._current_plan = SimpleNamespace(
        _original_initial_rules=[key, click, owner],
        initial_rules=[owner],
        monitoring_rules=[],
    )
    calls = []
    monkeypatch.setattr(
        executor,
        "_execute_rule",
        lambda active_rule, **_kwargs: calls.append(active_rule.rule_id) or _success(executor, active_rule),
    )

    assert executor._execute_transition_recovery_actions(owner, "6-2") is True
    assert calls == ["recover-key", "recover-key", "recover-click"]


def test_failure_mode_can_return_to_stable_previous_rule_id(monkeypatch):
    executor = _prepare_executor(monkeypatch)
    alerts = []
    executor.set_callbacks(on_transition_recovery_alert=lambda details: alerts.append(details))
    monkeypatch.setattr(executor, "_execute_rule", lambda active_rule, **_kwargs: _success(executor, active_rule))
    monkeypatch.setattr(executor, "_scan_transition_confirmation", lambda _spec: None)
    monkeypatch.setattr(
        executor,
        "_wait_for_transition_confirmation",
        lambda *_args, **_kwargs: {"status": "timeout", "waited": 0.5},
    )

    result = executor._execute_rule_with_retry(
        _rule(
            transition_recovery_count=1,
            transition_failure_mode="goto_rule",
            transition_failure_rule_id="stable-previous-id",
        ),
        ["expected.png"],
        next_rule=_next_rule(),
        step_num="6-2",
    )

    assert result.success is True
    assert result.rewind_previous_action is True
    assert result.rewind_target_rule_id == "stable-previous-id"
    assert len(alerts) == 1


def test_disabled_transition_option_preserves_legacy_repeat_count(monkeypatch):
    executor = _prepare_executor(monkeypatch)
    calls = []
    monkeypatch.setattr(
        executor,
        "_execute_rule",
        lambda active_rule, **_kwargs: calls.append(active_rule.rule_id) or _success(executor, active_rule),
    )

    result = executor._execute_rule_with_retry(
        _rule(
            action_type="hotkey",
            target_image=None,
            action_keys=["enter"],
            repeat_count=4,
            transition_recovery_enabled=False,
        ),
        step_num="2",
    )

    assert result.success is True
    assert calls == ["source"] * 4


def test_sequence_action_conversion_keeps_transition_settings():
    action = Action(
        action_id="action-source",
        action_type="hotkey",
        keys=["enter"],
        transition_recovery_policy=TRANSITION_POLICY_FORCE_ON,
        transition_recovery_enabled=True,
        transition_verify_mode="custom_image",
        transition_verify_image="verify.png",
        transition_verify_region=[1, 2, 3, 4],
        transition_verify_confidence=0.94,
        transition_verify_color=True,
        transition_verify_brightness=True,
        transition_recovery_mode="actions_retry",
        transition_recovery_count=3,
        transition_recovery_rule_ids=["recover"],
        transition_failure_mode="goto_rule",
        transition_failure_rule_id="previous",
    )
    holder = SimpleNamespace()
    holder._sequence_action_to_playback_rule = (
        lambda source: PlayerView._sequence_action_to_playback_rule(holder, source)
    )

    rule = holder._sequence_action_to_playback_rule(action)

    assert rule.transition_recovery_enabled is True
    assert rule.transition_recovery_policy == TRANSITION_POLICY_FORCE_ON
    assert rule.transition_verify_mode == "custom_image"
    assert rule.transition_verify_image == "verify.png"
    assert rule.transition_verify_region == [1, 2, 3, 4]
    assert rule.transition_verify_confidence == 0.94
    assert rule.transition_verify_color is True
    assert rule.transition_verify_brightness is True
    assert rule.transition_recovery_rule_ids == ["recover"]
    assert rule.transition_failure_rule_id == "previous"


def test_auto_hunt_6_2_uses_bounded_transition_recovery():
    plan = AutomationPlan.from_dict(
        json.loads(AUTO_HUNT_PLAN.read_text(encoding="utf-8")),
        templates_dir=ROOT / "data" / "templates",
    )

    def find(rules, rule_id):
        for candidate in rules:
            if candidate.rule_id == rule_id:
                return candidate
            nested = find(candidate.children, rule_id)
            if nested is not None:
                return nested
        return None

    source = find(plan.initial_rules, "rule_41a59d7d")
    assert source is not None
    assert plan.transition_recovery_auto_enabled is True
    assert source.description == "문파요강+복숭아 받기"
    assert source.repeat_count == 1
    assert source.transition_recovery_enabled is True
    assert source.transition_recovery_policy == TRANSITION_POLICY_FORCE_ON
    assert source.transition_verify_mode == "next_action"
    assert source.transition_verify_timeout == 5.0
    assert source.transition_recovery_mode == "refocus_retry"
    assert source.transition_recovery_count == 3
    assert source.transition_failure_mode == "alert_wait"
    assert source.children[0].target_image.endswith("img_f42d20e9.png")

    executor = RuleExecutor()
    flattened = executor._flatten_rules(plan.initial_rules)
    source_index = next(
        index for index, candidate in enumerate(flattened)
        if candidate.rule_id == source.rule_id
    )
    next_rule = executor._next_runtime_rule(flattened, source_index + 1)
    assert next_rule is source.children[0]
    assert executor._target_images_for_rule(next_rule) == [source.children[0].target_image]

    flattened_with_steps = flatten_enabled_transition_items(plan.initial_rules)
    context_exclusions = build_transition_recovery_context_exclusions(plan.initial_rules)
    auto_eligible = 0
    for index, (candidate, _step) in enumerate(flattened_with_steps):
        if transition_recovery_policy_for(candidate) != TRANSITION_POLICY_AUTO:
            continue
        candidate_next = (
            flattened_with_steps[index + 1][0]
            if index + 1 < len(flattened_with_steps)
            else None
        )
        if evaluate_auto_transition_recovery(
            candidate,
            candidate_next,
            transition_target_images(candidate_next) if candidate_next else [],
            context_exclusions.get(id(candidate)),
        ).eligible:
            auto_eligible += 1
    assert auto_eligible > 0


def test_transition_callback_is_cleared_with_other_executor_callbacks():
    executor = RuleExecutor()
    callback = lambda _details: None

    executor.set_callbacks(on_transition_recovery_alert=callback)
    assert executor._on_transition_recovery_alert is callback

    executor.clear_callbacks()
    assert executor._on_transition_recovery_alert is None


def test_transition_recovery_ui_is_scrollable_and_does_not_widen_action_rows():
    player_text = PLAYER_VIEW_SOURCE.read_text(encoding="utf-8-sig")
    main_text = MAIN_WINDOW_SOURCE.read_text(encoding="utf-8-sig")

    assert 'dialog.geometry("760x780")' in player_text
    assert 'dialog.geometry("800x700")' in player_text
    assert 'body = ctk.CTkScrollableFrame(dialog, fg_color="transparent")' in player_text
    assert 'text="호환 액션 자동 복구"' in player_text
    assert 'values=["전체", "적용 대상", "제외"]' in player_text
    assert player_text.count('text="화면복구 자동 OFF"') == 2
    assert '"자동 (플랜 설정 따름)": TRANSITION_POLICY_AUTO' in player_text
    assert '"강제 ON": TRANSITION_POLICY_FORCE_ON' in player_text
    assert '"강제 OFF": TRANSITION_POLICY_FORCE_OFF' in player_text
    assert player_text.count("실행 확인·복구 설정") >= 4
    assert "플랜 재로드 값이 저장 값과 일치하지 않습니다" in player_text
    assert "재생목록 재로드 값이 저장 값과 일치하지 않습니다" in player_text
    assert "on_transition_recovery_alert=on_transition_recovery_alert" in main_text
    assert "transition-recovery-alert" in main_text
    assert 'f"transition_recovery:{alert_key}"' in main_text


def test_partial_and_sequence_playback_keep_plan_auto_policy():
    player_text = PLAYER_VIEW_SOURCE.read_text(encoding="utf-8-sig")
    main_text = MAIN_WINDOW_SOURCE.read_text(encoding="utf-8-sig")

    assert player_text.count("transition_recovery_auto_enabled=bool(") >= 4
    assert main_text.count("transition_recovery_auto_enabled=bool(") >= 2
    assert (
        'transition_recovery_policy=getattr(action, "transition_recovery_policy", "")'
        in player_text
    )
    assert '"3. 복구 실패 후"' in player_text
