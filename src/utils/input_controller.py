"""
WinCro 입력 컨트롤러

pyautogui와 Arduino HID를 추상화하여 설정에 따라 적절한 입력 방식을 사용합니다.
"""

import time
import ctypes
import threading
from typing import Optional, List, Tuple
import pyautogui

from .logger import get_logger
from .config import get_config

logger = get_logger(__name__)


def _release_mouse_capture():
    """마우스 캡처/클리핑 해제 (다른 프로그램이 마우스를 잡고 있을 때 필요)"""
    try:
        ctypes.windll.user32.ReleaseCapture()
        ctypes.windll.user32.ClipCursor(None)
    except (OSError, AttributeError):
        pass

# 전역 Arduino HID 인스턴스 (지연 로딩)
_arduino_hid = None
_arduino_load_failed = False  # 로드 실패 플래그
_arduino_lock = threading.Lock()  # 스레드 안전성


def _get_arduino():
    """Arduino HID 인스턴스 반환 (지연 로딩, 재시도 지원, 스레드 안전)"""
    global _arduino_hid, _arduino_load_failed

    with _arduino_lock:
        # 이미 연결되어 있으면 반환
        if _arduino_hid is not None:
            return _arduino_hid

        # 이전에 모듈 로드 자체가 실패했으면 None 반환 (ImportError는 재시도 의미 없음)
        if _arduino_load_failed:
            return None

        try:
            from .arduino_hid import get_arduino_hid
            _arduino_hid = get_arduino_hid()
        except ImportError:
            logger.warning("Arduino HID 모듈을 불러올 수 없습니다")
            _arduino_load_failed = True
            _arduino_hid = None
        except Exception as e:
            # 연결 실패는 다음에 재시도 가능
            logger.debug(f"Arduino HID 초기화 실패 (재시도 가능): {e}")
            _arduino_hid = None

        return _arduino_hid


def reset_arduino_connection():
    """Arduino 연결 상태 초기화 (재연결 시도용, 스레드 안전)"""
    global _arduino_hid, _arduino_load_failed
    with _arduino_lock:
        _arduino_hid = None
        # ImportError가 아닌 경우에만 재시도 허용
        # _arduino_load_failed는 그대로 유지


def is_arduino_enabled() -> bool:
    """Arduino 입력이 활성화되어 있는지 확인"""
    config = get_config()
    if not config.arduino.enabled:
        return False

    arduino = _get_arduino()
    return arduino is not None and arduino.is_connected


