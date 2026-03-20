from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAYER_VIEW = ROOT / "src" / "ui" / "player_view.py"


def test_portal_helper_does_not_capture_current_pos_directly():
    text = PLAYER_VIEW.read_text(encoding="utf-8-sig")

    assert "def _is_portal_step_forbidden(_x, _y, _goal=None, _current=None):" in text
    assert "if _pos == current_pos:" not in text


def test_portal_helper_call_sites_pass_current_position():
    text = PLAYER_VIEW.read_text(encoding="utf-8-sig")

    expected_calls = [
        "_is_portal_step_forbidden(_nx, _ny, _goal_pos, (_cx, _cy))",
        "_is_portal_step_forbidden(next_pos[0], next_pos[1], target_pos, current_pos)",
        "_is_portal_step_forbidden(_ppx, _ppy, _goal, current_pos)",
        "_is_portal_step_forbidden(nx, ny, target_pos, current_pos)",
    ]

    for call in expected_calls:
        assert call in text
