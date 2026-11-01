"""End-to-end authoring/registration simulations, never real desktop playback."""

from pathlib import Path

import pytest

from src.agent_bridge import service as service_module
from src.agent_bridge.contracts import BridgeError
from src.agent_bridge.recording_jobs import RecordingJobs
from src.agent_bridge.storage import read_json, write_json
from src.ui.analysis_progress import AnalysisProgress
from tests.test_ai_draft_bridge import review
from tests.test_recording_jobs import setup as jobs_setup, finish, ReviewingActor, FakeHost


def start(jobs, media):
    return jobs.start(media, "Follow the selected recording without questions", {}, auto_register=True)


def test_analyze_finishes_with_one_durable_new_plan_and_no_human_review_claim(jobs_setup):
    from src.analyzer.automation_models import AutomationPlan
    jobs, media, original = jobs_setup
    before = original.read_bytes()
    job = start(jobs, media)
    final = finish(jobs)
    assert final["status"] == "published", final
    service, draft = jobs.load_result(job["job_id"])
    receipt = draft["publication"]
    assert receipt == final["publication"]
    exported = read_json(jobs.plans_dir / (receipt["plan_id"] + ".json"))
    assert exported["name"] == "synthetic · 분석 플랜"
    assert exported["initial_rules"] == draft["plan"]["initial_rules"]
    assert exported["user_verified"] is True  # Legacy player listing permission.
    assert receipt["user_reviewed"] is False
    assert AutomationPlan.from_dict(exported, jobs.templates_dir).initial_rules[0].action_keys == ["enter"]
    assert receipt["saved_and_reloaded"] and not receipt["playlist_changed"]
    assert original.read_bytes() == before
    assert not service.allow_publication and jobs.scope.revoked.is_set()
    restored = RecordingJobs(jobs.root, jobs.plans_dir, jobs.templates_dir)
    try:
        assert restored.list_jobs()[0]["status"] == "published"
        assert restored.load_result(job["job_id"])[1] == draft
        assert restored.register_plan(job["job_id"], draft["draft_id"], draft["revision"], receipt["name"])[0] == receipt
        assert len(list(jobs.plans_dir.glob("*.json"))) == 2
    finally:
        restored.close()


class RepairActor(ReviewingActor):
    calls = 0
    fix = True
    prompts = []

    def run(self, descriptor, prompt, cancelled, progress):
        type(self).calls += 1
        self.prompts.append(prompt)
        scope = FakeHost.scopes[descriptor]
        service, key = scope.service, scope.draft_id
        if self.calls == 1:
            super().run(descriptor, prompt, cancelled, progress)
        state = service.read_draft(key)
        service.stage_edits(key, state["revision"], f"enabled-{self.calls}", [{"op": "update_rule",
            "rule_id": state["plan"]["initial_rules"][0]["rule_id"], "fields": {"enabled": self.calls > 1 and self.fix}}])
        review(service, service.read_draft(key), next(iter(service.selected_media)))
        return {"exit_code": 0, "message": "Synthetic review only"}


@pytest.mark.parametrize("fix", [False, True])
def test_disabled_candidate_gets_explicit_repair_feedback_not_silent_publication(jobs_setup, fix):
    jobs, media, original = jobs_setup
    class Actor(RepairActor):
        calls, prompts = 0, []
    Actor.fix = fix
    jobs.runner_factory = Actor
    start(jobs, media)
    final = finish(jobs)
    assert "모든 액션이 비활성" in Actor.prompts[1]
    assert Actor.calls == (2 if fix else 3)
    assert final["status"] == ("published" if fix else "needs_review")
    assert len(list(jobs.plans_dir.glob("*.json"))) == (2 if fix else 1)
    if not fix:
        assert "모든 액션이 비활성" in final["message"]
        assert not jobs.load_result(final["job_id"])[1]["publication"]


@pytest.mark.parametrize("fail_count", [1, 100])
def test_registration_failure_retries_only_save_not_gpt_and_can_resume_without_duplicates(jobs_setup, monkeypatch, fail_count):
    jobs, media, original = jobs_setup
    publish = service_module.publish_bytes
    calls = []
    def fail(root, name, payload):
        if Path(root) == jobs.plans_dir:
            calls.append(name)
            if len(calls) <= fail_count:
                raise OSError("Synthetic disk failure")
        return publish(root, name, payload)
    monkeypatch.setattr(service_module, "publish_bytes", fail)
    start(jobs, media)
    final = finish(jobs)
    assert final["review_pass"] == 1
    assert len(calls) == 2 and calls[0] == calls[1]
    assert final["status"] == ("published" if fail_count == 1 else "registration_failed")
    if fail_count > 1:
        service, draft = jobs.load_result(final["job_id"])
        assert draft["analysis"] and service.validate_draft(draft["draft_id"])["crosscheck_ready"]
        assert list(jobs.plans_dir.glob("*.json")) == [original]
        monkeypatch.setattr(service_module, "publish_bytes", publish)
        receipt, _ = jobs.register_plan(final["job_id"], draft["draft_id"], draft["revision"], final["registration_name"])
        assert jobs.snapshot()["status"] == "published"
        assert read_json(jobs.plans_dir / (receipt["plan_id"] + ".json"))["user_verified"] is True
        assert receipt["user_reviewed"] is False
    assert len(list(jobs.plans_dir.glob("*.json"))) == 2


