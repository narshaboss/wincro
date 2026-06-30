"""
WinCro 규칙 기반 실행 엔진

조건 기반 자동화 규칙을 실행합니다.
시간 기반이 아닌 조건(이미지 감지 등) 기반으로 동작합니다.
"""

import time
import random
import threading
from typing import Optional, List, Dict, Any, Callable, Tuple
from dataclasses import dataclass
from enum import Enum
from datetime import datetime

import pyautogui
pyautogui.FAILSAFE = False
import pyperclip
import cv2
import numpy as np
from PIL import ImageGrab
from pathlib import Path
import ctypes
import ctypes.wintypes

try:
    import mss
except ImportError:  # pragma: no cover - packaged builds include mss, this is a safe fallback.
    mss = None

from ..utils.input_controller import (
    block_automation_input,
    get_input_controller,
    is_arduino_enabled,
    is_arduino_strict_enabled,
    unblock_automation_input,
)

# pynput 사용 시도 (멀티모니터 지원)
try:
    from pynput.mouse import Button, Controller as MouseController
    _pynput_mouse = MouseController()
    _has_pynput = True
except ImportError:
    _pynput_mouse = None
    _has_pynput = False

# Windows API 상수 (멀티모니터 지원)
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77

# SendInput 구조체 정의
class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))
    ]

class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_ulong),
        ("mi", MOUSEINPUT)
    ]

INPUT_MOUSE = 0

from ..utils.logger import get_logger
from ..utils.config import get_config
from ..analyzer.automation_models import AutomationPlan, AutomationRule, RuleType
from ..analyzer.enhanced_matcher import get_enhanced_matcher

logger = get_logger(__name__)

_MULTISCALE_FACTORS = (1.0, 1.1, 0.9, 1.25, 0.8, 1.4, 0.7, 1.5)
PLAYLIST_SKIP_TRIGGER_MISSING = "playlist_skip:trigger_missing"
PLAYLIST_SKIP_TRIGGER_TIMEOUT_SECONDS = 30.0
TRIGGER_COORD_SEARCH_RADIUS = 220
_screen_capture_lock = threading.Lock()


def _resize_template_gray(template_gray: np.ndarray, scale: float):
    if abs(scale - 1.0) < 1e-6:
        return template_gray
    interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
    resized = cv2.resize(template_gray, None, fx=scale, fy=scale, interpolation=interp)
    if resized is None or resized.size == 0:
        return None
    return resized


def _grab_screen_bgr() -> Optional[np.ndarray]:
    """Capture the screen without spawning per-capture daemon threads."""
    with _screen_capture_lock:
        if mss is not None:
            with mss.mss() as sct:
                monitor = sct.monitors[0] if sct.monitors else None
                if monitor is None:
                    return None
                screenshot = sct.grab(monitor)
            frame = np.array(screenshot)
            if frame.ndim == 3 and frame.shape[2] == 4:
                return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
            if frame.ndim == 3 and frame.shape[2] == 3:
                return frame
            return None

        screenshot = ImageGrab.grab()
        return cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)

# ANSI 색상 코드 상수 (성능 최적화)
_CYAN = "\033[96m"
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_RED = "\033[91m"
_MAGENTA = "\033[95m"
_RESET = "\033[0m"

# 실행 엔진 상수
PIXEL_TOLERANCE_SMALL = 10        # 마우스 위치 확인 허용 오차 (작은)
PIXEL_TOLERANCE_LARGE = 15        # 마우스 위치 확인 허용 오차 (큰)
MAX_FAIL_COUNT = 10               # 연속 실패 시 중단 임계값
MIN_WAIT_SECONDS = 0.8            # 최소 대기 시간 (초)
MIN_MOUSE_DURATION = 0.4          # 최소 마우스 이동 시간 (초)
INTERVENTION_DISTANCE_PX = 50     # 사용자 개입 감지 거리 (픽셀)
NEXT_SCREEN_CONFIDENCE = 0.45     # 다음 화면 대기 신뢰도
MAX_MOVE_ATTEMPTS = 10            # 마우스 이동 최대 재시도
IMAGE_CLICK_UNTIL_DISAPPEAR_MIN_CLICKS = 5
IMAGE_CLICK_UNTIL_DISAPPEAR_MAX_SECONDS = 30.0
IMAGE_CLICK_UNTIL_DISAPPEAR_MISS_CONFIRM = 2

# 성능 최적화용 캐시
_screen_size_cache = None
_screen_size_cache_time = 0
_SCREEN_SIZE_CACHE_TTL = 5.0
_screen_size_lock = threading.Lock()

# OrderedDict로 진정한 LRU 캐시 구현
from collections import OrderedDict
_template_cache: OrderedDict = OrderedDict()  # {image_path: (template_gray, h, w, mtime, template_bgr)}
_template_cache_lock = threading.Lock()
_MAX_TEMPLATE_CACHE = 50
_IMAGE_COLOR_DELTA_MAX = 18.0
_IMAGE_BRIGHTNESS_DELTA_MAX = 28.0


def _get_screen_size_cached() -> Tuple[int, int]:
    """캐시된 화면 크기 반환 (스레드 안전)"""
    global _screen_size_cache, _screen_size_cache_time
    current_time = time.time()
    with _screen_size_lock:
        if _screen_size_cache is None or (current_time - _screen_size_cache_time) > _SCREEN_SIZE_CACHE_TTL:
            _screen_size_cache = pyautogui.size()
            _screen_size_cache_time = current_time
        return _screen_size_cache


def _get_cached_template(image_path: str):
    """캐시된 템플릿 이미지 반환 (스레드 안전, LRU 방식)"""
    global _template_cache
    try:
        path = Path(image_path)
        if not path.exists():
            return None
        mtime = path.stat().st_mtime

        # 캐시 확인 (락 사용)
        with _template_cache_lock:
            if image_path in _template_cache:
                cached = _template_cache[image_path]
                if cached[3] == mtime:
                    # LRU: 최근 접근 항목을 맨 뒤로 이동
                    _template_cache.move_to_end(image_path)
                    return cached[0], cached[1], cached[2]

        # 캐시 미스 - 이미지 로드 (락 밖에서 I/O)
        # 한글 경로 지원
        img_array = np.fromfile(image_path, np.uint8)
        template = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        if template is None:
            return None
        template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
        h, w = template_gray.shape

        # 캐시에 저장 (락 사용, LRU)
        with _template_cache_lock:
            # 이미 있으면 삭제 후 새로 추가 (맨 뒤로)
            if image_path in _template_cache:
                del _template_cache[image_path]
            # 용량 초과시 가장 오래된 항목(맨 앞) 삭제
            while len(_template_cache) >= _MAX_TEMPLATE_CACHE:
                _template_cache.popitem(last=False)
            _template_cache[image_path] = (template_gray, h, w, mtime, template)

        return template_gray, h, w
    except Exception:
        return None


def _get_cached_template_bgr(image_path: str) -> Optional[np.ndarray]:
    """Return the cached color template used by optional color/brightness verification."""
    global _template_cache
    try:
        path = Path(image_path)
        if not path.exists():
            return None
        mtime = path.stat().st_mtime

        with _template_cache_lock:
            cached = _template_cache.get(image_path)
            if cached and cached[3] == mtime and len(cached) >= 5:
                _template_cache.move_to_end(image_path)
                return cached[4]

        img_array = np.fromfile(image_path, np.uint8)
        template = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        if template is None:
            return None
        template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
        h, w = template_gray.shape

        with _template_cache_lock:
            if image_path in _template_cache:
                del _template_cache[image_path]
            while len(_template_cache) >= _MAX_TEMPLATE_CACHE:
                _template_cache.popitem(last=False)
            _template_cache[image_path] = (template_gray, h, w, mtime, template)

        return template
    except Exception:
        return None


def _resize_template_bgr(template_bgr: np.ndarray, width: int, height: int) -> Optional[np.ndarray]:
    if template_bgr is None or width <= 0 or height <= 0:
        return None
    if template_bgr.shape[1] == width and template_bgr.shape[0] == height:
        return template_bgr
    interp = cv2.INTER_AREA if width < template_bgr.shape[1] or height < template_bgr.shape[0] else cv2.INTER_LINEAR
    resized = cv2.resize(template_bgr, (width, height), interpolation=interp)
    if resized is None or resized.size == 0:
        return None
    return resized


def _passes_image_visual_verification(
    screenshot_bgr: np.ndarray,
    template_bgr: Optional[np.ndarray],
    left: int,
    top: int,
    width: int,
    height: int,
    *,
    verify_color: bool = False,
    verify_brightness: bool = False,
) -> bool:
    """Filter shape matches with fixed color/brightness checks when explicitly enabled."""
    if not verify_color and not verify_brightness:
        return True
    if screenshot_bgr is None or template_bgr is None:
        return False
    if left < 0 or top < 0 or width <= 0 or height <= 0:
        return False
    crop = screenshot_bgr[top:top + height, left:left + width]
    if crop.size == 0 or crop.shape[0] != height or crop.shape[1] != width:
        return False

    template_scaled = _resize_template_bgr(template_bgr, width, height)
    if template_scaled is None:
        return False

    try:
        crop_lab = cv2.cvtColor(crop, cv2.COLOR_BGR2LAB)
        template_lab = cv2.cvtColor(template_scaled, cv2.COLOR_BGR2LAB)
    except cv2.error:
        return False

    if verify_brightness:
        brightness_delta = float(abs(np.mean(crop_lab[:, :, 0]) - np.mean(template_lab[:, :, 0])))
        if brightness_delta > _IMAGE_BRIGHTNESS_DELTA_MAX:
            return False

    if verify_color:
        crop_ab = crop_lab[:, :, 1:3].astype(np.float32)
        template_ab = template_lab[:, :, 1:3].astype(np.float32)
        color_delta = float(np.mean(np.linalg.norm(crop_ab - template_ab, axis=2)))
        if color_delta > _IMAGE_COLOR_DELTA_MAX:
            return False

    return True


def _perform_mouse_click(click_type: str = "click") -> bool:
    """마우스 클릭만 실행 (이동 없이, mouse_event 사용)"""
    try:
        user32 = ctypes.windll.user32
        if click_type == "double_click":
            for _ in range(2):
                user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
                time.sleep(0.02)
                user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
                time.sleep(0.05)
        elif click_type == "right_click":
            user32.mouse_event(MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, 0)
            time.sleep(0.02)
            user32.mouse_event(MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)
        else:
            user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            time.sleep(0.02)
            user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        return True
    except Exception as e:
        logger.error(f"[클릭] 저수준 클릭 실패: {e}")
        return False


def _normalize_click_coord(value: Any, axis: str) -> int:
    """Normalize click coordinates to plain ints before Win32 calls."""
    if value is None:
        raise TypeError(f"{axis} coordinate is None")

    if isinstance(value, np.ndarray):
        if value.size != 1:
            raise TypeError(f"{axis} coordinate array must contain exactly one value: {value!r}")
        value = value.reshape(-1)[0]

    if isinstance(value, np.generic):
        value = value.item()

    if isinstance(value, (list, tuple)):
        if len(value) != 1:
            raise TypeError(f"{axis} coordinate sequence must contain exactly one value: {value!r}")
        value = value[0]
        if isinstance(value, np.ndarray):
            if value.size != 1:
                raise TypeError(f"{axis} coordinate array must contain exactly one value: {value!r}")
            value = value.reshape(-1)[0]
        if isinstance(value, np.generic):
            value = value.item()

    try:
        return int(round(float(value)))
    except Exception as exc:
        raise TypeError(f"{axis} coordinate is not numeric: {value!r}") from exc


def _normalize_click_point(x: Any, y: Any) -> Tuple[int, int]:
    """Normalize an (x, y) pair to Win32-safe ints."""
    return _normalize_click_coord(x, "x"), _normalize_click_coord(y, "y")


def _win32_move_click(x: int, y: int, click_type: str = "click") -> bool:
    """
    멀티모니터 지원 마우스 이동 및 클릭 (pynput 우선, Win32 대체)
    """
    x, y = _normalize_click_point(x, y)

    # 마우스 캡처/클리핑 해제 - 게임 충돌 방지를 위해 비활성화
    # try:
    #     ctypes.windll.user32.ReleaseCapture()
    #     ctypes.windll.user32.ClipCursor(None)
    # except (OSError, AttributeError):
    #     pass

    # 1. pynput 시도 (가장 안정적)
    if _has_pynput:
        try:
            _pynput_mouse.position = (x, y)
            time.sleep(0.1)

            # 위치 확인
            actual = _pynput_mouse.position
            if abs(actual[0] - x) < PIXEL_TOLERANCE_SMALL and abs(actual[1] - y) < PIXEL_TOLERANCE_SMALL:
                # 클릭
                btn = Button.left
                if click_type == "right_click":
                    btn = Button.right

                if click_type == "double_click":
                    _pynput_mouse.click(btn, 2)
                else:
                    _pynput_mouse.click(btn, 1)

                logger.debug(f"[pynput] 클릭 성공: ({x}, {y})")
                return True
            else:
                logger.debug(f"[pynput] 이동 실패: 목표=({x}, {y}), 실제={actual}")
        except Exception as e:
            logger.debug(f"[pynput] 오류: {e}")

    # 2. ctypes SetCursorPos 시도
    try:
        ctypes.windll.user32.SetCursorPos(x, y)
        time.sleep(0.1)

        # 위치 확인
        actual_pos = pyautogui.position()
        if abs(actual_pos[0] - x) < PIXEL_TOLERANCE_SMALL and abs(actual_pos[1] - y) < PIXEL_TOLERANCE_SMALL:
            if _perform_mouse_click(click_type):
                return True
            logger.debug(f"[Win32] low-level click failed after SetCursorPos: target=({x}, {y})")
            return False
        else:
            logger.debug(f"[Win32] SetCursorPos 이동 실패: 목표=({x}, {y}), 실제={actual_pos}")
    except Exception as e:
        logger.debug(f"[Win32] SetCursorPos 오류: {e}")

    # 3. SendInput API 시도 (가장 저수준)
    try:
        # 가상 화면 크기 가져오기
        screen_width = ctypes.windll.user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        screen_height = ctypes.windll.user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
        screen_left = ctypes.windll.user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        screen_top = ctypes.windll.user32.GetSystemMetrics(SM_YVIRTUALSCREEN)

        # 절대 좌표 변환 (0-65535 범위)
        abs_x = int((x - screen_left) * 65535 / screen_width)
        abs_y = int((y - screen_top) * 65535 / screen_height)

        # 마우스 캡처 해제 재시도 - 게임 충돌 방지를 위해 비활성화
        # ctypes.windll.user32.ReleaseCapture()
        # ctypes.windll.user32.ClipCursor(None)

        # SendInput으로 마우스 이동
        move_input = INPUT()
        move_input.type = INPUT_MOUSE
        move_input.mi.dx = abs_x
        move_input.mi.dy = abs_y
        move_input.mi.mouseData = 0
        move_input.mi.dwFlags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
        move_input.mi.time = 0
        move_input.mi.dwExtraInfo = None

        ctypes.windll.user32.SendInput(1, ctypes.byref(move_input), ctypes.sizeof(INPUT))
        time.sleep(0.15)

        # 위치 확인
        actual_pos = pyautogui.position()
        if abs(actual_pos[0] - x) >= PIXEL_TOLERANCE_LARGE or abs(actual_pos[1] - y) >= PIXEL_TOLERANCE_LARGE:
            logger.debug(f"[SendInput] 이동 실패: 목표=({x}, {y}), 실제={actual_pos}")
            return False

        # 클릭
        click_down = INPUT()
        click_down.type = INPUT_MOUSE
        click_down.mi.dx = 0
        click_down.mi.dy = 0
        click_down.mi.mouseData = 0
        click_down.mi.dwFlags = MOUSEEVENTF_LEFTDOWN if click_type != "right_click" else MOUSEEVENTF_RIGHTDOWN
        click_down.mi.time = 0
        click_down.mi.dwExtraInfo = None

        click_up = INPUT()
        click_up.type = INPUT_MOUSE
        click_up.mi.dx = 0
        click_up.mi.dy = 0
        click_up.mi.mouseData = 0
        click_up.mi.dwFlags = MOUSEEVENTF_LEFTUP if click_type != "right_click" else MOUSEEVENTF_RIGHTUP
        click_up.mi.time = 0
        click_up.mi.dwExtraInfo = None

        if click_type == "double_click":
            for _ in range(2):
                ctypes.windll.user32.SendInput(1, ctypes.byref(click_down), ctypes.sizeof(INPUT))
                time.sleep(0.02)
                ctypes.windll.user32.SendInput(1, ctypes.byref(click_up), ctypes.sizeof(INPUT))
                time.sleep(0.05)
        else:
            ctypes.windll.user32.SendInput(1, ctypes.byref(click_down), ctypes.sizeof(INPUT))
            time.sleep(0.02)
            ctypes.windll.user32.SendInput(1, ctypes.byref(click_up), ctypes.sizeof(INPUT))

        logger.debug(f"[SendInput] 클릭 성공: ({x}, {y})")
        return True
    except Exception as e:
        logger.error(f"[SendInput] 마우스 제어 실패: {e}")
        return False


def _win32_force_click_at(x: int, y: int, click_type: str = "click") -> bool:
    """
    절대 좌표에 강제 클릭 (단순화된 버전)
    """
    try:
        x, y = _normalize_click_point(x, y)
        user32 = ctypes.windll.user32

        # 마우스 캡처 해제 - 게임 충돌 방지를 위해 비활성화
        # user32.ReleaseCapture()
        # user32.ClipCursor(None)

        # 마우스 이동
        user32.SetCursorPos(x, y)
        time.sleep(0.05)

        # 클릭 실행
        if not _perform_mouse_click(click_type):
            logger.error(f"[클릭] force-click low-level failure: target=({x}, {y})")
            return False
        time.sleep(0.05)
        logger.debug(f"[클릭] 완료 ({x}, {y})")
        return True

    except Exception as e:
        logger.error(f"[클릭] 실패: {e}")
        return False


class ExecutionState(Enum):
    """실행 상태"""
    IDLE = "idle"
    RUNNING_INITIAL = "running_initial"  # 초기 규칙 실행 중
    MONITORING = "monitoring"  # 모니터링 중
    PAUSED = "paused"
    STOPPED = "stopped"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class RuleExecutionResult:
    """규칙 실행 결과"""
    rule_id: str
    success: bool
    message: str = ""
    executed_at: Optional[datetime] = None
    execution_time_ms: int = 0
    skip_current_playlist: bool = False
    rewind_previous_action: bool = False
    rewind_delay: float = 0.0
    monitoring_jump_index: int = -1
    monitoring_jump_rule_id: str = ""


@dataclass
class ExecutionProgress:
    """실행 진행 상태"""
    state: ExecutionState = ExecutionState.IDLE
    current_rule: Optional[str] = None
    current_action_number: str = ""
    current_action_name: str = ""
    current_action_is_monitoring: bool = False
    initial_total: int = 0
    initial_completed: int = 0
    monitoring_rules_active: int = 0
    monitoring_triggers: int = 0  # 모니터링 규칙 발동 횟수
    message: str = ""

    @property
    def current_step(self) -> int:
        """현재 단계"""
        return self.initial_completed

    @property
    def total_steps(self) -> int:
        """전체 단계"""
        return self.initial_total

    @property
    def current_rule_description(self) -> str:
        """현재 규칙 설명"""
        return self.message


