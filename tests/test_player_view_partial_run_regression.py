from pathlib import Path
from types import SimpleNamespace
import threading

from src.analyzer.automation_models import AutomationPlan, AutomationRule
from src.database.models import Action
import src.ui.player_view as player_view_module
from src.ui.player_view import (
    GameModeDialog,
    PlanDetailDialog,
    _build_manual_partial_rules,
    _build_trigger_rewind_continuation,
    _detach_child_after_parent,
    _find_runnable_game_mode_index,
    _find_item_path_by_id,
    _flatten_children_after_parent,
    _manual_partial_start_index,
    _trigger_rewind_options,
)


def test_manual_partial_run_keeps_monitor_parent_start():
    child = AutomationRule(rule_id="child", action_type="double_click", description="real action")
    parent = AutomationRule(
        rule_id="parent",
        action_type="double_click",
        description="monitor parent",
        is_monitoring_mode=True,
        monitoring_watches=[{"image": "watch.png", "goto_index": 0}],
        children=[child],
    )
    after = AutomationRule(rule_id="after", action_type="click")
    flat_rules = [parent, child, after]

    assert _manual_partial_start_index(flat_rules, parent, 0) == 0


def test_manual_partial_run_from_monitor_parent_runs_parent_before_child():
    child = AutomationRule(rule_id="child", action_type="double_click", description="child action")
    parent = AutomationRule(
        rule_id="parent",
        action_type="double_click",
        description="monitor parent",
        is_monitoring_mode=True,
        monitoring_watches=[{"image": "watch.png", "goto_index": 0}],
        children=[child],
    )
    after = AutomationRule(rule_id="after", action_type="click")
    flat_rules = [parent, child, after]

    rules_to_run = _build_manual_partial_rules(flat_rules, _manual_partial_start_index(flat_rules, parent, 0))

    assert [rule.rule_id for rule in rules_to_run] == ["parent", "child", "after"]
    assert rules_to_run[0].children == []


def test_manual_partial_run_keeps_non_monitor_parent_start():
    child = AutomationRule(rule_id="child", action_type="double_click")
    parent = AutomationRule(rule_id="parent", action_type="click", children=[child])
    flat_rules = [parent, child]

    assert _manual_partial_start_index(flat_rules, parent, 0) == 0


def test_manual_partial_run_keeps_leaf_monitor_start():
    monitor = AutomationRule(
        rule_id="monitor",
        action_type="double_click",
        is_monitoring_mode=True,
        monitoring_watches=[{"image": "watch.png", "goto_index": 0}],
    )

    assert _manual_partial_start_index([monitor], monitor, 0) == 0


def test_manual_partial_run_preserves_click_until_children_without_duplicate():
    child = AutomationRule(rule_id="child", action_type="key_press", action_keys=["enter"])
    parent = AutomationRule(
        rule_id="parent",
        action_type="click",
        target_image="target.png",
        click_until_image_disappears=True,
        children=[child],
    )
    after = AutomationRule(rule_id="after", action_type="wait")
    flat_rules = [parent, child, after]

    rules_to_run = _build_manual_partial_rules(flat_rules, 0)

    assert [rule.rule_id for rule in rules_to_run] == ["parent", "after"]
    assert [rule.rule_id for rule in rules_to_run[0].children] == ["child"]


def test_manual_partial_run_preserves_auto_list_children_without_duplicate():
    craft = AutomationRule(rule_id="craft", action_type="click")
    extract = AutomationRule(rule_id="extract", action_type="click")
    parent = AutomationRule(
        rule_id="auto-list",
        action_type="auto_list",
        children=[craft, extract],
    )
    after = AutomationRule(rule_id="after", action_type="wait")
    flat_rules = [parent, craft, extract, after]

    rules_to_run = _build_manual_partial_rules(flat_rules, 0)

    assert [rule.rule_id for rule in rules_to_run] == ["auto-list", "after"]
    assert [rule.rule_id for rule in rules_to_run[0].children] == ["craft", "extract"]


def test_manual_partial_run_still_flattens_normal_parent_children():
    child = AutomationRule(rule_id="child", action_type="key_press", action_keys=["enter"])
    parent = AutomationRule(rule_id="parent", action_type="click", children=[child])
    flat_rules = [parent, child]

    rules_to_run = _build_manual_partial_rules(flat_rules, 0)

    assert [rule.rule_id for rule in rules_to_run] == ["parent", "child"]
    assert rules_to_run[0].children == []


