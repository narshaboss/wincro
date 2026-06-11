from pathlib import Path


def test_action_player_key_press_multi_key_uses_hotkey():
    source = Path("src/player/action_player.py").read_text(encoding="utf-8", errors="ignore")
    start = source.index("elif action_type == ActionType.KEY_PRESS.value:")
    end = source.index("elif action_type == ActionType.WAIT.value:", start)
    body = source[start:end]

    assert "input_ctrl.hotkey(*keys)" in body
    assert "input_ctrl.press(key.lower())" not in body


def test_rule_executor_key_press_multi_key_uses_hotkey():
    source = Path("src/player/rule_executor.py").read_text(encoding="utf-8", errors="ignore")
    start = source.index('elif action_type == "key_press":')
    end = source.index('elif action_type == "scroll":', start)
    body = source[start:end]

    assert "input_ctrl.hotkey(*keys)" in body
    assert "input_ctrl.press(key.lower())" not in body
