"""
WinCro 8방향 지능형 장애물 회피 모듈

진동 방지 및 동적 우회 거리를 지원하는 장애물 회피 알고리즘입니다.
"""

import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from collections import deque

logger = logging.getLogger(__name__)

# 8방향 정의 (dx, dy)
DIRECTIONS_8 = {
    "right": (1, 0),
    "left": (-1, 0),
    "down": (0, 1),
    "up": (0, -1),
    "up_right": (1, -1),
    "up_left": (-1, -1),
    "down_right": (1, 1),
    "down_left": (-1, 1),
}

# 4방향 (대각선 비활성화 시 사용)
DIRECTIONS_4 = {
    "right": (1, 0),
    "left": (-1, 0),
    "down": (0, 1),
    "up": (0, -1),
}

# 방향 반대 매핑
OPPOSITE_DIRECTION = {
    "right": "left",
    "left": "right",
    "up": "down",
    "down": "up",
    "up_right": "down_left",
    "up_left": "down_right",
    "down_right": "up_left",
    "down_left": "up_right",
}

# 4방향 시계방향 순서 (Wall Following용)
CLOCKWISE_4 = ["up", "right", "down", "left"]


@dataclass
class ObstacleMap:
    """
    좌표 기반 장애물 맵

    이동 실패 시 해당 좌표의 막힌 방향을 기록하고,
    BFS 경로 탐색 시 장애물 정보로 활용합니다.

    핵심: "방향"이 아닌 "좌표"를 기억!
    - 이전: "오른쪽이 막혔다" → 모든 위치에서 오른쪽 피함 (잘못됨)
    - 현재: "(30,25)에서 오른쪽이 막혔다" → (31,25)가 장애물
    """

    # 막힌 타일: {(x, y): set(막힌_방향들)}
    blocked_from: Dict[Tuple[int, int], set] = field(default_factory=dict)

    # 완전히 이동 불가능한 타일 (장애물)
    impassable_tiles: set = field(default_factory=set)

    # 이동 성공한 타일 (확실히 이동 가능)
    passable_tiles: set = field(default_factory=set)

    def mark_blocked(self, from_x: int, from_y: int, direction: str):
        """
        특정 좌표에서 특정 방향이 막힘 기록

        from_pos에서 direction으로 이동 시도했으나 실패
        → direction 방향의 타일이 장애물
        """
        key = (from_x, from_y)
        if key not in self.blocked_from:
            self.blocked_from[key] = set()
        self.blocked_from[key].add(direction)

        # 막힌 타일 좌표 계산 (4방향 + 대각선 모두 지원)
        if direction in DIRECTIONS_8:
            dx, dy = DIRECTIONS_8[direction]
            blocked_tile = (from_x + dx, from_y + dy)
            self.impassable_tiles.add(blocked_tile)
            logger.debug(f"[ObstacleMap] 장애물 기록: {blocked_tile} (from {key} -> {direction})")

    def mark_passable(self, x: int, y: int):
        """이동 성공한 타일 기록"""
        pos = (x, y)
        self.passable_tiles.add(pos)
        # impassable에서 제거 (잘못 기록된 경우 수정)
        self.impassable_tiles.discard(pos)

    def is_passable(self, x: int, y: int) -> bool:
        """해당 타일로 이동 가능한지"""
        return (x, y) not in self.impassable_tiles

    def get_blocked_directions(self, x: int, y: int) -> set:
        """해당 좌표에서 막힌 방향들 반환"""
        return self.blocked_from.get((x, y), set()).copy()

    def clear(self):
        """맵 초기화"""
        self.blocked_from.clear()
        self.impassable_tiles.clear()
        self.passable_tiles.clear()

    def get_obstacle_count(self) -> int:
        """발견된 장애물 타일 수"""
        return len(self.impassable_tiles)


class BFSPathfinder:
    """
    BFS 기반 경로 탐색

    장애물 맵을 활용하여 목표까지의 최단 경로를 찾습니다.
    이동 중 새로운 장애물 발견 시 경로를 재계산합니다.
    """

    def __init__(self, obstacle_map: ObstacleMap):
        self.obstacle_map = obstacle_map
        self.current_path: List[Tuple[int, int]] = []
        self.path_index: int = 0

    def find_path(self, start: Tuple[int, int], goal: Tuple[int, int],
                  max_distance: int = 100) -> Optional[List[Tuple[int, int]]]:
        """
        BFS로 경로 찾기

        Args:
            start: 시작 좌표 (x, y)
            goal: 목표 좌표 (x, y)
            max_distance: 최대 탐색 거리

        Returns:
            경로 리스트 [(x1,y1), (x2,y2), ...] 또는 None (경로 없음)
        """
        if start == goal:
            return [start]

        queue = deque([(start, 0)])
        visited = {start}
        came_from = {start: None}
        max_iterations = 10000
        iterations = 0

        while queue:
            iterations += 1
            if iterations > max_iterations:
                logger.warning(f"[BFS] 최대 반복 초과 ({max_iterations}): {start} → {goal}")
                return None

            (x, y), depth = queue.popleft()

            if (x, y) == goal:
                # came_from 역추적으로 경로 복원
                path = []
                cur = (x, y)
                while cur is not None:
                    path.append(cur)
                    cur = came_from[cur]
                return path[::-1]

            if depth > max_distance:
                continue

            # 4방향 탐색
            for direction, (dx, dy) in DIRECTIONS_4.items():
                nx, ny = x + dx, y + dy

                if (nx, ny) in visited:
                    continue

                # 막힌 타일이면 스킵
                if not self.obstacle_map.is_passable(nx, ny):
                    continue

                visited.add((nx, ny))
                came_from[(nx, ny)] = (x, y)
                queue.append(((nx, ny), depth + 1))

        return None  # 경로 없음

    def get_next_direction(self, current: Tuple[int, int]) -> Optional[str]:
        """현재 위치에서 다음 이동 방향 반환"""
        if not self.current_path or self.path_index >= len(self.current_path):
            return None

        next_pos = self.current_path[self.path_index]
        dx = next_pos[0] - current[0]
        dy = next_pos[1] - current[1]

        for direction, (ddx, ddy) in DIRECTIONS_4.items():
            if ddx == dx and ddy == dy:
                return direction

        return None

    def advance(self):
        """경로에서 다음 위치로 이동"""
        self.path_index += 1

    def is_path_complete(self) -> bool:
        """경로 이동 완료 여부"""
        return self.path_index >= len(self.current_path)

    def set_path(self, path: List[Tuple[int, int]]):
        """새 경로 설정"""
        self.current_path = path
        self.path_index = 1  # 0은 시작점이므로 1부터

    def get_path_length(self) -> int:
        """현재 경로 길이"""
        return len(self.current_path)

    def clear(self):
        """경로 초기화"""
        self.current_path = []
        self.path_index = 0


