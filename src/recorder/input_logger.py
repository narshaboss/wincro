"""
WinCro 입력 이벤트 로깅 모듈

pynput을 사용하여 마우스/키보드 입력을 기록합니다.

핵심 원칙:
- 키보드 훅은 하나만 사용 (keyboard.Listener)
- F7/F8은 keyboard.Listener에서 처리
- 단순한 구조 유지
"""

import json
import time
import threading
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any, Callable
from dataclasses import dataclass, field, asdict
from enum import Enum

from pynput import mouse, keyboard

from ..utils.logger import get_logger
from ..utils.config import DATA_DIR, get_config

logger = get_logger(__name__)


class InputEventType(Enum):
    MOUSE_MOVE = "mouse_move"
    MOUSE_CLICK = "mouse_click"
    MOUSE_SCROLL = "mouse_scroll"
    MOUSE_DRAG_START = "mouse_drag_start"
    MOUSE_DRAG_END = "mouse_drag_end"
    KEY_PRESS = "key_press"
    KEY_RELEASE = "key_release"


@dataclass
class InputEvent:
    event_type: str
    timestamp: float
    x: Optional[int] = None
    y: Optional[int] = None
    button: Optional[str] = None
    pressed: Optional[bool] = None
    key: Optional[str] = None
    scroll_dx: Optional[int] = None
    scroll_dy: Optional[int] = None
    modifiers: List[str] = field(default_factory=list)
    drag_path: Optional[List[Dict[str, Any]]] = None
    click_image: Optional[str] = None  # 클릭 시 캡처된 이미지 경로
    drag_duration: Optional[float] = None  # 드래그 소요 시간 (초)

    def to_dict(self) -> Dict[str, Any]:
        result = {k: v for k, v in asdict(self).items() if v is not None}
        if 'drag_path' in result and not result['drag_path']:
            del result['drag_path']
        if 'modifiers' in result and not result['modifiers']:
            del result['modifiers']
        return result


