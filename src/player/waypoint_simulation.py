from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import customtkinter as ctk
import tkinter as tk
from tkinter import ttk

from .game_map import GameMap
from .special_mode_harness import (
    PRESET_BOSS_REACQUIRE,
    PRESET_COORD_GLITCH,
    PRESET_NORMAL,
    PRESET_ROUTE_STAGNATION,
    SimulationFaultProfile,
    SimulationScanResult,
    SimulationTickRecord,
    build_fault_profile,
    classify_scan_result,
    generate_scan_profiles,
    run_boss_harness,
    run_route_harness,
)

Coord = tuple[int, int]


@dataclass
class SimulationStep:
    kind: str
    position: Optional[Coord]
    message: str
    detail: str = ""
    delay_ms: int = 70
    goal: Optional[Coord] = None
    boss_pos: Optional[Coord] = None


@dataclass
class WaypointSimulationScenario:
    segment_name: str
    game_map: GameMap
    start_pos: Coord
    goal_pos: Optional[Coord]
    is_boss_room: bool
    profile: SimulationFaultProfile
    steps: list[SimulationStep] = field(default_factory=list)
    records: list[SimulationTickRecord] = field(default_factory=list)
    scan_result: Optional[SimulationScanResult] = None


@dataclass
class SimulationBatchTarget:
    segment_name: str
    is_boss_room: bool
    scenario_factory: Callable[..., Optional[WaypointSimulationScenario]]


def _meta_from_waypoint(waypoint) -> dict[str, Any]:
    if isinstance(waypoint, (list, tuple)) and len(waypoint) >= 4 and isinstance(waypoint[3], dict):
        return waypoint[3]
    return {}


def _coord_from_meta_list(items: Any) -> Optional[Coord]:
    if not items:
        return None
    first = items[0]
    if isinstance(first, dict) and "x" in first and "y" in first:
        return int(first["x"]), int(first["y"])
    if isinstance(first, (list, tuple)) and len(first) >= 2:
        return int(first[0]), int(first[1])
    return None


def _arrival_key_text(meta: dict[str, Any]) -> Optional[str]:
    arr_keys = meta.get("arrival_keys") or []
    if arr_keys:
        return ",".join(str(item.get("key", "?")) for item in arr_keys)
    return None


def _choose_start(game_map: GameMap, waypoint) -> Coord:
    meta = _meta_from_waypoint(waypoint)
    route_start = _coord_from_meta_list(meta.get("route_starts"))
    if route_start is not None:
        return route_start
    if game_map.start_pos is not None:
        return game_map.start_pos
    if game_map.patrol_points:
        return tuple(game_map.patrol_points[0])
    if game_map.passable:
        return sorted(game_map.passable)[0]
    return 0, 0


def _choose_goal(game_map: GameMap, waypoint) -> Optional[Coord]:
    meta = _meta_from_waypoint(waypoint)
    route_end = _coord_from_meta_list(meta.get("route_ends"))
    if route_end is not None:
        return route_end
    if isinstance(waypoint, (list, tuple)) and len(waypoint) >= 2:
        wx, wy = int(waypoint[0]), int(waypoint[1])
        if not (wx == 0 and wy == 0):
            return wx, wy
    if game_map.end_pos is not None:
        return game_map.end_pos
    return None


def _choose_boss_spawn(game_map: GameMap, start: Coord, rng: random.Random) -> Optional[Coord]:
    candidates: list[Coord] = []
    for pos in sorted(game_map.passable):
        if abs(pos[0] - start[0]) + abs(pos[1] - start[1]) < 5:
            continue
        candidates.append(pos)
    return rng.choice(candidates) if candidates else None


def get_waypoint_simulation_preset_names(is_boss_room: bool) -> list[str]:
    names = [PRESET_NORMAL, PRESET_ROUTE_STAGNATION, PRESET_COORD_GLITCH]
    if is_boss_room:
        names.append(PRESET_BOSS_REACQUIRE)
    return names


