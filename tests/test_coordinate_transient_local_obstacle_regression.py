from pathlib import Path

from src.player.game_map import DIRECTIONS_4, GameMap, sanitize_transient_local_map_file
from src.player.simple_pathfinder import SimplePathfinder


RULE_EXECUTOR = Path("src/player/rule_executor.py")


def _method_body(text: str, start_marker: str, end_marker: str) -> str:
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    return text[start:end]


def test_game_map_can_clear_blocked_without_marking_passable():
    game_map = GameMap("clear-blocked")
    game_map.mark_blocked(15, 7)

    assert game_map.is_blocked(15, 7)
    assert game_map.clear_blocked(15, 7) is True
    assert not game_map.is_blocked(15, 7)
    assert not game_map.is_passable(15, 7)


def test_game_map_dynamic_obstacle_cleanup_removes_edges_and_learned_walls():
    game_map = GameMap("dynamic-cleanup")
    game_map.mark_passable(14, 7)
    game_map.mark_blocked(15, 7)
    game_map.mark_soft_blocked(16, 7)
    game_map.mark_blocked_edge(14, 7, "right")

    removed = game_map.clear_dynamic_obstacles(clear_blocked=True)

    assert removed == {"soft": 1, "blocked": 1, "edges": 1}
    assert game_map.is_passable(14, 7)
    assert not game_map.is_blocked(15, 7)
    assert not game_map.is_soft_blocked(16, 7)
    assert not game_map.is_edge_blocked(14, 7, "right")


def test_game_map_clear_resets_mapping_test_preserve_flag():
    game_map = GameMap("clear-preserve-flag")
    game_map.mark_passable(14, 7)
    game_map.mark_blocked(15, 7)
    game_map.mark_blocked_edge(14, 7, "right")
    game_map.preserve_learned_blocked = True

    game_map.clear()

    assert game_map.preserve_learned_blocked is False
    assert not game_map.is_passable(14, 7)
    assert not game_map.is_blocked(15, 7)
    assert not game_map.is_edge_blocked(14, 7, "right")


def test_local_map_file_sanitizer_removes_persistent_dynamic_obstacles(tmp_path):
    map_path = tmp_path / "ruleabcd_00_다이쇼_local_map.json"
    game_map = GameMap("local-file-cleanup")
    game_map.mark_passable(14, 7)
    game_map.mark_passable(15, 7)
    game_map.mark_blocked(16, 7)
    game_map.mark_soft_blocked(17, 7)
    game_map.mark_blocked_edge(14, 7, "right")
    game_map.save(str(map_path))

    assert sanitize_transient_local_map_file(str(map_path)) is True

    reloaded = GameMap("reload")
    assert reloaded.load(str(map_path))
    assert reloaded.is_passable(14, 7)
    assert reloaded.is_passable(15, 7)
    assert not reloaded.is_blocked(16, 7)
    assert not reloaded.is_soft_blocked(17, 7)
    assert not reloaded.is_edge_blocked(14, 7, "right")


def test_local_map_file_sanitizer_preserves_mapping_test_learned_walls(tmp_path):
    map_path = tmp_path / "ruleabcd_00_mapping_local_map.json"
    game_map = GameMap("local-file-preserve")
    game_map.mark_passable(14, 7)
    game_map.mark_passable(15, 7)
    game_map.mark_blocked(16, 7)
    game_map.mark_soft_blocked(17, 7)
    game_map.mark_blocked_edge(14, 7, "right")
    game_map.preserve_learned_blocked = True
    game_map.save(str(map_path))

    assert sanitize_transient_local_map_file(str(map_path)) is True

    reloaded = GameMap("reload")
    assert reloaded.load(str(map_path))
    assert reloaded.is_passable(14, 7)
    assert reloaded.is_passable(15, 7)
    assert reloaded.is_blocked(16, 7)
    assert not reloaded.is_soft_blocked(17, 7)
    assert reloaded.is_edge_blocked(14, 7, "right")
    assert reloaded.preserve_learned_blocked is True


