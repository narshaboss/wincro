# A* 경로탐색 알고리즘

## 개요

맵핑 시스템에서 사용하는 A* 기반 경로탐색 알고리즘입니다.
목표 좌표까지 벽을 피해 최적 경로를 찾습니다.

## 이전 방식의 문제점

```
목표로 직진 → 벽 만남 → 한 칸 피함 → 다시 목표로 직진 → 또 벽 → 무한 반복...
```

- 벽을 "피하기만" 하고 "돌아가는 경로"를 계획하지 않음
- 긴 벽 앞에서 좌우로 왔다갔다 반복
- 같은 위치에서 맴도는 현상 발생

## 새로운 방식: A* 경로탐색

### 핵심 원리

```
1. 현재 위치 → 목표까지 전체 경로를 미리 계산
2. 맵 데이터(이동가능/벽)를 보고 갈 수 있는 길 찾기
3. 경로대로 한 칸씩 이동
4. 새 벽 발견하면 → 경로 다시 계산
```

### 알고리즘 흐름

```
┌─────────────────────────────────────────────────────────┐
│                    경로 탐색 시작                         │
└─────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────┐
│  1차: 알려진 경로로만 탐색 (allow_unknown=False)          │
│      - passable 좌표들만 사용                            │
│      - 확실히 갈 수 있는 길                              │
└─────────────────────────────────────────────────────────┘
                            │
                   경로 발견? ─────── Yes ──→ 경로 따라 이동
                            │
                           No
                            │
                            ▼
┌─────────────────────────────────────────────────────────┐
│  2차: 미탐색 영역 포함 탐색 (allow_unknown=True)          │
│      - passable + 미탐색 좌표 사용                       │
│      - 새로운 길 개척 가능                               │
└─────────────────────────────────────────────────────────┘
                            │
                   경로 발견? ─────── Yes ──→ 경로 따라 이동
                            │
                           No
                            │
                            ▼
┌─────────────────────────────────────────────────────────┐
│  3차: 단순 직진 (fallback)                               │
│      - 목표 방향으로 직진 시도                            │
└─────────────────────────────────────────────────────────┘
```

### 벽 발견 시 처리

```
이동 시도 → 좌표 안 바뀜 (2회 연속) → 벽으로 등록 → 경로 재계산
```

## 데이터 구조

### GameMap 클래스

```python
class GameMap:
    passable: Set[Tuple[int, int]]  # 이동 가능한 좌표들
    blocked: Set[Tuple[int, int]]   # 벽/장애물 좌표들
```

### 주요 메서드

```python
# 좌표 등록
mark_passable(x, y)  # 이동 가능으로 등록
mark_blocked(x, y)   # 벽으로 등록

# 좌표 확인
is_blocked(x, y)     # 벽인지 확인
is_passable(x, y)    # 이동 가능한지 확인
is_known(x, y)       # 탐색된 좌표인지 확인

# A* 경로탐색용
get_walkable_neighbors(x, y, allow_unknown)  # 이동 가능한 인접 좌표
```

## A* 알고리즘 구현

### SimplePathfinder 클래스

```python
class SimplePathfinder:
    def find_path(self, start, goal, allow_unknown=False) -> PathResult:
        """
        A* 알고리즘으로 경로 탐색

        Args:
            start: 시작 좌표 (x, y)
            goal: 목표 좌표 (x, y)
            allow_unknown: 미탐색 영역 통과 허용 여부

        Returns:
            PathResult: 경로 정보 (found, path, directions, cost)
        """
```

### A* 알고리즘 핵심

```python
# 우선순위 큐 사용 (f_cost = g_cost + h_cost)
# g_cost: 시작점에서 현재까지 실제 비용
# h_cost: 현재에서 목표까지 예상 비용 (맨해튼 거리)

while open_set:
    current = heappop(open_set)  # f_cost가 가장 낮은 노드

    if current == goal:
        return path  # 목표 도달

    for neighbor in get_walkable_neighbors(current):
        new_cost = g_cost + 1
        if neighbor not in visited or new_cost < visited[neighbor]:
            heappush(open_set, (new_cost + heuristic(neighbor, goal), ...))
```

## 적용 모드

