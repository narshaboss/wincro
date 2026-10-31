"""Draft contracts and offline media. All writes use temporary directories."""

import copy
import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import Mock

import cv2
import numpy as np
import pytest

from src.agent_bridge.contracts import BridgeError, decode, digest
from src.agent_bridge.media import MediaTools
from src.agent_bridge.service import DraftService
from src.agent_bridge.storage import publish_bytes, read_json, write_json


@pytest.fixture
def service(tmp_path):
    plans = tmp_path / "plans"
    templates = tmp_path / "templates"
    plans.mkdir()
    templates.mkdir()
    return DraftService(plans, templates, tmp_path / "drafts")


def add(service, draft, kind="click", **fields):
    result = service.stage_edits(draft["draft_id"], draft["revision"], f"add-{draft['revision']}",
        [{"op": "add_rule", "action_type": kind, "fields": fields}])
    return service.read_draft(draft["draft_id"])


def review(service, draft, recording_id=None, questions=None):
    """Simulate an agent's explicit evidence report, NOT automatic log extraction."""
    from src.agent_bridge.contracts import walk
    if recording_id is None:
        path = service.workspace / "evidence.png"
        path.write_bytes(cv2.imencode(".png", np.full((20, 20, 3), 100, np.uint8))[1].tobytes())
        recording_id = MediaTools(service).register(path)["recording_id"]
    trace = MediaTools(service).get_frames(recording_id, [0])["frames"][0]["frame_trace_id"]
    service.record_analysis(draft["draft_id"], draft["revision"], "Test user goal", [
        {"rule_id": rule["rule_id"], "recording_id": recording_id, "seconds": 0, "frame_trace_id": trace,
         "observation": "A confirmation control is visible", "purpose": "Confirm the user's selection",
         "expected_result": "The next screen is shown"} for rule, *_ in walk(draft["plan"])], questions)
    for direction in ("video_to_plan", "plan_to_video"):
        current = service.read_draft(draft["draft_id"])
        trace = MediaTools(service).get_frames(recording_id, [0])["frames"][0]["frame_trace_id"]
        service.record_crosscheck(draft["draft_id"], current["revision"], direction, [
            {"rule_id": rule["rule_id"], "frame_trace_id": trace, "result": "match", "note": "This action agrees with the observed control and goal"}
            for rule, *_ in walk(current["plan"])])
    return service.read_draft(draft["draft_id"])


def test_complete_durable_pipeline_with_original_crop_and_new_plan_only(service, tmp_path):
    image = np.random.default_rng(4).integers(0, 255, (80, 100, 3), dtype=np.uint8)
    original = tmp_path / "recording.png"
    original.write_bytes(cv2.imencode(".png", image)[1].tobytes())
    log = tmp_path / "input.json"
    write_json(log, {"events": [{"event_type": "key_press", "timestamp": 0, "key": "enter"}]})
    media = MediaTools(service)
    selected = media.register(original, log)
    draft = service.open_draft("테스트 제작")
    crop = media.create_asset_crop(selected["recording_id"], 0, [12, 14, 45, 55], draft["draft_id"], draft["revision"])
    draft = service.read_draft(draft["draft_id"])
    draft = add(service, draft, "double_click", target_image=crop["asset_name"], wait_after=1.2,
                search_region=[0, 0, 100, 80], wait_random=True, repeat_count=2)
    assert service.validate_draft(draft["draft_id"])["valid"]
    assert service.save_draft(draft["draft_id"], draft["revision"])["saved_and_reloaded"]
    with pytest.raises(BridgeError, match="Approve"):
        service.publish_new_plan(draft["draft_id"], draft["revision"])
    draft = review(service, draft, selected["recording_id"])
    service.approve_publication(draft["draft_id"], draft["revision"])
    result = service.publish_new_plan(draft["draft_id"], draft["revision"])
    assert result == service.publish_new_plan(draft["draft_id"], draft["revision"])
    assert len(service.list_drafts()) == 1  # intent files must not be parsed as drafts
    saved = read_json(service.plans_dir / (result["plan_id"] + ".json"))
    assert saved["initial_rules"][0]["wait_after"] == 1.2
    assert saved["initial_rules"][0]["target_image"] == crop["asset_name"]
    copied = cv2.imdecode(np.frombuffer((service.templates_dir / crop["asset_name"]).read_bytes(), np.uint8), cv2.IMREAD_COLOR)
    assert np.array_equal(copied, image[14:55, 12:45])
    fresh = DraftService(service.plans_dir, service.templates_dir, service.workspace)
    assert fresh.read_draft(draft["draft_id"])["publication"] == result
    assert MediaTools(service).get_input_events(selected["recording_id"])["events"][0]["key"] == "enter"
    assert result["playlist_changed"] is False


