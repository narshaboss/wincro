import threading
import tkinter as tk
import time
from collections import deque
from typing import Callable, Deque, Generic, List, Optional, TypeVar


T = TypeVar("T")


def resolve_widget_ui_post(widget) -> Callable[[Callable[[], None]], None]:
    """Resolve a thread-safe UI poster while still on the Tk main thread."""
    current = widget
    seen = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        dispatcher = getattr(current, "_ui_dispatcher", None)
        post = getattr(dispatcher, "post", None)
        if callable(post):
            return post
        current = getattr(current, "master", None)

    def _fallback(callback: Callable[[], None]) -> None:
        if threading.current_thread() is threading.main_thread():
            callback()
            return
        try:
            widget.after(0, callback)
        except (tk.TclError, RuntimeError, AttributeError):
            pass

    return _fallback


class LatestOnlyWorker:
    """Run one background job at a time and coalesce bursts to the latest job."""

    def __init__(
        self,
        name: str,
        error_callback: Optional[Callable[[Exception], None]] = None,
    ):
        self._name = str(name or "wincro-latest-worker")
        self._error_callback = error_callback
        self._lock = threading.Lock()
        self._pending_job: Optional[Callable[[], None]] = None
        self._active = False
        self._closed = False

    def submit(self, job: Callable[[], None]) -> bool:
        """Keep the newest pending job; return True only when a worker starts."""
        if not callable(job):
            raise TypeError("job must be callable")
        with self._lock:
            if self._closed:
                return False
            self._pending_job = job
            if self._active:
                return False
            self._active = True
        try:
            threading.Thread(target=self._run, name=self._name, daemon=True).start()
        except Exception as exc:
            with self._lock:
                self._active = False
            callback = self._error_callback
            if callback is not None:
                try:
                    callback(exc)
                except Exception:
                    pass
            return False
        return True

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._pending_job = None

    def is_active(self) -> bool:
        with self._lock:
            return self._active

    def pending_count(self) -> int:
        with self._lock:
            return int(self._pending_job is not None)

    def _run(self) -> None:
        while True:
            with self._lock:
                if self._closed:
                    self._pending_job = None
                    self._active = False
                    return
                job = self._pending_job
                self._pending_job = None
                if job is None:
                    self._active = False
                    return
            try:
                job()
            except Exception as exc:
                callback = self._error_callback
                if callback is not None:
                    try:
                        callback(exc)
                    except Exception:
                        pass


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
    """Batch UI callbacks onto the Tk main thread.

    Child widgets reuse the nearest ancestor dispatcher.  That keeps editor
    views from each installing their own 20 ms Tk polling timer while still
    preserving per-view queues and close semantics.
    """

    def __init__(
        self,
        widget,
        tick_ms: int = 25,
        max_callbacks_per_tick: int = 64,
        max_millis_per_tick: float = 8.0,
        max_queue_items: int = 2000,
    ):
        self._widget = widget
        self._tick_ms = max(10, int(tick_ms))
        self._max_callbacks_per_tick = max(1, int(max_callbacks_per_tick))
        self._max_millis_per_tick = max(1.0, float(max_millis_per_tick))
        self._max_queue_items = max(128, int(max_queue_items))
        self._queue: Deque[tuple[Callable[[], None], bool]] = deque()
        self._lock = threading.Lock()
        self._after_id: Optional[str] = None
        self._closed = False
        self._dropped_count = 0
        self._parent_dispatcher = self._find_parent_dispatcher(widget)
        self._delegated_drain_enqueued = False
        if self._parent_dispatcher is None:
            self._schedule_tick()

    @staticmethod
    def _find_parent_dispatcher(widget):
        current = getattr(widget, "master", None)
        seen = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            dispatcher = getattr(current, "_ui_dispatcher", None)
            if isinstance(dispatcher, UiCallbackDispatcher) and not dispatcher._closed:
                return dispatcher
            current = getattr(current, "master", None)
        return None

    def post(self, callback: Callable[[], None], *, critical: bool = False) -> bool:
        if self._closed:
            return False
        if threading.current_thread() is threading.main_thread():
            try:
                callback()
            except (tk.TclError, RuntimeError):
                pass
            return True
        delegate = None
        with self._lock:
            if self._closed:
                return False
            while len(self._queue) >= self._max_queue_items:
                drop_index = next(
                    (
                        index
                        for index, (_queued_callback, queued_critical) in enumerate(self._queue)
                        if not queued_critical
                    ),
                    None,
                )
                if drop_index is None:
                    if critical:
                        # At most one protected drain is queued per child
                        # dispatcher, so this can only exceed the cap by the
                        # small number of live child dispatchers.
                        break
                    self._dropped_count += 1
                    return False
                del self._queue[drop_index]
                self._dropped_count += 1
            self._queue.append((callback, bool(critical)))
            if self._parent_dispatcher is not None and not self._delegated_drain_enqueued:
                self._delegated_drain_enqueued = True
                delegate = self._parent_dispatcher
        if delegate is not None:
            accepted = delegate.post(self._drain, critical=True)
            if not accepted:
                with self._lock:
                    self._delegated_drain_enqueued = False
                return False
        return True

    def clear(self) -> None:
        with self._lock:
            self._queue.clear()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._queue.clear()
        try:
            if self._after_id is not None and self._widget.winfo_exists():
                self._widget.after_cancel(self._after_id)
        except (tk.TclError, RuntimeError):
            pass
        self._after_id = None

    def pending_count(self) -> int:
        with self._lock:
            return len(self._queue)

    def dropped_count(self) -> int:
        with self._lock:
            return self._dropped_count

    def _schedule_tick(self) -> None:
        if self._closed or self._parent_dispatcher is not None:
            return
        try:
            if self._widget.winfo_exists():
                self._after_id = self._widget.after(self._tick_ms, self._drain)
        except (tk.TclError, RuntimeError):
            self._after_id = None

    def _drain(self) -> None:
        processed = 0
        started_at = time.perf_counter()
        try:
            while processed < self._max_callbacks_per_tick and not self._closed:
                with self._lock:
                    if not self._queue:
                        break
                    callback, _critical = self._queue.popleft()
                try:
                    callback()
                except (tk.TclError, RuntimeError):
                    pass
                processed += 1
                elapsed_ms = (time.perf_counter() - started_at) * 1000
                if processed >= 1 and elapsed_ms >= self._max_millis_per_tick:
                    break
        finally:
            self._after_id = None
            if self._parent_dispatcher is None:
                self._schedule_tick()
            else:
                has_more = False
                with self._lock:
                    self._delegated_drain_enqueued = False
                    if self._queue and not self._closed:
                        self._delegated_drain_enqueued = True
                        has_more = True
                if has_more:
                    try:
                        if self._widget.winfo_exists():
                            self._after_id = self._widget.after(0, self._drain)
                    except (tk.TclError, RuntimeError):
                        with self._lock:
                            self._delegated_drain_enqueued = False


