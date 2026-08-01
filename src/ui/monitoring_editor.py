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
from ..player.rule_executor import (
    _MULTISCALE_FACTORS,
    _get_cached_template_variants,
    _grab_screen_bgr,
    _passes_image_visual_verification,
    _resize_template_gray,
)
from .analyzer_view import (
    VIDEO_FILE_PATTERNS,
    get_cached_thumbnail,
    is_video_media_path,
    set_cached_thumbnail,
    submit_thumbnail_task,
)
from .constants import ACTION_NAMES_SHORT
from .key_input_dialog import KeyInputDialog, format_key_combo
from .random_key_sequence_dialog import RandomKeySequenceDialog
from .text_overflow import truncate_ui_text
from .theme import COLORS, IOS_FONTS, IOS_METRICS
from .ui_batcher import resolve_widget_ui_post
from ..player.random_key_sequence import (
    format_random_key_sequences_summary,
    normalize_random_key_sequences,
)

logger = get_logger(__name__)

_MONITORING_SETTINGS_CLIPBOARD: dict | None = None
_MAX_MONITORING_IMAGES_PER_ROUTE = 10
_MONITORING_ROUTE_RENDER_BATCH_SIZE = 2
_MONITORING_ACTION_RENDER_BATCH_SIZE = 2
_MONITORING_RENDER_BATCH_DELAY_MS = 12


