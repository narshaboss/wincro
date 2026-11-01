"""Deterministic live-play simulations. Never access a desktop/input device/network."""

import copy
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

from src.ai_play import candidate, ownership
from src.ai_play.jobs import PlayJobs, PlayScope
from src.ai_play.playback import CandidatePlayback
from src.ai_play.session import PlaySession, SAFE_KEYS, TOOLS
from src.agent_bridge.contracts import BridgeError
from src.agent_bridge.storage import read_json


class Backend:
    def __init__(self):
        self.sent = []
        self.releases = 0
        self.prepared = 0
        self.color = 1
        self.region = [100, 100, 260, 220]
        self.focus = True
        self.fail = False

    def prepare(self):
        self.prepared += 1
        self.focus = True

    def validate(self, expected=None):
        if not self.focus:
            raise BridgeError("FOCUS_LOST", "focus lost")
        if expected is not None and expected != self.region:
            raise BridgeError("TARGET_MOVED", "window moved")
        return self.region

    def capture(self):
        return Image.new("RGB", (160, 120), (self.color, 90, 50)), self.validate()

    def send(self, action, region, check):
        check()
        self.validate(region)
        self.sent.append(copy.deepcopy(action))
        self.color += 1
        return not self.fail

    def release(self):
        self.releases += 1
        return True


@pytest.fixture
def session(tmp_path):
    connected = [True]
    backend = Backend()
    result = PlaySession(tmp_path / "session", "clear and claim reward", ["up", "space", "enter"], backend, lambda: connected[0])
    result.connection_flag = connected
    result.start()
    yield result
    result.stop()
    for worker in result.release_workers:
        worker.join(2)


def frame(session):
    return session.observe()["frame_id"]


def authored(session, click=False):
    reference = frame(session)
    steps = [{"kind": "key", "key": "space", "frame_id": reference,
        "purpose": "attack the observed enemy", "expected_result": "clear result", "wait_after": .1}]
    if click:
        steps.insert(0, {"kind": "click", "frame_id": reference, "crop": [10, 10, 30, 30],
            "purpose": "join at NPC", "expected_result": "enter arena"})
    return candidate.build(session, "GPT new plan", steps, [])


def reviewed(session):
    state = authored(session)
    for direction in ("experience_to_plan", "plan_to_experience"):
        candidate.review(session, state["revision"], direction,
            [{"rule_id": e["rule_id"], "frame_id": e["frame_id"], "result": "match", "note": "Visible task matches"}
             for e in state["evidence"]], [])


def test_off_refuses_capture_and_input_before_any_backend_work(session):
    session.connection_flag[0] = False
    with pytest.raises(BridgeError) as error:
        session.observe()
    assert error.value.code == "CONNECTION_OFF"
    assert session.state == "paused"
    assert not session.backend.sent


def test_observe_act_no_replay_on_duplicate_and_requires_new_scene(session):
    reference = frame(session)
    args = (reference, "once", {"kind": "key", "key": "up"}, "approach NPC")
    receipt = session.act(*args)
    assert receipt["state"] == "sent_not_game_success"
    assert session.act(*args) == receipt
    assert len(session.backend.sent) == 1
    with pytest.raises(BridgeError) as err:
        session.act(reference, "two", {"kind": "key", "key": "up"}, "approach NPC")
    assert err.value.code == "STALE_OBSERVATION"
    assert read_json(session.folder / "session.json")["receipts"]["once"] == receipt


@pytest.mark.parametrize("action", [
    {"kind": "key", "key": "win"}, {"kind": "key", "key": "space", "hold": 1},
    {"kind": "key", "key": "ctrl+c"}, {"kind": "click", "x": -1, "y": 10},
    {"kind": "click", "x": 10, "y": 120}, {"kind": "click", "x": True, "y": 2},
    {"kind": "click", "x": 1, "y": 1, "button": "right"},
    {"kind": "wait", "seconds": float("nan")}, {"kind": "wait", "seconds": 20},
    {"kind": "shell", "command": "no"},
])
def test_bounded_action_validation_no_input(session, action):
    reference = frame(session)
    with pytest.raises((BridgeError, ValueError)):
        session.act(reference, "bad", action, "test")
    assert not session.backend.sent


def test_stale_expired_and_moved_window_fail_closed(session):
    reference = frame(session)
    session.frames[reference]["time"] -= 21
    with pytest.raises(BridgeError) as error:
        session.act(reference, "expired", {"kind": "key", "key": "up"}, "move")
    assert error.value.code == "STALE_OBSERVATION"
    reference = frame(session)
    session.backend.region = [101, 100, 261, 220]
    with pytest.raises(BridgeError) as error:
        session.act(reference, "moved", {"kind": "click", "x": 10, "y": 10}, "join")
    assert error.value.code == "TARGET_MOVED"
    assert not session.backend.sent


