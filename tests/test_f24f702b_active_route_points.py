import json
from pathlib import Path


def test_f24f702b_active_route_points_are_passable_and_unblocked():
    plan = json.loads(
        Path("data/plans/plan_20260205_000742.json").read_text(encoding="utf-8-sig")
    )
    game_mode = plan["game_modes"]["rule_f24f702b"]
    prefix = "f24f702b"

    issues = []
    for idx, waypoint in enumerate(game_mode.get("waypoints", [])):
        if not isinstance(waypoint, list) or len(waypoint) < 4 or not isinstance(waypoint[3], dict):
            continue

        map_path = next(Path("data/maps").glob(f"{prefix}_{idx:02d}_*.json"), None)
        if map_path is None:
            continue

        map_data = json.loads(map_path.read_text(encoding="utf-8-sig"))
        passable = {tuple(x) for x in map_data.get("passable", [])}
        blocked = {tuple(x) for x in map_data.get("blocked", [])}

        for kind in ("route_starts", "route_ends"):
            for route in waypoint[3].get(kind, []) or []:
                if not route.get("enabled", True):
                    continue
                point = (route.get("x"), route.get("y"))
                if None in point:
                    continue
                if point in blocked or point not in passable:
                    issues.append((idx, waypoint[2], kind, point, map_path.name))

    assert issues == []
