from src.ui.key_input_dialog import build_key_combo, format_key_combo, normalize_key_name


def test_key_input_dialog_captures_shift_arrow_combo():
    keys = build_key_combo("Up", 0x0001)

    assert keys == ["shift", "up"]
    assert format_key_combo(keys) == "SHIFT + UP"


def test_key_input_dialog_captures_multi_modifier_combo():
    keys = build_key_combo("Delete", 0x0004 | 0x0008 | 0x0001)

    assert keys == ["ctrl", "alt", "shift", "delete"]
    assert format_key_combo(keys) == "CTRL + ALT + SHIFT + DELETE"


def test_key_input_dialog_keeps_single_modifier_and_special_key_names():
    assert build_key_combo("Shift_L", 0) == ["shift"]
    assert build_key_combo("Next", 0) == ["pagedown"]
    assert normalize_key_name("Return") == "enter"


def test_key_input_dialog_uses_active_modifiers_over_stale_tk_state():
    keys = build_key_combo("Up", 0x0001 | 0x0008, active_modifiers={"shift"})

    assert keys == ["shift", "up"]
