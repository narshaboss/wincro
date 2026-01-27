"""
WinCro 키 입력 감지 다이얼로그

사용자가 키보드 키를 눌러 입력을 캡처합니다.
"""

import customtkinter as ctk
from typing import Optional

from .theme import COLORS


class KeyInputDialog(ctk.CTkToplevel):
    """키 입력 감지 다이얼로그"""

    def __init__(self, parent):
        super().__init__(parent)

        self._captured_key = None

        self.title("키 입력")
        self.geometry("350x200")
        self.resizable(False, False)
        self.configure(fg_color=COLORS["bg_dark"])

        self.transient(parent)
        self.grab_set()

        # 중앙 배치
        self.update_idletasks()
        x = (self.winfo_screenwidth() - 350) // 2
        y = (self.winfo_screenheight() - 200) // 2
        self.geometry(f"+{x}+{y}")

        # 안내 텍스트
        ctk.CTkLabel(
            self,
            text="원하는 키를 누르세요",
            font=ctk.CTkFont(size=14),
            text_color=COLORS["text_primary"],
        ).pack(pady=(20, 10))

        # 감지된 키 표시
        self._key_label = ctk.CTkLabel(
            self,
            text="대기 중...",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=COLORS["accent"],
        )
        self._key_label.pack(pady=10)

        # 버튼
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(pady=20)

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

        # 키 바인딩
        self.bind("<Key>", self._on_key_press)
        self.focus_set()

    def _on_key_press(self, event):
        """키 입력 감지"""
        key_name = event.keysym.lower()
        # 특수 키 이름 변환
        key_map = {
            "return": "enter",
            "escape": "esc",
            "control_l": "ctrl",
            "control_r": "ctrl",
            "alt_l": "alt",
            "alt_r": "alt",
            "shift_l": "shift",
            "shift_r": "shift",
        }
        self._captured_key = key_map.get(key_name, key_name)
        self._key_label.configure(text=self._captured_key.upper())

    def _on_ok(self):
        self.destroy()

    def _on_cancel(self):
        self._captured_key = None
        self.destroy()

    def get_key(self) -> Optional[str]:
        self.wait_window()
        return self._captured_key
