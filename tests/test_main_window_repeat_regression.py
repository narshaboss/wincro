from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN_WINDOW = ROOT / "src" / "ui" / "main_window.py"
SETTINGS_VIEW = ROOT / "src" / "ui" / "settings_view.py"
APP = ROOT / "src" / "app.py"


def _read_text() -> str:
    return MAIN_WINDOW.read_text(encoding="utf-8")


def _read_settings_text() -> str:
    return SETTINGS_VIEW.read_text(encoding="utf-8")


def _read_app_text() -> str:
    return APP.read_text(encoding="utf-8")


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

    assert 'text=f"v{APP_VERSION}"' in mini_slice
    assert 'self.title(f"{app_name}")' in text
    assert 'self.title(f"{app_name} v{APP_VERSION}")' not in text
    assert "info_frame = ctk.CTkFrame(" not in mini_slice
    assert "auto_state_frame = ctk.CTkFrame(active_frame" in mini_slice
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


def test_play_mode_shows_auto_shutdown_toggle_linked_to_editor_setting():
    text = _read_text()
    settings_text = _read_settings_text()
    mini_slice = text[
        text.index("def _create_mini_player_ui(self):"):
        text.index("def _refresh_mini_plans_sync(self):")
    ]

    assert 'self._mini_auto_shutdown_var = ctk.BooleanVar(' in mini_slice
    assert 'getattr(self._config.system, "shutdown_enabled", True)' in mini_slice
    assert "self._mini_auto_shutdown_indicator = ctk.CTkButton" in mini_slice
    assert "command=self._toggle_mini_auto_shutdown_from_indicator" in mini_slice
    assert 'self._mini_auto_shutdown_label.bind(' in mini_slice
    assert "def _update_mini_auto_shutdown_label(self):" in text
    assert "def _toggle_mini_auto_shutdown_from_indicator(self):" in text
    assert "def _toggle_mini_auto_shutdown(self):" in text
    assert "self._config.system.shutdown_enabled = enabled" in text
    assert "sync_shutdown_task_from_config(self._config.system)" in text
    assert 'config.system.shutdown_enabled = bool(self._shutdown_enabled_var.get())' in settings_text


def test_play_mode_log_copy_button_is_removed():
    text = _read_text()
    mini_slice = text[
        text.index("def _create_mini_player_ui(self):"):
        text.index("def _refresh_mini_plans_sync(self):")
    ]

    assert 'text="로그 전체복사"' not in mini_slice
    assert "command=self._copy_mini_log_to_clipboard" not in mini_slice
    assert "def _copy_mini_log_to_clipboard(self):" not in text


def test_play_mode_top_controls_are_simplified_and_korean_labeled():
    text = _read_text()
    mini_slice = text[
        text.index("def _create_mini_player_ui(self):"):
        text.index("def _refresh_mini_plans_sync(self):")
    ]
    control_slice = mini_slice[
        mini_slice.index("self._mini_play_btn = ctk.CTkButton("):
        mini_slice.index("# 상태 텍스트", mini_slice.index("self._mini_play_btn = ctk.CTkButton("))
    ]

    assert 'text="📋"' not in mini_slice
    assert "command=self._open_partial_execution" not in mini_slice
    assert 'text="↻ 새로고침"' not in mini_slice
    assert "command=self._refresh_mini_plans" not in mini_slice
    assert 'text="에디터"' in mini_slice
    assert 'text="✎ 에디터"' not in mini_slice
    assert 'text="플레이 모드"' not in mini_slice
    assert 'fg_color=COLORS["accent_pink"]' in mini_slice
    assert 'text="▶ 실행"' in control_slice
    assert 'text="⏸ 일시정지"' in control_slice
    assert 'text="⏹ 정지"' in control_slice
    assert control_slice.count("width=116") == 3
    assert control_slice.count("height=38") == 3
    assert 'text="pause"' not in text
    assert 'text="resume"' not in text
    assert "pause not available" not in text
    assert "self._mini_status.pack(" not in mini_slice