| 모드 | A* 경로탐색 | 맵 기록 | 설명 |
|------|------------|--------|------|
| **맵핑 시작** | O | O | 탐색하며 맵 데이터 축적 |
| **테스트 실행** | O | X | 기존 맵 데이터만 참조 |

## 로그 메시지

```
경로 발견: 15칸              # 알려진 경로로 가능
탐색 경로: 20칸 (미지 영역 포함)  # 새로운 곳 탐색 필요
경로 없음, 직진 시도          # 경로 못 찾음
벽 발견: x10y20              # 새 벽 발견, 경로 재계산
```

## 파일 구조

```
src/player/
├── game_map.py          # 맵 데이터 구조
├── simple_pathfinder.py # A* 경로탐색
├── map_explorer.py      # DFS 자동 탐색 (미사용)
└── map_visualizer.py    # 맵 시각화

src/ui/
└── player_view.py       # _run_coordinate_loop()에서 사용
```

## 장점

1. **전체 경로 계획**: 한 칸씩 피하는 게 아니라 전체 우회 경로를 계산
2. **맵 데이터 활용**: 이전에 탐색한 정보를 재사용
3. **동적 재계산**: 새 벽 발견 시 즉시 새 경로 탐색
4. **점진적 개선**: 맵핑할수록 더 정확한 경로 탐색 가능

## 참고

- A* 알고리즘: https://en.wikipedia.org/wiki/A*_search_algorithm
- 맨해튼 거리: |x1-x2| + |y1-y2| (4방향 이동에 적합한 휴리스틱)

---

# 초기 알고리즘 백업 (v1.0.107 기준)

> **이 섹션은 알고리즘 수정 시 복원용 백업입니다.**
> soft_blocked 등 새 기능 도입 후 문제 발생 시, 아래 코드로 되돌리면 됩니다.

## 백업 1: game_map.py 전체 코드

