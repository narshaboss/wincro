from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN_WINDOW = ROOT / "src" / "ui" / "main_window.py"


def _read_text() -> str:
    return MAIN_WINDOW.read_text(encoding="utf-8")


def test_main_window_reloads_single_plan_before_repeat():
    text = _read_text()
    assert "def _mini_reload_plan_for_repeat(self, plan):" in text
    assert "reloaded_plan = self._mini_reload_plan_for_repeat(plan)" in text
    assert "threading.Thread(target=reload_and_execute_current, daemon=True).start()" in text


def test_main_window_repeat_reload_uses_source_file_when_available():
    text = _read_text()
    assert 'plan_path = getattr(plan, "_source_file", None)' in text
    assert "data = load_json_file(plan_file)" in text
    assert "reloaded_plan = AutomationPlan.from_dict(data, templates_dir=templates_dir)" in text
    assert 'reloaded_plan._source_file = str(plan_file)' in text
    assert 'reloaded_plan.total_repeat_count = getattr(plan, "total_repeat_count", 1) or 1' in text


def test_play_mode_shows_version_and_auto_update_toggle():
    text = _read_text()
    mini_slice = text[
        text.index("def _create_mini_player_ui(self):"):
        text.index("def _refresh_mini_plans_sync(self):")
    ]

    assert 'text=f"버전 v{APP_VERSION}"' in mini_slice
    assert "self._mini_auto_update_var = ctk.BooleanVar(value=bool(self._config.update.auto_check))" in mini_slice
    assert "CTkSwitch" not in mini_slice
    assert "self._mini_auto_update_indicator = ctk.CTkButton" in mini_slice
    assert "corner_radius=9" in mini_slice
    assert "command=self._toggle_mini_auto_update_from_indicator" in mini_slice
    assert 'self._mini_auto_update_label.bind(' in mini_slice
    assert 'fg_color=COLORS["error"]' in mini_slice
    assert "def _update_mini_auto_update_label(self):" in text
    assert "def _toggle_mini_auto_update_from_indicator(self):" in text
    assert "def _toggle_mini_auto_update(self):" in text
    assert 'status_color = COLORS["success"] if enabled else COLORS["error"]' in text
    assert 'hover_color=COLORS["green_hover"] if enabled else COLORS["danger_hover"]' in text
    assert "self._config.update.auto_check = enabled" in text
    assert "save_config()" in text
