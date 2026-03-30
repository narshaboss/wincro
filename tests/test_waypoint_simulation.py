from pathlib import Path

from src.player.game_map import GameMap
from src.player.special_mode_harness import (
    MONSTER_PATTERN_BOSS_GUARD,
    MONSTER_PATTERN_CHOKE,
    MONSTER_PATTERN_FRONT_HOLD,
    PRESET_BOSS_REACQUIRE,
    PRESET_COORD_GLITCH,
    PRESET_NORMAL,
    PRESET_ROUTE_STAGNATION,
    PRESET_SCAN,
    build_fault_profile,
    classify_scan_result,
    generate_scan_profiles,
    run_boss_harness,
)
from src.player.waypoint_simulation import (
    build_full_test_profiles,
    build_waypoint_harness_config,
    build_waypoint_simulation,
    get_waypoint_simulation_preset_names,
    summarize_scan_results,
)


PLAYER_VIEW = Path(r"C:\Projects\wincro\src\ui\player_view.py")
SIM_MODULE = Path(r"C:\Projects\wincro\src\player\waypoint_simulation.py")


def _open_grid_map(size: int = 8) -> GameMap:
    game_map = GameMap(name="sim-test")
    game_map.passable = {(x, y) for x in range(size) for y in range(size)}
    game_map.start_pos = (0, 0)
    game_map.end_pos = (size - 1, size - 1)
    return game_map


def test_build_waypoint_simulation_for_normal_waypoint():
    game_map = _open_grid_map()
    waypoint = [4, 4, "normal", {"route_starts": [{"x": 0, "y": 0}], "route_ends": [{"x": 4, "y": 4}], "arrival_keys": [{"key": "6"}, {"key": "enter"}]}]
    scenario = build_waypoint_simulation(game_map, waypoint, segment_name="normal", rng_seed=1)
    kinds = [step.kind for step in scenario.steps]
    assert scenario.scan_result is not None
    assert scenario.scan_result.status == "PASS"
    assert "route_only" in kinds
    assert "arrival_keys" in kinds
    assert kinds[-1] == "done"


def test_build_waypoint_simulation_for_boss_waypoint():
    game_map = _open_grid_map(10)
    game_map.patrol_points = [(0, 0), (0, 3), (3, 3)]
    waypoint = [0, 0, "boss", {"route_starts": [{"x": 0, "y": 0}], "item_image": "item.png", "arrival_keys": [{"key": "6"}, {"key": "2"}, {"key": "enter"}]}]
    scenario = build_waypoint_simulation(game_map, waypoint, segment_name="boss", rng_seed=7)
    kinds = [step.kind for step in scenario.steps]
    assert scenario.scan_result is not None
    assert "boss_spawn" in kinds
    assert "boss_detect" in kinds
    assert "boss_contact" in kinds
    assert "boss_skill" in kinds
    assert "item_escape" in kinds
    assert "arrival_keys" in kinds


def test_route_stagnation_preset_surfaces_failure_and_dynamic_monsters():
    game_map = GameMap(name="corridor")
    corridor = {
        (19, 14), (18, 14), (17, 14), (16, 14), (15, 14), (14, 14), (13, 14),
        (13, 15), (13, 16), (13, 17), (13, 18), (13, 19), (13, 20), (13, 21), (13, 22),
    }
    game_map.passable = set(corridor)
    for x in range(12, 21):
        for y in range(13, 24):
            pos = (x, y)
            if pos not in corridor:
                game_map.blocked.add(pos)
    waypoint = [13, 22, "corridor", {"route_starts": [{"x": 19, "y": 14}], "route_ends": [{"x": 13, "y": 22}]}]
    profile = build_waypoint_harness_config(game_map, waypoint, preset_name=PRESET_ROUTE_STAGNATION, rng_seed=1)
    scenario = build_waypoint_simulation(game_map, waypoint, segment_name="corridor", rng_seed=1, harness_config=profile)
    kinds = [step.kind for step in scenario.steps]
    monster_shapes = {tuple(record.monster_blocks) for record in scenario.records if record.monster_blocks}
    assert MONSTER_PATTERN_FRONT_HOLD in profile.monster_patterns
    assert MONSTER_PATTERN_CHOKE in profile.monster_patterns
    assert "path_fail" in kinds
    assert "stopped" in kinds
    assert scenario.scan_result is not None
    assert scenario.scan_result.status == "FAIL"
    assert "실패요약:" in scenario.scan_result.details
    assert len(monster_shapes) >= 1


def test_coord_glitch_preset_surfaces_coord_fault():
    game_map = _open_grid_map(8)
    waypoint = [4, 4, "normal", {"route_starts": [{"x": 0, "y": 0}], "route_ends": [{"x": 4, "y": 4}]}]
    profile = build_waypoint_harness_config(game_map, waypoint, preset_name=PRESET_COORD_GLITCH, rng_seed=1)
    scenario = build_waypoint_simulation(game_map, waypoint, segment_name="normal", rng_seed=1, harness_config=profile)
    assert any(step.kind == "coord_glitch" for step in scenario.steps)


