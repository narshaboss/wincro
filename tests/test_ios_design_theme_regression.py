from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
THEME = ROOT / "src" / "ui" / "theme.py"
MAIN_WINDOW = ROOT / "src" / "ui" / "main_window.py"
PLAYER_VIEW = ROOT / "src" / "ui" / "player_view.py"
ANALYZER_VIEW = ROOT / "src" / "ui" / "analyzer_view.py"


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

    assert 'fg_color=COLORS["bg_glass"]' in player_view
    assert 'fg_color=COLORS["bg_elevated"]' in player_view
    assert 'corner_radius=999' in player_view
    assert 'hover_color=COLORS["green_hover"]' in player_view
    assert 'hover_color=COLORS["danger_hover"]' in player_view
    assert 'widget.configure(fg_color=COLORS["selection_green"])' in player_view


def test_editor_action_rows_use_ios_cards_without_changing_virtual_scroll_contract():
    player_view = PLAYER_VIEW.read_text(encoding="utf-8")

    assert "from .theme import COLORS, IOS_FONTS, IOS_METRICS" in player_view
    assert 'fg_color=COLORS["selection_green"] if is_selected else (COLORS["bg_glass"] if is_enabled else COLORS["bg_card"])' in player_view
    assert 'corner_radius=IOS_METRICS["control_radius"]' in player_view
    assert 'corner_radius=IOS_METRICS["card_radius_compact"]' in player_view
    assert 'fg_color=COLORS["bg_elevated"]' in player_view
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
    assert "self._schedule_action_list_render_batch" in analyzer_view


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
