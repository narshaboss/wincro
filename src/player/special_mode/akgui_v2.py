"""Akgui-only coordinate navigation engine.

This runner intentionally does not import or call the Wongak legacy runner.
It owns Akgui transition, obstacle, target-selection, and recovery policy.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import random
import time
from typing import Optional

from ...special_mode_profiles import (
    AKGUI_V2_PROFILE,
    normalize_special_mode_profile,
)
from ..game_map import DIRECTIONS_4


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AkguiSegment:
    index: int
    name: str
    targets: tuple[tuple[int, int], ...]
    map_locked: bool


class AkguiV2CoordinateRunner:
    """Independent Akgui navigation state machine."""

    profile_id = AKGUI_V2_PROFILE
    transition_jump_distance = 12
    transition_confirm_hits = 2
    coordinate_failure_limit = 30
    edge_cooldown_seconds = 2.5
    wall_promotion_failures = 5
    max_no_progress_iterations = 5000
    transition_wait_seconds = 20.0

    _direction_order = ("up", "right", "down", "left")

    def __init__(self, host):
        self.host = host
        self.config = host._config
        configured = normalize_special_mode_profile(
            getattr(self.config, "engine_profile", "")
        )
        if configured != self.profile_id:
            raise RuntimeError(
                "Akgui engine received a foreign profile: "
                f"configured={configured}"
            )
        self.stop_event = host._stop_event
        self._edge_failures: dict[tuple[int, int, str], int] = {}
        self._edge_cooldowns: dict[tuple[int, int, str], float] = {}
        self._last_direction: Optional[str] = None
        self._last_move_origin: Optional[tuple[int, int]] = None
        self._pending_jump: Optional[tuple[int, int]] = None
        self._pending_jump_hits = 0

    @staticmethod
    def _meta_for_waypoint(waypoint) -> dict:
        if (
            isinstance(waypoint, (list, tuple))
            and len(waypoint) >= 4
            and isinstance(waypoint[3], dict)
        ):
            return waypoint[3]
        return {}

    @classmethod
    def build_segments(cls, config) -> tuple[AkguiSegment, ...]:
        segments: list[AkguiSegment] = []
        for index, waypoint in enumerate(getattr(config, "waypoints", ()) or ()):
            if not isinstance(waypoint, (list, tuple)) or len(waypoint) < 2:
                continue
            meta = cls._meta_for_waypoint(waypoint)
            targets: list[tuple[int, int]] = []
            for item in meta.get("route_ends", ()) or ():
                if not isinstance(item, dict) or not item.get("enabled", True):
                    continue
                try:
                    targets.append((int(item["x"]), int(item["y"])))
                except (KeyError, TypeError, ValueError):
                    continue
            if not targets:
                targets.append((int(waypoint[0]), int(waypoint[1])))
            name = (
                str(waypoint[2]).strip()
                if len(waypoint) >= 3 and str(waypoint[2]).strip()
                else f"경유지 {index + 1}"
            )
            segments.append(
                AkguiSegment(
                    index=index,
                    name=name,
                    targets=tuple(dict.fromkeys(targets)),
                    map_locked=bool(meta.get("map_locked", False)),
                )
            )
        return tuple(segments)

    @classmethod
    def validate_config(cls, config) -> None:
        for waypoint in getattr(config, "waypoints", ()) or ():
            meta = cls._meta_for_waypoint(waypoint)
            if meta.get("target_image") or meta.get("target_images"):
                raise ValueError(
                    "악귀문 알고리즘에는 원각 보스 이미지 경유지를 넣을 수 없습니다."
                )
            if meta.get("character_image") or meta.get("item_image"):
                raise ValueError(
                    "악귀문 알고리즘에는 원각 보스/아이템 정책을 넣을 수 없습니다."
                )
        if getattr(config, "boss_skill_enabled", False):
            raise ValueError("악귀문 알고리즘에서는 원각 보스 스킬 정책을 사용할 수 없습니다.")

    @staticmethod
    def _distance(a: tuple[int, int], b: tuple[int, int]) -> int:
        return abs(a[0] - b[0]) + abs(a[1] - b[1])

    @classmethod
    def _direction_candidates(
        cls,
        current: tuple[int, int],
        goal: tuple[int, int],
    ) -> list[str]:
        candidates = []
        for order, direction in enumerate(cls._direction_order):
            dx, dy = DIRECTIONS_4[direction]
            next_pos = (current[0] + dx, current[1] + dy)
            candidates.append((cls._distance(next_pos, goal), order, direction))
        candidates.sort()
        return [direction for _, _, direction in candidates]

    def _edge_key(self, current: tuple[int, int], direction: str) -> tuple[int, int, str]:
        return current[0], current[1], direction

    def _edge_available(self, current: tuple[int, int], direction: str, now: float) -> bool:
        return self._edge_cooldowns.get(self._edge_key(current, direction), 0.0) <= now

    def _map_blocks(self, current: tuple[int, int], direction: str) -> bool:
        game_map = getattr(self.host, "_game_map", None)
        if game_map is None:
            return False
        dx, dy = DIRECTIONS_4[direction]
        next_pos = current[0] + dx, current[1] + dy
        try:
            return bool(game_map.is_blocked(*next_pos))
        except Exception:
            return False

    def choose_direction(
        self,
        current: tuple[int, int],
        goal: tuple[int, int],
        now: Optional[float] = None,
    ) -> Optional[str]:
        now = time.monotonic() if now is None else now
        candidates = self._direction_candidates(current, goal)

        pathfinder = getattr(self.host, "_map_pathfinder", None)
        if pathfinder is not None:
            try:
                pathfinder.set_goal(goal)
                preferred = pathfinder.get_next_direction(
                    current,
                    allow_unknown=True,
                    stop_event=self.stop_event,
                    max_iterations=2500,
                    respect_blocked_edges=False,
                )
                if preferred in candidates:
                    candidates.remove(preferred)
                    candidates.insert(0, preferred)
            except Exception:
                logger.debug("[악귀문V2] A* 후보 계산 실패", exc_info=True)

        for direction in candidates:
            if not self._edge_available(current, direction, now):
                continue
            if self._map_blocks(current, direction):
                continue
            return direction

        # Dynamic obstacles are temporary. Expire the oldest cooldown rather
        # than converting normal-play failures into a permanent wall.
        local_edges = [
            (expiry, key)
            for key, expiry in self._edge_cooldowns.items()
            if key[:2] == current
        ]
        if local_edges:
            _, oldest = min(local_edges)
            self._edge_cooldowns.pop(oldest, None)
            return oldest[2]
        return candidates[0] if candidates else None

    def _mapping_session_active(self, segment: AkguiSegment) -> bool:
        if segment.map_locked:
            return False
        if getattr(self.host, "_no_save_mode", False):
            return False
        return bool(
            getattr(self.host, "_is_mapping", False)
            or getattr(self.host, "_is_mapping_test", False)
        )

    def _record_success(
        self,
        segment: AkguiSegment,
        previous: Optional[tuple[int, int]],
        current: tuple[int, int],
    ) -> None:
        if not self._mapping_session_active(segment):
            return
        game_map = getattr(self.host, "_game_map", None)
        if game_map is None:
            return
        try:
            if previous is not None:
                game_map.mark_passable(*previous)
            game_map.mark_passable(*current)
        except Exception:
            logger.debug("[악귀문V2] 이동가능 타일 기록 실패", exc_info=True)

    def _record_failed_move(
        self,
        segment: AkguiSegment,
        origin: tuple[int, int],
        direction: str,
    ) -> None:
        edge = self._edge_key(origin, direction)
        failures = self._edge_failures.get(edge, 0) + 1
        self._edge_failures[edge] = failures
        self._edge_cooldowns[edge] = time.monotonic() + self.edge_cooldown_seconds

        if not self._mapping_session_active(segment):
            return
        if failures < self.wall_promotion_failures:
            return
        game_map = getattr(self.host, "_game_map", None)
        if game_map is None:
            return
        dx, dy = DIRECTIONS_4[direction]
        blocked = origin[0] + dx, origin[1] + dy
        try:
            game_map.mark_blocked(*blocked)
            self.host._append_log(
                f"🧱 [악귀문V2] 맵핑 반복실패 벽 확정: {blocked} "
                f"({failures}/{self.wall_promotion_failures})"
            )
        except Exception:
            logger.debug("[악귀문V2] 벽 기록 실패", exc_info=True)

    def _confirm_transition(
        self,
        previous: tuple[int, int],
        current: tuple[int, int],
    ) -> bool:
        if self._distance(previous, current) < self.transition_jump_distance:
            self._pending_jump = None
            self._pending_jump_hits = 0
            return False
        if current == self._pending_jump:
            self._pending_jump_hits += 1
        else:
            self._pending_jump = current
            self._pending_jump_hits = 1
        return self._pending_jump_hits >= self.transition_confirm_hits

    def _select_targets(
        self,
        segment: AkguiSegment,
        current: tuple[int, int],
    ) -> list[tuple[int, int]]:
        targets = list(segment.targets)
        if getattr(self.host, "_is_mapping_test", False) or getattr(
            self.host, "_is_mapping", False
        ):
            return targets
        if not targets:
            return []
        nearest_distance = min(self._distance(current, target) for target in targets)
        nearest = [
            target for target in targets
            if self._distance(current, target) == nearest_distance
        ]
        return [random.choice(nearest)]

    def _initial_segment_index(self, segments: tuple[AkguiSegment, ...]) -> int:
        if getattr(self.host, "_single_waypoint_mode", False):
            return max(
                0,
                min(
                    int(getattr(self.host, "_single_waypoint_idx", 0)),
                    len(segments) - 1,
                ),
            )
        if getattr(self.host, "_is_mapping", False):
            return max(
                0,
                min(
                    int(getattr(self.host, "_current_segment_idx", 0)),
                    len(segments) - 1,
                ),
            )
        return 0

    def _final_segment_index(self, segments: tuple[AkguiSegment, ...]) -> int:
        if getattr(self.host, "_single_waypoint_mode", False) and not getattr(
            self.host, "_is_mapping_test", False
        ):
            return self._initial_segment_index(segments)
        configured = int(getattr(self.config, "final_waypoint_idx", -1))
        if configured < 0 or configured >= len(segments):
            return len(segments) - 1
        if getattr(self.host, "_is_mapping_test", False):
            return len(segments) - 1
        return configured

    def _switch_segment(self, segment: AkguiSegment) -> None:
        self.host._current_segment_idx = segment.index
        self.host._switch_akgui_v2_segment_map(segment.index)
        self._edge_failures.clear()
        self._edge_cooldowns.clear()
        self._pending_jump = None
        self._pending_jump_hits = 0
        self._last_direction = None
        self._last_move_origin = None

    def _sleep_interval(self) -> None:
        interval = max(0.03, float(getattr(self.config, "analysis_interval", 0.1) or 0.1))
        self.stop_event.wait(interval)

    def run(self) -> None:
        """Run with an engine-local ESC hook and guaranteed input cleanup."""
        import keyboard

        hotkey_id = None
        try:
            hotkey_id = keyboard.add_hotkey(
                "escape",
                self.host._handle_escape_hotkey,
            )
            self._run_state_machine()
        finally:
            if hotkey_id is not None:
                try:
                    keyboard.remove_hotkey(hotkey_id)
                except Exception:
                    logger.debug("[악귀문V2] ESC 핫키 제거 실패", exc_info=True)
            try:
                from ...utils.input_controller import get_input_controller

                get_input_controller().release_all()
            except Exception:
                logger.debug("[악귀문V2] 입력 해제 실패", exc_info=True)

    def _run_state_machine(self) -> None:
        from ...utils.digit_templates import get_digit_matcher

        self.validate_config(self.config)
        segments = self.build_segments(self.config)
        if not segments:
            self.host._request_stop_execution(
                "akgui_no_waypoints",
                "악귀문 알고리즘 경유지가 없습니다.",
            )
            return

        matcher = get_digit_matcher()
        if not matcher.has_all_templates():
            self.host._request_stop_execution(
                "akgui_templates_incomplete",
                f"missing={matcher.get_missing_digits()}",
            )
            return

        start_index = self._initial_segment_index(segments)
        final_index = self._final_segment_index(segments)
        segment_index = start_index
        segment = segments[segment_index]
        self._switch_segment(segment)
        self.host._key_press_count = 0
        self.host._append_log(
            f"🚪 [악귀문V2] 독립 엔진 시작: {segment.name} "
            f"({segment_index + 1}/{final_index + 1})"
        )

        previous: Optional[tuple[int, int]] = None
        coordinate_failures = 0
        current_targets: list[tuple[int, int]] = []
        target_cursor = 0
        no_progress_iterations = 0
        best_distance: Optional[int] = None
        transition_wait_started: Optional[float] = None

        while not self.stop_event.is_set():
            x, y = self.host._read_game_coordinates(
                matcher,
                stop_event=self.stop_event,
            )
            if x is None or y is None:
                coordinate_failures += 1
                if coordinate_failures >= self.coordinate_failure_limit:
                    self.host._request_stop_execution(
                        "akgui_coord_fail_limit",
                        f"failures={coordinate_failures}",
                    )
                    return
                self._sleep_interval()
                continue

            coordinate_failures = 0
            current = int(x), int(y)
            self.host._remember_runtime_coordinate(
                current[0],
                current[1],
                source="akgui_v2",
                target_idx=segment.index,
            )

            if previous is not None and current != previous:
                jump_distance = self._distance(previous, current)
                if jump_distance >= self.transition_jump_distance:
                    transition_confirmed = self._confirm_transition(previous, current)
                    if not transition_confirmed:
                        self.host._append_log(
                            f"🔎 [악귀문V2] 맵 전환 좌표 재확인: "
                            f"{previous}→{current} "
                            f"({self._pending_jump_hits}/{self.transition_confirm_hits})"
                        )
                        self._sleep_interval()
                        continue
                else:
                    transition_confirmed = False
                    self._pending_jump = None
                    self._pending_jump_hits = 0

                if transition_confirmed:
                    if segment_index >= final_index:
                        self.host._append_log(
                            f"✅ [악귀문V2] 최종 구간 전환 확인: {previous}→{current}"
                        )
                        self.host._queue_normal_completion()
                        return
                    old_name = segment.name
                    segment_index += 1
                    segment = segments[segment_index]
                    self._switch_segment(segment)
                    current_targets = []
                    target_cursor = 0
                    best_distance = None
                    no_progress_iterations = 0
                    transition_wait_started = None
                    self.host._append_log(
                        f"🔄 [악귀문V2] 맵 전환: {old_name} → {segment.name} "
                        f"좌표={previous}→{current}"
                    )
                    previous = current
                    self._sleep_interval()
                    continue
                self._record_success(segment, previous, current)
                if self._last_direction and self._last_move_origin:
                    edge = self._edge_key(self._last_move_origin, self._last_direction)
                    self._edge_failures.pop(edge, None)
                    self._edge_cooldowns.pop(edge, None)
            elif (
                previous is not None
                and current == previous
                and self._last_direction
                and self._last_move_origin == current
            ):
                self._record_failed_move(segment, current, self._last_direction)

            if not current_targets:
                current_targets = self._select_targets(segment, current)
                target_cursor = 0
                if not current_targets:
                    self.host._request_stop_execution(
                        "akgui_segment_without_target",
                        f"segment={segment.index}",
                    )
                    return
                self.host._append_log(
                    f"🎯 [악귀문V2] {segment.name} 목표: "
                    + " → ".join(f"({tx},{ty})" for tx, ty in current_targets)
                )

            goal = current_targets[target_cursor]
            distance = self._distance(current, goal)
            if distance == 0:
                if target_cursor + 1 < len(current_targets):
                    target_cursor += 1
                    goal = current_targets[target_cursor]
                    best_distance = None
                    no_progress_iterations = 0
                    self.host._append_log(
                        f"✅ [악귀문V2] 경유좌표 도착 → 다음 좌표 {goal}"
                    )
                elif segment_index >= final_index:
                    self.host._append_log(
                        f"✅ [악귀문V2] 최종 목표 도착: {segment.name} {goal}"
                    )
                    self.host._queue_normal_completion()
                    return
                else:
                    if transition_wait_started is None:
                        transition_wait_started = time.monotonic()
                        self.host._append_log(
                            f"⏳ [악귀문V2] {segment.name} 도착, 다음 맵 전환 대기"
                        )
                    elif (
                        time.monotonic() - transition_wait_started
                        >= self.transition_wait_seconds
                    ):
                        self.host._request_stop_execution(
                            "akgui_transition_timeout",
                            f"segment={segment.index} coord={current}",
                        )
                        return
                    previous = current
                    self._last_direction = None
                    self._last_move_origin = None
                    self._sleep_interval()
                    continue

            if best_distance is None or distance < best_distance:
                best_distance = distance
                no_progress_iterations = 0
            else:
                no_progress_iterations += 1
            if no_progress_iterations >= self.max_no_progress_iterations:
                self.host._request_stop_execution(
                    "akgui_no_progress_limit",
                    f"segment={segment.index} current={current} goal={goal} "
                    f"best_distance={best_distance}",
                )
                return

            direction = self.choose_direction(current, goal)
            if direction is None:
                self.host._append_log(
                    f"⏸ [악귀문V2] 이동 후보 없음: {current}→{goal}"
                )
                previous = current
                self._sleep_interval()
                continue

            self.host._press_direction_key(direction)
            self.host._key_press_count += 1
            self._last_direction = direction
            self._last_move_origin = current
            previous = current
            self._sleep_interval()
