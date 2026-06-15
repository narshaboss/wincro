from types import SimpleNamespace

from src.utils import input_controller
from src.utils.arduino_hid import ArduinoHID
from src.utils.input_controller import InputController


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
