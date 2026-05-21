from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, field
import random
from typing import Any, Optional

from .game_map import DIRECTIONS_4, GameMap

Coord = tuple[int, int]
Edge = tuple[int, int, str]

SENSITIVITY_BALANCED = "균형형"

PRESET_NORMAL = "정상"
PRESET_ROUTE_STAGNATION = "경로정체 재현"
PRESET_COORD_GLITCH = "좌표오독 재현"
PRESET_BOSS_REACQUIRE = "보스 재감지 실패"
PRESET_SCAN = "자동 스캔"

MONSTER_PATTERN_FRONT_HOLD = "정면막기"
MONSTER_PATTERN_WAVE = "파도막기"
MONSTER_PATTERN_CROSS = "교차방해"
MONSTER_PATTERN_SURROUND = "포위막기"
MONSTER_PATTERN_CHOKE = "목막힘점유"
MONSTER_PATTERN_BOSS_GUARD = "보스가드"
MONSTER_PATTERN_SWEEP = "스윕방해"
MONSTER_PATTERN_PINCER = "집게포위"

FAILURE_SUMMARY = {
    "max_stagnation_reached": "장기 정체로 중단되었습니다.",
    "max_iterations_reached": "최대 반복 횟수 초과로 중단되었습니다.",
    "boss_blocked": "보스 접근 경로가 반복 차단되었습니다.",
    "boss_reacquire_fail": "보스 재감지가 반복 실패했습니다.",
    "boss_visibility_flap": "보스 인식 신호가 과하게 흔들렸습니다.",
    "boss_skill_without_contact": "보스 접촉 전 스킬 사용이 발생했습니다.",
    "arrival_before_boss_contact": "보스 접촉 전에 키입력 단계로 진행했습니다.",
    "item_escape_missing": "아이템 루팅 후 ESC 해제가 누락되었습니다.",
    "two_point_loop": "두 좌표를 왕복하는 루프가 발생했습니다.",
    "same_position_loop": "같은 좌표에 장시간 고정되었습니다.",
    "portal_wait_stall": "포탈 또는 전환 대기 상태가 끝나지 않았습니다.",
    "item_detect_miss": "아이템이 있어야 하는 흐름에서 탐지에 실패했습니다.",
    "z_state_residual": "Z 상태가 정리되지 않았습니다.",
    "monster_block_loop": "몬스터 점유가 경로를 반복 차단했습니다.",
}


@dataclass
class SimulationFaultProfile:
    name: str
    seed: int
    start: Coord
    goal: Optional[Coord]
    is_boss_room: bool
    sensitivity: str = SENSITIVITY_BALANCED
    coord_glitch_ticks: set[int] = field(default_factory=set)
    coord_freeze_ticks: set[int] = field(default_factory=set)
    move_fail_edges: set[Edge] = field(default_factory=set)
    blocked_dir_residual_ticks: set[int] = field(default_factory=set)
    boss_detect_drop_ticks: set[int] = field(default_factory=set)
    boss_false_positive_ticks: set[int] = field(default_factory=set)
    boss_reacquire_fail_ticks: set[int] = field(default_factory=set)
    boss_jitter_ticks: set[int] = field(default_factory=set)
    item_detect_miss_ticks: set[int] = field(default_factory=set)
    item_false_positive_ticks: set[int] = field(default_factory=set)
    z_sticky_ticks: set[int] = field(default_factory=set)
    portal_wait_stall_ticks: set[int] = field(default_factory=set)
    monster_patterns: list[str] = field(default_factory=list)
    max_iterations: int = 180
    max_stagnation: int = 36


@dataclass
class SimulationSensorFrame:
    tick: int
    current_pos: Coord
    observed_pos: Coord
    boss_pos: Optional[Coord] = None
    perceived_boss_pos: Optional[Coord] = None
    item_pos: Optional[Coord] = None
    monster_blocks: tuple[Coord, ...] = ()
    fault_flags: tuple[str, ...] = ()
    boss_signal: str = "stable"


@dataclass
class SimulationTickRecord:
    tick: int
    position: Coord
    state: str
    action: str
    reason: str = ""
    avoid_set: tuple[Coord, ...] = ()
    blocked_dirs: tuple[str, ...] = ()
    edge_fail_counts: tuple[tuple[Edge, int], ...] = ()
    monster_blocks: tuple[Coord, ...] = ()
    blocked_neighbor_tiles: tuple[Coord, ...] = ()
    fault_flags: tuple[str, ...] = ()
    boss_signal: str = "stable"
    no_detour: bool = False
    stop_reason: str = ""


