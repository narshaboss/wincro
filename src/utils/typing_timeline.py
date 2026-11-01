"""Opt-in visible-character timing. No device access or intent inference here."""

import math
import time


def validate_typing_timestamps(text, timestamps):
    if not isinstance(timestamps, list) or len(timestamps) != len(text) or not 1 <= len(timestamps) <= 4096:
        raise ValueError("typing_timestamps must contain one offset per character (max 4096)")
    if any(type(t) not in (int, float) or not math.isfinite(t) or not 0 <= t <= 86400 for t in timestamps):
        raise ValueError("Invalid typing timestamp")
    if timestamps[0] != 0 or any(b < a for a, b in zip(timestamps, timestamps[1:])):
        raise ValueError("Typing offsets must start at zero and never decrease")


def replay_typing_timeline(text, timestamps, send_character, wait, clock=None):
    validate_typing_timestamps(text, timestamps)
    clock = clock or time.monotonic
    previous_started = None
    previous_duration = 0.0
    for index, char in enumerate(text):
        if index:
            interval = timestamps[index] - timestamps[index - 1]
            remaining = interval if previous_duration > interval else max(0.0, interval - (clock() - previous_started))
            if not wait(remaining):
                return False
        # Re-anchor after a pause/slow device instead of catching up with a burst.
        previous_started = clock()
        if send_character(char) is False:
            return False
        previous_duration = clock() - previous_started
    return True
