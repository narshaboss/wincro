"""Pure simulations: no desktop, network, serial port or existing user plans."""

import copy
import io
from pathlib import Path
from types import SimpleNamespace
import threading

from PIL import Image, ImageDraw
import pytest

from src.agent_bridge.contracts import BridgeError, digest
from src.agent_bridge.storage import read_json
from src.ai_play.actions import validate_action
from src.ai_play.experience import ExperienceStore
from src.ai_play.session import PlaySession
from src.ai_play import behaviors, candidate
from tests.test_gpt_play import Backend, frame, released, isolated_admission_registry


class Game(Backend):
    def capture(self):
        image = Image.new("RGB", (160, 120), (10, 10, 10))
        draw = ImageDraw.Draw(image)
        draw.rectangle([4, 4, 23, 23], fill=(220, 220, 220) if self.color == 1 else (200, 20, 20))
        draw.rectangle([9, 9, 17, 17], fill=(20, 90, 240))
        return image, self.validate()


@pytest.fixture
def learned(tmp_path):
    store = ExperienceStore(tmp_path / "memory")
    task = store.create("enter and clear", {"executable": "game.exe", "title": "Game"})
    session = PlaySession(tmp_path / "session", "enter and clear", ["up", "z", "space"], Game(), lambda: True,
                          memory=store, task_id=task)
    session.start()
    yield session
    session.stop()
    released(session)


def routine(session):
    before = frame(session)
    session.act(before, "learn", {"kind": "hold", "keys": ["up"], "duration_ms": 200}, "enter")
    after = frame(session)
    return behaviors.author(session, "enter", [{
        "before": {"frame_id": before, "region": [0, 0, 28, 28], "threshold": .98},
        "action": {"kind": "hold", "keys": ["up"], "duration_ms": 200},
        "after": {"frame_id": after, "region": [0, 0, 28, 28], "threshold": .98},
        "purpose": "enter", "expected_result": "arena shown", "timeout": .1}])


def confirmed(session):
    record = routine(session)
    for i in range(3):
        session.backend.color = 1
        result = behaviors.run_behavior(session, record["behavior_id"], frame(session), str(i))
        assert result["status"] == "conditions_matched"
    return record


@pytest.mark.parametrize("action", [
    {"kind": "hold", "keys": ["up"], "duration_ms": 50},
    {"kind": "hold", "keys": ["up", "z"], "duration_ms": 1000},
    {"kind": "repeat", "key": "z", "duration_ms": 100, "interval_ms": 50, "count": 5},
])
def test_timed_contract_accepts_bounded_input(action):
    assert validate_action(action) == action


@pytest.mark.parametrize("action", [
    {"kind": "hold", "keys": ["up"], "duration_ms": 0},
    {"kind": "hold", "keys": ["up"], "duration_ms": True},
    {"kind": "hold", "keys": ["up"], "duration_ms": 1001},
    {"kind": "hold", "keys": ["ctrl", "z"], "duration_ms": 100},
    {"kind": "hold", "keys": ["alt", "f4"], "duration_ms": 100},
    {"kind": "hold", "keys": ["f10"], "duration_ms": 100},
    {"kind": "hold", "keys": ["up", "up"], "duration_ms": 100},
    {"kind": "hold", "keys": ["up", "down", "z"], "duration_ms": 100},
    {"kind": "repeat", "key": "z", "duration_ms": 500, "interval_ms": 50, "count": 3},
    {"kind": "repeat", "key": "z", "duration_ms": 100, "interval_ms": 50, "count": 6},
])
def test_timed_contract_denies_unsafe_input(action):
    with pytest.raises(BridgeError):
        validate_action(action)


def test_repeat_budget_counts_every_tap_before_any_input(learned):
    learned.max_actions = 2
    with pytest.raises(BridgeError, match="최대 입력"):
        learned.act(frame(learned), "r", {"kind": "repeat", "key": "z", "duration_ms": 100, "interval_ms": 50, "count": 3}, "test")
    assert not learned.backend.sent


