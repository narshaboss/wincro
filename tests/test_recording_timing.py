"""Measured-video timing contracts and device-isolated playback simulations."""

import copy
import threading
from types import SimpleNamespace

import pytest

from src.agent_bridge.contracts import BridgeError
from src.agent_bridge.media import MediaTools
from src.agent_bridge.recording_jobs import JobScope, RecordingJobs
from src.agent_bridge.service import DraftService
from src.agent_bridge.storage import read_json
from src.analyzer.automation_models import AutomationPlan
from src.player import rule_executor
from src.ui.ai_draft_guidance import readable_action, readable_analysis
from src.utils import typing_timeline
from tests.test_ai_draft_bridge import service, add
from tests.test_recording_analysis_performance import clip


class Clock:
    def __init__(self):
        self.now = 0.0
        self.waits = []
    def __call__(self):
        return self.now
    def wait(self, delay):
        self.waits.append(delay)
        self.now += delay
        return True


def test_irregular_typing_uses_observed_intervals_not_bulk_paste_or_extra_tail():
    clock, events = Clock(), []
    def send(char):
        events.append((char, clock()))
        clock.now += .04  # Device transmission is included, not added to every gap.
        return True
    offsets = [0, .3, .95, 1.7, 2.4]
    assert typing_timeline.replay_typing_timeline("안녕하세요", offsets, send, clock.wait, clock)
    assert [x[0] for x in events] == list("안녕하세요")
    assert [x[1] for x in events] == pytest.approx(offsets)
    assert clock.now == pytest.approx(2.44)
    assert len(clock.waits) == 4


def test_pause_reanchors_instead_of_compressing_remaining_characters():
    clock, events = Clock(), []
    def wait(delay):
        clock.wait(delay)
        if len(clock.waits) == 1:
            clock.now += 20
        return True
    assert typing_timeline.replay_typing_timeline("abc", [0, .3, .8],
        lambda char: events.append((char, clock())) or True, wait, clock)
    assert [t for _, t in events] == pytest.approx([0, 20.3, 20.8])


def test_pause_inside_device_send_does_not_merge_next_character():
    clock, events = Clock(), []
    def send(char):
        if not events:
            clock.now += 20
        events.append((char, clock()))
        return True
    assert typing_timeline.replay_typing_timeline("abc", [0, .3, .8], send, clock.wait, clock)
    assert [t for _, t in events] == pytest.approx([20, 20.3, 20.8])


@pytest.mark.parametrize("offsets", [[1], [0, -.1], [0, .5, .3], [0, True], [0, float('nan')], [0, float('inf')], None, "0,1"])
def test_invalid_timeline_sends_nothing(offsets):
    sent = []
    with pytest.raises(ValueError):
        typing_timeline.replay_typing_timeline("ab", offsets, sent.append, lambda _: True)
    assert not sent


def test_cancelled_wait_or_failed_send_cannot_continue():
    events = []
    assert not typing_timeline.replay_typing_timeline("abc", [0, .2, .7],
        lambda char: events.append(char) or True, lambda _: False)
    assert events == ["a"]
    events.clear()
    assert not typing_timeline.replay_typing_timeline("abc", [0, .2, .7],
        lambda char: (events.append(char), False)[1], lambda _: True)
    assert events == ["a"]


@pytest.mark.parametrize("text", ["abc", "안녕하세요"])
def test_executor_timed_type_both_entry_points_and_clipboard_restore(monkeypatch, text):
    clock = Clock()
    clipboard = {"value": "original"}
    events = []
    ctrl = SimpleNamespace(type_text=lambda char: events.append((char, clock())) or True,
        hotkey=lambda *keys: events.append((clipboard["value"], clock())) or True)
    monkeypatch.setattr(rule_executor, "get_input_controller", lambda: ctrl)
    monkeypatch.setattr(rule_executor.pyperclip, "paste", lambda: clipboard["value"])
    monkeypatch.setattr(rule_executor.pyperclip, "copy", lambda value: clipboard.update(value=value))
    monkeypatch.setattr(typing_timeline, "time", SimpleNamespace(monotonic=clock))
    executor = object.__new__(rule_executor.RuleExecutor)
    executor._wait_for_resume = lambda: False
    executor._auto_list_wait = clock.wait
    executor._stop_event = threading.Event()
    offsets = [i * .4 for i in range(len(text))]
    rule = SimpleNamespace(action_text=text, typing_random=False, typing_delay=.001,
        typing_delay_range=100, typing_timestamps=offsets)
    assert executor._type_rule_text(rule)
    assert [char for char, _ in events] == list(text)
    prep = .02 if not text.isascii() else 0
    assert [t for _, t in events] == pytest.approx([t + prep for t in offsets])
    assert clipboard["value"] == "original"
    # TYPE_TEXT and FIXED_SEQUENCE 'type' both route here; no monitor/special-mode changes.
    import inspect
    assert "self._type_rule_text(rule)" in inspect.getsource(rule_executor.RuleExecutor._execute_rule)
    assert "self._type_rule_text(rule)" in inspect.getsource(rule_executor.RuleExecutor._execute_fixed_action)