def test_main_window_uses_desktop_icon_symbol_in_both_modes():
    text = _read_text()
    mini_slice = text[
        text.index("def _create_mini_player_ui(self):"):
        text.index("def _refresh_mini_plans_sync(self):")
    ]
    topbar_slice = text[
        text.index("def _setup_topbar(self):"):
        text.index("def _setup_content_area(self):")
    ]

    assert 'APP_ICON_FILE = PROJECT_ROOT / "icon.ico"' in text
    assert 'APP_ICON_PREVIEW_FILE = PROJECT_ROOT / "icon_preview.png"' in text
    assert "self.iconbitmap(str(APP_ICON_FILE))" in text
    assert "def _create_brand_lockup(" in text
    assert "brand_bar = ctk.CTkFrame(" not in mini_slice
    assert "self._create_brand_lockup(brand_bar" not in mini_slice
    assert 'self._mini_version_label.pack(side="left", padx=(8, 4), pady=8)' in mini_slice
    assert "self._create_brand_lockup(logo_frame" in topbar_slice
    assert "self._brand_name_labels = []" in text
    assert "for label in getattr(self, \"_brand_name_labels\", []):" in text
    assert "self._brand_name_labels.append(name_label)" in text
    assert 'text=f"🤖 {self._app_name}"' not in text


def test_play_mode_active_bar_tracks_current_group_and_playlist():
    text = _read_text()
    app_text = _read_app_text()
    mini_slice = text[
        text.index("def _create_mini_player_ui(self):"):
        text.index("def _refresh_mini_plans_sync(self):")
    ]
    active_method = text[
        text.index("def _mini_update_active_bar("):
        text.index("def _toggle_mini_auto_update_from_indicator(", text.index("def _mini_update_active_bar("))
    ]

    assert 'text="현재 실행"' in mini_slice
    assert "self._mini_active_title" in mini_slice
    assert "self._mini_active_detail" in mini_slice
    assert 'font=ctk.CTkFont(size=13, weight="bold")' in mini_slice
    assert 'self._mini_update_active_bar("대기")' in mini_slice
    assert "def _mini_update_active_bar(" in text
    assert "display_names = [name for name in (group_name, plan_name) if name]" in active_method
    assert 'detail = " > ".join(display_names)' in active_method
    assert 'detail_color = COLORS["warning"]' in active_method
    assert "액션명/진행 메시지는 로그와 상태줄에만 남긴다." in active_method
    assert "self._mini_active_detail.configure(text=detail, text_color=detail_color)" in active_method
    assert "def _mini_active_group_name(self) -> str:" in text
    assert "get_active_plan_sequence_group(self._config.player)" in text
    assert 'def auto_run_sequence(self, plan_paths: list, repeats: list = None, group_name: str = "") -> bool:' in text
    assert "group = self._mini_group_by_name(group_name)" in text
    assert "group_repeat = normalize_repeat_count(group.get(\"repeat_count\", 1)) if group is not None else 1" in text
    assert "group_repeat=group_repeat" in text
    assert "self._sequence_group_name = group_name or self._mini_active_group_name()" in text
    assert "auto_run_sequence(converted_paths, repeats, group_name=group_name)" in app_text


def test_play_mode_dropdown_can_run_grouped_playlists():
    text = _read_text()

    assert 'MINI_GROUP_PREFIX = "그룹: "' in text
    assert "def _mini_dropdown_values(self) -> list[str]:" in text
    assert "values = [self._mini_group_label(group) for group in self._mini_sequence_groups()]" in text
    assert "self._mini_plan_dropdown.configure(values=plan_names)" in text
    assert "self._style_mini_plan_dropdown()" in text
    assert "def _is_mini_group_label(self, value: str) -> bool:" in text
    assert "def _style_mini_plan_dropdown(self) -> None:" in text
    assert 'selected_color = COLORS["warning"] if self._is_mini_group_label(selected) else COLORS["text_primary"]' in text
    assert "menu.entryconfigure(index, foreground=color, activeforeground=color)" in text
    assert "selected_group = self._mini_group_by_label(plan_name)" in text
    assert "self._mini_repeat_var.set(str(group_repeat))" in text
    assert "repeat_count = normalize_repeat_count(selected_group.get(\"repeat_count\", repeat_count))" in text
    assert 'self._mini_status.configure(text=f"✓ 그룹 반복 {repeat_count}회 저장됨")' in text
    assert "group_to_run = dict(selected_group)" in text
    assert "plan_paths, repeats = self._mini_expand_group_sequence(group_to_run)" in text
    assert "group_label=self._mini_group_label(selected_group)" in text
    assert "group_repeat=repeat_count" in text


