"""Modal editor for persistent window selectors, never a window-action preview."""

from __future__ import annotations

import copy
from collections.abc import Callable
import tkinter as tk

import customtkinter as ctk

from ..utils.window_control import (
    MAX_WINDOW_CONTROL_TARGETS,
    WindowController,
    normalize_window_control_config,
    validate_window_control_config,
)
from .theme import COLORS, IOS_FONTS, IOS_METRICS


OPERATION_LABELS = {
    "최대화": "maximize",
    "최소화": "minimize",
    "앞으로 가져오기": "foreground",
}
MATCH_MODE_LABELS = {
    "실행 파일명 + 창 제목": "title",
    "실행 파일명만": "executable",
}
PREFERRED_SIZE = (760, 720)
MINIMUM_SIZE = (420, 360)
SCREEN_MARGIN = (48, 100)


def _bounded_size(screen_width: int, screen_height: int, scale: float) -> tuple:
    """Convert physical screen bounds to CTk's logical geometry units."""
    available_width = max(1, int((screen_width - SCREEN_MARGIN[0]) / scale))
    available_height = max(1, int((screen_height - SCREEN_MARGIN[1]) / scale))
    width = min(PREFERRED_SIZE[0], available_width)
    height = min(PREFERRED_SIZE[1], available_height)
    minimum = (min(MINIMUM_SIZE[0], width), min(MINIMUM_SIZE[1], height))
    return width, height, minimum


