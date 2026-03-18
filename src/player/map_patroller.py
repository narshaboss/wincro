from __future__ import annotations

import logging
from typing import List, Optional, Set, Tuple

try:
    from .game_map import GameMap
except ImportError:
    from game_map import GameMap

logger = logging.getLogger(__name__)


class MapPatroller:
    """Visit registered patrol points in their stored order."""

    def __init__(self, game_map: GameMap):
        self.game_map = game_map
        self._visited: Set[Tuple[int, int]] = set()
        self._current_target: Optional[Tuple[int, int]] = None
        self._current_index: int = 0
        self._is_completed: bool = False

    def start(self, current_pos: Tuple[int, int]):
        """Start patrol from the first registered patrol point."""
        self._visited.clear()
        self._current_target = None
        self._is_completed = False
        self._current_index = 0
        count = len(self.game_map.patrol_points)
        logger.info(
            f"[Patroller] 순찰 시작: {count}개 순찰 좌표 (인덱스 0부터, 현재위치={current_pos})"
        )

    def reset(self, current_pos: Tuple[int, int]):
        """Reset patrol progress and restart from the beginning."""
        self._visited.clear()
        self._current_target = None
        self._is_completed = False
        self._current_index = 0
        logger.info("[Patroller] 순찰 리셋 (재순찰, 인덱스 0부터)")

    def get_next_target(self, current_pos: Tuple[int, int]) -> Optional[Tuple[int, int]]:
        """Return the current patrol target, advancing only on exact arrival."""
        patrol_points: List[Tuple[int, int]] = self.game_map.patrol_points
        if not patrol_points:
            return None

        # If one full lap has completed, keep the patroller parked until the
        # caller decides whether to advance to the next segment or reset patrol.
        if self._is_completed:
            return None

        if self._current_target is not None:
            dist = abs(current_pos[0] - self._current_target[0]) + abs(current_pos[1] - self._current_target[1])
            if dist == 0:
                self._visited.add(self._current_target)
                logger.info(
                    f"[Patroller] 순찰 좌표 도달: {self._current_target} ({len(self._visited)}/{len(patrol_points)})"
                )
                self._current_target = None
                self._current_index += 1

        if self._current_target is not None:
            return self._current_target

        next_target = self._find_next_in_order()
        if next_target is None:
            if self._visited:
                self._is_completed = True
                self._current_target = None
                logger.info(f"[Patroller] 전체 순찰 완료 ({len(self._visited)}개)")
            return None

        self._current_target = next_target
        return self._current_target

    def _find_next_in_order(self) -> Optional[Tuple[int, int]]:
        """Find the next unvisited patrol point in stored order."""
        patrol_points = self.game_map.patrol_points
        total = len(patrol_points)

        for i in range(self._current_index, total):
            pos = patrol_points[i]
            if pos not in self._visited:
                self._current_index = i
                return pos

        for i in range(0, self._current_index):
            pos = patrol_points[i]
            if pos not in self._visited:
                self._current_index = i
                return pos

        return None

    def skip_current_target(self):
        """Skip the current patrol target and move on to the next."""
        if self._current_target is not None:
            self._visited.add(self._current_target)
            logger.info(f"[Patroller] 순찰 좌표 스킵 (도달 불가): {self._current_target}")
            self._current_target = None
            self._current_index += 1

    @property
    def is_completed(self) -> bool:
        """Whether one full patrol lap has completed."""
        return self._is_completed

    @property
    def current_target(self) -> Optional[Tuple[int, int]]:
        """Current patrol target."""
        return self._current_target

    def get_progress(self) -> dict:
        """Return patrol progress summary."""
        total = len(self.game_map.patrol_points)
        visited = len(self._visited)
        return {
            "visited": visited,
            "total": total,
            "percent": round(visited / total * 100, 1) if total > 0 else 0,
            "remaining": total - visited,
            "current_target": self._current_target,
        }