@dataclass
class HarnessRunResult:
    steps: list[dict[str, Any]] = field(default_factory=list)
    records: list[SimulationTickRecord] = field(default_factory=list)
    final_pos: Coord = (0, 0)
    stop_reason: str = ""
    completed: bool = False


@dataclass
class SimulationScanResult:
    segment_name: str
    seed: int
    profile_name: str
    status: str
    stop_reason: str
    summary: str
    details: str
    final_pos: Coord
    culprit_variables: tuple[str, ...] = ()
    scenario: Any = None


def build_fault_profile(
    preset_name: str,
    *,
    seed: int,
    start: Coord,
    goal: Optional[Coord],
    is_boss_room: bool,
) -> SimulationFaultProfile:
    rng = random.Random(seed)
    profile = SimulationFaultProfile(
        name=preset_name,
        seed=seed,
        start=start,
        goal=goal,
        is_boss_room=is_boss_room,
    )
    if preset_name == PRESET_ROUTE_STAGNATION:
        profile.monster_patterns = [MONSTER_PATTERN_FRONT_HOLD, MONSTER_PATTERN_CHOKE]
        profile.blocked_dir_residual_ticks = set(range(3, 12))
        profile.max_stagnation = 12
    elif preset_name == PRESET_COORD_GLITCH:
        profile.coord_glitch_ticks = {3, 7, 11}
        profile.coord_freeze_ticks = {4, 8}
        profile.monster_patterns = [MONSTER_PATTERN_WAVE]
        profile.max_stagnation = 18
    elif preset_name == PRESET_BOSS_REACQUIRE:
        profile.boss_detect_drop_ticks = {2, 6}
        profile.boss_reacquire_fail_ticks = {3, 4, 7, 8}
        profile.boss_jitter_ticks = {5, 9}
        profile.monster_patterns = [MONSTER_PATTERN_BOSS_GUARD, MONSTER_PATTERN_SWEEP]
        profile.max_stagnation = 14
    elif preset_name == PRESET_SCAN:
        pattern_pool = [
            MONSTER_PATTERN_FRONT_HOLD,
            MONSTER_PATTERN_WAVE,
            MONSTER_PATTERN_CROSS,
            MONSTER_PATTERN_SURROUND,
            MONSTER_PATTERN_CHOKE,
            MONSTER_PATTERN_SWEEP,
            MONSTER_PATTERN_PINCER,
        ]
        if is_boss_room:
            pattern_pool.append(MONSTER_PATTERN_BOSS_GUARD)
        rng.shuffle(pattern_pool)
        profile.monster_patterns = pattern_pool[: rng.randint(2, min(4, len(pattern_pool)))]
        if rng.random() < 0.45:
            profile.coord_glitch_ticks = {rng.randint(4, 12)}
        if rng.random() < 0.35:
            profile.coord_freeze_ticks = {rng.randint(6, 14), rng.randint(15, 24)}
        if rng.random() < 0.55:
            profile.blocked_dir_residual_ticks = set(range(rng.randint(5, 9), rng.randint(12, 18)))
        if is_boss_room:
            if rng.random() < 0.55:
                profile.boss_detect_drop_ticks = {rng.randint(2, 8), rng.randint(9, 14)}
            if rng.random() < 0.5:
                profile.boss_false_positive_ticks = {rng.randint(3, 10)}
            if rng.random() < 0.45:
                profile.boss_reacquire_fail_ticks = set(range(rng.randint(4, 7), rng.randint(8, 12)))
            if rng.random() < 0.5:
                profile.boss_jitter_ticks = {rng.randint(5, 11), rng.randint(12, 18)}
            if rng.random() < 0.35:
                profile.item_detect_miss_ticks = {1}
            if rng.random() < 0.35:
                profile.z_sticky_ticks = {1}
            if rng.random() < 0.25:
                profile.portal_wait_stall_ticks = {1}
    return profile


def generate_scan_profiles(*, seed_start: int, count: int, is_boss_room: bool) -> list[SimulationFaultProfile]:
    profiles: list[SimulationFaultProfile] = []
    for seed in range(seed_start, seed_start + max(1, count)):
        profiles.append(
            build_fault_profile(
                PRESET_SCAN,
                seed=seed,
                start=(0, 0),
                goal=None,
                is_boss_room=is_boss_room,
            )
        )
    return profiles


