from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ANALYZER_VIEW = ROOT / "src" / "ui" / "analyzer_view.py"
RECORDER_VIEW = ROOT / "src" / "ui" / "recorder_view.py"
MAIN_WINDOW = ROOT / "src" / "ui" / "main_window.py"
VIRTUAL_SCROLL = ROOT / "src" / "ui" / "virtual_scroll.py"


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def test_virtual_scroll_supports_scroll_preservation():
    text = _read_text(VIRTUAL_SCROLL)

    assert 'def set_items(self, items: list, preserve_scroll: bool = False):' in text
    assert 'scroll_pos = self._canvas.yview()[0]' in text
    assert 'self._canvas.yview_moveto(max(0.0, min(scroll_pos, 1.0)))' in text


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
