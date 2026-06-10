import random

import pytest
from PIL import Image, ImageDraw

from src.utils.digit_templates import DigitTemplateMatcher, TEMPLATE_DIR


def _coordinate_image(value: str) -> Image.Image:
    image = Image.new("RGB", (48, 24), (255, 255, 255))
    x = 2
    for digit in value:
        digit_image = Image.open(TEMPLATE_DIR / f"digit_{digit}.png").convert("RGB")
        image.paste(digit_image, (x, 5))
        x += digit_image.width + 3
    return image


def _coordinate_image_on_game_bg(value: str, *, seed: int | None = None) -> Image.Image:
    image = Image.new("RGB", (62, 30), (84, 58, 24))
    x = 3
    for digit in value:
        digit_image = Image.open(TEMPLATE_DIR / f"digit_{digit}.png").convert("L")
        mask = digit_image.point(lambda p: 255 if p < 128 else 0)
        image.paste(Image.new("RGB", digit_image.size, (0, 0, 0)), (x, 5), mask)
        x += digit_image.width + 3

    if seed is not None:
        rng = random.Random(seed)
        draw = ImageDraw.Draw(image)
        for _ in range(12):
            draw.point((rng.randint(0, image.width - 1), rng.randint(0, image.height - 1)), fill=(0, 0, 0))

    return image


def _anchor_image(letter: str) -> Image.Image:
    image = Image.new("RGB", (12, 16), (112, 76, 28))
    draw = ImageDraw.Draw(image)
    draw.text((2, 1), letter, fill=(0, 0, 0))
    return image


def test_component_coordinate_reader_keeps_leading_zero():
    matcher = DigitTemplateMatcher()
    image = _coordinate_image("014")

    value = matcher.read_number_from_region(
        [0, 0, image.width, image.height],
        screenshot=image,
        expected_digits=3,
        prefer_components=True,
    )

    assert value == 14


def test_existing_xy_coordinate_reader_uses_component_path():
    matcher = DigitTemplateMatcher()
    screenshot = Image.new("RGB", (120, 32), (255, 255, 255))
    x_image = _coordinate_image("046")
    y_image = _coordinate_image("014")
    screenshot.paste(x_image, (0, 0))
    screenshot.paste(y_image, (60, 0))

    matcher._last_screenshot = screenshot

    def fake_grab(*args, **kwargs):
        return screenshot

    import src.utils.digit_templates as digit_templates

    original_grab = digit_templates._safe_grab
    digit_templates._safe_grab = fake_grab
    try:
        x_value, y_value = matcher.read_both_coordinates([0, 0, 48, 24], [60, 0, 108, 24])
    finally:
        digit_templates._safe_grab = original_grab

    assert (x_value, y_value) == (46, 14)


def test_anchor_coordinate_reader_offsets_regions_from_xy_label_images(tmp_path):
    import src.utils.digit_templates as digit_templates

    if digit_templates.cv2 is None:
        pytest.skip("opencv is required for anchor matching")

    matcher = DigitTemplateMatcher()
    screenshot = Image.new("RGB", (150, 40), (112, 76, 28))
    x_anchor = _anchor_image("X")
    y_anchor = _anchor_image("Y")
    x_digits = _coordinate_image("025")
    y_digits = _coordinate_image("061")
    screenshot.paste(x_anchor, (5, 10))
    screenshot.paste(x_digits, (22, 6))
    screenshot.paste(y_anchor, (75, 10))
    screenshot.paste(y_digits, (92, 6))

    x_anchor_path = tmp_path / "x_anchor.png"
    y_anchor_path = tmp_path / "y_anchor.png"
    x_anchor.save(x_anchor_path)
    y_anchor.save(y_anchor_path)

    def fake_grab(*args, **kwargs):
        return screenshot

    original_grab = digit_templates._safe_grab
    digit_templates._safe_grab = fake_grab
    try:
        x_value, y_value = matcher.read_both_coordinates(
            [0, 0, 48, 24],
            [60, 0, 108, 24],
            x_anchor_image=str(x_anchor_path),
            y_anchor_image=str(y_anchor_path),
            x_anchor_offset=[17, -4, 65, 20],
            y_anchor_offset=[17, -4, 65, 20],
        )
    finally:
        digit_templates._safe_grab = original_grab

    assert (x_value, y_value) == (25, 61)
    assert matcher.last_coordinate_read_meta["method"] == "anchor"
    assert matcher.last_coordinate_read_meta["x_anchor"]["anchor_found"] is True
    assert matcher.last_coordinate_read_meta["y_anchor"]["anchor_found"] is True


