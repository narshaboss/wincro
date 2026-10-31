"""Recording job simulations; scripted actors are not real GPT comprehension tests."""

import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest

from src.agent_bridge.contracts import BridgeError
from src.agent_bridge import recording_jobs as jobs_module
from src.agent_bridge.recording_jobs import JobScope, RecordingJobs
from src.agent_bridge.service import DraftService
from src.agent_bridge.storage import read_json, write_json
from src.agent_bridge.codex_runner import CodexRunner, CRITICAL_FEATURES, DISABLED_FEATURES, clean_message, load_runtime


class FakeHost:
    scopes = {}
    def __init__(self, service, folder):
        self.service = service
        self.client_connected = False
    def start(self):
        self.key = str(id(self))
        self.scopes[self.key] = self.service
        return self.key
    def close(self):
        self.client_connected = False


class ReviewingActor:
    """Uses actual frame/tool validation with predetermined test-only semantics."""
    def __init__(self, settings, folder):
        pass
    def cancel(self):
        pass
    def run(self, descriptor, prompt, cancelled, progress):
        scope = FakeHost.scopes[descriptor]
        self.scope = scope
        invoke = scope.dispatch
        key = scope.draft_id
        media = invoke("list_recordings", {})[0]["recording_id"]
        state = invoke("read_draft", {"draft_id": key})
        invoke("stage_edits", dict(draft_id=key, expected_revision=state["revision"], request_id="test-add",
            operations=[{"op": "add_rule", "action_type": "hotkey", "fields": {"description": "Confirm", "action_keys": ["enter"]}}]))
        state = invoke("read_draft", {"draft_id": key})
        rule = state["plan"]["initial_rules"][0]["rule_id"]
        frame = invoke("get_frames", dict(recording_id=media, timestamps=[0]))["frames"][0]
        invoke("record_analysis", dict(draft_id=key, expected_revision=state["revision"], goal="Test confirmation",
            steps=[dict(rule_id=rule, recording_id=media, seconds=0, frame_trace_id=frame["frame_trace_id"],
                observation="Synthetic confirmation frame", purpose="Confirm", expected_result="Next synthetic state")]))
        for direction in ("video_to_plan", "plan_to_video"):
            state = invoke("read_draft", {"draft_id": key})
            frame = invoke("get_frames", dict(recording_id=media, timestamps=[0]))["frames"][0]
            invoke("record_crosscheck", dict(draft_id=key, expected_revision=state["revision"], direction=direction,
                checks=[dict(rule_id=rule, frame_trace_id=frame["frame_trace_id"], result="match", note="Scripted simulation only")]))
        return {"exit_code": 0, "message": "Scripted actor: mechanical pipeline only"}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "LocalHost", FakeHost)
    monkeypatch.setattr(jobs_module, "protect_directory", lambda path: Path(path).mkdir(parents=True, exist_ok=True))
    plans, templates = tmp_path / "plans", tmp_path / "templates"
    plans.mkdir()
    templates.mkdir()
    original = plans / "plan_original.json"
    original.write_text('{"plan_id":"plan_original","name":"Untouched","initial_rules":[],"monitoring_rules":[]}', encoding="utf-8")
    from PIL import Image
    media = tmp_path / "synthetic.png"
    Image.new("RGB", (80, 60), "#606060").save(media)
    jobs = RecordingJobs(tmp_path / "jobs", plans, templates, ReviewingActor)
    yield jobs, media, original
    jobs.close()
    if jobs.worker:
        jobs.worker.join(5)


def finish(jobs):
    jobs.worker.join(8)
    assert not jobs.busy
    return jobs.snapshot()


def test_complete_tool_pipeline_stores_review_draft_but_never_changes_playback_plan(setup):
    jobs, media, original = setup
    before = original.read_bytes()
    result = jobs.start(media, "Confirm in the synthetic workflow", {})
    state = finish(jobs)
    assert state["status"] == "review", state
    service, draft = jobs.load_result(result["job_id"])
    assert draft["plan"]["initial_rules"][0]["action_keys"] == ["enter"]
    assert service.validate_draft(draft["draft_id"])["crosscheck_ready"]
    assert service.allow_publication is False
    with pytest.raises(BridgeError, match="cannot write playback"):
        service.publish_new_plan(draft["draft_id"], draft["revision"])
    assert original.read_bytes() == before
    assert list(original.parent.glob("*.json")) == [original]
    assert list(service.templates_dir.iterdir()) == []
    recovered = RecordingJobs(jobs.root, jobs.plans_dir, jobs.templates_dir)
    restored_service, restored = recovered.load_result(result["job_id"])
    assert restored == draft
    assert recovered.list_jobs()[0]["status"] == "review"
    rule = restored["plan"]["initial_rules"][0]
    restored_service.stage_edits(restored["draft_id"], restored["revision"], "manual-wait-change",
        [{"op": "update_rule", "rule_id": rule["rule_id"], "fields": {"wait_after": 1.25}}])
    assert recovered.list_jobs()[0]["status"] == "needs_review"
    recovered.close()