def test_batch_is_atomic_and_duplicate_request_is_not_reapplied(service):
    draft = service.open_draft("batch")
    ops = [{"op": "add_rule", "action_type": "hotkey", "fields": {"action_keys": ["enter"]}}]
    receipt = service.stage_edits(draft["draft_id"], 1, "same", ops)
    assert service.stage_edits(draft["draft_id"], 1, "same", ops) == receipt
    with pytest.raises(BridgeError) as err:
        service.stage_edits(draft["draft_id"], 1, "same", [{"op": "rename_plan", "name": "changed"}])
    assert err.value.code == "REQUEST_ID_REUSED"
    before = service.read_draft(draft["draft_id"])
    with pytest.raises(BridgeError):
        service.stage_edits(draft["draft_id"], 2, "bad", [ops[0], {"op": "delete_rule", "rule_id": "missing"}])
    assert before == service.read_draft(draft["draft_id"])


def test_stale_edit_approval_and_reference_source_change_are_rejected(service):
    source = {"plan_id": "plan_source", "name": "source", "initial_rules": [], "monitoring_rules": []}
    path = service.plans_dir / "plan_source.json"
    write_json(path, source)
    original = path.read_bytes()
    draft = service.open_draft("copy", "plan_source")
    draft["plan"]["name"] = "mutated returned copy"
    assert service.read_draft(draft["draft_id"])["plan"]["name"] == "copy"
    with pytest.raises(BridgeError) as err:
        service.stage_edits(draft["draft_id"], 0, "stale", [{"op": "rename_plan", "name": "bad"}])
    assert err.value.code == "REVISION_CONFLICT"
    assert path.read_bytes() == original
    draft = review(service, draft)
    service.approve_publication(draft["draft_id"], draft["revision"])
    updated = service.stage_edits(draft["draft_id"], draft["revision"], "new", [{"op": "rename_plan", "name": "next"}])
    with pytest.raises(BridgeError) as err:
        service.publish_new_plan(draft["draft_id"], updated["revision"])
    assert err.value.code == "PERMISSION_REQUIRED"
    draft = review(service, service.read_draft(draft["draft_id"]))
    service.approve_publication(draft["draft_id"], draft["revision"])
    source["name"] = "user changed source"
    write_json(path, source)
    with pytest.raises(BridgeError) as err:
        service.publish_new_plan(draft["draft_id"], draft["revision"])
    assert err.value.code == "REVISION_CONFLICT"


@pytest.mark.parametrize("field,value", [("wait_after", -1), ("confidence", 3), ("repeat_count", 0),
    ("repeat_count", True), ("enabled", "false"), ("search_region", [10, 0, 5, 8]),
    ("action_keys", "enter"), ("action_x", "12"), ("parent_id", "other"), ("wait_after", float("nan"))])
def test_invalid_edit_fields_do_not_modify_draft(service, field, value):
    draft = service.open_draft("fields")
    with pytest.raises((ValueError, BridgeError)):
        add(service, draft, **{field: value})
    assert service.read_draft(draft["draft_id"])["revision"] == 1


