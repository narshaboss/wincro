"""
WinCro 동작 재현 모듈

pyautogui를 사용하여 녹화된 동작을 재현합니다.
"""

import time
import random
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import Optional, List, Dict, Any, Callable, Tuple, Generator
from dataclasses import dataclass
from enum import Enum

import pyautogui
import pyperclip


@contextmanager
def _preserve_clipboard() -> Generator[None, None, None]:
    """클립보드 내용을 보존하는 컨텍스트 매니저"""
    original = None
    try:
        try:
            original = pyperclip.paste()
        except (OSError, pyperclip.PyperclipException):
            pass
        yield
    finally:
        if original is not None:
            try:
                time.sleep(0.05)
                pyperclip.copy(original)
            except (OSError, pyperclip.PyperclipException):
                pass
import cv2
import numpy as np
from PIL import ImageGrab
from pathlib import Path
from pynput import keyboard

from ..utils.logger import get_logger, create_execution_logger
from ..utils.config import get_config
from ..utils.input_controller import block_automation_input, get_input_controller, unblock_automation_input
from ..database.models import Action, ActionType, Sequence, ExecutionLog, ExecutionStatus
from ..database.db_manager import get_db
from ..analyzer.template_matcher import get_template_matcher
from ..analyzer.automation_models import AutomationPlan, AutomationRule
from ..recorder.screen_recorder import get_screen_recorder
from .rule_executor import RuleExecutor, get_rule_executor, ExecutionState, ExecutionProgress as RuleExecutionProgress

logger = get_logger(__name__)

# PyAutoGUI 안전 설정
pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.1  # 각 동작 후 대기 시간

# 화면 크기 캐시 (성능 최적화)
_screen_size_cache = None
_screen_size_cache_time = 0
_SCREEN_SIZE_CACHE_TTL = 5.0  # 5초마다 갱신

# 템플릿 이미지 캐시 (성능 최적화)
_template_cache = {}  # {image_path: (template_gray, h, w, mtime)}
_template_cache_lock = threading.Lock()
_MAX_TEMPLATE_CACHE = 50


def _get_screen_size() -> Tuple[int, int]:
    """캐시된 화면 크기 반환 (성능 최적화)"""
    global _screen_size_cache, _screen_size_cache_time
    current_time = time.time()
    if _screen_size_cache is None or (current_time - _screen_size_cache_time) > _SCREEN_SIZE_CACHE_TTL:
        _screen_size_cache = pyautogui.size()
        _screen_size_cache_time = current_time
    return _screen_size_cache


def _get_cached_template(image_path: str):
    """캐시된 템플릿 이미지 반환 (성능 최적화, 스레드 안전)"""
    global _template_cache

    try:
        path = Path(image_path)
        if not path.exists():
            return None, 0, 0

        mtime = path.stat().st_mtime

        # 캐시 확인 (파일 수정 시간으로 유효성 검사, 락 사용)
        with _template_cache_lock:
            if image_path in _template_cache:
                cached = _template_cache[image_path]
                if cached[3] == mtime:  # mtime이 같으면 캐시 사용
                    return cached[0], cached[1], cached[2]

        # 캐시에 없거나 변경됨 - 로드 (락 밖에서 I/O 수행)
        # 한글 경로 지원
        img_array = np.fromfile(image_path, np.uint8)
        template = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        if template is None:
            return None, 0, 0

        template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
        h, w = template_gray.shape

        # 캐시에 저장 (락 사용)
        with _template_cache_lock:
            # 캐시 크기 제한 - LRU 방식으로 오래된 항목 제거
            if len(_template_cache) >= _MAX_TEMPLATE_CACHE:
                # 가장 오래된 항목 제거 (Python 3.7+ dict는 삽입 순서 유지)
                try:
                    oldest_key = next(iter(_template_cache))
                    del _template_cache[oldest_key]
                except (StopIteration, KeyError):
                    pass  # 빈 캐시일 경우 무시

            _template_cache[image_path] = (template_gray, h, w, mtime)

        return template_gray, h, w

    except Exception:
        return None, 0, 0


def _validate_coordinates(x: int, y: int) -> Tuple[int, int]:
    """
    좌표가 화면 범위 내에 있는지 검증하고 범위를 벗어나면 보정합니다.

    Args:
        x: X 좌표
        y: Y 좌표

    Returns:
        Tuple[int, int]: 보정된 (x, y) 좌표
    """
    screen_width, screen_height = _get_screen_size()

    # 화면 크기가 유효하지 않은 경우 기본값 사용
    if screen_width <= 0 or screen_height <= 0:
        logger.warning(f"유효하지 않은 화면 크기: {screen_width}x{screen_height}, 기본값 사용")
        screen_width = 1920
        screen_height = 1080

    # 좌표를 화면 범위 내로 제한
    x = max(0, min(x, screen_width - 1))
    y = max(0, min(y, screen_height - 1))

    return x, y


