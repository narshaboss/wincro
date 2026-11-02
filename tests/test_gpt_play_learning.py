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


def test_uncertain_input_survives_multiple_restarts_until_fresh_reconciliation(learned, tmp_path):
    s, m = learned, learned.learning
    ident = frame(s)
    s.backend.fail = True
    with pytest.raises(BridgeError):
        s.act(ident, "unknown-send", {"kind": "key", "key": "space"}, "join")
    released(s)
    s.stop()
    released(s)
    for index in range(2):
        next_session = PlaySession(tmp_path / str(index), s.goal, s.keys, Game(), lambda: True,
                                   memory=m.store, task_id=m.task)
        next_session.start()
        fresh = frame(next_session)
        with pytest.raises(BridgeError, match="불확실"):
            next_session.act(fresh, "must-not-send", {"kind": "key", "key": "space"}, "join")
        assert not next_session.backend.sent
        if index == 1:
            next_session.learning.reconcile("unknown-send", fresh, "observed_applied", "arena already shown")
            assert not next_session.learning.pending
            next_session.act(fresh, "new-purpose", {"kind": "key", "key": "z"}, "attack observed enemy")
            assert len(next_session.backend.sent) == 1
        next_session.stop()
        released(next_session)
    assert not m.store.context(m.task)["checkpoint"]["uncertain_requests"]


def test_save_atomic_session_and_checkpoint_failure_rolls_back(learned, monkeypatch):
    store, task = learned.learning.store, learned.learning.task
    before = store.get(task, "checkpoint", "latest")
    # Invalid JSON is detected before either row is committed.
    with pytest.raises(ValueError):
        store.put_many(task, [("checkpoint", "latest", {"changed": True}), ("session", "bad", {"nan": float("nan")})])
    assert store.get(task, "checkpoint", "latest") == before


def test_pause_signals_cancel_before_a_busy_state_lock(learned):
    reached = threading.Event()
    with learned.state_lock:
        worker = threading.Thread(target=lambda: (learned.pause(), reached.set()))
        worker.start()
        assert learned.input_cancelled.wait(1)
        assert not reached.is_set()
    worker.join(3)
    assert reached.is_set()
    released(learned)


def test_candidate_and_reviews_survive_restart_but_need_new_round(learned, tmp_path):
    from src.ai_play.playback import CandidatePlayback
    s, m = learned, learned.learning
    behavior = confirmed(s)
    current = candidate.build(s, "verified timing", [{"kind": "behavior", "behavior_id": behavior["behavior_id"],
        "frame_id": frame(s), "purpose": "enter", "expected_result": "arena"}], [])
    for direction in ("experience_to_plan", "plan_to_experience"):
        candidate.review(s, current["revision"], direction, [{"rule_id": item["rule_id"], "frame_id": item["frame_id"],
            "result": "match", "note": "guard and hold timing match observed outcome"} for item in current["evidence"]], [])
    s.trials = [{"success": True, "session_id": s.instance_id, "reviewed_at": 999999999}]
    s.stop()
    released(s)
    new = PlaySession(tmp_path / "restart", s.goal, s.keys, Game(), lambda: True, memory=m.store, task_id=m.task,
                      clock=lambda: 100)
    try:
        new.start()
        assert new.candidate == s.candidate and new.reviews == s.reviews
        assert candidate.ready(new) and new.trials == s.trials
        new.playback = CandidatePlayback(new, executor_factory=lambda _: SimpleNamespace(run=lambda *_: True, stop=lambda: None))
        trial = new.playback.start(current["revision"], frame(new), "fresh NPC initial screen")
        new.playback.worker.join(3)
        assert trial["session_id"] == new.instance_id
    finally:
        new.stop()
        released(new)


def test_new_instruction_cannot_rereview_an_old_candidate(learned):
    behavior = confirmed(learned)
    current = candidate.build(learned, "old", [{"kind": "behavior", "behavior_id": behavior["behavior_id"],
        "frame_id": frame(learned), "purpose": "enter", "expected_result": "arena"}], [])
    learned.pause()
    released(learned)
    learned.add_command("do not enter again")
    learned.resume()
    with pytest.raises(BridgeError, match="지시가 바뀌"):
        candidate.review(learned, current["revision"], "experience_to_plan", [], [])
    assert not candidate.ready(learned)


