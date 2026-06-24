import numpy as np

from src.player.rule_executor import _passes_image_visual_verification


def test_image_visual_verification_is_off_by_default():
    screenshot = np.full((20, 40, 3), 230, dtype=np.uint8)
    template = np.full((20, 40, 3), 20, dtype=np.uint8)

    assert _passes_image_visual_verification(
        screenshot,
        template,
        0,
        0,
        40,
        20,
        verify_color=False,
        verify_brightness=False,
    )


def test_image_visual_verification_rejects_brightness_mismatch_when_enabled():
    screenshot = np.full((20, 40, 3), 230, dtype=np.uint8)
    template = np.full((20, 40, 3), 20, dtype=np.uint8)

    assert not _passes_image_visual_verification(
        screenshot,
        template,
        0,
        0,
        40,
        20,
        verify_brightness=True,
    )


def test_image_visual_verification_rejects_color_mismatch_when_enabled():
    screenshot = np.zeros((20, 40, 3), dtype=np.uint8)
    template = np.zeros((20, 40, 3), dtype=np.uint8)
    screenshot[:, :] = (0, 0, 255)
    template[:, :] = (255, 0, 0)

    assert not _passes_image_visual_verification(
        screenshot,
        template,
        0,
        0,
        40,
        20,
        verify_color=True,
    )
