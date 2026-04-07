from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
import re
import time
import unicodedata

import cv2
import numpy as np
import pytesseract

from .template_matcher import generate_text_mask
from ..utils.config import DATA_DIR


_TEXT_KEEP_RE = re.compile(r"[^0-9A-Z가-힣]+")
_HANGUL_RE = re.compile(r"[가-힣]")
_LOCAL_TESSDATA_DIR = DATA_DIR / "tessdata"
_OCR_TIMEOUT_S = 0.5
_OCR_FAST_TIMEOUT_S = 0.25
_HANGUL_BASE = 0xAC00
_HANGUL_LAST = 0xD7A3
_JAMO_L = [
    "ㄱ", "ㄲ", "ㄴ", "ㄷ", "ㄸ", "ㄹ", "ㅁ", "ㅂ", "ㅃ", "ㅅ",
    "ㅆ", "ㅇ", "ㅈ", "ㅉ", "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ",
]
_JAMO_V = [
    "ㅏ", "ㅐ", "ㅑ", "ㅒ", "ㅓ", "ㅔ", "ㅕ", "ㅖ", "ㅗ", "ㅘ",
    "ㅙ", "ㅚ", "ㅛ", "ㅜ", "ㅝ", "ㅞ", "ㅟ", "ㅠ", "ㅡ", "ㅢ", "ㅣ",
]
_JAMO_T = [
    "", "ㄱ", "ㄲ", "ㄳ", "ㄴ", "ㄵ", "ㄶ", "ㄷ", "ㄹ", "ㄺ",
    "ㄻ", "ㄼ", "ㄽ", "ㄾ", "ㄿ", "ㅀ", "ㅁ", "ㅂ", "ㅄ", "ㅅ",
    "ㅆ", "ㅇ", "ㅈ", "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ",
]


@dataclass(frozen=True)
class OCRTextMatch:
    found: bool = False
    text: str = ""
    normalized_text: str = ""
    confidence: float = 0.0
    score: float = 0.0
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0
    center_x: int = 0
    center_y: int = 0
    variant: str = ""


def normalize_ocr_text(text: str | None) -> str:
    _text = unicodedata.normalize("NFKC", str(text or "")).upper()
    _text = _TEXT_KEEP_RE.sub("", _text)
    return _text.strip()


def score_ocr_text_match(target_text: str | None, candidate_text: str | None) -> float:
    _target = normalize_ocr_text(target_text)
    _candidate = normalize_ocr_text(candidate_text)
    if not _target or not _candidate:
        return 0.0
    if _target == _candidate:
        return 1.0
    if _target in _candidate or _candidate in _target:
        _base = min(len(_target), len(_candidate)) / max(len(_target), len(_candidate))
        return max(0.90, min(0.99, _base))
    if len(_target) <= 2:
        _hangul_score = _score_short_hangul_match(_target, _candidate)
        if _hangul_score > 0.0:
            return _hangul_score
        return 0.0
    return float(SequenceMatcher(None, _target, _candidate).ratio())


def _is_hangul_syllable(ch: str) -> bool:
    if not ch:
        return False
    _code = ord(ch)
    return _HANGUL_BASE <= _code <= _HANGUL_LAST


def _decompose_hangul_text(text: str) -> str:
    _parts: list[str] = []
    for _ch in str(text or ""):
        if not _is_hangul_syllable(_ch):
            _parts.append(_ch)
            continue
        _code = ord(_ch) - _HANGUL_BASE
        _l = _code // 588
        _v = (_code % 588) // 28
        _t = _code % 28
        _parts.append(_JAMO_L[_l])
        _parts.append(_JAMO_V[_v])
        if _t:
            _parts.append(_JAMO_T[_t])
    return "".join(_parts)


def _score_short_hangul_match(target_text: str, candidate_text: str) -> float:
    if len(target_text) > 2 or len(candidate_text) > 2:
        return 0.0
    if not target_text or not candidate_text:
        return 0.0
    if not all(_is_hangul_syllable(ch) for ch in target_text):
        return 0.0
    if not all(_is_hangul_syllable(ch) for ch in candidate_text):
        return 0.0
    _same_chars = sum(1 for _a, _b in zip(target_text, candidate_text) if _a == _b)
    _char_ratio = _same_chars / max(len(target_text), len(candidate_text))
    _jamo_target = _decompose_hangul_text(target_text)
    _jamo_candidate = _decompose_hangul_text(candidate_text)
    _jamo_ratio = float(SequenceMatcher(None, _jamo_target, _jamo_candidate).ratio())
    _score = (_char_ratio * 0.45) + (_jamo_ratio * 0.55)
    if _same_chars >= 1 and _jamo_ratio >= 0.6:
        return max(0.72, min(0.89, _score))
    if _jamo_ratio >= 0.8:
        return max(0.70, min(0.86, _score))
    return 0.0


def _resize_for_ocr(image: np.ndarray, *, scale: float) -> np.ndarray:
    _h, _w = image.shape[:2]
    _new_w = max(1, int(round(_w * scale)))
    _new_h = max(1, int(round(_h * scale)))
    return cv2.resize(image, (_new_w, _new_h), interpolation=cv2.INTER_CUBIC)


def _screen_ocr_scale(image_shape: tuple[int, ...]) -> float:
    _h, _w = image_shape[:2]
    _max_dim = max(int(_h), int(_w))
    if _max_dim <= 220:
        return 4.0
    if _max_dim <= 480:
        return 2.5
    if _max_dim <= 960:
        return 1.5
    return 1.0


def _prepare_ocr_variants(image_bgr: np.ndarray, *, is_template: bool) -> list[tuple[str, np.ndarray, float]]:
    if image_bgr is None or image_bgr.size == 0:
        return []
    _gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY) if len(image_bgr.shape) == 3 else image_bgr.copy()
    _scale = 8.0 if is_template else _screen_ocr_scale(image_bgr.shape)
    _base = _resize_for_ocr(_gray, scale=_scale)
    _variants: list[tuple[str, np.ndarray, float]] = [("gray", _base, _scale)]

    _eq = cv2.equalizeHist(_base)
    _variants.append(("equalized", _eq, _scale))

    _blur = cv2.GaussianBlur(_eq, (3, 3), 0)
    _, _otsu = cv2.threshold(_blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    _variants.append(("otsu", _otsu, _scale))
    _variants.append(("otsu_inv", cv2.bitwise_not(_otsu), _scale))

    try:
        _mask_src = generate_text_mask(image_bgr)
        _mask = _resize_for_ocr(_mask_src, scale=_scale)
        _variants.append(("mask", _mask, _scale))
        _variants.append(("mask_inv", cv2.bitwise_not(_mask), _scale))
    except Exception:
        pass

    if not is_template:
        _keep = {"gray", "equalized", "mask", "mask_inv", "otsu", "otsu_inv"}
        _variants = [(_name, _img, _sc) for _name, _img, _sc in _variants if _name in _keep]

    return _variants


def _prepare_fast_screen_ocr_variants(image_bgr: np.ndarray) -> list[tuple[str, np.ndarray, float]]:
    if image_bgr is None or image_bgr.size == 0:
        return []
    _gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY) if len(image_bgr.shape) == 3 else image_bgr.copy()
    _scale = _screen_ocr_scale(image_bgr.shape)
    _base = _resize_for_ocr(_gray, scale=_scale)
    _variants: list[tuple[str, np.ndarray, float]] = [("gray", _base, _scale)]
    try:
        _mask_src = generate_text_mask(image_bgr)
        _mask = _resize_for_ocr(_mask_src, scale=_scale)
        _variants.append(("mask", _mask, _scale))
    except Exception:
        pass
    return _variants