class BufferedRecordPump(Generic[T]):
    """Collect repeated UI records and flush them in small batches."""

    def __init__(
        self,
        widget,
        dispatcher: UiCallbackDispatcher,
        flush_callback: Callable[[List[T]], None],
        flush_interval_ms: int = 40,
        max_items_per_flush: int = 200,
        max_queue_items: int = 2000,
    ):
        self._widget = widget
        self._dispatcher = dispatcher
        self._flush_callback = flush_callback
        self._flush_interval_ms = max(10, int(flush_interval_ms))
        self._max_items_per_flush = max(1, int(max_items_per_flush))
        self._max_queue_items = max(self._max_items_per_flush, int(max_queue_items))
        self._queue: Deque[T] = deque()
        self._lock = threading.Lock()
        self._after_id: Optional[str] = None
        self._scheduled = False
        self._closed = False
        self._dropped_count = 0

    def push(self, item: T) -> None:
        should_schedule = False
        with self._lock:
            if self._closed:
                return
            while len(self._queue) >= self._max_queue_items:
                self._queue.popleft()
                self._dropped_count += 1
            self._queue.append(item)
            if not self._scheduled:
                self._scheduled = True
                should_schedule = True
        if should_schedule:
            accepted = self._dispatcher.post(self._schedule_flush, critical=True)
            if not accepted:
                with self._lock:
                    self._scheduled = False

    def clear(self) -> None:
        with self._lock:
            self._queue.clear()
            self._scheduled = False
            after_id = self._after_id
            self._after_id = None
        try:
            if after_id is not None and self._widget.winfo_exists():
                self._widget.after_cancel(after_id)
        except (tk.TclError, RuntimeError):
            pass

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._queue.clear()
            self._scheduled = False
            after_id = self._after_id
            self._after_id = None
        try:
            if after_id is not None and self._widget.winfo_exists():
                self._widget.after_cancel(after_id)
        except (tk.TclError, RuntimeError):
            pass

    def pending_count(self) -> int:
        with self._lock:
            return len(self._queue)

    def dropped_count(self) -> int:
        with self._lock:
            return self._dropped_count

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
                accepted = self._dispatcher.post(self._schedule_flush, critical=True)
                if not accepted:
                    with self._lock:
                        self._scheduled = False
