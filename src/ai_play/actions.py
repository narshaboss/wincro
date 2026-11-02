"""Version-one bounded action contract, shared by learning and saved playback."""

import copy

from ..agent_bridge.contracts import BridgeError, number
from .key_policy import SAFE_KEYS, reserved_keys


def validate_action(action, allowed=None):
    allowed = set(SAFE_KEYS if allowed is None else allowed) - reserved_keys()
    if not isinstance(action, dict):
        raise BridgeError("INVALID_ACTION", "행동은 객체여야 합니다.")
    kind = action.get("kind")
    keys = []
    if kind == "key":
        if set(action) != {"kind", "key"}:
            raise BridgeError("INVALID_ACTION", "단일 키 필드가 올바르지 않습니다.")
        keys = [action["key"]]
    elif kind == "hold":
        keys = action.get("keys")
        if (set(action) != {"kind", "keys", "duration_ms"} or not isinstance(keys, list)
                or not 1 <= len(keys) <= 2 or any(not isinstance(k, str) for k in keys)
                or len(set(keys)) != len(keys) or type(action["duration_ms"]) is not int
                or not 50 <= action["duration_ms"] <= 1000):
            raise BridgeError("INVALID_ACTION", "키 유지: 1~2키, 50~1000ms만 가능합니다.")
        if len(keys) == 2 and set(keys) & {"ctrl", "shift", "alt", "ctrlleft", "ctrlright", "altleft", "altright", "shiftleft", "shiftright"}:
            raise BridgeError("KEY_DENIED", "시스템 조합키는 허용하지 않습니다.")
    elif kind == "repeat":
        if (set(action) != {"kind", "key", "duration_ms", "count", "interval_ms"}
                or any(type(action[k]) is not int for k in ("duration_ms", "count", "interval_ms"))
                or not 50 <= action["duration_ms"] <= 1000 or not 1 <= action["count"] <= 5
                or not 20 <= action["interval_ms"] <= 500
                or action["duration_ms"] * action["count"] + action["interval_ms"] * (action["count"] - 1) > 1000):
            raise BridgeError("INVALID_ACTION", "키 반복은 최대 5회, 전체 1초 이내로 제한됩니다.")
        keys = [action["key"]]
    elif kind == "control":
        segments = action.get("segments")
        if (set(action) != {"kind", "segments"} or not isinstance(segments, list)
                or not 1 <= len(segments) <= 8):
            raise BridgeError("INVALID_ACTION", "연속 조작은 1~8구간입니다.")
        total = 0
        for segment in segments:
            if not isinstance(segment, dict) or set(segment) != {"keys", "duration_ms"}:
                raise BridgeError("INVALID_ACTION", "조작 구간에는 keys와 duration_ms가 필요합니다.")
            validate_action({"kind": "hold", **segment}, allowed)
            total += segment["duration_ms"]
        if total > 2000:
            raise BridgeError("INVALID_ACTION", "새 화면 확인 전 연속 조작은 최대 2초입니다.")
    elif kind == "navigate":
        from .navigation import validate_navigation
        validate_navigation(action)
        keys = ["up", "down", "left", "right"]
    elif kind == "click":
        if set(action) != {"kind", "x", "y"} or any(type(action[k]) is not int or action[k] < 0 for k in ("x", "y")):
            raise BridgeError("INVALID_ACTION", "창 내부의 정수 x, y가 필요합니다.")
    elif kind == "wait":
        if set(action) != {"kind", "seconds"} or not number(action["seconds"], .05, 2):
            raise BridgeError("INVALID_ACTION", "대기는 0.05~2초만 가능합니다.")
    else:
        raise BridgeError("ACTION_DENIED", "지원하지 않는 GPT 입력 유형입니다.")
    if any(not isinstance(k, str) or k not in allowed for k in keys):
        raise BridgeError("KEY_DENIED", "허용되지 않은 키 또는 WinCro 제어 단축키입니다.")
    return copy.deepcopy(action)


def input_cost(action):
    if action["kind"] == "navigate":
        return 40  # Conservative hard bound, shared with the feedback executor.
    if action["kind"] == "control":
        return len(action["segments"])
    return action.get("count", 1) if action["kind"] == "repeat" else 1
