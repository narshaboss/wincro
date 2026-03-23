from pathlib import Path


def test_mapping_guard_before_commit_result_is_checked():
    source = Path(r"C:\Projects\wincro\src\ui\player_view.py").read_text(encoding="utf-8")

    assert 'if _guard_mapping_completion_before_commit(current_x, current_y, "before-commit"):' in source

