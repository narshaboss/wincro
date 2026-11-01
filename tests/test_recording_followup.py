"""Synthetic answers, durable continuation and bounded AI-review simulations."""

import copy
import threading

import pytest

from src.agent_bridge.contracts import BridgeError
from src.agent_bridge.recording_jobs import JobScope, RecordingJobs
from src.agent_bridge.storage import read_json, write_json
from tests.test_ai_draft_bridge import review
from tests.test_recording_jobs import setup as jobs_setup, finish, FakeHost, ReviewingActor


class QuestionActor(ReviewingActor):
    def run(self, descriptor, prompt, cancelled, progress):
        super().run(descriptor, prompt, cancelled, progress)
        service = self.scope.service
        state = service.read_draft(self.scope.draft_id)
        review(service, state, next(iter(service.selected_media)), ["녹화와 같은 속도로 진행할까요?"])
        return {"exit_code": 0, "message": "녹화와 같은 속도로 진행할까요?"}


class ContinueActor(ReviewingActor):
    prompts = []
    def run(self, descriptor, prompt, cancelled, progress):
        scope = FakeHost.scopes[descriptor]
        self.scope = scope
        self.prompts.append(prompt)
        service = scope.service
        state = service.read_draft(scope.draft_id)
        assert state["analysis"] is None and not state["crosschecks"]
        review(service, state, next(iter(service.selected_media)))
        return {"exit_code": 0, "message": "Synthetic follow-up reviewed"}


@pytest.fixture
def waiting(jobs_setup):
    jobs, media, original = jobs_setup
    jobs.runner_factory = QuestionActor
    job = jobs.start(media, "Synthetic complete task", {})
    assert finish(jobs)["status"] == "needs_review"
    service, state = jobs.load_result(job["job_id"])
    return jobs, media, original, service, state, job["job_id"]


def resume(waiting, answer="녹화와 같은 속도로 진행해주세요.", **kwargs):
    jobs, media, _, _, state, job_id = waiting
    return jobs.resume(job_id, state["draft_id"], state["revision"], answer, kwargs.pop("recording", media), {}, **kwargs)


def test_answer_continues_same_draft_after_restart_and_persists_everything(waiting):
    jobs, media, original, _, state, job_id = waiting
    before = original.read_bytes()
    recovered = RecordingJobs(jobs.root, jobs.plans_dir, jobs.templates_dir, ContinueActor)
    ContinueActor.prompts.clear()
    try:
        info = recovered.followup_info(job_id, state["draft_id"], state["revision"])
        assert info["questions"] == ["녹화와 같은 속도로 진행할까요?"]
        started = recovered.resume(job_id, state["draft_id"], state["revision"], "녹화 속도로", media, {})
        assert started["job_id"] == job_id and started["attempt"] == 2
        assert finish(recovered)["status"] == "review"
        service, restored = recovered.load_result(job_id)
        assert restored["draft_id"] == state["draft_id"]
        assert restored["plan"] == state["plan"]
        assert restored["revision"] > state["revision"]
        assert service.validate_draft(state["draft_id"])["crosscheck_ready"]
        assert restored["analysis"]["open_questions"] == []
        metadata = read_json(jobs.root / job_id / "job.json")
        assert metadata == recovered.snapshot()
        assert metadata["answers"][-1]["answer"] == "녹화 속도로"
        assert read_json(jobs.root / job_id / "review_before_attempt_2.json") == state
        assert "녹화 속도로" in ContinueActor.prompts[-1]
        assert original.read_bytes() == before
        assert list(original.parent.glob("*.json")) == [original]
        assert service.allow_publication is False
    finally:
        recovered.close()


@pytest.mark.parametrize("answer,code", [("", "ANSWER_REQUIRED"), ("  ", "ANSWER_REQUIRED"),
    (None, "INVALID_FIELD"), (3, "INVALID_FIELD"), ("x" * 4001, "INVALID_FIELD")])
def test_answer_validation_does_not_mutate_draft_or_job(waiting, answer, code):
    jobs, _, _, service, state, _ = waiting
    metadata = copy.deepcopy(jobs.snapshot())
    with pytest.raises(BridgeError) as error:
        resume(waiting, answer)
    assert error.value.code == code
    assert service.read_draft(state["draft_id"]) == state and jobs.snapshot() == metadata


def test_different_video_is_rejected_before_existing_review_is_invalidated(waiting, tmp_path):
    from PIL import Image
    jobs, _, _, service, state, _ = waiting
    other = tmp_path / "other.png"
    Image.new("RGB", (80, 60), "red").save(other)
    resume(waiting, recording=other)
    assert finish(jobs)["status"] == "error"
    assert "MEDIA_NOT_READY" in jobs.snapshot()["message"]
    assert service.read_draft(state["draft_id"]) == state


def test_manual_edits_are_preserved_and_stale_followup_revision_is_rejected(waiting):
    jobs, media, _, service, state, job_id = waiting
    rule_id = state["plan"]["initial_rules"][0]["rule_id"]
    service.stage_edits(state["draft_id"], state["revision"], "manual-change", [
        {"op": "update_rule", "rule_id": rule_id, "fields": {"wait_after": 1.25}}])
    with pytest.raises(BridgeError) as error:
        resume(waiting)
    assert error.value.code == "REVISION_CONFLICT"
    current = service.read_draft(state["draft_id"])
    jobs.runner_factory = ContinueActor
    jobs.resume(job_id, current["draft_id"], current["revision"], "수정한 대기시간 유지", media, {})
    assert finish(jobs)["status"] == "review"
    assert jobs.load_result(job_id)[1]["plan"]["initial_rules"][0]["wait_after"] == 1.25


