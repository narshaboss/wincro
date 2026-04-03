"""WinCro package."""

__version__ = "1.0.199"
__author__ = "WinCro"
__app_name__ = "WinCro"
__app_name_ko__ = "윈크로"

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