def test_process_exit_zero_without_gpt_evidence_is_not_analysis_completion(setup):
    jobs, media, original = setup
    class EmptyActor(ReviewingActor):
        def run(self, *_):
            return {"exit_code": 0, "message": "I am done"}
    jobs.runner_factory = EmptyActor
    jobs.start(media, "Do not mistake process completion for understanding", {})
    state = finish(jobs)
    assert state["status"] == "needs_review"
    assert jobs.load_result(state["job_id"])[1]["analysis"] is None


def test_cancellation_revokes_tools_and_preserves_saved_partial_draft(setup):
    jobs, media, original = setup
    entered, leave = threading.Event(), threading.Event()
    calls = []
    class PausingActor(ReviewingActor):
        def run(self, descriptor, prompt, cancelled, progress):
            scope = FakeHost.scopes[descriptor]
            self.scope = scope
            entered.set()
            leave.wait(5)
            try:
                scope.dispatch("save_draft", {"draft_id": scope.draft_id, "expected_revision": 1})
            except BridgeError as exc:
                calls.append(exc.code)
            return {"exit_code": 0}
    jobs.runner_factory = PausingActor
    jobs.start(media, "Cancel", {})
    assert entered.wait(5)
    jobs.cancel()
    leave.set()
    state = finish(jobs)
    assert state["status"] == "cancelled"
    assert calls == ["JOB_CANCELLED"]
    assert jobs.load_result(state["job_id"])[1]["revision"] == 1
    assert not jobs.connected


def test_runtime_failure_and_shutdown_do_not_silently_succeed_or_erase_drafts(setup):
    jobs, media, original = setup
    class BrokenActor(ReviewingActor):
        def run(self, *_):
            return {"exit_code": 1, "message": "authentication required"}
    jobs.runner_factory = BrokenActor
    jobs.start(media, "Need actual authentication", {})
    state = finish(jobs)
    assert state["status"] == "error"
    assert "authentication" in state["message"]
    assert jobs.load_result(state["job_id"])[1]["plan"]["initial_rules"] == []
    jobs.close()
    with pytest.raises(BridgeError):
        jobs.start(media, "closed", {})


def test_job_capability_cannot_access_other_drafts_publish_or_unselected_references(setup):
    jobs, media, original = setup
    service = DraftService(jobs.plans_dir, jobs.templates_dir, jobs.root / "scope" / "drafts", allow_publication=False)
    first, second = service.open_draft("First"), service.open_draft("Other")
    scope = JobScope(service, first["draft_id"])
    for method, arguments in (
        ("open_draft", {"name": "Unauthorized"}), ("publish_new_plan", {}), ("approve_publication", {}),
        ("read_draft", {"draft_id": second["draft_id"]}), ("read_plan", {"plan_id": "plan_original"}),
        ("get_frames", {"recording_id": "unknown", "timestamps": [0]}),
    ):
        with pytest.raises(BridgeError):
            scope.dispatch(method, arguments)
    assert len(scope.dispatch("list_drafts", {})) == 1
    scope.revoked.set()
    with pytest.raises(BridgeError):
        scope.dispatch("read_draft", {"draft_id": first["draft_id"]})


def test_draft_only_storage_rejects_overlap_and_publication_even_with_a_forged_approval(setup):
    jobs, media, original = setup
    with pytest.raises(BridgeError):
        RecordingJobs(jobs.plans_dir / "jobs", jobs.plans_dir, jobs.templates_dir)
    with pytest.raises(BridgeError):
        DraftService(jobs.plans_dir, jobs.templates_dir, jobs.plans_dir / "drafts", allow_publication=False)
    service = DraftService(jobs.plans_dir, jobs.templates_dir, jobs.root / "separate", allow_publication=False)
    draft = service.open_draft("No publication")
    with pytest.raises(BridgeError):
        service.approve_publication(draft["draft_id"], 1)
    state = read_json(service._path(draft["draft_id"]))
    state["approval"] = {"anything": "forged"}
    write_json(service._path(draft["draft_id"]), state)
    with pytest.raises(BridgeError):
        service.publish_new_plan(draft["draft_id"], 1)
    assert len(list(jobs.plans_dir.glob("*.json"))) == 1


def test_stale_running_record_is_read_only_not_falsely_declared_completed(setup):
    jobs, media, original = setup
    jobs.start(media, "Fixture", {})
    state = finish(jobs)
    path = jobs.root / state["job_id"] / "job.json"
    write_json(path, dict(read_json(path), status="running"))
    recovered = RecordingJobs(jobs.root, jobs.plans_dir, jobs.templates_dir)
    assert recovered.list_jobs()[0]["status"] == "unconfirmed"
    service, draft = recovered.load_result(state["job_id"])
    assert service.closed
    with pytest.raises(BridgeError):
        service.save_draft(draft["draft_id"], draft["revision"])
    recovered.close()


