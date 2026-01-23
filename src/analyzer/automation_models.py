"""
WinCro 자동화 모델

자동화 계획 및 규칙을 위한 데이터 클래스를 정의합니다.
AI 분석 없이 단순하게 녹화된 동작을 재생하기 위한 구조입니다.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from enum import Enum
from datetime import datetime
from pathlib import Path
import uuid
import os


def _to_relative_path(abs_path: Optional[str]) -> Optional[str]:
    """절대 경로를 파일명만 추출 (저장용)"""
    if not abs_path:
        return None
    return Path(abs_path).name


def _to_absolute_path(filename: Optional[str], base_dir: Path) -> Optional[str]:
    """파일명을 절대 경로로 변환 (로드용)"""
    if not filename:
        return None
    # 이미 절대 경로면 그대로 반환 (하위 호환)
    if os.path.isabs(filename):
        # 파일이 존재하면 그대로, 없으면 base_dir에서 찾기
        if Path(filename).exists():
            return filename
        filename = Path(filename).name
    full_path = base_dir / filename
    return str(full_path)


class RuleType(Enum):
    """규칙 유형"""
    FIXED_SEQUENCE = "fixed_sequence"  # 고정 시퀀스 (좌표/이미지 클릭)
    TYPE_TEXT = "type_text"  # 텍스트 입력
    HOTKEY = "hotkey"  # 단축키
    WAIT_FOR_IMAGE = "wait_for_image"  # 이미지 대기
    WAIT_FOR_DISAPPEAR = "wait_for_disappear"  # 이미지 사라짐 대기
    CLICK_ON_APPEAR = "click_on_appear"  # 이미지 나타나면 클릭
    MONITOR = "monitor"  # 모니터링 (계속 감시)


@dataclass
class AutomationRule:
    """
    자동화 규칙

    녹화된 하나의 동작을 나타냅니다.
    """
    rule_id: str = ""
    rule_type: str = RuleType.FIXED_SEQUENCE.value
    description: str = ""

    # 동작 정보
    action_type: str = "click"  # click, double_click, right_click, type, hotkey, scroll, drag
    action_x: Optional[int] = None  # 클릭 X 좌표
    action_y: Optional[int] = None  # 클릭 Y 좌표
    action_text: Optional[str] = None  # 입력할 텍스트
    action_keys: Optional[List[str]] = None  # 단축키

    # 드래그용
    drag_to_x: Optional[int] = None
    drag_to_y: Optional[int] = None
    drag_duration: Optional[float] = None  # 드래그 소요 시간 (초)

    # 스크롤용
    scroll_amount: int = 0

    # 이미지 매칭
    target_image: Optional[str] = None  # 클릭 대상 이미지 경로 (기본)
    target_images: List[str] = field(default_factory=list)  # 멀티이미지 (OR 조건)
    trigger_image: Optional[str] = None  # 트리거 이미지 (모니터링용)
    trigger_x: Optional[int] = None  # 트리거 검색 영역 중심 X 좌표
    trigger_y: Optional[int] = None  # 트리거 검색 영역 중심 Y 좌표
    confidence: float = 0.65  # 매칭 신뢰도 (낮출수록 더 유연하게 인식)
    search_radius: int = 0  # 타겟 검색 범위 (0=전체화면, >0=action_x/y 중심 반경 픽셀)
    move_mouse_before_search: bool = False  # 검색 전 마우스를 영역 밖으로 이동 (hover 효과 방지)

    # 타이밍
    wait_after: float = 0.5  # 동작 후 대기 시간 (초)
    wait_random: bool = False  # 대기시간 랜덤 적용
    wait_random_range: float = 0.3  # 대기시간 ±범위 (초)
    typing_random: bool = False  # 텍스트 입력 시 글자 사이 랜덤 딜레이
    typing_delay: float = 0.1  # 글자 사이 기본 딜레이 (초)
    typing_delay_range: float = 0.05  # 타이핑 딜레이 ±범위 (초)
    timeout: float = 30.0  # 타임아웃 (초)
    skip_on_not_found: bool = False  # 이미지 못찾으면 wait_after 후 다음 액션으로 스킵
    repeat_count: int = 1  # 반복 횟수 (1 = 1회 실행)
    repeat_delay: float = 0.5  # 반복 사이 대기시간 (초)
    repeat_delay_random: bool = False  # 반복 대기시간 랜덤 사용
    repeat_delay_random_range: float = 0.3  # 반복 대기시간 ±범위 (초)

    # 메타
    timestamp: float = 0.0  # 녹화 시점 타임스탬프

    # 계층 구조 (부모-자식)
    parent_id: Optional[str] = None  # 부모 규칙 ID
    children: List["AutomationRule"] = field(default_factory=list)  # 자식 규칙들

    # 모니터링 모드
    is_monitoring_mode: bool = False  # 모니터링 모드 여부
    monitoring_final_image: Optional[str] = None  # 최종 이미지 (이게 나오면 모니터링 종료)
    monitoring_watches: List[Dict[str, Any]] = field(default_factory=list)  # 감시 목록 [{image: str, goto_index: int}]

    def __post_init__(self):
        """초기화 후 처리"""
        if not self.rule_id:
            self.rule_id = f"rule_{uuid.uuid4().hex[:8]}"
        if self.action_keys is None:
            self.action_keys = []
        if self.target_images is None:
            self.target_images = []
        if self.children is None:
            self.children = []
        if self.monitoring_watches is None:
            self.monitoring_watches = []

    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환 (이미지 경로는 파일명만 저장)"""
        # monitoring_watches 내의 image 경로도 파일명만 저장
        watches_for_save = []
        for watch in self.monitoring_watches:
            watch_copy = watch.copy()
            if "image" in watch_copy and watch_copy["image"]:
                watch_copy["image"] = _to_relative_path(watch_copy["image"])
            watches_for_save.append(watch_copy)

        return {
            "rule_id": self.rule_id,
            "rule_type": self.rule_type,
            "description": self.description,
            "action_type": self.action_type,
            "action_x": self.action_x,
            "action_y": self.action_y,
            "action_text": self.action_text,
            "action_keys": self.action_keys,
            "drag_to_x": self.drag_to_x,
            "drag_to_y": self.drag_to_y,
            "drag_duration": self.drag_duration,
            "scroll_amount": self.scroll_amount,
            "target_image": _to_relative_path(self.target_image),
            "target_images": [_to_relative_path(p) for p in self.target_images if p],
            "trigger_image": _to_relative_path(self.trigger_image),
            "trigger_x": self.trigger_x,
            "trigger_y": self.trigger_y,
            "confidence": self.confidence,
            "search_radius": self.search_radius,
            "move_mouse_before_search": self.move_mouse_before_search,
            "wait_after": self.wait_after,
            "wait_random": self.wait_random,
            "wait_random_range": self.wait_random_range,
            "typing_random": self.typing_random,
            "typing_delay": self.typing_delay,
            "typing_delay_range": self.typing_delay_range,
            "timeout": self.timeout,
            "skip_on_not_found": self.skip_on_not_found,
            "repeat_count": self.repeat_count,
            "repeat_delay": self.repeat_delay,
            "repeat_delay_random": self.repeat_delay_random,
            "repeat_delay_random_range": self.repeat_delay_random_range,
            "timestamp": self.timestamp,
            "parent_id": self.parent_id,
            "children": [child.to_dict() for child in self.children],
            "is_monitoring_mode": self.is_monitoring_mode,
            "monitoring_final_image": _to_relative_path(self.monitoring_final_image),
            "monitoring_watches": watches_for_save,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any], templates_dir: Optional[Path] = None) -> "AutomationRule":
        """딕셔너리에서 생성 (이미지 경로를 절대 경로로 복원)"""
        # templates_dir이 없으면 config에서 가져오기
        if templates_dir is None:
            try:
                from ..utils.config import DATA_DIR
                templates_dir = DATA_DIR / "templates"
            except ImportError:
                templates_dir = Path("data/templates")

        children_data = data.get("children", [])
        children = [cls.from_dict(c, templates_dir) for c in children_data]

        # monitoring_watches 내의 image 경로도 절대 경로로 복원
        watches = data.get("monitoring_watches", [])
        watches_restored = []
        for watch in watches:
            watch_copy = watch.copy()
            if "image" in watch_copy and watch_copy["image"]:
                watch_copy["image"] = _to_absolute_path(watch_copy["image"], templates_dir)
            watches_restored.append(watch_copy)

        # target_images 복원
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
            drag_to_x=data.get("drag_to_x"),
            drag_to_y=data.get("drag_to_y"),
            drag_duration=data.get("drag_duration"),
            scroll_amount=data.get("scroll_amount", 0),
            target_image=_to_absolute_path(data.get("target_image"), templates_dir),
            target_images=target_images,
            trigger_image=_to_absolute_path(data.get("trigger_image"), templates_dir),
            trigger_x=data.get("trigger_x"),
            trigger_y=data.get("trigger_y"),
            confidence=data.get("confidence", 0.65),
            search_radius=data.get("search_radius", 0),
            move_mouse_before_search=data.get("move_mouse_before_search", False),
            wait_after=data.get("wait_after", 0.5),
            wait_random=data.get("wait_random", False),
            wait_random_range=data.get("wait_random_range", 0.3),
            typing_random=data.get("typing_random", False),
            typing_delay=data.get("typing_delay", 0.1),
            typing_delay_range=data.get("typing_delay_range", 0.05),
            timeout=data.get("timeout", 30.0),
            skip_on_not_found=data.get("skip_on_not_found", False),
            repeat_count=data.get("repeat_count", 1),
            repeat_delay=data.get("repeat_delay", 0.5),
            repeat_delay_random=data.get("repeat_delay_random", False),
            repeat_delay_random_range=data.get("repeat_delay_random_range", 0.3),
            timestamp=data.get("timestamp", 0.0),
            parent_id=data.get("parent_id"),
            children=children,
            is_monitoring_mode=data.get("is_monitoring_mode", False),
            monitoring_final_image=_to_absolute_path(data.get("monitoring_final_image"), templates_dir),
            monitoring_watches=watches_restored,
        )


