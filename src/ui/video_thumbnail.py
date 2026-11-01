"""Presentation-only video preview; never alters media or recognition samples."""

import cv2
from PIL import Image, ImageDraw


def load_video_thumbnail(path, size):
    capture = cv2.VideoCapture(str(path))
    try:
        ok, frame = capture.read()
        if not ok or frame is None:
            raise ValueError("Cannot read the video's first frame")
    finally:
        capture.release()
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    height, width = rgb.shape[:2]
    scale = min(size / width, size / height)
    width, height = max(1, int(width * scale)), max(1, int(height * scale))
    preview = Image.fromarray(cv2.resize(rgb, (width, height)))
    if min(width, height) < 12:
        return preview

    # Draw the badge separately so antialiasing does not soften the video frame.
    factor = 4
    overlay = Image.new("RGBA", (width * factor, height * factor))
    draw = ImageDraw.Draw(overlay)
    cx, cy = width * factor / 2, height * factor / 2
    radius = min(width, height) * .22 * factor
    draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius),
        fill="#60a5fa", outline="black", width=max(2, round(radius * .06)))
    draw.polygon(((cx - radius * .25, cy - radius * .5),
        (cx - radius * .25, cy + radius * .5), (cx + radius * .55, cy)), fill="black")
    overlay = overlay.resize((width, height), Image.Resampling.LANCZOS)
    return Image.alpha_composite(preview.convert("RGBA"), overlay).convert("RGB")