def build_full_test_profiles(*, seed_start: int, count: int, is_boss_room: bool) -> list[SimulationFaultProfile]:
    profiles: list[SimulationFaultProfile] = []
    for seed in range(seed_start, seed_start + max(1, count)):
        profiles.append(
            build_fault_profile(
                PRESET_NORMAL,
                seed=seed,
                start=(0, 0),
                goal=None,
                is_boss_room=is_boss_room,
            )
        )
    profiles.extend(generate_scan_profiles(seed_start=seed_start, count=count, is_boss_room=is_boss_room))
    return profiles


def summarize_scan_results(results: list[SimulationScanResult]) -> dict[str, int | str]:
    total = len(results)
    passed = sum(1 for result in results if result.status == "PASS")
    failed = total - passed
    normal_failed = sum(1 for result in results if result.status != "PASS" and result.profile_name == PRESET_NORMAL)
    fault_failed = sum(1 for result in results if result.status != "PASS" and result.profile_name != PRESET_NORMAL)
    if normal_failed:
        verdict = "버그 있음"
    elif fault_failed:
        verdict = "취약점 있음"
    else:
        verdict = "이상 없음"
    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "normal_failed": normal_failed,
        "fault_failed": fault_failed,
        "verdict": verdict,
    }


def build_waypoint_harness_config(
    game_map: GameMap,
    waypoint,
    *,
    preset_name: str = PRESET_NORMAL,
    rng_seed: Optional[int] = None,
) -> SimulationFaultProfile:
    start = _choose_start(game_map, waypoint)
    goal = _choose_goal(game_map, waypoint)
    is_boss_room = isinstance(waypoint, (list, tuple)) and len(waypoint) >= 2 and int(waypoint[0]) == 0 and int(waypoint[1]) == 0
    return build_fault_profile(preset_name, seed=rng_seed or 1, start=start, goal=goal, is_boss_room=is_boss_room)


def _extend_steps_from_run(scenario: WaypointSimulationScenario, run_result, *, delay_ms: int = 70):
    for step in run_result.steps:
        scenario.steps.append(
            SimulationStep(
                step["kind"],
                step.get("position"),
                step["message"],
                step.get("detail", ""),
                delay_ms=delay_ms,
                goal=step.get("goal"),
                boss_pos=step.get("boss_pos"),
            )
        )
    scenario.records.extend(run_result.records)


