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
    confidence: float = 0.65  # ??? ?????(?????? ???????? ???)
    search_radius: int = 0  # ????????? (0=??????, >0=action_x/y ??? ??? ???)
    search_region: Optional[List[int]] = None  # ?????? ?????? [x1, y1, x2, y2] (search_radius??? ???)
    move_mouse_before_search: bool = False  # ??????????? ??? ???????? (hover ??? ???)
    alternate_mouse_route: bool = False  # 이미지 클릭 시 기본 직선 이동 대신 반대 우회 경로로 접근
    click_until_image_disappears: bool = False  # 이미지가 사라질 때까지 반복 클릭

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
    trigger_missing_key_repeat_count: int = 1  # 트리거 미감지 종료 전 키입력 반복횟수
    trigger_missing_key_repeat_delay: float = 0.5  # 트리거 미감지 종료 전 키입력 반복 대기시간
    trigger_missing_key_repeat_delay_random: bool = False  # 트리거 미감지 종료 전 키입력 랜덤 대기
    trigger_missing_key_repeat_delay_random_range: float = 0.3  # 트리거 미감지 종료 전 키입력 랜덤 대기 범위
    repeat_count: int = 1  # ??? ??? (1 = 1?????)
    repeat_delay: float = 0.5  # ??? ??? ??????(??
    repeat_delay_random: bool = False  # ??? ????????? ???
    repeat_delay_random_range: float = 0.3  # ??? ?????????? (??

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
        if self.target_images is None:
            self.target_images = []
        if self.trigger_missing_keys is None:
            self.trigger_missing_keys = []
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

        return {
            "rule_id": self.rule_id,
            "rule_type": self.rule_type,
            "description": self.description,
            "action_type": self.action_type,
            "action_x": self.action_x,
            "action_y": self.action_y,
            "action_text": self.action_text,
            "action_keys": self.action_keys,
            "action_key_events": self.action_key_events,
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
            "alternate_mouse_route": self.alternate_mouse_route,
            "click_until_image_disappears": self.click_until_image_disappears,
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
            "trigger_missing_key_repeat_count": self.trigger_missing_key_repeat_count,
            "trigger_missing_key_repeat_delay": self.trigger_missing_key_repeat_delay,
            "trigger_missing_key_repeat_delay_random": self.trigger_missing_key_repeat_delay_random,
            "trigger_missing_key_repeat_delay_random_range": self.trigger_missing_key_repeat_delay_random_range,
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
            alternate_mouse_route=data.get("alternate_mouse_route", False),
            click_until_image_disappears=data.get("click_until_image_disappears", False),
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
            trigger_missing_key_repeat_count=data.get("trigger_missing_key_repeat_count", 1),
            trigger_missing_key_repeat_delay=data.get("trigger_missing_key_repeat_delay", 0.5),
            trigger_missing_key_repeat_delay_random=data.get("trigger_missing_key_repeat_delay_random", False),
            trigger_missing_key_repeat_delay_random_range=data.get("trigger_missing_key_repeat_delay_random_range", 0.3),
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
                game_modes[rule_id] = GameModeConfig.from_dict(cfg_data, templates_dir)
        elif "game_mode" in data and data["game_mode"]:
            # ??????????????: ??? game_mode ??game_modes dict
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
