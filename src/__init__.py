"""
업무지원도구 패키지.
"""

__version__ = "1.0.245"
__author__ = "윈크로"
__app_name__ = "업무지원도구"
__app_name_ko__ = "업무지원도구"

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
