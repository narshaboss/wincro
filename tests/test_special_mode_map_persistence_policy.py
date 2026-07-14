from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAYER_VIEW = ROOT / "src" / "ui" / "player_view.py"
MAP_RUNTIME = ROOT / "src" / "ui" / "player_map_runtime.py"


def _slice(text: str, start: str, end: str, offset: int = 0) -> str:
    begin = text.index(start, offset)
    finish = text.index(end, begin)
    return text[begin:finish]


def test_segment_map_name_lookup_has_no_file_side_effects():
    text = PLAYER_VIEW.read_text(encoding="utf-8-sig")
    canonical = _slice(
        text,
        "    def _get_segment_map_name(self, segment_idx: int) -> str:",
        "    def _resolve_segment_map_load_path(self, segment_idx: int) -> str:",
    )
    resolver = _slice(
        text,
        "    def _resolve_segment_map_load_path(self, segment_idx: int) -> str:",
        "    def _get_segment_display_name(self, segment_idx: int) -> str:",
    )

    assert "copy2(" not in canonical
    assert ".save(" not in canonical
    assert "copy2(" not in resolver
    assert ".save(" not in resolver


def test_map_lock_is_not_bypassed_by_mapping_test():
    text = PLAYER_VIEW.read_text(encoding="utf-8-sig")
    body = _slice(
        text,
        "    def _is_segment_map_locked(self, segment_idx: int) -> bool:",
        "    def _get_segment_waypoint_meta(self, segment_idx: int) -> dict:",
    )

    assert "_is_mapping_test" not in body
    assert "map_locked" in body


def test_mapping_start_checks_requested_segment_lock_and_resets_read_only_mode():
    text = PLAYER_VIEW.read_text(encoding="utf-8-sig")
    body = _slice(
        text,
        "    def _start_mapping(self):",
        "    def _run_mapping_loop(self):",
    )

    requested = body.index("requested_seg_idx = getattr(self, '_mapping_target_idx', None)")
    lock_check = body.index("self._is_segment_map_locked(seg_idx)")
    running = body.index("self._is_mapping = True")
    assert requested < lock_check < running
    assert "self._mapping_target_idx = None" in body
    assert "self._no_save_mode = False" in body


def test_individual_map_lock_cannot_change_during_execution_or_save():
    text = PLAYER_VIEW.read_text(encoding="utf-8-sig")
    body = _slice(
        text,
        "    def _toggle_map_lock(self, idx):",
        "    def _toggle_jump_ignore(self, idx):",
    )

    guard = body.index("getattr(self, '_is_mapping', False)")
    mutation = body.index("cfg['map_locked'] = locked")
    assert guard < mutation
    assert "getattr(self, '_is_mapping_test', False)" in body
    assert "getattr(self, '_is_running', False)" in body
    assert "self._map_save_lock.locked()" in body


def test_all_map_locks_cannot_change_during_execution_or_save():
    text = PLAYER_VIEW.read_text(encoding="utf-8-sig")
    body = _slice(
        text,
        "    def _toggle_all_map_locks(self):",
        "    def _toggle_group(self, parent_idx):",
    )

    guard = body.index("getattr(self, '_is_mapping', False)")
    mutation = body.index("cfg['map_locked'] = lock_all")
    assert guard < mutation
    assert "getattr(self, '_is_mapping_test', False)" in body
    assert "getattr(self, '_is_running', False)" in body
    assert "self._map_save_lock.locked()" in body


def test_settings_save_and_dialog_close_do_not_persist_maps():
    text = PLAYER_VIEW.read_text(encoding="utf-8-sig")
    save_body = _slice(text, "    def _save_config(self):", "    def _save_config_with_msg(self):")
    dialog_start = text.index("class GameModeDialog")
    dialog_end = text.index("class SequenceDetailDialog", dialog_start)
    close_start = text.rfind("    def _on_close(self):", dialog_start, dialog_end)
    close_body = text[close_start:dialog_end]

    assert "_auto_save_map" not in save_body
    assert "_auto_save_map" not in close_body


def test_runtime_save_gate_precedes_map_sanitization():
    text = MAP_RUNTIME.read_text(encoding="utf-8-sig")
    body = _slice(
        text,
        "    def _persist_map_snapshot(",
        "    def _load_segment_snapshot(",
    )

    lock_gate = body.index("owner._is_segment_map_locked(segment_idx)")
    session_gate = body.index("self._mapping_persistence_active(mapping_session)")
    sanitize = body.index("self.sanitize_segment_end_pos(game_map_ref, segment_idx)")
    assert lock_gate < sanitize
    assert session_gate < sanitize


def test_map_load_repair_never_writes_source_file():
    text = MAP_RUNTIME.read_text(encoding="utf-8-sig")
    body = _slice(
        text,
        "    def _load_segment_snapshot(",
        "    def auto_save_map(",
    )

    assert "game_map_ref.save(" not in body
    assert "로드 보정은 메모리에만 적용" in body