def _manhattan(a: Coord, b: Coord) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _direction_between(a: Coord, b: Coord) -> Optional[str]:
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    for direction, (mx, my) in DIRECTIONS_4.items():
        if (dx, dy) == (mx, my):
            return direction
    return None


def _step_from_dir(pos: Coord, direction: str) -> Coord:
    dx, dy = DIRECTIONS_4[direction]
    return pos[0] + dx, pos[1] + dy


def _find_path(game_map: GameMap, start: Coord, goal: Coord, blocked_positions: set[Coord], blocked_edges: set[Edge]) -> list[Coord]:
    if start == goal:
        return [start]
    queue = deque([start])
    parents: dict[Coord, Optional[Coord]] = {start: None}
    while queue:
        current = queue.popleft()
        for nx, ny, direction in game_map.get_walkable_neighbors(current[0], current[1], allow_unknown=False, allow_soft_blocked=True):
            nxt = (nx, ny)
            if nxt in parents:
                continue
            if nxt in blocked_positions:
                continue
            if (current[0], current[1], direction) in blocked_edges:
                continue
            parents[nxt] = current
            if nxt == goal:
                path = [goal]
                cursor = current
                while cursor is not None:
                    path.append(cursor)
                    cursor = parents[cursor]
                return list(reversed(path))
            queue.append(nxt)
    return []


def _build_monster_blocks(
    game_map: GameMap,
    current: Coord,
    goal: Coord,
    tick: int,
    patterns: list[str],
    boss_pos: Optional[Coord] = None,
) -> list[Coord]:
    base_path = _find_path(game_map, current, goal, set(), set())
    next_pos = base_path[1] if len(base_path) > 1 else goal
    blocks: set[Coord] = set()
    phase = tick % 4

    def _maybe_add(pos: Coord):
        if pos != current and game_map.is_passable(pos[0], pos[1]):
            blocks.add(pos)

    if MONSTER_PATTERN_FRONT_HOLD in patterns:
        _maybe_add(next_pos)
        if phase in (1, 2):
            _maybe_add((next_pos[0] + 1, next_pos[1]))
        if phase in (2, 3):
            _maybe_add((next_pos[0], next_pos[1] + 1))

    if MONSTER_PATTERN_WAVE in patterns and base_path:
        anchor = base_path[min(len(base_path) - 1, 1 + (tick % max(1, min(4, len(base_path)))))]
        if tick % 2 == 0:
            for dx in (-1, 0, 1):
                _maybe_add((anchor[0] + dx, anchor[1]))
        else:
            for dy in (-1, 0, 1):
                _maybe_add((anchor[0], anchor[1] + dy))

    if MONSTER_PATTERN_CROSS in patterns:
        _maybe_add((next_pos[0] + 1, next_pos[1]))
        _maybe_add((next_pos[0] - 1, next_pos[1]))
        _maybe_add((next_pos[0], next_pos[1] + 1))
        _maybe_add((next_pos[0], next_pos[1] - 1))

    if MONSTER_PATTERN_SURROUND in patterns:
        opening = tick % 4
        for idx, direction in enumerate(("up", "right", "down", "left")):
            if idx == opening:
                continue
            _maybe_add(_step_from_dir(current, direction))

    if MONSTER_PATTERN_CHOKE in patterns and len(base_path) > 2:
        for pos in base_path[1: min(len(base_path), 4)]:
            _maybe_add(pos)

    if MONSTER_PATTERN_SWEEP in patterns:
        offset = (tick % 5) - 2
        _maybe_add((next_pos[0] + offset, next_pos[1]))
        _maybe_add((next_pos[0], next_pos[1] + offset))

    if MONSTER_PATTERN_PINCER in patterns:
        _maybe_add((next_pos[0] + 1, next_pos[1] + 1))
        _maybe_add((next_pos[0] - 1, next_pos[1] - 1))

    if boss_pos is not None and MONSTER_PATTERN_BOSS_GUARD in patterns:
        opening = tick % 4
        for idx, direction in enumerate(("up", "right", "down", "left")):
            if idx == opening:
                continue
            _maybe_add(_step_from_dir(boss_pos, direction))

    return sorted(blocks)


