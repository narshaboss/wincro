"""
WinCro UI 상수 모듈

player_view.py 등 여러 UI 모듈에서 공통으로 사용되는 상수와 유틸리티 함수를 정의합니다.
"""

import uuid
import copy
import threading
from typing import Optional, Dict, List, Any

from .theme import COLORS

# 액션 클립보드 (복사/붙여넣기용) - 다이얼로그 간 공유
_action_clipboard = None
_clipboard_lock = threading.Lock()


def get_action_clipboard():
    """클립보드에서 액션 가져오기 (스레드 안전)"""
    with _clipboard_lock:
        return _action_clipboard


def set_action_clipboard(action):
    """클립보드에 액션 저장 (스레드 안전)"""
    global _action_clipboard
    with _clipboard_lock:
        _action_clipboard = action


# 경유지 클립보드 (복사/붙여넣기용) - 특화모드 다이얼로그 간 공유
_waypoint_clipboard = None


def get_waypoint_clipboard():
    """경유지 클립보드에서 가져오기 (스레드 안전, deep copy)"""
    with _clipboard_lock:
        return copy.deepcopy(_waypoint_clipboard) if _waypoint_clipboard is not None else None


def set_waypoint_clipboard(waypoints):
    """경유지 클립보드에 저장 (스레드 안전, deep copy)"""
    global _waypoint_clipboard
    with _clipboard_lock:
        _waypoint_clipboard = copy.deepcopy(waypoints) if waypoints is not None else None


# 액션 타입별 한국어 이름 (전체)
ACTION_NAMES = {
    "click": "왼쪽 클릭",
    "double_click": "더블 클릭",
    "right_click": "오른쪽 클릭",
    "type": "텍스트 입력",
    "hotkey": "단축키",
    "key_press": "키 입력",
    "random_key_sequence": "랜덤키 입력",
    "auto_list": "자동 목록 처리",
    "auto_list_value_input": "현재 처리수량 입력",
    "action_call": "액션 호출",
    "scroll": "스크롤",
    "drag": "드래그",
    "wait": "대기",
    "wait_for_image": "이미지 대기",
    "game_mode": "특화모드",
}

# 액션 타입별 짧은 한국어 이름 (모니터링 모드용)
ACTION_NAMES_SHORT = {
    "click": "클릭",
    "double_click": "더블클릭",
    "right_click": "우클릭",
    "type": "입력",
    "hotkey": "단축키",
    "key_press": "키",
    "random_key_sequence": "랜덤키",
    "auto_list": "자동 목록",
    "auto_list_value_input": "처리수량",
    "action_call": "호출",
    "scroll": "스크롤",
    "drag": "드래그",
    "game_mode": "특화모드",
}

# 액션 타입별 색상
ACTION_COLORS = {
    "click": COLORS["accent_blue"],
    "double_click": COLORS["accent_blue"],
    "right_click": COLORS["accent_blue"],
    "type": COLORS["success"],
    "hotkey": COLORS["accent_orange"],
    "key_press": COLORS["accent_orange"],
    "random_key_sequence": COLORS["accent_orange"],
    "auto_list": COLORS["accent_blue"],
    "auto_list_value_input": COLORS["success"],
    "action_call": COLORS["accent_orange"],
    "scroll": COLORS["scroll_purple"],
    "drag": COLORS["warning"],
    "wait": COLORS["text_muted"],
    "wait_for_image": COLORS["accent"],
    "game_mode": COLORS["error"],
}


