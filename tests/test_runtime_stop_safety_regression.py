from pathlib import Path

from src.utils import input_controller
from src.utils.input_controller import InputController


def test_automation_input_block_prevents_late_key_input(monkeypatch):
    controller = InputController()
    pressed = []

    monkeypatch.setattr(controller, "_use_arduino", lambda: False)
    monkeypatch.setattr(controller, "_strict_mode", lambda: False)
    monkeypatch.setattr(input_controller.pyautogui, "press", lambda key: pressed.append(key))

    input_controller.block_automation_input("test")
    try:
        assert controller.press("up") is False
        assert pressed == []
    finally:
        input_controller.unblock_automation_input()


def test_automation_input_block_prevents_late_mouse_move(monkeypatch):
    controller = InputController()
    moves = []

    monkeypatch.setattr(controller, "_use_arduino", lambda: False)
    monkeypatch.setattr(controller, "_strict_mode", lambda: False)
    monkeypatch.setattr(input_controller.pyautogui, "moveTo", lambda *args, **kwargs: moves.append((args, kwargs)))

    input_controller.block_automation_input("test")
    try:
        assert controller.move_to(10, 20) is False
        assert moves == []
    finally:
        input_controller.unblock_automation_input()


def test_plan_detail_partial_stop_uses_hard_stop_for_hidden_game_mode():
    source = Path("src/ui/player_view.py").read_text(encoding="utf-8", errors="ignore")
    start = source.index("class PlanDetailDialog")
    stop_start = source.index("def _stop_execution(self):", start)
    stop_end = source.index("def _game_mode_wait_seconds", stop_start)
    body = source[stop_start:stop_end]

    assert "request_hard_stop" in body
    assert "manual_partial_game_mode_stop" in body
    assert "block_automation_input" in body


def test_app_close_force_stops_registered_game_modes():
    source = Path("src/ui/main_window.py").read_text(encoding="utf-8", errors="ignore")
    start = source.index("def _on_close(self):")
    end = source.index("def _show_help", start)
    body = source[start:end]

    assert "force_stop_all_game_modes" in body
    assert "save_config()" in body
    assert "shutdown_done.wait(timeout=12.0)" in body
    assert "os._exit(1)" in body


def test_game_mode_start_unblocks_automation_input():
    source = Path("src/ui/player_view.py").read_text(encoding="utf-8", errors="ignore")
    start = source.index("def _start_execution(self):", source.index("class GameModeDialog"))
    end = source.index("def _stop_execution(self):", start)
    body = source[start:end]

    assert "unblock_automation_input()" in body