def build_waypoint_simulation(
    game_map: GameMap,
    waypoint,
    *,
    segment_name: str = "",
    rng_seed: Optional[int] = None,
    harness_config: Optional[SimulationFaultProfile] = None,
) -> WaypointSimulationScenario:
    meta = _meta_from_waypoint(waypoint)
    start = _choose_start(game_map, waypoint)
    goal = _choose_goal(game_map, waypoint)
    is_boss_room = isinstance(waypoint, (list, tuple)) and len(waypoint) >= 2 and int(waypoint[0]) == 0 and int(waypoint[1]) == 0
    profile = harness_config or build_waypoint_harness_config(game_map, waypoint, preset_name=PRESET_NORMAL, rng_seed=rng_seed)
    scenario = WaypointSimulationScenario(
        segment_name=segment_name or (waypoint[2] if len(waypoint) >= 3 else "경유지"),
        game_map=game_map,
        start_pos=start,
        goal_pos=goal,
        is_boss_room=is_boss_room,
        profile=profile,
    )
    scenario.steps.append(SimulationStep("start", start, f"시뮬레이션 시작 ({start[0]},{start[1]})", delay_ms=120))

    if is_boss_room:
        rng = random.Random(rng_seed or profile.seed)
        patrol_points = list(game_map.patrol_points or [])
        current = start
        if patrol_points:
            patrol_limit = min(len(patrol_points), max(1, rng.randint(1, len(patrol_points))))
            for idx, patrol in enumerate(patrol_points[:patrol_limit], start=1):
                patrol_run = run_route_harness(
                    game_map,
                    start=current,
                    goal=tuple(patrol),
                    profile=profile,
                    move_kind="patrolling",
                    travel_label="순찰",
                )
                _extend_steps_from_run(scenario, patrol_run)
                current = patrol_run.final_pos
                scenario.steps.append(SimulationStep("patrol_reached", current, f"순찰 좌표 도달 {idx}/{patrol_limit} ({current[0]},{current[1]})"))
                if not patrol_run.completed:
                    scenario.scan_result = classify_scan_result(
                        segment_name=scenario.segment_name,
                        seed=profile.seed,
                        profile=profile,
                        final_pos=current,
                        stop_reason=patrol_run.stop_reason,
                        completed=False,
                        steps=[step.__dict__ for step in scenario.steps],
                        records=scenario.records,
                    )
                    return scenario

        boss_spawn = _choose_boss_spawn(game_map, current, rng)
        if boss_spawn is None:
            scenario.steps.append(SimulationStep("stopped", current, "보스 생성 실패", "맵 후보가 없습니다"))
            scenario.scan_result = classify_scan_result(
                segment_name=scenario.segment_name,
                seed=profile.seed,
                profile=profile,
                final_pos=current,
                stop_reason="boss_blocked",
                completed=False,
                steps=[step.__dict__ for step in scenario.steps],
                records=scenario.records,
            )
            return scenario

        scenario.steps.append(SimulationStep("boss_spawn", boss_spawn, f"보스 출현 ({boss_spawn[0]},{boss_spawn[1]})", boss_pos=boss_spawn))
        boss_run = run_boss_harness(game_map, start=current, boss_pos=boss_spawn, profile=profile)
        _extend_steps_from_run(scenario, boss_run)
        current = boss_run.final_pos
        stop_reason = boss_run.stop_reason
        completed = boss_run.completed

        if completed:
            scenario.steps.append(SimulationStep("boss_skill", current, "보스 스킬 사용", boss_pos=boss_spawn))
            if profile.item_detect_miss_ticks:
                scenario.steps.append(SimulationStep("item_detect_fail", current, "아이템 탐지 실패", boss_pos=boss_spawn))
                stop_reason = "item_detect_miss"
                completed = False
            else:
                scenario.steps.append(SimulationStep("item_detect", current, "아이템 탐색 시작", boss_pos=boss_spawn))
                if profile.item_false_positive_ticks:
                    scenario.steps.append(SimulationStep("item_false_positive", current, "아이템 오탐 후보 감지", boss_pos=boss_spawn))
                scenario.steps.append(SimulationStep("item_z", current, "z 입력", "아이템 이동 스킬"))
                scenario.steps.append(SimulationStep("item_double_click", current, "아이템 더블클릭"))
                scenario.steps.append(SimulationStep("item_loot", current, ", 입력(먹기)"))
                if profile.z_sticky_ticks:
                    stop_reason = "z_state_residual"
                    completed = False
                else:
                    scenario.steps.append(SimulationStep("item_escape", current, "esc 입력", "z 상태 해제"))

        if completed:
            keys = _arrival_key_text(meta)
            if profile.portal_wait_stall_ticks:
                scenario.steps.append(SimulationStep("portal_wait", current, "전환 대기 지연", "포탈 확인이 끝나지 않음"))
                stop_reason = "portal_wait_stall"
                completed = False
            else:
                if keys:
                    scenario.steps.append(SimulationStep("arrival_keys", current, f"키입력: {keys}", "보스굴 종료 키입력"))
                scenario.steps.append(SimulationStep("done", current, "시뮬레이션 완료"))

        scenario.scan_result = classify_scan_result(
            segment_name=scenario.segment_name,
            seed=profile.seed,
            profile=profile,
            final_pos=current,
            stop_reason=stop_reason,
            completed=completed,
            steps=[step.__dict__ for step in scenario.steps],
            records=scenario.records,
        )
        return scenario

    if goal is None:
        scenario.steps.append(SimulationStep("stopped", start, "목표 좌표가 없습니다"))
        scenario.scan_result = classify_scan_result(
            segment_name=scenario.segment_name,
            seed=profile.seed,
            profile=profile,
            final_pos=start,
            stop_reason="max_iterations_reached",
            completed=False,
            steps=[step.__dict__ for step in scenario.steps],
            records=scenario.records,
        )
        return scenario

    route_run = run_route_harness(game_map, start=start, goal=goal, profile=profile, move_kind="route_only", travel_label="경로추적")
    _extend_steps_from_run(scenario, route_run)
    current = route_run.final_pos
    completed = route_run.completed
    stop_reason = route_run.stop_reason
    if completed:
        keys = _arrival_key_text(meta)
        if profile.portal_wait_stall_ticks:
            scenario.steps.append(SimulationStep("portal_wait", current, "전환 대기 지연", "포탈 확인이 끝나지 않음"))
            completed = False
            stop_reason = "portal_wait_stall"
        else:
            if keys:
                scenario.steps.append(SimulationStep("arrival_keys", current, f"키입력: {keys}", "도착 후 키입력"))
            scenario.steps.append(SimulationStep("done", current, "시뮬레이션 완료"))
    scenario.scan_result = classify_scan_result(
        segment_name=scenario.segment_name,
        seed=profile.seed,
        profile=profile,
        final_pos=current,
        stop_reason=stop_reason,
        completed=completed,
        steps=[step.__dict__ for step in scenario.steps],
        records=scenario.records,
    )
    return scenario


