"""
WinCro 데이터 모델 모듈

데이터베이스 테이블에 대응하는 데이터 클래스를 정의합니다.
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import List, Dict, Any, Optional
from enum import Enum
import json


class ActionType(Enum):
    """액션 유형 열거형"""
    CLICK = "click"
    DOUBLE_CLICK = "double_click"
    RIGHT_CLICK = "right_click"
    DRAG = "drag"
    SCROLL = "scroll"
    TYPE = "type"
    HOTKEY = "hotkey"
    KEY_PRESS = "key_press"
    WAIT = "wait"
    WAIT_FOR_IMAGE = "wait_for_image"  # 이미지가 나타날 때까지 대기
    SCREENSHOT = "screenshot"
    FIND_IMAGE = "find_image"
    FIND_TEXT = "find_text"
    CONDITION = "condition"
    LOOP = "loop"


class ExecutionStatus(Enum):
    """실행 상태 열거형"""
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Action:
    """
    단일 액션을 나타내는 데이터 클래스

    마우스 클릭, 키보드 입력 등 개별 동작을 표현합니다.
    """
    action_type: str  # ActionType의 value
    x: Optional[int] = None  # 마우스 X 좌표
    y: Optional[int] = None  # 마우스 Y 좌표
    button: str = "left"  # 마우스 버튼 (left, right, middle)
    text: Optional[str] = None  # 입력할 텍스트
    keys: Optional[List[str]] = None  # 단축키 조합
    duration: float = 0.0  # 액션 지속 시간 (초)
    wait_before: float = 0.0  # 실행 전 대기 시간 (초)
    wait_after: float = 0.0  # 실행 후 대기 시간 (초)
    wait_random: bool = False  # 대기시간 랜덤 적용
    wait_random_range: float = 0.3  # 대기시간 ±범위 (초)
    typing_random: bool = False  # 텍스트 입력 시 글자 사이 랜덤 딜레이
    typing_delay: float = 0.1  # 글자 사이 기본 딜레이 (초)
    typing_delay_range: float = 0.05  # 타이핑 딜레이 ±범위 (초)
    target_image: Optional[str] = None  # 대상 이미지 경로
    confidence: float = 0.8  # 이미지 매칭 신뢰도
    scroll_amount: int = 0  # 스크롤 양 (양수: 위, 음수: 아래)
    drag_to_x: Optional[int] = None  # 드래그 종료 X
    drag_to_y: Optional[int] = None  # 드래그 종료 Y
    drag_duration: Optional[float] = None  # 드래그 소요 시간 (초)
    timestamp: float = 0.0  # 원본 영상에서의 시간
    description: str = ""  # 액션 설명
    # 이미지 대기 관련 필드
    wait_for_image: Optional[str] = None  # 대기할 이미지 경로
    wait_for_image_timeout: float = 30.0  # 이미지 대기 타임아웃 (초)
    wait_for_image_disappear: bool = False  # True: 이미지가 사라질 때까지 대기
    # 반복 실행
    repeat_count: int = 1  # 반복 횟수 (1 = 1회 실행)
    repeat_delay: float = 0.5  # 반복 사이 대기시간 (초)
    repeat_delay_random: bool = False  # 반복 대기시간 랜덤 사용
    repeat_delay_random_range: float = 0.3  # 반복 대기시간 ±범위 (초)
    # 스킵 모드
    skip_on_not_found: bool = False  # 이미지 못찾으면 wait_after 후 다음 액션으로 스킵
    # 계층 구조 (부모-자식)
    parent_id: Optional[str] = None  # 부모 액션 ID
    children: List["Action"] = field(default_factory=list)  # 자식 액션들
    action_id: str = ""  # 액션 고유 ID

    def __post_init__(self):
        """초기화 후 처리"""
        import uuid
        if not self.action_id:
            self.action_id = f"action_{uuid.uuid4().hex[:8]}"
        if self.children is None:
            self.children = []

    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환"""
        result = asdict(self)
        # children을 재귀적으로 변환
        result["children"] = [child.to_dict() if hasattr(child, 'to_dict') else child for child in (self.children or [])]
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Action':
        """딕셔너리에서 생성 (하위 호환성 지원)"""
        # 유효한 필드 목록
        valid_fields = {
            'action_type', 'x', 'y', 'button', 'text', 'keys', 'duration',
            'wait_before', 'wait_after', 'wait_random', 'wait_random_range',
            'typing_random', 'typing_delay', 'typing_delay_range',
            'target_image', 'confidence',
            'scroll_amount', 'drag_to_x', 'drag_to_y', 'drag_duration', 'timestamp',
            'description', 'wait_for_image', 'wait_for_image_timeout',
            'wait_for_image_disappear', 'repeat_count', 'repeat_delay',
            'repeat_delay_random', 'repeat_delay_random_range', 'skip_on_not_found',
            'parent_id', 'action_id'
        }
        # 유효한 필드만 필터링 (새 버전에서 추가된 필드가 없어도 됨)
        filtered_data = {k: v for k, v in data.items() if k in valid_fields}
        # children 재귀적으로 처리
        children_data = data.get("children", [])
        children = [cls.from_dict(c) if isinstance(c, dict) else c for c in children_data]
        action = cls(**filtered_data)
        action.children = children
        return action

    def __str__(self) -> str:
        """문자열 표현"""
        if self.action_type == ActionType.CLICK.value:
            return f"클릭 ({self.x}, {self.y})"
        elif self.action_type == ActionType.DOUBLE_CLICK.value:
            return f"더블클릭 ({self.x}, {self.y})"
        elif self.action_type == ActionType.RIGHT_CLICK.value:
            return f"우클릭 ({self.x}, {self.y})"
        elif self.action_type == ActionType.TYPE.value:
            return f"입력: {self.text[:20]}..." if self.text and len(self.text) > 20 else f"입력: {self.text}"
        elif self.action_type == ActionType.HOTKEY.value:
            return f"단축키: {'+'.join(self.keys or [])}"
        elif self.action_type == ActionType.WAIT.value:
            return f"대기: {self.duration}초"
        elif self.action_type == ActionType.WAIT_FOR_IMAGE.value:
            mode = "사라질 때까지" if self.wait_for_image_disappear else "나타날 때까지"
            return f"이미지 대기: {mode} (최대 {self.wait_for_image_timeout}초)"
        elif self.action_type == ActionType.SCROLL.value:
            direction = "위" if self.scroll_amount > 0 else "아래"
            return f"스크롤: {direction} {abs(self.scroll_amount)}"
        elif self.action_type == ActionType.DRAG.value:
            return f"드래그: ({self.x}, {self.y}) → ({self.drag_to_x}, {self.drag_to_y})"
        else:
            return f"{self.action_type}"


