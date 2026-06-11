from pathlib import Path


def test_arrival_keys_use_immediate_tap_for_modified_arrows():
    source = Path("src/ui/player_view.py").read_text(encoding="utf-8")
    start = source.index("def _exec_arrival_keys")
    end = source.index("def _edit_arrival_key", start)
    body = source[start:end]

    assert "is_modified_direction" in body
    assert 'key_parts[-1] in {"up", "down", "left", "right"}' in body
    assert "input_ctrl.tap_combo_once(*key_parts)" in body