class MonitorActionEditorDialog(ctk.CTkToplevel):
    """Direct editor for monitoring-only actions."""

    VIDEO_CLICK_TYPE = "동영상클릭"
    LEGACY_VIDEO_CLICK_TYPE = "동영상 입력"
    RANDOM_KEY_TYPE = "랜덤키 입력"
    ACTION_TYPES = ("이미지 클릭", VIDEO_CLICK_TYPE, "마우스 클릭", "키 입력", RANDOM_KEY_TYPE, "텍스트 입력", "스크롤", "드래그")
    VIDEO_CLICK_TYPES = (VIDEO_CLICK_TYPE, LEGACY_VIDEO_CLICK_TYPE)
    MEDIA_CLICK_TYPES = ("이미지 클릭", VIDEO_CLICK_TYPE, LEGACY_VIDEO_CLICK_TYPE)
    CLICK_TYPES = ("click", "double_click", "right_click")

    @classmethod
    def _normalise_action_type(cls, action_type: str) -> str:
        if action_type == cls.LEGACY_VIDEO_CLICK_TYPE:
            return cls.VIDEO_CLICK_TYPE
        return action_type

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
        self._random_key_sequences = normalize_random_key_sequences(self._source_action.get("random_key_sequences", []))
        try:
            self._random_key_step_delay = max(0.0, float(self._source_action.get("random_key_step_delay", 0.8) or 0.0))
        except (TypeError, ValueError):
            self._random_key_step_delay = 0.8
        self._key_label = None
        self._random_key_label = None
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
            text="감시 이미지가 발견되었을 때만 실행되는 액션입니다. 이미지/동영상 클릭은 인식률, 검색범위, 색상/밝기 확인을 여기서 바로 설정합니다.",
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

        action_type = self._normalise_action_type(str(self._source_action.get("type") or "이미지 클릭"))
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
            text="\uc800\uc7a5",
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
        action_type = self._normalise_action_type(self._field_vars["type"].get())
        if action_type != self._field_vars["type"].get():
            self._field_vars["type"].set(action_type)
        ctk.CTkLabel(
            self._detail_frame,
            text="상세 설정",
            font=self._font(14, "bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor="w", padx=12, pady=(12, 6))

        if action_type in self.MEDIA_CLICK_TYPES:
            self._build_image_click_fields()
        elif action_type == "마우스 클릭":
            self._build_mouse_fields()
        elif action_type == "키 입력":
            self._build_key_fields()
        elif action_type == self.RANDOM_KEY_TYPE:
            self._build_random_key_fields()
        elif action_type == "텍스트 입력":
            self._build_text_fields()
        elif action_type == "\uc2a4\ud06c\ub864":
            self._build_scroll_fields()
        elif action_type == "\ub4dc\ub798\uadf8":
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
        action_type = self._normalise_action_type(self._field_vars["type"].get())
        is_video_action = action_type in self.VIDEO_CLICK_TYPES
        media_label = "\ub3d9\uc601\uc0c1" if is_video_action else "\uc774\ubbf8\uc9c0"
        image_row = self._row(self._detail_frame, media_label)
        self._image_label = ctk.CTkLabel(
            image_row,
            text=truncate_ui_text(Path(self._image_path).name, 34) if self._image_path else f"{media_label} 없음",
            anchor="w",
            font=self._font(12, "bold"),
            text_color=COLORS["accent_text"] if self._image_path else COLORS["text_muted"],
        )
        self._image_label.pack(side="left", fill="x", expand=True, padx=(0, 8))
        ctk.CTkButton(
            image_row,
            text="영상 선택" if is_video_action else "선택",
            width=76,
            height=30,
            font=self._font(12, "bold"),
            fg_color=COLORS["accent_blue"],
            hover_color=COLORS["hover_blue"],
            command=self._choose_image,
        ).pack(side="right")
        if is_video_action:
            ctk.CTkButton(
                image_row,
                text="영상 테스트",
                width=88,
                height=30,
                font=self._font(12, "bold"),
                fg_color=COLORS["success"],
                hover_color=COLORS["green_hover"],
                command=self._test_video_template,
            ).pack(side="right", padx=(0, 6))

        click_row = self._row(self._detail_frame, "클릭 유형")
        self._build_click_type_combo(click_row)

        conf_row = self._row(self._detail_frame, "\uc778\uc2dd\ub960")
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

        region_row = self._row(self._detail_frame, "\uac80\uc0c9\ubc94\uc704")
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
        for index, (key, label, default) in enumerate((
            ("verify_image_color", "색상 확인", False),
            ("verify_image_brightness", "밝기 확인", False),
            ("alternate_mouse_route", "직각 이동", False),
            ("click_until_image_disappears", "사라질 때까지 반복", False),
            ("click_until_image_disappears_safety_enabled", "반복 안전장치", True),
            ("skip_on_not_found", "못찾으면 스킵", False),
        )):
            ctk.CTkCheckBox(
                option_grid,
                text=label,
                variable=self._bool_var(key, default),
                font=self._font(11),
                text_color=COLORS["text_secondary"],
                fg_color=COLORS["accent_blue"],
                hover_color=COLORS["hover_blue"],
            ).grid(row=index // 2, column=index % 2, sticky="w", padx=(0, 22), pady=3)
        option_grid.grid_columnconfigure(0, weight=1, minsize=160)
        option_grid.grid_columnconfigure(1, weight=1, minsize=180)

        delay_row = self._row(self._detail_frame, "\uc0ac\ub77c\uc9d0 \ub300\uae30")
        self._entry(delay_row, "click_until_image_disappears_delay", 0.5, width=100)
        ctk.CTkLabel(delay_row, text="\ucd08", font=self._font(11), text_color=COLORS["text_muted"]).pack(side="left", padx=(6, 0))

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
        row = self._row(self._detail_frame, "\ud0a4")
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
        row = self._row(self._detail_frame, "\uc785\ub825\ubb38")
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
        ctk.CTkLabel(typing_row, text="초", font=self._font(11), text_color=COLORS["text_muted"]).pack(side="left", padx=5)
        self._entry(typing_row, "typing_delay_range", 0.05, width=80)

    def _build_scroll_fields(self) -> None:
        row = self._row(self._detail_frame, "\uc2a4\ud06c\ub864")
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

    def _build_random_key_fields(self) -> None:
        row = self._row(self._detail_frame, "키 묶음")
        self._random_key_label = ctk.CTkLabel(
            row,
            text=format_random_key_sequences_summary(self._random_key_sequences),
            anchor="w",
            font=self._font(12, "bold"),
            text_color=COLORS["accent_text"] if self._random_key_sequences else COLORS["text_muted"],
        )
        self._random_key_label.pack(side="left", fill="x", expand=True, padx=(0, 8))
        ctk.CTkButton(
            row,
            text="묶음 설정",
            width=96,
            height=30,
            font=self._font(12, "bold"),
            fg_color=COLORS["accent_orange"],
            hover_color=COLORS["confidence_amber_hover"],
            command=self._edit_random_key_sequences,
        ).pack(side="right")
        ctk.CTkLabel(
            self._detail_frame,
            text="실행 시 저장된 묶음 중 1개를 랜덤으로 골라 순서대로 입력합니다.",
            font=self._font(11),
            text_color=COLORS["text_muted"],
            anchor="w",
            wraplength=520,
            justify="left",
        ).pack(fill="x", padx=12, pady=(0, 8))

    def _edit_random_key_sequences(self) -> None:
        dialog = RandomKeySequenceDialog(
            self,
            sequences=self._random_key_sequences,
            step_delay=self._random_key_step_delay,
        )
        result = dialog.get_result()
        if not result:
            return
        sequences, step_delay = result
        self._random_key_sequences = sequences
        self._random_key_step_delay = step_delay
        if self._random_key_label is not None:
            self._random_key_label.configure(
                text=format_random_key_sequences_summary(sequences),
                text_color=COLORS["accent_text"] if sequences else COLORS["text_muted"],
            )

    def _build_common_options(self, parent) -> None:
        card = ctk.CTkFrame(parent, fg_color=COLORS["bg_card"], corner_radius=IOS_METRICS["control_radius"])
        card.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(
            card,
            text="\ubc18\ubcf5/\ub300\uae30",
            font=self._font(14, "bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor="w", padx=12, pady=(12, 6))

        row = self._row(card, "반복")
        self._entry(row, "repeat_count", 1, width=80)
        ctk.CTkLabel(row, text="\ud68c", font=self._font(11), text_color=COLORS["text_muted"]).pack(side="left", padx=(5, 12))
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

        wait_row = self._row(card, "\uc2e4\ud589 \ud6c4")
        self._entry(wait_row, "wait_after", 0.5, width=90)
        ctk.CTkLabel(wait_row, text="\ucd08 \ub300\uae30", font=self._font(11), text_color=COLORS["text_muted"]).pack(side="left", padx=(5, 12))
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
        type_var = self._field_vars.get("type")
        action_type = self._normalise_action_type(type_var.get() if type_var is not None else "이미지 클릭")
        is_video_action = action_type in self.VIDEO_CLICK_TYPES
        path = filedialog.askopenfilename(
            title="전용액션 동영상 선택" if is_video_action else "전용액션 이미지 선택",
            initialdir=str(templates_dir),
            filetypes=[("동영상 파일", VIDEO_FILE_PATTERNS), ("모든 파일", "*.*")] if is_video_action else [("이미지 파일", "*.png *.jpg *.jpeg *.bmp")],
        )
        if not path:
            return
        if is_video_action and not is_video_media_path(path):
            messagebox.showwarning("\uc804\uc6a9\uc561\uc158 \uc601\uc0c1", "\uc601\uc0c1 \ud30c\uc77c\ub9cc \uc120\ud0dd\ud558\uc138\uc694.", parent=self)
            return
        self._image_path = self._editor._copy_image_to_templates(
            path,
            prefix="monitor_action_video" if is_video_action else "monitor_action",
        )
        if self._image_label is not None:
            self._image_label.configure(
                text=truncate_ui_text(Path(self._image_path).name, 34),
                text_color=COLORS["accent_text"],
            )

    def _test_video_template(self) -> None:
        if not self._image_path:
            messagebox.showwarning("\uc601\uc0c1 \ud14c\uc2a4\ud2b8", "\uc601\uc0c1\uc774 \uc124\uc815\ub418\uc9c0 \uc54a\uc558\uc2b5\ub2c8\ub2e4.", parent=self)
            return
        if not Path(self._image_path).exists():
            messagebox.showerror("\uc601\uc0c1 \ud14c\uc2a4\ud2b8", f"\uc601\uc0c1 \ud30c\uc77c\uc774 \uc5c6\uc2b5\ub2c8\ub2e4.\n{self._image_path}", parent=self)
            return
        if not is_video_media_path(self._image_path):
            messagebox.showwarning("\uc601\uc0c1 \ud14c\uc2a4\ud2b8", "\uc601\uc0c1 \ud30c\uc77c\ub9cc \ud14c\uc2a4\ud2b8\ud560 \uc218 \uc788\uc2b5\ub2c8\ub2e4.", parent=self)
            return

        route = {
            "condition_image": self._image_path,
            "condition_confidence": self._confidence,
            "condition_search_region": copy.deepcopy(self._search_region),
            "condition_verify_image_color": bool(self._field_vars.get("verify_image_color").get()) if self._field_vars.get("verify_image_color") is not None else False,
            "condition_verify_image_brightness": bool(self._field_vars.get("verify_image_brightness").get()) if self._field_vars.get("verify_image_brightness") is not None else False,
        }
        ui_post = resolve_widget_ui_post(self)

        def worker() -> None:
            try:
                result = self._editor._match_condition_image_for_test(route)
            except Exception as exc:
                logger.exception("[모니터링] 전용액션 영상 테스트 오류")
                result = {"ok": False, "error": str(exc)}

            def show_result() -> None:
                try:
                    if not self.winfo_exists():
                        return
                except tk.TclError:
                    return
                if not result.get("ok"):
                    messagebox.showerror("\uc601\uc0c1 \ud14c\uc2a4\ud2b8", f"\ud14c\uc2a4\ud2b8 \uc2e4\ud328: {result.get('error', 'unknown error')}", parent=self)
                    return
                score_pct = int(round(float(result.get("score", 0.0)) * 100))
                threshold_pct = int(round(float(result.get("threshold", 0.0)) * 100))
                found = bool(result.get("found"))
                status = "\ubc1c\uacac" if found else "\ubbf8\ubc1c\uacac"
                detail = [
                    f"결과: {status}",
                    f"인식률: {score_pct}% / 기준 {threshold_pct}%",
                    f"검색범위: {result.get('region_label', '-')}",
                ]
                if result.get("position"):
                    x, y = result["position"]
                    detail.append(f"위치: ({x}, {y})")
                if result.get("variant"):
                    detail.append(f"동영상 프레임: {result.get('variant')}")
                if result.get("visual_failed"):
                    detail.append("추가 확인: 점수는 기준 이상이지만 색상/밝기 검증 실패")
                if result.get("verify"):
                    detail.append(f"검증옵션: {result.get('verify')}")
                messagebox.showinfo("\uc601\uc0c1 \ud14c\uc2a4\ud2b8", "\n".join(detail), parent=self)

            ui_post(show_result)

        threading.Thread(target=worker, daemon=True).start()

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
        dialog.title("\uc804\uc6a9\uc561\uc158 \uac80\uc0c9\ubc94\uc704")
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
            text="A/B영역은 일반 이미지 액션과 같은 공용 범위입니다. 자유영역은 이 전용액션에만 적용합니다.",
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

        if action_type in self.MEDIA_CLICK_TYPES or action_type == "마우스 클릭":
            var = self._field_vars.get("alternate_mouse_route")
            if var is not None:
                action["alternate_mouse_route"] = bool(var.get())

        if action_type in self.MEDIA_CLICK_TYPES:
            for key in (
                "verify_image_color",
                "verify_image_brightness",
                "click_until_image_disappears",
                "click_until_image_disappears_safety_enabled",
                "skip_on_not_found",
            ):
                var = self._field_vars.get(key)
                if var is not None:
                    action[key] = bool(var.get())

    def _save(self) -> None:
        action_type = self._normalise_action_type(self._field_vars["type"].get())
        action: dict = {"type": action_type}

        if action_type in self.MEDIA_CLICK_TYPES:
            if not self._image_path:
                media_label = "\ub3d9\uc601\uc0c1" if action_type in self.VIDEO_CLICK_TYPES else "\uc774\ubbf8\uc9c0"
                messagebox.showerror("설정 필요", f"{action_type} 액션에는 {media_label}가 필요합니다.", parent=self)
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
        elif action_type == self.RANDOM_KEY_TYPE:
            sequences = normalize_random_key_sequences(self._random_key_sequences)
            if not sequences:
                messagebox.showerror("설정 필요", "랜덤키 입력 액션에는 키 묶음이 필요합니다.", parent=self)
                return
            action["random_key_sequences"] = sequences
            action["random_key_step_delay"] = self._random_key_step_delay
        elif action_type == "텍스트 입력":
            text = self._text_box.get("1.0", "end").rstrip("\n") if self._text_box is not None else ""
            if not text:
                messagebox.showerror("설정 필요", "텍스트 입력 액션에는 입력문이 필요합니다.", parent=self)
                return
            action["text"] = text
        elif action_type == "\uc2a4\ud06c\ub864":
            action["amount"] = self._int_value(self._field_vars["amount"].get(), 0)
        elif action_type == "\ub4dc\ub798\uadf8":
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
        self._route_slots: dict[int, ctk.CTkFrame] = {}
        self._route_action_slots: dict[tuple[int, int], ctk.CTkFrame] = {}
        self._route_action_preview_hosts: dict[int, ctk.CTkFrame] = {}
        self._route_jump_rows: dict[int, ctk.CTkFrame] = {}
        self._route_action_toggle_buttons: dict[int, ctk.CTkButton] = {}
        self._route_render_generation = 0
        self._render_after_ids: set[str] = set()
        self._closed = False
        self._route_count_label = None
        self._save_status_label = None
        self._action_options: list[tuple[str, int]] = []
        self._font_cache: dict[tuple[int, str], ctk.CTkFont] = {}
        self._expanded_route_actions: set[int] = set()
        self._pending_thumbnail_labels: dict[tuple[str, tuple[int, int]], list] = {}

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
        self.geometry("1080x940")
        self.minsize(960, 840)
        self.resizable(True, True)
        self.configure(fg_color=COLORS["bg_dark"])
        self.transient(self._owner)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        self.update_idletasks()
        x = max(0, (self.winfo_screenwidth() - 1080) // 2)
        y = max(0, (self.winfo_screenheight() - 940) // 2)
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
            images = self._normalise_route_images(watch)
            self._route_watches.append(
                {
                    "image": images[0]["image"] if images else (watch.get("image") or watch.get("image_path")),
                    "images": images,
                    "search_region": copy.deepcopy(watch.get("search_region")),
                    "confidence": self._safe_confidence(watch.get("confidence", self._monitor_confidence)),
                    "goto_index": goto_index,
                    "goto_rule_id": str(watch.get("goto_rule_id") or ""),
                    "jump_enabled": bool(watch.get("jump_enabled", True)),
                    "pre_jump_recheck": bool(watch.get("pre_jump_recheck", True)),
                    "monitor_actions": copy.deepcopy(watch.get("monitor_actions", []) or []),
                    "condition_image": watch.get("condition_image"),
                    "condition_search_region": copy.deepcopy(watch.get("condition_search_region")),
                    "condition_confidence": self._safe_confidence(watch.get("condition_confidence", 0.8)),
                    "condition_jump_when_visible": bool(watch.get("condition_jump_when_visible", False)),
                    "condition_verify_image_color": bool(watch.get("condition_verify_image_color", False)),
                    "condition_verify_image_brightness": bool(watch.get("condition_verify_image_brightness", False)),
                }
            )

    def _build_action_options(self) -> list[dict]:
        options = [{"label": "액션 선택", "index": -1, "rule_id": "", "depth": 0, "step": "", "rule": None}]
        flat_index = 0

        def add_rule(action, step: str, depth: int) -> None:
            nonlocal flat_index
            action_type = ACTION_NAMES_SHORT.get(getattr(action, "action_type", ""), getattr(action, "action_type", "") or "동작")
            desc = getattr(action, "description", "") or ""
            desc_text = f" - {truncate_ui_text(desc, 22)}" if desc else ""
            disabled_text = " (비활성)" if not getattr(action, "enabled", True) else ""
            child_count = len(getattr(action, "children", []) or [])
            child_text = f" +하위{child_count}" if child_count else ""
            indent = "  " * depth
            options.append(
                {
                    "label": f"{indent}{step}. {action_type}{child_text}{disabled_text}{desc_text}",
                    "index": flat_index,
                    "rule_id": getattr(action, "rule_id", "") or "",
                    "depth": depth,
                    "step": step,
                    "rule": action,
                    "has_children": child_count > 0,
                }
            )
            flat_index += 1
            for child_idx, child in enumerate(getattr(action, "children", []) or [], start=1):
                add_rule(child, f"{step}-{child_idx}", depth + 1)

        for idx, action in enumerate(self._plan_rules, start=1):
            add_rule(action, str(idx), 0)
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

    @staticmethod
    def _safe_priority(value, default: int = 1) -> int:
        try:
            return max(1, min(_MAX_MONITORING_IMAGES_PER_ROUTE, int(value)))
        except (TypeError, ValueError):
            return default

    def _normalise_route_images(self, route: dict) -> list[dict]:
        images: list[dict] = []
        seen: set[str] = set()

        def add_image(image_path, priority=None) -> None:
            source_item = image_path if isinstance(image_path, dict) else None
            actual_path = (
                source_item.get("image") or source_item.get("image_path")
                if source_item is not None
                else image_path
            )
            if not actual_path:
                return
            text_path = str(actual_path)
            if text_path in seen:
                return
            seen.add(text_path)
            image_info = {
                "image": text_path,
                "priority": self._safe_priority(priority, len(images) + 1),
                "_order": len(images),
            }
            if source_item is not None:
                if source_item.get("confidence") is not None:
                    image_info["confidence"] = self._safe_confidence(source_item.get("confidence"), self._monitor_confidence)
                if source_item.get("search_region") is not None:
                    image_info["search_region"] = copy.deepcopy(source_item.get("search_region"))
                if source_item.get("verify_image_color") is not None:
                    image_info["verify_image_color"] = bool(source_item.get("verify_image_color"))
                if source_item.get("verify_image_brightness") is not None:
                    image_info["verify_image_brightness"] = bool(source_item.get("verify_image_brightness"))
            images.append(image_info)

        raw_images = route.get("images")
        if isinstance(raw_images, list):
            for item in raw_images:
                if isinstance(item, dict):
                    image_item = dict(item)
                    image_item["image"] = item.get("image") or item.get("image_path")
                    add_image(image_item, item.get("priority"))
                else:
                    add_image(item)

        add_image(route.get("image") or route.get("image_path"), len(images) + 1)
        images.sort(key=lambda item: (item["priority"], item.get("_order", 0)))
        return [
            {
                key: copy.deepcopy(value)
                for key, value in {**item, "priority": idx + 1}.items()
                if key != "_order"
            }
            for idx, item in enumerate(images[:_MAX_MONITORING_IMAGES_PER_ROUTE])
        ]

    def _set_route_images(self, idx: int, images: list[dict]) -> None:
        if not 0 <= idx < len(self._route_watches):
            return
        normalized = []
        seen: set[str] = set()
        for item in images[:_MAX_MONITORING_IMAGES_PER_ROUTE]:
            image_path = item.get("image") if isinstance(item, dict) else item
            if not image_path:
                continue
            text_path = str(image_path)
            if text_path in seen:
                continue
            seen.add(text_path)
            normalized_item = {"image": text_path, "priority": len(normalized) + 1}
            if isinstance(item, dict):
                for key in ("confidence", "search_region", "verify_image_color", "verify_image_brightness"):
                    if item.get(key) is not None:
                        normalized_item[key] = copy.deepcopy(item.get(key))
            normalized.append(normalized_item)
        self._route_watches[idx]["images"] = normalized
        self._route_watches[idx]["image"] = normalized[0]["image"] if normalized else None

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
            text="최종이미지를 기다리다가 등록한 모니터링 이미지가 먼저 보이면 전용액션을 실행한 뒤 지정한 액션으로 점프하고 모니터링을 종료합니다.",
            font=self._font(12),
            text_color=COLORS["text_secondary"],
            wraplength=920,
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
            text="1. 최종이미지 대기  →  2. 모니터링 이미지 발견  →  3. 전용액션 실행  →  지정 액션으로 점프",
            font=self._font(13, "bold"),
            text_color=COLORS["accent_text"],
            wraplength=980,
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
            text="\ub4f1\ub85d 0\uac1c",
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
            text="모니터링 이미지가 보이면 전용액션을 먼저 실행하고, 지정한 점프액션으로 이동하면 모니터링은 종료됩니다.",
            font=self._font(11),
            text_color=COLORS["text_secondary"],
            anchor="w",
            wraplength=980,
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
            text="\uc800\uc7a5",
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
            return "\ubbf8\uc124\uc815"
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

    def _apply_region_to_target(self, target: str, region, source_label: str = "\uac80\uc0c9\ubc94\uc704", idx: int | None = None) -> bool:
        normalized = self._normalize_search_region_value(region)
        if normalized is None:
            return False
        if target == "route" and idx is not None and 0 <= idx < len(self._route_watches):
            self._route_watches[idx]["search_region"] = normalized
            logger.info("[모니터링] R%s %s 적용: %s", idx + 1, source_label, normalized)
            self._refresh_route_row(idx)
            return True
        if target == "condition" and idx is not None and 0 <= idx < len(self._route_watches):
            self._route_watches[idx]["condition_search_region"] = normalized
            logger.info("[모니터링] R%s 조건 %s 적용: %s", idx + 1, source_label, normalized)
            self._refresh_route_row(idx)
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
            if is_video_media_path(path):
                frames = _get_cached_template_variants(str(path))
                if not frames:
                    return "동영상 조건 템플릿을 읽을 수 없습니다."
                # Moving templates are expected to vary, but every sampled frame
                # being nearly flat still makes the condition unreliable.
                gray_values = [frame[0] for frame in frames if frame and frame[0] is not None]
                if not gray_values:
                    return "동영상 조건 프레임이 비어 있습니다."
                mean_std = float(np.mean([np.std(gray) for gray in gray_values]))
                if mean_std < 3:
                    return "동영상 조건 프레임이 거의 단색입니다. 다시 캡처하세요."
                return ""

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
        pending_key = (cache_source, size)
        waiters = [
            waiter
            for waiter in self._pending_thumbnail_labels.get(pending_key, [])
            if self._widget_exists(waiter)
        ]
        waiters.append(label)
        self._pending_thumbnail_labels[pending_key] = waiters
        if len(waiters) > 1:
            return
        ui_post = resolve_widget_ui_post(self)

        def load_thumbnail():
            try:
                if self._closed:
                    return
                if is_video_media_path(source):
                    cap = cv2.VideoCapture(source)
                    try:
                        if not cap.isOpened():
                            raise ValueError("video open failed")
                        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
                        if frame_count > 1:
                            cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, frame_count // 2))
                        ok, frame = cap.read()
                        if not ok or frame is None:
                            raise ValueError("video frame read failed")
                        img_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    finally:
                        cap.release()
                else:
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
                        if self._closed:
                            self._pending_thumbnail_labels.pop(pending_key, None)
                            return
                        ctk_img = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=(new_w, new_h))
                        set_cached_thumbnail(cache_source, size, ctk_img)
                        waiters = self._pending_thumbnail_labels.pop(pending_key, [])
                        for waiter in waiters:
                            if not self._widget_exists(waiter):
                                continue
                            if getattr(waiter, "_thumb_source", None) != (source, size):
                                continue
                            waiter.configure(image=ctk_img, text="")
                            waiter._thumb_img = ctk_img
                    except (tk.TclError, RuntimeError):
                        pass

                ui_post(apply_thumbnail)
            except Exception as exc:
                ui_post(lambda: self._pending_thumbnail_labels.pop(pending_key, None))
                logger.warning("monitoring thumbnail load failed: %s - %s", source, exc)

        submit_thumbnail_task(
            load_thumbnail,
            on_drop=lambda: ui_post(
                lambda: self._pending_thumbnail_labels.pop(pending_key, None)
            ),
        )

    @staticmethod
    def _widget_exists(widget) -> bool:
        try:
            return bool(widget.winfo_exists())
        except (tk.TclError, RuntimeError):
            return False

    def _show_region_options(self, target: str, idx: int | None = None) -> None:
        if target in {"route", "condition"} and (idx is None or not 0 <= idx < len(self._route_watches)):
            return

        title_text = "\uc870\uac74 \uc774\ubbf8\uc9c0 \uac80\uc0c9\ubc94\uc704" if target == "condition" else "\ubaa8\ub2c8\ud130\ub9c1 \uc774\ubbf8\uc9c0 \uc561\uc158 \uac80\uc0c9\ubc94\uc704"
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
            text="A/B 영역은 일반 이미지 액션과 같은 공용 프리셋을 쓰고, 자유영역은 현재 모니터링 항목에만 적용합니다.",
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

    def _action_option_by_rule_id(self, rule_id: str | None) -> dict | None:
        if not rule_id:
            return None
        for option in self._action_options:
            if option.get("rule_id") == rule_id:
                return option
        return None

    def _top_level_option_by_index(self, goto_index: int) -> dict | None:
        top_seen = -1
        for option in self._action_options:
            if option.get("index", -1) < 0 or option.get("depth", 0) != 0:
                continue
            top_seen += 1
            if top_seen == goto_index:
                return option
        return None

    def _action_label_for_route(self, route: dict) -> str:
        option = self._action_option_by_rule_id(route.get("goto_rule_id"))
        if option is None:
            option = self._top_level_option_by_index(self._watch_goto_index(route))
        if option is None:
            return "액션 선택"
        return option.get("label") or "액션 선택"

    def _default_route_goto_index(self) -> int:
        for option in self._action_options:
            if option.get("index", -1) >= 0:
                return int(option.get("index", -1))
        return -1

    def _default_route_goto_rule_id(self) -> str:
        for option in self._action_options:
            if option.get("index", -1) >= 0:
                return str(option.get("rule_id") or "")
        return ""

    def _add_route_watch(self) -> None:
        default_goto_index = self._default_route_goto_index()
        self._route_watches.append(
            {
                "image": None,
                "images": [],
                "search_region": None,
                "confidence": self._monitor_confidence,
                "goto_index": default_goto_index,
                "goto_rule_id": self._default_route_goto_rule_id(),
                "jump_enabled": True,
                "pre_jump_recheck": default_goto_index >= 0,
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
        goto_rule_id = str(route.get("goto_rule_id") or "")
        option = self._action_option_by_rule_id(goto_rule_id)
        goto_index = self._watch_goto_index(route)
        if option is not None:
            goto_index = int(option.get("index", -1))
        elif goto_index < 0 or goto_index >= len(self._plan_rules):
            goto_index = self._default_route_goto_index()
            goto_rule_id = self._default_route_goto_rule_id()
        images = self._normalise_route_images(route)
        jump_enabled = bool(route.get("jump_enabled", True))
        return {
            "image": images[0]["image"] if images else (route.get("image") or route.get("image_path")),
            "images": images,
            "search_region": copy.deepcopy(route.get("search_region")),
            "confidence": self._safe_confidence(route.get("confidence", self._monitor_confidence)),
            "goto_index": goto_index,
            "goto_rule_id": goto_rule_id,
            "jump_enabled": jump_enabled,
            "pre_jump_recheck": bool(route.get("pre_jump_recheck", goto_index >= 0 and jump_enabled)) and goto_index >= 0 and jump_enabled,
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

    def _move_route_watch(self, idx: int, delta: int) -> None:
        target_idx = idx + delta
        if not 0 <= idx < len(self._route_watches) or not 0 <= target_idx < len(self._route_watches):
            return
        self._route_watches[idx], self._route_watches[target_idx] = self._route_watches[target_idx], self._route_watches[idx]
        remapped = set()
        for expanded in self._expanded_route_actions:
            if expanded == idx:
                remapped.add(target_idx)
            elif expanded == target_idx:
                remapped.add(idx)
            else:
                remapped.add(expanded)
        self._expanded_route_actions = remapped
        self._refresh_route_list()

    def _select_route_image(self, idx: int) -> None:
        self._open_route_images_dialog(idx)

    def _add_route_images(self, idx: int, on_change: Callable[[], None] | None = None) -> None:
        if not 0 <= idx < len(self._route_watches):
            return
        templates_dir = DATA_DIR / "templates"
        templates_dir.mkdir(parents=True, exist_ok=True)
        paths = filedialog.askopenfilenames(
            title="모니터링 이미지 선택",
            initialdir=str(templates_dir),
            filetypes=[("이미지 파일", "*.png *.jpg *.jpeg *.bmp")],
        )
        if not paths:
            return
        current = self._normalise_route_images(self._route_watches[idx])
        current_paths = {item["image"] for item in current}
        added = 0
        skipped = 0
        for path in paths:
            if len(current) >= _MAX_MONITORING_IMAGES_PER_ROUTE:
                skipped += 1
                continue
            copied = self._copy_image_to_templates(path, prefix="route")
            if copied in current_paths:
                skipped += 1
                continue
            current_paths.add(copied)
            current.append({"image": copied, "priority": len(current) + 1})
            added += 1
        self._set_route_images(idx, current)
        if skipped:
            self._set_status_text(
                f"\uc774\ubbf8\uc9c0 {added}\uac1c \ucd94\uac00, {skipped}\uac1c \uc81c\uc678 - \ucd5c\ub300 {_MAX_MONITORING_IMAGES_PER_ROUTE}\uac1c",
                COLORS["accent_text"],
            )
        elif added:
            self._set_status_text(f"이미지 {added}개 추가")
        self._refresh_route_row(idx)
        if on_change is not None:
            on_change()

    def _move_route_image(self, route_idx: int, image_idx: int, delta: int, on_change: Callable[[], None] | None = None) -> None:
        if not 0 <= route_idx < len(self._route_watches):
            return
        images = self._normalise_route_images(self._route_watches[route_idx])
        target_idx = image_idx + delta
        if not 0 <= image_idx < len(images) or not 0 <= target_idx < len(images):
            return
        images[image_idx], images[target_idx] = images[target_idx], images[image_idx]
        self._set_route_images(route_idx, images)
        self._refresh_route_row(route_idx)
        if on_change is not None:
            on_change()

    def _delete_route_image(self, route_idx: int, image_idx: int, on_change: Callable[[], None] | None = None) -> None:
        if not 0 <= route_idx < len(self._route_watches):
            return
        images = self._normalise_route_images(self._route_watches[route_idx])
        if not 0 <= image_idx < len(images):
            return
        images.pop(image_idx)
        self._set_route_images(route_idx, images)
        self._refresh_route_row(route_idx)
        if on_change is not None:
            on_change()

    def _open_route_images_dialog(self, idx: int) -> None:
        if not 0 <= idx < len(self._route_watches):
            return

        dialog = ctk.CTkToplevel(self)
        dialog.title("\ubaa8\ub2c8\ud130\ub9c1 \uc774\ubbf8\uc9c0 \uad00\ub9ac")
        dialog.geometry("780x660")
        dialog.minsize(720, 560)
        dialog.configure(fg_color=COLORS["bg_content"])
        dialog.transient(self)
        dialog.grab_set()

        dialog.update_idletasks()
        x = self.winfo_x() + max(0, (self.winfo_width() - 780) // 2)
        y = self.winfo_y() + max(0, (self.winfo_height() - 660) // 2)
        dialog.geometry(f"+{x}+{y}")

        root = ctk.CTkFrame(dialog, fg_color="transparent")
        root.pack(fill="both", expand=True, padx=18, pady=16)
        ctk.CTkLabel(
            root,
            text=f"{idx + 1}번 모니터링 이미지",
            font=self._font(18, "bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor="w")
        ctk.CTkLabel(
            root,
            text=f"같은 전용액션을 공유하는 이미지를 최대 {_MAX_MONITORING_IMAGES_PER_ROUTE}개까지 등록합니다. 위쪽 이미지가 먼저 검색됩니다.",
            font=self._font(12),
            text_color=COLORS["text_secondary"],
            wraplength=720,
            justify="left",
        ).pack(anchor="w", pady=(4, 12))

        count_var = tk.StringVar()
        list_frame = ctk.CTkScrollableFrame(root, fg_color=COLORS["bg_card"], height=430)
        list_frame.pack(fill="both", expand=True, pady=(0, 12))

        def refresh() -> None:
            for child in list_frame.winfo_children():
                child.destroy()
            images = self._normalise_route_images(self._route_watches[idx])
            count_var.set(f"\ub4f1\ub85d {len(images)}/{_MAX_MONITORING_IMAGES_PER_ROUTE}\uac1c")
            if not images:
                ctk.CTkLabel(
                    list_frame,
                    text="이미지를 추가하세요.",
                    font=self._font(12),
                    text_color=COLORS["text_muted"],
                ).pack(fill="x", pady=24)
                return

            def set_image_option(image_index: int, key: str, value, refresh_after: bool = False) -> None:
                current_images = self._normalise_route_images(self._route_watches[idx])
                if not 0 <= image_index < len(current_images):
                    return
                current_images[image_index][key] = copy.deepcopy(value)
                self._set_route_images(idx, current_images)
                if refresh_after:
                    refresh()

            def clear_image_region(image_index: int) -> None:
                current_images = self._normalise_route_images(self._route_watches[idx])
                if not 0 <= image_index < len(current_images):
                    return
                current_images[image_index].pop("search_region", None)
                self._set_route_images(idx, current_images)
                refresh()

            def open_image_region(image_index: int) -> None:
                from .analyzer_view import ScreenRegionSelector

                current_images = self._normalise_route_images(self._route_watches[idx])
                existing_region = None
                if 0 <= image_index < len(current_images):
                    existing_region = self._normalize_search_region_value(current_images[image_index].get("search_region"))

                try:
                    dialog.grab_release()
                    dialog.withdraw()
                except tk.TclError:
                    pass

                def on_region_select(x1, y1, x2, y2):
                    region = self._normalize_search_region_value([x1, y1, x2, y2])
                    if region is not None:
                        set_image_option(image_index, "search_region", region)
                    try:
                        dialog.deiconify()
                        dialog.grab_set()
                        dialog.focus_force()
                    except tk.TclError:
                        pass
                    refresh()

                def on_cancel():
                    try:
                        dialog.deiconify()
                        dialog.grab_set()
                        dialog.focus_force()
                    except tk.TclError:
                        pass

                ScreenRegionSelector(self._owner, on_region_select, on_cancel, existing_region=existing_region)

            for image_idx, item in enumerate(images):
                item_row = ctk.CTkFrame(
                    list_frame,
                    fg_color=COLORS["bg_glass"],
                    corner_radius=IOS_METRICS["control_radius_small"],
                )
                item_row.pack(fill="x", padx=8, pady=6)
                thumb = ctk.CTkLabel(
                    item_row,
                    text="IMG",
                    width=70,
                    height=48,
                    fg_color=COLORS["bg_elevated"],
                    corner_radius=IOS_METRICS["control_radius_small"],
                    text_color=COLORS["text_muted"],
                )
                thumb.pack(side="left", padx=(10, 10), pady=8)
                self._schedule_thumbnail(thumb, item.get("image"), size=(66, 44))
                info = ctk.CTkFrame(item_row, fg_color="transparent")
                info.pack(side="left", fill="x", expand=True, pady=8)
                ctk.CTkLabel(
                    info,
                    text=f"우선순위 {image_idx + 1}",
                    font=self._font(11, "bold"),
                    text_color=COLORS["accent_text"],
                    anchor="w",
                ).pack(fill="x")
                ctk.CTkLabel(
                    info,
                    text=truncate_ui_text(Path(item.get("image", "")).name, 48),
                    font=self._font(12, "bold"),
                    text_color=COLORS["text_primary"],
                    anchor="w",
                ).pack(fill="x", pady=(2, 0))
                option_row = ctk.CTkFrame(info, fg_color="transparent")
                option_row.pack(fill="x", pady=(6, 0))
                conf = self._safe_confidence(item.get("confidence", self._route_watches[idx].get("confidence", self._monitor_confidence)))
                conf_label = ctk.CTkLabel(
                    option_row,
                    text=f"{int(conf * 100)}%",
                    width=42,
                    font=self._font(10, "bold"),
                    text_color=COLORS["accent_text"],
                )
                conf_label.pack(side="left", padx=(0, 6))
                slider = ctk.CTkSlider(
                    option_row,
                    from_=0.3,
                    to=1.0,
                    number_of_steps=70,
                    width=105,
                    command=lambda value, i=image_idx, lbl=conf_label: (
                        set_image_option(i, "confidence", self._safe_confidence(value)),
                        lbl.configure(text=f"{int(float(value) * 100)}%"),
                    ),
                )
                slider.set(conf)
                slider.pack(side="left", padx=(0, 8))
                region_text = self._region_source_name(item.get("search_region")) if item.get("search_region") else "공통범위"
                self._small_button(
                    option_row,
                    f"범위:{region_text}",
                    COLORS["bg_elevated"],
                    COLORS["bg_card_hover"],
                    lambda i=image_idx: open_image_region(i),
                    width=90,
                ).pack(side="left", padx=(0, 5))
                self._small_button(
                    option_row,
                    "범위",
                    COLORS["bg_elevated"],
                    COLORS["bg_card_hover"],
                    lambda i=image_idx: clear_image_region(i),
                    width=54,
                ).pack(side="left", padx=(0, 8))
                color_var = tk.BooleanVar(value=bool(item.get("verify_image_color", False)))
                bright_var = tk.BooleanVar(value=bool(item.get("verify_image_brightness", False)))
                ctk.CTkCheckBox(
                    option_row,
                    text="색상",
                    variable=color_var,
                    command=lambda i=image_idx, v=color_var: set_image_option(i, "verify_image_color", bool(v.get())),
                    font=self._font(10, "bold"),
                    checkbox_width=16,
                    checkbox_height=16,
                ).pack(side="left", padx=(0, 8))
                ctk.CTkCheckBox(
                    option_row,
                    text="밝기",
                    variable=bright_var,
                    command=lambda i=image_idx, v=bright_var: set_image_option(i, "verify_image_brightness", bool(v.get())),
                    font=self._font(10, "bold"),
                    checkbox_width=16,
                    checkbox_height=16,
                ).pack(side="left", padx=(0, 4))
                self._small_button(
                    item_row,
                    "\u25b2",
                    COLORS["bg_elevated"],
                    COLORS["bg_card_hover"],
                    lambda i=image_idx: self._move_route_image(idx, i, -1, refresh),
                    width=40,
                ).pack(side="left", padx=(6, 4))
                self._small_button(
                    item_row,
                    "\u25bc",
                    COLORS["bg_elevated"],
                    COLORS["bg_card_hover"],
                    lambda i=image_idx: self._move_route_image(idx, i, 1, refresh),
                    width=40,
                ).pack(side="left", padx=(0, 4))
                self._small_button(
                    item_row,
                    "삭제",
                    COLORS["danger"],
                    COLORS["danger_hover"],
                    lambda i=image_idx: self._delete_route_image(idx, i, refresh),
                    width=40,
                ).pack(side="left", padx=(0, 10))

        footer = ctk.CTkFrame(root, fg_color="transparent")
        footer.pack(fill="x")
        ctk.CTkLabel(
            footer,
            textvariable=count_var,
            font=self._font(12, "bold"),
            text_color=COLORS["accent_text"],
        ).pack(side="left")
        self._small_button(
            footer,
            "+ 이미지 추가",
            COLORS["accent_blue"],
            COLORS["hover_blue"],
            lambda: self._add_route_images(idx, refresh),
            width=112,
        ).pack(side="right", padx=(8, 0))
        self._small_button(
            footer,
            "닫기",
            COLORS["bg_elevated"],
            COLORS["bg_card_hover"],
            dialog.destroy,
            width=82,
        ).pack(side="right")
        refresh()

    def _select_route_region(self, idx: int) -> None:
        if not 0 <= idx < len(self._route_watches):
            return
        self._show_region_options("route", idx)

    def _open_route_region_selector(self, idx: int) -> None:
        self._open_region_selector_for_target("route", idx)

    def _clear_route_region(self, idx: int) -> None:
        if 0 <= idx < len(self._route_watches):
            self._route_watches[idx]["search_region"] = None
            self._refresh_route_row(idx)

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
        self._refresh_route_row(idx)

    def _select_route_condition_video(self, idx: int) -> None:
        if not 0 <= idx < len(self._route_watches):
            return
        templates_dir = DATA_DIR / "templates"
        templates_dir.mkdir(parents=True, exist_ok=True)
        path = filedialog.askopenfilename(
            title="조건 동영상 선택",
            initialdir=str(templates_dir),
            filetypes=[("동영상 파일", VIDEO_FILE_PATTERNS), ("모든 파일", "*.*")],
        )
        if not path:
            return
        if not is_video_media_path(path):
            messagebox.showwarning("\uc870\uac74 \uc601\uc0c1", "\uc601\uc0c1 \ud30c\uc77c\ub9cc \uc120\ud0dd\ud558\uc138\uc694.", parent=self)
            return
        self._route_watches[idx]["condition_image"] = self._copy_image_to_templates(path, prefix="condition_video")
        self._refresh_route_row(idx)

    def _open_route_condition_settings(self, idx: int) -> None:
        if not 0 <= idx < len(self._route_watches):
            return

        dialog = ctk.CTkToplevel(self)
        dialog.title("조건 이미지 설정")
        dialog.geometry("620x580")
        dialog.minsize(580, 530)
        dialog.configure(fg_color=COLORS["bg_content"])
        dialog.transient(self)
        dialog.grab_set()

        dialog.update_idletasks()
        x = self.winfo_x() + max(0, (self.winfo_width() - 620) // 2)
        y = self.winfo_y() + max(0, (self.winfo_height() - 580) // 2)
        dialog.geometry(f"+{x}+{y}")

        route = self._route_watches[idx]
        image_var = tk.StringVar()
        region_var = tk.StringVar()
        confidence_var = tk.StringVar()
        warning_var = tk.StringVar()
        jump_mode_var = tk.StringVar(
            value="보이면 점프" if route.get("condition_jump_when_visible", False) else "안 보이면 점프"
        )
        verify_color_var = tk.BooleanVar(value=bool(route.get("condition_verify_image_color", False)))
        verify_brightness_var = tk.BooleanVar(value=bool(route.get("condition_verify_image_brightness", False)))

        root = ctk.CTkFrame(dialog, fg_color="transparent")
        root.pack(fill="both", expand=True, padx=18, pady=16)

        ctk.CTkLabel(
            root,
            text=f"{idx + 1}번 조건 이미지/동영상",
            font=self._font(18, "bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor="w")
        ctk.CTkLabel(
            root,
            text="조건 이미지/동영상이 계속 보이면 전용액션 후 점프를 보류합니다. 파일, 검색범위, 인식률을 여기서 설정하세요.",
            font=self._font(12),
            text_color=COLORS["text_secondary"],
            wraplength=560,
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
            wraplength=470,
        ).pack(fill="x", pady=(4, 0))

        def refresh_dialog() -> None:
            current = self._route_watches[idx] if 0 <= idx < len(self._route_watches) else {}
            image_path = current.get("condition_image")
            if image_path:
                kind = "\ub3d9\uc601\uc0c1" if is_video_media_path(image_path) else "\uc774\ubbf8\uc9c0"
                image_var.set(f"{kind}: {Path(image_path).name}")
            else:
                image_var.set("조건 이미지/동영상 없음")
            region_var.set(f"검색범위: {self._region_label_text(current.get('condition_search_region'))}")
            confidence = self._safe_confidence(current.get("condition_confidence", 0.8))
            confidence_var.set(f"{int(confidence * 100)}%")
            warning_var.set(self._image_quality_warning(image_path))
            self._schedule_thumbnail(preview, image_path, size=(88, 64))

        def choose_image() -> None:
            self._select_route_condition_image(idx)
            refresh_dialog()

        def choose_video() -> None:
            self._select_route_condition_video(idx)
            refresh_dialog()

        def clear_condition() -> None:
            self._clear_route_condition_image(idx)
            refresh_dialog()

        def test_condition() -> None:
            self._test_route_condition_image(idx, dialog)

        def clear_region() -> None:
            if 0 <= idx < len(self._route_watches):
                self._route_watches[idx]["condition_search_region"] = None
                self._refresh_route_row(idx)
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
        self._small_button(button_row, "동영상 입력", COLORS["accent_orange"], COLORS["confidence_amber_hover"], choose_video, width=92).pack(side="left", padx=(0, 6))
        self._small_button(button_row, "\uac80\uc0c9\ubc94\uc704", COLORS["bg_elevated"], COLORS["bg_card_hover"], open_region, width=82).pack(side="left", padx=(0, 6))
        self._small_button(button_row, "범위해제", COLORS["bg_elevated"], COLORS["bg_card_hover"], clear_region, width=82).pack(side="left", padx=(0, 6))
        self._small_button(button_row, "조건해제", COLORS["danger"], COLORS["danger_hover"], clear_condition, width=82).pack(side="left", padx=(0, 6))
        self._small_button(button_row, "조건 테스트", COLORS["success"], COLORS["green_hover"], test_condition, width=92).pack(side="left")

        confidence_row = ctk.CTkFrame(root, fg_color=COLORS["bg_card"], corner_radius=IOS_METRICS["control_radius_small"])
        confidence_row.pack(fill="x", pady=(0, 12))
        confidence_inner = ctk.CTkFrame(confidence_row, fg_color="transparent")
        confidence_inner.pack(fill="x", padx=12, pady=10)
        ctk.CTkLabel(
            confidence_inner,
            text="\uc870\uac74 \uc778\uc2dd\ub960",
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
            values=["안 보이면 점프", "보이면 점프"],
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

    def _test_route_condition_image(self, idx: int, parent=None) -> None:
        if not 0 <= idx < len(self._route_watches):
            return
        route = self._route_watches[idx]
        image_path = str(route.get("condition_image") or "").strip()
        if not image_path:
            messagebox.showwarning("\uc870\uac74 \ud14c\uc2a4\ud2b8", "\uc870\uac74 \uc774\ubbf8\uc9c0/\uc601\uc0c1\uc774 \uc124\uc815\ub418\uc9c0 \uc54a\uc558\uc2b5\ub2c8\ub2e4.", parent=parent or self)
            return
        if not Path(image_path).exists():
            messagebox.showerror("\uc870\uac74 \ud14c\uc2a4\ud2b8", f"\uc870\uac74 \ud30c\uc77c\uc774 \uc5c6\uc2b5\ub2c8\ub2e4.\n{image_path}", parent=parent or self)
            return
        ui_post = resolve_widget_ui_post(self)

        def worker() -> None:
            try:
                result = self._match_condition_image_for_test(route)
            except Exception as exc:
                logger.exception("[모니터링] 조건 이미지 테스트 오류")
                result = {"ok": False, "error": str(exc)}

            def show_result() -> None:
                try:
                    target_parent = parent if parent is not None and parent.winfo_exists() else self
                except tk.TclError:
                    target_parent = self
                if not result.get("ok"):
                    messagebox.showerror("\uc870\uac74 \ud14c\uc2a4\ud2b8", f"\ud14c\uc2a4\ud2b8 \uc2e4\ud328: {result.get('error', 'unknown error')}", parent=target_parent)
                    return
                score_pct = int(round(float(result.get("score", 0.0)) * 100))
                threshold_pct = int(round(float(result.get("threshold", 0.0)) * 100))
                found = bool(result.get("found"))
                status = "\ubc1c\uacac" if found else "\ubbf8\ubc1c\uacac"
                detail = [
                    f"결과: {status}",
                    f"인식률: {score_pct}% / 기준 {threshold_pct}%",
                    f"검색범위: {result.get('region_label', '-')}",
                ]
                if result.get("position"):
                    x, y = result["position"]
                    detail.append(f"위치: ({x}, {y})")
                if result.get("variant"):
                    detail.append(f"템플릿: {result.get('variant')}")
                if result.get("visual_failed"):
                    detail.append("추가 확인: 점수는 기준 이상이지만 색상/밝기 검증 실패")
                if result.get("verify"):
                    detail.append(f"검증옵션: {result.get('verify')}")
                messagebox.showinfo("\uc870\uac74 \ud14c\uc2a4\ud2b8", "\n".join(detail), parent=target_parent)

            ui_post(show_result)

        threading.Thread(target=worker, daemon=True).start()

    def _match_condition_image_for_test(self, route: dict) -> dict:
        image_path = str(route.get("condition_image") or "").strip()
        confidence = self._safe_confidence(route.get("condition_confidence", 0.8))
        verify_color = bool(route.get("condition_verify_image_color", False))
        verify_brightness = bool(route.get("condition_verify_image_brightness", False))
        verify_visual = verify_color or verify_brightness
        screenshot_bgr = _grab_screen_bgr()
        if screenshot_bgr is None:
            return {"ok": False, "error": "화면 캡처 실패"}

        screen_h, screen_w = screenshot_bgr.shape[:2]
        normalized_region = self._normalize_search_region_value(route.get("condition_search_region"))
        region_label = self._region_label_text(normalized_region)
        offset_x = 0
        offset_y = 0
        if normalized_region:
            x1, y1, x2, y2 = normalized_region
            x1 = max(0, min(screen_w, int(x1)))
            x2 = max(0, min(screen_w, int(x2)))
            y1 = max(0, min(screen_h, int(y1)))
            y2 = max(0, min(screen_h, int(y2)))
            if x2 <= x1 or y2 <= y1:
                return {"ok": False, "error": "조건 검색범위가 화면 밖이거나 비어 있습니다."}
            screenshot_bgr = screenshot_bgr[y1:y2, x1:x2]
            offset_x = x1
            offset_y = y1
            region_label = self._region_label_text([x1, y1, x2, y2])

        template_variants = _get_cached_template_variants(image_path)
        if not template_variants:
            return {"ok": False, "error": "조건 템플릿 로드 실패"}
        if verify_visual and not any(item[3] is not None for item in template_variants):
            return {"ok": False, "error": "색상/밝기 검증용 템플릿 로드 실패"}

        screen_gray = cv2.cvtColor(screenshot_bgr, cv2.COLOR_BGR2GRAY)
        sh, sw = screen_gray.shape[:2]
        best_score = 0.0
        best_position = None
        best_size = None
        best_variant = ""
        visual_failed = False

        for template_gray, _template_h, _template_w, template_bgr, variant_label in template_variants:
            if verify_visual and template_bgr is None:
                continue
            for scale in _MULTISCALE_FACTORS:
                scaled_template = _resize_template_gray(template_gray, scale)
                if scaled_template is None:
                    continue
                th, tw = scaled_template.shape[:2]
                if tw > sw or th > sh or tw < 4 or th < 4:
                    continue
                result = cv2.matchTemplate(screen_gray, scaled_template, cv2.TM_CCOEFF_NORMED)
                _, max_val, _, max_loc = cv2.minMaxLoc(result)
                max_val = float(max_val)
                if max_val > best_score:
                    best_score = max_val
                    best_position = (int(max_loc[0]) + tw // 2 + offset_x, int(max_loc[1]) + th // 2 + offset_y)
                    best_size = (tw, th)
                    best_variant = str(variant_label or "")

                if max_val < confidence:
                    continue
                if verify_visual:
                    candidate_points = np.argwhere(result >= confidence)
                    if candidate_points.size:
                        scores = result[candidate_points[:, 0], candidate_points[:, 1]]
                        for order in np.argsort(scores)[::-1][:25]:
                            row, col = candidate_points[order]
                            if _passes_image_visual_verification(
                                screenshot_bgr,
                                template_bgr,
                                int(col),
                                int(row),
                                tw,
                                th,
                                verify_color=verify_color,
                                verify_brightness=verify_brightness,
                            ):
                                score = float(scores[order])
                                return {
                                    "ok": True,
                                    "found": True,
                                    "score": score,
                                    "threshold": confidence,
                                    "region_label": region_label,
                                    "position": (int(col) + tw // 2 + offset_x, int(row) + th // 2 + offset_y),
                                    "verify": self._condition_verify_label(verify_color, verify_brightness),
                                    "variant": str(variant_label or ""),
                                }
                    visual_failed = True
                    continue
                return {
                    "ok": True,
                    "found": True,
                    "score": max_val,
                    "threshold": confidence,
                    "region_label": region_label,
                    "position": (int(max_loc[0]) + tw // 2 + offset_x, int(max_loc[1]) + th // 2 + offset_y),
                    "verify": "",
                    "variant": str(variant_label or ""),
                }

        return {
            "ok": True,
            "found": False,
            "score": best_score,
            "threshold": confidence,
            "region_label": region_label,
            "position": best_position if best_score > 0 else None,
            "size": best_size,
            "variant": best_variant,
            "visual_failed": visual_failed,
            "verify": self._condition_verify_label(verify_color, verify_brightness),
        }

    @staticmethod
    def _condition_verify_label(verify_color: bool, verify_brightness: bool) -> str:
        parts = []
        if verify_color:
            parts.append("색상")
        if verify_brightness:
            parts.append("밝기")
        return "/".join(parts)

    def _clear_route_condition_image(self, idx: int) -> None:
        if 0 <= idx < len(self._route_watches):
            self._route_watches[idx]["condition_image"] = None
            self._route_watches[idx]["condition_search_region"] = None
            self._route_watches[idx]["condition_confidence"] = 0.8
            self._route_watches[idx]["condition_jump_when_visible"] = False
            self._route_watches[idx]["condition_verify_image_color"] = False
            self._route_watches[idx]["condition_verify_image_brightness"] = False
            self._refresh_route_row(idx)

    def _clear_route_actions(self, idx: int) -> None:
        if not 0 <= idx < len(self._route_watches):
            return
        if not self._route_watches[idx].get("monitor_actions"):
            return
        if not messagebox.askyesno("전용 액션 삭제", f"{idx + 1}번 전용 액션을 모두 삭제할까요?", parent=self):
            return
        self._route_watches[idx]["monitor_actions"] = []
        self._expanded_route_actions.discard(idx)
        self._refresh_route_row(idx)

    def _set_route_jump_target(self, idx: int, option: dict) -> None:
        if not 0 <= idx < len(self._route_watches):
            return
        target_index = int(option.get("index", -1))
        if target_index < 0:
            return
        self._route_watches[idx]["goto_index"] = target_index
        self._route_watches[idx]["goto_rule_id"] = str(option.get("rule_id") or "")
        self._route_watches[idx]["goto_step"] = str(option.get("step") or "")
        self._route_watches[idx]["pre_jump_recheck"] = True
        self._refresh_route_row(idx)

    def _open_jump_target_dialog(self, idx: int) -> None:
        if not 0 <= idx < len(self._route_watches):
            return
        dialog = ctk.CTkToplevel(self)
        dialog.title("점프 액션 선택")
        dialog.geometry("800x660")
        dialog.minsize(720, 560)
        dialog.configure(fg_color=COLORS["bg_content"])
        dialog.transient(self)
        dialog.grab_set()

        dialog.update_idletasks()
        x = self.winfo_x() + max(0, (self.winfo_width() - 800) // 2)
        y = self.winfo_y() + max(0, (self.winfo_height() - 660) // 2)
        dialog.geometry(f"+{x}+{y}")

        expanded: set[str] = set()
        current_rule_id = str(self._route_watches[idx].get("goto_rule_id") or "")
        list_frame = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
        list_frame.pack(fill="both", expand=True, padx=18, pady=(16, 10))

        ctk.CTkLabel(
            list_frame,
            text="점프할 액션 선택",
            font=self._font(18, "bold"),
            text_color=COLORS["text_primary"],
            anchor="w",
        ).pack(fill="x", pady=(0, 4))
        ctk.CTkLabel(
            list_frame,
            text="상위 액션은 기본 접힘입니다. ▶ 버튼을 눌러 하위액션을 펼친 뒤 바로 선택할 수 있습니다.",
            font=self._font(12),
            text_color=COLORS["text_secondary"],
            anchor="w",
            wraplength=720,
            justify="left",
        ).pack(fill="x", pady=(0, 12))

        rows_frame = ctk.CTkFrame(list_frame, fg_color="transparent")
        rows_frame.pack(fill="both", expand=True)

        def child_options(parent_option: dict) -> list[dict]:
            parent_step = str(parent_option.get("step") or "")
            parent_depth = int(parent_option.get("depth", 0))
            prefix = f"{parent_step}-"
            result = []
            for option in self._action_options:
                if option.get("index", -1) < 0:
                    continue
                step = str(option.get("step") or "")
                depth = int(option.get("depth", 0))
                if step.startswith(prefix) and depth == parent_depth + 1:
                    result.append(option)
            return result

        def select_option(option: dict) -> None:
            self._set_route_jump_target(idx, option)
            try:
                dialog.destroy()
            except tk.TclError:
                pass

        def render() -> None:
            for child in rows_frame.winfo_children():
                child.destroy()
            for option in self._action_options:
                if option.get("index", -1) < 0 or option.get("depth", 0) != 0:
                    continue
                children = child_options(option)
                option_key = str(option.get("rule_id") or option.get("step"))
                row = ctk.CTkFrame(
                    rows_frame,
                    fg_color=COLORS["bg_glass"],
                    corner_radius=IOS_METRICS["control_radius_small"],
                )
                row.pack(fill="x", pady=5)
                if children:
                    self._small_button(
                        row,
                        "\u25be" if option_key in expanded else "\u25b8",
                        COLORS["bg_elevated"],
                        COLORS["bg_card_hover"],
                        lambda key=option_key: (expanded.remove(key) if key in expanded else expanded.add(key), render()),
                        width=36,
                    ).pack(side="left", padx=(10, 8), pady=8)
                else:
                    ctk.CTkLabel(row, text="", width=36).pack(side="left", padx=(10, 8), pady=8)
                selected = current_rule_id and option.get("rule_id") == current_rule_id
                ctk.CTkLabel(
                    row,
                    text=truncate_ui_text(option.get("label", ""), 70),
                    font=self._font(12, "bold"),
                    text_color=COLORS["accent_text"] if selected else COLORS["text_primary"],
                    anchor="w",
                ).pack(side="left", fill="x", expand=True, pady=8)
                self._small_button(
                    row,
                    "선택",
                    COLORS["success"] if selected else COLORS["accent_blue"],
                    COLORS["green_hover"] if selected else COLORS["hover_blue"],
                    lambda opt=option: select_option(opt),
                    width=60,
                ).pack(side="left", padx=(8, 10), pady=8)

                if option_key in expanded:
                    for child_option in children:
                        child_row = ctk.CTkFrame(
                            rows_frame,
                            fg_color=COLORS["bg_card"],
                            corner_radius=IOS_METRICS["control_radius_small"],
                        )
                        child_row.pack(fill="x", padx=(42, 0), pady=3)
                        child_selected = current_rule_id and child_option.get("rule_id") == current_rule_id
                        ctk.CTkLabel(
                            child_row,
                            text="\u2514",
                            width=24,
                            font=self._font(12, "bold"),
                            text_color=COLORS["text_muted"],
                        ).pack(side="left", padx=(10, 6), pady=7)
                        ctk.CTkLabel(
                            child_row,
                            text=truncate_ui_text(child_option.get("label", "").strip(), 68),
                            font=self._font(12, "bold"),
                            text_color=COLORS["accent_text"] if child_selected else COLORS["text_primary"],
                            anchor="w",
                        ).pack(side="left", fill="x", expand=True, pady=7)
                        self._small_button(
                            child_row,
                            "선택",
                            COLORS["success"] if child_selected else COLORS["accent_blue"],
                            COLORS["green_hover"] if child_selected else COLORS["hover_blue"],
                            lambda opt=child_option: select_option(opt),
                            width=60,
                        ).pack(side="left", padx=(8, 10), pady=7)

        footer = ctk.CTkFrame(dialog, fg_color="transparent")
        footer.pack(fill="x", padx=18, pady=(0, 16))
        self._small_button(
            footer,
            "닫기",
            COLORS["bg_elevated"],
            COLORS["bg_card_hover"],
            dialog.destroy,
            width=90,
        ).pack(side="right")
        render()

    def _on_route_jump_enabled_changed(self, idx: int, value: str) -> None:
        if 0 <= idx < len(self._route_watches):
            enabled = value == "활성"
            self._route_watches[idx]["jump_enabled"] = enabled
            if not enabled:
                self._route_watches[idx]["pre_jump_recheck"] = False
            elif self._watch_goto_index(self._route_watches[idx]) >= 0:
                self._route_watches[idx].setdefault("pre_jump_recheck", True)

    def _on_route_pre_jump_recheck_changed(self, idx: int, value: str) -> None:
        if 0 <= idx < len(self._route_watches):
            route = self._route_watches[idx]
            can_jump = self._watch_goto_index(route) >= 0 and bool(route.get("jump_enabled", True))
            route["pre_jump_recheck"] = bool(can_jump and value == "ON")

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
            self._route_watches[idx]["condition_jump_when_visible"] = value == "보이면 점프"

    def _schedule_render_callback(self, callback, delay_ms: int = _MONITORING_RENDER_BATCH_DELAY_MS) -> None:
        if self._closed:
            return
        holder = {}

        def _run() -> None:
            after_id = holder.get("id")
            if after_id is not None:
                self._render_after_ids.discard(after_id)
            if not self._closed:
                callback()

        try:
            after_id = self.after(max(0, int(delay_ms)), _run)
        except (tk.TclError, RuntimeError):
            return
        holder["id"] = after_id
        self._render_after_ids.add(after_id)

    def _refresh_route_list(self) -> None:
        self._update_route_count_label()
        if self._routes_frame is None:
            return
        self._route_render_generation += 1
        generation = self._route_render_generation
        self._route_slots.clear()
        self._route_action_slots.clear()
        self._route_action_preview_hosts.clear()
        self._route_jump_rows.clear()
        self._route_action_toggle_buttons.clear()
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

        end_idx = min(len(self._route_watches), _MONITORING_ROUTE_RENDER_BATCH_SIZE)
        for idx in range(end_idx):
            self._append_route_slot(idx)
        if end_idx < len(self._route_watches):
            self._schedule_render_callback(
                lambda: self._render_route_batch(end_idx, generation)
            )

    def _render_route_batch(self, start_idx: int, generation: int) -> None:
        if generation != self._route_render_generation:
            return
        if self._routes_frame is None or not self._widget_exists(self._routes_frame):
            return
        end_idx = min(len(self._route_watches), start_idx + _MONITORING_ROUTE_RENDER_BATCH_SIZE)
        for idx in range(start_idx, end_idx):
            if idx not in self._route_slots:
                self._append_route_slot(idx)
        if end_idx < len(self._route_watches):
            self._schedule_render_callback(
                lambda: self._render_route_batch(end_idx, generation)
            )

    def _append_route_slot(self, idx: int) -> None:
        if self._routes_frame is None or not 0 <= idx < len(self._route_watches):
            return
        slot = ctk.CTkFrame(self._routes_frame, fg_color="transparent")
        slot.pack(fill="x")
        self._route_slots[idx] = slot
        self._build_route_row(idx, self._route_watches[idx], parent=slot)
        if idx < len(self._route_watches) - 1:
            self._build_route_separator(parent=slot)

    def _update_route_count_label(self) -> None:
        if self._route_count_label is not None:
            image_count = sum(len(self._normalise_route_images(route)) for route in self._route_watches)
            self._route_count_label.configure(text=f"\ub4f1\ub85d {len(self._route_watches)}\uac1c / \uc774\ubbf8\uc9c0 {image_count}\uac1c")

    def _refresh_route_row(self, idx: int) -> None:
        self._update_route_count_label()
        if not 0 <= idx < len(self._route_watches):
            self._refresh_route_list()
            return
        slot = self._route_slots.get(idx)
        if slot is None or not getattr(slot, "winfo_exists", lambda: False)():
            self._refresh_route_list()
            return
        self._route_action_slots = {
            key: value
            for key, value in self._route_action_slots.items()
            if key[0] != idx
        }
        self._route_action_preview_hosts.pop(idx, None)
        self._route_jump_rows.pop(idx, None)
        self._route_action_toggle_buttons.pop(idx, None)
        for child in slot.winfo_children():
            child.destroy()
        self._build_route_row(idx, self._route_watches[idx], parent=slot)
        if idx < len(self._route_watches) - 1:
            self._build_route_separator(parent=slot)

    def _build_route_separator(self, parent=None) -> None:
        separator = ctk.CTkFrame(
            parent or self._routes_frame,
            height=2,
            fg_color=COLORS["accent"],
            corner_radius=1,
        )
        separator.pack(fill="x", padx=16, pady=(8, 10))
        separator.pack_propagate(False)

    def _build_route_row(self, idx: int, route: dict, parent=None) -> None:
        row = ctk.CTkFrame(parent or self._routes_frame, fg_color=COLORS["bg_glass"], corner_radius=IOS_METRICS["control_radius_small"])
        row.pack(fill="x", pady=4)
        inner = ctk.CTkFrame(row, fg_color="transparent")
        inner.pack(fill="x", padx=10, pady=8)

        route_images = self._normalise_route_images(route)
        image_path = route_images[0]["image"] if route_images else route.get("image")
        image_name = Path(image_path).name if image_path else "이미지 없음"
        image_label_text = truncate_ui_text(image_name, 42)
        image_count_text = f"+{len(route_images) - 1}\uac1c" if len(route_images) > 1 else ""
        if image_count_text:
            image_label_text = truncate_ui_text(f"{image_label_text} {image_count_text}", 42)
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
        condition_media_kind = "\ub3d9\uc601\uc0c1" if is_video_media_path(condition_image) else "\uc774\ubbf8\uc9c0"
        condition_text = "\uc870\uac74 \uc774\ubbf8\uc9c0 \uc0ac\ubcf8\ucc98 \ud544\uc694" if condition_warning else (
            f"{condition_mode_text}{condition_verify_text}: {condition_media_kind} {Path(condition_image).name}"
            if condition_image else "\uc870\uac74 \uc5c6\uc74c"
        )

        can_pre_jump_recheck = self._watch_goto_index(route) >= 0 and bool(route.get("jump_enabled", True))
        if not can_pre_jump_recheck:
            route["pre_jump_recheck"] = False
        elif "pre_jump_recheck" not in route:
            route["pre_jump_recheck"] = True
        pre_jump_recheck_enabled = bool(route.get("pre_jump_recheck", False)) and can_pre_jump_recheck

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
            text=image_label_text,
            anchor="w",
            font=self._font(12, "bold"),
            text_color=COLORS["text_primary"] if image_path else COLORS["text_muted"],
        ).pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._small_button(top, "\uc774\ubbf8\uc9c0", COLORS["accent_blue"], COLORS["hover_blue"], lambda i=idx: self._select_route_image(i), width=78).pack(side="left", padx=(0, 5))
        self._small_button(top, "범위", COLORS["bg_elevated"], COLORS["bg_card_hover"], lambda i=idx: self._select_route_region(i), width=46).pack(side="left", padx=(0, 5))
        self._small_button(top, "\u25b2", COLORS["bg_elevated"], COLORS["bg_card_hover"], lambda i=idx: self._move_route_watch(i, -1), width=34).pack(side="left", padx=(0, 4))
        self._small_button(top, "\u25bc", COLORS["bg_elevated"], COLORS["bg_card_hover"], lambda i=idx: self._move_route_watch(i, 1), width=34).pack(side="left", padx=(0, 4))
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
            text="\uc778\uc2dd\ub960",
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
            text=f"\uc804\uc6a9\uc561\uc158 {len(monitor_actions)}\uac1c",
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
        self._route_action_toggle_buttons[idx] = action_row.winfo_children()[-1]
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
        ctk.CTkLabel(
            action_row,
            text="\uc810\ud504\uc804 \uc7ac\ud655\uc778",
            width=82,
            anchor="w",
            font=self._font(10, "bold"),
            text_color=COLORS["text_secondary"],
        ).pack(side="left", padx=(0, 4))
        pre_jump_recheck_var = tk.StringVar(value="ON" if pre_jump_recheck_enabled else "OFF")

        def update_pre_jump_recheck(value: str, button=None, route_index: int = idx) -> None:
            self._on_route_pre_jump_recheck_changed(route_index, value)
            if button is not None and 0 <= route_index < len(self._route_watches):
                enabled = bool(self._route_watches[route_index].get("pre_jump_recheck", False))
                button.configure(
                    selected_color=COLORS["success"] if enabled else COLORS["danger"],
                    selected_hover_color=COLORS["green_hover"] if enabled else COLORS["danger_hover"],
                )

        pre_jump_recheck_toggle = ctk.CTkSegmentedButton(
            action_row,
            values=["ON", "OFF"],
            variable=pre_jump_recheck_var,
            width=76,
            height=28,
            fg_color=COLORS["bg_elevated"],
            selected_color=COLORS["success"] if pre_jump_recheck_enabled else COLORS["danger"],
            selected_hover_color=COLORS["green_hover"] if pre_jump_recheck_enabled else COLORS["danger_hover"],
            unselected_color=COLORS["bg_elevated"],
            unselected_hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_primary"],
            font=self._font(10, "bold"),
        )
        pre_jump_recheck_toggle.configure(
            command=lambda value, button=pre_jump_recheck_toggle: update_pre_jump_recheck(value, button)
        )
        if not can_pre_jump_recheck:
            pre_jump_recheck_toggle.configure(state="disabled")
        pre_jump_recheck_toggle.pack(side="left", padx=(0, 8))
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

        preview_host = ctk.CTkFrame(inner, fg_color="transparent")
        self._route_action_preview_hosts[idx] = preview_host

        jump_row = ctk.CTkFrame(inner, fg_color="transparent")
        jump_row.pack(fill="x", pady=(7, 0))
        self._route_jump_rows[idx] = jump_row
        if idx in self._expanded_route_actions:
            preview_host.pack(fill="x", before=jump_row)
            self._build_route_actions_preview(preview_host, idx, monitor_actions)
        ctk.CTkLabel(
            jump_row,
            text="점프액션",
            width=108,
            anchor="w",
            font=self._font(11, "bold"),
            text_color=COLORS["accent_text"],
        ).pack(side="left", padx=(40, 8))

        ctk.CTkLabel(
            jump_row,
            text=truncate_ui_text(self._action_label_for_route(route), 58),
            height=30,
            font=self._font(11),
            fg_color=COLORS["bg_elevated"],
            text_color=COLORS["text_primary"],
            corner_radius=IOS_METRICS["control_radius_small"],
            anchor="w",
        ).pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._small_button(
            jump_row,
            "선택",
            COLORS["accent_blue"],
            COLORS["hover_blue"],
            lambda i=idx: self._open_jump_target_dialog(i),
            width=60,
        ).pack(side="left", padx=(0, 8))
        jump_enabled_var = tk.StringVar(value="\ud65c\uc131" if route.get("jump_enabled", True) else "\ube44\ud65c\uc131")

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
            values=["\ud65c\uc131", "\ube44\ud65c\uc131"],
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
        host = self._route_action_preview_hosts.get(idx)
        jump_row = self._route_jump_rows.get(idx)
        button = self._route_action_toggle_buttons.get(idx)
        if idx in self._expanded_route_actions:
            self._expanded_route_actions.discard(idx)
            if host is not None and self._widget_exists(host):
                for child in host.winfo_children():
                    child.destroy()
                host.pack_forget()
                self._route_action_slots = {
                    key: value
                    for key, value in self._route_action_slots.items()
                    if key[0] != idx
                }
                if button is not None and self._widget_exists(button):
                    button.configure(text="보기")
                return
        else:
            self._expanded_route_actions.add(idx)
            if (
                host is not None
                and self._widget_exists(host)
                and jump_row is not None
                and self._widget_exists(jump_row)
            ):
                for child in host.winfo_children():
                    child.destroy()
                if not host.winfo_ismapped():
                    host.pack(fill="x", before=jump_row)
                self._build_route_actions_preview(host, idx, self._route_watches[idx].get("monitor_actions", []) or [])
                if button is not None and self._widget_exists(button):
                    button.configure(text="접기")
                return
        self._refresh_route_row(idx)

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
                text="전용액션 없음: + 추가를 눌러 이 이미지가 감지되었을 때 먼저 실행할 액션을 등록하세요.",
                font=self._font(10),
                text_color=COLORS["text_muted"],
                anchor="w",
            ).pack(fill="x", padx=10, pady=8)
            return

        for action_idx, action in enumerate(actions[:_MONITORING_ACTION_RENDER_BATCH_SIZE]):
            slot = ctk.CTkFrame(preview, fg_color="transparent")
            slot.pack(fill="x")
            self._route_action_slots[(route_idx, action_idx)] = slot
            item = ctk.CTkFrame(slot, fg_color="transparent")
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
                "\u25bc",
                COLORS["bg_elevated"],
                COLORS["bg_card_hover"],
                lambda r=route_idx, a=action_idx: self._move_route_action(r, a, 1),
                width=30,
            ).pack(side="right", padx=(5, 0))
            self._small_button(
                item,
                "\u25b2",
                COLORS["bg_elevated"],
                COLORS["bg_card_hover"],
                lambda r=route_idx, a=action_idx: self._move_route_action(r, a, -1),
                width=30,
            ).pack(side="right", padx=(5, 0))
            self._small_button(
                item,
                "\ud14c\uc2a4\ud2b8",
                COLORS["accent_orange"],
                COLORS["confidence_amber_hover"],
                lambda r=route_idx, a=action_idx: self._test_route_action(r, a),
                width=52,
            ).pack(side="right", padx=(5, 0))
            if action_idx < len(actions) - 1:
                separator = ctk.CTkFrame(
                    slot,
                    height=2,
                    fg_color=COLORS["accent"],
                    corner_radius=1,
                )
                separator.pack(fill="x", padx=10, pady=(4, 0))
                separator.pack_propagate(False)
        if len(actions) > _MONITORING_ACTION_RENDER_BATCH_SIZE:
            self._schedule_render_callback(
                lambda: self._render_route_action_batch(
                    preview,
                    route_idx,
                    actions,
                    _MONITORING_ACTION_RENDER_BATCH_SIZE,
                ),
            )

    def _render_route_action_batch(self, preview, route_idx: int, actions: list[dict], start_idx: int) -> None:
        if not self._widget_exists(preview):
            return
        if not 0 <= route_idx < len(self._route_watches):
            return
        current_actions = self._route_watches[route_idx].get("monitor_actions", []) or []
        if current_actions is not actions:
            return

        end_idx = min(len(actions), start_idx + _MONITORING_ACTION_RENDER_BATCH_SIZE)
        for action_idx in range(start_idx, end_idx):
            self._append_route_action_slot(preview, route_idx, action_idx, actions[action_idx], len(actions))

        if end_idx < len(actions):
            self._schedule_render_callback(
                lambda: self._render_route_action_batch(preview, route_idx, actions, end_idx),
            )

    def _append_route_action_slot(self, preview, route_idx: int, action_idx: int, action: dict, total_actions: int) -> None:
        if not self._widget_exists(preview):
            return
        slot = ctk.CTkFrame(preview, fg_color="transparent")
        slot.pack(fill="x")
        self._route_action_slots[(route_idx, action_idx)] = slot
        self._build_route_action_content(slot, route_idx, action_idx, action, total_actions)

    def _build_route_action_content(
        self,
        slot,
        route_idx: int,
        action_idx: int,
        action: dict,
        total_actions: int,
    ) -> None:
        item = ctk.CTkFrame(slot, fg_color="transparent")
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
            "\u25bc",
            COLORS["bg_elevated"],
            COLORS["bg_card_hover"],
            lambda r=route_idx, a=action_idx: self._move_route_action(r, a, 1),
            width=30,
        ).pack(side="right", padx=(5, 0))
        self._small_button(
            item,
            "\u25b2",
            COLORS["bg_elevated"],
            COLORS["bg_card_hover"],
            lambda r=route_idx, a=action_idx: self._move_route_action(r, a, -1),
            width=30,
        ).pack(side="right", padx=(5, 0))
        self._small_button(
            item,
            "\ud14c\uc2a4\ud2b8",
            COLORS["accent_orange"],
            COLORS["confidence_amber_hover"],
            lambda r=route_idx, a=action_idx: self._test_route_action(r, a),
            width=52,
        ).pack(side="right", padx=(5, 0))
        if action_idx < total_actions - 1:
            separator = ctk.CTkFrame(
                slot,
                height=2,
                fg_color=COLORS["accent"],
                corner_radius=1,
            )
            separator.pack(fill="x", padx=10, pady=(4, 0))
            separator.pack_propagate(False)

    def _refresh_route_action_slot(self, route_idx: int, action_idx: int) -> bool:
        if not 0 <= route_idx < len(self._route_watches):
            return False
        actions = self._route_watches[route_idx].get("monitor_actions", []) or []
        if not 0 <= action_idx < len(actions):
            return False
        slot = self._route_action_slots.get((route_idx, action_idx))
        if slot is None or not self._widget_exists(slot):
            return False

        for child in slot.winfo_children():
            child.destroy()

        action = actions[action_idx]
        item = ctk.CTkFrame(slot, fg_color="transparent")
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
            "\u25bc",
            COLORS["bg_elevated"],
            COLORS["bg_card_hover"],
            lambda r=route_idx, a=action_idx: self._move_route_action(r, a, 1),
            width=30,
        ).pack(side="right", padx=(5, 0))
        self._small_button(
            item,
            "\u25b2",
            COLORS["bg_elevated"],
            COLORS["bg_card_hover"],
            lambda r=route_idx, a=action_idx: self._move_route_action(r, a, -1),
            width=30,
        ).pack(side="right", padx=(5, 0))
        self._small_button(
            item,
            "\ud14c\uc2a4\ud2b8",
            COLORS["accent_orange"],
            COLORS["confidence_amber_hover"],
            lambda r=route_idx, a=action_idx: self._test_route_action(r, a),
            width=52,
        ).pack(side="right", padx=(5, 0))
        if action_idx < len(actions) - 1:
            separator = ctk.CTkFrame(
                slot,
                height=2,
                fg_color=COLORS["accent"],
                corner_radius=1,
            )
            separator.pack(fill="x", padx=10, pady=(4, 0))
            separator.pack_propagate(False)
        return True

    def _build_monitor_action_thumbnail(self, parent, action: dict) -> None:
        image_path = action.get("image") if action.get("type") in MonitorActionEditorDialog.MEDIA_CLICK_TYPES else None
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
            self._refresh_route_row(route_idx)

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
                if not self._refresh_route_action_slot(route_idx, action_idx):
                    self._refresh_route_row(route_idx)

        MonitorActionEditorDialog(self, actions[action_idx], on_save)

    def _delete_route_action(self, route_idx: int, action_idx: int) -> None:
        if not 0 <= route_idx < len(self._route_watches):
            return
        actions = self._route_watches[route_idx].get("monitor_actions", []) or []
        if 0 <= action_idx < len(actions):
            actions.pop(action_idx)
            self._expanded_route_actions.add(route_idx)
            self._refresh_route_row(route_idx)

    def _move_route_action(self, route_idx: int, action_idx: int, delta: int) -> None:
        if not 0 <= route_idx < len(self._route_watches):
            return
        actions = self._route_watches[route_idx].get("monitor_actions", []) or []
        new_idx = action_idx + delta
        if 0 <= action_idx < len(actions) and 0 <= new_idx < len(actions):
            actions[action_idx], actions[new_idx] = actions[new_idx], actions[action_idx]
            self._expanded_route_actions.add(route_idx)
            updated = (
                self._refresh_route_action_slot(route_idx, action_idx)
                and self._refresh_route_action_slot(route_idx, new_idx)
            )
            if not updated:
                self._refresh_route_row(route_idx)

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
            height=30,
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
        if action_type in {"키 입력", MonitorActionEditorDialog.RANDOM_KEY_TYPE}:
            return COLORS["accent_orange"]
        if action_type in {
            "마우스 클릭",
            "이미지 클릭",
            MonitorActionEditorDialog.VIDEO_CLICK_TYPE,
            MonitorActionEditorDialog.LEGACY_VIDEO_CLICK_TYPE,
        }:
            return COLORS["accent_blue"]
        if action_type == "\ub4dc\ub798\uadf8":
            return COLORS["warning"]
        return COLORS["text_secondary"]

    @staticmethod
    def _action_detail(action: dict) -> str:
        action_type = action.get("type", "")
        if action_type == "텍스트 입력":
            return f'"{str(action.get("text", ""))[:24]}"'
        if action_type == "키 입력":
            keys = action.get("keys", []) or []
            return format_key_combo([str(k).lower().strip() for k in keys if str(k).strip()]) or "\uae30\ub85d \uc5c6\uc74c"
        if action_type == MonitorActionEditorDialog.RANDOM_KEY_TYPE:
            return format_random_key_sequences_summary(action.get("random_key_sequences", []))
        if action_type == "마우스 클릭":
            return f"({action.get('x', 0)}, {action.get('y', 0)})"
        if action_type in {"이미지 클릭", MonitorActionEditorDialog.VIDEO_CLICK_TYPE, MonitorActionEditorDialog.LEGACY_VIDEO_CLICK_TYPE}:
            return Path(str(action.get("image", ""))).name if action.get("image") else "이미지 없음"
        if action_type == "\uc2a4\ud06c\ub864":
            return str(action.get("amount", 0))
        if action_type == "\ub4dc\ub798\uadf8":
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
        if action_type in {"이미지 클릭", MonitorActionEditorDialog.VIDEO_CLICK_TYPE, MonitorActionEditorDialog.LEGACY_VIDEO_CLICK_TYPE}:
            confidence = action.get("confidence")
            if confidence:
                parts.append(f"인식 {int(float(confidence) * 100)}%")
            if action.get("search_region"):
                parts.append("범위")
            if action.get("verify_image_color"):
                parts.append("색상")
            if action.get("verify_image_brightness"):
                parts.append("밝기")
            if action.get("skip_on_not_found"):
                parts.append("스킵")
            if action.get("click_until_image_disappears"):
                parts.append("사라질때까지")
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
            images = self._normalise_route_images(route)
            image = images[0]["image"] if images else route.get("image")
            goto_index = self._watch_goto_index(route)
            if not image and goto_index < 0:
                continue
            if not image:
                messagebox.showerror("설정 필요", f"모니터링 이미지 액션 {idx}번의 이미지를 선택하세요.", parent=self)
                return
            if goto_index < 0:
                messagebox.showerror("설정 필요", f"모니터링 이미지 액션 {idx}번의 이동 대상 액션을 선택하세요.", parent=self)
                return
            jump_enabled = bool(route.get("jump_enabled", True))
            pre_jump_recheck = (
                bool(route.get("pre_jump_recheck", goto_index >= 0 and jump_enabled))
                and goto_index >= 0
                and jump_enabled
            )
            valid_watches.append(
                {
                    "image": image,
                    "images": copy.deepcopy(images),
                    "search_region": copy.deepcopy(route.get("search_region")),
                    "confidence": self._safe_confidence(route.get("confidence", self._monitor_confidence)),
                    "goto_index": goto_index,
                    "goto_rule_id": str(route.get("goto_rule_id") or ""),
                    "goto_step": str(route.get("goto_step") or ""),
                    "jump_enabled": jump_enabled,
                    "pre_jump_recheck": pre_jump_recheck,
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

    def destroy(self) -> None:
        if getattr(self, "_closed", False):
            return
        self._closed = True
        self._route_render_generation += 1
        for after_id in list(getattr(self, "_render_after_ids", set())):
            try:
                self.after_cancel(after_id)
            except (tk.TclError, RuntimeError, ValueError):
                pass
        self._render_after_ids.clear()
        self._pending_thumbnail_labels.clear()
        self._route_slots.clear()
        self._route_action_slots.clear()
        self._route_action_preview_hosts.clear()
        self._route_jump_rows.clear()
        self._route_action_toggle_buttons.clear()
        try:
            self.grab_release()
        except (tk.TclError, RuntimeError):
            pass
        super().destroy()
