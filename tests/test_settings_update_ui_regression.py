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


def test_general_settings_include_pc_number_local_identifier():
    text = SETTINGS_VIEW.read_text(encoding="utf-8")
    general_start = text.index("    def _setup_general_settings(self, parent) -> None:")
    general_end = text.index("    def _setup_player_settings", general_start)
    general_settings = text[general_start:general_end]
    load_start = text.index("    def _load_settings(self) -> None:")
    load_end = text.index("    def _save_settings(self) -> bool:", load_start)
    load_settings = text[load_start:load_end]
    save_start = load_end
    save_end = text.index("    def _refresh_shutdown_status_label", save_start)
    save_settings = text[save_start:save_end]

    assert 'text="PC 번호"' in general_settings
    assert 'self._pc_number_var = ctk.StringVar()' in general_settings
    assert 'placeholder_text="예: 1, 02, A-3"' in general_settings
    assert 'self._pc_number_var.set(str(getattr(config.system, "pc_number", "") or ""))' in load_settings
    assert 'config.system.pc_number = self._pc_number_var.get().strip()' in save_settings


def test_general_settings_include_discord_notification_controls():
    text = SETTINGS_VIEW.read_text(encoding="utf-8")
    general_start = text.index("    def _setup_general_settings(self, parent) -> None:")
    general_end = text.index("    def _setup_player_settings", general_start)
    general_settings = text[general_start:general_end]
    load_start = text.index("    def _load_settings(self) -> None:")
    load_end = text.index("    def _save_settings(self) -> bool:", load_start)
    load_settings = text[load_start:load_end]
    save_start = load_end
    save_end = text.index("    def _refresh_shutdown_status_label", save_start)
    save_settings = text[save_start:save_end]

    assert "self._setup_discord_notification_settings(scroll_frame)" in general_settings
    assert 'text="디스코드 휴대폰 알림"' in general_settings
    assert 'placeholder_text="https://discord.com/api/webhooks/..."' in general_settings
    assert 'self._discord_enabled_var.set(bool(getattr(notification, "discord_enabled", False)))' in load_settings
    assert "notification.discord_enabled = bool(self._discord_enabled_var.get())" in save_settings
    assert "is_valid_discord_webhook_url(notification.discord_webhook_url)" in save_settings


def test_settings_update_controls_are_visible_and_named():
    text = SETTINGS_VIEW.read_text(encoding="utf-8")
    update_start = text.index("    def _setup_update_settings(self, parent) -> None:")
    update_end = text.index("    def _setup_save_button", update_start)
    update_settings = text[update_start:update_end]

    assert 'text="🔍 버전 확인"' in update_settings
    assert 'text="⬇️ 업데이트"' in update_settings
    assert 'text="자동 업데이트 확인"' in update_settings
    assert 'placeholder_text="username/repo"' in update_settings


def test_settings_arduino_firmware_check_requires_kq_capability():
    text = SETTINGS_VIEW.read_text(encoding="utf-8")
    check_start = text.index("    def _check_firmware_status")
    check_end = text.index("    def _disconnect_arduino", check_start)
    check_body = text[check_start:check_end]
    connect_start = text.index("    def _connect_arduino_thread")
    connect_end = text.index("    def _check_firmware", connect_start)
    connect_body = text[connect_start:connect_end]

    assert 'ser.write(b"KQ\\n")' in check_body
    assert 'return True, "current"' in check_body
    assert 'return False, "outdated"' in check_body
    assert "arduino_hid._supports_key_combo_tap = firmware_ok" in connect_body


def test_settings_background_threads_post_ui_safely():
    text = SETTINGS_VIEW.read_text(encoding="utf-8")

    assert "def _post_ui(self, callback, delay_ms: int = 0) -> None:" in text

    for start_marker, end_marker in [
        ("    def _check_version_thread", "    def _compare_versions"),
        ("    def _download_update_thread", "    def _start_update_and_exit"),
        ("    def _connect_arduino_thread", "    def _check_firmware"),
        ("    def _upload_arduino_firmware", None),
    ]:
        start = text.index(start_marker)
        if end_marker is None:
            next_method = text.find("\n    def ", start + len(start_marker))
            end = next_method if next_method != -1 else len(text)
        else:
            end = text.index(end_marker, start)
        body = text[start:end]
        assert "self.after(0," not in body
        assert "self._post_ui(" in body