```python
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
    - blocked: 장애물 좌표들 (못 가는 곳)
    """

    def __init__(self, name: str = "Unknown"):
        self.name = name
        self.passable: Set[Tuple[int, int]] = set()  # 이동 가능한 좌표
        self.blocked: Set[Tuple[int, int]] = set()   # 장애물 좌표

    def mark_passable(self, x: int, y: int):
        """이동 가능한 좌표로 등록"""
        pos = (int(x), int(y))
        self.passable.add(pos)
        # 장애물에서 제거 (혹시 잘못 등록됐으면)
        self.blocked.discard(pos)
        logger.debug(f"[Map] {pos} 이동 가능")

    def mark_blocked(self, x: int, y: int):
        """장애물 좌표로 등록"""
        pos = (int(x), int(y))
        self.blocked.add(pos)
        # 이동 가능에서 제거
        self.passable.discard(pos)
        logger.debug(f"[Map] {pos} 장애물!")

    def mark_walkable(self, from_x: int, from_y: int, direction: str, walkable: bool):
        """
        특정 방향 이동 가능 여부 기록 (rule_executor 호환용)
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
        return pos in self.passable or pos in self.blocked

    def record_move(self, from_x: int, from_y: int, direction: str,
                    to_x: int, to_y: int, success: bool):
        """이동 결과 기록"""
        self.mark_passable(from_x, from_y)
        if success:
            self.mark_passable(to_x, to_y)
        else:
            dx, dy = DIRECTIONS_4.get(direction, (0, 0))
            target_x = from_x + dx
            target_y = from_y + dy
            self.mark_blocked(target_x, target_y)

    def get_neighbors(self, x: int, y: int, avoid_blocked: bool = True,
                     avoid_unknown: bool = False) -> List[Tuple[int, int, str]]:
        """인접 좌표 목록"""
        neighbors = []
        for direction, (dx, dy) in DIRECTIONS_4.items():
            nx, ny = x + dx, y + dy
            if avoid_blocked and self.is_blocked(nx, ny):
                continue
            if avoid_unknown and not self.is_known(nx, ny):
                continue
            neighbors.append((nx, ny, direction))
        return neighbors

    def get_walkable_neighbors(self, x: int, y: int,
                               allow_unknown: bool = False) -> List[Tuple[int, int, str]]:
        """이동 가능한 인접 좌표 (A* 경로탐색용)"""
        neighbors = []
        for direction, (dx, dy) in DIRECTIONS_4.items():
            nx, ny = int(x + dx), int(y + dy)
            # 벽은 무조건 제외
            if self.is_blocked(nx, ny):
                continue
            # 미탐색 영역 처리
            if not allow_unknown and not self.is_passable(nx, ny):
                continue
            neighbors.append((nx, ny, direction))
        return neighbors

    def get_bounds(self) -> dict:
        """맵 경계"""
        all_coords = self.passable | self.blocked
        if not all_coords:
            return {"min_x": 0, "max_x": 0, "min_y": 0, "max_y": 0}
        xs = [p[0] for p in all_coords]
        ys = [p[1] for p in all_coords]
        return {
            "min_x": min(xs), "max_x": max(xs),
            "min_y": min(ys), "max_y": max(ys),
        }

    def get_statistics(self) -> dict:
        """맵 통계"""
        return {
            "name": self.name,
            "total_tiles": len(self.passable) + len(self.blocked),
            "passable_tiles": len(self.passable),
            "blocked_tiles": len(self.blocked),
            "explored_tiles": len(self.passable),
            "walkable_edges": len(self.passable),
            "blocked_edges": len(self.blocked),
            "bounds": self.get_bounds(),
        }

    def save(self, filepath: str):
        """JSON 저장"""
        data = {
            "name": self.name,
            "passable": [list(p) for p in self.passable],
            "blocked": [list(p) for p in self.blocked],
        }
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"[Map] 저장: {filepath} (이동가능 {len(self.passable)}개, 장애물 {len(self.blocked)}개)")

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
            logger.info(f"[Map] 로드: {filepath} (이동가능 {len(self.passable)}개, 장애물 {len(self.blocked)}개)")
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
            loaded_passable = set(tuple(p) for p in data.get("passable", []))
            loaded_blocked = set(tuple(p) for p in data.get("blocked", []))
            before = len(self.passable) + len(self.blocked)
            self.passable |= loaded_passable
            self.blocked |= loaded_blocked
            # 충돌 해결: passable이 우선
            self.blocked -= self.passable
            after = len(self.passable) + len(self.blocked)
            logger.info(f"[Map] 병합: {before}개 -> {after}개")
            return True
        except Exception as e:
            logger.error(f"[Map] 병합 실패: {e}")
            return False

    def clear(self):
        """초기화"""
        self.passable.clear()
        self.blocked.clear()
        logger.info("[Map] 초기화")


# 호환성을 위한 더미 클래스
class TileInfo:
    """호환성용 (사용 안함)"""
    pass
```

## 백업 2: simple_pathfinder.py 전체 코드

