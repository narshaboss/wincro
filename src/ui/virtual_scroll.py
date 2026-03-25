"""
WinCro virtual scroll frame.

Only visible items are rendered to keep large lists responsive.
"""

import customtkinter as ctk
import tkinter as tk

from .theme import COLORS


class VirtualScrollFrame(ctk.CTkFrame):
    """Virtualized scroll frame for large lists."""

    def __init__(self, parent, item_height=75, buffer_count=3, **kwargs):
        if "fg_color" not in kwargs:
            kwargs["fg_color"] = COLORS["bg_card"]
        super().__init__(parent, **kwargs)

        self._item_height = item_height
        self._buffer_count = buffer_count
        self._items = []
        self._visible_widgets = {}
        self._render_callback = None
        self._scroll_scheduled = False

        self._canvas = tk.Canvas(
            self,
            bg=self._apply_appearance_mode(COLORS["bg_card"]),
            highlightthickness=0,
            borderwidth=0,
        )

        self._scrollbar = ctk.CTkScrollbar(self, command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._scrollbar.set)

        self._scrollbar.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)

        self._container = ctk.CTkFrame(self._canvas, fg_color="transparent")
        self._canvas_window = self._canvas.create_window(
            (0, 0), window=self._container, anchor="nw", tags="container"
        )

        self._canvas.bind("<Configure>", self._on_canvas_configure)
        self._canvas.bind("<MouseWheel>", self._on_mousewheel)
        self._canvas.bind("<Button-4>", self._on_mousewheel_linux)
        self._canvas.bind("<Button-5>", self._on_mousewheel_linux)
        self._container.bind("<MouseWheel>", self._on_mousewheel)

        self._scrollbar.bind("<B1-Motion>", lambda e: self._schedule_render())
        self._scrollbar.bind("<ButtonRelease-1>", lambda e: self._schedule_render())

    def _apply_appearance_mode(self, color):
        if isinstance(color, tuple):
            return color[1] if ctk.get_appearance_mode() == "Dark" else color[0]
        return color

    def set_render_callback(self, callback):
        self._render_callback = callback

    def set_items(self, items: list, preserve_scroll: bool = False):
        scroll_pos = 0.0
        if preserve_scroll:
            try:
                scroll_pos = self._canvas.yview()[0]
            except (tk.TclError, RuntimeError, ValueError, IndexError):
                scroll_pos = 0.0

        self._items = items
        self._clear_all_widgets()
        self._update_scroll_region()

        if preserve_scroll:
            try:
                self._canvas.yview_moveto(max(0.0, min(scroll_pos, 1.0)))
            except (tk.TclError, RuntimeError, ValueError):
                pass

        self._render_visible_items()

    def get_items(self):
        return self._items

    def refresh(self):
        self._clear_all_widgets()
        self._update_scroll_region()
        self._render_visible_items()

    def refresh_item(self, index: int):
        if index in self._visible_widgets:
            self._visible_widgets[index].destroy()
            del self._visible_widgets[index]
            self._render_single_item(index)

    def _clear_all_widgets(self):
        for widget in self._visible_widgets.values():
            try:
                widget.destroy()
            except (tk.TclError, RuntimeError, AttributeError):
                pass
        self._visible_widgets.clear()

    def _update_scroll_region(self):
        total_height = len(self._items) * self._item_height
        canvas_width = self._canvas.winfo_width()
        if canvas_width < 10:
            canvas_width = 800
        self._canvas.configure(scrollregion=(0, 0, canvas_width, total_height))
        self._canvas.itemconfig(self._canvas_window, width=canvas_width)
        self._container.configure(height=total_height)

    def _on_canvas_configure(self, event):
        self._canvas.itemconfig(self._canvas_window, width=event.width)
        self._update_scroll_region()
        self._schedule_render()

    def _on_mousewheel(self, event):
        delta = -1 * (event.delta // 120)
        self._canvas.yview_scroll(delta, "units")
        self._schedule_render()

    def _on_mousewheel_linux(self, event):
        if event.num == 4:
            self._canvas.yview_scroll(-1, "units")
        elif event.num == 5:
            self._canvas.yview_scroll(1, "units")
        self._schedule_render()

    def _schedule_render(self):
        if not self._scroll_scheduled:
            self._scroll_scheduled = True
            self.after(10, self._do_scheduled_render)

    def _do_scheduled_render(self):
        self._scroll_scheduled = False
        self._render_visible_items()

    def _get_visible_range(self):
        if not self._items:
            return 0, 0

        canvas_height = self._canvas.winfo_height()
        if canvas_height < 10:
            canvas_height = 600

        scroll_top = self._canvas.yview()[0]
        scroll_bottom = self._canvas.yview()[1]
        total_height = len(self._items) * self._item_height

        top_px = scroll_top * total_height
        bottom_px = scroll_bottom * total_height

        start_idx = max(0, int(top_px / self._item_height) - self._buffer_count)
        end_idx = min(len(self._items), int(bottom_px / self._item_height) + self._buffer_count + 1)
        return start_idx, end_idx

    def _render_visible_items(self):
        if not self._items or not self._render_callback:
            return

        start_idx, end_idx = self._get_visible_range()

        to_remove = [idx for idx in self._visible_widgets if idx < start_idx or idx >= end_idx]
        for idx in to_remove:
            try:
                self._visible_widgets[idx].destroy()
            except (tk.TclError, RuntimeError, AttributeError):
                pass
            del self._visible_widgets[idx]

        for idx in range(start_idx, end_idx):
            if idx not in self._visible_widgets:
                self._render_single_item(idx)

    def _render_single_item(self, index: int):
        if index < 0 or index >= len(self._items):
            return

        if self._render_callback:
            item_data = self._items[index]
            widget = self._render_callback(self._container, item_data, index)
            if widget:
                y_pos = index * self._item_height
                widget.place(x=0, y=y_pos, relwidth=1.0)
                self._visible_widgets[index] = widget
                self._bind_mousewheel_recursive(widget)

    def _bind_mousewheel_recursive(self, widget):
        try:
            widget.bind("<MouseWheel>", self._on_mousewheel, add="+")
            widget.bind("<Button-4>", self._on_mousewheel_linux, add="+")
            widget.bind("<Button-5>", self._on_mousewheel_linux, add="+")
            for child in widget.winfo_children():
                self._bind_mousewheel_recursive(child)
        except (tk.TclError, RuntimeError, AttributeError):
            pass

    def scroll_to_item(self, index: int):
        if not self._items:
            return
        index = max(0, min(index, len(self._items) - 1))
        total_height = len(self._items) * self._item_height
        y_pos = index * self._item_height
        self._canvas.yview_moveto(y_pos / total_height)
        self._schedule_render()
