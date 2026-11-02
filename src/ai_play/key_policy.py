"""Automatic single-key policy for the opt-in GPT lab only."""

import string


CONTROL_KEYS = frozenset({"f7", "f8", "f9", "f10", "f11", "f12"})
SAFE_KEYS = (frozenset("up down left right enter esc space tab pageup pagedown home end insert delete backspace capslock numlock scrolllock pause ctrl shift alt".split())
             | frozenset(string.ascii_lowercase + string.digits + string.punctuation)
             | frozenset("f" + str(i) for i in range(1, 13))) - CONTROL_KEYS


def reserved_keys():
    from ..utils.config import get_config
    key = str(getattr(get_config().player, "emergency_stop_key", "escape")).strip().lower() or "escape"
    key = {"escape": "esc", "return": "enter", "control": "ctrl"}.get(key, key)
    return CONTROL_KEYS | {key}


def automatic_keys():
    return sorted(SAFE_KEYS - reserved_keys())
