from pathlib import Path

import cv2
import numpy as np

from src.analyzer.text_ocr import (
    _crop_mask_to_content,
    _match_template_glyph_sequence,
    _split_mask_into_glyphs,
    find_target_text_match,
    generate_text_mask,
    normalize_ocr_text,
    score_ocr_text_match,
)


def test_normalize_ocr_text_keeps_hangul_ascii_and_digits():
    assert normalize_ocr_text(" 원각 ") == "원각"
    assert normalize_ocr_text("Won-Gak 12") == "WONGAK12"
    assert normalize_ocr_text("[원각]!") == "원각"


def test_score_ocr_text_match_prefers_exact_match():
    assert score_ocr_text_match("원각", "원각") == 1.0
    assert score_ocr_text_match("원각", "원 각") == 1.0
    assert score_ocr_text_match("WONGAK", "won-gak") == 1.0


def test_score_ocr_text_match_allows_short_hangul_fuzzy_names():
    assert score_ocr_text_match("원각", "원가") >= 0.72
    assert score_ocr_text_match("원각", "원긱") >= 0.70


def test_score_ocr_text_match_still_rejects_unrelated_short_names():
    assert score_ocr_text_match("원각", "적군") == 0.0
    assert score_ocr_text_match("원각", "도사") == 0.0


def test_score_ocr_text_match_allows_longer_fuzzy_names():
    assert score_ocr_text_match("DOMESTICCITY", "DOMESTICC1TY") >= 0.85


def test_find_target_text_match_prefers_glyph_sequence_for_short_hangul_template():
    template_path = Path(__file__).resolve().parents[1] / "data" / "templates" / "wp_img_c917ed11.png"
    template = cv2.imread(str(template_path))
    assert template is not None

    screen = np.zeros((120, 180, 3), dtype=np.uint8)
    h, w = template.shape[:2]
    screen[40:40 + h, 70:70 + w] = template

    match = find_target_text_match(
        screen,
        "원각",
        template_bgr=template,
        fast_mode=True,
        max_time_s=0.5,
    )

    assert match.found
    assert match.variant == "word_mask_regions"
    assert match.score >= 0.54


def test_glyph_sequence_rejects_duplicate_first_glyph_false_positive():
    template_path = Path(__file__).resolve().parents[1] / "data" / "templates" / "wp_img_c917ed11.png"
    template = cv2.imread(str(template_path))
    assert template is not None

    word = _crop_mask_to_content(generate_text_mask(template))
    assert word is not None
    glyphs = _split_mask_into_glyphs(word[0], 2)
    assert len(glyphs) == 2

    first_glyph = glyphs[0][0]
    gh, gw = first_glyph.shape[:2]
    fake_mask = np.zeros((20, 30), dtype=np.uint8)
    fake_mask[4:4 + gh, 2:2 + gw] = first_glyph
    fake_mask[4:4 + gh, 16:16 + gw] = first_glyph
    fake_img = np.zeros((20, 30, 3), dtype=np.uint8)
    fake_img[fake_mask > 0] = (255, 255, 255)

    screen = np.zeros((90, 160, 3), dtype=np.uint8)
    screen[35:55, 60:90] = fake_img

    match = _match_template_glyph_sequence(
        screen,
        template,
        target_text="원각",
        fast_mode=True,
        max_time_s=0.5,
    )

    assert not match.found


def test_find_target_text_match_prefers_component_sequence_for_exact_short_hangul():
    template_path = Path(__file__).resolve().parents[1] / "data" / "templates" / "wp_img_c917ed11.png"
    template = cv2.imread(str(template_path))
    assert template is not None

    screen = np.zeros((120, 180, 3), dtype=np.uint8)
    h, w = template.shape[:2]
    screen[40:40 + h, 70:70 + w] = template

    match = find_target_text_match(
        screen,
        "원각",
        template_bgr=template,
        fast_mode=True,
        max_time_s=0.5,
    )

    assert match.found
    assert match.variant == "word_mask_regions"


def test_find_target_text_match_rejects_duplicate_first_glyph_false_positive():
    template_path = Path(__file__).resolve().parents[1] / "data" / "templates" / "wp_img_c917ed11.png"
    template = cv2.imread(str(template_path))
    assert template is not None

    word = _crop_mask_to_content(generate_text_mask(template))
    assert word is not None
    glyphs = _split_mask_into_glyphs(word[0], 2)
    assert len(glyphs) == 2

    first_glyph = glyphs[0][0]
    gh, gw = first_glyph.shape[:2]
    fake_mask = np.zeros((20, 30), dtype=np.uint8)
    fake_mask[4:4 + gh, 2:2 + gw] = first_glyph
    fake_mask[4:4 + gh, 16:16 + gw] = first_glyph
    fake_img = np.zeros((20, 30, 3), dtype=np.uint8)
    fake_img[fake_mask > 0] = (255, 255, 255)

    screen = np.zeros((90, 160, 3), dtype=np.uint8)
    screen[35:55, 60:90] = fake_img

    match = find_target_text_match(
        screen,
        "원각",
        template_bgr=template,
        fast_mode=True,
        max_time_s=0.5,
    )

    assert not match.found
