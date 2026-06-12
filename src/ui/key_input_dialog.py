"""
WinCro key input capture dialog.

Captures both the display combo, such as shift+up, and the exact key
down/up event sequence with inter-event timing.
"""

import time
from typing import Any, Dict, List, Optional, Tuple

import customtkinter as ctk

from .theme import COLORS, IOS_FONTS, IOS_METRICS


KEY_NAME_MAP = {
    "return": "enter",
    "escape": "esc",
    "control_l": "ctrl",
    "control_r": "ctrl",
    "alt_l": "alt",
    "alt_r": "alt",
    "shift_l": "shift",
    "shift_r": "shift",
    "prior": "pageup",
    "next": "pagedown",
    "backspace": "backspace",
    "delete": "delete",
    "insert": "insert",
    "space": "space",
    "up": "up",
    "down": "down",
    "left": "left",
    "right": "right",
}
MODIFIER_KEY_NAMES = {"shift", "ctrl", "alt"}
MODIFIER_ORDER = ("ctrl", "alt", "shift")
MODIFIER_STATE_MASKS = (("ctrl", 0x0004), ("alt", 0x0008), ("shift", 0x0001))


def normalize_key_name(keysym: str) -> str:
    key_name = (keysym or "").lower()
    return KEY_NAME_MAP.get(key_name, key_name)


def build_key_combo(keysym: str, state: int = 0, active_modifiers: Optional[set] = None) -> List[str]:
    key_name = normalize_key_name(keysym)
    if active_modifiers is not None:
        combo = [name for name in MODIFIER_ORDER if name in active_modifiers]
    else:
        combo = [name for name, mask in MODIFIER_STATE_MASKS if state & mask]
    if key_name and key_name not in MODIFIER_KEY_NAMES:
        combo.append(key_name)
    elif key_name and not combo:
        combo.append(key_name)
    return combo


def format_key_combo(keys: List[str]) -> str:
    return " + ".join((key or "").upper() for key in keys if key)


def format_key_events(events: List[Dict[str, Any]]) -> str:
    if not events:
        return "기록 대기 중"
    parts = []
    for event in events[-6:]:
        key = str(event.get("key", "")).upper()
        event_type = "↓" if event.get("event") == "down" else "↑"
        delay_ms = int(round(float(event.get("delay", 0.0) or 0.0) * 1000))
        parts.append(f"{delay_ms}ms {key}{event_type}")
    return "  ".join(parts)


