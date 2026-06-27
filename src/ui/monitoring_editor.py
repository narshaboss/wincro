"""
WinCro monitoring mode editor.

The monitoring mode is intentionally simple:
1. Wait for the final image of the action where monitoring is enabled.
2. If a monitoring image appears first, run its monitoring-only actions.
3. If that image has a route target, exit monitoring and continue from that action.
4. If the final image appears first, finish monitoring and continue normally.
"""

from __future__ import annotations

import copy
import shutil
import threading
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Callable

import customtkinter as ctk
import tkinter as tk
import cv2
import numpy as np
from PIL import Image

from ..utils.config import DATA_DIR, get_config, save_config
from ..utils.logger import get_logger
from .analyzer_view import get_cached_thumbnail, set_cached_thumbnail, submit_thumbnail_task
from .constants import ACTION_NAMES_SHORT
from .key_input_dialog import KeyInputDialog, format_key_combo
from .text_overflow import truncate_ui_text
from .theme import COLORS, IOS_FONTS, IOS_METRICS

logger = get_logger(__name__)

_MONITORING_SETTINGS_CLIPBOARD: dict | None = None


class MonitorActionEditorDialog(ctk.CTkToplevel):
    """Direct editor for monitoring-only actions."""

    ACTION_TYPES = ("이미지 클릭", "마우스 클릭", "키 입력", "텍스트 입력", "스크롤", "드래그")
    CLICK_TYPES = ("click", "double_click", "right_click")

    def __init__(self, editor: "MonitoringModeEditor", action: dict | None, on_save):
        super().__init__(editor)
        self._editor = editor
        self._source_action = copy.deepcopy(action or {})
        self._on_save = on_save
        self._field_vars: dict[str, tk.Variable] = {}
        self._text_box = None
        self._image_path = str(self._source_action.get("image") or "")
        self._search_region = MonitoringModeEditor._normalize_search_region_value(
            self._source_action.get("search_region")
        )
        self._confidence = MonitoringModeEditor._safe_confidence(self._source_action.get("confidence", 0.8))
        self._key_events = [dict(event) for event in (self._source_action.get("key_events") or []) if isinstance(event, dict)]
        self._key_label = None
        self._image_label = None
        self._region_label = None
        self._confidence_label = None
        self._detail_frame = None

        self.title("모니터링 전용액션")
        self.geometry("620x720")
        self.minsize(580, 620)
        self.configure(fg_color=COLORS["bg_dark"])
        self.transient(editor)
        self.grab_set()

        self.update_idletasks()
        x = max(0, editor.winfo_x() + (editor.winfo_width() - 620) // 2)
        y = max(0, editor.winfo_y() + (editor.winfo_height() - 720) // 2)
        self.geometry(f"+{x}+{y}")

        self._build_ui()

    def _font(self, size: int, weight: str | None = None):
        return self._editor._font(size, weight)

    def _build_ui(self) -> None:
        root = ctk.CTkScrollableFrame(self, fg_color="transparent")
        root.pack(fill="both", expand=True, padx=18, pady=16)

        ctk.CTkLabel(
            root,
            text="모니터링 전용액션",
            font=self._font(19, "bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor="w")
        ctk.CTkLabel(
            root,
            text="감시 이미지가 발견되었을 때만 실행되는 액션입니다. 이미지 클릭은 인식률, 검색범위, 색상/밝기 확인을 여기서 바로 설정합니다.",
            font=self._font(12),
            text_color=COLORS["text_secondary"],
            wraplength=560,
            justify="left",
        ).pack(anchor="w", pady=(4, 14))

        type_row = ctk.CTkFrame(root, fg_color=COLORS["bg_card"], corner_radius=IOS_METRICS["control_radius"])
        type_row.pack(fill="x", pady=(0, 10))
        type_inner = ctk.CTkFrame(type_row, fg_color="transparent")
        type_inner.pack(fill="x", padx=12, pady=12)
        ctk.CTkLabel(
            type_inner,
            text="액션 종류",
            width=90,
            anchor="w",
            font=self._font(13, "bold"),
            text_color=COLORS["text_primary"],
        ).pack(side="left", padx=(0, 8))

        action_type = str(self._source_action.get("type") or "이미지 클릭")
        if action_type not in self.ACTION_TYPES:
            action_type = "이미지 클릭"
        self._field_vars["type"] = tk.StringVar(value=action_type)
        ctk.CTkComboBox(
            type_inner,
            values=list(self.ACTION_TYPES),
            variable=self._field_vars["type"],
            width=220,
            height=32,
            font=self._font(12, "bold"),
            dropdown_font=self._font(12),
            fg_color=COLORS["bg_elevated"],
            button_color=COLORS["accent_blue"],
            button_hover_color=COLORS["hover_blue"],
            text_color=COLORS["text_primary"],
            command=lambda _value: self._rebuild_detail_fields(),
        ).pack(side="left", fill="x", expand=True)

        self._detail_frame = ctk.CTkFrame(root, fg_color=COLORS["bg_card"], corner_radius=IOS_METRICS["control_radius"])
        self._detail_frame.pack(fill="x", pady=(0, 10))
        self._rebuild_detail_fields()

        self._build_common_options(root)

        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(fill="x", padx=18, pady=(0, 16))
        ctk.CTkButton(
            bottom,
            text="저장",
            width=110,
            height=38,
            font=self._font(13, "bold"),
            fg_color=COLORS["success"],
            hover_color=COLORS["green_hover"],
            command=self._save,
        ).pack(side="right", padx=(8, 0))
        ctk.CTkButton(
            bottom,
            text="취소",
            width=110,
            height=38,
            font=self._font(13, "bold"),
            fg_color=COLORS["bg_card"],
            hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_secondary"],
            command=self.destroy,
        ).pack(side="right")

    def _clear_detail(self) -> None:
        if self._detail_frame is None:
            return
        for child in self._detail_frame.winfo_children():
            child.destroy()

    def _row(self, parent, label: str):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=6)
        ctk.CTkLabel(
            row,
            text=label,
            width=96,
            anchor="w",
            font=self._font(12, "bold"),
            text_color=COLORS["text_secondary"],
        ).pack(side="left", padx=(0, 8))
        return row

    def _entry_var(self, key: str, default="") -> tk.StringVar:
        if key not in self._field_vars:
            self._field_vars[key] = tk.StringVar(value=str(self._source_action.get(key, default)))
        return self._field_vars[key]

    def _bool_var(self, key: str, default=False) -> tk.BooleanVar:
        if key not in self._field_vars:
            self._field_vars[key] = tk.BooleanVar(value=bool(self._source_action.get(key, default)))
        return self._field_vars[key]

    def _entry(self, parent, key: str, default="", width=180):
        entry = ctk.CTkEntry(
            parent,
            textvariable=self._entry_var(key, default),
            width=width,
            height=30,
            font=self._font(12),
            fg_color=COLORS["bg_elevated"],
            text_color=COLORS["text_primary"],
        )
        entry.pack(side="left", fill="x", expand=True)
        return entry

    def _rebuild_detail_fields(self) -> None:
        self._clear_detail()
        action_type = self._field_vars["type"].get()
        ctk.CTkLabel(
            self._detail_frame,
            text="상세 설정",
            font=self._font(14, "bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor="w", padx=12, pady=(12, 6))

        if action_type == "이미지 클릭":
            self._build_image_click_fields()
        elif action_type == "마우스 클릭":
            self._build_mouse_fields()
        elif action_type == "키 입력":
            self._build_key_fields()
        elif action_type == "텍스트 입력":
            self._build_text_fields()
        elif action_type == "스크롤":
            self._build_scroll_fields()
        elif action_type == "드래그":
            self._build_drag_fields()

    def _build_click_type_combo(self, row, key="click_type") -> None:
        value = str(self._source_action.get(key, "click"))
        if value not in self.CLICK_TYPES:
            value = "click"
        self._field_vars[key] = tk.StringVar(value=value)
        ctk.CTkComboBox(
            row,
            values=list(self.CLICK_TYPES),
            variable=self._field_vars[key],
            width=160,
            height=30,
            font=self._font(12),
            dropdown_font=self._font(12),
            fg_color=COLORS["bg_elevated"],
            button_color=COLORS["accent_blue"],
            button_hover_color=COLORS["hover_blue"],
            text_color=COLORS["text_primary"],
        ).pack(side="left")

    def _build_image_click_fields(self) -> None:
        image_row = self._row(self._detail_frame, "이미지")
        self._image_label = ctk.CTkLabel(
            image_row,
            text=truncate_ui_text(Path(self._image_path).name, 34) if self._image_path else "이미지 없음",
            anchor="w",
            font=self._font(12, "bold"),
            text_color=COLORS["accent_text"] if self._image_path else COLORS["text_muted"],
        )
        self._image_label.pack(side="left", fill="x", expand=True, padx=(0, 8))
        ctk.CTkButton(
            image_row,
            text="선택",
            width=76,
            height=30,
            font=self._font(12, "bold"),
            fg_color=COLORS["accent_blue"],
            hover_color=COLORS["hover_blue"],
            command=self._choose_image,
        ).pack(side="right")

        click_row = self._row(self._detail_frame, "클릭 유형")
        self._build_click_type_combo(click_row)

        conf_row = self._row(self._detail_frame, "인식률")
        self._confidence_label = ctk.CTkLabel(
            conf_row,
            text=f"{int(self._confidence * 100)}%",
            width=48,
            font=self._font(12, "bold"),
            text_color=COLORS["accent_text"],
        )
        self._confidence_label.pack(side="left", padx=(0, 8))
        slider = ctk.CTkSlider(
            conf_row,
            from_=0.3,
            to=1.0,
            number_of_steps=70,
            width=250,
            command=self._on_confidence,
        )
        slider.set(self._confidence)
        slider.pack(side="left", fill="x", expand=True)

        region_row = self._row(self._detail_frame, "검색범위")
        self._region_label = ctk.CTkLabel(
            region_row,
            text=self._region_text(),
            anchor="w",
            font=self._font(11),
            text_color=COLORS["text_secondary"],
        )
        self._region_label.pack(side="left", fill="x", expand=True, padx=(0, 8))
        ctk.CTkButton(
            region_row,
            text="범위",
            width=68,
            height=30,
            font=self._font(12, "bold"),
            fg_color=COLORS["bg_elevated"],
            hover_color=COLORS["bg_card_hover"],
            command=self._show_region_options,
        ).pack(side="right", padx=(0, 6))
        ctk.CTkButton(
            region_row,
            text="전체",
            width=58,
            height=30,
            font=self._font(12),
            fg_color=COLORS["bg_card_hover"],
            hover_color=COLORS["bg_glass"],
            text_color=COLORS["text_secondary"],
            command=self._clear_region,
        ).pack(side="right")

        option_row = self._row(self._detail_frame, "확인 옵션")
        option_grid = ctk.CTkFrame(option_row, fg_color="transparent")
        option_grid.pack(side="left", fill="x", expand=True)
        for index, (key, label) in enumerate((
            ("verify_image_color", "색상 확인"),
            ("verify_image_brightness", "밝기 확인"),
            ("alternate_mouse_route", "직각 이동"),
            ("click_until_image_disappears", "사라질 때까지 반복"),
        )):
            ctk.CTkCheckBox(
                option_grid,
                text=label,
                variable=self._bool_var(key, False),
                font=self._font(11),
                text_color=COLORS["text_secondary"],
                fg_color=COLORS["accent_blue"],
                hover_color=COLORS["hover_blue"],
            ).grid(row=index // 2, column=index % 2, sticky="w", padx=(0, 22), pady=3)
        option_grid.grid_columnconfigure(0, weight=1, minsize=160)
        option_grid.grid_columnconfigure(1, weight=1, minsize=180)

        delay_row = self._row(self._detail_frame, "사라짐 대기")
        self._entry(delay_row, "click_until_image_disappears_delay", 0.5, width=100)
        ctk.CTkLabel(delay_row, text="초", font=self._font(11), text_color=COLORS["text_muted"]).pack(side="left", padx=(6, 0))

    def _build_mouse_fields(self) -> None:
        row = self._row(self._detail_frame, "좌표")
        self._entry(row, "x", 0, width=90)
        ctk.CTkLabel(row, text=",", font=self._font(12), text_color=COLORS["text_muted"]).pack(side="left", padx=4)
        self._entry(row, "y", 0, width=90)
        click_row = self._row(self._detail_frame, "클릭 유형")
        self._build_click_type_combo(click_row)
        option_row = self._row(self._detail_frame, "옵션")
        ctk.CTkCheckBox(
            option_row,
            text="직각 이동",
            variable=self._bool_var("alternate_mouse_route", False),
            font=self._font(11),
            text_color=COLORS["text_secondary"],
            fg_color=COLORS["accent_blue"],
            hover_color=COLORS["hover_blue"],
        ).pack(side="left")

    def _build_key_fields(self) -> None:
        row = self._row(self._detail_frame, "키")
        keys = [str(key).lower().strip() for key in (self._source_action.get("keys", []) or []) if str(key).strip()]
        self._field_vars["keys_text"] = tk.StringVar(value="+".join(keys))
        self._key_label = ctk.CTkLabel(
            row,
            text=self._format_key_text(keys),
            anchor="w",
            font=self._font(12, "bold"),
            text_color=COLORS["accent_text"] if keys else COLORS["text_muted"],
        )
        self._key_label.pack(side="left", fill="x", expand=True, padx=(0, 8))
        ctk.CTkButton(
            row,
            text="키 입력 등록",
            width=104,
            height=30,
            font=self._font(12, "bold"),
            fg_color=COLORS["accent_blue"],
            hover_color=COLORS["hover_blue"],
            command=self._capture_key_input,
        ).pack(side="right", padx=(6, 0))
        ctk.CTkButton(
            row,
            text="해제",
            width=56,
            height=30,
            font=self._font(12, "bold"),
            fg_color=COLORS["bg_card_hover"],
            hover_color=COLORS["bg_glass"],
            text_color=COLORS["text_secondary"],
            command=self._clear_key_input,
        ).pack(side="right")

        ctk.CTkLabel(
            self._detail_frame,
            text="일반 계획수정 키입력과 동일하게 실제 누른 순서와 떼는 타이밍까지 저장합니다.",
            font=self._font(11),
            text_color=COLORS["text_muted"],
            anchor="w",
            wraplength=520,
            justify="left",
        ).pack(fill="x", padx=12, pady=(0, 8))

    def _build_text_fields(self) -> None:
        row = self._row(self._detail_frame, "입력문")
        self._text_box = ctk.CTkTextbox(
            row,
            width=380,
            height=86,
            font=self._font(12),
            fg_color=COLORS["bg_elevated"],
            text_color=COLORS["text_primary"],
        )
        self._text_box.pack(side="left", fill="x", expand=True)
        self._text_box.insert("1.0", str(self._source_action.get("text", "")))

        typing_row = self._row(self._detail_frame, "타이핑")
        ctk.CTkCheckBox(
            typing_row,
            text="랜덤",
            variable=self._bool_var("typing_random", False),
            font=self._font(11),
            text_color=COLORS["text_secondary"],
            fg_color=COLORS["accent_blue"],
            hover_color=COLORS["hover_blue"],
        ).pack(side="left", padx=(0, 8))
        self._entry(typing_row, "typing_delay", 0.1, width=80)
        ctk.CTkLabel(typing_row, text="±", font=self._font(11), text_color=COLORS["text_muted"]).pack(side="left", padx=5)
        self._entry(typing_row, "typing_delay_range", 0.05, width=80)

    def _build_scroll_fields(self) -> None:
        row = self._row(self._detail_frame, "스크롤")
        self._entry(row, "amount", self._source_action.get("amount", 0), width=140)

    def _build_drag_fields(self) -> None:
        row = self._row(self._detail_frame, "시작")
        self._entry(row, "from_x", 0, width=90)
        ctk.CTkLabel(row, text=",", font=self._font(12), text_color=COLORS["text_muted"]).pack(side="left", padx=4)
        self._entry(row, "from_y", 0, width=90)
        row2 = self._row(self._detail_frame, "도착")
        self._entry(row2, "to_x", 0, width=90)
        ctk.CTkLabel(row2, text=",", font=self._font(12), text_color=COLORS["text_muted"]).pack(side="left", padx=4)
        self._entry(row2, "to_y", 0, width=90)

    @staticmethod
    def _format_key_text(keys: list[str]) -> str:
        clean_keys = [str(key).lower().strip() for key in (keys or []) if str(key).strip()]
        return format_key_combo(clean_keys) if clean_keys else "키 입력 없음"

    def _current_key_list(self) -> list[str]:
        raw = self._field_vars.get("keys_text")
        if raw is None:
            return []
        return [part.strip().lower() for part in str(raw.get()).replace(",", "+").split("+") if part.strip()]

    def _refresh_key_label(self) -> None:
        if self._key_label is None:
            return
        keys = self._current_key_list()
        self._key_label.configure(
            text=self._format_key_text(keys),
            text_color=COLORS["accent_text"] if keys else COLORS["text_muted"],
        )

    def _capture_key_input(self) -> None:
        dialog = KeyInputDialog(self)
        keys, key_events = dialog.get_result()
        if not keys:
            return
        clean_keys = [str(key).lower().strip() for key in keys if str(key).strip()]
        self._field_vars["keys_text"].set("+".join(clean_keys))
        self._key_events = [dict(event) for event in key_events if isinstance(event, dict)]
        self._refresh_key_label()

    def _clear_key_input(self) -> None:
        self._field_vars["keys_text"].set("")
        self._key_events = []
        self._refresh_key_label()

    def _build_common_options(self, parent) -> None:
        card = ctk.CTkFrame(parent, fg_color=COLORS["bg_card"], corner_radius=IOS_METRICS["control_radius"])
        card.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(
            card,
            text="반복/대기",
            font=self._font(14, "bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor="w", padx=12, pady=(12, 6))

        row = self._row(card, "반복")
        self._entry(row, "repeat_count", 1, width=80)
        ctk.CTkLabel(row, text="회", font=self._font(11), text_color=COLORS["text_muted"]).pack(side="left", padx=(5, 12))
        self._entry(row, "repeat_delay", 0.5, width=80)
        ctk.CTkLabel(row, text="초 간격", font=self._font(11), text_color=COLORS["text_muted"]).pack(side="left", padx=(5, 12))
        ctk.CTkCheckBox(
            row,
            text="랜덤",
            variable=self._bool_var("repeat_delay_random", False),
            font=self._font(11),
            text_color=COLORS["text_secondary"],
            fg_color=COLORS["accent_blue"],
            hover_color=COLORS["hover_blue"],
        ).pack(side="left", padx=(0, 6))
        self._entry(row, "repeat_delay_random_range", 0.3, width=70)

        wait_row = self._row(card, "실행 후")
        self._entry(wait_row, "wait_after", 0.5, width=90)
        ctk.CTkLabel(wait_row, text="초 대기", font=self._font(11), text_color=COLORS["text_muted"]).pack(side="left", padx=(5, 12))
        ctk.CTkCheckBox(
            wait_row,
            text="랜덤",
            variable=self._bool_var("wait_random", False),
            font=self._font(11),
            text_color=COLORS["text_secondary"],
            fg_color=COLORS["accent_blue"],
            hover_color=COLORS["hover_blue"],
        ).pack(side="left", padx=(0, 6))
        self._entry(wait_row, "wait_random_range", 0.3, width=70)

    def _choose_image(self) -> None:
        templates_dir = DATA_DIR / "templates"
        templates_dir.mkdir(parents=True, exist_ok=True)
        path = filedialog.askopenfilename(
            title="전용액션 이미지 선택",
            initialdir=str(templates_dir),
            filetypes=[("이미지 파일", "*.png *.jpg *.jpeg *.bmp")],
        )
        if not path:
            return
        self._image_path = self._editor._copy_image_to_templates(path, prefix="monitor_action")
        if self._image_label is not None:
            self._image_label.configure(
                text=truncate_ui_text(Path(self._image_path).name, 34),
                text_color=COLORS["accent_text"],
            )

    def _on_confidence(self, value) -> None:
        self._confidence = MonitoringModeEditor._safe_confidence(value)
        if self._confidence_label is not None:
            self._confidence_label.configure(text=f"{int(self._confidence * 100)}%")

    def _region_text(self) -> str:
        region = MonitoringModeEditor._normalize_search_region_value(self._search_region)
        if not region:
            return "전체 화면"
        x1, y1, x2, y2 = region
        source = self._editor._region_source_name(region)
        return f"{source} ({x1}, {y1}) ~ ({x2}, {y2})"

    def _refresh_region_label(self) -> None:
        if self._region_label is not None:
            self._region_label.configure(
                text=self._region_text(),
                text_color=COLORS["accent_text"] if self._search_region else COLORS["text_secondary"],
            )

    def _clear_region(self) -> None:
        self._search_region = None
        self._refresh_region_label()

    def _show_region_options(self) -> None:
        dialog = ctk.CTkToplevel(self)
        dialog.title("전용액션 검색범위")
        dialog.geometry("500x350")
        dialog.resizable(False, False)
        dialog.configure(fg_color=COLORS["bg_content"])
        dialog.transient(self)
        dialog.grab_set()

        main = ctk.CTkFrame(dialog, fg_color="transparent")
        main.pack(fill="both", expand=True, padx=16, pady=14)
        ctk.CTkLabel(
            main,
            text="검색범위 선택",
            font=self._font(17, "bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor="w", pady=(0, 4))
        ctk.CTkLabel(
            main,
            text="A/B영역은 일반 이미지 액션과 같은 공용 범위입니다. 자유영역은 이 전용액션에만 적용됩니다.",
            font=self._font(12),
            text_color=COLORS["text_secondary"],
            wraplength=460,
            justify="left",
        ).pack(anchor="w", pady=(0, 12))

        def close_then(callback):
            try:
                dialog.grab_release()
            except tk.TclError:
                pass
            dialog.destroy()
            callback()

        def preset_row(slot: str, label: str, color: str):
            saved = self._editor._saved_search_region(slot)
            row = ctk.CTkFrame(
                main,
                fg_color=COLORS["bg_glass"],
                corner_radius=IOS_METRICS["card_radius_compact"],
                border_width=IOS_METRICS["card_border_width"],
                border_color=color if saved else COLORS["border"],
            )
            row.pack(fill="x", pady=5)
            text = ctk.CTkFrame(row, fg_color="transparent")
            text.pack(side="left", fill="x", expand=True, padx=12, pady=9)
            ctk.CTkLabel(text, text=label, font=self._font(13, "bold"), text_color=color).pack(anchor="w")
            ctk.CTkLabel(
                text,
                text=self._editor._region_label_text(saved),
                font=self._font(11),
                text_color=COLORS["text_secondary"] if saved else COLORS["text_muted"],
            ).pack(anchor="w", pady=(2, 0))
            if saved:
                ctk.CTkButton(
                    row,
                    text="적용",
                    width=62,
                    height=30,
                    font=self._font(11, "bold"),
                    fg_color=color,
                    hover_color=COLORS["accent_hover"],
                    command=lambda r=saved: close_then(lambda: self._apply_region(r)),
                ).pack(side="right", padx=(0, 8))
            ctk.CTkButton(
                row,
                text="설정",
                width=62,
                height=30,
                font=self._font(11, "bold"),
                fg_color=COLORS["bg_elevated"],
                hover_color=COLORS["bg_card_hover"],
                command=lambda s=slot: close_then(lambda: self._capture_region(preset_slot=s)),
            ).pack(side="right", padx=(0, 8))

        preset_row("a", "A영역", COLORS["accent_blue"])
        preset_row("b", "B영역", COLORS["accent_orange"])
        ctk.CTkButton(
            main,
            text="자유영역 선택",
            height=34,
            font=self._font(12, "bold"),
            fg_color=COLORS["search_radius_purple"],
            hover_color=COLORS["search_radius_purple_hover"],
            command=lambda: close_then(lambda: self._capture_region(preset_slot=None)),
        ).pack(fill="x", pady=(8, 4))
        ctk.CTkButton(
            main,
            text="닫기",
            width=86,
            height=32,
            font=self._font(12, "bold"),
            fg_color=COLORS["bg_elevated"],
            hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_primary"],
            command=dialog.destroy,
        ).pack(anchor="e", pady=(8, 0))

    def _apply_region(self, region) -> None:
        self._search_region = MonitoringModeEditor._normalize_search_region_value(region)
        self._refresh_region_label()

    def _capture_region(self, preset_slot: str | None) -> None:
        from .analyzer_view import ScreenRegionSelector

        existing_region = self._editor._saved_search_region(preset_slot) if preset_slot else self._search_region
        self.withdraw()
        self._editor.withdraw()

        def on_region_select(x1, y1, x2, y2):
            region = MonitoringModeEditor._normalize_search_region_value([x1, y1, x2, y2])
            if region is not None:
                if preset_slot:
                    self._editor._save_search_region_preset(preset_slot, region)
                self._search_region = region
                self._refresh_region_label()
            self._editor.deiconify()
            self.deiconify()
            self.grab_set()
            self.focus_force()

        def on_cancel():
            self._editor.deiconify()
            self.deiconify()
            self.grab_set()
            self.focus_force()

        ScreenRegionSelector(self._editor._owner, on_region_select, on_cancel, existing_region=existing_region)

    @staticmethod
    def _int_value(value, default=0) -> int:
        try:
            return int(float(str(value).strip()))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _float_value(value, default=0.0) -> float:
        try:
            return float(str(value).strip())
        except (TypeError, ValueError):
            return default

    def _save_common_options(self, action: dict, action_type: str) -> None:
        for key, default in (
            ("repeat_count", 1),
            ("repeat_delay", 0.5),
            ("repeat_delay_random_range", 0.3),
            ("wait_after", 0.5),
            ("wait_random_range", 0.3),
            ("typing_delay", 0.1),
            ("typing_delay_range", 0.05),
            ("click_until_image_disappears_delay", 0.5),
        ):
            var = self._field_vars.get(key)
            if var is None:
                continue
            if key == "repeat_count":
                action[key] = max(1, self._int_value(var.get(), default))
            else:
                action[key] = max(0.0, self._float_value(var.get(), default))

        for key in (
            "repeat_delay_random",
            "wait_random",
        ):
            var = self._field_vars.get(key)
            if var is not None:
                action[key] = bool(var.get())

        if action_type == "텍스트 입력":
            var = self._field_vars.get("typing_random")
            if var is not None:
                action["typing_random"] = bool(var.get())

        if action_type in ("이미지 클릭", "마우스 클릭"):
            var = self._field_vars.get("alternate_mouse_route")
            if var is not None:
                action["alternate_mouse_route"] = bool(var.get())

        if action_type == "이미지 클릭":
            for key in (
                "verify_image_color",
                "verify_image_brightness",
                "click_until_image_disappears",
            ):
                var = self._field_vars.get(key)
                if var is not None:
                    action[key] = bool(var.get())

    def _save(self) -> None:
        action_type = self._field_vars["type"].get()
        action: dict = {"type": action_type}

        if action_type == "이미지 클릭":
            if not self._image_path:
                messagebox.showerror("설정 필요", "이미지 클릭 액션에는 이미지가 필요합니다.", parent=self)
                return
            action["image"] = self._image_path
            action["click_type"] = self._field_vars["click_type"].get()
            action["confidence"] = self._confidence
            action["search_region"] = copy.deepcopy(self._search_region)
        elif action_type == "마우스 클릭":
            action["x"] = self._int_value(self._field_vars["x"].get(), 0)
            action["y"] = self._int_value(self._field_vars["y"].get(), 0)
            action["click_type"] = self._field_vars["click_type"].get()
        elif action_type == "키 입력":
            keys = self._current_key_list()
            if not keys:
                messagebox.showerror("설정 필요", "키 입력 액션에는 키가 필요합니다.", parent=self)
                return
            action["keys"] = keys
            action["key_events"] = [dict(event) for event in self._key_events if isinstance(event, dict)]
        elif action_type == "텍스트 입력":
            text = self._text_box.get("1.0", "end").rstrip("\n") if self._text_box is not None else ""
            if not text:
                messagebox.showerror("설정 필요", "텍스트 입력 액션에는 입력문이 필요합니다.", parent=self)
                return
            action["text"] = text
        elif action_type == "스크롤":
            action["amount"] = self._int_value(self._field_vars["amount"].get(), 0)
        elif action_type == "드래그":
            for key in ("from_x", "from_y", "to_x", "to_y"):
                action[key] = self._int_value(self._field_vars[key].get(), 0)

        self._save_common_options(action, action_type)
        self._on_save(action)
        self.destroy()


class MonitoringModeEditor(ctk.CTkToplevel):
    """Simplified monitoring mode setup dialog."""

    def __init__(self, owner, rule, plan_rules, on_save: Callable[[], bool] | None = None):
        super().__init__(owner)
        self._owner = owner
        self._rule = rule
        self._plan_rules = plan_rules
        self._on_save = on_save
        self.was_saved = False

        self._enabled_var: ctk.BooleanVar | None = None
        self._monitor_confidence: float = 0.8
        self._route_watches: list[dict] = []
        self._routes_frame = None
        self._route_count_label = None
        self._save_status_label = None
        self._action_options: list[tuple[str, int]] = []
        self._font_cache: dict[tuple[int, str], ctk.CTkFont] = {}
        self._expanded_route_actions: set[int] = set()

        self._setup_dialog()
        self._init_data()
        self._build_ui()

    def _font(self, size: int, weight: str | None = None):
        key = (size, weight or "")
        cached = self._font_cache.get(key)
        if cached is None:
            kwargs = {"family": IOS_FONTS["family"], "size": size}
            if weight:
                kwargs["weight"] = weight
            cached = ctk.CTkFont(**kwargs)
            self._font_cache[key] = cached
        return cached

    def _setup_dialog(self) -> None:
        self.title("모니터링 모드 설정")
        self.geometry("980x920")
        self.minsize(900, 820)
        self.resizable(True, True)
        self.configure(fg_color=COLORS["bg_dark"])
        self.transient(self._owner)
        self.grab_set()

        self.update_idletasks()
        x = max(0, (self.winfo_screenwidth() - 980) // 2)
        y = max(0, (self.winfo_screenheight() - 920) // 2)
        self.geometry(f"+{x}+{y}")

    def _init_data(self) -> None:
        rule = self._rule
        watches = list(getattr(rule, "monitoring_watches", []) or [])
        self._action_options = self._build_action_options()
        confidence = getattr(rule, "confidence", 0.8)
        try:
            self._monitor_confidence = min(1.0, max(0.3, float(confidence or 0.8)))
        except (TypeError, ValueError):
            self._monitor_confidence = 0.8

        self._route_watches = []
        for watch in watches:
            if not isinstance(watch, dict):
                continue
            goto_index = self._watch_goto_index(watch)
            if goto_index < 0:
                continue
            self._route_watches.append(
                {
                    "image": watch.get("image") or watch.get("image_path"),
                    "search_region": copy.deepcopy(watch.get("search_region")),
                    "confidence": self._safe_confidence(watch.get("confidence", self._monitor_confidence)),
                    "goto_index": goto_index,
                    "jump_enabled": bool(watch.get("jump_enabled", True)),
                    "monitor_actions": copy.deepcopy(watch.get("monitor_actions", []) or []),
                    "condition_image": watch.get("condition_image"),
                    "condition_search_region": copy.deepcopy(watch.get("condition_search_region")),
                    "condition_confidence": self._safe_confidence(watch.get("condition_confidence", 0.8)),
                    "condition_jump_when_visible": bool(watch.get("condition_jump_when_visible", False)),
                    "condition_verify_image_color": bool(watch.get("condition_verify_image_color", False)),
                    "condition_verify_image_brightness": bool(watch.get("condition_verify_image_brightness", False)),
                }
            )

    def _build_action_options(self) -> list[tuple[str, int]]:
        options = [("액션 선택", -1)]
        for idx, action in enumerate(self._plan_rules):
            action_type = ACTION_NAMES_SHORT.get(getattr(action, "action_type", ""), getattr(action, "action_type", "") or "동작")
            desc = getattr(action, "description", "") or ""
            desc_text = f" - {truncate_ui_text(desc, 22)}" if desc else ""
            disabled_text = " (비활성)" if not getattr(action, "enabled", True) else ""
            child_text = f" +하위{len(action.children)}" if getattr(action, "children", None) else ""
            options.append((f"{idx + 1}. {action_type}{child_text}{disabled_text}{desc_text}", idx))
        return options

    @staticmethod
    def _watch_goto_index(watch: dict) -> int:
        try:
            return int(watch.get("goto_index", -1))
        except (TypeError, ValueError):
            return -1

    @staticmethod
    def _safe_confidence(value, default: float = 0.8) -> float:
        try:
            return min(1.0, max(0.3, float(value)))
        except (TypeError, ValueError):
            return default

    def _build_ui(self) -> None:
        root = ctk.CTkScrollableFrame(self, fg_color="transparent")
        root.pack(fill="both", expand=True, padx=18, pady=16)

        header = ctk.CTkFrame(root, fg_color="transparent")
        header.pack(fill="x", pady=(0, 12))

        title_box = ctk.CTkFrame(header, fg_color="transparent")
        title_box.pack(side="left", fill="x", expand=True)

        ctk.CTkLabel(
            title_box,
            text="모니터링 모드",
            font=self._font(20, "bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor="w")
        ctk.CTkLabel(
            title_box,
            text="최종이미지를 기다리다가 등록한 이동 이미지가 먼저 보이면 전용액션을 실행한 뒤 지정 액션으로 점프하고 모니터링을 끝냅니다.",
            font=self._font(12),
            text_color=COLORS["text_secondary"],
            wraplength=800,
            justify="left",
        ).pack(anchor="w", pady=(3, 0))

        self._enabled_var = ctk.BooleanVar(value=bool(getattr(self._rule, "is_monitoring_mode", False)))
        ctk.CTkCheckBox(
            header,
            text="사용",
            variable=self._enabled_var,
            font=self._font(14, "bold"),
            text_color=COLORS["success_text"],
            fg_color=COLORS["success"],
            hover_color=COLORS["green_hover"],
        ).pack(side="right", padx=(12, 0))

        flow = ctk.CTkFrame(root, fg_color=COLORS["bg_glass"], corner_radius=IOS_METRICS["control_radius"])
        flow.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(
            flow,
            text="1. 최종이미지 대기   →   2. 이동 이미지 발견   →   3. 전용액션 실행 후 지정 액션으로 점프",
            font=self._font(13, "bold"),
            text_color=COLORS["accent_text"],
            wraplength=880,
            justify="left",
        ).pack(anchor="w", padx=14, pady=10)

        self._build_routes_card(root)
        self._build_bottom_buttons()

    def _build_routes_card(self, parent) -> None:
        card = ctk.CTkFrame(parent, fg_color=COLORS["bg_card"], corner_radius=IOS_METRICS["control_radius"])
        card.pack(fill="x", pady=(0, 12))

        header = ctk.CTkFrame(card, fg_color="transparent")
        header.pack(fill="x", padx=14, pady=(12, 8))
        title = ctk.CTkFrame(header, fg_color="transparent")
        title.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(
            title,
            text="1. 모니터링 이미지 액션",
            font=self._font(15, "bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor="w")
        self._route_count_label = ctk.CTkLabel(
            title,
            text="등록 0개",
            font=self._font(11),
            text_color=COLORS["text_secondary"],
        )
        self._route_count_label.pack(anchor="w", pady=(2, 0))

        controls = ctk.CTkFrame(header, fg_color="transparent")
        controls.pack(side="right")
        ctk.CTkButton(
            controls,
            text="설정 복사",
            width=86,
            height=30,
            font=self._font(12, "bold"),
            fg_color=COLORS["bg_elevated"],
            hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_primary"],
            command=self._copy_monitoring_settings,
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            controls,
            text="붙여넣기",
            width=82,
            height=30,
            font=self._font(12, "bold"),
            fg_color=COLORS["search_radius_purple"],
            hover_color=COLORS["search_radius_purple_hover"],
            command=self._paste_monitoring_settings,
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            controls,
            text="+ 모니터링 이미지 추가",
            width=160,
            height=30,
            font=self._font(12, "bold"),
            fg_color=COLORS["accent_blue"],
            hover_color=COLORS["hover_blue"],
            command=self._add_route_watch,
        ).pack(side="left")

        ctk.CTkLabel(
            card,
            text="모니터링 이미지가 보이면 전용액션을 먼저 실행하고, 지정한 액션으로 점프하면서 모니터링은 종료됩니다.",
            font=self._font(11),
            text_color=COLORS["text_secondary"],
            anchor="w",
            wraplength=880,
            justify="left",
        ).pack(fill="x", padx=14, pady=(0, 8))

        self._routes_frame = ctk.CTkFrame(card, fg_color="transparent")
        self._routes_frame.pack(fill="x", padx=14, pady=(0, 12))
        self._refresh_route_list()

    def _build_bottom_buttons(self) -> None:
        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(fill="x", padx=18, pady=(0, 16))

        self._save_status_label = ctk.CTkLabel(
            bottom,
            text="",
            font=self._font(12, "bold"),
            text_color=COLORS["success_text"],
        )
        self._save_status_label.pack(side="left", padx=(4, 0))

        ctk.CTkButton(
            bottom,
            text="저장",
            width=120,
            height=40,
            font=self._font(14, "bold"),
            fg_color=COLORS["success"],
            hover_color=COLORS["green_hover"],
            command=self._save,
        ).pack(side="right", padx=(8, 0))
        ctk.CTkButton(
            bottom,
            text="닫기",
            width=120,
            height=40,
            font=self._font(14, "bold"),
            fg_color=COLORS["bg_card"],
            hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_secondary"],
            command=self.destroy,
        ).pack(side="right")

    @staticmethod
    def _normalize_search_region_value(region):
        if not isinstance(region, (list, tuple)) or len(region) != 4:
            return None
        try:
            x1, y1, x2, y2 = [int(v) for v in region]
        except (TypeError, ValueError):
            return None
        if x1 == x2 or y1 == y2:
            return None
        return [min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)]

    @staticmethod
    def _same_search_region(left, right) -> bool:
        return (
            MonitoringModeEditor._normalize_search_region_value(left)
            == MonitoringModeEditor._normalize_search_region_value(right)
        )

    def _saved_search_region(self, slot: str):
        key = f"image_search_region_{slot}"
        try:
            return self._normalize_search_region_value(getattr(get_config().player, key, None))
        except Exception:
            return None

    def _save_search_region_preset(self, slot: str, region) -> bool:
        normalized = self._normalize_search_region_value(region)
        if normalized is None:
            return False
        key = f"image_search_region_{slot}"
        try:
            setattr(get_config().player, key, normalized)
            if not save_config():
                logger.warning("검색범위 %s영역 저장 실패", slot.upper())
                return False
            return True
        except Exception as exc:
            logger.warning("검색범위 %s영역 저장 오류: %s", slot.upper(), exc)
            return False

    def _region_label_text(self, region) -> str:
        normalized = self._normalize_search_region_value(region)
        if not normalized:
            return "미설정"
        x1, y1, x2, y2 = normalized
        return f"({x1}, {y1}) ~ ({x2}, {y2})  {x2 - x1}x{y2 - y1}"

    def _region_source_name(self, region) -> str:
        normalized = self._normalize_search_region_value(region)
        if not normalized:
            return "전체"
        if self._same_search_region(normalized, self._saved_search_region("a")):
            return "A영역"
        if self._same_search_region(normalized, self._saved_search_region("b")):
            return "B영역"
        return "자유영역"

    def _current_target_region(self, target: str, idx: int | None = None):
        if target == "route" and idx is not None and 0 <= idx < len(self._route_watches):
            return self._normalize_search_region_value(self._route_watches[idx].get("search_region"))
        if target == "condition" and idx is not None and 0 <= idx < len(self._route_watches):
            return self._normalize_search_region_value(self._route_watches[idx].get("condition_search_region"))
        return None

    def _apply_region_to_target(self, target: str, region, source_label: str = "검색범위", idx: int | None = None) -> bool:
        normalized = self._normalize_search_region_value(region)
        if normalized is None:
            return False
        if target == "route" and idx is not None and 0 <= idx < len(self._route_watches):
            self._route_watches[idx]["search_region"] = normalized
            logger.info("[모니터링] R%s %s 적용: %s", idx + 1, source_label, normalized)
            self._refresh_route_list()
            return True
        if target == "condition" and idx is not None and 0 <= idx < len(self._route_watches):
            self._route_watches[idx]["condition_search_region"] = normalized
            logger.info("[모니터링] R%s 조건 %s 적용: %s", idx + 1, source_label, normalized)
            self._refresh_route_list()
            return True
        return False

    def _copy_image_to_templates(self, source: str, prefix: str) -> str:
        source_path = Path(source)
        templates_dir = DATA_DIR / "templates"
        templates_dir.mkdir(parents=True, exist_ok=True)
        try:
            if source_path.parent.resolve() == templates_dir.resolve():
                return str(source_path)
        except OSError:
            pass

        dest = templates_dir / source_path.name
        if dest.exists() and source_path.resolve() != dest.resolve():
            stem = source_path.stem
            suffix = source_path.suffix or ".png"
            counter = 1
            while dest.exists():
                dest = templates_dir / f"{prefix}_{stem}_{counter}{suffix}"
                counter += 1
        shutil.copy2(source_path, dest)
        return str(dest)

    @staticmethod
    def _image_quality_warning(path: str | None) -> str:
        if not path or not Path(path).exists():
            return ""
        try:
            img_arr = np.fromfile(str(path), np.uint8)
            img = cv2.imdecode(img_arr, cv2.IMREAD_UNCHANGED)
            if img is None:
                return "이미지를 읽을 수 없습니다."
            if img.ndim == 2:
                gray = img
            elif img.shape[2] == 4:
                alpha = img[:, :, 3]
                visible = alpha > 12
                if not np.any(visible):
                    return "이미지가 거의 투명합니다. 다시 캡처하세요."
                gray = cv2.cvtColor(img[:, :, :3], cv2.COLOR_BGR2GRAY)[visible]
            else:
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

            mean = float(np.mean(gray))
            std = float(np.std(gray))
            if mean < 16 and std < 6:
                return "이미지가 거의 검정 단색입니다. 다시 캡처하세요."
            if std < 3:
                return "이미지가 거의 단색입니다. 조건 이미지로 부적합할 수 있습니다."
        except Exception as exc:
            logger.warning("condition image quality check failed: %s - %s", path, exc)
        return ""

    def _schedule_thumbnail(self, label, path: str, size=(56, 56)) -> None:
        source = str(path or "")
        label._thumb_source = (source, size)
        if not source or not Path(source).exists():
            label.configure(image=None, text="IMG", text_color=COLORS["text_muted"])
            return

        cache_source = f"{source}::monitor_thumb_v2"
        cached = get_cached_thumbnail(cache_source, size)
        if cached is not None:
            label.configure(image=cached, text="")
            label._thumb_img = cached
            return

        label.configure(image=None, text="IMG", text_color=COLORS["text_muted"])

        def load_thumbnail():
            try:
                img_arr = np.fromfile(source, np.uint8)
                img = cv2.imdecode(img_arr, cv2.IMREAD_UNCHANGED)
                if img is None:
                    raise ValueError("image decode failed")
                if img.ndim == 2:
                    img_rgb = cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)
                elif img.shape[2] == 4:
                    bgr = img[:, :, :3].astype(np.float32)
                    alpha = img[:, :, 3:4].astype(np.float32) / 255.0
                    bg_rgb = tuple(int(COLORS["bg_card"].lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
                    bg_bgr = np.array([bg_rgb[2], bg_rgb[1], bg_rgb[0]], dtype=np.float32)
                    composited = (bgr * alpha) + (bg_bgr * (1.0 - alpha))
                    img_rgb = cv2.cvtColor(composited.astype(np.uint8), cv2.COLOR_BGR2RGB)
                else:
                    img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                h, w = img_rgb.shape[:2]
                scale = min(size[0] / w, size[1] / h)
                new_w, new_h = max(1, int(w * scale)), max(1, int(h * scale))
                resized = cv2.resize(img_rgb, (new_w, new_h))
                pil_img = Image.fromarray(resized)

                def apply_thumbnail():
                    try:
                        if not label.winfo_exists():
                            return
                        if getattr(label, "_thumb_source", None) != (source, size):
                            return
                        ctk_img = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=(new_w, new_h))
                        set_cached_thumbnail(cache_source, size, ctk_img)
                        label.configure(image=ctk_img, text="")
                        label._thumb_img = ctk_img
                    except (tk.TclError, RuntimeError):
                        pass

                self.after(0, apply_thumbnail)
            except Exception as exc:
                logger.warning("monitoring thumbnail load failed: %s - %s", source, exc)

        submit_thumbnail_task(load_thumbnail)

    def _show_region_options(self, target: str, idx: int | None = None) -> None:
        if target in {"route", "condition"} and (idx is None or not 0 <= idx < len(self._route_watches)):
            return

        title_text = "조건 이미지 검색범위" if target == "condition" else "모니터링 이미지 액션 검색범위"
        dialog = ctk.CTkToplevel(self)
        dialog.title("검색범위 선택")
        dialog.geometry("540x390")
        dialog.resizable(False, False)
        dialog.configure(fg_color=COLORS["bg_content"])
        dialog.transient(self)
        dialog.grab_set()

        dialog.update_idletasks()
        x = self.winfo_x() + max(0, (self.winfo_width() - 540) // 2)
        y = self.winfo_y() + max(0, (self.winfo_height() - 390) // 2)
        dialog.geometry(f"+{x}+{y}")

        main = ctk.CTkFrame(dialog, fg_color="transparent")
        main.pack(fill="both", expand=True, padx=18, pady=16)

        ctk.CTkLabel(
            main,
            text=title_text,
            font=self._font(17, "bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor="w", pady=(0, 4))
        ctk.CTkLabel(
            main,
            text="A/B 영역은 일반 이미지 액션과 같은 공용 프리셋을 쓰고, 자유영역은 현재 모니터링 항목에만 적용됩니다.",
            font=self._font(12),
            text_color=COLORS["text_secondary"],
            wraplength=500,
            justify="left",
        ).pack(anchor="w", pady=(0, 12))

        def close_then(callback):
            try:
                dialog.grab_release()
            except tk.TclError:
                pass
            try:
                dialog.destroy()
            except tk.TclError:
                pass
            callback()

        def build_preset_row(slot: str, label: str, color: str) -> None:
            saved_region = self._saved_search_region(slot)
            row = ctk.CTkFrame(
                main,
                fg_color=COLORS["bg_glass"],
                corner_radius=IOS_METRICS["card_radius_compact"],
                border_width=IOS_METRICS["card_border_width"],
                border_color=color if saved_region else COLORS["border"],
            )
            row.pack(fill="x", pady=5)

            text_col = ctk.CTkFrame(row, fg_color="transparent")
            text_col.pack(side="left", fill="x", expand=True, padx=12, pady=10)
            ctk.CTkLabel(
                text_col,
                text=label,
                font=self._font(14, "bold"),
                text_color=color,
            ).pack(anchor="w")
            ctk.CTkLabel(
                text_col,
                text=self._region_label_text(saved_region),
                font=self._font(11),
                text_color=COLORS["text_secondary"] if saved_region else COLORS["text_muted"],
            ).pack(anchor="w", pady=(3, 0))

            if saved_region:
                ctk.CTkButton(
                    row,
                    text="적용",
                    width=70,
                    height=32,
                    fg_color=color,
                    hover_color=COLORS["accent_hover"],
                    text_color=COLORS["text_on_accent"],
                    font=self._font(12, "bold"),
                    corner_radius=IOS_METRICS["pill_radius"],
                    command=lambda r=saved_region, source=label: close_then(
                        lambda: self._apply_region_to_target(target, r, source, idx)
                    ),
                ).pack(side="right", padx=(0, 8))
                set_text = "다시설정"
            else:
                set_text = "설정"

            ctk.CTkButton(
                row,
                text=set_text,
                width=82,
                height=32,
                fg_color=COLORS["bg_elevated"],
                hover_color=COLORS["bg_card_hover"],
                text_color=COLORS["text_primary"],
                font=self._font(12, "bold"),
                corner_radius=IOS_METRICS["pill_radius"],
                command=lambda s=slot, source=label: close_then(
                    lambda: self._start_region_selection(target, idx, preset_slot=s, source_label=source)
                ),
            ).pack(side="right", padx=(0, 8))

        build_preset_row("a", "A영역", COLORS["accent_blue"])
        build_preset_row("b", "B영역", COLORS["accent_orange"])

        free_row = ctk.CTkFrame(
            main,
            fg_color=COLORS["bg_glass"],
            corner_radius=IOS_METRICS["card_radius_compact"],
            border_width=IOS_METRICS["card_border_width"],
            border_color=COLORS["search_radius_purple"],
        )
        free_row.pack(fill="x", pady=5)

        free_text = ctk.CTkFrame(free_row, fg_color="transparent")
        free_text.pack(side="left", fill="x", expand=True, padx=12, pady=10)
        ctk.CTkLabel(
            free_text,
            text="자유영역",
            font=self._font(14, "bold"),
            text_color=COLORS["search_radius_purple"],
        ).pack(anchor="w")
        ctk.CTkLabel(
            free_text,
            text=self._region_label_text(self._current_target_region(target, idx)),
            font=self._font(11),
            text_color=COLORS["text_secondary"],
        ).pack(anchor="w", pady=(3, 0))

        ctk.CTkButton(
            free_row,
            text="선택",
            width=82,
            height=32,
            fg_color=COLORS["search_radius_purple"],
            hover_color=COLORS["search_radius_purple_hover"],
            text_color=COLORS["text_on_accent"],
            font=self._font(12, "bold"),
            corner_radius=IOS_METRICS["pill_radius"],
            command=lambda: close_then(
                lambda: self._start_region_selection(target, idx, preset_slot=None, source_label="자유영역")
            ),
        ).pack(side="right", padx=(0, 8))

        ctk.CTkButton(
            main,
            text="닫기",
            width=90,
            height=34,
            fg_color=COLORS["bg_elevated"],
            hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_primary"],
            font=self._font(12, "bold"),
            corner_radius=IOS_METRICS["pill_radius"],
            command=dialog.destroy,
        ).pack(anchor="e", pady=(10, 0))

    def _start_region_selection(
        self,
        target: str,
        idx: int | None = None,
        preset_slot: str | None = None,
        source_label: str = "자유영역",
    ) -> None:
        if target in {"route", "condition"} and (idx is None or not 0 <= idx < len(self._route_watches)):
            return
        self.withdraw()
        self.after(
            100,
            lambda: self._open_region_selector_for_target(
                target,
                idx,
                preset_slot=preset_slot,
                source_label=source_label,
            ),
        )

    def _open_region_selector_for_target(
        self,
        target: str,
        idx: int | None = None,
        preset_slot: str | None = None,
        source_label: str = "자유영역",
    ) -> None:
        from .analyzer_view import ScreenRegionSelector

        existing_region = self._saved_search_region(preset_slot) if preset_slot else self._current_target_region(target, idx)
        if existing_region is None:
            existing_region = self._current_target_region(target, idx)

        def on_region_select(x1, y1, x2, y2):
            region = self._normalize_search_region_value([x1, y1, x2, y2])
            if region is not None:
                if preset_slot:
                    self._save_search_region_preset(preset_slot, region)
                self._apply_region_to_target(target, region, source_label, idx)
            self.deiconify()
            self.grab_set()
            self.focus_force()

        def on_cancel():
            self.deiconify()
            self.grab_set()
            self.focus_force()

        ScreenRegionSelector(self._owner, on_region_select, on_cancel, existing_region=existing_region)

    def _action_label_for_index(self, goto_index: int) -> str:
        for label, idx in self._action_options:
            if idx == goto_index:
                return label
        return self._action_options[0][0] if self._action_options else "액션 선택"

    def _action_index_for_label(self, label: str) -> int:
        for option_label, idx in self._action_options:
            if option_label == label:
                return idx
        return -1

    def _default_route_goto_index(self) -> int:
        for _, idx in self._action_options:
            if idx >= 0:
                return idx
        return -1

    def _add_route_watch(self) -> None:
        self._route_watches.append(
            {
                "image": None,
                "search_region": None,
                "confidence": self._monitor_confidence,
                "goto_index": self._default_route_goto_index(),
                "jump_enabled": True,
                "monitor_actions": [],
                "condition_image": None,
                "condition_search_region": None,
                "condition_confidence": 0.8,
                "condition_jump_when_visible": False,
                "condition_verify_image_color": False,
                "condition_verify_image_brightness": False,
            }
        )
        self._refresh_route_list()

    def _set_status_text(self, text: str, color: str | None = None) -> None:
        if self._save_status_label is not None:
            self._save_status_label.configure(text=text, text_color=color or COLORS["success_text"])

    def _route_clipboard_snapshot(self) -> dict:
        enabled = bool(self._enabled_var.get()) if self._enabled_var is not None else False
        return {
            "version": 1,
            "enabled": enabled,
            "route_watches": copy.deepcopy(self._route_watches),
        }

    def _copy_monitoring_settings(self) -> None:
        global _MONITORING_SETTINGS_CLIPBOARD
        _MONITORING_SETTINGS_CLIPBOARD = self._route_clipboard_snapshot()
        route_count = len(_MONITORING_SETTINGS_CLIPBOARD.get("route_watches") or [])
        self._set_status_text(f"모니터링 설정 복사됨 ({route_count}개)")

    def _normalize_pasted_route(self, route: dict) -> dict:
        goto_index = self._watch_goto_index(route)
        if goto_index < 0 or goto_index >= len(self._plan_rules):
            goto_index = self._default_route_goto_index()
        return {
            "image": route.get("image") or route.get("image_path"),
            "search_region": copy.deepcopy(route.get("search_region")),
            "confidence": self._safe_confidence(route.get("confidence", self._monitor_confidence)),
            "goto_index": goto_index,
            "jump_enabled": bool(route.get("jump_enabled", True)),
            "monitor_actions": copy.deepcopy(route.get("monitor_actions", []) or []),
            "condition_image": route.get("condition_image"),
            "condition_search_region": copy.deepcopy(route.get("condition_search_region")),
            "condition_confidence": self._safe_confidence(route.get("condition_confidence", 0.8)),
            "condition_jump_when_visible": bool(route.get("condition_jump_when_visible", False)),
            "condition_verify_image_color": bool(route.get("condition_verify_image_color", False)),
            "condition_verify_image_brightness": bool(route.get("condition_verify_image_brightness", False)),
        }

    def _paste_monitoring_settings(self) -> None:
        if not _MONITORING_SETTINGS_CLIPBOARD:
            messagebox.showinfo("붙여넣기", "복사된 모니터링 설정이 없습니다.", parent=self)
            return
        if self._route_watches and not messagebox.askyesno(
            "모니터링 설정 붙여넣기",
            "현재 모니터링 이미지 액션을 복사한 설정으로 교체할까요?",
            parent=self,
        ):
            return
        routes = [
            self._normalize_pasted_route(route)
            for route in (_MONITORING_SETTINGS_CLIPBOARD.get("route_watches") or [])
            if isinstance(route, dict)
        ]
        self._route_watches = routes
        if self._enabled_var is not None:
            self._enabled_var.set(bool(_MONITORING_SETTINGS_CLIPBOARD.get("enabled", bool(routes))))
        self._expanded_route_actions = set()
        self._refresh_route_list()
        self._set_status_text(f"모니터링 설정 붙여넣기 완료 ({len(routes)}개) - 저장을 눌러 적용")

    def _delete_route_watch(self, idx: int) -> None:
        if 0 <= idx < len(self._route_watches):
            self._route_watches.pop(idx)
            self._expanded_route_actions = {
                expanded if expanded < idx else expanded - 1
                for expanded in self._expanded_route_actions
                if expanded != idx
            }
            self._refresh_route_list()

    def _select_route_image(self, idx: int) -> None:
        if not 0 <= idx < len(self._route_watches):
            return
        templates_dir = DATA_DIR / "templates"
        templates_dir.mkdir(parents=True, exist_ok=True)
        path = filedialog.askopenfilename(
            title="이동 감지 이미지 선택",
            initialdir=str(templates_dir),
            filetypes=[("이미지 파일", "*.png *.jpg *.jpeg *.bmp")],
        )
        if not path:
            return
        self._route_watches[idx]["image"] = self._copy_image_to_templates(path, prefix="route")
        self._refresh_route_list()

    def _select_route_region(self, idx: int) -> None:
        if not 0 <= idx < len(self._route_watches):
            return
        self._show_region_options("route", idx)

    def _open_route_region_selector(self, idx: int) -> None:
        self._open_region_selector_for_target("route", idx)

    def _clear_route_region(self, idx: int) -> None:
        if 0 <= idx < len(self._route_watches):
            self._route_watches[idx]["search_region"] = None
            self._refresh_route_list()

    def _select_route_condition_image(self, idx: int) -> None:
        if not 0 <= idx < len(self._route_watches):
            return
        templates_dir = DATA_DIR / "templates"
        templates_dir.mkdir(parents=True, exist_ok=True)
        path = filedialog.askopenfilename(
            title="조건 이미지 선택",
            initialdir=str(templates_dir),
            filetypes=[("이미지 파일", "*.png *.jpg *.jpeg *.bmp")],
        )
        if not path:
            return
        self._route_watches[idx]["condition_image"] = self._copy_image_to_templates(path, prefix="condition")
        self._refresh_route_list()

    def _open_route_condition_settings(self, idx: int) -> None:
        if not 0 <= idx < len(self._route_watches):
            return

        dialog = ctk.CTkToplevel(self)
        dialog.title("조건 이미지 설정")
        dialog.geometry("560x540")
        dialog.minsize(520, 500)
        dialog.configure(fg_color=COLORS["bg_content"])
        dialog.transient(self)
        dialog.grab_set()

        dialog.update_idletasks()
        x = self.winfo_x() + max(0, (self.winfo_width() - 560) // 2)
        y = self.winfo_y() + max(0, (self.winfo_height() - 540) // 2)
        dialog.geometry(f"+{x}+{y}")

        route = self._route_watches[idx]
        image_var = tk.StringVar()
        region_var = tk.StringVar()
        confidence_var = tk.StringVar()
        warning_var = tk.StringVar()
        jump_mode_var = tk.StringVar(
            value="보일 때 점프" if route.get("condition_jump_when_visible", False) else "안 보일 때 점프"
        )
        verify_color_var = tk.BooleanVar(value=bool(route.get("condition_verify_image_color", False)))
        verify_brightness_var = tk.BooleanVar(value=bool(route.get("condition_verify_image_brightness", False)))

        root = ctk.CTkFrame(dialog, fg_color="transparent")
        root.pack(fill="both", expand=True, padx=18, pady=16)

        ctk.CTkLabel(
            root,
            text=f"{idx + 1}번 조건 이미지",
            font=self._font(18, "bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor="w")
        ctk.CTkLabel(
            root,
            text="조건 이미지가 계속 보이면 전용액션 후 점프를 보류합니다. 이미지, 검색범위, 인식률을 여기서 설정하세요.",
            font=self._font(12),
            text_color=COLORS["text_secondary"],
            wraplength=500,
            justify="left",
        ).pack(anchor="w", pady=(4, 12))

        preview_card = ctk.CTkFrame(
            root,
            fg_color=COLORS["bg_card"],
            corner_radius=IOS_METRICS["control_radius"],
            border_width=IOS_METRICS["card_border_width"],
            border_color=COLORS["border"],
        )
        preview_card.pack(fill="x", pady=(0, 12))
        preview_inner = ctk.CTkFrame(preview_card, fg_color="transparent")
        preview_inner.pack(fill="x", padx=12, pady=12)

        preview = ctk.CTkLabel(
            preview_inner,
            text="조건",
            width=92,
            height=68,
            fg_color=COLORS["bg_elevated"],
            corner_radius=IOS_METRICS["control_radius_small"],
            text_color=COLORS["text_muted"],
        )
        preview.pack(side="left", padx=(0, 12))

        info_col = ctk.CTkFrame(preview_inner, fg_color="transparent")
        info_col.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(
            info_col,
            textvariable=image_var,
            font=self._font(13, "bold"),
            text_color=COLORS["text_primary"],
            anchor="w",
        ).pack(fill="x")
        ctk.CTkLabel(
            info_col,
            textvariable=region_var,
            font=self._font(11),
            text_color=COLORS["text_secondary"],
            anchor="w",
        ).pack(fill="x", pady=(4, 0))
        ctk.CTkLabel(
            info_col,
            textvariable=warning_var,
            font=self._font(11, "bold"),
            text_color=COLORS["error"],
            anchor="w",
            wraplength=400,
        ).pack(fill="x", pady=(4, 0))

        def refresh_dialog() -> None:
            current = self._route_watches[idx] if 0 <= idx < len(self._route_watches) else {}
            image_path = current.get("condition_image")
            image_var.set(Path(image_path).name if image_path else "조건 이미지 없음")
            region_var.set(f"검색범위: {self._region_label_text(current.get('condition_search_region'))}")
            confidence = self._safe_confidence(current.get("condition_confidence", 0.8))
            confidence_var.set(f"{int(confidence * 100)}%")
            warning_var.set(self._image_quality_warning(image_path))
            self._schedule_thumbnail(preview, image_path, size=(88, 64))

        def choose_image() -> None:
            self._select_route_condition_image(idx)
            refresh_dialog()

        def clear_condition() -> None:
            self._clear_route_condition_image(idx)
            refresh_dialog()

        def clear_region() -> None:
            if 0 <= idx < len(self._route_watches):
                self._route_watches[idx]["condition_search_region"] = None
                self._refresh_route_list()
                refresh_dialog()

        def open_region() -> None:
            try:
                dialog.grab_release()
                dialog.destroy()
            except tk.TclError:
                pass
            self._show_region_options("condition", idx)

        button_row = ctk.CTkFrame(root, fg_color="transparent")
        button_row.pack(fill="x", pady=(0, 12))
        self._small_button(button_row, "이미지 선택", COLORS["accent_blue"], COLORS["hover_blue"], choose_image, width=92).pack(side="left", padx=(0, 6))
        self._small_button(button_row, "검색범위", COLORS["bg_elevated"], COLORS["bg_card_hover"], open_region, width=82).pack(side="left", padx=(0, 6))
        self._small_button(button_row, "범위해제", COLORS["bg_elevated"], COLORS["bg_card_hover"], clear_region, width=82).pack(side="left", padx=(0, 6))
        self._small_button(button_row, "조건해제", COLORS["danger"], COLORS["danger_hover"], clear_condition, width=82).pack(side="left")

        confidence_row = ctk.CTkFrame(root, fg_color=COLORS["bg_card"], corner_radius=IOS_METRICS["control_radius_small"])
        confidence_row.pack(fill="x", pady=(0, 12))
        confidence_inner = ctk.CTkFrame(confidence_row, fg_color="transparent")
        confidence_inner.pack(fill="x", padx=12, pady=10)
        ctk.CTkLabel(
            confidence_inner,
            text="조건 인식률",
            width=90,
            anchor="w",
            font=self._font(12, "bold"),
            text_color=COLORS["text_primary"],
        ).pack(side="left", padx=(0, 8))
        ctk.CTkLabel(
            confidence_inner,
            textvariable=confidence_var,
            width=46,
            font=self._font(12, "bold"),
            text_color=COLORS["accent_text"],
        ).pack(side="left", padx=(0, 8))
        slider = ctk.CTkSlider(
            confidence_inner,
            from_=0.3,
            to=1.0,
            number_of_steps=70,
            width=260,
            command=lambda value: self._on_route_condition_confidence_changed(idx, value, confidence_var),
        )
        slider.set(self._safe_confidence(route.get("condition_confidence", 0.8)))
        slider.pack(side="left", fill="x", expand=True)

        mode_row = ctk.CTkFrame(root, fg_color=COLORS["bg_card"], corner_radius=IOS_METRICS["control_radius_small"])
        mode_row.pack(fill="x", pady=(0, 12))
        mode_inner = ctk.CTkFrame(mode_row, fg_color="transparent")
        mode_inner.pack(fill="x", padx=12, pady=10)
        ctk.CTkLabel(
            mode_inner,
            text="점프 조건",
            width=90,
            anchor="w",
            font=self._font(12, "bold"),
            text_color=COLORS["text_primary"],
        ).pack(side="left", padx=(0, 8))
        ctk.CTkSegmentedButton(
            mode_inner,
            values=["안 보일 때 점프", "보일 때 점프"],
            variable=jump_mode_var,
            command=lambda value: self._on_route_condition_jump_mode_changed(idx, value),
            fg_color=COLORS["bg_elevated"],
            selected_color=COLORS["accent_blue"],
            selected_hover_color=COLORS["hover_blue"],
            unselected_color=COLORS["bg_elevated"],
            unselected_hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_primary"],
            font=self._font(11, "bold"),
        ).pack(side="left", fill="x", expand=True)

        verify_row = ctk.CTkFrame(root, fg_color=COLORS["bg_card"], corner_radius=IOS_METRICS["control_radius_small"])
        verify_row.pack(fill="x", pady=(0, 12))
        verify_inner = ctk.CTkFrame(verify_row, fg_color="transparent")
        verify_inner.pack(fill="x", padx=12, pady=10)
        ctk.CTkLabel(
            verify_inner,
            text="추가 확인",
            width=90,
            anchor="w",
            font=self._font(12, "bold"),
            text_color=COLORS["text_primary"],
        ).pack(side="left", padx=(0, 8))

        def update_condition_verify_options() -> None:
            if 0 <= idx < len(self._route_watches):
                self._route_watches[idx]["condition_verify_image_color"] = bool(verify_color_var.get())
                self._route_watches[idx]["condition_verify_image_brightness"] = bool(verify_brightness_var.get())

        ctk.CTkCheckBox(
            verify_inner,
            text="색상 확인",
            variable=verify_color_var,
            command=update_condition_verify_options,
            font=self._font(12, "bold"),
            checkbox_width=20,
            checkbox_height=20,
        ).pack(side="left", padx=(0, 14))
        ctk.CTkCheckBox(
            verify_inner,
            text="밝기 확인",
            variable=verify_brightness_var,
            command=update_condition_verify_options,
            font=self._font(12, "bold"),
            checkbox_width=20,
            checkbox_height=20,
        ).pack(side="left")

        ctk.CTkButton(
            root,
            text="닫기",
            width=100,
            height=36,
            font=self._font(13, "bold"),
            fg_color=COLORS["bg_elevated"],
            hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_primary"],
            corner_radius=IOS_METRICS["pill_radius"],
            command=dialog.destroy,
        ).pack(anchor="e")

        refresh_dialog()

    def _clear_route_condition_image(self, idx: int) -> None:
        if 0 <= idx < len(self._route_watches):
            self._route_watches[idx]["condition_image"] = None
            self._route_watches[idx]["condition_search_region"] = None
            self._route_watches[idx]["condition_confidence"] = 0.8
            self._route_watches[idx]["condition_jump_when_visible"] = False
            self._route_watches[idx]["condition_verify_image_color"] = False
            self._route_watches[idx]["condition_verify_image_brightness"] = False
            self._refresh_route_list()

    def _clear_route_actions(self, idx: int) -> None:
        if not 0 <= idx < len(self._route_watches):
            return
        if not self._route_watches[idx].get("monitor_actions"):
            return
        if not messagebox.askyesno("전용 액션 삭제", f"{idx + 1}번 전용 액션을 모두 삭제할까요?", parent=self):
            return
        self._route_watches[idx]["monitor_actions"] = []
        self._expanded_route_actions.discard(idx)
        self._refresh_route_list()

    def _on_route_action_select(self, idx: int, label: str) -> None:
        if 0 <= idx < len(self._route_watches):
            self._route_watches[idx]["goto_index"] = self._action_index_for_label(label)

    def _on_route_jump_enabled_changed(self, idx: int, value: str) -> None:
        if 0 <= idx < len(self._route_watches):
            self._route_watches[idx]["jump_enabled"] = value == "활성"

    def _on_route_confidence_changed(self, idx: int, value) -> None:
        if 0 <= idx < len(self._route_watches):
            self._route_watches[idx]["confidence"] = self._safe_confidence(value)

    def _on_route_condition_confidence_changed(self, idx: int, value, label_var: tk.StringVar | None = None) -> None:
        if 0 <= idx < len(self._route_watches):
            confidence = self._safe_confidence(value)
            self._route_watches[idx]["condition_confidence"] = confidence
            if label_var is not None:
                label_var.set(f"{int(confidence * 100)}%")

    def _on_route_condition_jump_mode_changed(self, idx: int, value: str) -> None:
        if 0 <= idx < len(self._route_watches):
            self._route_watches[idx]["condition_jump_when_visible"] = value == "보일 때 점프"

    def _refresh_route_list(self) -> None:
        if self._route_count_label is not None:
            self._route_count_label.configure(text=f"등록 {len(self._route_watches)}개")
        if self._routes_frame is None:
            return
        for child in self._routes_frame.winfo_children():
            child.destroy()
        if not self._route_watches:
            ctk.CTkLabel(
                self._routes_frame,
                text="모니터링 이미지 액션을 추가하세요.",
                font=self._font(12),
                text_color=COLORS["text_muted"],
            ).pack(fill="x", pady=16)
            return

        for idx, route in enumerate(self._route_watches):
            self._build_route_row(idx, route)
            if idx < len(self._route_watches) - 1:
                self._build_route_separator()

    def _build_route_separator(self) -> None:
        separator = ctk.CTkFrame(
            self._routes_frame,
            height=2,
            fg_color=COLORS["accent"],
            corner_radius=1,
        )
        separator.pack(fill="x", padx=16, pady=(8, 10))
        separator.pack_propagate(False)

    def _build_route_row(self, idx: int, route: dict) -> None:
        row = ctk.CTkFrame(self._routes_frame, fg_color=COLORS["bg_glass"], corner_radius=IOS_METRICS["control_radius_small"])
        row.pack(fill="x", pady=4)
        inner = ctk.CTkFrame(row, fg_color="transparent")
        inner.pack(fill="x", padx=10, pady=8)

        image_path = route.get("image")
        image_name = Path(image_path).name if image_path else "이미지 없음"
        region = route.get("search_region")
        region_text = f"범위 {region[0]},{region[1]}~{region[2]},{region[3]}" if region and len(region) == 4 else "전체 화면"
        monitor_actions = route.get("monitor_actions", []) or []
        condition_image = route.get("condition_image")
        condition_warning = self._image_quality_warning(condition_image)
        condition_mode_text = "보이면 점프" if route.get("condition_jump_when_visible", False) else "안 보이면 점프"
        condition_verify_parts = []
        if route.get("condition_verify_image_color"):
            condition_verify_parts.append("색상")
        if route.get("condition_verify_image_brightness"):
            condition_verify_parts.append("밝기")
        condition_verify_text = f" ({'/'.join(condition_verify_parts)})" if condition_verify_parts else ""
        condition_text = "조건 이미지 재캡처 필요" if condition_warning else (
            f"{condition_mode_text}{condition_verify_text}: {Path(condition_image).name}" if condition_image else "조건 없음"
        )

        top = ctk.CTkFrame(inner, fg_color="transparent")
        top.pack(fill="x")
        ctk.CTkLabel(
            top,
            text=f"{idx + 1}",
            width=24,
            font=self._font(13, "bold"),
            text_color=COLORS["accent_text"],
        ).pack(side="left", padx=(0, 5))
        watch_thumb = ctk.CTkLabel(
            top,
            text="IMG",
            width=56,
            height=42,
            fg_color=COLORS["bg_card"],
            corner_radius=IOS_METRICS["control_radius_small"],
            text_color=COLORS["text_muted"],
        )
        watch_thumb.pack(side="left", padx=(0, 10))
        self._schedule_thumbnail(watch_thumb, image_path, size=(52, 38))
        ctk.CTkLabel(
            top,
            text=truncate_ui_text(image_name, 42),
            anchor="w",
            font=self._font(12, "bold"),
            text_color=COLORS["text_primary"] if image_path else COLORS["text_muted"],
        ).pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._small_button(top, "이미지", COLORS["accent_blue"], COLORS["hover_blue"], lambda i=idx: self._select_route_image(i), width=54).pack(side="left", padx=(0, 5))
        self._small_button(top, "범위", COLORS["bg_elevated"], COLORS["bg_card_hover"], lambda i=idx: self._select_route_region(i), width=46).pack(side="left", padx=(0, 5))
        self._small_button(top, "삭제", COLORS["danger"], COLORS["danger_hover"], lambda i=idx: self._delete_route_watch(i), width=48).pack(side="left")

        controls = ctk.CTkFrame(inner, fg_color="transparent")
        controls.pack(fill="x", pady=(7, 0))
        ctk.CTkLabel(
            controls,
            text=truncate_ui_text(region_text, 22),
            width=132,
            anchor="w",
            font=self._font(10),
            text_color=COLORS["text_muted"],
        ).pack(side="left", padx=(40, 8))
        ctk.CTkLabel(
            controls,
            text="인식률",
            width=50,
            anchor="w",
            font=self._font(10),
            text_color=COLORS["text_muted"],
        ).pack(side="left", padx=(0, 6))

        conf = self._safe_confidence(route.get("confidence", self._monitor_confidence))
        conf_label = ctk.CTkLabel(
            controls,
            text=f"{int(conf * 100)}%",
            width=42,
            font=self._font(11, "bold"),
            text_color=COLORS["accent_text"],
        )
        conf_label.pack(side="left", padx=(0, 5))
        slider = ctk.CTkSlider(
            controls,
            from_=0.3,
            to=1.0,
            number_of_steps=70,
            width=150,
            command=lambda value, i=idx, lbl=conf_label: (
                self._on_route_confidence_changed(i, value),
                lbl.configure(text=f"{int(float(value) * 100)}%"),
            ),
        )
        slider.set(conf)
        slider.pack(side="left", padx=(0, 5))
        self._small_button(controls, "범위해제", COLORS["bg_elevated"], COLORS["bg_card_hover"], lambda i=idx: self._clear_route_region(i), width=68).pack(side="left")

        action_row = ctk.CTkFrame(inner, fg_color="transparent")
        action_row.pack(fill="x", pady=(6, 0))
        ctk.CTkLabel(
            action_row,
            text=f"전용액션 {len(monitor_actions)}개",
            width=108,
            anchor="w",
            font=self._font(11, "bold"),
            text_color=COLORS["success_text"] if monitor_actions else COLORS["text_muted"],
        ).pack(side="left", padx=(40, 8))
        self._small_button(
            action_row,
            "접기" if idx in self._expanded_route_actions else "보기",
            COLORS["accent_orange"] if monitor_actions else COLORS["bg_elevated"],
            COLORS["confidence_amber_hover"] if monitor_actions else COLORS["bg_card_hover"],
            lambda i=idx: self._toggle_route_actions(i),
            width=46,
        ).pack(side="left", padx=(0, 5))
        self._small_button(
            action_row,
            "+ 추가",
            COLORS["success"],
            COLORS["green_hover"],
            lambda i=idx: self._add_route_action(i),
            width=58,
        ).pack(side="left", padx=(0, 5))
        self._small_button(
            action_row,
            "액션삭제",
            COLORS["bg_elevated"],
            COLORS["bg_card_hover"],
            lambda i=idx: self._clear_route_actions(i),
            width=66,
        ).pack(side="left", padx=(0, 8))
        condition_thumb = ctk.CTkLabel(
            action_row,
            text="조건",
            width=48,
            height=34,
            fg_color=COLORS["bg_card"],
            corner_radius=IOS_METRICS["control_radius_small"],
            text_color=COLORS["text_muted"],
        )
        condition_thumb.pack(side="left", padx=(0, 8))
        self._schedule_thumbnail(condition_thumb, condition_image, size=(44, 30))
        ctk.CTkLabel(
            action_row,
            text=truncate_ui_text(condition_text, 28),
            anchor="w",
            font=self._font(10),
            text_color=COLORS["error"] if condition_warning else (COLORS["accent_text"] if condition_image else COLORS["text_muted"]),
        ).pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._small_button(action_row, "조건", COLORS["scroll_purple"], COLORS["search_radius_purple_hover"], lambda i=idx: self._open_route_condition_settings(i), width=48).pack(side="left", padx=(0, 5))
        self._small_button(action_row, "조건해제", COLORS["bg_elevated"], COLORS["bg_card_hover"], lambda i=idx: self._clear_route_condition_image(i), width=68).pack(side="left")

        if idx in self._expanded_route_actions:
            self._build_route_actions_preview(inner, idx, monitor_actions)

        jump_row = ctk.CTkFrame(inner, fg_color="transparent")
        jump_row.pack(fill="x", pady=(7, 0))
        ctk.CTkLabel(
            jump_row,
            text="점프액션",
            width=108,
            anchor="w",
            font=self._font(11, "bold"),
            text_color=COLORS["accent_text"],
        ).pack(side="left", padx=(40, 8))

        values = [label for label, _ in self._action_options] or ["액션 선택"]
        combo = ctk.CTkComboBox(
            jump_row,
            values=values,
            width=360,
            height=30,
            font=self._font(11),
            dropdown_font=self._font(11),
            fg_color=COLORS["bg_elevated"],
            button_color=COLORS["accent_blue"],
            button_hover_color=COLORS["hover_blue"],
            text_color=COLORS["text_primary"],
            command=lambda value, i=idx: self._on_route_action_select(i, value),
        )
        combo.set(self._action_label_for_index(self._watch_goto_index(route)))
        combo.pack(side="left", fill="x", expand=True, padx=(0, 8))
        jump_enabled_var = tk.StringVar(value="활성" if route.get("jump_enabled", True) else "비활성")

        def update_jump_enabled(value: str, button=None, route_index: int = idx) -> None:
            self._on_route_jump_enabled_changed(route_index, value)
            if button is not None:
                enabled = value == "활성"
                button.configure(
                    selected_color=COLORS["success"] if enabled else COLORS["danger"],
                    selected_hover_color=COLORS["green_hover"] if enabled else COLORS["danger_hover"],
                )

        jump_toggle = ctk.CTkSegmentedButton(
            jump_row,
            values=["활성", "비활성"],
            variable=jump_enabled_var,
            width=126,
            height=30,
            fg_color=COLORS["bg_elevated"],
            selected_color=COLORS["success"] if route.get("jump_enabled", True) else COLORS["danger"],
            selected_hover_color=COLORS["green_hover"] if route.get("jump_enabled", True) else COLORS["danger_hover"],
            unselected_color=COLORS["bg_elevated"],
            unselected_hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_primary"],
            font=self._font(11, "bold"),
        )
        jump_toggle.configure(command=lambda value, button=jump_toggle: update_jump_enabled(value, button))
        jump_toggle.pack(side="left")

    def _toggle_route_actions(self, idx: int) -> None:
        if not 0 <= idx < len(self._route_watches):
            return
        if idx in self._expanded_route_actions:
            self._expanded_route_actions.discard(idx)
        else:
            self._expanded_route_actions.add(idx)
        self._refresh_route_list()

    def _build_route_actions_preview(self, parent, route_idx: int, actions: list[dict]) -> None:
        preview = ctk.CTkFrame(
            parent,
            fg_color=COLORS["bg_card"],
            corner_radius=IOS_METRICS["control_radius_small"],
            border_width=IOS_METRICS["card_border_width"],
            border_color=COLORS["border"],
        )
        preview.pack(fill="x", padx=(40, 0), pady=(7, 0))

        if not actions:
            ctk.CTkLabel(
                preview,
                text="전용액션 없음: + 추가를 눌러 이 이미지가 감지됐을 때 먼저 실행할 액션을 등록하세요.",
                font=self._font(10),
                text_color=COLORS["text_muted"],
                anchor="w",
            ).pack(fill="x", padx=10, pady=8)
            return

        for action_idx, action in enumerate(actions):
            item = ctk.CTkFrame(preview, fg_color="transparent")
            item.pack(fill="x", padx=10, pady=(8 if action_idx == 0 else 6, 4))
            action_type = str(action.get("type", "종류 없음"))
            ctk.CTkLabel(
                item,
                text=f"{route_idx + 1}-{action_idx + 1}",
                width=42,
                font=self._font(11, "bold"),
                text_color=COLORS["accent_text"],
            ).pack(side="left", padx=(0, 6))
            ctk.CTkLabel(
                item,
                text=truncate_ui_text(action_type, 13),
                width=86,
                anchor="w",
                font=self._font(11, "bold"),
                text_color=self._action_color(action_type),
            ).pack(side="left", padx=(0, 8))
            self._build_monitor_action_thumbnail(item, action)
            ctk.CTkLabel(
                item,
                text=truncate_ui_text(self._action_detail(action), 28),
                width=170,
                anchor="w",
                font=self._font(10),
                text_color=COLORS["text_secondary"],
            ).pack(side="left", padx=(0, 8))
            ctk.CTkLabel(
                item,
                text=truncate_ui_text(self._action_options_summary(action), 38),
                anchor="w",
                font=self._font(10),
                text_color=COLORS["text_muted"],
            ).pack(side="left", fill="x", expand=True)
            self._small_button(
                item,
                "수정",
                COLORS["accent_blue"],
                COLORS["hover_blue"],
                lambda r=route_idx, a=action_idx: self._edit_route_action(r, a),
                width=44,
            ).pack(side="right", padx=(5, 0))
            self._small_button(
                item,
                "삭제",
                COLORS["danger"],
                COLORS["danger_hover"],
                lambda r=route_idx, a=action_idx: self._delete_route_action(r, a),
                width=44,
            ).pack(side="right", padx=(5, 0))
            self._small_button(
                item,
                "↓",
                COLORS["bg_elevated"],
                COLORS["bg_card_hover"],
                lambda r=route_idx, a=action_idx: self._move_route_action(r, a, 1),
                width=30,
            ).pack(side="right", padx=(5, 0))
            self._small_button(
                item,
                "↑",
                COLORS["bg_elevated"],
                COLORS["bg_card_hover"],
                lambda r=route_idx, a=action_idx: self._move_route_action(r, a, -1),
                width=30,
            ).pack(side="right", padx=(5, 0))
            self._small_button(
                item,
                "테스트",
                COLORS["accent_orange"],
                COLORS["confidence_amber_hover"],
                lambda r=route_idx, a=action_idx: self._test_route_action(r, a),
                width=52,
            ).pack(side="right", padx=(5, 0))
            if action_idx < len(actions) - 1:
                separator = ctk.CTkFrame(
                    preview,
                    height=2,
                    fg_color=COLORS["accent"],
                    corner_radius=1,
                )
                separator.pack(fill="x", padx=10, pady=(4, 0))
                separator.pack_propagate(False)

    def _build_monitor_action_thumbnail(self, parent, action: dict) -> None:
        image_path = action.get("image") if action.get("type") == "이미지 클릭" else None
        thumb = ctk.CTkLabel(
            parent,
            text="IMG" if image_path else "-",
            width=34,
            height=26,
            fg_color=COLORS["bg_elevated"],
            corner_radius=IOS_METRICS["control_radius_small"],
            text_color=COLORS["text_muted"],
        )
        thumb.pack(side="left", padx=(0, 8))
        if image_path:
            self._schedule_thumbnail(thumb, image_path, size=(30, 22))

    def _add_route_action(self, route_idx: int) -> None:
        if not 0 <= route_idx < len(self._route_watches):
            return

        def on_save(action: dict) -> None:
            if not 0 <= route_idx < len(self._route_watches):
                return
            self._route_watches[route_idx].setdefault("monitor_actions", [])
            self._route_watches[route_idx]["monitor_actions"].append(action)
            self._expanded_route_actions.add(route_idx)
            self._refresh_route_list()

        MonitorActionEditorDialog(self, None, on_save)

    def _edit_route_action(self, route_idx: int, action_idx: int) -> None:
        if not 0 <= route_idx < len(self._route_watches):
            return
        actions = self._route_watches[route_idx].get("monitor_actions", []) or []
        if not 0 <= action_idx < len(actions):
            return

        def on_save(action: dict) -> None:
            current = self._route_watches[route_idx].setdefault("monitor_actions", [])
            if 0 <= action_idx < len(current):
                current[action_idx] = action
                self._expanded_route_actions.add(route_idx)
                self._refresh_route_list()

        MonitorActionEditorDialog(self, actions[action_idx], on_save)

    def _delete_route_action(self, route_idx: int, action_idx: int) -> None:
        if not 0 <= route_idx < len(self._route_watches):
            return
        actions = self._route_watches[route_idx].get("monitor_actions", []) or []
        if 0 <= action_idx < len(actions):
            actions.pop(action_idx)
            self._expanded_route_actions.add(route_idx)
            self._refresh_route_list()

    def _move_route_action(self, route_idx: int, action_idx: int, delta: int) -> None:
        if not 0 <= route_idx < len(self._route_watches):
            return
        actions = self._route_watches[route_idx].get("monitor_actions", []) or []
        new_idx = action_idx + delta
        if 0 <= action_idx < len(actions) and 0 <= new_idx < len(actions):
            actions[action_idx], actions[new_idx] = actions[new_idx], actions[action_idx]
            self._expanded_route_actions.add(route_idx)
            self._refresh_route_list()

    def _test_route_action(self, route_idx: int, action_idx: int) -> None:
        if not 0 <= route_idx < len(self._route_watches):
            return
        actions = self._route_watches[route_idx].get("monitor_actions", []) or []
        if not 0 <= action_idx < len(actions):
            return
        self._run_monitor_action_test(actions[action_idx])

    def _small_button(self, parent, text, color, hover, command, width=54):
        return ctk.CTkButton(
            parent,
            text=text,
            width=width,
            height=26,
            font=self._font(11, "bold"),
            fg_color=color,
            hover_color=hover,
            text_color=COLORS["text_primary"],
            corner_radius=IOS_METRICS["control_radius_small"],
            command=command,
        )

    @staticmethod
    def _action_color(action_type: str):
        if action_type == "텍스트 입력":
            return COLORS["success"]
        if action_type == "키 입력":
            return COLORS["accent_orange"]
        if action_type in {"마우스 클릭", "이미지 클릭"}:
            return COLORS["accent_blue"]
        if action_type == "드래그":
            return COLORS["warning"]
        return COLORS["text_secondary"]

    @staticmethod
    def _action_detail(action: dict) -> str:
        action_type = action.get("type", "")
        if action_type == "텍스트 입력":
            return f'"{str(action.get("text", ""))[:24]}"'
        if action_type == "키 입력":
            keys = action.get("keys", []) or []
            return format_key_combo([str(k).lower().strip() for k in keys if str(k).strip()]) or "기록 키"
        if action_type == "마우스 클릭":
            return f"({action.get('x', 0)}, {action.get('y', 0)})"
        if action_type == "이미지 클릭":
            return Path(str(action.get("image", ""))).name if action.get("image") else "이미지 없음"
        if action_type == "스크롤":
            return str(action.get("amount", 0))
        if action_type == "드래그":
            return f"({action.get('from_x', 0)},{action.get('from_y', 0)})→({action.get('to_x', 0)},{action.get('to_y', 0)})"
        return ""

    @staticmethod
    def _action_options_summary(action: dict) -> str:
        action_type = action.get("type", "")
        parts = []
        repeat = int(action.get("repeat_count", 1) or 1)
        if repeat > 1:
            parts.append(f"반복 {repeat}")
        wait = float(action.get("wait_after", 0.5) or 0)
        if wait:
            parts.append(f"대기 {wait:g}s")
        if action_type == "이미지 클릭":
            confidence = action.get("confidence")
            if confidence:
                parts.append(f"인식 {int(float(confidence) * 100)}%")
            if action.get("search_region"):
                parts.append("범위")
            if action.get("verify_image_color"):
                parts.append("색상")
            if action.get("verify_image_brightness"):
                parts.append("밝기")
        return " · ".join(parts) if parts else "기본 옵션"

    def _run_monitor_action_test(self, action: dict) -> None:
        action = copy.deepcopy(action)
        def run():
            try:
                from ..player.rule_executor import get_rule_executor

                success, message = get_rule_executor().test_single_monitor_action(action)
                level = "info" if success else "warning"
                getattr(logger, level)("[모니터링 테스트] %s", message)
            except Exception as exc:
                logger.error("[모니터링 테스트] 오류: %s", exc)

        threading.Thread(target=run, daemon=True).start()

    def _complete_save(self, status_text: str) -> None:
        self.was_saved = True
        saved_to_plan = True
        if self._on_save is not None:
            try:
                saved_to_plan = self._on_save() is not False
            except Exception as exc:
                saved_to_plan = False
                logger.error("[모니터링] 저장 후속 처리 실패: %s", exc)

        if self._save_status_label is not None:
            if saved_to_plan:
                self._save_status_label.configure(text=status_text, text_color=COLORS["success_text"])
            else:
                self._save_status_label.configure(text="저장 실패: 로그 확인", text_color=COLORS["danger"])

    def _save(self) -> None:
        enabled = bool(self._enabled_var.get()) if self._enabled_var is not None else False
        rule = self._rule
        final_image = getattr(rule, "target_image", None)

        if not enabled:
            rule.is_monitoring_mode = False
            rule.monitoring_final_image = None
            rule.monitoring_watches = []
            self._complete_save("저장됨: 모니터링 OFF")
            return

        valid_watches = []
        for idx, route in enumerate(self._route_watches, start=1):
            image = route.get("image")
            goto_index = self._watch_goto_index(route)
            if not image and goto_index < 0:
                continue
            if not image:
                messagebox.showerror("설정 필요", f"모니터링 이미지 액션 {idx}번의 이미지를 선택하세요.", parent=self)
                return
            if goto_index < 0:
                messagebox.showerror("설정 필요", f"모니터링 이미지 액션 {idx}번의 이동 대상 액션을 선택하세요.", parent=self)
                return
            valid_watches.append(
                {
                    "image": image,
                    "search_region": copy.deepcopy(route.get("search_region")),
                    "confidence": self._safe_confidence(route.get("confidence", self._monitor_confidence)),
                    "goto_index": goto_index,
                    "jump_enabled": bool(route.get("jump_enabled", True)),
                    "monitor_actions": copy.deepcopy(route.get("monitor_actions", []) or []),
                    "condition_image": route.get("condition_image"),
                    "condition_search_region": copy.deepcopy(route.get("condition_search_region")),
                    "condition_confidence": self._safe_confidence(route.get("condition_confidence", 0.8)),
                    "condition_jump_when_visible": bool(route.get("condition_jump_when_visible", False)),
                    "condition_verify_image_color": bool(route.get("condition_verify_image_color", False)),
                    "condition_verify_image_brightness": bool(route.get("condition_verify_image_brightness", False)),
                }
            )

        if not valid_watches:
            messagebox.showerror("설정 필요", "모니터링 이미지 액션을 하나 이상 설정하세요.", parent=self)
            return

        rule.is_monitoring_mode = True
        rule.monitoring_final_image = final_image
        rule.monitoring_watches = valid_watches

        logger.info(
            "[모니터링] 저장 완료: routes=%s",
            sum(1 for item in valid_watches if self._watch_goto_index(item) >= 0),
        )
        self._complete_save("저장됨")
