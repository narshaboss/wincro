"""
좌표 기반 맵핑 시스템 - A* 경로 탐색

맵 데이터를 기반으로 최단 경로를 찾습니다.
"""

import heapq
import logging
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

try:
    from .game_map import GameMap, DIRECTIONS_4
except ImportError:
    from game_map import GameMap, DIRECTIONS_4

logger = logging.getLogger(__name__)


@dataclass
class PathResult:
    """경로 탐색 결과"""
    found: bool  # 경로 발견 여부
    path: List[Tuple[int, int]]  # 경로 좌표 리스트
    directions: List[str]  # 이동 방향 리스트
    cost: int  # 총 비용 (이동 횟수)

    @property
    def length(self) -> int:
        """경로 길이"""
        return len(self.path)

    def get_next_position(self, current_index: int) -> Optional[Tuple[int, int]]:
        """다음 위치 반환"""
        if current_index + 1 < len(self.path):
            return self.path[current_index + 1]
        return None

    def get_next_direction(self, current_index: int) -> Optional[str]:
        """다음 방향 반환"""
        if current_index < len(self.directions):
            return self.directions[current_index]
        return None


class SimplePathfinder:
    """
    A* 알고리즘 기반 경로 탐색기

    GameMap의 walkability 정보를 사용하여 최단 경로를 찾습니다.
    """

    def __init__(self, game_map: GameMap):
        """
        Args:
            game_map: 맵 데이터
        """
        self.game_map = game_map

        # 현재 경로 상태
        self._current_path: Optional[PathResult] = None
        self._path_index: int = 0
        self._goal: Optional[Tuple[int, int]] = None
        self._path_options: Optional[Tuple[bool, bool]] = None

    def _heuristic(self, a: Tuple[int, int], b: Tuple[int, int]) -> int:
        """맨해튼 거리 휴리스틱"""
        return abs(a[0] - b[0]) + abs(a[1] - b[1])

    def find_path(self, start: Tuple[int, int], goal: Tuple[int, int],
                  allow_unknown: bool = False, stop_event=None,
                  max_iterations: int = 0,
                  allow_soft_blocked: bool = True,
                  unknown_cost: int = 1,
                  respect_blocked_edges: bool = False,
                  allow_avoid_goal: bool = True,
                  avoid_set: set = None) -> PathResult:
        """
        A* 알고리즘으로 경로 탐색 (came_from 역추적 방식)

        Args:
            start: 시작 좌표 (x, y)
            goal: 목표 좌표 (x, y)
            allow_unknown: 미탐색 영역 통과 허용 여부
            stop_event: threading.Event — set 시 즉시 중단

        Returns:
            PathResult 객체
        """
        if start == goal:
            return PathResult(
                found=True,
                path=[start],
                directions=[],
                cost=0
            )

        # 우선순위 큐: (f_cost, g_cost, position)
        open_set = [(self._heuristic(start, goal), 0, start)]
        heapq.heapify(open_set)

        # 각 노드의 최소 비용 (open + closed 통합)
        best_g: Dict[Tuple[int, int], int] = {start: 0}

        # 역추적용: {pos: (parent_pos, direction)}
        came_from: Dict[Tuple[int, int], Tuple[Tuple[int, int], str]] = {}

        _iter_count = 0
        while open_set:
            # 50회마다 중지 체크 (오버헤드 최소화)
            _iter_count += 1
            if _iter_count % 50 == 0:
                if stop_event and stop_event.is_set():
                    logger.debug("[Pathfinder] 중지 요청으로 탐색 중단")
                    return PathResult(found=False, path=[], directions=[], cost=-1)
                if max_iterations > 0 and _iter_count >= max_iterations:
                    logger.debug(f"[Pathfinder] 최대 반복 초과 ({max_iterations})")
                    return PathResult(found=False, path=[], directions=[], cost=-1)
            if _iter_count % 200 == 0:
                time.sleep(0)  # GIL 해제

            f_cost, g_cost, current = heapq.heappop(open_set)

            # 이미 더 좋은 경로로 처리된 경우 스킵
            if g_cost > best_g.get(current, float('inf')):
                continue

            # 목표 도달 → 역추적으로 경로 복원
            if current == goal:
                path = []
                directions = []
                node = current
                while node in came_from:
                    parent, direction = came_from[node]
                    path.append(node)
                    directions.append(direction)
                    node = parent
                path.append(start)
                path.reverse()
                directions.reverse()

                result = PathResult(
                    found=True,
                    path=path,
                    directions=directions,
                    cost=g_cost
                )
                logger.debug(f"[Pathfinder] 경로 발견: {len(path)}칸")
                return result

            # 인접 타일 탐색
            neighbors = self.game_map.get_walkable_neighbors(
                current[0], current[1], allow_unknown=allow_unknown,
                allow_soft_blocked=allow_soft_blocked,
                respect_blocked_edges=respect_blocked_edges,
            )

            # 목표가 인접하면 soft_blocked/unknown이어도 마지막 단계로 도달 허용
            # (단, 영구벽 is_blocked는 제외)
            if abs(current[0] - goal[0]) + abs(current[1] - goal[1]) == 1:
                if not self.game_map.is_blocked(goal[0], goal[1]):
                    if not any(nx == goal[0] and ny == goal[1] for nx, ny, _ in neighbors):
                        for d_name, (dx, dy) in DIRECTIONS_4.items():
                            if current[0] + dx == goal[0] and current[1] + dy == goal[1]:
                                neighbors.append((goal[0], goal[1], d_name))
                                break

            for nx, ny, direction in neighbors:
                next_pos = (nx, ny)
                if avoid_set and next_pos in avoid_set and (not allow_avoid_goal or next_pos != goal):
                    continue  # 회피 대상 건너뜀 (목표 자체는 허용)
                move_cost = self.game_map.get_soft_blocked_cost(nx, ny, unknown_cost=unknown_cost)
                new_cost = g_cost + move_cost

                # 더 좋은 경로일 때만 갱신
                if new_cost < best_g.get(next_pos, float('inf')):
                    best_g[next_pos] = new_cost
                    came_from[next_pos] = (current, direction)
                    h_cost = self._heuristic(next_pos, goal)
                    new_f_cost = new_cost + h_cost
                    heapq.heappush(open_set, (
                        new_f_cost,
                        new_cost,
                        next_pos,
                    ))

        # 경로 없음
        logger.debug(f"[Pathfinder] 경로 없음: {start} -> {goal}")
        return PathResult(
            found=False,
            path=[],
            directions=[],
            cost=-1
        )

    def set_goal(self, goal: Tuple[int, int]):
        """목표 설정 (변경 시에만 경로 초기화)"""
        if goal != self._goal:
            self._goal = goal
            self._current_path = None
            self._path_index = 0
            self._path_options = None

    def invalidate_path(self):
        """경로 무효화 (벽 발견 시 재계산 유도)"""
        self._current_path = None
        self._path_index = 0
        self._path_options = None

    def get_next_direction(self, current: Tuple[int, int],
                           allow_unknown: bool = False,
                           stop_event=None,
                           max_iterations: int = 5000,
                           respect_blocked_edges: bool = False) -> Optional[str]:
        """
        현재 위치에서 다음 이동 방향 반환

        경로가 없거나 현재 위치가 경로에서 벗어나면 재계산합니다.
        캐시된 경로가 유효하면 재사용합니다.

        Args:
            current: 현재 좌표
            allow_unknown: 미탐색 영역 통과 허용 여부
            stop_event: threading.Event — set 시 즉시 중단
            max_iterations: 최대 A* 반복 횟수

        Returns:
            방향 문자열 또는 None (도착/경로 없음)
        """
        if self._goal is None:
            return None

        # 도착 확인
        if current == self._goal:
            return None

        # 경로 재계산 필요 여부 확인
        need_recalculate = False
        desired_options = (bool(allow_unknown), bool(respect_blocked_edges))

        if self._current_path is None:
            need_recalculate = True
        elif not self._current_path.found:
            need_recalculate = True
        elif self._path_options != desired_options:
            need_recalculate = True
        elif self._path_index >= len(self._current_path.path):
            need_recalculate = True
        elif current not in self._current_path.path:
            # 현재 위치가 경로에 없음 (예: 장애물 발견으로 이탈)
            need_recalculate = True
        else:
            # 현재 위치가 경로에 있지만 인덱스가 맞지 않을 수 있음
            # list.index()는 첫 번째 발견만 반환 → 중복 좌표 시 오류
            # 현재 인덱스부터 앞으로 탐색 (가장 흔한 케이스: 전진)
            path = self._current_path.path
            if self._path_index < len(path) and path[self._path_index] == current:
                pass  # 이미 올바른 인덱스
            else:
                path_pos = None
                for i in range(self._path_index, len(path)):
                    if path[i] == current:
                        path_pos = i
                        break
                if path_pos is None:
                    # 뒤로 이동한 경우 (드물지만 가능)
                    for i in range(self._path_index - 1, -1, -1):
                        if path[i] == current:
                            path_pos = i
                            break
                if path_pos is not None:
                    self._path_index = path_pos
                else:
                    need_recalculate = True

        if need_recalculate:
            self._current_path = self.find_path(
                current, self._goal, allow_unknown,
                stop_event=stop_event, max_iterations=max_iterations,
                respect_blocked_edges=respect_blocked_edges)
            self._path_index = 0
            self._path_options = desired_options

            if not self._current_path.found:
                logger.warning(f"[Pathfinder] 경로 없음: {current} -> {self._goal}")
                return None

        # 다음 방향 반환
        direction = self._current_path.get_next_direction(self._path_index)
        return direction

    def advance(self):
        """경로에서 다음 위치로 이동 (인덱스 증가)"""
        self._path_index += 1

    def recalculate_path(self, current: Tuple[int, int], goal: Tuple[int, int],
                         allow_unknown: bool = False,
                         respect_blocked_edges: bool = False) -> PathResult:
        """
        경로 재계산

        장애물 발견 시 호출하여 새 경로를 찾습니다.

        Args:
            current: 현재 좌표
            goal: 목표 좌표
            allow_unknown: 미탐색 영역 통과 허용 여부

        Returns:
            PathResult 객체
        """
        self._goal = goal
        self._current_path = self.find_path(
            current, goal, allow_unknown, max_iterations=20000,
            respect_blocked_edges=respect_blocked_edges)
        self._path_index = 0
        self._path_options = (bool(allow_unknown), bool(respect_blocked_edges))

        if self._current_path.found:
            logger.info(f"[Pathfinder] 경로 재계산: {len(self._current_path.path)}칸")
        else:
            logger.warning(f"[Pathfinder] 경로 재계산 실패: {current} -> {goal}")

        return self._current_path

    def get_current_path(self) -> Optional[PathResult]:
        """현재 경로 반환"""
        return self._current_path

    def get_remaining_path(self) -> List[Tuple[int, int]]:
        """남은 경로 좌표 반환"""
        if self._current_path is None or not self._current_path.found:
            return []
        return self._current_path.path[self._path_index:]

    def get_remaining_directions(self) -> List[str]:
        """남은 방향 리스트 반환"""
        if self._current_path is None or not self._current_path.found:
            return []
        return self._current_path.directions[self._path_index:]

    def is_path_valid(self) -> bool:
        """현재 경로가 유효한지 확인"""
        return self._current_path is not None and self._current_path.found

    def is_at_goal(self, current: Tuple[int, int]) -> bool:
        """목표 도달 여부"""
        return self._goal is not None and current == self._goal

    def clear(self):
        """상태 초기화"""
        self._current_path = None
        self._path_index = 0
        self._goal = None
        self._path_options = None

    def get_state_info(self) -> dict:
        """상태 정보 반환"""
        path_length = 0
        remaining = 0

        if self._current_path and self._current_path.found:
            path_length = len(self._current_path.path)
            remaining = max(0, path_length - self._path_index)

        return {
            "goal": self._goal,
            "path_length": path_length,
            "path_index": self._path_index,
            "remaining": remaining,
            "has_path": self._current_path is not None and self._current_path.found,
        }
