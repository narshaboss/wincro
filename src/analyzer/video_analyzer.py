"""
WinCro 영상 분석 모듈

녹화된 영상과 입력 로그를 분석하여 자동화 계획을 생성합니다.
AI 분석 없이 입력 로그를 직접 변환합니다.
"""

import cv2
import numpy as np
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any, Callable, Tuple
from dataclasses import dataclass
import threading

from ..utils.logger import get_logger
from ..utils.config import get_config, DATA_DIR
from ..database.models import Action, ActionType, Sequence, Recording
from .action_extractor import ActionExtractor
from .automation_models import AutomationPlan, AutomationRule, RuleType

logger = get_logger(__name__)

# 스크린샷 크롭 크기 (클릭 위치 기준 ±픽셀)
SCREENSHOT_CROP_SIZE = 75


@dataclass
class AnalysisProgress:
    """분석 진행 상태"""
    total_frames: int = 0
    processed_frames: int = 0
    current_phase: str = ""
    message: str = ""

    @property
    def progress_percent(self) -> float:
        """진행률 (%)"""
        if self.total_frames == 0:
            return 0.0
        return (self.processed_frames / self.total_frames) * 100


@dataclass
class AnalysisResult:
    """분석 결과"""
    success: bool
    sequence: Optional[Sequence] = None
    error_message: Optional[str] = None
    analysis_time_seconds: float = 0.0
    action_count: int = 0
    frame_count: int = 0