```python
"""
좌표 기반 맵핑 시스템 - A* 경로 탐색

맵 데이터를 기반으로 최단 경로를 찾습니다.
"""

import heapq
import logging
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
    found: bool
    path: List[Tuple[int, int]]
    directions: List[str]
    cost: int

    @property
    def length(self) -> int:
        return len(self.path)

    def get_next_position(self, current_index: int) -> Optional[Tuple[int, int]]:
        if current_index + 1 < len(self.path):
            return self.path[current_index + 1]
        return None

    def get_next_direction(self, current_index: int) -> Optional[str]:
        if current_index < len(self.directions):
            return self.directions[current_index]
        return None


class SimplePathfinder:
    """A* 알고리즘 기반 경로 탐색기"""

    def __init__(self, game_map: GameMap):
        self.game_map = game_map
        self._current_path: Optional[PathResult] = None
        self._path_index: int = 0
        self._goal: Optional[Tuple[int, int]] = None

    def _heuristic(self, a: Tuple[int, int], b: Tuple[int, int]) -> int:
        """맨해튼 거리 휴리스틱"""
        return abs(a[0] - b[0]) + abs(a[1] - b[1])

    def find_path(self, start: Tuple[int, int], goal: Tuple[int, int],
                  allow_unknown: bool = False) -> PathResult:
        """A* 알고리즘으로 경로 탐색"""
        if start == goal:
            return PathResult(found=True, path=[start], directions=[], cost=0)

        open_set = [(self._heuristic(start, goal), 0, start, [start], [])]
        heapq.heapify(open_set)
        visited: Dict[Tuple[int, int], int] = {}

        while open_set:
            f_cost, g_cost, current, path, directions = heapq.heappop(open_set)

            if current == goal:
                result = PathResult(found=True, path=path, directions=directions, cost=g_cost)
                logger.debug(f"[Pathfinder] 경로 발견: {len(path)}칸")
                return result

            if current in visited and visited[current] <= g_cost:
                continue
            visited[current] = g_cost

            neighbors = self.game_map.get_walkable_neighbors(
                current[0], current[1], allow_unknown=allow_unknown
            )

            for nx, ny, direction in neighbors:
                new_cost = g_cost + 1
                next_pos = (nx, ny)

                if next_pos in visited and visited[next_pos] <= new_cost:
                    continue

                h_cost = self._heuristic(next_pos, goal)
                new_f_cost = new_cost + h_cost

                heapq.heappush(open_set, (
                    new_f_cost, new_cost, next_pos,
                    path + [next_pos], directions + [direction]
                ))

        logger.debug(f"[Pathfinder] 경로 없음: {start} -> {goal}")
        return PathResult(found=False, path=[], directions=[], cost=-1)

    def set_goal(self, goal: Tuple[int, int]):
        self._goal = goal
        self._current_path = None
        self._path_index = 0

    def get_next_direction(self, current: Tuple[int, int],
                           allow_unknown: bool = False) -> Optional[str]:
        if self._goal is None:
            return None
        if current == self._goal:
            return None

        need_recalculate = False
        if self._current_path is None:
            need_recalculate = True
        elif not self._current_path.found:
            need_recalculate = True
        elif self._path_index >= len(self._current_path.path):
            need_recalculate = True
        elif current not in self._current_path.path:
            need_recalculate = True
        else:
            try:
                path_pos = self._current_path.path.index(current)
                if path_pos != self._path_index:
                    self._path_index = path_pos
            except ValueError:
                need_recalculate = True

        if need_recalculate:
            self._current_path = self.find_path(current, self._goal, allow_unknown)
            self._path_index = 0
            if not self._current_path.found:
                logger.warning(f"[Pathfinder] 경로 없음: {current} -> {self._goal}")
                return None

        direction = self._current_path.get_next_direction(self._path_index)
        return direction

    def advance(self):
        self._path_index += 1

    def recalculate_path(self, current: Tuple[int, int], goal: Tuple[int, int],
                         allow_unknown: bool = False) -> PathResult:
        self._goal = goal
        self._current_path = self.find_path(current, goal, allow_unknown)
        self._path_index = 0
        if self._current_path.found:
            logger.info(f"[Pathfinder] 경로 재계산: {len(self._current_path.path)}칸")
        else:
            logger.warning(f"[Pathfinder] 경로 재계산 실패: {current} -> {goal}")
        return self._current_path

    def get_current_path(self) -> Optional[PathResult]:
        return self._current_path

    def get_remaining_path(self) -> List[Tuple[int, int]]:
        if self._current_path is None or not self._current_path.found:
            return []
        return self._current_path.path[self._path_index:]

    def get_remaining_directions(self) -> List[str]:
        if self._current_path is None or not self._current_path.found:
            return []
        return self._current_path.directions[self._path_index:]

    def is_path_valid(self) -> bool:
        return self._current_path is not None and self._current_path.found

    def is_at_goal(self, current: Tuple[int, int]) -> bool:
        return self._goal is not None and current == self._goal

    def clear(self):
        self._current_path = None
        self._path_index = 0
        self._goal = None

    def get_state_info(self) -> dict:
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
```

## 백업 3: 벽 판정 로직 (rule_executor.py 2988~3046행)

이 코드는 `execute_game_mode_coordinate()` 메서드 내부의 벽 판정 + 탈출 스킬 로직입니다.

