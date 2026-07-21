"""Isolated special-mode engine profiles.

Only UI/input/OCR infrastructure may be shared between profiles. Movement,
mapping, recovery, and transition policy must live behind an engine boundary.
"""

from .profiles import (
    AKGUI_V2_PROFILE,
    DEFAULT_SPECIAL_MODE_PROFILE,
    WONGAK_LEGACY_PROFILE,
    SpecialModeProfile,
    get_special_mode_profile,
    infer_legacy_special_mode_profile,
    normalize_special_mode_profile,
)

__all__ = [
    "AKGUI_V2_PROFILE",
    "DEFAULT_SPECIAL_MODE_PROFILE",
    "WONGAK_LEGACY_PROFILE",
    "SpecialModeProfile",
    "get_special_mode_profile",
    "infer_legacy_special_mode_profile",
    "normalize_special_mode_profile",
]
