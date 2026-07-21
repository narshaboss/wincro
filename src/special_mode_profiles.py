"""Immutable profile definitions shared by config and runtime dispatch.

This module deliberately lives outside ``src.player`` so automation model
deserialization does not import the player package and create a circular
dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


WONGAK_LEGACY_PROFILE = "wongak_legacy_v1"
AKGUI_V2_PROFILE = "akgui_v2"
DEFAULT_SPECIAL_MODE_PROFILE = WONGAK_LEGACY_PROFILE


@dataclass(frozen=True)
class SpecialModeProfile:
    profile_id: str
    display_name: str
    behavior_version: int
    map_namespace: str
    protected: bool
    description: str


_PROFILES: Mapping[str, SpecialModeProfile] = MappingProxyType(
    {
        WONGAK_LEGACY_PROFILE: SpecialModeProfile(
            profile_id=WONGAK_LEGACY_PROFILE,
            display_name="원각공장 알고리즘",
            behavior_version=1,
            map_namespace="wongak_legacy_v1",
            protected=True,
            description="검증된 원각공장 전용 레거시 엔진입니다.",
        ),
        AKGUI_V2_PROFILE: SpecialModeProfile(
            profile_id=AKGUI_V2_PROFILE,
            display_name="악귀문 알고리즘",
            behavior_version=2,
            map_namespace="akgui_v2",
            protected=False,
            description="악귀문공장 전용 독립 엔진입니다.",
        ),
    }
)

# Migration metadata only. Runtime dispatch never uses plan names or shapes.
_LEGACY_AKGUI_PLAN_IDS = frozenset({"plan_20260708_121550"})
_LEGACY_AKGUI_RULE_IDS = frozenset({"rule_584defa4"})


def get_special_mode_profiles() -> tuple[SpecialModeProfile, ...]:
    return tuple(_PROFILES.values())


def get_special_mode_profile(profile_id: str) -> SpecialModeProfile:
    normalized = normalize_special_mode_profile(profile_id)
    return _PROFILES[normalized]


def normalize_special_mode_profile(profile_id: str | None) -> str:
    value = str(profile_id or "").strip()
    if not value:
        return DEFAULT_SPECIAL_MODE_PROFILE
    if value not in _PROFILES:
        raise ValueError(f"Unknown special-mode engine profile: {value}")
    return value


def infer_legacy_special_mode_profile(
    *,
    plan_id: str = "",
    rule_id: str = "",
) -> str:
    """Migrate an untagged legacy config without runtime name heuristics."""
    if str(plan_id or "") in _LEGACY_AKGUI_PLAN_IDS:
        return AKGUI_V2_PROFILE
    if str(rule_id or "") in _LEGACY_AKGUI_RULE_IDS:
        return AKGUI_V2_PROFILE
    return WONGAK_LEGACY_PROFILE