def _fault_flags_for_tick(profile: SimulationFaultProfile, tick: int) -> list[str]:
    flags: list[str] = []
    if tick in profile.coord_glitch_ticks:
        flags.append("coord_glitch")
    if tick in profile.coord_freeze_ticks:
        flags.append("coord_freeze")
    if tick in profile.blocked_dir_residual_ticks:
        flags.append("blocked_dir_residual")
    if tick in profile.boss_detect_drop_ticks:
        flags.append("boss_detect_drop")
    if tick in profile.boss_false_positive_ticks:
        flags.append("boss_false_positive")
    if tick in profile.boss_reacquire_fail_ticks:
        flags.append("boss_reacquire_fail")
    if tick in profile.boss_jitter_ticks:
        flags.append("boss_jitter")
    if tick in profile.item_detect_miss_ticks:
        flags.append("item_detect_miss")
    if tick in profile.item_false_positive_ticks:
        flags.append("item_false_positive")
    if tick in profile.z_sticky_ticks:
        flags.append("z_sticky")
    if tick in profile.portal_wait_stall_ticks:
        flags.append("portal_wait_stall")
    return flags


def _profile_variables(profile: SimulationFaultProfile) -> tuple[str, ...]:
    parts: list[str] = []
    if profile.monster_patterns:
        parts.append("몬스터패턴=" + ",".join(profile.monster_patterns))
    if profile.coord_glitch_ticks:
        parts.append(f"좌표오독={sorted(profile.coord_glitch_ticks)}")
    if profile.coord_freeze_ticks:
        parts.append(f"좌표고정={sorted(profile.coord_freeze_ticks)}")
    if profile.blocked_dir_residual_ticks:
        parts.append(f"방향차단잔류={sorted(profile.blocked_dir_residual_ticks)}")
    if profile.boss_detect_drop_ticks:
        parts.append(f"보스드롭={sorted(profile.boss_detect_drop_ticks)}")
    if profile.boss_reacquire_fail_ticks:
        parts.append(f"보스재감지실패={sorted(profile.boss_reacquire_fail_ticks)}")
    if profile.boss_jitter_ticks:
        parts.append(f"보스흔들림={sorted(profile.boss_jitter_ticks)}")
    if profile.item_detect_miss_ticks:
        parts.append(f"아이템미탐={sorted(profile.item_detect_miss_ticks)}")
    if profile.z_sticky_ticks:
        parts.append(f"Z잔류={sorted(profile.z_sticky_ticks)}")
    if profile.portal_wait_stall_ticks:
        parts.append(f"전환지연={sorted(profile.portal_wait_stall_ticks)}")
    return tuple(parts)


def _append_step(steps: list[dict[str, Any]], kind: str, position: Coord, message: str, detail: str = "", goal: Optional[Coord] = None, boss_pos: Optional[Coord] = None):
    steps.append(
        {
            "kind": kind,
            "position": position,
            "message": message,
            "detail": detail,
            "goal": goal,
            "boss_pos": boss_pos,
        }
    )


def _analyze_neighbor_constraints(
    game_map: GameMap,
    current: Coord,
    monster_blocks: set[Coord],
    avoid_edges: set[Edge],
) -> tuple[tuple[Coord, ...], tuple[Coord, ...]]:
    blocked_tiles: set[Coord] = set()
    open_tiles: set[Coord] = set()
    for nx, ny, direction in game_map.get_walkable_neighbors(current[0], current[1], allow_unknown=False, allow_soft_blocked=True):
        nxt = (nx, ny)
        if nxt in monster_blocks or (current[0], current[1], direction) in avoid_edges:
            blocked_tiles.add(nxt)
        else:
            open_tiles.add(nxt)
    return tuple(sorted(blocked_tiles)), tuple(sorted(open_tiles))


