"""
Application identity helpers used by both source runs and packaged builds.
"""

from __future__ import annotations

from typing import Callable, Optional

PRIMARY_APP_NAME = "업무지원도구"
PRIMARY_APP_DESCRIPTION = "업무 지원 자동화 도구"
PRIMARY_COMPANY_NAME = "윈크로"
PRIMARY_EXECUTABLE_NAME = PRIMARY_APP_NAME
PRIMARY_EXECUTABLE_FILE = f"{PRIMARY_EXECUTABLE_NAME}.exe"
LEGACY_EXECUTABLE_ALIASES = [
    "작업도우미.exe",
    "WinCro.exe",
    "dwm.exe",
]
PRIMARY_PACKAGE_DIR_NAME = PRIMARY_APP_NAME
PRIMARY_RELEASE_NAME = PRIMARY_APP_NAME

def normalize_app_name(value: Optional[str]) -> str:
    name = (value or "").strip()
    return name or PRIMARY_APP_NAME


def get_effective_app_name(ui_config, save_callback: Optional[Callable[[], bool]] = None) -> str:
    return normalize_app_name(getattr(ui_config, "app_name", PRIMARY_APP_NAME))


def get_startup_entry_name(ui_config, save_callback: Optional[Callable[[], bool]] = None) -> str:
    return normalize_app_name(getattr(ui_config, "app_name", PRIMARY_APP_NAME))
