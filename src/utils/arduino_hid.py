"""
WinCro Arduino HID 통신 모듈

Arduino Leonardo를 통해 하드웨어 레벨의 마우스 클릭/키보드 입력을 생성합니다.
마우스 이동은 소프트웨어(pyautogui)로 처리하고, 클릭/키보드만 Arduino HID 사용.
"""

import time
import threading
from typing import Optional
import serial

from .logger import get_logger
from .config import get_config

logger = get_logger(__name__)


# 키보드 키코드 매핑 (Arduino Keyboard 라이브러리 호환)
KEY_CODES = {
    # 특수 키
    'ctrl': 128, 'control': 128,
    'shift': 129,
    'alt': 130,
    'win': 131, 'windows': 131, 'gui': 131,
    'enter': 176, 'return': 176,
    'esc': 177, 'escape': 177,
    'backspace': 178,
    'tab': 179,
    'space': 32, ' ': 32,
    'capslock': 193,
    'f1': 194, 'f2': 195, 'f3': 196, 'f4': 197,
    'f5': 198, 'f6': 199, 'f7': 200, 'f8': 201,
    'f9': 202, 'f10': 203, 'f11': 204, 'f12': 205,
    'printscreen': 206,
    'scrolllock': 207,
    'pause': 208,
    'insert': 209,
    'home': 210,
    'pageup': 211,
    'delete': 212,
    'end': 213,
    'pagedown': 214,
    'right': 215, 'rightarrow': 215,
    'left': 216, 'leftarrow': 216,
    'down': 217, 'downarrow': 217,
    'up': 218, 'uparrow': 218,
}


