"""
좌표 기반 맵핑 시스템 - 맵 데이터 구조 (단순화)

이동 가능한 좌표 vs 장애물 좌표를 기록합니다.
"""

import json
import logging
from typing import Dict, Optional, Tuple, List, Set
from pathlib import Path

logger = logging.getLogger(__name__)

# 4방향 정의 (dx, dy)
DIRECTIONS_4 = {
    "up": (0, -1),
    "down": (0, 1),
    "left": (-1, 0),
    "right": (1, 0),
}

# 방향 반대 매핑
OPPOSITE_DIRECTION = {
    "up": "down",
    "down": "up",
    "left": "right",
    "right": "left",
}


class GameMap:
    """
    게임 맵 데이터 (단순화)

    - passable: 이동 가능한 좌표들
    - blocked: 장애물 좌표들 (영구벽)
    - soft_blocked: 임시 장애물 좌표들 (몬스터 등, fail_count 기반)
    """

    SOFT_BLOCKED_PROMOTE_THRESHOLD = 5  # 이 횟수 이상 실패 시 영구벽 승격

    def __init__(self, name: str = "Unknown"):
        self.name = name
        self.passable: Set[Tuple[int, int]] = set()  # 이동 가능한 좌표
        self.blocked: Set[Tuple[int, int]] = set()   # 장애물 좌표 (영구)
        self.soft_blocked: Dict[Tuple[int, int], int] = {}  # 임시 장애물 {(x,y): fail_count}
        self.patrol_points: List[Tuple[int, int]] = []  # 순찰 좌표 (순서 유지)
        self.start_pos: Optional[Tuple[int, int]] = None  # 출발지
        self.end_pos: Optional[Tuple[int, int]] = None    # 도착지

    def mark_passable(self, x: int, y: int):
        """이동 가능한 좌표로 등록"""
        pos = (int(x), int(y))
        self.passable.add(pos)
        # 장애물에서 제거 (혹시 잘못 등록됐으면)
        self.blocked.discard(pos)
        # 임시 장애물에서도 제거
        self.soft_blocked.pop(pos, None)
        logger.debug(f"[Map] {pos} 이동 가능")

    def mark_blocked(self, x: int, y: int):
        """장애물 좌표로 등록 (영구벽)"""
        pos = (int(x), int(y))
        self.blocked.add(pos)
        # 이동 가능에서 제거
        self.passable.discard(pos)
        # 임시 장애물에서도 제거 (영구벽으로 승격됨)
        self.soft_blocked.pop(pos, None)
        logger.debug(f"[Map] {pos} 장애물!")

    def mark_soft_blocked(self, x: int, y: int):
        """임시 장애물로 등록 (몬스터 등). fail_count 누적, 임계값 초과 시 영구벽 승격"""
        pos = (int(x), int(y))
        # 이미 영구벽이면 무시
        if pos in self.blocked:
            return
        count = self.soft_blocked.get(pos, 0) + 1
        if count >= self.SOFT_BLOCKED_PROMOTE_THRESHOLD:
            # 영구벽으로 승격
            self.mark_blocked(x, y)
            logger.info(f"[Map] {pos} 임시→영구벽 승격 (실패 {count}회)")
        else:
            self.soft_blocked[pos] = count
            # passable에서 제거하지 않음 — soft_blocked은 임시이므로
            # 만료 시 passable 상태가 자동 복원됨 (A*는 soft_blocked을 별도 처리)
            logger.debug(f"[Map] {pos} 임시 장애물 (실패 {count}회)")

    def clear_soft_blocked(self, x: int, y: int):
        """임시 장애물 해제 (이동 성공 시)"""
        pos = (int(x), int(y))
        if pos in self.soft_blocked:
            del self.soft_blocked[pos]
            logger.debug(f"[Map] {pos} 임시 장애물 해제")

    def is_soft_blocked(self, x: int, y: int) -> bool:
        """임시 장애물인지 확인"""
        return (int(x), int(y)) in self.soft_blocked

    def get_soft_blocked_cost(self, x: int, y: int) -> int:
        """이동 비용 반환 (soft_blocked이면 50, 아니면 1)"""
        if (int(x), int(y)) in self.soft_blocked:
            return 50
        return 1

    def tick(self):
        """주기적 호출: 임시 장애물 fail_count 감소, 0 이하면 해제"""
        expired = []
        for pos, count in list(self.soft_blocked.items()):
            new_count = count - 1
            if new_count <= 0:
                expired.append(pos)
            else:
                self.soft_blocked[pos] = new_count
        for pos in expired:
            del self.soft_blocked[pos]
            logger.debug(f"[Map] {pos} 임시 장애물 만료")

    def mark_walkable(self, from_x: int, from_y: int, direction: str, walkable: bool):
        """
        특정 방향 이동 가능 여부 기록 (rule_executor 호환용)

        Args:
            from_x, from_y: 출발 좌표
            direction: 이동 방향
            walkable: True=이동 가능, False=막힘
        """
        # 출발 좌표는 항상 이동 가능
        self.mark_passable(from_x, from_y)

        # 목표 좌표 계산
        dx, dy = DIRECTIONS_4.get(direction, (0, 0))
        target_x = from_x + dx
        target_y = from_y + dy

        if walkable:
            self.mark_passable(target_x, target_y)
        else:
            self.mark_blocked(target_x, target_y)

    def is_blocked(self, x: int, y: int) -> bool:
        """장애물인지 확인"""
        return (int(x), int(y)) in self.blocked

    def is_passable(self, x: int, y: int) -> bool:
        """이동 가능한지 확인 (탐색된 좌표만)"""
        return (int(x), int(y)) in self.passable

    def is_known(self, x: int, y: int) -> bool:
        """탐색된 좌표인지"""
        pos = (int(x), int(y))
        return pos in self.passable or pos in self.blocked or pos in self.soft_blocked

    def record_move(self, from_x: int, from_y: int, direction: str,
                    to_x: int, to_y: int, success: bool):
        """
        이동 결과 기록

        Args:
            from_x, from_y: 출발 좌표
            direction: 이동 방향
            to_x, to_y: 도착 좌표
            success: 이동 성공 여부
        """
        # 출발 좌표는 무조건 이동 가능
        self.mark_passable(from_x, from_y)

        if success:
            # 이동 성공 → 도착 좌표도 이동 가능
            self.mark_passable(to_x, to_y)
        else:
            # 이동 실패 → 목표 좌표가 장애물
            dx, dy = DIRECTIONS_4.get(direction, (0, 0))
            target_x = from_x + dx
            target_y = from_y + dy
            self.mark_blocked(target_x, target_y)

    def get_neighbors(self, x: int, y: int, avoid_blocked: bool = True,
                     avoid_unknown: bool = False) -> List[Tuple[int, int, str]]:
        """
        인접 좌표 목록

        Returns:
            [(nx, ny, direction), ...]
        """
        neighbors = []
        for direction, (dx, dy) in DIRECTIONS_4.items():
            nx, ny = x + dx, y + dy

            # 장애물 피하기
            if avoid_blocked and self.is_blocked(nx, ny):
                continue

            # 미탐색 피하기
            if avoid_unknown and not self.is_known(nx, ny):
                continue

            neighbors.append((nx, ny, direction))

        return neighbors

    def get_walkable_neighbors(self, x: int, y: int,
                               allow_unknown: bool = False) -> List[Tuple[int, int, str]]:
        """
        이동 가능한 인접 좌표 (A* 경로탐색용)

        soft_blocked 타일은 통과 가능 (높은 비용으로 A*가 회피 시도)

        Args:
            x, y: 현재 좌표
            allow_unknown: True면 미탐색 영역도 이동 가능으로 간주

        Returns:
            [(nx, ny, direction), ...]
        """
        neighbors = []
        for direction, (dx, dy) in DIRECTIONS_4.items():
            nx, ny = int(x + dx), int(y + dy)

            # 영구벽은 무조건 제외
            if self.is_blocked(nx, ny):
                continue

            # 임시 장애물은 통과 허용 (비용이 높을 뿐)
            if self.is_soft_blocked(nx, ny):
                neighbors.append((nx, ny, direction))
                continue

            # 미탐색 영역 처리
            if not allow_unknown and not self.is_passable(nx, ny):
                continue

            neighbors.append((nx, ny, direction))

        return neighbors

    def get_bounds(self) -> dict:
        """맵 경계"""
        all_coords = self.passable | self.blocked | set(self.soft_blocked.keys())
        if not all_coords:
            return {"min_x": 0, "max_x": 0, "min_y": 0, "max_y": 0}

        xs = [p[0] for p in all_coords]
        ys = [p[1] for p in all_coords]
        return {
            "min_x": min(xs),
            "max_x": max(xs),
            "min_y": min(ys),
            "max_y": max(ys),
        }

    def get_statistics(self) -> dict:
        """맵 통계"""
        # soft_blocked이 passable과 겹칠 수 있으므로 중복 제거
        all_coords = self.passable | self.blocked | set(self.soft_blocked.keys())
        return {
            "name": self.name,
            "total_tiles": len(all_coords),
            "passable_tiles": len(self.passable),
            "blocked_tiles": len(self.blocked),
            "soft_blocked_tiles": len(self.soft_blocked),
            "explored_tiles": len(self.passable),  # 호환성
            "walkable_edges": len(self.passable),   # 호환성
            "blocked_edges": len(self.blocked),     # 호환성
            "bounds": self.get_bounds(),
        }

    def is_fully_explored(self) -> bool:
        """맵 경계가 완전히 폐쇄되었는지 확인.
        모든 passable 타일의 4방향 이웃이 known(passable/blocked/soft_blocked)이면 True."""
        if len(self.passable) < 10:
            return False
        for (x, y) in self.passable:
            for dx, dy in [(0, -1), (0, 1), (-1, 0), (1, 0)]:
                neighbor = (x + dx, y + dy)
                if neighbor not in self.passable and \
                   neighbor not in self.blocked and \
                   neighbor not in self.soft_blocked:
                    return False
        return True

    def save(self, filepath: str):
        """JSON 저장 (스레드 안전: 스냅샷 후 직렬화)"""
        # 스냅샷으로 복사 — 다른 스레드의 동시 수정으로 인한 RuntimeError 방지
        passable_snapshot = list(self.passable)
        blocked_snapshot = list(self.blocked)
        soft_blocked_snapshot = dict(self.soft_blocked)
        data = {
            "name": self.name,
            "passable": [list(p) for p in passable_snapshot],
            "blocked": [list(p) for p in blocked_snapshot],
            "soft_blocked": {f"{p[0]},{p[1]}": c for p, c in soft_blocked_snapshot.items()},
            "patrol_points": [list(p) for p in self.patrol_points],
            "start_pos": list(self.start_pos) if self.start_pos else None,
            "end_pos": list(self.end_pos) if self.end_pos else None,
        }

        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        logger.info(f"[Map] 저장: {filepath} (이동가능 {len(self.passable)}개, 장애물 {len(self.blocked)}개, 임시벽 {len(self.soft_blocked)}개)")

    def load(self, filepath: str) -> bool:
        """JSON 로드"""
        path = Path(filepath)
        if not path.exists():
            return False

        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)

            self.name = data.get("name", "Unknown")
            self.passable = set(tuple(p) for p in data.get("passable", []))
            self.blocked = set(tuple(p) for p in data.get("blocked", []))
            # soft_blocked 로드 (하위 호환: 없으면 빈 dict)
            raw_sb = data.get("soft_blocked", {})
            self.soft_blocked = {}
            for key, count in raw_sb.items():
                parts = key.split(",")
                pos = (int(parts[0]), int(parts[1]))
                self.soft_blocked[pos] = count
            # 순찰 좌표 로드 (하위 호환)
            self.patrol_points = [tuple(p) for p in data.get("patrol_points", [])]
            # 출발지/도착지 로드 (하위 호환)
            sp = data.get("start_pos")
            self.start_pos = tuple(sp) if sp else None
            ep = data.get("end_pos")
            self.end_pos = tuple(ep) if ep else None

            logger.info(f"[Map] 로드: {filepath} (이동가능 {len(self.passable)}개, 장애물 {len(self.blocked)}개, 순찰 {len(self.patrol_points)}개)")
            return True
        except Exception as e:
            logger.error(f"[Map] 로드 실패: {e}")
            return False

    def load_and_merge(self, filepath: str) -> bool:
        """JSON 로드 후 병합 (데이터 누적)"""
        path = Path(filepath)
        if not path.exists():
            return False

        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)

            if self.name == "Unknown":
                self.name = data.get("name", "Unknown")

            # 기존 데이터에 추가
            loaded_passable = set(tuple(p) for p in data.get("passable", []))
            loaded_blocked = set(tuple(p) for p in data.get("blocked", []))
            # soft_blocked 로드
            raw_sb = data.get("soft_blocked", {})
            loaded_soft_blocked = {}
            for key, count in raw_sb.items():
                parts = key.split(",")
                pos = (int(parts[0]), int(parts[1]))
                loaded_soft_blocked[pos] = count

            before = len(self.passable) + len(self.blocked)

            self.passable |= loaded_passable
            self.blocked |= loaded_blocked
            # soft_blocked 병합: 더 높은 fail_count 유지
            for pos, count in loaded_soft_blocked.items():
                existing = self.soft_blocked.get(pos, 0)
                self.soft_blocked[pos] = max(existing, count)

            # 충돌 해결: passable이 우선 (실제로 지나간 적 있으면 장애물 아님)
            self.blocked -= self.passable
            # soft_blocked도 passable/blocked와 충돌 해결
            for pos in list(self.soft_blocked.keys()):
                if pos in self.passable or pos in self.blocked:
                    del self.soft_blocked[pos]

            # 순찰 좌표 병합 (없으면 가져옴)
            loaded_patrol = [tuple(p) for p in data.get("patrol_points", [])]
            if loaded_patrol and not self.patrol_points:
                self.patrol_points = loaded_patrol
            # 출발지/도착지 병합 (없으면 가져옴)
            sp = data.get("start_pos")
            if sp and not self.start_pos:
                self.start_pos = tuple(sp)
            ep = data.get("end_pos")
            if ep and not self.end_pos:
                self.end_pos = tuple(ep)

            after = len(self.passable) + len(self.blocked)

            logger.info(f"[Map] 병합: {before}개 → {after}개")
            return True
        except Exception as e:
            logger.error(f"[Map] 병합 실패: {e}")
            return False

    def cleanup_outliers(self) -> int:
        """이상치(다른 맵에서 오염된 좌표) 자동 제거.
        가장 큰 좌표 클러스터만 남기고 나머지 삭제.
        Returns: 제거된 타일 수"""
        all_coords = list(self.passable | self.blocked | set(self.soft_blocked.keys()))
        if len(all_coords) < 10:
            return 0

        # X, Y 각각에서 메인 범위 찾기 (gap 기반)
        xs = sorted(set(p[0] for p in all_coords))
        ys = sorted(set(p[1] for p in all_coords))

        min_x, max_x = self._find_main_cluster(xs)
        min_y, max_y = self._find_main_cluster(ys)

        # 패딩 추가
        pad = 2
        min_x -= pad
        max_x += pad
        min_y -= pad
        max_y += pad

        # 범위 밖 타일 제거
        removed = 0
        for pos in list(self.passable):
            if not (min_x <= pos[0] <= max_x and min_y <= pos[1] <= max_y):
                self.passable.discard(pos)
                removed += 1
        for pos in list(self.blocked):
            if not (min_x <= pos[0] <= max_x and min_y <= pos[1] <= max_y):
                self.blocked.discard(pos)
                removed += 1
        for pos in list(self.soft_blocked.keys()):
            if not (min_x <= pos[0] <= max_x and min_y <= pos[1] <= max_y):
                del self.soft_blocked[pos]
                removed += 1

        if removed > 0:
            logger.info(f"[Map] 이상치 정리: {removed}개 타일 제거 (범위: X[{min_x}~{max_x}] Y[{min_y}~{max_y}])")
        return removed

    @staticmethod
    def _find_main_cluster(sorted_vals):
        """정렬된 좌표에서 가장 큰 클러스터 범위 반환"""
        if len(sorted_vals) <= 1:
            return sorted_vals[0], sorted_vals[0]

        total_span = sorted_vals[-1] - sorted_vals[0]
        if total_span <= 50:
            return sorted_vals[0], sorted_vals[-1]

        # 가장 큰 gap 찾기
        best_gap = 0
        best_idx = 0
        for i in range(len(sorted_vals) - 1):
            gap = sorted_vals[i + 1] - sorted_vals[i]
            if gap > best_gap:
                best_gap = gap
                best_idx = i

        # gap이 전체 범위의 15% 이상이면 분리
        if best_gap > total_span * 0.15:
            left = sorted_vals[:best_idx + 1]
            right = sorted_vals[best_idx + 1:]
            cluster = left if len(left) >= len(right) else right
            return cluster[0], cluster[-1]

        return sorted_vals[0], sorted_vals[-1]

    def clear(self):
        """초기화"""
        self.passable.clear()
        self.blocked.clear()
        self.soft_blocked.clear()
        self.patrol_points.clear()
        logger.info("[Map] 초기화")


# 호환성을 위한 더미 클래스
class TileInfo:
    """호환성용 (사용 안함)"""
    pass
