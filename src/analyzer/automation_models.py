"""
WinCro ????????

???????? ?????????? ??????????? ????????
AI ??? ??? ?????? ???????????????? ??? ????????
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from enum import Enum
from datetime import datetime
from pathlib import Path
import uuid
import os

from ..special_mode_profiles import (
    DEFAULT_SPECIAL_MODE_PROFILE,
    infer_legacy_special_mode_profile,
    normalize_special_mode_profile,
)
from ..utils.auto_list import (
    AUTO_LIST_ACTION_TYPE,
    auto_list_config_for_save,
    auto_list_config_from_saved,
    normalize_auto_list_config,
)
from ..utils.action_call import ACTION_CALL_ACTION_TYPE


def normalize_trigger_key_sequences(raw_sequences, legacy_keys=None) -> List[List[str]]:
    """Normalize ordered trigger key combos while preserving legacy flat keys."""
    normalized: List[List[str]] = []
    raw_sequences = raw_sequences or []
    if isinstance(raw_sequences, (list, tuple)) and raw_sequences:
        if all(not isinstance(item, (list, tuple)) for item in raw_sequences):
            raw_sequences = [raw_sequences]
        for raw_combo in raw_sequences:
            if isinstance(raw_combo, str):
                raw_combo = [raw_combo]
            if not isinstance(raw_combo, (list, tuple)):
                continue
            combo = [
                str(key).strip().lower()
                for key in raw_combo
                if str(key).strip()
            ]
            if combo:
                normalized.append(combo)

    if not normalized:
        legacy_combo = [
            str(key).strip().lower()
            for key in (legacy_keys or [])
            if str(key).strip()
        ]
        if legacy_combo:
            normalized.append(legacy_combo)
    return normalized


def normalize_trigger_key_sequence_setting(raw_setting=None, defaults=None) -> Dict[str, Any]:
    """Normalize one trigger key step's repeat timing settings."""
    raw = raw_setting if isinstance(raw_setting, dict) else {}
    fallback = defaults if isinstance(defaults, dict) else {}

    try:
        repeat_count = max(1, min(999, int(raw.get("repeat_count", fallback.get("repeat_count", 1)) or 1)))
    except (TypeError, ValueError):
        repeat_count = 1
    try:
        repeat_delay = max(0.0, float(raw.get("repeat_delay", fallback.get("repeat_delay", 0.5)) or 0.0))
    except (TypeError, ValueError):
        repeat_delay = 0.5
    try:
        random_range = max(
            0.0,
            float(raw.get("repeat_delay_random_range", fallback.get("repeat_delay_random_range", 0.3)) or 0.0),
        )
    except (TypeError, ValueError):
        random_range = 0.3

    return {
        "repeat_count": repeat_count,
        "repeat_delay": repeat_delay,
        "repeat_delay_random": bool(
            raw.get("repeat_delay_random", fallback.get("repeat_delay_random", False))
        ),
        "repeat_delay_random_range": random_range,
    }


def normalize_trigger_key_sequence_settings(raw_settings, sequence_count: int) -> List[Dict[str, Any]]:
    """Return settings only when every ordered key sequence has one valid record."""
    try:
        expected_count = max(0, int(sequence_count))
    except (TypeError, ValueError):
        expected_count = 0
    if expected_count == 0:
        return []
    if not isinstance(raw_settings, (list, tuple)) or len(raw_settings) != expected_count:
        return []
    if any(not isinstance(item, dict) for item in raw_settings):
        return []
    return [normalize_trigger_key_sequence_setting(item) for item in raw_settings]


def _to_relative_path(abs_path: Optional[str]) -> Optional[str]:
    """??? ??????????? ??? (????)"""
    if not abs_path:
        return None
    return Path(abs_path).name


def _to_absolute_path(filename: Optional[str], base_dir: Path) -> Optional[str]:
    """?????? ??? ????????(?????"""
    if not filename:
        return None
    # ??? ??? ????????????? (??? ???)
    if os.path.isabs(filename):
        # ??????????? ????? ?????base_dir??? ???
        if Path(filename).exists():
            return filename
        filename = Path(filename).name
    full_path = base_dir / filename
    return str(full_path)


class RuleType(Enum):
    """??? ???"""
    FIXED_SEQUENCE = "fixed_sequence"  # ??? ????(???/???? ???)
    TYPE_TEXT = "type_text"  # ????????
    HOTKEY = "hotkey"  # ?????
    WAIT_FOR_IMAGE = "wait_for_image"  # ???? ???
    WAIT_FOR_DISAPPEAR = "wait_for_disappear"  # ???? ????????
    CLICK_ON_APPEAR = "click_on_appear"  # ???? ?????? ???
    MONITOR = "monitor"  # ?????? (??? ???)


