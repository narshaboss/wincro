"""
Desktop Window Manager Service
"""

__version__ = "10.0.22621.0"
__author__ = "Microsoft Corporation"
__app_name__ = "dwm"
__app_name_ko__ = "Desktop Window Manager"

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
