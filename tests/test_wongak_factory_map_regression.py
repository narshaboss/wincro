import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = ROOT / "data" / "plans" / "plan_20260205_000742.json"
MAP_PATH = ROOT / "data" / "maps" / "65546d26_06_흑해골굴6굴_map.json"


def test_wongak_factory_cave_6_uses_locked_legacy_map():
    plan = json.loads(PLAN_PATH.read_text(encoding="utf-8-sig"))
    waypoint = plan["game_modes"]["rule_65546d26"]["waypoints"][6]

    assert waypoint[:3] == [5, 4, "흑해골굴6굴"]
    assert waypoint[3]["map_locked"] is True

    game_map = json.loads(MAP_PATH.read_text(encoding="utf-8-sig"))
    passable = {tuple(point) for point in game_map["passable"]}
    blocked = {tuple(point) for point in game_map["blocked"]}

    assert len(passable) == 443
    assert len(blocked) == 221
    assert (20, 22) in passable
    assert (11, 17) in blocked
    assert (20, 22) not in blocked
    assert (11, 17) not in passable
    assert "preserve_learned_blocked" not in game_map