def test_unknown_delivery_is_persisted_and_never_blindly_retried(session):
    reference = frame(session)
    session.backend.fail = True
    args = (reference, "unknown", {"kind": "key", "key": "space"}, "attack")
    with pytest.raises(BridgeError):
        session.act(*args)
    assert session.state == "paused"
    assert read_json(session.folder / "session.json")["receipts"]["unknown"]["state"] == "unknown"
    session.resume()
    assert session.act(*args)["state"] == "unknown"
    assert len(session.backend.sent) == 1


def test_question_has_real_answer_and_invalidates_old_scene(session):
    reference = frame(session)
    result = session.ask(reference, "부활은 어느 버튼인가요?", "사망 표시가 보이지만 무료 부활을 확인하지 못했습니다.")
    assert result["waiting_for_user"]
    assert session.state == "question"
    with pytest.raises(BridgeError):
        session.act(reference, "blocked", {"kind": "key", "key": "enter"}, "revive")
    with pytest.raises(BridgeError):
        session.resume("")
    assert session.question is not None
    session.resume("왼쪽 무료 부활 버튼을 눌러.")
    assert session.answers[-1]["answer"] == "왼쪽 무료 부활 버튼을 눌러."
    assert session.question is None
    with pytest.raises(BridgeError):
        session.act(reference, "stale", {"kind": "key", "key": "enter"}, "revive")
    assert session.latest is None
    assert frame(session)


def test_pause_during_inflight_click_prevents_key_send(session):
    ready, proceed = threading.Event(), threading.Event()
    def send(action, region, check):
        ready.set()
        assert proceed.wait(2)
        check()
        session.backend.sent.append(action)
        return True
    session.backend.send = send
    errors = []
    reference = frame(session)
    def act():
        try:
            session.act(reference, "race", {"kind": "click", "x": 1, "y": 1}, "join")
        except BridgeError as exc:
            errors.append(exc)
    worker = threading.Thread(target=act)
    worker.start()
    assert ready.wait(2)
    session.pause()
    proceed.set()
    worker.join(2)
    assert errors and not session.backend.sent


def test_action_limit_last_action_runs_but_no_extra(session):
    session.max_actions = 1
    session.act(frame(session), "last", {"kind": "key", "key": "up"}, "move")
    assert len(session.backend.sent) == 1
    with pytest.raises(BridgeError):
        session.act(frame(session), "extra", {"kind": "key", "key": "up"}, "move")
    assert len(session.backend.sent) == 1


def test_candidates_are_real_native_roundtrips_and_edits_invalidate_tests(session):
    state = authored(session, click=True)
    stored = read_json(session.folder / "candidate.json")
    assert state["revision"] == stored["revision"]
    from src.analyzer.automation_models import AutomationPlan
    assert AutomationPlan.from_dict(stored["plan"], session.folder / "assets").to_dict() == stored["plan"]
    assert len(stored["assets"]) == 1
    assert not stored["plan"].get("game_modes")
    reviewed(session)
    assert candidate.ready(session)
    session.trials = [{"success": True}]
    authored(session)
    assert not candidate.ready(session)
    assert session.trials == []


@pytest.mark.parametrize("bad", ["game_mode", "window_control", "type", "hold", "branch"])
def test_unsupported_plan_behavior_never_silently_compiled(session, bad):
    with pytest.raises(BridgeError):
        candidate.build(session, "bad", [{"kind": bad, "frame_id": frame(session),
            "purpose": "test", "expected_result": "unknown"}], [])
    assert session.candidate is None


def test_no_publication_without_independent_trials(session, tmp_path):
    reviewed(session)
    session.state = "complete"
    with pytest.raises(BridgeError):
        candidate.publish(session, tmp_path / "plans", tmp_path / "templates")
    assert not (tmp_path / "plans").exists()


class SimulatedExecutor:
    """No input log conversion; deterministic game outcome for lifecycle tests."""
    def __init__(self, session):
        self.session = session
        self.stopped = False

    def run(self, plan, assets):
        assert plan["initial_rules"][0]["action_type"] == "key_press"
        self.session.backend.color += 1
        self.session.action_count += 1
        return True

    def stop(self):
        self.stopped = True


def successful_trial(session, playback):
    session.backend.color += 3
    trial = playback.start(session.candidate["revision"], frame(session), "fresh round marker changed")
    playback.worker.join(3)
    assert not playback.busy
    return playback.result(trial["trial_id"], frame(session), True, "New clear and reward receipt visible")


