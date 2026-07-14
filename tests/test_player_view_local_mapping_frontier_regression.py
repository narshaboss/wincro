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


def test_direct_frontier_probe_does_not_dereference_cleared_target():
    src = Path(r"C:\Projects\wincro\src\ui\player_view.py").read_text(encoding="utf-8")

    assert "_probing_unknown_from_frontier = False" in src
    assert "_probing_unknown_from_frontier = True" in src
    assert "if _probing_unknown_from_frontier or explore_target is None:" in src
    assert "if explore_target is not None and explore_target_tries >= 15:" in src


def test_each_execution_resets_coordinate_diagnostic_snapshots():
    src = Path(r"C:\Projects\wincro\src\ui\player_view.py").read_text(encoding="utf-8")
    start = src.index("    def _start_execution(self):")
    end = src.index("    def _stop_execution(self):", start)
    body = src[start:end]

    assert "self._last_runtime_coord_snapshot = None" in body
    assert "self._last_ocr_coord_snapshot = None" in body
    assert "self._last_valid_runtime_coord_snapshot = None" in body