class RuleExecutor:
    """
    규칙 기반 실행 엔진

    AutomationPlan의 규칙들을 조건에 따라 실행합니다.
    """

    def __init__(self):
        """실행 엔진 초기화"""
        self._config = get_config()
        self._state = ExecutionState.IDLE
        self._current_plan: Optional[AutomationPlan] = None

        # 실행 제어
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()  # 기본값: 일시정지 아님

        # 스레드 추적
        self._monitor_thread: Optional[threading.Thread] = None
        self._execution_thread: Optional[threading.Thread] = None

        # 진행 상태
        self._progress = ExecutionProgress()

        # 결과 저장
        self._results: List[RuleExecutionResult] = []
        self._child_rules_executed_with_parent: set[str] = set()
        self._trigger_missing_rewind_attempts: dict[str, int] = {}

        # 콜백
        self._on_progress: Optional[Callable[[ExecutionProgress], None]] = None
        self._on_rule_executed: Optional[Callable[[RuleExecutionResult], None]] = None
        self._on_complete: Optional[Callable[[bool, str], None]] = None
        self._on_error: Optional[Callable[[str, AutomationRule], None]] = None

        # PyAutoGUI 설정
        pyautogui.FAILSAFE = False
        pyautogui.PAUSE = 0.3  # 기본 대기 시간

        # 속도 설정 (자연스러운 속도를 위해 최소값 설정)
        self._default_wait = max(self._config.player.default_wait_ms / 1000, MIN_WAIT_SECONDS)
        self._mouse_duration = max(self._config.player.mouse_move_duration, MIN_MOUSE_DURATION)
        self._typing_interval = self._config.player.typing_interval

        # 사용자 개입 감지
        self._user_intervention_enabled = False  # 사용자 개입 감지 비활성화 (로딩창 마우스 끌림 문제)
        self._last_mouse_pos = None  # 마지막 마우스 위치 (자동화가 이동시킨 위치)
        self._intervention_pause_seconds = 3  # 개입 시 대기 시간
        self._is_moving_mouse = False  # 자동화가 마우스 이동 중인지

        # 현재 실행 중인 액션 번호 (로깅용)
        self._current_step_num = ""
        self._last_monitoring_route_detail: Dict[str, Any] = {}
        self._current_monitoring_wait_detail: Dict[str, Any] = {}


    @property
    def state(self) -> ExecutionState:
        """현재 실행 상태"""
        return self._state

    @property
    def progress(self) -> ExecutionProgress:
        """실행 진행 상태"""
        return self._progress

    @property
    def results(self) -> List[RuleExecutionResult]:
        """실행 결과 목록"""
        return self._results.copy()

    @property
    def _step_prefix(self) -> str:
        """현재 액션 번호 접두사 (로깅용)"""
        return f"[{self._current_step_num}] " if self._current_step_num else ""

    @staticmethod
    def _valid_coord_region(region) -> bool:
        if not isinstance(region, (list, tuple)) or len(region) != 4:
            return False
        try:
            x1, y1, x2, y2 = [int(v) for v in region]
        except (TypeError, ValueError):
            return False
        return x2 > x1 and y2 > y1

    @staticmethod
    def _valid_file_path(path_value) -> bool:
        if not path_value:
            return False
        try:
            return Path(path_value).exists()
        except (TypeError, OSError):
            return False

    def _has_coordinate_reader_config(self, config) -> bool:
        split_anchor_ready = (
            bool(getattr(config, "coord_anchor_enabled", False)) and
            self._valid_coord_region(getattr(config, "coord_x_region", None)) and
            self._valid_coord_region(getattr(config, "coord_y_region", None)) and
            self._valid_file_path(getattr(config, "coord_x_anchor_image", "")) and
            self._valid_file_path(getattr(config, "coord_y_anchor_image", ""))
        )
        if split_anchor_ready:
            return True
        legacy_pair_ready = (
            bool(getattr(config, "coord_anchor_enabled", False)) and
            self._valid_coord_region(getattr(config, "coord_anchor_search_region", None)) and
            self._valid_file_path(getattr(config, "coord_x_anchor_image", "")) and
            self._valid_file_path(getattr(config, "coord_y_anchor_image", ""))
        )
        if legacy_pair_ready:
            return True
        if bool(getattr(config, "coord_anchor_enabled", False)):
            return False
        return (
            self._valid_coord_region(getattr(config, "coord_x_region", None)) and
            self._valid_coord_region(getattr(config, "coord_y_region", None))
        )

    def _read_game_coordinates(self, matcher, config):
        anchor_enabled = bool(getattr(config, "coord_anchor_enabled", False))
        split_anchor_regions = (
            anchor_enabled and
            self._valid_coord_region(getattr(config, "coord_x_region", None)) and
            self._valid_coord_region(getattr(config, "coord_y_region", None)) and
            self._valid_file_path(getattr(config, "coord_x_anchor_image", "")) and
            self._valid_file_path(getattr(config, "coord_y_anchor_image", ""))
        )
        legacy_pair_region = (
            anchor_enabled and
            not split_anchor_regions and
            self._valid_coord_region(getattr(config, "coord_anchor_search_region", None))
        )
        return matcher.read_both_coordinates(
            getattr(config, "coord_x_region", None),
            getattr(config, "coord_y_region", None),
            "X", "Y",
            stop_event=self._stop_event,
            x_anchor_image=getattr(config, "coord_x_anchor_image", None) if anchor_enabled else None,
            y_anchor_image=getattr(config, "coord_y_anchor_image", None) if anchor_enabled else None,
            x_anchor_offset=getattr(config, "coord_x_anchor_offset", None) if anchor_enabled and not split_anchor_regions else None,
            y_anchor_offset=getattr(config, "coord_y_anchor_offset", None) if anchor_enabled and not split_anchor_regions else None,
            anchor_search_region=getattr(config, "coord_anchor_search_region", None) if legacy_pair_region else None,
        )

    def set_callbacks(
        self,
        on_progress: Optional[Callable[[ExecutionProgress], None]] = None,
        on_rule_executed: Optional[Callable[[RuleExecutionResult], None]] = None,
        on_complete: Optional[Callable[[bool, str], None]] = None,
        on_error: Optional[Callable[[str, AutomationRule], None]] = None,
    ) -> None:
        """콜백 설정"""
        self._on_progress = on_progress
        self._on_rule_executed = on_rule_executed
        self._on_complete = on_complete
        self._on_error = on_error

    def execute_plan(self, plan: AutomationPlan) -> bool:
        """
        자동화 계획 실행

        Args:
            plan: 실행할 자동화 계획

        Returns:
            bool: 실행 시작 성공 여부
        """
        if self._state in [ExecutionState.RUNNING_INITIAL, ExecutionState.MONITORING]:
            logger.warning("이미 실행 중입니다")
            return False

        # 이전 실행 스레드가 아직 살아있으면 종료 대기
        if self._execution_thread and self._execution_thread.is_alive():
            self._execution_thread.join(timeout=5.0)
            if self._execution_thread.is_alive():
                logger.warning("이전 실행 스레드가 종료되지 않아 시작할 수 없습니다")
                return False

        self._current_plan = plan
        self._results.clear()
        self._last_monitoring_route_detail = {}
        self._current_monitoring_wait_detail = {}
        unblock_automation_input()
        self._stop_event.clear()
        self._pause_event.set()

        # 사용자 개입 감지 초기화
        self._last_mouse_pos = pyautogui.position()  # 현재 마우스 위치를 기준점으로
        self._is_moving_mouse = False

        # 진행 상태 초기화
        all_rules_count = len(self._flatten_rules(plan.initial_rules)) + len(self._flatten_rules(plan.monitoring_rules))
        self._progress = ExecutionProgress(
            state=ExecutionState.RUNNING_INITIAL,
            initial_total=all_rules_count,
            initial_completed=0,
            monitoring_rules_active=0,
            message="실행 시작",
        )

        # 실행 스레드 시작 (인스턴스 변수에 저장하여 추적)
        self._execution_thread = threading.Thread(target=self._execution_loop, daemon=True)
        self._execution_thread.start()

        return True

    def execute_plan_async(
        self,
        plan: AutomationPlan,
        on_complete: Optional[Callable[[bool], None]] = None,
    ) -> None:
        """비동기 자동화 계획 실행"""
        def run():
            success = self.execute_plan(plan)
            if on_complete:
                on_complete(success)

        threading.Thread(target=run, daemon=True).start()

    def pause(self) -> None:
        """실행 일시정지"""
        try:
            self._pause_event.clear()
            self._state_before_pause = self._state
            self._state = ExecutionState.PAUSED
            try:
                self._update_progress("일시정지됨")
            except Exception as e:
                logger.debug(f"일시정지 UI 업데이트 실패: {e}")
            logger.info(f"{_YELLOW}⏸ 일시정지{_RESET}")
        except Exception as e:
            logger.error(f"일시정지 오류: {e}")

    def resume(self) -> None:
        """실행 재개"""
        try:
            self._pause_event.set()
            self._state = getattr(self, '_state_before_pause', ExecutionState.RUNNING_INITIAL)
            try:
                self._update_progress("실행 재개")
            except Exception as e:
                logger.debug(f"재개 UI 업데이트 실패: {e}")
            logger.info(f"{_GREEN}▶ 재개{_RESET}")
        except Exception as e:
            logger.error(f"재개 오류: {e}")

    def stop(self) -> None:
        """실행 중지 (비차단 — UI 스레드에서 안전하게 호출 가능)"""
        try:
            self._stop_event.set()
            block_automation_input("RuleExecutor.stop")
            try:
                get_input_controller().release_all()
            except Exception:
                pass
            self._pause_event.set()  # 일시정지 상태에서도 종료 가능하게
            self._state = ExecutionState.STOPPED
            try:
                self._update_progress("실행 중지됨")
            except Exception as e:
                logger.debug(f"중지 UI 업데이트 실패: {e}")
            self._log_monitoring_stop_context("stop_called", self._step_prefix)

            # 스레드 종료 대기를 별도 스레드에서 수행 (UI 차단 방지)
            def _join_threads():
                if self._execution_thread and self._execution_thread.is_alive():
                    self._execution_thread.join(timeout=3.0)
                    if self._execution_thread.is_alive():
                        logger.warning("실행 스레드가 3초 후에도 응답 없음")
                if self._monitor_thread and self._monitor_thread.is_alive():
                    self._monitor_thread.join(timeout=2.0)
                    if self._monitor_thread.is_alive():
                        logger.warning("모니터링 스레드가 2초 후에도 응답 없음")

            threading.Thread(target=_join_threads, daemon=True).start()

            logger.info(f"{_MAGENTA}{self._step_prefix}■ 실행 중지됨{_RESET}")
        except Exception as e:
            logger.error(f"중지 오류: {e}")

    def _wait_for_resume(self) -> bool:
        """일시정지 상태에서 재개를 대기 (중지 이벤트를 주기적으로 체크).

        Returns:
            True면 중지 요청됨 (루프 탈출 필요), False면 재개됨.
        """
        while not self._pause_event.wait(timeout=0.3):
            if self._stop_event.is_set():
                return True
        return self._stop_event.is_set()

    def _check_user_intervention(self) -> bool:
        """
        사용자 개입 감지
        마우스가 자동화가 이동시킨 위치에서 벗어났는지 확인
        Returns: True if user intervened
        """
        if not self._user_intervention_enabled:
            return False

        if self._is_moving_mouse:
            return False  # 자동화가 마우스 이동 중이면 무시

        if self._last_mouse_pos is None:
            return False

        try:
            current_pos = pyautogui.position()
            last_x, last_y = self._last_mouse_pos

            # 마우스가 50픽셀 이상 이동했으면 사용자 개입으로 판단
            distance = ((current_pos[0] - last_x) ** 2 + (current_pos[1] - last_y) ** 2) ** 0.5
            if distance > INTERVENTION_DISTANCE_PX:
                return True
        except (TypeError, ValueError, AttributeError):
            pass

        return False

    def _wait_after_intervention(self) -> None:
        """사용자 개입 후 대기"""
        logger.info(f"{_YELLOW}⏸ 마우스 개입 감지 → {self._intervention_pause_seconds}초 대기{_RESET}")
        self._update_progress(f"사용자 개입 감지 - {self._intervention_pause_seconds}초 대기")

        for i in range(self._intervention_pause_seconds):
            if self._stop_event.is_set():
                return
            time.sleep(1)
            remaining = self._intervention_pause_seconds - i - 1
            if remaining > 0:
                self._update_progress(f"사용자 개입 - {remaining}초 후 재개")

        # 대기 후 현재 마우스 위치를 새 기준점으로 설정
        self._last_mouse_pos = pyautogui.position()
        logger.info(f"{_GREEN}▶ 대기 완료, 재개{_RESET}")
        self._update_progress("재개 중...")

        # 마우스 캡처 해제 시도 - 게임 충돌 방지를 위해 비활성화
        # try:
        #     ctypes.windll.user32.ReleaseCapture()
        #     ctypes.windll.user32.ClipCursor(None)
        #     # 데스크톱을 포커스하여 활성 창 해제
        #     desktop_hwnd = ctypes.windll.user32.GetDesktopWindow()
        #     ctypes.windll.user32.SetForegroundWindow(desktop_hwnd)
        #     time.sleep(0.1)
        #     logger.info("[개입감지] 마우스 캡처 해제 시도 완료")
        # except Exception as e:
        #     logger.warning(f"[개입감지] 마우스 캡처 해제 실패: {e}")

    def _flatten_rules(self, rules: List[AutomationRule]) -> List[AutomationRule]:
        """계층 구조를 평탄화하여 모든 규칙 반환 (자식 포함)"""
        result = []
        for rule in rules:
            if not getattr(rule, "enabled", True):
                continue
            result.append(rule)
            if rule.children:
                result.extend(self._flatten_rules(rule.children))
        return result

    def _flatten_rules_with_step(self, rules: List[AutomationRule], parent_step: str = "") -> List[Tuple[AutomationRule, str]]:
        """계층 구조를 평탄화하면서 단계 번호 추적 (예: "1", "1-1", "1-2")"""
        result = []
        visible_index = 0
        for i, rule in enumerate(rules):
            if not getattr(rule, "enabled", True):
                continue
            visible_index += 1
            if parent_step:
                step = f"{parent_step}-{visible_index}"
            else:
                step = str(visible_index)
            result.append((rule, step))
            if rule.children:
                result.extend(self._flatten_rules_with_step(rule.children, step))
        return result

    def _rule_id_step_map(self, rules: List[AutomationRule]) -> Dict[str, str]:
        """rule_id별 원본 단계 번호를 만든다."""
        mapping: Dict[str, str] = {}
        for rule, step in self._flatten_rules_with_step(rules or []):
            rule_id = str(getattr(rule, "rule_id", "") or "")
            if rule_id and rule_id not in mapping:
                mapping[rule_id] = str(step)
        return mapping

    def _rule_step_in_rules(self, rules: List[AutomationRule], target_rule: Optional[AutomationRule]) -> str:
        """대상 액션이 주어진 규칙 목록에서 몇 번으로 보이는지 찾는다."""
        if target_rule is None:
            return ""
        target_rule_id = str(getattr(target_rule, "rule_id", "") or "")
        for candidate, step in self._flatten_rules_with_step(rules or []):
            candidate_rule_id = str(getattr(candidate, "rule_id", "") or "")
            if target_rule_id and candidate_rule_id == target_rule_id:
                return str(step)
            if candidate is target_rule:
                return str(step)
        return ""

    def _format_step_alias(self, runtime_step: str, original_step: str) -> str:
        """부분실행 번호와 원본 플랜 번호가 다를 때 로그에 둘 다 표시한다."""
        runtime_step = str(runtime_step or "")
        original_step = str(original_step or "")
        if original_step and runtime_step and original_step != runtime_step:
            return f"{original_step} (현재목록 {runtime_step})"
        return original_step or runtime_step or "?"

    def _format_monitoring_detail(self, detail: Dict[str, Any]) -> str:
        if not detail:
            return "-"
        ordered_keys = (
            "action",
            "rule_id",
            "watch",
            "image",
            "priority",
            "monitor_image",
            "matched",
            "threshold",
            "search_region",
            "monitor_actions",
            "goto_index",
            "goto_step",
            "goto_rule_id",
            "target_step",
            "target_rule_id",
            "target_name",
            "watches",
            "final_images",
            "elapsed",
        )
        parts = []
        for key in ordered_keys:
            if key not in detail:
                continue
            value = detail.get(key)
            if value is None or value == "":
                value = "-"
            parts.append(f"{key}={value}")
        return " ".join(parts) if parts else "-"

    def _log_monitoring_stop_context(self, reason: str, step_prefix: str = "", start_time: Optional[datetime] = None) -> None:
        wait_detail = dict(self._current_monitoring_wait_detail or {})
        if start_time is not None:
            try:
                wait_detail["elapsed"] = f"{(datetime.now() - start_time).total_seconds():.1f}s"
            except Exception:
                pass
        route_detail = dict(self._last_monitoring_route_detail or {})
        logger.warning(
            f"{_YELLOW}{step_prefix}[모니터링중단상세] reason={reason} "
            f"current_wait=({self._format_monitoring_detail(wait_detail)}) "
            f"last_jump=({self._format_monitoring_detail(route_detail)}){_RESET}"
        )

    def _rules_match_for_jump(self, candidate: AutomationRule, target: AutomationRule) -> bool:
        """부분실행 복사본과 원본 액션을 같은 점프 대상으로 매칭한다."""
        if candidate is target:
            return True
        if candidate is None or target is None:
            return False
        return bool(
            getattr(candidate, "rule_id", None) == getattr(target, "rule_id", None)
            and getattr(candidate, "action_type", None) == getattr(target, "action_type", None)
            and (getattr(candidate, "description", "") or "") == (getattr(target, "description", "") or "")
        )

    def _find_jump_target_index(
        self,
        rules_with_step: List[Tuple[AutomationRule, str]],
        target_rule: AutomationRule,
    ) -> int:
        for candidate_index, (candidate_rule, _candidate_step) in enumerate(rules_with_step or []):
            if self._rules_match_for_jump(candidate_rule, target_rule):
                return candidate_index
        return -1

    def _execution_loop(self) -> None:
        """메인 실행 루프"""
        try:
            plan = self._current_plan
            if not plan:
                return

            # 전체 반복 횟수 (기본값 1)
            total_repeat_count = getattr(plan, 'total_repeat_count', 1) or 1
            current_repeat = 0

            logger.info(f"{_CYAN}{'═'*50}{_RESET}")
            if total_repeat_count > 1:
                logger.info(f"{_CYAN}▶ 실행 시작: {plan.name} ({total_repeat_count}회 반복){_RESET}")
            else:
                logger.info(f"{_CYAN}▶ 실행 시작: {plan.name}{_RESET}")
            self._state = ExecutionState.RUNNING_INITIAL

            # 하위 항목(children) 포함해서 평탄화 + 단계 번호 추적
            all_rules_with_step = self._flatten_rules_with_step(plan.initial_rules) + self._flatten_rules_with_step(plan.monitoring_rules)
            all_rules = [rule for rule, _ in all_rules_with_step]
            original_initial_rules_for_steps = list(getattr(plan, "_original_initial_rules", None) or [])
            original_step_by_rule_id = self._rule_id_step_map(original_initial_rules_for_steps)
            logger.info(f"{_CYAN}  총 {len(all_rules_with_step)}개 액션{_RESET}")
            logger.info(f"{_CYAN}{'═'*50}{_RESET}")

            # 전체 반복 루프
            while current_repeat < total_repeat_count:
                current_repeat += 1
                if total_repeat_count > 1:
                    logger.info(f"{_CYAN}── 반복 {current_repeat}/{total_repeat_count} 시작 ──{_RESET}")

                # 반복 시작 시 결과 초기화
                if current_repeat > 1:
                    self._results.clear()
                    self._progress.initial_completed = 0
                self._child_rules_executed_with_parent = set()

                self._trigger_missing_rewind_attempts.clear()

                # 모든 규칙 순차 실행 (룰과 스텝 번호를 함께 순회)
                i = 0
                while i < len(all_rules_with_step):
                    rule, runtime_step_num = all_rules_with_step[i]
                    if self._stop_event.is_set():
                        break

                    if rule.rule_id in self._child_rules_executed_with_parent:
                        skip_step_label = self._format_step_alias(
                            str(runtime_step_num or i + 1),
                            original_step_by_rule_id.get(str(getattr(rule, "rule_id", "") or ""), ""),
                        )
                        logger.debug(f"[반복묶음] 부모 반복에서 실행된 하위 액션 스킵: {skip_step_label} {rule.description or rule.action_type}")
                        self._progress.initial_completed = i + 1
                        i += 1
                        continue

                    # 일시정지 대기 (중지 이벤트 주기적 체크)
                    if self._wait_for_resume():
                        break

                    # 단계 번호와 이름 구성 (step_num이 없으면 인덱스 사용)
                    runtime_step_num = runtime_step_num if runtime_step_num else str(i + 1)
                    rule_id_for_step = str(getattr(rule, "rule_id", "") or "")
                    original_step_num = original_step_by_rule_id.get(rule_id_for_step, "")
                    display_step_num = original_step_num or str(runtime_step_num)
                    step_num = self._format_step_alias(str(runtime_step_num), original_step_num)
                    self._current_step_num = display_step_num  # 현재 액션 번호 저장 (로깅용)
                    action_name = rule.description if rule.description else rule.action_type

                    # 액션 헤더 (단계 번호 + 이름)
                    logger.info(f"{_CYAN}[{step_num}] {action_name}{_RESET}")

                    # 핵심 정보만 한 줄씩 (간결하게)
                    if rule.target_image:
                        logger.info(f"  대상: {Path(rule.target_image).name}")
                    if rule.action_keys:
                        logger.info(f"  키: {rule.action_keys}")
                    if rule.action_text:
                        text_preview = rule.action_text[:30] + "..." if len(rule.action_text) > 30 else rule.action_text
                        logger.info(f"  입력: {text_preview}")

                    # 모니터링 모드인 경우 별도 처리
                    # is_monitoring_mode가 True이거나, monitoring_watches가 있으면 모니터링 모드로 실행
                    has_monitoring_watches = len(getattr(rule, 'monitoring_watches', []) or []) > 0
                    is_monitoring = getattr(rule, 'is_monitoring_mode', False) or has_monitoring_watches
                    self._progress.current_rule = rule.rule_id
                    self._progress.current_action_number = str(display_step_num)
                    self._progress.current_action_name = action_name
                    self._progress.current_action_is_monitoring = bool(is_monitoring)
                    self._update_progress(f"[{step_num}] {action_name}")

                    logger.debug(f"[실행경로] rule={rule.description}, is_monitoring_mode={getattr(rule, 'is_monitoring_mode', False)}, watches={len(getattr(rule, 'monitoring_watches', []) or [])}, 최종판단={is_monitoring}")
                    if is_monitoring:
                        trigger_result = self._handle_trigger_gate(
                            rule,
                            datetime.now(),
                            step_num,
                            can_rewind_previous=i > 0,
                        )
                        if trigger_result is not None:
                            result = trigger_result
                        else:
                            self._state = ExecutionState.MONITORING
                            result = self._execute_monitoring_mode(rule, all_rules, i, step_num=step_num)
                            self._state = ExecutionState.RUNNING_INITIAL
                        self._results.append(result)
                    else:
                        # 다음 규칙의 타겟 이미지들 (확인용, 기본+멀티 OR)
                        next_target_images = []
                        next_rule = None
                        if not self._rule_repeats_child_actions(rule) and i + 1 < len(all_rules):
                            next_rule = all_rules[i + 1]
                            # 다음 액션이 모니터링이면 target_image는 종료 조건이므로 기다리지 않음
                            next_has_watches = len(getattr(next_rule, 'monitoring_watches', []) or []) > 0
                            next_is_monitoring = getattr(next_rule, 'is_monitoring_mode', False) or next_has_watches
                            if not next_is_monitoring:
                                next_target_images = self._target_images_for_rule(next_rule)

                        # 규칙 실행 (재시도 포함)
                        result = self._execute_rule_with_retry(
                            rule,
                            next_target_images,
                            next_rule=next_rule,
                            step_num=step_num,
                            can_rewind_previous=i > 0,
                        )
                        self._results.append(result)

                    if self._on_rule_executed:
                        self._on_rule_executed(result)

                    if getattr(result, "rewind_previous_action", False):
                        rewind_delay = max(0.0, float(getattr(result, "rewind_delay", 0.0) or 0.0))
                        target_index = max(0, i - 1)
                        target_step = all_rules_with_step[target_index][1] if all_rules_with_step else "1"
                        logger.warning(
                            f"{_YELLOW}↩ [{step_num}] 트리거 미감지 → 이전 액션 [{target_step}]으로 이동: "
                            f"{result.message}{_RESET}"
                        )
                        self._update_progress(f"[{step_num}] 트리거 미감지 → 이전 액션으로 이동")
                        if rewind_delay > 0 and self._stop_event.wait(rewind_delay):
                            break
                        i = target_index
                        continue

                    monitoring_jump_index = self._safe_int(getattr(result, "monitoring_jump_index", -1), -1)
                    monitoring_jump_rule_id = str(getattr(result, "monitoring_jump_rule_id", "") or "")
                    if monitoring_jump_index >= 0 or monitoring_jump_rule_id:
                        target_rule = None
                        original_initial_rules = []
                        try:
                            original_initial_rules = list(getattr(plan, "_original_initial_rules", None) or [])
                            runtime_initial_rules = list(getattr(plan, "initial_rules", []) or [])
                            lookup_rules = original_initial_rules or runtime_initial_rules
                            if monitoring_jump_rule_id:
                                for candidate in self._flatten_rules(lookup_rules):
                                    if getattr(candidate, "rule_id", None) == monitoring_jump_rule_id:
                                        target_rule = candidate
                                        break
                            elif 0 <= monitoring_jump_index < len(lookup_rules):
                                target_rule = lookup_rules[monitoring_jump_index]
                        except Exception:
                            target_rule = None

                        target_index = -1
                        if target_rule is not None:
                            target_index = self._find_jump_target_index(all_rules_with_step, target_rule)
                            if target_index < 0 and original_initial_rules:
                                original_rules_with_step = (
                                    self._flatten_rules_with_step(original_initial_rules)
                                    + self._flatten_rules_with_step(getattr(plan, "monitoring_rules", []) or [])
                                )
                                original_target_index = self._find_jump_target_index(original_rules_with_step, target_rule)
                                if original_target_index >= 0:
                                    logger.info(
                                        f"{_CYAN}↪ [{step_num}] 모니터링 점프 대상이 부분실행 범위 밖임 → "
                                        f"원본 플랜 범위로 전환{_RESET}"
                                    )
                                    all_rules_with_step = original_rules_with_step
                                    all_rules = [rule for rule, _ in all_rules_with_step]
                                    target_index = original_target_index
                        if target_index < 0:
                            target_label = monitoring_jump_rule_id or f"액션 {monitoring_jump_index + 1}"
                            message = f"모니터링 점프 대상 액션을 현재 실행 목록에서 찾지 못함: {target_label}"
                            logger.error(f"{_RED}✗ [{step_num}] {message}{_RESET}")
                            self._state = ExecutionState.FAILED
                            self._update_progress(message)
                            if self._on_error:
                                self._on_error(message, rule)
                            if self._on_complete:
                                self._on_complete(False, message)
                            return

                        if all_rules_with_step:
                            target_rule_for_log, target_runtime_step = all_rules_with_step[target_index]
                            target_original_step = original_step_by_rule_id.get(
                                str(getattr(target_rule_for_log, "rule_id", "") or ""),
                                "",
                            )
                            target_step = self._format_step_alias(str(target_runtime_step), target_original_step)
                        else:
                            target_step = str(monitoring_jump_index + 1)
                        logger.info(
                            f"{_CYAN}↪ [{step_num}] 모니터링 점프 → 액션 [{target_step}]부터 재생 계속: "
                            f"{result.message}{_RESET}"
                        )
                        self._update_progress(f"[{step_num}] 모니터링 점프 → 액션 {target_step}")
                        i = target_index
                        continue

                    if getattr(result, "skip_current_playlist", False):
                        self._progress.initial_completed = i + 1
                        self._state = ExecutionState.COMPLETED
                        message = result.message or PLAYLIST_SKIP_TRIGGER_MISSING
                        logger.warning(f"{_YELLOW}⏭ [{step_num}] 현재 재생목록 종료: {message}{_RESET}")
                        self._update_progress(f"재생목록 종료: {message}")
                        if self._on_complete:
                            self._on_complete(True, message)
                        return

                    if not result.success:
                        logger.error(f"{_RED}✗ [{step_num}] 실패: {result.message}{_RESET}")
                        if self._on_error:
                            self._on_error(result.message, rule)
                        # 실패 시 실행 중지
                        self._state = ExecutionState.FAILED
                        self._update_progress(f"실패: {result.message}")
                        if self._on_complete:
                            self._on_complete(False, f"동작 실패: {result.message}")
                        return

                    self._progress.initial_completed = i + 1

                    # 대기 시간 적용 (스킵된 경우 제외)
                    is_skipped = "스킵됨" in result.message if result.message else False
                    if not self._stop_event.is_set() and not is_skipped:
                        wait_time = getattr(rule, 'wait_after', self._default_wait)
                        if getattr(rule, 'wait_random', False):
                            wait_range = getattr(rule, 'wait_random_range', 0.3)
                            wait_time = wait_time + random.uniform(-wait_range, wait_range)
                            wait_time = max(0, wait_time)
                        if wait_time > 0:
                            if self._stop_event.wait(timeout=wait_time):
                                break

                    i += 1

                # 중지 이벤트 체크 - while 루프도 종료
                if self._stop_event.is_set():
                    break

                # 반복 완료 로그
                if total_repeat_count > 1:
                    logger.info(f"{_GREEN}▶ 반복 {current_repeat}/{total_repeat_count} 완료{_RESET}")

            # 완료
            if not self._stop_event.is_set():
                self._state = ExecutionState.COMPLETED
                success_count = sum(1 for r in self._results if r.success)
                total_count = len(self._results)
                logger.info(f"{_GREEN}{'═'*50}{_RESET}")
                if total_repeat_count > 1:
                    logger.info(f"{_GREEN}★ 완료! ({success_count}/{total_count} 성공, 총 {total_repeat_count}회 반복){_RESET}")
                else:
                    logger.info(f"{_GREEN}★ 완료! ({success_count}/{total_count} 성공){_RESET}")
                logger.info(f"{_GREEN}{'═'*50}{_RESET}")
                self._update_progress(f"완료 ({success_count}/{total_count} 성공)")
                if self._on_complete:
                    self._on_complete(True, f"자동화 실행 완료: {success_count}/{total_count} 성공")

        except Exception as e:
            logger.error(f"{_RED}✗ 실행 오류: {e}{_RESET}")
            self._state = ExecutionState.FAILED
            self._update_progress(f"실행 실패: {e}")
            if self._on_complete:
                self._on_complete(False, str(e))

    def _trigger_search_region_for_rule(self, rule: AutomationRule) -> Optional[list]:
        """트리거 좌표가 있으면 해당 좌표 주변만 검색한다."""
        trigger_x = getattr(rule, "trigger_x", None)
        trigger_y = getattr(rule, "trigger_y", None)
        if trigger_x is None or trigger_y is None:
            return None
        try:
            return self._radius_to_region(int(trigger_x), int(trigger_y), TRIGGER_COORD_SEARCH_RADIUS)
        except (TypeError, ValueError):
            return None

    def _image_search_region_for_rule(self, rule: AutomationRule) -> Optional[list]:
        """이미지 액션의 검색 범위를 search_region 우선으로 계산한다."""
        if rule is None:
            return None

        search_region = getattr(rule, "search_region", None)
        if search_region is not None:
            return list(search_region) if isinstance(search_region, (list, tuple)) else search_region

        search_radius = getattr(rule, "search_radius", 0) or 0
        action_x = getattr(rule, "action_x", None)
        action_y = getattr(rule, "action_y", None)
        if search_radius > 0 and action_x is not None and action_y is not None:
            return self._radius_to_region(action_x, action_y, search_radius)

        return None

    @staticmethod
    def _normalize_search_region(search_region, screen_w: int, screen_h: int) -> Tuple[Optional[list], bool]:
        """검색영역을 화면 안으로 정규화한다. 두 번째 값은 명시 영역 여부."""
        if search_region is None:
            return None, False
        if not isinstance(search_region, (list, tuple)) or len(search_region) != 4:
            logger.warning(f"검색영역 형식 오류: {search_region}")
            return None, True
        try:
            x1, y1, x2, y2 = [int(round(float(v))) for v in search_region]
        except (TypeError, ValueError):
            logger.warning(f"검색영역 좌표 오류: {search_region}")
            return None, True

        x1, x2 = sorted((x1, x2))
        y1, y2 = sorted((y1, y2))
        x1 = max(0, min(int(screen_w), x1))
        y1 = max(0, min(int(screen_h), y1))
        x2 = max(0, min(int(screen_w), x2))
        y2 = max(0, min(int(screen_h), y2))

        if x2 <= x1 or y2 <= y1:
            logger.warning(f"검색영역이 유효하지 않음: {search_region}")
            return None, True

        return [x1, y1, x2, y2], True

    @staticmethod
    def _point_in_search_region(x: int, y: int, search_region) -> bool:
        region, explicit = RuleExecutor._normalize_search_region(search_region, 10**9, 10**9)
        if not explicit or region is None:
            return True
        x1, y1, x2, y2 = region
        return x1 <= int(x) <= x2 and y1 <= int(y) <= y2

    def _execute_trigger_missing_keys(
        self,
        rule: AutomationRule,
        step_prefix: str = "",
        *,
        keys_attr: str = "trigger_missing_keys",
        repeat_count_attr: str = "trigger_missing_key_repeat_count",
        repeat_delay_attr: str = "trigger_missing_key_repeat_delay",
        repeat_delay_random_attr: str = "trigger_missing_key_repeat_delay_random",
        repeat_delay_range_attr: str = "trigger_missing_key_repeat_delay_random_range",
        log_label: str = "트리거 미감지 종료 전 키입력",
    ) -> bool:
        """트리거 미감지 처리 직전에 지정 키를 반복 입력한다."""
        keys = [
            str(key).strip().lower()
            for key in (getattr(rule, keys_attr, None) or [])
            if str(key).strip()
        ]
        if not keys:
            return True
        if self._stop_event.is_set():
            return False

        try:
            repeat_count = max(1, int(getattr(rule, repeat_count_attr, 1) or 1))
        except (TypeError, ValueError):
            repeat_count = 1
        try:
            repeat_delay = max(0.0, float(getattr(rule, repeat_delay_attr, 0.5) or 0.0))
        except (TypeError, ValueError):
            repeat_delay = 0.5
        try:
            repeat_delay_range = max(
                0.0,
                float(getattr(rule, repeat_delay_range_attr, 0.3) or 0.0),
            )
        except (TypeError, ValueError):
            repeat_delay_range = 0.3
        repeat_delay_random = bool(getattr(rule, repeat_delay_random_attr, False))

        input_ctrl = get_input_controller()
        key_label = " + ".join(key.upper() for key in keys)
        logger.info(
            f"{_YELLOW}{step_prefix}{log_label}: "
            f"{key_label} x{repeat_count} delay={repeat_delay:.2f}s random={repeat_delay_random}{_RESET}"
        )
        for index in range(repeat_count):
            if self._stop_event.is_set():
                return False
            try:
                if len(keys) == 1:
                    ok = input_ctrl.press(keys[0])
                else:
                    ok = input_ctrl.hotkey(*keys)
                if ok is False:
                    logger.warning(
                        f"{_YELLOW}{step_prefix}{log_label} 실패: "
                        f"{key_label} ({index + 1}/{repeat_count}){_RESET}"
                    )
                    return False
                logger.info(
                    f"{_YELLOW}{step_prefix}{log_label} 완료: "
                    f"{key_label} ({index + 1}/{repeat_count}){_RESET}"
                )
            except Exception as e:
                logger.warning(
                    f"{_YELLOW}{step_prefix}{log_label} 예외: "
                    f"{key_label} ({index + 1}/{repeat_count}, {e}){_RESET}"
                )
                return False
            if index < repeat_count - 1:
                actual_delay = repeat_delay
                if repeat_delay_random:
                    actual_delay = max(0.0, repeat_delay + random.uniform(-repeat_delay_range, repeat_delay_range))
                if actual_delay > 0 and self._stop_event.wait(actual_delay):
                    return False
        return True

    def _handle_trigger_gate(
        self,
        rule: AutomationRule,
        start_time: datetime,
        step_num: str = "",
        can_rewind_previous: bool = False,
    ) -> Optional[RuleExecutionResult]:
        """트리거 이미지 대기/재생목록 종료 옵션을 공통 처리한다."""
        trigger_image = getattr(rule, "trigger_image", None)
        if not trigger_image:
            return None

        trigger_path = Path(trigger_image)
        if not trigger_path.exists():
            return self._make_result(rule, False, f"트리거 이미지 파일 없음: {trigger_path.name}", start_time)

        trigger_confidence = rule.confidence if rule.confidence > 0 else 0.65
        step_prefix = f"[{step_num}] " if step_num else ""
        stop_playlist = bool(getattr(rule, "stop_playlist_on_trigger_missing", False))
        rewind_previous = bool(getattr(rule, "rewind_previous_on_trigger_missing", False))
        trigger_timeout = PLAYLIST_SKIP_TRIGGER_TIMEOUT_SECONDS if (stop_playlist or rewind_previous) else 0.0
        if rewind_previous and stop_playlist:
            mode_desc = f"{trigger_timeout:.1f}초 후 이전 액션 재시도, 횟수 초과 시 재생목록 종료"
        elif rewind_previous:
            mode_desc = f"{trigger_timeout:.1f}초 후 이전 액션 재시도"
        elif stop_playlist:
            mode_desc = f"{trigger_timeout:.1f}초 후 재생목록 종료"
        else:
            mode_desc = "무제한"
        search_region = self._trigger_search_region_for_rule(rule)

        logger.info(f"{_YELLOW}{step_prefix}⏳ 트리거 대기 중... ({mode_desc}){_RESET}")
        if search_region:
            logger.debug(f"[트리거] 검색범위={search_region}")
        self._update_progress(f"{step_prefix}트리거 대기 중: {rule.description}")

        trigger_location = self._wait_for_trigger(
            str(trigger_path),
            confidence=trigger_confidence,
            timeout=trigger_timeout,
            search_region=search_region,
        )

        if trigger_location is None:
            if self._stop_event.is_set():
                return self._make_result(rule, False, "트리거 이미지 대기 중 중지됨", start_time)

            if rewind_previous:
                try:
                    max_rewinds = max(1, int(getattr(rule, "trigger_missing_rewind_count", 1) or 1))
                except (TypeError, ValueError):
                    max_rewinds = 1
                used_rewinds = self._trigger_missing_rewind_attempts.get(rule.rule_id, 0)
                if can_rewind_previous and used_rewinds < max_rewinds:
                    self._trigger_missing_rewind_attempts[rule.rule_id] = used_rewinds + 1
                    keys_ok = self._execute_trigger_missing_keys(
                        rule,
                        step_prefix,
                        keys_attr="trigger_missing_rewind_keys",
                        repeat_count_attr="trigger_missing_rewind_key_repeat_count",
                        repeat_delay_attr="trigger_missing_rewind_key_repeat_delay",
                        repeat_delay_random_attr="trigger_missing_rewind_key_repeat_delay_random",
                        repeat_delay_range_attr="trigger_missing_rewind_key_repeat_delay_random_range",
                        log_label="트리거 미감지 종료 전 키입력(전 액션 복귀)",
                    )
                    if not keys_ok:
                        if self._stop_event.is_set():
                            return self._make_result(rule, False, "트리거 미감지 전 액션 복귀 전 키입력 중 중지됨", start_time)
                        return self._make_result(rule, False, "트리거 미감지 전 액션 복귀 전 키입력 실패", start_time)
                    try:
                        rewind_delay = max(0.0, float(getattr(rule, "trigger_missing_rewind_delay", 0.5) or 0.0))
                    except (TypeError, ValueError):
                        rewind_delay = 0.5
                    if bool(getattr(rule, "trigger_missing_rewind_delay_random", False)):
                        try:
                            rewind_range = max(
                                0.0,
                                float(getattr(rule, "trigger_missing_rewind_delay_random_range", 0.3) or 0.0),
                            )
                        except (TypeError, ValueError):
                            rewind_range = 0.3
                        rewind_delay = max(0.0, rewind_delay + random.uniform(-rewind_range, rewind_range))
                    message = (
                        f"트리거 이미지 없음 → 이전 액션으로 이동 "
                        f"({used_rewinds + 1}/{max_rewinds}, {trigger_path.name})"
                    )
                    return self._make_result(
                        rule,
                        True,
                        message,
                        start_time,
                        rewind_previous_action=True,
                        rewind_delay=rewind_delay,
                    )
                logger.warning(
                    f"{_YELLOW}{step_prefix}트리거 미감지 이전 액션 이동 불가/횟수초과: "
                    f"{used_rewinds}/{max_rewinds}, can_rewind={can_rewind_previous}{_RESET}"
                )
                if not stop_playlist:
                    message = (
                        f"트리거 미감지 이전 액션 이동 불가/횟수초과 "
                        f"({used_rewinds}/{max_rewinds}, can_rewind={can_rewind_previous})"
                    )
                    return self._make_result(rule, False, message, start_time)

            if stop_playlist:
                keys_ok = self._execute_trigger_missing_keys(rule, step_prefix)
                if not keys_ok:
                    if self._stop_event.is_set():
                        return self._make_result(rule, False, "트리거 미감지 종료 전 키입력 중 중지됨", start_time)
                    return self._make_result(rule, False, "트리거 미감지 종료 전 키입력 실패", start_time)
                message = (
                    f"{PLAYLIST_SKIP_TRIGGER_MISSING}: "
                    f"트리거 이미지 없음 ({trigger_path.name}, {trigger_timeout:.1f}초)"
                )
                logger.warning(f"{_YELLOW}{step_prefix}⏭ {message}{_RESET}")
                return self._make_result(
                    rule,
                    True,
                    message,
                    start_time,
                    skip_current_playlist=True,
                )

            return self._make_result(rule, False, "트리거 이미지 대기 중 중지됨", start_time)

        self._trigger_missing_rewind_attempts.pop(rule.rule_id, None)
        self._prepare_for_click_after_trigger()
        return None

    @staticmethod
    def _target_images_for_rule(rule: AutomationRule) -> List[str]:
        """Return primary + multi images in OR-search order without duplicates."""
        images: List[str] = []
        seen = set()
        raw_images = []
        primary = getattr(rule, "target_image", None)
        if primary:
            raw_images.append(primary)
        raw_images.extend(getattr(rule, "target_images", None) or [])

        for image_path in raw_images:
            if not image_path:
                continue
            text_path = str(image_path)
            if text_path in seen:
                continue
            images.append(text_path)
            seen.add(text_path)
        return images

    def _execute_rule_with_retry(
        self,
        rule: AutomationRule,
        next_target_images: Optional[List[str]] = None,
        max_retries: int = 3,
        next_rule: Optional[AutomationRule] = None,
        step_num: str = "",
        can_rewind_previous: bool = False,
    ) -> RuleExecutionResult:
        """
        규칙 실행 + 다음 이미지 확인 + 재시도

        클릭 동작 후 다음 이미지가 나타나는지 확인합니다.
        나타나지 않으면 재시도합니다.
        next_rule이 skip_on_not_found=True면 wait_after 시간만 대기.
        """
        start_time = datetime.now()
        if isinstance(next_target_images, str):
            next_target_images = [next_target_images]
        else:
            next_target_images = [p for p in (next_target_images or []) if p]

        # 화면 안정화 대기 (이전 액션 효과가 반영될 시간)
        time.sleep(0.2)

        trigger_result = self._handle_trigger_gate(
            rule,
            start_time,
            step_num,
            can_rewind_previous=can_rewind_previous,
        )
        if trigger_result is not None:
            return trigger_result

        # 클릭 계열 동작인지 확인
        is_click_action = rule.action_type in ["click", "double_click", "right_click"]

        # 반복 횟수
        click_until_disappears = bool(
            is_click_action
            and (getattr(rule, "target_image", None) or getattr(rule, "target_images", None))
            and getattr(rule, "click_until_image_disappears", False)
        )
        repeat_count = getattr(rule, 'repeat_count', 1)
        if click_until_disappears:
            repeat_count = 1
        if repeat_count < 1:
            repeat_count = 1

        for attempt in range(max_retries):
            if self._stop_event.is_set():
                return self._make_result(rule, False, "실행 중지됨", start_time)

            # 반복 실행
            result = None
            for rep in range(repeat_count):
                if self._stop_event.is_set():
                    return self._make_result(rule, False, "실행 중지됨", start_time)

                # 일시정지 체크
                if self._wait_for_resume():
                    return self._make_result(rule, False, "실행 중지됨", start_time)

                if repeat_count > 1:
                    logger.info(f"{_CYAN}  [반복 {rep + 1}/{repeat_count}] {rule.description or rule.action_type}{_RESET}")

                # 규칙 실행
                result = self._execute_rule(rule, step_num=step_num)

                if not result.success:
                    break  # 실패하면 반복 중단

                # 마지막 반복이 아니면 반복 대기시간 적용
                if rep < repeat_count - 1:
                    repeat_delay = getattr(rule, 'repeat_delay', 0.5)
                    if repeat_delay > 0:
                        # 랜덤 대기시간 적용
                        if getattr(rule, 'repeat_delay_random', False):
                            delay_range = getattr(rule, 'repeat_delay_random_range', 0.3)
                            actual_delay = max(0, repeat_delay + random.uniform(-delay_range, delay_range))
                        else:
                            actual_delay = repeat_delay
                        time.sleep(actual_delay)

            if not result.success:
                logger.warning(f"{_YELLOW}  ✗ 동작 실패 (시도 {attempt + 1}/{max_retries}): {result.message}{_RESET}")
                if attempt < max_retries - 1:
                    time.sleep(0.5)
                    continue
                return result

            # 클릭 동작이고 다음 타겟 이미지가 있으면 확인 (스킵된 경우 제외)
            is_skipped = "스킵됨" in result.message if result.message else False
            if is_click_action and next_target_images and not is_skipped:
                check_interval = 0.5
                waited = 0.0
                # 다음 액션에 스킵 설정이 있으면 wait_after 시간만 대기
                next_skip = getattr(next_rule, 'skip_on_not_found', False) if next_rule else False
                next_wait = getattr(next_rule, 'wait_after', 0) if next_rule else 0
                next_desc = getattr(next_rule, 'description', '') if next_rule else ''
                step_prefix = f"[{step_num}] " if step_num else ""
                logger.debug(f"  [DEBUG] 다음액션: {next_desc}, skip={next_skip}, wait_after={next_wait}")
                if next_skip and next_wait > 0:
                    max_wait_time = next_wait
                    logger.info(f"{_YELLOW}{step_prefix}⏳ 다음 화면 확인 중... ({max_wait_time:.0f}초){_RESET}")
                else:
                    max_wait_time = 0  # 무제한 대기
                    logger.info(f"{_YELLOW}{step_prefix}⏳ 다음 화면 확인 중... (무제한){_RESET}")

                while max_wait_time <= 0 or waited < max_wait_time:
                    if self._stop_event.is_set():
                        return self._make_result(rule, False, "실행 중지됨", start_time)

                    # 일시정지 체크
                    if self._wait_for_resume():
                        return self._make_result(rule, False, "실행 중지됨", start_time)

                    # 이미지 검색 시작 로그 (첫 번째만)
                    # "다음 화면 대기"는 화면 전환 확인용이므로 낮은 임계값(0.45) 사용
                    # 사용자가 설정한 인식률은 실제 액션(모니터링, 감시 등)에만 적용
                    next_confidence = NEXT_SCREEN_CONFIDENCE

                    # 다음 액션의 검색 범위 계산 (search_region 우선, 없으면 search_radius)
                    next_search_region = self._image_search_region_for_rule(next_rule) if next_rule else None

                    if waited == 0:
                        logger.debug(
                            f"[다음화면대기] 인식률=45% (고정), 검색범위={next_search_region}, "
                            f"이미지={len(next_target_images)}개"
                        )

                    search_start = time.time()
                    location = None
                    for image_path in next_target_images:
                        location = self._find_image_on_screen(
                            image_path,
                            next_confidence,
                            search_region=next_search_region,
                        )
                        if location:
                            break
                    search_time = time.time() - search_start

                    # 검색이 오래 걸리면 로그
                    if search_time > 3.0:
                        logger.debug(f"이미지 검색 {search_time:.1f}초 소요")
                    if location:
                        return self._make_result(rule, True, f"{result.message}", start_time)

                    if self._stop_event.wait(check_interval):
                        return self._make_result(rule, False, "실행 중지됨", start_time)
                    waited += check_interval

                    # 10초마다 로그 출력
                    if waited % 10 < check_interval and waited > 0:
                        if max_wait_time > 0:
                            logger.info(f"  ⏳ 다음 화면 대기... {waited:.0f}초 (최대 {max_wait_time:.0f}초)")
                        else:
                            logger.info(f"  ⏳ 다음 화면 대기... {waited:.0f}초 (무제한)")

                # 타임아웃 도달 = next_skip=True (max_wait_time>0) 경우만 가능
                # next_skip=False이면 max_wait_time=0(무제한)이므로 while에서 빠져나오지 않음
                if self._stop_event.is_set():
                    return self._make_result(rule, False, "실행 중지됨", start_time)
                logger.info(f"{_YELLOW}{self._step_prefix}⏭ 다음 화면 스킵 ({max_wait_time:.1f}초 대기 후){_RESET}")

            # 클릭이 아니거나 다음 이미지가 없으면 바로 성공
            return result

        return self._make_result(rule, False, "최대 재시도 횟수 초과", start_time)

    def _execute_rule(self, rule: AutomationRule, step_num: str = "") -> RuleExecutionResult:
        """단일 규칙 실행"""
        start_time = datetime.now()
        step_prefix = f"[{step_num}] " if step_num else ""

        # 중지 체크
        if self._stop_event.is_set():
            return self._make_result(rule, False, "실행 중지됨", start_time)

        try:
            rule_type = rule.rule_type

            if rule_type == RuleType.WAIT_FOR_IMAGE.value:
                # 이미지가 나타날 때까지 대기
                success, msg = self._wait_for_image(
                    rule.trigger_image,
                    rule.timeout,
                    rule.confidence,
                    disappear=False,
                    verify_color=bool(getattr(rule, "verify_image_color", False)),
                    verify_brightness=bool(getattr(rule, "verify_image_brightness", False)),
                )
                return self._make_result(rule, success, msg, start_time)

            elif rule_type == RuleType.WAIT_FOR_DISAPPEAR.value:
                # 이미지가 사라질 때까지 대기
                success, msg = self._wait_for_image(
                    rule.trigger_image,
                    rule.timeout,
                    rule.confidence,
                    disappear=True,
                    verify_color=bool(getattr(rule, "verify_image_color", False)),
                    verify_brightness=bool(getattr(rule, "verify_image_brightness", False)),
                )
                return self._make_result(rule, success, msg, start_time)

            elif rule_type == RuleType.CLICK_ON_APPEAR.value:
                # 이미지가 나타나면 클릭
                if rule.target_image:
                    location = self._find_image_on_screen(
                        rule.target_image,
                        rule.confidence,
                        verify_color=bool(getattr(rule, "verify_image_color", False)),
                        verify_brightness=bool(getattr(rule, "verify_image_brightness", False)),
                    )
                    if location:
                        self._click_at(location[0], location[1])
                        return self._make_result(rule, True, "이미지 클릭 완료", start_time)
                    else:
                        return self._make_result(rule, False, "대상 이미지를 찾을 수 없습니다", start_time)
                elif rule.action_x is not None and rule.action_y is not None:
                    self._click_at(rule.action_x, rule.action_y)
                    return self._make_result(rule, True, "좌표 클릭 완료", start_time)
                else:
                    return self._make_result(rule, False, "클릭 대상 없음", start_time)

            elif rule_type == RuleType.FIXED_SEQUENCE.value:
                # 고정 시퀀스 (기존 액션 실행)
                return self._execute_fixed_action(rule, start_time)

            elif rule_type == RuleType.TYPE_TEXT.value:
                # 텍스트 입력 (한글 지원 - 클립보드 사용)
                if rule.action_text:
                    typing_random = getattr(rule, 'typing_random', False)
                    typing_delay = getattr(rule, 'typing_delay', 0.1)
                    typing_delay_range = getattr(rule, 'typing_delay_range', 0.05)
                    self._type_text_with_clipboard(rule.action_text, typing_random, typing_delay, typing_delay_range)
                    return self._make_result(rule, True, "텍스트 입력 완료", start_time)
                else:
                    return self._make_result(rule, False, "입력할 텍스트 없음", start_time)

            elif rule_type == RuleType.HOTKEY.value:
                # 단축키
                input_ctrl = get_input_controller()
                key_events = getattr(rule, "action_key_events", None) or []
                if key_events:
                    if not input_ctrl.replay_key_events(key_events):
                        return self._make_result(rule, False, "기록 키 실행 실패", start_time)
                    return self._make_result(rule, True, "기록 키 실행 완료", start_time)
                if rule.action_keys:
                    keys = [k.lower() for k in rule.action_keys]
                    ok = input_ctrl.hotkey(*keys)
                    if ok is False:
                        logger.warning(f"{_YELLOW}{step_prefix}⚠ 단축키 전송 실패: {'+'.join(keys)}{_RESET}")
                        return self._make_result(rule, False, "단축키 전송 실패", start_time)
                    return self._make_result(rule, True, "단축키 실행 완료", start_time)
                else:
                    return self._make_result(rule, False, "단축키 없음", start_time)

            elif rule_type == RuleType.MONITOR.value:
                # 모니터링 규칙 (트리거 시 실행)
                if rule.target_image:
                    location = self._find_image_on_screen(
                        rule.target_image,
                        rule.confidence,
                        verify_color=bool(getattr(rule, "verify_image_color", False)),
                        verify_brightness=bool(getattr(rule, "verify_image_brightness", False)),
                    )
                    if location:
                        self._click_at(location[0], location[1])
                        return self._make_result(rule, True, "모니터링 규칙 실행", start_time)
                return self._make_result(rule, True, "모니터링 확인", start_time)

            else:
                return self._make_result(rule, False, f"알 수 없는 규칙 유형: {rule_type}", start_time)

        except Exception as e:
            logger.error(f"규칙 실행 오류: {e}")
            return self._make_result(rule, False, str(e), start_time)

    def _execute_fixed_action(
        self,
        rule: AutomationRule,
        start_time: datetime,
    ) -> RuleExecutionResult:
        """고정 액션 실행"""
        # 중지 체크
        if self._stop_event.is_set():
            return self._make_result(rule, False, "실행 중지됨", start_time)

        action_type = rule.action_type

        try:
            if action_type == "wait":
                wait_duration = getattr(rule, 'duration', None)
                if wait_duration is None:
                    try:
                        wait_duration = float(rule.action_text) if rule.action_text else 0.0
                    except (ValueError, TypeError):
                        wait_duration = 0.0
                wait_duration = max(0.0, float(wait_duration or 0.0))
                if wait_duration > 0:
                    logger.info(f"{_CYAN}{self._step_prefix}⏳ 대기 {wait_duration:.2f}초{_RESET}")
                    time.sleep(wait_duration)
                return self._make_result(rule, True, "대기 완료", start_time)

            if action_type in ["click", "double_click", "right_click"]:
                click_x, click_y = None, None
                click_method = "없음"

                # 이미지 인식 - 이미지가 나타날 때까지 무한 대기
                # 기본 이미지 + 멀티이미지를 모두 검색 (OR 조건)
                all_target_images = self._target_images_for_rule(rule)

                if all_target_images:

                    # 이미지 파일 존재 확인 (최소 하나는 있어야 함)
                    valid_images = [p for p in all_target_images if Path(p).exists()]
                    if not valid_images:
                        img_names = [Path(p).name for p in all_target_images]
                        logger.error(f"{_RED}{self._step_prefix}✗ 이미지 파일 없음: {', '.join(img_names)}{_RESET}")
                        return self._make_result(rule, False, f"이미지 파일 없음: {', '.join(img_names)}", start_time)

                    locations = []
                    found_image = None
                    wait_count = 0
                    click_until_disappears = bool(getattr(rule, "click_until_image_disappears", False))
                    disappear_absent_misses = 0
                    skip_on_not_found = getattr(rule, 'skip_on_not_found', False)
                    # 스킵 모드: wait_after 타임아웃 적용 / 일반: 무제한 대기
                    # wait_after <= 0이면 첫 검색 실패 시 즉시 스킵 (무한 대기 방지)
                    skip_timeout = rule.wait_after if skip_on_not_found else 0
                    search_start = time.time()
                    if skip_on_not_found:
                        logger.debug(f"  [DEBUG] 현재액션 스킵설정: wait_after={rule.wait_after}초")

                    # 이미지가 나타날 때까지 대기
                    while not locations:
                        # 중지 체크
                        if self._stop_event.is_set():
                            return self._make_result(rule, False, "실행 중지됨", start_time)

                        # 스킵 모드일 때 타임아웃 체크
                        # skip_timeout <= 0이면 첫 검색 실패 시 즉시 스킵 (무한 대기 방지)
                        if skip_on_not_found and skip_timeout > 0:
                            elapsed = time.time() - search_start
                            if elapsed >= skip_timeout:
                                logger.info(f"{_YELLOW}{self._step_prefix}⏭ 스킵: 이미지 못찾음 ({skip_timeout:.1f}초 대기 후 스킵){_RESET}")
                                return self._make_result(rule, True, f"스킵됨 (이미지 없음, {skip_timeout:.1f}초 대기)", start_time)

                        # 일시정지 대기
                        if self._wait_for_resume():
                            return self._make_result(rule, False, "실행 중지됨", start_time)

                        # 사용자 개입 확인 (이미지 대기 중에도)
                        if self._check_user_intervention():
                            self._wait_after_intervention()
                            if self._stop_event.is_set():
                                return self._make_result(rule, False, "실행 중지됨", start_time)

                        # 검색 전 마우스 이동 (hover 효과 방지)
                        mouse_moved_for_search = False
                        original_mouse_pos = None
                        if getattr(rule, 'move_mouse_before_search', False):
                            try:
                                original_mouse_pos = pyautogui.position()
                                # 화면 왼쪽 하단으로 이동
                                screen_w, screen_h = pyautogui.size()
                                safe_x, safe_y = 0, screen_h - 1
                                pyautogui.moveTo(safe_x, safe_y, duration=0)
                                mouse_moved_for_search = True
                                time.sleep(0.15)  # hover 효과 사라질 시간
                            except Exception:
                                pass

                        # 모든 타겟 이미지 검색 (OR 조건)
                        found_target = self._find_rule_image_click_target(rule, valid_images)
                        if found_target:
                            click_x = found_target["x"]
                            click_y = found_target["y"]
                            found_conf = found_target["confidence"]
                            found_image = found_target["image"]
                            click_method = found_target["method"]
                            locations = found_target["locations"]

                        # 검색 후 마우스 원위치 복원
                        if mouse_moved_for_search and original_mouse_pos:
                            try:
                                pyautogui.moveTo(original_mouse_pos[0], original_mouse_pos[1], duration=0)
                            except Exception:
                                pass

                        if not locations:
                            elapsed = time.time() - search_start
                            if click_until_disappears:
                                disappear_absent_misses += 1
                                if disappear_absent_misses >= IMAGE_CLICK_UNTIL_DISAPPEAR_MISS_CONFIRM:
                                    logger.info(
                                        f"{_GREEN}{self._step_prefix}✓ 이미지 없음 확인 → 반복 클릭 종료 "
                                        f"({disappear_absent_misses}회 확인){_RESET}"
                                    )
                                    self._mark_child_rules_handled_by_parent(rule)
                                    return self._make_result(rule, True, "이미지 없음 (사라짐 확인)", start_time)
                                time.sleep(0.2)
                                continue

                            # skip_on_not_found일 때만 타임아웃 체크 (일반 모드는 무제한 대기)
                            # skip_timeout <= 0이면 첫 검색 실패 시 즉시 스킵
                            if skip_on_not_found and (skip_timeout <= 0 or elapsed >= skip_timeout):
                                logger.info(f"{_YELLOW}{self._step_prefix}⏭ 스킵: 이미지 못찾음 ({elapsed:.1f}초 대기 후){_RESET}")
                                return self._make_result(rule, True, f"스킵됨 (이미지 없음, {elapsed:.1f}초 대기)", start_time)

                            wait_count += 1
                            if wait_count % 20 == 1:  # 10초마다 로그
                                if skip_on_not_found and skip_timeout > 0:
                                    remaining = skip_timeout - elapsed
                                    skip_info = f" (타임아웃: {remaining:.0f}초 후)" if remaining < 60 else ""
                                else:
                                    skip_info = ""
                                logger.info(f"{_YELLOW}{self._step_prefix}⏳ 타겟 이미지 대기 중... {elapsed:.0f}초{skip_info}{_RESET}")
                            time.sleep(0.5)  # 0.5초마다 재검색

                    # 찾은 이미지 이름
                    found_name = Path(found_image).name if found_image else "이미지"

                    if len(locations) == 1:
                        logger.info(f"{_GREEN}{self._step_prefix}✓ 이미지 발견: {found_name} ({int(found_conf * 100)}%){_RESET}")
                    elif len(locations) > 1:
                        logger.info(f"{_GREEN}{self._step_prefix}✓ 이미지 발견: {found_name} ({int(found_conf * 100)}%){_RESET}")

                # 클릭 실행
                if click_x is not None and click_y is not None:
                    # 검색 범위가 설정된 이미지 액션은 범위 밖 클릭을 절대 진행하지 않는다.
                    rule_search_region = self._image_search_region_for_rule(rule)
                    if all_target_images and rule_search_region is not None and not self._point_in_search_region(click_x, click_y, rule_search_region):
                        logger.warning(
                            f"{_YELLOW}{self._step_prefix}⚠ 검색영역 밖 클릭 차단: "
                            f"click=({click_x},{click_y}) region={rule_search_region}{_RESET}"
                        )
                        return self._make_result(rule, False, "검색영역 밖 이미지 후보 차단", start_time)

                    # 클릭 전 사용자 개입 확인
                    if self._check_user_intervention():
                        self._wait_after_intervention()
                        if self._stop_event.is_set():
                            return self._make_result(rule, False, "실행 중지됨", start_time)

                    if getattr(rule, "click_until_image_disappears", False):
                        return self._execute_click_until_image_disappears(
                            rule,
                            valid_images,
                            action_type,
                            start_time,
                            first_target={
                                "x": click_x,
                                "y": click_y,
                                "confidence": found_conf,
                                "image": found_image,
                                "method": click_method,
                                "locations": locations,
                            },
                        )

                    return self._execute_click_at(
                        rule,
                        action_type,
                        int(click_x),
                        int(click_y),
                        start_time,
                        image_click=bool(all_target_images),
                    )

                logger.warning(f"{_YELLOW}  ⚠ 클릭 대상 없음 (이미지 필요){_RESET}")
                return self._make_result(rule, False, "클릭 대상 없음 (target_image 필요)", start_time)

            elif action_type == "drag":
                # 드래그 액션
                if rule.action_x is not None and rule.action_y is not None:
                    input_ctrl = get_input_controller()

                    # 드래그 소요 시간 (녹화된 값이 있으면 사용, 없으면 기본값)
                    drag_dur = getattr(rule, 'drag_duration', None)
                    if drag_dur is None or drag_dur <= 0:
                        drag_dur = self._mouse_duration  # 기본값 사용
                    else:
                        # 최소 0.1초, 최대 10초로 제한
                        drag_dur = max(0.1, min(drag_dur, 10.0))

                    # 드래그 끝 좌표 계산
                    if rule.drag_to_x is not None and rule.drag_to_y is not None:
                        # 명시적 종료 좌표가 있는 경우
                        input_ctrl.drag(rule.action_x, rule.action_y, rule.drag_to_x, rule.drag_to_y, duration=drag_dur)
                        logger.info(f"{_GREEN}{self._step_prefix}✓ 드래그 완료{_RESET}")
                        return self._make_result(rule, True, f"드래그 완료", start_time)
                    elif rule.action_text:
                        # action_text에서 드래그 방향/거리 파싱 시도
                        try:
                            # "dx,dy" 형식 지원 (예: "100,-50")
                            parts = rule.action_text.split(',')
                            if len(parts) == 2:
                                drag_dx = int(parts[0].strip())
                                drag_dy = int(parts[1].strip())
                                end_x = rule.action_x + drag_dx
                                end_y = rule.action_y + drag_dy
                                input_ctrl.drag(rule.action_x, rule.action_y, end_x, end_y, duration=drag_dur)
                                logger.info(f"{_GREEN}{self._step_prefix}✓ 드래그 완료{_RESET}")
                                return self._make_result(rule, True, f"드래그 완료", start_time)
                        except (ValueError, IndexError):
                            pass

                    # 기본값: 드래그 정보가 없으면 실패 반환
                    return self._make_result(rule, False, "드래그 종료 좌표 없음 (drag_to_x, drag_to_y 또는 action_text에 'dx,dy' 형식 필요)", start_time)

                return self._make_result(rule, False, "드래그 시작 좌표 없음", start_time)

            elif action_type == "type":
                if rule.action_text:
                    typing_random = getattr(rule, 'typing_random', False)
                    typing_delay = getattr(rule, 'typing_delay', 0.1)
                    typing_delay_range = getattr(rule, 'typing_delay_range', 0.05)
                    self._type_text_with_clipboard(rule.action_text, typing_random, typing_delay, typing_delay_range)
                    logger.info(f"{_GREEN}{self._step_prefix}✓ 텍스트 입력 완료{_RESET}")
                    return self._make_result(rule, True, "입력 완료", start_time)
                return self._make_result(rule, False, "입력할 텍스트 없음", start_time)

            elif action_type == "hotkey":
                input_ctrl = get_input_controller()
                key_events = getattr(rule, "action_key_events", None) or []
                if key_events:
                    if not input_ctrl.replay_key_events(key_events):
                        return self._make_result(rule, False, "기록 키 실패", start_time)
                    logger.info(f"{_GREEN}{self._step_prefix}✓ 기록 키 완료{_RESET}")
                    return self._make_result(rule, True, "기록 키 완료", start_time)
                if rule.action_keys:
                    keys = [k.lower() for k in rule.action_keys]
                    ok = input_ctrl.hotkey(*keys)
                    if ok is False:
                        logger.warning(f"{_YELLOW}{self._step_prefix}⚠ 단축키 전송 실패: {'+'.join(keys)}{_RESET}")
                        return self._make_result(rule, False, "단축키 전송 실패", start_time)
                    logger.info(f"{_GREEN}{self._step_prefix}✓ 단축키 완료: {'+'.join(keys).upper()}{_RESET}")
                    return self._make_result(rule, True, f"단축키 완료", start_time)
                return self._make_result(rule, False, "단축키 없음", start_time)

            elif action_type == "key_press":
                input_ctrl = get_input_controller()
                key_events = getattr(rule, "action_key_events", None) or []
                if key_events:
                    if not input_ctrl.replay_key_events(key_events):
                        return self._make_result(rule, False, "기록 키 입력 실패", start_time)
                    logger.info(f"{_GREEN}{self._step_prefix}✓ 기록 키 입력 완료{_RESET}")
                    return self._make_result(rule, True, "기록 키 입력 완료", start_time)
                if rule.action_keys:
                    keys = [str(key).lower().strip() for key in rule.action_keys if str(key).strip()]
                    if len(keys) == 1:
                        ok = input_ctrl.press(keys[0])
                    elif keys:
                        ok = input_ctrl.hotkey(*keys)
                    else:
                        ok = False
                    key_label = "+".join(keys).upper()
                    if ok is False:
                        logger.warning(f"{_YELLOW}{self._step_prefix}⚠ 키 입력 전송 실패: {key_label}{_RESET}")
                        return self._make_result(rule, False, "키 입력 전송 실패", start_time)
                    logger.info(f"{_GREEN}{self._step_prefix}✓ 키 입력 완료: {key_label}{_RESET}")
                    return self._make_result(rule, True, f"키 입력 완료", start_time)
                return self._make_result(rule, False, "키 없음", start_time)

            elif action_type == "scroll":
                scroll_amount = rule.scroll_amount if rule.scroll_amount != 0 else 0

                if scroll_amount == 0:
                    try:
                        if rule.action_text:
                            scroll_amount = int(rule.action_text)
                    except (ValueError, TypeError):
                        return self._make_result(rule, False, "스크롤 양이 지정되지 않음", start_time)

                if scroll_amount == 0:
                    return self._make_result(rule, False, "스크롤 양이 지정되지 않음", start_time)

                input_ctrl = get_input_controller()
                input_ctrl.scroll(scroll_amount, rule.action_x, rule.action_y)
                logger.info(f"{_GREEN}{self._step_prefix}✓ 스크롤 완료{_RESET}")
                return self._make_result(rule, True, f"스크롤 완료", start_time)

            elif action_type == "game_mode":
                # game_mode는 rule_id로 해당 config를 조회하여 실행
                config = None
                if hasattr(self, '_current_plan') and self._current_plan:
                    config = self._current_plan.game_modes.get(rule.rule_id)
                if config:
                    config._rule_id = rule.rule_id  # 맵 경로 생성 시 rule_id prefix용
                    logger.info(f"{_GREEN}{self._step_prefix}🎮 특화모드 실행: {config.name or '특화모드'}{_RESET}")
                    success = self.execute_game_mode(config)
                    return self._make_result(rule, success, "특화모드 완료" if success else "특화모드 실패", start_time)
                else:
                    return self._make_result(rule, False, "특화모드 설정 없음", start_time)

            else:
                return self._make_result(rule, False, f"알 수 없는 액션: {action_type}", start_time)

        except Exception as e:
            import traceback
            logger.error(f"{_RED}✗ 액션 실행 오류: {e}{_RESET}")
            logger.debug(f"traceback: {traceback.format_exc()}")
            return self._make_result(rule, False, f"예외: {str(e)}", start_time)

    def _wait_for_image(
        self,
        image_path: Optional[str],
        timeout: float,
        confidence: float,
        disappear: bool = False,
        verify_color: bool = False,
        verify_brightness: bool = False,
    ) -> tuple:
        """이미지 대기"""
        if not image_path:
            return (False, "대기할 이미지가 없습니다")

        start_time = time.time()
        check_interval = 0.5

        while True:
            if self._stop_event.is_set():
                return (False, "실행 중지됨")

            # 일시정지 대기 (중지 이벤트 주기적 체크)
            if self._wait_for_resume():
                return (False, "실행 중지됨")

            # 타임아웃 설정 시에만 체크 (timeout > 0)
            if timeout > 0 and (time.time() - start_time) >= timeout:
                mode = "사라짐" if disappear else "나타남"
                return (False, f"타임아웃: 이미지 {mode} 대기 실패 ({timeout}초)")

            location = self._find_image_on_screen(
                image_path,
                confidence,
                verify_color=verify_color,
                verify_brightness=verify_brightness,
            )

            if disappear:
                if location is None:
                    return (True, "이미지가 사라졌습니다")
            else:
                if location is not None:
                    return (True, "이미지가 나타났습니다")

            time.sleep(check_interval)

    def _type_text_with_clipboard(self, text: str, typing_random: bool = False,
                                    typing_delay: float = 0.1, typing_delay_range: float = 0.05) -> None:
        """클립보드를 사용하여 텍스트 입력 (한글 지원)"""
        if not text:
            return

        input_ctrl = get_input_controller()

        # ASCII만 있는 경우
        if text.isascii():
            if typing_random:
                # 랜덤 딜레이로 글자 하나씩 입력 (기본값 ± 범위)
                for char in text:
                    input_ctrl.type_text(char)
                    delay = typing_delay + random.uniform(-typing_delay_range, typing_delay_range)
                    delay = max(0, delay)  # 음수 방지
                    time.sleep(delay)
            else:
                input_ctrl.type_text(text, interval=self._typing_interval)
        else:
            # 한글 등 비ASCII 문자가 있으면 클립보드 사용
            if typing_random:
                # 랜덤 딜레이로 글자 하나씩 입력 (기본값 ± 범위)
                original_clipboard = None
                try:
                    try:
                        original_clipboard = pyperclip.paste()
                    except (OSError, pyperclip.PyperclipException):
                        pass

                    for char in text:
                        pyperclip.copy(char)
                        time.sleep(0.02)
                        input_ctrl.hotkey('ctrl', 'v')
                        delay = typing_delay + random.uniform(-typing_delay_range, typing_delay_range)
                        delay = max(0, delay)  # 음수 방지
                        time.sleep(delay)

                finally:
                    if original_clipboard is not None:
                        try:
                            time.sleep(0.05)
                            pyperclip.copy(original_clipboard)
                        except (OSError, pyperclip.PyperclipException):
                            pass
            else:
                original_clipboard = None
                try:
                    try:
                        original_clipboard = pyperclip.paste()
                    except (OSError, pyperclip.PyperclipException):
                        pass

                    pyperclip.copy(text)
                    time.sleep(0.05)
                    input_ctrl.hotkey('ctrl', 'v')
                    time.sleep(0.1)

                finally:
                    if original_clipboard is not None:
                        try:
                            time.sleep(0.05)
                            pyperclip.copy(original_clipboard)
                        except (OSError, pyperclip.PyperclipException):
                            pass

    def _find_image_on_screen(
        self,
        image_path: str,
        confidence: float = 0.8,
        search_region: Optional[list] = None,
        verify_color: bool = False,
        verify_brightness: bool = False,
    ) -> Optional[tuple]:
        """
        화면에서 이미지 찾기 (마스크 매칭)

        Otsu 이진화로 전경/배경을 분리하고, 전경 픽셀만 비교하여
        배경 변화에 강한 정확한 매칭을 수행합니다.

        search_region: 검색 영역 제한 [x1, y1, x2, y2] 또는 None (전체 화면)
        """
        func_start = time.time()
        screenshot_bgr = None

        # 중지 체크
        if self._stop_event.is_set():
            return None

        try:
            # 파일 존재 확인 (직접 확인 - 스레드 불필요)
            if not image_path:
                logger.debug(f"이미지 경로가 없습니다")
                return None

            try:
                if not Path(image_path).exists():
                    logger.warning(f"템플릿 파일 없음: {Path(image_path).name}")
                    return None
            except OSError as e:
                logger.warning(f"파일 확인 실패: {e}")
                return None

            # 중지 체크
            if self._stop_event.is_set():
                return None

            capture_start = time.time()
            screenshot_bgr = _grab_screen_bgr()
            if screenshot_bgr is None:
                logger.warning("화면 캡처 실패")
                return None
            capture_time = time.time() - capture_start
            if capture_time > 2.0:
                logger.debug(f"화면 캡처 지연: {capture_time:.1f}초")

            # 검색 영역 제한
            region_offset_x, region_offset_y = 0, 0
            h, w = screenshot_bgr.shape[:2]
            normalized_region, explicit_region = self._normalize_search_region(search_region, w, h)
            if explicit_region:
                if normalized_region is None:
                    return None
                x1, y1, x2, y2 = normalized_region
                screenshot_bgr = screenshot_bgr[y1:y2, x1:x2]
                region_offset_x, region_offset_y = x1, y1

            # 중지 체크
            if self._stop_event.is_set():
                return None

            # TM_CCOEFF_NORMED 매칭 (오탐률 낮음 — 절대 변경 금지)
            cached = _get_cached_template(image_path)
            if cached is None:
                logger.warning(f"템플릿 로드 실패: {Path(image_path).name}")
                return None
            tmpl_gray, th, tw = cached
            verify_visual = bool(verify_color or verify_brightness)
            template_bgr = _get_cached_template_bgr(image_path) if verify_visual else None
            if verify_visual and template_bgr is None:
                logger.warning(f"템플릿 컬러 로드 실패: {Path(image_path).name}")
                return None

            screen_gray = cv2.cvtColor(screenshot_bgr, cv2.COLOR_BGR2GRAY)
            sh, sw = screen_gray.shape[:2]
            if tw > sw or th > sh:
                return None

            best_match = None
            best_scale = 1.0
            for scale in _MULTISCALE_FACTORS:
                scaled_tmpl = _resize_template_gray(tmpl_gray, scale)
                if scaled_tmpl is None:
                    continue
                sth, stw = scaled_tmpl.shape[:2]
                if stw > sw or sth > sh or stw < 4 or sth < 4:
                    continue
                result = cv2.matchTemplate(screen_gray, scaled_tmpl, cv2.TM_CCOEFF_NORMED)
                time.sleep(0)  # GIL 해제
                _, max_val, _, max_loc = cv2.minMaxLoc(result)
                if best_match is None or max_val > best_match[2]:
                    best_match = (max_loc, stw, sth, float(max_val))
                    best_scale = scale
                if max_val >= confidence:
                    if verify_visual:
                        candidate_points = np.argwhere(result >= confidence)
                        if candidate_points.size:
                            scores = result[candidate_points[:, 0], candidate_points[:, 1]]
                            for idx in np.argsort(scores)[::-1][:25]:
                                row, col = candidate_points[idx]
                                if _passes_image_visual_verification(
                                    screenshot_bgr,
                                    template_bgr,
                                    int(col),
                                    int(row),
                                    stw,
                                    sth,
                                    verify_color=verify_color,
                                    verify_brightness=verify_brightness,
                                ):
                                    logger.debug(
                                        f"[이미지 검색] CCOEFF+visual: conf={float(scores[idx]):.2f}, "
                                        f"설정={confidence:.2f}, scale={scale:.2f} - {Path(image_path).name}"
                                    )
                                    final_x = int(col) + stw // 2 + region_offset_x
                                    final_y = int(row) + sth // 2 + region_offset_y
                                    return (final_x, final_y, float(scores[idx]))
                        continue
                    logger.debug(
                        f"[이미지 검색] CCOEFF: conf={max_val:.2f}, 설정={confidence:.2f}, scale={scale:.2f} - {Path(image_path).name}"
                    )
                    final_x = max_loc[0] + stw // 2 + region_offset_x
                    final_y = max_loc[1] + sth // 2 + region_offset_y
                    return (final_x, final_y, float(max_val))

            if best_match is not None:
                logger.debug(
                    f"[이미지 검색] CCOEFF: conf={best_match[2]:.2f}, 설정={confidence:.2f}, best_scale={best_scale:.2f} - {Path(image_path).name}"
                )

            return None

        except Exception as e:
            logger.error(f"이미지 검색 오류: {e}")
            return None
        finally:
            # 메모리 해제 (저사양 PC 지원)
            del screenshot_bgr

    def _wait_for_trigger(
        self,
        image_path: str,
        confidence: float = 0.65,
        timeout: float = 30.0,
        search_region: Optional[list] = None,
    ) -> Optional[tuple]:
        """
        트리거 이미지가 나타날 때까지 대기 (새로운 단순화된 구현)

        Args:
            image_path: 트리거 이미지 경로
            confidence: 신뢰도 임계값
            timeout: 최대 대기 시간 (초)

        Returns:
            발견 시 (center_x, center_y), 실패 시 None
        """
        if self._stop_event.is_set():
            return None

        try:
            # 파일 확인
            image_path = str(Path(image_path).resolve())
            if not Path(image_path).exists():
                logger.error(f"[트리거] 파일 없음: {Path(image_path).name}")
                return None

            # 템플릿 로드 (캐시 사용)
            cached = _get_cached_template(image_path)
            if cached is None:
                logger.error(f"[트리거] 이미지 로드 실패: {Path(image_path).name}")
                return None

            template_gray, h, w = cached

            timeout_desc = "무제한" if timeout <= 0 else f"최대 {timeout}초"
            region_desc = f", 검색범위={search_region}" if search_region else ""
            logger.info(f"{_YELLOW}⏳ 트리거 대기: {Path(image_path).name} ({timeout_desc}{region_desc}){_RESET}")

            check_interval = 0.2
            trigger_start = time.time()
            last_log_time = trigger_start

            while True:
                if self._stop_event.is_set():
                    return None

                elapsed = time.time() - trigger_start

                # 타임아웃 설정 시에만 체크 (timeout > 0)
                if timeout > 0 and elapsed >= timeout:
                    logger.error(f"{_RED}[트리거] ✗ 타임아웃 ({timeout}초){_RESET}")
                    return None

                # 일시정지 체크
                if self._wait_for_resume():
                    return None

                # 화면 캡처 및 매칭
                screenshot_bgr = None
                screenshot_gray = None
                result = None
                try:
                    screenshot_bgr = _grab_screen_bgr()
                    if screenshot_bgr is None:
                        time.sleep(check_interval)
                        continue
                    screenshot_gray = cv2.cvtColor(screenshot_bgr, cv2.COLOR_BGR2GRAY)

                    scr_h, scr_w = screenshot_gray.shape[:2]
                    region_offset_x, region_offset_y = 0, 0
                    normalized_region, explicit_region = self._normalize_search_region(search_region, scr_w, scr_h)
                    if explicit_region:
                        if normalized_region is None:
                            return None
                        x1, y1, x2, y2 = normalized_region
                        screenshot_gray = screenshot_gray[y1:y2, x1:x2]
                        region_offset_x, region_offset_y = x1, y1
                        scr_h, scr_w = screenshot_gray.shape[:2]

                    # 크기 체크: 템플릿이 화면보다 크면 스킵
                    if h > scr_h or w > scr_w:
                        logger.warning(f"[트리거] 템플릿({w}x{h})이 화면({scr_w}x{scr_h})보다 큼 - 스킵")
                        return None

                    best_match = None
                    best_scale = 1.0
                    best_w = w
                    best_h = h
                    for scale in _MULTISCALE_FACTORS:
                        scaled_tmpl = _resize_template_gray(template_gray, scale)
                        if scaled_tmpl is None:
                            continue
                        sth, stw = scaled_tmpl.shape[:2]
                        if stw > scr_w or sth > scr_h or stw < 4 or sth < 4:
                            continue
                        try:
                            result = cv2.matchTemplate(screenshot_gray, scaled_tmpl, cv2.TM_CCOEFF_NORMED)
                        except cv2.error as e:
                            logger.error(f"[트리거] 매칭 오류: {e}")
                            return None
                        time.sleep(0)  # GIL 해제
                        _, max_val, _, max_loc = cv2.minMaxLoc(result)
                        if best_match is None or max_val > best_match[0]:
                            best_match = (float(max_val), max_loc)
                            best_scale = scale
                            best_w = stw
                            best_h = sth
                        if max_val >= confidence:
                            best_match = (float(max_val), max_loc)
                            best_scale = scale
                            best_w = stw
                            best_h = sth
                            break

                    if best_match and best_match[0] >= confidence:
                        center_x = best_match[1][0] + best_w // 2 + region_offset_x
                        center_y = best_match[1][1] + best_h // 2 + region_offset_y
                        logger.info(f"{_GREEN}✓ 트리거 발견! ({elapsed:.1f}초 대기){_RESET}")
                        logger.debug(f"[트리거] 위치=({center_x}, {center_y}), 점수={best_match[0]:.2f}, scale={best_scale:.2f}")
                        return (center_x, center_y)
                finally:
                    # 메모리 해제 (저사양 PC 지원)
                    del screenshot_bgr, screenshot_gray, result

                time.sleep(check_interval)

                now = time.time()
                if now - last_log_time >= 10.0:
                    actual_elapsed = now - trigger_start
                    logger.info(f"{_YELLOW}⏳ 트리거 대기 중... {actual_elapsed:.0f}초{_RESET}")
                    last_log_time = now

        except Exception as e:
            logger.error(f"[트리거] 오류: {e}")
            return None

    def _prepare_for_click_after_trigger(self) -> None:
        """
        트리거 발견 후 클릭 준비
        - 화면 안정화 대기
        - 브라우저/타겟 윈도우 포커스 확보
        """
        user32 = ctypes.windll.user32

        # 1. 화면 안정화 대기
        logger.debug("[트리거→클릭] 화면 안정화 대기 (0.3초)")
        time.sleep(0.3)

        # 2. 마우스 캡처 해제 - 게임 충돌 방지를 위해 비활성화
        # user32.ReleaseCapture()
        # user32.ClipCursor(None)

        # 3. 현재 마우스 위치의 윈도우 포커스 (Alt 키 트릭)
        pt = ctypes.wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))

        user32.WindowFromPoint.restype = ctypes.wintypes.HWND
        hwnd = user32.WindowFromPoint(ctypes.wintypes.POINT(pt.x, pt.y))

        if hwnd:
            root_hwnd = user32.GetAncestor(hwnd, 2)
            if root_hwnd:
                hwnd = root_hwnd

            # Alt 키 트릭으로 포커스 강제 획득
            VK_MENU = 0x12
            KEYEVENTF_EXTENDEDKEY = 0x0001
            KEYEVENTF_KEYUP = 0x0002

            user32.keybd_event(VK_MENU, 0, KEYEVENTF_EXTENDEDKEY, 0)
            user32.SetForegroundWindow(hwnd)
            user32.keybd_event(VK_MENU, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0)

            time.sleep(0.1)
            logger.debug(f"[트리거→클릭] 포커스 준비 완료 (hwnd={hwnd})")

    def _find_all_images_on_screen(
        self,
        image_path: str,
        confidence: float = 0.9,
        search_radius: int = 0,
        center_x: int = None,
        center_y: int = None,
        search_region: list = None,
        verify_color: bool = False,
        verify_brightness: bool = False,
    ) -> List[tuple]:
        """
        화면에서 모든 일치하는 이미지 위치 찾기

        Args:
            search_radius: 검색 범위 (0=전체화면, >0=center_x/y 중심 반경 픽셀)
            center_x, center_y: 검색 중심 좌표 (search_radius > 0일 때 사용)
            search_region: 직사각형 검색 범위 [x1, y1, x2, y2] (search_radius보다 우선)

        Returns:
            List[tuple]: 발견된 모든 위치 [(x, y), ...]
        """
        screenshot_bgr = None
        screenshot_gray = None
        result = None

        if self._stop_event.is_set():
            return []

        try:
            if not image_path or not Path(image_path).exists():
                return []

            # 중지 체크
            if self._stop_event.is_set():
                return []

            # 화면 캡처. 캡처마다 daemon 스레드를 만들면 장시간 실행 시 누적될 수 있다.
            screenshot_bgr = _grab_screen_bgr()
            if screenshot_bgr is None:
                return []
            screenshot_gray = cv2.cvtColor(screenshot_bgr, cv2.COLOR_BGR2GRAY)
            screen_h, screen_w = screenshot_gray.shape

            # 중지 체크
            if self._stop_event.is_set():
                return []

            # 템플릿 로드 (캐시 사용)
            cached = _get_cached_template(image_path)
            if cached is None:
                return []
            template_gray, h, w = cached
            verify_visual = bool(verify_color or verify_brightness)
            template_bgr = _get_cached_template_bgr(image_path) if verify_visual else None
            if verify_visual and template_bgr is None:
                return []

            # 중지 체크
            if self._stop_event.is_set():
                return []

            # ROI 적용 (search_region 우선, 없으면 search_radius)
            roi_offset_x, roi_offset_y = 0, 0
            roi_x1, roi_y1, roi_x2, roi_y2 = 0, 0, 0, 0
            has_roi = False
            normalized_region, explicit_region = self._normalize_search_region(search_region, screen_w, screen_h)
            if explicit_region:
                if normalized_region is None:
                    return []
                roi_x1, roi_y1, roi_x2, roi_y2 = normalized_region
                has_roi = True
            elif search_radius > 0 and center_x is not None and center_y is not None:
                try:
                    radius = int(search_radius)
                    cx = int(center_x)
                    cy = int(center_y)
                except (TypeError, ValueError):
                    return []
                roi_x1 = max(0, cx - radius)
                roi_y1 = max(0, cy - radius)
                roi_x2 = min(screen_w, cx + radius)
                roi_y2 = min(screen_h, cy + radius)
                if roi_x2 <= roi_x1 or roi_y2 <= roi_y1:
                    return []
                has_roi = True

            # ROI가 설정된 경우 반드시 해당 영역 안에서만 검색한다.
            if has_roi:
                screenshot_gray = screenshot_gray[roi_y1:roi_y2, roi_x1:roi_x2]
                roi_offset_x, roi_offset_y = roi_x1, roi_y1
                search_bgr = screenshot_bgr[roi_y1:roi_y2, roi_x1:roi_x2]
            else:
                search_bgr = screenshot_bgr

            # 크기 체크: 템플릿이 화면보다 크면 스킵
            scr_h, scr_w = screenshot_gray.shape[:2]
            if h > scr_h or w > scr_w:
                logger.warning(f"템플릿({w}x{h})이 검색 영역({scr_w}x{scr_h})보다 큼 - 스킵")
                return []

            # 템플릿 매칭 (1배율 우선, 실패 시 멀티스케일 fallback)
            locations = []
            best_result = None
            best_tmpl_w = w
            best_tmpl_h = h
            best_scale = 1.0
            for scale in _MULTISCALE_FACTORS:
                scaled_tmpl = _resize_template_gray(template_gray, scale)
                if scaled_tmpl is None:
                    continue
                sth, stw = scaled_tmpl.shape[:2]
                if stw > scr_w or sth > scr_h or stw < 4 or sth < 4:
                    continue
                try:
                    result = cv2.matchTemplate(screenshot_gray, scaled_tmpl, cv2.TM_CCOEFF_NORMED)
                except cv2.error as e:
                    logger.error(f"템플릿 매칭 오류: {e}")
                    return []
                time.sleep(0)  # GIL 해제

                if self._stop_event.is_set():
                    return []

                _, max_val, _, _ = cv2.minMaxLoc(result)
                if best_result is None or max_val > best_result[0]:
                    best_result = (float(max_val), result)
                    best_tmpl_w = stw
                    best_tmpl_h = sth
                    best_scale = scale
                if max_val >= confidence:
                    best_result = (float(max_val), result)
                    best_tmpl_w = stw
                    best_tmpl_h = sth
                    best_scale = scale
                    break

            if best_result is None:
                return []

            result = best_result[1]
            logger.debug(
                f"[이미지 검색/all] conf={best_result[0]:.2f}, 설정={confidence:.2f}, scale={best_scale:.2f} - {Path(image_path).name}"
            )

            # 임계값 이상인 모든 위치 찾기
            loc = np.where(result >= confidence)

            for pt in zip(*loc[::-1]):
                if verify_visual and not _passes_image_visual_verification(
                    search_bgr,
                    template_bgr,
                    int(pt[0]),
                    int(pt[1]),
                    best_tmpl_w,
                    best_tmpl_h,
                    verify_color=verify_color,
                    verify_brightness=verify_brightness,
                ):
                    continue

                found_x = pt[0] + best_tmpl_w // 2 + roi_offset_x
                found_y = pt[1] + best_tmpl_h // 2 + roi_offset_y
                score = result[pt[1], pt[0]]

                # 중복 제거 (가까운 위치는 하나로)
                is_duplicate = False
                for existing in locations:
                    if abs(existing[0] - found_x) < best_tmpl_w // 2 and abs(existing[1] - found_y) < best_tmpl_h // 2:
                        is_duplicate = True
                        break

                if not is_duplicate:
                    locations.append((found_x, found_y, float(score)))

            return locations

        except Exception as e:
            logger.error(f"이미지 검색 오류: {e}")
            return []
        finally:
            # 메모리 해제 (저사양 PC 지원)
            del screenshot_bgr, screenshot_gray, result

    def _find_rule_image_click_target(self, rule: AutomationRule, valid_images: List[str]) -> Optional[Dict[str, Any]]:
        """규칙의 이미지 검색 설정을 적용해 클릭 대상 1개를 찾는다."""
        rule_search_region = self._image_search_region_for_rule(rule)
        has_rule_search_region = rule_search_region is not None
        verify_color = bool(getattr(rule, "verify_image_color", False))
        verify_brightness = bool(getattr(rule, "verify_image_brightness", False))

        for img_path in valid_images:
            locations = self._find_all_images_on_screen(
                img_path,
                rule.confidence,
                search_radius=0 if has_rule_search_region else getattr(rule, "search_radius", 0),
                center_x=rule.action_x,
                center_y=rule.action_y,
                search_region=rule_search_region,
                verify_color=verify_color,
                verify_brightness=verify_brightness,
            )
            if has_rule_search_region:
                before_filter = len(locations)
                locations = [
                    loc for loc in locations
                    if self._point_in_search_region(loc[0], loc[1], rule_search_region)
                ]
                if before_filter and not locations:
                    logger.warning(
                        f"{_YELLOW}{self._step_prefix}⚠ 검색영역 밖 이미지 후보 차단: "
                        f"{Path(img_path).name} region={rule_search_region}{_RESET}"
                    )
            if not locations:
                continue

            if len(locations) == 1:
                chosen = locations[0]
                method = "이미지"
            elif rule.action_x is not None and rule.action_y is not None:
                chosen = self._find_closest_image(locations, rule.action_x, rule.action_y) or locations[0]
                method = f"{len(locations)}개 중 선택"
            else:
                chosen = locations[0]
                method = f"{len(locations)}개 중 첫번째"

            return {
                "x": chosen[0],
                "y": chosen[1],
                "confidence": chosen[2],
                "image": img_path,
                "method": method,
                "locations": locations,
            }

        return None

    def _execute_click_at(
        self,
        rule: AutomationRule,
        action_type: str,
        click_x: int,
        click_y: int,
        start_time: datetime,
        *,
        image_click: bool = False,
    ) -> RuleExecutionResult:
        """좌표 클릭 실행 경로를 일반 클릭과 반복 이미지 클릭에서 공통 사용한다."""
        action_name = {"double_click": "더블클릭", "right_click": "우클릭"}.get(action_type, "클릭")
        alternate_route = bool(getattr(rule, "alternate_mouse_route", False) and image_click)

        # Arduino strict/enabled 모드에서는 입력 경로를 통일한다.
        if is_arduino_enabled() or is_arduino_strict_enabled():
            input_ctrl = get_input_controller()
            click_ok = False
            if alternate_route:
                click_ok = self._move_mouse_to(click_x, click_y, alternate_route=True)
                if click_ok:
                    time.sleep(0.05)
                    if action_type == "double_click":
                        click_ok = input_ctrl.double_click()
                    elif action_type == "right_click":
                        click_ok = input_ctrl.right_click()
                    else:
                        click_ok = input_ctrl.click()
            else:
                if action_type == "double_click":
                    click_ok = input_ctrl.double_click(click_x, click_y, duration=self._mouse_duration)
                elif action_type == "right_click":
                    click_ok = input_ctrl.right_click(click_x, click_y, duration=self._mouse_duration)
                else:
                    click_ok = input_ctrl.click(click_x, click_y, duration=self._mouse_duration)
            if click_ok:
                logger.info(f"{_GREEN}{self._step_prefix}✓ {action_name} 완료{_RESET}")
                self._last_mouse_pos = (click_x, click_y)
                return self._make_result(rule, True, f"{action_type} 완료", start_time)
            if is_arduino_strict_enabled():
                logger.error(f"{_RED}{self._step_prefix}[click] {action_name} failed (strict mode blocks software fallback){_RESET}")
                return self._make_result(rule, False, f"{action_type} 실패", start_time)

        max_move_attempts = MAX_MOVE_ATTEMPTS
        move_success = False

        for move_attempt in range(max_move_attempts):
            if self._stop_event.is_set():
                return self._make_result(rule, False, "실행 중지됨", start_time)

            self._is_moving_mouse = True
            try:
                if not self._move_mouse_to(click_x, click_y, alternate_route=alternate_route):
                    return self._make_result(rule, False, "실행 중지됨", start_time)
            finally:
                self._is_moving_mouse = False

            pos_after_move = pyautogui.position()
            if abs(pos_after_move[0] - click_x) < PIXEL_TOLERANCE_SMALL and abs(pos_after_move[1] - click_y) < PIXEL_TOLERANCE_SMALL:
                move_success = True
                break

            if _win32_move_click(click_x, click_y, action_type):
                move_success = True
                logger.info(f"{_GREEN}{self._step_prefix}✓ {action_name} 완료{_RESET}")
                self._last_mouse_pos = pyautogui.position()
                return self._make_result(rule, True, f"{action_type} 완료", start_time)

            if move_attempt < max_move_attempts - 1:
                time.sleep(0.5)

        if move_success:
            time.sleep(0.1)
            if _win32_force_click_at(click_x, click_y, action_type):
                logger.info(f"{_GREEN}{self._step_prefix}[click] {action_name} complete{_RESET}")
                self._last_mouse_pos = (click_x, click_y)
                return self._make_result(rule, True, f"{action_type} complete", start_time)

            current_pos = pyautogui.position()
            if (
                abs(current_pos[0] - click_x) < PIXEL_TOLERANCE_SMALL
                and abs(current_pos[1] - click_y) < PIXEL_TOLERANCE_SMALL
                and _perform_mouse_click(action_type)
            ):
                logger.info(f"{_GREEN}{self._step_prefix}[click] {action_name} complete{_RESET}")
                self._last_mouse_pos = current_pos
                return self._make_result(rule, True, f"{action_type} complete", start_time)

            logger.error(
                f"{_RED}  [click] {action_name} failed{_RESET} "
                f"(move-success/click-fail target=({click_x}, {click_y}), current={current_pos})"
            )
            return self._make_result(rule, False, "click failed", start_time)

        if _win32_force_click_at(click_x, click_y, action_type):
            logger.info(f"{_GREEN}{self._step_prefix}✓ {action_name} 완료{_RESET}")
            self._last_mouse_pos = (click_x, click_y)
            return self._make_result(rule, True, f"{action_type} 완료", start_time)

        logger.error(f"{_RED}  ✗ {action_name} 실패{_RESET}")
        return self._make_result(rule, False, "클릭 실패", start_time)

    def _repeat_delay_for_rule(self, rule: AutomationRule) -> float:
        if getattr(rule, "click_until_image_disappears", False):
            delay = float(
                getattr(
                    rule,
                    "click_until_image_disappears_delay",
                    getattr(rule, "repeat_delay", 0.5),
                )
                or 0.0
            )
            return max(0.05, delay)

        delay = float(getattr(rule, "repeat_delay", 0.5) or 0.0)
        if getattr(rule, "repeat_delay_random", False):
            delay_range = float(getattr(rule, "repeat_delay_random_range", 0.3) or 0.0)
            delay += random.uniform(-delay_range, delay_range)
        return max(0.05, delay)

    def _rule_repeats_child_actions(self, rule: AutomationRule) -> bool:
        return bool(
            getattr(rule, "children", None)
            and rule.action_type in ["click", "double_click", "right_click"]
            and (getattr(rule, "target_image", None) or getattr(rule, "target_images", None))
            and getattr(rule, "click_until_image_disappears", False)
        )

    def _mark_child_rules_handled_by_parent(self, rule: AutomationRule) -> None:
        handled_children = self._flatten_rules(getattr(rule, "children", []) or [])
        if not handled_children:
            return
        self._child_rules_executed_with_parent.update(child.rule_id for child in handled_children)

    def _wait_after_rule_result(self, rule: AutomationRule, result: RuleExecutionResult) -> Optional[RuleExecutionResult]:
        if self._stop_event.is_set():
            return self._make_result(rule, False, "실행 중지됨", datetime.now())
        is_skipped = "스킵됨" in result.message if result.message else False
        if is_skipped:
            return None
        wait_time = getattr(rule, "wait_after", self._default_wait)
        if getattr(rule, "wait_random", False):
            wait_range = getattr(rule, "wait_random_range", 0.3)
            wait_time = max(0, wait_time + random.uniform(-wait_range, wait_range))
        if wait_time > 0 and self._stop_event.wait(timeout=wait_time):
            return self._make_result(rule, False, "실행 중지됨", datetime.now())
        return None

    def _execute_child_rules_for_repeat_click(
        self,
        parent_rule: AutomationRule,
        start_time: datetime,
    ) -> Optional[RuleExecutionResult]:
        child_rules = [child for child in (getattr(parent_rule, "children", []) or []) if getattr(child, "enabled", True)]
        if not child_rules:
            return None

        previous_step = self._current_step_num
        parent_step = previous_step or "반복"

        try:
            logger.info(f"{_CYAN}{self._step_prefix}↳ 클릭 후 하위액션 {len(child_rules)}개 실행{_RESET}")
            for visible_index, child in enumerate(child_rules, 1):
                step_num = f"{parent_step}-{visible_index}"
                result = self._execute_rule_tree_once(child, step_num)
                if result is not None:
                    return result
        finally:
            self._current_step_num = previous_step

        return None

    def _execute_rule_tree_once(
        self,
        rule: AutomationRule,
        step_num: str,
    ) -> Optional[RuleExecutionResult]:
        previous_step = self._current_step_num
        self._current_step_num = step_num
        action_name = rule.description if rule.description else rule.action_type
        logger.info(f"{_CYAN}[{step_num}] 반복묶음 하위: {action_name}{_RESET}")

        has_monitoring_watches = len(getattr(rule, "monitoring_watches", []) or []) > 0
        is_monitoring = getattr(rule, "is_monitoring_mode", False) or has_monitoring_watches
        self._progress.current_rule = rule.rule_id
        self._progress.current_action_number = str(step_num)
        self._progress.current_action_name = action_name
        self._progress.current_action_is_monitoring = bool(is_monitoring)
        self._update_progress(f"[{step_num}] {action_name}")

        if is_monitoring:
            trigger_result = self._handle_trigger_gate(rule, datetime.now(), step_num)
            if trigger_result is not None:
                result = trigger_result
            else:
                self._state = ExecutionState.MONITORING
                result = self._execute_monitoring_mode(rule, self._flatten_rules([rule]), 0, step_num=step_num)
                self._state = ExecutionState.RUNNING_INITIAL
        else:
            result = self._execute_rule_with_retry(rule, step_num=step_num)

        self._results.append(result)
        if self._on_rule_executed:
            self._on_rule_executed(result)

        if not result.success or getattr(result, "skip_current_playlist", False):
            self._current_step_num = previous_step
            return result

        wait_result = self._wait_after_rule_result(rule, result)
        if wait_result is not None:
            self._current_step_num = previous_step
            return wait_result

        if not self._rule_repeats_child_actions(rule):
            for visible_index, child in enumerate([c for c in (getattr(rule, "children", []) or []) if getattr(c, "enabled", True)], 1):
                child_result = self._execute_rule_tree_once(child, f"{step_num}-{visible_index}")
                if child_result is not None:
                    self._current_step_num = previous_step
                    return child_result

        self._current_step_num = previous_step
        return None

    def _execute_click_until_image_disappears(
        self,
        rule: AutomationRule,
        valid_images: List[str],
        action_type: str,
        start_time: datetime,
        first_target: Optional[Dict[str, Any]] = None,
    ) -> RuleExecutionResult:
        """이미지가 사라질 때까지 매번 재검색 후 반복 클릭한다."""
        try:
            configured_count = int(getattr(rule, "repeat_count", 1) or 1)
        except (TypeError, ValueError):
            configured_count = 1
        max_clicks = max(IMAGE_CLICK_UNTIL_DISAPPEAR_MIN_CLICKS, configured_count)
        max_seconds = IMAGE_CLICK_UNTIL_DISAPPEAR_MAX_SECONDS
        miss_confirm = IMAGE_CLICK_UNTIL_DISAPPEAR_MISS_CONFIRM
        started = time.time()
        clicks = 0
        misses = 0
        target = first_target

        logger.info(
            f"{_CYAN}{self._step_prefix}↻ 이미지 사라질 때까지 반복 클릭 시작 "
            f"(최대 {max_clicks}회/{max_seconds:.0f}초){_RESET}"
        )

        def _finish_guarded(reason: str) -> RuleExecutionResult:
            """반복 보호 한도에 걸려도 전체 재생은 멈추지 않는다.

            이 기능은 선택 이미지가 계속 보일 때까지 클릭하는 보조 동작이다.
            작은 템플릿/유사 이미지가 남은 화면에서 실패를 반환하면 전체 재생목록이
            멈추므로, 클릭과 하위 액션이 이미 수행된 뒤에는 경고 후 다음 액션으로 넘긴다.
            """
            logger.warning(
                f"{_YELLOW}{self._step_prefix}⚠ 이미지 반복 클릭 {reason} → 다음 액션 진행 "
                f"({clicks}회 클릭){_RESET}"
            )
            self._mark_child_rules_handled_by_parent(rule)
            return self._make_result(
                rule,
                True,
                f"이미지 반복 클릭 {reason} 후 진행 ({clicks}회)",
                start_time,
            )

        while not self._stop_event.is_set():
            if time.time() - started >= max_seconds:
                return _finish_guarded("시간초과")
            if clicks >= max_clicks:
                return _finish_guarded("한도 도달")

            if target is None:
                target = self._find_rule_image_click_target(rule, valid_images)

            if target is None:
                misses += 1
                if misses >= miss_confirm:
                    logger.info(f"{_GREEN}{self._step_prefix}✓ 이미지 사라짐 확인 ({clicks}회 클릭){_RESET}")
                    self._mark_child_rules_handled_by_parent(rule)
                    return self._make_result(rule, True, f"이미지 사라짐 ({clicks}회 클릭)", start_time)
                time.sleep(0.2)
                continue

            misses = 0
            found_name = Path(target["image"]).name
            logger.info(
                f"{_GREEN}{self._step_prefix}✓ 반복 클릭 대상: {found_name} "
                f"({int(target['confidence'] * 100)}%) #{clicks + 1}{_RESET}"
            )
            click_result = self._execute_click_at(
                rule,
                action_type,
                int(target["x"]),
                int(target["y"]),
                start_time,
                image_click=True,
            )
            if not click_result.success:
                return click_result

            child_result = self._execute_child_rules_for_repeat_click(rule, start_time)
            if child_result is not None:
                return child_result

            clicks += 1
            if self._stop_event.wait(timeout=self._repeat_delay_for_rule(rule)):
                return self._make_result(rule, False, "실행 중지됨", start_time)
            target = None

        return self._make_result(rule, False, "실행 중지됨", start_time)

    def _find_closest_image(
        self,
        locations: List[tuple],
        hint_x: int,
        hint_y: int,
    ) -> Optional[tuple]:
        """
        좌표 힌트와 가장 가까운 이미지 위치 반환
        """
        if not locations:
            return None

        if len(locations) == 1:
            return locations[0]

        # 거리 계산해서 가장 가까운 것 선택
        closest = min(locations, key=lambda loc:
            (loc[0] - hint_x) ** 2 + (loc[1] - hint_y) ** 2
        )
        return closest

    @staticmethod
    def _clamp_mouse_point(value: int, lower: int, upper: int) -> int:
        return max(lower, min(upper, int(value)))

    def _build_alternate_mouse_route(self, target_x: int, target_y: int) -> List[Tuple[int, int]]:
        """기본 직선 이동 대신 목표 반대편에서 직각 우회로 접근한다."""
        try:
            start_x, start_y = pyautogui.position()
            screen_w, screen_h = pyautogui.size()
        except Exception:
            return [(int(target_x), int(target_y))]

        margin = 8
        approach_gap = 140
        detour_gap = 120
        max_x = max(margin, int(screen_w) - margin)
        max_y = max(margin, int(screen_h) - margin)
        target_x = self._clamp_mouse_point(target_x, margin, max_x)
        target_y = self._clamp_mouse_point(target_y, margin, max_y)
        start_x = self._clamp_mouse_point(start_x, margin, max_x)
        start_y = self._clamp_mouse_point(start_y, margin, max_y)

        dx = target_x - start_x
        dy = target_y - start_y
        if abs(dx) >= abs(dy):
            approach_dir = 1 if dx >= 0 else -1
            approach_x = self._clamp_mouse_point(target_x + approach_dir * approach_gap, margin, max_x)
            if abs(approach_x - target_x) < 30:
                approach_x = self._clamp_mouse_point(target_x - approach_dir * approach_gap, margin, max_x)
            detour_y = (
                self._clamp_mouse_point(target_y + detour_gap, margin, max_y)
                if target_y < screen_h / 2
                else self._clamp_mouse_point(target_y - detour_gap, margin, max_y)
            )
            raw_points = [
                (start_x, detour_y),
                (approach_x, detour_y),
                (approach_x, target_y),
                (target_x, target_y),
            ]
        else:
            approach_dir = 1 if dy >= 0 else -1
            approach_y = self._clamp_mouse_point(target_y + approach_dir * approach_gap, margin, max_y)
            if abs(approach_y - target_y) < 30:
                approach_y = self._clamp_mouse_point(target_y - approach_dir * approach_gap, margin, max_y)
            detour_x = (
                self._clamp_mouse_point(target_x + detour_gap, margin, max_x)
                if target_x < screen_w / 2
                else self._clamp_mouse_point(target_x - detour_gap, margin, max_x)
            )
            raw_points = [
                (detour_x, start_y),
                (detour_x, approach_y),
                (target_x, approach_y),
                (target_x, target_y),
            ]

        points: List[Tuple[int, int]] = []
        for px, py in raw_points:
            point = (int(px), int(py))
            if not points or points[-1] != point:
                points.append(point)
        return points or [(target_x, target_y)]

    def _move_mouse_to(self, x: int, y: int, *, duration: Optional[float] = None, alternate_route: bool = False) -> bool:
        duration = self._mouse_duration if duration is None else max(0.0, float(duration))
        if not alternate_route:
            pyautogui.moveTo(x, y, duration=duration)
            return True

        points = self._build_alternate_mouse_route(x, y)
        segment_duration = duration / max(len(points), 1)
        input_ctrl = get_input_controller() if (is_arduino_enabled() or is_arduino_strict_enabled()) else None
        for px, py in points:
            if self._stop_event.is_set():
                return False
            if input_ctrl is not None:
                if not input_ctrl.move_to(px, py, duration=segment_duration):
                    return False
            else:
                pyautogui.moveTo(px, py, duration=segment_duration)
        return True

    def _click_at(self, x: int, y: int) -> None:
        """지정된 위치 클릭 (멀티모니터 지원)"""
        # Arduino strict/enabled 모드에서는 입력 경로를 통일한다.
        if is_arduino_enabled() or is_arduino_strict_enabled():
            input_ctrl = get_input_controller()
            if input_ctrl.click(x, y, duration=self._mouse_duration):
                return
            if is_arduino_strict_enabled():
                logger.error(f"[click] strict mode blocked software fallback at ({x}, {y})")
                return

        # pyautogui 사용
        pyautogui.moveTo(x, y, duration=self._mouse_duration)
        pos = pyautogui.position()

        if abs(pos[0] - x) < PIXEL_TOLERANCE_SMALL and abs(pos[1] - y) < PIXEL_TOLERANCE_SMALL:
            # PyAutoGUI 성공
            time.sleep(0.1)
            pyautogui.click(x, y)
        else:
            # Win32 API 사용
            _win32_move_click(x, y, "click")

    def _execute_monitoring_mode(
        self,
        rule: AutomationRule,
        all_rules: List[AutomationRule],
        current_index: int,
        step_num: str = "",
    ) -> RuleExecutionResult:
        """Run monitoring until the action's final image appears or a route jumps out.

        A route watch executes its dedicated monitor actions first, then returns a
        jump index to the main execution loop. The target action is not executed
        inside monitoring mode; jumping ends monitoring immediately.
        """
        del current_index
        start_time = datetime.now()
        step_prefix = f"[{step_num}] " if step_num else ""
        base_confidence = rule.confidence if getattr(rule, "confidence", 0) > 0 else 0.8
        watches = sorted(self._normalise_monitoring_watches(rule, base_confidence), key=self._monitoring_watch_priority)
        final_images = self._monitoring_final_images_for_rule(rule)
        final_search_region = self._image_search_region_for_rule(rule)

        if not watches:
            return self._make_result(rule, False, "모니터링 이미지가 설정되지 않음", start_time)

        monitor_rule_name = getattr(rule, "description", "") or getattr(rule, "action_type", "동작")
        self._current_monitoring_wait_detail = {
            "action": f"[{step_num}] {monitor_rule_name}" if step_num else monitor_rule_name,
            "rule_id": getattr(rule, "rule_id", "") or "-",
            "watches": len(watches),
            "final_images": ",".join(Path(image_path).name for image_path in final_images) or "-",
        }

        configured_actions_by_watch: Dict[int, int] = {}
        for watch in watches:
            try:
                watch_order = int(watch.get("_watch_order", 0))
            except (TypeError, ValueError):
                watch_order = len(configured_actions_by_watch)
            configured_actions_by_watch.setdefault(watch_order, len(watch.get("monitor_actions", []) or []))
        configured_actions = sum(configured_actions_by_watch.values())
        logger.info(
            f"{_CYAN}{step_prefix}▶ 모니터링 시작: 이미지 {len(watches)}개, "
            f"전용 액션 {configured_actions}개, 최종이미지 {len(final_images)}개{_RESET}"
        )
        self._update_progress(f"{step_prefix}모니터링 이미지 대기 중")

        wait_count = 0
        last_status = 0.0
        while True:
            if self._stop_event.is_set():
                self._log_monitoring_stop_context("stop_event_before_scan", step_prefix, start_time)
                return self._make_result(rule, False, "실행 중지됨", start_time)
            if self._wait_for_resume():
                self._log_monitoring_stop_context("wait_for_resume_stopped", step_prefix, start_time)
                return self._make_result(rule, False, "실행 중지됨", start_time)

            final_result = self._find_monitoring_final_image(rule, final_images, final_search_region, base_confidence)
            if final_result is not None:
                final_image, found = final_result
                final_confidence = found[2] if len(found) > 2 else 0
                logger.info(
                    f"{_GREEN}{step_prefix}✓ 모니터링 최종이미지 발견: {Path(final_image).name} "
                    f"({int(final_confidence * 100)}%) - 모니터링 종료{_RESET}"
                )
                self._current_monitoring_wait_detail = {}
                return self._make_result(rule, True, "모니터링 완료 - 최종이미지 발견", start_time)

            for watch in watches:
                image_path = watch.get("image")
                if not image_path:
                    continue
                if not Path(image_path).exists():
                    logger.warning(f"{_YELLOW}{step_prefix}⚠ 모니터링 이미지 파일 없음: {image_path}{_RESET}")
                    continue

                confidence = watch.get("confidence", base_confidence) or base_confidence
                search_region = watch.get("search_region")
                jump_enabled = bool(watch.get("jump_enabled", True))
                result = self._find_image_on_screen(
                    image_path,
                    confidence,
                    search_region=search_region,
                    verify_color=bool(watch.get("verify_image_color", False)),
                    verify_brightness=bool(watch.get("verify_image_brightness", False)),
                )
                if not result:
                    continue

                found_confidence = result[2] if len(result) > 2 else 0
                image_name = Path(image_path).name
                monitor_actions = watch.get("monitor_actions", []) or []
                goto_index = self._safe_int(watch.get("goto_index", -1), -1)
                goto_rule_id = str(watch.get("goto_rule_id") or "")
                goto_label = str(watch.get("goto_step") or f"{goto_index + 1}")
                if goto_index < 0:
                    logger.debug(
                        f"{step_prefix}모니터링 기본 감시 무시: {image_name} "
                        "(이미지별 이동 대상 없음)"
                    )
                    continue
                if goto_index >= 0:
                    watch_no = self._safe_int(watch.get("_watch_order", 0), 0) + 1
                    image_no = self._safe_int(watch.get("_image_order", 0), 0) + 1
                    image_priority = self._safe_int(watch.get("_image_priority", 999), 999)
                    threshold_pct = int(float(confidence or 0) * 100)
                    matched_pct = int(float(found_confidence or 0) * 100)
                    logger.info(
                        f"{_GREEN}{step_prefix}✓ 라우팅 이미지 발견: {image_name} "
                        f"({matched_pct}%) - 전용 액션 {len(monitor_actions)}개 후 액션 {goto_label} 이동 "
                        f"[watch={watch_no} image={image_no} priority={image_priority} threshold={threshold_pct}%]{_RESET}"
                    )
                    self._update_progress(f"{step_prefix}라우팅 이미지 발견 → 전용 액션/액션 {goto_label}")
                    if monitor_actions:
                        action_result = self._execute_monitor_action_sequence(
                            rule,
                            monitor_actions,
                            base_confidence,
                            start_time,
                            step_prefix,
                            matched_image=image_path,
                            matched_location=result,
                        )
                        if action_result is not None:
                            return action_result

                    if not jump_enabled:
                        logger.info(
                            f"{_YELLOW}{step_prefix}↷ 모니터링 점프 비활성: {image_name} "
                            f"전용 액션만 처리하고 최종이미지 대기로 복귀{_RESET}"
                        )
                        self._update_progress(f"{step_prefix}모니터링 점프 비활성 → 대기 계속")
                        if self._stop_event.wait(timeout=0.5):
                            self._log_monitoring_stop_context("jump_disabled_wait_stop", step_prefix, start_time)
                            return self._make_result(rule, False, "실행 중지됨", start_time)
                        break

                    if self._monitoring_route_condition_blocks_jump(watch, base_confidence, step_prefix):
                        if self._stop_event.wait(timeout=0.5):
                            self._log_monitoring_stop_context("condition_block_wait_stop", step_prefix, start_time)
                            return self._make_result(rule, False, "실행 중지됨", start_time)
                        break

                    target_rules = []
                    runtime_target_rules = []
                    plan = self._current_plan
                    if plan is not None:
                        runtime_target_rules = list(getattr(plan, "initial_rules", []) or [])
                        target_rules = list(
                            getattr(plan, "_original_initial_rules", None)
                            or runtime_target_rules
                            or []
                        )
                    if not target_rules:
                        target_rules = list(all_rules or [])
                    if not runtime_target_rules:
                        runtime_target_rules = list(all_rules or [])
                    flat_target_rules = self._flatten_rules(target_rules)
                    target_rule = None
                    target_index = goto_index
                    if goto_rule_id:
                        for flat_index, candidate in enumerate(flat_target_rules):
                            if getattr(candidate, "rule_id", None) == goto_rule_id:
                                target_rule = candidate
                                target_index = flat_index
                                break
                    elif 0 <= goto_index < len(target_rules):
                        target_rule = target_rules[goto_index]
                    if target_rule is None:
                        return self._make_result(
                            rule,
                            False,
                            f"모니터링 점프 대상 액션 번호 오류: {goto_index + 1}",
                            start_time,
                        )
                    if getattr(target_rule, "rule_id", None) == getattr(rule, "rule_id", None):
                        return self._make_result(rule, False, "모니터링 점프 자기 자신 실행 차단", start_time)
                    if not getattr(target_rule, "enabled", True):
                        return self._make_result(rule, False, f"모니터링 점프 대상 비활성: 액션 {target_index + 1}", start_time)

                    action_name = getattr(target_rule, "description", "") or getattr(target_rule, "action_type", "동작")
                    target_original_step = self._rule_step_in_rules(target_rules, target_rule)
                    target_runtime_step = self._rule_step_in_rules(runtime_target_rules, target_rule)
                    target_step_label = self._format_step_alias(target_runtime_step, target_original_step)
                    resolved_goto_rule_id = goto_rule_id or str(getattr(target_rule, "rule_id", "") or "")
                    runtime_note = ""
                    if target_original_step and not target_runtime_step:
                        runtime_note = " 현재목록=범위밖"
                    route_detail = {
                        "action": f"[{step_num}] {monitor_rule_name}" if step_num else monitor_rule_name,
                        "rule_id": getattr(rule, "rule_id", "") or "-",
                        "watch": watch_no,
                        "image": image_no,
                        "priority": image_priority,
                        "monitor_image": image_name,
                        "matched": f"{matched_pct}%",
                        "threshold": f"{threshold_pct}%",
                        "search_region": search_region or "-",
                        "monitor_actions": len(monitor_actions),
                        "goto_index": goto_index,
                        "goto_step": goto_label,
                        "goto_rule_id": goto_rule_id or "-",
                        "target_step": target_step_label,
                        "target_rule_id": resolved_goto_rule_id or "-",
                        "target_name": action_name,
                    }
                    self._last_monitoring_route_detail = route_detail
                    logger.info(
                        f"{_CYAN}{step_prefix}↪ 모니터링 점프 요청: 액션 {target_step_label} {action_name} "
                        f"rule_id={resolved_goto_rule_id or '-'} goto_index={goto_index}{runtime_note}{_RESET}"
                    )
                    logger.info(
                        f"{_CYAN}{step_prefix}[모니터링점프상세] "
                        f"{self._format_monitoring_detail(route_detail)}{runtime_note}{_RESET}"
                    )
                    self._update_progress(f"{step_prefix}모니터링 점프 → 액션 {target_step_label}")
                    self._current_monitoring_wait_detail = {}
                    return self._make_result(
                        rule,
                        True,
                        f"모니터링 점프 - 액션 {target_index + 1}",
                        start_time,
                        monitoring_jump_index=target_index,
                        monitoring_jump_rule_id=resolved_goto_rule_id,
                    )

            wait_count += 1
            now = time.time()
            if now - last_status >= 10.0:
                last_status = now
                elapsed = (datetime.now() - start_time).total_seconds()
                if elapsed >= 60:
                    logger.info(
                        f"{_YELLOW}{step_prefix}⏳ 모니터링 이미지 대기 중... "
                        f"{int(elapsed) // 60}분 {int(elapsed) % 60}초{_RESET}"
                    )
                else:
                    logger.info(f"{_YELLOW}{step_prefix}⏳ 모니터링 이미지 대기 중... {elapsed:.0f}초{_RESET}")
                self._update_progress(f"{step_prefix}모니터링 이미지 대기 중 ({wait_count}회)")

            if self._stop_event.wait(timeout=0.5):
                self._log_monitoring_stop_context("monitoring_wait_stop", step_prefix, start_time)
                return self._make_result(rule, False, "실행 중지됨", start_time)

    def _monitoring_final_images_for_rule(self, rule: AutomationRule) -> List[str]:
        """Return final images that stop monitoring without mixing them with watch images."""
        images: List[str] = []
        seen = set()
        watch_images = {
            image_path
            for watch in (getattr(rule, "monitoring_watches", None) or [])
            if isinstance(watch, dict)
            for image_path in self._monitoring_watch_image_paths(watch)
        }
        legacy_final = getattr(rule, "monitoring_final_image", None)
        raw_images = []
        if legacy_final:
            raw_images.append(legacy_final)
        raw_images.extend(self._target_images_for_rule(rule))

        for image_path in raw_images:
            if not image_path:
                continue
            text_path = str(image_path)
            if text_path in watch_images:
                continue
            if text_path in seen:
                continue
            seen.add(text_path)
            images.append(text_path)
        return images

    def _find_monitoring_final_image(
        self,
        rule: AutomationRule,
        final_images: List[str],
        search_region,
        confidence: float,
    ) -> Optional[Tuple[str, tuple]]:
        """Find the action's final image; this is the only normal monitoring stop condition."""
        verify_color = bool(getattr(rule, "verify_image_color", False))
        verify_brightness = bool(getattr(rule, "verify_image_brightness", False))
        for image_path in final_images:
            if not Path(image_path).exists():
                continue
            result = self._find_image_on_screen(
                image_path,
                confidence,
                search_region=search_region,
                verify_color=verify_color,
                verify_brightness=verify_brightness,
            )
            if result:
                return image_path, result
        return None

    def _normalise_monitoring_watches(
        self,
        rule: AutomationRule,
        base_confidence: float,
    ) -> List[Dict[str, Any]]:
        """Return monitoring watches in the new single-purpose shape."""
        watches: List[Dict[str, Any]] = []
        raw_watches = getattr(rule, "monitoring_watches", None) or []

        for watch_order, raw in enumerate(raw_watches):
            if not isinstance(raw, dict):
                continue
            goto_index = self._safe_int(raw.get("goto_index", -1), -1)
            if goto_index < 0:
                continue
            monitor_actions = list(raw.get("monitor_actions") or [])
            if not monitor_actions and raw.get("monitor_action"):
                monitor_actions = [raw.get("monitor_action")]
            watch_images = self._normalise_monitoring_watch_images(raw)
            if not watch_images:
                continue
            for image_order, image_item in enumerate(watch_images):
                watches.append(
                    {
                        "image": image_item["image"],
                        "search_region": image_item.get("search_region") or raw.get("search_region") or getattr(rule, "search_region", None),
                        "confidence": image_item.get("confidence", raw.get("confidence", base_confidence)),
                        "goto_index": goto_index,
                        "goto_rule_id": str(raw.get("goto_rule_id") or ""),
                        "jump_enabled": bool(raw.get("jump_enabled", True)),
                        "verify_image_color": image_item.get("verify_image_color", raw.get("verify_image_color", False)),
                        "verify_image_brightness": image_item.get("verify_image_brightness", raw.get("verify_image_brightness", False)),
                        "monitor_actions": [item for item in monitor_actions if isinstance(item, dict)],
                        "condition_image": raw.get("condition_image"),
                        "condition_search_region": raw.get("condition_search_region"),
                        "condition_confidence": raw.get("condition_confidence", 0.8),
                        "condition_jump_when_visible": bool(raw.get("condition_jump_when_visible", False)),
                        "condition_verify_image_color": bool(raw.get("condition_verify_image_color", False)),
                        "condition_verify_image_brightness": bool(raw.get("condition_verify_image_brightness", False)),
                        "_watch_order": watch_order,
                        "_image_order": image_order,
                        "_image_priority": image_item["priority"],
                    }
                )
        return watches

    def _monitoring_watch_image_paths(self, raw: dict) -> List[str]:
        return [item["image"] for item in self._normalise_monitoring_watch_images(raw)]

    def _normalise_monitoring_watch_images(self, raw: dict) -> List[Dict[str, Any]]:
        """Return up to 10 monitoring image candidates for one route watch."""
        images: List[Dict[str, Any]] = []
        seen: set[str] = set()

        def add_image(image_path, priority=None) -> None:
            source_item = image_path if isinstance(image_path, dict) else None
            actual_path = (
                source_item.get("image") or source_item.get("image_path")
                if source_item is not None
                else image_path
            )
            if not actual_path:
                return
            text_path = str(actual_path)
            if text_path in seen:
                return
            seen.add(text_path)
            try:
                priority_value = int(priority)
            except (TypeError, ValueError):
                priority_value = len(images) + 1
            image_info = {
                "image": text_path,
                "priority": max(1, min(10, priority_value)),
                "_input_order": len(images),
            }
            if source_item is not None:
                if source_item.get("confidence") is not None:
                    image_info["confidence"] = source_item.get("confidence")
                if source_item.get("search_region") is not None:
                    image_info["search_region"] = source_item.get("search_region")
                if source_item.get("verify_image_color") is not None:
                    image_info["verify_image_color"] = bool(source_item.get("verify_image_color"))
                if source_item.get("verify_image_brightness") is not None:
                    image_info["verify_image_brightness"] = bool(source_item.get("verify_image_brightness"))
            images.append(image_info)

        raw_images = raw.get("images")
        if isinstance(raw_images, list):
            for item in raw_images:
                if isinstance(item, dict):
                    image_item = dict(item)
                    image_item["image"] = item.get("image") or item.get("image_path")
                    add_image(image_item, item.get("priority"))
                else:
                    add_image(item)

        add_image(raw.get("image") or raw.get("image_path"), len(images) + 1)
        images.sort(key=lambda item: (item["priority"], item["_input_order"]))
        return images[:10]

    @staticmethod
    def _monitoring_watch_priority(watch: dict) -> tuple[int, int, int, int]:
        try:
            goto_index = int(watch.get("goto_index", -1))
        except (TypeError, ValueError):
            goto_index = -1
        try:
            image_priority = int(watch.get("_image_priority", 999))
        except (TypeError, ValueError):
            image_priority = 999
        try:
            watch_order = int(watch.get("_watch_order", 0))
        except (TypeError, ValueError):
            watch_order = 0
        try:
            image_order = int(watch.get("_image_order", 0))
        except (TypeError, ValueError):
            image_order = 0
        return (0 if goto_index >= 0 else 1, watch_order, image_priority, image_order)

    def _monitoring_route_condition_blocks_jump(self, watch: dict, base_confidence: float, step_prefix: str = "") -> bool:
        condition_image = str(watch.get("condition_image") or "").strip()
        if not condition_image:
            logger.info(f"{_GREEN}{step_prefix}✓ 모니터링 조건 없음 → 점프 실행{_RESET}")
            return False
        if not Path(condition_image).exists():
            logger.warning(f"{_YELLOW}{step_prefix}⚠ 모니터링 조건이미지 파일 없음: {condition_image} → 점프 실행{_RESET}")
            return False
        jump_when_visible = bool(watch.get("condition_jump_when_visible", False))
        condition_confidence = self._safe_float(watch.get("condition_confidence", 0.8), 0.8)
        condition_region = watch.get("condition_search_region")
        result = self._find_image_on_screen(
            condition_image,
            condition_confidence or base_confidence,
            search_region=condition_region,
            verify_color=bool(watch.get("condition_verify_image_color", False)),
            verify_brightness=bool(watch.get("condition_verify_image_brightness", False)),
        )
        if not result:
            if jump_when_visible:
                logger.info(
                    f"{_YELLOW}{step_prefix}⏳ 모니터링 조건 미충족: {Path(condition_image).name} 없음 "
                    f"→ 점프 대기{_RESET}"
                )
                self._update_progress(f"{step_prefix}모니터링 조건 대기 중")
                return True
            logger.info(f"{_GREEN}{step_prefix}✓ 모니터링 조건 해소: {Path(condition_image).name} 없음 → 점프 실행{_RESET}")
            return False
        actual_confidence = result[2] if len(result) > 2 else 0
        if jump_when_visible:
            logger.info(
                f"{_GREEN}{step_prefix}✓ 모니터링 조건 충족: {Path(condition_image).name} "
                f"({int(actual_confidence * 100)}%) → 점프 실행{_RESET}"
            )
            return False
        logger.info(
            f"{_YELLOW}{step_prefix}⏳ 모니터링 조건 유지: {Path(condition_image).name} "
            f"({int(actual_confidence * 100)}%) → 점프 대기{_RESET}"
        )
        self._update_progress(f"{step_prefix}모니터링 조건 대기 중")
        return True

    def _execute_monitor_action_sequence(
        self,
        rule: AutomationRule,
        monitor_actions: List[dict],
        confidence: float,
        start_time: datetime,
        step_prefix: str = "",
        matched_image: Optional[str] = None,
        matched_location: Optional[tuple] = None,
    ) -> Optional[RuleExecutionResult]:
        for action_index, monitor_action in enumerate(monitor_actions, start=1):
            if self._stop_event.is_set():
                return self._make_result(rule, False, "실행 중지됨", start_time)
            if not monitor_action or monitor_action.get("type") in {None, "", "없음"}:
                continue

            repeat_count = self._safe_positive_int(monitor_action.get("repeat_count", 1), 1)
            repeat_delay = self._safe_float(monitor_action.get("repeat_delay", 0.5), 0.5)
            repeat_delay_random = bool(monitor_action.get("repeat_delay_random", False))
            repeat_delay_range = self._safe_float(monitor_action.get("repeat_delay_random_range", 0.3), 0.3)
            action_type = monitor_action.get("type", "알수없음")

            for repeat_index in range(repeat_count):
                if self._stop_event.is_set():
                    return self._make_result(rule, False, "실행 중지됨", start_time)
                logger.info(
                    f"{_CYAN}{step_prefix}  ▷ 모니터링 전용 액션 {action_index}/{len(monitor_actions)} "
                    f"실행: {action_type} ({repeat_index + 1}/{repeat_count}){_RESET}"
                )
                prelocated_image = None
                if (
                    action_index == 1
                    and repeat_index == 0
                    and matched_location is not None
                    and self._monitor_action_matches_detected_image(monitor_action, matched_image)
                ):
                    prelocated_image = matched_location
                action_message = self._execute_monitor_action(
                    monitor_action,
                    confidence,
                    prelocated_image=prelocated_image,
                )
                if not action_message:
                    return self._make_result(
                        rule,
                        False,
                        f"모니터링 전용 액션 실패: {action_type}",
                        start_time,
                    )
                logger.info(f"{_GREEN}{step_prefix}  ✓ {action_message}{_RESET}")

                if repeat_index < repeat_count - 1:
                    delay = repeat_delay
                    if repeat_delay_random:
                        delay += random.uniform(-repeat_delay_range, repeat_delay_range)
                    if self._stop_event.wait(timeout=max(0.05, delay)):
                        return self._make_result(rule, False, "실행 중지됨", start_time)

            wait_after = self._safe_float(monitor_action.get("wait_after", 0.5), 0.5)
            if bool(monitor_action.get("wait_random", False)):
                wait_range = self._safe_float(monitor_action.get("wait_random_range", 0.3), 0.3)
                wait_after += random.uniform(-wait_range, wait_range)
            if wait_after > 0 and self._stop_event.wait(timeout=max(0.05, wait_after)):
                return self._make_result(rule, False, "실행 중지됨", start_time)
        return None

    @staticmethod
    def _monitor_action_matches_detected_image(monitor_action: dict, matched_image: Optional[str]) -> bool:
        if not matched_image or monitor_action.get("type") != "이미지 클릭":
            return False
        action_image = monitor_action.get("image")
        if not action_image:
            return False
        try:
            action_path = Path(str(action_image))
            matched_path = Path(str(matched_image))
            if action_path == matched_path:
                return True
            return bool(action_path.name and action_path.name == matched_path.name)
        except Exception:
            return str(action_image) == str(matched_image)

    @staticmethod
    def _safe_positive_int(value, default: int = 1) -> int:
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _safe_int(value, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _safe_float(value, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _execute_monitor_action(
        self,
        monitor_action: dict,
        confidence: float = 0.65,
        prelocated_image: Optional[tuple] = None,
    ) -> Optional[str]:
        """
        모니터링 액션 실행

        감시 이미지 발견 시 실행할 액션
        Returns: 성공 메시지 또는 None (실패 시)
        """
        action_type = monitor_action.get('type', '없음')

        # 디버그: monitor_action 정보
        logger.debug(f"[모니터링 액션] 인식률: {confidence:.0%}, 검색범위: {monitor_action.get('search_region', 'None')}")

        # 타이핑 랜덤 옵션
        typing_random = monitor_action.get('typing_random', False)
        typing_delay = monitor_action.get('typing_delay', 0.1)
        typing_delay_range = monitor_action.get('typing_delay_range', 0.05)

        # 이미지 검색 옵션 (monitor_action 개별 인식률 우선, 없으면 rule confidence 폴백)
        search_confidence = monitor_action.get('confidence', confidence)
        search_radius = monitor_action.get('search_radius', 0) or 0

        try:
            if action_type == '텍스트 입력':
                text = monitor_action.get('text', '')
                if text:
                    if typing_random:
                        # 글자별 랜덤 딜레이
                        input_ctrl = get_input_controller()
                        for char in text:
                            if char.isascii():
                                input_ctrl.typewrite([char], interval=0)
                            else:
                                pyperclip.copy(char)
                                time.sleep(0.02)
                                input_ctrl.hotkey('ctrl', 'v')
                            delay = typing_delay + random.uniform(-typing_delay_range, typing_delay_range)
                            time.sleep(max(0.01, delay))
                    else:
                        self._type_text_with_clipboard(text)
                    text_preview = text[:20] + "..." if len(text) > 20 else text
                    return f"텍스트 입력: {text_preview}"

            elif action_type == '키 입력':
                keys = monitor_action.get('keys', [])
                key_events = monitor_action.get('key_events', []) or []
                input_ctrl = get_input_controller()
                if key_events:
                    ok = input_ctrl.replay_key_events(key_events)
                    if ok is False:
                        return None
                    return "기록 키 입력"
                if keys:
                    key_list = [k.lower().strip() for k in keys if k.strip()]
                    if len(key_list) == 1:
                        ok = input_ctrl.press(key_list[0])
                    else:
                        ok = input_ctrl.hotkey(*key_list)
                    if ok is False:
                        return None
                    return f"키 입력: {'+'.join(key_list)}"

            elif action_type == '마우스 클릭':
                x = monitor_action.get('x')
                y = monitor_action.get('y')
                click_type = monitor_action.get('click_type', 'click')  # click, double_click, right_click
                if x is not None and y is not None:
                    input_ctrl = get_input_controller()
                    if monitor_action.get('alternate_mouse_route', False):
                        if not self._move_mouse_to(x, y, alternate_route=True):
                            return None
                    else:
                        input_ctrl.move_to(x, y, duration=self._mouse_duration)
                    time.sleep(0.05)
                    if click_type == 'double_click':
                        input_ctrl.double_click()  # 이미 이동했으므로 좌표 없이 클릭
                        return f"더블클릭: ({x}, {y})"
                    elif click_type == 'right_click':
                        input_ctrl.right_click()  # 이미 이동했으므로 좌표 없이 클릭
                        return f"우클릭: ({x}, {y})"
                    else:
                        input_ctrl.click()  # 이미 이동했으므로 좌표 없이 클릭
                        return f"마우스 클릭: ({x}, {y})"

            elif action_type == '이미지 클릭':
                image_path = monitor_action.get('image')
                click_type = monitor_action.get('click_type', 'click')
                alternate_route = bool(monitor_action.get('alternate_mouse_route', False))
                search_region = monitor_action.get('search_region')  # [x1, y1, x2, y2] 또는 None

                # INFO 레벨로 실제 사용 값 출력 (디버깅용)
                logger.debug(f"[이미지 클릭] 이미지: {Path(image_path).name if image_path else 'None'}, 인식률: {search_confidence:.0%}, 검색범위: {search_region}")

                # search_radius가 있고 search_region이 없으면 변환
                if not search_region and search_radius > 0:
                    action_center_x = monitor_action.get('x')
                    if action_center_x is None:
                        action_center_x = monitor_action.get('center_x')
                    action_center_y = monitor_action.get('y')
                    if action_center_y is None:
                        action_center_y = monitor_action.get('center_y')
                    if action_center_x is not None and action_center_y is not None:
                        search_region = self._radius_to_region(action_center_x, action_center_y, search_radius)
                        logger.debug(f"[이미지 클릭] search_radius로 범위 계산: {search_region}")

                if not image_path:
                    logger.warning(f"{_YELLOW}⚠ 이미지 클릭: 이미지가 설정되지 않음{_RESET}")
                    return None
                if not Path(image_path).exists():
                    logger.warning(f"{_YELLOW}⚠ 이미지 파일 없음: {Path(image_path).name}{_RESET}")
                    return None

                if monitor_action.get("click_until_image_disappears", False):
                    return self._execute_monitor_image_click_until_disappears(
                        monitor_action,
                        image_path,
                        click_type,
                        search_confidence,
                        search_region,
                        alternate_route,
                    )

                location = prelocated_image
                if location is not None:
                    conf = location[2] if len(location) > 2 else 0
                    logger.debug(
                        f"[이미지 클릭] 모니터링 감지 위치 재사용: 위치=({location[0]}, {location[1]}), "
                        f"인식률={conf:.0%}"
                    )
                else:
                    location = self._find_image_on_screen(
                        image_path,
                        search_confidence,
                        search_region=search_region,
                        verify_color=bool(monitor_action.get("verify_image_color", False)),
                        verify_brightness=bool(monitor_action.get("verify_image_brightness", False)),
                    )
                if location:
                    x, y = location[0], location[1]
                    conf = location[2] if len(location) > 2 else 0
                    logger.debug(f"[이미지 클릭] 찾음: 위치=({x}, {y}), 인식률={conf:.0%}")
                    input_ctrl = get_input_controller()
                    if alternate_route:
                        if not self._move_mouse_to(x, y, alternate_route=True):
                            return None
                    else:
                        input_ctrl.move_to(x, y, duration=self._mouse_duration)
                    time.sleep(0.05)
                    if click_type == 'double_click':
                        input_ctrl.double_click()  # 이미 이동했으므로 좌표 없이 클릭
                        return f"이미지 더블클릭: {Path(image_path).name}"
                    elif click_type == 'right_click':
                        input_ctrl.right_click()  # 이미 이동했으므로 좌표 없이 클릭
                        return f"이미지 우클릭: {Path(image_path).name}"
                    else:
                        input_ctrl.click()  # 이미 이동했으므로 좌표 없이 클릭
                        return f"이미지 클릭: {Path(image_path).name}"
                else:
                    logger.warning(f"{_YELLOW}  ⚠ 이미지 찾지 못함: {Path(image_path).name}{_RESET}")
                    return None

            elif action_type == '스크롤':
                amount = monitor_action.get('amount', 0)
                if amount != 0:
                    input_ctrl = get_input_controller()
                    input_ctrl.scroll(amount)
                    return f"스크롤: {amount}"

            elif action_type == '드래그':
                from_x = monitor_action.get('from_x')
                from_y = monitor_action.get('from_y')
                to_x = monitor_action.get('to_x')
                to_y = monitor_action.get('to_y')
                if all(v is not None for v in [from_x, from_y, to_x, to_y]):
                    input_ctrl = get_input_controller()
                    input_ctrl.drag(from_x, from_y, to_x, to_y, duration=0.3)
                    return f"드래그: ({from_x},{from_y})→({to_x},{to_y})"

        except Exception as e:
            logger.error(f"{_RED}✗ 모니터링 액션 오류: {e}{_RESET}")
            return None

        return None

    def _execute_monitor_image_click_until_disappears(
        self,
        monitor_action: dict,
        image_path: str,
        click_type: str,
        search_confidence: float,
        search_region,
        alternate_route: bool,
    ) -> Optional[str]:
        """Click a monitor-action image until it disappears, using normal action limits."""
        try:
            configured_count = int(monitor_action.get("repeat_count", 1) or 1)
        except (TypeError, ValueError):
            configured_count = 1
        max_clicks = max(IMAGE_CLICK_UNTIL_DISAPPEAR_MIN_CLICKS, configured_count)
        delay = self._safe_float(
            monitor_action.get(
                "click_until_image_disappears_delay",
                monitor_action.get("repeat_delay", 0.5),
            ),
            0.5,
        )
        started = time.time()
        clicks = 0
        misses = 0
        input_ctrl = get_input_controller()
        image_name = Path(image_path).name

        while not self._stop_event.is_set():
            if time.time() - started >= IMAGE_CLICK_UNTIL_DISAPPEAR_MAX_SECONDS:
                return f"이미지 반복 클릭 시간초과: {image_name} ({clicks}회)"
            if clicks >= max_clicks:
                return f"이미지 반복 클릭 한도 도달: {image_name} ({clicks}회)"

            location = self._find_image_on_screen(
                image_path,
                search_confidence,
                search_region=search_region,
                verify_color=bool(monitor_action.get("verify_image_color", False)),
                verify_brightness=bool(monitor_action.get("verify_image_brightness", False)),
            )
            if not location:
                misses += 1
                if misses >= IMAGE_CLICK_UNTIL_DISAPPEAR_MISS_CONFIRM:
                    return f"이미지 사라짐: {image_name} ({clicks}회)"
                if self._stop_event.wait(timeout=0.15):
                    return None
                continue

            misses = 0
            x, y = int(location[0]), int(location[1])
            if alternate_route:
                if not self._move_mouse_to(x, y, alternate_route=True):
                    return None
            else:
                input_ctrl.move_to(x, y, duration=self._mouse_duration)
            time.sleep(0.05)

            if click_type == "double_click":
                input_ctrl.double_click()
            elif click_type == "right_click":
                input_ctrl.right_click()
            else:
                input_ctrl.click()

            clicks += 1
            if self._stop_event.wait(timeout=max(0.05, delay)):
                return None

        return None

    def test_single_monitor_action(self, monitor_action: dict) -> Tuple[bool, str]:
        """
        단일 모니터링 액션 테스트 실행 (공개 메서드)

        Args:
            monitor_action: 모니터링 액션 딕셔너리

        Returns:
            (성공여부, 메시지) 튜플
        """
        try:
            result = self._execute_monitor_action(monitor_action, confidence=0.65)
            if result:
                return True, result
            else:
                return False, f"실행 실패: {monitor_action.get('type', '알수없음')}"
        except Exception as e:
            return False, f"오류: {str(e)}"

    def test_monitor_actions_sequence(
        self,
        monitor_actions: List[dict],
        on_progress: Optional[Callable[[int, int, str], None]] = None,
    ) -> Tuple[bool, List[str]]:
        """
        모니터링 액션 시퀀스 테스트 실행

        Args:
            monitor_actions: 모니터링 액션 리스트
            on_progress: 진행 콜백 (current, total, message)

        Returns:
            (전체성공여부, 결과메시지 리스트) 튜플
        """
        results = []
        all_success = True

        for i, action in enumerate(monitor_actions):
            if on_progress:
                action_type = action.get('type', '알수없음')
                on_progress(i + 1, len(monitor_actions), f"실행 중: {action_type}")

            # 반복 횟수 처리
            repeat_count = action.get('repeat_count', 1)
            if not isinstance(repeat_count, int) or repeat_count < 1:
                repeat_count = 1
            for rep in range(repeat_count):
                success, msg = self.test_single_monitor_action(action)
                if repeat_count > 1:
                    results.append(f"[{i+1}] ({rep+1}/{repeat_count}) {msg}")
                else:
                    results.append(f"[{i+1}] {msg}")

                if not success:
                    all_success = False

                # 반복 사이 대기
                if rep < repeat_count - 1:
                    repeat_delay = action.get('repeat_delay', 0.5)
                    time.sleep(repeat_delay)

            # 액션 간 대기
            wait_after = action.get('wait_after', 0.3)
            time.sleep(wait_after)

        if on_progress:
            on_progress(len(monitor_actions), len(monitor_actions), "완료")

        return all_success, results

    def test_single_rule(self, rule: AutomationRule) -> Tuple[bool, str]:
        """
        단일 규칙 테스트 실행 (공개 메서드)

        Args:
            rule: 실행할 AutomationRule

        Returns:
            (성공여부, 메시지) 튜플
        """
        try:
            result = self._execute_rule(rule)
            return result.success, result.message
        except Exception as e:
            logger.error(f"규칙 테스트 실행 오류: {e}")
            return False, f"오류: {str(e)}"

    def test_rule_with_children(
        self,
        rule: AutomationRule,
        on_progress: Optional[Callable[[int, int, str], None]] = None,
        stop_flag: Optional[List[bool]] = None,
    ) -> Tuple[bool, List[str]]:
        """
        규칙과 모든 자식 규칙을 순차 실행 (공개 메서드)

        Args:
            rule: 실행할 AutomationRule (자식 포함)
            on_progress: 진행 콜백 (current, total, message)
            stop_flag: 정지 플래그 [bool] (외부에서 True로 설정하면 중지)

        Returns:
            (전체성공여부, 결과메시지 리스트) 튜플
        """
        results = []
        all_success = True

        # 모든 규칙 수집 (부모 + 자식들)
        def collect_rules(r, depth=0):
            collected = [(r, depth)]
            for child in getattr(r, 'children', []) or []:
                collected.extend(collect_rules(child, depth + 1))
            return collected

        all_rules = collect_rules(rule)
        total = len(all_rules)

        action_names = {
            "click": "클릭", "double_click": "더블클릭", "right_click": "우클릭",
            "type": "입력", "hotkey": "단축키", "key_press": "키", "scroll": "스크롤", "drag": "드래그",
        }

        for i, (r, depth) in enumerate(all_rules):
            # 정지 체크
            if stop_flag and stop_flag[0]:
                logger.info(f"[규칙 테스트] 중지됨 ({i}/{total})")
                break

            action_type = action_names.get(r.action_type, r.action_type or "동작")
            indent = "  " * depth
            logger.info(f"[규칙 테스트] {indent}[{i+1}/{total}] {action_type}")

            if on_progress:
                on_progress(i + 1, total, f"{action_type}")

            try:
                result = self._execute_rule(r)
                if result.success:
                    results.append(f"[{i+1}] {indent}✓ {action_type}: {result.message}")
                    logger.debug(f"[규칙 테스트] {indent}  ✓ 성공: {result.message}")
                else:
                    results.append(f"[{i+1}] {indent}✗ {action_type}: {result.message}")
                    logger.warning(f"[규칙 테스트] {indent}  ✗ 실패: {result.message}")
                    all_success = False
            except Exception as e:
                results.append(f"[{i+1}] {indent}✗ {action_type}: 오류 - {e}")
                logger.error(f"[규칙 테스트] {indent}  ✗ 예외: {e}")
                all_success = False

        if on_progress:
            on_progress(total, total, "완료")

        return all_success, results

    def _radius_to_region(self, center_x, center_y, radius):
        """search_radius + center 좌표를 [x1, y1, x2, y2] 영역으로 변환"""
        screen_w, screen_h = _get_screen_size_cached()
        return [max(0, center_x - radius), max(0, center_y - radius),
                min(screen_w, center_x + radius), min(screen_h, center_y + radius)]

    def _make_result(
        self,
        rule: AutomationRule,
        success: bool,
        message: str,
        start_time: datetime,
        skip_current_playlist: bool = False,
        rewind_previous_action: bool = False,
        rewind_delay: float = 0.0,
        monitoring_jump_index: int = -1,
        monitoring_jump_rule_id: str = "",
    ) -> RuleExecutionResult:
        """실행 결과 생성"""
        execution_time = int((datetime.now() - start_time).total_seconds() * 1000)
        return RuleExecutionResult(
            rule_id=rule.rule_id,
            success=success,
            message=message,
            executed_at=datetime.now(),
            execution_time_ms=execution_time,
            skip_current_playlist=skip_current_playlist,
            rewind_previous_action=rewind_previous_action,
            rewind_delay=rewind_delay,
            monitoring_jump_index=monitoring_jump_index,
            monitoring_jump_rule_id=monitoring_jump_rule_id,
        )

    def _update_progress(self, message: str) -> None:
        """진행 상태 업데이트"""
        try:
            self._progress.state = self._state
            self._progress.message = message
            if self._on_progress:
                self._on_progress(self._progress)
        except Exception as e:
            logger.debug(f"진행 상태 업데이트 실패: {e}")

    def execute_game_mode(self, config) -> bool:
        """
        게임 특화모드 실행 - 목표까지 자동 이동

        Args:
            config: GameModeConfig 객체

        Returns:
            bool: 목표 도달 여부
        """
        from ..analyzer.automation_models import GameModeConfig

        # 탐색 모드 분기: 좌표 기반 모드 사용 (이미지 모드 제거됨)
        nav_mode = getattr(config, 'navigation_mode', 'coordinate')

        # 이미지 설정이 없거나 좌표 설정이 있으면 좌표 모드로 강제 전환
        has_image_settings = (config.character_image and Path(config.character_image).exists() and
                              config.target_image and Path(config.target_image).exists())
        has_coord_settings = self._has_coordinate_reader_config(config)

        if nav_mode == 'coordinate' or (not has_image_settings and has_coord_settings):
            return self.execute_game_mode_coordinate(config)

        # === 이미지 기반 모드 (레거시 호환용) ===
        logger.info(f"{_GREEN}[특화모드] ========== 실행 시작 =========={_RESET}")

        # 설정 검증
        if not config.character_image or not Path(config.character_image).exists():
            logger.error(f"[특화모드] 캐릭터 이미지 없음 - 좌표 모드로 전환하세요")
            return False
        if not config.target_image or not Path(config.target_image).exists():
            logger.error(f"[특화모드] 목표 이미지 없음 - 좌표 모드로 전환하세요")
            return False

        # 개별 인식률 (하위호환)
        char_conf = getattr(config, 'character_confidence', None) or config.confidence
        target_conf = getattr(config, 'target_confidence', None) or config.confidence

        # 설정 출력
        logger.info(f"[특화모드] 캐릭터 이미지: {Path(config.character_image).name} (인식률: {char_conf:.0%})")
        logger.info(f"[특화모드] 목표 이미지: {Path(config.target_image).name} (인식률: {target_conf:.0%})")
        logger.info(f"[특화모드] 도달거리: {config.arrival_threshold}px")
        logger.info(f"[특화모드] 이동키: ↑={config.move_keys.get('up')} ↓={config.move_keys.get('down')} ←={config.move_keys.get('left')} →={config.move_keys.get('right')}")
        logger.info(f"[특화모드] 분석간격: {config.analysis_interval}초")

        # 검색 영역
        search_region = config.search_region if hasattr(config, 'search_region') else None
        if search_region:
            logger.info(f"[특화모드] 검색영역: ({search_region[0]},{search_region[1]}) ~ ({search_region[2]},{search_region[3]})")
        else:
            logger.info(f"[특화모드] 검색영역: 전체 화면")

        # ESC 키로 중지할 수 있도록 키보드 훅 설정
        import keyboard
        _escape_hotkey_id = keyboard.add_hotkey('escape', self._stop_event.set)
        logger.info(f"[특화모드] ESC 키로 중지 가능")

        current_key = None  # 현재 누르고 있는 키 (finally에서 접근 필요)

        try:
            iteration = 0
            max_iterations = 3000  # 최대 반복 횟수 (5분 정도)
            char_not_found_count = 0
            target_not_found_count = 0

            while not self._stop_event.is_set() and iteration < max_iterations:
                iteration += 1

                # 1. 캐릭터 위치 찾기
                char_result = self._find_image_on_screen(config.character_image, char_conf, search_region)
                time.sleep(0)  # GIL 해제
                if not char_result:
                    char_not_found_count += 1
                    if char_not_found_count <= 3 or char_not_found_count % 20 == 0:
                        logger.warning(f"[특화모드] 캐릭터 찾기 실패 (#{char_not_found_count})")
                    # 캐릭터 못 찾으면 키 해제
                    if current_key:
                        get_input_controller().key_up(current_key)
                        current_key = None
                        logger.info(f"[특화모드] 키 해제 (캐릭터 미발견)")
                    self._stop_event.wait(config.analysis_interval)
                    continue
                else:
                    char_not_found_count = 0

                char_x, char_y, _ = char_result

                # 2. 목표 위치 찾기
                target_result = self._find_image_on_screen(config.target_image, target_conf, search_region)
                time.sleep(0)  # GIL 해제
                if not target_result:
                    target_not_found_count += 1
                    if target_not_found_count <= 3 or target_not_found_count % 20 == 0:
                        logger.warning(f"[특화모드] 목표 찾기 실패 (#{target_not_found_count})")
                    # 목표 못 찾으면 키 해제
                    if current_key:
                        get_input_controller().key_up(current_key)
                        current_key = None
                        logger.info(f"[특화모드] 키 해제 (목표 미발견)")
                    self._stop_event.wait(config.analysis_interval)
                    continue
                else:
                    target_not_found_count = 0

                target_x, target_y, _ = target_result

                # 3. 방향 및 거리 계산
                dx = target_x - char_x
                dy = target_y - char_y
                distance = (dx**2 + dy**2) ** 0.5

                # 상세 로그 (매 프레임)
                logger.info(f"[특화모드] #{iteration} 캐릭터({char_x},{char_y}) 목표({target_x},{target_y}) dx={dx:+.0f} dy={dy:+.0f} 거리={distance:.0f}px")

                # 4. 도달 판정
                if distance < config.arrival_threshold:
                    # 도달하면 키 해제
                    if current_key:
                        get_input_controller().key_up(current_key)
                        current_key = None
                    logger.info(f"{_GREEN}[특화모드] ★★★ 목표 도달! (거리: {distance:.1f}px) ★★★{_RESET}")
                    return True

                # 5. 이동 방향 결정
                if abs(dx) > abs(dy):
                    # 좌우 이동 우선
                    if dx > 0:
                        new_key = config.move_keys.get("right", "right")
                        direction = "→ 오른쪽"
                    else:
                        new_key = config.move_keys.get("left", "left")
                        direction = "← 왼쪽"
                else:
                    # 상하 이동 우선
                    if dy > 0:
                        new_key = config.move_keys.get("down", "down")
                        direction = "↓ 아래"
                    else:
                        new_key = config.move_keys.get("up", "up")
                        direction = "↑ 위"

                logger.info(f"[특화모드] 방향결정: |dx|={abs(dx):.0f} |dy|={abs(dy):.0f} → {direction} (key={new_key})")

                # 6. 키 입력 (방향이 바뀔 때만 키 변경)
                if new_key != current_key:
                    # 이전 키 해제
                    if current_key:
                        get_input_controller().key_up(current_key)
                        logger.info(f"[특화모드] keyUp: {current_key}")
                    # 새 키 누르기
                    logger.info(f"{_YELLOW}[특화모드] 방향전환: {direction} (키: {new_key}, 거리: {distance:.0f}px, dx={dx:.0f}, dy={dy:.0f}){_RESET}")
                    get_input_controller().key_down(new_key)
                    logger.info(f"[특화모드] keyDown: {new_key}")
                    current_key = new_key

                # 7. 대기 (키는 계속 누른 상태)
                self._stop_event.wait(config.analysis_interval)

            if iteration >= max_iterations:
                logger.warning(f"{_YELLOW}[특화모드] 최대 반복 횟수 초과 ({max_iterations}){_RESET}")

            # 루프 종료 시 키 해제
            if current_key:
                get_input_controller().key_up(current_key)
                current_key = None
                logger.info(f"[특화모드] 루프 종료 - 키 해제")

            return False  # 중지됨 또는 타임아웃

        except Exception as e:
            logger.error(f"[특화모드] 오류 발생: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False
        finally:
            # 키 해제 (ESC나 오류로 종료 시에도 확실히 해제)
            if current_key:
                try:
                    get_input_controller().key_up(current_key)
                    logger.info(f"[특화모드] finally - 키 해제: {current_key}")
                except Exception as e:
                    logger.warning(f"[특화모드] 키 해제 실패: {e}")
            # ESC 핫키 제거 (ID 기반 — 다른 ESC 핫키 보호)
            try:
                keyboard.remove_hotkey(_escape_hotkey_id)
            except Exception:
                pass
            logger.info(f"{_GREEN}[특화모드] ========== 실행 종료 =========={_RESET}")

    def execute_game_mode_coordinate(self, config) -> bool:
        """
        좌표 기반 게임 특화모드 실행 (A* 경로탐색 + GameMap 활용)

        템플릿 매칭으로 화면에서 현재 X, Y 좌표를 읽고 경유지 순서대로 자동 이동합니다.
        A* 알고리즘으로 최적 경로를 탐색하고, 각 경유지별 독립 맵 데이터를 활용합니다.

        Args:
            config: GameModeConfig 객체 (navigation_mode='coordinate')

        Returns:
            bool: 목표 도달 여부
        """
        from ..utils.digit_templates import get_digit_matcher
        from .game_map import GameMap, DIRECTIONS_4
        from .simple_pathfinder import SimplePathfinder

        logger.info(f"{_GREEN}[좌표모드] ========== 실행 시작 (A* 경로탐색) =========={_RESET}")

        def _exec_arrival_keys_re(arr_keys):
            """arrival_keys 실행: list-of-dict 또는 구형 문자열 모두 지원"""
            if not arr_keys:
                return
            if isinstance(arr_keys, list):
                for kd in arr_keys:
                    if isinstance(kd, dict):
                        k = kd.get('key', '')
                        w = kd.get('wait_after', 0.3)
                        key_events = kd.get('key_events', []) or []
                        if key_events:
                            get_input_controller().replay_key_events(key_events)
                            time.sleep(max(0.1, w))
                        elif k:
                            get_input_controller().press(k)
                            time.sleep(max(0.1, w))
                    elif isinstance(kd, str) and kd.strip():
                        get_input_controller().press(kd.strip())
                        time.sleep(0.1)
            elif isinstance(arr_keys, str):
                for _ak in arr_keys.split(','):
                    _ak = _ak.strip()
                    if _ak:
                        get_input_controller().press(_ak)
                        time.sleep(0.1)

        # 맵핑 시스템 초기화
        import os
        map_name = config.name or "autosave"
        map_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "maps")
        mapping_enabled = getattr(config, 'mapping_enabled', True)
        current_segment_idx = 0
        waypoints = getattr(config, 'waypoints', []) or []

        # rule_id prefix (UI의 _get_segment_map_name과 동일 형식)
        _rid = getattr(config, '_rule_id', '') or ''
        _rid_prefix = f"{_rid.replace('rule_', '')}_" if _rid else ''

        def _segment_meta(seg_idx):
            if 0 <= seg_idx < len(waypoints):
                wp = waypoints[seg_idx]
                if isinstance(wp, (list, tuple)) and len(wp) >= 4 and isinstance(wp[3], dict):
                    return wp[3]
            return {}

        def _is_boss_segment(seg_idx):
            if 0 <= seg_idx < len(waypoints):
                wp = waypoints[seg_idx]
                if isinstance(wp, (list, tuple)) and len(wp) >= 2:
                    try:
                        return int(wp[0]) == 0 and int(wp[1]) == 0
                    except Exception:
                        return False
            return False

        def _uses_transient_local_map(seg_idx):
            if _is_boss_segment(seg_idx):
                return False
            route_starts = _segment_meta(seg_idx).get('route_starts', []) or []
            return not bool(route_starts)

        # 구간별 맵 파일명 헬퍼 (UI의 _get_segment_map_name과 동일 형식)
        def get_segment_map_path(seg_idx):
            """경유지 인덱스에 해당하는 맵 파일 경로 (경유지와 1:1 대응)"""
            import shutil
            waypoints = getattr(config, 'waypoints', []) or []
            if seg_idx < len(waypoints):
                wp = waypoints[seg_idx]
                seg_name = wp[2] if len(wp) >= 3 and wp[2] else f"경유지{seg_idx+1}"
            else:
                seg_name = f"경유지{seg_idx+1}"
            # 보스 경유지(0,0) 판별
            is_boss = False
            if seg_idx < len(waypoints):
                wp = waypoints[seg_idx]
                if isinstance(wp, (list, tuple)) and len(wp) >= 2:
                    if int(wp[0]) == 0 and int(wp[1]) == 0:
                        is_boss = True
            # UI와 동일 형식: {rid_prefix}{seg_idx:02d}_{seg_name}_map.json
            if is_boss:
                new_path = os.path.join(map_dir, f"{_rid_prefix}{seg_idx:02d}_{seg_name}_boss_map.json")
            elif _uses_transient_local_map(seg_idx):
                new_path = os.path.join(map_dir, f"{_rid_prefix}{seg_idx:02d}_{seg_name}_local_map.json")
            else:
                new_path = os.path.join(map_dir, f"{_rid_prefix}{seg_idx:02d}_{seg_name}_map.json")
            # 파일이 이미 존재하면 바로 반환
            if os.path.exists(new_path):
                return new_path
            # 공유 맵 파일(소스)이 있으면 자체 경로로 복사 (원본 보호)
            if seg_idx < len(waypoints):
                wp = waypoints[seg_idx]
                if isinstance(wp, (list, tuple)) and len(wp) >= 4 and isinstance(wp[3], dict):
                    shared = wp[3].get('map_file')
                    if shared and os.path.exists(shared):
                        try:
                            os.makedirs(map_dir, exist_ok=True)
                            shutil.copy2(shared, new_path)
                            logger.info(f"[좌표모드] 공유맵 복사: {os.path.basename(shared)} → {os.path.basename(new_path)}")
                        except Exception:
                            pass
                        return new_path
            # 같은 이름의 다른 경유지가 있는지 확인
            has_dup = False
            for i, w in enumerate(waypoints):
                if i != seg_idx:
                    other = w[2] if isinstance(w, (list, tuple)) and len(w) >= 3 and w[2] else f"경유지{i+1}"
                    if other == seg_name:
                        has_dup = True
                        break
            # 마이그레이션: 새 파일 없고, 이름 중복 아닐 때만
            if not has_dup:
                # rule_id prefix 없는 기존 파일도 소스로 검색
                no_prefix_path = os.path.join(map_dir, f"{seg_idx:02d}_{seg_name}_{'boss_' if is_boss else ''}map.json")
                if is_boss:
                    old_candidates = [
                        no_prefix_path,
                        os.path.join(map_dir, f"{map_name}_{seg_idx:02d}_{seg_name}_boss_map.json"),
                        os.path.join(map_dir, f"{map_name}_{seg_idx}_{seg_name}_boss_map.json"),
                        os.path.join(map_dir, f"{map_name}_{seg_name}_boss{seg_idx}_map.json"),
                        os.path.join(map_dir, f"{map_name}_{seg_name}_map.json"),
                    ]
                else:
                    old_candidates = [no_prefix_path]
                    if _uses_transient_local_map(seg_idx):
                        old_candidates.extend([
                            os.path.join(map_dir, f"{map_name}_{seg_idx:02d}_{seg_name}_local_map.json"),
                            os.path.join(map_dir, f"{map_name}_{seg_idx}_{seg_name}_local_map.json"),
                            os.path.join(map_dir, f"{map_name}_{seg_name}_local_map.json"),
                        ])
                    old_candidates.extend([
                        os.path.join(map_dir, f"{map_name}_{seg_idx:02d}_{seg_name}_map.json"),
                        os.path.join(map_dir, f"{map_name}_{seg_idx}_{seg_name}_map.json"),
                        os.path.join(map_dir, f"{map_name}_{seg_name}_map.json"),
                    ])
                for old_path in old_candidates:
                    if os.path.exists(old_path):
                        try:
                            os.makedirs(map_dir, exist_ok=True)
                            shutil.copy2(old_path, new_path)
                            logger.info(f"[좌표모드] 맵 마이그레이션: {os.path.basename(old_path)} → {os.path.basename(new_path)}")
                        except Exception:
                            pass
                        break
            return new_path

        # 첫 번째 경유지 맵 로드
        def _sanitize_segment_start_pos(game_map_ref, seg_idx):
            if game_map_ref is None:
                return
            _meta = _segment_meta(seg_idx)
            route_starts = []
            for item in _meta.get('route_starts', []) or []:
                try:
                    route_starts.append((int(item.get('x')), int(item.get('y'))))
                except Exception:
                    continue
            if not route_starts:
                return
            preferred_start = tuple(route_starts[0])
            current_start = tuple(game_map_ref.start_pos) if getattr(game_map_ref, 'start_pos', None) is not None else None
            if current_start is not None and current_start not in route_starts:
                game_map_ref.start_pos = None
                adjacent_passable = 0
                for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
                    if game_map_ref.is_passable(current_start[0] + dx, current_start[1] + dy):
                        adjacent_passable += 1
                if adjacent_passable >= 2:
                    game_map_ref.mark_passable(current_start[0], current_start[1])
            # route_start는 메타 기준점으로만 유지하고, 실제 진입 시작 타일은
            # 포탈 직후 경로가 이어지도록 계속 걸을 수 있어야 한다.
            game_map_ref.start_pos = None
            game_map_ref.mark_passable(preferred_start[0], preferred_start[1])
            game_map_ref.start_pos = preferred_start

        game_map = GameMap(name=map_name)
        seg0_path = get_segment_map_path(0)
        if os.path.exists(seg0_path):
            game_map.load(seg0_path)
            _sanitize_segment_start_pos(game_map, 0)
            stats = game_map.get_statistics()
            logger.info(f"[좌표모드] 첫 경유지 맵 로드: {stats['total_tiles']}개 타일 (이동가능: {stats['passable_tiles']}, 벽: {stats['blocked_tiles']})")
        else:
            # 하위호환: 기존 '시작' 맵 또는 단일 맵 파일
            loaded = False
            for fallback in [f"{map_name}_시작_map.json", f"{map_name}_map.json"]:
                old_map_path = os.path.join(map_dir, fallback)
                if os.path.exists(old_map_path):
                    game_map.load(old_map_path)
                    _sanitize_segment_start_pos(game_map, 0)
                    stats = game_map.get_statistics()
                    logger.info(f"[좌표모드] 호환 맵 로드: {old_map_path} ({stats['total_tiles']}개 타일)")
                    loaded = True
                    break
            if not loaded:
                logger.info(f"[좌표모드] 기존 맵 없음, 새 맵 생성")

        def get_seg_name(seg_idx):
            """경유지 이름 반환"""
            waypoints = getattr(config, 'waypoints', []) or []
            if seg_idx < len(waypoints):
                wp = waypoints[seg_idx]
                return wp[2] if len(wp) >= 3 and wp[2] else f"경유지{seg_idx+1}"
            return f"경유지{seg_idx+1}"

        def switch_segment_map(new_seg_idx):
            """구간 맵 전환: 현재 맵 저장 → 새 구간 맵 로드"""
            nonlocal game_map, pathfinder, current_segment_idx
            old_name = get_seg_name(current_segment_idx)
            new_name = get_seg_name(new_seg_idx)
            # 현재 맵 저장
            try:
                save_path = get_segment_map_path(current_segment_idx)
                os.makedirs(os.path.dirname(save_path), exist_ok=True)
                _old_map_ref = game_map
                _sanitize_segment_start_pos(_old_map_ref, current_segment_idx)
                def _save_old_segment_async(_map=_old_map_ref, _path=save_path, _name=old_name):
                    try:
                        _map.save(_path)
                        logger.info(f"[좌표모드] '{_name}' 맵 저장: {_path}")
                    except Exception as _save_e:
                        logger.error(f"[좌표모드] 맵 저장 실패: {_save_e}")
                threading.Thread(target=_save_old_segment_async, daemon=True).start()
            except Exception as e:
                logger.error(f"[좌표모드] 맵 저장 실패: {e}")
            # 새 구간 맵 로드
            current_segment_idx = new_seg_idx
            game_map = GameMap(name=f"{map_name}_{new_name}")
            pathfinder = SimplePathfinder(game_map)
            new_path = get_segment_map_path(new_seg_idx)
            if os.path.exists(new_path):
                game_map.load(new_path)
                _sanitize_segment_start_pos(game_map, new_seg_idx)
                stats = game_map.get_statistics()
                logger.info(f"[좌표모드] '{old_name}'→'{new_name}' 전환, 맵 로드: {stats['total_tiles']}개 타일")
            else:
                logger.info(f"[좌표모드] '{old_name}'→'{new_name}' 전환, 새 맵 생성")

        def _reload_current_segment_map_runtime(seg_idx):
            """세그먼트 전환 직후 런타임 오염이 의심될 때 현재 맵을 디스크 기준으로 다시 읽는다."""
            nonlocal game_map, pathfinder
            map_path = get_segment_map_path(seg_idx)
            if not map_path or not os.path.exists(map_path):
                return False
            seg_name = get_seg_name(seg_idx)
            try:
                fresh_map = GameMap(name=f"{map_name}_{seg_name}")
                fresh_map.load(map_path)
                _sanitize_segment_start_pos(fresh_map, seg_idx)
                game_map = fresh_map
                pathfinder = SimplePathfinder(game_map)
                logger.warning(f"[좌표모드] '{seg_name}' 런타임 맵 재로드")
                return True
            except Exception as e:
                logger.error(f"[좌표모드] '{seg_name}' 런타임 맵 재로드 실패: {e}")
                return False

        if mapping_enabled:
            logger.info(f"[좌표모드] 맵핑 시스템 활성화")

        # A* 경로탐색기 초기화 (GameMap 활용)
        pathfinder = SimplePathfinder(game_map)

        # 템플릿 매처 확인
        matcher = get_digit_matcher()
        if not matcher.has_all_templates():
            missing = matcher.get_missing_digits()
            logger.error(f"[좌표모드] 숫자 템플릿 미완성: {missing}")
            return False

        # 설정 검증
        if not self._has_coordinate_reader_config(config):
            if bool(getattr(config, "coord_anchor_enabled", False)):
                logger.error("[좌표모드] X/Y 좌표바 영역 또는 X/Y 기준 이미지가 설정되지 않음")
            else:
                logger.error("[좌표모드] X/Y 좌표 영역이 설정되지 않음")
            return False

        # 설정값
        smooth_move = getattr(config, 'smooth_move', False)
        interval = config.analysis_interval

        # 경유지 기반 목표 리스트 (target_x/y 제거, 경유지만 사용)
        waypoints_raw = getattr(config, 'waypoints', []) or []
        final_wp_idx = getattr(config, 'final_waypoint_idx', -1)
        if final_wp_idx < 0 or final_wp_idx >= len(waypoints_raw):
            final_wp_idx = len(waypoints_raw) - 1

        all_targets = []  # Same tuple shape/order as editor mode.
        for i, wp in enumerate(waypoints_raw):
            if i > final_wp_idx:
                break
            if isinstance(wp, (list, tuple)) and len(wp) >= 2:
                wp_name = wp[2] if len(wp) >= 3 and wp[2] else f"경유지{i+1}"
                wp_cfg = wp[3] if len(wp) >= 4 and isinstance(wp[3], dict) else {}
                wp_x, wp_y = int(wp[0]), int(wp[1])
                _is_boss = (wp_x == 0 and wp_y == 0)
                _boss_img_path = wp_cfg.get('target_image')
                _char_img_path = wp_cfg.get('character_image')
                _arr_keys = wp_cfg.get('arrival_keys', [])
                _re_raw = wp_cfg.get('route_ends', [])
                _route_ends = []
                _disabled_ends = []
                for r in _re_raw:
                    if isinstance(r, dict) and 'x' in r and 'y' in r:
                        _pt = (int(r['x']), int(r['y']))
                        if r.get('enabled', True):
                            _route_ends.append(_pt)
                        else:
                            _disabled_ends.append(_pt)
                _route_starts = []
                for s in wp_cfg.get('route_starts', []) or []:
                    if isinstance(s, dict) and 'x' in s and 'y' in s:
                        _route_starts.append((int(s['x']), int(s['y'])))
                _route_walls = []
                for w in wp_cfg.get('route_walls', []) or []:
                    if isinstance(w, dict) and 'x' in w and 'y' in w:
                        _route_walls.append((int(w['x']), int(w['y'])))
                _route_walls.extend(_disabled_ends)
                _map_locked = wp_cfg.get('map_locked', False)
                all_targets.append((
                    wp_x, wp_y, wp_name, _is_boss,
                    _boss_img_path, _char_img_path,
                    _arr_keys, _route_ends, _route_starts,
                    _map_locked, _route_walls,
                ))

        if not all_targets:
            logger.error("[좌표모드] 경유지가 없습니다. 경유지를 추가하세요.")
            return False

        # 보스 경유지(0,0) 스킵 (RuleExecutor에서는 보스 던전 미지원)
        current_target_idx = 0
        while current_target_idx < len(all_targets) and all_targets[current_target_idx][3]:
            logger.warning(f"[좌표모드] 보스 경유지 '{all_targets[current_target_idx][2]}' 스킵 (부분실행 미지원)")
            current_target_idx += 1
        if current_target_idx >= len(all_targets):
            logger.error("[좌표모드] 모든 경유지가 보스 경유지 → 실행 불가")
            return False
        def _pick_target(tidx):
            _re = all_targets[tidx][7] if len(all_targets[tidx]) > 7 else []
            if _re:
                return random.choice(_re)
            return all_targets[tidx][0], all_targets[tidx][1]

        target_x, target_y = _pick_target(current_target_idx)

        # 첫 경유지 route_walls 등록
        if all_targets and len(all_targets[current_target_idx]) > 10:
            for rw in all_targets[current_target_idx][10]:
                if isinstance(rw, tuple) and len(rw) == 2:
                    game_map.mark_blocked(int(rw[0]), int(rw[1]))

        # 첫 경유지 map_locked 적용
        if all_targets and len(all_targets[current_target_idx]) > 9 and all_targets[current_target_idx][9]:
            mapping_enabled = False
            logger.info("[좌표모드] 잠금맵 → 맵핑 비활성화")

        # 탈출 스킬 설정
        escape_skill_enabled = getattr(config, 'escape_skill_enabled', False)
        escape_skill_key = getattr(config, 'escape_skill_key', 'z') or 'z'
        escape_skill_stuck_threshold = getattr(config, 'escape_skill_stuck_threshold', 10)
        escape_skill_direction_count = getattr(config, 'escape_skill_direction_count', 5)
        escape_skill_wait_after = getattr(config, 'escape_skill_wait_after', 0.5)
        escape_skill_cooldown = getattr(config, 'escape_skill_cooldown', 10.0)
        last_escape_time = 0
        def _load_cv_template_image(path_str):
            """배포 환경에서도 한글/윈도우 경로를 안정적으로 읽는다."""
            if not path_str:
                return None
            try:
                _img = cv2.imread(path_str)
                if _img is not None:
                    return _img
            except Exception:
                pass
            try:
                _raw = Path(path_str).read_bytes()
                if not _raw:
                    return None
                _buf = np.frombuffer(_raw, dtype=np.uint8)
                if _buf.size == 0:
                    return None
                return cv2.imdecode(_buf, cv2.IMREAD_COLOR)
            except Exception:
                return None

        auto_skill_enabled = getattr(config, 'auto_skill_enabled', False)
        auto_skill_key = getattr(config, 'auto_skill_key', '') or ''
        auto_skill_cooldown = getattr(config, 'auto_skill_cooldown', 5.0)
        auto_skill_cd_image = getattr(config, 'auto_skill_cooldown_image', '') or ''
        if auto_skill_cd_image and not Path(auto_skill_cd_image).exists():
            auto_skill_cd_image = ''
        auto_skill_cd_region = getattr(config, 'auto_skill_cd_region', None)
        _auto_skill_cd_tmpl = None
        if auto_skill_cd_image:
            try:
                _auto_skill_cd_tmpl = _load_cv_template_image(auto_skill_cd_image)
            except Exception:
                _auto_skill_cd_tmpl = None
        _auto_skill_diag_last_sig = None
        _auto_skill_diag_last_time = 0.0
        def _log_auto_skill_diag(sig, msg, force=False):
            nonlocal _auto_skill_diag_last_sig, _auto_skill_diag_last_time
            _now_diag = time.time()
            if force or sig != _auto_skill_diag_last_sig or (_now_diag - _auto_skill_diag_last_time) >= 3.0:
                logger.info(f"[coordinate-mode][auto-skill-diag] {msg}")
                _auto_skill_diag_last_sig = sig
                _auto_skill_diag_last_time = _now_diag

        wp_info = [(t[0], t[1], t[2]) for t in all_targets]
        logger.info(f"[좌표모드] 경유지 {len(all_targets)}개: {wp_info}")
        logger.info(f"[좌표모드] 최종 목표: {all_targets[-1][2]} ({all_targets[-1][0]},{all_targets[-1][1]})")
        logger.info(f"[좌표모드] 현재 목표: ({target_x}, {target_y}) [{all_targets[current_target_idx][2]}] [{current_target_idx+1}/{len(all_targets)}]")
        if (
            bool(getattr(config, "coord_anchor_enabled", False)) and
            self._valid_coord_region(getattr(config, "coord_x_region", None)) and
            self._valid_coord_region(getattr(config, "coord_y_region", None))
        ):
            _x_anchor_name = Path(getattr(config, "coord_x_anchor_image", "") or "").name or "-"
            _y_anchor_name = Path(getattr(config, "coord_y_anchor_image", "") or "").name or "-"
            logger.info(f"[좌표모드] X 좌표바 영역: {getattr(config, 'coord_x_region', None)}")
            logger.info(f"[좌표모드] Y 좌표바 영역: {getattr(config, 'coord_y_region', None)}")
            logger.info(f"[좌표모드] X/Y 기준 이미지: X={_x_anchor_name} Y={_y_anchor_name}")
        else:
            logger.info(f"[좌표모드] X 영역: {config.coord_x_region}")
            logger.info(f"[좌표모드] Y 영역: {config.coord_y_region}")
        if escape_skill_enabled:
            logger.info(f"[좌표모드] 탈출스킬: 키={escape_skill_key}, 발동조건={escape_skill_stuck_threshold}회")
        logger.info(f"[좌표모드] 이동키: ↑={config.move_keys.get('up')} ↓={config.move_keys.get('down')} ←={config.move_keys.get('left')} →={config.move_keys.get('right')}")
        logger.info(f"[좌표모드] 알고리즘: A* 경로탐색 + GameMap")

        import keyboard
        if auto_skill_enabled and auto_skill_key:
            _as_mode = "image" if _auto_skill_cd_tmpl is not None else f"timer({auto_skill_cooldown}s)"
            logger.info(f"[coordinate-mode] auto-skill: key={auto_skill_key}, cooldown={_as_mode}")
            _log_auto_skill_diag(
                ("config", bool(auto_skill_key), bool(auto_skill_cd_image), (_auto_skill_cd_tmpl is not None), tuple(auto_skill_cd_region) if isinstance(auto_skill_cd_region, (list, tuple)) else None),
                f"config enabled={auto_skill_enabled} key={auto_skill_key or '-'} image={'Y' if auto_skill_cd_image else 'N'} tmpl={'Y' if _auto_skill_cd_tmpl is not None else 'N'} region={auto_skill_cd_region}",
                force=True,
            )
            if auto_skill_cd_image and _auto_skill_cd_tmpl is None:
                _log_auto_skill_diag(
                    ("config", "template_load_fail", auto_skill_cd_image),
                    f"template load failed path={auto_skill_cd_image}",
                    force=True,
                )
        _escape_hotkey_id = keyboard.add_hotkey('escape', self._stop_event.set)
        logger.info("[좌표모드] ESC 키로 중지 가능")

        # 상태 변수
        prev_x, prev_y = None, None
        last_dir = None
        stuck_count = 0
        total_stuck_count = 0  # 탈출 스킬용 연속 정체 카운트
        transition_recovery_attempts = 0
        current_path = []
        path_index = 0
        tick_counter = 0  # soft_blocked tick용 카운터
        explored_from = {}  # {(x,y): set(방향)} — 자동 탐색용
        segment_transition_stabilize_until = 0.0
        segment_transition_last_coord = None
        segment_transition_stable_hits = 0
        segment_transition_logged = False
        unknown_path_fails = 0  # A* unknown 경로 연속 실패 횟수

        def press_key(direction):
            """방향키 누르기"""
            if self._stop_event.is_set():
                return
            key = config.move_keys.get(direction, direction)
            if smooth_move:
                self._smooth_key_input([key], interval)
            else:
                get_input_controller().press(key)
                # 중단 가능한 sleep
                sleep_until = time.time() + interval
                while time.time() < sleep_until:
                    if self._stop_event.is_set():
                        return
                    time.sleep(0.02)

        # 경로 위치 → 인덱스 역매핑 (O(1) 조회용)
        path_pos_index = {}  # {(x,y): index}

        def _rebuild_path_index():
            """경로 변경 시 역매핑 재구축"""
            nonlocal path_pos_index
            path_pos_index = {}
            for i, pos in enumerate(current_path):
                if pos not in path_pos_index:
                    path_pos_index[pos] = i

        def _wait_for_actual_jump(seg_idx):
            """Wait for a real portal jump before switching to a start-based next segment."""
            if seg_idx >= len(all_targets) - 1:
                return False
            _next_meta = _segment_meta(seg_idx + 1)
            _next_starts = _next_meta.get('route_starts', []) or []
            _arr_keys = all_targets[seg_idx][6] if len(all_targets[seg_idx]) > 6 else []
            return bool(_next_starts) and not bool(_arr_keys)

        def find_path_direction(cx, cy, tx, ty):
            """
            A* 경로탐색 + 자동 탐색으로 다음 방향 결정
            1. 알려진 경로로 탐색
            2. 미탐색 영역 포함해서 탐색
            3. 스마트 탐색 (미시도 방향 우선)
            4. 비벽 방향 재시도 (백트래킹)
            5. 최후 직진
            """
            nonlocal current_path, path_index, explored_from, unknown_path_fails
            nonlocal pathfinder, game_map, transition_recovery_attempts

            current_pos = (cx, cy)
            target_pos = (tx, ty)

            # 경로가 있고 현재 위치가 경로 상에 있으면 따라가기
            if current_path and path_index < len(current_path):
                idx = path_pos_index.get(current_pos)
                if idx is not None:
                    path_index = idx
                    if path_index + 1 < len(current_path):
                        next_pos = current_path[path_index + 1]
                        if not game_map.is_blocked(next_pos[0], next_pos[1]) and not game_map.is_soft_blocked(next_pos[0], next_pos[1]):
                            dx = next_pos[0] - cx
                            dy = next_pos[1] - cy
                            for d, (ddx, ddy) in DIRECTIONS_4.items():
                                if ddx == dx and ddy == dy:
                                    return d

            # 1차: 알려진 이동가능 경로만 (soft_blocked 제외)
            result = pathfinder.find_path(current_pos, target_pos, allow_unknown=False, allow_soft_blocked=False, max_iterations=20000, stop_event=self._stop_event)
            time.sleep(0)  # GIL 해제
            if result.found and len(result.directions) > 0:
                current_path = result.path
                path_index = 0
                _rebuild_path_index()
                unknown_path_fails = 0
                logger.info(f"[좌표모드] A* 경로 발견: {len(result.path)}칸")
                return result.directions[0]

            if self._stop_event.is_set():
                return None

            # 1.5차: soft_blocked 허용
            result = pathfinder.find_path(current_pos, target_pos, allow_unknown=False, allow_soft_blocked=True, max_iterations=20000, stop_event=self._stop_event)
            time.sleep(0)  # GIL 해제
            if result.found and len(result.directions) > 0:
                current_path = result.path
                path_index = 0
                _rebuild_path_index()
                unknown_path_fails = 0
                logger.info(f"[좌표모드] A* 경로 발견 (soft_blocked 허용): {len(result.path)}칸")
                return result.directions[0]

            if self._stop_event.is_set():
                return None

            # 2차: 미탐색 영역 포함 (연속 실패 3회 이상이면 건너뜀)
            if unknown_path_fails < 3:
                result = pathfinder.find_path(current_pos, target_pos, allow_unknown=True, unknown_cost=3, max_iterations=20000, stop_event=self._stop_event)
                time.sleep(0)  # GIL 해제
                if result.found and len(result.directions) > 0:
                    current_path = result.path
                    path_index = 0
                    _rebuild_path_index()
                    logger.info(f"[좌표모드] A* 탐색 경로: {len(result.path)}칸 (미지 영역 포함)")
                    return result.directions[0]

            if self._stop_event.is_set():
                return None

            _has_route_starts = bool(len(all_targets[current_target_idx]) > 8 and all_targets[current_target_idx][8])
            _map_locked = bool(len(all_targets[current_target_idx]) > 9 and all_targets[current_target_idx][9])
            if transition_recovery_attempts > 0 and _has_route_starts:
                transition_recovery_attempts -= 1
                game_map.mark_passable(cx, cy)
                _sanitize_segment_start_pos(game_map, current_target_idx)
                result = pathfinder.find_path(
                    current_pos,
                    target_pos,
                    allow_unknown=True,
                    unknown_cost=3,
                    max_iterations=20000,
                    stop_event=self._stop_event,
                    allow_soft_blocked=False,
                    respect_blocked_edges=True,
                )
                time.sleep(0)
                if self._stop_event.is_set():
                    return None
                if not (result.found and len(result.directions) > 0):
                    result = pathfinder.find_path(
                        current_pos,
                        target_pos,
                        allow_unknown=True,
                        unknown_cost=3,
                        max_iterations=20000,
                        stop_event=self._stop_event,
                        allow_soft_blocked=True,
                        respect_blocked_edges=True,
                    )
                    time.sleep(0)
                    if self._stop_event.is_set():
                        return None
                if result.found and len(result.directions) > 0:
                    current_path = result.path
                    path_index = 0
                    _rebuild_path_index()
                    unknown_path_fails = 0
                    logger.info(f"[좌표모드] 진입직후 경로복구: {len(result.path)}칸")
                    return result.directions[0]

                if _map_locked and _reload_current_segment_map_runtime(current_target_idx):
                    game_map.mark_passable(cx, cy)
                    _sanitize_segment_start_pos(game_map, current_target_idx)
                    result = pathfinder.find_path(
                        current_pos,
                        target_pos,
                        allow_unknown=True,
                        unknown_cost=3,
                        max_iterations=20000,
                        stop_event=self._stop_event,
                        allow_soft_blocked=False,
                        respect_blocked_edges=True,
                    )
                    time.sleep(0)
                    if self._stop_event.is_set():
                        return None
                    if not (result.found and len(result.directions) > 0):
                        result = pathfinder.find_path(
                            current_pos,
                            target_pos,
                            allow_unknown=True,
                            unknown_cost=3,
                            max_iterations=20000,
                            stop_event=self._stop_event,
                            allow_soft_blocked=True,
                            respect_blocked_edges=True,
                        )
                        time.sleep(0)
                        if self._stop_event.is_set():
                            return None
                    if result.found and len(result.directions) > 0:
                        current_path = result.path
                        path_index = 0
                        _rebuild_path_index()
                        unknown_path_fails = 0
                        logger.info(f"[좌표모드] 잠금맵 재로드 경로복구: {len(result.path)}칸")
                        return result.directions[0]

            # 3차: 스마트 탐색 — 현재 위치에서 아직 안 가본 방향 시도
            return None

        try:
            iteration = 0
            max_iterations = 5000
            guard_target_idx = current_target_idx
            guard_iterations = 0
            coord_fail_count = 0
            max_coord_fails = 50  # 연속 50회 실패 시 중단

            while not self._stop_event.is_set():
                iteration += 1
                if current_target_idx != guard_target_idx:
                    guard_target_idx = current_target_idx
                    guard_iterations = 0
                guard_iterations += 1
                if guard_iterations > max_iterations:
                    break

                # 1. 템플릿 매칭으로 현재 좌표 읽기
                current_x, current_y = self._read_game_coordinates(matcher, config)

                if current_x is None or current_y is None:
                    coord_fail_count += 1
                    if coord_fail_count >= max_coord_fails:
                        logger.error(f"[좌표모드] 좌표 읽기 연속 {max_coord_fails}회 실패 → 중단")
                        return False
                    if coord_fail_count % 10 == 1:
                        logger.warning(f"[좌표모드] #{iteration} 좌표 읽기 실패 ({coord_fail_count}회 연속)")
                    time.sleep(max(interval, 0.05))
                    continue

                coord_fail_count = 0  # 성공 시 리셋
                current_x = int(current_x)
                current_y = int(current_y)

                # 좌표 범위 검증 (OCR 오독 필터)
                if abs(current_x) > 500 or abs(current_y) > 500:
                    coord_fail_count += 1
                    if coord_fail_count % 10 == 1:
                        logger.warning(f"[??????] #{iteration} ??? ??? ???: ({current_x},{current_y})")
                    time.sleep(max(interval, 0.05))
                    continue

                if segment_transition_stabilize_until > 0.0:
                    now_ts = time.time()
                    if now_ts < segment_transition_stabilize_until:
                        if not segment_transition_logged:
                            logger.info("[coordinate-mode] segment transition stabilizing...")
                            segment_transition_logged = True
                        if segment_transition_last_coord is None:
                            segment_transition_stable_hits = 1
                        else:
                            px, py = segment_transition_last_coord
                            if abs(current_x - px) + abs(current_y - py) <= 1:
                                segment_transition_stable_hits += 1
                            else:
                                segment_transition_stable_hits = 1
                        segment_transition_last_coord = (current_x, current_y)
                        prev_x, prev_y = current_x, current_y
                        last_dir = None
                        current_path = []
                        path_index = 0
                        path_pos_index = {}
                        if segment_transition_stable_hits < 2:
                            time.sleep(0.05)
                            continue
                        segment_transition_stabilize_until = 0.0
                        logger.info(f"[coordinate-mode] segment transition stabilized: ({current_x},{current_y})")
                    else:
                        segment_transition_stabilize_until = 0.0

                # 1.5. 첫 반복: 시작 위치를 이동가능으로 등록
                if prev_x is None and mapping_enabled:
                    game_map.mark_passable(current_x, current_y)

                # 2. 도착 체크 (좌표 모드: threshold 최대 2로 제한)
                # route_ends가 있으면 어느 하나에 도착해도 경유지 완료
                _cur_route_ends = all_targets[current_target_idx][7] if len(all_targets[current_target_idx]) > 7 else []
                _arr_keys_cur = all_targets[current_target_idx][6] if len(all_targets[current_target_idx]) > 6 else []
                if _cur_route_ends:
                    _arrived_exact = any(current_x == ax and current_y == ay for ax, ay in _cur_route_ends)
                    _arrived_near = any(abs(current_x - ax) + abs(current_y - ay) <= 1 for ax, ay in _cur_route_ends)
                    _arrived = _arrived_exact or (bool(_arr_keys_cur) and _arrived_near)
                else:
                    _arrived = (current_x == target_x and current_y == target_y)
                _need_actual_jump = _wait_for_actual_jump(current_target_idx)
                if _arrived and not _need_actual_jump:
                    current_target_idx += 1
                    if current_target_idx >= len(all_targets):
                        # 최종 도착 키 입력
                        _arr_keys_final = all_targets[current_target_idx - 1][6] if len(all_targets[current_target_idx - 1]) > 6 else []
                        if _arr_keys_final:
                            _exec_arrival_keys_re(_arr_keys_final)
                            logger.info(f"[좌표모드] 최종 도착 키 입력: {_arr_keys_final}")
                        logger.info(f"{_GREEN}[좌표모드] ★★★ 최종 목표 도달! ({current_x}, {current_y}) ★★★{_RESET}")
                        return True
                    else:
                        # 도착 키 입력 (전환 전 현재 경유지)
                        _prev_idx = current_target_idx - 1
                        _arr_keys = all_targets[_prev_idx][6] if len(all_targets[_prev_idx]) > 6 else []
                        if _arr_keys:
                            _exec_arrival_keys_re(_arr_keys)
                            logger.info(f"[좌표모드] 도착 키 입력: {_arr_keys}")
                        # 보스 경유지 스킵
                        while current_target_idx < len(all_targets) and all_targets[current_target_idx][3]:
                            logger.warning(f"[좌표모드] 보스 경유지 '{all_targets[current_target_idx][2]}' 스킵")
                            current_target_idx += 1
                        if current_target_idx >= len(all_targets):
                            logger.info(f"{_GREEN}[좌표모드] ★★★ 최종 목표 도달! ({current_x}, {current_y}) ★★★{_RESET}")
                            return True
                        # 구간 맵 전환
                        switch_segment_map(current_target_idx)
                        target_x, target_y = _pick_target(current_target_idx)
                        seg_name = all_targets[current_target_idx][2]
                        logger.info(f"{_GREEN}[좌표모드] ▶ 경유지 도달! 다음: ({target_x},{target_y}) [{seg_name}] [{current_target_idx+1}/{len(all_targets)}]{_RESET}")
                        # route_walls 등록
                        _rw = all_targets[current_target_idx][10] if len(all_targets[current_target_idx]) > 10 else []
                        for _w in _rw:
                            if isinstance(_w, tuple) and len(_w) == 2:
                                game_map.mark_blocked(int(_w[0]), int(_w[1]))
                        # map_locked 적용
                        _ml = all_targets[current_target_idx][9] if len(all_targets[current_target_idx]) > 9 else False
                        if _ml:
                            mapping_enabled = False
                        else:
                            mapping_enabled = getattr(config, 'mapping_enabled', True)
                        stuck_count = 0
                        total_stuck_count = 0
                        transition_recovery_attempts = 3
                        current_path = []
                        path_index = 0
                        path_pos_index = {}
                        explored_from = {}
                        segment_transition_stabilize_until = time.time() + 1.5
                        segment_transition_last_coord = None
                        segment_transition_stable_hits = 0
                        segment_transition_logged = False
                        unknown_path_fails = 0
                        last_dir = None
                        prev_x, prev_y = current_x, current_y
                        time.sleep(0.3)
                        continue

                # 2.5. 포탈 감지: 감속 구간(목표/route_ends 3칸 이내)에서 좌표 점프 시 다음 경유지로 전환
                if prev_x is not None:
                    jump_dist = abs(current_x - prev_x) + abs(current_y - prev_y)
                    _portal_re = all_targets[current_target_idx][7] if len(all_targets[current_target_idx]) > 7 else []
                    if _portal_re:
                        near_target = any(abs(prev_x - ex) + abs(prev_y - ey) <= 1 for ex, ey in _portal_re)
                    else:
                        near_target = abs(prev_x - target_x) + abs(prev_y - target_y) <= 1
                    portal_threshold = max(8, int(interval / 0.02) + 5) if smooth_move else 5
                    if near_target:
                        portal_threshold = 3
                    if jump_dist >= portal_threshold and near_target and current_target_idx < len(all_targets) - 1:
                        # 도착 키 입력 (포탈 전 현재 경유지)
                        _arr_keys = all_targets[current_target_idx][6] if len(all_targets[current_target_idx]) > 6 else []
                        if _arr_keys:
                            _exec_arrival_keys_re(_arr_keys)
                            logger.info(f"[좌표모드] 포탈 도착 키 입력: {_arr_keys}")
                        # 구간 맵 전환 (현재 맵 저장 → 다음 구간 맵 로드)
                        next_segment = current_target_idx + 1
                        switch_segment_map(next_segment)
                        current_target_idx += 1
                        # 보스 경유지 스킵
                        while current_target_idx < len(all_targets) and all_targets[current_target_idx][3]:
                            logger.warning(f"[좌표모드] 보스 경유지 '{all_targets[current_target_idx][2]}' 스킵")
                            current_target_idx += 1
                        if current_target_idx >= len(all_targets):
                            logger.info(f"{_GREEN}[좌표모드] ★★★ 최종 목표 도달! ({current_x}, {current_y}) ★★★{_RESET}")
                            return True
                        target_x, target_y = _pick_target(current_target_idx)
                        seg_name = all_targets[current_target_idx][2]
                        logger.info(f"{_GREEN}[좌표모드] 🌀 포탈 감지! (점프 {jump_dist}칸) → {seg_name} 목표: ({target_x},{target_y}) [{current_target_idx+1}/{len(all_targets)}]{_RESET}")
                        # route_walls 등록
                        _rw = all_targets[current_target_idx][10] if len(all_targets[current_target_idx]) > 10 else []
                        for _w in _rw:
                            if isinstance(_w, tuple) and len(_w) == 2:
                                game_map.mark_blocked(int(_w[0]), int(_w[1]))
                        # map_locked 적용
                        _ml = all_targets[current_target_idx][9] if len(all_targets[current_target_idx]) > 9 else False
                        if _ml:
                            mapping_enabled = False
                        else:
                            mapping_enabled = getattr(config, 'mapping_enabled', True)
                        stuck_count = 0
                        total_stuck_count = 0
                        transition_recovery_attempts = 3
                        current_path = []
                        path_index = 0
                        path_pos_index = {}
                        explored_from = {}
                        segment_transition_stabilize_until = time.time() + 1.5
                        segment_transition_last_coord = None
                        segment_transition_stable_hits = 0
                        segment_transition_logged = False
                        unknown_path_fails = 0
                        last_dir = None
                        prev_x, prev_y = current_x, current_y
                        time.sleep(0.5)  # 던전 로딩 대기
                        continue

                # 3. 이동 성공/실패 판정
                if prev_x is not None:
                    moved = (prev_x != current_x or prev_y != current_y)

                    if moved:
                        # 이동 성공 → 이동가능으로 기록
                        if mapping_enabled:
                            game_map.mark_passable(current_x, current_y)
                        game_map.clear_soft_blocked(current_x, current_y)
                        if mapping_enabled:
                            # 왔던 방향의 반대를 explored_from에 기록
                            if last_dir:
                                new_pos = (current_x, current_y)
                                opposite = {"up": "down", "down": "up", "left": "right", "right": "left"}
                                tried = explored_from.get(new_pos, set())
                                tried.add(opposite[last_dir])
                                explored_from[new_pos] = tried
                                # explored_from 메모리 제한 (500개 초과 시 오래된 250개 삭제)
                                if len(explored_from) > 500:
                                    keys_to_remove = list(explored_from.keys())[:250]
                                    for k in keys_to_remove:
                                        del explored_from[k]
                        stuck_count = 0
                        total_stuck_count = 0
                        unknown_path_fails = 0
                    else:
                        # 이동 실패
                        stuck_count += 1
                        total_stuck_count += 1
                        unknown_path_fails += 1
                        # 실패한 방향을 explored_from에 기록
                        if last_dir and prev_x is not None:
                            fail_pos = (prev_x, prev_y)
                            tried = explored_from.get(fail_pos, set())
                            tried.add(last_dir)
                            explored_from[fail_pos] = tried

                        if stuck_count >= 3 and last_dir:
                            # 3번 연속 실패 → 임시 장애물 등록 (누적 시 영구벽 승격)
                            if mapping_enabled:
                                ddx, ddy = DIRECTIONS_4.get(last_dir, (0, 0))
                                wall_x = prev_x + ddx
                                wall_y = prev_y + ddy
                                game_map.mark_blocked(wall_x, wall_y)
                                logger.info(f"[좌표모드] 임시벽 발견: ({wall_x},{wall_y})")
                            # 장애물 발견 → 경로 재계산
                            current_path = []
                            path_index = 0
                            path_pos_index = {}
                            stuck_count = 0

                # 3.3. soft_blocked 자동 감소 (10회마다)
                if mapping_enabled:
                    tick_counter += 1
                    if tick_counter >= 10:
                        game_map.tick()
                        tick_counter = 0

                # 3.5. 탈출 스킬 체크
                # 3.4. auto skill check (aligned with editor mode)
                if auto_skill_enabled and auto_skill_key and not self._stop_event.is_set():
                    _use_skill = False
                    _auto_skill_reason = "blocked"
                    _auto_skill_score = None
                    if _auto_skill_cd_tmpl is not None:
                        try:
                            _as_pil = getattr(matcher, '_last_screenshot', None)
                            if _as_pil is not None:
                                if auto_skill_cd_region and len(auto_skill_cd_region) == 4:
                                    _rx1, _ry1, _rx2, _ry2 = auto_skill_cd_region
                                    _as_pil = _as_pil.crop((_rx1, _ry1, _rx2, _ry2))
                                _as_scr = cv2.cvtColor(np.array(_as_pil), cv2.COLOR_RGB2BGR)
                                _tmpl_h, _tmpl_w = _auto_skill_cd_tmpl.shape[:2]
                                _scr_h, _scr_w = _as_scr.shape[:2]
                                if _scr_h < _tmpl_h or _scr_w < _tmpl_w:
                                    _auto_skill_reason = "region_too_small"
                                    _log_auto_skill_diag(
                                        ("runtime", _auto_skill_reason, _scr_w, _scr_h, _tmpl_w, _tmpl_h),
                                        f"image mode: region smaller than template screen={_scr_w}x{_scr_h} tmpl={_tmpl_w}x{_tmpl_h} region={auto_skill_cd_region}",
                                    )
                                else:
                                    _as_res = cv2.matchTemplate(_as_scr, _auto_skill_cd_tmpl, cv2.TM_CCOEFF_NORMED)
                                    _, _as_max_val, _, _ = cv2.minMaxLoc(_as_res)
                                    _auto_skill_score = float(_as_max_val)
                                    _use_skill = _as_max_val < 0.8
                                    _auto_skill_reason = "cooldown_hidden" if _use_skill else "cooldown_visible"
                                    _log_auto_skill_diag(
                                        ("runtime", _auto_skill_reason, round(_auto_skill_score, 3)),
                                        f"image mode: score={_auto_skill_score:.3f} threshold=0.800 use={_use_skill} region={auto_skill_cd_region}",
                                    )
                            else:
                                _auto_skill_reason = "no_screenshot"
                                _log_auto_skill_diag(
                                    ("runtime", _auto_skill_reason),
                                    "image mode: last_screenshot missing -> auto-skill skipped",
                                )
                        except Exception as _auto_skill_exc:
                            _use_skill = True
                            _auto_skill_reason = f"exception:{type(_auto_skill_exc).__name__}"
                            _log_auto_skill_diag(
                                ("runtime", "exception", type(_auto_skill_exc).__name__),
                                f"image mode exception -> auto-skill press: {type(_auto_skill_exc).__name__}: {_auto_skill_exc}",
                            )
                    else:
                        _use_skill = True
                        _auto_skill_reason = "no_template"
                        _log_auto_skill_diag(
                            ("runtime", _auto_skill_reason, bool(auto_skill_cd_image)),
                            f"no image/template -> auto-skill press key={auto_skill_key} image={'Y' if auto_skill_cd_image else 'N'}",
                        )

                    if _use_skill:
                        try:
                            _orig_pause = pyautogui.PAUSE
                            pyautogui.PAUSE = 0
                            try:
                                get_input_controller().key_down(auto_skill_key)
                                time.sleep(0.015)
                                get_input_controller().key_up(auto_skill_key)
                            finally:
                                pyautogui.PAUSE = _orig_pause
                            _score_suffix = f" score={_auto_skill_score:.3f}" if _auto_skill_score is not None else ""
                            _log_auto_skill_diag(
                                ("press", _auto_skill_reason),
                                f"auto-skill press key={auto_skill_key} reason={_auto_skill_reason}{_score_suffix}",
                            )
                        except Exception as _auto_skill_press_exc:
                            _log_auto_skill_diag(
                                ("press_error", type(_auto_skill_press_exc).__name__),
                                f"auto-skill press failed key={auto_skill_key}: {type(_auto_skill_press_exc).__name__}: {_auto_skill_press_exc}",
                                force=True,
                            )

                if (escape_skill_enabled and
                    total_stuck_count >= escape_skill_stuck_threshold and
                    time.time() - last_escape_time >= escape_skill_cooldown):

                    logger.warning(f"{_YELLOW}[좌표모드] 탈출 스킬 발동! (연속 정체 {total_stuck_count}회){_RESET}")

                    try:
                        get_input_controller().press(escape_skill_key)
                    except Exception as e:
                        logger.error(f"[좌표모드] 탈출 스킬 키 입력 실패: {e}")
                        total_stuck_count = 0
                        last_escape_time = time.time()
                        time.sleep(0.05)
                        continue
                    time.sleep(0.3)

                    dx = target_x - current_x
                    dy = target_y - current_y
                    if abs(dx) >= abs(dy):
                        dir_name = "right" if dx > 0 else "left"
                    else:
                        dir_name = "down" if dy > 0 else "up"
                    direction_key = config.move_keys.get(dir_name, dir_name)

                    for _ in range(escape_skill_direction_count):
                        get_input_controller().press(direction_key)
                        time.sleep(0.05)

                    time.sleep(escape_skill_wait_after)

                    last_escape_time = time.time()
                    prev_x, prev_y = None, None
                    stuck_count = 0
                    total_stuck_count = 0
                    current_path = []
                    path_index = 0
                    path_pos_index = {}
                    explored_from = {}
                    unknown_path_fails = 0
                    continue

                # 4. A* 경로탐색으로 방향 결정
                direction = find_path_direction(current_x, current_y, target_x, target_y)

                if direction is None:
                    self._stop_event.wait(0.1)
                    prev_x, prev_y = current_x, current_y
                    continue

                # 거리 로그
                distance = abs(target_x - current_x) + abs(target_y - current_y)
                is_final = (current_target_idx == len(all_targets) - 1)
                slow_approach_dist = 3  # 모든 경유지 N칸 전부터 감속 (포탈 감지용)
                is_slow = distance <= slow_approach_dist

                dir_symbols = {"up": "↑", "down": "↓", "left": "←", "right": "→"}
                seg_name = all_targets[current_target_idx][2]
                mode = "🐢감속" if is_slow else ""
                logger.info(f"[좌표모드] #{iteration} ({current_x},{current_y})→({target_x},{target_y}) [{seg_name}] [{current_target_idx+1}/{len(all_targets)}] 거리={distance}칸 {dir_symbols.get(direction, direction)} {mode}")

                # 경유지 근접 시 감속 (포탈 등 좌표 변경 대비)
                if is_slow:
                    time.sleep(0.5)

                # 5. 이동
                press_key(direction)
                last_dir = direction
                prev_x, prev_y = current_x, current_y

                # 감속 중이면 이동 후에도 추가 대기
                if is_slow:
                    time.sleep(0.3)

            if guard_iterations > max_iterations:
                logger.warning(
                    f"{_YELLOW}[좌표모드] 현재 경유지 최대 반복 횟수 초과 "
                    f"(target_idx={current_target_idx}, {guard_iterations - 1}/{max_iterations}, total={iteration - 1}){_RESET}"
                )

            return False

        except Exception as e:
            logger.error(f"[좌표모드] 오류 발생: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False
        finally:
            try:
                keyboard.remove_hotkey(_escape_hotkey_id)
            except Exception:
                pass

            # 맵핑 결과 출력 및 저장 (현재 구간 맵)
            if mapping_enabled:
                stats = game_map.get_statistics()
                logger.info(f"[맵핑] '{get_seg_name(current_segment_idx)}' 탐색 결과: {stats['total_tiles']}개 타일 (이동가능: {stats['passable_tiles']}, 벽: {stats['blocked_tiles']})")

                save_path = get_segment_map_path(current_segment_idx)
                os.makedirs(os.path.dirname(save_path), exist_ok=True)
                _sanitize_segment_start_pos(game_map, current_segment_idx)
                game_map.save(save_path)
                seg_n = get_seg_name(current_segment_idx)
                logger.info(f"[맵핑] '{seg_n}' 맵 저장: {save_path}")

            logger.info(f"{_GREEN}[좌표모드] ========== 실행 종료 =========={_RESET}")

    # pyautogui.PAUSE 전역 상태 보호용 락
    _pyautogui_lock = threading.Lock()

    def _smooth_key_input(self, keys, interval):
        """부드러운 키 입력 (분석 간격 동안 연타)"""
        with self._pyautogui_lock:
            original_pause = pyautogui.PAUSE
            pyautogui.PAUSE = 0
            try:
                start_time = time.time()
                while time.time() - start_time < interval and not self._stop_event.is_set():
                    for key in keys:
                        get_input_controller().key_down(key)
                    time.sleep(0.015)  # 15ms 누르기
                    for key in keys:
                        get_input_controller().key_up(key)
                    time.sleep(0.005)  # 5ms 간격
            finally:
                # 예외 시에도 키 해제 보장
                for key in keys:
                    try:
                        get_input_controller().key_up(key)
                    except Exception:
                        pass
                pyautogui.PAUSE = original_pause


# 전역 실행 엔진 인스턴스
rule_executor = RuleExecutor()


def get_rule_executor() -> RuleExecutor:
    """규칙 실행 엔진 헬퍼 함수"""
    return rule_executor
