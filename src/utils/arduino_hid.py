"""
WinCro Arduino HID 통신 모듈.

Arduino Leonardo를 통해 하드웨어 레벨의 마우스/키보드 HID 입력을 생성합니다.
"""

import time
import threading
from typing import Optional
import serial

from .logger import get_logger
from .config import get_config

logger = get_logger(__name__)
_KEY_TAP_HOLD_DELAY = 0.055
_KEY_TAP_POST_DELAY = 0.045


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
    """Arduino Leonardo HID 컨트롤러."""

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
        self._supports_mouse_move = False
        self._supports_key_combo_tap = False
        # Serial request/response pairs must never overlap.  Concurrent player
        # transitions otherwise consume each other's OK response and can leave
        # a HID mouse button pressed after a failed drag release.
        self._io_lock = threading.RLock()

    @property
    def is_connected(self) -> bool:
        with self._io_lock:
            return bool(self._connected and self._serial and self._serial.is_open)

    def has_open_session(self) -> bool:
        """Return True while a serial session can still carry a release command."""
        with self._io_lock:
            return bool(self._serial and self._serial.is_open)

    def supports_mouse_move(self) -> bool:
        """True when the connected firmware supports MM relative move commands."""
        with self._io_lock:
            return bool(self._supports_mouse_move)

    def supports_key_combo_tap(self) -> bool:
        """True when the connected firmware supports KQ atomic combo tap commands."""
        with self._io_lock:
            return bool(self._supports_key_combo_tap)

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

        self._io_lock.acquire()
        try:
            # 이전 연결 정리
            self.disconnect()

            if not self._open_serial_session(port, baud_rate):
                self._close_serial_only()
                return False

            if self._initialize_connected_session(port, baud_rate):
                if self._supports_key_combo_tap:
                    logger.info(f"Arduino HID 연결 성공: {port}")
                    return True

                if self._auto_refresh_firmware(port, baud_rate, "KQ unsupported"):
                    return True

                if self.is_connected:
                    logger.warning(
                        "Arduino HID key combo tap remains unsupported; "
                        "continuing with guarded raw fallback"
                    )
                    return True
                return self._reconnect_without_refresh(
                    port,
                    baud_rate,
                    "firmware refresh failed",
                )

            logger.warning("Arduino HID 응답 없음 (펌웨어 확인 필요)")
            self._close_serial_only()
            if self._auto_refresh_firmware(port, baud_rate, "PING failed"):
                return True
            if self.is_connected:
                return True
            return self._reconnect_without_refresh(
                port,
                baud_rate,
                "firmware recovery failed",
            )

        except Exception as e:
            logger.error(f"Arduino HID 연결 실패: {e}")
            self._close_serial_only()
            return False
        finally:
            self._io_lock.release()

    def _open_serial_session(self, port: str, baud_rate: int) -> bool:
        """Open serial and wait for Leonardo reset."""
        self._serial = serial.Serial(
            port=port,
            baudrate=baud_rate,
            timeout=1,
            write_timeout=1,
            dsrdtr=False,
            rtscts=False
        )

        if not self._serial.is_open:
            return False

        self._serial.dtr = False
        time.sleep(0.1)
        self._serial.dtr = True
        time.sleep(2)
        self._serial.reset_input_buffer()
        return True

    def _initialize_connected_session(self, port: str, baud_rate: int) -> bool:
        """Validate firmware and cache supported capabilities."""
        if not self._ping():
            self._connected = False
            return False

        self._connected = True
        self._port = port
        self._baud_rate = baud_rate
        self._supports_mouse_move = self._probe_mouse_move_support()
        self._supports_key_combo_tap = self._probe_key_combo_tap_support()
        return True

    def _reconnect_without_refresh(
        self,
        port: str,
        baud_rate: int,
        reason: str,
    ) -> bool:
        """Recover the existing firmware after an attempted refresh failed."""
        logger.warning(f"Arduino HID reconnecting with existing firmware: {reason}")
        self._close_serial_only()
        try:
            if not self._open_serial_session(port, baud_rate):
                self._close_serial_only()
                return False
            if not self._initialize_connected_session(port, baud_rate):
                self._close_serial_only()
                return False
            if not self._supports_key_combo_tap:
                logger.warning(
                    "Arduino HID reconnected without KQ support; "
                    "guarded raw combo fallback remains active"
                )
            return self.is_connected
        except Exception as exc:
            logger.error(f"Arduino HID fallback reconnect failed: {exc}")
            self._close_serial_only()
            return False

    def _close_serial_only(self) -> None:
        with self._io_lock:
            try:
                if self._serial and self._serial.is_open:
                    self._send_command("KA")
            except Exception as e:
                logger.debug(f"Arduino HID pre-close release ignored: {e}")
            try:
                if self._serial:
                    self._serial.close()
            except Exception as e:
                logger.debug(f"Arduino HID serial close ignored: {e}")
            finally:
                self._serial = None
                self._connected = False
                self._supports_mouse_move = False
                self._supports_key_combo_tap = False

    def _auto_refresh_firmware(self, port: str, baud_rate: int, reason: str) -> bool:
        """Upload bundled firmware once, then reconnect and re-probe capabilities."""
        logger.warning(f"Arduino HID firmware refresh required: {reason}")
        self._close_serial_only()

        try:
            from .arduino_uploader import upload_firmware
            success, message = upload_firmware(port)
        except Exception as e:
            logger.error(f"Arduino HID firmware auto refresh failed: {e}")
            return False

        if not success:
            logger.error(f"Arduino HID firmware auto refresh failed: {message}")
            return False

        logger.info(f"Arduino HID firmware auto refresh complete: {message}")
        time.sleep(2)

        try:
            if not self._open_serial_session(port, baud_rate):
                return False
            if not self._initialize_connected_session(port, baud_rate):
                self._close_serial_only()
                return False
        except Exception as e:
            logger.error(f"Arduino HID reconnect after firmware refresh failed: {e}")
            self._close_serial_only()
            return False

        if not self._supports_key_combo_tap:
            logger.error("Arduino HID firmware refreshed but KQ is still unsupported")
            return False

        logger.info(f"Arduino HID 연결 성공: {port} (firmware refreshed)")
        return True

    def disconnect(self):
        """연결 해제"""
        self._close_serial_only()
        logger.info("Arduino HID 연결 해제")

    def _ping(self) -> bool:
        """연결 확인"""
        try:
            with self._io_lock:
                self._send_command("PING", wait_response=False)
                response = self._read_response()
            return response == "PONG"
        except (OSError, serial.SerialException, UnicodeDecodeError) as e:
            logger.debug(f"PING 실패: {e}")
            return False

    def _send_command(self, cmd: str, wait_response: bool = True) -> bool:
        """명령 전송 및 응답 대기"""
        with self._io_lock:
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

    def _probe_mouse_move_support(self) -> bool:
        """Probe MM support without moving the pointer."""
        try:
            supported = self._send_command("MM,0,0")
            if supported:
                logger.info("Arduino HID 마우스 이동 지원 확인")
            else:
                logger.warning("Arduino HID 마우스 이동 미지원 또는 구형 펌웨어")
            return supported
        except Exception as e:
            logger.warning(f"Arduino HID 마우스 이동 지원 확인 실패: {e}")
            return False

    def _probe_key_combo_tap_support(self) -> bool:
        """Probe firmware-side instant combo tap support without sending keys."""
        try:
            supported = self._send_command("KQ")
            if supported:
                logger.info("Arduino HID key combo tap support confirmed")
            else:
                logger.warning("Arduino HID key combo tap unsupported; using raw KP/KR fallback")
            return supported
        except Exception as e:
            logger.warning(f"Arduino HID key combo tap support probe failed: {e}")
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

    def mouse_move(self, dx: int, dy: int) -> bool:
        """마우스 상대 이동."""
        if not self.supports_mouse_move():
            return False
        return self._send_command(f"MM,{int(dx)},{int(dy)}")

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
        first = self._send_command(f"MC,{btn}")
        time.sleep(0.08)
        second = self._send_command(f"MC,{btn}")
        time.sleep(0.05)
        return first and second

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
        release_needed = False
        try:
            release_needed = True
            if not self.key_press(key):
                return False
            time.sleep(_KEY_TAP_HOLD_DELAY)
            released = bool(self.key_release(key))
            if released:
                release_needed = False
            time.sleep(_KEY_TAP_POST_DELAY)
            return released
        finally:
            if release_needed:
                self.release_all()

    def key_combo_tap(self, *keys: str) -> bool:
        """Press modifiers, tap final key, and release modifiers with minimum host-side delay."""
        normalized = [str(key).strip().lower() for key in keys if str(key).strip()]
        if not normalized:
            return True
        if len(normalized) == 1:
            return self.key_tap(normalized[0])

        keycodes = [self._get_keycode(key) for key in normalized]
        if any(not code for code in keycodes):
            return False

        if self._supports_key_combo_tap:
            sent = self._send_command("KQ," + ",".join(str(code) for code in keycodes))
            if not sent:
                self.release_all()
            return sent

        logger.warning("Arduino HID KQ unsupported; using guarded KP/KR combo tap fallback")
        ok = False
        try:
            if not self.release_all():
                return False
            time.sleep(0.005)
            for key in normalized[:-1]:
                if not self.key_press(key):
                    return False
            time.sleep(0.016)
            if not self.key_press(normalized[-1]):
                return False
            time.sleep(0.006)
            ok = bool(self.key_release(normalized[-1]))
            time.sleep(0.002)
        finally:
            released = self.release_all()
            ok = bool(ok and released)
            time.sleep(0.005)
        return ok

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
        pressed_keys = []
        unreleased_keys = set()
        ok = True
        try:
            for key in keys:
                if not self.key_press(key):
                    ok = False
                    break
                pressed_keys.append(key)
                unreleased_keys.add(key)
                time.sleep(0.02)

            if ok:
                time.sleep(0.05)

            for key in reversed(tuple(pressed_keys)):
                if not self.key_release(key):
                    ok = False
                else:
                    unreleased_keys.discard(key)
                time.sleep(0.02)
            return ok
        finally:
            if unreleased_keys or not ok:
                self.release_all()

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
