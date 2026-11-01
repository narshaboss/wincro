"""Fake serial/pointer simulations. Never open a COM port or send host input."""

import ast
from pathlib import Path
from types import SimpleNamespace
import threading

import pytest

from src.agent_bridge.contracts import BridgeError
from tests.test_gpt_play import isolated_admission_registry


def pointer(monkeypatch, *, gain=1.0, stationary=False, ack=True):
    from src.utils import input_controller as ic, arduino_hid
    position, calls, clicks = [0.0, 0.0], [], []
    def move(dx, dy):
        calls.append((dx, dy))
        if not stationary:
            position[0] += dx * gain
            position[1] += dy * gain
        return ack
    def forbidden(*args, **kwargs):
        pytest.fail("Reopen/reconnect/firmware/software input forbidden")
    device = SimpleNamespace(is_connected=True, supports_mouse_move=lambda: True,
        mouse_move=move, mouse_click=lambda key: clicks.append(key) or ack,
        connect=forbidden, disconnect=forbidden)
    monkeypatch.setattr(ic, "get_config", lambda: SimpleNamespace(arduino=SimpleNamespace(enabled=True)))
    monkeypatch.setattr(arduino_hid, "get_arduino_hid", lambda: device)
    monkeypatch.setattr(arduino_hid.serial, "Serial", forbidden)
    monkeypatch.setattr(ic, "_get_arduino", forbidden)
    monkeypatch.setattr(ic, "is_automation_input_blocked", lambda: False)
    monkeypatch.setattr(ic.time, "sleep", lambda _: None)
    monkeypatch.setattr(ic.pyautogui, "moveTo", forbidden)
    monkeypatch.setattr(ic.pyautogui, "click", forbidden)
    controller = ic.InputController()
    monkeypatch.setattr(controller, "_get_cursor_pos", lambda: tuple(round(x) for x in position))
    return controller, position, calls, clicks


@pytest.mark.parametrize("gain", [.5, 1.0, 1.5, 2.0, 3.0, 4.0])
@pytest.mark.parametrize("target", [(1279, 480), (-110, 40)])
def test_pointer_acceleration_converges_without_reopening_or_clicking(monkeypatch, gain, target):
    controller, position, calls, clicks = pointer(monkeypatch, gain=gain)
    assert controller.play_lab_move_to(*target, lambda: None)
    assert max(abs(round(position[i]) - target[i]) for i in (0, 1)) <= 3
    assert len(calls) < 80 and not clicks


def test_previous_pixel_delta_algorithm_reproduces_acceleration_oscillation():
    # Acknowledged HID movement is not equivalent to the requested screen delta.
    position, target = 0, 50
    for _ in range(80):
        error = target - position
        if abs(error) <= 3:
            break
        position += 2 * max(-64, min(64, error))
    assert abs(target - position) > 3


@pytest.mark.parametrize("stationary,ack,code", [(True, True, "CURSOR_NOT_MOVING"),
    (False, False, "ARDUINO_MOVE_UNCONFIRMED")])
def test_pointer_failure_is_not_mislabelled_as_disconnected(monkeypatch, stationary, ack, code):
    controller, _, calls, clicks = pointer(monkeypatch, stationary=stationary, ack=ack)
    with pytest.raises(BridgeError) as error:
        controller.play_lab_move_to(1279, 480, lambda: None)
    assert error.value.code == code
    assert "클릭은 보내지 않았습니다" in str(error.value)
    assert calls and not clicks


def test_click_ack_failure_is_distinct_from_pointer_failure(monkeypatch):
    controller, _, _, clicks = pointer(monkeypatch, ack=False)
    with pytest.raises(BridgeError) as error:
        controller.play_lab_click()
    assert error.value.code == "CLICK_DELIVERY_UNKNOWN"
    assert clicks == ["left"]


def test_existing_serial_connection_is_reused_without_any_hardware_io(monkeypatch):
    from src.utils.arduino_hid import ArduinoHID
    import src.utils.arduino_hid as module
    hid = object.__new__(ArduinoHID)
    hid.__init__()
    hid._connected = True
    hid._serial = SimpleNamespace(is_open=True)
    hid._port, hid._baud_rate = "COM7", 115200
    hid._supports_mouse_move = hid._supports_key_combo_tap = True
    monkeypatch.setattr(module, "get_config", lambda: SimpleNamespace(arduino=None))
    def forbidden(*_args, **_kwargs):
        pytest.fail("Existing port must not be reopened, reset, pinged or reflashed")
    monkeypatch.setattr(hid, "disconnect", forbidden)
    monkeypatch.setattr(hid, "_open_serial_session", forbidden)
    monkeypatch.setattr(hid, "_send_command", forbidden)
    results = []
    workers = [threading.Thread(target=lambda: results.append(hid.connect("com7", 115200, reuse_existing=True))) for _ in range(4)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(2)
    assert results == [True] * 4
    assert hid.connection_status() == {"connected": True, "open": True, "port": "COM7", "baud_rate": 115200,
                                       "mouse_move": True, "key_combo_tap": True}


def test_settings_connection_uses_single_owner_and_shows_save_failure(monkeypatch):
    import src.utils.arduino_hid as module
    from src.ai_play import ownership
    monkeypatch.setattr(ownership, "device_change_allowed", lambda: True)
    serial = object()
    calls, statuses = [], []
    state = {"connected": True, "open": True, "port": "COM7", "baud_rate": 115200,
             "key_combo_tap": True, "mouse_move": True}
    device = SimpleNamespace(connection_status=lambda: dict(state), _serial=serial,
        connect=lambda port, baud, **kw: calls.append((port, baud, kw)) or True)
    monkeypatch.setattr(module, "get_arduino_hid", lambda: device)
    monkeypatch.setattr(module.serial, "Serial", lambda *_a, **_k: pytest.fail("Second port open forbidden"))
    source = ast.parse(Path("src/ui/settings_view.py").read_text(encoding="utf-8-sig"))
    cls = next(node for node in source.body if isinstance(node, ast.ClassDef) and node.name == "SettingsView")
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "_connect_arduino_thread")
    config = SimpleNamespace(arduino=SimpleNamespace(com_port="", baud_rate=0))
    namespace = {"__package__": "src.ui", "get_config": lambda: config, "save_config": lambda: False,
                 "logger": __import__("logging").getLogger("test.settings")}
    exec(compile(ast.Module(body=[method], type_ignores=[]), "settings-connection-fixture", "exec"), namespace)
    view = SimpleNamespace(_post_ui=lambda fn: fn(), _update_arduino_status=lambda *args: statuses.append(args))
    namespace[method.name](view, "COM7", 115200)
    assert calls == [("COM7", 115200, {"reuse_existing": True})]
    assert view._arduino_serial is serial
    assert statuses[-1][0] is True and "설정 저장 실패" in statuses[-1][1]


def test_explicit_device_recovery_allowed_after_stopped_lab_but_not_running(tmp_path):
    from src.ai_play import ownership
    from src.ai_play.jobs import PlayJobs
    from src.ai_play.session import PlaySession
    from tests.test_gpt_play import Backend
    manager = PlayJobs(tmp_path, tmp_path / "plans", tmp_path / "templates", lambda: True)
    manager.session = PlaySession(tmp_path / "session", "clear", ["up"], Backend(), lambda: True)
    ownership.acquire(manager)
    try:
        assert not ownership.device_change_allowed()
        manager.session.state = "stopped"
        assert ownership.device_change_allowed()
        with manager.session.io_lock:
            assert not ownership.device_change_allowed()
    finally:
        ownership.release(manager)
