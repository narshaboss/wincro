from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAYER_VIEW = ROOT / "src" / "ui" / "player_view.py"
MAIN_WINDOW = ROOT / "src" / "ui" / "main_window.py"


def _player_view_text() -> str:
    return PLAYER_VIEW.read_text(encoding="utf-8")


def _main_window_text() -> str:
    return MAIN_WINDOW.read_text(encoding="utf-8")


def test_plan_detail_game_mode_wait_after_is_preserved_across_hidden_dialog_chain():
    text = _player_view_text()

    assert "def _run_game_mode(self, config_rule_id=None, source_rule=None, source_previous_rule=None):" in text
    assert "self._gm_current_rule = source_rule" in text
    assert "self._run_game_mode(\n                config_rule_id=gm_rule.rule_id," in text
    assert "self._run_game_mode(\n                    config_rule_id=rule.rule_id," in text
    assert "source_previous_rule=previous_rule" in text
    assert "def _continue_after_game_mode_wait(self, rule, callback" in text
    assert "self._continue_after_game_mode_wait(gm_rule, _continue_success, label=\"부분실행\")" in text
    assert "self._cancel_game_mode_wait()" in text


def test_player_playback_game_mode_wait_after_runs_before_continuing_rules():
    text = _player_view_text()

    assert "def _play_run_game_mode(self, config_rule_id, source_rule=None, source_previous_rule=None):" in text
    assert "self._playback_gm_current_rule = source_rule" in text
    assert "self._play_run_game_mode(\n                gm_rule.rule_id," in text
    assert "source_previous_rule=previous_rule" in text
    assert 'self._playback_remaining_rules = list(getattr(gm_rule, "children", []) or []) + list(rules_to_run[1:])' in text
    assert "def _continue_after_playback_game_mode_wait(self, rule, callback) -> None:" in text
    assert "self._continue_after_playback_game_mode_wait(gm_rule, _continue_success)" in text
    assert "self._cancel_playback_game_mode_wait()" in text


def test_plan_detail_game_mode_children_run_after_hidden_dialog_completion():
    text = _player_view_text()

    assert 'self._gm_remaining_rules = list(getattr(gm_rule, "children", []) or []) + list(rules_to_run[1:])' in text
    assert "self._run_game_mode(\n                config_rule_id=gm_rule.rule_id," in text


def test_mini_player_game_mode_wait_after_runs_before_next_playlist_step():
    text = _main_window_text()

    assert "def _mini_run_game_mode(self, config_rule_id, source_rule=None, source_previous_rule=None):" in text
    assert "self._mini_gm_current_rule = source_rule" in text
    assert "self._mini_run_game_mode(\n                gm_rule.rule_id," in text
    assert "source_previous_rule=previous_rule" in text
    assert 'self._mini_remaining_rules = list(getattr(gm_rule, "children", []) or []) + list(rules_to_run[1:])' in text
    assert "def _mini_continue_after_game_mode_wait(self, rule, callback) -> None:" in text
    assert "self._mini_continue_after_game_mode_wait(gm_rule, _continue_success)" in text
    assert "self._mini_cancel_game_mode_wait()" in text


def test_rule_executor_special_mode_handoff_is_consumed_by_all_ui_playback_chains():
    player_text = _player_view_text()
    main_text = _main_window_text()

    assert "handoff = executor.take_special_mode_route_handoff()" in main_text
    assert "self._mini_next_gm_previous_rule = handoff.previous_rule" in main_text
    assert "lambda rules=handoff.rules, g=callback_generation: self._mini_play_plan_rules(rules)" in main_text
    assert "allow_special_mode_handoff=True" in main_text
    assert "playback_rules = list(plan.initial_rules) + list(plan.monitoring_rules)" in main_text
    assert "self._mini_play_plan_rules(playback_rules)" in main_text

    assert "def _start_partial_executor(self, partial_plan" in player_text
    assert "self._start_partial_executor(\n            partial_plan," in player_text
    assert 'self._start_partial_executor(partial_plan, log_label="부분실행")' in player_text
    assert player_text.count("handoff = executor.take_special_mode_route_handoff()") >= 2
    assert "_gm_next_previous_rule = handoff.previous_rule" in player_text
    assert "self._playback_next_previous_rule = handoff.previous_rule" in player_text
    assert "lambda rules=handoff.rules: self._run_remaining_rules(rules)" in player_text
    assert "lambda rules=handoff.rules: self._play_plan_rules(rules)" in player_text
    assert player_text.count("allow_special_mode_handoff=True") >= 2
    assert "if _has_game_mode_rule(playback_rules):" in player_text
    assert "self._play_plan_rules(playback_rules)" in player_text
