"""Random key sequence action helpers."""

from __future__ import annotations

import random
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple


DEFAULT_RANDOM_KEY_STEP_DELAY = 0.8
DEFAULT_RANDOM_KEY_STEP_RANDOM_RANGE = 0.3


DEFAULT_RANDOM_KEY_SEQUENCES: List[List[Dict[str, Any]]] = [
    [{"keys": ["pageup"]}, {"keys": ["pagedown"]}, {"keys": ["left"]}, {"keys": ["enter"]}],
    [{"keys": ["pageup"]}, {"keys": ["pagedown"]}, {"keys": ["right"]}, {"keys": ["enter"]}],
    [{"keys": ["pageup"]}, {"keys": ["pagedown"]}, {"keys": ["enter"]}],
    [{"keys": ["pagedown"]}, {"keys": ["pageup"]}, {"keys": ["enter"]}],
]


def _clean_keys(raw_keys: Any) -> List[str]:
    if isinstance(raw_keys, str):
        raw_iterable: Iterable[Any] = [raw_keys]
    elif isinstance(raw_keys, Iterable):
        raw_iterable = raw_keys
    else:
        raw_iterable = []
    return [str(key).lower().strip() for key in raw_iterable if str(key).strip()]


def _clean_key_events(raw_events: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw_events, list):
        return []
    cleaned = []
    for event in raw_events:
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("event", "")).strip().lower()
        key = str(event.get("key", "")).strip().lower()
        if event_type not in {"down", "up"} or not key:
            continue
        try:
            delay = max(0.0, float(event.get("delay", 0.0) or 0.0))
        except (TypeError, ValueError):
            delay = 0.0
        cleaned.append({"event": event_type, "key": key, "delay": round(delay, 4)})
    return cleaned


def _clean_step_delay(value: Any, fallback: Optional[float] = None) -> Optional[float]:
    try:
        delay = float(value)
    except (TypeError, ValueError):
        return fallback
    return max(0.0, round(delay, 4))


def _clean_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def normalize_random_key_sequences(raw_sequences: Any) -> List[List[Dict[str, Any]]]:
    """Return normalized random key sequence groups.

    Shape:
    [
        [{"keys": ["pageup"], "key_events": []}, {"keys": ["enter"], "key_events": []}],
        ...
    ]
    """
    if not isinstance(raw_sequences, list):
        return []

    normalized: List[List[Dict[str, Any]]] = []
    for raw_sequence in raw_sequences:
        if not isinstance(raw_sequence, list):
            continue
        sequence: List[Dict[str, Any]] = []
        for raw_step in raw_sequence:
            delay_after = None
            delay_after_random = None
            delay_after_random_range = 0.0
            if isinstance(raw_step, dict):
                keys = _clean_keys(raw_step.get("keys", []))
                key_events = _clean_key_events(raw_step.get("key_events", []))
                delay_after = _clean_step_delay(raw_step.get("delay_after"))
                if "delay_after_random" in raw_step:
                    delay_after_random = _clean_bool(raw_step.get("delay_after_random", False))
                delay_after_random_range = _clean_step_delay(raw_step.get("delay_after_random_range"), DEFAULT_RANDOM_KEY_STEP_RANDOM_RANGE) or 0.0
            else:
                keys = _clean_keys(raw_step)
                key_events = []
            if not keys and not key_events:
                continue
            if not keys and key_events:
                keys = _keys_from_events(key_events)
            step = {"keys": keys, "key_events": key_events}
            if delay_after is not None:
                step["delay_after"] = delay_after
            if delay_after_random is not None:
                step["delay_after_random"] = delay_after_random
                step["delay_after_random_range"] = delay_after_random_range
            sequence.append(step)
        if sequence:
            normalized.append(sequence)
    return normalized


def clone_default_random_key_sequences() -> List[List[Dict[str, Any]]]:
    sequences = normalize_random_key_sequences(DEFAULT_RANDOM_KEY_SEQUENCES)
    for sequence in sequences:
        for step in sequence:
            apply_random_key_step_defaults(step)
    return sequences


def apply_random_key_step_defaults(step: Dict[str, Any]) -> Dict[str, Any]:
    """Fill missing per-step delay defaults without overwriting explicit user choices."""
    if "delay_after" not in step:
        step["delay_after"] = DEFAULT_RANDOM_KEY_STEP_DELAY
    if "delay_after_random" not in step:
        step["delay_after_random"] = True
    if "delay_after_random_range" not in step:
        step["delay_after_random_range"] = DEFAULT_RANDOM_KEY_STEP_RANDOM_RANGE
    return step


