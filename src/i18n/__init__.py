"""
다국어 지원 모듈

현재 지원 언어:
- ko: 한국어 (기본)
"""

from .ko import (
    TRANSLATIONS,
    get_text,
    t,
    APP_INFO,
    MENU,
    VIEWS,
    RECORDER,
    ANALYZER,
    PLAYER,
    SEQUENCE,
    SETTINGS,
    ACTION_TYPES,
    BUTTONS,
    MESSAGES,
    ERRORS,
    TOOLTIPS,
)

__all__ = [
    "TRANSLATIONS",
    "get_text",
    "t",
    "APP_INFO",
    "MENU",
    "VIEWS",
    "RECORDER",
    "ANALYZER",
    "PLAYER",
    "SEQUENCE",
    "SETTINGS",
    "ACTION_TYPES",
    "BUTTONS",
    "MESSAGES",
    "ERRORS",
    "TOOLTIPS",
]
