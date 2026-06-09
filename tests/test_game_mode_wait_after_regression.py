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

    assert "def _run_game_mode(self, config_rule_id=None, source_rule=None):" in text
    assert "self._gm_current_rule = source_rule" in text
    assert "_run_game_mode(config_rule_id=gm_rule.rule_id, source_rule=gm_rule)" in text
    assert "_run_game_mode(config_rule_id=rule.rule_id, source_rule=rule)" in text
    assert "def _continue_after_game_mode_wait(self, rule, callback" in text
    assert "self._continue_after_game_mode_wait(gm_rule, _continue_success, label=\"부분실행\")" in text
    assert "self._cancel_game_mode_wait()" in text


def test_player_playback_game_mode_wait_after_runs_before_continuing_rules():
    text = _player_view_text()

    assert "def _play_run_game_mode(self, config_rule_id, source_rule=None):" in text
    assert "self._playback_gm_current_rule = source_rule" in text
    assert "self._play_run_game_mode(gm_rule.rule_id, source_rule=gm_rule)" in text
    assert 'self._playback_remaining_rules = list(getattr(gm_rule, "children", []) or []) + list(rules_to_run[1:])' in text
    assert "def _continue_after_playback_game_mode_wait(self, rule, callback) -> None:" in text
    assert "self._continue_after_playback_game_mode_wait(gm_rule, _continue_success)" in text
    assert "self._cancel_playback_game_mode_wait()" in text


def test_plan_detail_game_mode_children_run_after_hidden_dialog_completion():
    text = _player_view_text()

    assert 'self._gm_remaining_rules = list(getattr(gm_rule, "children", []) or []) + list(rules_to_run[1:])' in text
    assert "self._run_game_mode(config_rule_id=gm_rule.rule_id, source_rule=gm_rule)" in text


def test_mini_player_game_mode_wait_after_runs_before_next_playlist_step():
    text = _main_window_text()

    assert "def _mini_run_game_mode(self, config_rule_id, source_rule=None):" in text
    assert "self._mini_gm_current_rule = source_rule" in text
    assert "self._mini_run_game_mode(gm_rule.rule_id, source_rule=gm_rule)" in text
    assert 'self._mini_remaining_rules = list(getattr(gm_rule, "children", []) or []) + list(rules_to_run[1:])' in text
    assert "def _mini_continue_after_game_mode_wait(self, rule, callback) -> None:" in text
    assert "self._mini_continue_after_game_mode_wait(gm_rule, _continue_success)" in text
    assert "self._mini_cancel_game_mode_wait()" in text
