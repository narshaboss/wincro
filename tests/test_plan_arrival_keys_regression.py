import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = ROOT / "data" / "plans" / "plan_20260205_000742.json"


def test_all_boss_cave_9f_waypoints_use_6_2_enter_arrival_keys():
    data = json.loads(PLAN_PATH.read_text(encoding="utf-8-sig"))
    suffix = "\u0039\uad74"
    issues = []

    for mode_name, mode in (data.get("game_modes") or {}).items():
        for idx, wp in enumerate(mode.get("waypoints") or []):
            if not (
                isinstance(wp, list)
                and len(wp) > 3
                and isinstance(wp[2], str)
                and wp[2].endswith(suffix)
            ):
                continue
            cfg = wp[3] if isinstance(wp[3], dict) else {}
            keys = [
                str(k.get("key"))
                for k in (cfg.get("arrival_keys") or [])
                if isinstance(k, dict) and "key" in k
            ]
            if keys != ["6", "2", "enter"]:
                issues.append((mode_name, idx, wp[2], keys))

    assert not issues, issues