def test_failure_after_commit_reconciles_same_plan_without_reanalysis(jobs_setup, monkeypatch):
    jobs, media, _ = jobs_setup
    write = service_module.write_json
    calls = []
    def uncertain(path, value):
        if value.get("publication"):
            calls.append(str(path))
            if len(calls) == 1:
                raise OSError("Synthetic lost receipt after plan commit")
        return write(path, value)
    monkeypatch.setattr(service_module, "write_json", uncertain)
    start(jobs, media)
    final = finish(jobs)
    assert final["status"] == "published", final
    assert final["review_pass"] == 1 and len(calls) == 2
    assert len(list(jobs.plans_dir.glob("*.json"))) == 2


def test_cancel_before_registration_leaves_draft_only(jobs_setup, monkeypatch):
    jobs, media, original = jobs_setup
    persist = jobs._persist
    def cancel_at_registration(**values):
        persist(**values)
        if values.get("status") == "registering":
            jobs.cancel()
    monkeypatch.setattr(jobs, "_persist", cancel_at_registration)
    start(jobs, media)
    assert finish(jobs)["status"] == "cancelled"
    assert list(jobs.plans_dir.glob("*.json")) == [original]


def test_cancel_after_commit_does_not_hide_saved_plan(jobs_setup, monkeypatch):
    jobs, media, _ = jobs_setup
    publish = service_module.publish_bytes
    def cancel_after_commit(root, name, payload):
        result = publish(root, name, payload)
        if Path(root) == jobs.plans_dir:
            jobs.cancel()
        return result
    monkeypatch.setattr(service_module, "publish_bytes", cancel_after_commit)
    start(jobs, media)
    assert finish(jobs)["status"] == "published"
    assert len(list(jobs.plans_dir.glob("*.json"))) == 2


@pytest.mark.parametrize("has_receipt", [True, False])
def test_restart_reconciles_registration_state_and_never_renames_uncertain_plan(jobs_setup, has_receipt):
    jobs, media, _ = jobs_setup
    job = start(jobs, media)
    assert finish(jobs)["status"] == "published"
    service, draft = jobs.load_result(job["job_id"])
    if not has_receipt:
        draft["publication"] = None
        write_json(service._path(draft["draft_id"]), draft)
    metadata = jobs.snapshot()
    write_json(service.workspace.parent / "job.json", dict(metadata, status="registering"))
    restored = RecordingJobs(jobs.root, jobs.plans_dir, jobs.templates_dir)
    try:
        assert restored.list_jobs()[0]["status"] == ("published" if has_receipt else "registration_failed")
        with pytest.raises(BridgeError):
            restored.register_plan(job["job_id"], draft["draft_id"], draft["revision"], "Different")
        receipt, _ = restored.register_plan(job["job_id"], draft["draft_id"], draft["revision"], metadata["registration_name"])
        assert receipt["saved_and_reloaded"]
        assert len(list(jobs.plans_dir.glob("*.json"))) == 2
    finally:
        restored.close()


@pytest.mark.parametrize("status,busy,expected", [("review", False, "unconfirmed"), ("registering", True, "registering"),
    ("registration_failed", False, "registration_failed"), ("published", True, "finishing"), ("published", False, "complete")])
def test_auto_registration_progress_never_completes_before_durable_registration(status, busy, expected):
    progress = AnalysisProgress().update({"job_id": "job_auto", "auto_register": True, "status": status}, busy)
    assert progress.phase == expected
    assert (progress.percent == 100) is (expected == "complete")


@pytest.mark.parametrize("mutate", ["delete", "change"])
def test_published_list_checks_disk_not_just_old_success_receipt(jobs_setup, mutate):
    jobs, media, _ = jobs_setup
    start(jobs, media)
    final = finish(jobs)
    target = jobs.plans_dir / (final["publication"]["plan_id"] + ".json")
    if mutate == "delete":
        target.unlink()
    else:
        write_json(target, dict(read_json(target), name="Edited elsewhere"))
    assert jobs.list_jobs()[0]["status"] == "error"
    with pytest.raises(BridgeError, match="삭제되었거나 변경"):
        jobs.load_result(final["job_id"])


def test_missing_publication_receipt_is_not_listed_as_completed(jobs_setup):
    jobs, media, _ = jobs_setup
    job = start(jobs, media)
    assert finish(jobs)["status"] == "published"
    service, draft = jobs.load_result(job["job_id"])
    draft["publication"] = None
    write_json(service._path(draft["draft_id"]), draft)
    assert jobs.list_jobs()[0]["status"] == "error"


def test_automatic_publication_is_not_an_ai_tool(jobs_setup):
    jobs, media, _ = jobs_setup
    start(jobs, media)
    assert finish(jobs)["status"] == "published"
    for method in ("register_plan", "approve_publication", "publish_new_plan", "_auto_register"):
        with pytest.raises(BridgeError):
            jobs.scope.dispatch(method, {})
