from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SETTINGS_VIEW = ROOT / "src" / "ui" / "settings_view.py"


def test_settings_view_restores_update_card_in_layout():
    text = SETTINGS_VIEW.read_text(encoding="utf-8")
    setup_start = text.index("    def _setup_ui(self) -> None:")
    setup_end = text.index("    def _setup_general_settings", setup_start)
    setup_ui = text[setup_start:setup_end]

    assert "self._setup_update_settings(update_frame)" in setup_ui
    assert "grid(row=2" in setup_ui
    assert "grid(row=3" in setup_ui
    assert "minsize=96" in setup_ui


def test_settings_save_and_load_include_update_config():
    text = SETTINGS_VIEW.read_text(encoding="utf-8")
    load_start = text.index("    def _load_settings(self) -> None:")
    load_end = text.index("    def _save_settings(self) -> bool:", load_start)
    load_settings = text[load_start:load_end]
    save_start = load_end
    save_end = text.index("    def _refresh_shutdown_status_label", save_start)
    save_settings = text[save_start:save_end]

    assert 'self._github_repo_var.set(getattr(config.update, "github_repo", "") or "")' in load_settings
    assert 'self._auto_update_var.set(bool(getattr(config.update, "auto_check", False)))' in load_settings
    assert "config.update.github_repo = repo" in save_settings
    assert "config.update.auto_check = bool(self._auto_update_var.get())" in save_settings


def test_settings_update_controls_are_visible_and_named():
    text = SETTINGS_VIEW.read_text(encoding="utf-8")
    update_start = text.index("    def _setup_update_settings(self, parent) -> None:")
    update_end = text.index("    def _setup_save_button", update_start)
    update_settings = text[update_start:update_end]

    assert 'text="🔍 버전 확인"' in update_settings
    assert 'text="⬇️ 업데이트"' in update_settings
    assert 'text="자동 업데이트 확인"' in update_settings
    assert 'placeholder_text="username/repo"' in update_settings
