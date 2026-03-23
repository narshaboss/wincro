from pathlib import Path


def test_single_mapping_test_uses_selected_target_idx():
    source = Path(r"C:\Projects\wincro\src\ui\player_view.py").read_text(encoding="utf-8")

    assert "if single_mode and 0 <= single_idx < len(all_targets):" in source
    assert "target_idx = single_idx" in source
    assert "start_idx = 0" in source
    assert "start_idx = single_idx if single_mode else 0" not in source
