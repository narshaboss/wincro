import json
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

from src.ui import main_window
from src.ui.main_window import MainWindow, _load_mini_plan_summaries, _persist_mini_plan_repeat
from src.ui.ui_batcher import UiCallbackDispatcher


ROOT = Path(__file__).resolve().parents[1]


def _run_fresh_python(script: str) -> list[str]:
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def _last_json_output(lines: list[str]):
    for line in reversed(lines):
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    raise AssertionError(f"fresh Python process did not emit JSON: {lines!r}")


class _FakeWidget:
    def __init__(self, master=None):
        self.master = master
        self._ui_dispatcher = None
        self.after_calls = []
        self.cancelled = []

    def winfo_exists(self):
        return True

    def after(self, delay, callback=None):
        token = f"after-{len(self.after_calls) + 1}"
        self.after_calls.append((delay, callback, token))
        return token

    def after_cancel(self, token):
        self.cancelled.append(token)


class _CountedResource:
    def __init__(self):
        self.calls = 0

    def close(self):
        self.calls += 1

    def cleanup(self):
        self.calls += 1

    def stop(self):
        self.calls += 1

    def clear_callbacks(self):
        self.calls += 1


def test_ui_package_import_does_not_eagerly_load_editor_views():
    lines = _run_fresh_python(
        "import json, sys; import src.ui; "
        "print(json.dumps({"
        "'player_view': 'src.ui.player_view' in sys.modules, "
        "'analyzer_view': 'src.ui.analyzer_view' in sys.modules, "
        "'pandas': 'pandas' in sys.modules, "
        "'pytesseract': 'pytesseract' in sys.modules}))"
    )

    assert _last_json_output(lines) == {
        "player_view": False,
        "analyzer_view": False,
        "pandas": False,
        "pytesseract": False,
    }


def test_ui_lazy_exports_preserve_main_window_and_submodule_imports():
    lines = _run_fresh_python(
        "import json, sys; "
        "from src.ui import MainWindow, BaseView, COLORS, capture_cleanup; "
        "print(json.dumps({"
        "'exports_ok': bool(MainWindow and BaseView and COLORS and capture_cleanup), "
        "'player_view': 'src.ui.player_view' in sys.modules, "
        "'pandas': 'pandas' in sys.modules}))"
    )

    assert _last_json_output(lines) == {
        "exports_ok": True,
        "player_view": False,
        "pandas": False,
    }


def test_rule_executor_import_does_not_construct_unrelated_runtime_singletons():
    lines = _run_fresh_python(
        "import json, sys; from src.player import RuleExecutor; "
        "print(json.dumps({"
        "'rule_executor': 'src.player.rule_executor' in sys.modules, "
        "'action_player': 'src.player.action_player' in sys.modules, "
        "'video_analyzer': 'src.analyzer.video_analyzer' in sys.modules, "
        "'db_manager': 'src.database.db_manager' in sys.modules, "
        "'type_ok': RuleExecutor.__name__ == 'RuleExecutor'}))"
    )

    assert _last_json_output(lines) == {
        "rule_executor": True,
        "action_player": False,
        "video_analyzer": False,
        "db_manager": False,
        "type_ok": True,
    }


def test_utility_package_import_does_not_eagerly_load_security_or_config():
    lines = _run_fresh_python(
        "import json, sys; import src.utils; "
        "print(json.dumps({"
        "'config': 'src.utils.config' in sys.modules, "
        "'security': 'src.utils.security' in sys.modules, "
        "'logger': 'src.utils.logger' in sys.modules}))"
    )

    assert _last_json_output(lines) == {
        "config": False,
        "security": False,
        "logger": False,
    }


def test_app_constructor_does_not_initialize_statistics_database():
    lines = _run_fresh_python(
        "import json, sys; from src.app import WinCroApp; app = WinCroApp(); "
        "print(json.dumps({"
        "'db_manager': 'src.database.db_manager' in sys.modules, "
        "'db_is_none': app._db is None}))"
    )

    assert _last_json_output(lines) == {
        "db_manager": False,
        "db_is_none": True,
    }


