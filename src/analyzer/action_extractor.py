"""
WinCro 액션 추출 모듈

입력 로그에서 재현 가능한 액션 시퀀스를 추출합니다.
"""

import json
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass

from ..utils.logger import get_logger
from ..utils.config import get_config
from ..database.models import Action, ActionType
from ..recorder.input_logger import InputEvent, InputEventType

logger = get_logger(__name__)


@dataclass
class ExtractedClick:
    """추출된 클릭 정보"""
    x: int
    y: int
    button: str
    click_type: str  # single, double, right
    timestamp: float
    modifiers: List[str]


# 더블클릭 감지 상수
DOUBLE_CLICK_TIME_MS = 500        # 더블클릭 판정 시간 (밀리초)
DOUBLE_CLICK_POSITION_PX = 5      # 더블클릭 판정 위치 오차 (픽셀)
DOUBLE_CLICK_POST_TIME = 0.5      # 후처리 더블클릭 판정 시간 (초)

# 대기 시간 상수
MIN_WAIT_TIME = 0.1               # 최소 대기 시간 (초)
MAX_WAIT_TIME = 5.0               # 최대 대기 시간 (초)


class ActionExtractor:
    """
    액션 추출기 클래스

    입력 이벤트 로그를 분석하여 재현 가능한 액션 시퀀스를 생성합니다.
    """

    def __init__(self):
        """액션 추출기 초기화"""
        self._config = get_config()
        self._merge_threshold_ms = self._config.analyzer.action_merge_threshold_ms

    def extract_from_log(self, log_path: str) -> List[Action]:
        """
        입력 로그 파일에서 액션 추출

        Args:
            log_path: 입력 로그 파일 경로

        Returns:
            List[Action]: 추출된 액션 목록
        """
        try:
            # 여러 인코딩 시도
            data = None
            encodings = ['utf-8', 'utf-8-sig', 'cp949', 'euc-kr']

            for encoding in encodings:
                try:
                    with open(log_path, 'r', encoding=encoding) as f:
                        data = json.load(f)
                    break
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue

            if data is None:
                logger.error(f"로그 파일 인코딩 실패: {log_path}")
                return []

            events = [self._dict_to_event(e) for e in data.get("events", [])]
            return self.extract_from_events(events)

        except Exception as e:
            logger.error(f"로그 파일 로드 실패: {e}")
            return []

    def _dict_to_event(self, data: Dict[str, Any]) -> InputEvent:
        """딕셔너리를 InputEvent로 변환"""
        return InputEvent(
            event_type=data.get("event_type", ""),
            timestamp=data.get("timestamp", 0),
            x=data.get("x"),
            y=data.get("y"),
            button=data.get("button"),
            pressed=data.get("pressed"),
            key=data.get("key"),
            scroll_dx=data.get("scroll_dx"),
            scroll_dy=data.get("scroll_dy"),
            modifiers=data.get("modifiers", []),
            drag_duration=data.get("drag_duration"),
        )

    def extract_from_events(self, events: List[InputEvent]) -> List[Action]:
        """
        입력 이벤트 목록에서 액션 추출

        Args:
            events: 입력 이벤트 목록

        Returns:
            List[Action]: 추출된 액션 목록
        """
        if not events:
            return []

        actions = []

        # 이벤트 그룹화 및 분석
        clicks = self._extract_clicks(events)
        drags = self._extract_drags(events)
        scrolls = self._extract_scrolls(events)
        key_inputs = self._extract_key_inputs(events)

        # 모든 액션을 타임스탬프 순으로 병합
        all_actions = clicks + drags + scrolls + key_inputs
        all_actions.sort(key=lambda a: a.timestamp)

        # 대기 시간 계산 및 추가
        actions = self._add_waits(all_actions)

        logger.info(f"액션 추출 완료: {len(actions)}개")
        return actions

    def _extract_clicks(self, events: List[InputEvent]) -> List[Action]:
        """클릭 이벤트 추출"""
        actions = []
        click_events = [e for e in events if e.event_type == InputEventType.MOUSE_CLICK.value]

        i = 0
        while i < len(click_events):
            event = click_events[i]

            # 누름 이벤트만 처리
            if not event.pressed:
                i += 1
                continue

            # 더블클릭 감지: 다음 누름 이벤트 확인 (release 건너뛰고)
            if i + 2 < len(click_events):
                next_press = click_events[i + 2]
                if next_press.pressed:
                    time_diff = (next_press.timestamp - event.timestamp) * 1000
                    # 더블클릭 조건: 500ms 이내 + 같은 버튼 + 같은 위치 (5픽셀 이내)
                    # 좌표가 None인 경우 같은 위치로 판단하지 않음
                    if event.x is None or event.y is None or next_press.x is None or next_press.y is None:
                        same_position = False
                    else:
                        same_position = (
                            abs(event.x - next_press.x) <= DOUBLE_CLICK_POSITION_PX and
                            abs(event.y - next_press.y) <= DOUBLE_CLICK_POSITION_PX
                        )
                    if time_diff < DOUBLE_CLICK_TIME_MS and event.button == next_press.button and same_position:
                        # 더블클릭
                        action = Action(
                            action_type=ActionType.DOUBLE_CLICK.value,
                            x=event.x,
                            y=event.y,
                            button=event.button or "left",
                            timestamp=event.timestamp,
                        )
                        actions.append(action)
                        # 안전하게 인덱스 증가 (범위 초과 방지)
                        i = min(i + 4, len(click_events))
                        continue

            # 일반 클릭
            if event.button == "right":
                action_type = ActionType.RIGHT_CLICK.value
            else:
                action_type = ActionType.CLICK.value

            action = Action(
                action_type=action_type,
                x=event.x,
                y=event.y,
                button=event.button or "left",
                timestamp=event.timestamp,
            )
            actions.append(action)
            # 안전하게 인덱스 증가 (범위 초과 방지)
            i = min(i + 2, len(click_events))

        return actions

    def _extract_drags(self, events: List[InputEvent]) -> List[Action]:
        """드래그 이벤트 추출"""
        actions = []

        drag_starts = [e for e in events if e.event_type == InputEventType.MOUSE_DRAG_START.value]
        drag_ends = [e for e in events if e.event_type == InputEventType.MOUSE_DRAG_END.value]

        # 타임스탬프 기반으로 드래그 시작/끝 매칭
        used_ends = set()
        for start in drag_starts:
            # 가장 가까운 종료 이벤트 찾기
            best_end = None
            best_time_diff = float('inf')

            for idx, end in enumerate(drag_ends):
                if idx in used_ends:
                    continue
                # 시작 이후의 종료 이벤트 중 가장 가까운 것 (동시 타임스탬프도 허용)
                time_diff = end.timestamp - start.timestamp
                if 0 <= time_diff < best_time_diff:
                    best_time_diff = time_diff
                    best_end = (idx, end)

            if best_end:
                used_ends.add(best_end[0])
                end = best_end[1]
                action = Action(
                    action_type=ActionType.DRAG.value,
                    x=start.x,
                    y=start.y,
                    drag_to_x=end.x,
                    drag_to_y=end.y,
                    drag_duration=start.drag_duration,  # 드래그 소요 시간
                    button=start.button or "left",
                    timestamp=start.timestamp,
                )
                actions.append(action)
            else:
                logger.warning(f"드래그 시작에 대응하는 끝 이벤트 없음: timestamp={start.timestamp}")

        return actions

    def _extract_scrolls(self, events: List[InputEvent]) -> List[Action]:
        """스크롤 이벤트 추출"""
        actions = []
        scroll_events = [e for e in events if e.event_type == InputEventType.MOUSE_SCROLL.value]

        # 연속된 스크롤 이벤트 병합
        merged_scrolls = []
        current_scroll = None

        for event in scroll_events:
            if current_scroll is None:
                current_scroll = {
                    "x": event.x,
                    "y": event.y,
                    "amount": event.scroll_dy or 0,
                    "timestamp": event.timestamp,
                }
            else:
                time_diff = (event.timestamp - current_scroll["timestamp"]) * 1000
                if time_diff < self._merge_threshold_ms:
                    # 병합
                    current_scroll["amount"] += event.scroll_dy or 0
                else:
                    # 새 스크롤 시작
                    merged_scrolls.append(current_scroll)
                    current_scroll = {
                        "x": event.x,
                        "y": event.y,
                        "amount": event.scroll_dy or 0,
                        "timestamp": event.timestamp,
                    }

        if current_scroll:
            merged_scrolls.append(current_scroll)

        # 액션 생성
        for scroll in merged_scrolls:
            if scroll["amount"] != 0:
                action = Action(
                    action_type=ActionType.SCROLL.value,
                    x=scroll["x"],
                    y=scroll["y"],
                    scroll_amount=scroll["amount"],
                    timestamp=scroll["timestamp"],
                )
                actions.append(action)

        return actions

    def _extract_key_inputs(self, events: List[InputEvent]) -> List[Action]:
        """키보드 입력 추출"""
        actions = []
        key_events = [e for e in events if e.event_type in [
            InputEventType.KEY_PRESS.value,
            InputEventType.KEY_RELEASE.value
        ]]

        if not key_events:
            return actions

        # 텍스트 입력과 단축키 분리
        current_text = ""
        text_start_time = None
        i = 0

        while i < len(key_events):
            event = key_events[i]

            if event.event_type != InputEventType.KEY_PRESS.value:
                i += 1
                continue

            key = event.key or ""

            # 수정자 키 확인
            is_modifier = any(mod in key.lower() for mod in ['ctrl', 'alt', 'shift', 'cmd', 'win'])

            if is_modifier or event.modifiers:
                # 현재까지의 텍스트 입력 저장
                if current_text:
                    action = Action(
                        action_type=ActionType.TYPE.value,
                        text=current_text,
                        timestamp=text_start_time if text_start_time is not None else event.timestamp,
                    )
                    actions.append(action)
                    current_text = ""
                    text_start_time = None

                # 단축키 처리
                if event.modifiers:
                    hotkey_keys = event.modifiers.copy()
                    if not is_modifier and key:
                        # 문자 키 추출
                        if key.startswith("Key."):
                            hotkey_keys.append(key.replace("Key.", ""))
                        elif len(key) == 1:
                            hotkey_keys.append(key)

                    if hotkey_keys:
                        action = Action(
                            action_type=ActionType.HOTKEY.value,
                            keys=hotkey_keys,
                            timestamp=event.timestamp,
                        )
                        actions.append(action)

            else:
                # 일반 텍스트 입력
                if text_start_time is None:
                    text_start_time = event.timestamp

                # 특수 키 처리
                if key.startswith("Key."):
                    key_name = key.replace("Key.", "")
                    if key_name == "space":
                        current_text += " "
                    elif key_name == "backspace" and current_text:
                        current_text = current_text[:-1]
                    elif (key_name in ["enter", "tab", "left", "right", "up", "down",
                                       "home", "end", "page_up", "page_down", "insert", "delete", "esc"]
                          or (key_name.startswith("f") and key_name[1:].isdigit())):
                        # Enter, Tab, 방향키, 기능키 등은 키 입력으로 처리
                        if current_text:
                            action = Action(
                                action_type=ActionType.TYPE.value,
                                text=current_text,
                                timestamp=text_start_time if text_start_time is not None else event.timestamp,
                            )
                            actions.append(action)
                            current_text = ""
                            text_start_time = None
                        action = Action(
                            action_type=ActionType.KEY_PRESS.value,
                            keys=[key_name],
                            timestamp=event.timestamp,
                        )
                        actions.append(action)
                    # 다른 특수 키는 무시 (caps_lock, num_lock 등)
                elif len(key) == 1:
                    current_text += key

            i += 1

        # 남은 텍스트 입력 저장
        if current_text:
            action = Action(
                action_type=ActionType.TYPE.value,
                text=current_text,
                timestamp=text_start_time if text_start_time is not None else 0,
            )
            actions.append(action)

        return actions

    def _add_waits(self, actions: List[Action]) -> List[Action]:
        """액션 사이에 대기 시간 추가"""
        if not actions:
            return actions

        result = []
        min_wait = MIN_WAIT_TIME
        max_wait = MAX_WAIT_TIME

        for i, action in enumerate(actions):
            if i > 0:
                prev_action = actions[i - 1]
                time_diff = action.timestamp - prev_action.timestamp

                if time_diff > min_wait:
                    wait_duration = min(time_diff, max_wait)
                    # 이전 액션에 wait_after 설정
                    if result:
                        result[-1].wait_after = wait_duration

            result.append(action)

        return result

    def optimize_actions(self, actions: List[Action]) -> List[Action]:
        """
        액션 시퀀스 최적화

        중복 제거, 불필요한 액션 정리 등을 수행합니다.

        Args:
            actions: 원본 액션 목록

        Returns:
            List[Action]: 최적화된 액션 목록
        """
        if not actions:
            return actions

        optimized = []

        for action in actions:
            # 빈 텍스트 입력 제거
            if action.action_type == ActionType.TYPE.value and not action.text:
                continue

            # 0 스크롤 제거
            if action.action_type == ActionType.SCROLL.value and action.scroll_amount == 0:
                continue

            # 빈 단축키 제거
            if action.action_type == ActionType.HOTKEY.value and not action.keys:
                continue

            optimized.append(action)

        # 연속된 동일 위치 클릭 병합 (더블클릭으로)
        result = []
        i = 0

        while i < len(optimized):
            action = optimized[i]

            if action.action_type == ActionType.CLICK.value and i + 1 < len(optimized):
                next_action = optimized[i + 1]

                if (next_action.action_type == ActionType.CLICK.value and
                    action.x == next_action.x and
                    action.y == next_action.y and
                    action.button == next_action.button):

                    time_diff = next_action.timestamp - action.timestamp
                    if time_diff < DOUBLE_CLICK_POST_TIME:
                        # 더블클릭으로 변환
                        double_click = Action(
                            action_type=ActionType.DOUBLE_CLICK.value,
                            x=action.x,
                            y=action.y,
                            button=action.button,
                            timestamp=action.timestamp,
                            wait_after=next_action.wait_after,
                        )
                        result.append(double_click)
                        i += 2
                        continue

            result.append(action)
            i += 1

        logger.info(f"액션 최적화: {len(actions)} -> {len(result)}")
        return result


# 전역 액션 추출기 인스턴스
action_extractor = ActionExtractor()


def get_action_extractor() -> ActionExtractor:
    """액션 추출기 헬퍼 함수"""
    return action_extractor
