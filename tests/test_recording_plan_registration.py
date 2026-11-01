"""Owner-approved recording exports; all files and scripted reviews are synthetic."""

import copy
from pathlib import Path
import threading

import pytest

from src.agent_bridge import service as service_module
from src.agent_bridge.contracts import BridgeError, digest
from src.agent_bridge.media import MediaTools
from src.agent_bridge.recording_jobs import JobScope, RecordingJobs
from src.agent_bridge.service import DraftService
from src.agent_bridge.storage import read_json, write_json
from tests.test_ai_draft_bridge import review
from tests.test_recording_jobs import setup as jobs_setup, finish


@pytest.fixture
def ready(jobs_setup):
    jobs, media, original = jobs_setup
    job = jobs.start(media, "Synthetic export approval", {})
    assert finish(jobs)["status"] == "review"
    service, state = jobs.load_result(job["job_id"])
    return jobs, service, state, job["job_id"], original


def register(fixture, title="내 새 플랜"):
    jobs, _, state, job_id, _ = fixture
    return jobs.register_plan(job_id, state["draft_id"], state["revision"], title)


def test_registration_does_not_shadow_tk_callback_registry():
    import tkinter as tk
    from src.ui.recording_workspace import DraftResultPane
    assert DraftResultPane._register is tk.Misc._register
    assert DraftResultPane.register is tk.Misc.register


@pytest.mark.parametrize("disabled", [False, True])
def test_late_job_list_cannot_downgrade_a_verified_publication_label(disabled):
    from types import MethodType, SimpleNamespace
    from unittest.mock import Mock
    from src.ui.recording_workspace import DraftResultPane
    combo = Mock()
    old_label = "Recording · 검토 가능 · abcdef"
    widget = {"text": old_label, "state": "disabled" if disabled else "readonly", "values": []}
    combo.get.side_effect = lambda: widget["text"]
    combo.cget.side_effect = lambda key: widget[key]
    combo.configure.side_effect = lambda **values: widget.update(values)
    def set_value(value):
        if widget["state"] != "disabled":
            widget["text"] = value
    combo.set.side_effect = set_value
    pane = SimpleNamespace(jobs=combo, options={old_label: "job_abcdef"},
        published_jobs={"job_abcdef"}, job_states=[], dirty=False, busy=False, current=None, service=None)
    pane._set_job_selection = MethodType(DraftResultPane._set_job_selection, pane)
    stale = {"job_id": "job_abcdef", "recording_name": "Recording", "status": "review"}
    DraftResultPane.set_jobs(pane, [stale])
    assert "플랜 등록 완료" in combo.get()
    assert pane.options[combo.get()] == "job_abcdef"
    assert stale["status"] == "review"
    assert widget["state"] == ("disabled" if disabled else "readonly")
    DraftResultPane.set_jobs(pane, [dict(stale, status="error")])
    assert "오류" in combo.get(), "Cached completion must not hide a later missing or modified plan"


def test_owner_title_export_is_durable_visible_to_plan_loader_and_does_not_mutate_review(ready):
    from src.analyzer.automation_models import AutomationPlan
    jobs, service, state, job_id, original = ready
    before, reviewed = original.read_bytes(), copy.deepcopy(state["plan"])
    publication, (returned, saved) = register(ready, "  제작 자동화 제목  ")
    path = jobs.plans_dir / (publication["plan_id"] + ".json")
    exported = read_json(path)
    assert exported == dict(reviewed, name="제작 자동화 제목", user_verified=True, modified=True)
    assert AutomationPlan.from_dict(exported, jobs.templates_dir).name == "제작 자동화 제목"
    assert returned is service and service.allow_publication is False
    assert saved["plan"] == reviewed and saved["analysis"]["plan_digest"] == digest(reviewed)
    assert saved["publication"] == publication and publication["saved_and_reloaded"]
    assert publication["playlist_changed"] is False
    assert original.read_bytes() == before
    assert len(list(jobs.plans_dir.glob("*.json"))) == 2
    assert jobs.list_jobs()[0]["status"] == "published"
    restored = RecordingJobs(jobs.root, jobs.plans_dir, jobs.templates_dir)
    try:
        assert restored.load_result(job_id)[1] == saved
        assert restored.register_plan(job_id, state["draft_id"], state["revision"], publication["name"])[0] == publication
    finally:
        restored.close()
    assert len(list(jobs.plans_dir.glob("*.json"))) == 2