class WaypointSimulationWindow(ctk.CTkToplevel):
    def __init__(
        self,
        master,
        scenario: WaypointSimulationScenario,
        *,
        scenario_factory: Callable[..., Optional[WaypointSimulationScenario]],
        preset_names: list[str],
        batch_targets_factory: Optional[Callable[[], list[SimulationBatchTarget]]] = None,
    ):
        super().__init__(master)
        self.title("특화모드 시뮬레이션")
        self.geometry("1680x920")
        self.transient(master)
        self.attributes("-topmost", True)
        self.after(250, lambda: self.attributes("-topmost", False))

        self.scenario = scenario
        self._scenario_factory = scenario_factory
        self._batch_targets_factory = batch_targets_factory
        self._current_preset = scenario.profile.name
        self._job = None
        self._test_job = None
        self._step_index = 0
        self._player_pos = scenario.start_pos
        self._boss_pos = None
        self._goal_pos = scenario.goal_pos
        self._scan_results: list[SimulationScanResult] = []
        self._test_queue: list[dict[str, Any]] = []
        self._fix_items: list[str] = []
        self._test_total = 0

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        top = ctk.CTkFrame(self)
        top.grid(row=0, column=0, sticky="ew", padx=12, pady=12)
        for idx in range(10):
            top.grid_columnconfigure(idx, weight=0)
        top.grid_columnconfigure(9, weight=1)

        ctk.CTkLabel(top, text="프리셋").grid(row=0, column=0, padx=(8, 4), pady=8)
        self._preset_menu = ctk.CTkOptionMenu(top, values=preset_names, command=self._on_preset_changed)
        self._preset_menu.grid(row=0, column=1, padx=4, pady=8)
        self._preset_menu.set(scenario.profile.name)

        ctk.CTkLabel(top, text="seed").grid(row=0, column=2, padx=(12, 4), pady=8)
        self._seed_entry = ctk.CTkEntry(top, width=80)
        self._seed_entry.grid(row=0, column=3, padx=4, pady=8)
        self._seed_entry.insert(0, str(scenario.profile.seed))

        ctk.CTkLabel(top, text="횟수").grid(row=0, column=4, padx=(12, 4), pady=8)
        self._count_entry = ctk.CTkEntry(top, width=80)
        self._count_entry.grid(row=0, column=5, padx=4, pady=8)
        self._count_entry.insert(0, "50")

        ctk.CTkButton(top, text="재생", width=90, command=self._start).grid(row=0, column=6, padx=4, pady=8)
        ctk.CTkButton(top, text="한 단계", width=90, command=self._advance_once).grid(row=0, column=7, padx=4, pady=8)
        ctk.CTkButton(top, text="정지", width=90, command=self._stop).grid(row=0, column=8, padx=4, pady=8)
        ctk.CTkButton(top, text="전체테스트", width=110, command=self._start_full_test).grid(row=0, column=9, padx=4, pady=8, sticky="e")

        self._status_label = ctk.CTkLabel(top, text="준비")
        self._status_label.grid(row=1, column=0, columnspan=4, sticky="w", padx=8, pady=(0, 8))
        self._summary_label = ctk.CTkLabel(top, text="검사 요약: 대기")
        self._summary_label.grid(row=1, column=4, columnspan=6, sticky="e", padx=8, pady=(0, 8))

        body = ctk.CTkFrame(self)
        body.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        body.grid_columnconfigure(0, weight=5)
        body.grid_columnconfigure(1, weight=4)
        body.grid_rowconfigure(0, weight=1)

        canvas_wrap = ctk.CTkFrame(body, fg_color="transparent")
        canvas_wrap.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        canvas_wrap.grid_columnconfigure(0, weight=1)
        canvas_wrap.grid_rowconfigure(0, weight=1)

        self._canvas = tk.Canvas(canvas_wrap, background="#0f172a", highlightthickness=0)
        self._canvas.grid(row=0, column=0, sticky="nsew")
        self._canvas.bind("<Configure>", self._on_canvas_configure)
        self._h_scroll = ctk.CTkScrollbar(canvas_wrap, orientation="horizontal", command=self._canvas.xview)
        self._h_scroll.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        self._v_scroll = ctk.CTkScrollbar(canvas_wrap, orientation="vertical", command=self._canvas.yview)
        self._v_scroll.grid(row=0, column=1, sticky="ns", padx=(6, 0))
        self._canvas.configure(xscrollcommand=self._h_scroll.set, yscrollcommand=self._v_scroll.set)

        right = ctk.CTkFrame(body)
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_columnconfigure(1, weight=1)
        right.grid_rowconfigure(1, weight=1)
        right.grid_rowconfigure(3, weight=1)
        right.grid_rowconfigure(5, weight=1)

        self._debug_label = ctk.CTkLabel(right, text="로그")
        self._debug_label.grid(row=0, column=0, sticky="w", padx=6, pady=(6, 2))
        self._result_label = ctk.CTkLabel(right, text="경유지 중단사유")
        self._result_label.grid(row=0, column=1, sticky="w", padx=6, pady=(6, 2))

        self._debug = ctk.CTkTextbox(right, wrap="word")
        self._debug.grid(row=1, column=0, sticky="nsew", padx=(0, 4), pady=(0, 8))

        self._result_tree = ttk.Treeview(right, columns=("segment", "seed", "profile", "status", "reason", "summary"), show="headings", height=10)
        self._result_tree.grid(row=1, column=1, sticky="nsew", padx=(4, 0), pady=(0, 8))
        for key, text, width in (("segment", "경유지", 140), ("seed", "seed", 60), ("profile", "프로필", 130), ("status", "상태", 70), ("reason", "중단사유", 170), ("summary", "요약", 320)):
            self._result_tree.heading(key, text=text)
            self._result_tree.column(key, width=width, stretch=True)
        self._result_tree.tag_configure("pass", foreground="#2e7d32")
        self._result_tree.tag_configure("fail", foreground="#c62828")
        self._result_tree.bind("<<TreeviewSelect>>", self._on_result_selected)

        self._fix_label = ctk.CTkLabel(right, text="수정해야 할 부분")
        self._fix_label.grid(row=2, column=0, columnspan=2, sticky="w", padx=6, pady=(0, 2))
        self._fix_box = ctk.CTkTextbox(right, wrap="word", height=180)
        self._fix_box.grid(row=3, column=0, columnspan=2, sticky="nsew", padx=0, pady=(0, 8))

        self._load_scenario(scenario)
        self.after(100, self._draw_map)
        self.after(160, self._start)

    def _read_seed(self) -> int:
        try:
            return max(1, int(self._seed_entry.get().strip()))
        except Exception:
            return 1

    def _read_count(self) -> int:
        try:
            return max(1, min(1000, int(self._count_entry.get().strip())))
        except Exception:
            return 50

    def _format_fix_item(self, result: SimulationScanResult) -> str:
        culprit = ", ".join(result.culprit_variables) if result.culprit_variables else "없음"
        return (
            f"- [{result.segment_name}] {result.summary}\n"
            f"  중단사유: {result.stop_reason or '없음'}\n"
            f"  주입변수: {culprit}\n"
            f"  상세: {result.details}"
        )

    def _refresh_fix_box(self):
        self._fix_box.delete("1.0", "end")
        if not self._fix_items:
            self._fix_box.insert("end", "현재 수정 필요 항목이 없습니다.\n")
            return
        self._fix_box.insert("end", "\n\n".join(self._fix_items) + "\n")

    def _load_scenario(self, scenario: WaypointSimulationScenario):
        self.scenario = scenario
        self._current_preset = scenario.profile.name
        self._step_index = 0
        self._player_pos = scenario.start_pos
        self._boss_pos = None
        self._goal_pos = scenario.goal_pos
        self._status_label.configure(text=f"준비: {self._current_preset}")
        self._summary_label.configure(text=f"검사 요약: 현재 '{scenario.segment_name}' 재생")
        self._debug.delete("1.0", "end")
        self._debug.insert("end", f"[프로필] {scenario.profile.name} seed={scenario.profile.seed}\n")
        if scenario.scan_result is not None:
            self._debug.insert("end", f"[요약] {scenario.scan_result.summary}\n")
            self._debug.insert("end", f"[상세] {scenario.scan_result.details}\n")
            self._fix_items = [self._format_fix_item(scenario.scan_result)] if scenario.scan_result.status != "PASS" else []
        else:
            self._fix_items = []
        self._refresh_fix_box()
        self._draw_map()

    def _on_preset_changed(self, preset_name: str):
        if self._scenario_factory is None:
            return
        self._stop()
        scenario = self._scenario_factory(preset_name=preset_name, seed_override=self._read_seed(), profile_override=None)
        if scenario is not None:
            self._load_scenario(scenario)
            self.after(60, self._start)

    def _draw_map(self):
        canvas = self._canvas
        canvas.delete("all")
        gm = self.scenario.game_map
        record = self._current_record()
        monster_blocks = set(record.monster_blocks) if record is not None else set()
        blocked_neighbor_tiles = set(record.blocked_neighbor_tiles) if record is not None else set()
        avoid_tiles = set(record.avoid_set) if record is not None else set()
        no_detour = bool(record.no_detour) if record is not None else False
        bounds = gm.get_bounds()
        min_x, max_x = bounds["min_x"], bounds["max_x"]
        min_y, max_y = bounds["min_y"], bounds["max_y"]
        width = max(1, max_x - min_x + 1)
        height = max(1, max_y - min_y + 1)
        cw = max(420, canvas.winfo_width() or 760)
        ch = max(320, canvas.winfo_height() or 620)
        legend_h = 86
        inner_w = max(80, cw - 32)
        inner_h = max(80, ch - legend_h - 32)
        fit_tile = max(1, min(inner_w // width, inner_h // height))
        tile = min(24, max(10, fit_tile))
        grid_w = width * tile
        grid_h = height * tile
        world_w = max(cw, grid_w + 32)
        world_h = max(ch, grid_h + legend_h + 32)
        offset_x = 16 if grid_w + 32 > cw else max(16, (cw - grid_w) // 2)
        offset_y = 16 if grid_h + legend_h + 32 > ch else max(16, (inner_h - grid_h) // 2 + 8)
        canvas.configure(scrollregion=(0, 0, world_w, world_h))
        for y in range(min_y, max_y + 1):
            for x in range(min_x, max_x + 1):
                fill = "#111827"
                if (x, y) in gm.blocked:
                    fill = "#4b5563"
                elif (x, y) in gm.passable:
                    fill = "#1f2937"
                if self._goal_pos == (x, y):
                    fill = "#2e7d32"
                if self._boss_pos == (x, y):
                    fill = "#c62828"
                if self._player_pos == (x, y):
                    fill = "#1976d2"
                px = offset_x + (x - min_x) * tile
                py = offset_y + (y - min_y) * tile
                canvas.create_rectangle(px, py, px + tile, py + tile, fill=fill, outline="#0b1020")
                if (x, y) in avoid_tiles:
                    canvas.create_rectangle(px + 1, py + 1, px + tile - 1, py + tile - 1, outline="#d946ef", width=2)
                if (x, y) in monster_blocks:
                    canvas.create_rectangle(px + 1, py + 1, px + tile - 1, py + tile - 1, fill="#fb923c", outline="#f97316", width=2)
                    if tile >= 10:
                        canvas.create_text(px + tile / 2, py + tile / 2, text="M", fill="#111827", font=("Malgun Gothic", max(6, tile // 2), "bold"))
                if (x, y) in blocked_neighbor_tiles:
                    canvas.create_rectangle(px + 2, py + 2, px + tile - 2, py + tile - 2, outline="#ef4444", width=2)
                    if tile >= 10:
                        canvas.create_line(px + 3, py + 3, px + tile - 3, py + tile - 3, fill="#ef4444", width=2)
                        canvas.create_line(px + tile - 3, py + 3, px + 3, py + tile - 3, fill="#ef4444", width=2)
        if no_detour:
            player_px = offset_x + (self._player_pos[0] - min_x) * tile
            player_py = offset_y + (self._player_pos[1] - min_y) * tile
            canvas.create_rectangle(player_px - 2, player_py - 2, player_px + tile + 2, player_py + tile + 2, outline="#facc15", width=3)
            canvas.create_text(
                cw / 2,
                max(18, offset_y - 6),
                text="우회 경로 없음",
                fill="#facc15",
                font=("Malgun Gothic", 12, "bold"),
            )
        legend_y = world_h - 54
        canvas.create_rectangle(12, legend_y, 28, legend_y + 16, fill="#fb923c", outline="")
        canvas.create_text(36, legend_y + 8, anchor="w", text="몬스터 점유", fill="#e5e7eb", font=("Malgun Gothic", 10))
        canvas.create_rectangle(130, legend_y, 146, legend_y + 16, outline="#ef4444", width=2)
        canvas.create_text(154, legend_y + 8, anchor="w", text="막힌 인접 칸", fill="#e5e7eb", font=("Malgun Gothic", 10))
        canvas.create_rectangle(278, legend_y, 294, legend_y + 16, outline="#d946ef", width=2)
        canvas.create_text(302, legend_y + 8, anchor="w", text="회피 누적 칸", fill="#e5e7eb", font=("Malgun Gothic", 10))
        canvas.create_text(
            12,
            world_h - 18,
            anchor="sw",
            text=(
                f"현재: {self._player_pos} | 목표: {self._goal_pos} | 보스: {self._boss_pos} | "
                f"몬스터:{len(monster_blocks)} | 막힘:{len(blocked_neighbor_tiles)} | 우회없음:{'예' if no_detour else '아니오'}"
            ),
            fill="#e5e7eb",
            font=("Malgun Gothic", 10),
        )

    def _on_canvas_configure(self, _event=None):
        try:
            self.after_idle(self._draw_map)
        except Exception:
            pass

    def _current_record(self) -> Optional[SimulationTickRecord]:
        if not self.scenario.records:
            return None
        idx = min(max(self._step_index - 1, 0), len(self.scenario.records) - 1)
        return self.scenario.records[idx]

    def _apply_step(self, step: SimulationStep):
        if step.position is not None:
            self._player_pos = step.position
        if step.boss_pos is not None:
            self._boss_pos = step.boss_pos
        if step.goal is not None:
            self._goal_pos = step.goal
        self._status_label.configure(text=step.message)
        self._debug.insert("end", f"[{self._step_index}] {step.message} {step.detail}\n")
        record = self._current_record()
        if record is not None:
            self._debug.insert(
                "end",
                (
                    f"tick={record.tick} state={record.state} action={record.action} reason={record.reason} "
                    f"avoid={list(record.avoid_set)} blocked={list(record.blocked_dirs)} edge_fail={list(record.edge_fail_counts)} "
                    f"monster={list(record.monster_blocks)} blocked_neighbors={list(record.blocked_neighbor_tiles)} "
                    f"no_detour={record.no_detour} fault={list(record.fault_flags)} boss_signal={record.boss_signal} stop={record.stop_reason}\n"
                ),
            )
        self._debug.see("end")
        self._draw_map()

    def _advance_once(self):
        self._stop()
        if self._step_index >= len(self.scenario.steps):
            return
        step = self.scenario.steps[self._step_index]
        self._step_index += 1
        self._apply_step(step)

    def _tick(self):
        if self._step_index >= len(self.scenario.steps):
            self._job = None
            return
        step = self.scenario.steps[self._step_index]
        self._step_index += 1
        self._apply_step(step)
        self._job = self.after(max(30, int(step.delay_ms)), self._tick)

    def _start(self):
        self._stop()
        self._tick()

    def _stop(self):
        if self._job is not None:
            try:
                self.after_cancel(self._job)
            except Exception:
                pass
            self._job = None

    def _prepare_results(self):
        self._scan_results.clear()
        self._fix_items.clear()
        self._refresh_fix_box()
        for item in self._result_tree.get_children():
            self._result_tree.delete(item)

    def _build_test_queue_for_target(self, target: SimulationBatchTarget) -> list[dict[str, Any]]:
        queue: list[dict[str, Any]] = []
        for profile in build_full_test_profiles(seed_start=self._read_seed(), count=self._read_count(), is_boss_room=target.is_boss_room):
            queue.append({"segment_name": target.segment_name, "profile": profile, "scenario_factory": target.scenario_factory})
        return queue

    def _start_full_test(self):
        self._stop()
        if self._test_job is not None:
            try:
                self.after_cancel(self._test_job)
            except Exception:
                pass
            self._test_job = None
        self._prepare_results()
        self._test_queue = []
        targets = self._batch_targets_factory() if self._batch_targets_factory is not None else [SimulationBatchTarget(segment_name=self.scenario.segment_name, is_boss_room=self.scenario.is_boss_room, scenario_factory=self._scenario_factory)]
        for target in targets:
            if target.scenario_factory is not None:
                self._test_queue.extend(self._build_test_queue_for_target(target))
        self._test_total = len(self._test_queue)
        self._summary_label.configure(text=f"검사 요약: 전체테스트 시작 (0/{self._test_total})")
        self._run_next_test()

    def _run_next_test(self):
        if not self._test_queue:
            self._update_result_summary()
            self._test_job = None
            return
        task = self._test_queue.pop(0)
        scenario = task["scenario_factory"](preset_name=task["profile"].name, seed_override=task["profile"].seed, profile_override=task["profile"])
        if scenario is not None and scenario.scan_result is not None:
            result = scenario.scan_result
            result.scenario = scenario
            self._scan_results.append(result)
            self._result_tree.insert(
                "",
                "end",
                values=(result.segment_name, result.seed, result.profile_name, result.status, result.stop_reason, result.summary),
                tags=(("pass" if result.status == "PASS" else "fail"),),
            )
            if result.status != "PASS":
                self._fix_items.append(self._format_fix_item(result))
                self._refresh_fix_box()
            self._update_result_summary()
        self._test_job = self.after(1, self._run_next_test)

    def _update_result_summary(self):
        summary = summarize_scan_results(self._scan_results)
        total_label = self._test_total if self._test_total else summary["total"]
        self._summary_label.configure(text=f"검사 요약: {summary['verdict']} | 진행 {summary['total']}/{total_label} | 정상실패 {summary['normal_failed']} | 오류주입실패 {summary['fault_failed']} | 통과 {summary['passed']}")

    def _on_result_selected(self, _event):
        sel = self._result_tree.selection()
        if not sel:
            return
        index = self._result_tree.index(sel[0])
        if 0 <= index < len(self._scan_results):
            result = self._scan_results[index]
            if result.scenario is not None:
                self._load_scenario(result.scenario)
                self._debug.insert("end", f"[결과] segment={result.segment_name} seed={result.seed}\n")
                self._debug.insert("end", f"[판정] {result.status} / {result.summary}\n")
                self._debug.insert("end", f"[설명] {result.details}\n\n")
                self._fix_items = [self._format_fix_item(result)] if result.status != "PASS" else []
                self._refresh_fix_box()
                self.after(60, self._start)

