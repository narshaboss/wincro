from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAYER_VIEW = ROOT / "src" / "ui" / "player_view.py"


def _read_text() -> str:
    return PLAYER_VIEW.read_text(encoding="utf-8-sig")


def test_stop_reason_log_includes_human_readable_description_and_detail():
    text = _read_text()
    gm_start = text.index("class GameModeDialog")
    stop_slice = text[
        text.index("def _stop_execution(self):", gm_start):
        text.index("# UI 업데이트", text.index("def _stop_execution(self):", gm_start))
    ]
    ui_slice = text[
        text.index("self._append_log(f\"실행 중지 - 총 키입력:", gm_start):
        text.index("# 맵핑 카드 버튼 복원")
    ]

    assert "def _describe_stop_reason(self, reason):" in text
    assert '"max_stagnation_reached": "장기 정체: 목표까지 거리 개선 없이 반복 한계 도달"' in text
    assert "def _format_stop_reason_for_log(self):" in text
    assert "reason_desc={self._describe_stop_reason(self._stop_reason or 'unknown')}" in stop_slice
    assert "🧭 중단사유: {self._format_stop_reason_for_log()}" in ui_slice
    assert "🧭 중단상세: {self._stop_detail}" in ui_slice


def test_stop_coordinate_log_keeps_primary_latest_and_ocr_snapshots():
    text = _read_text()
    coord_slice = text[
        text.index("def _get_stop_coordinate_log_lines(self):"):
        text.index("def _remember_runtime_issue(self, issue, detail=\"\", overwrite=False):")
    ]

    assert "📍 중단전 좌표:" in coord_slice
    assert "📍 마지막 판정좌표:" in coord_slice
    assert "📍 마지막 OCR좌표:" in coord_slice
    assert "_seen = set()" in coord_slice
    assert '_latest.get("source") != "ocr"' in coord_slice


def test_stop_route_diagnostic_log_keeps_last_route_snapshot():
    text = _read_text()
    route_slice = text[
        text.index("def _remember_route_diagnostic_snapshot(self, snapshot):"):
        text.index("def _remember_runtime_issue(self, issue, detail=\"\", overwrite=False):")
    ]
    gm_start = text.index("class GameModeDialog")
    stop_start = text.index("def _stop_execution(self):", gm_start)
    stop_slice = text[
        stop_start:
        text.index("def _run_loop(self):", stop_start)
    ]

    assert "def _get_stop_route_diagnostic_log_lines(self, max_age_s=45.0):" in route_slice
    assert "self._last_route_diagnostic_snapshot = _snapshot" in route_slice
    assert "blocked_dirs" in route_slice
    assert "suppressed_dirs" in route_slice
    assert "edge_fail" in route_slice
    assert "_route_diag_log_lines = self._get_stop_route_diagnostic_log_lines()" in stop_slice
    assert "route_diag={_route_diag_text}" in stop_slice
    assert "for _route_diag_line in _route_diag_log_lines:" in text


def test_runtime_coordinates_are_captured_before_stop_event_is_set():
    text = _read_text()
    gm_start = text.index("class GameModeDialog")
    stop_slice = text[
        text.index("def _stop_execution(self):", gm_start):
        text.index("# UI 업데이트", text.index("def _stop_execution(self):", gm_start))
    ]

    assert 'source="ocr"' in text
    assert 'source="movement_origin"' in text
    assert 'source="arrival_recheck_arrived"' in text
    assert "_stop_coord_log_lines = self._get_stop_coordinate_log_lines()" in stop_slice
    assert stop_slice.index("_stop_coord_log_lines = self._get_stop_coordinate_log_lines()") < stop_slice.index("self._stop_event.set()")
