from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAYER_VIEW = ROOT / "src" / "ui" / "player_view.py"


def test_detect_template_consensus_allows_strong_single_or_recent_hint_fallback():
    text = PLAYER_VIEW.read_text(encoding="utf-8-sig")

    assert "allow_single_strong: bool = False" in text
    assert "single_strong_threshold: float = 0.95" in text
    assert "recent_hint=None" in text
    assert "recent_hint_radius: int = 28" in text
    assert "if allow_single_strong and found:" in text
    assert '"fallback_source": _pick_name' in text


def test_detect_boss_pair_keeps_tracking_with_character_anchor_fallback():
    text = PLAYER_VIEW.read_text(encoding="utf-8-sig")

    assert '_last_boss_center = getattr(self, "_boss_detect_last_boss_center", None)' in text
    assert '_last_char_center = getattr(self, "_boss_detect_last_char_center", None)' in text
    assert '"char_anchor_source": None' in text
    assert '"char_anchor_valid": False' in text
    assert 'char_anchor_source = "recent_char"' in text
    assert 'char_anchor_source = "screen_center"' in text
    assert '"char_anchor_valid": True' in text


def test_patrolling_no_longer_discards_boss_when_character_anchor_is_available():
    text = PLAYER_VIEW.read_text(encoding="utf-8-sig")

    assert "_char_anchor_valid = bool(_det.get(\"char_anchor_valid\"))" in text
    assert "_char_anchor_source = _det.get(\"char_anchor_source\")" in text
    assert "if not _char_detected and not _char_anchor_valid:" in text
    assert "캐릭터 보정 사용" in text
