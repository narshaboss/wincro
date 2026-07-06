from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN_WINDOW = ROOT / "src" / "ui" / "main_window.py"
ANALYZER_VIEW = ROOT / "src" / "ui" / "analyzer_view.py"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def test_f9_template_video_capture_has_full_or_crop_mode_before_start():
    source = _text(MAIN_WINDOW)
    toggle_slice = source[
        source.index("def _toggle_template_video_capture(self):"):
        source.index("def _start_template_video_capture_locked", source.index("def _toggle_template_video_capture"))
    ]

    assert "def _choose_template_video_capture_mode(self) -> Optional[str]:" in source
    assert "def _select_template_video_crop_region(self):" in source
    assert "def _normalize_template_video_region(self, x1, y1, x2, y2) -> Optional[dict]:" in source
    assert 'mode = self._choose_template_video_capture_mode()' in toggle_slice
    assert 'self._select_template_video_crop_region()' in toggle_slice
    assert 'self._start_template_video_capture_locked(mode="full", region=None)' in toggle_slice
    assert 'self._start_template_video_capture_locked(mode="crop", region=region)' in source


def test_f9_template_video_crop_capture_records_selected_region_directly():
    source = _text(MAIN_WINDOW)
    run_slice = source[
        source.index("def _run_template_video_capture"):
        source.index("def _finish_template_video_capture", source.index("def _run_template_video_capture"))
    ]

    assert "self._template_video_mode = mode" in source
    assert "self._template_video_region = dict(region) if region else None" in source
    assert "args=(stop_event, output_path, mode, dict(region) if region else None)" in source
    assert 'monitor = dict(region) if region else dict(sct.monitors[0])' in run_slice
    assert 'if width % 2:' in run_slice
    assert 'if height % 2:' in run_slice
    assert 'frame = frame[:height, :width]' in run_slice
    assert 'cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)' in run_slice
    assert '"F9 CROP ON" if self._template_video_mode == "crop" else "F9 VIDEO ON"' in source


def test_video_crop_editor_reserves_extra_space_for_video_controls():
    source = _text(ANALYZER_VIEW)
    crop_slice = source[
        source.index("class ImageCropDialog"):
        source.index("class AltImageDialog", source.index("class ImageCropDialog"))
    ]

    assert "side_reserved = 360 if self._is_video else 300" in crop_slice
    assert "win_w = self._canvas_width + (360 if self._is_video else 300)" in crop_slice
    assert "width=230 if self._is_video else 200" in crop_slice
    assert "width=190" in crop_slice
    assert "height=34" in crop_slice
    assert 'text="재생"' in crop_slice
    assert 'text="중지"' in crop_slice
    assert 'text="처음으로"' in crop_slice
    assert "self._video_play_btn.pack(fill=\"x\", pady=(0, 6))" in crop_slice
    assert "self._video_stop_btn.pack(fill=\"x\", pady=(0, 6))" in crop_slice
    assert "self._video_progress_canvas = Canvas(" in crop_slice
    assert "def _update_video_progress_bar(self):" in crop_slice
    assert "self._video_play_btn.configure(text=\"재생\")" in crop_slice
