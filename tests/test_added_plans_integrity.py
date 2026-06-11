import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAN_DIR = ROOT / "data" / "plans"
IMAGE_DIRS = (ROOT / "data" / "images", ROOT / "data" / "templates", ROOT / "data")
ADDED_OR_UPDATED_PLANS = (
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
