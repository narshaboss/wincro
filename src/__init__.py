"""
작업도우미 패키지.
"""

__version__ = "1.0.178"
__author__ = "윈크로"
__app_name__ = "작업도우미"
__app_name_ko__ = "작업도우미"

from .app import WinCroApp, get_app, run_app

__all__ = [
    "__version__",
    "__author__",
    "__app_name__",
    "__app_name_ko__",
    "WinCroApp",
    "get_app",
    "run_app",
]




