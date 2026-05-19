import threading
import tkinter as tk
from collections import deque
from typing import Callable, Deque, Generic, List, Optional, TypeVar


T = TypeVar("T")


def dispatch_widget_after(widget, dispatcher, direct_after: Callable, ms, func=None, *args):
    """Route worker-thread after() calls onto the Tk main thread."""
    if func is None:
        return direct_after(ms)
    if dispatcher is None or threading.current_thread() is threading.main_thread():
        return direct_after(ms, func, *args)

    def _schedule_on_main():
        try:
            if not widget.winfo_exists():
                return
            direct_after(ms, func, *args)
        except (tk.TclError, RuntimeError):
            pass

    dispatcher.post(_schedule_on_main)
    return None


class UiCallbackDispatcher:
    """Batch UI callbacks onto the Tk main thread at a steady cadence."""

    def __init__(self, widget, tick_ms: int = 25, max_callbacks_per_tick: int = 64):
        self._widget = widget
        self._tick_ms = max(10, int(tick_ms))
        self._max_callbacks_per_tick = max(1, int(max_callbacks_per_tick))
        self._queue: Deque[Callable[[], None]] = deque()
        self._lock = threading.Lock()
        self._after_id: Optional[str] = None
        self._closed = False
        self._schedule_tick()

    def post(self, callback: Callable[[], None]) -> None:
        if self._closed:
            return
        if threading.current_thread() is threading.main_thread():
            try:
                callback()
            except (tk.TclError, RuntimeError):
                pass
            return
        with self._lock:
            self._queue.append(callback)

    def clear(self) -> None:
        with self._lock:
            self._queue.clear()

    def close(self) -> None:
        self._closed = True
        self.clear()
        try:
            if self._after_id is not None and self._widget.winfo_exists():
                self._widget.after_cancel(self._after_id)
        except (tk.TclError, RuntimeError):
            pass
        self._after_id = None

    def pending_count(self) -> int:
        with self._lock:
            return len(self._queue)

    def _schedule_tick(self) -> None:
        if self._closed:
            return
        try:
            if self._widget.winfo_exists():
                self._after_id = self._widget.after(self._tick_ms, self._drain)
        except (tk.TclError, RuntimeError):
            self._after_id = None

    def _drain(self) -> None:
        pending: List[Callable[[], None]] = []
        try:
            with self._lock:
                while self._queue and len(pending) < self._max_callbacks_per_tick:
                    pending.append(self._queue.popleft())
            for callback in pending:
                try:
                    callback()
                except (tk.TclError, RuntimeError):
                    pass
        finally:
            self._after_id = None
            self._schedule_tick()


class BufferedRecordPump(Generic[T]):
    """Collect repeated UI records and flush them in small batches."""

    def __init__(
        self,
        widget,
        dispatcher: UiCallbackDispatcher,
        flush_callback: Callable[[List[T]], None],
        flush_interval_ms: int = 40,
        max_items_per_flush: int = 200,
    ):
        self._widget = widget
        self._dispatcher = dispatcher
        self._flush_callback = flush_callback
        self._flush_interval_ms = max(10, int(flush_interval_ms))
        self._max_items_per_flush = max(1, int(max_items_per_flush))
        self._queue: Deque[T] = deque()
        self._lock = threading.Lock()
        self._after_id: Optional[str] = None
        self._scheduled = False
        self._closed = False

    def push(self, item: T) -> None:
        if self._closed:
            return
        should_schedule = False
        with self._lock:
            self._queue.append(item)
            if not self._scheduled:
                self._scheduled = True
                should_schedule = True
        if should_schedule:
            self._dispatcher.post(self._schedule_flush)

    def clear(self) -> None:
        with self._lock:
            self._queue.clear()
            self._scheduled = False
        try:
            if self._after_id is not None and self._widget.winfo_exists():
                self._widget.after_cancel(self._after_id)
        except (tk.TclError, RuntimeError):
            pass
        self._after_id = None

    def close(self) -> None:
        self._closed = True
        self.clear()

    def pending_count(self) -> int:
        with self._lock:
            return len(self._queue)

    def _schedule_flush(self) -> None:
        if self._closed:
            return
        try:
            if not self._widget.winfo_exists():
                self.clear()
                return
            self._after_id = self._widget.after(self._flush_interval_ms, self._flush)
        except (tk.TclError, RuntimeError):
            self.clear()

    def _flush(self) -> None:
        records: List[T] = []
        try:
            with self._lock:
                while self._queue and len(records) < self._max_items_per_flush:
                    records.append(self._queue.popleft())
                has_more = bool(self._queue)
                self._scheduled = has_more
            if records:
                self._flush_callback(records)
        finally:
            self._after_id = None
            if self._closed:
                has_more = False
            else:
                with self._lock:
                    has_more = bool(self._queue)
                    self._scheduled = has_more
            if has_more:
                self._dispatcher.post(self._schedule_flush)