def test_legacy_type_does_not_opt_in_to_timeline(monkeypatch):
    executor = object.__new__(rule_executor.RuleExecutor)
    calls = []
    executor._type_text_with_clipboard = lambda *args, **kw: calls.append((args, kw)) or True
    assert executor._type_rule_text(SimpleNamespace(action_text="안녕하세요", typing_random=False))
    assert calls == [(("안녕하세요", False, .1, .05), {})]


def measured_draft(clip):
    media, key, _ = clip
    service = media.service
    media.read_recording(key)
    draft = add(service, service.open_draft("Measured text"), "type", action_text="안녕하세요",
        typing_timestamps=[0, 1/3, 1, 5/3, 2], wait_after=.5, wait_random=False)
    frames = media.get_frames(key, [1, 3])["frames"]
    rule = draft["plan"]["initial_rules"][0]
    step = dict(rule_id=rule["rule_id"], recording_id=key, seconds=3,
        frame_trace_id=frames[1]["frame_trace_id"], observation="Synthetic growing text", purpose="Type slowly",
        expected_result="Greeting remains", timing=dict(start_seconds=1, end_seconds=3, next_start_seconds=3.5,
            precision_seconds=1/3, start_frame_trace_id=frames[0]["frame_trace_id"],
            end_frame_trace_id=frames[1]["frame_trace_id"], text_mode="typed"))
    return service, draft, step


def test_timing_wait_math_and_type_schedule_are_durable(clip):
    service, draft, step = measured_draft(clip)
    key = draft["draft_id"]
    result = service.record_analysis(key, draft["revision"], "Measured greeting", [step])
    state = service.read_draft(key)
    assert state["analysis"]["timing_measured"]
    for direction in ("video_to_plan", "plan_to_video"):
        service.record_crosscheck(key, state["revision"], direction, [dict(rule_id=step["rule_id"],
            frame_trace_id=step["frame_trace_id"], result="match", note="Synthetic timing check")])
        state = service.read_draft(key)
    service.save_draft(key, state["revision"])
    reloaded = DraftService(service.plans_dir, service.templates_dir, service.workspace).read_draft(key)
    assert reloaded == state
    service.approve_publication(key, state["revision"], name="Measured greeting")
    receipt = service.publish_new_plan(key, state["revision"], "Measured greeting")
    saved = read_json(service.plans_dir / (receipt["plan_id"] + ".json"))
    restored = AutomationPlan.from_dict(saved, service.templates_dir)
    assert restored.initial_rules[0].typing_timestamps == [0, 1/3, 1, 5/3, 2]
    assert restored.initial_rules[0].wait_after == .5
    assert "시점별 입력" in readable_action(saved["initial_rules"][0])
    assert "사이 대기 0.500초" in readable_analysis(state)


@pytest.mark.parametrize("change", ["gap", "precision", "reversed", "wrong_frame", "paste", "no_schedule", "random"])
def test_inconsistent_measurements_cannot_be_recorded_as_success(clip, change):
    service, draft, step = measured_draft(clip)
    key = draft["draft_id"]
    if change == "gap": step["timing"]["next_start_seconds"] = 4
    if change == "precision": step["timing"]["precision_seconds"] = .0001
    if change == "reversed": step["timing"]["end_seconds"] = .5
    if change == "wrong_frame": step["timing"]["start_frame_trace_id"] = step["frame_trace_id"]
    if change == "paste": step["timing"]["text_mode"] = "paste"
    if change in {"no_schedule", "random"}:
        fields = {"typing_timestamps": []} if change == "no_schedule" else {"wait_random": True}
        service.stage_edits(draft["draft_id"], draft["revision"], "break", [
            {"op": "update_rule", "rule_id": step["rule_id"], "fields": fields}])
    before = service.read_draft(key)
    with pytest.raises(BridgeError):
        service.record_analysis(before["draft_id"], before["revision"], "Do not fake timing", [step])
    assert service.read_draft(before["draft_id"]) == before


def test_required_recording_scope_rejects_prose_only_timing(clip):
    service, draft, step = measured_draft(clip)
    scope = JobScope(service, draft["draft_id"], preserve_timing=True)
    prose_only = copy.deepcopy(step)
    prose_only.pop("timing")
    with pytest.raises(BridgeError) as error:
        scope.dispatch("record_analysis", dict(draft_id=draft["draft_id"], expected_revision=draft["revision"],
            goal="Natural typing", steps=[prose_only]))
    assert error.value.code == "TIMING_REQUIRED"
    scope.dispatch("record_analysis", dict(draft_id=draft["draft_id"], expected_revision=draft["revision"],
        goal="Measured typing", steps=[step]))
    assert service.read_draft(draft["draft_id"])["analysis"]["timing_measured"]