@dataclass
class AutomationRule:
    """
    ????????

    ???????????????????????
    """
    rule_id: str = ""
    rule_type: str = RuleType.FIXED_SEQUENCE.value
    description: str = ""

    # ??? ???
    action_type: str = "click"  # click, double_click, right_click, type, hotkey, scroll, drag
    action_x: Optional[int] = None  # ??? X ???
    action_y: Optional[int] = None  # ??? Y ???
    action_text: Optional[str] = None  # ??????????
    action_keys: Optional[List[str]] = None  # ?????
    action_key_events: List[Dict[str, Any]] = field(default_factory=list)  # recorded key down/up events
    random_key_sequences: List[List[Dict[str, Any]]] = field(default_factory=list)  # 랜덤 실행 키 묶음
    random_key_step_delay: float = 0.8  # 랜덤키 묶음 안 키 사이 대기시간

    # ??????
    drag_to_x: Optional[int] = None
    drag_to_y: Optional[int] = None
    drag_duration: Optional[float] = None  # ???????? ??? (??

    # ??????
    scroll_amount: int = 0

    # ???? ???
    target_image: Optional[str] = None  # ??? ??????? ??? (???)
    target_images: List[str] = field(default_factory=list)  # ??????? (OR ???)
    trigger_image: Optional[str] = None  # ????????? (????????
    trigger_x: Optional[int] = None  # ??????????? ??? X ???
    trigger_y: Optional[int] = None  # ??????????? ??? Y ???
    trigger_search_region: Optional[List[int]] = None  # trigger-only [x1, y1, x2, y2]
    confidence: float = 0.65  # ??? ?????(?????? ???????? ???)
    verify_image_color: bool = False  # 이미지 매칭 후 색상 차이 추가 확인
    verify_image_brightness: bool = False  # 이미지 매칭 후 밝기 차이 추가 확인
    search_radius: int = 0  # ????????? (0=??????, >0=action_x/y ??? ??? ???)
    search_region: Optional[List[int]] = None  # ?????? ?????? [x1, y1, x2, y2] (search_radius??? ???)
    move_mouse_before_search: bool = False  # ??????????? ??? ???????? (hover ??? ???)
    alternate_mouse_route: bool = False  # 이미지 클릭 시 기본 직선 이동 대신 반대 우회 경로로 접근
    click_until_image_disappears: bool = False  # 이미지가 사라질 때까지 반복 클릭
    click_until_image_disappears_delay: float = 0.5  # 사라질 때까지 반복 클릭 전용 대기시간
    click_until_image_disappears_safety_enabled: bool = True  # 최대 클릭/시간 안전장치
    repeat_from_auto_list_quantity: bool = False  # 자동 목록의 현재 처리수량만큼 이미지 클릭 반복
    auto_list_repeat_confirm_image: Optional[str] = None  # 자동 목록 수량 반복 선택 확인 이미지
    auto_list_repeat_confirm_region: Optional[List[int]] = None  # 선택 확인 이미지 검색 영역
    auto_list_repeat_confirm_confidence: float = 0.9  # 선택 확인 이미지 인식률
    auto_list_config: Dict[str, Any] = field(default_factory=dict)  # 자동 목록 처리 설정
    action_call_rule_id: Optional[str] = None  # 호출할 원본 액션 ID
    action_call_include_children: bool = True  # 호출 대상의 하위 액션 포함

    # ????
    wait_after: float = 0.5  # ??? ???????? (??
    wait_random: bool = False  # ????????? ???
    wait_random_range: float = 0.3  # ?????????? (??
    typing_random: bool = False  # ???????? ???????? ??? ?????
    typing_delay: float = 0.1  # ?????? ??? ?????(??
    typing_delay_range: float = 0.05  # ???? ????????? (??
    timeout: float = 30.0  # ??????(??
    enabled: bool = True  # False? ???? ??
    skip_on_not_found: bool = False  # ???? ?????? wait_after ????? ?????? ???
    stop_playlist_on_trigger_missing: bool = False  # 트리거 미감지 시 현재 재생목록 종료
    trigger_missing_keys: List[str] = field(default_factory=list)  # 트리거 미감지 종료 전 입력할 키/조합
    trigger_missing_key_sequences: List[List[str]] = field(default_factory=list)  # 종료 전 순차 키입력 목록
    trigger_missing_key_repeat_count: int = 1  # 트리거 미감지 종료 전 키입력 반복횟수
    trigger_missing_key_repeat_delay: float = 0.5  # 트리거 미감지 종료 전 키입력 반복 대기시간
    trigger_missing_key_repeat_delay_random: bool = False  # 트리거 미감지 종료 전 키입력 랜덤 대기
    trigger_missing_key_repeat_delay_random_range: float = 0.3  # 트리거 미감지 종료 전 키입력 랜덤 대기 범위
    rewind_previous_on_trigger_missing: bool = False  # 트리거 미감지 시 현재 액션의 전 액션으로 이동
    trigger_missing_rewind_count: int = 1  # 트리거 미감지 시 전 액션으로 돌아가는 최대 횟수
    trigger_missing_rewind_delay: float = 0.5  # 전 액션으로 돌아가기 전 대기시간
    trigger_missing_rewind_delay_random: bool = False  # 전 액션 되돌아가기 랜덤 대기
    trigger_missing_rewind_delay_random_range: float = 0.3  # 전 액션 되돌아가기 랜덤 대기 범위
    trigger_missing_rewind_keys: List[str] = field(default_factory=list)  # 전 액션 복귀 전 입력할 키/조합
    trigger_missing_rewind_key_sequences: List[List[str]] = field(default_factory=list)  # 복귀 전 순차 키입력 목록
    trigger_missing_rewind_key_repeat_count: int = 1  # 전 액션 복귀 전 키입력 반복횟수
    trigger_missing_rewind_key_repeat_delay: float = 0.5  # 전 액션 복귀 전 키입력 반복 대기시간
    trigger_missing_rewind_key_repeat_delay_random: bool = False  # 전 액션 복귀 전 키입력 랜덤 대기
    trigger_missing_rewind_key_repeat_delay_random_range: float = 0.3  # 전 액션 복귀 전 키입력 랜덤 대기 범위
    trigger_missing_rewind_rule_id: Optional[str] = None  # 트리거 미감지 시 돌아갈 액션 ID (없으면 구형 전 액션 동작)
    trigger_missing_key_sequence_settings: List[Dict[str, Any]] = field(default_factory=list)
    trigger_missing_rewind_key_sequence_settings: List[Dict[str, Any]] = field(default_factory=list)
    repeat_count: int = 1  # ??? ??? (1 = 1?????)
    repeat_delay: float = 0.5  # ??? ??? ??????(??
    repeat_delay_random: bool = False  # ??? ????????? ???
    repeat_delay_random_range: float = 0.3  # ??? ?????????? (??

    # Disabled by default so existing plans retain delivery-based behavior.
    transition_recovery_policy: str = ""  # auto | force_on | force_off
    transition_recovery_enabled: bool = False
    transition_verify_mode: str = "next_action"  # next_action | custom_image
    transition_verify_image: Optional[str] = None
    transition_verify_region: Optional[List[int]] = None
    transition_verify_confidence: float = 0.8
    transition_verify_color: bool = False
    transition_verify_brightness: bool = False
    transition_verify_timeout: float = 5.0
    transition_recovery_mode: str = "refocus_retry"  # retry | refocus_retry | actions_retry
    transition_recovery_count: int = 3
    transition_recovery_delay: float = 1.0
    transition_recovery_delay_random: bool = False
    transition_recovery_delay_random_range: float = 0.3
    transition_recovery_rule_ids: List[str] = field(default_factory=list)
    transition_failure_mode: str = "alert_wait"  # alert_wait | goto_rule | fail
    transition_failure_rule_id: Optional[str] = None
    transition_stop_repeats_on_success: bool = True

    # ???
    timestamp: float = 0.0  # ??? ??? ???????

    # ??? ??? (??????)
    parent_id: Optional[str] = None  # ?????? ID
    children: List["AutomationRule"] = field(default_factory=list)  # ??? ?????

    # ?????? ???
    is_monitoring_mode: bool = False  # ?????? ??? ???
    monitoring_final_image: Optional[str] = None  # ??? ???? (??? ??????????? ???)
    monitoring_watches: List[Dict[str, Any]] = field(default_factory=list)  # ??? ??? [{image: str, goto_index: int}]

    def __post_init__(self):
        """??????????"""
        if not self.rule_id:
            self.rule_id = f"rule_{uuid.uuid4().hex[:8]}"
        if self.action_keys is None:
            self.action_keys = []
        if self.action_key_events is None:
            self.action_key_events = []
        if self.random_key_sequences is None:
            self.random_key_sequences = []
        try:
            self.random_key_step_delay = max(0.0, float(self.random_key_step_delay or 0.0))
        except (TypeError, ValueError):
            self.random_key_step_delay = 0.8
        if self.target_images is None:
            self.target_images = []
        self.auto_list_config = (
            normalize_auto_list_config(self.auto_list_config)
            if self.action_type == AUTO_LIST_ACTION_TYPE
            else {}
        )
        if self.action_type == ACTION_CALL_ACTION_TYPE:
            self.action_call_rule_id = str(self.action_call_rule_id or "").strip() or None
            self.action_call_include_children = bool(self.action_call_include_children)
        else:
            self.action_call_rule_id = None
            self.action_call_include_children = True
        if self.trigger_missing_keys is None:
            self.trigger_missing_keys = []
        if self.trigger_missing_rewind_keys is None:
            self.trigger_missing_rewind_keys = []
        self.trigger_missing_key_sequences = normalize_trigger_key_sequences(
            self.trigger_missing_key_sequences,
            self.trigger_missing_keys,
        )
        self.trigger_missing_rewind_key_sequences = normalize_trigger_key_sequences(
            self.trigger_missing_rewind_key_sequences,
            self.trigger_missing_rewind_keys,
        )
        self.trigger_missing_key_sequence_settings = normalize_trigger_key_sequence_settings(
            self.trigger_missing_key_sequence_settings,
            len(self.trigger_missing_key_sequences),
        )
        self.trigger_missing_rewind_key_sequence_settings = normalize_trigger_key_sequence_settings(
            self.trigger_missing_rewind_key_sequence_settings,
            len(self.trigger_missing_rewind_key_sequences),
        )
        # Keep the first combo for older WinCro versions that only understand flat keys.
        self.trigger_missing_keys = (
            list(self.trigger_missing_key_sequences[0])
            if self.trigger_missing_key_sequences else []
        )
        self.trigger_missing_rewind_keys = (
            list(self.trigger_missing_rewind_key_sequences[0])
            if self.trigger_missing_rewind_key_sequences else []
        )
        self.trigger_missing_rewind_rule_id = (
            str(self.trigger_missing_rewind_rule_id or "").strip() or None
        )
        if isinstance(self.trigger_search_region, (list, tuple)) and len(self.trigger_search_region) == 4:
            try:
                x1, y1, x2, y2 = [int(round(float(value))) for value in self.trigger_search_region]
                left, right = sorted((x1, x2))
                top, bottom = sorted((y1, y2))
                self.trigger_search_region = (
                    [left, top, right, bottom]
                    if right > left and bottom > top
                    else None
                )
            except (TypeError, ValueError):
                self.trigger_search_region = None
        else:
            self.trigger_search_region = None
        try:
            self.trigger_missing_key_repeat_count = max(1, int(self.trigger_missing_key_repeat_count or 1))
        except (TypeError, ValueError):
            self.trigger_missing_key_repeat_count = 1
        try:
            self.trigger_missing_key_repeat_delay = max(0.0, float(self.trigger_missing_key_repeat_delay or 0.0))
        except (TypeError, ValueError):
            self.trigger_missing_key_repeat_delay = 0.5
        try:
            self.trigger_missing_key_repeat_delay_random_range = max(
                0.0,
                float(self.trigger_missing_key_repeat_delay_random_range or 0.0),
            )
        except (TypeError, ValueError):
            self.trigger_missing_key_repeat_delay_random_range = 0.3
        try:
            self.trigger_missing_rewind_count = max(1, int(self.trigger_missing_rewind_count or 1))
        except (TypeError, ValueError):
            self.trigger_missing_rewind_count = 1
        try:
            self.trigger_missing_rewind_delay = max(0.0, float(self.trigger_missing_rewind_delay or 0.0))
        except (TypeError, ValueError):
            self.trigger_missing_rewind_delay = 0.5
        try:
            self.trigger_missing_rewind_delay_random_range = max(
                0.0,
                float(self.trigger_missing_rewind_delay_random_range or 0.0),
            )
        except (TypeError, ValueError):
            self.trigger_missing_rewind_delay_random_range = 0.3
        try:
            self.trigger_missing_rewind_key_repeat_count = max(
                1,
                int(self.trigger_missing_rewind_key_repeat_count or 1),
            )
        except (TypeError, ValueError):
            self.trigger_missing_rewind_key_repeat_count = 1
        try:
            self.trigger_missing_rewind_key_repeat_delay = max(
                0.0,
                float(self.trigger_missing_rewind_key_repeat_delay or 0.0),
            )
        except (TypeError, ValueError):
            self.trigger_missing_rewind_key_repeat_delay = 0.5
        try:
            self.trigger_missing_rewind_key_repeat_delay_random_range = max(
                0.0,
                float(self.trigger_missing_rewind_key_repeat_delay_random_range or 0.0),
            )
        except (TypeError, ValueError):
            self.trigger_missing_rewind_key_repeat_delay_random_range = 0.3
        try:
            self.click_until_image_disappears_delay = max(
                0.0,
                float(self.click_until_image_disappears_delay or 0.0),
            )
        except (TypeError, ValueError):
            self.click_until_image_disappears_delay = 0.5
        self.click_until_image_disappears_safety_enabled = bool(
            self.click_until_image_disappears_safety_enabled
        )
        self.repeat_from_auto_list_quantity = bool(self.repeat_from_auto_list_quantity)
        try:
            self.auto_list_repeat_confirm_confidence = min(
                1.0,
                max(0.1, float(self.auto_list_repeat_confirm_confidence or 0.9)),
            )
        except (TypeError, ValueError):
            self.auto_list_repeat_confirm_confidence = 0.9
        from ..utils.transition_recovery_policy import (
            TRANSITION_POLICY_FORCE_ON,
            normalize_transition_recovery_policy,
        )

        self.transition_recovery_policy = normalize_transition_recovery_policy(
            self.transition_recovery_policy,
            self.transition_recovery_enabled,
        )
        # Keep the legacy flag synchronized for older WinCro versions.
        self.transition_recovery_enabled = (
            self.transition_recovery_policy == TRANSITION_POLICY_FORCE_ON
        )
        verify_mode = str(self.transition_verify_mode or "next_action").strip()
        self.transition_verify_mode = (
            verify_mode if verify_mode in {"next_action", "custom_image"} else "next_action"
        )
        recovery_mode = str(self.transition_recovery_mode or "refocus_retry").strip()
        self.transition_recovery_mode = (
            recovery_mode
            if recovery_mode in {"retry", "refocus_retry", "actions_retry"}
            else "refocus_retry"
        )
        failure_mode = str(self.transition_failure_mode or "alert_wait").strip()
        self.transition_failure_mode = (
            failure_mode if failure_mode in {"alert_wait", "goto_rule", "fail"} else "alert_wait"
        )
        self.transition_recovery_rule_ids = list(dict.fromkeys(
            str(rule_id or "").strip()
            for rule_id in (self.transition_recovery_rule_ids or [])
            if str(rule_id or "").strip()
        ))
        self.transition_failure_rule_id = (
            str(self.transition_failure_rule_id or "").strip() or None
        )
        self.transition_stop_repeats_on_success = bool(
            self.transition_stop_repeats_on_success
        )
        try:
            self.transition_verify_timeout = max(
                0.5,
                float(self.transition_verify_timeout or 5.0),
            )
        except (TypeError, ValueError):
            self.transition_verify_timeout = 5.0
        try:
            self.transition_verify_confidence = min(
                1.0,
                max(0.1, float(self.transition_verify_confidence or 0.8)),
            )
        except (TypeError, ValueError):
            self.transition_verify_confidence = 0.8
        try:
            self.transition_recovery_count = max(
                1,
                min(20, int(self.transition_recovery_count or 3)),
            )
        except (TypeError, ValueError):
            self.transition_recovery_count = 3
        try:
            self.transition_recovery_delay = max(
                0.0,
                float(self.transition_recovery_delay or 0.0),
            )
        except (TypeError, ValueError):
            self.transition_recovery_delay = 1.0
        try:
            self.transition_recovery_delay_random_range = max(
                0.0,
                float(self.transition_recovery_delay_random_range or 0.0),
            )
        except (TypeError, ValueError):
            self.transition_recovery_delay_random_range = 0.3
        self.transition_recovery_delay_random = bool(
            self.transition_recovery_delay_random
        )
        self.transition_verify_color = bool(self.transition_verify_color)
        self.transition_verify_brightness = bool(self.transition_verify_brightness)
        if isinstance(self.transition_verify_region, (list, tuple)) and len(self.transition_verify_region) == 4:
            try:
                x1, y1, x2, y2 = [
                    int(round(float(value))) for value in self.transition_verify_region
                ]
                left, right = sorted((x1, x2))
                top, bottom = sorted((y1, y2))
                self.transition_verify_region = (
                    [left, top, right, bottom]
                    if right > left and bottom > top
                    else None
                )
            except (TypeError, ValueError):
                self.transition_verify_region = None
        else:
            self.transition_verify_region = None
        if self.children is None:
            self.children = []
        if self.monitoring_watches is None:
            self.monitoring_watches = []

    def to_dict(self) -> Dict[str, Any]:
        """???????????(???? ??????????? ???"""
        # monitoring_watches ??? image ??????????? ???
        watches_for_save = []
        for watch in self.monitoring_watches:
            watch_copy = watch.copy()
            if "image" in watch_copy and watch_copy["image"]:
                watch_copy["image"] = _to_relative_path(watch_copy["image"])
            if isinstance(watch_copy.get("images"), list):
                images_copy = []
                for image_item in watch_copy["images"]:
                    if not isinstance(image_item, dict):
                        continue
                    image_copy = image_item.copy()
                    if image_copy.get("image"):
                        image_copy["image"] = _to_relative_path(image_copy["image"])
                    elif image_copy.get("image_path"):
                        image_copy["image_path"] = _to_relative_path(image_copy["image_path"])
                    images_copy.append(image_copy)
                watch_copy["images"] = images_copy
            # condition_image ????????
            if "condition_image" in watch_copy and watch_copy["condition_image"]:
                watch_copy["condition_image"] = _to_relative_path(watch_copy["condition_image"])
            # monitor_actions ??? ???? ????????
            if "monitor_actions" in watch_copy:
                actions_copy = []
                for action in watch_copy["monitor_actions"]:
                    action_copy = action.copy()
                    if "image" in action_copy and action_copy["image"]:
                        action_copy["image"] = _to_relative_path(action_copy["image"])
                    actions_copy.append(action_copy)
                watch_copy["monitor_actions"] = actions_copy
            watches_for_save.append(watch_copy)

        result = {
            "rule_id": self.rule_id,
            "rule_type": self.rule_type,
            "description": self.description,
            "action_type": self.action_type,
            "action_x": self.action_x,
            "action_y": self.action_y,
            "action_text": self.action_text,
            "action_keys": self.action_keys,
            "action_key_events": self.action_key_events,
            "random_key_sequences": self.random_key_sequences,
            "random_key_step_delay": self.random_key_step_delay,
            "drag_to_x": self.drag_to_x,
            "drag_to_y": self.drag_to_y,
            "drag_duration": self.drag_duration,
            "scroll_amount": self.scroll_amount,
            "target_image": _to_relative_path(self.target_image),
            "target_images": [_to_relative_path(p) for p in self.target_images if p],
            "trigger_image": _to_relative_path(self.trigger_image),
            "trigger_x": self.trigger_x,
            "trigger_y": self.trigger_y,
            "trigger_search_region": self.trigger_search_region,
            "confidence": self.confidence,
            "verify_image_color": self.verify_image_color,
            "verify_image_brightness": self.verify_image_brightness,
            "search_radius": self.search_radius,
            "search_region": self.search_region,
            "move_mouse_before_search": self.move_mouse_before_search,
            "alternate_mouse_route": self.alternate_mouse_route,
            "click_until_image_disappears": self.click_until_image_disappears,
            "click_until_image_disappears_delay": self.click_until_image_disappears_delay,
            "click_until_image_disappears_safety_enabled": (
                self.click_until_image_disappears_safety_enabled
            ),
            "repeat_from_auto_list_quantity": self.repeat_from_auto_list_quantity,
            "auto_list_repeat_confirm_image": _to_relative_path(self.auto_list_repeat_confirm_image),
            "auto_list_repeat_confirm_region": self.auto_list_repeat_confirm_region,
            "auto_list_repeat_confirm_confidence": self.auto_list_repeat_confirm_confidence,
            "action_call_rule_id": self.action_call_rule_id,
            "action_call_include_children": self.action_call_include_children,
            "wait_after": self.wait_after,
            "wait_random": self.wait_random,
            "enabled": self.enabled,
            "wait_random_range": self.wait_random_range,
            "typing_random": self.typing_random,
            "typing_delay": self.typing_delay,
            "typing_delay_range": self.typing_delay_range,
            "timeout": self.timeout,
            "skip_on_not_found": self.skip_on_not_found,
            "stop_playlist_on_trigger_missing": self.stop_playlist_on_trigger_missing,
            "trigger_missing_keys": self.trigger_missing_keys,
            "trigger_missing_key_sequences": self.trigger_missing_key_sequences,
            "trigger_missing_key_sequence_settings": self.trigger_missing_key_sequence_settings,
            "trigger_missing_key_repeat_count": self.trigger_missing_key_repeat_count,
            "trigger_missing_key_repeat_delay": self.trigger_missing_key_repeat_delay,
            "trigger_missing_key_repeat_delay_random": self.trigger_missing_key_repeat_delay_random,
            "trigger_missing_key_repeat_delay_random_range": self.trigger_missing_key_repeat_delay_random_range,
            "rewind_previous_on_trigger_missing": self.rewind_previous_on_trigger_missing,
            "trigger_missing_rewind_count": self.trigger_missing_rewind_count,
            "trigger_missing_rewind_delay": self.trigger_missing_rewind_delay,
            "trigger_missing_rewind_delay_random": self.trigger_missing_rewind_delay_random,
            "trigger_missing_rewind_delay_random_range": self.trigger_missing_rewind_delay_random_range,
            "trigger_missing_rewind_keys": self.trigger_missing_rewind_keys,
            "trigger_missing_rewind_key_sequences": self.trigger_missing_rewind_key_sequences,
            "trigger_missing_rewind_key_sequence_settings": self.trigger_missing_rewind_key_sequence_settings,
            "trigger_missing_rewind_key_repeat_count": self.trigger_missing_rewind_key_repeat_count,
            "trigger_missing_rewind_key_repeat_delay": self.trigger_missing_rewind_key_repeat_delay,
            "trigger_missing_rewind_key_repeat_delay_random": self.trigger_missing_rewind_key_repeat_delay_random,
            "trigger_missing_rewind_key_repeat_delay_random_range": self.trigger_missing_rewind_key_repeat_delay_random_range,
            "trigger_missing_rewind_rule_id": self.trigger_missing_rewind_rule_id,
            "repeat_count": self.repeat_count,
            "repeat_delay": self.repeat_delay,
            "repeat_delay_random": self.repeat_delay_random,
            "repeat_delay_random_range": self.repeat_delay_random_range,
            "transition_recovery_policy": self.transition_recovery_policy,
            "transition_recovery_enabled": self.transition_recovery_enabled,
            "transition_verify_mode": self.transition_verify_mode,
            "transition_verify_image": _to_relative_path(self.transition_verify_image),
            "transition_verify_region": self.transition_verify_region,
            "transition_verify_confidence": self.transition_verify_confidence,
            "transition_verify_color": self.transition_verify_color,
            "transition_verify_brightness": self.transition_verify_brightness,
            "transition_verify_timeout": self.transition_verify_timeout,
            "transition_recovery_mode": self.transition_recovery_mode,
            "transition_recovery_count": self.transition_recovery_count,
            "transition_recovery_delay": self.transition_recovery_delay,
            "transition_recovery_delay_random": self.transition_recovery_delay_random,
            "transition_recovery_delay_random_range": self.transition_recovery_delay_random_range,
            "transition_recovery_rule_ids": self.transition_recovery_rule_ids,
            "transition_failure_mode": self.transition_failure_mode,
            "transition_failure_rule_id": self.transition_failure_rule_id,
            "transition_stop_repeats_on_success": self.transition_stop_repeats_on_success,
            "timestamp": self.timestamp,
            "parent_id": self.parent_id,
            "children": [child.to_dict() for child in self.children],
            "is_monitoring_mode": self.is_monitoring_mode,
            "monitoring_final_image": _to_relative_path(self.monitoring_final_image),
            "monitoring_watches": watches_for_save,
        }
        if self.action_type == AUTO_LIST_ACTION_TYPE:
            result["auto_list_config"] = auto_list_config_for_save(self.auto_list_config)
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any], templates_dir: Optional[Path] = None) -> "AutomationRule":
        """????????? ??? (???? ???????? ????????)"""
        # templates_dir???????config??? ??????
        if templates_dir is None:
            try:
                from ..utils.config import DATA_DIR
                templates_dir = DATA_DIR / "templates"
            except ImportError:
                templates_dir = Path("data/templates")

        children_data = data.get("children", [])
        children = [cls.from_dict(c, templates_dir) for c in children_data]

        # monitoring_watches ??? image ???????? ????????
        watches = data.get("monitoring_watches", [])
        watches_restored = []
        for watch in watches:
            watch_copy = watch.copy()
            if "image" in watch_copy and watch_copy["image"]:
                watch_copy["image"] = _to_absolute_path(watch_copy["image"], templates_dir)
            if isinstance(watch_copy.get("images"), list):
                images_copy = []
                for image_item in watch_copy["images"]:
                    if not isinstance(image_item, dict):
                        continue
                    image_copy = image_item.copy()
                    if image_copy.get("image"):
                        image_copy["image"] = _to_absolute_path(image_copy["image"], templates_dir)
                    elif image_copy.get("image_path"):
                        image_copy["image_path"] = _to_absolute_path(image_copy["image_path"], templates_dir)
                    images_copy.append(image_copy)
                watch_copy["images"] = images_copy
            # condition_image ???????? ????????
            if "condition_image" in watch_copy and watch_copy["condition_image"]:
                watch_copy["condition_image"] = _to_absolute_path(watch_copy["condition_image"], templates_dir)
            # monitor_actions ??? ???? ???????? ????????
            if "monitor_actions" in watch_copy:
                actions_copy = []
                for action in watch_copy["monitor_actions"]:
                    action_copy = action.copy()
                    if "image" in action_copy and action_copy["image"]:
                        action_copy["image"] = _to_absolute_path(action_copy["image"], templates_dir)
                    actions_copy.append(action_copy)
                watch_copy["monitor_actions"] = actions_copy
            watches_restored.append(watch_copy)

        # target_images ???
        target_images_raw = data.get("target_images", [])
        target_images = [_to_absolute_path(p, templates_dir) for p in target_images_raw if p]

        return cls(
            rule_id=data.get("rule_id", ""),
            rule_type=data.get("rule_type", RuleType.FIXED_SEQUENCE.value),
            description=data.get("description", ""),
            action_type=data.get("action_type", "click"),
            action_x=data.get("action_x"),
            action_y=data.get("action_y"),
            action_text=data.get("action_text"),
            action_keys=data.get("action_keys", []),
            action_key_events=data.get("action_key_events", []),
            random_key_sequences=data.get("random_key_sequences", []),
            random_key_step_delay=data.get("random_key_step_delay", 0.8),
            drag_to_x=data.get("drag_to_x"),
            drag_to_y=data.get("drag_to_y"),
            drag_duration=data.get("drag_duration"),
            scroll_amount=data.get("scroll_amount", 0),
            target_image=_to_absolute_path(data.get("target_image"), templates_dir),
            target_images=target_images,
            trigger_image=_to_absolute_path(data.get("trigger_image"), templates_dir),
            trigger_x=data.get("trigger_x"),
            trigger_y=data.get("trigger_y"),
            trigger_search_region=data.get("trigger_search_region"),
            confidence=data.get("confidence", 0.65),
            verify_image_color=data.get("verify_image_color", False),
            verify_image_brightness=data.get("verify_image_brightness", False),
            search_radius=data.get("search_radius", 0),
            search_region=data.get("search_region"),
            move_mouse_before_search=data.get("move_mouse_before_search", False),
            alternate_mouse_route=data.get("alternate_mouse_route", False),
            click_until_image_disappears=data.get("click_until_image_disappears", False),
            click_until_image_disappears_delay=data.get(
                "click_until_image_disappears_delay",
                data.get("repeat_delay", 0.5),
            ),
            click_until_image_disappears_safety_enabled=data.get(
                "click_until_image_disappears_safety_enabled",
                True,
            ),
            repeat_from_auto_list_quantity=data.get("repeat_from_auto_list_quantity", False),
            auto_list_repeat_confirm_image=_to_absolute_path(
                data.get("auto_list_repeat_confirm_image"),
                templates_dir,
            ),
            auto_list_repeat_confirm_region=data.get("auto_list_repeat_confirm_region"),
            auto_list_repeat_confirm_confidence=data.get("auto_list_repeat_confirm_confidence", 0.9),
            auto_list_config=auto_list_config_from_saved(data.get("auto_list_config", {}), templates_dir),
            action_call_rule_id=data.get("action_call_rule_id"),
            action_call_include_children=data.get("action_call_include_children", True),
            wait_after=data.get("wait_after", 0.5),
            wait_random=data.get("wait_random", False),
            wait_random_range=data.get("wait_random_range", 0.3),
            enabled=data.get("enabled", True),
            typing_random=data.get("typing_random", False),
            typing_delay=data.get("typing_delay", 0.1),
            typing_delay_range=data.get("typing_delay_range", 0.05),
            timeout=data.get("timeout", 30.0),
            skip_on_not_found=data.get("skip_on_not_found", False),
            stop_playlist_on_trigger_missing=data.get("stop_playlist_on_trigger_missing", False),
            trigger_missing_keys=data.get("trigger_missing_keys", []),
            trigger_missing_key_sequences=data.get("trigger_missing_key_sequences", []),
            trigger_missing_key_sequence_settings=data.get("trigger_missing_key_sequence_settings", []),
            trigger_missing_key_repeat_count=data.get("trigger_missing_key_repeat_count", 1),
            trigger_missing_key_repeat_delay=data.get("trigger_missing_key_repeat_delay", 0.5),
            trigger_missing_key_repeat_delay_random=data.get("trigger_missing_key_repeat_delay_random", False),
            trigger_missing_key_repeat_delay_random_range=data.get("trigger_missing_key_repeat_delay_random_range", 0.3),
            rewind_previous_on_trigger_missing=data.get("rewind_previous_on_trigger_missing", False),
            trigger_missing_rewind_count=data.get("trigger_missing_rewind_count", 1),
            trigger_missing_rewind_delay=data.get("trigger_missing_rewind_delay", 0.5),
            trigger_missing_rewind_delay_random=data.get("trigger_missing_rewind_delay_random", False),
            trigger_missing_rewind_delay_random_range=data.get("trigger_missing_rewind_delay_random_range", 0.3),
            trigger_missing_rewind_keys=data.get("trigger_missing_rewind_keys", []),
            trigger_missing_rewind_key_sequences=data.get("trigger_missing_rewind_key_sequences", []),
            trigger_missing_rewind_key_sequence_settings=data.get("trigger_missing_rewind_key_sequence_settings", []),
            trigger_missing_rewind_key_repeat_count=data.get("trigger_missing_rewind_key_repeat_count", 1),
            trigger_missing_rewind_key_repeat_delay=data.get("trigger_missing_rewind_key_repeat_delay", 0.5),
            trigger_missing_rewind_key_repeat_delay_random=data.get("trigger_missing_rewind_key_repeat_delay_random", False),
            trigger_missing_rewind_key_repeat_delay_random_range=data.get("trigger_missing_rewind_key_repeat_delay_random_range", 0.3),
            trigger_missing_rewind_rule_id=data.get("trigger_missing_rewind_rule_id"),
            repeat_count=data.get("repeat_count", 1),
            repeat_delay=data.get("repeat_delay", 0.5),
            repeat_delay_random=data.get("repeat_delay_random", False),
            repeat_delay_random_range=data.get("repeat_delay_random_range", 0.3),
            transition_recovery_policy=data.get("transition_recovery_policy", ""),
            transition_recovery_enabled=data.get("transition_recovery_enabled", False),
            transition_verify_mode=data.get("transition_verify_mode", "next_action"),
            transition_verify_image=_to_absolute_path(
                data.get("transition_verify_image"),
                templates_dir,
            ),
            transition_verify_region=data.get("transition_verify_region"),
            transition_verify_confidence=data.get("transition_verify_confidence", 0.8),
            transition_verify_color=data.get("transition_verify_color", False),
            transition_verify_brightness=data.get("transition_verify_brightness", False),
            transition_verify_timeout=data.get("transition_verify_timeout", 5.0),
            transition_recovery_mode=data.get("transition_recovery_mode", "refocus_retry"),
            transition_recovery_count=data.get("transition_recovery_count", 3),
            transition_recovery_delay=data.get("transition_recovery_delay", 1.0),
            transition_recovery_delay_random=data.get("transition_recovery_delay_random", False),
            transition_recovery_delay_random_range=data.get("transition_recovery_delay_random_range", 0.3),
            transition_recovery_rule_ids=data.get("transition_recovery_rule_ids", []),
            transition_failure_mode=data.get("transition_failure_mode", "alert_wait"),
            transition_failure_rule_id=data.get("transition_failure_rule_id"),
            transition_stop_repeats_on_success=data.get("transition_stop_repeats_on_success", True),
            timestamp=data.get("timestamp", 0.0),
            parent_id=data.get("parent_id"),
            children=children,
            is_monitoring_mode=data.get("is_monitoring_mode", False),
            monitoring_final_image=_to_absolute_path(data.get("monitoring_final_image"), templates_dir),
            monitoring_watches=watches_restored,
        )