def test_native_player_dispatches_new_type_once_without_legacy_fallback(learned, monkeypatch):
    from src.player.rule_executor import RuleExecutor
    from src.analyzer.automation_models import AutomationRule
    import src.ai_play.saved_runtime as runtime
    record = routine(learned)
    program = learned.learning.store.get(learned.learning.task, "behavior", record["behavior_id"])["program"]
    rule = AutomationRule(action_type="gpt_play_behavior", gpt_play_config=program)
    executor = object.__new__(RuleExecutor)
    executor._stop_event, executor._pause_event = threading.Event(), threading.Event()
    executor._pause_event.set()
    executor._make_result = lambda _rule, ok, message, _start: {"ok": ok, "message": message}
    seen = []
    def run(value, check):
        check()
        seen.append(value)
        return {"status": "conditions_matched"}
    monkeypatch.setattr(runtime, "run_saved", run)
    assert executor._execute_rule(rule)["ok"]
    assert seen == [program]
    executor._pause_event.clear()
    with pytest.raises(BridgeError, match="일시정지"):
        executor._execute_gpt_play_behavior(rule, None)


def test_old_plans_do_not_gain_new_fields_on_save():
    from src.analyzer.automation_models import AutomationRule
    rule = AutomationRule(action_type="key_press", action_keys=["up"])
    assert "gpt_play_config" not in rule.to_dict()


def test_ui_poll_does_not_wait_for_database_state_lock(learned):
    expected = learned.ui_snapshot()
    ready, finish = threading.Event(), threading.Event()
    def hold():
        with learned.state_lock:
            ready.set()
            finish.wait(3)
    worker = threading.Thread(target=hold)
    worker.start()
    assert ready.wait(1)
    try:
        assert learned.ui_snapshot() is expected
        assert learned.input_released is False
        assert not finish.is_set()
    finally:
        finish.set()
        worker.join(3)


def test_memory_pages_keep_old_facts_accessible(learned):
    store, task = learned.learning.store, learned.learning.task
    for i in range(23):
        store.event(task, "fact", {"observation": str(i)})
    first, older, last = [store.context(task, offset) for offset in (0, 10, 20)]
    assert first["has_more"] and older["has_more"] and not last["has_more"]
    values = [r["record"]["observation"] for page in (first, older, last) for r in page["recent"]]
    assert len(set(values)) == 23
    with pytest.raises(BridgeError):
        store.context(task, -1)


def test_post_input_save_failure_restores_unknown_not_duplicate_send(learned, monkeypatch, tmp_path):
    s, m = learned, learned.learning
    ident = frame(s)
    original = m.store.put_many
    def fail_after_input(task, rows):
        if s.backend.sent:
            raise OSError("post-input disk failure")
        return original(task, rows)
    monkeypatch.setattr(m.store, "put_many", fail_after_input)
    with pytest.raises(OSError):
        s.act(ident, "sent-before-crash", {"kind": "key", "key": "space"}, "join once")
    assert len(s.backend.sent) == 1 and s.storage_failed
    assert m.store.context(m.task)["checkpoint"]["uncertain_requests"][0]["request_id"] == "sent-before-crash"
    monkeypatch.undo()
    restored = PlaySession(tmp_path / "restored", s.goal, s.keys, Game(), lambda: True, memory=m.store, task_id=m.task)
    try:
        restored.start()
        with pytest.raises(BridgeError, match="불확실"):
            restored.act(frame(restored), "again", {"kind": "key", "key": "space"}, "join")
        assert not restored.backend.sent
    finally:
        restored.stop()
        released(restored)


def test_native_candidate_unknown_input_blocks_same_session_retrial(learned, monkeypatch):
    from src.ai_play.playback import native_executor
    from src.player import rule_executor
    monkeypatch.setattr(rule_executor.pyautogui, "position", lambda: (0, 0))
    s = learned
    behavior = confirmed(s)
    candidate.build(s, "unknown test", [{"kind": "behavior", "behavior_id": behavior["behavior_id"],
        "frame_id": frame(s), "purpose": "enter", "expected_result": "arena"}], [])
    s.trial = {"trial_id": "trial"}
    s.backend.fail = True
    with pytest.raises(BridgeError):
        native_executor(s)._send({"kind": "hold", "keys": ["up"], "duration_ms": 100}, s.backend.validate())
    assert s.learning.pending
    with pytest.raises(BridgeError, match="불확실"):
        s.check(input_allowed=True)


