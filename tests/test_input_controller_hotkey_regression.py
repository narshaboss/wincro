import threading
import time
from types import SimpleNamespace

from src.utils import input_controller
from src.utils.arduino_hid import ArduinoHID
from src.utils.input_controller import InputController


def test_arduino_commands_are_serialized_across_threads(monkeypatch):
    class FakeSerial:
        def __init__(self):
            self.is_open = True
            self.timeout = 1.0
            self.active = 0
            self.max_active = 0
            self.guard = threading.Lock()

        def reset_input_buffer(self):
            with self.guard:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            time.sleep(0.02)

        def write(self, _data):
            return None

        def flush(self):
            return None

        def readline(self):
            time.sleep(0.01)
            with self.guard:
                self.active -= 1
            return b"OK\n"

    hid = ArduinoHID()
    fake_serial = FakeSerial()
    monkeypatch.setattr(hid, "_serial", fake_serial)
    barrier = threading.Barrier(3)
    results = []

    def send(command):
        barrier.wait()
        results.append(hid._send_command(command))

    workers = [
        threading.Thread(target=send, args=("MC,L",)),
        threading.Thread(target=send, args=("KA",)),
    ]
    for worker in workers:
        worker.start()
    barrier.wait()
    for worker in workers:
        worker.join(timeout=2.0)

    assert results == [True, True]
    assert fake_serial.max_active == 1


def test_arduino_disconnect_releases_hid_before_closing_serial(monkeypatch):
    events = []

    class FakeSerial:
        is_open = True
        timeout = 1.0

        def reset_input_buffer(self):
            events.append("reset")

        def write(self, data):
            events.append(("write", data))

        def flush(self):
            events.append("flush")

        def readline(self):
            return b"OK\n"

        def close(self):
            events.append("close")

    hid = ArduinoHID()
    monkeypatch.setattr(hid, "_serial", FakeSerial())
    monkeypatch.setattr(hid, "_connected", True)
    monkeypatch.setattr(hid, "_supports_mouse_move", True)
    monkeypatch.setattr(hid, "_supports_key_combo_tap", True)

    hid.disconnect()

    assert ("write", b"KA\n") in events
    assert events.index(("write", b"KA\n")) < events.index("close")
    assert hid._serial is None
    assert hid._connected is False


def test_arduino_disconnect_clears_state_even_when_serial_close_raises(monkeypatch):
    class BrokenCloseSerial:
        is_open = True
        timeout = 1.0

        def reset_input_buffer(self):
            pass

        def write(self, _data):
            pass

        def flush(self):
            pass

        def readline(self):
            return b"OK\n"

        def close(self):
            raise OSError("close failed")

    hid = ArduinoHID()
    monkeypatch.setattr(hid, "_serial", BrokenCloseSerial())
    monkeypatch.setattr(hid, "_connected", True)

    hid.disconnect()

    assert hid._serial is None
    assert hid._connected is False


def test_release_all_retries_arduino_and_always_releases_software_input(monkeypatch):
    controller = InputController()
    arduino_calls = []
    key_ups = []
    mouse_ups = []
    fake_arduino = SimpleNamespace(
        release_all=lambda: arduino_calls.append(True) or len(arduino_calls) >= 2,
    )

    monkeypatch.setattr(controller, "_use_arduino", lambda: True)
    monkeypatch.setattr(input_controller, "_get_arduino", lambda: fake_arduino)
    monkeypatch.setattr(input_controller.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        input_controller.pyautogui,
        "keyUp",
        lambda key, **_kwargs: key_ups.append(key),
    )
    monkeypatch.setattr(
        input_controller.pyautogui,
        "mouseUp",
        lambda button="left", **_kwargs: mouse_ups.append(button),
    )

    assert controller.release_all() is True
    assert len(arduino_calls) == 2
    assert {"left", "right", "middle"}.issubset(mouse_ups)
    assert {"shift", "ctrl", "alt", "win"}.issubset(key_ups)