def convert_to_monitor_action(action) -> Optional[Dict[str, Any]]:
    """
    액션(AutomationRule 또는 Action)을 monitor_action 딕셔너리 형식으로 변환합니다.

    Args:
        action: AutomationRule 또는 Action 객체

    Returns:
        변환된 monitor_action 딕셔너리, 또는 None
    """
    action_type = getattr(action, 'action_type', None)
    if action_type is None:
        return None

    ma = None

    if action_type == "type":
        text = getattr(action, 'action_text', None) or getattr(action, 'text', '') or ''
        ma = {"type": "텍스트 입력", "text": text}

    elif action_type in ["hotkey", "key_press"]:
        keys = getattr(action, 'action_keys', None) or getattr(action, 'keys', []) or []
        ma = {"type": "키 입력", "keys": keys}

    elif action_type == "random_key_sequence":
        sequences = getattr(action, "random_key_sequences", []) or []
        ma = {
            "type": "랜덤키 입력",
            "random_key_sequences": sequences,
            "random_key_step_delay": getattr(action, "random_key_step_delay", 0.8),
        }

    elif action_type in ["click", "double_click", "right_click"]:
        click_type_map = {
            "click": "click",
            "double_click": "double_click",
            "right_click": "right_click",
        }
        target_img = getattr(action, 'target_image', None) or getattr(action, 'screenshot_path', None)
        if target_img:
            ma = {
                "type": "이미지 클릭",
                "image_path": str(target_img),
                "click_type": click_type_map.get(action_type, "click"),
            }
        else:
            # AutomationRule은 action_x/action_y, Action은 x/y
            x = getattr(action, 'x', None)
            if x is None:
                x = getattr(action, 'action_x', None)
            if x is None:
                x = 0
            y = getattr(action, 'y', None)
            if y is None:
                y = getattr(action, 'action_y', None)
            if y is None:
                y = 0
            ma = {
                "type": "좌표 클릭",
                "x": x,
                "y": y,
                "click_type": click_type_map.get(action_type, "click"),
            }

    elif action_type == "scroll":
        dx = getattr(action, 'scroll_dx', 0) or 0
        dy = getattr(action, 'scroll_dy', 0) or 0
        ma = {"type": "스크롤", "dx": dx, "dy": dy}

    elif action_type == "drag":
        # AutomationRule은 action_x/action_y + drag_to_x/drag_to_y
        x = getattr(action, 'x', None)
        if x is None:
            x = getattr(action, 'action_x', None)
        if x is None:
            x = 0
        y = getattr(action, 'y', None)
        if y is None:
            y = getattr(action, 'action_y', None)
        if y is None:
            y = 0
        end_x = getattr(action, 'end_x', None)
        if end_x is None:
            end_x = getattr(action, 'drag_end_x', None)
        if end_x is None:
            end_x = getattr(action, 'drag_to_x', None)
        if end_x is None:
            end_x = 0
        end_y = getattr(action, 'end_y', None)
        if end_y is None:
            end_y = getattr(action, 'drag_end_y', None)
        if end_y is None:
            end_y = getattr(action, 'drag_to_y', None)
        if end_y is None:
            end_y = 0
        ma = {"type": "드래그", "start_x": x, "start_y": y, "end_x": end_x, "end_y": end_y}

    if ma is None:
        return None

    # 공통 속성 복사
    for attr in ['wait_after', 'wait_random', 'wait_random_range', 'repeat_count',
                 'repeat_delay', 'repeat_delay_random', 'repeat_delay_random_range',
                 'skip', 'skip_on_not_found', 'name', 'search_region', 'confidence', 'alternate_mouse_route',
                 'click_until_image_disappears', 'click_until_image_disappears_delay',
                 'verify_image_color', 'verify_image_brightness']:
        val = getattr(action, attr, None)
        if val is not None:
            ma[attr] = val

    return ma


def collect_all_actions(action) -> list:
    """
    액션과 모든 하위 액션을 재귀적으로 수집합니다.

    Args:
        action: 최상위 액션 객체

    Returns:
        모든 액션의 플랫 리스트
    """
    result = [action]
    children = getattr(action, 'children', None) or []
    for child in children:
        result.extend(collect_all_actions(child))
    return result


def assign_new_ids(action, parent_id: int = None) -> None:
    """
    액션과 모든 하위 액션에 새 고유 ID를 할당합니다.

    Args:
        action: 최상위 액션 객체
        parent_id: 부모 액션의 ID
    """
    new_id = int(uuid.uuid4().int % 1000000000)

    # AutomationRule인 경우
    if hasattr(action, 'rule_id'):
        action.rule_id = new_id
        if hasattr(action, 'parent_rule_id'):
            action.parent_rule_id = parent_id
        children = getattr(action, 'children', None) or []
        for child in children:
            assign_new_ids(child, new_id)

    # Action인 경우
    elif hasattr(action, 'action_id'):
        action.action_id = new_id
        if hasattr(action, 'parent_id'):
            action.parent_id = parent_id
        children = getattr(action, 'children', None) or []
        for child in children:
            assign_new_ids(child, new_id)
