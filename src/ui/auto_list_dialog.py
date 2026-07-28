"""Configuration dialog for the general-purpose automatic list action."""

from __future__ import annotations

import copy
import ctypes
import shutil
import time
import uuid
from pathlib import Path

import customtkinter as ctk
import cv2
import numpy as np
from PIL import Image, ImageGrab
from tkinter import filedialog, messagebox

from ..utils.auto_list import (
    AUTO_LIST_MODE_TARGET,
    AUTO_LIST_MODE_UNTIL_EXHAUSTED,
    AUTO_LIST_MAX_ITEMS,
    classify_colour_state,
    crop_bgr_region,
    normalize_auto_list_config,
    set_auto_list_item_search_region,
    translate_screen_region,
)
from ..utils.config import DATA_DIR
from .theme import COLORS, IOS_FONTS, IOS_METRICS


class AutoListDialog(ctk.CTkToplevel):
    """Edit ordered image items and the shared quantity/color decision settings."""

    def __init__(self, parent, *, config=None):
        super().__init__(parent)
        self._result = None
        self._config = normalize_auto_list_config(config)
        self._selected_index = 0 if self._config["items"] else -1
        self._item_photos = []

        self.title("자동 목록 처리 설정")
        self.geometry("1040x730")
        self.minsize(920, 650)
        self.configure(fg_color=COLORS["bg_content"])
        self.transient(parent)
        self.grab_set()

        self._build_ui()
        self._refresh_items()
        self._load_selected_item()
        self.after(50, self.focus_force)

    def _font(self, size: int, weight: str = "normal") -> ctk.CTkFont:
        return ctk.CTkFont(family=IOS_FONTS["family"], size=size, weight=weight)

    def _card(self, parent):
        return ctk.CTkFrame(
            parent,
            fg_color=COLORS["bg_glass"],
            corner_radius=IOS_METRICS["card_radius_compact"],
            border_width=IOS_METRICS["card_border_width"],
            border_color=COLORS["border"],
        )

    def _button(self, parent, text, command, color=None, hover=None, width=90):
        return ctk.CTkButton(
            parent,
            text=text,
            command=command,
            width=width,
            height=32,
            fg_color=color or COLORS["bg_card"],
            hover_color=hover or COLORS["bg_card_hover"],
            text_color=COLORS["text_on_accent"] if color else COLORS["text_primary"],
            font=self._font(12, "bold"),
            corner_radius=IOS_METRICS["control_radius_small"],
        )

    def _build_ui(self):
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=18, pady=(16, 8))
        ctk.CTkLabel(
            header,
            text="자동 목록 처리",
            font=self._font(21, "bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor="w")
        ctk.CTkLabel(
            header,
            text="항목을 위에서부터 찾고, 가능한 가장 큰 값을 선택한 뒤 이 액션의 하위 액션을 실행합니다.",
            font=self._font(12),
            text_color=COLORS["text_secondary"],
        ).pack(anchor="w", pady=(4, 0))

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=18, pady=(0, 10))

        left = self._card(body)
        left.pack(side="left", fill="both", expand=True, padx=(0, 7))
        right = self._card(body)
        right.pack(side="left", fill="both", expand=True, padx=(7, 0))

        left_header = ctk.CTkFrame(left, fg_color="transparent")
        left_header.pack(fill="x", padx=12, pady=(12, 7))
        ctk.CTkLabel(
            left_header,
            text="처리 항목과 순서",
            font=self._font(15, "bold"),
            text_color=COLORS["accent_text"],
        ).pack(side="left")
        self._button(
            left_header,
            "+ 항목 추가",
            self._add_item,
            COLORS["accent_blue"],
            COLORS["hover_blue"],
            105,
        ).pack(side="right")

        self._item_list = ctk.CTkScrollableFrame(left, fg_color="transparent")
        self._item_list.pack(fill="both", expand=True, padx=10, pady=(0, 8))

        item_editor = ctk.CTkFrame(left, fg_color=COLORS["bg_card"], corner_radius=12)
        item_editor.pack(fill="x", padx=10, pady=(0, 10))
        ctk.CTkLabel(item_editor, text="선택 항목", font=self._font(13, "bold"), text_color=COLORS["text_primary"]).grid(
            row=0, column=0, columnspan=4, sticky="w", padx=10, pady=(9, 5)
        )
        self._item_name_var = ctk.StringVar()
        self._target_count_var = ctk.StringVar(value="1")
        self._confidence_var = ctk.StringVar(value="80")
        self._enabled_var = ctk.BooleanVar(value=True)
        ctk.CTkLabel(item_editor, text="이름", font=self._font(11), text_color=COLORS["text_secondary"]).grid(row=1, column=0, sticky="w", padx=(10, 5))
        ctk.CTkEntry(item_editor, textvariable=self._item_name_var, height=30).grid(row=1, column=1, columnspan=3, sticky="ew", padx=(0, 10), pady=3)
        ctk.CTkLabel(item_editor, text="목표 수량", font=self._font(11), text_color=COLORS["text_secondary"]).grid(row=2, column=0, sticky="w", padx=(10, 5))
        self._target_count_entry = ctk.CTkEntry(
            item_editor,
            textvariable=self._target_count_var,
            width=70,
            height=30,
        )
        self._target_count_entry.grid(row=2, column=1, sticky="w", pady=3)
        ctk.CTkLabel(item_editor, text="인식률 %", font=self._font(11), text_color=COLORS["text_secondary"]).grid(row=2, column=2, sticky="e", padx=(8, 5))
        ctk.CTkEntry(item_editor, textvariable=self._confidence_var, width=65, height=30).grid(row=2, column=3, sticky="e", padx=(0, 10), pady=3)
        ctk.CTkCheckBox(
            item_editor,
            text="사용",
            variable=self._enabled_var,
            font=self._font(11, "bold"),
            text_color=COLORS["text_primary"],
            fg_color=COLORS["success"],
            hover_color=COLORS["green_hover"],
        ).grid(row=3, column=0, sticky="w", padx=10, pady=(5, 10))
        self._item_region_label = ctk.CTkLabel(item_editor, text="공통 검색범위: 전체", font=self._font(10), text_color=COLORS["text_muted"])
        self._item_region_label.grid(row=3, column=1, sticky="w", pady=(5, 10))
        self._button(item_editor, "범위 설정", self._select_item_region, width=72).grid(row=3, column=2, sticky="e", padx=(4, 2), pady=(5, 10))
        self._button(item_editor, "해제", self._clear_item_region, width=50).grid(row=3, column=3, sticky="e", padx=(2, 10), pady=(5, 10))
        item_editor.grid_columnconfigure(1, weight=1)
        self._item_editor = item_editor

        right_title = ctk.CTkFrame(right, fg_color="transparent")
        right_title.pack(fill="x", padx=12, pady=(12, 5))
        ctk.CTkLabel(right_title, text="공통 실행 설정", font=self._font(15, "bold"), text_color=COLORS["accent_text"]).pack(anchor="w")
        settings = ctk.CTkScrollableFrame(right, fg_color="transparent")
        settings.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self._quantity_region_var = ctk.StringVar()
        self._status_region_var = ctk.StringVar()
        self._processing_mode_var = ctk.StringVar(
            value=(
                "재료 소진까지"
                if self._config["processing_mode"] == AUTO_LIST_MODE_UNTIL_EXHAUSTED
                else "목표 수량까지"
            )
        )
        self._max_value_var = ctk.StringVar(value=str(self._config["max_value"]))
        self._min_value_var = ctk.StringVar(value=str(self._config["min_value"]))
        self._render_wait_var = ctk.StringVar(value=str(self._config["render_wait"]))
        self._after_wait_var = ctk.StringVar(value=str(self._config["after_process_wait"]))
        self._item_timeout_var = ctk.StringVar(value=str(self._config["item_timeout"]))
        self._max_cycles_var = ctk.StringVar(value=str(self._config["max_cycles_per_item"]))
        self._max_runtime_var = ctk.StringVar(value=str(self._config["max_runtime_per_item"]))
        self._skip_missing_var = ctk.BooleanVar(value=self._config["skip_missing_item"])
        self._reselect_each_cycle_var = ctk.BooleanVar(value=self._config["reselect_each_cycle"])
        self._sync_common_labels()

        mode_card = ctk.CTkFrame(settings, fg_color=COLORS["bg_card"], corner_radius=12)
        mode_card.pack(fill="x", padx=4, pady=5)
        ctk.CTkLabel(
            mode_card,
            text="처리 방식",
            font=self._font(12, "bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor="w", padx=10, pady=(8, 4))
        ctk.CTkSegmentedButton(
            mode_card,
            values=["재료 소진까지", "목표 수량까지"],
            variable=self._processing_mode_var,
            command=self._on_processing_mode_change,
            selected_color=COLORS["accent_blue"],
            selected_hover_color=COLORS["hover_blue"],
            unselected_color=COLORS["bg_elevated"],
            unselected_hover_color=COLORS["bg_card_hover"],
            font=self._font(11, "bold"),
        ).pack(fill="x", padx=10, pady=(0, 10))

        self._setting_row(settings, "값 입력 영역", self._quantity_region_var, "영역 설정", self._select_quantity_region)
        self._setting_row(settings, "상태 색상 영역", self._status_region_var, "영역 설정", self._select_status_region)
        self._button(settings, "색상 판정 테스트", self._test_colour, COLORS["accent_orange"], COLORS["confidence_amber_hover"], 150).pack(anchor="w", padx=10, pady=(0, 8))
        self._colour_result_label = ctk.CTkLabel(settings, text="테스트 전", font=self._font(11, "bold"), text_color=COLORS["text_muted"])
        self._colour_result_label.pack(anchor="w", padx=10, pady=(0, 10))

        values = ctk.CTkFrame(settings, fg_color=COLORS["bg_card"], corner_radius=12)
        values.pack(fill="x", padx=4, pady=5)
        self._entry_grid(values, "최대 입력", self._max_value_var, 0, 0)
        self._entry_grid(values, "최소 입력", self._min_value_var, 0, 2)
        self._entry_grid(values, "화면 반영 대기", self._render_wait_var, 1, 0, "초")
        self._entry_grid(values, "처리 후 대기", self._after_wait_var, 1, 2, "초")
        self._entry_grid(values, "항목 검색 제한", self._item_timeout_var, 2, 0, "초")
        self._entry_grid(values, "항목 최대 반복", self._max_cycles_var, 3, 0, "회")
        self._entry_grid(values, "항목 최대 시간", self._max_runtime_var, 3, 2, "초")
        ctk.CTkCheckBox(
            values,
            text="없는 항목 건너뛰기",
            variable=self._skip_missing_var,
            font=self._font(11, "bold"),
            text_color=COLORS["text_primary"],
            fg_color=COLORS["success"],
            hover_color=COLORS["green_hover"],
        ).grid(row=2, column=2, columnspan=2, sticky="w", padx=8, pady=10)
        ctk.CTkCheckBox(
            values,
            text="매 반복 전 항목 다시 찾기",
            variable=self._reselect_each_cycle_var,
            font=self._font(11, "bold"),
            text_color=COLORS["text_primary"],
            fg_color=COLORS["success"],
            hover_color=COLORS["green_hover"],
        ).grid(row=4, column=0, columnspan=4, sticky="w", padx=10, pady=(5, 10))
        values.grid_columnconfigure(1, weight=1)
        values.grid_columnconfigure(3, weight=1)

        help_card = ctk.CTkFrame(settings, fg_color=COLORS["bg_elevated"], corner_radius=12)
        help_card.pack(fill="x", padx=4, pady=(8, 4))
        ctk.CTkLabel(
            help_card,
            text=(
                "사용 순서\n"
                "1. 항목 이미지를 처리 순서대로 추가합니다.\n"
                "2. 수량을 입력할 칸의 영역과 빨강/초록 상태 영역을 지정합니다.\n"
                "3. 재료 소진 모드는 10부터 1까지 낮춰 가능한 최대 수량을 찾습니다.\n"
                "4. 수량은 설정한 입력 위치에 자동 입력되며, 하위 액션에는 제작·분해 동작을 넣습니다.\n"
                "5. 기존 화면 진입 동작이 필요하면 '액션 호출'로 실행하고 자동 목록으로 복귀합니다.\n"
                "6. 1도 빨강이거나 처리한 항목이 사라지면 다음 항목으로 이동합니다.\n"
                "7. 없는 항목 건너뛰기를 켜면 검색 제한 후 다음 등록 항목을 찾습니다."
            ),
            justify="left",
            anchor="w",
            wraplength=410,
            font=self._font(11),
            text_color=COLORS["text_secondary"],
        ).pack(fill="x", padx=12, pady=10)

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(fill="x", padx=18, pady=(0, 16))
        self._button(footer, "저장", self._save, COLORS["success"], COLORS["green_hover"], 130).pack(side="left", expand=True, fill="x", padx=(0, 6))
        self._button(footer, "취소", self._cancel, width=130).pack(side="left", expand=True, fill="x", padx=(6, 0))
        self._on_processing_mode_change(self._processing_mode_var.get(), refresh=False)

    def _setting_row(self, parent, label, variable, button_text, command):
        frame = ctk.CTkFrame(parent, fg_color=COLORS["bg_card"], corner_radius=12)
        frame.pack(fill="x", padx=4, pady=5)
        ctk.CTkLabel(frame, text=label, font=self._font(12, "bold"), text_color=COLORS["text_primary"]).pack(anchor="w", padx=10, pady=(8, 2))
        line = ctk.CTkFrame(frame, fg_color="transparent")
        line.pack(fill="x", padx=10, pady=(0, 8))
        ctk.CTkLabel(line, textvariable=variable, anchor="w", font=self._font(11), text_color=COLORS["text_secondary"]).pack(side="left", fill="x", expand=True)
        self._button(line, button_text, command, COLORS["accent_blue"], COLORS["hover_blue"], 120).pack(side="right")

    def _entry_grid(self, parent, label, variable, row, column, suffix=""):
        ctk.CTkLabel(parent, text=label, font=self._font(11), text_color=COLORS["text_secondary"]).grid(row=row, column=column, sticky="w", padx=(10, 5), pady=7)
        entry = ctk.CTkEntry(parent, textvariable=variable, width=72, height=30)
        entry.grid(row=row, column=column + 1, sticky="ew", padx=(0, 5), pady=7)
        if suffix:
            entry.configure(placeholder_text=suffix)

    def _sync_common_labels(self):
        quantity_region = self._config.get("quantity_region")
        region = self._config.get("status_region")
        self._quantity_region_var.set(
            f"({quantity_region[0]}, {quantity_region[1]}) - ({quantity_region[2]}, {quantity_region[3]})"
            if quantity_region else "설정 안 됨"
        )
        self._status_region_var.set(
            f"({region[0]}, {region[1]}) - ({region[2]}, {region[3]})" if region else "설정 안 됨"
        )

    def _on_processing_mode_change(self, selected=None, *, refresh=True):
        mode = (
            AUTO_LIST_MODE_UNTIL_EXHAUSTED
            if (selected or self._processing_mode_var.get()) == "재료 소진까지"
            else AUTO_LIST_MODE_TARGET
        )
        self._config["processing_mode"] = mode
        self._target_count_entry.configure(
            state="disabled" if mode == AUTO_LIST_MODE_UNTIL_EXHAUSTED else "normal"
        )
        if refresh:
            self._commit_selected_item(quiet=True)
            self._refresh_items()

    def _thumbnail(self, path: str):
        try:
            image = Image.open(path).convert("RGB")
            image.thumbnail((54, 38), Image.Resampling.LANCZOS)
            canvas = Image.new("RGB", (54, 38), COLORS["image_canvas_bg"])
            canvas.paste(image, ((54 - image.width) // 2, (38 - image.height) // 2))
            photo = ctk.CTkImage(
                light_image=canvas,
                dark_image=canvas,
                size=(54, 38),
            )
            self._item_photos.append(photo)
            return photo
        except Exception:
            return None

    def _commit_selected_item(self, *, quiet=True):
        if not (0 <= self._selected_index < len(self._config["items"])):
            return True
        try:
            confidence = max(30.0, min(100.0, float(self._confidence_var.get().strip()))) / 100.0
            if self._config["processing_mode"] == AUTO_LIST_MODE_UNTIL_EXHAUSTED:
                target_count = int(self._config["items"][self._selected_index].get("target_count", 1) or 1)
            else:
                target_count = max(1, int(self._target_count_var.get().strip()))
        except (TypeError, ValueError):
            if not quiet:
                messagebox.showerror("입력 확인", "목표 수량과 인식률을 숫자로 입력하세요.", parent=self)
            return False
        item = self._config["items"][self._selected_index]
        item["name"] = self._item_name_var.get().strip() or Path(item["image"]).stem
        item["target_count"] = target_count
        item["confidence"] = confidence
        item["enabled"] = bool(self._enabled_var.get())
        return True

    def _refresh_items(self):
        for widget in self._item_list.winfo_children():
            widget.destroy()
        self._item_photos.clear()
        if self._selected_index >= len(self._config["items"]):
            self._selected_index = len(self._config["items"]) - 1
        for index, item in enumerate(self._config["items"]):
            selected = index == self._selected_index
            row = ctk.CTkFrame(
                self._item_list,
                fg_color=COLORS["bg_elevated"] if selected else COLORS["bg_card"],
                corner_radius=10,
                border_width=2 if selected else 1,
                border_color=COLORS["accent_blue"] if selected else COLORS["separator"],
            )
            row.pack(fill="x", pady=3)
            thumb = self._thumbnail(item["image"])
            ctk.CTkButton(
                row,
                text="" if thumb else "IMG",
                image=thumb,
                width=58,
                height=42,
                fg_color="transparent",
                hover_color=COLORS["bg_card_hover"],
                command=lambda i=index: self._select_item(i),
            ).pack(side="left", padx=(7, 5), pady=6)
            process_label = (
                "재료 소진까지"
                if self._config["processing_mode"] == AUTO_LIST_MODE_UNTIL_EXHAUSTED
                else f"목표 {item['target_count']}"
            )
            label = f"{index + 1}. {item['name']}\n{process_label}  ·  인식 {int(item['confidence'] * 100)}%"
            ctk.CTkButton(
                row,
                text=label,
                anchor="w",
                command=lambda i=index: self._select_item(i),
                fg_color="transparent",
                hover_color=COLORS["bg_card_hover"],
                text_color=COLORS["text_primary"] if item["enabled"] else COLORS["text_muted"],
                font=self._font(11, "bold"),
                height=48,
            ).pack(side="left", fill="x", expand=True, pady=4)
            self._button(row, "↓", lambda i=index: self._move_item(i, 1), width=30).pack(side="right", padx=(1, 6))
            self._button(row, "↑", lambda i=index: self._move_item(i, -1), width=30).pack(side="right", padx=1)
            self._button(row, "삭제", lambda i=index: self._delete_item(i), COLORS["error"], COLORS["danger_hover"], 46).pack(side="right", padx=1)

    def _load_selected_item(self):
        enabled = 0 <= self._selected_index < len(self._config["items"])
        if enabled:
            item = self._config["items"][self._selected_index]
            self._item_name_var.set(item["name"])
            self._target_count_var.set(str(item["target_count"]))
            self._confidence_var.set(str(int(round(item["confidence"] * 100))))
            self._enabled_var.set(item["enabled"])
            region = self._config.get("item_search_region") or item.get("search_region")
            self._item_region_label.configure(
                text=f"공통 검색범위: {region}" if region else "공통 검색범위: 전체"
            )
        else:
            self._item_name_var.set("")
            self._target_count_var.set("1")
            self._confidence_var.set("80")
            self._enabled_var.set(True)
            self._item_region_label.configure(text="공통 검색범위: 전체")
        self._target_count_entry.configure(
            state=(
                "disabled"
                if enabled and self._config["processing_mode"] == AUTO_LIST_MODE_UNTIL_EXHAUSTED
                else ("normal" if enabled else "disabled")
            )
        )
        for child in self._item_editor.winfo_children():
            if child is self._target_count_entry:
                continue
            if child is not self._item_editor:
                try:
                    child.configure(state="normal" if enabled else "disabled")
                except Exception:
                    pass

    def _select_item(self, index):
        if index == self._selected_index:
            return
        if not self._commit_selected_item(quiet=False):
            return
        self._selected_index = index
        self._refresh_items()
        self._load_selected_item()

    def _add_item(self):
        if len(self._config["items"]) >= AUTO_LIST_MAX_ITEMS:
            messagebox.showwarning("항목 제한", f"항목은 최대 {AUTO_LIST_MAX_ITEMS}개까지 추가할 수 있습니다.", parent=self)
            return
        path = filedialog.askopenfilename(
            title="처리 항목 이미지 선택",
            initialdir=str(DATA_DIR / "templates"),
            filetypes=[("이미지 파일", "*.png *.jpg *.jpeg *.bmp *.gif *.webp"), ("모든 파일", "*.*")],
            parent=self,
        )
        if not path:
            return
        source = Path(path)
        templates = DATA_DIR / "templates"
        templates.mkdir(parents=True, exist_ok=True)
        try:
            if source.resolve().parent != templates.resolve():
                destination = templates / f"auto_list_{uuid.uuid4().hex[:8]}_{source.name}"
                shutil.copy2(source, destination)
                source = destination
        except OSError as exc:
            messagebox.showerror("이미지 추가 실패", str(exc), parent=self)
            return
        if not self._commit_selected_item(quiet=False):
            return
        self._config["items"].append(
            {
                "name": source.stem,
                "image": str(source),
                "target_count": 1,
                "confidence": 0.8,
                "search_region": copy.deepcopy(self._config.get("item_search_region")),
                "enabled": True,
            }
        )
        self._selected_index = len(self._config["items"]) - 1
        self._refresh_items()
        self._load_selected_item()

    def _delete_item(self, index):
        if not (0 <= index < len(self._config["items"])):
            return
        if index != self._selected_index and not self._commit_selected_item(quiet=False):
            return
        del self._config["items"][index]
        if self._selected_index > index:
            self._selected_index -= 1
        elif self._selected_index == index:
            self._selected_index = min(index, len(self._config["items"]) - 1)
        self._refresh_items()
        self._load_selected_item()

    def _move_item(self, index, delta):
        target = index + delta
        if not (0 <= index < len(self._config["items"]) and 0 <= target < len(self._config["items"])):
            return
        if not self._commit_selected_item(quiet=False):
            return
        self._config["items"][index], self._config["items"][target] = self._config["items"][target], self._config["items"][index]
        if self._selected_index == index:
            self._selected_index = target
        elif self._selected_index == target:
            self._selected_index = index
        self._refresh_items()
        self._load_selected_item()

    def _with_region_selector(self, callback, existing=None):
        from .analyzer_view import ScreenRegionSelector

        try:
            self.grab_release()
        except Exception:
            pass
        self.withdraw()

        def selected(x1, y1, x2, y2):
            self.deiconify()
            self.grab_set()
            callback([x1, y1, x2, y2])

        def cancelled():
            self.deiconify()
            self.grab_set()

        ScreenRegionSelector(self, selected, cancelled, existing_region=existing)

    def _select_item_region(self):
        if not (0 <= self._selected_index < len(self._config["items"])):
            return
        existing = self._config.get("item_search_region")
        self._with_region_selector(self._set_item_region, existing)

    def _set_item_region(self, region):
        set_auto_list_item_search_region(self._config, region)
        self._load_selected_item()

    def _clear_item_region(self):
        if not (0 <= self._selected_index < len(self._config["items"])):
            return
        set_auto_list_item_search_region(self._config, None)
        self._load_selected_item()

    def _select_status_region(self):
        self._with_region_selector(self._set_status_region, self._config.get("status_region"))

    def _set_status_region(self, region):
        self._config["status_region"] = region
        self._sync_common_labels()

    def _select_quantity_region(self):
        self._with_region_selector(self._set_quantity_region, self._config.get("quantity_region"))

    def _set_quantity_region(self, region):
        self._config["quantity_region"] = region
        self._sync_common_labels()

    def _test_colour(self):
        region = self._config.get("status_region")
        if not region:
            messagebox.showwarning("영역 필요", "먼저 상태 색상 영역을 설정하세요.", parent=self)
            return
        result = None
        error = None
        try:
            self.grab_release()
            self.withdraw()
            self.update_idletasks()
            time.sleep(0.15)
            rgb = np.asarray(ImageGrab.grab(all_screens=True).convert("RGB"))
            frame = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
            origin_x = ctypes.windll.user32.GetSystemMetrics(76)
            origin_y = ctypes.windll.user32.GetSystemMetrics(77)
            frame_region = translate_screen_region(region, origin_x, origin_y)
            result = classify_colour_state(
                crop_bgr_region(frame, frame_region),
                red_min_pixels=self._config["red_min_pixels"],
                green_min_pixels=self._config["green_min_pixels"],
            )
        except Exception as exc:
            error = exc
        finally:
            self.deiconify()
            self.grab_set()
        if error is not None:
            messagebox.showerror("색상 테스트 실패", str(error), parent=self)
            return
        labels = {"available": "사용 가능 (초록)", "unavailable": "부족 (빨강)", "unknown": "판정 불명"}
        colors = {"available": COLORS["success_text"], "unavailable": COLORS["error"], "unknown": COLORS["warning_text"]}
        self._colour_result_label.configure(
            text=f"{labels[result.state]}  ·  빨강 {result.red_pixels}px / 초록 {result.green_pixels}px",
            text_color=colors[result.state],
        )

    def _save(self):
        if not self._commit_selected_item(quiet=False):
            return
        if not self._config["items"]:
            messagebox.showwarning("항목 필요", "처리할 항목 이미지를 하나 이상 추가하세요.", parent=self)
            return
        if not self._config.get("quantity_region"):
            messagebox.showwarning("영역 필요", "값 입력 영역을 설정하세요.", parent=self)
            return
        if not self._config.get("status_region"):
            messagebox.showwarning("영역 필요", "상태 색상 영역을 설정하세요.", parent=self)
            return
        try:
            raw_max = int(self._max_value_var.get())
            raw_min = int(self._min_value_var.get())
            if raw_min > raw_max:
                raise ValueError("최소 입력값은 최대 입력값보다 클 수 없습니다.")
            self._config.update(
                {
                    "max_value": raw_max,
                    "min_value": raw_min,
                    "render_wait": float(self._render_wait_var.get()),
                    "after_process_wait": float(self._after_wait_var.get()),
                    "item_timeout": float(self._item_timeout_var.get()),
                    "max_cycles_per_item": int(self._max_cycles_var.get()),
                    "max_runtime_per_item": float(self._max_runtime_var.get()),
                    "skip_missing_item": bool(self._skip_missing_var.get()),
                    "reselect_each_cycle": bool(self._reselect_each_cycle_var.get()),
                    "processing_mode": (
                        AUTO_LIST_MODE_UNTIL_EXHAUSTED
                        if self._processing_mode_var.get() == "재료 소진까지"
                        else AUTO_LIST_MODE_TARGET
                    ),
                }
            )
        except (TypeError, ValueError) as exc:
            messagebox.showerror("입력 확인", str(exc) or "입력 범위와 대기시간을 숫자로 입력하세요.", parent=self)
            return
        config = normalize_auto_list_config(self._config)
        self._result = copy.deepcopy(config)
        self.grab_release()
        self.destroy()

    def _cancel(self):
        self._result = None
        self.grab_release()
        self.destroy()

    def get_result(self):
        self.wait_window()
        return copy.deepcopy(self._result)


class AutoListValueInputDialog(ctk.CTkToplevel):
    """Configure the input region for the accepted automatic-list value."""

    def __init__(self, parent, *, region=None):
        super().__init__(parent)
        self._result = None
        self._region = list(region) if isinstance(region, (list, tuple)) and len(region) == 4 else None
        self.title("현재 처리수량 입력 설정")
        self.geometry("560x300")
        self.minsize(520, 280)
        self.configure(fg_color=COLORS["bg_content"])
        self.transient(parent)
        self.grab_set()

        card = ctk.CTkFrame(
            self,
            fg_color=COLORS["bg_glass"],
            corner_radius=IOS_METRICS["card_radius_compact"],
            border_width=IOS_METRICS["card_border_width"],
            border_color=COLORS["border"],
        )
        card.pack(fill="both", expand=True, padx=18, pady=18)
        ctk.CTkLabel(
            card,
            text="현재 처리수량 입력",
            font=ctk.CTkFont(family=IOS_FONTS["family"], size=19, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor="w", padx=16, pady=(16, 5))
        ctk.CTkLabel(
            card,
            text="자동 목록에서 확정된 수량을 분해 수량칸 등에 그대로 입력합니다.\n"
                 "자동 목록 처리의 하위 액션으로 배치해야 합니다.",
            justify="left",
            anchor="w",
            font=ctk.CTkFont(family=IOS_FONTS["family"], size=12),
            text_color=COLORS["text_secondary"],
        ).pack(fill="x", padx=16, pady=(0, 12))

        region_row = ctk.CTkFrame(card, fg_color=COLORS["bg_card"], corner_radius=12)
        region_row.pack(fill="x", padx=16, pady=5)
        self._region_var = ctk.StringVar()
        self._sync_region_label()
        ctk.CTkLabel(
            region_row,
            text="값 입력 영역",
            font=ctk.CTkFont(family=IOS_FONTS["family"], size=12, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(side="left", padx=12, pady=12)
        ctk.CTkLabel(
            region_row,
            textvariable=self._region_var,
            font=ctk.CTkFont(family=IOS_FONTS["family"], size=11),
            text_color=COLORS["text_secondary"],
        ).pack(side="left", fill="x", expand=True, padx=6)
        ctk.CTkButton(
            region_row,
            text="영역 설정",
            command=self._select_region,
            width=105,
            height=32,
            fg_color=COLORS["accent_blue"],
            hover_color=COLORS["hover_blue"],
            text_color=COLORS["text_on_accent"],
            font=ctk.CTkFont(family=IOS_FONTS["family"], size=12, weight="bold"),
            corner_radius=IOS_METRICS["control_radius_small"],
        ).pack(side="right", padx=10, pady=8)

        footer = ctk.CTkFrame(card, fg_color="transparent")
        footer.pack(fill="x", padx=16, pady=(12, 16))
        ctk.CTkButton(
            footer,
            text="저장",
            command=self._save,
            fg_color=COLORS["success"],
            hover_color=COLORS["green_hover"],
            font=ctk.CTkFont(family=IOS_FONTS["family"], size=12, weight="bold"),
        ).pack(side="left", fill="x", expand=True, padx=(0, 5))
        ctk.CTkButton(
            footer,
            text="취소",
            command=self._cancel,
            fg_color=COLORS["bg_card"],
            hover_color=COLORS["bg_card_hover"],
            font=ctk.CTkFont(family=IOS_FONTS["family"], size=12, weight="bold"),
        ).pack(side="left", fill="x", expand=True, padx=(5, 0))

    def _sync_region_label(self):
        if self._region:
            self._region_var.set(
                f"({self._region[0]}, {self._region[1]}) - ({self._region[2]}, {self._region[3]})"
            )
        else:
            self._region_var.set("설정 안 됨")

    def _select_region(self):
        from .analyzer_view import ScreenRegionSelector

        self.grab_release()
        self.withdraw()

        def selected(x1, y1, x2, y2):
            self._region = [x1, y1, x2, y2]
            self.deiconify()
            self.grab_set()
            self._sync_region_label()

        def cancelled():
            self.deiconify()
            self.grab_set()

        ScreenRegionSelector(self, selected, cancelled, existing_region=self._region)

    def _save(self):
        if not self._region:
            messagebox.showwarning("영역 필요", "현재 처리수량을 입력할 영역을 설정하세요.", parent=self)
            return
        self._result = list(self._region)
        self.grab_release()
        self.destroy()

    def _cancel(self):
        self._result = None
        self.grab_release()
        self.destroy()

    def get_result(self):
        self.wait_window()
        return copy.deepcopy(self._result)
