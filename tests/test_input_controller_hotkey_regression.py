from src.utils import input_controller
from src.utils.input_controller import InputController


def test_hotkey_holds_shift_until_arrow_is_released(monkeypatch):
    controller = InputController()
    events = []

    monkeypatch.setattr(input_controller.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(controller, "key_down", lambda key: events.append(("down", key)) or True)
    monkeypatch.setattr(controller, "key_up", lambda key: events.append(("up", key)) or True)

    assert controller.hotkey("shift", "up") is True
    assert events == [
        ("down", "shift"),
        ("down", "up"),
        ("up", "up"),
        ("up", "shift"),
    ]


def test_hotkey_releases_pressed_modifier_when_primary_key_fails(monkeypatch):
    controller = InputController()
    events = []

    def fake_key_down(key):
        events.append(("down", key))
        return key != "up"

    monkeypatch.setattr(input_controller.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(controller, "key_down", fake_key_down)
    monkeypatch.setattr(controller, "key_up", lambda key: events.append(("up", key)) or True)

    assert controller.hotkey("shift", "up") is False
    assert events == [
        ("down", "shift"),
        ("down", "up"),
        ("up", "shift"),
    ]


def test_hotkey_single_key_delegates_to_press(monkeypatch):
    controller = InputController()
    pressed = []

    monkeypatch.setattr(controller, "press", lambda key: pressed.append(key) or True)

    assert controller.hotkey("up") is True
    assert pressed == ["up"]
