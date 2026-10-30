"""Permanent regressions for the six resolved audit findings; no real HID is used."""

import inspect
import re
import threading
import time
from types import SimpleNamespace

import pytest

from src.utils import config as config_module
from src.ui import settings_view
from src.player import rule_executor
from src.analyzer.automation_models import AutomationRule


def test_unreadable_config_must_survive_automatic_save(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    original = b'{"player": {"speed_multiplier": 1.5}, "ui": '
    path.write_bytes(original)
    monkeypatch.setattr(config_module, "CONFIG_FILE", path)
    manager = object.__new__(config_module.ConfigManager)
    manager._config = None
    monkeypatch.setattr(manager, "_seed_packaged_defaults", lambda _: None)
    manager.load()
    assert manager._load_status == "error"
    manager.save()
    assert path.read_bytes() == original


def test_rejected_settings_must_not_change_shared_config(monkeypatch):
    config = config_module.AppConfig()
    original = config.system.pc_number
    monkeypatch.setattr(settings_view, "get_config", lambda: config)
    monkeypatch.setattr(settings_view, "save_config", lambda: pytest.fail("Invalid form must not save"))
    values = {
        "_pc_number_var": "audit-changed",
        "_discord_enabled_var": False,
        "_discord_stuck_seconds_var": "180",
        "_discord_cooldown_seconds_var": "30",
        "_shutdown_time_var": "00:00",
        "_arduino_baud_var": "9600",
        "_speed_var": "invalid-speed",
        "_login_action_repeat_count_var": "4",
        "_github_repo_var": "",
    }
    form = SimpleNamespace()
    names = re.findall(r"self\.(\w+_var)\.get\(\)",
                       inspect.getsource(settings_view.SettingsView._save_settings))
    for name in names:
        setattr(form, name, SimpleNamespace(get=lambda n=name: values.get(n, "0")))
    form._parse_int = lambda *args: settings_view.SettingsView._parse_int(form, *args)
    form._parse_float = lambda *args: settings_view.SettingsView._parse_float(form, *args)
    form._seq_write_groups_to_config = lambda _: None
    form._show_message = lambda *args: None
    assert settings_view.SettingsView._save_settings(form) is False
    assert config.system.pc_number == original


def test_stop_must_not_wait_for_device_io(monkeypatch):
    executor = rule_executor.RuleExecutor()
    monkeypatch.setattr(rule_executor, "block_automation_input", lambda *args: None)
    calls = []
    def slow_release():
        calls.append(1)
        time.sleep(0.25)
        return True
    monkeypatch.setattr(rule_executor, "get_input_controller",
                        lambda: SimpleNamespace(release_all=slow_release))
    started = time.perf_counter()
    executor.stop()
    elapsed = time.perf_counter() - started
    assert executor._stop_event.is_set()
    assert elapsed < 0.1, f"UI caller blocked for {elapsed:.3f}s"
    assert executor._stop_cleanup_done.wait(2)
    assert calls == [1]


def test_wait_action_must_wake_on_stop(monkeypatch):
    executor = rule_executor.RuleExecutor()
    executor._pause_event.set()
    entered = threading.Event()
    real_wait = executor._auto_list_wait
    def observed_wait(seconds):
        entered.set()
        return real_wait(seconds)
    monkeypatch.setattr(executor, "_auto_list_wait", observed_wait)
    rule = AutomationRule(action_type="wait", action_text="0.4")
    results = []
    worker = threading.Thread(target=lambda: results.append(executor._execute_rule(rule)))
    worker.start()
    try:
        assert entered.wait(2)
        executor._stop_event.set()
        worker.join(0.1)
        still_running = worker.is_alive()
    finally:
        worker.join(2)
    assert not still_running
    assert len(results) == 1 and results[0].success is False


def test_replaced_template_must_invalidate_cache(tmp_path):
    import cv2
    import numpy as np
    from src.analyzer.template_matcher import TemplateMatcher

    path = tmp_path / "template.png"
    first = np.full((8, 8, 3), 30, dtype=np.uint8)
    second = np.full((8, 8, 3), 200, dtype=np.uint8)
    path.write_bytes(cv2.imencode(".png", first)[1].tobytes())
    matcher = TemplateMatcher()
    assert np.array_equal(matcher._load_template(str(path)), first)
    path.write_bytes(cv2.imencode(".png", second)[1].tobytes())
    assert np.array_equal(matcher._load_template(str(path)), second)


def test_sequence_save_must_reject_missing_database_record(tmp_path):
    from src.database.db_manager import DatabaseManager
    from src.database.models import Action, Sequence
    from src.ui.player_view import SequenceDetailDialog

    manager = object.__new__(DatabaseManager)
    manager._db_path = tmp_path / "missing.db"
    manager._wal_initialized = False
    manager._ensure_database()
    sequence = Sequence(id=10000, name="missing", actions=[Action(action_type="wait")])
    dialog = SimpleNamespace(_sequence=sequence, _db=manager, _modified=True)
    saved = SequenceDetailDialog._save_sequence_with_options(dialog, show_message=False)
    assert manager.get_sequence(sequence.id) is None
    assert saved is False
    assert dialog._modified is True
