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
    search_region: Optional[List[int]] = None  # 직사각형 검색 범위 [x1, y1, x2, y2] (search_radius보다 우선)
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
            # condition_image 경로도 변환
            if "condition_image" in watch_copy and watch_copy["condition_image"]:
                watch_copy["condition_image"] = _to_relative_path(watch_copy["condition_image"])
            # monitor_actions 내의 이미지 경로도 변환
            if "monitor_actions" in watch_copy:
                actions_copy = []
                for action in watch_copy["monitor_actions"]:
                    action_copy = action.copy()
                    if "image" in action_copy and action_copy["image"]:
                        action_copy["image"] = _to_relative_path(action_copy["image"])
                    actions_copy.append(action_copy)
                watch_copy["monitor_actions"] = actions_copy
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
            "search_region": self.search_region,
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
            # condition_image 경로도 절대 경로로 복원
            if "condition_image" in watch_copy and watch_copy["condition_image"]:
                watch_copy["condition_image"] = _to_absolute_path(watch_copy["condition_image"], templates_dir)
            # monitor_actions 내의 이미지 경로도 절대 경로로 복원
            if "monitor_actions" in watch_copy:
                actions_copy = []
                for action in watch_copy["monitor_actions"]:
                    action_copy = action.copy()
                    if "image" in action_copy and action_copy["image"]:
                        action_copy["image"] = _to_absolute_path(action_copy["image"], templates_dir)
                    actions_copy.append(action_copy)
                watch_copy["monitor_actions"] = actions_copy
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
            search_region=data.get("search_region"),
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
class MinimapConfig:
    """미니맵 자동 이동 설정"""
    enabled: bool = False

    # 1. 미니맵 이미지 (전체 맵 이미지)
    minimap_image_path: Optional[str] = None

    # 2. 목적지 이미지 (미니맵에서 크롭한 이미지)
    destination_image_path: Optional[str] = None

    # 3. 게임 화면에서 미니맵 영역 [x1, y1, x2, y2]
    minimap_region: Optional[List[int]] = None

    # 4. 장애물 색상 (HSV) - 파란색 (강/물)
    obstacle_hsv_lower: List[int] = field(default_factory=lambda: [100, 50, 50])
    obstacle_hsv_upper: List[int] = field(default_factory=lambda: [130, 255, 255])

    # 5. 노란점 색상 (HSV) - 현재 위치
    yellow_hsv_lower: List[int] = field(default_factory=lambda: [20, 150, 150])
    yellow_hsv_upper: List[int] = field(default_factory=lambda: [35, 255, 255])

    # 캐시된 목적지 좌표 (템플릿 매칭 결과)
    cached_destination_pixel: Optional[List[int]] = None

    # 캐시된 경로 (A* 결과)
    cached_path: Optional[List[List[int]]] = None

    # 도착 판정 거리 (픽셀)
    arrival_threshold: int = 10

    # 분석 간격 (초)
    analysis_interval: float = 0.1

    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환"""
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
        """딕셔너리에서 생성"""
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
    게임 특화 모드 설정

    이미지 인식 기반으로 캐릭터를 목표까지 자동 이동시킵니다.
    """
    enabled: bool = False
    name: str = ""                 # 사용자 지정 이름
    character_image: str = ""      # 캐릭터 이미지 (위치 파악용)
    target_image: str = ""         # 목표 이미지
    obstacle_images: List[str] = field(default_factory=list)  # 장애물 이미지들 (Phase 2)
    move_keys: Dict[str, str] = field(default_factory=lambda: {
        "up": "up", "down": "down", "left": "left", "right": "right"
    })
    analysis_interval: float = 0.1  # 분석 간격 (초)
    confidence: float = 0.65        # 인식 신뢰도 (기본값, 하위호환)
    character_confidence: float = 0.65  # 캐릭터 인식률
    target_confidence: float = 0.65     # 목표 인식률
    arrival_threshold: int = 30     # 도달 판정 거리 (픽셀)
    search_region: Optional[List[int]] = None  # 검색 영역 [x1, y1, x2, y2], None=전체화면
    smooth_move: bool = False  # True=키 홀드(부드러움), False=키 반복(호환성 좋음)
    move_skill_key: str = ""  # 이동 스킬 키 (예: "4")
    move_skill_distance: int = 150  # 이동 스킬 사용 거리 (픽셀, 이 거리 이상이면 스킬 사용)
    auto_skill_key: str = ""  # 상시 사용 스킬 키
    auto_skill_cooldown_image: str = ""  # 스킬 쿨타임 이미지 (이 이미지가 없으면 스킬 사용)
    auto_skill_cd_region: Optional[List[int]] = None  # 쿨타임 이미지 검색 영역 [x1, y1, x2, y2]

    # === 좌표 기반 탐색 모드 ===
    navigation_mode: str = "coordinate"  # "coordinate" (좌표 기반)
    coord_x_region: Optional[List[int]] = None  # X 좌표 OCR 영역 [x1, y1, x2, y2]
    coord_y_region: Optional[List[int]] = None  # Y 좌표 OCR 영역 [x1, y1, x2, y2]
    target_x: int = 0  # 목표 X 좌표
    target_y: int = 0  # 목표 Y 좌표
    waypoints: List = field(default_factory=list)  # [[x,y,name] 또는 [x,y,name,{image_config}]]
    final_waypoint_idx: int = -1  # 최종 목표 경유지 인덱스 (-1: 마지막 경유지)
    obstacle_detection_enabled: bool = True  # 장애물 감지 활성화
    stuck_threshold: int = 3  # 정체 판정 프레임 수 (이 횟수 동안 좌표 변화 없으면 장애물로 판정)
    detour_distance: int = 2  # 우회 이동 거리 (타일 수)

    # === 8방향 지능형 회피 설정 ===
    diagonal_movement_enabled: bool = False  # 대각선 이동 허용 (게임이 지원하는 경우만)
    oscillation_threshold: int = 3  # 진동 판정 반복 횟수
    detour_distance_max: int = 8  # 우회 거리 최대값

    # === 탈출 스킬 설정 ===
    escape_skill_enabled: bool = False       # 탈출 스킬 활성화
    escape_skill_key: str = "z"              # 스킬 키
    escape_skill_cooldown: float = 10.0      # 쿨타임 (초)
    escape_skill_stuck_threshold: int = 10   # 연속 정체 횟수 (발동 조건)
    escape_skill_direction_count: int = 5    # 방향키 입력 횟수 (범위 내 최대 이동)
    escape_skill_wait_after: float = 0.5     # 스킬 후 대기 (텔레포트 애니메이션)

    # === 미니맵 경로 탐색 설정 ===
    minimap_config: Optional[MinimapConfig] = None

    # === 맵핑 시스템 설정 ===
    mapping_enabled: bool = True  # 이동 시 맵 데이터 기록 여부

    # === 보스 스킬 설정 ===
    boss_skill_enabled: bool = False        # 보스 스킬 활성화
    boss_skill_key: str = ""                # 스킬 키
    boss_skill_cooldown: float = 3.0        # 쿨타임 (초)

    # === 스킬 활성화 플래그 ===
    move_skill_enabled: bool = False   # 이동 스킬 사용 여부
    auto_skill_enabled: bool = False   # 상시 스킬 사용 여부

    # === 스킬 쿨타임 ===
    move_skill_cooldown: float = 5.0   # 이동 스킬 쿨타임 (초)
    auto_skill_cooldown: float = 5.0   # 상시 스킬 쿨타임 (초)

    def _serialize_waypoints(self):
        """waypoints 직렬화 (이미지 경로 → 상대경로)"""
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
        """waypoints 역직렬화 (파일명 → 절대경로)"""
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
        """딕셔너리로 변환"""
        return {
            "enabled": self.enabled,
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
            # 좌표 기반 탐색 모드
            "navigation_mode": self.navigation_mode,
            "coord_x_region": self.coord_x_region,
            "coord_y_region": self.coord_y_region,
            "target_x": self.target_x,
            "target_y": self.target_y,
            "waypoints": self._serialize_waypoints(),
            "final_waypoint_idx": self.final_waypoint_idx,
            "obstacle_detection_enabled": self.obstacle_detection_enabled,
            "stuck_threshold": self.stuck_threshold,
            "detour_distance": self.detour_distance,
            # 8방향 지능형 회피 설정
            "diagonal_movement_enabled": self.diagonal_movement_enabled,
            "oscillation_threshold": self.oscillation_threshold,
            "detour_distance_max": self.detour_distance_max,
            # 탈출 스킬 설정
            "escape_skill_enabled": self.escape_skill_enabled,
            "escape_skill_key": self.escape_skill_key,
            "escape_skill_cooldown": self.escape_skill_cooldown,
            "escape_skill_stuck_threshold": self.escape_skill_stuck_threshold,
            "escape_skill_direction_count": self.escape_skill_direction_count,
            "escape_skill_wait_after": self.escape_skill_wait_after,
            # 미니맵 설정
            "minimap_config": self.minimap_config.to_dict() if self.minimap_config else None,
            # 맵핑 시스템 설정
            "mapping_enabled": self.mapping_enabled,
            # 보스 스킬 설정
            "boss_skill_enabled": self.boss_skill_enabled,
            "boss_skill_key": self.boss_skill_key,
            "boss_skill_cooldown": self.boss_skill_cooldown,
            # 스킬 활성화 플래그
            "move_skill_enabled": self.move_skill_enabled,
            "auto_skill_enabled": self.auto_skill_enabled,
            # 스킬 쿨타임
            "move_skill_cooldown": self.move_skill_cooldown,
            "auto_skill_cooldown": self.auto_skill_cooldown,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any], templates_dir: Optional[Path] = None) -> "GameModeConfig":
        """딕셔너리에서 생성"""
        if templates_dir is None:
            try:
                from ..utils.config import DATA_DIR
                templates_dir = DATA_DIR / "templates"
            except ImportError:
                templates_dir = Path("data/templates")

        obstacle_images = [
            _to_absolute_path(p, templates_dir) for p in data.get("obstacle_images", []) if p
        ]

        # 하위호환: character_confidence/target_confidence가 없으면 confidence 사용
        default_conf = data.get("confidence", 0.65)
        return cls(
            enabled=data.get("enabled", False),
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
            # 좌표 기반 탐색 모드
            navigation_mode=data.get("navigation_mode", "coordinate"),
            coord_x_region=data.get("coord_x_region"),
            coord_y_region=data.get("coord_y_region"),
            target_x=data.get("target_x", 0),
            target_y=data.get("target_y", 0),
            waypoints=cls._deserialize_waypoints(data.get("waypoints") or [], templates_dir),
            final_waypoint_idx=data.get("final_waypoint_idx", -1),
            obstacle_detection_enabled=data.get("obstacle_detection_enabled", True),
            stuck_threshold=data.get("stuck_threshold", 3),
            detour_distance=data.get("detour_distance", 2),
            # 8방향 지능형 회피 설정
            diagonal_movement_enabled=data.get("diagonal_movement_enabled", False),
            oscillation_threshold=data.get("oscillation_threshold", 3),
            detour_distance_max=data.get("detour_distance_max", 8),
            # 탈출 스킬 설정
            escape_skill_enabled=data.get("escape_skill_enabled", False),
            escape_skill_key=data.get("escape_skill_key", "z"),
            escape_skill_cooldown=data.get("escape_skill_cooldown", 10.0),
            escape_skill_stuck_threshold=data.get("escape_skill_stuck_threshold", 10),
            escape_skill_direction_count=data.get("escape_skill_direction_count", 5),
            escape_skill_wait_after=data.get("escape_skill_wait_after", 0.5),
            # 미니맵 설정
            minimap_config=MinimapConfig.from_dict(data["minimap_config"], templates_dir) if data.get("minimap_config") else None,
            # 맵핑 시스템 설정
            mapping_enabled=data.get("mapping_enabled", True),
            # 보스 스킬 설정
            boss_skill_enabled=data.get("boss_skill_enabled", False),
            boss_skill_key=data.get("boss_skill_key", ""),
            boss_skill_cooldown=data.get("boss_skill_cooldown", 3.0),
            # 스킬 활성화 플래그
            move_skill_enabled=data.get("move_skill_enabled", False),
            auto_skill_enabled=data.get("auto_skill_enabled", False),
            # 스킬 쿨타임
            move_skill_cooldown=data.get("move_skill_cooldown", 5.0),
            auto_skill_cooldown=data.get("auto_skill_cooldown", 5.0),
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

    # 게임 특화 모드 (다중 지원: key = game_mode rule의 rule_id)
    game_modes: Dict[str, GameModeConfig] = field(default_factory=dict)

    @property
    def game_mode(self) -> Optional[GameModeConfig]:
        """하위호환: 첫 번째 특화모드 반환"""
        return next(iter(self.game_modes.values()), None) if self.game_modes else None

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
        }
        if self.game_modes:
            result["game_modes"] = {
                rule_id: cfg.to_dict() for rule_id, cfg in self.game_modes.items()
            }
        return result

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

        # 게임 모드 설정 복원 (다중 지원 + 구버전 마이그레이션)
        game_modes: Dict[str, GameModeConfig] = {}
        if "game_modes" in data and data["game_modes"]:
            # 신버전: {rule_id: config_dict}
            for rule_id, cfg_data in data["game_modes"].items():
                game_modes[rule_id] = GameModeConfig.from_dict(cfg_data, templates_dir)
        elif "game_mode" in data and data["game_mode"]:
            # 구버전 마이그레이션: 단일 game_mode → game_modes dict
            config = GameModeConfig.from_dict(data["game_mode"], templates_dir)
            gm_rule_id = None
            for r in data.get("initial_rules", []):
                if r.get("action_type") == "game_mode":
                    gm_rule_id = r.get("rule_id")
                    break
            game_modes[gm_rule_id or "_default"] = config

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
            game_modes=game_modes,
        )