class VideoAnalyzer:
    """
    영상 분석기 클래스

    녹화된 영상에서 클릭 위치 스크린샷을 추출하고
    입력 로그를 자동화 계획으로 변환합니다.
    """

    def __init__(self):
        """영상 분석기 초기화"""
        self._config = get_config()
        self._action_extractor = ActionExtractor()

        # 스레드 안전성을 위한 락
        self._state_lock = threading.Lock()

        self._analyzing = False
        self._cancelled = False
        self._progress = AnalysisProgress()

        # 마지막 자동화 계획
        self._last_automation_plan: Optional[AutomationPlan] = None

        # 콜백
        self._on_progress: Optional[Callable[[AnalysisProgress], None]] = None
        self._on_complete: Optional[Callable[[AnalysisResult], None]] = None
        self._on_automation_plan_ready: Optional[Callable[[AutomationPlan], bool]] = None

    @property
    def is_analyzing(self) -> bool:
        """분석 중 여부"""
        with self._state_lock:
            return self._analyzing

    @property
    def progress(self) -> AnalysisProgress:
        """현재 진행 상태"""
        return self._progress

    @property
    def last_automation_plan(self) -> Optional[AutomationPlan]:
        """마지막 생성된 자동화 계획"""
        with self._state_lock:
            return self._last_automation_plan

    def set_callbacks(
        self,
        on_progress: Optional[Callable[[AnalysisProgress], None]] = None,
        on_complete: Optional[Callable[[AnalysisResult], None]] = None,
        on_automation_plan_ready: Optional[Callable[[AutomationPlan], bool]] = None,
    ) -> None:
        """콜백 설정"""
        self._on_progress = on_progress
        self._on_complete = on_complete
        self._on_automation_plan_ready = on_automation_plan_ready

    def cancel(self) -> None:
        """분석 취소"""
        with self._state_lock:
            self._cancelled = True
        logger.info("영상 분석 취소 요청")

    def _update_progress(
        self,
        phase: str,
        message: str,
        processed: int = 0,
        total: int = 0,
    ) -> None:
        """진행 상태 업데이트"""
        self._progress.current_phase = phase
        self._progress.message = message
        if total > 0:
            self._progress.total_frames = total
            self._progress.processed_frames = processed

        if self._on_progress:
            self._on_progress(self._progress)

    def analyze_for_automation(
        self,
        video_path: str,
        input_log_path: Optional[str] = None,
        plan_name: Optional[str] = None,
    ) -> Optional[AutomationPlan]:
        """
        영상을 분석하여 자동화 계획 생성

        입력 로그에서 동작을 추출하고, 영상에서 클릭 위치 스크린샷을 캡처합니다.

        Args:
            video_path: 영상 파일 경로
            input_log_path: 입력 로그 파일 경로
            plan_name: 자동화 계획 이름

        Returns:
            AutomationPlan: 생성된 자동화 계획, 실패 시 None
        """
        with self._state_lock:
            if self._analyzing:
                logger.warning("이미 분석이 진행 중입니다")
                return None
            self._analyzing = True
            self._cancelled = False

        try:
            # 영상 파일 확인
            video_path_obj = Path(video_path)
            if not video_path_obj.exists():
                raise FileNotFoundError(f"영상 파일을 찾을 수 없습니다: {video_path}")

            logger.info(f"자동화 분석 시작: {video_path}")

            # 입력 로그에서 액션 추출
            self._update_progress("입력 분석", "입력 로그에서 동작 추출 중...")
            actions = []
            input_log_duration = None

            if input_log_path and Path(input_log_path).exists():
                actions = self._action_extractor.extract_from_log(input_log_path)
                logger.info(f"입력 로그에서 {len(actions)}개 동작 추출")

                # 입력 로그 총 시간 읽기 (타임스탬프 보정용)
                try:
                    import json
                    with open(input_log_path, 'r', encoding='utf-8') as f:
                        log_data = json.load(f)
                        input_log_duration = log_data.get('duration_seconds')
                except (json.JSONDecodeError, IOError, OSError) as e:
                    logger.debug(f"입력 로그 시간 읽기 실패: {e}")
                    pass

            if not actions:
                logger.warning("추출된 동작이 없습니다")
                return None

            # 클릭 위치 스크린샷 추출 (입력 로그 시간 전달하여 보정)
            self._update_progress("이미지 추출", "클릭 위치 스크린샷 추출 중...")
            self._extract_click_screenshots(str(video_path), actions, input_log_duration)

            # 자동화 계획 생성
            self._update_progress("계획 생성", "자동화 계획 생성 중...")
            plan = self._create_automation_plan(actions, plan_name or video_path_obj.stem)

            plan.video_path = str(video_path)
            plan.input_log_path = input_log_path

            # 마지막 계획 저장
            with self._state_lock:
                self._last_automation_plan = plan

            logger.info(f"자동화 계획 생성 완료: {plan.name} ({len(plan.initial_rules)}개 동작)")

            # 사용자 검증 콜백 호출
            if self._on_automation_plan_ready:
                user_verified = self._on_automation_plan_ready(plan)
                plan.user_verified = user_verified

            return plan

        except Exception as e:
            logger.error(f"자동화 분석 실패: {e}")
            return None

        finally:
            with self._state_lock:
                self._analyzing = False

    def analyze_for_automation_async(
        self,
        video_path: str,
        input_log_path: Optional[str] = None,
        plan_name: Optional[str] = None,
        on_complete: Optional[Callable[[Optional[AutomationPlan]], None]] = None,
    ) -> None:
        """
        비동기 자동화 분석

        Args:
            video_path: 영상 파일 경로
            input_log_path: 입력 로그 파일 경로
            plan_name: 자동화 계획 이름
            on_complete: 완료 콜백
        """
        def run():
            plan = self.analyze_for_automation(
                video_path=video_path,
                input_log_path=input_log_path,
                plan_name=plan_name,
            )
            if on_complete:
                on_complete(plan)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()

    def _extract_click_screenshots(
        self,
        video_path: str,
        actions: List[Action],
        input_log_duration: Optional[float] = None,
    ) -> None:
        """
        클릭 위치의 스크린샷 추출

        정확한 매칭을 위해:
        1. 실제 녹화 시간과 비디오 시간 차이 보정
        2. 각 클릭에 대해 클릭 직전 프레임 선택
        3. 병렬 저장

        Args:
            video_path: 영상 파일 경로
            actions: 액션 목록
            input_log_duration: 입력 로그 총 시간 (타임스탬프 보정용)
        """
        import uuid
        import os
        from concurrent.futures import ThreadPoolExecutor, as_completed

        output_dir = Path(DATA_DIR / "templates")
        output_dir.mkdir(parents=True, exist_ok=True)
        unique_prefix = uuid.uuid4().hex[:8]

        cap = None
        frame_cache = {}  # finally에서 참조할 수 있도록 미리 초기화

        try:
            # ===== 1단계: 비디오 정보 =====
            cap = cv2.VideoCapture(video_path)
            if not cap.isOpened():
                logger.error(f"영상 파일을 열 수 없습니다: {video_path}")
                return

            fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
            if fps <= 0:
                fps = 30.0
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            # 프레임 수가 유효하지 않은 경우 처리
            if total_frames <= 0:
                logger.error(f"유효하지 않은 프레임 수: {total_frames}")
                return
            frame_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            video_duration = total_frames / fps if fps > 0 else 0

            # 타임스탬프 보정 비율 계산
            # 비디오와 입력 로그의 시간 차이를 보정
            time_scale = 1.0
            if input_log_duration and input_log_duration > 0 and video_duration > 0:
                # 시간 차이가 10% 이상일 때만 보정
                ratio = video_duration / input_log_duration
                if ratio < 0.9 or ratio > 1.1:
                    time_scale = ratio
                    logger.warning(f"타임스탬프 보정: 비디오={video_duration:.2f}초, 입력로그={input_log_duration:.2f}초, 비율={time_scale:.3f}")

            logger.info(f"비디오: {frame_w}x{frame_h}, {total_frames}프레임, {fps:.1f}fps, {video_duration:.2f}초")

            # ===== 2단계: 클릭 데이터 =====
            click_actions = [
                (i, a) for i, a in enumerate(actions)
                if a.action_type in ["click", "double_click", "right_click"]
                and a.x is not None and a.y is not None
            ]

            if not click_actions:
                logger.info("추출할 클릭 없음")
                return

            total_clicks = len(click_actions)
            logger.info(f"클릭 수: {total_clicks}개")

            # 클릭 정보
            clicks = []
            for click_idx, (_, action) in enumerate(click_actions):
                # 타임스탬프 보정 적용
                adjusted_timestamp = action.timestamp * time_scale
                # 클릭 시점의 프레임 번호 계산
                target_frame = int(adjusted_timestamp * fps)
                # 클릭 직전 프레임을 원함 (클릭한 대상 캡처)
                target_frame = max(0, min(target_frame - 1, total_frames - 1))

                clicks.append({
                    'idx': click_idx,
                    'action': action,
                    'timestamp': action.timestamp,
                    'adjusted_timestamp': adjusted_timestamp,
                    'target_frame': target_frame,
                    'x': action.x,
                    'y': action.y,
                    'frame': None,
                })

            # 필요한 프레임 번호들
            needed_frames = set(c['target_frame'] for c in clicks)
            # 앞뒤 여유 프레임 추가
            expanded_frames = set()
            for f in needed_frames:
                for offset in range(-2, 3):
                    expanded_frames.add(max(0, min(f + offset, total_frames - 1)))

            min_frame = min(expanded_frames)
            max_frame = max(expanded_frames)

            logger.info(f"필요 프레임: {min_frame} ~ {max_frame} ({len(expanded_frames)}개)")

            # ===== 3단계: 프레임 읽기 =====
            self._update_progress("이미지 추출", "프레임 읽기 중...", 0, total_clicks)

            frame_cache = {}

            # 시작 프레임으로 이동
            cap.set(cv2.CAP_PROP_POS_FRAMES, min_frame)

            for frame_num in range(min_frame, max_frame + 1):
                ret, frame = cap.read()
                if not ret:
                    break

                if frame_num in expanded_frames:
                    frame_cache[frame_num] = frame.copy()

                if frame_num % 30 == 0:
                    self._update_progress(
                        "이미지 추출",
                        f"프레임 읽기: {frame_num - min_frame}/{max_frame - min_frame}",
                        len(frame_cache), len(expanded_frames)
                    )

            logger.info(f"프레임 캐시: {len(frame_cache)}개")

            # ===== 4단계: 클릭에 프레임 매칭 =====
            cached_frames = sorted(frame_cache.keys())

            for click in clicks:
                target = click['target_frame']

                # 정확히 일치하는 프레임 있으면 사용
                if target in frame_cache:
                    click['frame'] = frame_cache[target]
                    continue

                # 없으면 가장 가까운 프레임 (클릭 직전 우선)
                best_frame = None
                best_diff = float('inf')

                for f in cached_frames:
                    diff = target - f  # 양수면 f가 target 이전
                    abs_diff = abs(diff)

                    # 클릭 이전 프레임 우선 (diff > 0)
                    if abs_diff < best_diff or (abs_diff == best_diff and diff > 0):
                        best_diff = abs_diff
                        best_frame = frame_cache[f]

                click['frame'] = best_frame

                # 프레임을 찾지 못한 경우 경고
                if best_frame is None:
                    logger.warning(f"클릭 {click['idx']}에 대한 프레임을 찾지 못함 (target: {target})")

            matched = sum(1 for c in clicks if c['frame'] is not None)
            logger.info(f"프레임 매칭: {matched}/{total_clicks}개")

            # ===== 5단계: 스크린샷 저장 =====
            self._update_progress("이미지 추출", "저장 중...", total_clicks, total_clicks)

            def save_screenshot(click):
                try:
                    frame = click['frame']
                    if frame is None:
                        return False, click['idx']

                    x, y = click['x'], click['y']
                    h, w = frame.shape[:2]

                    x = max(0, min(x, w - 1))
                    y = max(0, min(y, h - 1))

                    x1 = max(0, x - SCREENSHOT_CROP_SIZE)
                    y1 = max(0, y - SCREENSHOT_CROP_SIZE)
                    x2 = min(w, x + SCREENSHOT_CROP_SIZE)
                    y2 = min(h, y + SCREENSHOT_CROP_SIZE)

                    if x2 - x1 < 10 or y2 - y1 < 10:
                        return False, click['idx']

                    crop = frame[y1:y2, x1:x2]
                    path = str(output_dir / f"click_{unique_prefix}_{click['idx']:04d}.png")

                    if cv2.imwrite(path, crop, [cv2.IMWRITE_PNG_COMPRESSION, 1]):
                        click['action'].target_image = path
                        return True, click['idx']

                    return False, click['idx']
                except Exception as e:
                    logger.debug(f"저장 실패 [{click['idx']}]: {e}")
                    return False, click['idx']

            saved = 0
            failed = 0
            max_workers = min(8, os.cpu_count() or 4)

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [executor.submit(save_screenshot, c) for c in clicks]
                for future in as_completed(futures):
                    try:
                        success, idx = future.result()
                        if success:
                            saved += 1
                        else:
                            failed += 1
                    except Exception as e:
                        logger.debug(f"스크린샷 작업 예외: {e}")
                        failed += 1

            logger.info(f"스크린샷 완료: {saved}/{total_clicks}개" +
                       (f", 실패: {failed}" if failed else ""))

        except Exception as e:
            logger.error(f"스크린샷 추출 실패: {e}")
            import traceback
            logger.error(traceback.format_exc())

        finally:
            if cap is not None:
                cap.release()
            # 메모리 정리
            try:
                frame_cache.clear()
            except (NameError, AttributeError):
                pass

    def _create_automation_plan(
        self,
        actions: List[Action],
        plan_name: str,
    ) -> AutomationPlan:
        """
        액션 목록에서 자동화 계획 생성

        Args:
            actions: 액션 목록
            plan_name: 계획 이름

        Returns:
            AutomationPlan: 생성된 자동화 계획
        """
        rules = []

        for i, action in enumerate(actions):
            # 동작 유형에 따른 규칙 생성
            rule = self._action_to_rule(action, i)
            if rule:
                rules.append(rule)

        # 대기 시간 계산 (다음 동작까지의 시간)
        for i in range(len(rules) - 1):
            if i < len(actions) - 1:
                wait_time = actions[i + 1].timestamp - actions[i].timestamp
                # 최소 0.3초, 최대 10초
                rules[i].wait_after = max(0.3, min(wait_time, 10.0))

        plan = AutomationPlan(
            name=f"{plan_name}_자동화",
            description=f"{len(rules)}개 동작으로 구성된 자동화 계획",
            initial_rules=rules,
            monitoring_rules=[],
        )

        return plan

    def _action_to_rule(self, action: Action, index: int) -> Optional[AutomationRule]:
        """
        Action을 AutomationRule로 변환

        Args:
            action: 변환할 액션
            index: 액션 인덱스

        Returns:
            AutomationRule: 생성된 규칙
        """
        action_type = action.action_type

        # 동작 설명 생성
        if action_type in ["click", "double_click", "right_click"]:
            type_name = {"click": "클릭", "double_click": "더블클릭", "right_click": "우클릭"}
            desc = f"{type_name.get(action_type, action_type)} ({action.x}, {action.y})"
        elif action_type == "type":
            text_preview = (action.text[:20] + "...") if action.text and len(action.text) > 20 else action.text
            desc = f"텍스트 입력: {text_preview}"
        elif action_type == "hotkey":
            keys = " + ".join(action.keys) if action.keys else ""
            desc = f"단축키: {keys}"
        elif action_type == "scroll":
            direction = "위" if (action.scroll_amount or 0) > 0 else "아래"
            desc = f"스크롤 {direction}"
        elif action_type == "drag":
            if action.drag_to_x is not None and action.drag_to_y is not None:
                desc = f"드래그 ({action.x}, {action.y}) → ({action.drag_to_x}, {action.drag_to_y})"
            else:
                desc = f"드래그 ({action.x}, {action.y})"
        else:
            desc = f"{action_type}"

        # 클릭 액션의 경우 검색 범위를 제한 (원래 클릭 위치 근처에서만 이미지 검색)
        # 이렇게 하면 화면 다른 곳에 비슷한 이미지가 있어도 무시하고, 원래 위치에서 대기함
        search_radius = 0  # 기본값: 전체 화면
        if action_type in ["click", "double_click", "right_click"] and action.x is not None and action.y is not None:
            search_radius = 120  # 클릭 위치 중심 120픽셀 반경에서만 검색

        rule = AutomationRule(
            rule_id=f"rule_{index:04d}",
            rule_type=RuleType.FIXED_SEQUENCE.value,
            description=desc,
            action_type=action_type,
            action_x=action.x,
            action_y=action.y,
            action_text=action.text,
            target_image=action.target_image,
            scroll_amount=action.scroll_amount or 0,
            timestamp=action.timestamp,
            wait_after=0.5,  # 기본 대기 시간
            search_radius=search_radius,  # 검색 범위 제한 (0=전체화면, >0=반경 픽셀)
            drag_to_x=action.drag_to_x,  # 드래그 종료 X
            drag_to_y=action.drag_to_y,  # 드래그 종료 Y
            drag_duration=action.drag_duration,  # 드래그 소요 시간
        )

        # 단축키/키 입력 처리
        if action_type in ("hotkey", "key_press") and action.keys:
            rule.action_keys = [k.lower() for k in action.keys]

        return rule

    def get_video_info(self, video_path: str) -> Dict[str, Any]:
        """
        영상 정보 조회

        Args:
            video_path: 영상 파일 경로

        Returns:
            Dict: 영상 정보
        """
        cap = None
        try:
            cap = cv2.VideoCapture(video_path)
            if not cap.isOpened():
                return {}

            fps = cap.get(cv2.CAP_PROP_FPS)
            frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

            if fps <= 0:
                fps = 30.0
            duration_seconds = frame_count / fps if fps > 0 else 0.0

            info = {
                "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                "fps": fps,
                "frame_count": frame_count,
                "duration_seconds": duration_seconds,
            }

            return info

        except Exception as e:
            logger.error(f"영상 정보 조회 실패: {e}")
            return {}

        finally:
            if cap is not None:
                cap.release()


# 전역 영상 분석기 인스턴스
video_analyzer = VideoAnalyzer()


def get_video_analyzer() -> VideoAnalyzer:
    """영상 분석기 헬퍼 함수"""
    return video_analyzer
