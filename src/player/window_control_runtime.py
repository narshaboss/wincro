"""Interruptible window preparation shared by the two playback adapters."""

import time

from ..utils.window_control import WindowController, validate_window_control_config


def wait_for_window_control(
    config, *, should_stop, is_paused, wait, on_wait=None, controller=None,
):
    """Wait for a verified state without reporting polling as playback progress."""
    try:
        config = validate_window_control_config(config)
    except (TypeError, ValueError) as exc:
        return False, f"창 제어 설정 오류: {exc}"
    controller = controller if controller is not None else WindowController()
    last_log = None
    while not should_stop():
        if is_paused():
            if wait(0.1):
                return False, "실행 중지됨"
            continue
        result = controller.step(config)
        if should_stop():
            break
        if is_paused():
            continue
        if result.ready:
            return True, result.message
        if result.status in {"unsupported", "invalid_config"}:
            return False, result.message
        now = time.monotonic()
        if on_wait is not None and (last_log is None or now - last_log >= 15):
            on_wait(result.message)
            last_log = now
        # Short slices make pause/stop responsive without repeatedly touching windows.
        for _ in range(5):
            if should_stop() or is_paused():
                break
            if wait(0.1):
                return False, "실행 중지됨"
    return False, "실행 중지됨"