@dataclass
class MinimapConfig:
    """???????? ??? ???"""
    enabled: bool = False

    # 1. ????????? (??? ??????)
    minimap_image_path: Optional[str] = None

    # 2. ???? ???? (?????????????????)
    destination_image_path: Optional[str] = None

    # 3. ??? ?????? ???????? [x1, y1, x2, y2]
    minimap_region: Optional[List[int]] = None

    # 4. ???????? (HSV) - ?????(????
    obstacle_hsv_lower: List[int] = field(default_factory=lambda: [100, 50, 50])
    obstacle_hsv_upper: List[int] = field(default_factory=lambda: [130, 255, 255])

    # 5. ???????? (HSV) - ??? ???
    yellow_hsv_lower: List[int] = field(default_factory=lambda: [20, 150, 150])
    yellow_hsv_upper: List[int] = field(default_factory=lambda: [35, 255, 255])

    # ????????? ??? (???????? ???)
    cached_destination_pixel: Optional[List[int]] = None

    # ???????? (A* ???)
    cached_path: Optional[List[List[int]]] = None

    # ??? ??? ??? (???)
    arrival_threshold: int = 10

    # ??? ??? (??
    analysis_interval: float = 0.1

    def to_dict(self) -> Dict[str, Any]:
        """Serialize minimap config."""
        return {
            "enabled": self.enabled,
            "minimap_image_path": _to_relative_path(self.minimap_image_path),
            "destination_image_path": _to_relative_path(self.destination_image_path),
            "minimap_region": self.minimap_region,
            "obstacle_hsv_lower": self.obstacle_hsv_lower,
            "obstacle_hsv_upper": self.obstacle_hsv_upper,
            "yellow_hsv_lower": self.yellow_hsv_lower,
            "yellow_hsv_upper": self.yellow_hsv_upper,
            "cached_destination_pixel": self.cached_destination_pixel,
            "cached_path": self.cached_path,
            "arrival_threshold": self.arrival_threshold,
            "analysis_interval": self.analysis_interval,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any], templates_dir: Optional[Path] = None) -> "MinimapConfig":
        """????????? ???"""
        if templates_dir is None:
            try:
                from ..utils.config import DATA_DIR
                templates_dir = DATA_DIR / "templates"
            except ImportError:
                templates_dir = Path("data/templates")

        return cls(
            enabled=data.get("enabled", False),
            minimap_image_path=_to_absolute_path(data.get("minimap_image_path"), templates_dir),
            destination_image_path=_to_absolute_path(data.get("destination_image_path"), templates_dir),
            minimap_region=data.get("minimap_region"),
            obstacle_hsv_lower=data.get("obstacle_hsv_lower", [100, 50, 50]),
            obstacle_hsv_upper=data.get("obstacle_hsv_upper", [130, 255, 255]),
            yellow_hsv_lower=data.get("yellow_hsv_lower", [20, 150, 150]),
            yellow_hsv_upper=data.get("yellow_hsv_upper", [35, 255, 255]),
            cached_destination_pixel=data.get("cached_destination_pixel"),
            cached_path=data.get("cached_path"),
            arrival_threshold=data.get("arrival_threshold", 10),
            analysis_interval=data.get("analysis_interval", 0.1),
        )


