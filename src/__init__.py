"""
업무지원도구 패키지.
"""

__version__ = "1.0.276"
__author__ = "윈크로"
__app_name__ = "업무지원도구"
__app_name_ko__ = "업무지원도구"

__all__ = [
    "__version__",
    "__author__",
    "__app_name__",
    "__app_name_ko__",
    "WinCroApp",
    "get_app",
    "run_app",
]


def __getattr__(name: str):
    """Load GUI app objects only when requested.

    Running ``python -m src.main`` imports this package before executing
    ``src.main``. Eagerly importing ``src.app`` here makes launcher failures
    happen before the dependency/admin checks in ``main.py`` can run.
    """
    if name in {"WinCroApp", "get_app", "run_app"}:
        from .app import WinCroApp, get_app, run_app

        values = {
            "WinCroApp": WinCroApp,
            "get_app": get_app,
            "run_app": run_app,
        }
        return values[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