```python
# === 벽 판정 핵심 로직 (rule_executor.py) ===
# 위치: execute_game_mode_coordinate() 메서드 내 while 루프

# 상태 변수 (루프 시작 전 초기화)
prev_x, prev_y = None, None
last_dir = None
stuck_count = 0
total_stuck_count = 0  # 탈출 스킬용 연속 정체 카운트

# --- 이동 성공/실패 판정 ---
if prev_x is not None:
    moved = (prev_x != current_x or prev_y != current_y)

    if moved:
        # 이동 성공 → 이동가능으로 기록
        if mapping_enabled:
            game_map.mark_passable(current_x, current_y)
        stuck_count = 0
        total_stuck_count = 0
    else:
        # 이동 실패
        stuck_count += 1
        total_stuck_count += 1

        if stuck_count >= 2 and last_dir:
            # 2번 연속 실패 → 벽으로 등록
            if mapping_enabled:
                ddx, ddy = DIRECTIONS_4.get(last_dir, (0, 0))
                wall_x = prev_x + ddx
                wall_y = prev_y + ddy
                game_map.mark_blocked(wall_x, wall_y)
                logger.info(f"[좌표모드] 벽 발견: ({wall_x},{wall_y})")
            # 벽 발견 → 경로 재계산
            current_path = []
            path_index = 0
            stuck_count = 0

# --- 탈출 스킬 체크 ---
if (escape_skill_enabled and
    total_stuck_count >= escape_skill_stuck_threshold and
    time.time() - last_escape_time >= escape_skill_cooldown):

    logger.warning(f"[좌표모드] 탈출 스킬 발동! (연속 정체 {total_stuck_count}회)")

    pyautogui.press(escape_skill_key)
    time.sleep(0.3)

    dx = target_x - current_x
    dy = target_y - current_y
    if abs(dx) >= abs(dy):
        direction_key = config.move_keys.get("right" if dx > 0 else "left")
    else:
        direction_key = config.move_keys.get("down" if dy > 0 else "up")

    for _ in range(escape_skill_direction_count):
        pyautogui.press(direction_key)
        time.sleep(0.05)

    pyautogui.press('enter')
    time.sleep(escape_skill_wait_after)

    last_escape_time = time.time()
    prev_x, prev_y = None, None
    stuck_count = 0
    total_stuck_count = 0
    current_path = []
    path_index = 0
    continue
```

## 백업 4: 벽 판정 로직 (player_view.py _run_coordinate_loop 3344~3371행)

UI 테스트 실행용 벽 판정 로직입니다. rule_executor.py와 동일한 구조입니다.

```python
# === 벽 판정 핵심 로직 (player_view.py) ===
# 위치: _run_coordinate_loop() 메서드 내 while 루프

# 이동 성공/실패 판정
if prev_x is not None:
    moved = (prev_x != current_x or prev_y != current_y)

    if moved:
        # 이동 성공 → 현재 위치 이동가능으로 기록
        if mapping_on and use_map:
            self._game_map.mark_passable(current_x, current_y)
            # 10칸마다 자동 저장
            if len(self._game_map.passable) % 10 == 0:
                threading.Thread(target=self._auto_save_map, daemon=True).start()
        stuck_count = 0
    else:
        # 이동 실패
        stuck_count += 1
        if stuck_count >= 2 and last_dir:
            # 2번 연속 실패 → 벽으로 등록
            if mapping_on and use_map:
                ddx, ddy = DIRECTIONS_4.get(last_dir, (0, 0))
                wall_x = prev_x + ddx
                wall_y = prev_y + ddy
                self._game_map.mark_blocked(wall_x, wall_y)
                self.after(0, lambda wx=wall_x, wy=wall_y:
                    self._append_log(f"벽 발견: x{wx}y{wy}"))
                # 벽 발견시 경로 재계산 필요
                current_path = []
                path_index = 0
                # 벽 발견시 즉시 저장
                threading.Thread(target=self._auto_save_map, daemon=True).start()
            stuck_count = 0
```

## 복원 방법

1. `game_map.py` → 백업 1의 코드로 덮어쓰기
2. `simple_pathfinder.py` → 백업 2의 코드로 덮어쓰기
3. `rule_executor.py` → 백업 3의 벽 판정 로직을 `execute_game_mode_coordinate()` 메서드에 복원
4. `player_view.py` → 백업 4의 벽 판정 로직을 `_run_coordinate_loop()` 메서드에 복원

## 핵심 판정 규칙 요약 (v1.0.107)

