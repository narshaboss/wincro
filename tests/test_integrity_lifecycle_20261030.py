"""Temporary-disk and simulated-input regressions; never drive user applications."""

import copy
import inspect
import json
import os
import re
import threading
import time
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np
import pytest

from src.analyzer.automation_models import AutomationPlan, AutomationRule
from src.analyzer.template_matcher import TemplateMatcher
from src.database.db_manager import DatabaseManager
from src.database.models import Action, Sequence
from src.player import rule_executor as runtime
from src.ui import player_view, settings_view
from src.ui.main_window import MainWindow
from src.ui.player_view import GameModeDialog, SequenceDetailDialog
from src.utils import config as configuration
from src.utils import input_controller
from src.utils.discord_notifier import DiscordAlert, build_discord_payload


def manager_at(tmp_path, monkeypatch):
    monkeypatch.setattr(configuration, "CONFIG_FILE", tmp_path / "config.json")
    manager = object.__new__(configuration.ConfigManager)
    manager._config = configuration.AppConfig()
    manager._source_read_failed = False
    manager._load_status, manager._load_error = "missing", ""
    monkeypatch.setattr(manager, "_seed_packaged_defaults", lambda _: None)
    monkeypatch.setattr(manager, "_normalize_loaded_local_config", lambda _: False)
    return manager


@pytest.mark.parametrize("raw", [b'{"player":', b'[]', b'{"player": []}', b'{"ui": "invalid"}'])
def test_unreadable_source_blocks_all_implicit_saves_and_preserves_original(tmp_path, monkeypatch, raw):
    manager = manager_at(tmp_path, monkeypatch)
    configuration.CONFIG_FILE.write_bytes(raw)
    manager.load()
    assert manager.get_load_status() == "error"
    assert manager.save() is False
    assert manager.save(configuration.AppConfig()) is False
    assert manager.update(version="changed") is False
    assert manager._persist_loaded_player_config(manager.get()) is False
    assert configuration.CONFIG_FILE.read_bytes() == raw


def test_explicit_reset_keeps_unreadable_original_backup_and_reloads_saved_defaults(tmp_path, monkeypatch):
    manager = manager_at(tmp_path, monkeypatch)
    original = b'{"incomplete":'
    configuration.CONFIG_FILE.write_bytes(original)
    manager.load()
    restored = manager.reset()
    backups = list(tmp_path.glob("config.json.before-reset-*.bak"))
    assert len(backups) == 1 and backups[0].read_bytes() == original
    assert manager._config_to_dict(restored) == json.loads(configuration.CONFIG_FILE.read_text(encoding="utf-8"))
    assert manager.get_load_status() == "loaded"


def test_failed_candidate_save_keeps_live_object_and_allows_verified_retry(tmp_path, monkeypatch):
    manager = manager_at(tmp_path, monkeypatch)
    original, section = manager.get(), manager.get().player
    original.system.pc_number = "original"
    assert manager.save()
    candidate = copy.deepcopy(original)
    candidate.system.pc_number = "updated"
    candidate.player.login_action_repeat_count = 2
    real_dump = configuration.dump_json_file
    monkeypatch.setattr(configuration, "dump_json_file", Mock(side_effect=OSError("disk unavailable")))
    assert manager.save(candidate) is False
    assert manager.get() is original and manager.get().system.pc_number == "original"
    assert manager.get().player is section and section.login_action_repeat_count == 4
    monkeypatch.setattr(configuration, "dump_json_file", real_dump)
    assert manager.save(candidate) is True
    assert manager.get() is original and manager.get().player is section
    assert section.login_action_repeat_count == 2
    saved = json.loads(configuration.CONFIG_FILE.read_text(encoding="utf-8"))
    assert saved == manager._config_to_dict(candidate)
    manager._config = None
    assert manager._config_to_dict(manager.load()) == saved


def test_settings_candidate_merges_unrelated_runtime_changes_and_rejects_conflicts(tmp_path, monkeypatch):
    manager = manager_at(tmp_path, monkeypatch)
    assert manager.save()
    baseline = copy.deepcopy(manager.get())
    candidate = copy.deepcopy(baseline)
    candidate.system.pc_number = "17"
    manager.get().update.last_version = "background-update-result"
    assert manager.save(candidate, baseline=baseline)
    assert manager.get().update.last_version == "background-update-result"
    assert manager.get().system.pc_number == "17"
    before = configuration.CONFIG_FILE.read_bytes()
    baseline = copy.deepcopy(manager.get())
    candidate = copy.deepcopy(baseline)
    candidate.system.pc_number = "18"
    manager.get().system.pc_number = "19"
    assert manager.save(candidate, baseline=baseline) is False
    assert manager.get().system.pc_number == "19"
    assert configuration.CONFIG_FILE.read_bytes() == before