def _crop_mask_to_content(mask: np.ndarray):
    if mask is None or mask.size == 0:
        return None
    _ys, _xs = np.where(mask > 0)
    if len(_xs) == 0:
        return None
    _x0 = int(_xs.min())
    _y0 = int(_ys.min())
    _x1 = int(_xs.max()) + 1
    _y1 = int(_ys.max()) + 1
    return mask[_y0:_y1, _x0:_x1].copy(), (_x0, _y0, _x1, _y1)


def _find_projection_split(mask: np.ndarray, left: int, right: int, *, min_width: int = 2) -> int | None:
    _width = int(right - left)
    if _width < (min_width * 2):
        return None
    _cols = (mask > 0).sum(axis=0).astype(np.int32)
    _center = (left + right) / 2.0

    _zero_runs: list[tuple[int, int]] = []
    _idx = int(left + min_width)
    _right_limit = int(right - min_width)
    while _idx < _right_limit:
        if _cols[_idx] != 0:
            _idx += 1
            continue
        _run_start = _idx
        while _idx < _right_limit and _cols[_idx] == 0:
            _idx += 1
        _run_end = _idx
        _zero_runs.append((_run_start, _run_end))
    if _zero_runs:
        _best_run = min(
            _zero_runs,
            key=lambda item: (
                -int(item[1] - item[0]),
                abs((((item[0] + item[1]) / 2.0) - _center)),
            ),
        )
        _split = int((_best_run[0] + _best_run[1]) // 2)
        if (left + min_width) <= _split <= (right - min_width):
            return _split

    _best_split = None
    _best_score = None
    for _split in range(int(left + min_width), int(right - min_width + 1)):
        _local = int(_cols[max(left, _split - 1):min(right, _split + 2)].sum())
        _balance = abs((_split - _center) / max(1.0, _width))
        _score = (_local, _balance)
        if _best_score is None or _score < _best_score:
            _best_score = _score
            _best_split = _split
    return _best_split


def _split_mask_into_glyphs(mask: np.ndarray, expected_count: int):
    if mask is None or mask.size == 0 or expected_count <= 0:
        return []
    _cropped = _crop_mask_to_content(mask)
    if _cropped is None:
        return []
    _mask_crop, (_word_x0, _word_y0, _word_x1, _word_y1) = _cropped
    if expected_count == 1:
        return [(_mask_crop, (_word_x0, _word_y0, _word_x1, _word_y1))]

    _ranges: list[tuple[int, int]] = [(0, int(_mask_crop.shape[1]))]
    while len(_ranges) < int(expected_count):
        _best_idx = None
        _best_split = None
        _best_width = -1
        for _idx, (_left, _right) in enumerate(_ranges):
            _split = _find_projection_split(_mask_crop, _left, _right, min_width=2)
            if _split is None:
                continue
            _width = int(_right - _left)
            if _width > _best_width:
                _best_idx = _idx
                _best_split = _split
                _best_width = _width
        if _best_idx is None or _best_split is None:
            break
        _left, _right = _ranges.pop(_best_idx)
        _ranges.insert(_best_idx, (_left, int(_best_split)))
        _ranges.insert(_best_idx + 1, (int(_best_split), _right))

    if len(_ranges) != int(expected_count):
        return []

    _glyphs: list[tuple[np.ndarray, tuple[int, int, int, int]]] = []
    for _left, _right in _ranges:
        _segment = _mask_crop[:, _left:_right]
        _segment_crop = _crop_mask_to_content(_segment)
        if _segment_crop is None:
            return []
        _glyph_mask, (_gx0, _gy0, _gx1, _gy1) = _segment_crop
        _glyphs.append(
            (
                _glyph_mask,
                (
                    int(_word_x0 + _left + _gx0),
                    int(_word_y0 + _gy0),
                    int(_word_x0 + _left + _gx1),
                    int(_word_y0 + _gy1),
                ),
            )
        )
    return _glyphs


def _extract_component_glyphs(mask: np.ndarray):
    if mask is None or mask.size == 0:
        return []
    try:
        _num, _labels, _stats, _ = cv2.connectedComponentsWithStats((mask > 0).astype(np.uint8), 8)
    except Exception:
        return []
    _glyphs: list[tuple[np.ndarray, tuple[int, int, int, int]]] = []
    for _idx in range(1, int(_num)):
        _x, _y, _w, _h, _area = [int(v) for v in _stats[_idx]]
        if _area < 4 or _w < 2 or _h < 4:
            continue
        _crop = mask[_y:_y + _h, _x:_x + _w]
        _glyphs.append((_crop.copy(), (int(_x), int(_y), int(_x + _w), int(_y + _h))))
    _glyphs.sort(key=lambda item: (int(item[1][0]), int(item[1][1])))
    return _glyphs


def _score_binary_mask_similarity(template_mask: np.ndarray, candidate_mask: np.ndarray) -> float:
    if template_mask is None or candidate_mask is None or template_mask.size == 0 or candidate_mask.size == 0:
        return 0.0
    _th, _tw = template_mask.shape[:2]
    if _th <= 0 or _tw <= 0:
        return 0.0
    _resized = cv2.resize(candidate_mask, (_tw, _th), interpolation=cv2.INTER_NEAREST)
    _tmpl_bin = (template_mask > 0).astype(np.uint8)
    _cand_bin = (_resized > 0).astype(np.uint8)
    _intersection = float(np.logical_and(_tmpl_bin > 0, _cand_bin > 0).sum())
    _union = float(np.logical_or(_tmpl_bin > 0, _cand_bin > 0).sum())
    _iou = (_intersection / _union) if _union > 0 else 0.0
    _col_t = _tmpl_bin.sum(axis=0).astype(np.float32)
    _col_c = _cand_bin.sum(axis=0).astype(np.float32)
    _row_t = _tmpl_bin.sum(axis=1).astype(np.float32)
    _row_c = _cand_bin.sum(axis=1).astype(np.float32)
    if _col_t.max() > 0:
        _col_t /= _col_t.max()
    if _col_c.max() > 0:
        _col_c /= _col_c.max()
    if _row_t.max() > 0:
        _row_t /= _row_t.max()
    if _row_c.max() > 0:
        _row_c /= _row_c.max()
    _col_score = 1.0 - float(np.mean(np.abs(_col_t - _col_c)))
    _row_score = 1.0 - float(np.mean(np.abs(_row_t - _row_c)))
    _fill_t = float(_tmpl_bin.mean())
    _fill_c = float(_cand_bin.mean())
    _fill_score = 1.0 - min(1.0, abs(_fill_t - _fill_c) * 2.0)
    return max(0.0, min(1.0, (_iou * 0.55) + (_col_score * 0.20) + (_row_score * 0.20) + (_fill_score * 0.05)))


def _match_template_glyph_sequence(
    image_bgr: np.ndarray,
    template_bgr: np.ndarray,
    *,
    target_text: str,
    fast_mode: bool = False,
    max_time_s: float | None = None,
) -> OCRTextMatch:
    if image_bgr is None or image_bgr.size == 0 or template_bgr is None or template_bgr.size == 0:
        return OCRTextMatch(found=False)
    _target = normalize_ocr_text(target_text)
    _expected_count = max(1, len(_target))
    if _expected_count > 4:
        return OCRTextMatch(found=False)
    try:
        _template_mask = generate_text_mask(template_bgr)
    except Exception:
        return OCRTextMatch(found=False)
    _template_word = _crop_mask_to_content(_template_mask)
    if _template_word is None:
        return OCRTextMatch(found=False)
    _template_word_mask, _template_word_box = _template_word
    _template_glyphs = _split_mask_into_glyphs(_template_word_mask, _expected_count)
    if len(_template_glyphs) != _expected_count:
        return OCRTextMatch(found=False)
    _template_word_h, _template_word_w = _template_word_mask.shape[:2]
    _template_fill = float((_template_word_mask > 0).mean())
    _template_glyph_widths = [max(1, int(_box[2] - _box[0])) for _, _box in _template_glyphs]
    _template_glyph_heights = [max(1, int(_box[3] - _box[1])) for _, _box in _template_glyphs]
    _template_gap_ratios: list[float] = []
    for _idx in range(len(_template_glyphs) - 1):
        _left_box = _template_glyphs[_idx][1]
        _right_box = _template_glyphs[_idx + 1][1]
        _gap = max(0, int(_right_box[0]) - int(_left_box[2]))
        _template_gap_ratios.append(float(_gap) / float(max(1, _template_word_w)))

    def _relative_score(_expected: int, _actual: int, *, _scale: float = 1.0) -> float:
        return 1.0 - min(
            1.0,
            (abs(float(_actual) - float(_expected)) / float(max(1, int(_expected)))) * float(_scale),
        )

    _best = OCRTextMatch(found=False)
    _deadline = (time.perf_counter() + float(max_time_s)) if max_time_s and float(max_time_s) > 0.0 else None
    _regions: list[tuple[np.ndarray, int, int]] = []
    _seen: set[tuple[int, int, int]] = set()

    def _append_region(_crop, _ox, _oy):
        _shape = getattr(_crop, "shape", (0, 0))
        _key = (int(_ox), int(_oy), int(_shape[0]) * 10000 + int(_shape[1]))
        if _key in _seen:
            return
        _seen.add(_key)
        _regions.append((_crop, _ox, _oy))

    for _crop, _ox, _oy in _iter_colored_text_candidate_regions(image_bgr, template_bgr) or []:
        _append_region(_crop, _ox, _oy)
    for _crop, _ox, _oy in _iter_text_candidate_regions(image_bgr) or []:
        _append_region(_crop, _ox, _oy)
    if not _regions:
        for _crop, _ox, _oy in _iter_screen_ocr_regions(image_bgr):
            _append_region(_crop, _ox, _oy)

    _max_regions = 8 if fast_mode else 12
    _threshold = 0.68 if fast_mode else 0.74
    _min_glyph_floor = 0.58 if fast_mode else 0.64
    _box_floor = 0.62 if fast_mode else 0.68

    for _crop, _ox, _oy in _regions[:_max_regions]:
        if _deadline is not None and time.perf_counter() >= _deadline:
            return _best
        try:
            _crop_mask = generate_text_mask(_crop)
        except Exception:
            continue
        _candidate_word = _crop_mask_to_content(_crop_mask)
        if _candidate_word is None:
            continue
        _candidate_word_mask, (_cx0, _cy0, _cx1, _cy1) = _candidate_word
        _candidate_glyphs = _split_mask_into_glyphs(_candidate_word_mask, _expected_count)
        if len(_candidate_glyphs) != _expected_count:
            continue
        _glyph_scores: list[float] = []
        for (_tmpl_mask, _), (_cand_mask, _) in zip(_template_glyphs, _candidate_glyphs):
            _glyph_scores.append(_score_binary_mask_similarity(_tmpl_mask, _cand_mask))
        if not _glyph_scores:
            continue
        _min_glyph = min(_glyph_scores)
        if _min_glyph < _min_glyph_floor:
            continue
        _mean_glyph = float(sum(_glyph_scores) / len(_glyph_scores))
        _cw = max(1, int(_candidate_word_mask.shape[1]))
        _ch = max(1, int(_candidate_word_mask.shape[0]))
        _word_width_score = 1.0 - min(1.0, abs(_cw - _template_word_w) / float(max(1, _template_word_w)))
        _word_height_score = 1.0 - min(1.0, abs(_ch - _template_word_h) / float(max(1, _template_word_h)))
        _candidate_fill = float((_candidate_word_mask > 0).mean())
        _fill_score = 1.0 - min(1.0, abs(_candidate_fill - _template_fill) * 2.0)
        _candidate_glyph_widths = [max(1, int(_box[2] - _box[0])) for _, _box in _candidate_glyphs]
        _candidate_glyph_heights = [max(1, int(_box[3] - _box[1])) for _, _box in _candidate_glyphs]
        _glyph_box_scores: list[float] = []
        for _idx, (_tmpl_w, _tmpl_h, _cand_w, _cand_h) in enumerate(zip(_template_glyph_widths, _template_glyph_heights, _candidate_glyph_widths, _candidate_glyph_heights)):
            _w_score = _relative_score(_tmpl_w, _cand_w, _scale=1.0)
            _h_score = _relative_score(_tmpl_h, _cand_h, _scale=1.25)
            _glyph_box_scores.append((_w_score * 0.40) + (_h_score * 0.60))
        _gap_scores: list[float] = []
        for _idx in range(len(_candidate_glyphs) - 1):
            _left_box = _candidate_glyphs[_idx][1]
            _right_box = _candidate_glyphs[_idx + 1][1]
            _gap = max(0, int(_right_box[0]) - int(_left_box[2]))
            _gap_ratio = float(_gap) / float(max(1, _cw))
            _tmpl_gap_ratio = float(_template_gap_ratios[_idx]) if _idx < len(_template_gap_ratios) else 0.0
            _gap_scores.append(1.0 - min(1.0, abs(_gap_ratio - _tmpl_gap_ratio) * 5.0))
        _box_parts = list(_glyph_box_scores)
        _box_parts.extend(_gap_scores)
        _box_parts.append(_fill_score)
        _box_score = float(sum(_box_parts) / len(_box_parts)) if _box_parts else 0.0
        if _box_score < _box_floor:
            continue
        _score = (
            (_mean_glyph * 0.50)
            + (_min_glyph * 0.25)
            + (_box_score * 0.15)
            + (_word_width_score * 0.05)
            + (_word_height_score * 0.05)
        )
        _abs_x = int(_ox + _cx0)
        _abs_y = int(_oy + _cy0)
        _cand = OCRTextMatch(
            found=_score >= _threshold,
            text=_target,
            normalized_text=_target,
            confidence=float(_score),
            score=float(_score),
            x=_abs_x,
            y=_abs_y,
            width=int(_cx1 - _cx0),
            height=int(_cy1 - _cy0),
            center_x=int(_abs_x + ((_cx1 - _cx0) // 2)),
            center_y=int(_abs_y + ((_cy1 - _cy0) // 2)),
            variant="glyph_sequence",
        )
        if (not _best.found) or (_cand.score > _best.score):
            _best = _cand
        if _cand.found:
            return _cand
    return _best


def _match_template_component_sequence(
    image_bgr: np.ndarray,
    template_bgr: np.ndarray,
    *,
    target_text: str,
    fast_mode: bool = False,
    max_time_s: float | None = None,
) -> OCRTextMatch:
    if image_bgr is None or image_bgr.size == 0 or template_bgr is None or template_bgr.size == 0:
        return OCRTextMatch(found=False)
    _target = normalize_ocr_text(target_text)
    _expected_count = max(1, len(_target))
    if _expected_count <= 1 or _expected_count > 4:
        return OCRTextMatch(found=False)
    try:
        _template_mask = generate_text_mask(template_bgr)
    except Exception:
        return OCRTextMatch(found=False)
    _template_word = _crop_mask_to_content(_template_mask)
    if _template_word is None:
        return OCRTextMatch(found=False)
    _template_word_mask, _ = _template_word
    _template_components = _extract_component_glyphs(_template_word_mask)
    if len(_template_components) != _expected_count:
        _template_components = _split_mask_into_glyphs(_template_word_mask, _expected_count)
    if len(_template_components) != _expected_count:
        return OCRTextMatch(found=False)

    _template_word_h, _template_word_w = _template_word_mask.shape[:2]
    _template_fill = float((_template_word_mask > 0).mean())
    _template_gap_ratios: list[float] = []
    for _idx in range(len(_template_components) - 1):
        _left_box = _template_components[_idx][1]
        _right_box = _template_components[_idx + 1][1]
        _gap = max(0, int(_right_box[0]) - int(_left_box[2]))
        _template_gap_ratios.append(float(_gap) / float(max(1, _template_word_w)))

    def _relative_score(_expected: int, _actual: int, *, _scale: float = 1.0) -> float:
        return 1.0 - min(
            1.0,
            (abs(float(_actual) - float(_expected)) / float(max(1, int(_expected)))) * float(_scale),
        )

    _deadline = (time.perf_counter() + float(max_time_s)) if max_time_s and float(max_time_s) > 0.0 else None
    _best = OCRTextMatch(found=False)
    _regions: list[tuple[np.ndarray, int, int]] = []
    _seen: set[tuple[int, int, int]] = set()

    def _append_region(_crop, _ox, _oy):
        _shape = getattr(_crop, "shape", (0, 0))
        _key = (int(_ox), int(_oy), int(_shape[0]) * 10000 + int(_shape[1]))
        if _key in _seen:
            return
        _seen.add(_key)
        _regions.append((_crop, _ox, _oy))

    for _crop, _ox, _oy in _iter_colored_text_candidate_regions(image_bgr, template_bgr) or []:
        _append_region(_crop, _ox, _oy)
    for _crop, _ox, _oy in _iter_text_candidate_regions(image_bgr) or []:
        _append_region(_crop, _ox, _oy)
    if not _regions:
        for _crop, _ox, _oy in _iter_screen_ocr_regions(image_bgr):
            _append_region(_crop, _ox, _oy)

    _max_regions = 8 if fast_mode else 12
    _threshold = 0.76 if fast_mode else 0.82
    _min_glyph_floor = 0.72 if fast_mode else 0.78
    _box_floor = 0.70 if fast_mode else 0.76

    for _crop, _ox, _oy in _regions[:_max_regions]:
        if _deadline is not None and time.perf_counter() >= _deadline:
            return _best
        try:
            _crop_mask = generate_text_mask(_crop)
        except Exception:
            continue
        _crop_word = _crop_mask_to_content(_crop_mask)
        if _crop_word is None:
            continue
        _crop_word_mask, _crop_word_box = _crop_word
        _components = _extract_component_glyphs(_crop_word_mask)
        if len(_components) < _expected_count:
            continue
        for _start in range(0, len(_components) - _expected_count + 1):
            if _deadline is not None and time.perf_counter() >= _deadline:
                return _best
            _window = _components[_start:_start + _expected_count]
            _glyph_scores: list[float] = []
            _width_scores: list[float] = []
            _height_scores: list[float] = []
            for (_tmpl_mask, _tmpl_box), (_cand_mask, _cand_box) in zip(_template_components, _window):
                _glyph_score = _score_binary_mask_similarity(_tmpl_mask, _cand_mask)
                _glyph_scores.append(_glyph_score)
                _tmpl_w = max(1, int(_tmpl_box[2] - _tmpl_box[0]))
                _tmpl_h = max(1, int(_tmpl_box[3] - _tmpl_box[1]))
                _cand_w = max(1, int(_cand_box[2] - _cand_box[0]))
                _cand_h = max(1, int(_cand_box[3] - _cand_box[1]))
                _width_scores.append(_relative_score(_tmpl_w, _cand_w, _scale=1.0))
                _height_scores.append(_relative_score(_tmpl_h, _cand_h, _scale=1.25))
            _min_glyph = min(_glyph_scores)
            if _min_glyph < _min_glyph_floor:
                continue
            _mean_glyph = float(sum(_glyph_scores) / len(_glyph_scores))
            _win_x0 = min(int(_box[0]) for _, _box in _window)
            _win_y0 = min(int(_box[1]) for _, _box in _window)
            _win_x1 = max(int(_box[2]) for _, _box in _window)
            _win_y1 = max(int(_box[3]) for _, _box in _window)
            _window_mask = _crop_word_mask[_win_y0:_win_y1, _win_x0:_win_x1]
            _cw = max(1, int(_win_x1 - _win_x0))
            _ch = max(1, int(_win_y1 - _win_y0))
            _word_width_score = _relative_score(_template_word_w, _cw, _scale=1.0)
            _word_height_score = _relative_score(_template_word_h, _ch, _scale=1.0)
            _window_fill = float((_window_mask > 0).mean()) if _window_mask.size else 0.0
            _fill_score = 1.0 - min(1.0, abs(_window_fill - _template_fill) * 2.0)
            _gap_scores: list[float] = []
            for _idx in range(len(_window) - 1):
                _left_box = _window[_idx][1]
                _right_box = _window[_idx + 1][1]
                _gap = max(0, int(_right_box[0]) - int(_left_box[2]))
                _gap_ratio = float(_gap) / float(max(1, _cw))
                _tmpl_gap_ratio = float(_template_gap_ratios[_idx]) if _idx < len(_template_gap_ratios) else 0.0
                _gap_scores.append(1.0 - min(1.0, abs(_gap_ratio - _tmpl_gap_ratio) * 6.0))
            _box_parts = list(_width_scores)
            _box_parts.extend(_height_scores)
            _box_parts.extend(_gap_scores)
            _box_parts.append(_fill_score)
            _box_score = float(sum(_box_parts) / len(_box_parts)) if _box_parts else 0.0
            if _box_score < _box_floor:
                continue
            _score = (
                (_mean_glyph * 0.55)
                + (_min_glyph * 0.25)
                + (_box_score * 0.15)
                + (_word_width_score * 0.03)
                + (_word_height_score * 0.02)
            )
            _abs_x = int(_ox + _crop_word_box[0] + _win_x0)
            _abs_y = int(_oy + _crop_word_box[1] + _win_y0)
            _cand = OCRTextMatch(
                found=_score >= _threshold,
                text=_target,
                normalized_text=_target,
                confidence=float(_score),
                score=float(_score),
                x=_abs_x,
                y=_abs_y,
                width=int(_cw),
                height=int(_ch),
                center_x=int(_abs_x + (_cw // 2)),
                center_y=int(_abs_y + (_ch // 2)),
                variant="glyph_components",
            )
            if (not _best.found) or (_cand.score > _best.score):
                _best = _cand
            if _cand.found:
                return _cand
    return _best


def _match_template_word_mask_regions(
    image_bgr: np.ndarray,
    template_bgr: np.ndarray,
    *,
    fast_mode: bool = False,
    max_time_s: float | None = None,
) -> OCRTextMatch:
    if image_bgr is None or image_bgr.size == 0 or template_bgr is None or template_bgr.size == 0:
        return OCRTextMatch(found=False)
    try:
        _template_mask = generate_text_mask(template_bgr)
    except Exception:
        return OCRTextMatch(found=False)
    _template_word = _crop_mask_to_content(_template_mask)
    if _template_word is None:
        return OCRTextMatch(found=False)
    _template_word_mask, _ = _template_word
    _deadline = (time.perf_counter() + float(max_time_s)) if max_time_s and float(max_time_s) > 0.0 else None
    _best = OCRTextMatch(found=False)
    _regions: list[tuple[np.ndarray, int, int]] = []
    _seen: set[tuple[int, int, int]] = set()

    def _append_region(_crop, _ox, _oy):
        _shape = getattr(_crop, "shape", (0, 0))
        _key = (int(_ox), int(_oy), int(_shape[0]) * 10000 + int(_shape[1]))
        if _key in _seen:
            return
        _seen.add(_key)
        _regions.append((_crop, _ox, _oy))

    for _crop, _ox, _oy in _iter_colored_text_candidate_regions(image_bgr, template_bgr) or []:
        _append_region(_crop, _ox, _oy)
    for _crop, _ox, _oy in _iter_text_candidate_regions(image_bgr) or []:
        _append_region(_crop, _ox, _oy)
    if not _regions:
        for _crop, _ox, _oy in _iter_screen_ocr_regions(image_bgr):
            _append_region(_crop, _ox, _oy)

    _max_regions = 8 if fast_mode else 12
    _threshold = 0.78 if fast_mode else 0.84

    for _crop, _ox, _oy in _regions[:_max_regions]:
        if _deadline is not None and time.perf_counter() >= _deadline:
            return _best
        try:
            _crop_mask = generate_text_mask(_crop)
        except Exception:
            continue
        _candidate_word = _crop_mask_to_content(_crop_mask)
        if _candidate_word is None:
            continue
        _candidate_word_mask, (_cx0, _cy0, _cx1, _cy1) = _candidate_word
        _score = _score_binary_mask_similarity(_template_word_mask, _candidate_word_mask)
        _abs_x = int(_ox + _cx0)
        _abs_y = int(_oy + _cy0)
        _cand = OCRTextMatch(
            found=_score >= _threshold,
            confidence=float(_score),
            score=float(_score),
            x=_abs_x,
            y=_abs_y,
            width=int(_cx1 - _cx0),
            height=int(_cy1 - _cy0),
            center_x=int(_abs_x + ((_cx1 - _cx0) // 2)),
            center_y=int(_abs_y + ((_cy1 - _cy0) // 2)),
            variant="word_mask_regions",
        )
        if (not _best.found) or (_cand.score > _best.score):
            _best = _cand
        if _cand.found:
            return _cand
    return _best


def _match_template_text_mask(
    image_bgr: np.ndarray,
    template_bgr: np.ndarray,
    *,
    fast_mode: bool = False,
    max_time_s: float | None = None,
) -> OCRTextMatch:
    if image_bgr is None or image_bgr.size == 0 or template_bgr is None or template_bgr.size == 0:
        return OCRTextMatch(found=False)
    _ih, _iw = image_bgr.shape[:2]
    _th, _tw = template_bgr.shape[:2]
    if _tw > _iw or _th > _ih:
        return OCRTextMatch(found=False)
    try:
        _tmpl_gray = cv2.cvtColor(template_bgr, cv2.COLOR_BGR2GRAY) if len(template_bgr.shape) == 3 else template_bgr.copy()
        _tmpl_mask = generate_text_mask(template_bgr)
    except Exception:
        return OCRTextMatch(found=False)
    if _tmpl_mask is None or int(np.count_nonzero(_tmpl_mask)) < 6:
        return OCRTextMatch(found=False)

    _best = OCRTextMatch(found=False)
    _deadline = (time.perf_counter() + float(max_time_s)) if max_time_s and float(max_time_s) > 0.0 else None
    _regions: list[tuple[np.ndarray, int, int]] = []
    _seen: set[tuple[int, int, int]] = set()

    def _append_region(_crop, _ox, _oy):
        _key = (int(_ox), int(_oy), int(getattr(_crop, "shape", [0, 0])[0]) * 10000 + int(getattr(_crop, "shape", [0, 0])[1]))
        if _key in _seen:
            return
        _seen.add(_key)
        _regions.append((_crop, _ox, _oy))

    for _crop, _ox, _oy in _iter_colored_text_candidate_regions(image_bgr, template_bgr) or []:
        _append_region(_crop, _ox, _oy)
    for _crop, _ox, _oy in _iter_text_candidate_regions(image_bgr) or []:
        _append_region(_crop, _ox, _oy)
    if not _regions:
        for _crop, _ox, _oy in _iter_screen_ocr_regions(image_bgr):
            _append_region(_crop, _ox, _oy)

    _max_regions = 6 if fast_mode else 10
    _threshold = 0.68 if fast_mode else 0.74

    for _crop, _ox, _oy in _regions[:_max_regions]:
        if _deadline is not None and time.perf_counter() >= _deadline:
            return _best
        _ch, _cw = _crop.shape[:2]
        if _cw < _tw or _ch < _th:
            continue
        try:
            _crop_gray = cv2.cvtColor(_crop, cv2.COLOR_BGR2GRAY) if len(_crop.shape) == 3 else _crop.copy()
            _result = cv2.matchTemplate(_crop_gray, _tmpl_gray, cv2.TM_SQDIFF_NORMED, mask=_tmpl_mask)
            _min_val, _, _min_loc, _ = cv2.minMaxLoc(_result)
            _confidence = float(1.0 - _min_val)
        except Exception:
            continue
        _cand = OCRTextMatch(
            found=_confidence >= _threshold,
            confidence=_confidence,
            score=_confidence,
            x=int(_ox + _min_loc[0]),
            y=int(_oy + _min_loc[1]),
            width=int(_tw),
            height=int(_th),
            center_x=int(_ox + _min_loc[0] + (_tw // 2)),
            center_y=int(_oy + _min_loc[1] + (_th // 2)),
            variant="text_mask",
        )
        if (not _best.found) or (_cand.score > _best.score):
            _best = _cand
        if _cand.found:
            return _cand
    return _best


def _extract_template_text_hsv(template_bgr: np.ndarray) -> tuple[int, int, int] | None:
    try:
        _mask = generate_text_mask(template_bgr)
    except Exception:
        _mask = None
    _img = template_bgr if len(template_bgr.shape) == 3 else cv2.cvtColor(template_bgr, cv2.COLOR_GRAY2BGR)
    _hsv = cv2.cvtColor(_img, cv2.COLOR_BGR2HSV)
    if _mask is not None and np.count_nonzero(_mask) >= 4:
        _pixels = _hsv[_mask > 0]
    else:
        _pixels = _hsv.reshape(-1, 3)
        _pixels = _pixels[(_pixels[:, 1] >= 60) & (_pixels[:, 2] >= 60)]
    if _pixels is None or len(_pixels) == 0:
        return None
    _med = np.median(_pixels, axis=0)
    return int(_med[0]), int(_med[1]), int(_med[2])


def _iter_colored_text_candidate_regions(image_bgr: np.ndarray, template_bgr: np.ndarray):
    _hsv_ref = _extract_template_text_hsv(template_bgr)
    if _hsv_ref is None:
        return
    _hue, _sat, _val = _hsv_ref
    _img = image_bgr if len(image_bgr.shape) == 3 else cv2.cvtColor(image_bgr, cv2.COLOR_GRAY2BGR)
    _hsv = cv2.cvtColor(_img, cv2.COLOR_BGR2HSV)
    _h = _hsv[:, :, 0].astype(np.int16)
    _s = _hsv[:, :, 1].astype(np.int16)
    _v = _hsv[:, :, 2].astype(np.int16)
    _diff = np.abs(_h - int(_hue))
    _diff = np.minimum(_diff, 180 - _diff)
    _mask = (
        (_diff <= 16)
        & (_s >= max(45, int(_sat) - 70))
        & (_v >= max(45, int(_val) - 90))
    ).astype(np.uint8) * 255
    if int(np.count_nonzero(_mask)) < 10:
        return
    _mask = cv2.dilate(_mask, np.ones((3, 3), np.uint8), iterations=1)
    _num, _labels, _stats, _centroids = cv2.connectedComponentsWithStats(_mask, 8)
    _ih, _iw = image_bgr.shape[:2]
    _th, _tw = template_bgr.shape[:2]
    _template_area = max(1, int(_tw) * int(_th))
    _template_ratio = float(_tw) / float(max(1, _th))
    _regions: list[tuple[float, int, int, int, int, int]] = []
    for _idx in range(1, int(_num)):
        _x, _y, _rw, _rh, _area = [int(v) for v in _stats[_idx]]
        if _area < 8:
            continue
        if _rw < 6 or _rh < 6:
            continue
        _pad = 12
        _x1 = max(0, _x - _pad)
        _y1 = max(0, _y - _pad)
        _x2 = min(_iw, _x + _rw + _pad)
        _y2 = min(_ih, _y + _rh + _pad)
        _crop_w = max(1, _x2 - _x1)
        _crop_h = max(1, _y2 - _y1)
        _area_delta = abs((_crop_w * _crop_h) - _template_area) / float(_template_area)
        _ratio_delta = abs((float(_crop_w) / float(_crop_h)) - _template_ratio)
        _rank = (_area_delta * 0.7) + (_ratio_delta * 0.3)
        _regions.append((_rank, _x1, _y1, _x2, _y2, _area))
    _regions.sort(key=lambda item: (item[0], -item[5]))
    for _rank, _x1, _y1, _x2, _y2, _area in _regions[:8]:
        yield image_bgr[_y1:_y2, _x1:_x2], _x1, _y1


def _read_ocr_line(image_variant: np.ndarray, *, lang: str, psm: int) -> dict:
    _config = _build_tesseract_config(lang=lang, psm=psm)
    try:
        _raw_text = pytesseract.image_to_string(
            image_variant, lang=lang, config=_config, timeout=_OCR_TIMEOUT_S
        )
        _boxes_text = pytesseract.image_to_boxes(
            image_variant, lang=lang, config=_config, timeout=_OCR_TIMEOUT_S
        )
    except RuntimeError:
        return {
            "text": "",
            "normalized_text": "",
            "x": 0,
            "y": 0,
            "width": 0,
            "height": 0,
        }
    _norm_text = normalize_ocr_text(_raw_text)
    _h = int(image_variant.shape[0])
    _coords: list[tuple[int, int, int, int]] = []
    for _line in str(_boxes_text or "").splitlines():
        _parts = _line.split()
        if len(_parts) < 5:
            continue
        try:
            _x1 = int(_parts[1])
            _y1 = int(_parts[2])
            _x2 = int(_parts[3])
            _y2 = int(_parts[4])
        except Exception:
            continue
        _coords.append((_x1, _h - _y2, _x2, _h - _y1))
    if _coords:
        _left = min(c[0] for c in _coords)
        _top = min(c[1] for c in _coords)
        _right = max(c[2] for c in _coords)
        _bottom = max(c[3] for c in _coords)
    else:
        _left = _top = _right = _bottom = 0
    return {
        "text": str(_raw_text or "").strip(),
        "normalized_text": _norm_text,
        "x": _left,
        "y": _top,
        "width": max(0, _right - _left),
        "height": max(0, _bottom - _top),
    }


def _read_ocr_text_only(image_variant: np.ndarray, *, lang: str, psm: int) -> dict:
    _config = _build_tesseract_config(lang=lang, psm=psm)
    try:
        _raw_text = pytesseract.image_to_string(
            image_variant, lang=lang, config=_config, timeout=_OCR_FAST_TIMEOUT_S
        )
    except RuntimeError:
        return {
            "text": "",
            "normalized_text": "",
        }
    return {
        "text": str(_raw_text or "").strip(),
        "normalized_text": normalize_ocr_text(_raw_text),
    }


def _iter_screen_ocr_regions(image_bgr: np.ndarray):
    _h, _w = image_bgr.shape[:2]
    if (_h * _w) <= 350_000:
        yield image_bgr, 0, 0
        return
    _band_h = min(260, max(170, _h // 4))
    _overlap = 64
    _step = max(64, _band_h - _overlap)
    _y = 0
    while _y < _h:
        _y2 = min(_h, _y + _band_h)
        yield image_bgr[_y:_y2, :], 0, _y
        if _y2 >= _h:
            break
        _y += _step


def _iter_text_candidate_regions(image_bgr: np.ndarray):
    try:
        _mask = generate_text_mask(image_bgr)
        _num, _labels, _stats, _centroids = cv2.connectedComponentsWithStats(_mask, 8)
    except Exception:
        return
    _h, _w = image_bgr.shape[:2]
    _regions: list[tuple[int, int, int, int, int]] = []
    for _idx in range(1, int(_num)):
        _x, _y, _rw, _rh, _area = [int(v) for v in _stats[_idx]]
        if _area < 10:
            continue
        if _rw < 8 or _rh < 8:
            continue
        _pad = 12
        _x1 = max(0, _x - _pad)
        _y1 = max(0, _y - _pad)
        _x2 = min(_w, _x + _rw + _pad)
        _y2 = min(_h, _y + _rh + _pad)
        _regions.append((_x1, _y1, _x2, _y2, _area))
    _regions.sort(key=lambda item: item[4], reverse=True)
    for _x1, _y1, _x2, _y2, _area in _regions[:8]:
        yield image_bgr[_y1:_y2, _x1:_x2], _x1, _y1


def extract_template_target_text(image_bgr: np.ndarray, *, lang: str = "kor+eng") -> str:
    _lang = _select_ocr_language(requested_lang=lang, target_text="")
    _best_text = ""
    _best_score = -1.0
    _prefer_hangul = "kor" in str(_lang or "").lower()
    for _variant_name, _variant_img, _scale in _prepare_ocr_variants(image_bgr, is_template=True):
        for _psm in (7, 6, 13):
            _entry = _read_ocr_line(_variant_img, lang=_lang, psm=_psm)
            _norm = _entry["normalized_text"]
            if not _norm:
                continue
            _has_hangul = bool(_HANGUL_RE.search(_norm))
            _has_alpha = any("A" <= ch <= "Z" for ch in _norm)
            _has_digit = any(ch.isdigit() for ch in _norm)
            _score = (len(_norm) * 10.0)
            if _prefer_hangul:
                if _has_hangul:
                    _score += 200.0
                elif _has_digit and not _has_alpha:
                    _score -= 80.0
                elif _has_alpha:
                    _score -= 40.0
            if _score > _best_score:
                _best_score = _score
                _best_text = _norm
    return _best_text


def find_target_text_match(
    image_bgr: np.ndarray,
    target_text: str,
    *,
    lang: str = "kor+eng",
    min_score: float = 0.92,
    template_bgr: np.ndarray | None = None,
    fast_mode: bool = False,
    max_time_s: float | None = None,
) -> OCRTextMatch:
    _target = normalize_ocr_text(target_text)
    if not _target or image_bgr is None or image_bgr.size == 0:
        return OCRTextMatch(found=False)
    _lang = _select_ocr_language(requested_lang=lang, target_text=_target)
    _has_template = template_bgr is not None
    _short_hangul_target = bool(re.fullmatch(r"[\uAC00-\uD7A3]{1,2}", str(_target or "")))
    _deadline = (time.perf_counter() + float(max_time_s)) if max_time_s and float(max_time_s) > 0.0 else None
    _best = OCRTextMatch(found=False)

    if _has_template and _short_hangul_target:
        _word_mask_budget = None
        if _deadline is not None:
            _word_mask_budget = max(0.20, (_deadline - time.perf_counter()) * 0.75)
        _word_mask_match = _match_template_word_mask_regions(
            image_bgr,
            template_bgr,
            fast_mode=fast_mode,
            max_time_s=_word_mask_budget,
        )
        if _word_mask_match.found:
            return OCRTextMatch(
                found=True,
                text=_target,
                normalized_text=_target,
                confidence=float(_word_mask_match.confidence),
                score=float(_word_mask_match.score),
                x=int(_word_mask_match.x),
                y=int(_word_mask_match.y),
                width=int(_word_mask_match.width),
                height=int(_word_mask_match.height),
                center_x=int(_word_mask_match.center_x),
                center_y=int(_word_mask_match.center_y),
                variant="word_mask_regions",
            )
        if _word_mask_match.score > _best.score:
            _best = _word_mask_match

        _component_budget = None
        if _deadline is not None:
            _component_budget = max(0.20, (_deadline - time.perf_counter()) * 0.70)
        _component_match = _match_template_component_sequence(
            image_bgr,
            template_bgr,
            target_text=_target,
            fast_mode=fast_mode,
            max_time_s=_component_budget,
        )
        if _component_match.found:
            return OCRTextMatch(
                found=True,
                text=_target,
                normalized_text=_target,
                confidence=float(_component_match.confidence),
                score=float(_component_match.score),
                x=int(_component_match.x),
                y=int(_component_match.y),
                width=int(_component_match.width),
                height=int(_component_match.height),
                center_x=int(_component_match.center_x),
                center_y=int(_component_match.center_y),
                variant="glyph_components",
            )
        if _component_match.score > _best.score:
            _best = _component_match

        _glyph_budget = None
        if _deadline is not None:
            _glyph_budget = max(0.20, (_deadline - time.perf_counter()) * 0.65)
        _glyph_match = _match_template_glyph_sequence(
            image_bgr,
            template_bgr,
            target_text=_target,
            fast_mode=fast_mode,
            max_time_s=_glyph_budget,
        )
        if _glyph_match.found:
            return OCRTextMatch(
                found=True,
                text=_target,
                normalized_text=_target,
                confidence=float(_glyph_match.confidence),
                score=float(_glyph_match.score),
                x=int(_glyph_match.x),
                y=int(_glyph_match.y),
                width=int(_glyph_match.width),
                height=int(_glyph_match.height),
                center_x=int(_glyph_match.center_x),
                center_y=int(_glyph_match.center_y),
                variant="glyph_sequence",
            )
        if _glyph_match.score > _best.score:
            _best = _glyph_match
        if not fast_mode:
            _mask_budget = None
            if _deadline is not None:
                _mask_budget = max(0.10, (_deadline - time.perf_counter()) * 0.40)
            _mask_match = _match_template_text_mask(
                image_bgr,
                template_bgr,
                fast_mode=fast_mode,
                max_time_s=_mask_budget,
            )
            if _mask_match.found:
                return OCRTextMatch(
                    found=True,
                    text=_target,
                    normalized_text=_target,
                    confidence=float(_mask_match.confidence),
                    score=float(_mask_match.score),
                    x=int(_mask_match.x),
                    y=int(_mask_match.y),
                    width=int(_mask_match.width),
                    height=int(_mask_match.height),
                    center_x=int(_mask_match.center_x),
                    center_y=int(_mask_match.center_y),
                    variant="text_mask",
                )
            if _mask_match.score > _best.score:
                _best = _mask_match

    _search_regions: list[tuple[np.ndarray, int, int]] = []
    _seen: set[tuple[int, int, int]] = set()

    def _append_region(_crop, _ox, _oy):
        _key = (int(_ox), int(_oy), int(getattr(_crop, "shape", [0, 0])[0]) * 10000 + int(getattr(_crop, "shape", [0, 0])[1]))
        if _key in _seen:
            return
        _seen.add(_key)
        _search_regions.append((_crop, _ox, _oy))

    _max_regions = 6 if fast_mode and _has_template and _short_hangul_target else (3 if fast_mode else 8)

    if template_bgr is not None:
        for _crop, _ox, _oy in _iter_colored_text_candidate_regions(image_bgr, template_bgr) or []:
            _append_region(_crop, _ox, _oy)
    for _crop, _ox, _oy in _iter_text_candidate_regions(image_bgr) or []:
        _append_region(_crop, _ox, _oy)
    if not _search_regions:
        for _crop, _ox, _oy in _iter_screen_ocr_regions(image_bgr):
            _append_region(_crop, _ox, _oy)

    for _crop, _ox, _oy in _search_regions[:_max_regions]:
        if _deadline is not None and time.perf_counter() >= _deadline:
            return _best
        _variants = _prepare_fast_screen_ocr_variants(_crop) if fast_mode else _prepare_ocr_variants(_crop, is_template=False)
        _psm_values = (7,) if fast_mode else (7, 6)
        for _variant_name, _variant_img, _scale in _variants:
            if _deadline is not None and time.perf_counter() >= _deadline:
                return _best
            for _psm in _psm_values:
                if _deadline is not None and time.perf_counter() >= _deadline:
                    return _best
                _entry = _read_ocr_text_only(_variant_img, lang=_lang, psm=_psm) if fast_mode else _read_ocr_line(_variant_img, lang=_lang, psm=_psm)
                _text_score = score_ocr_text_match(_target, _entry["normalized_text"])
                if _text_score < min_score:
                    continue
                if fast_mode:
                    _x = int(_ox)
                    _y = int(_oy)
                    _w = max(1, int(_crop.shape[1]))
                    _h = max(1, int(_crop.shape[0]))
                else:
                    _x = _ox + int(round(_entry["x"] / _scale))
                    _y = _oy + int(round(_entry["y"] / _scale))
                    _w = max(1, int(round(_entry["width"] / _scale)))
                    _h = max(1, int(round(_entry["height"] / _scale)))
                _cand = OCRTextMatch(
                    found=True,
                    text=str(_entry["text"]),
                    normalized_text=str(_entry["normalized_text"]),
                    confidence=float(_text_score),
                    score=float(_text_score),
                    x=_x,
                    y=_y,
                    width=_w,
                    height=_h,
                    center_x=_x + (_w // 2),
                    center_y=_y + (_h // 2),
                    variant=f"{_variant_name}:psm{_psm}",
                )
                if (not _best.found) or (_cand.score > _best.score):
                    _best = _cand
                if _cand.score >= 1.0:
                    return _cand
    return _best


def _select_ocr_language(*, requested_lang: str, target_text: str) -> str:
    _requested = str(requested_lang or "kor+eng").strip() or "kor+eng"
    _has_hangul_target = bool(_HANGUL_RE.search(str(target_text or "")))
    _kor_path = _LOCAL_TESSDATA_DIR / "kor.traineddata"
    if _kor_path.exists():
        if _has_hangul_target or "kor" in _requested:
            return "kor"
    return _requested


def _build_tesseract_config(*, lang: str, psm: int) -> str:
    _parts = [f"--oem 3 --psm {int(psm)}"]
    if str(lang or "").strip() == "kor":
        _kor_path = _LOCAL_TESSDATA_DIR / "kor.traineddata"
        if _kor_path.exists():
            _parts.append(f"--tessdata-dir {str(_LOCAL_TESSDATA_DIR).replace(chr(92), '/')}")
    return " ".join(_parts)