def test_tree_move_child_and_invalid_cycles(service):
    draft = add(service, service.open_draft("tree"), description="parent")
    parent = draft["plan"]["initial_rules"][0]["rule_id"]
    service.stage_edits(draft["draft_id"], draft["revision"], "child", [
        {"op": "add_rule", "parent_id": parent, "fields": {"description": "child"}}])
    draft = service.read_draft(draft["draft_id"])
    child = draft["plan"]["initial_rules"][0]["children"][0]["rule_id"]
    with pytest.raises(BridgeError):
        service.stage_edits(draft["draft_id"], draft["revision"], "cycle", [{"op": "move_rule", "rule_id": parent, "parent_id": child}])
    with pytest.raises(BridgeError):
        service.stage_edits(draft["draft_id"], draft["revision"], "delete", [{"op": "delete_rule", "rule_id": parent}])
    service.stage_edits(draft["draft_id"], draft["revision"], "move", [{"op": "move_rule", "rule_id": child}])
    after = service.read_draft(draft["draft_id"])
    assert len(after["plan"]["initial_rules"]) == 2
    assert after["plan"]["initial_rules"][1]["parent_id"] is None


def test_reference_plan_reading_is_explicitly_granted(service):
    write_json(service.plans_dir / "plan_source.json", {"name": "source", "initial_rules": []})
    assert service.dispatch("list_plans", {}) == []
    with pytest.raises(BridgeError):
        service.dispatch("read_plan", {"plan_id": "plan_source"})
    service.granted_sources.add("plan_source")
    assert service.dispatch("read_plan", {"plan_id": "plan_source"})["source"] == "disk"
    assert len(service.dispatch("list_plans", {})) == 1


@pytest.mark.parametrize("method", ["approve_publication", "run_test", "close", "_apply", "delete_plan", "exec", "read_file"])
def test_dangerous_tools_are_not_dispatched(service, method):
    with pytest.raises(BridgeError) as err:
        service.dispatch(method, {})
    assert err.value.code == "TOOL_DENIED"


@pytest.mark.parametrize("identifier", ["../outside", "draft_../outside", "draft_abc/x", "draft_abc:evil"])
def test_draft_path_traversal_is_rejected(service, identifier):
    with pytest.raises(BridgeError):
        service.read_draft(identifier)


def test_no_clobber_publication_and_save_failure(service, monkeypatch):
    publish_bytes(service.plans_dir, "occupied.json", b"original")
    with pytest.raises(FileExistsError):
        publish_bytes(service.plans_dir, "occupied.json", b"changed")
    assert (service.plans_dir / "occupied.json").read_bytes() == b"original"
    draft = service.open_draft("save failure")
    import src.agent_bridge.service as module
    monkeypatch.setattr(module, "write_json", Mock(side_effect=PermissionError("locked")))
    with pytest.raises(PermissionError):
        add(service, draft)
    assert service.read_draft(draft["draft_id"])["revision"] == 1


def test_media_private_intervals_and_replacement(service, tmp_path):
    image = tmp_path / "one.png"
    image.write_bytes(cv2.imencode(".png", np.full((20, 30, 3), 128, np.uint8))[1].tobytes())
    media = MediaTools(service)
    selected = media.register(image, excluded=[[0, 1]])
    with pytest.raises(BridgeError) as err:
        media.get_frames(selected["recording_id"], [0])
    assert err.value.code == "PRIVATE_INTERVAL"
    image.write_bytes(b"replaced")
    with pytest.raises(BridgeError) as err:
        media.read_recording(selected["recording_id"])
    assert err.value.code == "MEDIA_NOT_READY"
    assert len(service.dispatch("list_recordings", {})) == 1


def test_actual_video_frame_crop_dispatch_without_desktop_capture(service):
    clip = Path(__file__).resolve().parents[1] / "data/templates/template_video_20260703_154056_306.mp4"
    selected = MediaTools(service).register(clip)
    result = service.dispatch("get_frames", {"recording_id": selected["recording_id"], "timestamps": [0, 1.5]})
    assert len(result["frames"]) == 2
    assert result["frames"][0]["original_width"] == 30
    assert result["frames"][0]["original_height"] == 32
    assert result["frames"][0]["preview_scale"] == 1


def test_decode_rejects_ambiguous_or_nonfinite_json():
    for payload in (b'{"a":1,"a":2}', b'{"x":NaN}'):
        with pytest.raises(ValueError):
            decode(payload)


def test_real_saved_plan_trees_are_read_only_and_never_normalized(service):
    root = Path(__file__).resolve().parents[1] / "data/plans"
    original = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.glob("*.json")}
    reader = DraftService(root, root.parent / "templates", service.workspace)
    for row in reader.list_plans():
        saved = reader.read_plan(row["plan_id"])
        assert saved["plan"] == json.loads((root / (row["plan_id"] + ".json")).read_text(encoding="utf-8-sig"))
    assert original == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.glob("*.json")}


