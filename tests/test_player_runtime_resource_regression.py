import logging
from pathlib import Path


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