def _is_valid_coordinate(x: Optional[int], y: Optional[int]) -> bool:
    """좌표가 유효한지 확인합니다."""
    if x is None or y is None:
        return False

    screen_width, screen_height = _get_screen_size()
    return 0 <= x < screen_width and 0 <= y < screen_height


class PlayerState(Enum):
    """플레이어 상태"""
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class PlaybackProgress:
    """재생 진행 상태"""
    current_step: int = 0
    total_steps: int = 0
    current_action: Optional[Action] = None
    elapsed_time: float = 0.0
    state: PlayerState = PlayerState.IDLE
    message: str = ""

    @property
    def progress_percent(self) -> float:
        """진행률 (%)"""
        if self.total_steps == 0:
            return 0.0
        return (self.current_step / self.total_steps) * 100


class EmergencyStopHandler:
    """긴급 중지 핸들러"""

    def __init__(self, stop_key: str = "escape", stop_count: int = 2):
        """
        긴급 중지 핸들러 초기화

        Args:
            stop_key: 중지 키
            stop_count: 중지에 필요한 연속 키 입력 횟수
        """
        self._stop_key = stop_key
        self._stop_count = stop_count
        self._press_times: List[float] = []
        self._triggered = False
        self._listener: Optional[keyboard.Listener] = None
        self._on_stop: Optional[Callable[[], None]] = None

    @property
    def is_triggered(self) -> bool:
        """긴급 중지 트리거 여부"""
        return self._triggered

    def start(self, on_stop: Optional[Callable[[], None]] = None) -> None:
        """핸들러 시작"""
        self._triggered = False
        self._press_times.clear()
        self._on_stop = on_stop

        self._listener = keyboard.Listener(on_press=self._on_key_press)
        self._listener.start()

    def stop(self) -> None:
        """핸들러 중지"""
        if self._listener:
            try:
                self._listener.stop()
                # 리스너 스레드 종료 대기 (최대 1초)
                self._listener.join(timeout=1.0)
            except (OSError, RuntimeError):
                pass
            finally:
                self._listener = None

    def reset(self) -> None:
        """상태 초기화"""
        self._triggered = False
        self._press_times.clear()

    def _on_key_press(self, key) -> None:
        """키 입력 처리"""
        try:
            key_name = key.name if hasattr(key, 'name') else str(key)
        except (AttributeError, ValueError, TypeError):
            key_name = str(key)

        if key_name.lower() == self._stop_key.lower():
            current_time = time.time()
            self._press_times.append(current_time)

            # 오래된 입력 제거 (1초 이내만 유효)
            self._press_times = [t for t in self._press_times if current_time - t < 1.0]

            if len(self._press_times) >= self._stop_count:
                self._triggered = True
                logger.warning("긴급 중지 트리거됨!")
                if self._on_stop:
                    self._on_stop()