def test_group_sequence_keeps_group_selection_when_inner_plan_runs():
    text = _read_text()
    setup_slice = text[
        text.index("def _setup_mini_player_ui(self):"):
        text.index("def _create_mini_player_ui(self):")
    ]
    play_slice = text[
        text.index("def _mini_on_play(self):"):
        text.index("def _start_sequence_mode(", text.index("def _mini_on_play(self):"))
    ]
    start_slice = text[
        text.index("def _start_sequence_mode("):
        text.index("def _run_sequence_plan(self, index: int, playback_generation: int | None = None):")
    ]
    run_slice = text[
        text.index("def _run_sequence_plan(self, index: int, playback_generation: int | None = None):"):
        text.index("def _mini_on_load_failed(self, message: str, playback_generation: int | None = None):")
    ]
    stop_slice = text[
        text.index("def _mini_on_stop(self):"):
        text.index("def _mini_on_progress(self, progress):")
    ]
    complete_slice = text[
        text.index("def _mini_on_complete(self, success, message, playback_generation: int | None = None):"):
        text.index("def _setup_topbar(self):")
    ]

    assert "self._sequence_group_label = \"\"" in setup_slice
    assert "self._sequence_group_repeat_count = 1" in setup_slice
    assert "self._mini_playback_generation = 0" in setup_slice
    assert "def _mini_prepare_new_playback_request(self) -> int:" in text
    assert "def _mini_is_current_playback_generation(" in text
    assert "def _mini_cancel_sequence_start(" in text
    assert "def _mini_restore_group_selection(" in text
    assert "playback_generation = self._mini_prepare_new_playback_request()" in play_slice
    assert "group_label=self._mini_group_label(selected_group)" in play_slice
    assert "group_repeat=repeat_count" in play_slice
    assert "playback_generation=playback_generation" in play_slice
    assert "configured_group = self._mini_group_by_name(self._sequence_group_name)" in start_slice
    assert "self._sequence_group_label = group_label or (" in start_slice
    assert "self._mini_plan_var.set(self._sequence_group_label)" in start_slice
    assert "self._mini_repeat_var.set(str(self._sequence_group_repeat_count))" in start_slice
    assert "playback_generation: int | None = None" in run_slice
    assert "start skipped because playback already stopped" in run_slice
    assert "self._mini_cancel_sequence_start(" in run_slice
    assert "그룹 실행 중에는 선택값을 내부 플랜명으로 덮지 않는다." in run_slice
    assert "self._mini_plan_var.set(self._sequence_group_label)" in run_slice
    assert "self._mini_plan_var.set(plan.name)" in run_slice
    assert "stopped_group_name = getattr(self, \"_sequence_group_name\", \"\")" in stop_slice
    assert "self._mini_playback_generation = getattr(self, \"_mini_playback_generation\", 0) + 1" in stop_slice
    assert "self._mini_restore_group_selection(stopped_group_name, stopped_group_label, stopped_group_repeat)" in stop_slice
    assert "self._sequence_group_label = \"\"" in stop_slice
    assert "self._sequence_group_repeat_count = 1" in stop_slice
    assert "completed_group_name = getattr(self, \"_sequence_group_name\", \"\")" in complete_slice
    assert "self._mini_restore_group_selection(" in complete_slice
    assert "self._sequence_group_label = \"\"" in complete_slice
    assert "self._sequence_group_repeat_count = 1" in complete_slice