def test_trigger_rewind_options_only_lists_enabled_actions_before_source():
    nested = AutomationRule(rule_id="nested", action_type="hotkey", description="nested")
    first = AutomationRule(rule_id="first", action_type="click", description="first", children=[nested])
    disabled = AutomationRule(rule_id="disabled", action_type="wait", description="disabled", enabled=False)
    source = AutomationRule(rule_id="source", action_type="click", description="source")
    after = AutomationRule(rule_id="after", action_type="wait", description="after")

    options = _trigger_rewind_options(
        [first, disabled, source, after],
        "rule_id",
        "source",
    )

    assert options == [
        ("◆ 상위 [1] first", "first"),
        ("↳ 하위 [1-1] nested", "nested"),
    ]


def test_trigger_rewind_continuation_uses_original_nested_rule_id():
    target = AutomationRule(rule_id="target", action_type="hotkey", description="target")
    parent = AutomationRule(rule_id="parent", action_type="click", children=[target])
    middle = AutomationRule(rule_id="middle", action_type="wait")
    source = AutomationRule(rule_id="source", action_type="game_mode")
    after = AutomationRule(rule_id="after", action_type="wait")

    continuation, resolved, error = _build_trigger_rewind_continuation(
        [parent, middle, source, after],
        source,
        "target",
    )

    assert error == ""
    assert resolved is target
    assert [rule.rule_id for rule in continuation] == ["target", "middle", "source", "after"]


def test_trigger_rewind_continuation_rejects_deleted_disabled_and_forward_targets():
    disabled = AutomationRule(rule_id="disabled", action_type="wait", enabled=False)
    source = AutomationRule(rule_id="source", action_type="game_mode")
    forward = AutomationRule(rule_id="forward", action_type="wait")
    roots = [disabled, source, forward]

    for target_id, expected in (
        ("missing", "찾을 수 없습니다"),
        ("disabled", "비활성화"),
        ("forward", "앞에 있어야"),
    ):
        continuation, resolved, error = _build_trigger_rewind_continuation(
            roots,
            source,
            target_id,
        )
        assert continuation == []
        assert resolved is None
        assert expected in error


def test_trigger_rewind_continuation_keeps_legacy_previous_behavior():
    previous = AutomationRule(rule_id="previous", action_type="wait")
    source = AutomationRule(rule_id="source", action_type="game_mode")
    after = AutomationRule(rule_id="after", action_type="wait")

    continuation, resolved, error = _build_trigger_rewind_continuation(
        [previous, source, after],
        source,
        None,
        legacy_previous_rule=previous,
        legacy_remaining_rules=[after],
    )

    assert error == ""
    assert resolved is previous
    assert [rule.rule_id for rule in continuation] == ["previous", "source", "after"]


def test_game_mode_trigger_gate_propagates_configured_rewind_target(monkeypatch):
    source = AutomationRule(
        rule_id="source",
        action_type="game_mode",
        trigger_image="trigger.png",
    )

    class FakeExecutor:
        def __init__(self):
            self._stop_event = None
            self._trigger_missing_rewind_attempts = None

        def set_callbacks(self, **_callbacks):
            return None

        def _handle_trigger_gate(self, *_args, **_kwargs):
            return SimpleNamespace(
                message="rewind",
                rewind_previous_action=True,
                rewind_target_rule_id="target",
                rewind_delay=0.25,
                skip_current_playlist=False,
            )

    dialog = GameModeDialog.__new__(GameModeDialog)
    dialog._source_rule = source
    dialog._source_previous_rule = None
    dialog._stop_event = threading.Event()
    dialog._trigger_rewind_attempts = {}
    dialog._rewind_previous_action = False
    dialog._rewind_target_rule_id = ""
    dialog._rewind_delay = 0.0
    dialog._completed_normally = False
    dialog._is_running = True
    dialog._schedule_ui_log = lambda *_args, **_kwargs: None
    monkeypatch.setattr(player_view_module, "RuleExecutor", FakeExecutor)

    assert dialog._handle_source_trigger_gate() is False
    assert dialog._rewind_previous_action is True
    assert dialog._rewind_target_rule_id == "target"
    assert dialog._rewind_delay == 0.25
    assert dialog._completed_normally is True
    assert dialog._stop_event.is_set()


def test_partial_game_mode_completion_restarts_from_configured_target():
    target = AutomationRule(rule_id="target", action_type="hotkey", description="target")
    middle = AutomationRule(rule_id="middle", action_type="wait")
    game_mode = AutomationRule(rule_id="game", action_type="game_mode")
    after = AutomationRule(rule_id="after", action_type="wait")
    dialog = object.__new__(PlanDetailDialog)
    dialog._plan = AutomationPlan(
        name="rewind partial",
        initial_rules=[target, middle, game_mode, after],
        monitoring_rules=[],
    )
    dialog._gm_remaining_rules = [after]
    dialog._gm_current_rule = game_mode
    dialog._gm_previous_rule = middle
    dialog._is_running = True
    dialog.winfo_exists = lambda: True
    dialog.title = lambda *_args: None
    dialog.configure = lambda **_kwargs: None
    dialog._set_partial_status_text = lambda *_args, **_kwargs: None
    dialog._run_remaining_rules = lambda rules: setattr(dialog, "captured_rules", rules)

    dialog._on_game_mode_complete(
        False,
        "rewind",
        rewind_previous_action=True,
        rewind_target_rule_id="target",
        rewind_delay=0,
    )

    assert [rule.rule_id for rule in dialog.captured_rules] == [
        "target",
        "middle",
        "game",
        "after",
    ]