def test_anchor_coordinate_reader_reads_split_xy_bar_regions(tmp_path):
    import src.utils.digit_templates as digit_templates

    if digit_templates.cv2 is None:
        pytest.skip("opencv is required for anchor matching")

    matcher = DigitTemplateMatcher()
    screenshot = Image.new("RGB", (150, 40), (112, 76, 28))
    x_anchor = _anchor_image("X")
    y_anchor = _anchor_image("Y")
    x_digits = _coordinate_image("025")
    y_digits = _coordinate_image("061")
    screenshot.paste(x_anchor, (5, 10))
    screenshot.paste(x_digits, (22, 6))
    screenshot.paste(y_anchor, (80, 10))
    screenshot.paste(y_digits, (97, 6))

    x_anchor_path = tmp_path / "x_anchor.png"
    y_anchor_path = tmp_path / "y_anchor.png"
    x_anchor.save(x_anchor_path)
    y_anchor.save(y_anchor_path)

    def fake_grab(*args, **kwargs):
        return screenshot

    original_grab = digit_templates._safe_grab
    digit_templates._safe_grab = fake_grab
    try:
        x_value, y_value = matcher.read_both_coordinates(
            [0, 0, 72, 34],
            [75, 0, 145, 34],
            x_anchor_image=str(x_anchor_path),
            y_anchor_image=str(y_anchor_path),
        )
    finally:
        digit_templates._safe_grab = original_grab

    assert (x_value, y_value) == (25, 61)
    assert matcher.last_coordinate_read_meta["method"] == "anchor"
    assert matcher.last_coordinate_read_meta["x_anchor"]["mode"] == "anchor_inside_region"
    assert matcher.last_coordinate_read_meta["y_anchor"]["mode"] == "anchor_inside_region"
    assert matcher.last_coordinate_read_meta["x_region"][0] > 5
    assert matcher.last_coordinate_read_meta["y_region"][0] > 80


def test_anchor_pair_coordinate_reader_reads_digits_inside_single_xy_bar_region(tmp_path):
    import src.utils.digit_templates as digit_templates

    if digit_templates.cv2 is None:
        pytest.skip("opencv is required for anchor matching")

    matcher = DigitTemplateMatcher()
    screenshot = Image.new("RGB", (150, 40), (112, 76, 28))
    x_anchor = _anchor_image("X")
    y_anchor = _anchor_image("Y")
    x_digits = _coordinate_image("025")
    y_digits = _coordinate_image("061")
    screenshot.paste(x_anchor, (5, 10))
    screenshot.paste(x_digits, (22, 6))
    screenshot.paste(y_anchor, (75, 10))
    screenshot.paste(y_digits, (92, 6))

    x_anchor_path = tmp_path / "x_anchor.png"
    y_anchor_path = tmp_path / "y_anchor.png"
    x_anchor.save(x_anchor_path)
    y_anchor.save(y_anchor_path)

    def fake_grab(*args, **kwargs):
        return screenshot

    original_grab = digit_templates._safe_grab
    digit_templates._safe_grab = fake_grab
    try:
        x_value, y_value = matcher.read_both_coordinates(
            None,
            None,
            x_anchor_image=str(x_anchor_path),
            y_anchor_image=str(y_anchor_path),
            anchor_search_region=[0, 0, 145, 35],
        )
    finally:
        digit_templates._safe_grab = original_grab

    assert (x_value, y_value) == (25, 61)
    assert matcher.last_coordinate_read_meta["method"] == "anchor_pair"
    assert matcher.last_coordinate_read_meta["anchor_pair"]["x_region"][0] > 5
    assert matcher.last_coordinate_read_meta["anchor_pair"]["y_region"][0] > 75


def test_anchor_coordinate_reader_falls_back_to_manual_region_when_anchor_missing(tmp_path):
    import src.utils.digit_templates as digit_templates

    matcher = DigitTemplateMatcher()
    screenshot = Image.new("RGB", (120, 32), (255, 255, 255))
    x_image = _coordinate_image("046")
    y_image = _coordinate_image("014")
    screenshot.paste(x_image, (0, 0))
    screenshot.paste(y_image, (60, 0))

    def fake_grab(*args, **kwargs):
        return screenshot

    original_grab = digit_templates._safe_grab
    digit_templates._safe_grab = fake_grab
    try:
        x_value, y_value = matcher.read_both_coordinates(
            [0, 0, 48, 24],
            [60, 0, 108, 24],
            x_anchor_image=str(tmp_path / "missing_x.png"),
            y_anchor_image=str(tmp_path / "missing_y.png"),
        )
    finally:
        digit_templates._safe_grab = original_grab

    assert (x_value, y_value) == (46, 14)
    assert matcher.last_coordinate_read_meta["x_anchor"]["fallback"] == "manual_region"
    assert matcher.last_coordinate_read_meta["y_anchor"]["fallback"] == "manual_region"


def test_coordinate_reader_recognizes_all_template_digit_combinations():
    matcher = DigitTemplateMatcher()

    for number in range(1000):
        image = _coordinate_image(f"{number:03d}")
        value = matcher.read_number_from_region(
            [0, 0, image.width, image.height],
            screenshot=image,
            expected_digits=3,
            prefer_components=True,
        )
        assert value == number


def test_low_confidence_component_result_falls_back_to_sliding_matcher():
    matcher = DigitTemplateMatcher()
    image = _coordinate_image_on_game_bg("004", seed=19)

    value = matcher.read_number_from_region(
        [0, 0, image.width, image.height],
        screenshot=image,
        expected_digits=3,
        prefer_components=True,
    )

    assert value == 4