```
[벽 등록]
- 조건: 같은 방향으로 2회 연속 이동 실패 (stuck_count >= 2)
- 동작: mark_blocked(이동방향 1칸 앞 좌표)
- 특성: 즉시 영구 벽 등록, 해제 조건 없음
  (단, mark_passable이 호출되면 blocked에서 제거됨)

[이동가능 등록]
- 조건: 이동 성공 (좌표 변화 감지)
- 동작: mark_passable(현재 좌표)
- 특성: blocked에서 자동 제거

[경로 재계산]
- 조건: 벽 발견 시
- 동작: current_path = [], path_index = 0 → 다음 루프에서 A* 재탐색

[탈출 스킬]
- 조건: total_stuck_count >= threshold AND 쿨타임 경과
- 동작: 스킬키 → 방향키 연타 → Enter → 상태 초기화
```

---

# Soft Blocked 변경사항 (v1.0.108)

> **변경일: 2026-02-05**
> **문제: 던전 몬스터가 영구벽으로 등록되는 문제**
> 이동 실패 2회 → 즉시 `blocked`(영구벽) 등록이었기 때문에,
> 몬스터 같은 임시 장애물도 영구벽이 되어 맵이 오염됨.

## 변경 개요

`blocked`(영구벽) 대신 `soft_blocked`(임시벽)을 먼저 거치는 2단계 시스템 도입.

```
[기존] 이동 실패 2회 → mark_blocked (영구벽, 즉시)
[변경] 이동 실패 2회 → mark_soft_blocked (임시벽, fail_count 누적)
                       → fail_count >= 5 → mark_blocked (영구벽 승격)
                       → 이동 성공 시 → clear_soft_blocked (즉시 해제)
                       → tick() 호출 시 → fail_count 감소, 0이면 자동 해제
```

## 변경된 판정 규칙 (v1.0.108)

```
[임시벽 등록]
- 조건: 같은 방향으로 2회 연속 이동 실패 (stuck_count >= 2)
- 동작: mark_soft_blocked(이동방향 1칸 앞 좌표) → fail_count +1
- 특성: passable에서 제거, soft_blocked에 추가

[영구벽 승격]
- 조건: 같은 좌표에서 fail_count >= 5 (SOFT_BLOCKED_PROMOTE_THRESHOLD)
- 동작: mark_blocked()로 영구벽 등록
- 특성: soft_blocked에서 제거, blocked에 추가

[임시벽 해제]
- 조건1: 해당 좌표 이동 성공 → clear_soft_blocked() 즉시 해제
- 조건2: mark_passable() 호출 시 자동 해제
- 조건3: tick() 호출 시 fail_count 감소, 0 이하면 자동 해제

[A* 비용]
- passable / unknown: cost = 1
- soft_blocked: cost = 5 (통과 가능하지만 회피 우선)
- blocked: 통과 불가 (제외)

[tick 자동 감소]
- 메인 루프 10회마다 game_map.tick() 호출
- 모든 soft_blocked의 fail_count를 1 감소
- 0 이하면 soft_blocked에서 제거 (자동 만료)
```

## 수정 파일 목록

| 파일 | 변경 내용 |
|------|-----------|
| `src/player/game_map.py` | `soft_blocked: Dict` 추가, 6개 메서드 추가, save/load/merge/stats 등 기존 메서드 수정 |
| `src/player/simple_pathfinder.py` | A* 비용 `g_cost + 1` → `g_cost + get_soft_blocked_cost()` |
| `src/player/rule_executor.py` | `mark_blocked` → `mark_soft_blocked`, `clear_soft_blocked` 추가, `tick()` 호출 |
| `src/ui/player_view.py` | rule_executor.py와 동일 변경, 통계 표시에 임시벽 추가 |
| `src/player/map_canvas.py` | 주황색 임시벽 렌더링, 범례/클릭팝업 업데이트 |
| `src/player/map_visualizer.py` | `▒` 기호 추가, 범례 업데이트 |

## game_map.py 추가/변경 상세

### 추가된 속성

```python
SOFT_BLOCKED_PROMOTE_THRESHOLD = 5  # 영구벽 승격 임계값
soft_blocked: Dict[Tuple[int, int], int] = {}  # {(x,y): fail_count}
```

### 추가된 메서드

