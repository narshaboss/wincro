import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = ROOT / "data" / "plans" / "plan_20260205_000742.json"


def _collect_game_rule_ids(rules):
    ids = set()
    for rule in rules or []:
        if rule.get("action_type") == "game_mode":
            ids.add(rule.get("rule_id"))
        ids.update(_collect_game_rule_ids(rule.get("children") or []))
    return ids


def test_wongak_plan_has_no_orphan_game_modes():
    data = json.loads(PLAN_PATH.read_text(encoding="utf-8-sig"))

    active_game_rules = _collect_game_rule_ids(data.get("initial_rules") or [])
    active_game_rules.update(_collect_game_rule_ids(data.get("monitoring_rules") or []))

    game_modes = set((data.get("game_modes") or {}).keys())
    assert game_modes == active_game_rules