class InputLogger:
    """입력 이벤트 로거"""

    def __init__(self):
        self._config = get_config()
        self._logging = False
        self._events: List[InputEvent] = []
        self._start_time: Optional[float] = None
        self._output_path: Optional[Path] = None

        # 리스너
        self._mouse_listener: Optional[mouse.Listener] = None
        self._keyboard_listener: Optional[keyboard.Listener] = None

        # 상태
        self._pressed_keys: set = set()
        self._pressed_keys_lock = threading.Lock()
        self._events_lock = threading.Lock()
        self._mouse_pressed = False
        self._drag_start_pos: Optional[tuple] = None
        self._drag_start_time: Optional[float] = None
        self._drag_button: Optional[str] = None
        self._last_mouse_pos: Optional[tuple] = None

        # 샘플링
        self._last_move_time: float = 0
        self._move_sample_interval: float = 0.05

        # 콜백
        self._on_f7_pressed: Optional[Callable[[], None]] = None
        self._on_f8_pressed: Optional[Callable[[], None]] = None
        self._on_click_capture: Optional[Callable[[int, int, float], Optional[str]]] = None  # (x, y, timestamp) -> image_path

    @property
    def is_logging(self) -> bool:
        return self._logging

    @property
    def event_count(self) -> int:
        with self._events_lock:
            return len(self._events)

    @property
    def events(self) -> List[InputEvent]:
        with self._events_lock:
            return self._events.copy()

    def set_f7_callback(self, callback: Optional[Callable[[], None]]) -> None:
        self._on_f7_pressed = callback

    def set_f8_callback(self, callback: Optional[Callable[[], None]]) -> None:
        self._on_f8_pressed = callback

    def set_click_capture_callback(self, callback: Optional[Callable[[int, int, float], Optional[str]]]) -> None:
        """클릭 시 스크린샷 캡처 콜백 설정 (x, y, timestamp) -> image_path"""
        self._on_click_capture = callback

    def start(self, output_name: Optional[str] = None, start_time: Optional[float] = None) -> bool:
        """입력 로깅 시작"""
        if self._logging:
            return False

        try:
            if output_name is None:
                output_name = f"input_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

            recordings_dir = DATA_DIR / "recordings"
            recordings_dir.mkdir(parents=True, exist_ok=True)
            self._output_path = recordings_dir / f"{output_name}.json"

            # 상태 초기화
            with self._events_lock:
                self._events.clear()
            with self._pressed_keys_lock:
                self._pressed_keys.clear()
            self._mouse_pressed = False
            self._drag_start_pos = None
            self._drag_start_time = None
            self._drag_button = None
            self._last_mouse_pos = None
            self._start_time = start_time if start_time else time.time()

            # 마우스 리스너 (직접 시작)
            self._mouse_listener = mouse.Listener(
                on_move=self._on_mouse_move,
                on_click=self._on_mouse_click,
                on_scroll=self._on_mouse_scroll,
            )
            self._mouse_listener.start()

            # 키보드 리스너 (직접 시작 - 딜레이 최소화)
            time.sleep(0.1)
            self._keyboard_listener = keyboard.Listener(
                on_press=self._on_key_press,
                on_release=self._on_key_release,
            )
            self._keyboard_listener.start()

            self._logging = True
            logger.info(f"입력 로깅 시작: {self._output_path}")
            return True

        except Exception as e:
            logger.error(f"입력 로깅 시작 실패: {e}")
            self._cleanup()
            return False

    def stop(self) -> Optional[str]:
        """입력 로깅 중지"""
        if not self._logging:
            return None

        self._logging = False

        # 리스너 중지
        if self._mouse_listener:
            try:
                self._mouse_listener.stop()
            except Exception as e:
                logger.debug(f"마우스 리스너 중지 중 오류: {e}")
            self._mouse_listener = None

        if self._keyboard_listener:
            try:
                self._keyboard_listener.stop()
            except Exception as e:
                logger.debug(f"키보드 리스너 중지 중 오류: {e}")
            self._keyboard_listener = None

        output_path = self._save_log()
        logger.info(f"입력 로깅 중지: {output_path}")
        return output_path

    def _cleanup(self) -> None:
        """리소스 정리 - 리스너 중지 후 참조 해제"""
        if self._mouse_listener:
            try:
                self._mouse_listener.stop()
            except Exception:
                pass
        if self._keyboard_listener:
            try:
                self._keyboard_listener.stop()
            except Exception:
                pass
        self._mouse_listener = None
        self._keyboard_listener = None
        self._logging = False

    def _get_timestamp(self) -> float:
        if self._start_time is None:
            return 0.0
        return time.time() - self._start_time

    def _get_modifiers(self) -> List[str]:
        modifiers = []
        mod_map = {
            'ctrl': ['Key.ctrl_l', 'Key.ctrl_r', 'Key.ctrl'],
            'alt': ['Key.alt_l', 'Key.alt_r', 'Key.alt'],
            'shift': ['Key.shift_l', 'Key.shift_r', 'Key.shift'],
        }
        with self._pressed_keys_lock:
            for mod, keys in mod_map.items():
                if any(k in self._pressed_keys for k in keys):
                    modifiers.append(mod)
        return modifiers

    def _add_event(self, event: InputEvent) -> None:
        with self._events_lock:
            self._events.append(event)

    # === 마우스 이벤트 ===

    def _on_mouse_move(self, x: int, y: int) -> None:
        if not self._logging:
            return

        self._last_mouse_pos = (x, y)
        current_time = time.time()

        if current_time - self._last_move_time < self._move_sample_interval:
            if not self._mouse_pressed:
                return

        self._last_move_time = current_time
        self._add_event(InputEvent(
            event_type=InputEventType.MOUSE_MOVE.value,
            timestamp=self._get_timestamp(),
            x=x, y=y,
        ))

    def _on_mouse_click(self, x: int, y: int, button: mouse.Button, pressed: bool) -> None:
        if not self._logging:
            return

        button_name = button.name if hasattr(button, 'name') else str(button)

        if pressed:
            self._mouse_pressed = True
            self._drag_start_pos = (x, y)
            self._drag_start_time = self._get_timestamp()
            self._drag_button = button_name
            self._last_mouse_pos = (x, y)
        else:
            self._handle_release(x, y, button_name)

    def _handle_release(self, x: int, y: int, button_name: str) -> None:
        if not self._mouse_pressed:
            return

        self._mouse_pressed = False

        is_drag = False
        if self._drag_start_pos and self._drag_start_time is not None:
            dx = abs(self._drag_start_pos[0] - x)
            dy = abs(self._drag_start_pos[1] - y)
            hold_time = self._get_timestamp() - self._drag_start_time

            threshold_dist = getattr(self._config.recording, 'drag_threshold_distance', 10)
            threshold_time = getattr(self._config.recording, 'drag_threshold_time', 0.1)
            is_drag = (dx >= threshold_dist or dy >= threshold_dist) and hold_time >= threshold_time

        if is_drag:
            # 드래그 소요 시간 계산
            drag_duration = hold_time  # 이미 계산됨: self._get_timestamp() - self._drag_start_time

            self._add_event(InputEvent(
                event_type=InputEventType.MOUSE_DRAG_START.value,
                timestamp=self._drag_start_time,
                x=self._drag_start_pos[0], y=self._drag_start_pos[1],
                button=button_name,
                drag_duration=drag_duration,  # 시작 이벤트에도 기록
            ))
            self._add_event(InputEvent(
                event_type=InputEventType.MOUSE_DRAG_END.value,
                timestamp=self._get_timestamp(),
                x=x, y=y,
                button=button_name,
                drag_duration=drag_duration,  # 종료 이벤트에도 기록
            ))
        else:
            # 클릭 시 스크린샷 자동 캡처
            click_timestamp = self._drag_start_time or self._get_timestamp()
            click_x = self._drag_start_pos[0] if self._drag_start_pos else x
            click_y = self._drag_start_pos[1] if self._drag_start_pos else y
            click_image_path = None

            if self._on_click_capture:
                try:
                    logger.info(f"클릭 감지: ({click_x}, {click_y})")
                    click_image_path = self._on_click_capture(click_x, click_y, click_timestamp)
                except Exception as e:
                    logger.warning(f"클릭 스크린샷 캡처 실패: {e}")
            else:
                logger.warning("클릭 캡처 콜백 미설정")

            if self._drag_start_pos:
                self._add_event(InputEvent(
                    event_type=InputEventType.MOUSE_CLICK.value,
                    timestamp=click_timestamp,
                    x=click_x, y=click_y,
                    button=button_name, pressed=True,
                    click_image=click_image_path,
                ))
            self._add_event(InputEvent(
                event_type=InputEventType.MOUSE_CLICK.value,
                timestamp=self._get_timestamp(),
                x=x, y=y,
                button=button_name, pressed=False,
            ))

        self._drag_start_pos = None
        self._drag_start_time = None
        self._drag_button = None

    def _on_mouse_scroll(self, x: int, y: int, dx: int, dy: int) -> None:
        if not self._logging:
            return

        self._add_event(InputEvent(
            event_type=InputEventType.MOUSE_SCROLL.value,
            timestamp=self._get_timestamp(),
            x=x, y=y,
            scroll_dx=dx, scroll_dy=dy,
        ))

    # === 키보드 이벤트 ===

    def _key_to_string(self, key) -> str:
        try:
            if hasattr(key, 'char') and key.char:
                return key.char
            return str(key)
        except (AttributeError, TypeError):
            return str(key)

    def _on_key_press(self, key) -> None:
        if not self._logging:
            return

        # F7: 녹화 중지
        if key == keyboard.Key.f7:
            logger.info("F7 감지 (InputLogger)")
            if self._on_f7_pressed:
                self._on_f7_pressed()
            return

        # F8: 스크린샷
        if key == keyboard.Key.f8:
            if self._on_f8_pressed:
                self._on_f8_pressed()
            return

        key_str = self._key_to_string(key)

        # 키 반복(key repeat) 이벤트 무시 - 이미 눌린 키는 다시 기록하지 않음
        with self._pressed_keys_lock:
            if key_str in self._pressed_keys:
                return
            self._pressed_keys.add(key_str)

        self._add_event(InputEvent(
            event_type=InputEventType.KEY_PRESS.value,
            timestamp=self._get_timestamp(),
            key=key_str,
            modifiers=self._get_modifiers(),
        ))

    def _on_key_release(self, key) -> None:
        if not self._logging:
            return

        if key in (keyboard.Key.f7, keyboard.Key.f8):
            return

        key_str = self._key_to_string(key)
        with self._pressed_keys_lock:
            self._pressed_keys.discard(key_str)

        self._add_event(InputEvent(
            event_type=InputEventType.KEY_RELEASE.value,
            timestamp=self._get_timestamp(),
            key=key_str,
        ))

    def _save_log(self) -> Optional[str]:
        if not self._output_path:
            return None

        try:
            with self._events_lock:
                events_snapshot = list(self._events)
            log_data = {
                "version": "1.0",
                "created_at": datetime.now().isoformat(),
                "duration_seconds": self._get_timestamp(),
                "event_count": len(events_snapshot),
                "events": [e.to_dict() for e in events_snapshot],
            }
            with open(self._output_path, 'w', encoding='utf-8') as f:
                json.dump(log_data, f, ensure_ascii=False, indent=2)
            return str(self._output_path)
        except Exception as e:
            logger.error(f"로그 저장 실패: {e}")
            return None

    def load_log(self, path: str) -> List[InputEvent]:
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return [
                InputEvent(
                    event_type=e.get("event_type", ""),
                    timestamp=e.get("timestamp", 0),
                    x=e.get("x"), y=e.get("y"),
                    button=e.get("button"),
                    pressed=e.get("pressed"),
                    key=e.get("key"),
                    scroll_dx=e.get("scroll_dx"),
                    scroll_dy=e.get("scroll_dy"),
                    modifiers=e.get("modifiers", []),
                    drag_path=e.get("drag_path"),
                    click_image=e.get("click_image"),
                    drag_duration=e.get("drag_duration"),
                )
                for e in data.get("events", [])
            ]
        except Exception as e:
            logger.error(f"로그 로드 실패: {e}")
            return []


