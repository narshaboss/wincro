from src.analyzer.automation_models import AutomationRule
from src.database.models import Action


def test_automation_rule_preserves_recorded_key_events():
    events = [
        {"event": "down", "key": "shift", "delay": 0.0},
        {"event": "down", "key": "up", "delay": 0.012},
        {"event": "up", "key": "up", "delay": 0.005},
        {"event": "up", "key": "shift", "delay": 0.004},
    ]
    rule = AutomationRule(action_type="hotkey", action_keys=["shift", "up"], action_key_events=events)

    restored = AutomationRule.from_dict(rule.to_dict())

    assert restored.action_keys == ["shift", "up"]
    assert restored.action_key_events == events


def test_action_preserves_recorded_key_events():
    events = [
        {"event": "down", "key": "shift", "delay": 0.0},
        {"event": "down", "key": "up", "delay": 0.01},
        {"event": "up", "key": "up", "delay": 0.004},
        {"event": "up", "key": "shift", "delay": 0.003},
    ]
    action = Action(action_type="hotkey", keys=["shift", "up"], key_events=events)

    restored = Action.from_dict(action.to_dict())

    assert restored.keys == ["shift", "up"]
    assert restored.key_events == events


def test_rule_executor_prefers_recorded_key_events():
    source = open("src/player/rule_executor.py", encoding="utf-8").read()

    assert 'getattr(rule, "action_key_events", None) or []' in source
    assert "input_ctrl.replay_key_events(key_events)" in source


def test_monitoring_key_action_prefers_recorded_key_events():
    source = open("src/player/rule_executor.py", encoding="utf-8").read()
    start = source.index("elif action_type == '키 입력':")
    end = source.index("elif action_type == '마우스 클릭':", start)
    method = source[start:end]

    assert "key_events = monitor_action.get('key_events', []) or []" in method
    assert "input_ctrl.replay_key_events(key_events)" in method
    assert "return \"기록 키 입력\"" in method


def test_key_input_dialog_exposes_recorded_result_api():
    source = open("src/ui/key_input_dialog.py", encoding="utf-8").read()

    assert "def get_result" in source
    assert '"event": event_type' in source
    assert '"delay": round(delay, 4)' in source
