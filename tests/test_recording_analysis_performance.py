"""Bounded offline media and scripted repair tests, not proof of GPT comprehension."""

import base64
import copy
import os
from unittest.mock import Mock

import cv2
import numpy as np
import pytest

from src.agent_bridge.contracts import BridgeError, digest, encoded
from src.agent_bridge.media import MediaTools
from src.agent_bridge.recording_jobs import RecordingJobs
from src.agent_bridge.service import DraftService, EDITABLE
from src.agent_bridge.storage import read_json
from tests.test_ai_draft_bridge import service, add, review
from tests.test_recording_jobs import setup as jobs_setup, finish, FakeHost, ReviewingActor


@pytest.fixture
def clip(service, tmp_path):
    path = tmp_path / "ten-seconds.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 3, (1280, 720))
    assert writer.isOpened()
    try:
        for index in range(30):
            frame = np.full((720, 1280, 3), index * 8, np.uint8)
            cv2.putText(frame, str(index), (500, 350), cv2.FONT_HERSHEY_SIMPLEX, 3, (0, 0, 255), 3)
            writer.write(frame)
    finally:
        writer.release()
    media = MediaTools(service)
    key = media.register(path)["recording_id"]
    return media, key, path


def test_twelve_overview_frames_use_one_decoder_and_one_integrity_hash(clip, monkeypatch):
    media, key, _ = clip
    real = cv2.VideoCapture
    opened = []
    def capture(path):
        wrapper = Mock(wraps=real(path))
        opened.append(wrapper)
        return wrapper
    monkeypatch.setattr(cv2, "VideoCapture", capture)
    hashed = Mock(wraps=media._hash)
    monkeypatch.setattr(media, "_hash", hashed)
    overview = media.get_frames(key, [i * .75 for i in range(12)], overview=True)
    assert len(opened) == 1 and hashed.call_count == 1
    opened[0].release.assert_called_once()
    assert not opened[0].isOpened()
    assert len(overview["frames"]) == len(media.service.frame_traces) == 12
    for frame in overview["frames"]:
        image = cv2.imdecode(np.frombuffer(base64.b64decode(frame["base64"]), np.uint8), cv2.IMREAD_COLOR)
        assert image.shape[:2] == (360, 640)
        assert frame["original_width"] == 1280 and frame["preview_scale"] == .5
        assert abs(frame["actual_seconds"] - frame["requested_seconds"]) < .34
    assert len(encoded(overview)) < 4_000_000 - 1024
    detail = media.get_frames(key, [0, 1, 2, 3])
    assert len(opened) == 2
    assert all(frame["preview_scale"] == 1 for frame in detail["frames"])
    assert not set(f["frame_trace_id"] for f in detail["frames"]) & set(f["frame_trace_id"] for f in overview["frames"])


@pytest.mark.parametrize("times,overview,code", [([], True, "LIMIT_REACHED"), ([0] * 13, True, "LIMIT_REACHED"),
    ([0] * 5, False, "LIMIT_REACHED"), ([0], 1, "INVALID_FIELD"), ([0, 10], True, "INVALID_MEDIA"),
    ([0, float("nan")], True, "INVALID_FIELD")])
def test_batch_limits_and_failed_batches_do_not_create_evidence(clip, times, overview, code):
    media, key, _ = clip
    with pytest.raises(BridgeError) as error:
        media.get_frames(key, times, overview=overview)
    assert error.value.code == code
    assert not media.service.frame_traces


def test_private_frame_failure_closes_batch_decoder_and_keeps_no_traces(clip, monkeypatch):
    media, key, _ = clip
    media.service.selected_media[key]["excluded"] = [[1, 2]]
    real = cv2.VideoCapture
    captures = []
    def capture(path):
        cap = real(path)
        captures.append(cap)
        return cap
    monkeypatch.setattr(cv2, "VideoCapture", capture)
    with pytest.raises(BridgeError, match="excluded"):
        media.get_frames(key, [0, 1.5], overview=True)
    assert len(captures) == 1 and not captures[0].isOpened()
    assert not media.service.frame_traces


def test_overview_never_reuses_changed_file_even_with_same_stat(clip):
    media, key, path = clip
    media.get_frames(key, [0], overview=True)
    before = path.stat()
    with path.open("r+b") as stream:
        stream.seek(-1, 2)
        old = stream.read(1)
        stream.seek(-1, 2)
        stream.write(bytes([old[0] ^ 1]))
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    with pytest.raises(BridgeError) as error:
        media.get_frames(key, [0], overview=True)
    assert error.value.code == "MEDIA_NOT_READY"


def test_transport_budget_rejects_batch_before_issuing_evidence(clip, monkeypatch):
    import src.agent_bridge.media as module
    media, key, _ = clip
    monkeypatch.setattr(module, "MAX_MESSAGE", 1100)
    with pytest.raises(BridgeError) as error:
        media.get_frames(key, [0], overview=True)
    assert error.value.code == "MESSAGE_TOO_LARGE"
    assert not media.service.frame_traces