def test_three_trials_publish_new_only_and_reload_idempotent(session, tmp_path):
    plans, templates = tmp_path / "plans", tmp_path / "templates"
    plans.mkdir()
    old = plans / "original.json"
    old.write_text('{"untouched": true}', encoding="utf-8")
    reviewed(session)
    playback = CandidatePlayback(session, SimulatedExecutor)
    session.playback = playback
    for _ in range(3):
        successful_trial(session, playback)
    recovery = {k: {"status": "not_applicable", "note": "This deterministic fixture has no death/NPC state"}
                for k in ("failure_or_death", "exit", "revive", "return_to_npc")}
    PlayScope(session).finish("3 independent fixture runs passed", [], recovery)
    receipt = candidate.publish(session, plans, templates)
    assert receipt["saved_and_reloaded"] and not receipt["playlist_changed"] and not receipt["user_reviewed"]
    assert candidate.publish(session, plans, templates) == receipt
    assert len(list(plans.glob("*.json"))) == 2
    assert old.read_text() == '{"untouched": true}'
    actual = read_json(plans / (receipt["plan_id"] + ".json"))
    assert actual["user_verified"] is True
    assert actual["initial_rules"] == session.candidate["plan"]["initial_rules"]


def test_gpt_cannot_rescue_or_edit_while_candidate_running(session):
    reviewed(session)
    gate = threading.Event()
    class Held(SimulatedExecutor):
        def run(self, plan, assets):
            gate.wait(2)
            return super().run(plan, assets)
    playback = CandidatePlayback(session, Held)
    session.playback = playback
    playback.start(session.candidate["revision"], frame(session), "new round")
    try:
        with pytest.raises(BridgeError) as error:
            session.act(frame(session), "rescue", {"kind": "key", "key": "up"}, "help")
        assert error.value.code == "OBSERVER_ONLY"
        with pytest.raises(BridgeError):
            authored(session)
    finally:
        gate.set()
        playback.worker.join(3)


def test_trial_failure_cannot_be_reported_as_success(session):
    reviewed(session)
    class Failed(SimulatedExecutor):
        def run(self, *_):
            return False
    playback = CandidatePlayback(session, Failed)
    session.playback = playback
    trial = playback.start(session.candidate["revision"], frame(session), "new round")
    playback.worker.join(2)
    session.backend.color += 1
    with pytest.raises(BridgeError):
        playback.result(trial["trial_id"], frame(session), True, "attempted lie")


def test_gpt_cannot_rescue_after_executor_finishes_before_outcome_review(session):
    reviewed(session)
    playback = CandidatePlayback(session, SimulatedExecutor)
    session.playback = playback
    trial = playback.start(session.candidate["revision"], frame(session), "new round")
    playback.worker.join(2)
    assert session.state == "reviewing_trial"
    with pytest.raises(BridgeError) as err:
        session.act(frame(session), "late-rescue", {"kind": "key", "key": "enter"}, "claim the missed reward")
    assert err.value.code == "OBSERVER_ONLY"
    session.pause()
    session.resume()
    with pytest.raises(BridgeError):
        playback.result(trial["trial_id"], frame(session), True, "interrupted is not success")


def test_input_ownership_reserves_startup_and_excludes_competing_players():
    entered, unblock = threading.Event(), threading.Event()
    class Player:
        is_running = False
        @ownership.legacy_start
        def play(self):
            entered.set()
            unblock.wait(2)
            self.is_running = True
            return True
    player = Player()
    thread = threading.Thread(target=player.play)
    thread.start()
    assert entered.wait(2)
    owner = object()
    try:
        with pytest.raises(ValueError):
            ownership.acquire(owner)
        unblock.set()
        thread.join(2)
        with pytest.raises(ValueError):
            ownership.acquire(owner)
        player.is_running = False
        ownership.acquire(owner)
        assert player.play() is False
        with ownership.authorized(owner):
            assert player.play() is True
    finally:
        player.is_running = False
        unblock.set()
        thread.join(2)
        ownership.release(owner)


def test_old_recording_tools_never_get_live_authority():
    from src.agent_bridge.recording_jobs import JOB_TOOLS
    from src.agent_bridge.recording_connection import ConnectionScope
    assert not (TOOLS - {"connection_status"}) & JOB_TOOLS
    for method in TOOLS - {"connection_status"}:
        with pytest.raises(BridgeError):
            ConnectionScope().dispatch(method, {})
    assert "win" not in SAFE_KEYS and "ctrl" not in SAFE_KEYS and "alt" not in SAFE_KEYS