def test_preserved_mapping_map_sanitizer_keeps_clean_learned_walls_without_rewrite(tmp_path):
    map_path = tmp_path / "ruleabcd_00_clean_preserved_local_map.json"
    game_map = GameMap("clean-preserved")
    game_map.mark_passable(14, 7)
    game_map.mark_passable(15, 7)
    game_map.mark_blocked(16, 7)
    game_map.mark_blocked_edge(14, 7, "right")
    game_map.preserve_learned_blocked = True
    game_map.save(str(map_path))

    assert sanitize_transient_local_map_file(str(map_path)) is False

    reloaded = GameMap("reload-clean-preserved")
    assert reloaded.load(str(map_path))
    assert reloaded.is_blocked(16, 7)
    assert reloaded.is_edge_blocked(14, 7, "right")
    assert reloaded.preserve_learned_blocked is True


def test_preserved_local_blocked_edge_is_used_by_second_run_pathfinding(tmp_path):
    map_path = tmp_path / "ruleabcd_00_mapping_local_map.json"
    game_map = GameMap("local-edge-route")
    for x in range(0, 5):
        for y in (-1, 0, 1):
            game_map.mark_passable(x, y)
    game_map.mark_blocked_edge(1, 0, "right")
    game_map.preserve_learned_blocked = True
    game_map.save(str(map_path))

    assert sanitize_transient_local_map_file(str(map_path)) is False

    reloaded = GameMap("second-run")
    assert reloaded.load(str(map_path))
    assert reloaded.is_edge_blocked(1, 0, "right")

    result = SimplePathfinder(reloaded).find_path(
        (0, 0),
        (4, 0),
        allow_unknown=False,
        respect_blocked_edges=True,
    )

    assert result.found is True
    assert (2, 0) in result.path
    assert result.path[:3] != [(0, 0), (1, 0), (2, 0)]
    assert not any(a == (1, 0) and b == (2, 0) for a, b in zip(result.path, result.path[1:]))


def test_preserved_local_long_wall_is_used_by_second_run_pathfinding(tmp_path):
    map_path = tmp_path / "ruleabcd_00_long_wall_local_map.json"
    game_map = GameMap("local-long-wall")
    for x in range(-2, 11):
        for y in range(-2, 3):
            game_map.mark_passable(x, y)
    for wall_x in range(-2, 9):
        game_map.mark_blocked(wall_x, 0)
    game_map.preserve_learned_blocked = True
    game_map.save(str(map_path))

    assert sanitize_transient_local_map_file(str(map_path)) is False

    reloaded = GameMap("second-run-long-wall")
    assert reloaded.load(str(map_path))
    for wall_x in range(-2, 9):
        assert reloaded.is_blocked(wall_x, 0)

    result = SimplePathfinder(reloaded).find_path(
        (4, 2),
        (4, -2),
        allow_unknown=False,
        respect_blocked_edges=True,
    )

    assert result.found is True
    assert result.path[0] == (4, 2)
    assert result.path[-1] == (4, -2)
    assert (9, 0) in result.path
    assert not any(pos[1] == 0 and pos[0] < 9 for pos in result.path)
    assert len(result.path) > 5


def test_second_run_appends_new_obstacle_to_preserved_mapping_map(tmp_path):
    map_path = tmp_path / "ruleabcd_00_incremental_local_map.json"
    first_run = GameMap("first-run")
    for x in range(0, 7):
        for y in (-1, 0, 1):
            first_run.mark_passable(x, y)
    first_run.mark_blocked_edge(1, 0, "right")
    first_run.preserve_learned_blocked = True
    first_run.save(str(map_path))

    second_run = GameMap("second-run")
    assert second_run.load(str(map_path))
    assert second_run.is_edge_blocked(1, 0, "right")
    second_run.mark_blocked(2, -1)
    second_run.save(str(map_path))

    third_run = GameMap("third-run")
    assert third_run.load(str(map_path))
    assert third_run.preserve_learned_blocked is True
    assert third_run.is_edge_blocked(1, 0, "right")
    assert third_run.is_blocked(2, -1)

    result = SimplePathfinder(third_run).find_path(
        (0, 0),
        (6, 0),
        allow_unknown=False,
        respect_blocked_edges=True,
    )

    assert result.found is True
    assert not any(a == (1, 0) and b == (2, 0) for a, b in zip(result.path, result.path[1:]))
    assert (2, -1) not in result.path
    assert result.path[-1] == (6, 0)