def run_route_harness(
    game_map: GameMap,
    *,
    start: Coord,
    goal: Coord,
    profile: SimulationFaultProfile,
    move_kind: str = "route_only",
    travel_label: str = "경로추적",
) -> HarnessRunResult:
    current = start
    observed = start
    steps: list[dict[str, Any]] = []
    records: list[SimulationTickRecord] = []
    blocked_dirs: Counter[str] = Counter()
    edge_fail_counts: Counter[Edge] = Counter()
    hard_failed_edges: set[Edge] = set()
    recent_positions: deque[Coord] = deque(maxlen=8)
    best_distance = _manhattan(start, goal)
    stagnation = 0
    stop_reason = ""

    for tick in range(1, profile.max_iterations + 1):
        for direction in list(blocked_dirs):
            blocked_dirs[direction] -= 1
            if blocked_dirs[direction] <= 0:
                del blocked_dirs[direction]

        fault_flags = _fault_flags_for_tick(profile, tick)
        monster_blocks = _build_monster_blocks(game_map, current, goal, tick, profile.monster_patterns)
        avoid_edges = set(hard_failed_edges)
        if "blocked_dir_residual" in fault_flags:
            for direction in tuple(blocked_dirs):
                blocked_dirs[direction] = max(blocked_dirs[direction], 3)
        for edge, fail_count in edge_fail_counts.items():
            if fail_count >= 2:
                avoid_edges.add(edge)
        blocked_neighbor_tiles, open_neighbor_tiles = _analyze_neighbor_constraints(
            game_map,
            current,
            set(monster_blocks),
            avoid_edges,
        )

        if "coord_glitch" in fault_flags:
            observed = (current[0] + 100, current[1] + 100)
            _append_step(steps, "coord_glitch", current, f"좌표 오독 주입 ({current[0]},{current[1]})→({observed[0]},{observed[1]})")
        elif "coord_freeze" not in fault_flags:
            observed = current

        path = _find_path(game_map, current, goal, set(monster_blocks), avoid_edges)
        record_reason = ""
        action = "idle"

        if not path:
            record_reason = "최단경로 실패"
            _append_step(
                steps,
                "path_fail",
                current,
                f"최단경로 실패 ({current[0]},{current[1]})→({goal[0]},{goal[1]})",
                detail=f"avoid={sorted((x, y) for x, y, _ in avoid_edges)} monster={monster_blocks}",
                goal=goal,
            )
            if stagnation >= 1:
                _append_step(steps, "no_direction", current, f"방향 없음 ({current[0]},{current[1]})", goal=goal)
            stagnation += 1
        elif len(path) == 1:
            current = goal
            _append_step(steps, move_kind, current, f"{travel_label} 완료 ({current[0]},{current[1]})", goal=goal)
            records.append(
                SimulationTickRecord(
                    tick=tick,
                    position=current,
                    state=move_kind,
                    action="done",
                    reason="도착",
                    avoid_set=tuple(sorted((x, y) for x, y, _ in avoid_edges)),
                    blocked_dirs=tuple(sorted(blocked_dirs)),
                    edge_fail_counts=tuple(sorted(edge_fail_counts.items())),
                    monster_blocks=tuple(monster_blocks),
                    blocked_neighbor_tiles=blocked_neighbor_tiles,
                    fault_flags=tuple(fault_flags),
                    no_detour=False,
                )
            )
            return HarnessRunResult(steps=steps, records=records, final_pos=current, completed=True)
        else:
            next_pos = path[1]
            direction = _direction_between(current, next_pos) or "unknown"
            action = f"move:{direction}"
            move_failed = (current[0], current[1], direction) in profile.move_fail_edges or next_pos in monster_blocks
            if move_failed:
                edge = (current[0], current[1], direction)
                edge_fail_counts[edge] += 1
                blocked_dirs[direction] = max(blocked_dirs[direction], 3)
                if edge_fail_counts[edge] >= 2:
                    hard_failed_edges.add(edge)
                _append_step(steps, "move_fail", current, f"이동 실패 ({current[0]},{current[1]}) → {direction}", goal=goal)
                record_reason = "몬스터 또는 주입 실패"
                stagnation += 1
            else:
                current = next_pos
                recent_positions.append(current)
                remaining = _manhattan(current, goal)
                if remaining < best_distance:
                    best_distance = remaining
                    stagnation = 0
                else:
                    stagnation += 1
                _append_step(
                    steps,
                    move_kind,
                    current,
                    f"{travel_label} {direction} (잔여:{remaining}칸)",
                    goal=goal,
                )

        if len(recent_positions) >= 4 and list(recent_positions)[-4:] in ([recent_positions[-4], recent_positions[-3], recent_positions[-4], recent_positions[-3]],):
            stop_reason = "two_point_loop"
        elif len(recent_positions) >= 4 and len(set(list(recent_positions)[-4:])) == 1:
            stop_reason = "same_position_loop"
        elif stagnation >= profile.max_stagnation and any(monster_blocks):
            stop_reason = "monster_block_loop"
        elif stagnation >= profile.max_stagnation:
            stop_reason = "max_stagnation_reached"

        records.append(
            SimulationTickRecord(
                tick=tick,
                position=current,
                state=move_kind,
                action=action,
                reason=record_reason,
                avoid_set=tuple(sorted((x, y) for x, y, _ in avoid_edges)),
                blocked_dirs=tuple(sorted(blocked_dirs)),
                edge_fail_counts=tuple(sorted(edge_fail_counts.items())),
                monster_blocks=tuple(monster_blocks),
                blocked_neighbor_tiles=blocked_neighbor_tiles,
                fault_flags=tuple(fault_flags),
                no_detour=not path and not open_neighbor_tiles,
                stop_reason=stop_reason,
            )
        )
        if stop_reason:
            _append_step(steps, "stopped", current, f"중단: {FAILURE_SUMMARY.get(stop_reason, stop_reason)}", goal=goal)
            return HarnessRunResult(steps=steps, records=records, final_pos=current, stop_reason=stop_reason, completed=False)

    stop_reason = "max_iterations_reached"
    _append_step(steps, "stopped", current, f"중단: {FAILURE_SUMMARY[stop_reason]}", goal=goal)
    return HarnessRunResult(steps=steps, records=records, final_pos=current, stop_reason=stop_reason, completed=False)


