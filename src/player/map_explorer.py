"""
좌표 기반 맵핑 시스템 - DFS 자동 탐색

미탐색 영역을 자동으로 탐색하여 맵을 완성합니다.
"""

import logging
from enum import Enum
from typing import Callable, List, Optional, Tuple

try:
    from .game_map import GameMap, DIRECTIONS_4, OPPOSITE_DIRECTION
except ImportError:
    from game_map import GameMap, DIRECTIONS_4, OPPOSITE_DIRECTION

logger = logging.getLogger(__name__)


class ExplorationState(Enum):
    """탐색 상태"""
    IDLE = "idle"           # 대기 중
    EXPLORING = "exploring"  # 탐색 중
    BACKTRACKING = "backtracking"  # 백트래킹 중
    COMPLETED = "completed"  # 탐색 완료


class MapExplorer:
    """
    DFS 기반 자동 맵 탐색기

    미탐색 방향을 찾아 이동하고, 막다른 길에서는 백트래킹합니다.
    GameMap의 passable/blocked/soft_blocked sets 기반 API를 사용합니다.
    """

    def __init__(self, game_map: GameMap):
        """
        Args:
            game_map: 맵 데이터
        """
        self.game_map = game_map

        # 탐색 상태
        self._state = ExplorationState.IDLE
        self._stack: List[Tuple[int, int]] = []  # 백트래킹용 스택
        self._visited: set = set()  # 방문한 좌표
        self._explored_dirs: dict = {}  # {(x,y): set(시도한 방향들)}
        self._current_direction: Optional[str] = None

        # 콜백
        self._on_tile_explored: Optional[Callable[[int, int], None]] = None
        self._on_exploration_complete: Optional[Callable[[], None]] = None

    def set_callbacks(self,
                      on_tile_explored: Callable[[int, int], None] = None,
                      on_exploration_complete: Callable[[], None] = None):
        """
        콜백 설정

        Args:
            on_tile_explored: 타일 탐색 완료 시 (x, y)
            on_exploration_complete: 전체 탐색 완료 시
        """
        self._on_tile_explored = on_tile_explored
        self._on_exploration_complete = on_exploration_complete

    def start_exploration(self, start_x: int, start_y: int):
        """
        탐색 시작

        Args:
            start_x, start_y: 시작 좌표
        """
        self._state = ExplorationState.EXPLORING
        self._stack = [(start_x, start_y)]
        self._visited = {(start_x, start_y)}
        self._explored_dirs = {}
        self._current_direction = None

        # 시작 위치를 이동 가능으로 등록
        self.game_map.mark_passable(start_x, start_y)

        logger.info(f"[Explorer] 탐색 시작: ({start_x}, {start_y})")

    def stop_exploration(self):
        """탐색 중지"""
        self._state = ExplorationState.IDLE
        logger.info("[Explorer] 탐색 중지")

    def is_exploring(self) -> bool:
        """탐색 중인지"""
        return self._state in (ExplorationState.EXPLORING, ExplorationState.BACKTRACKING)

    def is_completed(self) -> bool:
        """탐색 완료인지"""
        return self._state == ExplorationState.COMPLETED

    def get_state(self) -> ExplorationState:
        """현재 상태"""
        return self._state

    def _get_unexplored_directions(self, x: int, y: int) -> List[str]:
        """
        현재 위치에서 아직 시도하지 않은 방향 목록 반환

        blocked인 방향은 제외, 이미 시도한 방향도 제외
        """
        pos = (x, y)
        tried = self._explored_dirs.get(pos, set())
        unexplored = []

        for direction, (dx, dy) in DIRECTIONS_4.items():
            if direction in tried:
                continue
            nx, ny = x + dx, y + dy
            # 영구벽이면 시도 불필요
            if self.game_map.is_blocked(nx, ny):
                tried.add(direction)
                continue
            unexplored.append(direction)

        self._explored_dirs[pos] = tried
        return unexplored

    def exploration_step(self, current_x: int, current_y: int,
                        prev_x: int, prev_y: int) -> Optional[str]:
        """
        탐색 한 스텝 실행

        메인 루프에서 반복 호출합니다.
        이동 결과를 맵에 기록하고 다음 방향을 반환합니다.

        Args:
            current_x, current_y: 현재 좌표
            prev_x, prev_y: 이전 좌표 (이동 전)

        Returns:
            다음 이동 방향 또는 None (완료/대기)
        """
        if self._state == ExplorationState.IDLE:
            return None

        if self._state == ExplorationState.COMPLETED:
            return None

        current_pos = (current_x, current_y)

        # 이동 결과 기록
        if self._current_direction is not None:
            moved = (current_x != prev_x) or (current_y != prev_y)

            if moved:
                # 이동 성공 → 현재 위치를 이동 가능으로 기록
                self.game_map.mark_passable(current_x, current_y)

                if current_pos not in self._visited:
                    self._visited.add(current_pos)
                    self._stack.append(current_pos)
                    logger.debug(f"[Explorer] 이동 성공: ({prev_x},{prev_y}) -> ({current_x},{current_y})")
            else:
                # 이동 실패 → 해당 방향 벽으로 기록
                dx, dy = DIRECTIONS_4.get(self._current_direction, (0, 0))
                wall_x = prev_x + dx
                wall_y = prev_y + dy
                self.game_map.mark_soft_blocked(wall_x, wall_y)
                logger.debug(f"[Explorer] 이동 실패: ({prev_x},{prev_y}) {self._current_direction} → ({wall_x},{wall_y}) 임시벽")

        # 현재 위치에서 미탐색 방향 찾기
        unexplored = self._get_unexplored_directions(current_x, current_y)

        if unexplored:
            # 미탐색 방향이 있음 - 탐색 계속
            self._state = ExplorationState.EXPLORING
            direction = unexplored[0]
            self._current_direction = direction

            # 시도 기록
            pos = (current_x, current_y)
            tried = self._explored_dirs.get(pos, set())
            tried.add(direction)
            self._explored_dirs[pos] = tried

            logger.debug(f"[Explorer] ({current_x},{current_y}) 미탐색 방향: {direction}")
            return direction

        # 현재 타일 탐색 완료
        if self._on_tile_explored:
            self._on_tile_explored(current_x, current_y)

        # 백트래킹 필요
        return self._backtrack(current_x, current_y)

    def _backtrack(self, current_x: int, current_y: int) -> Optional[str]:
        """
        백트래킹

        스택에서 미탐색 방향이 있는 타일을 찾아 이동합니다.

        Returns:
            이동 방향 또는 None (탐색 완료)
        """
        self._state = ExplorationState.BACKTRACKING
        max_iterations = 200
        iterations = 0

        while self._stack:
            iterations += 1
            if iterations > max_iterations:
                logger.warning(f"[Explorer] 백트래킹 최대 반복 초과 ({max_iterations}), 스택 초기화")
                self._stack.clear()
                return None

            # 현재 위치가 스택 맨 위와 같으면 pop
            if self._stack[-1] == (current_x, current_y):
                self._stack.pop()
                if not self._stack:
                    break
                continue

            # 이전 위치로 돌아가기
            target = self._stack[-1]
            direction = self._get_direction_to(current_x, current_y, target[0], target[1])

            if direction:
                # 이동 가능한 경로인지 확인 (passable이거나 unknown)
                tx, ty = target
                if not self.game_map.is_blocked(tx, ty) and not self.game_map.is_soft_blocked(tx, ty):
                    self._current_direction = direction
                    logger.debug(f"[Explorer] 백트래킹: ({current_x},{current_y}) -> {target}")
                    return direction

            # 직접 이동 불가 - 스택에서 제거하고 계속
            self._stack.pop()

        # 스택이 비었음 - 탐색 완료
        self._complete_exploration()
        return None

    def _get_direction_to(self, from_x: int, from_y: int,
                          to_x: int, to_y: int) -> Optional[str]:
        """두 좌표 사이의 방향 반환 (인접한 경우만)"""
        dx = to_x - from_x
        dy = to_y - from_y

        for direction, (ddx, ddy) in DIRECTIONS_4.items():
            if ddx == dx and ddy == dy:
                return direction

        return None

    def _complete_exploration(self):
        """탐색 완료 처리"""
        self._state = ExplorationState.COMPLETED
        self._current_direction = None

        stats = self.game_map.get_statistics()
        logger.info(f"[Explorer] 탐색 완료: {stats['total_tiles']}개 타일, "
                   f"이동가능 {stats['passable_tiles']}개, 벽 {stats['blocked_tiles']}개")

        if self._on_exploration_complete:
            self._on_exploration_complete()

    def get_exploration_progress(self) -> dict:
        """탐색 진행 상황"""
        stats = self.game_map.get_statistics()

        return {
            "state": self._state.value,
            "visited_count": len(self._visited),
            "stack_depth": len(self._stack),
            "total_tiles": stats["total_tiles"],
            "passable_tiles": stats["passable_tiles"],
            "blocked_tiles": stats["blocked_tiles"],
            "current_direction": self._current_direction,
        }

    def reset(self):
        """상태 초기화"""
        self._state = ExplorationState.IDLE
        self._stack = []
        self._visited = set()
        self._explored_dirs = {}
        self._current_direction = None
