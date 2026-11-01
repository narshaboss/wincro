"""Diagnostic/focus simulations only: no real desktop, device, model or network."""

import json
from pathlib import Path
from types import SimpleNamespace
import threading

import pytest

from src.agent_bridge.contracts import BridgeError
from src.ai_play.diagnostics import PlayTrace
from src.ai_play.jobs import PlayJobs, PlayScope
from src.ai_play.session import PlaySession
from tests.test_gpt_play import Backend, FakeHost, isolated_admission_registry


def records(folder):
    return [json.loads(line) for line in (Path(folder) / "events.jsonl").read_text(encoding="utf-8").splitlines()]


def test_log_is_incremental_bounded_redacted_and_durable(tmp_path, caplog):
    trace = PlayTrace(tmp_path, "fixture")
    with caplog.at_level("INFO"):
        trace.emit("error", "Bearer abcdef12345678901234567890\nline two", level="error")
        assert "abcdef12345678901234567890" not in caplog.text
        assert "[redacted]" in caplog.text
        for i in range(310):
            trace.emit("step", str(i))
    rows = trace.since(0)
    assert len(rows) == 300
    assert not trace.since(rows[-1]["seq"])
    assert len(records(tmp_path)) == 311
    assert "abcdef12345678901234567890" not in (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    trace.max_file_bytes = trace.file_bytes
    trace.emit("step", "capacity")
    assert trace.file_disabled
    trace.emit("still_visible", "still running")
    assert trace.since(0)[-1]["event"] == "still_visible"
    assert sum(row["event"] == "log_file_error" for row in trace.since(0)) == 1
    assert len(records(tmp_path)) == 311


def test_disk_log_failure_never_stops_play_and_warns_once(tmp_path):
    (tmp_path / "events.jsonl").mkdir()
    trace = PlayTrace(tmp_path)
    trace.emit("first", "one")
    trace.emit("second", "two")
    assert [r["event"] for r in trace.since(0)] == ["first", "log_file_error", "second"]


def test_log_threads_have_monotonic_nonduplicated_sequence(tmp_path):
    trace = PlayTrace(tmp_path)
    workers = [threading.Thread(target=lambda: [trace.emit("step", "input") for _ in range(30)]) for _ in range(4)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(2)
        assert not worker.is_alive()
    assert [r["seq"] for r in records(tmp_path)] == list(range(1, 121))


def test_waiting_heartbeat_does_not_claim_progress_or_flood_logs(tmp_path):
    now = [0.0]
    trace = PlayTrace(tmp_path, clock=lambda: now[0])
    trace.emit("model_start", "waiting", phase="GPT 응답 대기")
    for tick in range(1, 32):
        now[0] = tick
        trace.heartbeat("exploring", 0, 0)
    waits = [r for r in records(tmp_path) if r["event"] == "waiting"]
    assert len(waits) == 2
    assert "관찰=0회 요청=0회" in waits[-1]["message"]
    assert trace.status() == ("GPT 응답 대기", 31)
    trace.progress("same")
    trace.progress("same")
    assert len([r for r in records(tmp_path) if r["event"] == "model_progress"]) == 1


def test_action_logs_keep_actual_input_and_observation_separate(tmp_path):
    backend = Backend()
    session = PlaySession(tmp_path, "clear", ["up"], backend, lambda: True)
    scope = PlayScope(session)
    session.start()
    try:
        for index, action in enumerate([{"kind": "key", "key": "up"},
                                       {"kind": "click", "x": 5, "y": 6},
                                       {"kind": "wait", "seconds": .05}]):
            reference = scope.dispatch("play_observe", {})["frame_id"]
            scope.dispatch("play_act", {"observation_id": reference, "request_id": str(index),
                "action": action, "purpose": "reach NPC"})
        reference = session.observe()["frame_id"]
        session.note(reference, "NPC visible", "join", "arena")
        previous = len(backend.sent)
        with pytest.raises(BridgeError):
            scope.dispatch("play_act", {"observation_id": reference, "request_id": "bad",
                "action": {"kind": "key", "key": "win"}, "purpose": "denied"})
        assert len(backend.sent) == previous == 2
        rows = records(tmp_path)
        assert sum(r["event"] == "action_sent" for r in rows) == 3
        assert any("키 UP" in r["message"] for r in rows)
        assert any("창내(5, 6)" in r["message"] and "화면좌표(105, 106)" in r["message"] for r in rows)
        assert any("KEY_DENIED" in r["message"] for r in rows)
        assert all("게임 반응은 아직 미확인" in r["message"] for r in rows if r["event"] == "action_sent")
        assert any(r["event"] == "observation_note" for r in rows)
        assert "png_base64" not in (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    finally:
        session.stop()
        for worker in session.release_workers:
            worker.join(2)


def test_prepare_failure_reason_survives_user_stop_and_never_calls_gpt(tmp_path, monkeypatch):
    from src.ai_play import jobs as module
    monkeypatch.setattr(module, "protect_directory", lambda p: Path(p).mkdir(parents=True, exist_ok=True))
    backend = Backend()
    def prepare():
        raise BridgeError("FOCUS_REQUIRED", "target=7 foreground=8")
    backend.prepare = prepare
    def forbidden(*_):
        pytest.fail("GPT must not run before target validation")
    jobs = PlayJobs(tmp_path, tmp_path / "plans", tmp_path / "templates", lambda: True, runner_factory=forbidden)
    try:
        jobs.start(None, "clear", ["up"], {}, test_environment=True, backend=backend)
        jobs.worker.join(3)
        assert not jobs.busy
        assert jobs.session.state == "paused"
        assert jobs.session.action_count == 0 and not jobs.session.frames
        assert "FOCUS_REQUIRED" in jobs.session.message
        jobs._progress("late GPT progress")
        assert "FOCUS_REQUIRED" in jobs.session.message
        jobs.stop()
        jobs._release_owner(jobs.session)
        rows = records(jobs.session.folder)
        assert any(r["event"] == "execution_failed" and "target=7 foreground=8" in r["message"] for r in rows)
        assert any(r["event"] == "stopped" for r in rows)
        assert not backend.sent
    finally:
        jobs.close()


def fake_window_backend(monkeypatch, tmp_path, *, delay=.15, minimized=False, accepted=True):
    from src.ai_play import windows
    from src.utils import input_controller as ic
    now, focuses, restored, rearmed = [0.0], [], [], []
    monkeypatch.setattr(windows.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(windows.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    monkeypatch.setattr(ic, "wait_for_input_cleanup", lambda **_: True)
    monkeypatch.setattr(ic, "unblock_automation_input", lambda: rearmed.append(True) or True)
    backend = object.__new__(windows.WindowIO)
    backend.target = SimpleNamespace(handle=7)
    target = SimpleNamespace(minimized=minimized, executable_name="game.exe", title="Game")
    def window(handle):
        if handle == 7:
            if restored and now[0] >= .1:
                target.minimized = False
            return target
        return SimpleNamespace(executable_name="other.exe", title="Other")
    backend.windows = SimpleNamespace(get_window=window,
        set_foreground_window=lambda _: focuses.append(True) or accepted,
        show_window_async=lambda *_: restored.append(True) or True,
        get_foreground_window=lambda: 7 if now[0] >= delay else 8)
    backend.controller = SimpleNamespace(play_lab_prepare=lambda: True)
    def validate():
        if target.minimized or now[0] < delay:
            raise BridgeError("FOCUS_LOST", "not foreground")
        return [0, 0, 100, 100]
    backend.validate = validate
    trace = PlayTrace(tmp_path, clock=lambda: now[0])
    backend.set_diagnostics(trace, lambda: None)
    return backend, now, focuses, restored, rearmed


@pytest.mark.parametrize("minimized,accepted", [(False, True), (True, True), (False, False)])
def test_start_waits_for_actual_focus_not_command_ack(tmp_path, monkeypatch, minimized, accepted):
    backend, now, focuses, restored, rearmed = fake_window_backend(monkeypatch, tmp_path,
        minimized=minimized, accepted=accepted)
    backend.prepare()
    assert .15 <= now[0] < 2
    assert len(focuses) == 1 and rearmed == [True]
    assert len(restored) == int(minimized)
    assert any(r["event"] == "focus_ready" for r in records(tmp_path))


def test_focus_timeout_retains_target_and_actual_window_without_input(tmp_path, monkeypatch):
    backend, now, focuses, _, rearmed = fake_window_backend(monkeypatch, tmp_path, delay=100)
    with pytest.raises(BridgeError) as error:
        backend.prepare()
    assert error.value.code == "FOCUS_REQUIRED"
    assert "hwnd=7" in str(error.value) and "hwnd=8" in str(error.value)
    assert "other.exe" in str(error.value)
    assert len(focuses) == 1 and not rearmed
    assert 2 <= now[0] < 2.1


def test_stop_or_pause_during_focus_wait_exits_before_input_rearm(tmp_path, monkeypatch):
    backend, now, focuses, _, rearmed = fake_window_backend(monkeypatch, tmp_path, delay=100)
    def check():
        if now[0] >= .1:
            raise BridgeError("SESSION_STOPPED", "stop")
    backend.preparation_check = check
    with pytest.raises(BridgeError, match="stop"):
        backend.prepare()
    assert now[0] < .2 and len(focuses) == 1 and not rearmed


def test_target_closing_during_focus_prepare_is_not_retried(tmp_path, monkeypatch):
    backend, now, focuses, _, rearmed = fake_window_backend(monkeypatch, tmp_path, delay=100)
    original = backend.windows.get_window
    backend.windows.get_window = lambda handle: None if now[0] >= .1 else original(handle)
    with pytest.raises(BridgeError) as error:
        backend.prepare()
    assert error.value.code == "TARGET_CLOSED"
    assert len(focuses) == 1 and not rearmed
