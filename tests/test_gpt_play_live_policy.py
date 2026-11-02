"""Real policy/reader/executors, synthetic screens and device only. No desktop I/O."""

import base64
import copy
import hashlib
import io
import threading
import time

import pytest
from PIL import Image, ImageDraw

from src.agent_bridge.contracts import BridgeError
from src.ai_play import behaviors, candidate
from src.ai_play.experience import ExperienceStore
from src.ai_play.jobs import PlayScope
from src.ai_play.navigation import CoordinateReader, execute_navigation, validate_navigation
from src.ai_play.observation_stream import ObservationStream
from src.ai_play.session import PlaySession
from tests.test_gpt_play import frame, released, isolated_admission_registry
from tests.test_gpt_play_learning import Game, routine, learned


@pytest.fixture
def live(tmp_path):
    store = ExperienceStore(tmp_path / "memory")
    task = store.create("enter and clear", {"executable": "game.exe", "title": "Game"})
    s = PlaySession(tmp_path / "session", "enter and clear", ["up", "down", "left", "right", "z"],
                    Game(), lambda: True, memory=store, task_id=task, live_policy=True)
    s.start()
    from src.ai_play.playback import CandidatePlayback
    s.playback = CandidatePlayback(s)
    yield s
    s.stop()
    released(s)


def review(s, ident=None, stage="start"):
    context = s.dispatch("play_memory", {"stage": stage})
    ident = ident or frame(s)
    s.dispatch("play_use_experience", {"observation_id": ident, "retrieval_id": context["retrieval_id"],
        "references": [r["reference"] for r in context["records"]], "decision": "Apply observed success; avoid recorded failure."})
    return ident


def test_input_requires_actual_retrieval_and_durable_review(live):
    ident = frame(live)
    with pytest.raises(BridgeError) as error:
        live.act(ident, "first", {"kind": "key", "key": "up"}, "test")
    assert error.value.code == "MEMORY_REVIEW_REQUIRED" and not live.backend.sent
    review(live, ident)
    live.act(ident, "first", {"kind": "key", "key": "up"}, "test")
    saved = ExperienceStore(live.learning.store.root).get(live.learning.task, "session", live.instance_id)
    assert saved["live"]["experience_review"]["decision"]
    assert len(live.backend.sent) == 1


def test_wrong_references_and_ignoring_available_experience_are_rejected(live):
    ident = frame(live)
    live.learning.record(ident, "fact", "NPC exists", stage="NPC")
    context = live.live.recall("NPC")
    for references in ([], ["event:999999"]):
        with pytest.raises(BridgeError):
            live.live.use(ident, context["retrieval_id"], references, "ignore")
    assert not live.backend.sent


def test_old_stage_experience_not_hidden_by_recent_unrelated_records(live):
    store, task = live.learning.store, live.learning.task
    store.event(task, "fact", {"stage": "NPC", "observation": "old useful fact"})
    for _ in range(45):
        store.event(task, "fact", {"stage": "fight", "observation": "new unrelated"})
    result = live.live.recall("NPC")
    assert result["records"][0]["record"]["observation"] == "old useful fact"
    assert result["has_more"]


def test_resume_retry_and_stage_change_require_new_review(live):
    ident = review(live)
    live.learning.attempt_event("start", ident, "NPC", "join")
    live.learning.attempt_event("result", ident, "NPC", "join", "incomplete", "no outcome")
    with pytest.raises(BridgeError, match="play_memory"):
        live.check(input_allowed=True)
    review(live)
    live.live.recall("fight")
    with pytest.raises(BridgeError):
        live.check(input_allowed=True)
    review(live)
    live.pause()
    released(live)
    live.resume()
    with pytest.raises(BridgeError):
        live.check(input_allowed=True)


def test_ephemeral_observation_is_bounded_and_only_references_are_durable(live):
    first = frame(live)
    for _ in range(20):
        live.backend.color += 1
        current = frame(live)
    assert len(live.ephemeral_frames) == 6
    assert live.learning.store.documents(live.learning.task, "frame", 100) == []
    with pytest.raises(BridgeError):
        live.read_frame(first)
    live.note(current, "observed", "test", "expected")
    saved = ExperienceStore(live.learning.store.root)
    assert saved.read_frame(live.learning.task, current)[1]
    assert len(saved.documents(live.learning.task, "frame", 100)) == 1


def test_new_run_restores_evidence_not_review_or_input_authority(live, tmp_path):
    ident = review(live)
    live.learning.record(ident, "fact", "door opened")
    other = PlaySession(tmp_path / "restart", live.goal, live.keys, Game(), lambda: True,
        memory=ExperienceStore(live.learning.store.root), task_id=live.learning.task, live_policy=True)
    try:
        other.start()
        assert other.read_frame(ident)["historical_not_current"]
        assert other.live.review is None
        with pytest.raises(BridgeError):
            other.check(input_allowed=True)
        assert other.live.recall()["records"]
    finally:
        other.stop()
        released(other)