@dataclass
class AutomationPlan:
    """
    자동화 계획

    녹화된 동작들의 집합입니다.
    """
    plan_id: str = ""
    name: str = ""
    description: str = ""

    # 규칙 목록 (순서대로 실행)
    initial_rules: List[AutomationRule] = field(default_factory=list)
    monitoring_rules: List[AutomationRule] = field(default_factory=list)  # 현재 미사용

    # 메타 정보
    created_at: str = ""
    video_path: Optional[str] = None
    input_log_path: Optional[str] = None

    # 상태
    user_verified: bool = False
    modified: bool = False  # 수정 여부 (수정된 계획은 재분석 불가)

    # 재생 설정
    total_repeat_count: int = 1  # 전체 재생 반복 횟수

    def __post_init__(self):
        """초기화 후 처리"""
        if not self.plan_id:
            self.plan_id = f"plan_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        if not self.created_at:
            self.created_at = datetime.now().isoformat()

    @property
    def all_rules(self) -> List[AutomationRule]:
        """모든 규칙 반환"""
        return self.initial_rules + self.monitoring_rules

    @property
    def rule_count(self) -> int:
        """전체 규칙 수"""
        return len(self.initial_rules) + len(self.monitoring_rules)

    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환"""
        return {
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
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any], templates_dir: Optional[Path] = None) -> "AutomationPlan":
        """딕셔너리에서 생성 (이미지 경로를 절대 경로로 복원)"""
        # templates_dir이 없으면 config에서 가져오기
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
        )
