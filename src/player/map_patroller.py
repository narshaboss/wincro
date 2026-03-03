"""
맵 순찰 모듈 - 등록된 순찰 좌표 기반 순회

벽수정에서 등록한 순찰 좌표(검정 박스)를
등록 순서대로 방문합니다.
"""

import logging
from typing import Optional, Set, Tuple, List

try:
    from .game_map import GameMap
except ImportError:
    from game_map import GameMap

logger = logging.getLogger(__name__)


class MapPatroller:
    """
    등록된 순찰 좌표 기반 순회 클래스

    - GameMap.patrol_points에 등록된 좌표를 등록 순서대로 방문
    - 모든 좌표 방문 완료 시 리셋 후 재순찰
    """

    def __init__(self, game_map: GameMap):
        self.game_map = game_map
        self._visited: Set[Tuple[int, int]] = set()
        self._current_target: Optional[Tuple[int, int]] = None
        self._current_index: int = 0  # 등록 순서 인덱스
        self._is_completed: bool = False

    def start(self, current_pos: Tuple[int, int]):
        """순찰 시작 — 등록 순서대로 인덱스 0부터"""
        self._visited.clear()
        self._current_target = None
        self._is_completed = False
        self._current_index = 0
        count = len(self.game_map.patrol_points)
        logger.info(f"[Patroller] 순찰 시작: {count}개 순찰 좌표 (인덱스 0부터, 현재위치={current_pos})")

    def reset(self, current_pos: Tuple[int, int]):
        """방문 기록 리셋 후 재순찰 (등록 순서대로 인덱스 0부터)"""
        self._visited.clear()
        self._current_target = None
        self._is_completed = False
        self._current_index = 0
        logger.info(f"[Patroller] 순찰 리셋 (재순찰, 인덱스 0부터)")

    def get_next_target(self, current_pos: Tuple[int, int]) -> Optional[Tuple[int, int]]:
        """
        다음 순찰 목표 좌표 반환 (등록 순서대로)

        Returns:
            목표 좌표 또는 None (순찰 좌표 없음)
        """
        patrol_points = self.game_map.patrol_points
        if not patrol_points:
            return None

        # 현재 목표 도달 확인 (±1타일 허용)
        if self._current_target is not None:
            _dist = abs(current_pos[0] - self._current_target[0]) + abs(current_pos[1] - self._current_target[1])
            if _dist <= 1:
                self._visited.add(self._current_target)
                logger.info(f"[Patroller] 순찰 좌표 도달: {self._current_target} ({len(self._visited)}/{len(patrol_points)})")
                self._current_target = None
                self._current_index += 1

        # 현재 목표가 이미 있으면 유지
        if self._current_target is not None:
            return self._current_target

        # 등록 순서대로 다음 미방문 좌표
        next_target = self._find_next_in_order()

        if next_target is None:
            # 모든 좌표 방문 완료 → 리셋 (등록 순서대로 인덱스 0부터)
            if self._visited:
                self._is_completed = True
                logger.info(f"[Patroller] 전체 순찰 완료 ({len(self._visited)}개) → 재순찰")
                self._visited.clear()
                self._current_index = 0
                next_target = self._find_next_in_order()
            if next_target is None:
                return None

        self._current_target = next_target
        return self._current_target

    def _find_next_in_order(self) -> Optional[Tuple[int, int]]:
        """등록 순서대로 다음 미방문 순찰 좌표"""
        patrol_points = self.game_map.patrol_points
        total = len(patrol_points)

        # 현재 인덱스부터 끝까지 순서대로 탐색
        for i in range(self._current_index, total):
            pos = patrol_points[i]
            if pos not in self._visited:
                self._current_index = i
                return pos

        # 래핑: 처음부터 현재 인덱스까지 탐색 (미방문 포인트 놓치지 않도록)
        for i in range(0, self._current_index):
            pos = patrol_points[i]
            if pos not in self._visited:
                self._current_index = i
                return pos

        return None

    def skip_current_target(self):
        """현재 목표를 도달 불가로 처리하고 다음으로 넘김"""
        if self._current_target is not None:
            self._visited.add(self._current_target)
            logger.info(f"[Patroller] 순찰 좌표 스킵 (도달 불가): {self._current_target}")
            self._current_target = None
            self._current_index += 1

    @property
    def is_completed(self) -> bool:
        """전체 순찰 완료 여부 (리셋 전)"""
        return self._is_completed

    @property
    def current_target(self) -> Optional[Tuple[int, int]]:
        """현재 순찰 목표"""
        return self._current_target

    def get_progress(self) -> dict:
        """순찰 진행 상황"""
        total = len(self.game_map.patrol_points)
        visited = len(self._visited)
        return {
            "visited": visited,
            "total": total,
            "percent": round(visited / total * 100, 1) if total > 0 else 0,
            "remaining": total - visited,
            "current_target": self._current_target,
        }