@dataclass
class OscillationDetector:
    """
    진동 패턴 감지기

    최근 위치 히스토리를 분석하여 A-B-A-B 같은 진동 패턴을 감지합니다.
    """
    history_size: int = 10
    oscillation_threshold: int = 3  # 같은 위치 반복 횟수

    # 내부 상태
    position_history: deque = field(default_factory=lambda: deque(maxlen=10))
    direction_history: deque = field(default_factory=lambda: deque(maxlen=10))

    def __post_init__(self):
        self.position_history = deque(maxlen=self.history_size)
        self.direction_history = deque(maxlen=self.history_size)

    def add_position(self, x: int, y: int, direction: Optional[str] = None):
        """위치 기록 추가"""
        self.position_history.append((x, y))
        if direction:
            self.direction_history.append(direction)

    def detect_oscillation(self) -> bool:
        """
        진동 패턴 감지

        패턴 1: A-B-A-B 반복 (2개 위치 사이 왔다갔다)
        패턴 2: 같은 위치 3회 이상 방문
        패턴 3: up-down-up-down 방향 진동

        Returns:
            bool: 진동 감지 여부
        """
        if len(self.position_history) < 4:
            return False

        # 패턴 1: A-B-A-B 반복 감지
        positions = list(self.position_history)
        if len(positions) >= 4:
            # 마지막 4개 위치가 A-B-A-B 패턴인지 확인
            if (positions[-4] == positions[-2] and
                positions[-3] == positions[-1] and
                positions[-4] != positions[-3]):
                logger.debug(f"[진동감지] A-B-A-B 패턴: {positions[-4]} ↔ {positions[-3]}")
                return True

        # 패턴 2: 같은 위치 반복 방문
        if len(positions) >= self.oscillation_threshold:
            recent = positions[-self.oscillation_threshold:]
            position_counts = {}
            for pos in recent:
                position_counts[pos] = position_counts.get(pos, 0) + 1
                if position_counts[pos] >= self.oscillation_threshold:
                    logger.debug(f"[진동감지] 위치 {pos} 반복 방문 {position_counts[pos]}회")
                    return True

        # 패턴 3: 방향 진동 (up-down-up-down 또는 left-right-left-right)
        if len(self.direction_history) >= 4:
            directions = list(self.direction_history)[-4:]
            # 수직 진동
            if all(d in ["up", "down"] for d in directions):
                if directions[0] != directions[1] and directions[1] != directions[2] and directions[2] != directions[3]:
                    logger.debug(f"[진동감지] 수직 방향 진동: {directions}")
                    return True
            # 수평 진동
            if all(d in ["left", "right"] for d in directions):
                if directions[0] != directions[1] and directions[1] != directions[2] and directions[2] != directions[3]:
                    logger.debug(f"[진동감지] 수평 방향 진동: {directions}")
                    return True

        return False

    def clear(self):
        """히스토리 초기화"""
        self.position_history.clear()
        self.direction_history.clear()


@dataclass
class DynamicDetourDistance:
    """
    동적 우회 거리 조정기

    우회 실패 시 거리를 증가시키고, 성공 시 초기화합니다.
    """
    base_distance: int = 2
    max_multiplier: int = 4  # 최대 base × 4

    # 내부 상태
    current_distance: int = 2
    failure_count: int = 0
    success_streak: int = 0

    def __post_init__(self):
        self.current_distance = self.base_distance

    def on_failure(self):
        """우회 실패 시 호출"""
        self.failure_count += 1
        self.success_streak = 0
        # 실패할 때마다 +1칸 (최대 base × 3)
        new_distance = min(self.base_distance + self.failure_count,
                          self.base_distance * 3)
        if new_distance != self.current_distance:
            self.current_distance = new_distance
            logger.info(f"[우회거리] 실패로 증가: {self.current_distance}칸")

    def on_oscillation(self):
        """진동 감지 시 호출"""
        self.failure_count += 2  # 진동은 더 심각
        self.success_streak = 0
        # 진동 시 +2칸 (최대 base × 4)
        new_distance = min(self.current_distance + 2,
                          self.base_distance * self.max_multiplier)
        if new_distance != self.current_distance:
            self.current_distance = new_distance
            logger.info(f"[우회거리] 진동으로 증가: {self.current_distance}칸")

    def on_success(self):
        """이동 성공 시 호출"""
        self.success_streak += 1
        # 3회 연속 성공 시 초기화
        if self.success_streak >= 3:
            self.failure_count = 0
            self.current_distance = self.base_distance
            self.success_streak = 0
            logger.debug(f"[우회거리] 연속 성공, 초기화: {self.current_distance}칸")

    def get_distance(self) -> int:
        """현재 우회 거리 반환"""
        return self.current_distance

    def reset(self):
        """완전 초기화"""
        self.current_distance = self.base_distance
        self.failure_count = 0
        self.success_streak = 0


@dataclass
class DirectionMemory:
    """
    위치별 방향 기록 관리자

    각 위치에서 시도했던 방향과 그 결과를 기록합니다.
    시간 기반 decay로 오래된 기록은 자동 삭제됩니다.
    """
    decay_count: int = 20  # 이 횟수 이후 기록 삭제

    # 내부 상태: {(x, y): {"tried": [방향들], "blocked": [막힌방향들], "age": 나이}}
    memory: Dict[Tuple[int, int], Dict] = field(default_factory=dict)
    tick: int = 0

    def add_tried(self, x: int, y: int, direction: str):
        """시도한 방향 기록"""
        key = (x, y)
        if key not in self.memory:
            self.memory[key] = {"tried": [], "blocked": [], "age": self.tick}
        if direction not in self.memory[key]["tried"]:
            self.memory[key]["tried"].append(direction)
        self.memory[key]["age"] = self.tick

    def add_blocked(self, x: int, y: int, direction: str):
        """막힌 방향 기록"""
        key = (x, y)
        if key not in self.memory:
            self.memory[key] = {"tried": [], "blocked": [], "age": self.tick}
        if direction not in self.memory[key]["blocked"]:
            self.memory[key]["blocked"].append(direction)
        self.memory[key]["age"] = self.tick

    def get_blocked(self, x: int, y: int) -> List[str]:
        """해당 위치에서 막힌 방향 목록 반환"""
        key = (x, y)
        if key in self.memory:
            return self.memory[key]["blocked"].copy()
        return []

    def get_tried(self, x: int, y: int) -> List[str]:
        """해당 위치에서 시도한 방향 목록 반환"""
        key = (x, y)
        if key in self.memory:
            return self.memory[key]["tried"].copy()
        return []

    def tick_and_decay(self):
        """틱 증가 및 오래된 기록 삭제"""
        self.tick += 1
        # decay_count 이상 지난 기록 삭제
        old_keys = [k for k, v in self.memory.items()
                   if self.tick - v["age"] > self.decay_count]
        for key in old_keys:
            del self.memory[key]
            logger.debug(f"[방향메모리] decay: {key}")

    def clear(self):
        """전체 초기화"""
        self.memory.clear()
        self.tick = 0

    def clear_position(self, x: int, y: int):
        """특정 위치 기록 삭제"""
        key = (x, y)
        if key in self.memory:
            del self.memory[key]