def settings_form(monkeypatch, manager):
    values = {
        "_pc_number_var": "17", "_discord_enabled_var": False, "_discord_webhook_var": "",
        "_discord_stuck_seconds_var": "180", "_discord_cooldown_seconds_var": "300",
        "_shutdown_time_var": "00:00", "_arduino_baud_var": "115200",
        "_speed_var": "1.0", "_wait_var": "500", "_retry_var": "3",
        "_login_action_repeat_count_var": "2", "_github_repo_var": "narshaboss/wincro",
        "_app_name_var": manager.get().ui.app_name, "_auto_start_var": False,
        "_stop_key_var": "escape", "_window_mode_var": "플레이 모드",
    }
    form = SimpleNamespace()
    for name in re.findall(r"self\.(\w+_var)\.get\(\)", inspect.getsource(settings_view.SettingsView._save_settings)):
        setattr(form, name, SimpleNamespace(get=lambda n=name: values.get(n, False)))
    form._parse_int = lambda *args: settings_view.SettingsView._parse_int(form, *args)
    form._parse_float = lambda *args: settings_view.SettingsView._parse_float(form, *args)
    form._seq_write_groups_to_config = lambda _: None
    form._show_message = Mock()
    form._sync_shutdown_schedule_from_settings = Mock()
    form._update_auto_start_registry = Mock()
    form.winfo_toplevel = lambda: SimpleNamespace(update_title=Mock())
    monkeypatch.setattr(settings_view, "get_config", manager.get)
    monkeypatch.setattr(settings_view, "save_config", manager.save)
    return form, values


def test_settings_save_is_durable_before_live_apply_or_external_side_effects(tmp_path, monkeypatch):
    manager = manager_at(tmp_path, monkeypatch)
    form, values = settings_form(monkeypatch, manager)
    before = manager._config_to_dict(manager.get())
    real_dump = configuration.dump_json_file
    monkeypatch.setattr(configuration, "dump_json_file", Mock(side_effect=OSError("write failed")))
    assert settings_view.SettingsView._save_settings(form) is False
    assert manager._config_to_dict(manager.get()) == before
    form._sync_shutdown_schedule_from_settings.assert_not_called()
    form._update_auto_start_registry.assert_not_called()
    monkeypatch.setattr(configuration, "dump_json_file", real_dump)
    assert settings_view.SettingsView._save_settings(form) is True
    assert manager.get().system.pc_number == "17" and manager.get().player.login_action_repeat_count == 2
    assert json.loads(configuration.CONFIG_FILE.read_text(encoding="utf-8")) == manager._config_to_dict(manager.get())
    form._sync_shutdown_schedule_from_settings.assert_called_once()


def test_database_save_reads_back_nested_actions_and_rejects_mismatches(tmp_path):
    db = object.__new__(DatabaseManager)
    db._db_path, db._wal_initialized = tmp_path / "sequences.db", False
    db._ensure_database()
    sequence = Sequence(name="test", actions=[Action(action_type="hotkey", keys=["enter"],
        children=[Action(action_type="wait", wait_after=1.5)])])
    sequence.id = db.create_sequence(sequence)
    dialog = SimpleNamespace(_sequence=sequence, _db=db, _modified=True)
    sequence.actions[0].children[0].wait_after = 2.5
    assert SequenceDetailDialog._save_sequence_with_options(dialog, show_message=False)
    assert dialog._modified is False
    assert [a.to_dict() for a in db.get_sequence(sequence.id).actions] == [a.to_dict() for a in sequence.actions]
    wrong = copy.deepcopy(sequence)
    wrong.actions[0].children[0].wait_after = 99
    dialog._db = SimpleNamespace(update_sequence=lambda _: True, get_sequence=lambda _: wrong)
    dialog._modified = True
    assert SequenceDetailDialog._save_sequence_with_options(dialog, show_message=False) is False
    assert dialog._modified is True and sequence.actions[0].children[0].wait_after == 2.5


def write_image(path, value):
    array = np.full((8, 8, 3), value, dtype=np.uint8)
    path.write_bytes(cv2.imencode(".png", array)[1].tobytes())
    return array