class ArduinoHID:
    """Arduino Leonardo HID 컨트롤러 (클릭/키보드 전용)"""

    _instance: Optional['ArduinoHID'] = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, '_initialized'):
            return
        self._initialized = True

        self._serial: Optional[serial.Serial] = None
        self._connected = False
        self._port = ""
        self._baud_rate = 115200

    @property
    def is_connected(self) -> bool:
        return self._connected and self._serial and self._serial.is_open

    def connect(self, port: str = None, baud_rate: int = None) -> bool:
        """아두이노에 연결"""
        config = get_config()

        if port is None:
            port = config.arduino.com_port
        if baud_rate is None:
            baud_rate = config.arduino.baud_rate

        if not port:
            logger.error("COM 포트가 설정되지 않음")
            return False

        try:
            # 이전 연결 정리
            self.disconnect()

            self._serial = serial.Serial(
                port=port,
                baudrate=baud_rate,
                timeout=1,
                write_timeout=1,
                dsrdtr=False,
                rtscts=False
            )

            if self._serial.is_open:
                # Leonardo 리셋
                self._serial.dtr = False
                time.sleep(0.1)
                self._serial.dtr = True
                time.sleep(2)  # 부트로더 대기

                self._serial.reset_input_buffer()

                # PING 테스트
                if self._ping():
                    self._connected = True
                    self._port = port
                    self._baud_rate = baud_rate
                    logger.info(f"Arduino HID 연결 성공: {port}")
                    return True
                else:
                    logger.warning("Arduino HID 응답 없음 (펌웨어 확인 필요)")
                    self._connected = False
                    if self._serial:
                        self._serial.close()
                    self._serial = None
                    return False

        except Exception as e:
            logger.error(f"Arduino HID 연결 실패: {e}")
            self._connected = False
            return False

    def disconnect(self):
        """연결 해제"""
        try:
            if self._serial:
                self._serial.close()
        except (OSError, serial.SerialException) as e:
            logger.debug(f"연결 해제 중 오류 (무시): {e}")
        self._serial = None
        self._connected = False
        logger.info("Arduino HID 연결 해제")

    def _ping(self) -> bool:
        """연결 확인"""
        try:
            self._send_command("PING", wait_response=False)
            response = self._read_response()
            return response == "PONG"
        except (OSError, serial.SerialException, UnicodeDecodeError) as e:
            logger.debug(f"PING 실패: {e}")
            return False

    def _send_command(self, cmd: str, wait_response: bool = True) -> bool:
        """명령 전송 및 응답 대기"""
        if not self._serial or not self._serial.is_open:
            return False

        try:
            self._serial.reset_input_buffer()
            self._serial.write(f"{cmd}\n".encode())
            self._serial.flush()

            if wait_response:
                response = self._read_response(timeout=1.0)
                if response == "OK":
                    return True
                elif response == "ERR:UNKNOWN_CMD":
                    logger.warning(f"알 수 없는 명령: {cmd}")
                    return False
                elif response == "":
                    logger.warning(f"명령 응답 없음: {cmd}")
                    return False
                else:
                    logger.warning(f"예상치 못한 응답: {response} (명령: {cmd})")
                    return False

            return True
        except Exception as e:
            logger.error(f"명령 전송 실패: {e}")
            return False

    def _read_response(self, timeout: float = 1.0) -> str:
        """응답 읽기"""
        if not self._serial:
            return ""

        try:
            self._serial.timeout = timeout
            response = self._serial.readline().decode().strip()
            return response
        except (OSError, serial.SerialException, UnicodeDecodeError) as e:
            logger.debug(f"응답 읽기 실패: {e}")
            return ""

    # ==================== 마우스 클릭 (하드웨어) ====================

    def mouse_click(self, button: str = 'left') -> bool:
        """마우스 클릭"""
        btn = 'L' if button.lower() == 'left' else ('R' if button.lower() == 'right' else 'M')
        result = self._send_command(f"MC,{btn}")
        time.sleep(0.05)
        return result

    def mouse_press(self, button: str = 'left') -> bool:
        """마우스 버튼 누르기"""
        btn = 'L' if button.lower() == 'left' else ('R' if button.lower() == 'right' else 'M')
        return self._send_command(f"MP,{btn}")

    def mouse_release(self, button: str = 'left') -> bool:
        """마우스 버튼 떼기"""
        btn = 'L' if button.lower() == 'left' else ('R' if button.lower() == 'right' else 'M')
        return self._send_command(f"MR,{btn}")

    def mouse_double_click(self, button: str = 'left') -> bool:
        """마우스 더블 클릭"""
        btn = 'L' if button.lower() == 'left' else ('R' if button.lower() == 'right' else 'M')
        self._send_command(f"MC,{btn}")
        time.sleep(0.08)
        self._send_command(f"MC,{btn}")
        time.sleep(0.05)
        return True

    def mouse_scroll(self, amount: int) -> bool:
        """마우스 스크롤 (양수: 위, 음수: 아래)"""
        return self._send_command(f"MS,{amount}")

    # ==================== 키보드 (하드웨어) ====================

    def _get_keycode(self, key: str) -> int:
        """키 이름을 키코드로 변환"""
        key_lower = key.lower()
        if key_lower in KEY_CODES:
            return KEY_CODES[key_lower]
        elif len(key) == 1:
            return ord(key)
        else:
            logger.warning(f"알 수 없는 키: {key}")
            return 0

    def key_press(self, key: str) -> bool:
        """키 누르기"""
        keycode = self._get_keycode(key)
        if keycode:
            return self._send_command(f"KP,{keycode}")
        return False

    def key_release(self, key: str) -> bool:
        """키 떼기"""
        keycode = self._get_keycode(key)
        if keycode:
            return self._send_command(f"KR,{keycode}")
        return False

    def key_tap(self, key: str) -> bool:
        """키 한번 누르고 떼기"""
        self.key_press(key)
        time.sleep(0.05)
        return self.key_release(key)

    def set_typing_delay(self, delay_ms: int) -> bool:
        """Arduino 타이핑 딜레이 설정 (0~200ms)"""
        delay_ms = max(0, min(200, delay_ms))
        return self._send_command(f"KD,{delay_ms}")

    def type_text(self, text: str, interval: float = None) -> bool:
        """텍스트 타이핑 (아두이노는 1.5배 느리게)"""
        if interval is None:
            config = get_config()
            interval = config.player.typing_interval

        # 아두이노 HID는 1.5배 느리게 (안정성 향상)
        interval = interval * 1.5

        # Arduino에 타이핑 딜레이 설정 (초 -> ms 변환)
        delay_ms = int(interval * 1000)
        self.set_typing_delay(delay_ms)

        if len(text) <= 32:
            return self._send_command(f"KT,{text}")
        else:
            for i in range(0, len(text), 32):
                chunk = text[i:i+32]
                self._send_command(f"KT,{chunk}")
                time.sleep(0.08)  # 청크 사이 대기 (0.05 -> 0.08)
            return True

    def hotkey(self, *keys) -> bool:
        """핫키 조합 (예: hotkey('ctrl', 'c'))"""
        for key in keys:
            self.key_press(key)
            time.sleep(0.02)

        time.sleep(0.05)

        for key in reversed(keys):
            self.key_release(key)
            time.sleep(0.02)

        return True

    def release_all(self) -> bool:
        """모든 키/버튼 떼기"""
        return self._send_command("KA")


# 전역 인스턴스
_arduino_hid: Optional[ArduinoHID] = None


def get_arduino_hid() -> ArduinoHID:
    """Arduino HID 인스턴스 반환"""
    global _arduino_hid
    if _arduino_hid is None:
        _arduino_hid = ArduinoHID()
    return _arduino_hid