def test_goal_freezes_input_but_allows_new_draft_and_disk_reload(live):
    review(live)
    record = routine(live)
    final = frame(live)
    live.dispatch("play_goal_achieved", {"observation_id": final, "observation": "reward visibly received"})
    released(live)
    with pytest.raises(BridgeError) as error:
        live.act(final, "extra", {"kind": "key", "key": "up"}, "wrong reentry")
    assert error.value.code == "GOAL_INPUT_FROZEN"
    live.backend.focus = False
    result = candidate.build(live, "draft", [{"kind": "behavior", "behavior_id": record["behavior_id"],
        "frame_id": final, "purpose": "enter", "expected_result": "arena"}], [])
    assert result["revision"] == 1
    from src.agent_bridge.storage import read_json
    assert read_json(live.folder / "candidate.json")["plan"] == live.candidate["plan"]
    assert not candidate.ready(live)
    live.backend.focus = True


def test_goal_and_memory_save_failure_never_allow_input(live, monkeypatch):
    ident = review(live)
    def fail(*_args):
        raise OSError("disk full")
    monkeypatch.setattr(live.learning.store, "event", fail)
    with pytest.raises(OSError):
        live.dispatch("play_goal_achieved", {"observation_id": ident, "observation": "reward"})
    assert live.storage_failed and live.live.goal
    with pytest.raises(BridgeError):
        live.check(input_allowed=True)
    assert not live.backend.sent


def test_replay_permit_cannot_be_granted_by_model(live):
    with pytest.raises(BridgeError):
        live.dispatch("authorize_verification", {})
    with pytest.raises(BridgeError):
        live.live.consume_verification()
    live.live.goal, live.candidate = {"observation": "reward"}, {"revision": 1}
    # Use the policy directly to test one-shot semantics, not candidate compilation.
    live._persist = lambda: None
    live.live.authorize_verification()
    live.live.consume_verification()
    with pytest.raises(BridgeError):
        live.live.consume_verification()
    live.candidate = None


def test_tool_timings_do_not_mislabel_model_only_latency(live):
    scope = PlayScope(live)
    scope.dispatch("play_observe", {})
    scope.dispatch("play_status", {})
    timings = live.live.snapshot()["timings_ms"]
    assert timings["capture"]["samples"] == 1
    assert "between_tools_model_transport" in timings


def test_slow_write_ahead_never_sends_from_expired_screen(live, monkeypatch):
    now = [100.0]
    live.clock = live.stream.clock = lambda: now[0]
    ident = review(live)
    original = live._persist
    def slow_save():
        original()
        now[0] += 11
    monkeypatch.setattr(live, "_persist", slow_save)
    with pytest.raises(BridgeError) as error:
        live.act(ident, "stale", {"kind": "key", "key": "up"}, "must not send")
    assert error.value.code == "STALE_OBSERVATION"
    assert live.receipts["stale"]["state"] == "not_sent"
    assert not live.backend.sent and not live.learning.pending


def test_real_execution_failure_still_quarantines_routine(learned):
    record = routine(learned)
    learned.backend.color = 1
    learned.backend.send = lambda _a, _r, check: check() or True
    result = behaviors.run_behavior(learned, record["behavior_id"], frame(learned), "stuck-result")
    assert result["reason"] == "result_condition"
    saved = learned.learning.store.get(learned.learning.task, "behavior", record["behavior_id"])
    assert saved["status"] == "revalidate" and saved["failures"] == 1


@pytest.mark.parametrize("complete_history", [True, False])
def test_old_entry_only_quarantine_requires_complete_history(learned, complete_history):
    record = routine(learned)
    m = learned.learning
    saved = m.store.get(m.task, "behavior", record["behavior_id"])
    saved.update(status="revalidate", failures=1)
    m.store.put(m.task, "behavior", record["behavior_id"], saved)
    if complete_history:
        m.store.event(m.task, "behavior_run", {"behavior_id": record["behavior_id"],
            "status": "mismatch", "step": 1, "reason": "start_condition"})
    learned.backend.color = 1
    if complete_history:
        result = behaviors.run_behavior(learned, record["behavior_id"], frame(learned), "recovered")
        assert result["status"] == "conditions_matched"
    else:
        with pytest.raises(BridgeError):
            behaviors.run_behavior(learned, record["behavior_id"], frame(learned), "blocked")