class ActionPlayer:
    """
    동작 재현 클래스

    시퀀스의 액션들을 순차적으로 실행합니다.
    """

    def __init__(self):
        """동작 재현기 초기화"""
        self._config = get_config()
        self._db = get_db()
        self._template_matcher = get_template_matcher()
        self._screen_recorder = get_screen_recorder()

        self._state = PlayerState.IDLE
        self._progress = PlaybackProgress()
        self._current_sequence: Optional[Sequence] = None
        self._execution_log: Optional[ExecutionLog] = None
        self._execution_logger = None

        self._thread: Optional[threading.Thread] = None
        self._emergency_stop = EmergencyStopHandler(
            stop_key=self._config.player.emergency_stop_key,
            stop_count=self._config.player.emergency_stop_count,
        )

        # 콜백
        self._on_progress: Optional[Callable[[PlaybackProgress], None]] = None
        self._on_complete: Optional[Callable[[bool, str], None]] = None
        self._on_action_start: Optional[Callable[[int, Action], None]] = None
        self._on_action_complete: Optional[Callable[[int, Action, bool], None]] = None

        # 규칙 실행 엔진
        self._rule_executor: Optional[RuleExecutor] = None
        self._current_automation_plan: Optional[AutomationPlan] = None

    def _get_enabled_actions(self, sequence: Sequence) -> List[Action]:
        """실행 대상 액션만 반환"""
        return [action for action in sequence.actions if getattr(action, "enabled", True)]

    @property
    def state(self) -> PlayerState:
        """현재 상태"""
        return self._state

    @property
    def progress(self) -> PlaybackProgress:
        """진행 상태"""
        return self._progress

    @property
    def is_running(self) -> bool:
        """실행 중 여부"""
        return self._state in [PlayerState.RUNNING, PlayerState.PAUSED]

    def set_callbacks(
        self,
        on_progress: Optional[Callable[[PlaybackProgress], None]] = None,
        on_complete: Optional[Callable[[bool, str], None]] = None,
        on_action_start: Optional[Callable[[int, Action], None]] = None,
        on_action_complete: Optional[Callable[[int, Action, bool], None]] = None,
    ) -> None:
        """콜백 설정"""
        self._on_progress = on_progress
        self._on_complete = on_complete
        self._on_action_start = on_action_start
        self._on_action_complete = on_action_complete

    def play(
        self,
        sequence: Sequence,
        repeat_count: int = 1,
        speed_multiplier: Optional[float] = None,
    ) -> bool:
        """
        시퀀스 실행

        Args:
            sequence: 실행할 시퀀스
            repeat_count: 반복 횟수 (0이면 무한 반복)
            speed_multiplier: 실행 속도 배율

        Returns:
            bool: 시작 성공 여부
        """
        if self._state == PlayerState.RUNNING:
            logger.warning("이미 실행 중입니다")
            return False

        if not sequence.actions:
            logger.warning("시퀀스에 액션이 없습니다")
            return False

        enabled_actions = self._get_enabled_actions(sequence)
        if not enabled_actions:
            logger.warning("활성화된 액션이 없습니다")
            return False

        self._current_sequence = sequence
        self._state = PlayerState.RUNNING
        unblock_automation_input()

        # 진행 상태 초기화
        self._progress = PlaybackProgress(
            total_steps=len(enabled_actions) * (repeat_count if repeat_count > 0 else 1),
            state=PlayerState.RUNNING,
        )

        # 실행 로그 생성
        self._execution_log = ExecutionLog(
            sequence_id=sequence.id,
            sequence_name=sequence.name,
            started_at=datetime.now(),
            status=ExecutionStatus.RUNNING.value,
            total_steps=len(enabled_actions),
        )
        log_id = self._db.create_execution_log(self._execution_log)
        self._execution_log.id = log_id

        # 실행 로거 생성
        self._execution_logger = create_execution_logger(sequence.name)

        # 긴급 중지 핸들러 시작
        self._emergency_stop.start(on_stop=self._on_emergency_stop)

        # 실행 스레드 시작
        self._thread = threading.Thread(
            target=self._play_loop,
            args=(sequence, repeat_count, speed_multiplier),
            daemon=True,
        )
        self._thread.start()

        logger.info(f"시퀀스 실행 시작: {sequence.name}")
        return True

    def pause(self) -> bool:
        """실행 일시 정지"""
        if self._state != PlayerState.RUNNING:
            return False

        self._state = PlayerState.PAUSED
        self._progress.state = PlayerState.PAUSED
        self._update_progress("일시 정지됨")
        logger.info("실행 일시 정지")
        return True

    def resume(self) -> bool:
        """실행 재개"""
        if self._state != PlayerState.PAUSED:
            return False

        self._state = PlayerState.RUNNING
        self._progress.state = PlayerState.RUNNING
        self._update_progress("실행 재개")
        logger.info("실행 재개")
        return True

    def stop(self) -> bool:
        """실행 중지 (비차단 — UI 스레드에서 안전하게 호출 가능)"""
        if not self.is_running:
            return False

        self._state = PlayerState.STOPPED
        block_automation_input("ActionPlayer.stop")
        try:
            get_input_controller().release_all()
        except Exception:
            pass
        self._progress.state = PlayerState.STOPPED
        self._emergency_stop.stop()
        self._update_progress("사용자에 의해 중지됨")
        logger.info("실행 중지")

        # 스레드 종료 대기를 별도 스레드에서 수행 (UI 차단 방지)
        def _join_and_finalize():
            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=2.0)
                if self._thread.is_alive():
                    logger.warning("실행 스레드가 2초 후에도 응답 없음")
            self._finalize_execution(False, "사용자에 의해 중지됨")

        threading.Thread(target=_join_and_finalize, daemon=True).start()
        return True

    def _on_emergency_stop(self) -> None:
        """긴급 중지 콜백"""
        self._state = PlayerState.STOPPED
        self._progress.state = PlayerState.STOPPED
        self._update_progress("긴급 중지!")
        logger.warning("긴급 중지!")

    def _play_loop(
        self,
        sequence: Sequence,
        repeat_count: int,
        speed_multiplier: Optional[float],
    ) -> None:
        """실행 메인 루프"""
        if speed_multiplier is None:
            speed_multiplier = self._config.player.speed_multiplier

        start_time = time.time()
        iteration = 0
        total_completed = 0
        total_failed = 0
        enabled_actions = self._get_enabled_actions(sequence)
        enabled_action_count = len(enabled_actions)

        try:
            while True:
                # 무한 반복이 아닌 경우 횟수 확인
                if repeat_count > 0 and iteration >= repeat_count:
                    break

                iteration += 1
                self._execution_logger.info(f"=== 반복 {iteration} 시작 ===")

                for i, action in enumerate(enabled_actions):
                    # 상태 확인
                    if self._state == PlayerState.STOPPED:
                        self._finalize_execution(False, "중지됨")
                        return

                    if self._emergency_stop.is_triggered:
                        self._finalize_execution(False, "긴급 중지")
                        return

                    # 일시 정지 대기
                    while self._state == PlayerState.PAUSED:
                        time.sleep(0.1)
                        if self._state == PlayerState.STOPPED:
                            return

                    # 진행 상태 업데이트
                    step_number = (iteration - 1) * enabled_action_count + i + 1
                    self._progress.current_step = step_number
                    self._progress.current_action = action
                    self._progress.elapsed_time = time.time() - start_time
                    self._update_progress(f"액션 {i + 1}/{enabled_action_count} 실행 중")

                    if self._on_action_start:
                        try:
                            self._on_action_start(i, action)
                        except Exception as cb_err:
                            logger.warning(f"on_action_start 콜백 오류: {cb_err}")

                    # 액션 반복 횟수
                    action_repeat = getattr(action, 'repeat_count', 1)
                    if action_repeat < 1:
                        action_repeat = 1

                    # 반복 실행
                    success = True
                    error = ""
                    for rep in range(action_repeat):
                        if self._state == PlayerState.STOPPED:
                            break
                        if self._emergency_stop.is_triggered:
                            break

                        # 일시 정지 대기
                        while self._state == PlayerState.PAUSED:
                            time.sleep(0.1)
                            if self._state == PlayerState.STOPPED:
                                break

                        if action_repeat > 1:
                            self._execution_logger.info(f"  [반복 {rep + 1}/{action_repeat}] {action.description or action.action_type}")

                        # 액션 실행
                        success, error = self._execute_action(action, speed_multiplier)

                        if not success:
                            break  # 실패하면 반복 중단

                        # 마지막 반복이 아니면 반복 대기시간 적용
                        if rep < action_repeat - 1:
                            repeat_delay = getattr(action, 'repeat_delay', 0.5)
                            if repeat_delay > 0:
                                # 랜덤 대기시간 적용
                                if getattr(action, 'repeat_delay_random', False):
                                    delay_range = getattr(action, 'repeat_delay_random_range', 0.3)
                                    actual_delay = max(0, repeat_delay + random.uniform(-delay_range, delay_range))
                                else:
                                    actual_delay = repeat_delay
                                time.sleep(actual_delay / speed_multiplier)

                    if success:
                        total_completed += 1
                        self._execution_logger.info(f"액션 {i + 1} 성공: {action}")
                    else:
                        total_failed += 1
                        self._execution_logger.error(f"액션 {i + 1} 실패: {error}")

                    if self._on_action_complete:
                        try:
                            self._on_action_complete(i, action, success)
                        except Exception as cb_err:
                            logger.warning(f"on_action_complete 콜백 오류: {cb_err}")

                self._execution_logger.info(f"=== 반복 {iteration} 완료 ===")

            # 성공적으로 완료
            self._state = PlayerState.COMPLETED
            self._progress.state = PlayerState.COMPLETED
            self._update_progress("완료")

            # 실행 로그 업데이트
            if self._execution_log:
                self._execution_log.completed_steps = total_completed
                self._execution_log.failed_steps = total_failed

            self._finalize_execution(True, "성공적으로 완료")

        except Exception as e:
            logger.error(f"실행 중 오류: {e}")
            self._state = PlayerState.FAILED
            self._progress.state = PlayerState.FAILED
            self._finalize_execution(False, str(e))

    def _find_all_images_on_screen(
        self,
        image_path: str,
        confidence: float = 0.9,
    ) -> List[tuple]:
        """화면에서 모든 일치하는 이미지 위치 찾기 (캐시 사용)"""
        screenshot = None
        screenshot_np = None
        screenshot_gray = None
        result = None
        try:
            if not image_path:
                logger.warning("이미지 경로가 비어있습니다")
                return []

            # 캐시된 템플릿 사용 (성능 최적화)
            template_gray, h, w = _get_cached_template(image_path)
            if template_gray is None:
                logger.warning(f"이미지 로드 실패: {image_path}")
                return []

            # 스크린샷 캡처
            screenshot = ImageGrab.grab()
            screenshot_np = np.array(screenshot)
            screenshot_gray = cv2.cvtColor(screenshot_np, cv2.COLOR_RGB2GRAY)

            # 크기 체크: 템플릿이 화면보다 크면 스킵
            scr_h, scr_w = screenshot_gray.shape[:2]
            if h > scr_h or w > scr_w:
                logger.warning(f"템플릿({w}x{h})이 화면({scr_w}x{scr_h})보다 큼 - 스킵")
                return []

            try:
                result = cv2.matchTemplate(screenshot_gray, template_gray, cv2.TM_CCOEFF_NORMED)
            except cv2.error as e:
                logger.error(f"템플릿 매칭 오류: {e}")
                return []
            time.sleep(0)  # GIL 해제

            # 최고 매칭 점수 확인 (디버그용)
            min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)
            logger.debug(f"이미지 매칭 최고 점수: {max_val:.3f} (임계값: {confidence})")

            # 매칭 위치 찾기 (최적화된 중복 제거)
            loc = np.where(result >= confidence)
            points = list(zip(*loc[::-1]))

            if not points:
                return []

            # 중복 제거 (간격 기반)
            locations = []
            half_w, half_h = w // 2, h // 2
            for pt in points:
                center_x = pt[0] + half_w
                center_y = pt[1] + half_h

                # 기존 위치와 비교
                is_duplicate = False
                for ex, ey in locations:
                    if abs(ex - center_x) < half_w and abs(ey - center_y) < half_h:
                        is_duplicate = True
                        break

                if not is_duplicate:
                    locations.append((center_x, center_y))

            return locations

        except Exception as e:
            logger.error(f"이미지 검색 오류: {e}")
            return []
        finally:
            # 메모리 해제 (저사양 PC 지원)
            del screenshot, screenshot_np, screenshot_gray, result

    def _find_closest_image(
        self,
        locations: List[tuple],
        hint_x: int,
        hint_y: int,
    ) -> Optional[tuple]:
        """좌표 힌트와 가장 가까운 이미지 위치 반환"""
        if not locations:
            return None

        if len(locations) == 1:
            return locations[0]

        closest = min(locations, key=lambda loc:
            (loc[0] - hint_x) ** 2 + (loc[1] - hint_y) ** 2
        )
        logger.info(f"여러 이미지 중 좌표({hint_x}, {hint_y})와 가장 가까운 위치 선택: {closest}")
        return closest

    def _execute_action(
        self,
        action: Action,
        speed_multiplier: float,
    ) -> tuple[bool, str]:
        """
        단일 액션 실행

        Args:
            action: 실행할 액션
            speed_multiplier: 속도 배율

        Returns:
            tuple: (성공 여부, 에러 메시지)
        """
        try:
            # 실행 전 대기
            if action.wait_before > 0:
                time.sleep(action.wait_before / speed_multiplier)

            # 이미지 기반 인식 우선, 좌표는 대체 수단
            x, y = action.x, action.y

            if action.target_image:
                # 이미지 매칭 시도 - 이미지가 나타날 때까지 대기 (타임아웃 있음)
                # 기본 신뢰도 0.8 (0.9는 너무 엄격함)
                confidence = action.confidence if action.confidence and action.confidence > 0 else 0.8
                locations = []

                # 이미지 파일 존재 확인
                if not Path(action.target_image).exists():
                    logger.error(f"조건 이미지 파일이 존재하지 않습니다: {action.target_image}")
                    return False, f"조건 이미지 파일 없음: {action.target_image}"

                # 스킵 모드 확인
                skip_on_not_found = getattr(action, 'skip_on_not_found', False)
                # 스킵 모드면 wait_after를 타임아웃으로, 아니면 무제한
                skip_timeout = action.wait_after if skip_on_not_found and action.wait_after > 0 else 0
                start_wait_time = time.time()

                # 이미지가 나타날 때까지 대기 (무제한 - stop으로만 중지)
                while not locations:
                    elapsed = time.time() - start_wait_time

                    # 스킵 모드일 때만 타임아웃 체크
                    if skip_on_not_found and skip_timeout > 0 and elapsed > skip_timeout:
                        logger.info(f"\033[93m⏭ 스킵: 이미지 못찾음 ({skip_timeout:.1f}초 대기 후 스킵)\033[0m")
                        return True, f"스킵됨 (이미지 없음, {skip_timeout:.1f}초 대기)"

                    # 긴급 중지 확인
                    if self._emergency_stop.is_triggered:
                        return False, "긴급 중지"
                    if self._state == PlayerState.STOPPED:
                        return False, "중지됨"

                    # 일시 정지 대기
                    while self._state == PlayerState.PAUSED:
                        time.sleep(0.1)
                        if self._state == PlayerState.STOPPED:
                            return False, "중지됨"
                    # After pause loop - check if we should exit
                    if self._state == PlayerState.STOPPED:
                        return False, "중지됨"

                    locations = self._find_all_images_on_screen(action.target_image, confidence)
                    if not locations:
                        # 30초마다 간단한 대기 상태 로그
                        elapsed_int = int(elapsed)
                        if elapsed_int % 30 == 0 and elapsed_int > 0:
                            if not hasattr(self, '_last_wait_log_time') or self._last_wait_log_time != elapsed_int:
                                logger.debug(f"이미지 대기 중: {elapsed_int}초 - {Path(action.target_image).name}")
                                self._last_wait_log_time = elapsed_int
                        time.sleep(0.5)  # 0.5초마다 재검색

                if not locations:
                    return False, "이미지를 찾을 수 없습니다"
                elif len(locations) == 1:
                    x, y = locations[0]
                    logger.info(f"이미지 매칭 성공: ({x}, {y})")
                else:
                    # 여러 이미지 발견 시 저장된 좌표와 가장 가까운 것 선택
                    if action.x is not None and action.y is not None:
                        result = self._find_closest_image(locations, action.x, action.y)
                        if result:
                            x, y = result
                    else:
                        # 좌표 힌트가 없으면 첫 번째 사용
                        x, y = locations[0]
                        logger.info(f"이미지 매칭 성공 (첫 번째 사용): ({x}, {y})")

            # 액션 유형별 실행
            action_type = action.action_type
            input_ctrl = get_input_controller()

            if action_type == ActionType.CLICK.value:
                if x is not None and y is not None:
                    x, y = _validate_coordinates(x, y)
                    input_ctrl.click(x, y)
                else:
                    return False, "클릭 좌표가 없습니다"

            elif action_type == ActionType.DOUBLE_CLICK.value:
                if x is not None and y is not None:
                    x, y = _validate_coordinates(x, y)
                    input_ctrl.double_click(x, y)
                else:
                    return False, "클릭 좌표가 없습니다"

            elif action_type == ActionType.RIGHT_CLICK.value:
                if x is not None and y is not None:
                    x, y = _validate_coordinates(x, y)
                    input_ctrl.right_click(x, y)
                else:
                    return False, "클릭 좌표가 없습니다"

            elif action_type == ActionType.DRAG.value:
                if x is not None and y is not None and action.drag_to_x is not None and action.drag_to_y is not None:
                    duration = self._config.player.mouse_move_duration / speed_multiplier
                    # 원래 상대 이동량 계산 (이미지 매칭 시에도 드래그 벡터 유지)
                    if action.x is not None and action.y is not None:
                        delta_x = action.drag_to_x - action.x
                        delta_y = action.drag_to_y - action.y
                        end_x = x + delta_x
                        end_y = y + delta_y
                    else:
                        # action.x/y가 없으면 drag_to를 그대로 사용
                        end_x = action.drag_to_x
                        end_y = action.drag_to_y
                    input_ctrl.drag(x, y, end_x, end_y, duration=duration)
                else:
                    return False, "드래그 좌표가 없습니다"

            elif action_type == ActionType.SCROLL.value:
                input_ctrl.scroll(action.scroll_amount, x, y)

            elif action_type == ActionType.TYPE.value:
                if action.text:
                    interval = self._config.player.typing_interval / speed_multiplier
                    typing_random = getattr(action, 'typing_random', False)
                    typing_delay = getattr(action, 'typing_delay', 0.1)
                    typing_delay_range = getattr(action, 'typing_delay_range', 0.05)
                    self._type_text_with_clipboard(action.text, interval, typing_random, typing_delay, typing_delay_range)
                else:
                    return False, "입력할 텍스트가 없습니다"

            elif action_type == ActionType.HOTKEY.value:
                key_events = getattr(action, "key_events", None) or []
                if key_events:
                    input_ctrl.replay_key_events(key_events, speed_multiplier=speed_multiplier)
                elif action.keys:
                    keys = [k.lower() for k in action.keys]
                    input_ctrl.hotkey(*keys)
                else:
                    return False, "단축키가 없습니다"

            elif action_type == ActionType.KEY_PRESS.value:
                key_events = getattr(action, "key_events", None) or []
                if key_events:
                    input_ctrl.replay_key_events(key_events, speed_multiplier=speed_multiplier)
                elif action.keys:
                    keys = [str(key).lower().strip() for key in action.keys if str(key).strip()]
                    if len(keys) == 1:
                        input_ctrl.press(keys[0])
                    elif keys:
                        input_ctrl.hotkey(*keys)
                else:
                    return False, "키가 없습니다"

            elif action_type == ActionType.WAIT.value:
                time.sleep(action.duration / speed_multiplier)

            elif action_type == ActionType.WAIT_FOR_IMAGE.value:
                # 이미지가 나타나거나 사라질 때까지 대기
                success, error = self._wait_for_image(action)
                if not success:
                    return False, error

            else:
                return False, f"알 수 없는 액션 유형: {action_type}"

            # 실행 후 대기
            wait_time = action.wait_after
            # 랜덤 대기시간 적용 (기본값 ± 범위)
            if getattr(action, 'wait_random', False):
                wait_range = getattr(action, 'wait_random_range', 0.3)
                wait_time = wait_time + random.uniform(-wait_range, wait_range)
                wait_time = max(0, wait_time)  # 음수 방지
            if wait_time > 0:
                time.sleep(wait_time / speed_multiplier)

            return True, ""

        except pyautogui.FailSafeException:
            return False, "페일세이프 트리거 (화면 모서리로 마우스 이동)"

        except Exception as e:
            return False, str(e)

    def _wait_for_image(self, action: Action) -> tuple[bool, str]:
        """
        이미지가 나타나거나 사라질 때까지 대기

        Args:
            action: WAIT_FOR_IMAGE 액션

        Returns:
            tuple: (성공 여부, 에러 메시지)
        """
        if not action.wait_for_image:
            return False, "대기할 이미지가 지정되지 않았습니다"

        disappear = action.wait_for_image_disappear
        check_interval = 0.5  # 0.5초마다 확인

        start_time = time.time()
        mode = "사라짐" if disappear else "나타남"

        logger.info(f"이미지 대기 시작: {mode} 대기 (무제한)")

        while True:
            # 긴급 중지 확인
            if self._emergency_stop.is_triggered:
                return False, "긴급 중지"

            # 상태 확인
            if self._state == PlayerState.STOPPED:
                return False, "중지됨"

            # 일시 정지 대기
            while self._state == PlayerState.PAUSED:
                time.sleep(0.1)
                if self._state == PlayerState.STOPPED:
                    return False, "중지됨"
            # After pause loop - check if we should exit
            if self._state == PlayerState.STOPPED:
                return False, "중지됨"

            # 화면 캡처 및 이미지 매칭
            screen = None
            try:
                screen = self._screen_recorder.capture_screenshot()
                if screen is not None:
                    # TM_CCOEFF_NORMED 직접 매칭 (rule_executor 방식 통일)
                    img_array = np.fromfile(action.wait_for_image, np.uint8)
                    template = cv2.imdecode(img_array, cv2.IMREAD_GRAYSCALE)
                    if template is not None:
                        screen_gray = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY) if len(screen.shape) == 3 else screen
                        th, tw = template.shape[:2]
                        sh, sw = screen_gray.shape[:2]
                        found = False
                        center_x, center_y = 0, 0
                        if tw <= sw and th <= sh:
                            match_result = cv2.matchTemplate(screen_gray, template, cv2.TM_CCOEFF_NORMED)
                            time.sleep(0)  # GIL 해제
                            _, max_val, _, max_loc = cv2.minMaxLoc(match_result)
                            if max_val >= action.confidence:
                                found = True
                                center_x = max_loc[0] + tw // 2
                                center_y = max_loc[1] + th // 2

                        elapsed = time.time() - start_time

                        if disappear:
                            # 이미지가 사라질 때까지 대기
                            if not found:
                                logger.info(f"이미지 사라짐 감지 (대기 시간: {elapsed:.1f}초)")
                                return True, ""
                        else:
                            # 이미지가 나타날 때까지 대기
                            if found:
                                logger.info(f"이미지 나타남 감지: ({center_x}, {center_y}) (대기 시간: {elapsed:.1f}초)")
                                return True, ""
            finally:
                # 메모리 해제 (저사양 PC 지원)
                del screen

            time.sleep(check_interval)

    def _type_text_with_clipboard(self, text: str, interval: float = 0.05,
                                    typing_random: bool = False,
                                    typing_delay: float = 0.1, typing_delay_range: float = 0.05) -> None:
        """클립보드를 사용하여 텍스트 입력 (한글 지원)"""
        if not text:
            return

        input_ctrl = get_input_controller()

        # ASCII만 있는 경우 직접 입력
        if text.isascii():
            if typing_random:
                for char in text:
                    input_ctrl.type_text(char)
                    delay = max(0, typing_delay + random.uniform(-typing_delay_range, typing_delay_range))
                    time.sleep(delay)
            else:
                input_ctrl.type_text(text, interval=interval)
            return

        # 한글 등 비ASCII 문자는 클립보드 사용
        with _preserve_clipboard():
            if typing_random:
                for char in text:
                    pyperclip.copy(char)
                    time.sleep(0.02)
                    input_ctrl.hotkey('ctrl', 'v')
                    delay = max(0, typing_delay + random.uniform(-typing_delay_range, typing_delay_range))
                    time.sleep(delay)
            else:
                pyperclip.copy(text)
                time.sleep(0.05)
                input_ctrl.hotkey('ctrl', 'v')
                time.sleep(0.1)

    def _update_progress(self, message: str) -> None:
        """진행 상태 업데이트"""
        self._progress.message = message
        if self._on_progress:
            self._on_progress(self._progress)

    def _finalize_execution(self, success: bool, message: str) -> None:
        """실행 완료 처리"""
        self._emergency_stop.stop()

        # 실행 로그 업데이트
        if self._execution_log:
            self._execution_log.ended_at = datetime.now()
            self._execution_log.status = (
                ExecutionStatus.COMPLETED.value if success
                else ExecutionStatus.FAILED.value
            )
            self._execution_log.error_message = None if success else message

            if self._execution_log.started_at:
                self._execution_log.execution_time_ms = int(
                    (self._execution_log.ended_at - self._execution_log.started_at).total_seconds() * 1000
                )

            self._db.update_execution_log(self._execution_log)

            # 시퀀스 통계 업데이트
            if self._current_sequence and self._current_sequence.id:
                self._db.update_sequence_run_stats(self._current_sequence.id, success)

        # 콜백 호출
        if self._on_complete:
            self._on_complete(success, message)

        logger.info(f"실행 완료: {'성공' if success else '실패'} - {message}")


    def play_automation_plan(
        self,
        plan: AutomationPlan,
        on_rule_progress: Optional[Callable[[RuleExecutionProgress], None]] = None,
    ) -> bool:
        """
        자동화 계획 실행 (규칙 기반)

        Args:
            plan: 실행할 자동화 계획
            on_rule_progress: 규칙 실행 진행 콜백

        Returns:
            bool: 시작 성공 여부
        """
        if self._state == PlayerState.RUNNING:
            logger.warning("이미 실행 중입니다")
            return False

        if not plan.initial_rules and not plan.monitoring_rules:
            logger.warning("자동화 계획에 규칙이 없습니다")
            return False

        self._current_automation_plan = plan
        self._state = PlayerState.RUNNING

        # 규칙 실행 엔진 가져오기
        self._rule_executor = get_rule_executor()

        # 규칙 실행 콜백 설정
        self._rule_executor.set_callbacks(
            on_progress=lambda p: self._on_rule_executor_progress(p, on_rule_progress),
            on_complete=self._on_rule_executor_complete,
            on_error=self._on_rule_executor_error,
        )

        # 긴급 중지 핸들러 시작
        self._emergency_stop.start(on_stop=self._on_emergency_stop_rule)

        # 규칙 실행 시작
        logger.info(f"자동화 계획 실행 시작: {plan.name}")
        logger.info(f"  - 초기 규칙: {len(plan.initial_rules)}개")
        logger.info(f"  - 모니터링 규칙: {len(plan.monitoring_rules)}개")

        success = self._rule_executor.execute_plan(plan)

        if not success:
            self._state = PlayerState.IDLE
            logger.error("자동화 계획 실행 시작 실패")

        return success

    def stop_automation_plan(self) -> bool:
        """자동화 계획 실행 중지"""
        if self._rule_executor is None:
            return False

        self._rule_executor.stop()
        self._emergency_stop.stop()
        self._state = PlayerState.STOPPED
        logger.info("자동화 계획 실행 중지")
        return True

    def pause_automation_plan(self) -> bool:
        """자동화 계획 실행 일시정지"""
        if self._rule_executor is None:
            return False

        self._rule_executor.pause()
        self._state = PlayerState.PAUSED
        logger.info("자동화 계획 실행 일시정지")
        return True

    def resume_automation_plan(self) -> bool:
        """자동화 계획 실행 재개"""
        if self._rule_executor is None:
            return False

        self._rule_executor.resume()
        self._state = PlayerState.RUNNING
        logger.info("자동화 계획 실행 재개")
        return True

    def _on_rule_executor_progress(
        self,
        progress: RuleExecutionProgress,
        callback: Optional[Callable[[RuleExecutionProgress], None]],
    ) -> None:
        """규칙 실행 진행 콜백"""
        # 상태 동기화
        if progress.state == ExecutionState.MONITORING:
            self._progress.message = f"모니터링 중... (발동: {progress.monitoring_triggers}회)"
        else:
            self._progress.message = progress.message

        self._progress.current_step = progress.initial_completed
        self._progress.total_steps = progress.initial_total

        if callback:
            callback(progress)

    def _on_rule_executor_complete(self, success: bool, message: str) -> None:
        """규칙 실행 완료 콜백"""
        self._emergency_stop.stop()

        if success:
            self._state = PlayerState.COMPLETED
            logger.info(f"자동화 계획 실행 완료: {message}")
        else:
            self._state = PlayerState.FAILED
            logger.error(f"자동화 계획 실행 실패: {message}")

        if self._on_complete:
            self._on_complete(success, message)

    def _on_rule_executor_error(self, error: str, rule: AutomationRule) -> None:
        """규칙 실행 오류 콜백"""
        logger.error(f"규칙 오류: {rule.rule_id} - {error}")

    def _on_emergency_stop_rule(self) -> None:
        """규칙 실행 긴급 중지 콜백"""
        if self._rule_executor:
            self._rule_executor.stop()
        self._state = PlayerState.STOPPED
        logger.warning("규칙 실행 긴급 중지!")

    @property
    def current_automation_plan(self) -> Optional[AutomationPlan]:
        """현재 실행 중인 자동화 계획"""
        return self._current_automation_plan

    @property
    def rule_executor_state(self) -> Optional[ExecutionState]:
        """규칙 실행 엔진 상태"""
        if self._rule_executor:
            return self._rule_executor.state
        return None


# 전역 동작 재현기 인스턴스
action_player = ActionPlayer()


def get_action_player() -> ActionPlayer:
    """동작 재현기 헬퍼 함수"""
    return action_player
