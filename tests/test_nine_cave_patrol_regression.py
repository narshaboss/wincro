import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAPS_DIR = ROOT / "data" / "maps"
PLAYER_VIEW = ROOT / "src" / "ui" / "player_view.py"


def test_nine_cave_boss_maps_have_projected_patrol_points():
    base_path = next(MAPS_DIR.glob("65546d26_09_*boss_map.json"))
    base_data = json.loads(base_path.read_text(encoding="utf-8-sig"))
    expected_len = len(base_data.get("patrol_points") or [])
    assert expected_len >= 2

    for map_path in sorted(MAPS_DIR.glob("*9굴*_boss_map.json")):
        if map_path.name == base_path.name or ".bak" in map_path.name:
            continue
        data = json.loads(map_path.read_text(encoding="utf-8-sig"))
        patrol_points = [tuple(p) for p in (data.get("patrol_points") or [])]
        passable = {tuple(p) for p in (data.get("passable") or [])}
        assert len(patrol_points) == expected_len, map_path.name
        assert all(point in passable for point in patrol_points), map_path.name


def test_eight_cave_map_was_not_rewritten_as_boss_patrol():
    sample_path = next(MAPS_DIR.glob("163037ce_09_*8굴_map.json"))
    sample_data = json.loads(sample_path.read_text(encoding="utf-8-sig"))
    assert sample_data.get("patrol_points") in (None, []), sample_path.name


def test_ai_patrol_builder_uses_nine_cave_template_first():
    text = PLAYER_VIEW.read_text(encoding="utf-8-sig")

    assert "def _is_nine_cave_boss_segment(self, segment_idx: int) -> bool:" in text
    assert "def _load_nine_cave_patrol_template(self):" in text
    assert "def _project_nine_cave_patrol_points(self, game_map_ref):" in text
    assert "if self._is_nine_cave_boss_segment(segment_idx):" in text
    assert "template_points = self._project_nine_cave_patrol_points(game_map_ref)" in text
