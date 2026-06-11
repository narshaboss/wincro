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


def test_action_player_recorded_key_failure_is_not_reported_success():
    source = Path("src/player/action_player.py").read_text(encoding="utf-8", errors="ignore")
    hotkey_start = source.index("elif action_type == ActionType.HOTKEY.value:")
    key_press_start = source.index("elif action_type == ActionType.KEY_PRESS.value:")
    wait_start = source.index("elif action_type == ActionType.WAIT.value:", key_press_start)

    assert "if not input_ctrl.replay_key_events" in source[hotkey_start:key_press_start]
    assert "기록 키 실행 실패" in source[hotkey_start:key_press_start]
    assert "if not input_ctrl.replay_key_events" in source[key_press_start:wait_start]
    assert "기록 키 입력 실패" in source[key_press_start:wait_start]


def test_rule_executor_recorded_key_failure_is_not_reported_success():
    source = Path("src/player/rule_executor.py").read_text(encoding="utf-8", errors="ignore")
    hotkey_start = source.index('elif action_type == "hotkey":')
    key_press_start = source.index('elif action_type == "key_press":')
    scroll_start = source.index('elif action_type == "scroll":', key_press_start)

    assert "if not input_ctrl.replay_key_events" in source[hotkey_start:key_press_start]
    assert "기록 키 실패" in source[hotkey_start:key_press_start]
    assert "if not input_ctrl.replay_key_events" in source[key_press_start:scroll_start]
    assert "기록 키 입력 실패" in source[key_press_start:scroll_start]
