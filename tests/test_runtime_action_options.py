from types import SimpleNamespace
from unittest.mock import patch
import importlib

from src.analyzer.automation_models import AutomationRule
from src.player.runtime_action_options import (
    effective_action_repeat_count,
    is_login_count_action,
    is_pumpkin_action,
    is_runtime_action_enabled,
    should_skip_pumpkin_action,
)
from src.utils.config import AppConfig, ConfigManager, PlayerConfig


RULE_EXECUTOR_MODULE = importlib.import_module("src.player.rule_executor")
ACTION_PLAYER_MODULE = importlib.import_module("src.player.action_player")


def _player_config(**overrides):
    values = {
        "default_wait_ms": 0,
        "mouse_move_duration": 0.0,
        "typing_interval": 0.0,
        "emergency_stop_key": "esc",
        "emergency_stop_count": 2,
        "pumpkin_action_enabled": True,
        "login_action_repeat_count": 4,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _executor(**player_overrides):
    cfg = SimpleNamespace(player=_player_config(**player_overrides))
    with patch.object(RULE_EXECUTOR_MODULE, "get_config", return_value=cfg):
        return RULE_EXECUTOR_MODULE.RuleExecutor()


def test_player_config_defaults_keep_pumpkin_on_and_login_repeat_four():
    player = PlayerConfig()

    assert player.pumpkin_action_enabled is True
    assert player.login_action_repeat_count == 4


def test_runtime_options_survive_config_serialization_round_trip():
    config = AppConfig()
    config.player.pumpkin_action_enabled = False
    config.player.login_action_repeat_count = 9

    payload = ConfigManager._config_to_dict(None, config)
    restored = ConfigManager._dict_to_config(None, payload)

    assert restored.player.pumpkin_action_enabled is False
    assert restored.player.login_action_repeat_count == 9


def test_runtime_names_match_only_exact_action_names():
    pumpkin = AutomationRule(description="호박")
    spaced_pumpkin = AutomationRule(description="  호박  ")
    remaining_pumpkin = AutomationRule(description="연 남은호박 자동사냥 시작")
    login = AutomationRule(description="로그인횟수")
    other_login = AutomationRule(description="로그인 횟수")

    assert is_pumpkin_action(pumpkin) is True
    assert is_pumpkin_action(spaced_pumpkin) is True
    assert is_pumpkin_action(remaining_pumpkin) is False
    assert is_login_count_action(login) is True
    assert is_login_count_action(other_login) is False


def test_pumpkin_runtime_option_is_pc_local_and_defaults_to_on():
    pumpkin = AutomationRule(description="호박")
    partial_name = AutomationRule(description="호박 정리")

    assert should_skip_pumpkin_action(pumpkin, SimpleNamespace()) is False
    assert should_skip_pumpkin_action(
        pumpkin,
        _player_config(pumpkin_action_enabled=False),
    ) is True
    assert should_skip_pumpkin_action(
        partial_name,
        _player_config(pumpkin_action_enabled=False),
    ) is False


def test_pumpkin_runtime_toggle_supersedes_stale_plan_disabled_state():
    pumpkin = AutomationRule(description="호박", enabled=False)
    ordinary = AutomationRule(description="다른 액션", enabled=False)

    assert is_runtime_action_enabled(pumpkin, _player_config()) is True
    assert is_runtime_action_enabled(ordinary, _player_config()) is False


def test_login_repeat_setting_overrides_only_exact_login_action():
    login = AutomationRule(description="로그인횟수", repeat_count=2)
    other = AutomationRule(description="다른 액션", repeat_count=2)
    player = _player_config(login_action_repeat_count=7)

    assert effective_action_repeat_count(login, player) == 7
    assert effective_action_repeat_count(other, player) == 2


def test_legacy_action_player_uses_the_same_pumpkin_runtime_toggle():
    cfg = SimpleNamespace(
        player=_player_config(pumpkin_action_enabled=False),
    )
    with patch.object(ACTION_PLAYER_MODULE, "get_config", return_value=cfg):
        player = ACTION_PLAYER_MODULE.ActionPlayer()

    pumpkin = SimpleNamespace(description="호박", enabled=False)
    ordinary_enabled = SimpleNamespace(description="일반", enabled=True)
    ordinary_disabled = SimpleNamespace(description="비활성", enabled=False)
    sequence = SimpleNamespace(actions=[pumpkin, ordinary_enabled, ordinary_disabled])

    assert player._get_enabled_actions(sequence) == [ordinary_enabled]

    player._config.player.pumpkin_action_enabled = True
    assert player._get_enabled_actions(sequence) == [pumpkin, ordinary_enabled]


def test_pumpkin_parent_is_skipped_but_its_child_still_executes():
    executor = _executor(pumpkin_action_enabled=False)
    child = AutomationRule(
        action_type="hotkey",
        description="호박 하위 키입력",
        action_keys=["enter"],
        wait_after=0.0,
    )
    pumpkin = AutomationRule(
        action_type="double_click",
        description="호박",
        enabled=False,
        target_image="pumpkin.png",
        click_until_image_disappears=True,
        wait_after=0.0,
        children=[child],
    )
    flattened = executor._flatten_rules_with_step([pumpkin])

    assert [(rule.description, step) for rule, step in flattened] == [
        ("호박", "1"),
        ("호박 하위 키입력", "1-1"),
    ]

    def execute_success(rule, step_num=""):
        return executor._make_result(rule, True, "ok", RULE_EXECUTOR_MODULE.datetime.now())

    with (
        patch.object(executor, "_execute_rule", side_effect=execute_success) as execute_mock,
        patch.object(RULE_EXECUTOR_MODULE.time, "sleep"),
    ):
        result = executor._execute_rule_tree_once(pumpkin, "1")

    assert result is None
    assert execute_mock.call_count == 1
    assert execute_mock.call_args.args[0] is child
    assert any(
        item.rule_id == pumpkin.rule_id and "액션 자체만 스킵됨" in item.message
        for item in executor.results
    )


def test_previous_action_looks_past_skipped_pumpkin_to_its_child():
    executor = _executor(pumpkin_action_enabled=False)
    child = AutomationRule(description="호박 하위 액션")
    pumpkin = AutomationRule(description="호박", children=[child])
    following = AutomationRule(description="다음 액션")
    flattened = executor._flatten_rules([pumpkin, following])

    assert executor._next_runtime_rule(flattened, 0) is child


def test_rule_executor_uses_login_repeat_setting_without_mutating_plan():
    executor = _executor(login_action_repeat_count=6)
    login = AutomationRule(
        action_type="hotkey",
        description="로그인횟수",
        action_keys=["enter"],
        repeat_count=4,
        repeat_delay=0.0,
    )

    def execute_success(rule, step_num=""):
        return executor._make_result(rule, True, "ok", RULE_EXECUTOR_MODULE.datetime.now())

    with (
        patch.object(executor, "_execute_rule", side_effect=execute_success) as execute_mock,
        patch.object(RULE_EXECUTOR_MODULE.time, "sleep"),
    ):
        result = executor._execute_rule_with_retry(login, max_retries=1, step_num="3-2")

    assert result.success is True
    assert execute_mock.call_count == 6
    assert login.repeat_count == 4


def test_login_repeat_stepper_clamps_and_recovers_invalid_input():
    from src.ui.settings_view import SettingsView

    class FakeVar:
        def __init__(self, value):
            self.value = value

        def get(self):
            return self.value

        def set(self, value):
            self.value = value

    stub = SimpleNamespace(_login_action_repeat_count_var=FakeVar("1000"))
    SettingsView._adjust_login_action_repeat_count(stub, 1)
    assert stub._login_action_repeat_count_var.get() == "1000"

    stub._login_action_repeat_count_var.set("1")
    SettingsView._adjust_login_action_repeat_count(stub, -1)
    assert stub._login_action_repeat_count_var.get() == "1"

    stub._login_action_repeat_count_var.set("잘못된 값")
    SettingsView._adjust_login_action_repeat_count(stub, 1)
    assert stub._login_action_repeat_count_var.get() == "5"


def test_player_and_settings_ui_expose_linked_runtime_options():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    main_window = (root / "src" / "ui" / "main_window.py").read_text(encoding="utf-8")
    settings = (root / "src" / "ui" / "settings_view.py").read_text(encoding="utf-8")

    assert "self._mini_auto_update_indicator.pack" in main_window
    assert "self._mini_pumpkin_var = ctk.BooleanVar" in main_window
    assert "command=self._toggle_mini_pumpkin_from_indicator" in main_window
    assert "self._config.player.pumpkin_action_enabled = enabled" in main_window
    assert 'auto_state_frame.pack(side="right"' in main_window
    assert 'auto_state_frame.pack(side="bottom"' not in main_window
    assert main_window.index("self._mini_auto_update_indicator.pack") < main_window.index(
        "self._mini_pumpkin_var = ctk.BooleanVar"
    )
    assert 'text="호박 액션"' in settings
    assert "pumpkin_options.pack" in settings
    assert "pumpkin_header.pack" in settings
    assert "self._pumpkin_action_enabled_checkbox.pack(side=\"left\"" in settings
    assert "login_options.pack" in settings
    assert "login_repeat_frame = ctk.CTkFrame(login_options" in settings
    assert 'text="▲"' in settings
    assert 'text="▼"' in settings
    assert "self._adjust_login_action_repeat_count(1)" in settings
    assert "self._adjust_login_action_repeat_count(-1)" in settings
    assert 'text="로그인 횟수"' in settings
    assert "config.player.pumpkin_action_enabled" in settings
    assert "config.player.login_action_repeat_count" in settings
