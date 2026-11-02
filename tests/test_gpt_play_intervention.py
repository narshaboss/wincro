"""Simulated operator intervention. No real hotkeys, focus changes or device I/O."""

import ctypes
from pathlib import Path
from types import SimpleNamespace
import threading
from unittest.mock import Mock

import pytest

from src.agent_bridge.contracts import BridgeError
from src.agent_bridge.storage import read_json
from src.ai_play.jobs import PlayJobs
from src.ai_play.session import PlaySession
from src.ai_play.key_policy import automatic_keys, reserved_keys
from tests.test_gpt_play import Backend, FakeHost, frame, isolated_admission_registry, released


def test_automatic_keys_exclude_current_stop_and_all_wincro_hotkeys(monkeypatch):
    import src.utils.config as config
    monkeypatch.setattr(config, "get_config", lambda: SimpleNamespace(player=SimpleNamespace(emergency_stop_key="x")))
    keys = automatic_keys()
    assert {"up", "down", "ctrl", "shift", "alt", "a", "1", ";", "f1", "f6"} <= set(keys)
    assert not {"x", "win", "windows", "ctrl+c", "f7", "f8", "f9", "f10", "f11", "f12"} & set(keys)
    assert "x" in reserved_keys()


def test_dynamic_stop_key_change_blocks_previously_allowed_input(tmp_path, monkeypatch):
    import src.utils.config as config
    stop = ["escape"]
    monkeypatch.setattr(config, "get_config", lambda: SimpleNamespace(player=SimpleNamespace(emergency_stop_key=stop[0])))
    s = PlaySession(tmp_path, "clear", ["up"], Backend(), lambda: True)
    s.start()
    observation = frame(s)
    stop[0] = "up"
    with pytest.raises(BridgeError) as error:
        s.act(observation, "denied", {"kind": "key", "key": "up"}, "test")
    assert error.value.code == "KEY_DENIED" and not s.backend.sent


def test_resume_waits_for_inflight_input_release(tmp_path):
    backend = Backend()
    enter, finish = threading.Event(), threading.Event()
    def release():
        enter.set()
        assert finish.wait(2)
        return True
    backend.release = release
    s = PlaySession(tmp_path, "clear", ["up"], backend, lambda: True)
    s.start()
    s.pause()
    try:
        assert enter.wait(1)
        assert not s.input_released
        with pytest.raises(BridgeError) as error:
            s.resume()
        assert error.value.code == "INPUT_RELEASE_PENDING"
    finally:
        finish.set()
        released(s)
    s.resume()
    assert s.state == "exploring"


def test_intervention_cancels_old_turn_persists_command_and_reobserves(tmp_path, monkeypatch):
    from src.ai_play import jobs as module
    monkeypatch.setattr(module, "PlayHost", FakeHost)
    monkeypatch.setattr(module, "protect_directory", lambda p: Path(p).mkdir(parents=True, exist_ok=True))
    ready = threading.Event()
    prompts, observations = [], []
    class Actor:
        def __init__(self, *_):
            pass
        def cancel(self):
            pass
        def run(self, descriptor, prompt, cancel, progress):
            scope = FakeHost.hosts[descriptor].scope
            prompts.append(prompt)
            observation = scope.dispatch("play_observe", {})["frame_id"]
            observations.append(observation)
            if len(prompts) == 1:
                ready.set()
                assert cancel.wait(3)
            else:
                assert "NPC에 다시 말을 걸고 참가해" in prompt
                assert scope.session.latest != observations[0]
                from tests.test_gpt_play import review_live_memory
                review_live_memory(scope, observation)
                scope.dispatch("play_act", {"observation_id": observation, "request_id": "new-turn",
                    "action": {"kind": "key", "key": "up"}, "purpose": "new instruction"})
            return "end"
    manager = PlayJobs(tmp_path, tmp_path / "plans", tmp_path / "templates", lambda: True, runner_factory=Actor)
    backend = Backend()
    try:
        manager.start(None, "clear", ["up"], {}, test_environment=True, backend=backend)
        assert ready.wait(2)
        manager.interrupt()
        manager.worker.join(3)
        released(manager.session)
        assert manager.intervention_requested.is_set()
        assert manager.session.latest is None and not backend.sent
        manager.send_command("NPC에 다시 말을 걸고 참가해")
        manager.worker.join(3)
        assert len(prompts) == 2
        assert backend.sent == [{"kind": "key", "key": "up"}]
        persisted = read_json(manager.session.folder / "session.json")
        assert persisted["commands"][0]["command"] == "NPC에 다시 말을 걸고 참가해"
        assert manager.session.commands == persisted["commands"]
        assert not manager.intervention_requested.is_set()
        assert not (tmp_path / "plans").exists()
    finally:
        manager.stop()
        if manager.worker:
            manager.worker.join(3)
        if manager.session:
            manager._release_owner(manager.session)


