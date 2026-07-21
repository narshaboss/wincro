"""Strict dispatcher for isolated special-mode engines."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .profiles import (
    AKGUI_V2_PROFILE,
    WONGAK_LEGACY_PROFILE,
    get_special_mode_profile,
    normalize_special_mode_profile,
)


class SpecialModeRuntimeHost(Protocol):
    def _run_wongak_legacy_coordinate_loop(self) -> None: ...

    def _run_akgui_v2_coordinate_loop(self) -> None: ...


class SpecialModeEngine(Protocol):
    profile_id: str

    def run(self, host: SpecialModeRuntimeHost) -> None: ...


@dataclass(frozen=True)
class WongakLegacyEngine:
    profile_id: str = WONGAK_LEGACY_PROFILE

    def run(self, host: SpecialModeRuntimeHost) -> None:
        _assert_host_profile(host, self.profile_id)
        host._run_wongak_legacy_coordinate_loop()


@dataclass(frozen=True)
class AkguiV2Engine:
    profile_id: str = AKGUI_V2_PROFILE

    def run(self, host: SpecialModeRuntimeHost) -> None:
        _assert_host_profile(host, self.profile_id)
        host._run_akgui_v2_coordinate_loop()


_ENGINES = {
    WONGAK_LEGACY_PROFILE: WongakLegacyEngine(),
    AKGUI_V2_PROFILE: AkguiV2Engine(),
}


def _assert_host_profile(host: SpecialModeRuntimeHost, expected: str) -> None:
    configured = normalize_special_mode_profile(
        getattr(getattr(host, "_config", None), "engine_profile", "")
    )
    if configured != expected:
        raise RuntimeError(
            "Special-mode engine/profile mismatch: "
            f"engine={expected}, configured={configured}"
        )


def get_special_mode_engine(profile_id: str) -> SpecialModeEngine:
    normalized = normalize_special_mode_profile(profile_id)
    profile = get_special_mode_profile(normalized)
    engine = _ENGINES.get(profile.profile_id)
    if engine is None:
        raise RuntimeError(f"No engine registered for special-mode profile: {normalized}")
    return engine
