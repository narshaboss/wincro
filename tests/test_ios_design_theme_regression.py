import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
THEME = ROOT / "src" / "ui" / "theme.py"
CTK_WHITE_GOLD_THEME = ROOT / "src" / "ui" / "ctk_white_gold_theme.json"
MAIN_WINDOW = ROOT / "src" / "ui" / "main_window.py"
PLAYER_VIEW = ROOT / "src" / "ui" / "player_view.py"
ANALYZER_VIEW = ROOT / "src" / "ui" / "analyzer_view.py"
SETTINGS_VIEW = ROOT / "src" / "ui" / "settings_view.py"
RECORDER_VIEW = ROOT / "src" / "ui" / "recorder_view.py"
LOG_VIEW = ROOT / "src" / "ui" / "log_view.py"
VIRTUAL_SCROLL = ROOT / "src" / "ui" / "virtual_scroll.py"
GUIDE_VIEW = ROOT / "src" / "ui" / "guide_view.py"
KEY_INPUT_DIALOG = ROOT / "src" / "ui" / "key_input_dialog.py"
MONITORING_EDITOR = ROOT / "src" / "ui" / "monitoring_editor.py"
HELP_DIALOG = ROOT / "src" / "ui" / "help_dialog.py"
UI_DIR = ROOT / "src" / "ui"
ALLOWED_COLOR_LITERAL_FILES = {
    (ROOT / "src" / "ui" / "theme.py").resolve(),
    (ROOT / "src" / "ui" / "ctk_white_gold_theme.json").resolve(),
}
BUTTON_SOURCE_FILES = sorted((ROOT / "src").rglob("*.py"))


def _ctk_button_blocks(path: Path) -> list[tuple[int, str]]:
    text = path.read_text(encoding="utf-8")
    blocks = []
    for match in re.finditer(r"ctk\.CTkButton\(", text):
        depth = 0
        end = None
        for idx in range(match.end() - 1, len(text)):
            char = text[idx]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    end = idx + 1
                    break
        if end is None:
            continue
        blocks.append((text.count("\n", 0, match.start()) + 1, text[match.start():end]))
    return blocks


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    value = hex_color.strip()
    assert re.fullmatch(r"#[0-9A-Fa-f]{6}", value)
    return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))


