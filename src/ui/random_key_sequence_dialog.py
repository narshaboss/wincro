"""Dialog for configuring random key sequence actions."""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Tuple

import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox

from ..player.random_key_sequence import (
    DEFAULT_RANDOM_KEY_STEP_DELAY,
    DEFAULT_RANDOM_KEY_STEP_RANDOM_RANGE,
    apply_random_key_step_defaults,
    clone_default_random_key_sequences,
    format_key_step,
    format_random_key_sequence,
    get_step_delay_after,
    normalize_random_key_sequences,
)
from .key_input_dialog import KeyInputDialog
from .theme import COLORS, IOS_FONTS, IOS_METRICS


class RandomKeySequenceDialog(ctk.CTkToplevel):
    """Configure groups of key steps where one group is chosen at runtime."""

    def __init__(
        self,
        parent,
        *,
        sequences: List[List[Dict[str, Any]]] | None = None,
        step_delay: float = DEFAULT_RANDOM_KEY_STEP_DELAY,
    ):
        super().__init__(parent)
        self._result: Tuple[List[List[Dict[str, Any]]], float] | None = None
        self._sequences = normalize_random_key_sequences(sequences)
        if not self._sequences:
            self._sequences = clone_default_random_key_sequences()
        self._ensure_step_defaults()
        try:
            self._step_delay = max(0.0, float(step_delay or DEFAULT_RANDOM_KEY_STEP_DELAY))
        except (TypeError, ValueError):
            self._step_delay = DEFAULT_RANDOM_KEY_STEP_DELAY
        self._selected_group = 0
        self._copied_sequence: List[Dict[str, Any]] | None = None
        self._group_rows: List[ctk.CTkButton] = []
        self._step_rows: List[ctk.CTkFrame] = []

        self.title("랜덤키 입력 설정")
        self.geometry("960x620")
        self.minsize(900, 560)
        self.configure(fg_color=COLORS["bg_content"])
        self.transient(parent)
        self.grab_set()

        self._build_ui()
        self._refresh_groups()
        self._refresh_steps()
        self.after(50, self.focus_force)

    def _font(self, size: int, weight: str = "normal") -> ctk.CTkFont:
        return ctk.CTkFont(family=IOS_FONTS["family"], size=size, weight=weight)

    def _build_ui(self) -> None:
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=18, pady=(16, 8))
        ctk.CTkLabel(
            header,
            text="랜덤키 입력",
            font=self._font(20, "bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor="w")
        ctk.CTkLabel(
            header,
            text="실행 시 아래 묶음 중 하나가 랜덤으로 선택되고, 묶음 안 키들이 순서대로 입력됩니다.",
            font=self._font(12),
            text_color=COLORS["text_secondary"],
        ).pack(anchor="w", pady=(4, 0))

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=18, pady=(0, 10))

        left = ctk.CTkFrame(
            body,
            fg_color=COLORS["bg_glass"],
            corner_radius=IOS_METRICS["card_radius_compact"],
            border_width=IOS_METRICS["card_border_width"],
            border_color=COLORS["border"],
        )
        left.pack(side="left", fill="both", padx=(0, 8))
        left.configure(width=280)
        left.pack_propagate(False)
        right = ctk.CTkFrame(
            body,
            fg_color=COLORS["bg_glass"],
            corner_radius=IOS_METRICS["card_radius_compact"],
            border_width=IOS_METRICS["card_border_width"],
            border_color=COLORS["border"],
        )
        right.pack(side="left", fill="both", expand=True, padx=(8, 0))

        ctk.CTkLabel(left, text="랜덤 묶음", font=self._font(15, "bold"), text_color=COLORS["accent_text"]).pack(anchor="w", padx=12, pady=(12, 6))
        self._group_list = ctk.CTkScrollableFrame(left, fg_color="transparent")
        self._group_list.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        self._group_list.bind("<Button-1>", self._paste_group_from_empty_click, add="+")
        group_btns_top = ctk.CTkFrame(left, fg_color="transparent")
        group_btns_top.pack(fill="x", padx=10, pady=(0, 6))
        self._small_button(group_btns_top, "+ 묶음", self._add_group, COLORS["success"], COLORS["green_hover"]).pack(side="left", expand=True, fill="x", padx=(0, 4))
        self._small_button(group_btns_top, "삭제", self._delete_group, COLORS["error"], COLORS["danger_hover"]).pack(side="left", expand=True, fill="x", padx=(4, 0))
        group_btns_bottom = ctk.CTkFrame(left, fg_color="transparent")
        group_btns_bottom.pack(fill="x", padx=10, pady=(0, 10))
        self._small_button(group_btns_bottom, "복사", self._copy_group, COLORS["accent_blue"], COLORS["hover_blue"]).pack(side="left", expand=True, fill="x", padx=(0, 4))
        self._small_button(group_btns_bottom, "붙여넣기", self._paste_group_below, COLORS["accent_orange"], COLORS["confidence_amber_hover"]).pack(side="left", expand=True, fill="x", padx=(4, 0))

        ctk.CTkLabel(right, text="선택 묶음 키 순서", font=self._font(15, "bold"), text_color=COLORS["accent_text"]).pack(anchor="w", padx=12, pady=(12, 6))
        self._step_list = ctk.CTkScrollableFrame(right, fg_color="transparent")
        self._step_list.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        step_btns = ctk.CTkFrame(right, fg_color="transparent")
        step_btns.pack(fill="x", padx=10, pady=(0, 10))
        self._small_button(step_btns, "+ 키 추가", self._add_key_step, COLORS["accent_orange"], COLORS["confidence_amber_hover"]).pack(side="left", expand=True, fill="x", padx=(0, 4))
        self._small_button(step_btns, "초기값", self._reset_defaults, COLORS["accent_blue"], COLORS["hover_blue"]).pack(side="left", expand=True, fill="x", padx=(4, 0))

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(fill="x", padx=18, pady=(0, 16))
        ctk.CTkButton(
            footer,
            text="저장",
            command=self._save,
            height=40,
            fg_color=COLORS["success"],
            hover_color=COLORS["green_hover"],
            text_color=COLORS["text_on_accent"],
            font=self._font(14, "bold"),
            corner_radius=IOS_METRICS["pill_radius"],
        ).pack(side="left", expand=True, fill="x", padx=(0, 6))
        ctk.CTkButton(
            footer,
            text="취소",
            command=self._cancel,
            height=40,
            fg_color=COLORS["bg_card"],
            hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_primary"],
            font=self._font(14, "bold"),
            corner_radius=IOS_METRICS["pill_radius"],
        ).pack(side="left", expand=True, fill="x", padx=(6, 0))

    def _small_button(self, parent, text: str, command, fg_color: str, hover_color: str):
        return ctk.CTkButton(
            parent,
            text=text,
            command=command,
            height=32,
            fg_color=fg_color,
            hover_color=hover_color,
            text_color=COLORS["text_on_accent"],
            font=self._font(12, "bold"),
            corner_radius=IOS_METRICS["control_radius_small"],
        )

    def _refresh_groups(self) -> None:
        for child in self._group_list.winfo_children():
            child.destroy()
        self._group_rows = []
        if self._selected_group >= len(self._sequences):
            self._selected_group = max(0, len(self._sequences) - 1)
        for idx, sequence in enumerate(self._sequences):
            selected = idx == self._selected_group
            row = ctk.CTkButton(
                self._group_list,
                text=f"{idx + 1}. {format_random_key_sequence(sequence, max_steps=5)}",
                anchor="w",
                command=lambda i=idx: self._select_group(i),
                height=36,
                fg_color=COLORS["accent_orange"] if selected else COLORS["bg_card"],
                hover_color=COLORS["confidence_amber_hover"] if selected else COLORS["bg_card_hover"],
                text_color=COLORS["text_on_accent"] if selected else COLORS["text_primary"],
                font=self._font(12, "bold"),
                corner_radius=IOS_METRICS["control_radius_small"],
            )
            row.pack(fill="x", pady=3)
            self._group_rows.append(row)

    def _update_selected_group_preview(self) -> None:
        if not (0 <= self._selected_group < len(self._sequences)):
            return
        if not (0 <= self._selected_group < len(self._group_rows)):
            return
        self._group_rows[self._selected_group].configure(
            text=f"{self._selected_group + 1}. {format_random_key_sequence(self._sequences[self._selected_group], max_steps=5)}"
        )

    def _refresh_steps(self) -> None:
        for child in self._step_list.winfo_children():
            child.destroy()
        self._step_rows = []
        if not self._sequences:
            return
        sequence = self._sequences[self._selected_group]
        for idx, step in enumerate(sequence):
            row = ctk.CTkFrame(self._step_list, fg_color=COLORS["bg_card"], corner_radius=IOS_METRICS["control_radius_small"])
            row.pack(fill="x", pady=3)
            self._step_rows.append(row)
            index_label = ctk.CTkLabel(
                row,
                text=f"{idx + 1}",
                width=24,
                font=self._font(13, "bold"),
                text_color=COLORS["accent_text"],
            )
            index_label.pack(side="left", padx=(8, 2), pady=8)
            row._wincro_index_label = index_label
            arrow_frame = ctk.CTkFrame(row, fg_color="transparent")
            arrow_frame.pack(side="left", padx=(0, 4), pady=5)
            up_button = ctk.CTkButton(
                arrow_frame,
                text="▲",
                width=26,
                height=18,
                command=lambda r=row: self._move_step_by_row(r, -1),
                fg_color=COLORS["bg_elevated"] if idx > 0 else COLORS["bg_card"],
                hover_color=COLORS["accent_hover"],
                text_color=COLORS["accent_text"] if idx > 0 else COLORS["text_muted"],
                font=self._font(10, "bold"),
                corner_radius=IOS_METRICS["control_radius_small"],
                state="normal" if idx > 0 else "disabled",
            )
            up_button.pack(side="top", pady=(0, 2))
            row._wincro_up_button = up_button
            down_button = ctk.CTkButton(
                arrow_frame,
                text="▼",
                width=26,
                height=18,
                command=lambda r=row: self._move_step_by_row(r, 1),
                fg_color=COLORS["bg_elevated"] if idx < len(sequence) - 1 else COLORS["bg_card"],
                hover_color=COLORS["accent_hover"],
                text_color=COLORS["accent_text"] if idx < len(sequence) - 1 else COLORS["text_muted"],
                font=self._font(10, "bold"),
                corner_radius=IOS_METRICS["control_radius_small"],
                state="normal" if idx < len(sequence) - 1 else "disabled",
            )
            down_button.pack(side="top")
            row._wincro_down_button = down_button
            ctk.CTkLabel(
                row,
                text=format_key_step(step),
                font=self._font(13, "bold"),
                text_color=COLORS["text_primary"],
                anchor="w",
            ).pack(side="left", fill="x", expand=True, padx=4, pady=8)
            ctk.CTkButton(
                row,
                text="삭제",
                width=58,
                height=28,
                command=lambda r=row: self._delete_step_by_row(r),
                fg_color=COLORS["error"],
                hover_color=COLORS["danger_hover"],
                text_color=COLORS["text_on_accent"],
                font=self._font(11, "bold"),
                corner_radius=IOS_METRICS["control_radius_small"],
            ).pack(side="right", padx=8, pady=6)
            range_entry = ctk.CTkEntry(
                row,
                width=58,
                height=28,
                font=self._font(12, "bold"),
                fg_color=COLORS["bg_elevated"],
                text_color=COLORS["text_primary"],
            )
            range_entry.insert(0, f"{float(step.get('delay_after_random_range', DEFAULT_RANDOM_KEY_STEP_RANDOM_RANGE) or 0.0):.2f}")
            range_entry.pack(side="right", padx=(4, 0), pady=6)
            range_entry.bind("<KeyRelease>", lambda _event, r=row, entry=range_entry: self._update_step_delay_random_range_by_row(r, entry.get()))
            range_entry.bind("<FocusOut>", lambda _event, r=row, entry=range_entry: self._update_step_delay_random_range_by_row(r, entry.get(), normalize=True))
            random_var = tk.BooleanVar(value=bool(step.get("delay_after_random", True)))
            ctk.CTkCheckBox(
                row,
                text="랜덤",
                variable=random_var,
                width=62,
                font=self._font(11, "bold"),
                text_color=COLORS["text_secondary"],
                fg_color=COLORS["accent_blue"],
                hover_color=COLORS["hover_blue"],
                command=lambda r=row, var=random_var: self._update_step_delay_random_by_row(r, var.get()),
            ).pack(side="right", padx=(6, 0), pady=6)
            delay_entry = ctk.CTkEntry(
                row,
                width=64,
                height=28,
                font=self._font(12, "bold"),
                fg_color=COLORS["bg_elevated"],
                text_color=COLORS["text_primary"],
            )
            delay_entry.insert(0, f"{get_step_delay_after(step, self._step_delay):.2f}")
            delay_entry.pack(side="right", padx=(4, 0), pady=6)
            delay_entry.bind("<KeyRelease>", lambda _event, r=row, entry=delay_entry: self._update_step_delay_by_row(r, entry.get()))
            delay_entry.bind("<FocusOut>", lambda _event, r=row, entry=delay_entry: self._update_step_delay_by_row(r, entry.get(), normalize=True))
            ctk.CTkLabel(
                row,
                text="대기",
                width=34,
                font=self._font(11, "bold"),
                text_color=COLORS["text_secondary"],
            ).pack(side="right", padx=(6, 2), pady=6)

    def _select_group(self, index: int) -> None:
        self._selected_group = index
        self._refresh_groups()
        self._refresh_steps()

    def _add_group(self) -> None:
        self._sequences.append([self._make_key_step(["enter"], [])])
        self._selected_group = len(self._sequences) - 1
        self._refresh_groups()
        self._refresh_steps()

    def _copy_group(self) -> None:
        if not self._sequences or not (0 <= self._selected_group < len(self._sequences)):
            return
        self._copied_sequence = copy.deepcopy(self._sequences[self._selected_group])

    def _paste_group_from_empty_click(self, event) -> None:
        if self._copied_sequence:
            self._paste_group_below()

    def _paste_group_below(self) -> None:
        if not self._copied_sequence:
            messagebox.showwarning("알림", "먼저 복사할 랜덤 묶음을 선택하고 복사하세요.", parent=self)
            return
        insert_at = min(len(self._sequences), self._selected_group + 1)
        self._sequences.insert(insert_at, copy.deepcopy(self._copied_sequence))
        self._selected_group = insert_at
        self._refresh_groups()
        self._refresh_steps()

    def _delete_group(self) -> None:
        if len(self._sequences) <= 1:
            messagebox.showwarning("알림", "랜덤 묶음은 최소 1개가 필요합니다.", parent=self)
            return
        self._sequences.pop(self._selected_group)
        self._selected_group = min(self._selected_group, len(self._sequences) - 1)
        self._refresh_groups()
        self._refresh_steps()

    def _add_key_step(self) -> None:
        dialog = KeyInputDialog(self)
        keys, key_events = dialog.get_result()
        if not keys and not key_events:
            return
        step = {
            **self._make_key_step(keys, key_events),
        }
        self._sequences[self._selected_group].append(step)
        self._refresh_groups()
        self._refresh_steps()

    def _delete_step(self, index: int) -> None:
        sequence = self._sequences[self._selected_group]
        if len(sequence) <= 1:
            messagebox.showwarning("알림", "묶음 안에는 최소 1개 키가 필요합니다.", parent=self)
            return
        sequence.pop(index)
        self._refresh_groups()
        self._refresh_steps()

    def _step_row_index(self, row: ctk.CTkFrame) -> int:
        try:
            return self._step_rows.index(row)
        except ValueError:
            return -1

    def _delete_step_by_row(self, row: ctk.CTkFrame) -> None:
        index = self._step_row_index(row)
        if index >= 0:
            self._delete_step(index)

    def _move_step(self, index: int, delta: int) -> None:
        if not self._sequences or not (0 <= self._selected_group < len(self._sequences)):
            return
        sequence = self._sequences[self._selected_group]
        new_index = index + delta
        if not (0 <= index < len(sequence)) or not (0 <= new_index < len(sequence)):
            return
        sequence[index], sequence[new_index] = sequence[new_index], sequence[index]
        self._move_step_row(index, new_index)
        self._refresh_step_row_controls()
        self._update_selected_group_preview()

    def _move_step_by_row(self, row: ctk.CTkFrame, delta: int) -> None:
        index = self._step_row_index(row)
        if index >= 0:
            self._move_step(index, delta)

    def _move_step_row(self, old_index: int, new_index: int) -> None:
        if not (0 <= old_index < len(self._step_rows)) or not (0 <= new_index < len(self._step_rows)):
            return
        row = self._step_rows.pop(old_index)
        self._step_rows.insert(new_index, row)
        row.pack_forget()
        try:
            if new_index == 0 and len(self._step_rows) > 1:
                row.pack(fill="x", pady=3, before=self._step_rows[1])
            elif new_index > 0:
                row.pack(fill="x", pady=3, after=self._step_rows[new_index - 1])
            else:
                row.pack(fill="x", pady=3)
        except tk.TclError:
            for item in self._step_rows:
                item.pack_forget()
            for item in self._step_rows:
                item.pack(fill="x", pady=3)

    def _refresh_step_row_controls(self) -> None:
        last_index = len(self._step_rows) - 1
        for idx, row in enumerate(self._step_rows):
            label = getattr(row, "_wincro_index_label", None)
            if label is not None:
                label.configure(text=f"{idx + 1}")
            up_button = getattr(row, "_wincro_up_button", None)
            if up_button is not None:
                can_move_up = idx > 0
                up_button.configure(
                    state="normal" if can_move_up else "disabled",
                    fg_color=COLORS["bg_elevated"] if can_move_up else COLORS["bg_card"],
                    text_color=COLORS["accent_text"] if can_move_up else COLORS["text_muted"],
                )
            down_button = getattr(row, "_wincro_down_button", None)
            if down_button is not None:
                can_move_down = idx < last_index
                down_button.configure(
                    state="normal" if can_move_down else "disabled",
                    fg_color=COLORS["bg_elevated"] if can_move_down else COLORS["bg_card"],
                    text_color=COLORS["accent_text"] if can_move_down else COLORS["text_muted"],
                )

    def _update_step_delay_by_row(self, row: ctk.CTkFrame, value: str, *, normalize: bool = False) -> None:
        index = self._step_row_index(row)
        if index >= 0:
            self._update_step_delay(index, value, normalize=normalize)

    def _update_step_delay_random_by_row(self, row: ctk.CTkFrame, enabled: bool) -> None:
        index = self._step_row_index(row)
        if index >= 0:
            self._update_step_delay_random(index, enabled)

    def _update_step_delay_random_range_by_row(self, row: ctk.CTkFrame, value: str, *, normalize: bool = False) -> None:
        index = self._step_row_index(row)
        if index >= 0:
            self._update_step_delay_random_range(index, value, normalize=normalize)

    def _update_step_delay(self, index: int, value: str, *, normalize: bool = False) -> None:
        if not self._sequences or not (0 <= self._selected_group < len(self._sequences)):
            return
        sequence = self._sequences[self._selected_group]
        if not 0 <= index < len(sequence):
            return
        raw_value = str(value or "").strip().replace(",", ".")
        if not raw_value:
            sequence[index].pop("delay_after", None)
            return
        try:
            delay = max(0.0, float(raw_value))
        except ValueError:
            return
        sequence[index]["delay_after"] = round(delay, 4)

    def _update_step_delay_random(self, index: int, enabled: bool) -> None:
        if not self._sequences or not (0 <= self._selected_group < len(self._sequences)):
            return
        sequence = self._sequences[self._selected_group]
        if not 0 <= index < len(sequence):
            return
        if enabled:
            sequence[index]["delay_after_random"] = True
            sequence[index]["delay_after_random_range"] = max(
                0.0,
                float(sequence[index].get("delay_after_random_range", DEFAULT_RANDOM_KEY_STEP_RANDOM_RANGE) or 0.0),
            )
        else:
            sequence[index]["delay_after_random"] = False

    def _update_step_delay_random_range(self, index: int, value: str, *, normalize: bool = False) -> None:
        if not self._sequences or not (0 <= self._selected_group < len(self._sequences)):
            return
        sequence = self._sequences[self._selected_group]
        if not 0 <= index < len(sequence):
            return
        raw_value = str(value or "").strip().replace(",", ".")
        if not raw_value:
            sequence[index]["delay_after_random_range"] = DEFAULT_RANDOM_KEY_STEP_RANDOM_RANGE
            return
        try:
            delay_range = max(0.0, float(raw_value))
        except ValueError:
            return
        sequence[index]["delay_after_random_range"] = round(delay_range, 4)

    def _make_key_step(self, keys: List[str], key_events: List[Dict[str, Any]]) -> Dict[str, Any]:
        step = {
            "keys": [str(key).lower().strip() for key in keys if str(key).strip()],
            "key_events": [dict(event) for event in key_events],
        }
        return apply_random_key_step_defaults(step)

    def _reset_defaults(self) -> None:
        self._sequences = clone_default_random_key_sequences()
        self._selected_group = 0
        self._refresh_groups()
        self._refresh_steps()

    def _ensure_step_defaults(self) -> None:
        for sequence in self._sequences:
            for step in sequence:
                apply_random_key_step_defaults(step)

    def _save(self) -> None:
        self._ensure_step_defaults()
        sequences = normalize_random_key_sequences(self._sequences)
        if not sequences:
            messagebox.showerror("설정 필요", "랜덤키 묶음을 1개 이상 설정하세요.", parent=self)
            return
        self._result = (sequences, self._step_delay)
        self.destroy()

    def _cancel(self) -> None:
        self._result = None
        self.destroy()

    def get_result(self) -> Tuple[List[List[Dict[str, Any]]], float] | None:
        self.wait_window()
        return self._result