def test_partial_game_mode_boundary_uses_shared_runtime_option_filter():
    disabled = AutomationRule(rule_id="disabled", action_type="game_mode", enabled=False)
    runnable = AutomationRule(rule_id="runnable", action_type="game_mode")
    config = SimpleNamespace(pumpkin_action_enabled=True)

    assert _find_runnable_game_mode_index(
        [disabled, runnable],
        {"disabled": object(), "runnable": object()},
        config,
    ) == 1


def test_partial_run_config_accessor_is_not_shadowed_by_a_local_import():
    for method in (
        PlanDetailDialog._test_run_rule_impl,
        PlanDetailDialog._run_remaining_via_executor,
        PlanDetailDialog._start_partial_executor,
    ):
        assert "get_config" not in method.__code__.co_varnames
        assert "get_rule_executor" not in method.__code__.co_names


def test_partial_run_button_reaches_dedicated_executor(monkeypatch):
    rule = AutomationRule(rule_id="partial-start", action_type="wait", description="부분실행 시작")
    plan = AutomationPlan(name="partial-test", initial_rules=[rule], monitoring_rules=[])
    executed = []

    class FakeExecutor:
        def __init__(self):
            self.callbacks = None

        def set_callbacks(self, **callbacks):
            self.callbacks = callbacks

        def execute_plan(self, partial_plan, **kwargs):
            executed.append((partial_plan, kwargs))

    class ImmediateThread:
        def __init__(self, target, **_kwargs):
            self.target = target

        def start(self):
            self.target()

    dialog = object.__new__(PlanDetailDialog)
    dialog._plan = plan
    dialog._is_running = False
    dialog._modified = False
    dialog._running_executor = None
    dialog._ensure_arduino_ready_for_playback = lambda _label: True
    dialog._start_partial_run_visual = lambda _rule_id: None
    dialog._stop_partial_run_visual = lambda: None
    dialog._notify_player_partial_run_started = lambda _mode: None
    dialog._notify_player_partial_run_stopped = lambda: None
    dialog._make_partial_progress_callback = lambda: None
    dialog.title = lambda *_args: None
    dialog.configure = lambda **_kwargs: None
    dialog.grab_release = lambda: None
    dialog.winfo_toplevel = lambda: SimpleNamespace(master=None)

    monkeypatch.setattr(player_view_module, "RuleExecutor", FakeExecutor)
    monkeypatch.setattr(
        player_view_module,
        "get_config",
        lambda: SimpleNamespace(
            player=SimpleNamespace(pumpkin_action_enabled=True),
            ui=SimpleNamespace(minimize_on_run=False),
        ),
    )
    monkeypatch.setattr(threading, "Thread", ImmediateThread)

    dialog._test_run_rule(rule)

    assert len(executed) == 1
    partial_plan, kwargs = executed[0]
    assert [item.rule_id for item in partial_plan.initial_rules] == [rule.rule_id]
    assert partial_plan._original_initial_rules is plan.initial_rules
    assert kwargs == {"allow_special_mode_handoff": True}
    assert isinstance(dialog._running_executor, FakeExecutor)


def test_partial_run_start_error_restores_idle_state(monkeypatch):
    rule = AutomationRule(rule_id="broken-start", action_type="wait", description="broken")
    dialog = object.__new__(PlanDetailDialog)
    dialog._plan = SimpleNamespace(name="partial-test")
    dialog._is_running = True
    dialog._running_executor = object()
    stopped = []
    shown = []
    dialog._test_run_rule_impl = lambda _rule: (_ for _ in ()).throw(RuntimeError("start failed"))
    dialog._stop_partial_run_visual = lambda: stopped.append("visual")
    dialog._notify_player_partial_run_stopped = lambda: stopped.append("notify")
    dialog.title = lambda *_args: None

    import tkinter.messagebox as messagebox

    monkeypatch.setattr(messagebox, "showerror", lambda *args: shown.append(args))

    dialog._test_run_rule(rule)

    assert dialog._is_running is False
    assert dialog._running_executor is None
    assert stopped == ["visual", "notify"]
    assert shown and "start failed" in shown[0][1]