class InputController:
    """
    통합 입력 컨트롤러

    Arduino가 활성화되면 Arduino HID를 사용하고,
    그렇지 않으면 pyautogui를 사용합니다.
    """

    def __init__(self):
        self._config = get_config()

    def _use_arduino(self) -> bool:
        """Arduino 사용 여부"""
        return is_arduino_enabled()

    # ==================== 마우스 동작 ====================
    # 마우스 이동: 소프트웨어 (pyautogui)
    # 마우스 클릭/버튼: 하드웨어 (Arduino)

    def move_to(self, x: int, y: int, duration: float = 0.0) -> None:
        """마우스를 절대 좌표로 이동 (항상 소프트웨어)"""
        _release_mouse_capture()
        pyautogui.moveTo(x, y, duration=duration)

    def move(self, dx: int, dy: int) -> None:
        """마우스를 상대적으로 이동 (항상 소프트웨어)"""
        pyautogui.move(dx, dy)

    def click(self, x: Optional[int] = None, y: Optional[int] = None,
              button: str = 'left', duration: float = 0.0) -> None:
        """마우스 클릭 (이동: 소프트웨어, 클릭: 하드웨어)"""
        _release_mouse_capture()

        # 이동은 항상 소프트웨어
        if x is not None and y is not None:
            pyautogui.moveTo(x, y, duration=duration)
            time.sleep(0.05)

        # 클릭은 Arduino 사용 시 하드웨어
        if self._use_arduino():
            arduino = _get_arduino()
            if arduino is not None:
                arduino.mouse_click(button)
            else:
                pyautogui.click(button=button)
        else:
            pyautogui.click(button=button)

    def double_click(self, x: Optional[int] = None, y: Optional[int] = None,
                     duration: float = 0.0) -> None:
        """마우스 더블 클릭 (이동: 소프트웨어, 클릭: 하드웨어)"""
        _release_mouse_capture()

        # 이동은 항상 소프트웨어
        if x is not None and y is not None:
            pyautogui.moveTo(x, y, duration=duration)
            time.sleep(0.05)

        # 더블클릭은 Arduino 사용 시 하드웨어
        if self._use_arduino():
            arduino = _get_arduino()
            if arduino is not None:
                arduino.mouse_double_click()
            else:
                pyautogui.doubleClick()
        else:
            pyautogui.doubleClick()

    def right_click(self, x: Optional[int] = None, y: Optional[int] = None,
                    duration: float = 0.0) -> None:
        """마우스 우클릭 (이동: 소프트웨어, 클릭: 하드웨어)"""
        _release_mouse_capture()

        # 이동은 항상 소프트웨어
        if x is not None and y is not None:
            pyautogui.moveTo(x, y, duration=duration)
            time.sleep(0.05)

        # 우클릭은 Arduino 사용 시 하드웨어
        if self._use_arduino():
            arduino = _get_arduino()
            if arduino is not None:
                arduino.mouse_click('right')
            else:
                pyautogui.rightClick()
        else:
            pyautogui.rightClick()

    def mouse_down(self, button: str = 'left') -> None:
        """마우스 버튼 누르기 (하드웨어)"""
        if self._use_arduino():
            arduino = _get_arduino()
            if arduino is not None:
                arduino.mouse_press(button)
            else:
                pyautogui.mouseDown(button=button)
        else:
            pyautogui.mouseDown(button=button)

    def mouse_up(self, button: str = 'left') -> None:
        """마우스 버튼 떼기 (하드웨어)"""
        if self._use_arduino():
            arduino = _get_arduino()
            if arduino is not None:
                arduino.mouse_release(button)
            else:
                pyautogui.mouseUp(button=button)
        else:
            pyautogui.mouseUp(button=button)

    def drag(self, start_x: int, start_y: int, end_x: int, end_y: int,
             duration: float = 0.5, button: str = 'left') -> None:
        """마우스 드래그 (이동: 소프트웨어, 버튼: 하드웨어)"""
        _release_mouse_capture()

        # 시작 위치로 이동 (소프트웨어)
        pyautogui.moveTo(start_x, start_y)
        time.sleep(0.05)

        if self._use_arduino():
            arduino = _get_arduino()
            if arduino is not None:
                # 버튼 누르기 (하드웨어)
                arduino.mouse_press(button)
                time.sleep(0.05)
                # 끝 위치로 이동 (소프트웨어)
                pyautogui.moveTo(end_x, end_y, duration=duration)
                time.sleep(0.05)
                # 버튼 떼기 (하드웨어)
                arduino.mouse_release(button)
            else:
                dx = end_x - start_x
                dy = end_y - start_y
                pyautogui.drag(dx, dy, duration=duration, button=button)
        else:
            dx = end_x - start_x
            dy = end_y - start_y
            pyautogui.drag(dx, dy, duration=duration, button=button)

    def scroll(self, amount: int, x: Optional[int] = None, y: Optional[int] = None) -> None:
        """마우스 스크롤 (이동: 소프트웨어, 스크롤: 하드웨어)"""
        _release_mouse_capture()

        # 이동은 항상 소프트웨어
        if x is not None and y is not None:
            pyautogui.moveTo(x, y)
            time.sleep(0.05)

        # 스크롤은 Arduino 사용 시 하드웨어
        if self._use_arduino():
            arduino = _get_arduino()
            if arduino is not None:
                arduino.mouse_scroll(amount)
            else:
                pyautogui.scroll(amount)
        else:
            pyautogui.scroll(amount)

    # ==================== 키보드 동작 ====================

    def press(self, key: str) -> None:
        """키 누르고 떼기"""
        if self._use_arduino():
            arduino = _get_arduino()
            if arduino is not None:
                arduino.key_tap(key)
            else:
                pyautogui.press(key)
        else:
            pyautogui.press(key)

    def key_down(self, key: str) -> None:
        """키 누르기"""
        if self._use_arduino():
            arduino = _get_arduino()
            if arduino is not None:
                arduino.key_press(key)
            else:
                pyautogui.keyDown(key)
        else:
            pyautogui.keyDown(key)

    def key_up(self, key: str) -> None:
        """키 떼기"""
        if self._use_arduino():
            arduino = _get_arduino()
            if arduino is not None:
                arduino.key_release(key)
            else:
                pyautogui.keyUp(key)
        else:
            pyautogui.keyUp(key)

    def hotkey(self, *keys) -> None:
        """단축키 조합"""
        if self._use_arduino():
            arduino = _get_arduino()
            if arduino is not None:
                arduino.hotkey(*keys)
            else:
                pyautogui.hotkey(*keys)
        else:
            pyautogui.hotkey(*keys)

    def type_text(self, text: str, interval: float = 0.0) -> None:
        """텍스트 입력 (ASCII만)"""
        if self._use_arduino():
            arduino = _get_arduino()
            if arduino is not None:
                arduino.type_text(text, interval)
            else:
                pyautogui.write(text, interval=interval)
        else:
            pyautogui.write(text, interval=interval)

    def release_all(self) -> None:
        """모든 키/버튼 떼기"""
        if self._use_arduino():
            arduino = _get_arduino()
            if arduino is not None:
                arduino.release_all()
        # pyautogui에는 release_all이 없음


# 전역 인스턴스
_input_controller: Optional[InputController] = None


def get_input_controller() -> InputController:
    """입력 컨트롤러 인스턴스 반환"""
    global _input_controller
    if _input_controller is None:
        _input_controller = InputController()
    return _input_controller
