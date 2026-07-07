from pathlib import Path

from src.player.game_map import GameMap, sanitize_transient_local_map_file


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