def test_type_pacing_and_compact_actions_survive_publish_and_reload(service):
    draft = add(service, service.open_draft("Typing task"), "type", action_text="안녕하세요",
                typing_delay=.23, typing_random=True, typing_delay_range=.04, wait_after=.9)
    rows = service.read_actions(draft_id=draft["draft_id"])["rows"]
    rule = draft["plan"]["initial_rules"][0]
    assert all(rows[0][key] == rule[key] for key in EDITABLE if key in rule)
    assert len(encoded(rows[0])) < len(encoded(rule)) / 2
    draft = review(service, draft)
    service.save_draft(draft["draft_id"], draft["revision"])
    restored = DraftService(service.plans_dir, service.templates_dir, service.workspace)
    assert restored.read_draft(draft["draft_id"]) == draft
    restored.approve_publication(draft["draft_id"], draft["revision"], name="Typing task")
    receipt = restored.publish_new_plan(draft["draft_id"], draft["revision"], "Typing task")
    saved = read_json(service.plans_dir / (receipt["plan_id"] + ".json"))
    actual = saved["initial_rules"][0]
    for field in ("action_text", "typing_delay", "typing_random", "typing_delay_range", "wait_after"):
        assert actual[field] == rule[field]


@pytest.mark.parametrize("field,value", [("typing_delay", -1), ("typing_delay", float("inf")),
    ("typing_delay_range", "fast"), ("typing_delay_range", True), ("typing_random", 1)])
def test_invalid_pacing_is_atomic(service, field, value):
    draft = service.open_draft("No unsafe pacing")
    with pytest.raises(BridgeError):
        add(service, draft, "type", action_text="x", **{field: value})
    assert service.read_draft(draft["draft_id"]) == draft


def test_rephrased_issues_and_new_traces_are_not_fake_repair_progress(service):
    draft = review(service, add(service, service.open_draft("Review"), "hotkey", action_keys=["enter"]))
    changed = copy.deepcopy(draft)
    for item in changed["crosschecks"]:
        item["passed"] = False
        item["remaining_issues"] = ["One genuine unresolved task step"]
        item["checks"][0]["result"] = "uncertain"
    old = RecordingJobs._review_progress(changed)
    changed["revision"] += 5
    for item in changed["crosschecks"]:
        item["remaining_issues"] = ["Reworded unresolved step"]
        item["checks"][0].update(note="A different explanation", frame_trace_id="fresh-trace")
    assert RecordingJobs._review_progress(changed) == old
    changed["crosschecks"][-1]["checks"][0]["result"] = "match"
    assert RecordingJobs._review_progress(changed) != old


def test_actual_edit_progress_can_use_third_pass_and_metrics_are_durable(jobs_setup):
    jobs, media, original = jobs_setup
    before = original.read_bytes()
    class Actor(ReviewingActor):
        calls = 0
        def run(self, descriptor, prompt, cancelled, progress):
            type(self).calls += 1
            scope = FakeHost.scopes[descriptor]
            if self.calls == 1:
                super().run(descriptor, prompt, cancelled, progress)
            state = scope.service.read_draft(scope.draft_id)
            if self.calls < 3:
                scope.dispatch("stage_edits", {"draft_id": scope.draft_id, "expected_revision": state["revision"],
                    "request_id": f"change-{self.calls}", "operations": [{"op": "update_rule",
                        "rule_id": state["plan"]["initial_rules"][0]["rule_id"], "fields": {"wait_after": self.calls}}]})
            else:
                review(scope.service, state, next(iter(scope.service.selected_media)))
            return {"exit_code": 0, "message": "Simulation only"}
    jobs.runner_factory = Actor
    jobs.start(media, "Analyze", {})
    result = finish(jobs)
    assert result["status"] == "review" and result["review_pass"] == 3
    assert not result["review_stalled"]
    metrics = result["performance"]
    assert len(metrics["passes"]) == 3 and all(p["seconds"] >= 0 for p in metrics["passes"])
    assert metrics["tools"]["stage_edits"]["calls"] == 3
    assert read_json(jobs.root / result["job_id"] / "job.json")["performance"] == metrics
    assert original.read_bytes() == before


def test_prompt_requires_semantic_task_boundary_detail_review_and_no_false_success(jobs_setup):
    jobs, media, _ = jobs_setup
    jobs.start(media, "Reproduce visible work", {})
    finish(jobs)
    prompt = jobs._prompt("recording", "draft")
    for phrase in ("overview=true", "Transient IME", "unless demonstrating them is the goal",
                   "never silently omit a real task action", "full-detail frames", "typing_delay",
                   "actual unsupported task steps", "not remaining_issues", "both reviews", "Do not claim recognition"):
        assert phrase in prompt
