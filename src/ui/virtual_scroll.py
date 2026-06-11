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
        self._surface_color = kwargs["fg_color"]
        super().__init__(parent, **kwargs)

        self._item_height = item_height
        self._buffer_count = buffer_count
        self._items = []
        self._item_index_by_identity = {}
        self._item_index_dirty = False
        self._visible_widgets = {}
        self._render_callback = None
        self._destroy_callback = None
        self._update_callback = None
        self._scroll_scheduled = False

        self._canvas = tk.Canvas(
            self,
            bg=self._apply_appearance_mode(self._surface_color),
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

    def set_destroy_callback(self, callback):
        self._destroy_callback = callback

    def set_update_callback(self, callback):
        self._update_callback = callback

    def set_items(self, items: list, preserve_scroll: bool = False):
        scroll_pos = 0.0
        if preserve_scroll:
            try:
                scroll_pos = self._canvas.yview()[0]
            except (tk.TclError, RuntimeError, ValueError, IndexError):
                scroll_pos = 0.0

        old_items = self._items
        old_visible_widgets = dict(self._visible_widgets)
        old_visible_by_key = {}
        for index, widget in old_visible_widgets.items():
            if 0 <= index < len(old_items):
                item = old_items[index]
                old_visible_by_key[self._item_identity(item)] = (
                    index,
                    widget,
                    self._item_layout_signature(item),
                    item,
                )

        self._items = items
        self._item_index_by_identity = {}
        self._item_index_dirty = True
        self._visible_widgets = {}
        self._update_scroll_region()

        if preserve_scroll:
            try:
                self._canvas.yview_moveto(max(0.0, min(scroll_pos, 1.0)))
            except (tk.TclError, RuntimeError, ValueError):
                pass

        used_old_indices = set()
        start_idx, end_idx = self._get_visible_range()
        for index in range(start_idx, end_idx):
            item = self._items[index]
            old_entry = old_visible_by_key.get(self._item_identity(item))
            if old_entry is None:
                continue
            old_index, widget, old_signature, old_item = old_entry
            if old_signature != self._item_layout_signature(item):
                continue
            try:
                widget.place_configure(y=index * self._item_height)
            except (tk.TclError, RuntimeError):
                self._visible_widgets.pop(index, None)
                continue
            used_old_indices.add(old_index)
            self._visible_widgets[index] = widget
            self._update_visible_widget(index, widget, item, old_item)

        for old_index, widget in old_visible_widgets.items():
            if old_index in used_old_indices:
                continue
            old_item = old_items[old_index] if 0 <= old_index < len(old_items) else None
            self._destroy_visible_widget(old_index, widget, item_data=old_item)

        self._render_visible_items()

    def get_items(self):
        return self._items

    def refresh(self):
        self._clear_all_widgets()
        self._update_scroll_region()
        self._render_visible_items()

    def refresh_item(self, index: int):
        if index in self._visible_widgets:
            widget = self._visible_widgets.pop(index)
            self._destroy_visible_widget(index, widget)
            self._render_single_item(index)

    def splice_items(self, start: int, delete_count: int, new_items: list):
        """Patch a small visible-list range without rebuilding every visible row."""
        old_items = self._items
        old_len = len(old_items)
        old_visible_widgets = dict(self._visible_widgets)
        old_visible_items = {
            index: old_items[index]
            for index in old_visible_widgets
            if 0 <= index < old_len
        }
        start = max(0, min(start, old_len))
        delete_count = max(0, min(delete_count, old_len - start))
        end = start + delete_count
        delta = len(new_items) - delete_count

        new_items = list(new_items)
        self._items[start:end] = new_items
        self._patch_item_index_for_splice(None, start, delete_count, new_items)
        self._visible_widgets = {}
        self._update_scroll_region()

        visible_start, visible_end = self._get_visible_range()
        for old_index, widget in old_visible_widgets.items():
            if old_index < start:
                new_index = old_index
            elif old_index >= end:
                new_index = old_index + delta
            else:
                old_item = old_visible_items.get(old_index)
                self._destroy_visible_widget(old_index, widget, item_data=old_item)
                continue

            if not (visible_start <= new_index < visible_end):
                old_item = old_visible_items.get(old_index)
                self._destroy_visible_widget(old_index, widget, item_data=old_item)
                continue

            old_item = old_visible_items.get(old_index)
            new_item = self._items[new_index] if 0 <= new_index < len(self._items) else None
            if (
                old_item is None
                or new_item is None
                or self._item_identity(old_item) != self._item_identity(new_item)
                or self._item_layout_signature(old_item) != self._item_layout_signature(new_item)
            ):
                self._destroy_visible_widget(old_index, widget, item_data=old_item)
                continue

            try:
                widget.place_configure(y=new_index * self._item_height)
            except (tk.TclError, RuntimeError):
                self._destroy_visible_widget(old_index, widget, item_data=old_item)
                continue
            self._visible_widgets[new_index] = widget
            self._update_visible_widget(new_index, widget, new_item, old_item)

        self._render_visible_items()

    def replace_items_range(self, start: int, delete_count: int, new_items: list):
        """Replace a small item range while reusing moved visible rows by identity."""
        old_visible_widgets = dict(self._visible_widgets)
        old_len = len(self._items)
        start = max(0, min(start, old_len))
        delete_count = max(0, min(delete_count, old_len - start))
        end = start + delete_count

        old_visible_by_key = {}
        for index, widget in old_visible_widgets.items():
            if 0 <= index < len(self._items):
                item = self._items[index]
                old_visible_by_key[self._item_identity(item)] = (
                    index,
                    widget,
                    self._item_layout_signature(item),
                    item,
                )

        old_identities = [
            self._item_identity(self._items[index])
            for index in range(start, end)
        ]
        new_items = list(new_items)
        self._items[start:end] = new_items
        self._patch_item_index_for_replace(start, delete_count, new_items, old_identities)
        self._visible_widgets = {}
        self._update_scroll_region()

        used_old_indices = set()
        visible_start, visible_end = self._get_visible_range()
        for index in range(visible_start, visible_end):
            item = self._items[index]
            old_entry = old_visible_by_key.get(self._item_identity(item))
            if old_entry is None:
                continue
            old_index, widget, old_signature, old_item = old_entry
            if old_signature != self._item_layout_signature(item):
                continue
            try:
                widget.place_configure(y=index * self._item_height)
            except (tk.TclError, RuntimeError):
                self._destroy_visible_widget(old_index, widget, item_data=old_item)
                continue
            used_old_indices.add(old_index)
            self._visible_widgets[index] = widget
            self._update_visible_widget(index, widget, item, old_item)

        for old_index, widget in old_visible_widgets.items():
            if old_index in used_old_indices:
                continue
            old_item = None
            for candidate_index, _widget, _signature, candidate_item in old_visible_by_key.values():
                if candidate_index == old_index:
                    old_item = candidate_item
                    break
            self._destroy_visible_widget(old_index, widget, item_data=old_item)

        self._render_visible_items()

    def update_items_metadata(self, items: list) -> bool:
        """Update item metadata such as numbering without recreating visible rows."""
        items = list(items)
        if len(items) != len(self._items):
            self.set_items(items, preserve_scroll=True)
            return False

        for old_item, new_item in zip(self._items, items):
            if (
                self._item_identity(old_item) != self._item_identity(new_item)
                or self._item_layout_signature(old_item) != self._item_layout_signature(new_item)
            ):
                self.set_items(items, preserve_scroll=True)
                return False

        old_items = self._items
        self._items = items
        for index, widget in list(self._visible_widgets.items()):
            if 0 <= index < len(self._items):
                old_item = old_items[index] if 0 <= index < len(old_items) else None
                self._update_visible_widget(index, widget, self._items[index], old_item)
        return True

    def update_visible_items_metadata(self, updater) -> bool:
        """Update metadata only for currently rendered rows."""
        if updater is None:
            return False
        for index, widget in list(self._visible_widgets.items()):
            if not (0 <= index < len(self._items)):
                continue
            old_item = self._items[index]
            new_item = updater(index, old_item)
            if new_item is None:
                continue
            if (
                self._item_identity(old_item) != self._item_identity(new_item)
                or self._item_layout_signature(old_item) != self._item_layout_signature(new_item)
            ):
                self.refresh_item(index)
                continue
            self._items[index] = new_item
            self._update_visible_widget(index, widget, new_item, old_item)
        return True

    def _clear_all_widgets(self):
        for index, widget in list(self._visible_widgets.items()):
            self._destroy_visible_widget(index, widget)
        self._visible_widgets.clear()

    def _item_identity(self, item):
        if isinstance(item, dict):
            obj = item.get("rule") or item.get("action")
            obj_id = getattr(obj, "rule_id", None) or getattr(obj, "action_id", None)
            if obj_id:
                return (type(obj).__name__, obj_id)
            if obj is not None:
                return ("object", id(obj))
        return ("item", id(item))

    def _object_identity(self, obj):
        obj_id = getattr(obj, "rule_id", None) or getattr(obj, "action_id", None)
        if obj_id:
            return (type(obj).__name__, obj_id)
        return ("object", id(obj))

    def _rebuild_item_index(self):
        self._item_index_by_identity = {}
        for index, item in enumerate(self._items):
            self._item_index_by_identity.setdefault(self._item_identity(item), index)
        self._item_index_dirty = False

    def _patch_item_index_for_splice(self, old_items, start: int, delete_count: int, new_items: list):
        if not hasattr(self, "_item_index_by_identity"):
            self._rebuild_item_index()
            return
        self._item_index_dirty = True

    def _patch_item_index_for_replace(self, start: int, delete_count: int, new_items: list, old_identities=None):
        if not hasattr(self, "_item_index_by_identity"):
            self._rebuild_item_index()
            return
        if getattr(self, "_item_index_dirty", False):
            return

        end = start + delete_count
        delta = len(new_items) - delete_count
        if delta == 0 and old_identities is not None:
            for identity in old_identities:
                self._item_index_by_identity.pop(identity, None)
            for offset, item in enumerate(new_items):
                identity = self._item_identity(item)
                new_index = start + offset
                if identity not in self._item_index_by_identity or new_index < self._item_index_by_identity[identity]:
                    self._item_index_by_identity[identity] = new_index
            return

        next_index = {}
        for identity, index in self._item_index_by_identity.items():
            if start <= index < end:
                continue
            if index >= end:
                index += delta
            next_index[identity] = index

        for offset, item in enumerate(new_items):
            identity = self._item_identity(item)
            new_index = start + offset
            if identity not in next_index or new_index < next_index[identity]:
                next_index[identity] = new_index

        self._item_index_by_identity = next_index

    def find_item_index_by_object(self, obj) -> int:
        if obj is None:
            return -1
        if not hasattr(self, "_item_index_by_identity") or getattr(self, "_item_index_dirty", False):
            self._rebuild_item_index()
        return self._item_index_by_identity.get(self._object_identity(obj), -1)

    def find_item_index_by_object_id(self, object_id: str, type_name: str = "") -> int:
        if not object_id:
            return -1
        if not hasattr(self, "_item_index_by_identity") or getattr(self, "_item_index_dirty", False):
            self._rebuild_item_index()
        if type_name:
            return self._item_index_by_identity.get((type_name, object_id), -1)
        for (_kind, candidate_id), index in self._item_index_by_identity.items():
            if candidate_id == object_id:
                return index
        return -1

    def _item_layout_signature(self, item):
        if isinstance(item, dict):
            return (
                self._item_identity(item),
                item.get("depth"),
                item.get("parent_id"),
            )
        return (self._item_identity(item),)

    def _update_visible_widget(self, index, widget, item_data, old_item_data=None):
        if self._update_callback:
            try:
                self._update_callback(item_data, index, widget, old_item_data)
            except Exception:
                pass

    def _destroy_visible_widget(self, index, widget, item_data=None):
        if self._destroy_callback:
            try:
                if item_data is None and 0 <= index < len(self._items):
                    item_data = self._items[index]
                if item_data is not None:
                    self._destroy_callback(item_data, index, widget)
            except Exception:
                pass
        try:
            widget.destroy()
        except (tk.TclError, RuntimeError, AttributeError):
            pass

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
        total_height = len(self._items) * self._item_height

        top_px = scroll_top * total_height
        # yview()[1] can temporarily report 1.0 before Tk finishes layout after a
        # scrollregion change. Use the actual viewport height so initial refreshes
        # never render the whole list.
        bottom_px = top_px + canvas_height

        start_idx = max(0, int(top_px / self._item_height) - self._buffer_count)
        end_idx = min(len(self._items), int(bottom_px / self._item_height) + self._buffer_count + 1)
        return start_idx, end_idx

    def _render_visible_items(self):
        if not self._items or not self._render_callback:
            return

        start_idx, end_idx = self._get_visible_range()

        to_remove = [idx for idx in self._visible_widgets if idx < start_idx or idx >= end_idx]
        for idx in to_remove:
            widget = self._visible_widgets.pop(idx)
            self._destroy_visible_widget(idx, widget)

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
