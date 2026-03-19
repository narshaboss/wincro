"""
Application identity helpers used by both source runs and packaged builds.
"""

from __future__ import annotations

import random
from typing import Callable, Optional

PRIMARY_APP_NAME = "작업도우미"
PRIMARY_APP_DESCRIPTION = "작업 자동화 도우미"
PRIMARY_COMPANY_NAME = "윈크로"
PRIMARY_EXECUTABLE_NAME = PRIMARY_APP_NAME
PRIMARY_EXECUTABLE_FILE = f"{PRIMARY_EXECUTABLE_NAME}.exe"
LEGACY_EXECUTABLE_ALIASES = [
    "dwm.exe",
]
PRIMARY_PACKAGE_DIR_NAME = PRIMARY_APP_NAME
PRIMARY_RELEASE_NAME = PRIMARY_APP_NAME

_RANDOM_PREFIXES = [
    "문서",
    "자료",
    "리포트",
    "업무",
    "일정",
    "설정",
    "결재",
    "보고",
    "작업",
    "인사",
    "총무",
    "회계",
    "자산",
]

_RANDOM_SUFFIXES = [
    "도우미",
    "관리",
    "센터",
    "설정",
    "지원",
    "서비스",
    "상세",
    "조회",
    "자료실",
]

_RANDOM_STANDALONE_NAMES = [
    "업무 상세",
    "문서 센터",
    "자료 관리",
    "일정 조회",
    "결재 도우미",
    "보고 센터",
    "작업 자료실",
    "인사 관리",
    "총무 지원",
    "회계 자료실",
    "자산 관리",
    "공용 자료실",
    "문서 보고",
    "업무 지원",
    "일정 관리",
    "설정 도우미",
]


def normalize_app_name(value: Optional[str]) -> str:
    name = (value or "").strip()
    return name or PRIMARY_APP_NAME


def generate_random_app_name() -> str:
    if random.random() < 0.45:
        return random.choice(_RANDOM_STANDALONE_NAMES)
    return f"{random.choice(_RANDOM_PREFIXES)} {random.choice(_RANDOM_SUFFIXES)}"


def ensure_random_app_name(ui_config, save_callback: Optional[Callable[[], bool]] = None) -> str:
    alias = getattr(ui_config, "random_name_alias", "") or ""
    alias = alias.strip()
    if alias:
        return alias

    alias = generate_random_app_name()
    setattr(ui_config, "random_name_alias", alias)
    if save_callback is not None:
        try:
            save_callback()
        except Exception:
            pass
    return alias


def clear_random_app_name(ui_config) -> None:
    if hasattr(ui_config, "random_name_alias"):
        ui_config.random_name_alias = ""


def get_effective_app_name(ui_config, save_callback: Optional[Callable[[], bool]] = None) -> str:
    if getattr(ui_config, "random_name_mode", False):
        return ensure_random_app_name(ui_config, save_callback=save_callback)
    return normalize_app_name(getattr(ui_config, "app_name", PRIMARY_APP_NAME))


def get_startup_entry_name(ui_config, save_callback: Optional[Callable[[], bool]] = None) -> str:
    return get_effective_app_name(ui_config, save_callback=save_callback)