def test_mcp_registry_excludes_approval_and_input(monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import wincro_mcp_server as server
    import asyncio
    tools = asyncio.run(server.mcp.list_tools())
    names = {tool.name for tool in tools}
    assert {"stage_edits", "get_frames", "record_analysis", "publish_new_plan"} <= names
    assert not ({"approve_publication", "run_test", "exec", "click"} & names)
    monkeypatch.setattr(server, "invoke", lambda method, arguments: {"ok": True, "result": arguments})
    assert server.stage_edits("draft_abc", 3, "r", [{"op": "rename_plan", "name": "n"}])["result"]["expected_revision"] == 3


def test_ai_analysis_is_required_and_edits_invalidate_it(service):
    draft = add(service, service.open_draft("AI interpretation required"))
    with pytest.raises(BridgeError) as err:
        service.approve_publication(draft["draft_id"], draft["revision"])
    assert err.value.code == "ANALYSIS_REQUIRED"
    draft = review(service, draft, questions=["Should this repeat until the image disappears?"])
    with pytest.raises(BridgeError) as err:
        service.approve_publication(draft["draft_id"], draft["revision"])
    assert err.value.code == "ANALYSIS_REQUIRED"
    draft = review(service, draft)
    assert service.validate_draft(draft["draft_id"])["ai_analysis_current"]
    draft = add(service, draft, "hotkey", action_keys=["enter"])
    assert service.read_draft(draft["draft_id"])["analysis"] is None
    assert not service.validate_draft(draft["draft_id"])["ai_analysis_current"]


def test_unknown_schema_is_preserved_but_never_silently_published(service):
    source = {"name": "future", "initial_rules": [], "future_field": {"keep": [1, 2, 3]}}
    write_json(service.plans_dir / "plan_future.json", source)
    draft = service.open_draft("copy future", "plan_future")
    assert draft["plan"]["future_field"] == source["future_field"]
    with pytest.raises(BridgeError) as err:
        service.validate_draft(draft["draft_id"])
    assert err.value.code == "UNSUPPORTED_SCHEMA"
    assert read_json(service.plans_dir / "plan_future.json") == source


def test_reference_asset_replacement_invalidates_validation(service):
    path = service.templates_dir / "asset.png"
    path.write_bytes(cv2.imencode(".png", np.zeros((10, 10, 3), np.uint8))[1].tobytes())
    draft = add(service, service.open_draft("asset snapshot"), target_image="asset.png")
    path.write_bytes(b"changed source")
    with pytest.raises(BridgeError) as err:
        service.validate_draft(draft["draft_id"])
    assert err.value.code == "MEDIA_NOT_READY"


def test_oversized_state_is_rejected_before_overwriting_durable_draft(service, monkeypatch):
    draft = service.open_draft("size limit")
    import src.agent_bridge.service as module
    monkeypatch.setattr(module, "MAX_MESSAGE", 100)
    with pytest.raises(BridgeError):
        add(service, draft)
    assert service.read_draft(draft["draft_id"])["revision"] == 1


def test_crosscheck_must_be_clean_in_both_directions_and_is_reset_by_edit(service):
    draft = review(service, add(service, service.open_draft("cross review")))
    assert service.validate_draft(draft["draft_id"])["crosscheck_ready"]
    trace = next(iter(service.frame_traces))
    key = draft["plan"]["initial_rules"][0]["rule_id"]
    service.record_crosscheck(draft["draft_id"], draft["revision"], "video_to_plan", [
        {"rule_id": key, "frame_trace_id": trace, "result": "uncertain", "note": "The repeat stopping condition is not visible"}], ["Ask the user about termination"])
    latest = service.read_draft(draft["draft_id"])
    with pytest.raises(BridgeError) as err:
        service.approve_publication(latest["draft_id"], latest["revision"])
    assert err.value.code == "CROSSCHECK_REQUIRED"
    updated = add(service, latest)
    assert updated["analysis"] is None
    assert updated["crosschecks"] == []