def test_boss_reacquire_preset_surfaces_boss_reacquire_fail():
    game_map = _open_grid_map(12)
    game_map.patrol_points = [(0, 0), (0, 4), (4, 4)]
    waypoint = [0, 0, "boss", {"route_starts": [{"x": 0, "y": 0}]}]
    profile = build_waypoint_harness_config(game_map, waypoint, preset_name=PRESET_BOSS_REACQUIRE, rng_seed=5)
    scenario = build_waypoint_simulation(game_map, waypoint, segment_name="boss", rng_seed=5, harness_config=profile)
    assert MONSTER_PATTERN_BOSS_GUARD in profile.monster_patterns
    assert any(step.kind == "boss_reacquire_fail" for step in scenario.steps)


def test_scan_profile_generation_is_deterministic_and_boss_capable():
    boss_profiles = generate_scan_profiles(seed_start=1, count=5, is_boss_room=True)
    boss_profiles_again = generate_scan_profiles(seed_start=1, count=5, is_boss_room=True)
    normal_profiles = generate_scan_profiles(seed_start=1, count=3, is_boss_room=False)
    assert [p.seed for p in boss_profiles] == [1, 2, 3, 4, 5]
    assert [p.seed for p in boss_profiles] == [p.seed for p in boss_profiles_again]
    assert all(profile.name == PRESET_SCAN for profile in normal_profiles)
    assert any(profile.monster_patterns for profile in boss_profiles + normal_profiles)


def test_build_full_test_profiles_contains_normal_and_faults():
    profiles = build_full_test_profiles(seed_start=3, count=4, is_boss_room=True)
    names = [profile.name for profile in profiles]
    assert names[:4] == [PRESET_NORMAL] * 4
    assert any(name != PRESET_NORMAL for name in names)


def test_classify_scan_result_detects_loop_like_failures():
    profile = build_fault_profile(PRESET_NORMAL, seed=11, start=(0, 0), goal=(1, 1), is_boss_room=False)
    steps = [
        {"kind": "route_only", "position": (1, 1)},
        {"kind": "route_only", "position": (1, 2)},
        {"kind": "route_only", "position": (1, 1)},
        {"kind": "route_only", "position": (1, 2)},
    ]
    result = classify_scan_result(segment_name="loop-test", seed=11, profile=profile, final_pos=(1, 2), stop_reason="two_point_loop", completed=False, steps=steps, records=[])
    assert result.status == "FAIL"
    assert result.stop_reason == "two_point_loop"
    assert "실패요약:" in result.details
    assert "연쇄반응:" in result.details


def test_summarize_scan_results_reports_bug_and_weakness():
    results = [
        classify_scan_result(segment_name="a", seed=1, profile=build_fault_profile(PRESET_NORMAL, seed=1, start=(0, 0), goal=None, is_boss_room=False), final_pos=(0, 0), stop_reason="max_stagnation_reached", completed=False, steps=[{"kind": "stopped", "position": (0, 0)}], records=[]),
        classify_scan_result(segment_name="a", seed=2, profile=build_fault_profile(PRESET_ROUTE_STAGNATION, seed=2, start=(0, 0), goal=None, is_boss_room=False), final_pos=(0, 0), stop_reason="monster_block_loop", completed=False, steps=[{"kind": "stopped", "position": (0, 0)}], records=[]),
        classify_scan_result(segment_name="a", seed=3, profile=build_fault_profile(PRESET_NORMAL, seed=3, start=(0, 0), goal=None, is_boss_room=False), final_pos=(1, 1), stop_reason="", completed=True, steps=[{"kind": "done", "position": (1, 1)}], records=[]),
    ]
    summary = summarize_scan_results(results)
    assert summary["total"] == 3
    assert summary["passed"] == 1
    assert summary["normal_failed"] == 1
    assert summary["fault_failed"] == 1
    assert summary["verdict"] == "버그 있음"


def test_get_waypoint_simulation_preset_names():
    assert get_waypoint_simulation_preset_names(False) == [PRESET_NORMAL, PRESET_ROUTE_STAGNATION, PRESET_COORD_GLITCH]
    assert get_waypoint_simulation_preset_names(True) == [PRESET_NORMAL, PRESET_ROUTE_STAGNATION, PRESET_COORD_GLITCH, PRESET_BOSS_REACQUIRE]


def test_boss_harness_exposes_signal_instability():
    game_map = _open_grid_map(10)
    profile = build_fault_profile(PRESET_BOSS_REACQUIRE, seed=9, start=(0, 0), goal=None, is_boss_room=True)
    result = run_boss_harness(game_map, start=(0, 0), boss_pos=(7, 7), profile=profile)
    signals = {record.boss_signal for record in result.records}
    assert {"drop", "reacquire_fail", "jitter"} & signals


def test_player_view_contains_batch_scan_wiring():
    src = PLAYER_VIEW.read_text(encoding="utf-8")
    assert "SimulationBatchTarget" in src
    assert "scenario_factory=_build_scenario" in src
    assert "batch_targets_factory=_build_batch_targets" in src
    assert "def _build_batch_targets()" in src


def test_simulation_window_contains_full_test_ui_hooks():
    src = SIM_MODULE.read_text(encoding="utf-8")
    assert "class WaypointSimulationWindow" in src
    assert "CTkOptionMenu" in src
    assert 'text="전체테스트"' in src
    assert 'text="로그"' in src
    assert 'text="경유지 중단사유"' in src
    assert 'text="수정해야 할 부분"' in src
    assert "def _format_fix_item" in src
    assert "def _refresh_fix_box" in src
    assert "def _start_full_test" in src
    assert "def _update_result_summary" in src
    assert "Treeview" in src
    assert "summarize_scan_results" in src