```python
def mark_soft_blocked(self, x, y):
    """임시 장애물 등록. fail_count 누적, 임계값 초과 시 영구벽 승격"""
    # 이미 영구벽이면 무시
    # count >= SOFT_BLOCKED_PROMOTE_THRESHOLD → mark_blocked() 호출
    # 아니면 soft_blocked[pos] = count, passable에서 제거

def clear_soft_blocked(self, x, y):
    """임시 장애물 해제 (이동 성공 시)"""

def is_soft_blocked(self, x, y) -> bool:
    """임시 장애물인지 확인"""

def get_soft_blocked_cost(self, x, y) -> int:
    """이동 비용 반환 (soft_blocked이면 5, 아니면 1)"""

def tick(self):
    """주기적 호출: 모든 soft_blocked fail_count 1 감소, 0이면 제거"""
```

### 변경된 메서드

```python
mark_passable()   # soft_blocked에서도 제거 추가
mark_blocked()    # soft_blocked에서도 제거 추가
is_known()        # soft_blocked도 known으로 처리
get_walkable_neighbors()  # soft_blocked 타일 통과 허용 (blocked만 제외)
get_bounds()      # soft_blocked 좌표 포함
get_statistics()  # soft_blocked_tiles 필드 추가
save()            # soft_blocked 직렬화 ("x,y": count 형태)
load()            # soft_blocked 역직렬화 (하위 호환: 없으면 빈 dict)
load_and_merge()  # soft_blocked 병합 (높은 fail_count 유지, passable/blocked 충돌 해결)
clear()           # soft_blocked 초기화 추가
```

## simple_pathfinder.py 변경 상세

```python
# 변경 전 (line 122)
new_cost = g_cost + 1

# 변경 후
move_cost = self.game_map.get_soft_blocked_cost(nx, ny)
new_cost = g_cost + move_cost
# soft_blocked 타일: cost=5 → A*가 우회로를 선호하되, 불가피하면 통과
```

## rule_executor.py 변경 상세

```python
# 변경 전 (line 3009~3014)
game_map.mark_blocked(wall_x, wall_y)
logger.info(f"[좌표모드] 벽 발견: ({wall_x},{wall_y})")

# 변경 후
game_map.mark_soft_blocked(wall_x, wall_y)
logger.info(f"[좌표모드] 임시벽 발견: ({wall_x},{wall_y})")

# 추가: 이동 성공 시 (line 2999~3000 부근)
game_map.clear_soft_blocked(current_x, current_y)

# 추가: tick 카운터 (상태 변수에 tick_counter = 0 추가)
# 이동 판정 후 10회마다 game_map.tick() 호출
tick_counter += 1
if tick_counter >= 10:
    game_map.tick()
    tick_counter = 0
```

## player_view.py 변경 상세

rule_executor.py와 동일한 변경:
- `mark_blocked` → `mark_soft_blocked`
- 이동 성공 시 `clear_soft_blocked` 추가
- `tick_counter` + `tick()` 호출 추가
- 로그 메시지: `"🧱 벽 발견"` → `"⚠ 임시벽"`
- 통계 표시에 `임시벽: N개` 추가 (맵 로드, 복원, 통계 다이얼로그)

## 시각화 변경

### map_canvas.py
- 색상 추가: `soft_blocked = "#e8a040"` (주황), `soft_blocked_border = "#c88030"`
- 렌더링: 영구벽(빨강) 뒤에 임시벽(주황) 블록 렌더링
- 범례: "임시벽" 항목 추가
- 클릭 팝업: `"임시벽(N)"` 표시 (N = fail_count)

### map_visualizer.py
- 기호: `▒` = 임시벽
- 범례: `▒임시벽` 추가
- 통계: 임시벽 카운트 표시

## 하위 호환성

- 기존 맵 JSON 파일에 `soft_blocked` 키가 없어도 정상 로드 (빈 dict 기본값)
- 기존 `blocked` / `passable` 데이터는 그대로 유지
- A* 알고리즘 구조(heapq, heuristic, visited) 변경 없음, 비용 함수만 변경

## 복원 방법

위 "초기 알고리즘 백업" 섹션의 백업 1~4 코드로 되돌리면 v1.0.107 상태로 복원 가능.