def test_partial_executor_thread_error_restores_ui_state(monkeypatch):
    partial_plan = AutomationPlan(
        name="partial-thread-error",
        initial_rules=[AutomationRule(rule_id="wait", action_type="wait")],
        monitoring_rules=[],
    )
    completed = []

    class FailingExecutor:
        def set_callbacks(self, **_callbacks):
            return None

        def execute_plan(self, *_args, **_kwargs):
            raise RuntimeError("executor failed")

    class ImmediateThread:
        def __init__(self, target, **_kwargs):
            self.target = target

        def start(self):
            self.target()

    dialog = object.__new__(PlanDetailDialog)
    dialog._running_executor = None
    dialog._is_running = False
    dialog.title = lambda *_args: None
    dialog.configure = lambda **_kwargs: None
    dialog.grab_release = lambda: None
    dialog.winfo_toplevel = lambda: SimpleNamespace(master=None)
    dialog.winfo_exists = lambda: True
    dialog.after = lambda _delay, callback: callback()
    dialog._make_partial_progress_callback = lambda: None
    dialog._on_execution_complete = lambda: completed.append(True)

    monkeypatch.setattr(player_view_module, "RuleExecutor", FailingExecutor)
    monkeypatch.setattr(
        player_view_module,
        "get_config",
        lambda: SimpleNamespace(ui=SimpleNamespace(minimize_on_run=False)),
    )
    monkeypatch.setattr(threading, "Thread", ImmediateThread)

    dialog._start_partial_executor(partial_plan, log_label="부분실행")

    assert completed == [True]


def test_detach_rule_places_child_immediately_after_top_level_parent():
    child = AutomationRule(rule_id="child", action_type="wait", parent_id="parent")
    parent = AutomationRule(rule_id="parent", action_type="click", children=[child])
    after = AutomationRule(rule_id="after", action_type="wait")
    rules = [parent, after]

    assert _detach_child_after_parent(rules, parent, child, "rule_id") is True

    assert [rule.rule_id for rule in rules] == ["parent", "child", "after"]
    assert parent.children == []
    assert child.parent_id is None


def test_detach_rule_places_child_after_nested_parent_at_same_level():
    child = AutomationRule(rule_id="child", action_type="wait", parent_id="parent")
    parent = AutomationRule(rule_id="parent", action_type="click", parent_id="grand", children=[child])
    sibling = AutomationRule(rule_id="sibling", action_type="wait", parent_id="grand")
    grand = AutomationRule(rule_id="grand", action_type="click", children=[parent, sibling])
    rules = [grand]

    assert _detach_child_after_parent(rules, parent, child, "rule_id") is True

    assert [rule.rule_id for rule in grand.children] == ["parent", "child", "sibling"]
    assert parent.children == []
    assert child.parent_id == "grand"


def test_flatten_action_children_places_them_after_parent():
    child1 = Action(action_type="wait", action_id="child1", parent_id="parent")
    child2 = Action(action_type="wait", action_id="child2", parent_id="parent")
    parent = Action(action_type="click", action_id="parent", children=[child1, child2])
    after = Action(action_type="wait", action_id="after")
    actions = [parent, after]

    assert _flatten_children_after_parent(actions, parent, "action_id") == 2

    assert [action.action_id for action in actions] == ["parent", "child1", "child2", "after"]
    assert parent.children == []
    assert child1.parent_id is None
    assert child2.parent_id is None


def test_find_item_path_by_id_returns_full_ancestor_path():
    child = AutomationRule(rule_id="child", action_type="wait", parent_id="parent")
    parent = AutomationRule(rule_id="parent", action_type="click", parent_id="grand", children=[child])
    grand = AutomationRule(rule_id="grand", action_type="click", children=[parent])

    path = _find_item_path_by_id([grand], "child", "rule_id")

    assert [rule.rule_id for rule in path] == ["grand", "parent", "child"]


def test_partial_run_stop_updates_running_badge_without_row_rebuild():
    source = Path("src/ui/player_view.py").read_text(encoding="utf-8", errors="ignore")
    start = source.index("def _clear_current_partial_rule")
    end = source.index("def _make_partial_progress_callback", start)
    body = source[start:end]

    assert "self._active_partial_rule_id = None" in body
    assert "self._update_rule_row_in_place(previous_rule)" in body
    assert "self._refresh_rule_row(previous_rule_id)" not in body
    assert "self._schedule_action_list_refresh()" not in body


def test_partial_run_progress_callback_ignores_late_updates_after_stop():
    source = Path("src/ui/player_view.py").read_text(encoding="utf-8", errors="ignore")
    start = source.index("def _make_partial_progress_callback")
    end = source.index("def _start_partial_run_visual", start)
    body = source[start:end]

    assert "callback_generation" in body
    assert "_partial_run_generation" in body
    assert "not getattr(self, \"_is_running\", False)" in body
