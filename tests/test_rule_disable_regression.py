from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import importlib

from src.analyzer.automation_models import AutomationRule
from src.ui.player_view import _find_runnable_game_mode_index


PLAYER_VIEW = Path(r"C:\Projects\wincro\src\ui\player_view.py")
MAIN_WINDOW = Path(r"C:\Projects\wincro\src\ui\main_window.py")
RULE_EXECUTOR_MODULE = importlib.import_module("src.player.rule_executor")


def test_rule_enabled_round_trip():
    rule = AutomationRule(action_type="click", description="국내성흑해골굴", enabled=False)
    payload = rule.to_dict()

    assert payload["enabled"] is False

    rebuilt = AutomationRule.from_dict(payload)

    assert rebuilt.enabled is False


def test_rule_executor_skips_disabled_rules():
    cfg = SimpleNamespace(
        player=SimpleNamespace(
            default_wait_ms=800,
            mouse_move_duration=0.4,
            typing_interval=0.1,
        )
    )

    with patch.object(RULE_EXECUTOR_MODULE, "get_config", return_value=cfg):
        executor = RULE_EXECUTOR_MODULE.RuleExecutor()

    rules = [
        AutomationRule(description="enabled-1", enabled=True),
        AutomationRule(description="disabled-root", enabled=False),
        AutomationRule(
            description="enabled-parent",
            enabled=True,
            children=[
                AutomationRule(description="disabled-child", enabled=False),
                AutomationRule(description="enabled-child", enabled=True),
            ],
        ),
    ]

    flattened = executor._flatten_rules_with_step(rules)

    assert [(rule.description, step) for rule, step in flattened] == [
        ("enabled-1", "1"),
        ("enabled-parent", "2"),
        ("enabled-child", "2-1"),
    ]


def test_player_view_contains_rule_enable_toggle():
    src = PLAYER_VIEW.read_text(encoding="utf-8")

    assert "def _rule_is_enabled(rule: AutomationRule) -> bool:" in src
    assert 'label="비활성화" if _rule_is_enabled(r) else "활성화"' in src
    assert "def _toggle_rule_enabled(self, rule: AutomationRule):" in src
    assert 'text="  [비활성]"' in src
    assert 'state="normal" if is_enabled else "disabled"' in src


def test_player_view_ignores_disabled_game_mode_in_chain_detection():
    disabled = AutomationRule(rule_id="disabled", action_type="game_mode", enabled=False)
    pumpkin = AutomationRule(rule_id="pumpkin", action_type="game_mode", description="호박")
    runnable = AutomationRule(rule_id="runnable", action_type="game_mode")

    assert _find_runnable_game_mode_index(
        [disabled, pumpkin, runnable],
        {"disabled": object(), "pumpkin": object(), "runnable": object()},
        SimpleNamespace(pumpkin_action_enabled=False),
    ) == 2


def test_mini_player_ignores_disabled_game_mode_in_chain_detection():
    src = MAIN_WINDOW.read_text(encoding="utf-8")
    execute_slice = src[
        src.index("def _mini_execute_plan"):
        src.index("def _mini_play_plan_rules")
    ]
    chain_slice = src[
        src.index("def _mini_play_plan_rules"):
        src.index("def _mini_game_mode_wait_seconds")
    ]

    assert "is_runtime_action_enabled(rule, self._config.player)" in execute_slice
    assert 'rule.action_type == "game_mode"' in execute_slice
    assert "is_runtime_action_enabled(rule, self._config.player)" in chain_slice
    assert 'rule.action_type == "game_mode"' in chain_slice
