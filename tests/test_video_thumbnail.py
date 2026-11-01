from unittest.mock import Mock

import cv2
import numpy as np
import pytest

from src.ui import video_thumbnail


def test_video_thumbnail_uses_first_frame_and_draws_blue_play_badge(monkeypatch):
    frame = np.full((32, 30, 3), (15, 110, 210), dtype=np.uint8)
    capture = Mock()
    capture.read.return_value = (True, frame)
    monkeypatch.setattr(video_thumbnail.cv2, "VideoCapture", lambda path: capture)
    preview = video_thumbnail.load_video_thumbnail("clip.mp4", 100)
    capture.read.assert_called_once_with()
    capture.release.assert_called_once_with()
    assert preview.size == (93, 100)
    assert preview.getpixel((0, 0)) == (210, 110, 15)
    pixels = np.asarray(preview)
    assert ((pixels == (96, 165, 250)).all(axis=2)).sum() > 200
    assert preview.getpixel((46, 50)) == (0, 0, 0)
    assert np.all(frame == (15, 110, 210))


@pytest.mark.parametrize("raises", [False, True])
def test_video_thumbnail_releases_capture_on_decode_failure(monkeypatch, raises):
    capture = Mock()
    if raises:
        capture.read.side_effect = cv2.error("bad video")
    else:
        capture.read.return_value = (False, None)
    monkeypatch.setattr(video_thumbnail.cv2, "VideoCapture", lambda path: capture)
    with pytest.raises((ValueError, cv2.error)):
        video_thumbnail.load_video_thumbnail("broken.mp4", 60)
    capture.release.assert_called_once_with()


def test_video_thumbnail_clamps_thin_preview_to_nonzero_dimensions(monkeypatch):
    capture = Mock()
    capture.read.return_value = (True, np.zeros((1000, 1, 3), dtype=np.uint8))
    monkeypatch.setattr(video_thumbnail.cv2, "VideoCapture", lambda path: capture)
    assert video_thumbnail.load_video_thumbnail("thin.mp4", 24).size == (1, 24)