def test_runner_keeps_sandbox_and_uses_separate_server(tmp_path):
    from src.ai_play.jobs import PlayRunner
    runner = PlayRunner({}, tmp_path)
    args = runner.arguments("codex", "python", "play_server.py", "descriptor")
    assert runner.mcp_config["args"][0] == "play_server.py"
    assert set(runner.mcp_config["enabled_tools"]) == TOOLS
    assert runner.thread_parameters()["environments"] == []
    assert "features.computer_use=false" in args
    assert "features.shell_tool=false" in args
    from src.agent_bridge.codex_runner import CodexRunner
    draft = {"editing_only": True, "live_execution": False}
    live = {"editing_only": False, "live_execution": True, "authority": "bounded_gpt_play_only", "recording_authority": False}
    assert not runner.accepts_connection(draft)
    assert runner.accepts_connection(live)
    assert CodexRunner({}, tmp_path).accepts_connection(draft)
    assert not CodexRunner({}, tmp_path).accepts_connection(live)


class FakeHost:
    hosts = {}
    def __init__(self, scope, _):
        self.scope = scope
        self.closed = False
    def start(self):
        key = str(id(self))
        self.hosts[key] = self
        return key
    def close(self):
        self.closed = True


def test_jobs_off_gate_and_question_resume_roundtrip(tmp_path, monkeypatch):
    from src.ai_play import jobs as module
    monkeypatch.setattr(module, "PlayHost", FakeHost)
    monkeypatch.setattr(module, "protect_directory", lambda p: Path(p).mkdir(parents=True, exist_ok=True))
    calls = []
    class Actor:
        def __init__(self, *_):
            pass
        def cancel(self):
            pass
        def run(self, descriptor, prompt, cancel, progress):
            scope = FakeHost.hosts[descriptor].scope
            s = scope.session
            reference = scope.dispatch("play_observe", {})["frame_id"]
            if not s.answers:
                calls.append("question")
                scope.dispatch("play_question", {"observation_id": reference, "question": "Which revive?", "context": "Dead"})
            else:
                calls.append(s.answers[-1]["answer"])
                scope.dispatch("play_act", {"observation_id": reference, "request_id": "revive", "action": {"kind": "key", "key": "enter"}, "purpose": "free revive"})
            return "turn complete"
    connected = [False]
    manager = PlayJobs(tmp_path, tmp_path / "plans", tmp_path / "templates", lambda: connected[0], runner_factory=Actor)
    backend = Backend()
    with pytest.raises(BridgeError):
        manager.start(None, "clear", ["enter"], {}, test_environment=True, backend=backend)
    assert backend.prepared == 0
    connected[0] = True
    try:
        manager.start(None, "clear", ["enter"], {}, test_environment=True, backend=backend)
        manager.worker.join(3)
        assert manager.session.state == "question"
        manager.resume("Use free revive")
        manager.worker.join(3)
        assert calls == ["question", "Use free revive"]
        assert backend.sent == [{"kind": "key", "key": "enter"}]
        assert manager.session.state == "paused"
    finally:
        manager.stop()
        if manager.worker:
            manager.worker.join(3)
        manager._release_owner(manager.session)


def test_native_candidate_uses_existing_key_rule_loop_no_desktop(session, monkeypatch):
    from src.ai_play.playback import native_executor
    from src.player import rule_executor
    monkeypatch.setattr(rule_executor.pyautogui, "position", lambda: (0, 0))
    reviewed(session)
    session.state = "verifying"
    session.trial = {"trial_id": "native-test"}
    executor = native_executor(session)
    executor._auto_list_wait = lambda _: True
    assert executor.run(session.candidate["plan"], session.folder / "assets")
    assert session.backend.sent == [{"kind": "key", "key": "space"}]
    assert session.action_count == 1


def test_atomic_arduino_tap_never_falls_back_or_sends_split_edges(monkeypatch):
    from src.utils import input_controller as module
    from src.utils import arduino_hid
    commands = []
    device = SimpleNamespace(is_connected=True, supports_key_combo_tap=lambda: True,
        _get_keycode=lambda key: 218, _send_command=lambda command: commands.append(command) or True)
    monkeypatch.setattr(arduino_hid, "get_arduino_hid", lambda: device)
    monkeypatch.setattr(module, "is_automation_input_blocked", lambda: False)
    controller = module.InputController()
    monkeypatch.setattr(controller, "_use_arduino", lambda: True)
    monkeypatch.setattr(controller, "press", lambda *_: pytest.fail("software/raw fallback forbidden"))
    assert controller.play_lab_tap("up")
    assert commands == ["KQ,218"]
    device.is_connected = False
    assert controller.play_lab_tap("up") is False
    assert len(commands) == 1