@pytest.mark.parametrize("title", [None, "", "   ", "a" * 201, "a\nb", "a\rb", "a\tb", "a\x00b", "a\x7fb", 123])
def test_invalid_titles_never_publish(ready, title):
    jobs, _, state, job_id, original = ready
    # The owner entry point requires an explicit title; None must not mean an implicit draft title.
    with pytest.raises(BridgeError, match="제목"):
        jobs.register_plan(job_id, state["draft_id"], state["revision"], title)
    assert list(jobs.plans_dir.glob("*.json")) == [original]


def test_same_title_on_separate_results_is_safe_and_names_are_not_paths(ready):
    jobs, _, _, _, original = ready
    first, _ = register(ready, "../동일 제목")
    job = jobs.start(jobs.root.parent / "synthetic.png", "Another synthetic result", {})
    assert finish(jobs)["status"] == "review"
    _, state = jobs.load_result(job["job_id"])
    second, _ = jobs.register_plan(job["job_id"], state["draft_id"], state["revision"], "../동일 제목")
    assert first["plan_id"] != second["plan_id"]
    assert len(list(original.parent.glob("*.json"))) == 3
    assert all(path.parent == jobs.plans_dir for path in jobs.plans_dir.glob("*.json"))


def test_approval_is_bound_to_exact_title_and_duplicate_cannot_rename_or_overwrite(ready):
    jobs, service, state, _, _ = ready
    publisher = DraftService(jobs.plans_dir, jobs.templates_dir, service.workspace)
    publisher.approve_publication(state["draft_id"], state["revision"], name="Approved")
    with pytest.raises(BridgeError) as error:
        publisher.publish_new_plan(state["draft_id"], state["revision"], name="Not approved")
    assert error.value.code == "PERMISSION_REQUIRED"
    publication, _ = register(ready, "Approved")
    before = (jobs.plans_dir / (publication["plan_id"] + ".json")).read_bytes()
    with pytest.raises(BridgeError) as error:
        register(ready, "Changed title")
    assert error.value.code == "ALREADY_PUBLISHED"
    assert (jobs.plans_dir / (publication["plan_id"] + ".json")).read_bytes() == before


def test_manual_edit_invalidates_review_and_stale_revision_cannot_register(ready):
    jobs, service, state, job_id, original = ready
    service.stage_edits(state["draft_id"], state["revision"], "manual",
        [{"op": "update_rule", "rule_id": state["plan"]["initial_rules"][0]["rule_id"], "fields": {"wait_after": 1.25}}])
    with pytest.raises(BridgeError) as error:
        register(ready)
    assert error.value.code == "REVISION_CONFLICT"
    with pytest.raises(BridgeError) as error:
        jobs.register_plan(job_id, state["draft_id"], state["revision"] + 1, "Edited")
    assert error.value.code == "ANALYSIS_REQUIRED"
    assert list(jobs.plans_dir.glob("*.json")) == [original]


@pytest.mark.parametrize("change,code", [("questions", "ANALYSIS_REQUIRED"), ("crosscheck", "CROSSCHECK_REQUIRED"),
                                       ("empty", "ANALYSIS_REQUIRED"), ("special", "PROTECTED_ACTION")])
def test_unreviewed_uncertain_empty_or_special_result_cannot_publish(ready, change, code):
    jobs, service, state, job_id, original = ready
    if change == "questions":
        state["analysis"]["open_questions"] = ["What is the termination rule?"]
    elif change == "crosscheck":
        state["crosschecks"] = state["crosschecks"][:1]
    elif change == "empty":
        state["plan"]["initial_rules"] = []
    else:
        state["plan"]["initial_rules"][0]["action_type"] = "game_mode"
    write_json(service._path(state["draft_id"]), state)
    with pytest.raises(BridgeError) as error:
        register(ready)
    assert error.value.code == code
    assert list(jobs.plans_dir.glob("*.json")) == [original]


def test_ai_capability_and_draft_only_service_never_gain_export_rights(ready):
    jobs, service, state, _, _ = ready
    scope = JobScope(service, state["draft_id"])
    for method in ("register_plan", "approve_publication", "publish_new_plan"):
        with pytest.raises(BridgeError):
            scope.dispatch(method, {})
    register(ready)
    assert not service.allow_publication
    with pytest.raises(BridgeError):
        service.publish_new_plan(state["draft_id"], state["revision"])


@pytest.mark.parametrize("status", ["running", "cancelled", "error"])
def test_unconfirmed_cancelled_or_failed_jobs_do_not_register(ready, status):
    jobs, service, _, _, original = ready
    path = service.workspace.parent / "job.json"
    write_json(path, dict(read_json(path), status=status))
    with pytest.raises(BridgeError):
        register(ready)
    assert list(jobs.plans_dir.glob("*.json")) == [original]


