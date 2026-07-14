from pathlib import Path

from src.player.game_map import GameMap


RULE_EXECUTOR = Path("src/player/rule_executor.py")


def _method_body(text: str, start_marker: str, end_marker: str) -> str:
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    return text[start:end]


def test_game_map_does_not_expose_post_269_clear_blocked_helper():
    assert not hasattr(GameMap, "clear_blocked")


def test_coordinate_failure_policy_matches_pre_270_baseline():
    text = RULE_EXECUTOR.read_text(encoding="utf-8")
    body = _method_body(
        text,
        "    def execute_game_mode_coordinate(self, config) -> bool:",
        "    # pyautogui.PAUSE",
    )

    assert "def _clear_transient_local_dynamic_blocks" not in body
    assert "mark_soft_blocked(wall_x, wall_y, allow_promote=False)" not in body
    assert "if mapping_enabled:" in body
    assert "game_map.mark_blocked(wall_x, wall_y)" in body
    assert "if mapping_enabled or _uses_transient_local_map(current_target_idx):" not in body


def test_coordinate_playback_map_access_is_disk_read_only():
    text = RULE_EXECUTOR.read_text(encoding="utf-8")
    body = _method_body(
        text,
        "    def execute_game_mode_coordinate(self, config) -> bool:",
        "    # pyautogui.PAUSE",
    )

    assert "shutil.copy2" not in body
    assert ".save(" not in body
    assert "플레이 실행 맵 정책: 읽기 전용" in body
    assert "플레이 실행 중 변경된 맵 데이터 폐기" in body
