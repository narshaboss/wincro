from pathlib import Path
from types import SimpleNamespace

from src.player.boss_detector import BossDetector


ROOT = Path(__file__).resolve().parents[1]
PLAYER_VIEW = ROOT / "src" / "ui" / "player_view.py"
STATE_MACHINE = ROOT / "src" / "player" / "boss_state_machine.py"
DETECTOR = ROOT / "src" / "player" / "boss_detector.py"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def _between(text: str, start: str, end: str) -> str:
    _start = text.index(start)
    _end = text.index(end, _start)
    return text[_start:_end]


def test_player_view_uses_boss_detector_bridge_helpers():
    text = _text(PLAYER_VIEW)

    assert "from ..player.boss_detector import BossDetector, BossFrameEvidence" in text
    assert "def _get_boss_frame_detector(self) -> BossDetector:" in text
    assert "def _make_boss_detection_from_evidence(evidence: BossFrameEvidence | None) -> BossDetection | None:" in text


def test_runtime_bridge_uses_buffer_state_not_candidate_mode():
    text = _text(PLAYER_VIEW)
    bridge = _between(text, "def _get_boss_runtime_state():", "def _clear_boss_combat_flags():")

    assert "buffer=BossEvidenceBuffer(" in bridge
    assert "_state.buffer.hits" in bridge
    assert "_state.buffer.visual_hits" in bridge
    assert "_state.buffer.last_match_source" in bridge
    assert "_state_mode in (\"exploring\", MODE_PATROLLING, MODE_CHASING)" in bridge
    assert "candidate=BossCandidateState(" not in bridge
    assert "_state.candidate." not in bridge


def test_patrol_branch_uses_detector_and_drops_runtime_candidate_hold_mode():
    text = _text(PLAYER_VIEW)
    patrol = _between(text, "elif boss_mode in (MODE_PATROLLING, MODE_CHASING):", "_t_boss_ms = int((time.time() - _t_boss_start) * 1000)")

    assert "_patrol_async_mode = boss_mode == MODE_PATROLLING and not _boss_chasing" in patrol
    assert "_evidence = _async_payload.get(\"evidence\")" in patrol
    assert "self._get_boss_frame_detector().detect_frame(" in patrol
    assert "self._make_boss_detection_from_evidence(_evidence)" in patrol
    assert "_detect_boss_pair_with_region_fallback(" not in patrol
    assert "MODE_CANDIDATE_HOLD" not in patrol


def test_approach_and_reacquire_paths_use_detector_evidence():
    text = _text(PLAYER_VIEW)

    assert "_appr_evidence3 = self._get_boss_frame_detector().detect_frame(" in text
    assert "_approach_detection = self._make_boss_detection_from_evidence(_appr_evidence3)" in text
    assert "_evidence2 = self._get_boss_frame_detector().detect_frame(" in text


def test_debug_snapshot_reports_buffer_candidate_flag():
    text = _text(PLAYER_VIEW)
    block = _between(text, "def _build_boss_debug_snapshot(", "def _make_bosstest_preview_image(")

    assert "candidate_active: bool = False" in block
    assert "f\"candidate={'Y' if candidate_active else 'N'}\"" in block
    assert "MODE_CANDIDATE_HOLD" not in block


def test_bosstest_uses_same_detector_and_reports_visual_vs_ocr_fields():
    text = _text(PLAYER_VIEW)
    payload = _between(text, "def _build_bosstest_ocr_payload(", "def _build_bosstest_overlay_bgr(")
    live = _between(text, "def _run_bosstest_live_detect_once(self):", "def _run_bosstest_live_session(self, *, sample_count: int = 1, interval_s: float = 0.12):")
    run = _between(text, "def _bosstest_run(self):", "def _bosstest_run_continuous(self):")

    assert "char_img_path: str | None = None" in payload
    assert "self._get_boss_frame_detector().detect_frame(" in payload
    assert "\"visual_source\": _evidence.visual.source" in payload
    assert "\"ocr_fallback_used\": bool(_evidence.ocr_fallback_used)" in payload
    assert "\"stabilized_state\": _stabilized_state" in payload
    assert "_char_image_path = self._resolve_template_image_path(self._bosstest_char_image_path or \"\")" in live
    assert "_char_image_path," in live
    assert "state={_det.get('stabilized_state') or '-'}" in run
    assert "visual={_det.get('visual_source') or '-'}" in run
    assert "ocr_fb={'Y' if _det.get('ocr_fallback_used') else 'N'}" in run