def test_closed_busy_or_wrong_job_cannot_publish(ready):
    jobs, _, state, job_id, original = ready
    leave = threading.Event()
    worker = threading.Thread(target=leave.wait)
    jobs.worker = worker
    worker.start()
    try:
        with pytest.raises(BridgeError) as error:
            register(ready)
        assert error.value.code == "JOB_BUSY"
    finally:
        leave.set()
        worker.join(2)
    with pytest.raises(BridgeError):
        jobs.register_plan(job_id, "draft_wrong", state["revision"], "Wrong draft")
    jobs.close()
    with pytest.raises(BridgeError):
        register(ready)
    assert list(jobs.plans_dir.glob("*.json")) == [original]


def test_export_copies_original_crops_and_preserves_nested_actions(ready):
    jobs, service, state, job_id, _ = ready
    media_id = next(iter(service.selected_media))
    crop = MediaTools(service).create_asset_crop(media_id, 0, [3, 4, 30, 25], state["draft_id"], state["revision"])
    state = service.read_draft(state["draft_id"])
    parent = state["plan"]["initial_rules"][0]["rule_id"]
    service.stage_edits(state["draft_id"], state["revision"], "nested-crop", [
        {"op": "add_rule", "action_type": "click", "parent_id": parent,
         "fields": {"target_image": crop["asset_name"], "search_region": [0, 0, 80, 60], "repeat_count": 3}}])
    state = review(service, service.read_draft(state["draft_id"]), media_id)
    publication, _ = jobs.register_plan(job_id, state["draft_id"], state["revision"], "Nested crops")
    exported = read_json(jobs.plans_dir / (publication["plan_id"] + ".json"))
    assert exported["initial_rules"] == state["plan"]["initial_rules"]
    assert (jobs.templates_dir / crop["asset_name"]).read_bytes() == (service.workspace / "assets" / crop["asset_name"]).read_bytes()


def test_write_failure_is_not_success_and_retry_preserves_original(ready, monkeypatch):
    jobs, service, state, _, original = ready
    before = original.read_bytes()
    publish = service_module.publish_bytes
    def fail(root, filename, payload):
        if Path(root) == jobs.plans_dir:
            raise PermissionError("Synthetic disk failure")
        return publish(root, filename, payload)
    monkeypatch.setattr(service_module, "publish_bytes", fail)
    with pytest.raises(PermissionError):
        register(ready)
    assert service.read_draft(state["draft_id"])["publication"] is None
    assert list(jobs.plans_dir.glob("*.json")) == [original]
    monkeypatch.setattr(service_module, "publish_bytes", publish)
    assert register(ready)[0]["saved_and_reloaded"]
    assert original.read_bytes() == before


def test_uncertain_commit_reconciles_same_payload_but_never_overwrites_with_changed_title(ready, monkeypatch):
    jobs, service, state, _, original = ready
    write = service_module.write_json
    failed = False
    def fail_record(path, value):
        nonlocal failed
        if value.get("publication") and not failed:
            failed = True
            raise OSError("Synthetic failure after plan publication")
        return write(path, value)
    monkeypatch.setattr(service_module, "write_json", fail_record)
    with pytest.raises(OSError):
        register(ready, "Exact title")
    path = jobs.plans_dir / (state["plan"]["plan_id"] + ".json")
    before = path.read_bytes()
    assert service.read_draft(state["draft_id"])["publication"] is None
    with pytest.raises(BridgeError) as error:
        register(ready, "Other title")
    assert error.value.code == "SAVE_STATE_UNKNOWN"
    assert path.read_bytes() == before
    publication, _ = register(ready, "Exact title")
    assert publication["saved_and_reloaded"] and path.read_bytes() == before
    assert len(list(original.parent.glob("*.json"))) == 2


@pytest.mark.parametrize("change", ["deleted", "modified"])
def test_previous_receipt_is_not_success_if_plan_was_deleted_or_changed(ready, change):
    jobs, _, _, _, _ = ready
    publication, _ = register(ready)
    path = jobs.plans_dir / (publication["plan_id"] + ".json")
    if change == "deleted":
        path.unlink()
    else:
        write_json(path, dict(read_json(path), name="Changed outside registration"))
    with pytest.raises(BridgeError) as error:
        register(ready)
    assert error.value.code == "SAVE_STATE_UNKNOWN"