def _boss_signal_frame(tick: int, current: Coord, boss_pos: Coord, profile: SimulationFaultProfile) -> tuple[str, bool, Coord, list[str]]:
    fault_flags = _fault_flags_for_tick(profile, tick)
    if "boss_reacquire_fail" in fault_flags:
        return "reacquire_fail", False, boss_pos, fault_flags
    if "boss_detect_drop" in fault_flags:
        return "drop", False, boss_pos, fault_flags
    if "boss_false_positive" in fault_flags:
        decoy = (boss_pos[0] + 2, boss_pos[1] - 2)
        return "decoy", True, decoy, fault_flags
    if "boss_jitter" in fault_flags:
        jitter = (boss_pos[0] + (1 if tick % 2 == 0 else -1), boss_pos[1] + (1 if tick % 3 == 0 else 0))
        return "jitter", True, jitter, fault_flags
    return "stable", True, boss_pos, fault_flags


def _best_boss_adjacent(game_map: GameMap, current: Coord, perceived_boss: Coord, monster_blocks: list[Coord], avoid_edges: set[Edge]) -> Optional[tuple[Coord, list[Coord]]]:
    candidates: list[tuple[int, int, Coord, list[Coord]]] = []
    for direction in ("up", "right", "down", "left"):
        goal = _step_from_dir(perceived_boss, direction)
        if not game_map.is_passable(goal[0], goal[1]):
            continue
        if goal in monster_blocks:
            continue
        path = _find_path(game_map, current, goal, set(monster_blocks), avoid_edges)
        if not path:
            continue
        candidates.append((len(path), _manhattan(goal, perceived_boss), goal, path))
    if not candidates:
        return None
    _, _, goal, path = min(candidates, key=lambda item: (item[0], item[1]))
    return goal, path


