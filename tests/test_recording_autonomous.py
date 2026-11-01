"""Autonomous recording analysis without dialog prompts or extra media scope."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.agent_bridge.contracts import BridgeError
from src.agent_bridge.recording_jobs import JobScope
from src.agent_bridge.storage import read_json, write_json
from src.ui.recording_workspace import RecordingWorkspace
from src.ui.recording_workspace import DraftResultPane
from tests.test_recording_followup import waiting, resume, ContinueActor
from tests.test_recording_jobs import setup as jobs_setup, finish


def test_analyze_is_direct_and_only_uses_selected_recording_and_associated_log():
    view = object.__new__(RecordingWorkspace)
    view.jobs = SimpleNamespace(busy=False)
    view.busy = False
    view.owner = SimpleNamespace(_is_recording=False, _starting=False, _stopping=False)
    view.result = SimpleNamespace(dirty=False, busy=False)
    view.connection = SimpleNamespace(connected=True)
    view.selected = SimpleNamespace(path="selected.mp4", input_log="associated.json")
    view._start_analysis = Mock()
    view.set_status = Mock()
    view._analyze()
    view._start_analysis.assert_called_once_with("selected.mp4", "associated.json", ())
    view.selected = None
    view._start_analysis.reset_mock()
    view._analyze()
    view._start_analysis.assert_not_called()


def test_automatic_scope_rejects_question_requests_without_modifying_the_draft(waiting):
    _, _, _, service, state, _ = waiting
    scope = JobScope(service, state["draft_id"])
    with pytest.raises(BridgeError) as error:
        scope.dispatch("record_analysis", {"draft_id": state["draft_id"], "open_questions": ["Which speed?"]})
    assert error.value.code == "AUTONOMOUS_REVIEW_REQUIRED"
    assert service.read_draft(state["draft_id"]) == state


def test_legacy_questions_are_reanalyzed_without_requesting_an_answer(waiting):
    jobs, _, _, _, state, job_id = waiting
    jobs.runner_factory = ContinueActor
    resume(waiting, answer="")
    assert finish(jobs)["status"] == "review"
    assert jobs.load_result(job_id)[1]["plan"] == state["plan"]
    assert jobs.load_result(job_id)[1]["analysis"]["open_questions"] == []
    assert jobs.snapshot()["answers"][-1]["answer"] == ""


def test_prompt_requires_recorded_defaults_and_no_questions(waiting):
    jobs, _, _, _, state, _ = waiting
    prompt = jobs._prompt("recording_fixture", state["draft_id"])
    for phrase in ("NEVER ask the user questions", "Preserve observed action order", "one bounded performance",
                   "open_questions=[]", "factual remaining_issues", "both fresh crosschecks"):
        assert phrase in prompt


def test_linked_input_log_identity_is_preserved_and_changes_block_resume(jobs_setup, tmp_path):
    jobs, media, _ = jobs_setup
    log = tmp_path / "selected-input.json"
    write_json(log, {"events": []})
    job = jobs.start(media, "Video and selected linked log", {}, input_log=log)
    assert finish(jobs)["status"] == "review"
    metadata = read_json(jobs.root / job["job_id"] / "job.json")
    assert Path(metadata["media"]["input_log"]) == log
    assert metadata["media"]["log_sha256"]
    service, state = jobs.load_result(job["job_id"])
    write_json(log, {"events": [{"key": "changed"}]})
    jobs.resume(job["job_id"], state["draft_id"], state["revision"], "", media, {}, input_log=log)
    assert finish(jobs)["status"] == "error"
    assert "MEDIA_NOT_READY" in jobs.snapshot()["message"]
    assert service.read_draft(state["draft_id"]) == state


def test_recording_workspace_contains_no_consent_or_question_dialog_entrypoint():
    import inspect
    import src.ui.recording_workspace as workspace
    source = inspect.getsource(workspace)
    assert "MediaConsentDialog" not in source
    assert "RecordingFollowupDialog" not in source
    assert "질문 답변 · 계속" not in source
    assert "동의하고 분석" not in source
    assert not (Path(workspace.__file__).parent / "recording_followup.py").exists()


def test_newer_result_revision_blocks_a_stale_ui_but_manual_edits_do_not():
    snapshot = {"job_id": "job_one", "result_revision": 9}
    pane = SimpleNamespace(current={"revision": 8}, service=SimpleNamespace(workspace=Path("jobs/job_one/drafts")),
        workspace=SimpleNamespace(jobs=SimpleNamespace(snapshot=lambda: snapshot)))
    assert DraftResultPane._awaiting_current_result(pane)
    pane.current["revision"] = 9
    assert not DraftResultPane._awaiting_current_result(pane)
    pane.current["revision"] = 10
    assert not DraftResultPane._awaiting_current_result(pane)
    snapshot["job_id"] = "job_other"
    pane.current["revision"] = 1
    assert not DraftResultPane._awaiting_current_result(pane)
