"""Continuous play and local control; fake device/window only, no desktop input."""

import copy
from types import SimpleNamespace

import pytest

from src.agent_bridge.codex_runner import CodexRunner
from src.agent_bridge.contracts import BridgeError
from src.ai_play.actions import validate_action, input_cost
from src.ai_play.jobs import PlayRunner
from src.ai_play.jobs import PlayJobs
from src.ai_play.windows import WindowIO
from tests.test_gpt_play import frame, released, isolated_admission_registry
from tests.test_gpt_play_learning import learned
from tests.test_gpt_play_learning import routine


def segments():
    return [{"keys": ["up"], "duration_ms": 300},
            {"keys": ["up", "z"], "duration_ms": 200},
            {"keys": ["z"], "duration_ms": 100}]


def test_session_has_no_elapsed_or_action_ceiling_and_can_resume(learned):
    learned.clock = lambda: 100_000_000
    learned.action_count = 50_000
    learned.check(input_allowed=True)
    learned.act(frame(learned), "beyond-old-budget", {"kind": "key", "key": "up"}, "move")
    learned.pause()
    released(learned)
    learned.resume()
    assert learned.snapshot()["remaining_seconds"] is None
    assert learned.snapshot()["max_actions"] is None
    assert learned.action_count == 50_001


def test_analysis_retains_limits_while_play_runner_is_continuous(tmp_path):
    assert CodexRunner({"max_seconds": 60}, tmp_path).execution_limits() == (60, 10000)
    assert PlayRunner({"max_seconds": 60}, tmp_path).execution_limits() == (None, None)
    with pytest.raises(BridgeError, match="30~3600"):
        CodexRunner({"max_seconds": 1}, tmp_path).execution_limits()


def test_normal_model_turns_continue_past_twenty_without_restarting(tmp_path, monkeypatch):
    from src.ai_play import jobs as jobs_module
    from tests.test_gpt_play import Backend
    class Host:
        thread = None
        def __init__(self, *_): pass
        def start(self): return "fake-pipe"
        def close(self): pass
    class Runner(PlayRunner):
        def run(self, *_args, **_kwargs):
            for _ in range(25):
                jobs.session.action_count += 1  # Simulate progress; no device sends.
                assert self.continue_prompt() is not None
            jobs.session.pause("fixture complete")
            return {"exit_code": 0}
    monkeypatch.setattr(jobs_module, "PlayHost", Host)
    jobs = PlayJobs(tmp_path / "jobs", tmp_path / "plans", tmp_path / "templates",
                    lambda: True, runner_factory=Runner)
    backend = Backend()
    try:
        jobs.start(None, "continuous fake test", ["up"], {}, test_environment=True, backend=backend)
        jobs.worker.join(5)
        assert not jobs.busy
        assert jobs.session.action_count == 25
        assert len([r for r in jobs.trace.since(0) if r["event"] == "model_continue"]) == 25
        assert not backend.sent
    finally:
        jobs.stop()
        if jobs.worker:
            jobs.worker.join(5)
        if jobs.session:
            jobs._release_owner(jobs.session)


def test_more_than_ten_attempts_preserve_each_result(learned):
    m = learned.learning
    ident = frame(learned)
    for _ in range(12):
        m.attempt_event("start", ident, "arena", "test strategy")
        m.attempt_event("result", ident, "arena", "test strategy", "incomplete", "synthetic outcome")
    assert m.attempt_count == 12
    assert len(m.store.documents(m.task, "attempt", 100)) == 12


def test_control_one_request_returns_fresh_result_and_duplicate_never_resends(learned):
    start = frame(learned)
    args = dict(observation_id=start, request_id="control-once", segments=segments(), purpose="move and use skill")
    result = learned.dispatch("play_control", args)
    assert learned.backend.sent == [{"kind": "control", "segments": segments()}]
    assert learned.action_count == 3
    assert result["observation"]["frame_id"] != start
    assert result["observation"]["frame_id"] == learned.latest
    assert result["observation"]["png_base64"]
    learned.dispatch("play_control", args)
    assert len(learned.backend.sent) == 1


@pytest.mark.parametrize("value", [[], [{}], [{"keys": ["alt", "f4"], "duration_ms": 100}],
    [{"keys": ["f12"], "duration_ms": 100}], [{"keys": ["up"], "duration_ms": 1001}],
    [{"keys": ["up"], "duration_ms": 1000}] * 3,
    [{"keys": ["up"], "duration_ms": 50}] * 9,
    [{"keys": ["up"], "duration_ms": True}]])
