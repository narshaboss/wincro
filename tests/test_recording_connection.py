"""Connection state and list removal simulations; no operator input or network."""

from pathlib import Path
import threading
import time

import pytest

from src.agent_bridge import recording_connection as module
from src.agent_bridge.recording_connection import ConnectionScope, RecordingConnection
from src.agent_bridge.recording_catalog import RecordingVisibility
from src.agent_bridge.contracts import BridgeError
from src.agent_bridge.storage import write_json


class Host:
    all = {}
    def __init__(self, scope, folder):
        self.scope = scope
        self.client_connected = False
    def start(self):
        self.key = str(id(self))
        self.all[self.key] = self
        return self.key
    def close(self):
        self.client_connected = False


def wait(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(.01)
    assert predicate()


@pytest.fixture
def controller(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "LocalHost", Host)
    monkeypatch.setattr(module, "protect_directory", lambda folder: Path(folder).mkdir(parents=True, exist_ok=True))
    result = RecordingConnection(tmp_path / "connections")
    yield result
    result.close()
    if result.worker:
        result.worker.join(3)


def test_connection_scope_cannot_read_any_recording_or_plan():
    scope = ConnectionScope()
    for method in ("list_plans", "list_recordings", "get_frames", "stage_edits", "publish_new_plan"):
        with pytest.raises(BridgeError, match="cannot read"):
            scope.dispatch(method, {})


def test_on_requires_both_valid_handshake_and_live_transport_and_off_revokes_immediately(controller):
    handshake, release = threading.Event(), threading.Event()
    class Runner:
        def __init__(self, *_):
            pass
        def connect(self, descriptor, cancelled, ready):
            host = Host.all[descriptor]
            ready({"instance_id": host.scope.instance_id})
            handshake.set()
            release.wait(2)
            if not cancelled.is_set():
                host.client_connected = True
            cancelled.wait(3)
        def cancel(self):
            release.set()
    controller.runner_factory = Runner
    assert controller.snapshot()["state"] == "off"
    controller.enable({})
    assert handshake.wait(2)
    assert not controller.connected
    release.set()
    wait(lambda: controller.connected)
    assert controller.snapshot()["state"] == "on"
    controller.disable()
    assert not controller.connected
    wait(lambda: not controller.busy)
    assert controller.snapshot()["state"] == "off"


def test_cancelled_connection_cannot_become_on_after_a_late_callback(controller):
    started, release = threading.Event(), threading.Event()
    class Runner:
        def __init__(self, *_):
            pass
        def connect(self, descriptor, cancelled, ready):
            host = Host.all[descriptor]
            started.set()
            release.wait(2)
            ready({"instance_id": host.scope.instance_id})
            host.client_connected = True
        def cancel(self):
            release.set()
    controller.runner_factory = Runner
    controller.enable({})
    assert started.wait(2)
    controller.disable()
    release.set()
    wait(lambda: not controller.busy)
    assert not controller.connected and not controller.handshake
    assert controller.snapshot()["state"] == "off"
    controller.close()
    with pytest.raises(BridgeError):
        controller.enable({})


def test_connection_failure_does_not_look_connected_or_read_media(controller):
    class Runner:
        def __init__(self, *_):
            pass
        def connect(self, *_):
            raise BridgeError("LOGIN_REQUIRED", "login first")
        def cancel(self):
            pass
    controller.runner_factory = Runner
    controller.enable({})
    wait(lambda: not controller.busy)
    assert controller.snapshot()["state"] == "error"
    assert "LOGIN_REQUIRED" in controller.snapshot()["message"]
    assert not controller.connected
    assert not list(controller.root.rglob("*.mp4"))


def test_list_removal_survives_reload_and_restores_without_touching_source_or_plans(tmp_path):
    video, plan, log = [tmp_path / name for name in ("video.mp4", "plan.json", "keys.json")]
    for path in (video, plan, log):
        path.write_bytes(b"unchanged fixture")
    expected = {path: path.read_bytes() for path in (video, plan, log)}
    file = tmp_path / "settings" / "hidden.json"
    visibility = RecordingVisibility(file)
    assert not file.exists()
    visibility.set_hidden(video, True)
    restored = RecordingVisibility(file)
    assert str(video.resolve()).casefold() in restored.hidden()
    restored.set_hidden(video, False)
    assert not RecordingVisibility(file).hidden()
    assert all(path.read_bytes() == value for path, value in expected.items())


def test_corrupt_removal_manifest_is_preserved_not_reset_or_overwritten(tmp_path):
    file = tmp_path / "hidden.json"
    write_json(file, {"hidden": [None]})
    original = file.read_bytes()
    with pytest.raises(ValueError):
        RecordingVisibility(file).set_hidden(tmp_path / "video.mp4", True)
    assert file.read_bytes() == original