def test_template_and_mask_deletion_replacement_and_creation_invalidate_cache(tmp_path):
    path, mask = tmp_path / "image.png", tmp_path / "image_mask.png"
    first = write_image(path, 20)
    write_image(mask, 255)
    matcher = TemplateMatcher()
    assert np.array_equal(matcher._load_template(str(path)), first)
    assert matcher._load_mask(str(path)) is not None
    mask.unlink()
    assert matcher._load_mask(str(path)) is None
    write_image(mask, 100)
    assert np.all(matcher._load_mask(str(path)) == 100)
    path.unlink()
    assert matcher._load_template(str(path)) is None and str(path) not in matcher._cache
    second = write_image(path, 200)
    assert np.array_equal(matcher._load_template(str(path)), second)


def test_atomic_template_replace_with_restored_mtime_is_not_a_cache_hit(tmp_path):
    path, replacement = tmp_path / "same.png", tmp_path / "replacement.png"
    write_image(path, 20)
    matcher = TemplateMatcher()
    matcher._load_template(str(path))
    stat = path.stat()
    expected = write_image(replacement, 200)
    os.utime(replacement, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    os.replace(replacement, path)
    assert np.array_equal(matcher._load_template(str(path)), expected)
    matcher.clear_cache()
    assert not matcher._cache and not matcher._cache_versions
    assert not matcher._mask_cache and not matcher._mask_versions


def test_stop_is_nonblocking_and_new_executor_waits_for_old_worker_exit(monkeypatch):
    released, old_exit, new_started = threading.Event(), threading.Event(), threading.Event()
    releases = []
    controller = SimpleNamespace(release_all=lambda: releases.append(threading.current_thread().name) or released.set() or True)
    monkeypatch.setattr(runtime, "get_input_controller", lambda: controller)
    monkeypatch.setattr(runtime.pyautogui, "position", lambda: (0, 0))
    source, target = runtime.RuleExecutor(), runtime.RuleExecutor()
    old = threading.Thread(target=lambda: old_exit.wait(3), name="old-fixture")
    source._execution_thread = old
    old.start()
    original_loop = target._execution_loop
    target._execution_loop = lambda: new_started.set() or original_loop()
    result = []
    starter = None
    try:
        started = time.perf_counter()
        source.stop()
        assert time.perf_counter() - started < 0.1
        assert released.wait(2) and source._stop_cleanup_done.wait(2)
        assert input_controller.wait_for_input_cleanup(0) is False
        starter = threading.Thread(target=lambda: result.append(target.execute_plan(AutomationPlan(name="next"))))
        starter.start()
        assert not new_started.wait(0.05)
        old_exit.set()
        starter.join(3)
        assert result == [True] and new_started.wait(2)
        target._execution_thread.join(3)
        assert not target._execution_thread.is_alive()
        assert input_controller.wait_for_input_cleanup(2)
    finally:
        old_exit.set()
        old.join(3)
        if starter is not None:
            starter.join(3)
        input_controller.wait_for_input_cleanup(3)
        input_controller.unblock_automation_input()


@pytest.mark.parametrize("rule_type", ["fixed_sequence", "type_text"])
def test_random_text_stop_does_not_emit_later_characters_or_success(monkeypatch, rule_type):
    executor = runtime.RuleExecutor()
    emitted = []

    def type_text(char):
        emitted.append(char)
        executor._stop_event.set()
        return True

    monkeypatch.setattr(runtime, "get_input_controller", lambda: SimpleNamespace(type_text=type_text))
    rule = AutomationRule(rule_type=rule_type, action_type="type", action_text="abc", typing_random=True, typing_delay=10)
    result = executor._execute_rule(rule)
    assert result.success is False and emitted == ["a"]


def test_monitoring_text_uses_the_same_cancellation_contract(monkeypatch):
    executor = runtime.RuleExecutor()
    emitted = []

    def type_text(char):
        emitted.append(char)
        executor._stop_event.set()
        return True

    monkeypatch.setattr(runtime, "get_input_controller", lambda: SimpleNamespace(type_text=type_text))
    result = executor._execute_monitor_action({"type": "텍스트 입력", "text": "abc", "typing_random": True})
    assert result is None and emitted == ["a"]


def test_template_content_changes_are_detected_even_when_file_metadata_is_identical(tmp_path, monkeypatch):
    path = tmp_path / "same.png"
    first = write_image(path, 30)
    matcher = TemplateMatcher()
    metadata = matcher._file_version(path)
    monkeypatch.setattr(matcher, "_file_version", lambda _: metadata)
    decoder = Mock(wraps=cv2.imdecode)
    monkeypatch.setattr(cv2, "imdecode", decoder)
    assert np.array_equal(matcher._load_template(str(path)), first)
    for _ in range(20):
        assert np.array_equal(matcher._load_template(str(path)), first)
    assert decoder.call_count == 1
    second = write_image(path, 200)
    assert np.array_equal(matcher._load_template(str(path)), second)
    assert decoder.call_count == 2


def test_action_call_failure_keeps_the_actual_failing_rule_id(monkeypatch):
    target = AutomationRule(rule_id="target", action_type="click")
    caller = AutomationRule(rule_id="caller", action_type="action_call", action_call_rule_id="target")
    executor = runtime.RuleExecutor()
    executor._current_plan = SimpleNamespace(initial_rules=[caller], _original_initial_rules=[target, caller])
    failure = runtime.RuleExecutionResult(rule_id="actual-child", success=False, message="child failed")
    monkeypatch.setattr(executor, "_execute_rule_tree_once", lambda *args: failure)
    result = executor._execute_action_call(caller, datetime.now())
    assert result.success is False and result.rule_id == "caller"
    assert result.failure_rule_id == "actual-child"


def test_stop_during_startup_device_release_does_not_wait_for_lifecycle_lock(monkeypatch):
    entered, finish = threading.Event(), threading.Event()
    executor = runtime.RuleExecutor()
    results = []

    def release_all():
        entered.set()
        assert finish.wait(3)
        return True

    monkeypatch.setattr(runtime, "get_input_controller", lambda: SimpleNamespace(release_all=release_all))
    monkeypatch.setattr(runtime.pyautogui, "position", lambda: (0, 0))
    starter = threading.Thread(target=lambda: results.append(executor.execute_plan(AutomationPlan(name="startup"))))
    starter.start()
    try:
        assert entered.wait(2)
        started = time.perf_counter()
        executor.stop()
        assert time.perf_counter() - started < 0.1
        assert input_controller.wait_for_input_cleanup(0) is False
        finish.set()
        starter.join(3)
        assert results == [False] and executor._execution_thread is None
        assert input_controller.wait_for_input_cleanup(3)
    finally:
        finish.set()
        starter.join(3)
        input_controller.wait_for_input_cleanup(3)
        input_controller.unblock_automation_input()


def test_template_cache_and_version_metadata_are_bounded_together(tmp_path):
    matcher = TemplateMatcher()
    for i in range(matcher._MAX_CACHE_SIZE + 3):
        path = tmp_path / f"image_{i}.png"
        write_image(path, i)
        write_image(path.with_name(path.stem + "_mask.png"), 255)
        matcher._load_template(str(path))
        matcher._load_mask(str(path))
    assert len(matcher._cache) == len(matcher._cache_versions) == matcher._MAX_CACHE_SIZE
    assert len(matcher._mask_cache) == len(matcher._mask_versions) == matcher._MAX_CACHE_SIZE


@pytest.mark.parametrize("invalid_path", [None, 123, ["image.png"], "\x00invalid.png"])
def test_invalid_template_or_mask_path_fails_closed(invalid_path):
    matcher = TemplateMatcher()
    assert matcher._load_template(invalid_path) is None
    assert matcher._load_mask(invalid_path) is None
    assert not matcher._cache and not matcher._mask_cache


def notification_context(executor, plan):
    context = SimpleNamespace(_rule_executor=executor, _mini_active_plan=plan,
        _mini_notification_last_progress_text="stale [3-5]", _mini_notification_last_progress_snapshot={
            "action_number": "3-5", "action_name": "stale child", "message": "stale [3-5]"})
    for name in ("_mini_format_notification_progress", "_mini_build_notification_snapshot", "_mini_infer_stuck_reason"):
        setattr(context, name, getattr(MainWindow, name).__get__(context))
    return context


@pytest.mark.parametrize("child_succeeds", [True, False])
def test_nested_return_restores_parent_identity_but_failures_keep_child_identity(monkeypatch, child_succeeds):
    executor = runtime.RuleExecutor()
    child = AutomationRule(rule_id="child", action_type="hotkey", description="child", wait_after=0)
    parent = AutomationRule(rule_id="parent", action_type="click", description="parent", children=[child])
    plan = AutomationPlan(name="test", initial_rules=[parent])
    executor._current_plan = plan
    executor._original_step_by_rule_id = {"parent": "3-1", "child": "3-5"}
    executor._set_action_context(parent, "3-1")
    executor._update_progress("[3-1] parent")
    snapshots = []
    executor.set_callbacks(on_progress=snapshots.append)
    monkeypatch.setattr(executor, "_execute_rule_with_retry", lambda rule, **kwargs:
        runtime.RuleExecutionResult(rule_id=rule.rule_id, success=child_succeeds, message="simulated"))
    result = executor._execute_child_rules_for_repeat_click(parent, datetime.now())
    expected_step, expected_name = ("3-1", "parent") if child_succeeds else ("3-5", "child")
    assert (result is None) is child_succeeds
    assert executor._current_step_num == expected_step
    assert executor.progress.current_action_number == expected_step
    child_snapshot = next(item for item in snapshots if item.current_rule == "child")
    assert child_snapshot.current_action_number == "3-5"
    context = notification_context(executor, plan)
    fields = MainWindow._mini_build_stuck_diagnostic_fields(context, 180, 180)
    assert dict(fields)["현재 액션"] == f"[{expected_step}] {expected_name}"
    payload = build_discord_payload(DiscordAlert(title="stuck", pc_number="7", fields=fields))["content"]
    assert payload.splitlines()[0] == "PC번호: 7번"
    assert f"[{expected_step}] {expected_name}" in payload
    executor._set_action_context(AutomationRule(rule_id="new", description="new"), "9")
    assert child_snapshot.current_rule == "child" and child_snapshot.current_action_number == "3-5"


def test_delayed_mini_status_callback_cannot_overwrite_a_different_executor():
    queued = []
    old, new = object(), object()
    context = SimpleNamespace(_rule_executor=old, _mini_playback_generation=3,
        _mini_current_repeat=0, _mini_total_repeat=1, _sequence_mode=False,
        _mini_record_notification_progress=Mock(), after=lambda delay, callback: queued.append(callback),
        _mini_is_current_playback_generation=lambda generation: generation == 3,
        winfo_exists=lambda: True, _mini_status=object(), _mini_set_status=Mock(),
        _mini_update_active_bar=Mock())
    progress = runtime.ExecutionProgress(current_rule="old", current_action_number="3-5", message="old")
    MainWindow._mini_on_progress(context, progress)
    assert len(queued) == 1
    context._rule_executor = new
    queued.pop()()
    context._mini_set_status.assert_not_called()


def test_invalid_progress_does_not_reset_stuck_deadline():
    context = SimpleNamespace(_mini_notification_last_progress_at=10.0,
        _mini_format_notification_progress=Mock(side_effect=ValueError("invalid progress")))
    MainWindow._mini_record_notification_progress(context, object())
    assert context._mini_notification_last_progress_at == 10.0


def test_ui_release_is_nonblocking_coalesced_and_waits_for_actor_exit(monkeypatch):
    entered, release, actor_exit = threading.Event(), threading.Event(), threading.Event()
    calls = []

    def reset():
        calls.append(threading.current_thread().name)
        entered.set()
        assert release.wait(3)
        return True

    monkeypatch.setattr(input_controller, "get_input_controller", lambda: SimpleNamespace(release_all=reset))
    actor = threading.Thread(target=lambda: actor_exit.wait(3))
    actor.start()
    try:
        started = time.perf_counter()
        done = input_controller.request_input_release("fixture stop", workers=(actor,))
        assert time.perf_counter() - started < 0.1
        assert entered.wait(2)
        assert input_controller.request_input_release("duplicate stop", workers=(actor,)) is done
        assert input_controller.unblock_automation_input() is False
        release.set()
        assert not done.wait(0.05)
        actor_exit.set()
        assert done.wait(3) and done.succeeded is True
        assert len(calls) == 2 and input_controller.wait_for_input_cleanup(0)
    finally:
        release.set()
        actor_exit.set()
        actor.join(3)
        input_controller.wait_for_input_cleanup(3)
        input_controller.unblock_automation_input()


def test_failed_ui_release_refuses_unblock_until_a_fresh_verified_release(monkeypatch):
    reset = Mock(return_value=False)
    monkeypatch.setattr(input_controller, "get_input_controller", lambda: SimpleNamespace(release_all=reset))
    failed = input_controller.request_input_release("failed fixture")
    assert failed.wait(3) and failed.succeeded is False
    assert input_controller.unblock_automation_input() is False
    reset.return_value = True
    recovered = input_controller.request_input_release("verified retry fixture")
    assert recovered.wait(3) and recovered.succeeded is True
    assert input_controller.unblock_automation_input() is True


@pytest.mark.parametrize("cancelled", [False, True])
def test_pending_special_mode_start_waits_without_ui_blocking_and_cannot_restart_after_cancel(monkeypatch, cancelled):
    callbacks = []
    monkeypatch.setattr(player_view, "unblock_automation_input", lambda: False)
    monkeypatch.setattr(player_view, "wait_for_input_cleanup", lambda **kwargs: False)
    release = Mock(side_effect=AssertionError("Do not add a release while another cleanup is pending"))
    monkeypatch.setattr(player_view, "request_input_release", release)
    host = SimpleNamespace(_is_running=False, _ensure_arduino_ready_for_execution=lambda _: True,
        _append_log=Mock(), after=lambda delay, callback: callbacks.append(callback), winfo_exists=lambda: True)
    host._retry_pending_execution_start = GameModeDialog._retry_pending_execution_start.__get__(host)
    GameModeDialog._start_execution(host)
    assert host._is_running and host._pending_start_token is not None and len(callbacks) == 1
    host._start_execution = Mock()
    if cancelled:
        host._pending_start_token = None
    monkeypatch.setattr(player_view, "wait_for_input_cleanup", lambda **kwargs: True)
    callbacks.pop()()
    assert host._start_execution.call_count == (0 if cancelled else 1)
    release.assert_not_called()


def test_pending_special_mode_start_is_not_reported_as_fresh_navigation_progress():
    host = SimpleNamespace(_mini_gm_dialog=SimpleNamespace(_pending_start_token=object()),
        _mini_notification_last_progress_at=5.0)
    MainWindow._mini_record_game_mode_notification_activity(host)
    assert host._mini_notification_last_progress_at == 5.0


def test_legacy_stop_does_not_wait_for_hid_or_listener_on_ui_thread():
    from src.player.action_player import ActionPlayer, PlaybackProgress, PlayerState

    release, entered = threading.Event(), threading.Event()
    player = object.__new__(ActionPlayer)
    player._finalize_lock = threading.Lock()
    player._execution_generation, player._execution_finalized = 1, False
    player._state, player._thread = PlayerState.RUNNING, None
    player._progress = PlaybackProgress()
    player._update_progress = Mock()
    player._finalize_execution = Mock()

    def delayed():
        entered.set()
        assert release.wait(3)
        return True

    player._reset_input_state = lambda _: delayed()
    player._emergency_stop = SimpleNamespace(stop=delayed)
    try:
        started = time.perf_counter()
        assert player.stop()
        assert time.perf_counter() - started < 0.1
        assert entered.wait(2)
    finally:
        release.set()
        assert input_controller.wait_for_input_cleanup(3)
        input_controller.unblock_automation_input()


def test_legacy_stop_completion_waits_for_old_actor_and_final_release():
    from src.player.action_player import ActionPlayer, PlaybackProgress, PlayerState

    release, release_started, finalizing, completed = (threading.Event() for _ in range(4))
    player = object.__new__(ActionPlayer)
    player._finalize_lock = threading.Lock()
    player._execution_generation, player._execution_finalized = 1, False
    player._execution_finalizing = False
    player._state, player._progress = PlayerState.RUNNING, PlaybackProgress()
    player._execution_log = player._current_sequence = player._execution_logger = None
    player._emergency_stop = SimpleNamespace(stop=lambda: None)
    player._close_execution_logger = Mock()
    player._update_progress = Mock()
    callbacks = []

    def reset(context):
        if context == "legacy-execution-stop":
            release_started.set()
            assert release.wait(3)
        return True

    def on_complete(success, message):
        callbacks.append((success, message, worker.is_alive(), input_controller.wait_for_input_cleanup(0)))
        completed.set()

    def actor():
        assert finalizing.wait(3)
        player._finalize_execution(False, "stopped", generation=1)

    player._reset_input_state, player._on_complete = reset, on_complete
    worker = player._thread = threading.Thread(target=actor)
    worker.start()
    try:
        assert player.stop() and release_started.wait(2)
        finalizing.set()
        worker.join(2)
        assert not worker.is_alive()
        assert not completed.wait(0.05)
        release.set()
        assert completed.wait(3)
        assert callbacks == [(False, "stopped", False, True)]
    finally:
        finalizing.set()
        release.set()
        worker.join(3)
        assert input_controller.wait_for_input_cleanup(3)
        input_controller.unblock_automation_input()