class RecordingSession:
    """녹화 세션 - 화면 녹화 + 입력 로깅"""

    def __init__(self):
        from .screen_recorder import ScreenRecorder
        self._screen_recorder = ScreenRecorder()
        self._input_logger = InputLogger()
        self._session_name: Optional[str] = None
        self._recording = False
        self._trigger_images: List[str] = []

        # 콜백
        self._on_f7_callback: Optional[Callable[[], None]] = None
        self._on_trigger_capture: Optional[Callable[[str], None]] = None

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def elapsed_time(self) -> float:
        return self._screen_recorder.elapsed_time

    @property
    def frame_count(self) -> int:
        return self._screen_recorder.frame_count

    @property
    def event_count(self) -> int:
        return self._input_logger.event_count

    @property
    def capture_engine(self) -> str:
        return self._screen_recorder.capture_engine

    @property
    def trigger_images(self) -> List[str]:
        return self._trigger_images.copy()

    def set_f7_callback(self, callback: Optional[Callable[[], None]]) -> None:
        self._on_f7_callback = callback

    def set_trigger_capture_callback(self, callback: Optional[Callable[[str], None]]) -> None:
        self._on_trigger_capture = callback

    def start(self, session_name: Optional[str] = None, region=None) -> bool:
        if self._recording:
            return False

        if session_name is None:
            session_name = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        self._session_name = session_name
        self._trigger_images.clear()

        # 화면 녹화 시작
        if not self._screen_recorder.start(region=region, output_name=session_name):
            return False

        # 입력 로깅 콜백 설정 및 시작
        self._input_logger.set_f7_callback(self._on_f7_callback)
        self._input_logger.set_f8_callback(self._handle_f8)
        self._input_logger.set_click_capture_callback(self._handle_click_capture)
        logger.info("클릭 캡처 콜백 설정 완료")

        if not self._input_logger.start(
            output_name=f"{session_name}_input",
            start_time=self._screen_recorder.start_time or time.time()
        ):
            self._screen_recorder.stop()
            return False

        self._recording = True
        logger.info(f"녹화 세션 시작: {session_name}")
        return True

    def stop(self) -> Dict[str, Optional[str]]:
        if not self._recording:
            return {"video": None, "input_log": None}

        self._recording = False
        video_path = self._screen_recorder.stop()
        input_log_path = self._input_logger.stop()

        logger.info(f"녹화 세션 종료: {self._session_name}")
        return {"video": video_path, "input_log": input_log_path}

    def pause(self) -> bool:
        if not self._recording:
            return False
        return self._screen_recorder.pause()

    def resume(self) -> bool:
        if not self._recording:
            return False
        return self._screen_recorder.resume()

    def _handle_f8(self) -> None:
        """F8 스크린샷 (녹화 중에는 현재 캡처 프레임 사용)"""
        if not self._recording:
            return

        try:
            # 녹화 중에는 dxcam/mss 캡처 프레임 재사용
            frame = self._screen_recorder.get_current_frame()
            if frame is None:
                return

            templates_dir = DATA_DIR / "templates"
            templates_dir.mkdir(parents=True, exist_ok=True)

            import cv2
            filename = f"trigger_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.png"
            filepath = templates_dir / filename
            cv2.imwrite(str(filepath), frame)

            self._trigger_images.append(str(filepath))
            logger.info(f"트리거 캡처: {filepath}")

            if self._on_trigger_capture:
                self._on_trigger_capture(str(filepath))
        except Exception as e:
            logger.error(f"F8 캡처 오류: {e}")

    def _handle_click_capture(self, x: int, y: int, timestamp: float) -> Optional[str]:
        """마우스 클릭 시 자동 스크린샷 캡처"""
        if not self._recording:
            return None

        try:
            # 현재 캡처 프레임 사용
            frame = self._screen_recorder.get_current_frame()
            if frame is None:
                logger.warning("클릭 캡처: 프레임 없음")
                return None

            # 클릭 이미지 저장 디렉토리 (templates 폴더)
            templates_dir = DATA_DIR / "templates"
            templates_dir.mkdir(parents=True, exist_ok=True)

            import cv2
            filename = f"click_{self._session_name}_{timestamp:.3f}.png"
            filepath = templates_dir / filename
            cv2.imwrite(str(filepath), frame)

            logger.info(f"클릭 캡처 저장: {filepath}")
            return str(filepath)
        except Exception as e:
            logger.error(f"클릭 캡처 오류: {e}")
            return None


# 전역 인스턴스
input_logger = InputLogger()

def get_input_logger() -> InputLogger:
    return input_logger
