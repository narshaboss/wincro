"""WinCro UI package with compatibility-preserving lazy exports."""

from importlib import import_module
from typing import TYPE_CHECKING


_LAZY_EXPORTS = {
    "MainWindow": (".main_window", "MainWindow"),
    "BaseView": (".main_window", "BaseView"),
    "COLORS": (".main_window", "COLORS"),
    "RecorderView": (".recorder_view", "RecorderView"),
    "AnalyzerView": (".analyzer_view", "AnalyzerView"),
    "PlayerView": (".player_view", "PlayerView"),
    "SettingsView": (".settings_view", "SettingsView"),
    "GuideView": (".guide_view", "GuideView"),
    "HelpDialog": (".help_dialog", "HelpDialog"),
    "show_help_dialog": (".help_dialog", "show_help_dialog"),
}

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str):
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = target
    value = getattr(import_module(module_name, __name__), attribute_name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))


if TYPE_CHECKING:
    from .analyzer_view import AnalyzerView
    from .guide_view import GuideView
    from .help_dialog import HelpDialog, show_help_dialog
    from .main_window import BaseView, COLORS, MainWindow
    from .player_view import PlayerView
    from .recorder_view import RecorderView
    from .settings_view import SettingsView
