import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
THEME = ROOT / "src" / "ui" / "theme.py"
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


def test_ui_source_tree_does_not_ship_stale_python_backups():
    stale_backups = [
        path.relative_to(ROOT).as_posix()
        for path in UI_DIR.iterdir()
        if path.name.endswith(".bak") or ".bak_" in path.name or ".pre_sanitize_" in path.name
    ]

    assert stale_backups == []


def test_ios_theme_tokens_drive_shared_ui_style():
    theme = THEME.read_text(encoding="utf-8")
    main_window = MAIN_WINDOW.read_text(encoding="utf-8")

    assert "IOS_METRICS = {" in theme
    assert '"card_radius": 22' in theme
    assert '"pill_radius": 999' in theme
    assert '"accent": "#0A84FF"' in theme
    assert '"bg_glass": "#161618"' in theme
    assert "from .theme import COLORS, IOS_FONTS, IOS_METRICS" in main_window
    assert 'corner_radius=IOS_METRICS["card_radius"]' in main_window
    assert 'corner_radius=IOS_METRICS["pill_radius"]' in main_window
    assert 'ctk.set_default_color_theme("dark-blue")' in main_window


def test_player_core_controls_use_ios_rounding_and_elevated_surfaces():
    player_view = PLAYER_VIEW.read_text(encoding="utf-8")

    assert "def _ios_state_button_style" in player_view
    assert "def _ios_repeat_button_style" in player_view
    assert "def _ios_run_button_style" in player_view
    assert 'fg_color=COLORS["bg_glass"]' in player_view
    assert 'fg_color=COLORS["bg_elevated"]' in player_view
    assert 'corner_radius=999' in player_view
    assert 'hover_color=COLORS["green_hover"]' in player_view
    assert 'hover_color=COLORS["danger_hover"]' in player_view
    assert 'widget.configure(fg_color=COLORS["selection_green"])' in player_view
    assert 'text_color=COLORS["warning"] if active else COLORS["text_secondary"]' in player_view
    assert 'border_color=COLORS["warning"] if is_active else COLORS["border"]' in player_view
    assert not re.search(r"#[0-9A-Fa-f]{6}", player_view)
    assert '"#facc15"' not in player_view
    assert '"#2e7d32"' not in player_view
    assert 'text_color="white"' not in player_view
    assert "corner_radius=4" not in player_view
    assert "corner_radius=8" not in player_view
    assert "corner_radius=6" not in player_view


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
    assert 'corner_radius=IOS_METRICS["control_radius"]' in analyzer_view
    assert 'fg_color=COLORS["bg_elevated"]' in analyzer_view
    assert 'outline=COLORS["info"]' in analyzer_view
    assert '"scroll": COLORS["scroll_purple"]' in analyzer_view
    assert "self._schedule_action_list_render_batch" in analyzer_view
    assert not re.search(r"#[0-9A-Fa-f]{6}", analyzer_view)
    assert 'text_color="white"' not in analyzer_view
    assert "corner_radius=6" not in analyzer_view
    assert "corner_radius=8" not in analyzer_view


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

    assert "from .theme import IOS_METRICS" in recorder_view
    assert "UiCallbackDispatcher" in recorder_view
    assert "VirtualScrollFrame(" in recorder_view
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

    assert "from .theme import COLORS, IOS_METRICS" in monitoring_editor
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
