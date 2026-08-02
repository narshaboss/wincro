import logging
import threading
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig", errors="ignore")


def test_action_player_closes_per_run_execution_logger_handlers(tmp_path):
    from src.player.action_player import ActionPlayer

    player = object.__new__(ActionPlayer)
    logger = logging.getLogger("wincro_test.execution_handler_cleanup")
    for existing in list(logger.handlers):
        logger.removeHandler(existing)
        existing.close()

    handler = logging.FileHandler(tmp_path / "execution.log", encoding="utf-8")
    logger.addHandler(handler)
    player._execution_logger = logger

    player._close_execution_logger()

    assert player._execution_logger is None
    assert handler not in logger.handlers
    assert handler.stream is None
    assert logger.name not in logging.Logger.manager.loggerDict


def test_execution_logger_is_unique_and_does_not_propagate_to_root():
    from src.utils.logger import create_execution_logger

    first = create_execution_logger("resource-regression")
    second = create_execution_logger("resource-regression")
    try:
        assert first.name != second.name
        assert first.propagate is False
        assert second.propagate is False
        assert len(first.handlers) == 1
        assert len(second.handlers) == 1
    finally:
        for logger in (first, second):
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                handler.close()


def test_action_player_start_and_finalize_are_wired_to_logger_cleanup():
    text = _read_text(ROOT / "src" / "player" / "action_player.py")

    start_method = text[
        text.index("def start("):
        text.index("def pause(", text.index("def start("))
    ]
    finalize_method = text[
        text.index("def _finalize_execution("):
        text.index("def play_automation_plan(", text.index("def _finalize_execution("))
    ]

    assert "self._close_execution_logger()" in start_method
    assert "self._close_execution_logger()" in finalize_method


def test_action_player_cleans_old_run_before_completion_callback():
    from src.player.action_player import ActionPlayer

    player = object.__new__(ActionPlayer)
    player._finalize_lock = threading.Lock()
    player._execution_generation = 4
    player._execution_finalized = False
    player._execution_log = None
    player._current_sequence = None
    player._db = SimpleNamespace()
    player._emergency_stop = SimpleNamespace(stop=lambda: events.append("stop-listener"))
    player._thread = None
    player._execution_logger = "old-logger"
    player._reset_input_state = lambda context: events.append(context) or True

    events = []

    def close_logger():
        events.append("close-old-logger")
        player._execution_logger = None

    def on_complete(_success, _message):
        events.append("callback-start-next")
        player._execution_logger = "new-logger"

    player._close_execution_logger = close_logger
    player._on_complete = on_complete

    player._finalize_execution(True, "complete", generation=4)
    player._finalize_execution(True, "duplicate", generation=4)

    assert events == [
        "legacy-execution-complete",
        "stop-listener",
        "close-old-logger",
        "callback-start-next",
    ]
    assert player._execution_logger == "new-logger"


def test_action_player_ignores_stale_generation_completion():
    from src.player.action_player import ActionPlayer

    player = object.__new__(ActionPlayer)
    player._finalize_lock = threading.Lock()
    player._execution_generation = 8
    player._execution_finalized = False
    events = []
    player._reset_input_state = lambda _context: events.append("release") or True

    player._finalize_execution(False, "old", generation=7)

    assert events == []
    assert player._execution_finalized is False


def test_action_player_rejects_new_generation_while_old_worker_is_alive():
    from src.player.action_player import ActionPlayer, PlayerState

    player = object.__new__(ActionPlayer)
    player._finalize_lock = threading.Lock()
    player._execution_generation = 2
    player._execution_finalized = True
    player._state = PlayerState.STOPPED
    player._thread = SimpleNamespace(is_alive=lambda: True)

    assert player._try_begin_execution_generation() is None
    assert player._execution_generation == 2
    assert player._execution_finalized is True


def test_action_player_start_failure_rolls_back_reserved_execution(monkeypatch):
    import importlib

    action_player_module = importlib.import_module("src.player.action_player")
    from src.player.action_player import ActionPlayer, PlaybackProgress, PlayerState

    events = []
    player = object.__new__(ActionPlayer)
    player._finalize_lock = threading.Lock()
    player._execution_generation = 0
    player._execution_finalized = True
    player._state = PlayerState.IDLE
    player._thread = None
    player._progress = PlaybackProgress()
    player._execution_logger = None
    player._execution_log = None
    player._current_sequence = None
    player._on_complete = None
    player._db = SimpleNamespace(
        create_execution_log=lambda _log: (_ for _ in ()).throw(RuntimeError("db unavailable")),
    )
    player._emergency_stop = SimpleNamespace(stop=lambda: events.append("stop-listener"))
    player._get_enabled_actions = lambda _sequence: [SimpleNamespace()]
    player._reset_input_state = lambda context: events.append(context) or True
    monkeypatch.setattr(
        action_player_module,
        "block_automation_input",
        lambda reason: events.append(f"block:{reason}"),
    )
    sequence = SimpleNamespace(actions=[SimpleNamespace()], id=1, name="start-failure")

    assert player.play(sequence) is False
    assert player._state == PlayerState.FAILED
    assert player._execution_finalized is True
    assert player._thread is None
    assert events == [
        "legacy-execution-start",
        "block:ActionPlayer.start_failed",
        "legacy-execution-start-failed",
        "stop-listener",
    ]