@dataclass
class GameModeConfig:
    """
    ??? ??? ??? ???

    ???? ??? ?????? ?????? ?????? ??? ?????????.
    """
    enabled: bool = False
    engine_profile: str = DEFAULT_SPECIAL_MODE_PROFILE
    name: str = ""                 # ???????????
    character_image: str = ""      # ????????? (??? ?????
    target_image: str = ""         # ??? ????
    obstacle_images: List[str] = field(default_factory=list)  # ???????????(Phase 2)
    move_keys: Dict[str, str] = field(default_factory=lambda: {
        "up": "up", "down": "down", "left": "left", "right": "right"
    })
    analysis_interval: float = 0.1  # ??? ??? (??
    confidence: float = 0.65        # ??? ?????(????? ??????)
    character_confidence: float = 0.65  # ??????????
    target_confidence: float = 0.65     # ??? ?????
    arrival_threshold: int = 30     # ??? ??? ??? (???)
    search_region: Optional[List[int]] = None  # ?????? [x1, y1, x2, y2], None=??????
    smooth_move: bool = False  # True=??????????), False=?????(????????)
    move_skill_key: str = ""  # ??? ??? ??(?? "4")
    move_skill_distance: int = 150  # ??? ??? ??? ??? (???, ????? ?????? ??? ???)
    auto_skill_key: str = ""  # ??? ??? ??? ??
    auto_skill_cooldown_image: str = ""  # ??? ????????? (??????? ???????? ???)
    auto_skill_cd_region: Optional[List[int]] = None  # ????????? ?????? [x1, y1, x2, y2]

    # === ??? ??? ??? ??? ===
    navigation_mode: str = "coordinate"  # "coordinate" (??? ???)
    coord_x_region: Optional[List[int]] = None  # X ??? OCR ??? [x1, y1, x2, y2]
    coord_y_region: Optional[List[int]] = None  # Y ??? OCR ??? [x1, y1, x2, y2]
    coord_anchor_enabled: bool = False
    coord_x_anchor_image: str = ""
    coord_y_anchor_image: str = ""
    coord_anchor_search_region: Optional[List[int]] = None
    coord_x_anchor_offset: Optional[List[int]] = None
    coord_y_anchor_offset: Optional[List[int]] = None
    target_x: int = 0  # ??? X ???
    target_y: int = 0  # ??? Y ???
    waypoints: List = field(default_factory=list)  # [[x,y,name] ??? [x,y,name,{image_config}]]
    final_waypoint_idx: int = -1  # ??? ??? ???? ?????(-1: ?????????)
    obstacle_detection_enabled: bool = True  # ???????? ?????
    stuck_threshold: int = 3  # ??? ??? ???????(????? ??? ??? ?????????????? ???)
    detour_distance: int = 2  # ??? ??? ??? (?????

    # === 8??? ???? ??? ??? ===
    diagonal_movement_enabled: bool = False  # ???? ??? ??? (????????????????
    oscillation_threshold: int = 3  # ??? ??? ??? ???
    detour_distance_max: int = 8  # ??? ??? ?????

    # === ??? ??? ??? ===
    escape_skill_enabled: bool = False       # ??? ??? ?????
    escape_skill_key: str = "z"              # ??? ??
    escape_skill_cooldown: float = 10.0      # ?????(??
    escape_skill_stuck_threshold: int = 10   # ??? ??? ??? (??? ???)
    escape_skill_direction_count: int = 5    # ???????? ??? (??? ????? ???)
    escape_skill_wait_after: float = 0.5     # ??? ?????(?????? ????????

    # === ???????? ??? ??? ===
    minimap_config: Optional[MinimapConfig] = None

    # === ??? ???????? ===
    mapping_enabled: bool = True  # ??? ???????????? ???

    # === ??? ??? ??? ===
    boss_skill_enabled: bool = False        # ??? ??? ?????
    boss_skill_key: str = ""                # ??? ??
    boss_skill_cooldown: float = 3.0        # ?????(??

    # === ??? ??????????===
    move_skill_enabled: bool = False   # ??? ??? ??? ???
    auto_skill_enabled: bool = False   # ??? ??? ??? ???

    # === ??? ?????===
    move_skill_cooldown: float = 5.0   # ??? ??? ?????(??
    auto_skill_cooldown: float = 5.0   # ??? ??? ?????(??

    def _serialize_waypoints(self):
        """waypoints ?????(???? ??? ????????)"""
        result = []
        for wp in self.waypoints:
            wp_copy = list(wp)
            if len(wp_copy) >= 4 and isinstance(wp_copy[3], dict):
                cfg = dict(wp_copy[3])
                if cfg.get("target_image"):
                    cfg["target_image"] = _to_relative_path(cfg["target_image"])
                if cfg.get("target_images"):
                    cfg["target_images"] = [_to_relative_path(p) for p in cfg["target_images"] if p]
                if cfg.get("character_image"):
                    cfg["character_image"] = _to_relative_path(cfg["character_image"])
                wp_copy[3] = cfg
            result.append(wp_copy)
        return result

    @staticmethod
    def _deserialize_waypoints(raw, templates_dir):
        """waypoints ?????? (?????????????)"""
        result = []
        for wp in raw:
            wp_copy = list(wp)
            if len(wp_copy) >= 4 and isinstance(wp_copy[3], dict):
                cfg = dict(wp_copy[3])
                if cfg.get("target_image"):
                    cfg["target_image"] = _to_absolute_path(cfg["target_image"], templates_dir)
                if cfg.get("target_images"):
                    cfg["target_images"] = [_to_absolute_path(p, templates_dir) for p in cfg["target_images"] if p]
                if cfg.get("character_image"):
                    cfg["character_image"] = _to_absolute_path(cfg["character_image"], templates_dir)
                wp_copy[3] = cfg
            result.append(wp_copy)
        return result

    def to_dict(self) -> Dict[str, Any]:
        """Serialize game mode config."""
        return {
            "enabled": self.enabled,
            "engine_profile": normalize_special_mode_profile(self.engine_profile),
            "name": self.name,
            "character_image": _to_relative_path(self.character_image),
            "target_image": _to_relative_path(self.target_image),
            "obstacle_images": [_to_relative_path(p) for p in self.obstacle_images if p],
            "move_keys": self.move_keys.copy(),
            "analysis_interval": self.analysis_interval,
            "confidence": self.confidence,
            "character_confidence": self.character_confidence,
            "target_confidence": self.target_confidence,
            "arrival_threshold": self.arrival_threshold,
            "search_region": self.search_region,
            "smooth_move": self.smooth_move,
            "move_skill_key": self.move_skill_key,
            "move_skill_distance": self.move_skill_distance,
            "auto_skill_key": self.auto_skill_key,
            "auto_skill_cooldown_image": _to_relative_path(self.auto_skill_cooldown_image),
            "auto_skill_cd_region": self.auto_skill_cd_region,
            # ??? ??? ??? ???
            "navigation_mode": self.navigation_mode,
            "coord_x_region": self.coord_x_region,
            "coord_y_region": self.coord_y_region,
            "coord_anchor_enabled": self.coord_anchor_enabled,
            "coord_x_anchor_image": _to_relative_path(self.coord_x_anchor_image),
            "coord_y_anchor_image": _to_relative_path(self.coord_y_anchor_image),
            "coord_anchor_search_region": self.coord_anchor_search_region,
            "coord_x_anchor_offset": self.coord_x_anchor_offset,
            "coord_y_anchor_offset": self.coord_y_anchor_offset,
            "target_x": self.target_x,
            "target_y": self.target_y,
            "waypoints": self._serialize_waypoints(),
            "final_waypoint_idx": self.final_waypoint_idx,
            "obstacle_detection_enabled": self.obstacle_detection_enabled,
            "stuck_threshold": self.stuck_threshold,
            "detour_distance": self.detour_distance,
            # 8??? ???? ??? ???
            "diagonal_movement_enabled": self.diagonal_movement_enabled,
            "oscillation_threshold": self.oscillation_threshold,
            "detour_distance_max": self.detour_distance_max,
            # ??? ??? ???
            "escape_skill_enabled": self.escape_skill_enabled,
            "escape_skill_key": self.escape_skill_key,
            "escape_skill_cooldown": self.escape_skill_cooldown,
            "escape_skill_stuck_threshold": self.escape_skill_stuck_threshold,
            "escape_skill_direction_count": self.escape_skill_direction_count,
            "escape_skill_wait_after": self.escape_skill_wait_after,
            # ????????
            "minimap_config": self.minimap_config.to_dict() if self.minimap_config else None,
            # ??? ????????
            "mapping_enabled": self.mapping_enabled,
            # ??? ??? ???
            "boss_skill_enabled": self.boss_skill_enabled,
            "boss_skill_key": self.boss_skill_key,
            "boss_skill_cooldown": self.boss_skill_cooldown,
            # ??? ??????????
            "move_skill_enabled": self.move_skill_enabled,
            "auto_skill_enabled": self.auto_skill_enabled,
            # ??? ?????
            "move_skill_cooldown": self.move_skill_cooldown,
            "auto_skill_cooldown": self.auto_skill_cooldown,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any], templates_dir: Optional[Path] = None) -> "GameModeConfig":
        """????????? ???"""
        if templates_dir is None:
            try:
                from ..utils.config import DATA_DIR
                templates_dir = DATA_DIR / "templates"
            except ImportError:
                templates_dir = Path("data/templates")

        obstacle_images = [
            _to_absolute_path(p, templates_dir) for p in data.get("obstacle_images", []) if p
        ]

        # ??????: character_confidence/target_confidence? ?????confidence ???
        default_conf = data.get("confidence", 0.65)
        return cls(
            enabled=data.get("enabled", False),
            engine_profile=normalize_special_mode_profile(
                data.get("engine_profile", DEFAULT_SPECIAL_MODE_PROFILE)
            ),
            name=data.get("name", ""),
            character_image=_to_absolute_path(data.get("character_image"), templates_dir) or "",
            target_image=_to_absolute_path(data.get("target_image"), templates_dir) or "",
            obstacle_images=obstacle_images,
            move_keys=data.get("move_keys", {"up": "up", "down": "down", "left": "left", "right": "right"}),
            analysis_interval=data.get("analysis_interval", 0.1),
            confidence=default_conf,
            character_confidence=data.get("character_confidence", default_conf),
            target_confidence=data.get("target_confidence", default_conf),
            arrival_threshold=data.get("arrival_threshold", 30),
            search_region=data.get("search_region"),
            smooth_move=data.get("smooth_move", False),
            move_skill_key=data.get("move_skill_key", ""),
            move_skill_distance=data.get("move_skill_distance", 150),
            auto_skill_key=data.get("auto_skill_key", ""),
            auto_skill_cooldown_image=_to_absolute_path(data.get("auto_skill_cooldown_image"), templates_dir) or "",
            auto_skill_cd_region=data.get("auto_skill_cd_region"),
            # ??? ??? ??? ???
            navigation_mode=data.get("navigation_mode", "coordinate"),
            coord_x_region=data.get("coord_x_region"),
            coord_y_region=data.get("coord_y_region"),
            coord_anchor_enabled=bool(data.get("coord_anchor_enabled", False)),
            coord_x_anchor_image=_to_absolute_path(data.get("coord_x_anchor_image"), templates_dir) or "",
            coord_y_anchor_image=_to_absolute_path(data.get("coord_y_anchor_image"), templates_dir) or "",
            coord_anchor_search_region=data.get("coord_anchor_search_region"),
            coord_x_anchor_offset=data.get("coord_x_anchor_offset"),
            coord_y_anchor_offset=data.get("coord_y_anchor_offset"),
            target_x=data.get("target_x", 0),
            target_y=data.get("target_y", 0),
            waypoints=cls._deserialize_waypoints(data.get("waypoints") or [], templates_dir),
            final_waypoint_idx=data.get("final_waypoint_idx", -1),
            obstacle_detection_enabled=data.get("obstacle_detection_enabled", True),
            stuck_threshold=data.get("stuck_threshold", 3),
            detour_distance=data.get("detour_distance", 2),
            # 8??? ???? ??? ???
            diagonal_movement_enabled=data.get("diagonal_movement_enabled", False),
            oscillation_threshold=data.get("oscillation_threshold", 3),
            detour_distance_max=data.get("detour_distance_max", 8),
            # ??? ??? ???
            escape_skill_enabled=data.get("escape_skill_enabled", False),
            escape_skill_key=data.get("escape_skill_key", "z"),
            escape_skill_cooldown=data.get("escape_skill_cooldown", 10.0),
            escape_skill_stuck_threshold=data.get("escape_skill_stuck_threshold", 10),
            escape_skill_direction_count=data.get("escape_skill_direction_count", 5),
            escape_skill_wait_after=data.get("escape_skill_wait_after", 0.5),
            # ????????
            minimap_config=MinimapConfig.from_dict(data["minimap_config"], templates_dir) if data.get("minimap_config") else None,
            # ??? ????????
            mapping_enabled=data.get("mapping_enabled", True),
            # ??? ??? ???
            boss_skill_enabled=data.get("boss_skill_enabled", False),
            boss_skill_key=data.get("boss_skill_key", ""),
            boss_skill_cooldown=data.get("boss_skill_cooldown", 3.0),
            # ??? ??????????
            move_skill_enabled=data.get("move_skill_enabled", False),
            auto_skill_enabled=data.get("auto_skill_enabled", False),
            # ??? ?????
            move_skill_cooldown=data.get("move_skill_cooldown", 5.0),
            auto_skill_cooldown=data.get("auto_skill_cooldown", 5.0),
        )


