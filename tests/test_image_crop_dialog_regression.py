from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ANALYZER_VIEW = ROOT / "src" / "ui" / "analyzer_view.py"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def test_image_crop_dialog_has_freeform_mask_controls():
    text = _text(ANALYZER_VIEW)

    assert 'text="영역 선택"' in text
    assert 'text="마스크 지우기"' in text
    assert 'text="마스크 복원"' in text
    assert 'text="마스크 초기화"' in text
    assert 'text="자동 배경 제거"' in text
    assert "self._brush_slider = ctk.CTkSlider(" in text
    assert "self._set_edit_mode(\"select\")" in text


def test_image_crop_dialog_uses_sidecar_mask_pipeline():
    text = _text(ANALYZER_VIEW)

    assert "self._full_image_mask = load_sidecar_mask(self._image_path, (h, w))" in text
    assert "self._crop_mask = auto_extract_foreground_mask(self._original_image[y1:y2, x1:x2].copy())" in text
    assert "def _auto_extract_crop_mask(self):" in text
    assert "mask_path = get_sidecar_mask_path(new_path)" in text
    assert "mask_success = cv2.imwrite(str(mask_path), crop_mask)" in text
    assert "apply_mask_overlay_rgb(" in text
    assert "normalize_binary_mask(self._crop_mask, cropped.shape[:2])" in text