def test_goal_finish_waits_without_publishing_or_reentering(live):
    review(live)
    record = routine(live)
    final = frame(live)
    live.live.achieved(final, "reward visible")
    released(live)
    candidate.build(live, "draft", [{"kind": "behavior", "behavior_id": record["behavior_id"],
        "frame_id": final, "purpose": "enter", "expected_result": "arena"}], [])
    count = len(live.backend.sent)
    result = PlayScope(live).finish("Draft saved; replay not authorized", ["independent replay not performed"],
        {key: {"status": "unknown", "note": "not observed"}
         for key in ["failure_or_death", "exit", "revive", "return_to_npc"]})
    released(live)
    assert result["state"] == "paused" and live.publication is None
    assert live.live.goal and len(live.backend.sent) == count
    live.resume()
    with pytest.raises(BridgeError) as error:
        live.check(input_allowed=True)
    assert error.value.code == "GOAL_INPUT_FROZEN"


def test_stream_ring_bound_resize_pause_and_shutdown():
    region = [0, 0, 80, 60]
    active = [True]
    stream = ObservationStream(lambda: (Image.new("RGB", (80, 60)), list(region)), lambda: active[0], interval=.01)
    stream.start()
    try:
        time.sleep(.15)
        assert 1 <= len(stream.recent(100)) <= 6
        region[0] = 1
        stream.sample()
        assert all(s["region"][0] == 1 for s in stream.recent())
        active[0] = False
        time.sleep(.05)
        assert not stream.recent()
    finally:
        assert stream.close()
    assert not stream.worker.is_alive()


PATTERNS = [
    ["111", "101", "101", "101", "111"], ["010", "110", "010", "010", "111"],
    ["111", "001", "111", "100", "111"], ["111", "001", "111", "001", "111"],
    ["101", "101", "111", "001", "001"], ["111", "100", "111", "001", "111"],
    ["111", "100", "111", "101", "111"], ["111", "001", "010", "010", "010"],
    ["111", "101", "111", "101", "111"], ["111", "101", "111", "001", "111"]]


def digit_image(digit):
    image = Image.new("RGB", (6, 10))
    draw = ImageDraw.Draw(image)
    for y, row in enumerate(PATTERNS[digit]):
        for x, value in enumerate(row):
            if value == "1":
                draw.rectangle((x*2, y*2, x*2+1, y*2+1), fill="white")
    return image


def packed_image(image):
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    raw = stream.getvalue()
    return {"png": base64.b64encode(raw).decode(), "sha256": hashlib.sha256(raw).hexdigest()}


def profile():
    return {"version": 1, "size": [160, 120], "x_region": [1, 30, 35, 40], "y_region": [1, 45, 35, 55],
        "light": True, "level": 128, "digits": [{"digit": n, **packed_image(digit_image(n))} for n in range(10)]}


class GridGame(Game):
    def __init__(self):
        super().__init__()
        self.xy = [12, 7]
        self.blocked = False
        self.jump = False
        self.scene = False

    def capture(self):
        image, region = super().capture()
        for number, y in zip(self.xy, [30, 45]):
            for i, digit in enumerate(str(number)):
                image.paste(digit_image(int(digit)), (1+i*8, y))
        return image, region

    def send(self, action, region, check):
        check()
        if action["kind"] == "navigate":
            return execute_navigation(action, self, region, check)
        self.sent.append(copy.deepcopy(action))
        if self.jump:
            self.xy[0] += 30
        elif not self.blocked:
            delta = {"up": (0,-1), "down": (0,1), "left": (-1,0), "right": (1,0)}[action["keys"][0]]
            self.xy = [a+b for a, b in zip(self.xy, delta)]
        if self.scene:
            self.color = 2
        return True


def navigation(game, target=None):
    image, _ = game.capture()
    return {"kind": "navigate", "profile": profile(), "target": target or [15, 9],
        "axis_order": "xy", "hold_ms": 300, "max_ms": 4000,
        "scene_guard": {"region": [0, 0, 28, 28], "threshold": .98, "frame_id": "test",
                        **packed_image(image.crop([0, 0, 28, 28]))}}


@pytest.mark.parametrize("xy", [[0,0], [12,7], [98,65], [1234,5678]])
def test_actual_screen_digit_reader(xy):
    game = GridGame()
    game.xy = xy
    assert CoordinateReader(profile()).read(game.capture()[0])["xy"] == xy


def test_missing_or_ambiguous_digit_and_size_change_fail_closed():
    game, p = GridGame(), profile()
    p["digits"] = [d for d in p["digits"] if d["digit"] != 7]
    with pytest.raises(BridgeError):
        CoordinateReader(p).read(game.capture()[0])
    p = profile()
    p["digits"][0] = {**p["digits"][1], "digit": 0}
    with pytest.raises(BridgeError):
        CoordinateReader(p).read(game.capture()[0])
    with pytest.raises(BridgeError):
        CoordinateReader(profile()).read(Image.new("RGB", (150, 120)))


