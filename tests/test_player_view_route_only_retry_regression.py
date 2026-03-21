from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAYER_VIEW = ROOT / "src" / "ui" / "player_view.py"


def test_route_only_does_not_expand_local_avoid_to_route_start_segments():
    text = PLAYER_VIEW.read_text(encoding="utf-8-sig")

    assert "_short_route_local_avoid" not in text
    assert "_segment_has_starts = _segment_has_route_starts(target_idx)" in text
    assert "_local_avoid_mode = not _segment_has_starts" in text


def test_route_only_relax_and_retry_are_guarded_for_failed_chokepoints():
    text = PLAYER_VIEW.read_text(encoding="utf-8-sig")

    assert "def _should_preserve_route_dir_avoid():" in text
    assert "def _is_route_only_failed_chokepoint(_cx, _cy, _dir, _goal_pos):" in text
    assert "if not _route_only_mode:" not in text[text.index("def _is_route_only_failed_chokepoint(_cx, _cy, _dir, _goal_pos):"):text.index("def press_key(direction):")]
    assert "_allow_route_dir_relax = not _should_preserve_route_dir_avoid()" in text
    assert "_route_avoid and _dir_avoid and _allow_route_dir_relax" in text
    assert "_route_failed_chokepoint = _is_route_only_failed_chokepoint(" in text
    assert "유일통로 국소회피" in text
    assert "_preserve_failed_edge = (" in text
    assert "재시도 중단" in text
