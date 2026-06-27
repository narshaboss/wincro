"""
Unified input controller.

Mouse move currently stays on pyautogui.
Mouse click / button / keyboard / scroll can use Arduino HID when enabled.
"""

import math
import threading
import time
from typing import Optional, Tuple

import pyautogui

from .config import get_config
from .logger import get_logger

logger = get_logger(__name__)

_arduino_hid = None
_arduino_load_failed = False
_strict_move_warned = False
_input_controller: Optional['InputController'] = None
_MOVE_VERIFY_TOLERANCE = 3
_MOVE_MAX_CORRECTIONS = 80
_MOVE_STEP_LIMIT = 64
_MOVE_MIN_STEP_LIMIT = 6
_MOVE_MIN_HUMAN_DURATION = 0.10
_MOVE_MAX_HUMAN_DURATION = 0.45
_MOVE_MIN_STEP_DELAY = 0.004
_MOVE_MAX_STEP_DELAY = 0.02
_HOTKEY_SETTLE_DELAY = 0.06
_HOTKEY_HOLD_DELAY = 0.08
_HOTKEY_RELEASE_DELAY = 0.02
_COMBO_PRE_RELEASE_DELAY = 0.002
_COMBO_MODIFIER_SETTLE_DELAY = 0.016
_COMBO_PRIMARY_TAP_DELAY = 0.006
_COMBO_POST_RELEASE_DELAY = 0.006
_KEY_TAP_HOLD_DELAY = 0.055
_KEY_TAP_POST_DELAY = 0.045
_MODIFIER_KEYS = {"shift", "ctrl", "alt", "win", "cmd", "command", "option"}
_DIRECTION_KEYS = {"up", "down", "left", "right"}
_KEY_EVENT_MAX_DELAY = 5.0
_RECORDED_DIRECTION_PRE_DELAY_CAP = 0.030
_RECORDED_DIRECTION_HOLD_DELAY_CAP = 0.012
_RECORDED_DIRECTION_POST_DELAY_CAP = 0.006
_input_block_event = threading.Event()
_input_block_reason = ""


def block_automation_input(reason: str = "") -> None:
    """Block automation-generated input until the next explicit execution start."""
    global _input_block_reason
    _input_block_reason = str(reason or "blocked")
    _input_block_event.set()


def unblock_automation_input() -> None:
    """Allow automation-generated input again."""
    global _input_block_reason
    _input_block_reason = ""
    _input_block_event.clear()


def is_automation_input_blocked() -> bool:
    return _input_block_event.is_set()


def _recorded_modifier_direction_combo_keys(events) -> list[str] | None:
    """Return combo keys when recorded events are exactly modifier(s)+direction tap."""
    if not events:
        return None

    direction_down_index = None
    for idx, event in enumerate(events):
        if event["event"] == "down" and event["key"] in _DIRECTION_KEYS:
            if direction_down_index is not None:
                return None
            direction_down_index = idx
    if direction_down_index is None:
        return None

    direction_key = events[direction_down_index]["key"]
    direction_up_index = None
    for idx in range(direction_down_index + 1, len(events)):
        event = events[idx]
        if event["event"] == "up" and event["key"] == direction_key:
            direction_up_index = idx
            break
    if direction_up_index is None:
        return None

    modifiers: list[str] = []
    pressed_modifiers = set()
    for event in events[:direction_down_index]:
        if event["event"] != "down" or event["key"] not in _MODIFIER_KEYS:
            return None
        key = event["key"]
        if key in pressed_modifiers:
            return None
        modifiers.append(key)
        pressed_modifiers.add(key)

    if not modifiers:
        return None

    if any(event["event"] == "down" for event in events[direction_down_index + 1:]):
        return None

    released_modifiers: list[str] = []
    for event in events[direction_up_index + 1:]:
        if event["event"] != "up" or event["key"] not in pressed_modifiers:
            return None
        released_modifiers.append(event["key"])

    if set(released_modifiers) != pressed_modifiers:
        return None

    return modifiers + [direction_key]


def _get_arduino():
    """Return Arduino HID singleton with lazy import."""
    global _arduino_hid, _arduino_load_failed

    if _arduino_hid is not None:
        return _arduino_hid
    if _arduino_load_failed:
        return None

    try:
        from .arduino_hid import get_arduino_hid

        _arduino_hid = get_arduino_hid()
    except ImportError:
        logger.warning("Arduino HID module is not available")
        _arduino_load_failed = True
        _arduino_hid = None
    except Exception as e:
        logger.debug(f"Arduino HID initialization failed, retry allowed: {e}")
        _arduino_hid = None

    return _arduino_hid