def run_boss_harness(
    game_map: GameMap,
    *,
    start: Coord,
    boss_pos: Coord,
    profile: SimulationFaultProfile,
) -> HarnessRunResult:
    current = start
    steps: list[dict[str, Any]] = []
    records: list[SimulationTickRecord] = []
    blocked_dirs: Counter[str] = Counter()
    edge_fail_counts: Counter[Edge] = Counter()
    hard_failed_edges: set[Edge] = set()
    stable_contact_frames = 0
    stagnation = 0
    visibility_flaps = 0
    prev_visible: Optional[bool] = None
    stop_reason = ""

    for tick in range(1, profile.max_iterations + 1):
        for direction in list(blocked_dirs):
            blocked_dirs[direction] -= 1
            if blocked_dirs[direction] <= 0:
                del blocked_dirs[direction]

        signal, visible, perceived_boss, fault_flags = _boss_signal_frame(tick, current, boss_pos, profile)
        if prev_visible is not None and prev_visible != visible:
            visibility_flaps += 1
        prev_visible = visible
        monster_blocks = _build_monster_blocks(game_map, current, boss_pos, tick, profile.monster_patterns, boss_pos=boss_pos)
        avoid_edges = set(hard_failed_edges)
        for edge, fail_count in edge_fail_counts.items():
            if fail_count >= 2:
                avoid_edges.add(edge)
        blocked_neighbor_tiles, open_neighbor_tiles = _analyze_neighbor_constraints(
            game_map,
            current,
            set(monster_blocks),
            avoid_edges,
        )

        if visible:
            _append_step(
                steps,
                "boss_detect",
                current,
                f"보스 신호 감지 ({signal})",
                detail=f"실보스={boss_pos} 인식보스={perceived_boss}",
                boss_pos=perceived_boss,
            )
        else:
            _append_step(
                steps,
                "boss_reacquire_fail",
                current,
                "보스 재감지 실패",
                detail=f"tick={tick} signal={signal}",
                boss_pos=boss_pos,
            )
            stagnation += 1
            if tick in profile.boss_reacquire_fail_ticks and stagnation >= max(4, profile.max_stagnation // 2):
                stop_reason = "boss_reacquire_fail"

        if _manhattan(current, boss_pos) == 1 and visible and signal in {"stable", "jitter"}:
            stable_contact_frames += 1
            if stable_contact_frames >= 2:
                _append_step(steps, "boss_contact", current, f"보스 밀착 성공 ({current[0]},{current[1]})", boss_pos=boss_pos)
                records.append(
                    SimulationTickRecord(
                        tick=tick,
                        position=current,
                        state="boss_chasing",
                        action="boss_contact",
                    reason="밀착 성공",
                    avoid_set=tuple(sorted((x, y) for x, y, _ in avoid_edges)),
                    blocked_dirs=tuple(sorted(blocked_dirs)),
                    edge_fail_counts=tuple(sorted(edge_fail_counts.items())),
                    monster_blocks=tuple(monster_blocks),
                    blocked_neighbor_tiles=blocked_neighbor_tiles,
                    fault_flags=tuple(fault_flags),
                    boss_signal=signal,
                    no_detour=False,
                )
            )
            return HarnessRunResult(steps=steps, records=records, final_pos=current, completed=True)
        else:
            stable_contact_frames = 0

        if visible and not stop_reason:
            target = _best_boss_adjacent(game_map, current, perceived_boss, monster_blocks, avoid_edges)
            if target is None:
                _append_step(steps, "path_fail", current, "보스 접근 경로 실패", detail=f"signal={signal} boss={perceived_boss}", boss_pos=perceived_boss)
                stagnation += 1
            else:
                goal, path = target
                if len(path) < 2:
                    stagnation += 1
                else:
                    next_pos = path[1]
                    direction = _direction_between(current, next_pos) or "unknown"
                    if next_pos in monster_blocks:
                        edge = (current[0], current[1], direction)
                        edge_fail_counts[edge] += 1
                        blocked_dirs[direction] = max(blocked_dirs[direction], 3)
                        if edge_fail_counts[edge] >= 2:
                            hard_failed_edges.add(edge)
                        _append_step(steps, "move_fail", current, f"보스 접근 실패 ({current[0]},{current[1]}) → {direction}", boss_pos=perceived_boss)
                        stagnation += 1
                    else:
                        current = next_pos
                        _append_step(
                            steps,
                            "boss_approach",
                            current,
                            f"보스 방향 접근 {direction}",
                            detail=f"목표={goal} 실보스={boss_pos} 인식보스={perceived_boss}",
                            goal=goal,
                            boss_pos=perceived_boss,
                        )
                        stagnation = max(0, stagnation - 1)

        if visibility_flaps >= 6:
            stop_reason = stop_reason or "boss_visibility_flap"
        elif stagnation >= profile.max_stagnation and any(monster_blocks):
            stop_reason = stop_reason or "boss_blocked"
        elif stagnation >= profile.max_stagnation:
            stop_reason = stop_reason or "max_stagnation_reached"

        records.append(
            SimulationTickRecord(
                tick=tick,
                position=current,
                state="boss_chasing",
                action=steps[-1]["kind"] if steps else "idle",
                reason=steps[-1]["message"] if steps else "",
                avoid_set=tuple(sorted((x, y) for x, y, _ in avoid_edges)),
                blocked_dirs=tuple(sorted(blocked_dirs)),
                edge_fail_counts=tuple(sorted(edge_fail_counts.items())),
                monster_blocks=tuple(monster_blocks),
                blocked_neighbor_tiles=blocked_neighbor_tiles,
                fault_flags=tuple(fault_flags),
                boss_signal=signal,
                no_detour=not visible or not open_neighbor_tiles,
                stop_reason=stop_reason,
            )
        )
        if stop_reason:
            _append_step(steps, "stopped", current, f"중단: {FAILURE_SUMMARY.get(stop_reason, stop_reason)}", boss_pos=boss_pos)
            return HarnessRunResult(steps=steps, records=records, final_pos=current, stop_reason=stop_reason, completed=False)

    stop_reason = "max_iterations_reached"
    _append_step(steps, "stopped", current, f"중단: {FAILURE_SUMMARY[stop_reason]}", boss_pos=boss_pos)
    return HarnessRunResult(steps=steps, records=records, final_pos=current, stop_reason=stop_reason, completed=False)


def _infer_stop_reason(steps: list[dict[str, Any]], explicit_reason: str) -> str:
    if explicit_reason:
        return explicit_reason
    kinds = [step.get("kind", "") for step in steps]
    if "boss_reacquire_fail" in kinds:
        return "boss_reacquire_fail"
    if "coord_glitch" in kinds:
        return "max_stagnation_reached"
    if "path_fail" in kinds and "no_direction" in kinds:
        return "monster_block_loop"
    return "max_iterations_reached"


def _build_failure_chain(reason: str, records: list[SimulationTickRecord]) -> str:
    notes: list[str] = []
    if any(record.monster_blocks for record in records):
        notes.append("몬스터 점유가 경로를 잘랐습니다")
    if any("coord_glitch" in record.fault_flags for record in records):
        notes.append("좌표 오독이 판단을 흔들었습니다")
    if any("coord_freeze" in record.fault_flags for record in records):
        notes.append("좌표 고정이 이동 확인을 막았습니다")
    if any(record.boss_signal in {"drop", "reacquire_fail", "jitter", "decoy"} for record in records):
        notes.append("보스 인식 신호가 불안정했습니다")
    if any("z_sticky" in record.fault_flags for record in records):
        notes.append("Z 상태 정리가 누락되었습니다")
    if any("portal_wait_stall" in record.fault_flags for record in records):
        notes.append("전환 대기가 끝나지 않았습니다")
    if reason == "monster_block_loop" and not notes:
        notes.append("몬스터 점유와 방향차단이 누적되었습니다")
    return " -> ".join(notes) if notes else "연쇄반응 없음"


def _build_failure_details(reason: str, final_pos: Coord, profile: SimulationFaultProfile, records: list[SimulationTickRecord]) -> str:
    last = records[-1] if records else None
    summary = FAILURE_SUMMARY.get(reason, reason or "원인 미상")
    chain = _build_failure_chain(reason, records)
    variables = ", ".join(_profile_variables(profile)) or "없음"
    last_state = f"{last.state}/{last.action}" if last is not None else "없음"
    last_analysis = (
        f"avoid={list(last.avoid_set)} blocked={list(last.blocked_dirs)} edge_fail={list(last.edge_fail_counts)} "
        f"monster={list(last.monster_blocks)} boss_signal={last.boss_signal}"
        if last is not None else "기록 없음"
    )
    return (
        f"실패요약: {summary} | 최종좌표: {final_pos} | 연쇄반응: {chain} | "
        f"주입변수: {variables} | 마지막상태: {last_state} | 마지막분석: {last_analysis}"
    )


def classify_scan_result(
    *,
    segment_name: str,
    seed: int,
    profile: SimulationFaultProfile,
    final_pos: Coord,
    stop_reason: str,
    completed: bool,
    steps: list[dict[str, Any]],
    records: list[SimulationTickRecord],
) -> SimulationScanResult:
    reason = "" if completed else _infer_stop_reason(steps, stop_reason)
    status = "PASS" if completed and not reason else "FAIL"
    summary = "정상 완료" if status == "PASS" else FAILURE_SUMMARY.get(reason, reason or "실패")
    details = (
        f"완료요약: 정상 종료 | 최종좌표: {final_pos} | 주입변수: {', '.join(_profile_variables(profile)) or '없음'}"
        if status == "PASS"
        else _build_failure_details(reason, final_pos, profile, records)
    )
    return SimulationScanResult(
        segment_name=segment_name,
        seed=seed,
        profile_name=profile.name,
        status=status,
        stop_reason=reason,
        summary=summary,
        details=details,
        final_pos=final_pos,
        culprit_variables=_profile_variables(profile),
    )


