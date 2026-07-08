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


def test_local_mapping_frontier_direct_probe_does_not_reuse_cleared_target():
    src = Path(r"C:\Projects\wincro\src\ui\player_view.py").read_text(encoding="utf-8")
    marker = "if explore_target:\n                                _probing_unknown_from_frontier = False"
    start = src.index(marker)
    frontier_slice = src[start:src.index("if explore_target is not None and explore_target_tries >= 15:", start)]

    assert "_probing_unknown_from_frontier = True" in frontier_slice
    assert "explore_target = None" in frontier_slice
    assert "if _probing_unknown_from_frontier or explore_target is None:" in frontier_slice
    assert frontier_slice.index("if _probing_unknown_from_frontier or explore_target is None:") < frontier_slice.index("_cur_dist = abs(current_x - explore_target[0])")


def test_execution_start_clears_stale_runtime_coordinate_snapshots():
    src = Path(r"C:\Projects\wincro\src\ui\player_view.py").read_text(encoding="utf-8")
    start_slice = src[
        src.index("def _start_execution(self):"):
        src.index("self._stop_event.clear()", src.index("def _start_execution(self):"))
    ]

    assert "self._last_runtime_coord_snapshot = None" in start_slice
    assert "self._last_ocr_coord_snapshot = None" in start_slice
    assert "self._last_valid_runtime_coord_snapshot = None" in start_slice
