"""Selected-window I/O. Never used merely by opening the GPT Play UI."""

import ctypes
from ctypes import wintypes
import os

from ..agent_bridge.contracts import BridgeError


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
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.user.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        self.user.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
        self.user.WindowFromPoint.argtypes = [wintypes.POINT]
        self.user.WindowFromPoint.restype = wintypes.HWND
        self.user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        self.user.GetAncestor.restype = wintypes.HWND
        if target.process_id == os.getpid():
            raise BridgeError("TARGET_DENIED", "WinCro 자체는 플레이 대상으로 선택할 수 없습니다.")

    def prepare(self):
        from ..utils.input_controller import is_arduino_enabled, is_arduino_strict_enabled
        if is_arduino_enabled() or is_arduino_strict_enabled():
            from ..utils.arduino_hid import get_arduino_hid
            device = get_arduino_hid()
            if not device.is_connected or not device.supports_key_combo_tap():
                raise BridgeError("DEVICE_NOT_READY", "먼저 기존 설정에서 Arduino를 연결하세요. GPT 플레이는 KQ 원자 입력 지원이 필요하며 펌웨어를 자동 수정하지 않습니다.")
        if not self.windows.set_foreground_window(self.target):
            raise BridgeError("FOCUS_REQUIRED", "선택한 게임 창을 앞으로 가져오지 못했습니다. 직접 선택한 뒤 계속하세요.")

    def validate(self, expected_region=None):
        from ..utils.window_control import _same_identity
        current = self.windows.get_window(self.target.handle)
        if current is None or not _same_identity(self.target, current):
            raise BridgeError("TARGET_CLOSED", "게임 창이 종료되거나 교체되었습니다.")
        if current.minimized or self.windows.get_foreground_window() != current.handle:
            raise BridgeError("FOCUS_LOST", "선택한 게임 창이 앞에 있지 않습니다. 입력을 중단합니다.")
        rect, point = wintypes.RECT(), wintypes.POINT(0, 0)
        if not self.user.GetClientRect(current.handle, ctypes.byref(rect)) or not self.user.ClientToScreen(current.handle, ctypes.byref(point)):
            raise BridgeError("TARGET_UNAVAILABLE", "게임 화면 범위를 확인하지 못했습니다.")
        region = [point.x, point.y, point.x + rect.right, point.y + rect.bottom]
        if rect.right <= 0 or rect.bottom <= 0 or rect.right * rect.bottom > 8_000_000:
            raise BridgeError("TARGET_UNAVAILABLE", "게임 창 크기가 유효하지 않습니다.")
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
            return self.controller.play_lab_tap(action["key"])
        x, y = region[0] + action["x"], region[1] + action["y"]
        if not self.controller.move_to(x, y, duration=.2):
            return False
        check()
        self.validate(region)
        hit = self.user.WindowFromPoint(wintypes.POINT(x, y))
        if not hit or int(self.user.GetAncestor(hit, 2) or hit) != self.target.handle:
            raise BridgeError("TARGET_OCCLUDED", "다른 창이 클릭 위치를 가리고 있습니다.")
        return self.controller.click()

    def release(self):
        return self.controller.release_all()