def test_new_guarded_candidate_ten_native_trials_and_durable_publication(learned, monkeypatch, tmp_path):
    from src.ai_play.playback import CandidatePlayback
    from src.player import rule_executor
    from src.analyzer.automation_models import AutomationPlan
    monkeypatch.setattr(rule_executor.pyautogui, "position", lambda: (0, 0))
    s = learned
    behavior = confirmed(s)
    current = candidate.build(s, "Guarded new plan", [{"kind": "behavior", "behavior_id": behavior["behavior_id"],
        "frame_id": frame(s), "purpose": "enter", "expected_result": "arena"}], [])
    for direction in ("experience_to_plan", "plan_to_experience"):
        candidate.review(s, current["revision"], direction, [{"rule_id": item["rule_id"], "frame_id": item["frame_id"],
            "result": "match", "note": "source and compiled program agree"} for item in current["evidence"]], [])
    s.playback = CandidatePlayback(s)
    for i in range(10):
        s.backend.color = 1
        trial = s.playback.start(current["revision"], frame(s), "new simulated initial round " + str(i))
        s.playback.worker.join(5)
        assert not s.playback.busy and s.trial["execution_ok"]
        result = s.playback.result(trial["trial_id"], frame(s), True, "simulated result differs from initial screen")
        assert result["success"]
    s.state = "complete"
    plans, templates = tmp_path / "new-plans", tmp_path / "new-templates"
    publication = candidate.publish(s, plans, templates)
    files = list(plans.glob("*.json"))
    assert len(files) == 1
    saved = read_json(files[0])
    model = AutomationPlan.from_dict(saved, templates)
    assert model.to_dict() == saved
    assert model.initial_rules[0].gpt_play_config == s.candidate["plan"]["initial_rules"][0]["gpt_play_config"]
    assert all(action["kind"] == "hold" and action["duration_ms"] == 200 for action in s.backend.sent)
    assert publication["plan_id"] == saved["plan_id"]


def test_native_receipt_limit_never_deletes_or_sends(learned, monkeypatch, tmp_path):
    from src.ai_play import saved_runtime
    record = routine(learned)
    program = learned.learning.store.get(learned.learning.task, "behavior", record["behavior_id"])["program"]
    root = tmp_path / "receipts"
    (root / "run_existing").mkdir(parents=True)
    monkeypatch.setattr(saved_runtime, "MAX_RECEIPT_RUNS", 1)
    backend = Game()
    with pytest.raises(BridgeError, match="한도"):
        saved_runtime.run_saved(program, lambda: None, backend=backend, receipt_root=root)
    assert not backend.sent and (root / "run_existing").exists()


def test_stop_stays_stopped_and_releases_even_when_checkpoint_save_fails(learned, monkeypatch):
    def fail(*_args, **_kwargs):
        raise OSError("disk unavailable")
    monkeypatch.setattr(learned.learning.store, "put_many", fail)
    with pytest.raises(OSError):
        learned.stop()
    assert learned.state == "stopped" and learned.stop_event.is_set()
    released(learned)
    assert learned.backend.releases > 0
    monkeypatch.undo()


@pytest.mark.parametrize("operation", ["pause", "stop", "interrupt"])
def test_control_cancels_model_even_if_session_save_fails(tmp_path, operation, monkeypatch):
    from unittest.mock import Mock
    from src.ai_play.jobs import PlayJobs
    jobs = PlayJobs(tmp_path / "jobs", tmp_path / "plans", tmp_path / "templates", lambda: True)
    jobs._owns_input = True
    jobs.session = SimpleNamespace(state="exploring", pause=Mock(side_effect=OSError("disk full")),
                                   stop=Mock(side_effect=OSError("disk full")))
    cancel, cleanup = Mock(), Mock()
    monkeypatch.setattr(jobs, "_cancel_runner", cancel)
    monkeypatch.setattr(jobs, "_release_owner", cleanup)
    with pytest.raises(OSError):
        getattr(jobs, operation)()
    cancel.assert_called_once()
    if operation == "stop":
        assert jobs.monitor_stop.is_set()
    if operation == "interrupt":
        assert jobs.intervention_requested.is_set()


def test_verification_counts_consecutive_current_candidate_only(learned):
    behavior = confirmed(learned)
    candidate.build(learned, "streak", [{"kind": "behavior", "behavior_id": behavior["behavior_id"],
        "frame_id": frame(learned), "purpose": "enter", "expected_result": "arena"}], [])
    result = {"success": True, "revision": learned.candidate["revision"], "candidate_digest": learned.candidate["digest"]}
    learned.trials = [dict(result) for _ in range(10)] + [{"success": False}] + [dict(result)]
    assert candidate.success_streak(learned) == 1
    assert learned.ui_snapshot()["verification"]["successes"] == 1
    learned.trials.append({**result, "candidate_digest": "stale"})
    assert candidate.success_streak(learned) == 0


def test_watchdog_save_failure_still_cancels_runner(learned, monkeypatch, tmp_path):
    from unittest.mock import Mock
    from src.ai_play.jobs import PlayJobs
    jobs = PlayJobs(tmp_path / "watch", tmp_path / "plans", tmp_path / "templates", lambda: True)
    jobs.session = learned
    monkeypatch.setattr(learned.backend, "start_controls", Mock(side_effect=OSError("controls failed")), raising=False)
    monkeypatch.setattr(learned.learning.store, "put_many", Mock(side_effect=OSError("disk full")))
    cancel = Mock()
    monkeypatch.setattr(jobs, "_cancel_runner", cancel)
    with pytest.raises(OSError):
        jobs._watch(learned, threading.Event())
    cancel.assert_called_once()
    assert learned.input_cancelled.is_set()
    monkeypatch.undo()
