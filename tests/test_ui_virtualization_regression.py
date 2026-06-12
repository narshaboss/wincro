import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ANALYZER_VIEW = ROOT / "src" / "ui" / "analyzer_view.py"
RECORDER_VIEW = ROOT / "src" / "ui" / "recorder_view.py"
MAIN_WINDOW = ROOT / "src" / "ui" / "main_window.py"
VIRTUAL_SCROLL = ROOT / "src" / "ui" / "virtual_scroll.py"
MONITORING_EDITOR = ROOT / "src" / "ui" / "monitoring_editor.py"
PLAYER_VIEW = ROOT / "src" / "ui" / "player_view.py"
UI_BATCHER = ROOT / "src" / "ui" / "ui_batcher.py"


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def test_virtual_scroll_supports_scroll_preservation():
    text = _read_text(VIRTUAL_SCROLL)

    assert 'def set_items(self, items: list, preserve_scroll: bool = False):' in text
    assert 'scroll_pos = self._canvas.yview()[0]' in text
    assert 'self._canvas.yview_moveto(max(0.0, min(scroll_pos, 1.0)))' in text
    assert 'if isinstance(item, tuple) and item:' in text
    assert 'getattr(obj, "plan_id", None)' in text
    assert 'getattr(obj, "id", None)' in text


def test_virtual_scroll_cancels_pending_render_on_destroy():
    text = _read_text(VIRTUAL_SCROLL)
    schedule_method = text[
        text.index("def _schedule_render(self):"):
        text.index("def _do_scheduled_render(self):")
    ]
    run_method = text[
        text.index("def _do_scheduled_render(self):"):
        text.index("def _get_visible_range(self):")
    ]
    destroy_method = text[
        text.index("def destroy(self):"):
    ]

    assert "self._scroll_after_id = None" in text
    assert "self._scroll_after_id = self.after(10, self._do_scheduled_render)" in schedule_method
    assert "self._scroll_after_id = None" in run_method
    assert "self.after_cancel(after_id)" in destroy_method
    assert "self._clear_all_widgets()" in destroy_method
    assert "super().destroy()" in destroy_method


def test_ui_callback_dispatcher_limits_work_by_time_budget():
    text = _read_text(UI_BATCHER)
    init_method = text[
        text.index("def __init__(\n        self,\n        widget,"):
        text.index("def post(self, callback", text.index("class UiCallbackDispatcher"))
    ]
    drain_method = text[
        text.index("def _drain(self) -> None:"):
        text.index("class BufferedRecordPump", text.index("def _drain(self) -> None:"))
    ]

    assert "import time" in text
    assert "max_millis_per_tick: float = 8.0" in init_method
    assert "self._max_millis_per_tick = max(1.0, float(max_millis_per_tick))" in init_method
    assert "started_at = time.perf_counter()" in drain_method
    assert "processed < self._max_callbacks_per_tick" in drain_method
    assert "elapsed_ms = (time.perf_counter() - started_at) * 1000" in drain_method
    assert "elapsed_ms >= self._max_millis_per_tick" in drain_method


def test_analyzer_view_uses_virtual_lists_and_async_apply():
    text = _read_text(ANALYZER_VIEW)

    assert 'from .virtual_scroll import VirtualScrollFrame' in text
    assert 'list_frame = VirtualScrollFrame(' in text
    assert 'self._plans_scroll = VirtualScrollFrame(' in text
    assert 'self._recordings_scroll = VirtualScrollFrame(' in text
    assert 'def _render_image_row(self, parent, item_data, _index: int):' in text
    assert 'def _load_recordings_async(self):' in text
    assert 'def _apply_recordings(self, recordings, generation=None):' in text
    assert 'self._plans_scroll.set_items(self._plan_items, preserve_scroll=True)' in text
    assert 'self._recordings_scroll.set_items(self._recording_items, preserve_scroll=True)' in text


def test_recorder_view_uses_virtual_list_and_async_apply():
    text = _read_text(RECORDER_VIEW)

    assert 'from .virtual_scroll import VirtualScrollFrame' in text
    assert 'from .ui_batcher import UiCallbackDispatcher' in text
    assert 'self._recordings_scroll = VirtualScrollFrame(' in text
    assert 'def _refresh_recordings_list_async(self):' in text
    assert 'def _apply_recordings_list(self, recordings, generation=None):' in text
    assert 'self._recordings_scroll.set_items(self._recording_items, preserve_scroll=True)' in text


def test_main_window_defers_hidden_view_refreshes():
    text = _read_text(MAIN_WINDOW)

    assert 'self._dirty_views = set()' in text
    assert 'if view_id == self._current_view:' in text
    assert 'self._dirty_views.add(view_id)' in text
    assert 'def _refresh_view_if_needed(self, view_id: Optional[str]):' in text
    assert 'if view_id in self._dirty_views:' in text


