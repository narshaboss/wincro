from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ANALYZER_VIEW = ROOT / "src" / "ui" / "analyzer_view.py"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def test_image_crop_dialog_hides_freeform_mask_controls():
    text = _text(ANALYZER_VIEW)

    assert 'text="영역 선택"' in text
    assert 'text="마스크 지우기"' not in text
    assert 'text="마스크 복원"' not in text
    assert 'text="마스크 초기화"' not in text
    assert 'text="자동 배경 제거"' not in text
    assert 'text="배경제거 이미지따기"' in text
    assert "마스크 모드에서는 좌클릭 브러시" not in text
    assert "self._brush_slider = ctk.CTkSlider(" not in text
    assert "def _apply_mask_brush_at_canvas(" not in text
    assert "self._set_edit_mode(\"select\")" in text


def test_image_crop_dialog_uses_sidecar_mask_pipeline():
    text = _text(ANALYZER_VIEW)

    assert "self._full_image_mask = load_sidecar_mask(self._image_path, (h, w))" in text
    assert "self._crop_mask = auto_extract_foreground_mask(cropped)" in text
    assert "self._crop_mask_needs_refresh = True" in text
    assert "def _ensure_current_crop_mask(self, *, refresh_view: bool = False):" in text
    assert "self._ensure_current_crop_mask()" in text
    assert "mask_path = get_sidecar_mask_path(new_path)" in text
    assert "mask_success = cv2.imwrite(str(mask_path), crop_mask)" in text
    assert "preview_source = self._compose_cutout_preview_rgb(cropped, crop_mask) if self._background_cutout_enabled() else cropped" in text
    assert "preview_resized, _ = fit_image_to_box(preview_source, 180, 180)" in text
    assert "normalize_binary_mask(self._crop_mask, cropped.shape[:2])" in text
    assert "def _background_cutout_enabled(self) -> bool:" in text
    assert "cropped_bgra = cv2.cvtColor(cropped, cv2.COLOR_RGB2BGRA)" in text
    assert "cropped_bgra[:, :, 3] = crop_mask" in text