@dataclass
class WallFollowingState:
    """Wall Following 상태 데이터 - Sweep 패턴 + 좌표 추적"""
    active: bool = False
    max_steps: int = 100

    start_position: Optional[Tuple[int, int]] = None
    target_direction: Optional[str] = None  # 원래 가고 싶었던 방향
    sweep_direction: Optional[str] = None   # 주 이동 방향
    secondary_direction: Optional[str] = None  # 보조 방향

    steps_taken: int = 0
    probe_interval: int = 3
    last_probe_step: int = 0

    visited_positions: set = field(default_factory=set)
    position_visit_count: Dict[Tuple[int, int], int] = field(default_factory=dict)
    exit_reason: Optional[str] = None

    sweep_blocked_count: int = 0

    # 좌표 추적 - 진행 상황 모니터링
    last_position: Optional[Tuple[int, int]] = None
    best_distance: float = float('inf')  # 목표까지 가장 가까웠던 거리
    steps_without_progress: int = 0  # 진행 없는 스텝 수
    sweep_flip_count: int = 0  # sweep 방향 전환 횟수


class WallFollower:
    """
    Sweep 패턴 우회 알고리즘

    핵심: 한 방향으로 쭉 가면서, 막히면 한 칸 비껴간 후 다시 같은 방향 시도
    예: 목표가 위쪽인데 막혔으면
        1. 왼쪽으로 쭉 이동
        2. 왼쪽 막히면 → 아래로 한 칸 → 다시 왼쪽 시도
        3. 주기적으로 위쪽(목표) 시도
        4. 위쪽 열리면 위쪽으로 이동
    """

    def __init__(self):
        self.state = WallFollowingState()
        self._blocked_directions: set = set()
        self._last_was_secondary: bool = False  # 직전에 보조방향으로 이동했는지

    def start(self, cx: int, cy: int, tx: int, ty: int,
              blocked_direction: str, blocked_dirs: set = None) -> Optional[str]:
        """
        Sweep 우회 시작

        blocked_direction: 막힌 방향 (= 목표 방향)
        목표 위치에 따라 sweep 방향 결정
        """
        self.state = WallFollowingState()
        self.state.active = True
        self.state.start_position = (cx, cy)
        self.state.last_position = (cx, cy)
        self.state.target_direction = blocked_direction
        self.state.visited_positions = {(cx, cy)}
        self.state.position_visit_count = {(cx, cy): 1}

        # 초기 거리 계산
        self.state.best_distance = abs(tx - cx) + abs(ty - cy)

        self._blocked_directions = blocked_dirs.copy() if blocked_dirs else {blocked_direction}
        self._last_was_secondary = False

        dx = tx - cx
        dy = ty - cy

        # Sweep 방향 결정: 목표 방향에 수직인 방향 중 목표에 가까운 쪽
        if blocked_direction in ["up", "down"]:
            # 수직 막힘 → 좌우로 sweep
            if dx <= 0:
                self.state.sweep_direction = "left"  # 왼쪽으로 쭉
            else:
                self.state.sweep_direction = "right"  # 오른쪽으로 쭉
            # 보조 방향: 목표 반대쪽 (막혔을 때 비껴가기)
            self.state.secondary_direction = "down" if blocked_direction == "up" else "up"
        else:
            # 수평 막힘 → 상하로 sweep
            if dy <= 0:
                self.state.sweep_direction = "up"  # 위로 쭉
            else:
                self.state.sweep_direction = "down"  # 아래로 쭉
            # 보조 방향
            self.state.secondary_direction = "right" if blocked_direction == "left" else "left"

        first_dir = self.state.sweep_direction

        # sweep 방향이 막혀있으면 보조 방향으로 시작
        if first_dir in self._blocked_directions:
            first_dir = self.state.secondary_direction

        logger.info(f"[WallFollow] START pos=({cx},{cy}) target_dir={blocked_direction} "
                    f"sweep={self.state.sweep_direction} secondary={self.state.secondary_direction}")

        return first_dir

    def _get_next_direction(self) -> Optional[str]:
        """
        다음 이동 방향 계산 - Sweep 패턴

        우선순위:
        1. 주기적으로 목표 방향 시도 (probe)
        2. sweep 방향으로 쭉
        3. sweep 막히면 → 보조 방향으로 한 칸 (비껴가기)
        4. 보조도 막히면 → 반대 보조 방향
        5. 모두 막히면 → sweep 반대 방향
        """
        target = self.state.target_direction
        sweep = self.state.sweep_direction
        secondary = self.state.secondary_direction

        # 1. 주기적으로 목표 방향 시도 (probe_interval 스텝마다)
        if (self.state.steps_taken > 0 and
            self.state.steps_taken - self.state.last_probe_step >= self.state.probe_interval):
            if target and target not in self._blocked_directions:
                self.state.last_probe_step = self.state.steps_taken
                logger.debug(f"[WallFollow] Probe target: {target}")
                return target

        # 2. 직전에 보조방향으로 이동했으면 → 다시 sweep 방향 시도
        if self._last_was_secondary:
            self._last_was_secondary = False
            if sweep not in self._blocked_directions:
                return sweep

        # 3. sweep 방향으로 쭉
        if sweep not in self._blocked_directions:
            self.state.sweep_blocked_count = 0
            return sweep

        # 4. sweep 막힘 → 보조 방향으로 한 칸 비껴가기
        self.state.sweep_blocked_count += 1

        if secondary not in self._blocked_directions:
            self._last_was_secondary = True
            logger.debug(f"[WallFollow] Sweep blocked, sidestep: {secondary}")
            return secondary

        # 5. 보조도 막힘 → 반대 보조 방향
        opposite_secondary = OPPOSITE_DIRECTION[secondary]
        if opposite_secondary not in self._blocked_directions:
            self._last_was_secondary = True
            return opposite_secondary

        # 6. sweep 방향 전환 (연속 5번 이상 막히면)
        if self.state.sweep_blocked_count >= 5:
            opposite_sweep = OPPOSITE_DIRECTION[sweep]
            if opposite_sweep not in self._blocked_directions:
                self.state.sweep_direction = opposite_sweep
                self.state.sweep_blocked_count = 0
                logger.info(f"[WallFollow] Sweep direction flip: {sweep} → {opposite_sweep}")
                return opposite_sweep

        # 7. 목표 방향 시도 (마지막 수단)
        if target not in self._blocked_directions:
            return target

        # 8. 모두 막힘
        self.state.exit_reason = "all_blocked"
        self.state.active = False
        logger.warning("[WallFollow] EXIT reason=all_blocked")
        return None

    def _calc_distance(self, x1: int, y1: int, x2: int, y2: int) -> float:
        """맨해튼 거리 계산"""
        return abs(x2 - x1) + abs(y2 - y1)

    def update(self, cx: int, cy: int, tx: int, ty: int,
               moved: bool, blocked_dirs: set = None) -> Tuple[bool, Optional[str]]:
        """이동 후 상태 업데이트 - 좌표 추적 포함"""
        if not self.state.active:
            return False, None

        pos = (cx, cy)
        current_dist = self._calc_distance(cx, cy, tx, ty)

        if moved:
            # 이동 성공 - 막힌 방향 초기화
            self._blocked_directions.clear()
            self._last_was_secondary = False
            self.state.steps_taken += 1
            self.state.visited_positions.add(pos)
            self.state.position_visit_count[pos] = self.state.position_visit_count.get(pos, 0) + 1

            # === 좌표 추적: 목표에 가까워지고 있는지 확인 ===
            if current_dist < self.state.best_distance:
                # 진행 있음!
                self.state.best_distance = current_dist
                self.state.steps_without_progress = 0
                logger.debug(f"[WallFollow] Progress! dist={current_dist} pos=({cx},{cy})")
            else:
                self.state.steps_without_progress += 1

            # 8스텝 동안 진행 없으면 전략 변경
            if self.state.steps_without_progress >= 8:
                self._change_strategy(cx, cy, tx, ty)

            # 같은 위치 3회 이상 방문 - 막다른 골목
            if self.state.position_visit_count.get(pos, 0) >= 3:
                logger.warning(f"[WallFollow] Same position {pos} visited 3+ times, changing strategy")
                self._change_strategy(cx, cy, tx, ty)

            self.state.last_position = pos

            # 종료 조건 체크
            exit_reason = self._check_exit(cx, cy, tx, ty)
            if exit_reason:
                self.state.exit_reason = exit_reason
                self.state.active = False
                logger.info(f"[WallFollow] EXIT reason={exit_reason} steps={self.state.steps_taken}")
                return False, None
        else:
            # 이동 실패
            self.state.steps_without_progress += 1

        next_dir = self._get_next_direction()
        if next_dir:
            logger.debug(f"[WallFollow] Step {self.state.steps_taken}: pos=({cx},{cy}) next={next_dir} dist={current_dist}")

        return self.state.active, next_dir

    def _change_strategy(self, cx: int, cy: int, tx: int, ty: int):
        """전략 변경 - 막다른 골목 탈출"""
        self.state.steps_without_progress = 0
        self.state.sweep_flip_count += 1

        old_sweep = self.state.sweep_direction
        old_secondary = self.state.secondary_direction

        if self.state.sweep_flip_count <= 2:
            # 1-2회: sweep 방향만 전환
            self.state.sweep_direction = OPPOSITE_DIRECTION[old_sweep]
            logger.info(f"[WallFollow] Strategy change #{self.state.sweep_flip_count}: "
                       f"sweep {old_sweep}→{self.state.sweep_direction}")
        else:
            # 3회 이상: secondary 방향도 전환 (더 크게 우회)
            self.state.sweep_direction = OPPOSITE_DIRECTION[old_sweep]
            self.state.secondary_direction = OPPOSITE_DIRECTION[old_secondary]
            logger.info(f"[WallFollow] Strategy change #{self.state.sweep_flip_count}: "
                       f"sweep {old_sweep}→{self.state.sweep_direction}, "
                       f"secondary {old_secondary}→{self.state.secondary_direction}")

        # 방문 기록 일부 초기화 (새로운 경로 탐색 허용)
        self.state.position_visit_count.clear()
        self._blocked_directions.clear()

    def _check_exit(self, cx: int, cy: int, tx: int, ty: int) -> Optional[str]:
        """종료 조건 체크"""
        pos = (cx, cy)

        # 도착
        if cx == tx and cy == ty:
            return "arrived"

        # 타임아웃
        if self.state.steps_taken >= self.state.max_steps:
            return "timeout"

        # 루프 감지 (같은 위치 8회 이상 방문)
        if self.state.position_visit_count.get(pos, 0) >= 8:
            return "loop_detected"

        return None

    def stop(self):
        """중지"""
        if self.state.active:
            logger.info(f"[WallFollow] STOP steps={self.state.steps_taken}")
        self.state.active = False

    def is_active(self) -> bool:
        return self.state.active