def test_no_map_mapping_run_discovers_path_around_wall_and_persists(tmp_path):
    map_path = tmp_path / "ruleabcd_00_discovery_local_map.json"
    actual_walkable = {
        (x, y)
        for x in range(0, 7)
        for y in range(-1, 3)
        if (x, y) not in {(2, -1), (2, 0), (2, 1)}
    }
    current = (0, 0)
    goal = (6, 0)
    game_map = GameMap("first-discovery")
    game_map.preserve_learned_blocked = True
    game_map.mark_passable(*current)
    visited = [current]

    for _ in range(80):
        if current == goal:
            break
        result = SimplePathfinder(game_map).find_path(
            current,
            goal,
            allow_unknown=True,
            unknown_cost=3,
            respect_blocked_edges=True,
        )
        assert result.found is True
        assert result.directions

        direction = result.directions[0]
        dx, dy = DIRECTIONS_4[direction]
        next_pos = (current[0] + dx, current[1] + dy)
        if next_pos in actual_walkable:
            game_map.mark_passable(*next_pos)
            current = next_pos
            visited.append(current)
        else:
            game_map.mark_blocked(*next_pos)
            game_map.mark_blocked_edge(current[0], current[1], direction)
    else:
        raise AssertionError(f"discovery did not reach {goal}; current={current} visited={visited}")

    assert current == goal
    assert (2, 0) in game_map.blocked
    assert all(pos in game_map.passable for pos in visited)
    game_map.save(str(map_path))

    reloaded = GameMap("second-run-discovery")
    assert reloaded.load(str(map_path))
    assert reloaded.preserve_learned_blocked is True
    known_path = SimplePathfinder(reloaded).find_path(
        (0, 0),
        goal,
        allow_unknown=False,
        respect_blocked_edges=True,
    )

    assert known_path.found is True
    assert known_path.path[-1] == goal
    assert not any(pos in reloaded.blocked for pos in known_path.path)
    assert len(known_path.path) > abs(goal[0] - 0) + abs(goal[1] - 0) + 1


def test_no_map_mapping_run_discovers_path_around_horizontal_wall_and_persists(tmp_path):
    map_path = tmp_path / "ruleabcd_00_horizontal_wall_discovery_local_map.json"
    horizontal_wall = {(x, 0) for x in range(-3, 4)}
    actual_walkable = {
        (x, y)
        for x in range(-5, 6)
        for y in range(-3, 4)
        if (x, y) not in horizontal_wall
    }
    current = (0, -2)
    goal = (0, 2)
    game_map = GameMap("first-horizontal-discovery")
    game_map.preserve_learned_blocked = True
    game_map.mark_passable(*current)
    visited = [current]

    for _ in range(160):
        if current == goal:
            break
        result = SimplePathfinder(game_map).find_path(
            current,
            goal,
            allow_unknown=True,
            unknown_cost=3,
            respect_blocked_edges=True,
        )
        assert result.found is True
        assert result.directions

        direction = result.directions[0]
        dx, dy = DIRECTIONS_4[direction]
        next_pos = (current[0] + dx, current[1] + dy)
        if next_pos in actual_walkable:
            game_map.mark_passable(*next_pos)
            current = next_pos
            visited.append(current)
        else:
            game_map.mark_blocked(*next_pos)
            game_map.mark_blocked_edge(current[0], current[1], direction)
    else:
        raise AssertionError(
            f"horizontal wall discovery did not reach {goal}; current={current} visited={visited}"
        )

    assert current == goal
    assert game_map.blocked & horizontal_wall
    assert all(pos in game_map.passable for pos in visited)
    game_map.save(str(map_path))

    reloaded = GameMap("second-run-horizontal-discovery")
    assert reloaded.load(str(map_path))
    assert reloaded.preserve_learned_blocked is True
    known_path = SimplePathfinder(reloaded).find_path(
        (0, -2),
        goal,
        allow_unknown=False,
        respect_blocked_edges=True,
    )

    assert known_path.found is True
    assert known_path.path[-1] == goal
    assert not any(pos in reloaded.blocked for pos in known_path.path)
    assert any(pos[1] == 0 and abs(pos[0]) >= 4 for pos in known_path.path)
    assert len(known_path.path) > abs(goal[0] - 0) + abs(goal[1] - (-2)) + 1