def _relative_luminance(hex_color: str) -> float:
    channels = []
    for raw in _hex_to_rgb(hex_color):
        value = raw / 255
        channels.append(value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _contrast_ratio(foreground: str, background: str) -> float:
    high, low = sorted(
        (_relative_luminance(foreground), _relative_luminance(background)),
        reverse=True,
    )
    return (high + 0.05) / (low + 0.05)


def test_ui_source_tree_does_not_ship_stale_python_backups():
    stale_backups = [
        path.relative_to(ROOT).as_posix()
        for path in UI_DIR.iterdir()
        if path.name.endswith(".bak") or ".bak_" in path.name or ".pre_sanitize_" in path.name
    ]

    assert stale_backups == []


def test_ui_color_literals_are_centralized_in_white_gold_theme_files():
    offenders = []
    for path in UI_DIR.rglob("*"):
        if path.suffix not in {".py", ".json"}:
            continue
        if path.resolve() in ALLOWED_COLOR_LITERAL_FILES:
            continue
        text = path.read_text(encoding="utf-8")
        if re.search(r"#[0-9A-Fa-f]{6}", text):
            offenders.append(path.relative_to(ROOT).as_posix())

    assert offenders == []


def test_white_gold_palette_preserves_required_text_contrast():
    from src.ui.theme import COLORS

    required_pairs = [
        ("text_primary", "bg_content", 7.0),
        ("text_primary", "bg_card", 7.0),
        ("text_secondary", "bg_content", 4.5),
        ("text_muted", "bg_content", 4.5),
        ("success_text", "bg_content", 4.5),
        ("warning_text", "bg_content", 4.5),
        ("info_text", "bg_content", 4.5),
        ("accent_text", "bg_content", 4.5),
        ("accent_blue_text", "bg_content", 4.5),
        ("accent_pink_text", "bg_content", 4.5),
        ("scroll_purple_text", "bg_content", 4.5),
        ("text_on_accent", "accent", 4.5),
        ("text_on_accent", "accent_blue", 4.5),
        ("text_on_accent", "accent_orange", 4.5),
        ("text_on_accent", "accent_pink", 4.5),
        ("text_on_accent", "success", 4.5),
        ("text_on_accent", "warning", 4.5),
        ("text_on_accent", "error", 4.5),
        ("text_on_accent", "info", 4.5),
    ]

    failures = []
    for foreground, background, minimum in required_pairs:
        ratio = _contrast_ratio(COLORS[foreground], COLORS[background])
        if ratio < minimum:
            failures.append((foreground, background, round(ratio, 2), minimum))

    assert failures == []


def test_ios_theme_tokens_drive_shared_ui_style():
    theme = THEME.read_text(encoding="utf-8")
    ctk_theme = CTK_WHITE_GOLD_THEME.read_text(encoding="utf-8")
    main_window = MAIN_WINDOW.read_text(encoding="utf-8")

    assert "IOS_METRICS = {" in theme
    assert '"card_radius": 22' in theme
    assert '"pill_radius": 999' in theme
    assert '"accent": "#E0B341"' in theme
    assert '"accent_hover": "#FACC15"' in theme
    assert '"success": "#22C55E"' in theme
    assert '"info": "#60A5FA"' in theme
    assert '"text_on_accent": "#0D0B08"' in theme
    assert '"bg_content": "#0F0D0A"' in theme
    assert '"bg_card": "#17130E"' in theme
    assert '"bg_log": "#0A0907"' in theme
    assert '"bg_glass": "#15110C"' in theme
    assert '"text_primary": "#FFF7E6"' in theme
    assert '"text_secondary": "#E8D3A6"' in theme
    assert '"text_muted": "#BFA06A"' in theme
    assert '"card_border_width": 2' in theme
    assert '"canvas_border_width": 2' in theme
    assert '"border": "#8F5E0A"' in theme
    assert '"separator": "#A97010"' in theme
    assert '"image_canvas_border": "#7A4A00"' in theme
    assert '"button_border": "#000000"' in theme
    assert '"success_text": "#BBF7D0"' in theme
    assert '"warning_text": "#FDE68A"' in theme
    assert '"info_text": "#BFDBFE"' in theme
    assert "from .theme import COLORS, IOS_FONTS, IOS_METRICS" in main_window
    assert 'corner_radius=IOS_METRICS["card_radius"]' in main_window
    assert 'corner_radius=IOS_METRICS["pill_radius"]' in main_window
    assert "WHITE_GOLD_CTK_THEME = Path(__file__).with_name(\"ctk_white_gold_theme.json\")" in main_window
    assert 'ctk.set_appearance_mode("dark")' in main_window
    assert "ctk.set_default_color_theme(str(WHITE_GOLD_CTK_THEME))" in main_window
    assert '"CTkButton": {' in ctk_theme
    assert '"border_width": 2' in ctk_theme
    assert '"border_color": ["#000000", "#000000"]' in ctk_theme
    assert '"fg_color": ["#E0B341", "#E0B341"]' in ctk_theme
    assert '"text_color": ["#0D0B08", "#0D0B08"]' in ctk_theme
    assert '"border_color": ["#8F5E0A", "#8F5E0A"]' in ctk_theme
    assert '"border_color": ["#7A4A00", "#7A4A00"]' in ctk_theme
    assert '"text_color": ["#FFF7E6", "#FFF7E6"]' in ctk_theme
    assert '"text_color_disabled": ["#8C7855", "#8C7855"]' in ctk_theme
    assert '"DropdownMenu": {' in ctk_theme
    assert 'ctk.set_default_color_theme("blue")' not in main_window
    assert 'ctk.set_default_color_theme("dark-blue")' not in main_window
    assert 'ctk.set_appearance_mode("light")' not in main_window
    assert 'activeforeground="white"' not in main_window
    assert '"text_color": "white"' not in main_window


def test_ctk_buttons_keep_visible_black_borders():
    offenders = []
    for path in BUTTON_SOURCE_FILES:
        for line, block in _ctk_button_blocks(path):
            if "border_width=0" in block or "border_width = 0" in block:
                offenders.append(f"{path.relative_to(ROOT).as_posix()}:{line}: border_width=0")
                continue
            if "border_width=1" in block or "border_width = 1" in block:
                offenders.append(f"{path.relative_to(ROOT).as_posix()}:{line}: border_width=1")
                continue

            explicit_border_tokens = re.findall(r'border_color=COLORS\["([^"]+)"\]', block)
            invalid_tokens = sorted(
                token for token in set(explicit_border_tokens) if token != "button_border"
            )
            if invalid_tokens:
                offenders.append(
                    f"{path.relative_to(ROOT).as_posix()}:{line}: {','.join(invalid_tokens)}"
                )

    assert offenders == []


def test_ui_cards_and_canvases_do_not_use_faint_one_pixel_borders():
    offenders = []
    for path in UI_DIR.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for pattern in ("border_width=1", "border_width = 1", "highlightthickness=1"):
            if pattern in text:
                offenders.append(f"{path.relative_to(ROOT).as_posix()}: {pattern}")

    assert offenders == []


def test_bottom_log_header_buttons_have_room_for_visible_borders():
    main_window = MAIN_WINDOW.read_text(encoding="utf-8")
    log_panel_slice = main_window[
        main_window.index("class LogPanel"):
        main_window.index("class MainWindow")
    ]

    assert "self._collapsed_height = 48" in log_panel_slice
    assert 'height=48, corner_radius=0' in log_panel_slice
    assert "height=32" in log_panel_slice
    assert 'fg_color=COLORS["bg_elevated"]' in log_panel_slice
    assert 'border_width=IOS_METRICS["card_border_width"]' in log_panel_slice
    assert 'border_color=COLORS["button_border"]' in log_panel_slice
    assert 'self._filter_combo = ctk.CTkComboBox(' in log_panel_slice
    assert 'self._toggle_btn.pack(side="left", padx=8, pady=8)' in log_panel_slice
    assert 'self._filter_combo.pack(side="left", padx=5, pady=8)' in log_panel_slice
    assert 'self._clear_btn.pack(side="right", padx=8, pady=8)' in log_panel_slice

    toggle_slice = log_panel_slice[
        log_panel_slice.index("self._toggle_btn = ctk.CTkButton("):
        log_panel_slice.index("self._toggle_btn.pack(")
    ]
    filter_slice = log_panel_slice[
        log_panel_slice.index("self._filter_combo = ctk.CTkComboBox("):
        log_panel_slice.index("self._filter_combo.pack(")
    ]
    clear_slice = log_panel_slice[
        log_panel_slice.index("self._clear_btn = ctk.CTkButton("):
        log_panel_slice.index("self._clear_btn.pack(")
    ]

    for control_slice in (toggle_slice, filter_slice, clear_slice):
        assert 'fg_color="transparent"' not in control_slice
        assert 'border_width=IOS_METRICS["card_border_width"]' in control_slice
        assert 'border_color=COLORS["button_border"]' in control_slice


def test_player_core_controls_use_ios_rounding_and_elevated_surfaces():
    player_view = PLAYER_VIEW.read_text(encoding="utf-8")

    assert "def _ios_state_button_style" in player_view
    assert "def _ios_repeat_button_style" in player_view
    assert "def _ios_run_button_style" in player_view
    assert 'fg_color=COLORS["bg_glass"]' in player_view
    assert 'fg_color=COLORS["bg_elevated"]' in player_view
    assert 'bg=COLORS["image_canvas_bg"]' in player_view
    assert 'highlightbackground=COLORS["image_canvas_border"]' in player_view
    assert 'corner_radius=999' in player_view
    assert 'hover_color=COLORS["green_hover"]' in player_view
    assert 'hover_color=COLORS["danger_hover"]' in player_view
    assert 'widget.configure(fg_color=COLORS["selection_green"])' in player_view
    assert '"border_color": COLORS["button_border"]' in player_view
    assert '"text_color": COLORS["text_on_accent"] if active else COLORS["text_primary"]' in player_view
    assert 'return COLORS["accent_pink"] if is_enabled else COLORS["accent_pink_hover"]' in player_view
    assert 'border_width=0' in player_view
    assert not re.search(r"#[0-9A-Fa-f]{6}", player_view)
    assert '"#facc15"' not in player_view
    assert '"#2e7d32"' not in player_view
    assert 'text_color="white"' not in player_view
    assert "corner_radius=4" not in player_view
    assert "corner_radius=8" not in player_view
    assert "corner_radius=6" not in player_view


def test_image_crop_navigation_uses_font_safe_labels():
    analyzer_view = ANALYZER_VIEW.read_text(encoding="utf-8")
    navigation_slice = analyzer_view[
        analyzer_view.index("# 이미지 내비게이션"):
        analyzer_view.index("# 버튼 프레임", analyzer_view.index("# 이미지 내비게이션"))
    ]

    assert 'text="< 이전"' in navigation_slice
    assert 'text="다음 >"' in navigation_slice
    assert 'text="(좌/우 방향키로 이동)"' in navigation_slice
    assert "◀ 이전" not in navigation_slice
    assert "다음 ▶" not in navigation_slice
    assert "← → 키로 이동" not in navigation_slice


def test_editor_action_rows_use_ios_cards_without_changing_virtual_scroll_contract():
    player_view = PLAYER_VIEW.read_text(encoding="utf-8")

    assert "from .theme import COLORS, IOS_FONTS, IOS_METRICS" in player_view
    assert 'fg_color=COLORS["selection_green"] if is_selected else (COLORS["bg_glass"] if is_enabled else COLORS["bg_card"])' in player_view
    assert 'corner_radius=IOS_METRICS["control_radius"]' in player_view
    assert 'corner_radius=IOS_METRICS["card_radius_compact"]' in player_view
    assert 'fg_color=COLORS["bg_elevated"]' in player_view
    assert "**_ios_state_button_style(is_skip)" in player_view
    assert "**_ios_repeat_button_style(repeat_count, until_disappears)" in player_view
    assert "**_ios_run_button_style(is_enabled)" in player_view
    assert "item_height=76" in player_view


def test_special_mode_coordinate_panels_use_ios_cards_and_status_surfaces():
    player_view = PLAYER_VIEW.read_text(encoding="utf-8")
    coordinate_slice = player_view[player_view.index("def _build_coordinate_ui(self):"):]

    assert 'fg_color=COLORS["bg_glass"]' in coordinate_slice
    assert 'corner_radius=IOS_METRICS["card_radius_compact"]' in coordinate_slice
    assert 'border_color=COLORS["border"]' in coordinate_slice
    assert 'corner_radius=IOS_METRICS["pill_radius"]' in coordinate_slice
    assert 'fg_color=COLORS["bg_elevated"]' in coordinate_slice
    assert 'hover_color=COLORS["green_hover"]' in coordinate_slice


def test_analyzer_action_rows_use_ios_tokens_while_preserving_batch_rendering():
    analyzer_view = ANALYZER_VIEW.read_text(encoding="utf-8")

    assert "from .theme import IOS_FONTS, IOS_METRICS" in analyzer_view
    assert 'fg_color=COLORS["bg_glass"]' in analyzer_view
    assert 'bg_color = COLORS["bg_glass"] if depth == 0 else COLORS["child_bg"]' in analyzer_view
    assert 'self.configure(bg=COLORS["overlay_dim"])' in analyzer_view
    assert 'fill=COLORS["overlay_text"]' in analyzer_view
    assert 'corner_radius=IOS_METRICS["control_radius"]' in analyzer_view
    assert 'fg_color=COLORS["bg_elevated"]' in analyzer_view
    assert 'outline=COLORS["info"]' in analyzer_view
    assert '"scroll": COLORS["scroll_purple"]' in analyzer_view
    assert "self._schedule_action_list_render_batch" in analyzer_view
    assert not re.search(r"#[0-9A-Fa-f]{6}", analyzer_view)
    assert 'text_color="white"' not in analyzer_view
    assert "corner_radius=6" not in analyzer_view
    assert "corner_radius=8" not in analyzer_view


def test_analyzer_layout_places_plans_left_and_recordings_right():
    analyzer_view = ANALYZER_VIEW.read_text(encoding="utf-8")
    setup_slice = analyzer_view[
        analyzer_view.index("def _setup_ui(self):"):
        analyzer_view.index("def _setup_analyze_card")
    ]

    assert 'plans_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 4), pady=(0, 4))' in setup_slice
    assert 'recordings_frame.grid(row=0, column=1, sticky="nsew", padx=(4, 0), pady=(0, 4))' in setup_slice
    assert 'analyze_frame.grid(row=1, column=0, sticky="nsew", padx=(0, 4), pady=(4, 0))' in setup_slice
    assert 'result_frame.grid(row=1, column=1, sticky="nsew", padx=(4, 0), pady=(4, 0))' in setup_slice
    assert setup_slice.index("self._setup_plans_card(plans_frame)") < setup_slice.index(
        "self._setup_recordings_card(recordings_frame)"
    )


def test_image_crop_dialog_uses_ios_editor_surfaces():
    analyzer_view = ANALYZER_VIEW.read_text(encoding="utf-8")
    crop_slice = analyzer_view[
        analyzer_view.index("class ImageCropDialog"):
        analyzer_view.index("class AltImageDialog")
    ]

    assert 'fg_color=COLORS["bg_glass"]' in crop_slice
    assert 'bg=COLORS["bg_log"]' in crop_slice
    assert 'corner_radius=IOS_METRICS["card_radius_compact"]' in crop_slice
    assert 'corner_radius=IOS_METRICS["pill_radius"]' in crop_slice
    assert 'hover_color=COLORS["confidence_amber_hover"]' in crop_slice
    assert '"#1a1b26"' not in crop_slice
    assert '"#f59e0b"' not in crop_slice


def test_image_crop_dialog_upscales_small_images_while_showing_original_size():
    analyzer_view = ANALYZER_VIEW.read_text(encoding="utf-8")
    crop_slice = analyzer_view[
        analyzer_view.index("class ImageCropDialog"):
        analyzer_view.index("class AltImageDialog")
    ]

    assert "def _configure_image_view_metrics" in crop_slice
    assert "def _fit_canvas_scale" in crop_slice
    assert "self._initial_zoom_cap = 5.0" in crop_slice
    assert "self._max_scale = 8.0" in crop_slice
    assert "self._configure_image_view_metrics(w, h)" in crop_slice
    assert "self._reset_image_scale(w, h)" in crop_slice
    assert "text=self._format_image_info_text()" in crop_slice
    assert "원본: {w} x {h} px" in crop_slice
    assert "표시: {int(self._scale * 100)}%" in crop_slice
    assert "self._canvas_width = 800" not in crop_slice
    assert "self._max_scale = 3.0" not in crop_slice
    assert "self._canvas_height / h, 1.0" not in crop_slice


def test_alt_image_dialog_uses_ios_cards_without_losing_virtual_scroll():
    analyzer_view = ANALYZER_VIEW.read_text(encoding="utf-8")
    alt_slice = analyzer_view[
        analyzer_view.index("class AltImageDialog"):
        analyzer_view.index("class AnalyzerView")
    ]

    assert "VirtualScrollFrame(" in alt_slice
    assert "item_height=72" in alt_slice
    assert 'fg_color=COLORS["bg_glass"]' in alt_slice
    assert 'fg_color=COLORS["bg_elevated"]' in alt_slice
    assert 'corner_radius=IOS_METRICS["control_radius"]' in alt_slice
    assert 'corner_radius=IOS_METRICS["pill_radius"]' in alt_slice
    assert '"#45a049"' not in alt_slice
    assert '"#dc2626"' not in alt_slice


def test_settings_auto_run_group_editor_uses_ios_cards_and_keeps_selection_contract():
    settings_view = SETTINGS_VIEW.read_text(encoding="utf-8")
    player_slice = settings_view[
        settings_view.index("def _setup_player_settings(self, parent) -> None:"):
        settings_view.index("def _seq_plan_name")
    ]

    assert "from .theme import IOS_FONTS, IOS_METRICS" in settings_view
    assert 'fg_color=COLORS["bg_glass"]' in player_slice
    assert 'fg_color=COLORS["bg_elevated"]' in player_slice
    assert 'corner_radius=IOS_METRICS["card_radius"]' in player_slice
    assert 'corner_radius=IOS_METRICS["card_radius_compact"]' in player_slice
    assert 'corner_radius=IOS_METRICS["control_radius"]' in player_slice
    assert 'corner_radius=IOS_METRICS["pill_radius"]' in player_slice
    assert 'font=(IOS_FONTS["fallback"], 10)' in player_slice
    assert 'font=(IOS_FONTS["fallback"], 11)' in player_slice
    assert 'exportselection=False' in player_slice
    assert '"#2563eb"' not in player_slice
    assert not re.search(r"#[0-9A-Fa-f]{6}", settings_view)
    assert "corner_radius=6" not in settings_view
    assert "corner_radius=8" not in settings_view


def test_recorder_view_uses_ios_surfaces_without_losing_virtualized_list():
    recorder_view = RECORDER_VIEW.read_text(encoding="utf-8")

    assert "from .theme import IOS_FONTS, IOS_METRICS" in recorder_view
    assert "UiCallbackDispatcher" in recorder_view
    assert "VirtualScrollFrame(" in recorder_view
    assert 'family=IOS_FONTS["family"]' in recorder_view
    assert "self._label_text_cache" in recorder_view
    assert "self._set_label_text" in recorder_view
    assert 'fg_color=COLORS["bg_glass"]' in recorder_view
    assert 'fg_color=COLORS["bg_elevated"]' in recorder_view
    assert 'corner_radius=IOS_METRICS["card_radius_compact"]' in recorder_view
    assert 'corner_radius=IOS_METRICS["control_radius"]' in recorder_view
    assert 'fg_color=COLORS["bg_dark"]' not in recorder_view
    assert 'corner_radius=8' not in recorder_view


def test_log_view_uses_ios_log_surfaces_while_preserving_buffered_pump():
    log_view = LOG_VIEW.read_text(encoding="utf-8")

    assert "BufferedRecordPump" in log_view
    assert "UiCallbackDispatcher" in log_view
    assert "from .theme import COLORS, IOS_FONTS, IOS_METRICS" in log_view
    assert 'fg_color=COLORS["bg_glass"]' in log_view
    assert 'bg=COLORS["bg_log"]' in log_view
    assert 'font=(IOS_FONTS["fallback"], 10)' in log_view
    assert 'foreground=COLORS["error"]' in log_view
    assert '"#1a1a2e"' not in log_view
    assert 'font=("Consolas", 10)' not in log_view
    assert 'text_color="gray"' not in log_view


def test_virtual_scroll_canvas_uses_configured_surface_color():
    virtual_scroll = VIRTUAL_SCROLL.read_text(encoding="utf-8")

    assert 'self._surface_color = kwargs["fg_color"]' in virtual_scroll
    assert "bg=self._apply_appearance_mode(self._surface_color)" in virtual_scroll
    assert "bg=self._apply_appearance_mode(COLORS[\"bg_card\"])" not in virtual_scroll
    assert "self._last_render_range = None" in virtual_scroll
    assert "current_range == self._last_render_range" in virtual_scroll


def test_guide_and_key_capture_dialogs_use_ios_tokens():
    guide_view = GUIDE_VIEW.read_text(encoding="utf-8")
    key_dialog = KEY_INPUT_DIALOG.read_text(encoding="utf-8")

    assert "from .theme import COLORS, IOS_FONTS, IOS_METRICS" in guide_view
    assert "from .theme import COLORS, IOS_FONTS, IOS_METRICS" in key_dialog
    assert 'fg_color=COLORS["bg_content"]' in guide_view
    assert 'fg_color=COLORS["bg_glass"]' in guide_view
    assert 'fg_color=COLORS["bg_log"]' in guide_view
    assert 'corner_radius=IOS_METRICS["card_radius"]' in guide_view
    assert 'corner_radius=IOS_METRICS["pill_radius"]' in guide_view
    assert 'fg_color=COLORS["bg_content"]' in key_dialog
    assert 'fg_color=COLORS["bg_glass"]' in key_dialog
    assert 'corner_radius=IOS_METRICS["pill_radius"]' in key_dialog
    assert 'hover_color=COLORS["green_hover"]' in key_dialog
    assert 'hover_color=COLORS["danger_hover"]' in key_dialog
    assert 'fg_color=COLORS["bg_dark"]' not in guide_view
    assert 'fg_color=COLORS["bg_dark"]' not in key_dialog


def test_monitoring_editor_and_help_dialog_use_ios_tokens():
    monitoring_editor = MONITORING_EDITOR.read_text(encoding="utf-8")
    help_dialog = HELP_DIALOG.read_text(encoding="utf-8")

    assert "from .theme import COLORS, IOS_FONTS, IOS_METRICS" in monitoring_editor
    assert "from .theme import COLORS, IOS_METRICS" in help_dialog
    assert 'hover_color=COLORS["green_hover"]' in monitoring_editor
    assert 'hover_color=COLORS["danger_hover"]' in monitoring_editor
    assert 'hover_color=COLORS["hover_blue"]' in monitoring_editor
    assert 'fg_color=COLORS["scroll_purple"]' in monitoring_editor
    assert 'fg_color=COLORS["bg_glass"]' in help_dialog
    assert 'fg_color=COLORS["bg_log"]' in help_dialog
    assert 'corner_radius=IOS_METRICS["card_radius"]' in help_dialog
    assert 'corner_radius=IOS_METRICS["control_radius"]' in monitoring_editor
    assert not re.search(r"#[0-9A-Fa-f]{6}", monitoring_editor)
    assert not re.search(r"#[0-9A-Fa-f]{6}", help_dialog)
    for source in (monitoring_editor, help_dialog):
        assert "corner_radius=4" not in source
        assert "corner_radius=6" not in source
        assert "corner_radius=8" not in source
        assert "corner_radius=2" not in source
        assert 'text_color="gray"' not in source
        assert 'text_color="white"' not in source