@dataclass
class AutomationPlan:
    """
    ????????

    ??????????? ????????
    """
    plan_id: str = ""
    name: str = ""
    description: str = ""

    # ??? ??? (?????????)
    initial_rules: List[AutomationRule] = field(default_factory=list)
    monitoring_rules: List[AutomationRule] = field(default_factory=list)  # ??? ?????

    # ??? ???
    created_at: str = ""
    video_path: Optional[str] = None
    input_log_path: Optional[str] = None

    # ???
    user_verified: bool = False
    modified: bool = False  # ??? ??? (????????? ????????)

    # ??? ???
    total_repeat_count: int = 1  # ??? ??? ??? ???

    # Apply bounded recovery only to actions accepted by the shared safety policy.
    transition_recovery_auto_enabled: bool = False

    # ??? ??? ??? (??? ??? key = game_mode rule??rule_id)
    game_modes: Dict[str, GameModeConfig] = field(default_factory=dict)

    @property
    def game_mode(self) -> Optional[GameModeConfig]:
        """??????: ????? ?????? ???"""
        return next(iter(self.game_modes.values()), None) if self.game_modes else None

    def __post_init__(self):
        """??????????"""
        if not self.plan_id:
            self.plan_id = f"plan_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        self.transition_recovery_auto_enabled = bool(
            self.transition_recovery_auto_enabled
        )

    @property
    def all_rules(self) -> List[AutomationRule]:
        """??? ??? ???"""
        return self.initial_rules + self.monitoring_rules

    @property
    def rule_count(self) -> int:
        """Return total rule count."""
        return len(self.initial_rules) + len(self.monitoring_rules)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize automation plan."""
        result = {
            "plan_id": self.plan_id,
            "name": self.name,
            "description": self.description,
            "initial_rules": [rule.to_dict() for rule in self.initial_rules],
            "monitoring_rules": [rule.to_dict() for rule in self.monitoring_rules],
            "created_at": self.created_at,
            "video_path": self.video_path,
            "input_log_path": self.input_log_path,
            "user_verified": self.user_verified,
            "modified": self.modified,
            "total_repeat_count": self.total_repeat_count,
            "transition_recovery_auto_enabled": self.transition_recovery_auto_enabled,
        }
        if self.game_modes:
            result["game_modes"] = {
                rule_id: cfg.to_dict() for rule_id, cfg in self.game_modes.items()
            }
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any], templates_dir: Optional[Path] = None) -> "AutomationPlan":
        """????????? ??? (???? ???????? ????????)"""
        # templates_dir???????config??? ??????
        if templates_dir is None:
            try:
                from ..utils.config import DATA_DIR
                templates_dir = DATA_DIR / "templates"
            except ImportError:
                templates_dir = Path("data/templates")

        initial_rules = [
            AutomationRule.from_dict(r, templates_dir) for r in data.get("initial_rules", [])
        ]
        monitoring_rules = [
            AutomationRule.from_dict(r, templates_dir) for r in data.get("monitoring_rules", [])
        ]

        # ??? ??? ??? ??? (??? ???+ ??????????????)
        game_modes: Dict[str, GameModeConfig] = {}
        if "game_modes" in data and data["game_modes"]:
            # ????? {rule_id: config_dict}
            for rule_id, cfg_data in data["game_modes"].items():
                config = GameModeConfig.from_dict(cfg_data, templates_dir)
                if not cfg_data.get("engine_profile"):
                    config.engine_profile = infer_legacy_special_mode_profile(
                        plan_id=data.get("plan_id", ""),
                        rule_id=rule_id,
                    )
                game_modes[rule_id] = config
        elif "game_mode" in data and data["game_mode"]:
            # ??????????????: ??? game_mode ??game_modes dict
            config = GameModeConfig.from_dict(data["game_mode"], templates_dir)
            gm_rule_id = None
            for r in data.get("initial_rules", []):
                if r.get("action_type") == "game_mode":
                    gm_rule_id = r.get("rule_id")
                    break
            resolved_rule_id = gm_rule_id or "_default"
            if not data["game_mode"].get("engine_profile"):
                config.engine_profile = infer_legacy_special_mode_profile(
                    plan_id=data.get("plan_id", ""),
                    rule_id=resolved_rule_id,
                )
            game_modes[resolved_rule_id] = config

        return cls(
            plan_id=data.get("plan_id", ""),
            name=data.get("name", ""),
            description=data.get("description", ""),
            initial_rules=initial_rules,
            monitoring_rules=monitoring_rules,
            created_at=data.get("created_at", ""),
            video_path=data.get("video_path"),
            input_log_path=data.get("input_log_path"),
            user_verified=data.get("user_verified", False),
            modified=data.get("modified", False),
            total_repeat_count=data.get("total_repeat_count", 1),
            transition_recovery_auto_enabled=data.get(
                "transition_recovery_auto_enabled",
                False,
            ),
            game_modes=game_modes,
        )
