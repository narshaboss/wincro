import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAN_DIR = ROOT / "data" / "plans"
IMAGE_DIRS = (ROOT / "data" / "images", ROOT / "data" / "templates", ROOT / "data")
ADDED_OR_UPDATED_PLANS = (
    "plan_20260118_174859.json",
    "plan_20260519_152141.json",
    "plan_20260502_164945.json",
    "plan_20260205_000742.json",
)


def _image_exists(image_name: str) -> bool:
    path = Path(str(image_name))
    if path.is_absolute():
        return path.exists()
    return any((base / str(image_name)).exists() for base in IMAGE_DIRS)


def _git_ls_files() -> set[str]:
    output = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True)
    return {line.strip().replace("\\", "/") for line in output.splitlines() if line.strip()}


def _candidate_repo_paths(image_name: str) -> list[str]:
    path = Path(str(image_name))
    if path.is_absolute():
        try:
            return [path.resolve().relative_to(ROOT).as_posix()]
        except ValueError:
            return [path.as_posix()]

    return [(base / str(image_name)).relative_to(ROOT).as_posix() for base in IMAGE_DIRS]


def _sidecar_mask_paths(repo_path: str) -> list[str]:
    path = ROOT / repo_path
    mask_path = path.parent / f"{path.stem}_mask{path.suffix}"
    if not mask_path.exists():
        return []
    return [mask_path.relative_to(ROOT).as_posix()]


def _iter_image_refs(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key.endswith("image") or key == "target_images":
                values = value if isinstance(value, list) else [value]
                for image_name in values:
                    if image_name:
                        yield str(image_name)
            yield from _iter_image_refs(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_image_refs(item)


def test_added_or_updated_plans_are_loadable_and_reference_existing_images():
    for plan_name in ADDED_OR_UPDATED_PLANS:
        plan_path = PLAN_DIR / plan_name
        data = json.loads(plan_path.read_text(encoding="utf-8-sig"))

        assert data.get("plan_name") or data.get("name")
        assert data.get("initial_rules")
        assert int(data.get("total_repeat_count") or 0) > 0

        missing = [image_name for image_name in _iter_image_refs(data) if not _image_exists(image_name)]
        assert missing == []


def test_tracked_plans_reference_only_tracked_release_assets():
    tracked = _git_ls_files()
    plan_files = sorted(path for path in tracked if path.startswith("data/plans/") and path.endswith(".json"))
    missing = []
    absolute_refs = []
    untracked_masks = []

    for plan_file in plan_files:
        data = json.loads((ROOT / plan_file).read_text(encoding="utf-8-sig"))
        for image_name in sorted(set(_iter_image_refs(data))):
            if Path(image_name).is_absolute():
                absolute_refs.append((plan_file, image_name))

            candidates = _candidate_repo_paths(image_name)
            tracked_candidates = [candidate for candidate in candidates if candidate in tracked]
            if not tracked_candidates:
                missing.append((plan_file, image_name, candidates))
                continue

            for tracked_candidate in tracked_candidates:
                for mask_path in _sidecar_mask_paths(tracked_candidate):
                    if mask_path not in tracked:
                        untracked_masks.append((plan_file, image_name, mask_path))

    assert absolute_refs == []
    assert missing == []
    assert untracked_masks == []


def test_auto_hunt_top_level_action_order_matches_the_restored_sequence():
    plan_path = PLAN_DIR / "plan_20260118_174859.json"
    data = json.loads(plan_path.read_text(encoding="utf-8-sig"))
    rules = data["initial_rules"]

    expected_rule_ids = [
        "rule_0009",
        "rule_0000",
        "rule_0004",
        "rule_645bae48",
        "rule_7d656a1a",
        "rule_17a4fe66",
        "rule_1c06f502",
        "rule_0007",
        "rule_539f6503",
        "rule_34d9dd72",
        "rule_ecd5d910",
        "rule_cd9afb77",
        "rule_3d1eb89a",
        "rule_becb6d10",
    ]

    assert [rule["rule_id"] for rule in rules] == expected_rule_ids
    assert all(rule.get("parent_id") is None for rule in rules)

    character_select = rules[4]
    assert character_select["description"].strip() == "연+호동  캐릭터 선택후 접속"
    assert len(character_select.get("children") or []) == 6

    auto_hunt = rules[5]
    benefits = next(
        child for child in auto_hunt["children"] if child["rule_id"] == "rule_41a59d7d"
    )
    assert all(
        child["rule_id"] != character_select["rule_id"]
        for child in benefits.get("children") or []
    )
