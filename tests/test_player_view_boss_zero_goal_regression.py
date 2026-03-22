from pathlib import Path


def test_boss_zero_goal_guard_present():
    src = Path(r"C:\Projects\wincro\src\ui\player_view.py").read_text(encoding="utf-8")

    assert "def _is_sentinel_boss_goal(tidx, tx, ty):" in src
    assert "not _is_sentinel_boss_goal(target_idx, target_x, target_y)" in src
    assert "_sentinel_boss_zero_goal = _is_sentinel_boss_goal(target_idx, target_x, target_y)" in src
    assert 'self._append_log(f"⚠️ 보스굴 좌표 0,0 오독 무시: (0,0) ({c}/{m})")' in src


def test_f24f702b_boss_waypoint_uses_sentinel_zero_goal():
    import json

    plan = json.loads(Path(r"C:\Projects\wincro\data\plans\plan_20260205_000742.json").read_text(encoding="utf-8"))
    waypoints = plan["game_modes"]["rule_f24f702b"]["waypoints"]
    boss_wp = waypoints[10]
    meta = boss_wp[3]

    assert tuple(boss_wp[:2]) == (0, 0)
    assert meta["map_locked"] is True
    assert meta["route_starts"] == [{"x": 12, "y": 4}]
    assert not meta.get("route_ends")