def test_feedback_navigates_multiple_tiles_without_model_roundtrips():
    game = GridGame()
    result = execute_navigation(navigation(game), game, game.region, lambda: None)
    assert result["reason"] == "arrived" and game.xy == [15,9]
    assert [a["keys"][0] for a in game.sent] == ["right"]*3 + ["down"]*2
    assert all(a["kind"] == "hold" for a in game.sent)
    assert result["game_success"] is False


@pytest.mark.parametrize("mode,reason,count", [("blocked", "blocked", 2), ("jump", "coordinate_jump", 1), ("scene", "scene_changed", 1)])
def test_feedback_returns_to_gpt_without_learning_walls(mode, reason, count):
    game = GridGame()
    action = navigation(game)
    setattr(game, mode, True)
    result = execute_navigation(action, game, game.region, lambda: None)
    assert result["reason"] == reason and len(game.sent) == count
    assert not hasattr(game, "walls")


def test_cancel_between_holds_never_sends_next_input():
    game = GridGame()
    def check():
        if game.sent:
            raise BridgeError("STOPPED", "stop")
    with pytest.raises(BridgeError):
        execute_navigation(navigation(game), game, game.region, check)
    assert len(game.sent) == 1


@pytest.mark.parametrize("partial", [False, True])
def test_navigation_firmware_loss_classification(partial):
    game = GridGame()
    original = game.send
    def send(action, region, check):
        if partial and not game.sent:
            return original(action, region, check)
        raise BridgeError("TIMED_INPUT_UNSUPPORTED", "unsupported")
    game.send = send
    with pytest.raises(BridgeError) as error:
        execute_navigation(navigation(game), game, game.region, lambda: None)
    assert error.value.code == ("DELIVERY_UNKNOWN" if partial else "TIMED_INPUT_UNSUPPORTED")


def test_navigation_profile_roundtrip_and_exact_saved_runtime(live, tmp_path):
    from src.ai_play.saved_runtime import run_saved
    from src.analyzer.automation_models import AutomationPlan
    live.backend = GridGame()
    live.stream.capture = live.backend.capture
    before = review(live)
    p = profile()
    live.learning.store.put(live.learning.task, "coordinate_profile", "active", p)
    assert ExperienceStore(live.learning.store.root).get(live.learning.task, "coordinate_profile", "active") == p
    args = {"observation_id": before, "request_id": "nav", "target": [15,9], "axis_order": "xy",
            "hold_ms": 300, "max_ms": 4000, "scene": {"frame_id": before, "region": [0,0,28,28], "threshold": .98}, "purpose": "reach exit"}
    result = live.dispatch("play_navigate", args)
    assert result["receipt"]["feedback"]["reason"] == "arrived"
    count = len(live.backend.sent)
    live.dispatch("play_navigate", args)
    assert len(live.backend.sent) == count
    after = result["observation"]["frame_id"]
    record = behaviors.author(live, "reach exit", [{"before": {"frame_id": before,"region": [0,28,36,57],"threshold": .98},
        "after": {"frame_id": after,"region": [0,28,36,57],"threshold": .98},
        "action": result["receipt"]["action"], "timeout": .1, "purpose": "reach exit", "expected_result": "XY15,9"}])
    candidate.build(live, "test navigation", [{"kind": "behavior", "behavior_id": record["behavior_id"],
        "frame_id": after, "purpose": "reach exit", "expected_result": "XY15,9"}], [])
    from src.agent_bridge.storage import read_json
    saved = read_json(live.folder / "candidate.json")["plan"]
    model = AutomationPlan.from_dict(saved, tmp_path)
    assert model.to_dict() == saved
    program = model.initial_rules[0].gpt_play_config
    live.backend.xy = [12,7]
    result = run_saved(program, lambda: None, backend=live.backend, receipt_root=tmp_path / "receipts")
    assert result["status"] == "conditions_matched" and live.backend.xy == [15,9]


def test_model_calibration_saves_reloadable_screen_samples(live):
    class Samples(GridGame):
        def capture(self):
            image, region = super().capture()
            for n in range(10):
                image.paste(digit_image(n), (n*8, 80))
            return image, region
    live.backend = Samples()
    live.stream.capture = live.backend.capture
    ident = frame(live)
    args = {"observation_id": ident, "x_region": [1,30,35,40], "y_region": [1,45,35,55], "light": True, "level": 128,
            "samples": [{"digit": n, "frame_id": ident, "region": [n*8,80,n*8+6,90]} for n in range(10)]}
    result = live.dispatch("play_coordinate_setup", args)
    assert result["reading"]["xy"] == [12,7] and result["saved_and_reloaded"]
    saved = ExperienceStore(live.learning.store.root).get(live.learning.task, "coordinate_profile", "active")
    assert CoordinateReader(saved).read(live.backend.capture()[0])["xy"] == [12,7]
    assert not live.backend.sent