def test_action_player_stop_waits_for_worker_exit_before_finalize(monkeypatch):
    import importlib

    action_player_module = importlib.import_module("src.player.action_player")
    from src.player.action_player import ActionPlayer, PlaybackProgress, PlayerState

    allow_exit = threading.Event()
    finalized = threading.Event()

    class Worker:
        def is_alive(self):
            return not allow_exit.is_set()

        def join(self, timeout=None):
            allow_exit.wait(timeout)

    player = object.__new__(ActionPlayer)
    player._finalize_lock = threading.Lock()
    player._execution_generation = 3
    player._execution_finalized = False
    player._state = PlayerState.RUNNING
    player._thread = Worker()
    player._progress = PlaybackProgress(state=PlayerState.RUNNING)
    player._emergency_stop = SimpleNamespace(stop=lambda: None)
    player._reset_input_state = lambda _context: True
    player._update_progress = lambda _message: None
    player._finalize_execution = lambda *_args, **_kwargs: finalized.set()
    monkeypatch.setattr(action_player_module, "block_automation_input", lambda _reason: None)

    assert player.stop() is True
    assert finalized.wait(timeout=0.15) is False
    allow_exit.set()
    assert finalized.wait(timeout=2.0) is True


def test_action_player_keeps_execution_reserved_until_finalize_cleanup_finishes():
    from src.player.action_player import ActionPlayer, PlayerState

    cleanup_started = threading.Event()
    allow_cleanup = threading.Event()
    player = object.__new__(ActionPlayer)
    player._finalize_lock = threading.Lock()
    player._execution_generation = 9
    player._execution_finalized = False
    player._execution_finalizing = False
    player._state = PlayerState.STOPPED
    player._thread = None
    player._execution_log = None
    player._current_sequence = None
    player._db = SimpleNamespace()
    player._emergency_stop = SimpleNamespace(stop=lambda: None)
    player._execution_logger = None
    player._close_execution_logger = lambda: None
    player._on_complete = None

    def slow_reset(_context):
        cleanup_started.set()
        allow_cleanup.wait(timeout=2.0)
        return True

    player._reset_input_state = slow_reset
    worker = threading.Thread(
        target=player._finalize_execution,
        args=(False, "stopped", 9),
    )
    worker.start()
    assert cleanup_started.wait(timeout=1.0)

    assert player._try_begin_execution_generation() is None
    assert player._execution_finalized is False

    allow_cleanup.set()
    worker.join(timeout=2.0)
    assert not worker.is_alive()
    assert player._execution_finalized is True


def test_action_player_completion_release_failure_is_not_reported_as_success(monkeypatch):
    import importlib

    action_player_module = importlib.import_module("src.player.action_player")
    from src.player.action_player import ActionPlayer, PlayerState

    callbacks = []
    blocked = []
    player = object.__new__(ActionPlayer)
    player._finalize_lock = threading.Lock()
    player._execution_generation = 2
    player._execution_finalized = False
    player._execution_finalizing = False
    player._state = PlayerState.COMPLETED
    player._thread = None
    player._execution_log = None
    player._current_sequence = None
    player._db = SimpleNamespace()
    player._emergency_stop = SimpleNamespace(stop=lambda: None)
    player._execution_logger = None
    player._close_execution_logger = lambda: None
    player._reset_input_state = lambda _context: False
    player._on_complete = lambda success, message: callbacks.append((success, message))
    monkeypatch.setattr(
        action_player_module,
        "block_automation_input",
        lambda reason: blocked.append(reason),
    )

    player._finalize_execution(True, "finished", generation=2)

    assert blocked == ["ActionPlayer.completion_reset_failed"]
    assert callbacks[0][0] is False
    assert "입력 장치 안전 해제 실패" in callbacks[0][1]


def test_game_mode_destroy_closes_dialog_local_ui_queues():
    text = _read_text(ROOT / "src" / "ui" / "player_view.py")
    game_mode_text = text[text.index("class GameModeDialog"):]

    cleanup_method = game_mode_text[
        game_mode_text.index("def _close_runtime_ui_queues(self) -> None:"):
        game_mode_text.index("def destroy(self):", game_mode_text.index("def _close_runtime_ui_queues(self) -> None:"))
    ]
    destroy_method = game_mode_text[
        game_mode_text.index("def destroy(self):"):
        game_mode_text.index("def after(self,", game_mode_text.index("def destroy(self):"))
    ]
    on_close_method = game_mode_text[
        game_mode_text.index("def _on_close(self):", game_mode_text.index("def _save_window_geometry(self):")):
        game_mode_text.index("class SequenceDetailDialog", game_mode_text.index("def _on_close(self):", game_mode_text.index("def _save_window_geometry(self):")))
    ]

    assert '"_ui_log_pump", "_ui_dispatcher"' in cleanup_method
    assert "self._ui_call_queue.clear()" in cleanup_method
    assert "self._ui_log_flush_scheduled = False" in cleanup_method
    assert "setattr(self, attr, None)" in cleanup_method
    assert "self._close_runtime_ui_queues()" in destroy_method
    assert "self._close_runtime_ui_queues()" in on_close_method


def test_player_view_cleanup_releases_rule_executor_callbacks():
    text = _read_text(ROOT / "src" / "ui" / "player_view.py")
    player_view_text = text[text.index("class PlayerView(BaseView):"):]
    cleanup_method = player_view_text[player_view_text.index("def cleanup(self) -> None:"):]

    assert "self._rule_executor.stop()" in cleanup_method
    assert "self._rule_executor.clear_callbacks()" in cleanup_method
    assert cleanup_method.index("self._rule_executor.clear_callbacks()") < cleanup_method.index(
        "self._rule_executor = None"
    )