def test_recording_catalog_never_includes_templates_or_writes_database(tmp_path):
    from src.agent_bridge.recording_catalog import recording_catalog
    recording_dir = tmp_path / "recordings"
    recording_dir.mkdir()
    video = recording_dir / "video.mp4"
    video.write_bytes(b"fixture metadata only")
    (recording_dir / "mask.png").write_bytes(b"not a recording")
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "template.mp4").write_bytes(b"not a recording")
    database = SimpleNamespace(get_all_recordings=lambda: [SimpleNamespace(video_path=str(video), name="Named", id=7, input_log_path="chosen.json")])
    result = recording_catalog(database, recording_dir)
    assert len(result) == 1
    assert result[0]["name"] == "Named" and result[0]["recording_id"] == 7


def test_codex_command_is_read_only_isolated_and_only_exposes_bounded_job_tools(tmp_path):
    runner = CodexRunner({}, tmp_path)
    args = runner.arguments("codex.exe", "python.exe", "C:/work/tools/wincro_mcp_server.py", "secret-path.json")
    assert args[1:4] == ["app-server", "--listen", "stdio://"]
    parameters = runner.thread_parameters()
    assert parameters["environments"] == [] and parameters["ephemeral"] is True
    assert parameters["sandbox"] == "read-only" and parameters["approvalPolicy"] == "never"
    for feature in DISABLED_FEATURES:
        assert f"features.{feature}=false" in args
    assert "approval_policy=\"never\"" in args
    assert "--dangerously-bypass-approvals-and-sandbox" not in args
    assert not any(value.startswith("agents.enabled=") for value in args)
    tools = json.loads(next(value.split("=", 1)[1] for value in args if value.startswith(f"mcp_servers.{runner.server_name}.enabled_tools=")))
    assert "get_frames" in tools and "stage_edits" in tools
    assert "publish_new_plan" not in tools and "open_draft" not in tools
    assert "notify=[]" in args and 'model_reasoning_effort="high"' in args
    assert 'service_tier="fast"' in args and "features.fast_mode=false" in args
    assert parameters["serviceTier"] is None
    assert runner.turn_parameters("isolated-thread", "goal")["serviceTier"] is None
    assert runner.turn_parameters("isolated-thread", "goal")["environments"] == []
    assert f'profile="{runner.server_name}"' in args and f"profiles.{runner.server_name}={{}}" in args
    connections = runner.thread_parameters([runner.server_name, "unselected", "other.with.dots"])["config"]["mcp_servers"]
    assert connections["unselected"] == connections["other.with.dots"] == {"enabled": False}
    assert connections[runner.server_name] == runner.mcp_config


def test_preflight_refuses_missing_executable_and_secret_errors_are_bounded(tmp_path):
    runner = CodexRunner({"codex": "missing.exe"}, tmp_path)
    with pytest.raises(BridgeError) as caught:
        runner.preflight()
    assert caught.value.code == "RUNTIME_MISSING"
    text = clean_message("Bearer " + "x"*80 + " sk-" + "y"*100)
    assert "x"*80 not in text and "y"*100 not in text


@pytest.mark.parametrize("value", [None, [], {"python": None}, {"max_seconds": True}])
def test_malformed_runtime_settings_are_not_applied_or_overwritten(tmp_path, value):
    path = tmp_path / "runtime.json"
    write_json(path, value)
    before = path.read_bytes()
    with pytest.raises(ValueError):
        load_runtime(path)
    assert path.read_bytes() == before


def test_runtime_reload_retains_only_supported_settings(tmp_path):
    path = tmp_path / "runtime.json"
    values = {"codex": "codex.exe", "python": "python.exe", "model": "", "reference": None, "max_seconds": 1200}
    write_json(path, dict(values, unknown="not a runtime option"))
    assert load_runtime(path) == values
    assert load_runtime(tmp_path / "not_created.json")["max_seconds"] == 1200
    assert not (tmp_path / "not_created.json").exists()


@pytest.mark.parametrize("value", [None, [], {"job_id": "wrong"}])
def test_corrupt_job_record_does_not_hide_other_saved_results_or_delete_files(setup, value):
    jobs, media, original = setup
    jobs.start(media, "Keep valid results", {})
    state = finish(jobs)
    damaged = jobs.root / "job_corrupt123"
    damaged.mkdir()
    path = damaged / "job.json"
    write_json(path, value)
    before = path.read_bytes()
    records = jobs.list_jobs()
    assert len(records) == 2
    assert records[0]["job_id"] == state["job_id"]
    assert records[1]["status"] == "error" and "보존" in records[1]["recording_name"]
    with pytest.raises(BridgeError, match="보존"):
        jobs.load_result(damaged.name)
    assert jobs.load_result(state["job_id"])[1]["analysis"]
    assert path.read_bytes() == before