def _keys_from_events(events: List[Dict[str, Any]]) -> List[str]:
    keys = []
    for event in events:
        key = str(event.get("key", "")).lower().strip()
        if key and key not in keys:
            keys.append(key)
    return keys


def format_key_step(step: Dict[str, Any]) -> str:
    keys = _clean_keys(step.get("keys", []))
    if not keys:
        keys = _keys_from_events(_clean_key_events(step.get("key_events", [])))
    return "+".join(key.upper() for key in keys) if keys else "EMPTY"


def get_step_delay_after(step: Dict[str, Any], fallback: float = DEFAULT_RANDOM_KEY_STEP_DELAY) -> float:
    fallback_delay = _clean_step_delay(fallback, DEFAULT_RANDOM_KEY_STEP_DELAY)
    return _clean_step_delay(step.get("delay_after"), fallback_delay) or 0.0


def get_step_delay_after_randomized(step: Dict[str, Any], fallback: float = DEFAULT_RANDOM_KEY_STEP_DELAY) -> float:
    delay = get_step_delay_after(step, fallback)
    if not _clean_bool(step.get("delay_after_random", False)):
        return delay
    delay_range = _clean_step_delay(step.get("delay_after_random_range"), DEFAULT_RANDOM_KEY_STEP_RANDOM_RANGE) or 0.0
    if delay_range <= 0:
        return delay
    return max(0.0, delay + random.uniform(-delay_range, delay_range))


def format_random_key_sequence(sequence: List[Dict[str, Any]], *, max_steps: int = 8) -> str:
    normalized = normalize_random_key_sequences([sequence])
    if not normalized:
        return "EMPTY"
    steps = [format_key_step(step) for step in normalized[0]]
    if len(steps) > max_steps:
        return " → ".join(steps[:max_steps]) + " ..."
    return " → ".join(steps)


def format_random_key_sequences_summary(raw_sequences: Any) -> str:
    sequences = normalize_random_key_sequences(raw_sequences)
    if not sequences:
        return "묶음 없음"
    preview = format_random_key_sequence(sequences[0], max_steps=5)
    suffix = f" 외 {len(sequences) - 1}개" if len(sequences) > 1 else ""
    return f"{len(sequences)}개 묶음: {preview}{suffix}"


def execute_random_key_sequence(
    input_ctrl: Any,
    raw_sequences: Any,
    *,
    step_delay: float = DEFAULT_RANDOM_KEY_STEP_DELAY,
    speed_multiplier: float = 1.0,
    stop_event: Optional[Any] = None,
    log_prefix: str = "",
    log_func: Optional[Any] = None,
) -> Tuple[bool, str, int, str]:
    """Pick one sequence randomly and execute its key steps."""
    sequences = normalize_random_key_sequences(raw_sequences)
    if not sequences:
        return False, "랜덤키 묶음 없음", -1, ""

    selected_index = random.randrange(len(sequences))
    selected = sequences[selected_index]
    selected_label = format_random_key_sequence(selected)
    prefix = f"{log_prefix} " if log_prefix else ""
    if log_func:
        log_func(f"{prefix}랜덤키 선택 {selected_index + 1}/{len(sequences)}: {selected_label}")

    try:
        delay = max(0.0, float(step_delay or 0.0))
    except (TypeError, ValueError):
        delay = DEFAULT_RANDOM_KEY_STEP_DELAY
    try:
        speed = max(0.01, float(speed_multiplier or 1.0))
    except (TypeError, ValueError):
        speed = 1.0

    for step_index, step in enumerate(selected):
        if stop_event is not None and stop_event.is_set():
            return False, "실행 중지됨", selected_index, selected_label

        key_events = _clean_key_events(step.get("key_events", []))
        keys = _clean_keys(step.get("keys", []))
        step_label = format_key_step(step)

        if key_events:
            ok = input_ctrl.replay_key_events(key_events, speed_multiplier=speed)
        elif len(keys) == 1:
            ok = input_ctrl.press(keys[0])
        elif keys:
            ok = input_ctrl.hotkey(*keys)
        else:
            ok = False

        if ok is False:
            return False, f"랜덤키 입력 실패: {step_label}", selected_index, selected_label
        if log_func:
            log_func(f"{prefix}랜덤키 단계 {step_index + 1}/{len(selected)} 완료: {step_label}")

        if step_index < len(selected) - 1:
            step_wait = get_step_delay_after_randomized(step, delay)
            wait_time = step_wait / speed
            if wait_time <= 0:
                continue
            if stop_event is not None:
                if stop_event.wait(wait_time):
                    return False, "실행 중지됨", selected_index, selected_label
            else:
                time.sleep(wait_time)

    return True, f"랜덤키 완료: {selected_label}", selected_index, selected_label