def test_control_still_rejects_unsafe_or_blind_long_input(value):
    with pytest.raises(BridgeError):
        validate_action({"kind": "control", "segments": value})


def test_control_uses_same_hold_controller_and_stops_before_next_segment():
    io = WindowIO.__new__(WindowIO)
    io.used_keys, io.trace = set(), None
    io.validate = lambda region=None: [0, 0, 160, 120]
    sent = []
    cancelled = [False]
    def check():
        if cancelled[0]:
            raise BridgeError("SESSION_STOPPED", "stopped")
    def hold(keys, duration, guard):
        guard()
        sent.append((copy.deepcopy(keys), duration))
        if len(sent) == 2:
            cancelled[0] = True
        return True
    io.controller = SimpleNamespace(play_lab_hold=hold)
    with pytest.raises(BridgeError, match="stopped"):
        io.send({"kind": "control", "segments": segments()}, [0, 0, 160, 120], check)
    assert sent == [(["up"], 300), (["up", "z"], 200)]
    assert input_cost({"kind": "control", "segments": segments()}) == 3


@pytest.mark.parametrize("first_succeeds", [False, True])
def test_missing_firmware_is_not_sent_but_mid_control_reset_is_unknown(first_succeeds):
    io = WindowIO.__new__(WindowIO)
    io.used_keys, io.trace = set(), None
    io.validate = lambda region=None: [0, 0, 160, 120]
    calls = []
    def hold(keys, duration, guard):
        guard()
        calls.append(keys)
        if first_succeeds and len(calls) == 1:
            return True
        raise BridgeError("TIMED_INPUT_UNSUPPORTED", "GL1 unavailable")
    io.controller = SimpleNamespace(play_lab_hold=hold)
    with pytest.raises(BridgeError) as result:
        io.send({"kind": "control", "segments": segments()}, [0, 0, 160, 120], lambda: None)
    assert result.value.code == ("DELIVERY_UNKNOWN" if first_succeeds else "TIMED_INPUT_UNSUPPORTED")
    assert len(calls) == (2 if first_succeeds else 1)


def test_archived_input_id_still_deduplicates_after_cache_eviction(learned):
    first = frame(learned)
    args = (first, "original", {"kind": "key", "key": "up"}, "test")
    receipt = learned.act(*args)
    for i in range(140):
        learned.receipts[str(i)] = {"state": "sent_not_game_success", "digest": str(i)}
    learned._persist()
    assert len(learned.receipts) == 128
    assert "original" not in learned.receipts
    assert learned.act(*args) == receipt
    assert len(learned.backend.sent) == 1


def test_archived_frame_remains_readable_and_600_does_not_stop_learning(learned):
    first = frame(learned)
    learned.frame_count = 600
    for i in range(140):
        learned.frames["cache-" + str(i)] = copy.deepcopy(learned.frames[first])
    result = learned.observe()
    assert len(learned.frames) == 128
    assert learned.frame_count == 601
    assert learned.read_frame(first)["historical_not_current"]
    with pytest.raises(BridgeError, match="관찰"):
        learned._frame(first, fresh=True)
    assert learned._frame(result["frame_id"], fresh=True)


def test_saved_control_program_preserves_all_160_validated_input_segments(learned, tmp_path):
    from src.ai_play.saved_runtime import run_saved
    s = learned
    entry = routine(s)
    program = copy.deepcopy(s.learning.store.get(s.learning.task, "behavior", entry["behavior_id"])["program"])
    step = program["steps"][0]
    step["action"] = {"kind": "control", "segments": [{"keys": ["up"], "duration_ms": 50}] * 8}
    program["steps"] = []
    for i in range(20):
        current = copy.deepcopy(step)
        if i % 2:
            current["before"], current["after"] = current["after"], current["before"]
        program["steps"].append(current)
    original_send = s.backend.send
    def toggle(action, region, check):
        color = s.backend.color
        result = original_send(action, region, check)
        s.backend.color = 2 if color == 1 else 1
        return result
    s.backend.send = toggle
    s.backend.color = 1
    before = len(s.backend.sent)
    result = run_saved(program, lambda: None, backend=s.backend, receipt_root=tmp_path / "receipts")
    assert result["status"] == "conditions_matched"
    assert len(s.backend.sent) - before == 20
    assert sum(input_cost(action) for action in s.backend.sent[before:]) == 160