def test_editor_child_dispatchers_share_one_root_poll_timer():
    root_widget = _FakeWidget()
    root_dispatcher = UiCallbackDispatcher(root_widget, tick_ms=20)
    root_widget._ui_dispatcher = root_dispatcher

    child_widget = _FakeWidget(master=root_widget)
    child_dispatcher = UiCallbackDispatcher(
        child_widget,
        tick_ms=20,
        max_callbacks_per_tick=200,
    )
    child_widget._ui_dispatcher = child_dispatcher

    completed = []

    def submit_from_worker():
        for value in range(100):
            child_dispatcher.post(lambda item=value: completed.append(item))

    worker = threading.Thread(target=submit_from_worker)
    worker.start()
    worker.join(timeout=2.0)

    assert len(root_widget.after_calls) == 1
    assert child_widget.after_calls == []
    assert root_dispatcher.pending_count() == 1
    assert child_dispatcher.pending_count() == 100

    root_dispatcher._drain()

    assert completed == list(range(100))
    assert child_dispatcher.pending_count() == 0
    assert child_widget.after_calls == []
    child_dispatcher.close()
    root_dispatcher.close()


def test_closed_child_dispatcher_drops_queued_parent_callback():
    root_widget = _FakeWidget()
    root_dispatcher = UiCallbackDispatcher(root_widget)
    root_widget._ui_dispatcher = root_dispatcher
    child_widget = _FakeWidget(master=root_widget)
    child_dispatcher = UiCallbackDispatcher(child_widget)

    completed = []
    worker = threading.Thread(target=lambda: child_dispatcher.post(lambda: completed.append(True)))
    worker.start()
    worker.join(timeout=2.0)
    child_dispatcher.close()
    root_dispatcher._drain()

    assert completed == []
    assert child_dispatcher.pending_count() == 0
    root_dispatcher.close()


def test_delegated_dispatcher_continues_large_batches_without_recursive_drain():
    root_widget = _FakeWidget()
    root_dispatcher = UiCallbackDispatcher(root_widget)
    root_widget._ui_dispatcher = root_dispatcher
    child_widget = _FakeWidget(master=root_widget)
    child_dispatcher = UiCallbackDispatcher(
        child_widget,
        max_callbacks_per_tick=7,
        max_millis_per_tick=1000,
    )

    completed = []

    def submit_from_worker():
        for value in range(25):
            child_dispatcher.post(lambda item=value: completed.append(item))

    worker = threading.Thread(target=submit_from_worker)
    worker.start()
    worker.join(timeout=2.0)
    root_dispatcher._drain()

    next_after = 0
    while child_dispatcher.pending_count():
        delay, callback, _token = child_widget.after_calls[next_after]
        next_after += 1
        assert delay == 0
        callback()

    assert completed == list(range(25))
    assert len(child_widget.after_calls) == 3
    child_dispatcher.close()
    root_dispatcher.close()


def test_mini_player_plan_index_is_lightweight_and_repeat_save_is_verified(tmp_path):
    plan_file = tmp_path / "plan_large.json"
    payload = {
        "plan_id": "plan_large",
        "name": "대형 플랜",
        "total_repeat_count": 4,
        "initial_rules": [{"payload": "x" * 5000} for _ in range(50)],
        "future_field": {"keep": True},
    }
    plan_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    summaries = _load_mini_plan_summaries(tmp_path)

    assert len(summaries) == 1
    assert summaries[0].name == "대형 플랜"
    assert summaries[0].plan_id == "plan_large"
    assert summaries[0].total_repeat_count == 4
    assert not hasattr(summaries[0], "initial_rules")

    _persist_mini_plan_repeat(plan_file, 7)
    reloaded = json.loads(plan_file.read_text(encoding="utf-8"))
    assert reloaded["total_repeat_count"] == 7
    assert reloaded["future_field"] == {"keep": True}
    assert len(reloaded["initial_rules"]) == 50