def test_durable_frames_notes_attempts_and_backup(learned):
    s, m = learned, learned.learning
    first = frame(s)
    m.attempt_event("start", first, "NPC", "join once")
    s.act(first, "join", {"kind": "key", "key": "space"}, "join")
    final = frame(s)
    m.record(final, "fact", "arena appeared", stage="arena")
    m.attempt_event("result", final, "arena", "join once", "success", "arena appeared")
    reopened = ExperienceStore(m.store.root)
    context = reopened.context(m.task)
    assert context["attempts"][0]["status"] == "success"
    assert context["attempts"][0]["hypothesis_is_proven"] is False
    assert reopened.read_frame(m.task, final)[0]["sha256"] == s.frames[final]["sha256"]
    assert reopened.get(m.task, "session", s.instance_id)["receipts"]["join"]["state"] == "sent_not_game_success"
    assert reopened.backup().is_file()


def test_restart_keeps_commands_but_never_restores_inputs_or_old_frame(learned, tmp_path):
    s, m = learned, learned.learning
    first = frame(s)
    m.attempt_event("start", first, "NPC", "join")
    s.receipts["unknown"] = {"state": "unknown", "action": {"kind": "key", "key": "space"}}
    s.pause()
    released(s)
    s.add_command("claim reward only once")
    s.stop()
    released(s)
    restored = PlaySession(tmp_path / "new", s.goal, s.keys, Game(), lambda: True,
                           memory=ExperienceStore(m.store.root), task_id=m.task)
    try:
        assert restored.commands[0]["command"] == "claim reward only once"
        assert restored.action_count == 0 and restored.latest is None and not restored.backend.sent
        assert restored.learning.context["checkpoint"]["uncertain_requests"]
        assert restored.learning.store.context(m.task)["attempts"][0]["status"] == "incomplete"
        with pytest.raises(BridgeError):
            restored._frame(first, fresh=True)
        assert restored.learning.frame(first)["historical_not_current"]
    finally:
        restored.stop()
        released(restored)


def test_task_lock_and_other_task_evidence_denied(learned):
    m = learned.learning
    lock = m.store.claim(m.task)
    try:
        with pytest.raises(BridgeError, match="다른 실행"):
            m.store.claim(m.task)
    finally:
        lock.close()
    m.store.claim(m.task).close()
    other = m.store.create("other", {"executable": "other.exe", "title": "Other"})
    with pytest.raises(BridgeError):
        m.store.read_frame(other, frame(learned))


def test_no_false_game_success_without_change(learned):
    ident = frame(learned)
    learned.learning.attempt_event("start", ident, "NPC", "join")
    with pytest.raises(BridgeError, match="입력과 새"):
        learned.learning.attempt_event("result", ident, "NPC", "join", "success", "guess")