def test_no_start_local_coordinate_failures_use_soft_blocked_not_hard_wall():
    text = RULE_EXECUTOR.read_text(encoding="utf-8")
    body = _method_body(
        text,
        "    def execute_game_mode_coordinate(self, config) -> bool:",
        "    # pyautogui.PAUSE",
    )

    assert "def _clear_transient_local_dynamic_blocks" in body
    assert "mark_soft_blocked(wall_x, wall_y, allow_promote=False)" in body
    assert "clear_dynamic_obstacles(" in body
    assert "elif mapping_enabled:" in body
    assert "game_map.mark_blocked(wall_x, wall_y)" in body

    local_branch = body[
        body.index("if _uses_transient_local_map(current_target_idx):"):
        body.index("elif mapping_enabled:", body.index("if _uses_transient_local_map(current_target_idx):"))
    ]
    assert "mark_soft_blocked(wall_x, wall_y, allow_promote=False)" in local_branch
    assert "mark_blocked(wall_x, wall_y)" not in local_branch


def test_no_start_local_coordinate_failures_block_failed_edge_temporarily():
    text = RULE_EXECUTOR.read_text(encoding="utf-8")
    body = _method_body(
        text,
        "    def execute_game_mode_coordinate(self, config) -> bool:",
        "    # pyautogui.PAUSE",
    )
    local_branch = body[
        body.index("if _uses_transient_local_map(current_target_idx):"):
        body.index("elif mapping_enabled:", body.index("if _uses_transient_local_map(current_target_idx):"))
    ]

    assert "TRANSIENT_DYNAMIC_EDGE_TTL = 45" in body
    assert "TransientDirectionBlocker(default_ttl=TRANSIENT_DYNAMIC_EDGE_TTL)" in body
    assert "def _expire_transient_dynamic_edges(current_iteration):" in body
    assert "transient_dynamic_edges.cleanup_map_edges(game_map, current_iteration)" in body
    assert "def _remember_transient_dynamic_edge(x, y, direction, current_iteration):" in body
    assert "transient_dynamic_edges.register_map_edge(" in body
    assert "_expire_transient_dynamic_edges(iteration)" in body
    assert "_remember_transient_dynamic_edge(prev_x, prev_y, last_dir, iteration)" in local_branch
    assert "edge={'Y' if _edge_marked else 'keep'}" in local_branch


def test_coordinate_mode_pathfinding_respects_blocked_edges():
    text = RULE_EXECUTOR.read_text(encoding="utf-8")
    body = _method_body(
        text,
        "    def execute_game_mode_coordinate(self, config) -> bool:",
        "    # pyautogui.PAUSE",
    )
    first_path_slice = body[
        body.index("# 1차: 알려진 이동가능 경로만"):
        body.index("# 3차: 스마트 탐색", body.index("# 1차: 알려진 이동가능 경로만"))
    ]

    assert first_path_slice.count("pathfinder.find_path(") == first_path_slice.count("respect_blocked_edges=True")
    assert "if game_map.is_edge_blocked(cx, cy, d):" in body


def test_runtime_blocked_edge_forces_coordinate_path_around_dynamic_obstacle():
    game_map = GameMap("coordinate-runtime-edge")
    for x in range(15, 18):
        for y in range(6, 9):
            game_map.mark_passable(x, y)
    game_map.mark_soft_blocked(16, 7, allow_promote=False)
    game_map.mark_blocked_edge(15, 7, "right")

    result = SimplePathfinder(game_map).find_path(
        (15, 7),
        (17, 7),
        allow_unknown=False,
        allow_soft_blocked=True,
        respect_blocked_edges=True,
    )

    assert result.found is True
    assert result.directions
    assert result.directions[0] != "right"
    assert not any(a == (15, 7) and b == (16, 7) for a, b in zip(result.path, result.path[1:]))


def test_transient_local_dynamic_obstacles_are_not_persisted():
    text = RULE_EXECUTOR.read_text(encoding="utf-8")
    app_text = Path("src/app.py").read_text(encoding="utf-8")
    body = _method_body(
        text,
        "    def execute_game_mode_coordinate(self, config) -> bool:",
        "    # pyautogui.PAUSE",
    )

    assert "clear_learned_blocked=True" in body
    assert "edge={removed['edges']}" in body
    assert 'reason="load"' in body
    assert 'reason="load-switch"' in body
    assert 'reason="reload-runtime"' in body
    assert 'reason="save-switch"' in body
    assert 'reason="save-final"' in body
    assert "_sanitize_transient_local_maps_async" in app_text
    assert "sanitize_transient_local_maps(str(DATA_DIR / \"maps\"))" in app_text