def test_main_window_resource_cleanup_is_idempotent():
    window = object.__new__(MainWindow)
    video = _CountedResource()
    listener = _CountedResource()
    executor = _CountedResource()
    loader = _CountedResource()
    log_panel = _CountedResource()
    view = _CountedResource()
    dispatcher = _CountedResource()
    cancelled = []

    window._resources_cleaned = False
    window._view_switch_after_id = None
    window._stop_template_video_capture_for_shutdown = video.close
    window._keyboard_listener = listener
    window._rule_executor = executor
    window._mini_cancel_notification_watchdog = lambda: cancelled.append("watchdog")
    window._mini_cancel_game_mode_wait = lambda: cancelled.append("game-mode")
    window._mini_plan_loader = loader
    window._mini_log_handler = None
    window._log_panel = log_panel
    window._views = {"player": view}
    window._ui_dispatcher = dispatcher

    MainWindow.cleanup_resources(window)
    MainWindow.cleanup_resources(window)

    assert video.calls == 1
    assert listener.calls == 1
    assert executor.calls == 2
    assert loader.calls == 1
    assert log_panel.calls == 1
    assert view.calls == 1
    assert dispatcher.calls == 1
    assert cancelled == ["watchdog", "game-mode"]


def test_editor_tab_switch_burst_keeps_only_latest_idle_callback():
    window = object.__new__(MainWindow)
    window._view_switch_after_id = None
    pending = {}
    counter = [0]

    def after_idle(callback):
        counter[0] += 1
        token = f"idle-{counter[0]}"
        pending[token] = callback
        return token

    window.after_idle = after_idle
    window.after_cancel = lambda token: pending.pop(token, None)
    completed = []

    for value in range(10_000):
        MainWindow._schedule_latest_view_switch(
            window,
            lambda item=value: completed.append(item),
        )

    assert len(pending) == 1
    callback = next(iter(pending.values()))
    callback()
    assert completed == [9_999]
    assert window._view_switch_after_id is None


def test_mode_switch_spawn_failure_restores_previous_mode(monkeypatch):
    window = object.__new__(MainWindow)
    window._mode_change_in_progress = False
    window._window_mode = "editor"
    window._config = SimpleNamespace(
        ui=SimpleNamespace(window_mode="editor"),
        update=SimpleNamespace(auto_check=True),
    )
    window._mode_switch_btn = SimpleNamespace(configure=lambda **_kwargs: None)
    window._is_running = False
    cleanup_calls = []
    window.cleanup_resources = lambda: cleanup_calls.append(True)

    monkeypatch.setattr(main_window, "save_config", lambda: True)
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("boom")))

    MainWindow._change_window_mode(window, "play")

    assert window._config.ui.window_mode == "editor"
    assert window._mode_change_in_progress is False
    assert cleanup_calls == []


def test_player_mode_hydrates_only_selected_plan_and_editor_uses_bulk_lock_query():
    main_text = (ROOT / "src" / "ui" / "main_window.py").read_text(encoding="utf-8-sig")
    player_text = (ROOT / "src" / "ui" / "player_view.py").read_text(encoding="utf-8-sig")
    app_text = (ROOT / "src" / "app.py").read_text(encoding="utf-8-sig")

    play_start = main_text.index("        def load_and_start():")
    play_end = main_text.index("    def _mini_prepare_new_playback_request", play_start)
    play_body = main_text[play_start:play_end]
    load_start = player_text.index("    def _load_sequences_async(self):")
    load_end = player_text.index("    def _apply_loaded_data", load_start)
    load_body = player_text[load_start:load_end]

    assert "self._refresh_mini_plans_sync()" not in play_body
    assert "AutomationPlan.from_dict" in play_body
    assert "logger.debug(f\"[미니플레이어] 룰" in play_body
    assert "logger.info(f\"[미니플레이어] 룰" not in play_body
    assert "self._db.get_all_recordings()" in load_body
    assert "get_recording_by_plan_id" not in load_body
    assert 'os.environ.pop("WINCRO_MODE_SWITCH_RESTART", "")' in app_text
    assert "cleanup_window = getattr(self._main_window, \"cleanup_resources\", None)" in app_text
