from pathlib import Path


def test_local_mapping_frontier_recomputes_unknown_dirs_for_new_target():
    src = Path(r"C:\Projects\wincro\src\ui\player_view.py").read_text(encoding="utf-8")

    assert "def _frontier_unknown_dirs(_fx, _fy):" in src

    recompute_marker = (
        "if explore_target is not None:\n"
        "                                _et_unknown_dirs = _frontier_unknown_dirs(explore_target[0], explore_target[1])"
    )
    assert recompute_marker in src


def test_local_mapping_frontier_validation_uses_shared_unknown_dir_helper():
    src = Path(r"C:\Projects\wincro\src\ui\player_view.py").read_text(encoding="utf-8")

    assert "_et_unknown_dirs = _frontier_unknown_dirs(et[0], et[1])" in src
