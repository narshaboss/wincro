from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


TRUSTED_ANCHOR_SOURCES = {"template", "recent_char"}


@dataclass(frozen=True)
class BossVisualMatch:
    found: bool = False
    confidence: float = 0.0
    source: str | None = None
    result: Any = None


@dataclass(frozen=True)
class CharacterAnchor:
    found: bool = False
    valid: bool = False
    trusted: bool = False
    source: str | None = None
    result: Any = None


@dataclass(frozen=True)
class BossFrameEvidence:
    found: bool = False
    visual: BossVisualMatch = field(default_factory=BossVisualMatch)
    anchor: CharacterAnchor = field(default_factory=CharacterAnchor)
    ocr_fallback_used: bool = False
    detect_reason: str | None = None
    match_source: str | None = None
    boss_target_text: str = ""
    boss_match_text: str = ""
    boss_ocr_variant: str = ""
    confidence: float = 0.0
    dx_px: int = 0
    dy_px: int = 0
    dx_tiles: int = 0
    dy_tiles: int = 0
    tile_dist: int = 0
    pixel_dist: float = 0.0
    roi_applied: bool = False
    search_region: list[int] | None = None
    raw_det: dict[str, Any] = field(default_factory=dict)
    raw_result: Any = None

    @property
    def visual_found(self) -> bool:
        return bool(self.visual.found)

    @property
    def char_found(self) -> bool:
        return bool(self.anchor.found)

    @property
    def anchor_valid(self) -> bool:
        return bool(self.anchor.valid)

    @property
    def anchor_source(self) -> str | None:
        return self.anchor.source

    @property
    def trusted_anchor(self) -> bool:
        return bool(self.anchor.trusted)


class BossDetector:
    def __init__(
        self,
        *,
        detect_pair_with_region_fallback: Callable[..., dict[str, Any]],
        detect_pair: Callable[..., dict[str, Any]] | None = None,
    ):
        self._detect_pair_with_region_fallback = detect_pair_with_region_fallback
        self._detect_pair = detect_pair

    def detect_frame(
        self,
        screen,
        boss_img_path: str,
        char_img_path: str | None,
        *,
        search_region=None,
        relaxed_boss: bool = False,
        relaxed_char: bool = False,
        full_screen_fallback: bool = False,
        force_full_screen: bool = False,
        allow_ocr_fallback: bool = True,
        allow_screen_center_anchor: bool = False,
    ) -> BossFrameEvidence:
        if force_full_screen and self._detect_pair is not None:
            det = self._detect_pair(
                screen,
                boss_img_path,
                char_img_path or "",
                search_region=None,
                force_full_screen=True,
                relaxed_boss=relaxed_boss,
                relaxed_char=relaxed_char,
                allow_screen_center_anchor=allow_screen_center_anchor,
            ) or {}
        else:
            det = self._detect_pair_with_region_fallback(
                screen,
                boss_img_path,
                char_img_path or "",
                search_region=search_region,
                relaxed_boss=relaxed_boss,
                relaxed_char=relaxed_char,
                allow_screen_center_anchor=allow_screen_center_anchor,
                full_screen_fallback=full_screen_fallback,
            ) or {}

        boss_result = det.get("boss_result")
        boss_match_source = str(det.get("boss_match_source") or "")
        boss_found = bool(boss_result and getattr(boss_result, "found", False))
        visual_found = bool(boss_found and boss_match_source != "ocr_text")
        ocr_fallback_used = bool(boss_found and boss_match_source == "ocr_text")
        effective_found = bool(visual_found or (allow_ocr_fallback and ocr_fallback_used))

        anchor_source = str(det.get("char_anchor_source") or "") or None
        char_found = bool(det.get("char_found"))
        anchor_valid = bool(det.get("char_anchor_valid"))
        trusted_anchor = bool(char_found or (anchor_valid and anchor_source in TRUSTED_ANCHOR_SOURCES))

        raw_det = dict(det)
        raw_det["visual_found"] = visual_found
        raw_det["ocr_fallback_used"] = ocr_fallback_used
        raw_det["effective_found"] = effective_found
        raw_det["char_anchor_trusted"] = trusted_anchor
        raw_det["visual_source"] = None if not visual_found else (boss_match_source or "visual")

        return BossFrameEvidence(
            found=effective_found,
            visual=BossVisualMatch(
                found=visual_found,
                confidence=float(getattr(boss_result, "confidence", 0.0) or 0.0) if visual_found else 0.0,
                source=raw_det["visual_source"],
                result=boss_result if visual_found else None,
            ),
            anchor=CharacterAnchor(
                found=char_found,
                valid=anchor_valid,
                trusted=trusted_anchor,
                source=anchor_source,
                result=det.get("char_result"),
            ),
            ocr_fallback_used=ocr_fallback_used,
            detect_reason=str(det.get("detect_reason") or ""),
            match_source=boss_match_source or None,
            boss_target_text=str(det.get("boss_target_text") or ""),
            boss_match_text=str(det.get("boss_match_text") or ""),
            boss_ocr_variant=str(det.get("boss_ocr_variant") or ""),
            confidence=float(getattr(boss_result, "confidence", 0.0) or 0.0),
            dx_px=int(det.get("dx_px", 0) or 0),
            dy_px=int(det.get("dy_px", 0) or 0),
            dx_tiles=int(det.get("dx_tiles", 0) or 0),
            dy_tiles=int(det.get("dy_tiles", 0) or 0),
            tile_dist=int(det.get("tile_dist", 0) or 0),
            pixel_dist=float(det.get("pixel_dist", 0.0) or 0.0),
            roi_applied=bool(det.get("roi_applied")),
            search_region=list(det.get("search_region") or []) or None,
            raw_det=raw_det,
            raw_result=boss_result if effective_found else None,
        )