class WindowControlDialog(ctk.CTkToplevel):
    """Edit a window-control action without operating on any target window.

    ``on_save`` is optional and synchronous. It must persist the parent document
    durably, reload/verify it, and return exactly True on success. False or an
    exception leaves this editor open. Without it, Apply returns a draft only;
    the caller must retain it and save the parent document before discarding it.
    """

    def __init__(self, parent, config=None, *, on_save: Callable[[dict], bool] | None = None):
        config = normalize_window_control_config(copy.deepcopy(config))
        if on_save is not None and not callable(on_save):
            raise TypeError("on_save must be callable or None")
        self._previous_grab = None
        self._previous_grab_status = None
        super().__init__(parent)
        self._result: dict | None = None
        self._on_save = on_save
        self._saving = False
        self._windows = []
        self._rows = []
        self._row_labels = []
        self._editor_labels = []
        self._selected_handle = None
        self._manual_target = False
        self._enumeration_error = ""
        self._single_selector = {
            key: config[key] for key in ("executable_name", "title_contains", "match_mode")
            if key in config
        }
        self._targets = copy.deepcopy(config.get("targets", []))
        self._target_index = 0
        self._displaying_multi = False
        self._loading_selector = False
        self._has_multi_config = "multi_enabled" in config
        self._multi_var = tk.BooleanVar(self, value=config.get("multi_enabled", False))
        self._target_choice_var = tk.StringVar(self, value="")
        self._target_choices = {}
        self._executable_var = tk.StringVar(self, value=config["executable_name"])
        self._title_var = tk.StringVar(self, value=config["title_contains"])
        match_mode_label = next(
            (label for label, value in MATCH_MODE_LABELS.items()
             if value == config.get("match_mode", "title")),
            "찾기 방식을 선택하세요",
        )
        self._match_mode_var = tk.StringVar(self, value=match_mode_label)
        self._selection_var = tk.StringVar(self, value="")
        operation_label = next(
            (label for label, value in OPERATION_LABELS.items() if value == config["operation"]),
            "동작을 선택하세요",
        )
        self._operation_var = tk.StringVar(self, value=operation_label)

        self.title("창 제어 설정")
        self.configure(fg_color=COLORS["bg_content"])
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.bind("<Escape>", lambda _event: self._cancel())
        self._configure_geometry()
        self._build_ui()
        self._multi_changed(initial=True)
        self._sync_title_entry()
        self._refresh_windows()
        self._executable_var.trace_add("write", self._criteria_changed)
        self._title_var.trace_add("write", self._criteria_changed)
        self._previous_grab = self.grab_current()
        if self._previous_grab is not None:
            self._previous_grab_status = self._previous_grab.grab_status()
        self.grab_set()

    def _configure_geometry(self) -> None:
        # CTk scales dimensions, but not geometry offsets. Do not scale twice.
        scale = self._get_window_scaling()
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        width, height, minimum = _bounded_size(screen_width, screen_height, scale)
        self.minsize(*minimum)
        self.maxsize(
            max(width, int((screen_width - SCREEN_MARGIN[0]) / scale)),
            max(height, int((screen_height - SCREEN_MARGIN[1]) / scale)),
        )
        x = max(0, int((screen_width - width * scale) / 2))
        # Reserve the lower screen strip for the taskbar and non-client chrome.
        y = max(0, int((screen_height - height * scale - SCREEN_MARGIN[1]) / 2))
        self.geometry(f"{width}x{height}+{x}+{y}")

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

    def _label(self, parent, text, *, heading=False):
        return ctk.CTkLabel(
            parent, text=text, width=1, anchor="w", justify="left",
            font=self._font(13 if heading else 12, "bold" if heading else "normal"),
            text_color=COLORS["accent_text"] if heading else COLORS["text_secondary"],
        )

    def _build_ui(self) -> None:
        padding = IOS_METRICS["window_padding"]
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1, minsize=90)
        self.grid_rowconfigure(2, weight=2, minsize=60)
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=padding, pady=(12, 6))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            header, text="창 제어", anchor="w", font=self._font(20, "bold"),
            text_color=COLORS["text_primary"],
        ).grid(row=0, column=0, sticky="ew")
        self._multi_toggle = ctk.CTkCheckBox(
            header, text="멀티", variable=self._multi_var, command=self._multi_changed,
            width=80, font=self._font(13, "bold"), text_color=COLORS["text_primary"],
            fg_color=COLORS["accent_blue"], hover_color=COLORS["hover_blue"],
            border_color=COLORS["border"],
        )
        self._multi_toggle.grid(row=0, column=1, padx=(12, 0))

        list_card = self._card(self)
        list_card.grid(row=1, column=0, sticky="nsew", padx=padding, pady=(0, 8))
        list_card.grid_columnconfigure(0, weight=1)
        list_card.grid_rowconfigure(1, weight=1)
        toolbar = ctk.CTkFrame(list_card, fg_color="transparent")
        toolbar.grid(row=0, column=0, sticky="ew", padx=12, pady=(8, 4))
        self._list_status = self._label(toolbar, "실행 중인 창", heading=True)
        self._list_status.pack(side="left", fill="x", expand=True)
        self._refresh_button = ctk.CTkButton(
            toolbar, text="새로고침", command=self._refresh_windows, width=90,
            height=IOS_METRICS["button_height_small"],
            corner_radius=IOS_METRICS["control_radius_small"],
            fg_color=COLORS["accent_blue"], hover_color=COLORS["hover_blue"],
            text_color=COLORS["text_on_accent"], font=self._font(12, "bold"),
        )
        self._refresh_button.pack(side="right", padx=(8, 0))
        self._window_list = ctk.CTkScrollableFrame(
            list_card, height=150, fg_color="transparent", corner_radius=0,
        )
        self._window_list.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        self._window_list.grid_columnconfigure(0, weight=1)
        self._window_list.bind("<Configure>", self._resize_list_labels, add="+")

        # Sibling scroll areas avoid nested CTk mouse-wheel handlers scrolling twice.
        self._editor = ctk.CTkScrollableFrame(
            self, height=300, fg_color=COLORS["bg_glass"],
            corner_radius=IOS_METRICS["card_radius_compact"],
            border_width=IOS_METRICS["card_border_width"], border_color=COLORS["border"],
        )
        self._editor.grid(row=2, column=0, sticky="nsew", padx=padding, pady=(0, 8))
        self._editor.grid_columnconfigure(0, weight=1)
        self._editor.bind("<Configure>", self._resize_editor_labels, add="+")
        self._build_target_controls()
        self._label(self._editor, "찾기 방식", heading=True).grid(
            row=1, column=0, sticky="ew", padx=10, pady=(4, 2),
        )
        self._match_mode_menu = ctk.CTkOptionMenu(
            self._editor, variable=self._match_mode_var, values=list(MATCH_MODE_LABELS),
            command=self._match_mode_changed, height=IOS_METRICS["button_height_small"],
            fg_color=COLORS["bg_card"], button_color=COLORS["bg_elevated"],
            button_hover_color=COLORS["bg_card_hover"], text_color=COLORS["text_primary"],
            dropdown_fg_color=COLORS["bg_card"], dropdown_hover_color=COLORS["bg_card_hover"],
            dropdown_text_color=COLORS["text_primary"], font=self._font(12),
            dropdown_font=self._font(12), corner_radius=IOS_METRICS["control_radius_small"],
        )
        self._match_mode_menu.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 6))
        executable_header = ctk.CTkFrame(self._editor, fg_color="transparent")
        executable_header.grid(row=3, column=0, sticky="ew", padx=10, pady=(4, 2))
        self._label(executable_header, "실행 파일명", heading=True).pack(side="left")
        self._manual_button = ctk.CTkButton(
            executable_header, text="직접 입력", command=self._enable_manual_target,
            width=90, height=IOS_METRICS["button_height_small"],
            fg_color=COLORS["bg_card"], hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_primary"], font=self._font(12, "bold"),
            corner_radius=IOS_METRICS["control_radius_small"],
        )
        self._manual_button.pack(side="right")
        self._executable_entry = self._entry(self._executable_var, 3)
        self._executable_note = self._editor_note("", 4)
        self._title_label = self._label(self._editor, "창 제목에 포함할 문자열", heading=True)
        self._title_label.grid(
            row=6, column=0, sticky="ew", padx=10, pady=(8, 2),
        )
        self._title_entry = self._entry(self._title_var, 6)
        self._label(self._editor, "동작", heading=True).grid(
            row=8, column=0, sticky="ew", padx=10, pady=(8, 2),
        )
        self._operation_menu = ctk.CTkOptionMenu(
            self._editor, variable=self._operation_var, values=list(OPERATION_LABELS),
            height=IOS_METRICS["button_height_small"],
            fg_color=COLORS["bg_card"], button_color=COLORS["bg_elevated"],
            button_hover_color=COLORS["bg_card_hover"], text_color=COLORS["text_primary"],
            dropdown_fg_color=COLORS["bg_card"], dropdown_hover_color=COLORS["bg_card_hover"],
            dropdown_text_color=COLORS["text_primary"], font=self._font(12),
            dropdown_font=self._font(12), corner_radius=IOS_METRICS["control_radius_small"],
        )
        self._operation_menu.grid(row=9, column=0, sticky="ew", padx=10, pady=(0, 6))
        self._match_note = self._editor_note("", 9)
        self._editor_note(
            "실행 파일명만으로 찾으면 이벤트 페이지 등 제목 변경에 영향받지 않습니다. "
            "같은 조건에 실제 창이 여러 개면 대기하므로 제목으로 구분하세요. "
            "멀티는 없는 후보만 즉시 넘기며, 모두 없을 때 기존 S(스킵)를 적용합니다. "
            "이 설정 창에서는 대상 창을 조작하지 않습니다.", 10,
        )
        self._editor_note(
            "적용 후 상위 창에서 저장해야 유지됩니다."
            if self._on_save is None else "저장 시 현재 플랜/재생목록도 함께 저장됩니다.", 11,
        )

        # The footer is not inside either scroll area, including its error display.
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=3, column=0, sticky="ew", padx=padding, pady=(0, 12))
        footer.grid_columnconfigure((0, 1), weight=1, uniform="buttons")
        self._error_label = self._label(footer, "")
        self._error_label.configure(text_color=COLORS["error"])
        self._error_label.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 4))
        footer.bind("<Configure>", self._resize_error_label, add="+")
        self._save_button = ctk.CTkButton(
            footer, text="저장" if self._on_save is not None else "적용", command=self._save,
            height=IOS_METRICS["button_height"], fg_color=COLORS["success"],
            hover_color=COLORS["green_hover"], text_color=COLORS["text_on_accent"],
            font=self._font(14, "bold"), corner_radius=IOS_METRICS["pill_radius"],
        )
        self._save_button.grid(row=1, column=0, sticky="ew", padx=(0, 6))
        self._cancel_button = ctk.CTkButton(
            footer, text="취소", command=self._cancel,
            height=IOS_METRICS["button_height"], fg_color=COLORS["bg_card"],
            hover_color=COLORS["bg_card_hover"], text_color=COLORS["text_primary"],
            font=self._font(14, "bold"), corner_radius=IOS_METRICS["pill_radius"],
        )
        self._cancel_button.grid(row=1, column=1, sticky="ew", padx=(6, 0))

    def _entry(self, variable, row):
        entry = ctk.CTkEntry(
            self._editor, textvariable=variable, height=IOS_METRICS["button_height_small"],
            font=self._font(12), fg_color=COLORS["bg_card"],
            text_color=COLORS["text_primary"], border_color=COLORS["border"],
            corner_radius=IOS_METRICS["control_radius_small"],
        )
        entry.grid(row=row + 1, column=0, sticky="ew", padx=10, pady=(0, 4))
        return entry

    def _editor_note(self, text, row):
        label = self._label(self._editor, text)
        label.configure(wraplength=300)
        label.grid(row=row + 1, column=0, sticky="ew", padx=10, pady=(0, 6))
        self._editor_labels.append(label)
        return label

    def _build_target_controls(self) -> None:
        self._target_panel = ctk.CTkFrame(self._editor, fg_color="transparent")
        self._target_panel.grid(row=0, column=0, sticky="ew", padx=10, pady=(4, 10))
        self._target_panel.grid_columnconfigure(0, weight=1)
        self._target_heading = self._label(self._target_panel, "우선순위 후보", heading=True)
        self._target_heading.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 4))
        self._target_menu = ctk.CTkOptionMenu(
            self._target_panel, variable=self._target_choice_var, values=["1번 후보"],
            command=self._choose_target, dynamic_resizing=False,
            height=IOS_METRICS["button_height_small"],
            fg_color=COLORS["bg_card"], button_color=COLORS["bg_elevated"],
            button_hover_color=COLORS["bg_card_hover"], text_color=COLORS["text_primary"],
            dropdown_fg_color=COLORS["bg_card"], dropdown_hover_color=COLORS["bg_card_hover"],
            dropdown_text_color=COLORS["text_primary"], font=self._font(12),
            dropdown_font=self._font(12), corner_radius=IOS_METRICS["control_radius_small"],
        )
        self._target_menu.grid(row=1, column=0, sticky="ew", padx=(0, 6))
        self._target_up = ctk.CTkButton(
            self._target_panel, text="▲", width=30, height=30, command=lambda: self._move_target(-1),
            fg_color=COLORS["bg_card"], hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["accent_text"], font=self._font(12, "bold"),
        )
        self._target_down = ctk.CTkButton(
            self._target_panel, text="▼", width=30, height=30, command=lambda: self._move_target(1),
            fg_color=COLORS["bg_card"], hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["accent_text"], font=self._font(12, "bold"),
        )
        self._target_up.grid(row=1, column=1, padx=(0, 4))
        self._target_down.grid(row=1, column=2)
        buttons = ctk.CTkFrame(self._target_panel, fg_color="transparent")
        buttons.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(6, 4))
        buttons.grid_columnconfigure((0, 1), weight=1, uniform="candidate-buttons")
        self._target_add = ctk.CTkButton(
            buttons, text="+ 후보 추가", command=self._add_target, width=90, height=28,
            fg_color=COLORS["accent_blue"], hover_color=COLORS["hover_blue"],
            text_color=COLORS["text_on_accent"], font=self._font(12, "bold"),
        )
        self._target_delete = ctk.CTkButton(
            buttons, text="후보 삭제", command=self._delete_target, width=90, height=28,
            fg_color=COLORS["bg_card"], hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_primary"], font=self._font(12, "bold"),
        )
        self._target_add.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self._target_delete.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        self._target_hint = self._label(
            self._target_panel,
            "순서대로 찾아 처음 발견한 창 하나만 제어합니다. 선택한 후보의 조건을 아래에서 편집하세요.",
        )
        self._target_hint.configure(wraplength=280)
        self._target_hint.grid(row=3, column=0, columnspan=3, sticky="ew")

    def _selector_from_form(self) -> dict:
        selector = {"executable_name": self._executable_var.get().strip(),
                    "title_contains": self._title_var.get().strip()}
        mode = MATCH_MODE_LABELS.get(self._match_mode_var.get())
        if mode == "executable":
            selector.update(match_mode=mode, title_contains="")
        elif mode is None:
            selector["match_mode"] = self._match_mode_var.get()
        return selector

    def _stash_selector(self) -> None:
        if self._loading_selector:
            return
        selector = self._selector_from_form()
        if self._displaying_multi:
            self._targets[self._target_index] = selector
        else:
            self._single_selector = selector

    def _load_selector(self, selector) -> None:
        self._loading_selector = True
        try:
            self._executable_var.set(selector.get("executable_name", ""))
            self._title_var.set(selector.get("title_contains", ""))
            self._match_mode_var.set(next(
                (label for label, mode in MATCH_MODE_LABELS.items()
                 if mode == selector.get("match_mode", "title")), "찾기 방식을 선택하세요",
            ))
            self._manual_target, self._selected_handle = False, None
        finally:
            self._loading_selector = False
        self._sync_title_entry()
        self._sync_executable_entry()
        self._sync_selection()
        self._highlight_selection()
        self._update_match_note()

    def _multi_changed(self, *, initial=False) -> None:
        if self._saving:
            return
        self._stash_selector()
        if not initial:
            self._has_multi_config = True
        self._displaying_multi = bool(self._multi_var.get())
        if self._displaying_multi:
            if not self._targets:
                self._targets = [copy.deepcopy(self._single_selector)]
            self._target_index = min(self._target_index, len(self._targets) - 1)
            self._target_panel.grid()
            self._load_selector(self._targets[self._target_index])
        else:
            self._target_panel.grid_remove()
            self._load_selector(self._single_selector)
        self._refresh_target_controls()

    def _refresh_target_controls(self) -> None:
        self._target_heading.configure(text=f"우선순위 후보 ({len(self._targets)}개)")
        choices = {}
        for index, selector in enumerate(self._targets):
            name = selector.get("executable_name", "") or "새 창 조건"
            if len(name) > 26:
                name = name[:19] + "..." + name[-4:]
            choices[f"{index + 1}. {name}"] = index
        self._target_choices = choices
        self._target_menu.configure(values=list(choices) or ["후보 없음"])
        if choices:
            self._target_choice_var.set(list(choices)[self._target_index])
        enabled = self._displaying_multi and not self._saving
        for widget, available in (
            (self._target_menu, bool(choices)),
            (self._target_add, len(self._targets) < MAX_WINDOW_CONTROL_TARGETS),
            (self._target_delete, len(self._targets) > 1),
            (self._target_up, self._target_index > 0),
            (self._target_down, self._target_index < len(self._targets) - 1),
        ):
            widget.configure(state="normal" if enabled and available else "disabled")

    def _choose_target(self, label) -> None:
        if self._saving or not self._displaying_multi:
            return
        index = self._target_choices.get(label)
        if index is None:
            return
        self._stash_selector()
        self._target_index = index
        self._load_selector(self._targets[index])
        self._refresh_target_controls()

    def _add_target(self) -> None:
        if self._saving or not self._displaying_multi or len(self._targets) >= MAX_WINDOW_CONTROL_TARGETS:
            return
        self._stash_selector()
        selector = {"executable_name": "", "title_contains": ""}
        if MATCH_MODE_LABELS.get(self._match_mode_var.get()) == "executable":
            selector["match_mode"] = "executable"
        self._targets.append(selector)
        self._target_index = len(self._targets) - 1
        self._load_selector(selector)
        self._refresh_target_controls()
        self._enable_manual_target()

    def _delete_target(self) -> None:
        if self._saving or not self._displaying_multi or len(self._targets) <= 1:
            return
        self._targets.pop(self._target_index)
        self._target_index = min(self._target_index, len(self._targets) - 1)
        self._load_selector(self._targets[self._target_index])
        self._refresh_target_controls()

    def _move_target(self, delta) -> None:
        destination = self._target_index + delta
        if self._saving or not self._displaying_multi or not 0 <= destination < len(self._targets):
            return
        self._stash_selector()
        self._targets[self._target_index], self._targets[destination] = (
            self._targets[destination], self._targets[self._target_index],
        )
        self._target_index = destination
        self._refresh_target_controls()

    def _resize_list_labels(self, event) -> None:
        width = max(40, int(event.width / self._window_list._get_widget_scaling()) - 70)
        for label in self._row_labels:
            if label.cget("wraplength") != width:
                label.configure(wraplength=width)

    def _resize_editor_labels(self, event) -> None:
        width = max(40, int(event.width / self._editor._get_widget_scaling()) - 24)
        for label in self._editor_labels:
            if label.cget("wraplength") != width:
                label.configure(wraplength=width)
        self._target_hint.configure(wraplength=width)

    def _resize_error_label(self, event) -> None:
        width = max(40, int(event.width / self._save_button._get_widget_scaling()))
        if self._error_label.cget("wraplength") != width:
            self._error_label.configure(wraplength=width)

    def _refresh_windows(self) -> None:
        if self._saving:
            return
        self._refresh_button.configure(state="disabled")
        try:
            windows = list(WindowController().list_windows())
        except Exception as exc:
            # A failed refresh invalidates the old live snapshot, not the user's draft.
            self._windows = []
            self._enumeration_error = str(exc) or type(exc).__name__
        else:
            self._windows = windows
            self._enumeration_error = ""
        finally:
            self._refresh_button.configure(state="normal")
        self._list_status.configure(text=f"실행 중인 창 ({len(self._windows)})")
        self._sync_selection()
        self._render_windows()
        self._sync_executable_entry()
        self._update_match_note()

    def _matching_windows(self):
        executable = self._executable_var.get().strip().casefold()
        title = self._title_var.get().strip().casefold()
        mode = MATCH_MODE_LABELS.get(self._match_mode_var.get())
        if not executable or mode is None or (mode == "title" and not title):
            return []
        return [
            window for window in self._windows
            if window.executable_name.casefold() == executable
            and (mode == "executable" or title in window.title.casefold())
        ]

    def _sync_title_entry(self) -> None:
        title_unused = MATCH_MODE_LABELS.get(self._match_mode_var.get()) == "executable"
        self._title_entry.configure(state="disabled" if self._saving or title_unused else "normal")
        self._title_label.configure(
            text="창 제목 조건 (사용 안 함)" if title_unused else "창 제목에 포함할 문자열",
        )

    def _match_mode_changed(self, _label=None) -> None:
        if self._saving:
            return
        self._sync_title_entry()
        self._sync_selection()
        self._highlight_selection()
        self._update_match_note()
        self._stash_selector()
        self._refresh_target_controls()

    def _sync_selection(self) -> None:
        matches = self._matching_windows()
        if self._manual_target:
            self._selected_handle = None
        elif len(matches) == 1:
            self._selected_handle = matches[0].handle
        elif not any(window.handle == self._selected_handle for window in matches):
            self._selected_handle = None
        self._selection_var.set("" if self._selected_handle is None else str(self._selected_handle))

    def _render_windows(self) -> None:
        for child in self._window_list.winfo_children():
            child.destroy()
        self._rows = []
        self._row_labels = []
        if not self._windows:
            label = self._label(
                self._window_list,
                "목록을 읽지 못했습니다. 새로고침하거나 파일명을 직접 입력하세요."
                if self._enumeration_error else "실행 중인 대상이 없어도 파일명을 직접 입력할 수 있습니다.",
            )
            label.configure(wraplength=300)
            label.grid(row=0, column=0, sticky="ew", padx=8, pady=8)
            self._row_labels.append(label)
            return
        for index, window in enumerate(self._windows):
            row = ctk.CTkFrame(
                self._window_list, fg_color=COLORS["bg_card"],
                corner_radius=IOS_METRICS["control_radius_small"],
                border_width=IOS_METRICS["card_border_width"], border_color=COLORS["border"],
            )
            row.grid(row=index, column=0, sticky="ew", pady=3)
            row.grid_columnconfigure(1, weight=1)
            command = lambda selected=window: self._select_window(selected)
            ctk.CTkRadioButton(
                row, text="", width=24, variable=self._selection_var, value=str(window.handle),
                command=command, fg_color=COLORS["accent_blue"],
                hover_color=COLORS["hover_blue"], border_color=COLORS["border"],
            ).grid(row=0, column=0, rowspan=2, padx=(10, 4), pady=8)
            title = self._label(row, window.title or "(제목 없음)", heading=True)
            executable = self._label(row, window.executable_name)
            for number, label in enumerate((title, executable)):
                label.configure(wraplength=300)
                label.grid(row=number, column=1, sticky="ew", padx=(0, 10), pady=(4, 2))
                label.bind("<Button-1>", lambda _event, selected=window: self._select_window(selected))
                self._row_labels.append(label)
            row.bind("<Button-1>", lambda _event, selected=window: self._select_window(selected))
            self._rows.append((window.handle, row))
        self._highlight_selection()

    def _highlight_selection(self) -> None:
        for handle, row in self._rows:
            row.configure(fg_color=COLORS["bg_elevated"] if handle == self._selected_handle else COLORS["bg_card"])

    def _select_window(self, window) -> None:
        if self._saving:
            return
        self._manual_target = False
        self._selected_handle = window.handle
        self._selection_var.set(str(window.handle))
        self._executable_var.set(window.executable_name)
        self._title_var.set(window.title)
        self._sync_executable_entry()
        self._highlight_selection()
        self._update_match_note()

    def _enable_manual_target(self) -> None:
        if self._saving:
            return
        self._manual_target = True
        self._selected_handle = None
        self._selection_var.set("")
        self._highlight_selection()
        self._sync_executable_entry()
        self._executable_entry.focus_set()

    def _sync_executable_entry(self) -> None:
        executable = self._executable_var.get().strip().casefold()
        present = any(window.executable_name.casefold() == executable for window in self._windows)
        readonly = present and not self._manual_target
        self._executable_entry.configure(state="readonly" if readonly else "normal")
        self._executable_note.configure(
            text="선택한 실행 파일명입니다. 다른 대상은 '직접 입력'을 누르세요."
            if readonly else "경로가 아닌 파일명만 입력하세요 (예: app.exe). 실행 중이 아니어도 설정할 수 있습니다.",
        )

    def _criteria_changed(self, *_args) -> None:
        # Only inspect the cached snapshot; no native calls or row rebuilding here.
        if self._loading_selector:
            return
        self._update_match_note()
        self._stash_selector()
        if self._displaying_multi:
            self._refresh_target_controls()

    def _update_match_note(self) -> None:
        matches = self._matching_windows()
        if self._enumeration_error:
            text = f"창 목록 조회 실패: {self._enumeration_error[:180]}"
            color = COLORS["warning"]
        elif len(matches) > 1:
            text = f"현재 목록에 {len(matches)}개 창이 일치합니다. 중복 조건에서는 실행되지 않습니다. 제목 조건 방식으로 구분하세요."
            color = COLORS["warning"]
        elif matches:
            text = "현재 목록에 1개 창이 일치합니다. 실제 실행 시 다시 찾습니다."
            color = COLORS["text_secondary"]
        else:
            text = "현재 일치하는 창이 없습니다. 설정은 유지되며 실제 실행 시 다시 찾습니다."
            color = COLORS["text_secondary"]
        self._match_note.configure(text=text, text_color=color)

    def _collect_config(self) -> dict:
        self._stash_selector()
        operation = OPERATION_LABELS.get(self._operation_var.get())
        if operation is None:
            raise ValueError("동작을 선택하세요.")
        # Explicit allowlist: never retain a handle, PID, path or unrelated config key.
        config = {**copy.deepcopy(self._single_selector), "operation": operation}
        if self._has_multi_config:
            config.update(multi_enabled=bool(self._multi_var.get()), targets=copy.deepcopy(self._targets))
        return validate_window_control_config(config)

    def _set_saving(self, saving: bool) -> None:
        self._saving = saving
        state = "disabled" if saving else "normal"
        for widget in (
            self._save_button, self._cancel_button, self._refresh_button,
            self._manual_button, self._title_entry, self._operation_menu,
            self._match_mode_menu,
            self._multi_toggle,
        ):
            widget.configure(state=state)
        if saving:
            self._executable_entry.configure(state="disabled")
        else:
            self._sync_executable_entry()
            self._sync_title_entry()
            self._sync_selection()
            self._highlight_selection()
        self._refresh_target_controls()

    def _save(self) -> None:
        if self._saving:
            return
        self._error_label.configure(text="")
        try:
            config = self._collect_config()
        except (ValueError, KeyError) as exc:
            self._error_label.configure(text=str(exc)[:180] or "입력 내용을 확인하세요.")
            return
        if self._on_save is not None:
            self._set_saving(True)
            try:
                if self._on_save(copy.deepcopy(config)) is not True:
                    self._error_label.configure(
                        text="저장 완료를 확인하지 못했습니다. 변경사항은 편집기에 남아 있습니다. 다시 저장하세요.",
                    )
                    return
            except Exception as exc:
                self._error_label.configure(
                    text=f"저장 실패: {str(exc)[:120] or type(exc).__name__}\n"
                    "변경사항은 편집기에 남아 있습니다. 다시 저장하세요.",
                )
                return
            finally:
                self._set_saving(False)
        self._result = copy.deepcopy(config)
        self.grab_release()
        self.destroy()

    def _cancel(self) -> None:
        if self._saving:
            return
        self._result = None
        self.grab_release()
        self.destroy()

    def destroy(self) -> None:
        previous = self._previous_grab
        previous_status = self._previous_grab_status
        self._previous_grab = None
        self._previous_grab_status = None
        try:
            super().destroy()
        finally:
            # Restore Tk modality, not OS focus. Never steal a newer modal's grab.
            if previous is not None:
                try:
                    if previous.winfo_exists() and previous.grab_current() is None:
                        if previous_status == "global":
                            previous.grab_set_global()
                        elif previous_status == "local":
                            previous.grab_set()
                except tk.TclError:
                    # The owning document or Tcl interpreter may be closing too.
                    pass

    def get_result(self) -> dict | None:
        self.wait_window()
        return copy.deepcopy(self._result)