class ObstacleAvoidanceController:
    """
    8방향 지능형 장애물 회피 컨트롤러

    진동 감지, 동적 우회 거리, 위치별 방향 기록을 통합 관리합니다.
    """

    def __init__(self,
                 base_detour_distance: int = 2,
                 oscillation_threshold: int = 3,
                 max_detour_distance: int = 8,
                 diagonal_enabled: bool = True,
                 escape_skill_config: Optional[Dict] = None):
        """
        Args:
            base_detour_distance: 기본 우회 거리 (칸)
            oscillation_threshold: 진동 판정 반복 횟수
            max_detour_distance: 우회 거리 최대값
            diagonal_enabled: 대각선 이동 허용 여부
            escape_skill_config: 탈출 스킬 설정 딕셔너리
        """
        self.diagonal_enabled = diagonal_enabled
        self.max_detour_distance = max_detour_distance

        self.oscillation_detector = OscillationDetector(
            oscillation_threshold=oscillation_threshold
        )
        self.detour_distance = DynamicDetourDistance(
            base_distance=base_detour_distance,
            max_multiplier=max(4, max_detour_distance // base_detour_distance)
        )
        self.direction_memory = DirectionMemory()

        # 탈출 스킬 관리자
        if escape_skill_config:
            self.escape_manager = EscapeSkillManager(
                enabled=escape_skill_config.get("enabled", False),
                skill_key=escape_skill_config.get("skill_key", "z"),
                cooldown=escape_skill_config.get("cooldown", 10.0),
                stuck_threshold=escape_skill_config.get("stuck_threshold", 10),
                direction_count=escape_skill_config.get("direction_count", 5),
                wait_after=escape_skill_config.get("wait_after", 0.5),
            )
        else:
            self.escape_manager = EscapeSkillManager()

        # 현재 상태
        self.detour_mode = False
        self.detour_direction: Optional[str] = None
        self.detour_steps_remaining = 0
        self.detour_start_pos: Optional[Tuple[int, int]] = None
        self.last_main_direction: Optional[str] = None
        self.forced_escape_direction: Optional[str] = None  # 진동 탈출용 강제 방향

        # BFS 경로 탐색 (WallFollower 대체)
        self.obstacle_map = ObstacleMap()
        self.pathfinder = BFSPathfinder(self.obstacle_map)
        self.pathfind_mode = False  # BFS 경로 따라 이동 중
        self.detour_consecutive_failures = 0  # 연속 detour 실패 횟수
        self.pathfind_trigger_threshold = 2  # N회 연속 실패 시 BFS 경로 탐색 시작

        # 하위 호환성 (기존 코드가 wall_follow_mode 참조할 수 있음)
        self.wall_follow_mode = False

    def get_directions(self) -> Dict[str, Tuple[int, int]]:
        """사용 가능한 방향 반환"""
        if self.diagonal_enabled:
            return DIRECTIONS_8
        return DIRECTIONS_4

    def select_detour_direction(self,
                                current_x: int,
                                current_y: int,
                                target_x: int,
                                target_y: int,
                                blocked_direction: Optional[str] = None) -> Optional[str]:
        """
        최적 우회 방향 선택

        우선순위:
        1. 목표 방향과 수직인 대각선 (대각선 활성화 시)
        2. 목표 방향과 수직인 직선
        3. 목표에 가까워지는 대각선
        4. 나머지 방향 (후퇴 제외)

        Args:
            current_x, current_y: 현재 좌표
            target_x, target_y: 목표 좌표
            blocked_direction: 막힌 방향 (이 방향은 제외)

        Returns:
            선택된 방향 문자열 또는 None
        """
        dx = target_x - current_x
        dy = target_y - current_y

        # 이미 막힌 것으로 기록된 방향들
        blocked_dirs = self.direction_memory.get_blocked(current_x, current_y)
        if blocked_direction and blocked_direction not in blocked_dirs:
            blocked_dirs.append(blocked_direction)

        # 반대 방향 (후퇴)도 피함
        retreat_dir = OPPOSITE_DIRECTION.get(blocked_direction) if blocked_direction else None

        directions = self.get_directions()
        candidates = []

        for dir_name, (ddx, ddy) in directions.items():
            # 막힌 방향 제외
            if dir_name in blocked_dirs:
                continue
            # 정반대 방향 (후퇴) 제외 - 마지막 수단으로만
            if dir_name == retreat_dir:
                continue

            # 점수 계산
            score = self._calculate_direction_score(
                dx, dy, ddx, ddy, dir_name, blocked_direction
            )
            candidates.append((dir_name, score))

        if not candidates:
            # 후퇴 방향도 포함해서 재시도
            for dir_name, (ddx, ddy) in directions.items():
                if dir_name in blocked_dirs:
                    continue
                score = self._calculate_direction_score(
                    dx, dy, ddx, ddy, dir_name, blocked_direction
                )
                candidates.append((dir_name, score))

        if not candidates:
            logger.warning("[회피] 선택 가능한 방향 없음")
            return None

        # 점수 높은 순 정렬
        candidates.sort(key=lambda x: x[1], reverse=True)

        selected = candidates[0][0]
        logger.info(f"[회피] 우회 방향 선택: {selected} (점수: {candidates[0][1]:.2f})")
        logger.debug(f"[회피] 후보들: {[(d, f'{s:.2f}') for d, s in candidates[:4]]}")

        return selected

    def _calculate_direction_score(self,
                                   dx: int, dy: int,
                                   ddx: int, ddy: int,
                                   dir_name: str,
                                   blocked_direction: Optional[str]) -> float:
        """
        방향 점수 계산

        점수 = 목표방향 내적 + 수직보너스 + 대각선보너스

        Args:
            dx, dy: 목표까지의 벡터
            ddx, ddy: 이동 방향 벡터
            dir_name: 방향 이름
            blocked_direction: 막힌 방향

        Returns:
            점수 (높을수록 좋음)
        """
        # 목표 방향 벡터 정규화
        target_mag = math.sqrt(dx * dx + dy * dy) if (dx != 0 or dy != 0) else 1
        norm_dx = dx / target_mag if target_mag > 0 else 0
        norm_dy = dy / target_mag if target_mag > 0 else 0

        # 이동 방향 정규화
        move_mag = math.sqrt(ddx * ddx + ddy * ddy)
        norm_ddx = ddx / move_mag
        norm_ddy = ddy / move_mag

        # 1. 목표 방향 내적 (목표에 가까워지면 +, 멀어지면 -)
        dot_product = norm_dx * norm_ddx + norm_dy * norm_ddy
        score = dot_product * 0.5  # 가중치

        # 2. 수직 보너스 (막힌 방향에 수직이면 가산)
        if blocked_direction:
            blocked_vec = DIRECTIONS_8.get(blocked_direction, (0, 0))
            if blocked_vec != (0, 0):
                # 수직 = 내적이 0에 가까움
                blocked_mag = math.sqrt(blocked_vec[0]**2 + blocked_vec[1]**2)
                blocked_norm = (blocked_vec[0] / blocked_mag, blocked_vec[1] / blocked_mag)
                perpendicular = abs(blocked_norm[0] * norm_ddx + blocked_norm[1] * norm_ddy)
                # 수직에 가까울수록 (perpendicular가 0에 가까울수록) 보너스
                score += (1 - perpendicular) * 0.3

        # 3. 대각선 보너스 (대각선은 수직+전진 동시에 가능)
        if "_" in dir_name:  # 대각선 방향
            score += 0.5

        # 4. 후퇴 페널티
        if blocked_direction and dir_name == OPPOSITE_DIRECTION.get(blocked_direction):
            score -= 1.0

        return score

    def start_detour(self,
                     current_x: int,
                     current_y: int,
                     target_x: int,
                     target_y: int,
                     blocked_direction: str) -> Optional[str]:
        """
        우회 모드 시작

        Args:
            current_x, current_y: 현재 좌표
            target_x, target_y: 목표 좌표
            blocked_direction: 막힌 방향

        Returns:
            우회 방향 또는 None
        """
        # BFS 경로 탐색 모드 중이면 경로 업데이트
        if self.pathfind_mode:
            return self._update_pathfinding(current_x, current_y, target_x, target_y, False, blocked_direction)

        # 막힌 방향 기록 (DirectionMemory와 ObstacleMap 모두에)
        self.direction_memory.add_blocked(current_x, current_y, blocked_direction)
        self.obstacle_map.mark_blocked(current_x, current_y, blocked_direction)

        # 연속 실패 체크 - BFS 경로 탐색 전환 조건
        if self.detour_consecutive_failures >= self.pathfind_trigger_threshold:
            logger.warning(f"[회피] Detour 연속 {self.detour_consecutive_failures}회 실패! BFS 경로 탐색 시작")
            bfs_dir = self._start_pathfinding(current_x, current_y, target_x, target_y, blocked_direction)
            if bfs_dir:
                return bfs_dir

        # 진동 감지 확인
        oscillation_detected = self.oscillation_detector.detect_oscillation()
        if oscillation_detected:
            logger.warning("[회피] 진동 감지! 강제 탈출 모드")
            self.detour_distance.on_oscillation()
            # 대각선 강제 탈출
            self.forced_escape_direction = self._get_escape_diagonal(
                current_x, current_y, target_x, target_y
            )
            if self.forced_escape_direction:
                self.detour_direction = self.forced_escape_direction
            else:
                self.detour_direction = self.select_detour_direction(
                    current_x, current_y, target_x, target_y, blocked_direction
                )
        else:
            self.detour_direction = self.select_detour_direction(
                current_x, current_y, target_x, target_y, blocked_direction
            )

        if not self.detour_direction:
            # Detour 방향 선택 실패
            self.detour_consecutive_failures += 1
            logger.warning(f"[회피] Detour 방향 없음 (실패 {self.detour_consecutive_failures}회)")

            # 진동 상태에서 모든 방향 막힘 → BFS 경로 탐색 시도
            if oscillation_detected:
                logger.warning("[회피] 진동 + 모든 방향 막힘 → BFS 경로 탐색 시도")
                self.direction_memory.clear_position(current_x, current_y)
                self.oscillation_detector.clear()
                # BFS 경로 탐색 시도
                bfs_dir = self._start_pathfinding(current_x, current_y, target_x, target_y, blocked_direction)
                if bfs_dir:
                    logger.info(f"[회피] BFS 경로 탐색 시작: {bfs_dir}")
                    return bfs_dir
                # BFS도 실패 → 수직 방향 강제 선택
                perpendicular = self._get_perpendicular_direction(blocked_direction)
                if perpendicular:
                    logger.info(f"[회피] 수직 방향 강제 이동: {perpendicular}")
                    self.detour_mode = True
                    self.detour_direction = perpendicular
                    self.detour_steps_remaining = self.detour_distance.get_distance()
                    self.detour_start_pos = (current_x, current_y)
                    return perpendicular
                return None

            # 일반 실패 → BFS 경로 탐색 시도
            if self.detour_consecutive_failures >= self.pathfind_trigger_threshold:
                bfs_dir = self._start_pathfinding(current_x, current_y, target_x, target_y, blocked_direction)
                if bfs_dir:
                    return bfs_dir

            return None

        self.detour_mode = True
        self.detour_steps_remaining = self.detour_distance.get_distance()
        self.detour_start_pos = (current_x, current_y)

        # 시도한 방향 기록
        self.direction_memory.add_tried(current_x, current_y, self.detour_direction)

        logger.info(f"[회피] 우회 시작: {self.detour_direction} × {self.detour_steps_remaining}칸")

        return self.detour_direction

    def _start_pathfinding(self, cx: int, cy: int, tx: int, ty: int,
                           blocked_direction: str) -> Optional[str]:
        """
        BFS 경로 탐색 시작

        Args:
            cx, cy: 현재 좌표
            tx, ty: 목표 좌표
            blocked_direction: 막힌 방향

        Returns:
            다음 이동 방향 또는 None
        """
        # 막힌 타일 기록
        self.obstacle_map.mark_blocked(cx, cy, blocked_direction)

        # BFS로 경로 찾기
        path = self.pathfinder.find_path((cx, cy), (tx, ty))

        if path and len(path) > 1:
            self.pathfinder.set_path(path)
            self.pathfind_mode = True
            self.wall_follow_mode = True  # 하위 호환성
            next_dir = self.pathfinder.get_next_direction((cx, cy))
            self.detour_direction = next_dir
            self.detour_consecutive_failures = 0
            logger.info(f"[BFS] 경로 발견: {len(path)}칸, 첫 방향: {next_dir}")
            logger.info(f"[BFS] 경로: {path[:10]}{'...' if len(path) > 10 else ''}")
            return next_dir
        else:
            self.pathfind_mode = False
            self.wall_follow_mode = False  # 하위 호환성
            logger.warning(f"[BFS] 경로 없음! 장애물 {self.obstacle_map.get_obstacle_count()}개 발견됨")
            return None

    def _update_pathfinding(self, cx: int, cy: int, tx: int, ty: int,
                           moved: bool, blocked_direction: Optional[str] = None) -> Optional[str]:
        """
        BFS 경로 따라 이동 업데이트

        Args:
            cx, cy: 현재 좌표
            tx, ty: 목표 좌표
            moved: 이동 성공 여부
            blocked_direction: 막힌 방향 (이동 실패 시)

        Returns:
            다음 이동 방향 또는 None
        """
        if moved:
            self.obstacle_map.mark_passable(cx, cy)
            self.pathfinder.advance()

            if self.pathfinder.is_path_complete():
                self.pathfind_mode = False
                self.wall_follow_mode = False  # 하위 호환성
                self.detour_consecutive_failures = 0
                logger.info("[BFS] 경로 이동 완료")
                return None

            next_dir = self.pathfinder.get_next_direction((cx, cy))
            self.detour_direction = next_dir
            logger.debug(f"[BFS] 경로 따라 이동: {next_dir}")
            return next_dir
        else:
            # 이동 실패 - 경로 재계산
            if blocked_direction:
                self.obstacle_map.mark_blocked(cx, cy, blocked_direction)
                logger.info(f"[BFS] 이동 실패, 장애물 추가: ({cx},{cy}) -> {blocked_direction}")

            path = self.pathfinder.find_path((cx, cy), (tx, ty))
            if path and len(path) > 1:
                self.pathfinder.set_path(path)
                next_dir = self.pathfinder.get_next_direction((cx, cy))
                self.detour_direction = next_dir
                logger.info(f"[BFS] 경로 재계산: {len(path)}칸, 방향: {next_dir}")
                return next_dir
            else:
                self.pathfind_mode = False
                self.wall_follow_mode = False  # 하위 호환성
                logger.warning("[BFS] 경로 재계산 실패")
                return None

    def _get_escape_diagonal(self,
                             current_x: int,
                             current_y: int,
                             target_x: int,
                             target_y: int) -> Optional[str]:
        """진동 탈출용 대각선 방향 선택"""
        if not self.diagonal_enabled:
            return None

        dx = target_x - current_x
        dy = target_y - current_y
        blocked = self.direction_memory.get_blocked(current_x, current_y)

        # 목표 방향에 가장 가까운 대각선 중 막히지 않은 것
        diagonals = ["up_right", "up_left", "down_right", "down_left"]
        best = None
        best_score = -999

        for diag in diagonals:
            if diag in blocked:
                continue
            ddx, ddy = DIRECTIONS_8[diag]
            # 목표 방향과의 일치도 (축 정렬 시 중립 점수)
            score = 0
            if dx != 0:
                score += 1 if (dx > 0) == (ddx > 0) else -1
            if dy != 0:
                score += 1 if (dy > 0) == (ddy > 0) else -1
            if score > best_score:
                best_score = score
                best = diag

        return best

    def _get_perpendicular_direction(self, blocked_direction: str) -> Optional[str]:
        """막힌 방향에 수직인 방향 반환"""
        perpendicular_map = {
            "right": ["up", "down"],
            "left": ["up", "down"],
            "up": ["left", "right"],
            "down": ["left", "right"],
        }
        options = perpendicular_map.get(blocked_direction, [])
        if options:
            # 첫 번째 수직 방향 반환 (4방향 모드용)
            return options[0]
        return None

    def update_detour(self,
                      current_x: int,
                      current_y: int,
                      prev_x: int,
                      prev_y: int,
                      target_x: int = 0,
                      target_y: int = 0) -> Tuple[bool, Optional[str]]:
        """
        우회 상태 업데이트

        Args:
            current_x, current_y: 현재 좌표
            prev_x, prev_y: 이전 좌표
            target_x, target_y: 목표 좌표 (BFS 경로 탐색용)

        Returns:
            (우회 중 여부, 이동 방향)
        """
        # BFS 경로 탐색 모드 처리
        if self.pathfind_mode:
            moved = (current_x != prev_x) or (current_y != prev_y)
            # 이전에 시도한 방향 (이동 실패 시 막힌 방향으로 기록)
            last_tried = self.detour_direction
            next_dir = self._update_pathfinding(current_x, current_y, target_x, target_y, moved, last_tried)
            if next_dir:
                self.detour_direction = next_dir  # 다음에 시도할 방향 저장
                return True, next_dir
            return False, None

        if not self.detour_mode:
            return False, None

        # 이동 성공 확인
        moved = (current_x != prev_x) or (current_y != prev_y)

        if moved:
            self.detour_steps_remaining -= 1
            logger.debug(f"[회피] 우회 이동 성공, 남은 거리: {self.detour_steps_remaining}")

            if self.detour_steps_remaining <= 0:
                # 우회 완료
                self._complete_detour(current_x, current_y, success=True)
                return False, None
        else:
            # 우회 중에도 막힘 - 우회 실패
            logger.warning(f"[회피] 우회 '{self.detour_direction}'도 막힘!")
            self.direction_memory.add_blocked(
                self.detour_start_pos[0] if self.detour_start_pos else current_x,
                self.detour_start_pos[1] if self.detour_start_pos else current_y,
                self.detour_direction
            )
            self._complete_detour(current_x, current_y, success=False)
            return False, None

        return True, self.detour_direction

    def _complete_detour(self, current_x: int, current_y: int, success: bool):
        """우회 완료 처리"""
        if success:
            logger.info("[회피] 우회 완료")
            # 성공 시 연속 실패 카운트 리셋
            self.detour_consecutive_failures = 0
        else:
            logger.warning("[회피] 우회 실패")
            self.detour_distance.on_failure()
            # 연속 실패 카운트 증가
            self.detour_consecutive_failures += 1

        self.detour_mode = False
        self.detour_direction = None
        self.detour_steps_remaining = 0
        self.detour_start_pos = None
        self.forced_escape_direction = None

    def get_main_direction(self,
                           current_x: int,
                           current_y: int,
                           target_x: int,
                           target_y: int,
                           current_axis: Optional[str] = None) -> Tuple[str, str]:
        """
        메인 이동 방향 결정

        Args:
            current_x, current_y: 현재 좌표
            target_x, target_y: 목표 좌표
            current_axis: 현재 이동 축 ("horizontal" 또는 "vertical")

        Returns:
            (방향 이름, 새 축)
        """
        dx = target_x - current_x
        dy = target_y - current_y

        # 도착
        if dx == 0 and dy == 0:
            return "arrived", current_axis or ""

        # 현재 축 유지 (진동 방지)
        new_axis = current_axis

        if current_axis == "horizontal":
            if dx == 0:  # X축 정렬됨 → Y축으로 전환
                new_axis = "vertical" if dy != 0 else None
        elif current_axis == "vertical":
            if dy == 0:  # Y축 정렬됨 → X축으로 전환
                new_axis = "horizontal" if dx != 0 else None

        # 축이 없으면 더 큰 차이 방향 선택
        if new_axis is None:
            abs_dx = abs(dx)
            abs_dy = abs(dy)

            # 거리 차이가 큰 축 우선 (1.5배 이상 차이나면 확실히 그 축 선택)
            if abs_dx > abs_dy * 1.5 and dx != 0:
                new_axis = "horizontal"
            elif abs_dy > abs_dx * 1.5 and dy != 0:
                new_axis = "vertical"
            # 비슷할 때는 거리가 큰 쪽 (엄격하게 비교)
            elif abs_dx > abs_dy and dx != 0:
                new_axis = "horizontal"
            elif abs_dy > abs_dx and dy != 0:
                new_axis = "vertical"
            # 완전히 같을 때
            elif dy != 0:
                new_axis = "vertical"  # Y축 우선 (기존과 반대)
            elif dx != 0:
                new_axis = "horizontal"

        # 방향 결정
        if new_axis == "horizontal":
            direction = "right" if dx > 0 else "left"
        elif new_axis == "vertical":
            direction = "down" if dy > 0 else "up"
        else:
            direction = "none"

        self.last_main_direction = direction
        return direction, new_axis

    def on_move_success(self):
        """메인 방향 이동 성공 시 호출"""
        self.detour_distance.on_success()
        # 탈출 스킬 정체 카운트 리셋
        self.escape_manager.consecutive_stuck_count = 0

    def is_completely_stuck(self, current_x: int, current_y: int) -> bool:
        """모든 방향이 막혔는지 확인"""
        blocked = self.direction_memory.get_blocked(current_x, current_y)
        directions = self.get_directions()
        return len(blocked) >= len(directions)

    def check_escape_needed(self, current_x: int, current_y: int, moved: bool) -> bool:
        """탈출 스킬 필요 여부 확인"""
        all_blocked = self.is_completely_stuck(current_x, current_y)
        return self.escape_manager.check_escape_needed(moved, all_blocked)

    def on_escape_skill_used(self):
        """탈출 스킬 사용 후 호출"""
        self.escape_manager.on_skill_used()
        # 막힘 기록 초기화 (새 위치로 이동했으므로)
        self.direction_memory.clear()
        self.oscillation_detector.clear()
        self.detour_mode = False
        self.detour_direction = None

    def record_position(self, x: int, y: int, direction: Optional[str] = None):
        """위치 및 방향 기록 (진동 감지용)"""
        # DirectionMemory 하드 캡: 1000개 초과 시 가장 오래된 500개 강제 제거
        if len(self.direction_memory.memory) > 1000:
            sorted_keys = sorted(self.direction_memory.memory.keys(),
                                 key=lambda k: self.direction_memory.memory[k]["age"])
            for key in sorted_keys[:500]:
                del self.direction_memory.memory[key]
            logger.debug(f"[방향메모리] 하드 캡 적용: {len(self.direction_memory.memory)}개로 축소")

        self.oscillation_detector.add_position(x, y, direction)
        self.direction_memory.tick_and_decay()

    def clear_blocked_at_position(self, x: int, y: int):
        """특정 위치의 막힘 기록 초기화 (장애물 통과 성공 시)"""
        self.direction_memory.clear_position(x, y)

    def reset(self):
        """전체 상태 초기화"""
        self.oscillation_detector.clear()
        self.detour_distance.reset()
        self.direction_memory.clear()
        self.detour_mode = False
        self.detour_direction = None
        self.detour_steps_remaining = 0
        self.detour_start_pos = None
        self.last_main_direction = None
        self.forced_escape_direction = None
        # BFS 경로 탐색 상태 초기화
        self.obstacle_map.clear()
        self.pathfinder.clear()
        self.pathfind_mode = False
        self.wall_follow_mode = False  # 하위 호환성
        self.detour_consecutive_failures = 0

    def get_obstacle_map(self) -> ObstacleMap:
        """장애물 맵 반환 (디버깅용)"""
        return self.obstacle_map

    def is_pathfind_mode(self) -> bool:
        """BFS 경로 탐색 모드 여부"""
        return self.pathfind_mode


def get_diagonal_keys(direction: str, move_keys: Dict[str, str]) -> List[str]:
    """
    대각선 방향을 위한 키 조합 반환

    Args:
        direction: 방향 이름 (예: "up_right")
        move_keys: 방향 → 키 매핑

    Returns:
        동시에 눌러야 할 키 리스트
    """
    if "_" not in direction:
        # 단일 방향
        return [move_keys.get(direction, direction)]

    # 대각선: "up_right" → ["up", "right"]
    parts = direction.split("_")
    keys = []
    for part in parts:
        key = move_keys.get(part, part)
        keys.append(key)
    return keys


def is_diagonal(direction: str) -> bool:
    """대각선 방향인지 확인"""
    return "_" in direction


@dataclass
class EscapeSkillManager:
    """
    탈출 스킬 관리자

    모든 방향이 막히거나 연속 정체 시 텔레포트 스킬을 자동 사용합니다.
    """
    enabled: bool = False
    skill_key: str = "z"
    cooldown: float = 10.0
    stuck_threshold: int = 10  # 연속 정체 횟수
    direction_count: int = 5   # 방향키 입력 횟수
    wait_after: float = 0.5    # 스킬 후 대기

    # 내부 상태
    last_use_time: float = 0.0
    consecutive_stuck_count: int = 0

    def is_cooldown_ready(self) -> bool:
        """쿨타임 준비 완료 여부"""
        import time
        return (time.time() - self.last_use_time) >= self.cooldown

    def get_cooldown_remaining(self) -> float:
        """남은 쿨타임 (초)"""
        import time
        remaining = self.cooldown - (time.time() - self.last_use_time)
        return max(0.0, remaining)

    def check_escape_needed(self, moved: bool, all_blocked: bool = False) -> bool:
        """
        탈출 필요 여부 판단

        Args:
            moved: 이번 프레임에 이동했는지
            all_blocked: 모든 방향이 막혔는지

        Returns:
            bool: 탈출 스킬 사용해야 하는지
        """
        if not self.enabled:
            return False

        if not self.is_cooldown_ready():
            return False

        # 조건 1: 모든 방향 막힘
        if all_blocked:
            logger.info("[탈출스킬] 모든 방향 막힘 - 탈출 필요")
            return True

        # 조건 2: 연속 정체
        if moved:
            self.consecutive_stuck_count = 0
        else:
            self.consecutive_stuck_count += 1
            if self.consecutive_stuck_count >= self.stuck_threshold:
                logger.info(f"[탈출스킬] 연속 정체 {self.consecutive_stuck_count}회 - 탈출 필요")
                return True

        return False

    def on_skill_used(self):
        """스킬 사용 후 상태 리셋"""
        import time
        self.last_use_time = time.time()
        self.consecutive_stuck_count = 0
        logger.info(f"[탈출스킬] 스킬 사용 완료, 쿨타임 {self.cooldown}초 시작")

    def reset(self):
        """상태 초기화 (쿨타임 유지)"""
        self.consecutive_stuck_count = 0