class KeyInputDialog(ctk.CTkToplevel):
    """Key capture dialog with exact key down/up timing."""

    def __init__(self, parent):
        super().__init__(parent)

        self._captured_key: Optional[str] = None
        self._captured_keys: List[str] = []
        self._captured_key_events: List[Dict[str, Any]] = []
        self._active_modifiers = set()
        self._pressed_keys = set()
        self._last_event_at: Optional[float] = None
        self._did_wait = False

        self.title("키 입력")
        self.geometry("430x290")
        self.resizable(False, False)
        self.configure(fg_color=COLORS["bg_content"])

        self.transient(parent)
        self.grab_set()

        self.update_idletasks()
        x = (self.winfo_screenwidth() - 430) // 2
        y = (self.winfo_screenheight() - 290) // 2
        self.geometry(f"+{x}+{y}")

        ctk.CTkLabel(
            self,
            text="등록할 키를 실제로 눌렀다 떼세요",
            font=ctk.CTkFont(family=IOS_FONTS["family"], size=15, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(pady=(20, 6))

        ctk.CTkLabel(
            self,
            text="Shift+방향키처럼 민감한 입력은 누른 순서, 누른 시간, 떼는 순서까지 같이 저장됩니다.",
            font=ctk.CTkFont(family=IOS_FONTS["fallback"], size=11),
            text_color=COLORS["text_secondary"],
            wraplength=360,
            justify="center",
        ).pack(pady=(0, 10))

        self._key_label = ctk.CTkLabel(
            self,
            text="대기 중",
            font=ctk.CTkFont(family=IOS_FONTS["family"], size=20, weight="bold"),
            text_color=COLORS["accent"],
        )
        self._key_label.pack(pady=(4, 8))

        self._event_label = ctk.CTkLabel(
            self,
            text="기록 대기 중",
            font=ctk.CTkFont(family=IOS_FONTS["fallback"], size=11),
            text_color=COLORS["text_secondary"],
            wraplength=380,
            justify="center",
        )
        self._event_label.pack(pady=(0, 8))

        helper = ctk.CTkFrame(
            self,
            fg_color=COLORS["bg_glass"],
            corner_radius=IOS_METRICS["card_radius_compact"],
            border_width=IOS_METRICS["hairline"],
            border_color=COLORS["separator"],
        )
        helper.pack(fill="x", padx=24, pady=(0, 8))
        ctk.CTkLabel(
            helper,
            text="예: Shift 누름 → Up 누름 → Up 뗌 → Shift 뗌 → 확인",
            font=ctk.CTkFont(family=IOS_FONTS["fallback"], size=11),
            text_color=COLORS["text_primary"],
        ).pack(pady=8)

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(pady=12)

        ctk.CTkButton(
            btn_frame,
            text="확인",
            width=120,
            height=40,
            font=ctk.CTkFont(family=IOS_FONTS["family"], size=14, weight="bold"),
            fg_color=COLORS["success"],
            hover_color=COLORS["green_hover"],
            text_color=COLORS["bg_content"],
            corner_radius=IOS_METRICS["pill_radius"],
            command=self._on_ok,
        ).pack(side="left", padx=10)

        ctk.CTkButton(
            btn_frame,
            text="취소",
            width=120,
            height=40,
            font=ctk.CTkFont(family=IOS_FONTS["family"], size=14, weight="bold"),
            fg_color=COLORS["error"],
            hover_color=COLORS["danger_hover"],
            text_color=COLORS["text_primary"],
            corner_radius=IOS_METRICS["pill_radius"],
            command=self._on_cancel,
        ).pack(side="left", padx=10)

        self.bind("<KeyPress>", self._on_key_press)
        self.bind("<KeyRelease>", self._on_key_release)
        self.focus_set()

    def _record_event(self, event_type: str, key_name: str) -> None:
        now = time.perf_counter()
        delay = 0.0 if self._last_event_at is None else max(0.0, now - self._last_event_at)
        self._last_event_at = now
        self._captured_key_events.append({
            "event": event_type,
            "key": key_name,
            "delay": round(delay, 4),
        })
        self._event_label.configure(text=format_key_events(self._captured_key_events))

    def _on_key_press(self, event):
        key_name = normalize_key_name(event.keysym)
        if not key_name:
            return

        if key_name in self._pressed_keys:
            return

        self._pressed_keys.add(key_name)
        if key_name in MODIFIER_KEY_NAMES:
            self._active_modifiers.add(key_name)

        self._record_event("down", key_name)

        keys = build_key_combo(event.keysym, active_modifiers=self._active_modifiers)
        if keys:
            self._captured_keys = keys
            self._captured_key = "+".join(keys)
            self._key_label.configure(text=format_key_combo(keys))

    def _on_key_release(self, event):
        key_name = normalize_key_name(event.keysym)
        if not key_name:
            return

        if key_name in self._pressed_keys:
            self._record_event("up", key_name)
            self._pressed_keys.discard(key_name)

        if key_name in MODIFIER_KEY_NAMES:
            self._active_modifiers.discard(key_name)

    def _wait_for_close(self) -> None:
        if not self._did_wait:
            self._did_wait = True
            self.wait_window()

    def _on_ok(self):
        self.destroy()

    def _on_cancel(self):
        self._captured_key = None
        self._captured_keys = []
        self._captured_key_events = []
        self.destroy()

    def get_result(self) -> Tuple[List[str], List[Dict[str, Any]]]:
        self._wait_for_close()
        return list(self._captured_keys), [dict(event) for event in self._captured_key_events]

    def get_key(self) -> Optional[str]:
        self._wait_for_close()
        return self._captured_key

    def get_keys(self) -> List[str]:
        self._wait_for_close()
        return list(self._captured_keys)

    def get_key_events(self) -> List[Dict[str, Any]]:
        self._wait_for_close()
        return [dict(event) for event in self._captured_key_events]