def test_monitoring_editor_single_watch_changes_do_not_rebuild_entire_list():
    text = _read_text(MONITORING_EDITOR)

    assert "def _create_watch_item(self, idx: int, watch: dict, before_widget=None):" in text
    assert "def _refresh_watch_item(self, idx: int) -> None:" in text
    assert "self._watch_action_card_widgets = {}" in text
    assert "def _append_monitor_action_card(self, watch_idx):" in text
    assert "def _refresh_monitor_action_card(self, watch_idx, action_idx):" in text
    assert "if not self._append_monitor_action_card(i):" in text
    assert "if not self._refresh_monitor_action_card(i, a):" in text
    assert "self._watch_widgets[idx] = self._create_watch_item" in text
    assert "self._refresh_watch_item(i)" in text
    assert "self._refresh_watch_list()" in text


def test_monitoring_editor_watch_list_full_refresh_renders_in_batches():
    text = _read_text(MONITORING_EDITOR)
    refresh_method = text[
        text.index("def _refresh_watch_list(self):"):
        text.index("def _create_watch_item(self, idx: int, watch: dict, before_widget=None):")
    ]
    destroy_method = text[
        text.index("def destroy(self):"):
        text.index("# ------------------------------------------------------------------", text.index("def destroy(self):"))
    ]

    assert "self._watch_render_after_id = None" in text
    assert "self._watch_render_generation = 0" in text
    assert "self._watch_render_batch_size = 12" in text
    assert "self._cancel_watch_list_render_batch()" in refresh_method
    assert "self._watch_widgets = [None] * len(self._watches_data)" in refresh_method
    assert "def _render_watch_list_batch(self, start: int, generation: int):" in refresh_method
    assert "start + self._watch_render_batch_size" in refresh_method
    assert "self._schedule_watch_list_render_batch(" in refresh_method
    assert "self.after_cancel(after_id)" in refresh_method
    assert "self._cancel_watch_list_render_batch()" in destroy_method
    assert "super().destroy()" in destroy_method


def test_monitoring_editor_watch_list_thumbnails_do_not_decode_on_ui_thread():
    text = _read_text(MONITORING_EDITOR)
    helper = text[
        text.index("def _schedule_watch_thumbnail(self, label, path, size=(28, 28)):"):
        text.index("@staticmethod\n    def _convert_rule_to_monitor_action", text.index("def _schedule_watch_thumbnail"))
    ]
    header = text[
        text.index("def _build_watch_header(self, item_frame, idx, watch, is_collapsed):"):
        text.index("def _build_watch_play_button(self, header_row, idx):")
    ]

    assert "from .analyzer_view import get_cached_thumbnail, set_cached_thumbnail, submit_thumbnail_task" in text
    assert "submit_thumbnail_task(load_thumbnail)" in helper
    assert "ctk.CTkImage(" in helper
    assert helper.index("def load_thumbnail(") < helper.index("ctk.CTkImage(")
    assert "self.after(0, apply_thumbnail)" in helper
    assert "self._schedule_watch_thumbnail(thumb_label, watch.get(\"image\"), size=(28, 28))" in header
    assert "self._load_thumbnail(watch" not in header


def test_player_sequence_picker_uses_virtual_scroll_instead_of_full_rebuild():
    text = _read_text(PLAYER_VIEW)
    start = text.index("def _render_sequence_list(self):")
    end = text.index("def _setup_ui(self) -> None:")
    sequence_render = text[start:end]

    assert "self._sequence_frame = VirtualScrollFrame(" in text
    assert "self._sequence_frame.set_render_callback(self._render_sequence_list_item)" in text
    assert "self._sequence_frame.set_items(items, preserve_scroll=True)" in sequence_render
    assert "winfo_children()" not in sequence_render
    assert "widget.destroy()" not in sequence_render


def test_player_view_avoids_reentrant_tk_update_calls():
    text = _read_text(PLAYER_VIEW)

    assert not re.search(r"\bself\.update\(\)", text)


def test_player_view_skips_duplicate_high_frequency_progress_updates():
    text = _read_text(PLAYER_VIEW)
    player_view_text = text[text.index("class PlayerView(BaseView):"):]
    init_method = text[
        text.index("def __init__(self, parent, **kwargs):", text.index("class PlayerView(BaseView):")):
        text.index("def after(self, ms, func=None, *args):", text.index("class PlayerView(BaseView):"))
    ]
    plan_method = player_view_text[
        player_view_text.index("def _update_plan_progress(self, current: int, total: int, message: str) -> None:"):
        player_view_text.index("def _on_plan_complete(self, success: bool, message: str) -> None:")
    ]
    playback_method = player_view_text[
        player_view_text.index("def _update_progress_snapshot(self, current_step: int, total_steps: int, progress_percent: float) -> None:"):
        player_view_text.index("def _on_action_start(self, index: int, action: Action) -> None:")
    ]
    action_method = player_view_text[
        player_view_text.index("def _flush_action_text_update(self) -> None:"):
        player_view_text.index("def _deferred_load(self):")
    ]

    assert "self._last_plan_progress_snapshot = None" in init_method
    assert "self._last_playback_progress_snapshot = None" in init_method
    assert "self._last_action_text = None" in init_method
    assert "if snapshot == self._last_plan_progress_snapshot:" in plan_method
    assert "if message != self._last_action_text:" in plan_method
    assert "if snapshot == self._last_playback_progress_snapshot:" in playback_method
    assert "if text == self._last_action_text:" in action_method