def reset_arduino_connection():
    """Reset cached Arduino connection state."""
    global _arduino_hid
    _arduino_hid = None


def is_arduino_enabled() -> bool:
    """True when Arduino HID input is enabled and connected."""
    config = get_config()
    if not config.arduino.enabled:
        return False

    arduino = _get_arduino()
    return arduino is not None and arduino.is_connected


def is_arduino_strict_enabled() -> bool:
    """True when software fallback must not be used for click/key/scroll."""
    config = get_config()
    return bool(config.arduino.enabled and getattr(config.arduino, "strict_mode", False))


def ensure_arduino_ready(force_connect: bool = True) -> Tuple[bool, str]:
    """Verify Arduino is ready before playback starts."""
    config = get_config()
    if not getattr(config.arduino, "require_for_playback", True):
        return True, "Arduino playback guard is disabled."
    if not config.arduino.enabled:
        return False, "Arduino input is disabled in settings."

    com_port = (config.arduino.com_port or "").strip()
    if not com_port:
        return False, "Arduino COM port is not configured."

    arduino = _get_arduino()
    if arduino is None:
        return False, "Arduino HID backend is unavailable."

    try:
        if arduino.is_connected:
            return True, f"{com_port} connected"
    except Exception as e:
        logger.warning(f"[ArduinoGuard] connection check failed: {e}")

    if not force_connect:
        return False, f"{com_port} is not connected."

    try:
        logger.info(f"[ArduinoGuard] connect attempt: port={com_port}, baud={config.arduino.baud_rate}")
        if arduino.connect(com_port, config.arduino.baud_rate) and arduino.is_connected:
            logger.info(f"[ArduinoGuard] connected: {com_port}")
            return True, f"{com_port} connected"
    except Exception as e:
        logger.error(f"[ArduinoGuard] connect failed: {e}")
        return False, f"{com_port} connect failed: {e}"

    return False, f"{com_port} connection failed."