def test_detector_force_full_screen_uses_direct_detect_pair():
    calls = {"fallback": 0, "direct": 0}

    def _fallback(*_args, **_kwargs):
        calls["fallback"] += 1
        return {}

    def _direct(*_args, **_kwargs):
        calls["direct"] += 1
        return {
            "boss_result": SimpleNamespace(found=False, confidence=0.0),
            "boss_match_source": None,
            "char_found": False,
            "char_anchor_valid": False,
            "char_anchor_source": None,
            "dx_px": 0,
            "dy_px": 0,
            "dx_tiles": 0,
            "dy_tiles": 0,
            "tile_dist": 0,
            "pixel_dist": 0.0,
            "roi_applied": False,
        }

    detector = BossDetector(
        detect_pair_with_region_fallback=_fallback,
        detect_pair=_direct,
    )
    detector.detect_frame(
        object(),
        "boss.png",
        "char.png",
        force_full_screen=True,
    )

    assert calls["direct"] == 1
    assert calls["fallback"] == 0


def test_detector_treats_screen_center_as_untrusted_and_ocr_as_fallback_only():
    def _detect(*_args, **_kwargs):
        return {
            "boss_result": SimpleNamespace(found=True, confidence=0.915),
            "boss_match_source": "ocr_text",
            "char_found": False,
            "char_anchor_valid": True,
            "char_anchor_source": "screen_center",
            "dx_px": -160,
            "dy_px": -212,
            "dx_tiles": -4,
            "dy_tiles": -5,
            "tile_dist": 5,
            "pixel_dist": 265.6,
            "roi_applied": True,
            "search_region": [14, 35, 1400, 884],
        }

    evidence = BossDetector(detect_pair_with_region_fallback=_detect).detect_frame(
        object(),
        "boss.png",
        "char.png",
    )

    assert evidence.found is True
    assert evidence.visual_found is False
    assert evidence.ocr_fallback_used is True
    assert evidence.anchor_valid is True
    assert evidence.trusted_anchor is False


def test_detector_marks_visual_template_hit_and_trusted_anchor():
    def _detect(*_args, **_kwargs):
        return {
            "boss_result": SimpleNamespace(found=True, confidence=0.878),
            "boss_match_source": "binary",
            "char_found": True,
            "char_anchor_valid": True,
            "char_anchor_source": "template",
            "dx_px": 18,
            "dy_px": 81,
            "dx_tiles": 0,
            "dy_tiles": 2,
            "tile_dist": 2,
            "pixel_dist": 82.0,
            "roi_applied": True,
            "search_region": [14, 35, 1400, 884],
        }

    evidence = BossDetector(detect_pair_with_region_fallback=_detect).detect_frame(
        object(),
        "boss.png",
        "char.png",
    )

    assert evidence.found is True
    assert evidence.visual_found is True
    assert evidence.ocr_fallback_used is False
    assert evidence.trusted_anchor is True
    assert evidence.visual.source == "binary"


def test_state_machine_module_is_three_state_and_buffer_based():
    text = _text(STATE_MACHINE)

    assert "class BossEvidenceBuffer:" in text
    assert "class BossTrackerState:" in text
    assert "PATROL_BUFFER_PROMOTE_HITS = 2" in text
    assert "if detection.ocr_fallback_used and not detection.visual_found:" in text
    assert "return BossDecision(next_state=next_state, event=\"candidate_hold\")" in text
    assert "MODE_CANDIDATE_HOLD = \"candidate_hold\"  # compatibility only; no longer used as runtime mode" in text


def test_detector_module_exposes_frame_evidence_types():
    text = _text(DETECTOR)

    assert "class BossVisualMatch:" in text
    assert "class CharacterAnchor:" in text
    assert "class BossFrameEvidence:" in text
    assert "class BossDetector:" in text
    assert "force_full_screen: bool = False" in text
