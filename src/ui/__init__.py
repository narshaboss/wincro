"""
GUI 모듈

- main_window.py: 메인 윈도우 (사이드바 + 하단 로그 패널)
- recorder_view.py: 녹화 화면
- analyzer_view.py: 분석 화면
- player_view.py: 실행 화면
- settings_view.py: 설정 화면
- guide_view.py: 가이드 화면
"""

from .main_window import MainWindow, BaseView, COLORS
from .recorder_view import RecorderView
from .analyzer_view import AnalyzerView
from .player_view import PlayerView
from .settings_view import SettingsView
from .guide_view import GuideView
from .help_dialog import HelpDialog, show_help_dialog

__all__ = [
    "MainWindow",
    "BaseView",
    "COLORS",
    "RecorderView",
    "AnalyzerView",
    "PlayerView",
    "SettingsView",
    "GuideView",
    "HelpDialog",
    "show_help_dialog",
]