@dataclass
class Sequence:
    """
    액션 시퀀스를 나타내는 데이터 클래스

    여러 액션의 순차적 집합을 표현합니다.
    """
    id: Optional[int] = None
    name: str = ""
    description: str = ""
    actions: List[Action] = field(default_factory=list)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    last_run: Optional[datetime] = None
    run_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    tags: List[str] = field(default_factory=list)
    version: str = "1.0"

    @property
    def success_rate(self) -> float:
        """성공률 계산"""
        if self.run_count == 0:
            return 0.0
        return (self.success_count / self.run_count) * 100

    @property
    def action_count(self) -> int:
        """액션 수"""
        return len(self.actions)

    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환 (DB 저장용)"""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "actions": json.dumps([a.to_dict() for a in self.actions], ensure_ascii=False),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "last_run": self.last_run.isoformat() if self.last_run else None,
            "run_count": self.run_count,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "tags": json.dumps(self.tags, ensure_ascii=False),
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Sequence':
        """딕셔너리에서 생성"""
        actions_data = data.get("actions", "[]")
        if isinstance(actions_data, str):
            actions_data = json.loads(actions_data)

        tags_data = data.get("tags", "[]")
        if isinstance(tags_data, str):
            tags_data = json.loads(tags_data)

        return cls(
            id=data.get("id"),
            name=data.get("name", ""),
            description=data.get("description", ""),
            actions=[Action.from_dict(a) for a in actions_data],
            created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else None,
            updated_at=datetime.fromisoformat(data["updated_at"]) if data.get("updated_at") else None,
            last_run=datetime.fromisoformat(data["last_run"]) if data.get("last_run") else None,
            run_count=data.get("run_count", 0),
            success_count=data.get("success_count", 0),
            failure_count=data.get("failure_count", 0),
            tags=tags_data,
            version=data.get("version", "1.0"),
        )

    def to_export_dict(self) -> Dict[str, Any]:
        """내보내기용 딕셔너리 (JSON 파일 저장용)"""
        return {
            "name": self.name,
            "description": self.description,
            "actions": [a.to_dict() for a in self.actions],
            "tags": self.tags,
            "version": self.version,
            "exported_at": datetime.now().isoformat(),
        }

    @classmethod
    def from_export_dict(cls, data: Dict[str, Any]) -> 'Sequence':
        """내보내기 딕셔너리에서 생성"""
        return cls(
            name=data.get("name", "가져온 시퀀스"),
            description=data.get("description", ""),
            actions=[Action.from_dict(a) for a in data.get("actions", [])],
            tags=data.get("tags", []),
            version=data.get("version", "1.0"),
            created_at=datetime.now(),
        )


@dataclass
class ActionTemplate:
    """
    액션 템플릿을 나타내는 데이터 클래스

    특정 시퀀스 내의 개별 단계를 저장합니다.
    """
    id: Optional[int] = None
    sequence_id: Optional[int] = None
    step_order: int = 0
    action_type: str = ""
    target_image_path: Optional[str] = None
    parameters: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환"""
        return {
            "id": self.id,
            "sequence_id": self.sequence_id,
            "step_order": self.step_order,
            "action_type": self.action_type,
            "target_image_path": self.target_image_path,
            "parameters": json.dumps(self.parameters, ensure_ascii=False),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ActionTemplate':
        """딕셔너리에서 생성 (하위 호환성 지원)"""
        params = data.get("parameters", "{}")
        if isinstance(params, str):
            try:
                params = json.loads(params)
            except json.JSONDecodeError:
                params = {}

        return cls(
            id=data.get("id"),
            sequence_id=data.get("sequence_id"),
            step_order=data.get("step_order", 0),
            action_type=data.get("action_type", ""),
            target_image_path=data.get("target_image_path"),
            parameters=params if isinstance(params, dict) else {},
        )


@dataclass
class ExecutionLog:
    """
    실행 로그를 나타내는 데이터 클래스

    시퀀스 실행 기록을 저장합니다.
    """
    id: Optional[int] = None
    sequence_id: Optional[int] = None
    sequence_name: str = ""
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    status: str = ExecutionStatus.PENDING.value
    error_message: Optional[str] = None
    ai_interventions: int = 0
    total_steps: int = 0
    completed_steps: int = 0
    skipped_steps: int = 0
    failed_steps: int = 0
    execution_time_ms: int = 0
    notes: str = ""

    @property
    def duration(self) -> Optional[float]:
        """실행 시간 (초)"""
        if self.started_at and self.ended_at:
            return (self.ended_at - self.started_at).total_seconds()
        return None

    @property
    def is_success(self) -> bool:
        """성공 여부"""
        return self.status == ExecutionStatus.COMPLETED.value

    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환"""
        return {
            "id": self.id,
            "sequence_id": self.sequence_id,
            "sequence_name": self.sequence_name,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "status": self.status,
            "error_message": self.error_message,
            "ai_interventions": self.ai_interventions,
            "total_steps": self.total_steps,
            "completed_steps": self.completed_steps,
            "skipped_steps": self.skipped_steps,
            "failed_steps": self.failed_steps,
            "execution_time_ms": self.execution_time_ms,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ExecutionLog':
        """딕셔너리에서 생성"""
        return cls(
            id=data.get("id"),
            sequence_id=data.get("sequence_id"),
            sequence_name=data.get("sequence_name", ""),
            started_at=datetime.fromisoformat(data["started_at"]) if data.get("started_at") else None,
            ended_at=datetime.fromisoformat(data["ended_at"]) if data.get("ended_at") else None,
            status=data.get("status", ExecutionStatus.PENDING.value),
            error_message=data.get("error_message"),
            ai_interventions=data.get("ai_interventions", 0),
            total_steps=data.get("total_steps", 0),
            completed_steps=data.get("completed_steps", 0),
            skipped_steps=data.get("skipped_steps", 0),
            failed_steps=data.get("failed_steps", 0),
            execution_time_ms=data.get("execution_time_ms", 0),
            notes=data.get("notes", ""),
        )


@dataclass
class Recording:
    """
    녹화 정보를 나타내는 데이터 클래스

    녹화된 영상 및 입력 로그 정보를 저장합니다.
    """
    id: Optional[int] = None
    name: str = ""
    video_path: str = ""
    input_log_path: str = ""
    duration_seconds: float = 0.0
    fps: int = 30
    resolution_width: int = 1920
    resolution_height: int = 1080
    created_at: Optional[datetime] = None
    analyzed: bool = False
    ai_analyzed: bool = False  # AI 자동화 분석 완료 여부
    sequence_id: Optional[int] = None  # 분석 후 생성된 시퀀스 ID
    automation_plan_id: Optional[str] = None  # AI 자동화 계획 ID
    locked: bool = False  # 잠금 여부 (잠금 시 삭제 불가)

    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환"""
        return {
            "id": self.id,
            "name": self.name,
            "video_path": self.video_path,
            "input_log_path": self.input_log_path,
            "duration_seconds": self.duration_seconds,
            "fps": self.fps,
            "resolution_width": self.resolution_width,
            "resolution_height": self.resolution_height,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "analyzed": self.analyzed,
            "ai_analyzed": self.ai_analyzed,
            "sequence_id": self.sequence_id,
            "automation_plan_id": self.automation_plan_id,
            "locked": self.locked,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Recording':
        """딕셔너리에서 생성"""
        return cls(
            id=data.get("id"),
            name=data.get("name", ""),
            video_path=data.get("video_path", ""),
            input_log_path=data.get("input_log_path", ""),
            duration_seconds=data.get("duration_seconds", 0.0),
            fps=data.get("fps", 30),
            resolution_width=data.get("resolution_width", 1920),
            resolution_height=data.get("resolution_height", 1080),
            created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else None,
            analyzed=data.get("analyzed", False),
            ai_analyzed=data.get("ai_analyzed", False),
            sequence_id=data.get("sequence_id"),
            automation_plan_id=data.get("automation_plan_id"),
            locked=data.get("locked", False),
        )
