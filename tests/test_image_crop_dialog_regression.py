from pathlib import Path

from src.ui.analyzer_view import sanitize_template_filename, unique_template_path
from src.ui.image_crop_utils import get_sidecar_mask_path


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


def test_image_crop_dialog_has_search_region_preset_picker():
    text = _text(ANALYZER_VIEW)
    start = text.index("class ImageCropDialog")
    end = text.index("class AltImageDialog", start)
    crop_class = text[start:end]

    assert "command=self._show_search_region_options" in crop_class
    assert "def _show_search_region_options(self):" in crop_class
    assert 'build_preset_row("a", "A영역", COLORS["accent_blue"])' in crop_class
    assert 'build_preset_row("b", "B영역", COLORS["accent_orange"])' in crop_class
    assert 'self._start_search_region_selection(preset_slot=None, source_label="자유영역")' in crop_class
    assert 'key = f"image_search_region_{slot}"' in crop_class
    assert "save_config()" in crop_class
    assert "def _apply_search_region_to_rule(self, region, source_label: str = \"검색범위\") -> bool:" in crop_class
    assert "self._rule.search_region = normalized" in crop_class


def test_image_crop_dialog_uses_sidecar_mask_pipeline():
    text = _text(ANALYZER_VIEW)

    assert "self._full_image_mask = load_sidecar_mask(self._image_path, (h, w))" in text
    assert "self._crop_mask = auto_extract_foreground_mask(cropped)" in text
    assert "self._crop_mask_needs_refresh = True" in text
    assert "def _ensure_current_crop_mask(self, *, refresh_view: bool = False):" in text
    assert "self._ensure_current_crop_mask()" in text
    assert "mask_path = get_sidecar_mask_path(new_path)" in text
    assert "mask_success = write_image_file(mask_path, crop_mask)" in text
    assert "preview_source = self._compose_cutout_preview_rgb(cropped, crop_mask) if self._background_cutout_enabled() else cropped" in text
    assert "preview_resized, _ = fit_image_to_box(preview_source, 180, 180)" in text
    assert "normalize_binary_mask(self._crop_mask, cropped.shape[:2])" in text
    assert "def _background_cutout_enabled(self) -> bool:" in text
    assert "self._crop_mask_needs_refresh = True" in text
    assert "checker = ((yy // tile + xx // tile) % 2)" in text
    assert "cropped_bgra = cv2.cvtColor(cropped, cv2.COLOR_RGB2BGRA)" in text
    assert "cropped_bgra[:, :, 3] = crop_mask" in text


def test_image_crop_dialog_supports_named_crop_save():
    text = _text(ANALYZER_VIEW)
    crop_method = text[
        text.index("def _save_crop(self):"):
        text.index("def _delete_image(self):", text.index("def _save_crop(self):"))
    ]

    assert "self._crop_filename_var = tk.StringVar" in text
    assert "self._crop_filename_entry = ctk.CTkEntry" in text
    assert 'self._crop_filename_entry.bind("<Return>", lambda _event: self._save_crop())' in text
    assert "placeholder_text=self._crop_filename_placeholder()" in text
    assert "custom_filename = sanitize_template_filename(self._crop_filename_var.get(), suffix)" in crop_method
    assert "new_path = unique_template_path(DATA_DIR / \"templates\", custom_filename)" in crop_method
    assert "success = write_image_file(new_path, cropped_bgra)" in crop_method
    assert "success = write_image_file(new_path, cropped_bgr)" in crop_method
    assert "mask_success = write_image_file(mask_path, crop_mask)" in crop_method


def test_image_crop_dialog_enables_filename_after_crop_and_resets_between_images():
    text = _text(ANALYZER_VIEW)
    selection_method = text[
        text.index("def _set_crop_selection(self, coords: tuple[int, int, int, int], *, refresh_mask: bool = True):"):
        text.index("def _ensure_current_crop_mask", text.index("def _set_crop_selection"))
    ]
    navigate_method = text[
        text.index("def _navigate_image(self, direction: int):"):
        text.index("def _update_nav_buttons", text.index("def _navigate_image"))
    ]
    change_method = text[
        text.index("def _change_image(self):"):
        text.index("def _invoke_image_callback", text.index("def _change_image"))
    ]

    assert "self._crop_filename_hint_label = None" in text
    assert 'state="normal" if self._crop_coords is not None else "disabled"' in text
    assert "def _crop_filename_placeholder(self) -> str:" in text
    assert "def _update_crop_filename_state(self):" in text
    assert "def _reset_crop_filename(self):" in text
    assert "self._update_crop_filename_state()" in selection_method
    assert 'state="normal" if ready else "disabled"' in text
    assert "self._crop_filename_var.set(\"\")" in navigate_method
    assert "self._update_crop_filename_state()" in navigate_method
    assert "self._reset_crop_filename()" in change_method


def test_sanitize_template_filename_preserves_korean_and_blocks_paths():
    assert sanitize_template_filename("환수의모험 확인버튼", ".png") == "환수의모험 확인버튼.png"
    assert sanitize_template_filename("../bad:name?.jpg", ".png") == "bad_name_.jpg"
    assert sanitize_template_filename("CON", ".png") is None
    assert sanitize_template_filename("   ", ".png") is None


def test_unique_template_path_does_not_overwrite_image_or_mask(tmp_path):
    first = tmp_path / "sample.png"
    first.write_bytes(b"image")
    candidate = unique_template_path(tmp_path, "sample.png")

    assert candidate.name == "sample_2.png"

    get_sidecar_mask_path(candidate).write_bytes(b"mask")
    next_candidate = unique_template_path(tmp_path, "sample.png")
    assert next_candidate.name == "sample_3.png"
