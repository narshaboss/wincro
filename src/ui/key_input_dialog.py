"""
WinCro key input capture dialog.

Captures a single key or a modifier combination such as shift+up.
"""

import customtkinter as ctk
from typing import List, Optional

from .theme import COLORS


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


class KeyInputDialog(ctk.CTkToplevel):
    """키 입력 감지 다이얼로그."""

    def __init__(self, parent):
        super().__init__(parent)

        self._captured_key: Optional[str] = None
        self._captured_keys: List[str] = []
        self._active_modifiers = set()

        self.title("키 입력")
        self.geometry("350x210")
        self.resizable(False, False)
        self.configure(fg_color=COLORS["bg_dark"])

        self.transient(parent)
        self.grab_set()

        self.update_idletasks()
        x = (self.winfo_screenwidth() - 350) // 2
        y = (self.winfo_screenheight() - 210) // 2
        self.geometry(f"+{x}+{y}")

        ctk.CTkLabel(
            self,
            text="등록할 키를 누르세요",
            font=ctk.CTkFont(size=14),
            text_color=COLORS["text_primary"],
        ).pack(pady=(20, 6))

        ctk.CTkLabel(
            self,
            text="Shift/Ctrl/Alt를 누른 상태로 방향키나 특정 키를 누르면 조합키로 등록됩니다.",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_secondary"],
            wraplength=300,
            justify="center",
        ).pack(pady=(0, 8))

        self._key_label = ctk.CTkLabel(
            self,
            text="대기 중...",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=COLORS["accent"],
        )
        self._key_label.pack(pady=8)

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(pady=16)

        ctk.CTkButton(
            btn_frame,
            text="확인",
            width=120,
            height=40,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=COLORS["success"],
            hover_color="#2ea44f",
            text_color="white",
            corner_radius=8,
            command=self._on_ok,
        ).pack(side="left", padx=10)

        ctk.CTkButton(
            btn_frame,
            text="취소",
            width=120,
            height=40,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=COLORS["error"],
            hover_color="#c0392b",
            text_color="white",
            corner_radius=8,
            command=self._on_cancel,
        ).pack(side="left", padx=10)

        self.bind("<KeyPress>", self._on_key_press)
        self.bind("<KeyRelease>", self._on_key_release)
        self.focus_set()

    def _on_key_press(self, event):
        key_name = normalize_key_name(event.keysym)
        if key_name in MODIFIER_KEY_NAMES:
            self._active_modifiers.add(key_name)
        keys = build_key_combo(event.keysym, active_modifiers=self._active_modifiers)
        if not keys:
            return
        self._captured_keys = keys
        self._captured_key = "+".join(keys)
        self._key_label.configure(text=format_key_combo(keys))

    def _on_key_release(self, event):
        key_name = normalize_key_name(event.keysym)
        if key_name in MODIFIER_KEY_NAMES:
            self._active_modifiers.discard(key_name)

    def _on_ok(self):
        self.destroy()

    def _on_cancel(self):
        self._captured_key = None
        self._captured_keys = []
        self.destroy()

    def get_key(self) -> Optional[str]:
        self.wait_window()
        return self._captured_key

    def get_keys(self) -> List[str]:
        self.wait_window()
        return list(self._captured_keys)
