"""Interruptible window preparation shared by the two playback adapters."""

import math
import time

from ..utils.window_control import WindowController, validate_window_control_config


def wait_for_window_control(
    config, *, should_stop, is_paused, wait, on_wait=None, controller=None,
    skip_on_not_found=False, skip_timeout=0.0,
):
    """Wait for a verified state, optionally skipping a continuously missing target."""
    try:
        config = validate_window_control_config(config)
        if skip_on_not_found:
            skip_timeout = float(skip_timeout)
            if not math.isfinite(skip_timeout):
                raise ValueError("스킵 대기시간은 유한한 숫자여야 합니다.")
            skip_timeout = max(0.0, skip_timeout)
    except (TypeError, ValueError, OverflowError) as exc:
        return False, f"창 제어 설정 오류: {exc}"
    controller = controller if controller is not None else WindowController()
    last_log = None
    missing_since = None
    paused_since = None
    while not should_stop():
        if is_paused():
            if paused_since is None:
                paused_since = time.monotonic()
            if wait(0.1):
                return False, "실행 중지됨"
            continue
        if paused_since is not None:
            if missing_since is not None:
                missing_since += time.monotonic() - paused_since
            paused_since = None
        result = controller.step(config)
        if should_stop():
            break
        if result.status != "missing":
            missing_since = None
        if is_paused():
            paused_since = time.monotonic()
            continue
        if result.ready:
            return True, result.message
        if result.status in {"unsupported", "invalid_config"}:
            return False, result.message
        now = time.monotonic()
        # Never bypass ambiguity or denied activation: those targets do exist.
        if skip_on_not_found and result.status == "missing":
            if missing_since is None:
                missing_since = now
            if now - missing_since >= skip_timeout:
                return True, f"스킵됨 (대상 창 없음, {now - missing_since:.1f}초 대기)"
        else:
            missing_since = None
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
