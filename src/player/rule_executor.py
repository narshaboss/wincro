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
pyautogui.FAILSAFE = True  # 화면 구석 이동 시 안전 기능 활성화 (action_player와 일관성 유지)
import pyperclip
import cv2
import numpy as np
from PIL import ImageGrab
from pathlib import Path
import ctypes
import ctypes.wintypes

from ..utils.input_controller import get_input_controller

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

# 성능 최적화용 캐시
_screen_size_cache = None
_screen_size_cache_time = 0
_SCREEN_SIZE_CACHE_TTL = 5.0

_template_cache = {}  # {image_path: (template_gray, h, w, mtime)}
_template_cache_lock = threading.Lock()
_MAX_TEMPLATE_CACHE = 50


def _get_screen_size_cached() -> Tuple[int, int]:
    """캐시된 화면 크기 반환"""
    global _screen_size_cache, _screen_size_cache_time
    current_time = time.time()
    if _screen_size_cache is None or (current_time - _screen_size_cache_time) > _SCREEN_SIZE_CACHE_TTL:
        _screen_size_cache = pyautogui.size()
        _screen_size_cache_time = current_time
    return _screen_size_cache


def _get_cached_template(image_path: str):
    """캐시된 템플릿 이미지 반환 (스레드 안전)"""
    global _template_cache
    try:
        path = Path(image_path)
        if not path.exists():
            return None, 0, 0
        mtime = path.stat().st_mtime

        # 캐시 확인 (락 사용)
        with _template_cache_lock:
            if image_path in _template_cache:
                cached = _template_cache[image_path]
                if cached[3] == mtime:
                    return cached[0], cached[1], cached[2]

        # 캐시 미스 - 이미지 로드 (락 밖에서 I/O)
        # 한글 경로 지원
        img_array = np.fromfile(image_path, np.uint8)
        template = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        if template is None:
            return None, 0, 0
        template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
        h, w = template_gray.shape

        # 캐시에 저장 (락 사용)
        with _template_cache_lock:
            if len(_template_cache) >= _MAX_TEMPLATE_CACHE:
                try:
                    oldest_key = next(iter(_template_cache))
                    del _template_cache[oldest_key]
                except (StopIteration, KeyError):
                    pass
            _template_cache[image_path] = (template_gray, h, w, mtime)

        return template_gray, h, w
    except Exception:
        return None, 0, 0


def _win32_move_click(x: int, y: int, click_type: str = "click") -> bool:
    """
    멀티모니터 지원 마우스 이동 및 클릭 (pynput 우선, Win32 대체)
    """
    x, y = int(x), int(y)

    # 마우스 캡처/클리핑 해제
    try:
        ctypes.windll.user32.ReleaseCapture()
        ctypes.windll.user32.ClipCursor(None)
    except (OSError, AttributeError):
        pass

    # 1. pynput 시도 (가장 안정적)
    if _has_pynput:
        try:
            _pynput_mouse.position = (x, y)
            time.sleep(0.1)

            # 위치 확인
            actual = _pynput_mouse.position
            if abs(actual[0] - x) < 10 and abs(actual[1] - y) < 10:
                # 클릭
                btn = Button.left
                if click_type == "right_click":
                    btn = Button.right

                if click_type == "double_click":
                    _pynput_mouse.click(btn, 2)
                else:
                    _pynput_mouse.click(btn, 1)

                logger.info(f"[pynput] 클릭 성공: ({x}, {y})")
                return True
            else:
                logger.warning(f"[pynput] 이동 실패: 목표=({x}, {y}), 실제={actual}")
        except Exception as e:
            logger.warning(f"[pynput] 오류: {e}")

    # 2. ctypes SetCursorPos 시도
    try:
        ctypes.windll.user32.SetCursorPos(x, y)
        time.sleep(0.1)

        # 위치 확인
        actual_pos = pyautogui.position()
        if abs(actual_pos[0] - x) < 10 and abs(actual_pos[1] - y) < 10:
            # 클릭
            if click_type == "click":
                ctypes.windll.user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
                time.sleep(0.02)
                ctypes.windll.user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
            elif click_type == "double_click":
                for _ in range(2):
                    ctypes.windll.user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
                    time.sleep(0.02)
                    ctypes.windll.user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
                    time.sleep(0.05)
            elif click_type == "right_click":
                ctypes.windll.user32.mouse_event(MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, 0)
                time.sleep(0.02)
                ctypes.windll.user32.mouse_event(MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)
            return True
        else:
            logger.warning(f"[Win32] SetCursorPos 이동 실패: 목표=({x}, {y}), 실제={actual_pos}")
    except Exception as e:
        logger.warning(f"[Win32] SetCursorPos 오류: {e}")

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

        # 마우스 캡처 해제 재시도
        ctypes.windll.user32.ReleaseCapture()
        ctypes.windll.user32.ClipCursor(None)

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
        if abs(actual_pos[0] - x) >= 15 or abs(actual_pos[1] - y) >= 15:
            logger.warning(f"[SendInput] 이동 실패: 목표=({x}, {y}), 실제={actual_pos}")
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

        logger.info(f"[SendInput] 클릭 성공: ({x}, {y})")
        return True
    except Exception as e:
        logger.error(f"[SendInput] 마우스 제어 실패: {e}")
        return False


def _win32_force_click_at(x: int, y: int, click_type: str = "click") -> bool:
    """
    절대 좌표에 강제 클릭 (단순화된 버전)
    """
    try:
        user32 = ctypes.windll.user32

        # 마우스 캡처 해제
        user32.ReleaseCapture()
        user32.ClipCursor(None)

        # 마우스 이동
        user32.SetCursorPos(x, y)
        time.sleep(0.05)

        # 클릭 실행
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

        time.sleep(0.05)
        logger.info(f"[클릭] 완료 ({x}, {y})")
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