def test_release_all_uses_open_arduino_session_even_when_setting_is_disabled(monkeypatch):
    controller = InputController()
    arduino_calls = []
    fake_arduino = SimpleNamespace(
        has_open_session=lambda: True,
        release_all=lambda: arduino_calls.append(True) or True,
    )

    monkeypatch.setattr(controller, "_use_arduino", lambda: False)
    monkeypatch.setattr(input_controller, "_get_arduino", lambda: fake_arduino)
    monkeypatch.setattr(input_controller.pyautogui, "keyUp", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(input_controller.pyautogui, "mouseUp", lambda **_kwargs: None)

    assert controller.release_all() is True
    assert arduino_calls == [True]


def test_release_all_includes_arbitrary_tracked_keys_and_buttons(monkeypatch):
    controller = InputController()
    key_ups = []
    mouse_ups = []

    monkeypatch.setattr(controller, "_use_arduino", lambda: False)
    monkeypatch.setattr(controller, "_strict_mode", lambda: False)
    monkeypatch.setattr(input_controller.pyautogui, "keyDown", lambda _key: None)
    monkeypatch.setattr(input_controller.pyautogui, "mouseDown", lambda button="left": None)
    monkeypatch.setattr(
        input_controller.pyautogui,
        "keyUp",
        lambda key, **_kwargs: key_ups.append(key),
    )
    monkeypatch.setattr(
        input_controller.pyautogui,
        "mouseUp",
        lambda button="left", **_kwargs: mouse_ups.append(button),
    )

    assert controller.key_down("z") is True
    assert controller.mouse_down("x1") is True
    assert controller.release_all() is True

    assert "z" in key_ups
    assert "x1" in mouse_ups
    assert controller._pressed_keys == set()
    assert controller._pressed_buttons == set()


def test_arduino_drag_release_failure_forces_emergency_release(monkeypatch):
    controller = InputController()
    calls = []
    fake_arduino = SimpleNamespace(
        supports_mouse_move=lambda: True,
        mouse_press=lambda button: calls.append(("press", button)) or True,
        mouse_release=lambda button: calls.append(("release", button)) or False,
        release_all=lambda: calls.append(("release_all", None)) or True,
    )

    monkeypatch.setattr(controller, "_use_arduino", lambda: True)
    monkeypatch.setattr(controller, "_strict_mode", lambda: True)
    monkeypatch.setattr(controller, "_arduino_move_to", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(input_controller, "_get_arduino", lambda: fake_arduino)
    monkeypatch.setattr(input_controller.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(input_controller.pyautogui, "mouseUp", lambda **_kwargs: calls.append(("software_up", None)))

    assert controller.drag(1, 2, 30, 40, duration=0.2) is False
    assert calls.count(("release", "left")) >= 2
    assert ("release_all", None) in calls
    assert ("software_up", None) in calls


def test_hotkey_delegates_modified_arrow_to_instant_tap(monkeypatch):
    controller = InputController()
    calls = []

    monkeypatch.setattr(controller, "tap_combo_once", lambda *keys: calls.append(keys) or True)

    assert controller.hotkey("shift", "up") is True
    assert calls == [("shift", "up")]


def test_hotkey_single_key_delegates_to_press(monkeypatch):
    controller = InputController()
    pressed = []

    monkeypatch.setattr(controller, "press", lambda key: pressed.append(key) or True)

    assert controller.hotkey("up") is True
    assert pressed == ["up"]


def test_press_single_key_uses_explicit_down_up_tap(monkeypatch):
    controller = InputController()
    events = []
    sleeps = []

    monkeypatch.setattr(controller, "_use_arduino", lambda: False)
    monkeypatch.setattr(controller, "_strict_mode", lambda: False)
    monkeypatch.setattr(input_controller.pyautogui, "PAUSE", 0.25)
    monkeypatch.setattr(input_controller.pyautogui, "keyDown", lambda key: events.append(("down", key)))
    monkeypatch.setattr(input_controller.pyautogui, "keyUp", lambda key: events.append(("up", key)))
    monkeypatch.setattr(input_controller.time, "sleep", lambda seconds: sleeps.append(seconds))

    assert controller.press("enter") is True
    assert events == [("down", "enter"), ("up", "enter")]
    assert input_controller._KEY_TAP_HOLD_DELAY in sleeps
    assert input_controller._KEY_TAP_POST_DELAY in sleeps
    assert input_controller.pyautogui.PAUSE == 0.25


def test_press_plus_separated_combo_delegates_to_hotkey(monkeypatch):
    controller = InputController()
    calls = []

    monkeypatch.setattr(controller, "hotkey", lambda *keys: calls.append(keys) or True)

    assert controller.press("shift+up") is True
    assert controller.press(" shift + up ") is True
    assert calls == [("shift", "up"), ("shift", "up")]


def test_tap_combo_once_uses_arduino_combo_command(monkeypatch):
    controller = InputController()
    calls = []
    fake_arduino = SimpleNamespace(
        supports_key_combo_tap=lambda: True,
        key_combo_tap=lambda *keys: calls.append(keys) or True,
    )

    monkeypatch.setattr(controller, "_use_arduino", lambda: True)
    monkeypatch.setattr(input_controller, "_get_arduino", lambda: fake_arduino)

    assert controller.tap_combo_once("shift", "up") is True
    assert calls == [("shift", "up")]


def test_tap_combo_once_software_path_releases_primary_and_modifier_immediately(monkeypatch):
    controller = InputController()
    events = []
    sleeps = []

    monkeypatch.setattr(controller, "_use_arduino", lambda: False)
    monkeypatch.setattr(controller, "_strict_mode", lambda: False)
    monkeypatch.setattr(input_controller.pyautogui, "PAUSE", 0.3)
    monkeypatch.setattr(input_controller.pyautogui, "keyDown", lambda key: events.append(("down", key)))
    monkeypatch.setattr(input_controller.pyautogui, "keyUp", lambda key: events.append(("up", key)))
    monkeypatch.setattr(input_controller.time, "sleep", lambda seconds: sleeps.append(seconds))

    assert controller.tap_combo_once("shift", "up") is True
    assert events == [
        ("up", "up"),
        ("up", "shift"),
        ("down", "shift"),
        ("down", "up"),
        ("up", "up"),
        ("up", "shift"),
    ]
    assert input_controller._COMBO_MODIFIER_SETTLE_DELAY in sleeps
    assert input_controller._COMBO_PRIMARY_TAP_DELAY in sleeps
    assert input_controller._COMBO_POST_RELEASE_DELAY in sleeps
    assert input_controller.pyautogui.PAUSE == 0.3


def test_tap_combo_once_software_path_releases_modifier_when_primary_fails(monkeypatch):
    controller = InputController()
    events = []

    def fake_key_down(key):
        events.append(("down", key))
        if key == "up":
            raise RuntimeError("primary failed")

    monkeypatch.setattr(controller, "_use_arduino", lambda: False)
    monkeypatch.setattr(controller, "_strict_mode", lambda: False)
    monkeypatch.setattr(input_controller.pyautogui, "keyDown", fake_key_down)
    monkeypatch.setattr(input_controller.pyautogui, "keyUp", lambda key: events.append(("up", key)))
    monkeypatch.setattr(input_controller.time, "sleep", lambda seconds: None)

    assert controller.tap_combo_once("shift", "up") is False
    assert events == [
        ("up", "up"),
        ("up", "shift"),
        ("down", "shift"),
        ("down", "up"),
        ("up", "up"),
        ("up", "shift"),
    ]


def test_hotkey_short_taps_modified_arrow_without_hold(monkeypatch):
    controller = InputController()
    sleeps = []

    monkeypatch.setattr(input_controller.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(controller, "tap_combo_once", lambda *keys: True)

    assert controller.hotkey("shift", "up") is True
    assert sleeps == []
    assert input_controller._HOTKEY_HOLD_DELAY not in sleeps


def test_hotkey_keeps_hold_for_non_direction_combo(monkeypatch):
    controller = InputController()
    sleeps = []

    monkeypatch.setattr(input_controller.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(controller, "key_down", lambda key: True)
    monkeypatch.setattr(controller, "key_up", lambda key: True)

    assert controller.hotkey("ctrl", "v") is True
    assert input_controller._HOTKEY_HOLD_DELAY in sleeps


def test_arduino_combo_tap_uses_single_firmware_command(monkeypatch):
    hid = ArduinoHID()
    sent = []

    hid._supports_key_combo_tap = True
    monkeypatch.setattr(hid, "_send_command", lambda cmd, wait_response=True: sent.append((cmd, wait_response)) or True)

    assert hid.key_combo_tap("shift", "up") is True
    assert sent == [("KQ,129,218", True)]


def test_arduino_combo_command_failure_forces_release_all(monkeypatch):
    hid = ArduinoHID()
    releases = []
    hid._supports_key_combo_tap = True
    monkeypatch.setattr(hid, "_send_command", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(hid, "release_all", lambda: releases.append(True) or True)

    assert hid.key_combo_tap("shift", "up") is False
    assert releases == [True]


def test_arduino_hotkey_releases_every_pressed_key(monkeypatch):
    hid = ArduinoHID()
    events = []
    monkeypatch.setattr(hid, "key_press", lambda key: events.append(("down", key)) or True)
    monkeypatch.setattr(hid, "key_release", lambda key: events.append(("up", key)) or True)
    monkeypatch.setattr("src.utils.arduino_hid.time.sleep", lambda _seconds: None)

    assert hid.hotkey("ctrl", "shift", "a") is True
    assert events == [
        ("down", "ctrl"),
        ("down", "shift"),
        ("down", "a"),
        ("up", "a"),
        ("up", "shift"),
        ("up", "ctrl"),
    ]


def test_arduino_key_tap_does_not_hide_press_failure(monkeypatch):
    hid = ArduinoHID()
    releases = []

    monkeypatch.setattr(hid, "key_press", lambda key: False)
    monkeypatch.setattr(hid, "key_release", lambda key: (_ for _ in ()).throw(AssertionError("release used")))
    monkeypatch.setattr(hid, "release_all", lambda: releases.append(True) or True)

    assert hid.key_tap("enter") is False
    assert releases == [True]


def test_arduino_key_tap_release_failure_forces_release_all(monkeypatch):
    hid = ArduinoHID()
    calls = []

    monkeypatch.setattr(hid, "key_press", lambda key: calls.append(("press", key)) or True)
    monkeypatch.setattr(hid, "key_release", lambda key: calls.append(("release", key)) or False)
    monkeypatch.setattr(hid, "release_all", lambda: calls.append(("release_all", None)) or True)
    monkeypatch.setattr("src.utils.arduino_hid.time.sleep", lambda _seconds: None)

    assert hid.key_tap("enter") is False
    assert calls == [
        ("press", "enter"),
        ("release", "enter"),
        ("release_all", None),
    ]


def test_hotkey_release_failure_forces_release_all(monkeypatch):
    controller = InputController()
    calls = []

    monkeypatch.setattr(controller, "key_down", lambda key: calls.append(("down", key)) or True)
    monkeypatch.setattr(
        controller,
        "key_up",
        lambda key: calls.append(("up", key)) or key != "ctrl",
    )
    monkeypatch.setattr(controller, "release_all", lambda: calls.append(("release_all", None)) or True)
    monkeypatch.setattr(input_controller.time, "sleep", lambda _seconds: None)

    assert controller.hotkey("ctrl", "a") is False
    assert calls[-1] == ("release_all", None)


def test_replay_key_events_preserves_down_up_order_and_delay(monkeypatch):
    controller = InputController()
    events = []
    sleeps = []

    monkeypatch.setattr(controller, "key_down", lambda key: events.append(("down", key)) or True)
    monkeypatch.setattr(controller, "key_up", lambda key: events.append(("up", key)) or True)
    monkeypatch.setattr(input_controller.time, "sleep", lambda seconds: sleeps.append(seconds))

    assert controller.replay_key_events([
        {"event": "down", "key": "shift", "delay": 0.0},
        {"event": "down", "key": "a", "delay": 0.011},
        {"event": "up", "key": "a", "delay": 0.006},
        {"event": "up", "key": "shift", "delay": 0.004},
    ]) is True
    assert events == [("down", "shift"), ("down", "a"), ("up", "a"), ("up", "shift")]
    assert sleeps == [0.011, 0.006, 0.004]


def test_replay_key_events_uses_atomic_tap_for_recorded_modifier_direction(monkeypatch):
    controller = InputController()
    calls = []

    monkeypatch.setattr(controller, "tap_combo_once", lambda *keys: calls.append(keys) or True)
    monkeypatch.setattr(controller, "key_down", lambda key: (_ for _ in ()).throw(AssertionError("raw key_down used")))
    monkeypatch.setattr(controller, "key_up", lambda key: (_ for _ in ()).throw(AssertionError("raw key_up used")))

    assert controller.replay_key_events([
        {"event": "down", "key": "shift", "delay": 0.0},
        {"event": "down", "key": "up", "delay": 0.4801},
        {"event": "up", "key": "up", "delay": 0.1497},
        {"event": "up", "key": "shift", "delay": 0.2257},
    ]) is True
    assert calls == [("shift", "up")]


def test_replay_key_events_releases_pressed_keys_on_failure(monkeypatch):
    controller = InputController()
    events = []

    def fake_key_down(key):
        events.append(("down", key))
        return key != "up"

    monkeypatch.setattr(controller, "key_down", fake_key_down)
    monkeypatch.setattr(controller, "key_up", lambda key: events.append(("up", key)) or True)
    monkeypatch.setattr(input_controller.time, "sleep", lambda seconds: None)

    assert controller.replay_key_events([
        {"event": "down", "key": "shift", "delay": 0.0},
        {"event": "down", "key": "up", "delay": 0.0},
    ]) is False
    assert events == [("down", "shift"), ("down", "up"), ("up", "shift")]


def test_arduino_combo_tap_old_firmware_uses_guarded_raw_fallback(monkeypatch):
    hid = ArduinoHID()
    sent = []

    hid._supports_key_combo_tap = False
    monkeypatch.setattr(hid, "_send_command", lambda cmd, wait_response=True: sent.append((cmd, wait_response)) or True)
    monkeypatch.setattr("src.utils.arduino_hid.time.sleep", lambda seconds: None)

    assert hid.key_combo_tap("shift", "up") is True
    assert sent == [
        ("KA", True),
        ("KP,129", True),
        ("KP,218", True),
        ("KR,218", True),
        ("KA", True),
    ]


def test_arduino_combo_tap_aborts_raw_fallback_when_initial_release_fails(monkeypatch):
    hid = ArduinoHID()
    sent = []

    hid._supports_key_combo_tap = False

    def send(command, wait_response=True):
        sent.append((command, wait_response))
        return False if command == "KA" else True

    monkeypatch.setattr(hid, "_send_command", send)
    monkeypatch.setattr("src.utils.arduino_hid.time.sleep", lambda _seconds: None)

    assert hid.key_combo_tap("shift", "up") is False
    assert all(not command.startswith("KP,") for command, _wait in sent)


def test_firmware_combo_tap_uses_release_all_and_stable_timing():
    from pathlib import Path

    for firmware_path in (
        Path("arduino/wincro_hid.ino"),
        Path("arduino/wincro_hid/wincro_hid.ino"),
    ):
        source = firmware_path.read_text(encoding="utf-8", errors="ignore")
        start = source.index('if (cmd.startsWith("KQ,"))')
        end = source.index('if (cmd.startsWith("KT,")', start) if 'if (cmd.startsWith("KT,")' in source[start:] else source.index('if (cmd.startsWith("KD,")', start)
        body = source[start:end]

        assert "Keyboard.releaseAll();" in body
        assert "delay(16);" in body
        assert "delay(6);" in body


def test_bundled_upload_firmware_uses_app_baud_rate():
    from pathlib import Path

    source = Path("arduino/wincro_hid/wincro_hid.ino").read_text(encoding="utf-8", errors="ignore")

    assert "Serial.begin(115200);" in source
    assert "Serial.begin(9600);" not in source


def test_arduino_connect_refreshes_old_firmware_without_kq(monkeypatch):
    import src.utils.arduino_hid as arduino_hid_module
    import src.utils.arduino_uploader as uploader_module

    class FakeSerial:
        instances = []

        def __init__(self, *args, **kwargs):
            self.index = len(self.instances)
            self.commands = []
            self.is_open = True
            self.dtr = True
            self.instances.append(self)

        def reset_input_buffer(self):
            pass

        def write(self, data):
            self.commands.append(data.decode().strip())

        def flush(self):
            pass

        def readline(self):
            cmd = self.commands[-1] if self.commands else ""
            if cmd == "PING":
                return b"PONG\n"
            if cmd == "MM,0,0":
                return b"OK\n"
            if cmd == "KQ":
                return b"ERR:UNKNOWN_CMD\n" if self.index == 0 else b"OK\n"
            return b"OK\n"

        def close(self):
            self.is_open = False

    upload_calls = []

    monkeypatch.setattr(arduino_hid_module.serial, "Serial", FakeSerial)
    monkeypatch.setattr(arduino_hid_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        uploader_module,
        "upload_firmware",
        lambda port: upload_calls.append(port) or (True, "uploaded"),
    )

    hid = ArduinoHID()
    hid.disconnect()

    assert hid.connect(port="COM7", baud_rate=115200) is True
    assert upload_calls == ["COM7"]
    assert len(FakeSerial.instances) == 2
    assert hid.supports_key_combo_tap() is True

    hid.disconnect()


def test_arduino_connect_never_reports_success_after_failed_refresh_and_reconnect(monkeypatch):
    hid = ArduinoHID()
    hid.disconnect()
    open_calls = []

    class FakeSerial:
        is_open = True

    def open_session(_port, _baud_rate):
        open_calls.append(True)
        if len(open_calls) > 1:
            return False
        hid._serial = FakeSerial()
        return True

    def initialize(_port, _baud_rate):
        hid._connected = True
        hid._supports_key_combo_tap = False
        return True

    def failed_refresh(_port, _baud_rate, _reason):
        hid._serial = None
        hid._connected = False
        return False

    monkeypatch.setattr(hid, "_open_serial_session", open_session)
    monkeypatch.setattr(hid, "_initialize_connected_session", initialize)
    monkeypatch.setattr(hid, "_auto_refresh_firmware", failed_refresh)

    assert hid.connect(port="COM9", baud_rate=115200) is False
    assert len(open_calls) == 2
    assert hid.is_connected is False
