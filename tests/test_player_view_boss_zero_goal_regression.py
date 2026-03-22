from pathlib import Path
import json


PLAYER_VIEW = Path(r"C:\Projects\wincro\src\ui\player_view.py")
PLAN_PATH = Path(r"C:\Projects\wincro\data\plans\plan_20260205_000742.json")


def test_boss_zero_goal_guard_uses_fallback_for_sentinel_room():
    src = PLAYER_VIEW.read_text(encoding="utf-8")

    assert "def _is_sentinel_boss_goal(tidx, tx, ty):" in src
    assert "_boss_zero_coord_ocr_count = 0" in src
    assert "⚠️ 보스굴 좌표 0,0 보정:" in src
    assert "current_x, current_y = _fallback_coord" in src
    assert "not _is_sentinel_boss_goal(target_idx, target_x, target_y)" in src


def test_f24f702b_boss_waypoint_uses_sentinel_zero_goal():
    plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    waypoints = plan["game_modes"]["rule_f24f702b"]["waypoints"]
    boss_wp = waypoints[10]
    meta = boss_wp[3]

    assert tuple(boss_wp[:2]) == (0, 0)
    assert meta["map_locked"] is True
    assert meta["route_starts"] == [{"x": 12, "y": 4}]
    assert not meta.get("route_ends")
