from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
THEME = ROOT / "src" / "ui" / "theme.py"
MAIN_WINDOW = ROOT / "src" / "ui" / "main_window.py"
PLAYER_VIEW = ROOT / "src" / "ui" / "player_view.py"


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