@dataclass
class ExecutionProgress:
    """실행 진행 상태"""
    state: ExecutionState = ExecutionState.IDLE
    current_rule: Optional[str] = None
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

        # 모니터링 스레드
        self._monitor_thread: Optional[threading.Thread] = None

        # 진행 상태
        self._progress = ExecutionProgress()

        # 결과 저장
        self._results: List[RuleExecutionResult] = []

        # 콜백
        self._on_progress: Optional[Callable[[ExecutionProgress], None]] = None
        self._on_rule_executed: Optional[Callable[[RuleExecutionResult], None]] = None
        self._on_complete: Optional[Callable[[bool, str], None]] = None
        self._on_error: Optional[Callable[[str, AutomationRule], None]] = None

        # PyAutoGUI 설정
        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0.3  # 기본 대기 시간

        # 속도 설정 (자연스러운 속도를 위해 최소값 설정)
        self._default_wait = max(self._config.player.default_wait_ms / 1000, 0.8)  # 최소 0.8초 대기
        self._mouse_duration = max(self._config.player.mouse_move_duration, 0.4)  # 최소 0.4초 이동
        self._typing_interval = self._config.player.typing_interval

        # 사용자 개입 감지
        self._user_intervention_enabled = False  # 사용자 개입 감지 비활성화 (로딩창 마우스 끌림 문제)
        self._last_mouse_pos = None  # 마지막 마우스 위치 (자동화가 이동시킨 위치)
        self._intervention_pause_seconds = 3  # 개입 시 대기 시간
        self._is_moving_mouse = False  # 자동화가 마우스 이동 중인지


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

        self._current_plan = plan
        self._results.clear()
        self._stop_event.clear()
        self._pause_event.set()

        # 사용자 개입 감지 초기화
        self._last_mouse_pos = pyautogui.position()  # 현재 마우스 위치를 기준점으로
        self._is_moving_mouse = False
        logger.info(f"[개입감지] 초기 마우스 위치: {self._last_mouse_pos}")

        # 진행 상태 초기화
        all_rules_count = len(plan.initial_rules) + len(plan.monitoring_rules)
        self._progress = ExecutionProgress(
            state=ExecutionState.RUNNING_INITIAL,
            initial_total=all_rules_count,
            initial_completed=0,
            monitoring_rules_active=0,
            message="실행 시작",
        )

        # 실행 스레드 시작
        thread = threading.Thread(target=self._execution_loop, daemon=True)
        thread.start()

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
        self._pause_event.clear()
        self._state = ExecutionState.PAUSED
        self._update_progress("일시정지됨")
        logger.info("실행 일시정지")

    def resume(self) -> None:
        """실행 재개"""
        self._pause_event.set()
        if self._progress.initial_completed < self._progress.initial_total:
            self._state = ExecutionState.RUNNING_INITIAL
        else:
            self._state = ExecutionState.MONITORING
        self._update_progress("실행 재개")
        logger.info("실행 재개")

    def stop(self) -> None:
        """실행 중지"""
        self._stop_event.set()
        self._pause_event.set()  # 일시정지 상태에서도 종료 가능하게
        self._state = ExecutionState.STOPPED
        self._update_progress("실행 중지됨")
        logger.info(f"\033[95m■ 실행 중지됨\033[0m")

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
            if distance > 50:
                logger.info(f"[개입감지] 마우스 이동 감지: ({last_x}, {last_y}) -> ({current_pos[0]}, {current_pos[1]}) 거리={distance:.0f}px")
                return True
        except (TypeError, ValueError, AttributeError):
            pass

        return False

    def _wait_after_intervention(self) -> None:
        """사용자 개입 후 대기"""
        logger.info(f"[개입감지] 사용자 개입 감지! {self._intervention_pause_seconds}초 대기 후 재개...")
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
        logger.info(f"[개입감지] 대기 완료, 재개합니다. 새 기준점: {self._last_mouse_pos}")
        self._update_progress("재개 중...")

        # 마우스 캡처 해제 시도 (다른 앱이 마우스를 잡고 있을 수 있음)
        try:
            ctypes.windll.user32.ReleaseCapture()
            ctypes.windll.user32.ClipCursor(None)
            # 데스크톱을 포커스하여 활성 창 해제
            desktop_hwnd = ctypes.windll.user32.GetDesktopWindow()
            ctypes.windll.user32.SetForegroundWindow(desktop_hwnd)
            time.sleep(0.1)
            logger.info("[개입감지] 마우스 캡처 해제 시도 완료")
        except Exception as e:
            logger.warning(f"[개입감지] 마우스 캡처 해제 실패: {e}")

    def _flatten_rules(self, rules: List[AutomationRule]) -> List[AutomationRule]:
        """계층 구조를 평탄화하여 모든 규칙 반환 (자식 포함)"""
        result = []
        for rule in rules:
            result.append(rule)
            if rule.children:
                result.extend(self._flatten_rules(rule.children))
        return result

    def _flatten_rules_with_step(self, rules: List[AutomationRule], parent_step: str = "") -> List[Tuple[AutomationRule, str]]:
        """계층 구조를 평탄화하면서 단계 번호 추적 (예: "1", "1-1", "1-2")"""
        result = []
        for i, rule in enumerate(rules):
            if parent_step:
                step = f"{parent_step}-{i + 1}"
            else:
                step = str(i + 1)
            result.append((rule, step))
            if rule.children:
                result.extend(self._flatten_rules_with_step(rule.children, step))
        return result

    def _execution_loop(self) -> None:
        """메인 실행 루프"""
        try:
            plan = self._current_plan
            if not plan:
                return

            logger.info(f"\033[96m{'═'*50}\033[0m")
            logger.info(f"\033[96m▶ 실행 시작: {plan.name}\033[0m")
            self._state = ExecutionState.RUNNING_INITIAL

            # 하위 항목(children) 포함해서 평탄화 + 단계 번호 추적
            all_rules_with_step = self._flatten_rules_with_step(plan.initial_rules) + self._flatten_rules_with_step(plan.monitoring_rules)
            all_rules = [rule for rule, _ in all_rules_with_step]
            logger.info(f"\033[96m  총 {len(all_rules_with_step)}개 액션\033[0m")
            logger.info(f"\033[96m{'═'*50}\033[0m")

            # 모든 규칙 순차 실행 (룰과 스텝 번호를 함께 순회)
            for i, (rule, step_num) in enumerate(all_rules_with_step):
                if self._stop_event.is_set():
                    break

                # 일시정지 대기
                self._pause_event.wait()
                if self._stop_event.is_set():
                    break

                # 단계 번호와 이름 구성 (step_num이 없으면 인덱스 사용)
                step_num = step_num if step_num else str(i + 1)
                action_name = rule.description if rule.description else rule.action_type

                # 액션 헤더 (단계 번호 + 이름)
                logger.info(f"")
                logger.info(f"\033[96m[{step_num}] {action_name}\033[0m")

                # 핵심 정보만 한 줄씩 (간결하게)
                if rule.target_image:
                    logger.info(f"  📷 {Path(rule.target_image).name}")
                if rule.action_keys:
                    logger.info(f"  ⌨️ {rule.action_keys}")
                if rule.action_text:
                    text_preview = rule.action_text[:30] + "..." if len(rule.action_text) > 30 else rule.action_text
                    logger.info(f"  📝 {text_preview}")

                self._progress.current_rule = rule.rule_id
                self._update_progress(f"[{step_num}] {action_name}")

                # 모니터링 모드인 경우 별도 처리
                # is_monitoring_mode가 True이거나, monitoring_watches가 있으면 모니터링 모드로 실행
                has_monitoring_watches = len(getattr(rule, 'monitoring_watches', []) or []) > 0
                is_monitoring = getattr(rule, 'is_monitoring_mode', False) or has_monitoring_watches
                logger.debug(f"[실행경로] rule={rule.description}, is_monitoring_mode={getattr(rule, 'is_monitoring_mode', False)}, watches={len(getattr(rule, 'monitoring_watches', []) or [])}, 최종판단={is_monitoring}")
                if is_monitoring:
                    result = self._execute_monitoring_mode(rule, all_rules, i)
                    self._results.append(result)
                else:
                    # 다음 규칙의 타겟 이미지 (확인용)
                    next_target_image = None
                    next_rule = None
                    if i + 1 < len(all_rules):
                        next_rule = all_rules[i + 1]
                        next_target_image = next_rule.target_image

                    # 규칙 실행 (재시도 포함)
                    result = self._execute_rule_with_retry(rule, next_target_image, next_rule=next_rule)
                    self._results.append(result)

                if self._on_rule_executed:
                    self._on_rule_executed(result)

                if not result.success:
                    logger.error(f"\033[91m✗ [{step_num}] 실패: {result.message}\033[0m")
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
                if is_skipped:
                    logger.info(f"  → 대기시간 생략, 바로 다음으로")
                if not self._stop_event.is_set() and not is_skipped:
                    wait_time = getattr(rule, 'wait_after', self._default_wait)
                    if getattr(rule, 'wait_random', False):
                        wait_range = getattr(rule, 'wait_random_range', 0.3)
                        wait_time = wait_time + random.uniform(-wait_range, wait_range)
                        wait_time = max(0, wait_time)
                    if wait_time > 0:
                        if self._stop_event.wait(timeout=wait_time):
                            break

            # 완료
            if not self._stop_event.is_set():
                self._state = ExecutionState.COMPLETED
                success_count = sum(1 for r in self._results if r.success)
                total_count = len(self._results)
                logger.info(f"")
                logger.info(f"\033[92m{'═'*50}\033[0m")
                logger.info(f"\033[92m★ 완료! ({success_count}/{total_count} 성공)\033[0m")
                logger.info(f"\033[92m{'═'*50}\033[0m")
                self._update_progress(f"완료 ({success_count}/{total_count} 성공)")
                if self._on_complete:
                    self._on_complete(True, f"자동화 실행 완료: {success_count}/{total_count} 성공")

        except Exception as e:
            logger.error(f"\033[91m✗ 실행 오류: {e}\033[0m")
            self._state = ExecutionState.FAILED
            self._update_progress(f"실행 실패: {e}")
            if self._on_complete:
                self._on_complete(False, str(e))

    def _monitor_loop(self, rules: List[AutomationRule]) -> None:
        """모니터링 루프 - 조건 감시"""
        check_interval = 0.5  # 0.5초마다 확인

        while not self._stop_event.is_set():
            # 일시정지 대기
            self._pause_event.wait()
            if self._stop_event.is_set():
                break

            # 각 모니터링 규칙 확인
            for rule in rules:
                if self._stop_event.is_set():
                    break

                # 트리거 조건 확인
                if self._check_trigger(rule):
                    logger.info(f"모니터링 규칙 트리거: {rule.description}")
                    self._progress.current_rule = rule.rule_id
                    self._update_progress(f"모니터링 규칙 발동: {rule.description}")

                    result = self._execute_rule(rule)
                    self._results.append(result)
                    self._progress.monitoring_triggers += 1

                    if self._on_rule_executed:
                        self._on_rule_executed(result)

            time.sleep(check_interval)

    def _check_trigger(self, rule: AutomationRule) -> bool:
        """트리거 조건 확인"""
        if rule.trigger_image:
            location = self._find_image_on_screen(rule.trigger_image, rule.confidence)
            return location is not None
        return False

    def _execute_rule_with_retry(
        self,
        rule: AutomationRule,
        next_target_image: Optional[str] = None,
        max_retries: int = 3,
        next_rule: Optional[AutomationRule] = None,
    ) -> RuleExecutionResult:
        """
        규칙 실행 + 다음 이미지 확인 + 재시도

        클릭 동작 후 다음 이미지가 나타나는지 확인합니다.
        나타나지 않으면 재시도합니다.
        next_rule이 skip_on_not_found=True면 wait_after 시간만 대기.
        """
        start_time = datetime.now()

        # 화면 안정화 대기 (이전 액션 효과가 반영될 시간)
        time.sleep(0.2)

        # trigger_image가 설정되어 있으면 해당 이미지가 나타날 때까지 대기
        if rule.trigger_image:
            timeout = rule.timeout if rule.timeout > 0 else 30.0
            trigger_confidence = rule.confidence if rule.confidence > 0 else 0.65
            self._update_progress(f"트리거 대기 중: {rule.description}")

            # 새로운 단순화된 트리거 대기
            trigger_location = self._wait_for_trigger(
                rule.trigger_image,
                confidence=trigger_confidence,
                timeout=timeout
            )

            if trigger_location is None:
                return self._make_result(rule, False, f"트리거 이미지 타임아웃 ({timeout}초)", start_time)

            # 트리거 발견 후 클릭 준비 (포커스 확보)
            self._prepare_for_click_after_trigger()

        # 클릭 계열 동작인지 확인
        is_click_action = rule.action_type in ["click", "double_click", "right_click"]

        # 반복 횟수
        repeat_count = getattr(rule, 'repeat_count', 1)
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
                while not self._pause_event.is_set():
                    time.sleep(0.1)
                    if self._stop_event.is_set():
                        return self._make_result(rule, False, "실행 중지됨", start_time)

                if repeat_count > 1:
                    logger.info(f"\033[96m  [반복 {rep + 1}/{repeat_count}] {rule.description or rule.action_type}\033[0m")

                # 규칙 실행
                result = self._execute_rule(rule)

                if not result.success:
                    break  # 실패하면 반복 중단

                # 마지막 반복이 아니면 반복 대기시간 적용
                if rep < repeat_count - 1:
                    repeat_delay = getattr(rule, 'repeat_delay', 0.5)
                    if repeat_delay > 0:
                        # 랜덤 대기시간 적용
                        if getattr(rule, 'repeat_delay_random', False):
                            delay_range = getattr(rule, 'repeat_delay_random_range', 0.3)
                            import random
                            actual_delay = max(0, repeat_delay + random.uniform(-delay_range, delay_range))
                        else:
                            actual_delay = repeat_delay
                        time.sleep(actual_delay)

            if not result.success:
                logger.warning(f"\033[91m  ✗ 동작 실패 (시도 {attempt + 1}/{max_retries}): {result.message}\033[0m")
                if attempt < max_retries - 1:
                    time.sleep(0.5)
                    continue
                return result

            # 클릭 동작이고 다음 타겟 이미지가 있으면 확인 (스킵된 경우 제외)
            is_skipped = "스킵됨" in result.message if result.message else False
            if is_skipped:
                logger.info(f"  → 스킵 완료, 다음 액션 준비")
            if is_click_action and next_target_image and not is_skipped:
                check_interval = 0.5
                waited = 0.0
                # 다음 액션에 스킵 설정이 있으면 wait_after 시간만 대기
                next_skip = getattr(next_rule, 'skip_on_not_found', False) if next_rule else False
                next_wait = getattr(next_rule, 'wait_after', 0) if next_rule else 0
                next_desc = getattr(next_rule, 'description', '') if next_rule else ''
                logger.debug(f"  [DEBUG] 다음액션: {next_desc}, skip={next_skip}, wait_after={next_wait}")
                if next_skip and next_wait > 0:
                    max_wait_time = next_wait
                    logger.info(f"  → 다음 화면 확인: {Path(next_target_image).name} (스킵 대기: {max_wait_time:.1f}초)")
                else:
                    max_wait_time = 300.0  # 5분 타임아웃
                    logger.info(f"  → 다음 화면 확인: {Path(next_target_image).name if next_target_image else 'None'}")

                while waited < max_wait_time:
                    if self._stop_event.is_set():
                        return self._make_result(rule, False, "실행 중지됨", start_time)

                    # 일시정지 체크 (타임아웃 추가)
                    pause_wait_start = time.time()
                    while not self._pause_event.is_set():
                        time.sleep(0.1)
                        if self._stop_event.is_set():
                            return self._make_result(rule, False, "실행 중지됨", start_time)
                        # 일시정지 대기 5초 초과시 로그
                        if time.time() - pause_wait_start > 5:
                            logger.warning(f"  ⏸ 일시정지 대기 중...")
                            pause_wait_start = time.time()

                    # 이미지 검색 시작 로그 (첫 번째만)
                    if waited == 0:
                        logger.debug(f"  [DEBUG] 이미지 검색 시작")

                    search_start = time.time()
                    location = self._find_image_on_screen(next_target_image, 0.65)
                    search_time = time.time() - search_start

                    # 검색이 오래 걸리면 로그
                    if search_time > 3.0:
                        logger.warning(f"  [DEBUG] 이미지 검색 {search_time:.1f}초 소요")
                    if location:
                        return self._make_result(rule, True, f"{result.message}", start_time)

                    time.sleep(check_interval)
                    waited += check_interval

                    # 10초마다 로그 출력
                    if waited % 10 < check_interval and waited > 0:
                        logger.info(f"  ⏳ 다음 화면 대기... {waited:.0f}초 (최대 {max_wait_time:.0f}초)")

                # 타임아웃 - 경고 후 계속 진행
                if next_skip:
                    logger.info(f"  ⏭ 다음 화면 스킵 ({max_wait_time:.1f}초 대기 후)")
                else:
                    logger.warning(f"  ⚠ 다음 화면 대기 타임아웃 ({max_wait_time:.0f}초) - 계속 진행")

            # 클릭이 아니거나 다음 이미지가 없으면 바로 성공
            return result

        return self._make_result(rule, False, "최대 재시도 횟수 초과", start_time)

    def _execute_rule(self, rule: AutomationRule) -> RuleExecutionResult:
        """단일 규칙 실행"""
        start_time = datetime.now()

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
                )
                return self._make_result(rule, success, msg, start_time)

            elif rule_type == RuleType.WAIT_FOR_DISAPPEAR.value:
                # 이미지가 사라질 때까지 대기
                success, msg = self._wait_for_image(
                    rule.trigger_image,
                    rule.timeout,
                    rule.confidence,
                    disappear=True,
                )
                return self._make_result(rule, success, msg, start_time)

            elif rule_type == RuleType.CLICK_ON_APPEAR.value:
                # 이미지가 나타나면 클릭
                if rule.target_image:
                    location = self._find_image_on_screen(rule.target_image, rule.confidence)
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
                if rule.action_keys:
                    keys = [k.lower() for k in rule.action_keys]
                    input_ctrl = get_input_controller()
                    input_ctrl.hotkey(*keys)
                    return self._make_result(rule, True, "단축키 실행 완료", start_time)
                else:
                    return self._make_result(rule, False, "단축키 없음", start_time)

            elif rule_type == RuleType.MONITOR.value:
                # 모니터링 규칙 (트리거 시 실행)
                if rule.target_image:
                    location = self._find_image_on_screen(rule.target_image, rule.confidence)
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
            if action_type in ["click", "double_click", "right_click"]:
                click_x, click_y = None, None
                click_method = "없음"

                # 이미지 인식 - 이미지가 나타날 때까지 무한 대기
                # 기본 이미지 + 멀티이미지를 모두 검색 (OR 조건)
                all_target_images = []
                if rule.target_image:
                    all_target_images.append(rule.target_image)
                if hasattr(rule, 'target_images') and rule.target_images:
                    all_target_images.extend(rule.target_images)

                if all_target_images:

                    # 이미지 파일 존재 확인 (최소 하나는 있어야 함)
                    valid_images = [p for p in all_target_images if Path(p).exists()]
                    if not valid_images:
                        logger.error(f"타겟 이미지 파일 없음: {all_target_images}")
                        return self._make_result(rule, False, f"이미지 파일 없음: {all_target_images}", start_time)

                    locations = []
                    found_image = None
                    wait_count = 0
                    skip_on_not_found = getattr(rule, 'skip_on_not_found', False)
                    skip_timeout = rule.wait_after if skip_on_not_found else float('inf')
                    search_start = time.time()
                    if skip_on_not_found:
                        logger.debug(f"  [DEBUG] 현재액션 스킵설정: wait_after={rule.wait_after}초")

                    # 이미지가 나타날 때까지 대기
                    while not locations:
                        # 중지 체크
                        if self._stop_event.is_set():
                            return self._make_result(rule, False, "실행 중지됨", start_time)

                        # 스킵 모드: 대기시간 초과시 다음 액션으로
                        if skip_on_not_found and (time.time() - search_start) >= skip_timeout:
                            logger.info(f"\033[93m  ⏭ 스킵: 이미지 못찾음 ({skip_timeout:.1f}초 대기 후 스킵)\033[0m")
                            return self._make_result(rule, True, f"스킵됨 (이미지 없음, {skip_timeout:.1f}초 대기)", start_time)

                        # 일시정지 대기 (pause_event가 clear되면 일시정지)
                        while not self._pause_event.is_set():
                            time.sleep(0.1)
                            if self._stop_event.is_set():
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
                        for img_path in valid_images:
                            locations = self._find_all_images_on_screen(
                                img_path,
                                rule.confidence,
                                search_radius=rule.search_radius,
                                center_x=rule.action_x,
                                center_y=rule.action_y,
                            )
                            if locations:
                                found_image = img_path
                                break  # 하나라도 찾으면 중단

                        # 검색 후 마우스 원위치 복원
                        if mouse_moved_for_search and original_mouse_pos:
                            try:
                                pyautogui.moveTo(original_mouse_pos[0], original_mouse_pos[1], duration=0)
                            except Exception:
                                pass

                        if not locations:
                            # 이미지 검색 후 스킵 체크 (검색이 오래 걸릴 수 있음)
                            if skip_on_not_found and (time.time() - search_start) >= skip_timeout:
                                logger.info(f"\033[93m  ⏭ 스킵: 이미지 못찾음 ({skip_timeout:.1f}초 대기 후 스킵)\033[0m")
                                logger.info(f"  → 스킵 처리 중... 다음 액션으로 이동")
                                return self._make_result(rule, True, f"스킵됨 (이미지 없음, {skip_timeout:.1f}초 대기)", start_time)

                            wait_count += 1
                            if wait_count % 20 == 1:  # 10초마다 로그
                                skip_info = f" (스킵: {skip_timeout:.1f}초 후)" if skip_on_not_found else ""
                                logger.info(f"  ⏳ 타겟 이미지 대기 중... {wait_count * 0.5:.0f}초{skip_info}")
                            time.sleep(0.5)  # 0.5초마다 재검색

                    # 찾은 이미지 이름
                    found_name = Path(found_image).name if found_image else "이미지"

                    if len(locations) == 1:
                        click_x, click_y, found_conf = locations[0]
                        click_method = "이미지"
                        logger.info(f"\033[93m  ✓ 타겟 발견 [{found_name}] ({int(found_conf * 100)}%)\033[0m")
                    elif len(locations) > 1:
                        # 여러 개 발견됨 - action_x/y 힌트로 가장 가까운 것 선택
                        if rule.action_x is not None and rule.action_y is not None:
                            closest = self._find_closest_image(locations, rule.action_x, rule.action_y)
                            if closest:
                                click_x, click_y, found_conf = closest
                                click_method = f"{len(locations)}개 중 선택"
                                logger.info(f"\033[93m  ✓ 타겟 발견 [{found_name}] ({int(found_conf * 100)}%) - {len(locations)}개 중 선택\033[0m")
                            else:
                                click_x, click_y, found_conf = locations[0]
                                click_method = f"{len(locations)}개 중 첫번째"
                        else:
                            click_x, click_y, found_conf = locations[0]
                            click_method = f"{len(locations)}개 중 첫번째"
                            logger.info(f"\033[93m  ✓ 타겟 발견 [{found_name}] ({int(found_conf * 100)}%) - 첫번째\033[0m")
                    else:
                        click_x, click_y, found_conf = locations[0]
                        click_method = "이미지"
                        logger.info(f"\033[93m  ✓ 타겟 발견 [{found_name}] ({int(found_conf * 100)}%)\033[0m")

                # 클릭 실행
                if click_x is not None and click_y is not None:
                    # 검색 범위가 설정된 경우, 클릭 좌표가 범위 안에 있는지 확인 (경고만, 차단 안 함)
                    if rule.search_radius > 0 and rule.action_x is not None and rule.action_y is not None:
                        dist_from_center = ((click_x - rule.action_x) ** 2 + (click_y - rule.action_y) ** 2) ** 0.5
                        if dist_from_center > rule.search_radius:
                            # 범위 밖이어도 경고만 하고 클릭은 진행 (해상도/스케일링 차이 허용)
                            logger.warning(f"\033[93m  ⚠ 클릭 좌표가 검색 범위를 벗어남 (거리: {dist_from_center:.0f}px, 범위: {rule.search_radius}px) - 클릭 진행\033[0m")

                    # 클릭 전 사용자 개입 확인
                    if self._check_user_intervention():
                        self._wait_after_intervention()
                        if self._stop_event.is_set():
                            return self._make_result(rule, False, "실행 중지됨", start_time)

                    action_name = {"double_click": "더블클릭", "right_click": "우클릭"}.get(action_type, "클릭")

                    # Arduino HID가 활성화되어 있으면 Arduino HID 사용
                    from ..utils.input_controller import is_arduino_enabled
                    if is_arduino_enabled():
                        input_ctrl = get_input_controller()
                        if action_type == "double_click":
                            input_ctrl.double_click(click_x, click_y, duration=self._mouse_duration)
                        elif action_type == "right_click":
                            input_ctrl.right_click(click_x, click_y, duration=self._mouse_duration)
                        else:
                            input_ctrl.click(click_x, click_y, duration=self._mouse_duration)
                        logger.info(f"\033[92m  ✓ {action_name} 완료\033[0m")
                        self._last_mouse_pos = (click_x, click_y)
                        return self._make_result(rule, True, f"{action_type} 완료", start_time)

                    # 마우스 이동 시도 (로딩 등으로 마우스가 잠겨있을 수 있으므로 반복 시도)
                    max_move_attempts = 10  # 최대 10번 시도 (약 5초)
                    move_success = False

                    for move_attempt in range(max_move_attempts):
                        if self._stop_event.is_set():
                            return self._make_result(rule, False, "실행 중지됨", start_time)

                        # 마우스 캡처/클리핑 해제
                        try:
                            ctypes.windll.user32.ReleaseCapture()
                            ctypes.windll.user32.ClipCursor(None)
                        except (OSError, AttributeError):
                            pass

                        # PyAutoGUI로 시도
                        self._is_moving_mouse = True
                        pyautogui.moveTo(click_x, click_y, duration=self._mouse_duration)
                        self._is_moving_mouse = False

                        pos_after_move = pyautogui.position()
                        if abs(pos_after_move[0] - click_x) < 10 and abs(pos_after_move[1] - click_y) < 10:
                            move_success = True
                            break

                        # Win32 API 시도
                        if _win32_move_click(click_x, click_y, action_type):
                            move_success = True
                            logger.info(f"\033[92m  ✓ {action_name} 완료\033[0m")
                            self._last_mouse_pos = pyautogui.position()
                            return self._make_result(rule, True, f"{action_type} 완료", start_time)

                        # 실패 시 대기 후 재시도
                        if move_attempt < max_move_attempts - 1:
                            time.sleep(0.5)

                    if move_success:
                        # 클릭 전 짧은 대기 (페이지 안정화)
                        time.sleep(0.1)
                        if _win32_force_click_at(click_x, click_y, action_type):
                            logger.info(f"\033[92m  ✓ {action_name} 완료\033[0m")
                            self._last_mouse_pos = (click_x, click_y)
                            return self._make_result(rule, True, f"{action_type} 완료", start_time)

                        # 절대 좌표 클릭 실패 시 기존 방식으로 폴백
                        if action_type == "double_click":
                            for _ in range(2):
                                ctypes.windll.user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
                                time.sleep(0.02)
                                ctypes.windll.user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
                                time.sleep(0.05)
                        elif action_type == "right_click":
                            ctypes.windll.user32.mouse_event(MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, 0)
                            time.sleep(0.02)
                            ctypes.windll.user32.mouse_event(MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)
                        else:
                            ctypes.windll.user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
                            time.sleep(0.02)
                            ctypes.windll.user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)

                        logger.info(f"\033[92m  ✓ {action_name} 완료\033[0m")
                        self._last_mouse_pos = pyautogui.position()
                        return self._make_result(rule, True, f"{action_type} 완료", start_time)
                    else:
                        # 마우스 이동 실패 - 강제 클릭 시도
                        if _win32_force_click_at(click_x, click_y, action_type):
                            logger.info(f"\033[92m  ✓ {action_name} 완료\033[0m")
                            self._last_mouse_pos = (click_x, click_y)
                            return self._make_result(rule, True, f"{action_type} 완료", start_time)
                        else:
                            logger.error(f"\033[91m  ✗ {action_name} 실패\033[0m")
                            return self._make_result(rule, False, "클릭 실패", start_time)

                logger.warning(f"\033[91m  ✗ 클릭 대상 없음\033[0m")
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
                        logger.info(f"\033[92m  ✓ 드래그 완료\033[0m")
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
                                logger.info(f"\033[92m  ✓ 드래그 완료\033[0m")
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
                    logger.info(f"\033[92m  ✓ 텍스트 입력 완료\033[0m")
                    return self._make_result(rule, True, "입력 완료", start_time)
                return self._make_result(rule, False, "입력할 텍스트 없음", start_time)

            elif action_type == "hotkey":
                if rule.action_keys:
                    keys = [k.lower() for k in rule.action_keys]
                    input_ctrl = get_input_controller()
                    input_ctrl.hotkey(*keys)
                    logger.info(f"\033[92m  ✓ 단축키 완료\033[0m")
                    return self._make_result(rule, True, f"단축키 완료", start_time)
                return self._make_result(rule, False, "단축키 없음", start_time)

            elif action_type == "key_press":
                if rule.action_keys:
                    input_ctrl = get_input_controller()
                    for key in rule.action_keys:
                        input_ctrl.press(key.lower())
                    logger.info(f"\033[92m  ✓ 키 입력 완료\033[0m")
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

                input_ctrl = get_input_controller()
                input_ctrl.scroll(scroll_amount, rule.action_x, rule.action_y)
                logger.info(f"\033[92m  ✓ 스크롤 완료\033[0m")
                return self._make_result(rule, True, f"스크롤 완료", start_time)

            else:
                return self._make_result(rule, False, f"알 수 없는 액션: {action_type}", start_time)

        except Exception as e:
            import traceback
            logger.error(f"액션 실행 중 예외 발생: {e}\n{traceback.format_exc()}")
            return self._make_result(rule, False, f"예외: {str(e)}", start_time)

    def _wait_for_image(
        self,
        image_path: Optional[str],
        timeout: float,
        confidence: float,
        disappear: bool = False,
    ) -> tuple:
        """이미지 대기"""
        if not image_path:
            return (False, "대기할 이미지가 없습니다")

        start_time = time.time()
        check_interval = 0.5

        while time.time() - start_time < timeout:
            if self._stop_event.is_set():
                return (False, "실행 중지됨")

            location = self._find_image_on_screen(image_path, confidence)

            if disappear:
                # 이미지가 사라질 때까지 대기
                if location is None:
                    return (True, "이미지가 사라졌습니다")
            else:
                # 이미지가 나타날 때까지 대기
                if location is not None:
                    return (True, "이미지가 나타났습니다")

            time.sleep(check_interval)

        mode = "사라짐" if disappear else "나타남"
        return (False, f"타임아웃: 이미지 {mode} 대기 실패 ({timeout}초)")

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
    ) -> Optional[tuple]:
        """
        화면에서 이미지 찾기 (강화된 매칭)

        여러 매칭 기법을 조합하여 더 정확한 이미지 인식을 제공합니다:
        1. 그레이스케일 매칭 (색상 변화에 강함)
        2. 엣지 기반 매칭 (밝기 변화에 강함)
        3. 다중 스케일 매칭 (크기 변화에 강함)
        4. 특징점 매칭 (부분 가림/변형에 강함)

        search_region: 검색 영역 제한 [x1, y1, x2, y2] 또는 None (전체 화면)
        """
        func_start = time.time()

        # 중지 체크
        if self._stop_event.is_set():
            return None

        try:
            # 파일 존재 확인 (타임아웃 적용)
            if not image_path:
                logger.warning(f"이미지 경로가 없습니다")
                return None

            file_exists = [False]
            def check_file():
                try:
                    file_exists[0] = Path(image_path).exists()
                except Exception:
                    file_exists[0] = False

            check_thread = threading.Thread(target=check_file, daemon=True)
            check_thread.start()
            check_thread.join(timeout=3.0)  # 3초 타임아웃

            if check_thread.is_alive():
                logger.warning(f"파일 존재 확인 타임아웃 (3초): {Path(image_path).name}")
                return None

            if not file_exists[0]:
                logger.warning(f"템플릿 파일 없음: {Path(image_path).name}")
                return None

            logger.debug(f"  [DEBUG] 파일 확인 완료: {time.time() - func_start:.2f}초")

            # 중지 체크
            if self._stop_event.is_set():
                return None

            # 화면 캡처 (타임아웃 적용)
            screenshot = None
            capture_result = [None]

            def capture_screen():
                try:
                    capture_result[0] = ImageGrab.grab()
                except Exception as e:
                    logger.error(f"화면 캡처 오류: {e}")

            capture_thread = threading.Thread(target=capture_screen, daemon=True)
            capture_start = time.time()
            capture_thread.start()
            capture_thread.join(timeout=5.0)  # 5초 타임아웃

            if capture_thread.is_alive():
                logger.warning(f"화면 캡처 타임아웃 (5초) - 건너뜀")
                return None

            screenshot = capture_result[0]
            if screenshot is None:
                logger.warning("화면 캡처 실패")
                return None

            capture_time = time.time() - capture_start
            if capture_time > 2.0:
                logger.warning(f"화면 캡처 지연: {capture_time:.1f}초")
            screenshot_np = np.array(screenshot)
            screenshot_bgr = cv2.cvtColor(screenshot_np, cv2.COLOR_RGB2BGR)

            # 검색 영역 제한
            region_offset_x, region_offset_y = 0, 0
            if search_region and len(search_region) == 4:
                h, w = screenshot_bgr.shape[:2]
                x1 = max(0, search_region[0])
                y1 = max(0, search_region[1])
                x2 = min(w, search_region[2])
                y2 = min(h, search_region[3])
                if x2 > x1 and y2 > y1:
                    screenshot_bgr = screenshot_bgr[y1:y2, x1:x2]
                    region_offset_x, region_offset_y = x1, y1

            # 중지 체크
            if self._stop_event.is_set():
                return None

            # 강화된 매처 사용
            matcher = get_enhanced_matcher()

            # ROI 기반 적응형 매칭 (이전 위치 근처 먼저 검색)
            result = matcher.match_with_roi(screenshot_bgr, image_path, confidence)

            logger.debug(f"[이미지 검색] ROI매칭 결과: found={result.found}, conf={result.confidence:.2f}, "
                        f"좌표=({result.x},{result.y}), 크기=({result.width}x{result.height}), "
                        f"중심=({result.center_x},{result.center_y}), 방법={result.method_used}")

            if result.found and result.confidence >= confidence:
                # 사용자 설정 confidence 이상일 때만 인정 (오탐 방지)
                final_x = result.center_x + region_offset_x
                final_y = result.center_y + region_offset_y
                logger.debug(f"[이미지 검색] 최종좌표: ({final_x}, {final_y}) = 중심({result.center_x},{result.center_y}) + 오프셋({region_offset_x},{region_offset_y})")
                return (final_x, final_y, result.confidence)

            # 중지 체크 - ROI 매칭 실패 후 추가 매칭 전에 확인
            if self._stop_event.is_set():
                return None

            # ROI 매칭 실패 시 추가 매칭 시도 (단, 사용자 설정 confidence 존중)
            # edge_regions 등 낮은 신뢰도 매칭은 오탐 가능성이 높으므로 제한
            min_threshold = max(confidence, 0.6)  # 사용자 설정값 이상으로만 매칭
            result = matcher.match_best_effort(
                screenshot_bgr, image_path,
                min_threshold=min_threshold
            )

            logger.debug(f"[이미지 검색] BestEffort 결과: found={result.found}, conf={result.confidence:.2f}, "
                        f"중심=({result.center_x},{result.center_y}), 방법={result.method_used}")

            if result.found and result.confidence >= confidence:
                # 사용자 설정 confidence 이상일 때만 인정
                final_x = result.center_x + region_offset_x
                final_y = result.center_y + region_offset_y
                logger.debug(f"[이미지 검색] 최종좌표: ({final_x}, {final_y})")
                return (final_x, final_y, result.confidence)

            # 실패 - 이유 로깅
            if result.found:
                logger.info(f"[이미지 검색] 인식률 부족: {result.confidence:.2f} < 설정 인식률 {confidence:.2f}")
            return None

        except Exception as e:
            logger.error(f"이미지 검색 오류: {e}")
            return None

    def _wait_for_trigger(
        self,
        image_path: str,
        confidence: float = 0.65,
        timeout: float = 30.0,
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
                logger.error(f"[트리거] 파일 없음: {image_path}")
                return None

            # 템플릿 로드 (캐시 사용)
            cached = _get_cached_template(image_path)
            if cached is None:
                logger.error(f"[트리거] 이미지 로드 실패: {image_path}")
                return None

            template_gray, h, w = cached

            logger.info(f"[트리거] 대기 시작: {Path(image_path).name}, 인식률={confidence:.0%}")

            waited = 0.0
            check_interval = 0.2

            while waited < timeout:
                if self._stop_event.is_set():
                    return None

                # 일시정지 체크
                while not self._pause_event.is_set():
                    time.sleep(0.1)
                    if self._stop_event.is_set():
                        return None

                # 화면 캡처 및 매칭
                screenshot = ImageGrab.grab()
                screenshot_np = np.array(screenshot)
                screenshot_gray = cv2.cvtColor(screenshot_np, cv2.COLOR_RGB2GRAY)

                # 크기 체크: 템플릿이 화면보다 크면 스킵
                scr_h, scr_w = screenshot_gray.shape[:2]
                if h > scr_h or w > scr_w:
                    logger.warning(f"[트리거] 템플릿({w}x{h})이 화면({scr_w}x{scr_h})보다 큼 - 스킵")
                    return None

                try:
                    result = cv2.matchTemplate(screenshot_gray, template_gray, cv2.TM_CCOEFF_NORMED)
                except cv2.error as e:
                    logger.error(f"[트리거] 매칭 오류: {e}")
                    return None

                _, max_val, _, max_loc = cv2.minMaxLoc(result)

                if max_val >= confidence:
                    center_x = max_loc[0] + w // 2
                    center_y = max_loc[1] + h // 2
                    logger.info(f"[트리거] ✓ 발견! 위치=({center_x}, {center_y}), 점수={max_val:.2f}, 대기={waited:.1f}초")
                    return (center_x, center_y)

                time.sleep(check_interval)
                waited += check_interval

                if waited % 5 < check_interval and waited > 0:
                    logger.info(f"[트리거] 대기 중... {waited:.0f}초 (최고점수: {max_val:.2f})")

            logger.error(f"[트리거] ✗ 타임아웃 ({timeout}초)")
            return None

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
        logger.info("[트리거→클릭] 화면 안정화 대기 (0.3초)")
        time.sleep(0.3)

        # 2. 마우스 캡처 해제
        user32.ReleaseCapture()
        user32.ClipCursor(None)

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
            logger.info(f"[트리거→클릭] 포커스 준비 완료 (hwnd={hwnd})")

    def _find_all_images_on_screen(
        self,
        image_path: str,
        confidence: float = 0.9,
        search_radius: int = 0,
        center_x: int = None,
        center_y: int = None,
    ) -> List[tuple]:
        """
        화면에서 모든 일치하는 이미지 위치 찾기

        Args:
            search_radius: 검색 범위 (0=전체화면, >0=center_x/y 중심 반경 픽셀)
            center_x, center_y: 검색 중심 좌표 (search_radius > 0일 때 사용)

        Returns:
            List[tuple]: 발견된 모든 위치 [(x, y), ...]
        """
        if self._stop_event.is_set():
            return []

        try:
            if not image_path or not Path(image_path).exists():
                return []

            # 중지 체크
            if self._stop_event.is_set():
                return []

            # 화면 캡처
            screenshot = ImageGrab.grab()
            screenshot_np = np.array(screenshot)
            screenshot_bgr = cv2.cvtColor(screenshot_np, cv2.COLOR_RGB2BGR)
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

            # 중지 체크
            if self._stop_event.is_set():
                return []

            # ROI 적용 (search_radius > 0이면 해당 범위만 검색)
            roi_offset_x, roi_offset_y = 0, 0
            if search_radius > 0 and center_x is not None and center_y is not None:
                # ROI 영역 계산
                roi_x1 = max(0, center_x - search_radius)
                roi_y1 = max(0, center_y - search_radius)
                roi_x2 = min(screen_w, center_x + search_radius)
                roi_y2 = min(screen_h, center_y + search_radius)

                # ROI가 템플릿보다 작으면 전체 화면 검색
                if (roi_x2 - roi_x1) > w and (roi_y2 - roi_y1) > h:
                    screenshot_gray = screenshot_gray[roi_y1:roi_y2, roi_x1:roi_x2]
                    roi_offset_x, roi_offset_y = roi_x1, roi_y1

            # 크기 체크: 템플릿이 화면보다 크면 스킵
            scr_h, scr_w = screenshot_gray.shape[:2]
            if h > scr_h or w > scr_w:
                logger.warning(f"템플릿({w}x{h})이 검색 영역({scr_w}x{scr_h})보다 큼 - 스킵")
                return []

            # 템플릿 매칭
            try:
                result = cv2.matchTemplate(screenshot_gray, template_gray, cv2.TM_CCOEFF_NORMED)
            except cv2.error as e:
                logger.error(f"템플릿 매칭 오류: {e}")
                return []

            # 중지 체크
            if self._stop_event.is_set():
                return []

            # 최고 매칭 점수 확인
            min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)

            # 임계값 이상인 모든 위치 찾기
            locations = []
            loc = np.where(result >= confidence)

            for pt in zip(*loc[::-1]):
                found_x = pt[0] + w // 2 + roi_offset_x
                found_y = pt[1] + h // 2 + roi_offset_y
                score = result[pt[1], pt[0]]

                # 중복 제거 (가까운 위치는 하나로)
                is_duplicate = False
                for existing in locations:
                    if abs(existing[0] - found_x) < w // 2 and abs(existing[1] - found_y) < h // 2:
                        is_duplicate = True
                        break

                if not is_duplicate:
                    locations.append((found_x, found_y, float(score)))

            return locations

        except Exception as e:
            logger.error(f"이미지 검색 오류: {e}")
            return []

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

    def _click_at(self, x: int, y: int) -> None:
        """지정된 위치 클릭 (멀티모니터 지원)"""
        from ..utils.input_controller import is_arduino_enabled

        # Arduino가 활성화되어 있으면 Arduino HID 사용
        if is_arduino_enabled():
            input_ctrl = get_input_controller()
            input_ctrl.click(x, y, duration=self._mouse_duration)
            return

        # pyautogui 사용
        pyautogui.moveTo(x, y, duration=self._mouse_duration)
        pos = pyautogui.position()

        if abs(pos[0] - x) < 10 and abs(pos[1] - y) < 10:
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
    ) -> RuleExecutionResult:
        """
        모니터링 모드 실행

        최종 이미지가 나타날 때까지 대기하면서,
        감시 이미지가 나타나면 해당 액션으로 점프 후 복귀
        """
        start_time = datetime.now()

        # 타겟 이미지를 최종 이미지로 사용
        final_image = rule.target_image
        watches = getattr(rule, 'monitoring_watches', []) or []
        confidence = rule.confidence if rule.confidence > 0 else 0.65

        if not final_image:
            return self._make_result(rule, False, "타겟 이미지가 설정되지 않음", start_time)

        if not Path(final_image).exists():
            return self._make_result(rule, False, f"타겟 이미지 파일 없음: {final_image}", start_time)

        # 유효한 감시 이미지만 필터링
        valid_watches = []
        for watch in watches:
            img_path = watch.get('image')
            goto_idx = watch.get('goto_index')
            if img_path and Path(img_path).exists() and goto_idx is not None:
                valid_watches.append(watch)

        final_name = Path(final_image).name

        # 최종 이미지 검색 범위 계산 (rule의 search_radius 사용)
        final_search_region = None
        if rule.search_radius > 0 and rule.action_x is not None and rule.action_y is not None:
            from PIL import ImageGrab
            screen = ImageGrab.grab()
            screen_w, screen_h = screen.size
            x1 = max(0, rule.action_x - rule.search_radius)
            y1 = max(0, rule.action_y - rule.search_radius)
            x2 = min(screen_w, rule.action_x + rule.search_radius)
            y2 = min(screen_h, rule.action_y + rule.search_radius)
            final_search_region = [x1, y1, x2, y2]

        logger.info(f"\033[96m▶ 모니터링 시작 (감시 {len(valid_watches)}개)\033[0m")

        wait_count = 0

        while True:
            # 중지 체크
            if self._stop_event.is_set():
                return self._make_result(rule, False, "실행 중지됨", start_time)

            # 일시정지 대기
            while not self._pause_event.is_set():
                time.sleep(0.1)
                if self._stop_event.is_set():
                    return self._make_result(rule, False, "실행 중지됨", start_time)

            # 1. 최종 이미지 검색 (search_radius가 있으면 해당 범위에서만 검색)
            final_result = self._find_image_on_screen(final_image, confidence, search_region=final_search_region)
            if final_result:
                _, _, final_conf = final_result
                logger.info(f"\033[92m  ✓ 최종 이미지 발견! [{final_name}] ({int(final_conf * 100)}%) - 모니터링 종료\033[0m")
                return self._make_result(rule, True, "모니터링 완료 - 최종 이미지 발견", start_time)

            # 2. 감시 이미지들 검색
            for watch in valid_watches:
                if self._stop_event.is_set():
                    return self._make_result(rule, False, "실행 중지됨", start_time)

                watch_image = watch.get('image')
                goto_index = watch.get('goto_index')
                watch_name = Path(watch_image).name
                search_region = watch.get('search_region')
                # rule의 confidence 통일 사용 (watch별 개별 설정 제거됨)
                watch_confidence = confidence

                # search_radius가 있고 search_region이 없으면 변환
                watch_search_radius = watch.get('search_radius', 0)
                if not search_region and watch_search_radius > 0:
                    watch_center_x = watch.get('center_x') or watch.get('x')
                    watch_center_y = watch.get('center_y') or watch.get('y')
                    if watch_center_x is not None and watch_center_y is not None:
                        screen_w, screen_h = pyautogui.size()
                        x1 = max(0, watch_center_x - watch_search_radius)
                        y1 = max(0, watch_center_y - watch_search_radius)
                        x2 = min(screen_w, watch_center_x + watch_search_radius)
                        y2 = min(screen_h, watch_center_y + watch_search_radius)
                        search_region = [x1, y1, x2, y2]

                logger.debug(f"[감시] {watch_name} 검색 중... (인식률={watch_confidence:.0%}, 검색범위={search_region})")
                watch_result = self._find_image_on_screen(
                    watch_image, watch_confidence,
                    search_region=search_region
                )
                if watch_result:
                    watch_x, watch_y, found_conf = watch_result
                    conf_pct = int(found_conf * 100)
                    if goto_index >= 0:
                        logger.info(f"\033[93m  ⚡ 감시 이미지 발견! [{watch_name}] ({conf_pct}%) → 액션 {goto_index + 1}로 점프\033[0m")
                        self._update_progress(f"감시 이미지 발견 → 액션 {goto_index + 1} 실행")
                    else:
                        logger.info(f"\033[93m  ⚡ 감시 이미지 발견! [{watch_name}] ({conf_pct}%) → 모니터링 액션 실행\033[0m")
                        self._update_progress(f"감시 이미지 발견 → 모니터링 액션 실행")

                    # 모니터링 액션들 순차 실행 (monitor_actions 리스트)
                    monitor_actions = watch.get('monitor_actions', [])
                    # 하위 호환: 단수형 monitor_action도 지원
                    if not monitor_actions and watch.get('monitor_action'):
                        monitor_actions = [watch.get('monitor_action')]

                    # 디버그: monitor_actions 확인
                    logger.debug(f"[모니터링] monitor_actions 개수: {len(monitor_actions)}")

                    for monitor_action in monitor_actions:
                        if self._stop_event.is_set():
                            break
                        if monitor_action and monitor_action.get('type') and monitor_action.get('type') != '없음':
                            # 반복 횟수
                            repeat_count = monitor_action.get('repeat_count', 1)
                            repeat_delay = monitor_action.get('repeat_delay', 0.5)
                            repeat_delay_random = monitor_action.get('repeat_delay_random', False)
                            repeat_delay_range = monitor_action.get('repeat_delay_random_range', 0.3)

                            for repeat_i in range(repeat_count):
                                if self._stop_event.is_set():
                                    break

                                action_result = self._execute_monitor_action(monitor_action, confidence)
                                if action_result:
                                    if repeat_count > 1:
                                        logger.info(f"\033[92m  ✓ 모니터링 액션 완료 ({repeat_i+1}/{repeat_count})\033[0m")
                                    else:
                                        logger.info(f"\033[92m  ✓ 모니터링 액션 완료\033[0m")
                                else:
                                    action_type = monitor_action.get('type', '알수없음')
                                    if repeat_count > 1:
                                        logger.warning(f"\033[93m  ⚠ 모니터링 액션 실패 ({repeat_i+1}/{repeat_count}): {action_type}\033[0m")
                                    else:
                                        logger.warning(f"\033[93m  ⚠ 모니터링 액션 실패: {action_type}\033[0m")

                                # 반복 사이 대기 (마지막 반복 제외)
                                if repeat_i < repeat_count - 1:
                                    delay = repeat_delay
                                    if repeat_delay_random:
                                        delay += random.uniform(-repeat_delay_range, repeat_delay_range)
                                    time.sleep(max(0.05, delay))

                            if self._stop_event.is_set():
                                break

                            # 액션 후 대기시간
                            wait_after = monitor_action.get('wait_after', 0.5)
                            wait_random = monitor_action.get('wait_random', False)
                            wait_range = monitor_action.get('wait_random_range', 0.3)

                            if wait_random:
                                wait_after += random.uniform(-wait_range, wait_range)
                            time.sleep(max(0.05, wait_after))

                    if monitor_actions:
                        time.sleep(0.1)  # 모든 액션 완료 후 대기

                    # 중지 체크 - 모니터링 액션 후 goto 실행 전
                    if self._stop_event.is_set():
                        return self._make_result(rule, False, "실행 중지됨", start_time)

                    # 해당 부모 액션 + 자식들 실행 (goto_index가 유효할 때만)
                    if goto_index >= 0:
                        plan = self._current_plan
                        # 부분 실행 시 원본 rules 사용 (goto_index는 원본 기준)
                        goto_rules = getattr(plan, '_original_initial_rules', None) or plan.initial_rules
                        if plan and goto_index < len(goto_rules):
                            parent_rule = goto_rules[goto_index]
                            # 부모 + 자식 평탄화
                            rules_to_execute = self._flatten_rules([parent_rule])
                            children_count = len(rules_to_execute) - 1

                            # 디버그: 실행할 액션 정보 상세 출력
                            logger.info(f"  📌 goto 실행: 액션 {goto_index + 1} ({parent_rule.action_type}) - {parent_rule.description or ''}")
                            if children_count > 0:
                                logger.info(f"  📂 부모 액션 + 하위 {children_count}개 실행")

                            for exec_rule in rules_to_execute:
                                if self._stop_event.is_set():
                                    break
                                jump_result = self._execute_rule_with_retry(exec_rule)
                                if not jump_result.success:
                                    logger.warning(f"  점프 액션 실패: {jump_result.message}")
                                    break
                                # 대기 시간 적용
                                wait_time = getattr(exec_rule, 'wait_after', 0.5)
                                if wait_time > 0:
                                    time.sleep(wait_time)
                            else:
                                logger.info(f"\033[92m  ✓ 점프 액션 완료 → 모니터링 복귀\033[0m")
                        else:
                            logger.error(f"  잘못된 액션 인덱스: {goto_index} (전체 액션 수: {len(goto_rules)})")
                    # goto_index가 -1이면 점프 없이 모니터링 액션만 실행 (정상 케이스)

                    # 다시 모니터링으로 복귀 (wait_count 초기화)
                    wait_count = 0
                    self._update_progress(f"모니터링 복귀: {final_name} 대기 중")
                    break  # 감시 루프 탈출하고 처음부터 다시 검색

            # 3. 대기
            wait_count += 1
            if wait_count % 20 == 1:  # 10초마다 로그
                logger.info(f"  ⏳ 모니터링 대기 중... {wait_count * 0.5:.0f}초")
            time.sleep(0.5)

    def _execute_monitor_action(
        self,
        monitor_action: dict,
        confidence: float = 0.65,
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

        # 이미지 검색 옵션 (rule.confidence 통일 사용)
        search_confidence = confidence
        search_radius = monitor_action.get('search_radius', 0) or 0

        try:
            if action_type == '텍스트 입력':
                text = monitor_action.get('text', '')
                if text:
                    if typing_random:
                        # 글자별 랜덤 딜레이
                        input_ctrl = get_input_controller()
                        for char in text:
                            input_ctrl.typewrite([char] if char.isascii() else char, interval=0)
                            delay = typing_delay + random.uniform(-typing_delay_range, typing_delay_range)
                            time.sleep(max(0.01, delay))
                    else:
                        self._type_text_with_clipboard(text)
                    text_preview = text[:20] + "..." if len(text) > 20 else text
                    return f"텍스트 입력: {text_preview}"

            elif action_type == '키 입력':
                keys = monitor_action.get('keys', [])
                if keys:
                    input_ctrl = get_input_controller()
                    key_list = [k.lower().strip() for k in keys if k.strip()]
                    if len(key_list) == 1:
                        input_ctrl.press(key_list[0])
                    else:
                        input_ctrl.hotkey(*key_list)
                    return f"키 입력: {'+'.join(key_list)}"

            elif action_type == '마우스 클릭':
                x = monitor_action.get('x')
                y = monitor_action.get('y')
                click_type = monitor_action.get('click_type', 'click')  # click, double_click, right_click
                if x is not None and y is not None:
                    input_ctrl = get_input_controller()
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
                search_region = monitor_action.get('search_region')  # [x1, y1, x2, y2] 또는 None

                # INFO 레벨로 실제 사용 값 출력 (디버깅용)
                logger.info(f"[이미지 클릭] 이미지: {Path(image_path).name if image_path else 'None'}, 인식률: {search_confidence:.0%}, 검색범위: {search_region}")

                # search_radius가 있고 search_region이 없으면 변환
                if not search_region and search_radius > 0:
                    action_center_x = monitor_action.get('x') or monitor_action.get('center_x')
                    action_center_y = monitor_action.get('y') or monitor_action.get('center_y')
                    if action_center_x is not None and action_center_y is not None:
                        screen_w, screen_h = pyautogui.size()
                        x1 = max(0, action_center_x - search_radius)
                        y1 = max(0, action_center_y - search_radius)
                        x2 = min(screen_w, action_center_x + search_radius)
                        y2 = min(screen_h, action_center_y + search_radius)
                        search_region = [x1, y1, x2, y2]
                        logger.debug(f"[이미지 클릭] search_radius로 범위 계산: {search_region}")

                if not image_path:
                    logger.warning(f"[이미지 클릭] 이미지 경로가 설정되지 않음")
                    return None
                if not Path(image_path).exists():
                    logger.warning(f"[이미지 클릭] 이미지 파일 없음: {image_path}")
                    return None

                location = self._find_image_on_screen(image_path, search_confidence, search_region=search_region)
                if location:
                    x, y = location[0], location[1]
                    conf = location[2] if len(location) > 2 else 0
                    logger.info(f"[이미지 클릭] 찾음! 위치=({x}, {y}), 인식률={conf:.0%}")
                    input_ctrl = get_input_controller()
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
                    logger.warning(f"  모니터링 액션 이미지 찾지 못함: {Path(image_path).name}")
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
            logger.error(f"모니터링 액션 실행 오류: {e}")
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

    def _make_result(
        self,
        rule: AutomationRule,
        success: bool,
        message: str,
        start_time: datetime,
    ) -> RuleExecutionResult:
        """실행 결과 생성"""
        execution_time = int((datetime.now() - start_time).total_seconds() * 1000)
        return RuleExecutionResult(
            rule_id=rule.rule_id,
            success=success,
            message=message,
            executed_at=datetime.now(),
            execution_time_ms=execution_time,
        )

    def _update_progress(self, message: str) -> None:
        """진행 상태 업데이트"""
        self._progress.state = self._state
        self._progress.message = message
        if self._on_progress:
            self._on_progress(self._progress)


# 전역 실행 엔진 인스턴스
rule_executor = RuleExecutor()


def get_rule_executor() -> RuleExecutor:
    """규칙 실행 엔진 헬퍼 함수"""
    return rule_executor