def test_pre_input_save_failure_sends_nothing_and_blocks_future_input(learned, monkeypatch):
    ident = frame(learned)
    def fail(*_args, **_kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(learned.learning.store, "put_many", fail)
    with pytest.raises(OSError):
        learned.act(ident, "a", {"kind": "key", "key": "space"}, "join")
    assert not learned.backend.sent
    with pytest.raises(BridgeError):
        learned.act(ident, "b", {"kind": "key", "key": "space"}, "join")
    monkeypatch.undo()


def test_memory_limit_and_corruption_never_reset_database(tmp_path):
    store = ExperienceStore(tmp_path / "quota", quota=1)
    with pytest.raises(BridgeError, match="한도"):
        store.create("goal", {})
    bad = tmp_path / "bad"
    bad.mkdir()
    original = b"not a database"
    (bad / "experience.db").write_bytes(original)
    with pytest.raises(BridgeError):
        ExperienceStore(bad)
    assert (bad / "experience.db").read_bytes() == original


def test_guarded_reuse_is_idempotent_and_has_no_model_per_step(learned):
    record = routine(learned)
    learned.backend.color = 1
    ident = frame(learned)
    before = len(learned.backend.sent)
    first = behaviors.run_behavior(learned, record["behavior_id"], ident, "reuse")
    second = behaviors.run_behavior(learned, record["behavior_id"], ident, "reuse")
    assert first == second and len(learned.backend.sent) == before + 1
    assert first["game_success"] == "not_inferred_from_screen_match"


def test_wrong_start_guard_does_not_click_and_requires_revalidation(learned):
    record = routine(learned)
    count = len(learned.backend.sent)
    result = behaviors.run_behavior(learned, record["behavior_id"], frame(learned), "wrong")
    assert result["reason"] == "start_condition" and len(learned.backend.sent) == count
    learned.backend.color = 1
    with pytest.raises(BridgeError, match="목표가 바뀌었거나"):
        behaviors.run_behavior(learned, record["behavior_id"], frame(learned), "again")


def test_command_change_invalidates_reuse_but_preserves_memory(learned):
    record = confirmed(learned)
    learned.pause()
    released(learned)
    learned.add_command("do not claim rewards")
    learned.resume()
    learned.backend.color = 1
    with pytest.raises(BridgeError):
        behaviors.run_behavior(learned, record["behavior_id"], frame(learned), "old-goal")
    assert learned.learning.store.get(learned.learning.task, "behavior", record["behavior_id"])["successes"] == 3


def test_saved_native_model_roundtrip_preserves_exact_timing_and_guards(learned, tmp_path):
    from src.analyzer.automation_models import AutomationPlan
    from src.ai_play.saved_runtime import run_saved
    record = confirmed(learned)
    compiled = candidate.build(learned, "new independent plan", [{"kind": "behavior", "behavior_id": record["behavior_id"],
        "frame_id": frame(learned), "purpose": "join", "expected_result": "arena"}], [])
    plan = read_json(learned.folder / "candidate.json")["plan"]
    restored = AutomationPlan.from_dict(plan, learned.folder / "assets").to_dict()
    assert restored == plan
    program = plan["initial_rules"][0]["gpt_play_config"]
    assert program["steps"][0]["action"]["duration_ms"] == 200
    backend = Game()
    result = run_saved(program, lambda: None, backend=backend, receipt_root=tmp_path / "receipts")
    assert result["status"] == "conditions_matched"
    assert backend.sent == [{"kind": "hold", "keys": ["up"], "duration_ms": 200}]
    assert backend.releases == 1
    assert candidate.required_trials(learned) == 10
    learned.state = "complete"
    with pytest.raises(BridgeError, match="10회"):
        candidate.publish(learned, tmp_path / "plans", tmp_path / "templates")
    learned.state = "exploring"


def test_learning_cannot_export_timed_behavior_as_legacy_key(learned):
    with pytest.raises(BridgeError, match="재사용 구간"):
        candidate.build(learned, "wrong", [{"kind": "key", "key": "up", "frame_id": frame(learned),
            "purpose": "move", "expected_result": "reach"}], [])


def test_background_does_not_match_distinctive_glyph(learned):
    record = routine(learned)
    program = learned.learning.store.get(learned.learning.task, "behavior", record["behavior_id"])["program"]
    hit, _ = behaviors.matches(Image.new("RGB", (160, 120), (10, 10, 10)), program["steps"][0]["before"])
    assert not hit


def test_timed_protocol_cancel_and_unknown_are_not_retried(monkeypatch):
    from src.ai_play import timed_hid
    commands = []
    state = ["RUNNING"]
    def exchange(_device, command):
        commands.append(command)
        if command == "GL1":
            return "GL1:READY"
        token = command.split(",")[1]
        if command.startswith("GLP"):
            state[0] = "DONE"
        if command.startswith("GLC") and state[0] == "RUNNING":
            state[0] = "CANCELLED"
        return "GL1:" + token + ":" + state[0]
    monkeypatch.setattr(timed_hid, "exchange", exchange)
    assert timed_hid.run(object(), [218], 200, lambda: None)
    assert sum(c.startswith("GLS") for c in commands) == 1
    assert commands[-1].startswith("GLC")
    commands.clear()
    state[0] = "RUNNING"
    def cancelled():
        if any(c.startswith("GLS") for c in commands):
            raise BridgeError("STOP", "cancelled")
    with pytest.raises(BridgeError, match="cancelled"):
        timed_hid.run(object(), [218], 200, cancelled)
    assert state[0] == "CANCELLED"
    assert sum(c.startswith("GLS") for c in commands) == 1


def test_old_firmware_has_no_down_up_or_software_fallback(monkeypatch):
    from src.ai_play import timed_hid
    commands = []
    monkeypatch.setattr(timed_hid, "exchange", lambda _d, command: commands.append(command) or "ERR:UNKNOWN_CMD")
    with pytest.raises(BridgeError, match="GL1"):
        timed_hid.run(object(), [218], 200, lambda: None)
    assert commands == ["GL1"]


def test_old_model_scope_rejects_late_input(learned):
    from src.ai_play.jobs import PlayScope
    scope = PlayScope(learned)
    learned.pause()
    released(learned)
    learned.resume()
    with pytest.raises(BridgeError, match="이전 GPT 턴"):
        scope.dispatch("play_observe", {})
    assert not learned.backend.sent
