"""Check on-disk plan roundtrips without changing the shipped plan documents."""

import json
from pathlib import Path

import pytest

from src.analyzer.automation_models import AutomationPlan
from src.utils.json_utils import dump_json_file, load_json_file


ROOT = Path(__file__).resolve().parents[1]
PLAN_FILES = sorted((ROOT / "data/plans").glob("*.json"))


@pytest.mark.parametrize("path", PLAN_FILES, ids=lambda path: path.stem)
def test_packaged_plan_roundtrips_through_disk_without_losing_actions(path, tmp_path):
    original = path.read_bytes()
    plan = AutomationPlan.from_dict(json.loads(original.decode("utf-8-sig")), templates_dir=ROOT / "data/templates")
    expected = plan.to_dict()
    saved_path = tmp_path / path.name
    dump_json_file(saved_path, expected)
    saved = load_json_file(saved_path)
    assert saved == expected
    restored = AutomationPlan.from_dict(saved, templates_dir=ROOT / "data/templates")
    assert restored.to_dict() == expected
    assert path.read_bytes() == original
