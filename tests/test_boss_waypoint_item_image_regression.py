import json
from pathlib import Path


PLAN_PATH = Path(r"C:\Projects\wincro\data\plans\plan_20260205_000742.json")


def test_all_boss_waypoints_use_domestic_boss_item_image():
    with open(PLAN_PATH, "r", encoding="utf-8-sig") as f:
        data = json.load(f)

    domestic_item_image = (
        data["game_modes"]["rule_65546d26"]["waypoints"][9][3]["item_image"]
    )

    boss_waypoints = []
    for game_mode in data.get("game_modes", {}).values():
        for waypoint in game_mode.get("waypoints", []):
            if (
                isinstance(waypoint, list)
                and len(waypoint) >= 4
                and waypoint[0] == 0
                and waypoint[1] == 0
                and isinstance(waypoint[3], dict)
            ):
                boss_waypoints.append(waypoint)

    assert boss_waypoints
    for boss_wp in boss_waypoints:
        assert boss_wp[3].get("item_image") == domestic_item_image
