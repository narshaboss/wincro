"""Selected-window I/O. Never used merely by opening the GPT Play UI."""

import ctypes
from ctypes import wintypes
import os
import time

from ..agent_bridge.contracts import BridgeError
from .diagnostics import concise


def windows():
    from ..utils.window_control import _WindowsBackend
    return _WindowsBackend().list_windows()


class WindowIO:
    def __init__(self, target):
        from ..utils.window_control import _WindowsBackend
        from ..utils.input_controller import get_input_controller
        self.target = target
        self.windows = _WindowsBackend()
        self.controller = get_input_controller()
        self.used_keys = set()
        self.trace = None
        self.preparation_check = lambda: None
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.user.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        self.user.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
        self.user.WindowFromPoint.argtypes = [wintypes.POINT]
        self.user.WindowFromPoint.restype = wintypes.HWND
        self.user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        self.user.GetAncestor.restype = wintypes.HWND
        self.user.GetAsyncKeyState(0x7B)  # Discard an F12 press from before this session.
        if target.process_id == os.getpid():
            raise BridgeError("TARGET_DENIED", "WinCro 자체는 플레이 대상으로 선택할 수 없습니다.")

    def prepare(self):
        from ..utils.input_controller import wait_for_input_cleanup, unblock_automation_input
        check = getattr(self, "preparation_check", lambda: None)
        check()
        if not wait_for_input_cleanup(timeout=0):
            raise BridgeError("INPUT_CLEANUP_PENDING", "이전 입력 장치 정리가 끝난 뒤 계속하세요.")
        if not self.controller.play_lab_prepare():
            raise BridgeError("DEVICE_NOT_READY", "GPT 플레이는 이미 연결된 Arduino의 KQ·MM 지원이 필요합니다. 펌웨어를 자동 수정하지 않습니다.")
        self._log("device_ready", "입력 장치 준비 확인 완료 · 아직 키·마우스 입력은 없습니다.")
        self._prepare_foreground(check)
        check()
        if not unblock_automation_input():
            raise BridgeError("INPUT_CLEANUP_PENDING", "이전 입력 해제 실패 상태입니다. 입력 장치를 확인한 뒤 계속하세요.")

    def set_diagnostics(self, trace, check):
        self.trace, self.preparation_check = trace, check

    def _log(self, event, message):
        if getattr(self, "trace", None):
            self.trace.emit(event, message)

    def _focus_details(self, foreground=None):
        foreground = self.windows.get_foreground_window() if foreground is None else foreground
        actual = self.windows.get_window(foreground) if foreground else None
        actual_name = f"{actual.executable_name} | {actual.title}" if actual else "확인 불가"
        return f"대상 hwnd={self.target.handle}, 현재 전면 hwnd={foreground} ({concise(actual_name, 180)})"

    def _prepare_foreground(self, check):
        current = self.windows.get_window(self.target.handle)
        if current is None:
            raise BridgeError("TARGET_CLOSED", "선택한 게임 창이 종료되었습니다. 목록을 새로고침하세요.")
        # Only the explicit Start/Continue path may restore or request foreground.
        if current.minimized:
            self._log("window_restore", "선택한 게임 창의 최소화를 해제합니다.")
            self.windows.show_window_async(self.target, "restore")
        deadline = time.monotonic() + 2
        requested = False
        while True:
            check()
            current = self.windows.get_window(self.target.handle)
            if current is None:
                raise BridgeError("TARGET_CLOSED", "준비 중 게임 창이 종료되었습니다.")
            if not current.minimized and not requested:
                accepted = self.windows.set_foreground_window(self.target)
                requested = True
                self._log("focus_requested", f"전면 전환 요청 반환={accepted} · 실제 전면 상태를 최대 2초 확인합니다.")
            try:
                region = self.validate()
            except BridgeError as exc:
                if exc.code != "FOCUS_LOST":
                    raise
                if time.monotonic() >= deadline:
                    raise BridgeError("FOCUS_REQUIRED", "게임 창 전면 확인 실패 · " + self._focus_details()
                        + " · 다른 팝업·최소화·권한 차이를 확인하고 계속을 누르세요. 전면 확인 전에는 입력하지 않습니다.") from exc
                time.sleep(.05)
            else:
                self._log("focus_ready", "게임 창 전면 확인 완료 · " + self._focus_details() + f" · 화면범위={region}")
                return

    def validate(self, expected_region=None):
        from ..utils.window_control import _same_identity
        current = self.windows.get_window(self.target.handle)
        if current is None or not _same_identity(self.target, current):
            raise BridgeError("TARGET_CLOSED", "게임 창이 종료되거나 교체되었습니다.")
        foreground = self.windows.get_foreground_window()
        if current.minimized or foreground != current.handle:
            raise BridgeError("FOCUS_LOST", "선택한 게임 창이 앞에 있지 않습니다. 입력을 중단합니다. "
                              + self._focus_details(foreground) + f" · 최소화={current.minimized}")
        rect, point = wintypes.RECT(), wintypes.POINT(0, 0)
        if not self.user.GetClientRect(current.handle, ctypes.byref(rect)) or not self.user.ClientToScreen(current.handle, ctypes.byref(point)):
            raise BridgeError("TARGET_UNAVAILABLE", "게임 화면 범위를 확인하지 못했습니다.")
        region = [point.x, point.y, point.x + rect.right, point.y + rect.bottom]
        if rect.right <= 0 or rect.bottom <= 0 or rect.right * rect.bottom > 8_000_000:
            raise BridgeError("TARGET_UNAVAILABLE", "게임 창 크기가 유효하지 않습니다.")
        if (self.user.GetSystemMetrics(76) != 0 or self.user.GetSystemMetrics(77) != 0
                or region[0] < 0 or region[1] < 0 or region[2] > self.user.GetSystemMetrics(0)
                or region[3] > self.user.GetSystemMetrics(1)):
            raise BridgeError("DISPLAY_LAYOUT_UNSUPPORTED", "초기 GPT 플레이는 원점 (0,0)인 주 모니터 안의 게임 창만 지원합니다. 테스트 화면 배치를 확인하세요.")
        if expected_region is not None and list(expected_region) != region:
            raise BridgeError("TARGET_MOVED", "관찰 후 창 위치·크기가 바뀌었습니다. 다시 관찰하세요.")
        return region

    def capture(self):
        import mss
        from PIL import Image
        region = self.validate()
        with mss.mss() as capture:
            shot = capture.grab({"left": region[0], "top": region[1],
                "width": region[2] - region[0], "height": region[3] - region[1]})
            result = Image.frombytes("RGB", shot.size, shot.rgb)
        self.validate(region)
        return result, region

    def send(self, action, region, check):
        check()
        self.validate(region)
        if action["kind"] == "key":
            self.used_keys.add(action["key"])
            return self.controller.play_lab_tap(action["key"])
        x, y = region[0] + action["x"], region[1] + action["y"]
        self._log("pointer_move", f"클릭 전 마우스 이동: 화면좌표({x}, {y}) · 아직 클릭하지 않았습니다.")
        if not self.controller.play_lab_move_to(x, y, check, duration=.2):
            raise BridgeError("POINTER_MOVE_UNCONFIRMED", f"커서 이동 완료 미확인: ({x}, {y}). 클릭 명령은 보내지 않았습니다.")
        self._log("pointer_ready", f"마우스 위치 확인 완료: ({x}, {y}) · 클릭 위치의 창을 재확인합니다.")
        check()
        self.validate(region)
        hit = self.user.WindowFromPoint(wintypes.POINT(x, y))
        if not hit or int(self.user.GetAncestor(hit, 2) or hit) != self.target.handle:
            raise BridgeError("TARGET_OCCLUDED", "다른 창이 클릭 위치를 가리고 있습니다.")
        self._log("click_send", "선택한 게임 창에 왼쪽 클릭을 전송합니다.")
        return self.controller.play_lab_click()

    def release(self):
        return self.controller.play_lab_release(tuple(self.used_keys))

    def emergency_pressed(self):
        return bool(self.user.GetAsyncKeyState(0x7B) & 0x8001)  # Include a short press between polls.