def test_settings_group_repeat_apply_keeps_list_selection_when_entry_has_focus():
    text = _read_settings_text()
    settings_slice = text[
        text.index("def _setup_player_settings(self, parent) -> None:"):
        text.index("def _load_plan_list(self) -> list:")
    ]

    assert "self._seq_selected_entry_index = 0" in settings_slice
    assert "exportselection=False" in settings_slice
    assert "def _seq_current_entry_index(self, group: Optional[dict]) -> Optional[int]:" in settings_slice
    assert "idx = self._seq_current_entry_index(group)" in settings_slice
    assert "if not group or idx is None:" in settings_slice


def test_play_mode_high_frequency_status_updates_skip_duplicate_configures():
    text = _read_text()
    status_helper = text[
        text.index("def _mini_set_status(self, text: str, text_color=None) -> None:"):
        text.index("def _mini_update_active_bar(", text.index("def _mini_set_status"))
    ]
    active_method = text[
        text.index("def _mini_update_active_bar("):
        text.index("def _toggle_mini_auto_update_from_indicator(", text.index("def _mini_update_active_bar("))
    ]
    progress_method = text[
        text.index("def _mini_on_progress(self, progress):"):
        text.index("def _mini_on_repeat_complete(self, success, message):")
    ]
    repeat_method = text[
        text.index("def _mini_on_repeat_complete(self, success, message):"):
        text.index(
            "if self._sequence_mode and self._sequence_index < len(self._sequence_plans):",
            text.index("def _mini_on_repeat_complete"),
        )
    ]
    log_flush = text[
        text.index("def _flush_log_buffer():"):
        text.index("def add_log(msg: str, level: str):")
    ]

    assert "current_text = self._mini_status.cget(\"text\")" in status_helper
    assert "if current_text == text:" in status_helper
    assert "self._mini_status.configure(text=text, text_color=text_color)" in status_helper
    assert "self._mini_active_bar_snapshot = None" in text
    assert "if snapshot == getattr(self, \"_mini_active_bar_snapshot\", None):" in active_method
    assert "self._mini_active_bar_snapshot = snapshot" in active_method
    assert "self._mini_set_status(" in progress_method
    assert "self._mini_set_status(" in repeat_method
    assert "self._mini_set_status(" in log_flush


def test_play_mode_discord_notification_watchdog_hooks_are_present():
    text = _read_text()
    setup_start = text.index("    def _setup_mini_player_ui(self):")
    setup_end = text.index("    def _create_mini_player_ui", setup_start)
    setup_body = text[setup_start:setup_end]
    start_start = text.index("    def _mini_start_execution(")
    start_end = text.index("    def _mini_execute_plan", start_start)
    start_body = text[start_start:start_end]
    progress_start = text.index("    def _mini_on_progress(")
    progress_end = text.index("    def _mini_on_repeat_complete", progress_start)
    progress_body = text[progress_start:progress_end]
    stop_start = text.index("    def _mini_on_stop(")
    stop_end = text.index("    def _mini_on_progress", stop_start)
    stop_body = text[stop_start:stop_end]
    complete_start = text.index("    def _mini_on_complete(")
    complete_end = text.index("    def _setup_topbar", complete_start)
    complete_body = text[complete_start:complete_end]

    assert "self._mini_notification_after_id = None" in setup_body
    assert "def _mini_start_notification_watchdog" in text
    assert "def _mini_cancel_notification_watchdog" in text
    assert "def _mini_send_discord_alert" in text
    assert "elif event_key == \"group_complete\":" in text
    assert "event_type = \"complete\"" in text
    assert "self._mini_start_notification_watchdog(playback_generation)" in start_body
    assert "self._mini_record_notification_progress(progress)" in progress_body
    assert "self._mini_cancel_notification_watchdog()" in stop_body
    assert "success and was_sequence" in complete_body
    assert "WinCro 그룹 실행 완료" in complete_body
    assert "message != \"stopped\"" in complete_body
    assert "WinCro 재생 실패" in complete_body