def test_legacy_job_requires_new_explicit_selection_and_preserves_old_draft(waiting):
    jobs, media, _, service, state, job_id = waiting
    path = jobs.root / job_id / "job.json"
    old = read_json(path)
    old.pop("media")
    write_json(path, old)
    assert jobs.followup_info(job_id, state["draft_id"], state["revision"])["media"] is None
    with pytest.raises(BridgeError) as error:
        resume(waiting)
    assert error.value.code == "MEDIA_RESELECT_REQUIRED"
    jobs.runner_factory = ReviewingActor
    fresh = jobs.start(media, old["goal"], {}, answer="녹화대로")
    assert finish(jobs)["status"] == "review"
    assert fresh["job_id"] != job_id
    assert service.read_draft(state["draft_id"]) == state
    assert jobs.snapshot()["answers"][0]["answer"] == "녹화대로"


def test_private_intervals_cannot_be_removed_by_continuation(waiting):
    jobs, _, _, service, state, job_id = waiting
    path = jobs.root / job_id / "job.json"
    metadata = read_json(path)
    metadata["media"]["excluded"] = [[0, 1]]
    write_json(path, metadata)
    jobs.runner_factory = ContinueActor
    resume(waiting, excluded=())
    assert finish(jobs)["status"] == "error"
    assert "PRIVATE_INTERVAL" in jobs.snapshot()["message"]
    assert jobs.service.selected_media[next(iter(jobs.service.selected_media))]["excluded"] == [[0, 1]]


def test_unconfirmed_busy_published_or_pending_publication_cannot_continue(waiting):
    jobs, _, _, service, state, job_id = waiting
    scope = JobScope(service, state["draft_id"])
    for method in ("resume", "followup_info", "begin_reanalysis"):
        with pytest.raises(BridgeError):
            scope.dispatch(method, {})
    intent = service.workspace / (state["draft_id"] + ".publish-intent.json")
    write_json(intent, {"filename": "plan_fixture.json", "sha256": "unknown"})
    with pytest.raises(BridgeError) as error:
        resume(waiting)
    assert error.value.code == "SAVE_STATE_UNKNOWN"
    intent.unlink()
    leave = threading.Event()
    worker = threading.Thread(target=leave.wait)
    jobs.worker = worker
    worker.start()
    try:
        with pytest.raises(BridgeError) as error:
            resume(waiting)
        assert error.value.code == "JOB_BUSY"
    finally:
        leave.set()
        worker.join(2)
    state["publication"] = {"name": "Published"}
    write_json(service._path(state["draft_id"]), state)
    with pytest.raises(BridgeError):
        resume(waiting)


def test_cancel_continuation_revokes_scope_and_preserves_saved_actions(waiting):
    jobs, _, _, _, state, job_id = waiting
    entered = threading.Event()
    class WaitingActor(ContinueActor):
        def run(self, descriptor, prompt, cancelled, progress):
            entered.set()
            cancelled.wait(5)
            return {"exit_code": 0}
    jobs.runner_factory = WaitingActor
    resume(waiting)
    assert entered.wait(5)
    jobs.cancel()
    assert finish(jobs)["status"] == "cancelled"
    assert jobs.scope.revoked.is_set()
    assert jobs.load_result(job_id)[1]["plan"] == state["plan"]


@pytest.mark.parametrize("finish_after,expected_calls,expected_status", [(2, 2, "review"), (99, 3, "needs_review")])
def test_missing_crosschecks_get_bounded_ai_followups_not_fake_completion(jobs_setup, finish_after, expected_calls, expected_status):
    jobs, media, original = jobs_setup
    class PartialActor(ReviewingActor):
        calls = 0
        def run(self, descriptor, prompt, cancelled, progress):
            type(self).calls += 1
            scope = FakeHost.scopes[descriptor]
            service = scope.service
            state = service.read_draft(scope.draft_id)
            if not state["plan"]["initial_rules"]:
                service.stage_edits(scope.draft_id, state["revision"], "once", [
                    {"op": "add_rule", "action_type": "hotkey", "fields": {"action_keys": ["enter"]}}])
            current = review(service, service.read_draft(scope.draft_id), next(iter(service.selected_media)))
            if type(self).calls < finish_after:
                analysis = current["analysis"]
                service.record_analysis(scope.draft_id, current["revision"], analysis["goal"], analysis["steps"])
            return {"exit_code": 0, "message": "Synthetic partial review"}
    jobs.runner_factory = PartialActor
    jobs.start(media, "Complete fresh reviews", {})
    assert finish(jobs)["status"] == expected_status
    assert PartialActor.calls == expected_calls
    assert len(jobs.load_result(jobs.snapshot()["job_id"])[1]["plan"]["initial_rules"]) == 1
    assert list(original.parent.glob("*.json")) == [original]


def test_question_does_not_trigger_automatic_guessing_or_extra_model_turns(waiting):
    jobs, _, _, _, _, _ = waiting
    assert jobs.snapshot()["review_pass"] == 1
    assert jobs.snapshot()["status"] == "needs_review"


def test_failed_answer_save_does_not_claim_a_started_continuation(waiting, monkeypatch):
    from src.agent_bridge import recording_jobs as module
    jobs, _, _, service, state, _ = waiting
    metadata = copy.deepcopy(jobs.snapshot())
    monkeypatch.setattr(module, "write_json", lambda *args: (_ for _ in ()).throw(OSError("Synthetic full disk")))
    with pytest.raises(OSError):
        resume(waiting)
    assert not jobs.busy and service.read_draft(state["draft_id"]) == state
    assert jobs.snapshot() == metadata
