import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAN_DIR = ROOT / "data" / "plans"
IMAGE_DIRS = (ROOT / "data" / "images", ROOT / "data" / "templates", ROOT / "data")
ADDED_OR_UPDATED_PLANS = (
    "plan_20260519_152141.json",
    "plan_20260502_164945.json",
)


def _image_exists(image_name: str) -> bool:
    path = Path(str(image_name))
    if path.is_absolute():
        return path.exists()
    return any((base / str(image_name)).exists() for base in IMAGE_DIRS)


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
