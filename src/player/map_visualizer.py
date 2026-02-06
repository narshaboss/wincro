"""
좌표 기반 맵핑 시스템 - 텍스트 맵 시각화 (단순화)

맵 데이터를 텍스트로 출력합니다.
"""

import logging
import unicodedata
from typing import List, Optional, Tuple, Set

try:
    from .game_map import GameMap, DIRECTIONS_4
except ImportError:
    from game_map import GameMap, DIRECTIONS_4

logger = logging.getLogger(__name__)


def _display_width(s: str) -> int:
    """문자열의 실제 표시 폭 계산 (전각 문자 = 2칸)"""
    w = 0
    for ch in s:
        eaw = unicodedata.east_asian_width(ch)
        w += 2 if eaw in ('W', 'F') else 1
    return w


def _ljust_wide(s: str, width: int) -> str:
    """전각 문자 폭을 고려한 ljust"""
    pad = width - _display_width(s)
    return s + " " * max(0, pad)


class GameStyleVisualizer:
    """
    게임 스타일 텍스트 맵 시각화

    ○ = 이동 가능
    ■ = 장애물
    ☆ = 현재 위치
    ◎ = 목표
    """

    def __init__(self, game_map: GameMap):
        self.game_map = game_map

    def render(self, player_pos: Optional[Tuple[int, int]] = None,
               target_pos: Optional[Tuple[int, int]] = None,
               path: Optional[List[Tuple[int, int]]] = None,
               padding: int = 1,
               title: str = "MAP") -> str:
        """텍스트 맵 렌더링"""
        bounds = self.game_map.get_bounds()

        if not self.game_map.passable and not self.game_map.blocked:
            return self._empty_map(title)

        min_x = bounds["min_x"] - padding
        max_x = bounds["max_x"] + padding
        min_y = bounds["min_y"] - padding
        max_y = bounds["max_y"] + padding

        path_set: Set[Tuple[int, int]] = set(path) if path else set()

        width = (max_x - min_x + 1) * 2 + 10
        lines = []

        # 상단
        lines.append("╔" + "═" * (width - 2) + "╗")
        lines.append("║" + _ljust_wide(f" 🗺️ {title}", width - 2) + "║")
        lines.append("╠" + "═" * (width - 2) + "╣")

        # 맵 본체
        for y in range(min_y, max_y + 1):
            line = "║ "
            for x in range(min_x, max_x + 1):
                pos = (x, y)

                if player_pos and pos == player_pos:
                    char = "☆"
                elif target_pos and pos == target_pos:
                    char = "◎"
                elif pos in path_set:
                    char = "◇"
                elif pos in self.game_map.blocked:
                    char = "■"
                elif pos in self.game_map.soft_blocked:
                    char = "▒"
                elif pos in self.game_map.passable:
                    char = "○"
                else:
                    char = "·"

                line += char + " "

            line = _ljust_wide(line, width - 1) + "║"
            lines.append(line)

        # 범례
        lines.append("╠" + "═" * (width - 2) + "╣")
        legend = "║ ☆현재 ◎목표 ◇경로 ○이동가능 ■벽 ▒임시벽 ·미탐색"
        lines.append(_ljust_wide(legend, width - 1) + "║")

        # 정보
        lines.append("╠" + "═" * (width - 2) + "╣")
        stats = self.game_map.get_statistics()

        if player_pos:
            info = f"║ 현재: x{player_pos[0]}y{player_pos[1]}"
        else:
            info = "║ 현재: -"

        if target_pos:
            info += f"  목표: x{target_pos[0]}y{target_pos[1]}"
            if player_pos:
                dist = abs(target_pos[0] - player_pos[0]) + abs(target_pos[1] - player_pos[1])
                info += f"  거리: {dist}칸"

        lines.append(_ljust_wide(info, width - 1) + "║")
        soft_count = stats.get('soft_blocked_tiles', 0)
        stat_line = f"║ 탐색: {stats['passable_tiles']}칸  벽: {stats['blocked_tiles']}개"
        if soft_count > 0:
            stat_line += f"  임시벽: {soft_count}개"
        lines.append(_ljust_wide(stat_line, width - 1) + "║")
        lines.append("╚" + "═" * (width - 2) + "╝")

        return "\n".join(lines)

    def _empty_map(self, title: str) -> str:
        header = "║ 🗺️ " + title
        header = _ljust_wide(header, 37) + "║"
        return f"""╔════════════════════════════════════╗
{header}
╠════════════════════════════════════╣
║                                    ║
║        맵 데이터가 없습니다        ║
║                                    ║
╚════════════════════════════════════╝"""


class CompactVisualizer:
    """컴팩트 맵 시각화"""

    def __init__(self, game_map: GameMap):
        self.game_map = game_map

    def render(self, player_pos: Optional[Tuple[int, int]] = None,
               target_pos: Optional[Tuple[int, int]] = None,
               path: Optional[List[Tuple[int, int]]] = None,
               padding: int = 1) -> str:
        """컴팩트 렌더링"""
        bounds = self.game_map.get_bounds()

        if not self.game_map.passable and not self.game_map.blocked:
            return "[맵 데이터 없음]"

        min_x = bounds["min_x"] - padding
        max_x = bounds["max_x"] + padding
        min_y = bounds["min_y"] - padding
        max_y = bounds["max_y"] + padding

        path_set: Set[Tuple[int, int]] = set(path) if path else set()
        lines = []

        for y in range(min_y, max_y + 1):
            line = ""
            for x in range(min_x, max_x + 1):
                pos = (x, y)

                if player_pos and pos == player_pos:
                    char = "☆"
                elif target_pos and pos == target_pos:
                    char = "◎"
                elif pos in path_set:
                    char = "◇"
                elif pos in self.game_map.blocked:
                    char = "■"
                elif pos in self.game_map.soft_blocked:
                    char = "▒"
                elif pos in self.game_map.passable:
                    char = "○"
                else:
                    char = "·"

                line += char + " "

            lines.append(line.rstrip())

        return "\n".join(lines)


# 호환성
MapVisualizer = GameStyleVisualizer