class InputController:
    """Unified input controller with optional Arduino HID priority."""

    def __init__(self):
        self._config = get_config()

    def _use_arduino(self) -> bool:
        return is_arduino_enabled()

    def _strict_mode(self) -> bool:
        return is_arduino_strict_enabled()

    def _warn_software_move_if_strict(self) -> None:
        global _strict_move_warned
        if self._strict_mode() and not _strict_move_warned:
            _strict_move_warned = True
            logger.warning(
                "[ArduinoStrict] mouse move software fallback blocked or firmware lacks MM support"
            )

    def _arduino_move_relative(self, dx: int, dy: int) -> bool:
        arduino = _get_arduino()
        if arduino is None or not arduino.supports_mouse_move():
            return False
        try:
            return bool(arduino.mouse_move(int(dx), int(dy)))
        except Exception as e:
            logger.error(f"[ArduinoInput] mouse_move failed: {e}")
            return False

    def _get_cursor_pos(self) -> Optional[Tuple[int, int]]:
        try:
            pos = pyautogui.position()
            return int(pos[0]), int(pos[1])
        except Exception as e:
            logger.error(f"[SoftwareInput] cursor position read failed: {e}")
            return None

    def _compute_human_move_duration(self, distance: float, duration: float) -> float:
        if duration and duration > 0:
            return max(_MOVE_MIN_HUMAN_DURATION, min(_MOVE_MAX_HUMAN_DURATION, duration))
        auto_duration = 0.06 + min(distance / 900.0, 0.22)
        return max(_MOVE_MIN_HUMAN_DURATION, min(_MOVE_MAX_HUMAN_DURATION, auto_duration))

    def _arduino_move_to(self, x: int, y: int, duration: float = 0.0) -> bool:
        target_x = int(round(x))
        target_y = int(round(y))
        current = self._get_cursor_pos()
        if current is None:
            return False

        distance = math.hypot(target_x - current[0], target_y - current[1])
        effective_duration = self._compute_human_move_duration(distance, duration)
        step_budget = max(8, min(_MOVE_MAX_CORRECTIONS, int(distance / 5) + 1))
        step_delay = max(_MOVE_MIN_STEP_DELAY, min(_MOVE_MAX_STEP_DELAY, effective_duration / max(1, step_budget)))

        for attempt in range(_MOVE_MAX_CORRECTIONS):
            current_x, current_y = current
            dx = target_x - current_x
            dy = target_y - current_y

            if abs(dx) <= _MOVE_VERIFY_TOLERANCE and abs(dy) <= _MOVE_VERIFY_TOLERANCE:
                return True

            progress = min(1.0, attempt / max(1, step_budget - 1))
            ease = 1.0 - abs(2.0 * progress - 1.0)
            remaining = max(abs(dx), abs(dy))
            dynamic_cap = int(round(min(
                _MOVE_STEP_LIMIT,
                max(
                    _MOVE_MIN_STEP_LIMIT,
                    remaining * (0.42 if remaining > 120 else 0.32),
                ),
            )))
            max_step = int(round(_MOVE_MIN_STEP_LIMIT + (dynamic_cap - _MOVE_MIN_STEP_LIMIT) * max(0.35, ease)))
            max_step = max(_MOVE_MIN_STEP_LIMIT, min(_MOVE_STEP_LIMIT, max_step))

            step_x = max(-max_step, min(max_step, dx))
            step_y = max(-max_step, min(max_step, dy))
            if step_x == 0 and dx != 0:
                step_x = 1 if dx > 0 else -1
            if step_y == 0 and dy != 0:
                step_y = 1 if dy > 0 else -1

            if not self._arduino_move_relative(step_x, step_y):
                return False

            # 시작/끝은 조금 더 천천히, 중간은 더 시원하게 이동한다.
            edge_weight = 1.05 - (0.45 * ease)
            time.sleep(max(_MOVE_MIN_STEP_DELAY, min(_MOVE_MAX_STEP_DELAY, step_delay * edge_weight)))

            current = self._get_cursor_pos()
            if current is None:
                return False

        final_pos = self._get_cursor_pos()
        if final_pos is None:
            return False
        final_x, final_y = final_pos
        ok = abs(final_x - target_x) <= _MOVE_VERIFY_TOLERANCE and abs(final_y - target_y) <= _MOVE_VERIFY_TOLERANCE
        if not ok:
            logger.warning(
                f"[ArduinoInput] move_to verify failed target=({target_x},{target_y}) actual=({final_x},{final_y})"
            )
        return ok

    def _with_arduino_fallback(self, action_name, arduino_fn, fallback_fn):
        if is_automation_input_blocked():
            logger.warning(f"[InputSafety] blocked automation input: {action_name} reason={_input_block_reason or '-'}")
            return False

        if self._use_arduino():
            arduino = _get_arduino()
            if arduino is not None:
                try:
                    result = arduino_fn(arduino)
                    return True if result is None else bool(result)
                except Exception as e:
                    logger.error(f"[ArduinoInput] {action_name} failed via Arduino: {e}")
                    if self._strict_mode():
                        logger.warning(f"[ArduinoStrict] blocked software fallback for {action_name}")
                        return False

        if self._strict_mode():
            logger.warning(f"[ArduinoStrict] blocked software fallback for {action_name}")
            return False

        try:
            result = fallback_fn()
            return True if result is None else bool(result)
        except Exception as e:
            logger.error(f"[SoftwareInput] {action_name} failed: {e}")
            return False

    def _move_before_action(self, x: Optional[int], y: Optional[int], duration: float = 0.0) -> bool:
        if x is not None and y is not None:
            if not self.move_to(x, y, duration=duration):
                return False
            time.sleep(0.05)
        return True

    # Mouse operations
    def move_to(self, x: int, y: int, duration: float = 0.0) -> bool:
        if is_automation_input_blocked():
            logger.warning(f"[InputSafety] blocked automation input: move_to reason={_input_block_reason or '-'}")
            return False
        arduino = _get_arduino()
        if arduino is not None and self._use_arduino() and arduino.supports_mouse_move():
            if self._arduino_move_to(x, y, duration=duration):
                return True
            if self._strict_mode():
                self._warn_software_move_if_strict()
                return False
        if self._strict_mode():
            self._warn_software_move_if_strict()
            return False
        self._warn_software_move_if_strict()
        try:
            pyautogui.moveTo(x, y, duration=duration)
            return True
        except Exception as e:
            logger.error(f"[SoftwareInput] move_to failed: {e}")
            return False

    def move(self, dx: int, dy: int) -> bool:
        if is_automation_input_blocked():
            logger.warning(f"[InputSafety] blocked automation input: move reason={_input_block_reason or '-'}")
            return False
        arduino = _get_arduino()
        if arduino is not None and self._use_arduino() and arduino.supports_mouse_move():
            if self._arduino_move_relative(dx, dy):
                return True
            if self._strict_mode():
                self._warn_software_move_if_strict()
                return False
        if self._strict_mode():
            self._warn_software_move_if_strict()
            return False
        self._warn_software_move_if_strict()
        try:
            pyautogui.move(dx, dy)
            return True
        except Exception as e:
            logger.error(f"[SoftwareInput] move failed: {e}")
            return False

    def click(self, x: Optional[int] = None, y: Optional[int] = None, button: str = 'left', duration: float = 0.0) -> bool:
        if not self._move_before_action(x, y, duration):
            return False
        return self._with_arduino_fallback(
            f"click:{button}",
            lambda a: a.mouse_click(button),
            lambda: pyautogui.click(button=button),
        )

    def double_click(self, x: Optional[int] = None, y: Optional[int] = None, duration: float = 0.0) -> bool:
        if not self._move_before_action(x, y, duration):
            return False
        return self._with_arduino_fallback(
            "double_click",
            lambda a: a.mouse_double_click(),
            lambda: pyautogui.doubleClick(),
        )

    def right_click(self, x: Optional[int] = None, y: Optional[int] = None, duration: float = 0.0) -> bool:
        if not self._move_before_action(x, y, duration):
            return False
        return self._with_arduino_fallback(
            "right_click",
            lambda a: a.mouse_click('right'),
            lambda: pyautogui.rightClick(),
        )

    def mouse_down(self, button: str = 'left') -> bool:
        return self._with_arduino_fallback(
            f"mouse_down:{button}",
            lambda a: a.mouse_press(button),
            lambda: pyautogui.mouseDown(button=button),
        )

    def mouse_up(self, button: str = 'left') -> bool:
        return self._with_arduino_fallback(
            f"mouse_up:{button}",
            lambda a: a.mouse_release(button),
            lambda: pyautogui.mouseUp(button=button),
        )

    def drag(self, start_x: int, start_y: int, end_x: int, end_y: int, duration: float = 0.5, button: str = 'left') -> bool:
        if is_automation_input_blocked():
            logger.warning(f"[InputSafety] blocked automation input: drag reason={_input_block_reason or '-'}")
            return False
        arduino = _get_arduino()
        if arduino is not None and self._use_arduino() and arduino.supports_mouse_move():
                try:
                    if not self._arduino_move_to(start_x, start_y):
                        return False
                    time.sleep(0.05)
                    arduino.mouse_press(button)
                    time.sleep(0.05)
                    if not self._arduino_move_to(end_x, end_y, duration=duration):
                        arduino.mouse_release(button)
                        return False
                    time.sleep(0.05)
                    arduino.mouse_release(button)
                    return True
                except Exception as e:
                    logger.error(f"[ArduinoInput] drag failed: {e}")
                    if self._strict_mode():
                        logger.warning("[ArduinoStrict] blocked software drag fallback")
                        return False

        if self._strict_mode():
            logger.warning("[ArduinoStrict] blocked software drag fallback")
            return False

        self._warn_software_move_if_strict()
        try:
            pyautogui.moveTo(start_x, start_y)
            time.sleep(0.05)
        except Exception as e:
            logger.error(f"[SoftwareInput] drag move-to-start failed: {e}")
            return False

        try:
            dx = end_x - start_x
            dy = end_y - start_y
            pyautogui.drag(dx, dy, duration=duration, button=button)
            return True
        except Exception as e:
            logger.error(f"[SoftwareInput] drag failed: {e}")
            return False

    def scroll(self, amount: int, x: Optional[int] = None, y: Optional[int] = None) -> bool:
        if not self._move_before_action(x, y):
            return False
        return self._with_arduino_fallback(
            f"scroll:{amount}",
            lambda a: a.mouse_scroll(amount),
            lambda: pyautogui.scroll(amount),
        )

    # Keyboard operations
    def press(self, key: str) -> bool:
        key_text = str(key).strip().lower()
        if not key_text:
            return False
        key_parts = [part.strip() for part in key_text.split("+") if part.strip()]
        if len(key_parts) > 1:
            return self.hotkey(*key_parts)

        def _software_key_tap() -> bool:
            pressed = False
            previous_pause = getattr(pyautogui, "PAUSE", 0)
            try:
                pyautogui.PAUSE = 0
                pyautogui.keyDown(key_text)
                pressed = True
                time.sleep(_KEY_TAP_HOLD_DELAY)
                pyautogui.keyUp(key_text)
                pressed = False
                time.sleep(_KEY_TAP_POST_DELAY)
                return True
            finally:
                if pressed:
                    try:
                        pyautogui.keyUp(key_text)
                    except Exception:
                        pass
                pyautogui.PAUSE = previous_pause

        return self._with_arduino_fallback(
            f"press:{key_text}",
            lambda a: a.key_tap(key_text),
            _software_key_tap,
        )

    def tap_combo_once(self, *keys) -> bool:
        """Press modifiers, tap the final key once, then release modifiers immediately."""
        normalized_keys = [str(key).strip().lower() for key in keys if str(key).strip()]
        if not normalized_keys:
            return True
        if len(normalized_keys) == 1:
            return self.press(normalized_keys[0])

        combo = "+".join(normalized_keys)

        def _software_tap_combo() -> bool:
            pressed_modifiers = []
            primary_key = normalized_keys[-1]
            primary_pressed = False
            ok = True
            previous_pause = getattr(pyautogui, "PAUSE", 0)
            try:
                pyautogui.PAUSE = 0
                for key in reversed(normalized_keys):
                    pyautogui.keyUp(key)
                time.sleep(_COMBO_PRE_RELEASE_DELAY)

                for key in normalized_keys[:-1]:
                    pyautogui.keyDown(key)
                    pressed_modifiers.append(key)
                time.sleep(_COMBO_MODIFIER_SETTLE_DELAY)

                primary_pressed = True
                pyautogui.keyDown(primary_key)
                time.sleep(_COMBO_PRIMARY_TAP_DELAY)
                pyautogui.keyUp(primary_key)
                primary_pressed = False
            except Exception as e:
                ok = False
                logger.error(f"[SoftwareInput] tap_combo_once failed: {combo} ({e})")
            finally:
                if primary_pressed:
                    try:
                        pyautogui.keyUp(primary_key)
                    except Exception:
                        pass
                for key in reversed(pressed_modifiers):
                    try:
                        pyautogui.keyUp(key)
                    except Exception:
                        pass
                time.sleep(_COMBO_POST_RELEASE_DELAY)
                pyautogui.PAUSE = previous_pause
            return ok

        if is_automation_input_blocked():
            logger.warning(f"[InputSafety] blocked automation input: tap_combo_once:{combo} reason={_input_block_reason or '-'}")
            return False

        if self._use_arduino():
            arduino = _get_arduino()
            if arduino is not None:
                try:
                    return bool(arduino.key_combo_tap(*normalized_keys))
                except Exception as e:
                    logger.error(f"[ArduinoInput] tap_combo_once:{combo} failed via Arduino: {e}")
                    if self._strict_mode():
                        logger.warning(f"[ArduinoStrict] blocked software fallback for tap_combo_once:{combo}")
                        return False

        if self._strict_mode():
            logger.warning(f"[ArduinoStrict] blocked software fallback for tap_combo_once:{combo}")
            return False

        return _software_tap_combo()

    def key_down(self, key: str) -> bool:
        return self._with_arduino_fallback(
            f"key_down:{key}",
            lambda a: a.key_press(key),
            lambda: pyautogui.keyDown(key),
        )

    def key_up(self, key: str) -> bool:
        return self._with_arduino_fallback(
            f"key_up:{key}",
            lambda a: a.key_release(key),
            lambda: pyautogui.keyUp(key),
        )

    def hotkey(self, *keys) -> bool:
        normalized_keys = [str(key).strip().lower() for key in keys if str(key).strip()]
        if not normalized_keys:
            return True
        if len(normalized_keys) == 1:
            return self.press(normalized_keys[0])

        combo = "+".join(normalized_keys)
        pressed_keys = []
        ok = True
        primary_key = normalized_keys[-1]
        has_modifier = any(key in _MODIFIER_KEYS for key in normalized_keys[:-1])
        is_direction_tap = has_modifier and primary_key in _DIRECTION_KEYS
        if is_direction_tap:
            return self.tap_combo_once(*normalized_keys)

        # pyautogui.hotkey()/Arduino hotkey()의 짧은 탭 타이밍을 쓰지 않고,
        # modifier가 확실히 눌린 상태에서 본 키가 들어가도록 down/up을 직접 제어한다.
        previous_pause = getattr(pyautogui, "PAUSE", 0)
        try:
            pyautogui.PAUSE = 0
            for key in normalized_keys:
                if not self.key_down(key):
                    ok = False
                    logger.warning(f"[InputController] hotkey key_down failed: {combo} key={key}")
                    break
                pressed_keys.append(key)
                time.sleep(_HOTKEY_SETTLE_DELAY)

            if ok:
                time.sleep(_HOTKEY_HOLD_DELAY)

            release_ok = True
            for key in reversed(pressed_keys):
                if not self.key_up(key):
                    release_ok = False
                    logger.warning(f"[InputController] hotkey key_up failed: {combo} key={key}")
                time.sleep(_HOTKEY_RELEASE_DELAY)
        finally:
            pyautogui.PAUSE = previous_pause

        return ok and release_ok

    def replay_key_events(self, events, *, speed_multiplier: float = 1.0) -> bool:
        """Replay recorded key down/up events with their captured inter-event delays."""
        if not events:
            return True

        speed = max(0.01, float(speed_multiplier or 1.0))
        sanitized_events = []
        for raw_event in events:
            if not isinstance(raw_event, dict):
                continue
            key = str(raw_event.get("key", "")).strip().lower()
            event_type = str(raw_event.get("event", raw_event.get("type", ""))).strip().lower()
            if not key or event_type not in {"down", "up"}:
                continue
            try:
                raw_delay = float(raw_event.get("delay", 0.0) or 0.0)
            except (TypeError, ValueError):
                raw_delay = 0.0
            sanitized_events.append({
                "event": event_type,
                "key": key,
                "delay": max(0.0, min(_KEY_EVENT_MAX_DELAY, raw_delay)),
            })

        if not sanitized_events:
            return True

        atomic_combo_keys = _recorded_modifier_direction_combo_keys(sanitized_events)
        if atomic_combo_keys:
            replay_id = int(time.time() * 1000) % 1000000
            combo = "+".join(atomic_combo_keys)
            logger.info(
                "[KeyReplay] atomic modifier-direction tap id=%s combo=%s events=%s",
                replay_id,
                combo,
                len(sanitized_events),
            )
            ok = self.tap_combo_once(*atomic_combo_keys)
            logger.info(
                "[KeyReplay] atomic modifier-direction done id=%s combo=%s ok=%s",
                replay_id,
                combo,
                "Y" if ok else "N",
            )
            return ok

        direction_down_index = next(
            (
                idx for idx, event in enumerate(sanitized_events)
                if event["event"] == "down" and event["key"] in _DIRECTION_KEYS
            ),
            None,
        )
        direction_key = sanitized_events[direction_down_index]["key"] if direction_down_index is not None else ""
        direction_up_index = None
        if direction_down_index is not None:
            direction_up_index = next(
                (
                    idx for idx in range(direction_down_index + 1, len(sanitized_events))
                    if sanitized_events[idx]["event"] == "up" and sanitized_events[idx]["key"] == direction_key
                ),
                None,
            )

        modifier_before_direction = (
            direction_down_index is not None
            and any(
                event["event"] == "down" and event["key"] in _MODIFIER_KEYS
                for event in sanitized_events[:direction_down_index]
            )
        )
        capped_direction_combo = (
            modifier_before_direction
            and direction_up_index is not None
            and any(
                event["event"] == "up" and event["key"] in _MODIFIER_KEYS
                for event in sanitized_events[direction_up_index + 1:]
            )
        )
        if capped_direction_combo:
            original_pre = sanitized_events[direction_down_index]["delay"]
            original_hold = sanitized_events[direction_up_index]["delay"]
            sanitized_events[direction_down_index]["delay"] = min(
                original_pre,
                _RECORDED_DIRECTION_PRE_DELAY_CAP,
            )
            sanitized_events[direction_up_index]["delay"] = min(
                original_hold,
                _RECORDED_DIRECTION_HOLD_DELAY_CAP,
            )
            for idx in range(direction_up_index + 1, len(sanitized_events)):
                if sanitized_events[idx]["event"] == "up" and sanitized_events[idx]["key"] in _MODIFIER_KEYS:
                    original_post = sanitized_events[idx]["delay"]
                    sanitized_events[idx]["delay"] = min(
                        original_post,
                        _RECORDED_DIRECTION_POST_DELAY_CAP,
                    )
                    break
            if (
                sanitized_events[direction_down_index]["delay"] != original_pre
                or sanitized_events[direction_up_index]["delay"] != original_hold
            ):
                logger.info(
                    "[KeyReplay] direction combo timing capped: key=%s pre=%.4f->%.4f hold=%.4f->%.4f",
                    direction_key,
                    original_pre,
                    sanitized_events[direction_down_index]["delay"],
                    original_hold,
                    sanitized_events[direction_up_index]["delay"],
                )

        replay_id = int(time.time() * 1000) % 1000000
        replay_started = time.perf_counter()
        logger.info(
            "[KeyReplay] start id=%s events=%s speed=%.2f capped_direction=%s seq=%s",
            replay_id,
            len(sanitized_events),
            speed,
            "Y" if capped_direction_combo else "N",
            ",".join(f"{event['key']}:{event['event']}:{event['delay']:.4f}" for event in sanitized_events),
        )
        pressed_keys = []
        ok = True

        for idx, raw_event in enumerate(sanitized_events, start=1):
            if is_automation_input_blocked():
                logger.warning(f"[InputSafety] blocked automation input: replay_key_events reason={_input_block_reason or '-'}")
                ok = False
                break

            key = raw_event["key"]
            event_type = raw_event["event"]
            delay = raw_event["delay"] / speed
            if delay > 0:
                time.sleep(delay)

            event_started = time.perf_counter()
            if event_type == "down":
                if not self.key_down(key):
                    ok = False
                    logger.warning(
                        "[KeyReplay] event id=%s %s/%s %s %s failed after_delay=%.4f elapsed=%.4f",
                        replay_id,
                        idx,
                        len(sanitized_events),
                        key,
                        event_type,
                        delay,
                        time.perf_counter() - replay_started,
                    )
                    break
                pressed_keys.append(key)
            else:
                if not self.key_up(key):
                    ok = False
                    logger.warning(
                        "[KeyReplay] event id=%s %s/%s %s %s failed after_delay=%.4f elapsed=%.4f",
                        replay_id,
                        idx,
                        len(sanitized_events),
                        key,
                        event_type,
                        delay,
                        time.perf_counter() - replay_started,
                    )
                    break
                try:
                    pressed_keys.remove(key)
                except ValueError:
                    pass
            logger.info(
                "[KeyReplay] event id=%s %s/%s %s %s delay=%.4f action_ms=%.1f elapsed=%.4f",
                replay_id,
                idx,
                len(sanitized_events),
                key,
                event_type,
                delay,
                (time.perf_counter() - event_started) * 1000.0,
                time.perf_counter() - replay_started,
            )

        for key in reversed(pressed_keys):
            try:
                self.key_up(key)
            except Exception:
                pass
        logger.info(
            "[KeyReplay] done id=%s ok=%s elapsed=%.4f",
            replay_id,
            "Y" if ok else "N",
            time.perf_counter() - replay_started,
        )
        return ok

    def type_text(self, text: str, interval: float = 0.0) -> bool:
        return self._with_arduino_fallback(
            "type_text",
            lambda a: a.type_text(text, interval),
            lambda: pyautogui.write(text, interval=interval),
        )

    def typewrite(self, text, interval: float = 0.0) -> bool:
        if isinstance(text, (list, tuple)):
            return self.type_text("".join(str(part) for part in text), interval=interval)
        return self.type_text(str(text), interval=interval)

    def release_all(self) -> None:
        released_by_arduino = False
        if self._use_arduino():
            arduino = _get_arduino()
            if arduino is not None:
                try:
                    arduino.release_all()
                    released_by_arduino = True
                except Exception as e:
                    logger.error(f"[ArduinoInput] release_all failed: {e}")
        if released_by_arduino:
            return

        for key in (
            "shift", "ctrl", "alt", "win", "cmd", "command", "option",
            "up", "down", "left", "right", "enter", "space", "esc",
        ):
            try:
                pyautogui.keyUp(key)
            except Exception:
                pass
        for button in ("left", "right", "middle"):
            try:
                pyautogui.mouseUp(button=button)
            except Exception:
                pass


def get_input_controller() -> InputController:
    global _input_controller
    if _input_controller is None:
        _input_controller = InputController()
    return _input_controller