def test_command_save_failure_rolls_back_without_claiming_success(tmp_path, monkeypatch):
    s = PlaySession(tmp_path, "clear", ["up"], Backend(), lambda: True)
    s.start()
    s.pause()
    released(s)
    s.reviews, s.trials = ["old-review"], ["old-trial"]
    def fail():
        raise OSError("disk full")
    monkeypatch.setattr(s, "_persist", fail)
    with pytest.raises(OSError, match="disk full"):
        s.add_command("new command")
    assert not s.commands and s.reviews == ["old-review"] and s.trials == ["old-trial"]
    assert not read_json(tmp_path / "session.json")["commands"]


def test_user_instruction_invalidates_prior_verification(tmp_path):
    s = PlaySession(tmp_path, "clear", ["up"], Backend(), lambda: True)
    s.start()
    s.pause()
    released(s)
    s.reviews, s.trials, s.trial = [1], [{"success": True}] * 3, {"state": "executed"}
    s.add_command("새 규칙으로 해")
    assert s.reviews == s.trials == [] and s.trial is None
    assert read_json(tmp_path / "session.json")["commands"][0]["command"] == "새 규칙으로 해"


def test_new_command_waits_for_retired_tool_request_even_after_model_exits(tmp_path):
    manager = PlayJobs(tmp_path, tmp_path / "plans", tmp_path / "templates", lambda: True)
    manager.session = PlaySession(tmp_path / "session", "clear", ["up"], Backend(), lambda: True)
    manager.session.start()
    manager.session.pause()
    released(manager.session)
    pending = [True]
    manager.retiring_host = SimpleNamespace(thread=SimpleNamespace(is_alive=lambda: pending[0]))
    assert manager.busy
    with pytest.raises(BridgeError) as error:
        manager.send_command("new instruction")
    assert error.value.code == "PLAY_BUSY"
    assert not manager.session.commands
    pending[0] = False
    assert not manager.busy


def test_failed_input_release_keeps_resume_blocked(tmp_path):
    backend = Backend()
    backend.release = lambda: False
    s = PlaySession(tmp_path, "clear", ["up"], backend, lambda: True)
    s.start()
    s.pause()
    for worker in s.release_workers:
        worker.join(2)
    assert not s.input_released
    with pytest.raises(BridgeError) as error:
        s.resume()
    assert error.value.code == "INPUT_RELEASE_PENDING"


def test_resume_save_failure_preserves_pause_and_does_not_start_model(tmp_path, monkeypatch):
    manager = PlayJobs(tmp_path, tmp_path / "plans", tmp_path / "templates", lambda: True)
    s = manager.session = PlaySession(tmp_path / "session", "clear", ["up"], Backend(), lambda: True)
    s.start()
    s.pause()
    released(s)
    previous = s.state, s.epoch, s.latest
    def fail():
        raise OSError("save failed")
    monkeypatch.setattr(s, "_persist", fail)
    with pytest.raises(OSError, match="save failed"):
        manager.resume()
    assert (s.state, s.epoch, s.latest) == previous
    assert manager.worker is None and not manager.busy and not manager.preparing.is_set()


def test_hotkey_registration_is_owned_polled_and_released(monkeypatch):
    from src.ai_play import controls
    events = [0x5C10, 0x5C11]
    def peek(message, *_):
        if not events:
            return 0
        message._obj.wParam = events.pop(0)
        return 1
    api = SimpleNamespace(RegisterHotKey=Mock(return_value=1), UnregisterHotKey=Mock(return_value=1),
                          PeekMessageW=Mock(side_effect=peek))
    monkeypatch.setattr(controls.ctypes, "WinDLL", lambda *_a, **_k: api)
    hotkeys = controls.PlayHotkeys()
    assert hotkeys.poll() == ["intervene", "pause"]
    assert not hotkeys.poll()
    hotkeys.close()
    hotkeys.close()
    assert api.RegisterHotKey.call_count == api.UnregisterHotKey.call_count == 2
