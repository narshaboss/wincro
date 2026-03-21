import json
from pathlib import Path

from src.utils.json_utils import dump_json_file, load_json_file


def test_load_json_file_accepts_utf8_bom(tmp_path: Path):
    plan_file = tmp_path / "plan.json"
    payload = {"name": "원각공장", "initial_rules": [], "game_modes": {}}
    plan_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8-sig")

    data = load_json_file(plan_file)

    assert data["name"] == "원각공장"


def test_dump_json_file_writes_plain_utf8(tmp_path: Path):
    plan_file = tmp_path / "plan.json"
    dump_json_file(plan_file, {"name": "원각공장"}, ensure_ascii=False, indent=2)

    raw = plan_file.read_bytes()

    assert not raw.startswith(b"\xef\xbb\xbf")

