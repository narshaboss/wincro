import queue
import threading
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from src.player.rule_executor import RuleExecutor
from src.ui import analyzer_view
from src.ui.recorder_view import RecorderView, TemplateMediaItem
from src.ui.ui_batcher import LatestOnlyWorker


ROOT = Path(__file__).resolve().parents[1]


def test_latest_only_worker_coalesces_burst_to_newest_job():
    first_started = threading.Event()
    release_first = threading.Event()
    latest_finished = threading.Event()
    executed = []
    worker = LatestOnlyWorker("test-latest-only")

    def first_job():
        executed.append("first")
        first_started.set()
        assert release_first.wait(2.0)

    assert worker.submit(first_job) is True
    assert first_started.wait(2.0)

    for value in range(100):
        def pending_job(item=value):
            executed.append(item)
            if item == 99:
                latest_finished.set()

        worker.submit(pending_job)

    assert worker.pending_count() == 1
    release_first.set()
    assert latest_finished.wait(2.0)
    deadline = time.monotonic() + 2.0
    while worker.is_active() and time.monotonic() < deadline:
        time.sleep(0.01)
    worker.close()

    assert executed == ["first", 99]


def test_thumbnail_queue_keeps_only_newest_work_when_full():
    task_queue = queue.Queue(maxsize=3)
    tasks = [lambda value=value: value for value in range(10)]

    dropped = 0
    for task in tasks:
        dropped += analyzer_view._enqueue_thumbnail_task(task_queue, task)

    remaining = []
    while not task_queue.empty():
        remaining.append(task_queue.get_nowait()())
        task_queue.task_done()

    assert dropped == 7
    assert remaining == [7, 8, 9]


def test_thumbnail_queue_cleans_up_evicted_work():
    task_queue = queue.Queue(maxsize=1)
    cleaned = []
    first = analyzer_view._QueuedThumbnailTask(lambda: None, lambda: cleaned.append("first"))
    second = analyzer_view._QueuedThumbnailTask(lambda: None, lambda: cleaned.append("second"))

    assert analyzer_view._enqueue_thumbnail_task(task_queue, first) == 0
    assert analyzer_view._enqueue_thumbnail_task(task_queue, second) == 1

    remaining = task_queue.get_nowait()
    task_queue.task_done()
    assert cleaned == ["first"]
    remaining.discard()
    assert cleaned == ["first", "second"]


def test_thumbnail_caches_are_true_lru_and_bounded(monkeypatch):
    monkeypatch.setattr(analyzer_view, "MAX_THUMBNAIL_CACHE", 3)
    analyzer_view._thumbnail_cache.clear()

    analyzer_view.set_cached_thumbnail("one.png", (10, 10), "one")
    analyzer_view.set_cached_thumbnail("two.png", (10, 10), "two")
    analyzer_view.set_cached_thumbnail("three.png", (10, 10), "three")
    assert analyzer_view.get_cached_thumbnail("one.png", (10, 10)) == "one"
    analyzer_view.set_cached_thumbnail("four.png", (10, 10), "four")

    assert len(analyzer_view._thumbnail_cache) == 3
    assert analyzer_view.get_cached_thumbnail("two.png", (10, 10)) is None
    assert analyzer_view.get_cached_thumbnail("one.png", (10, 10)) == "one"


def test_monitoring_scan_reuses_one_capture_then_releases_it(tmp_path, monkeypatch):
    import src.player.rule_executor as rule_executor_module

    rng = np.random.default_rng(20260801)
    frame = rng.integers(0, 256, size=(40, 50, 3), dtype=np.uint8)
    template_bgr = frame[11:19, 17:26].copy()
    template_gray = cv2.cvtColor(template_bgr, cv2.COLOR_BGR2GRAY)
    image_path = tmp_path / "template.png"
    image_path.write_bytes(b"exists")
    captures = []

    def fake_capture():
        captures.append(1)
        return frame.copy()

    monkeypatch.setattr(rule_executor_module, "_grab_screen_bgr", fake_capture)
    monkeypatch.setattr(
        rule_executor_module,
        "_get_cached_template_variants",
        lambda _path: [(template_gray, 8, 9, template_bgr, "base")],
    )

    executor = RuleExecutor()
    executor._begin_shared_image_scan()
    first = executor._find_image_on_screen(str(image_path), confidence=0.99)
    second = executor._find_image_on_screen(str(image_path), confidence=0.99)
    executor._end_shared_image_scan()
    third = executor._find_image_on_screen(str(image_path), confidence=0.99)

    assert first is not None
    assert second is not None
    assert third is not None
    assert len(captures) == 2


def test_repeated_stop_uses_one_join_worker(monkeypatch):
    import src.player.rule_executor as rule_executor_module

    join_started = threading.Event()
    release_join = threading.Event()

    class BlockingExecutionThread:
        def is_alive(self):
            return not release_join.is_set()

        def join(self, timeout=None):
            join_started.set()
            release_join.wait(1.0)

    class FakeInputController:
        def release_all(self):
            return None

    monkeypatch.setattr(rule_executor_module, "block_automation_input", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(rule_executor_module, "get_input_controller", lambda: FakeInputController())

    executor = RuleExecutor()
    executor._execution_thread = BlockingExecutionThread()
    executor.stop()
    assert join_started.wait(1.0)

    for _ in range(20):
        executor.stop()

    matching = [
        thread
        for thread in threading.enumerate()
        if thread.name == "wincro-rule-stop-join" and thread.is_alive()
    ]
    assert len(matching) == 1
    release_join.set()


def test_dialog_thumbnail_references_and_monitoring_callbacks_are_bounded():
    analyzer_text = (ROOT / "src" / "ui" / "analyzer_view.py").read_text(encoding="utf-8-sig")
    player_text = (ROOT / "src" / "ui" / "player_view.py").read_text(encoding="utf-8-sig")
    monitoring_text = (ROOT / "src" / "ui" / "monitoring_editor.py").read_text(encoding="utf-8-sig")

    assert "deque(maxlen=MAX_DIALOG_THUMBNAIL_REFS)" in analyzer_text
    assert "deque(maxlen=MAX_DIALOG_THUMBNAIL_REFS)" in player_text
    assert "ui_post = resolve_widget_ui_post(self)" in monitoring_text
    assert "self._pending_thumbnail_labels.clear()" in monitoring_text
    assert "self._render_after_ids.clear()" in monitoring_text
    assert "on_drop=lambda: ui_post(" in monitoring_text


def test_recorder_thumbnail_decode_is_bounded_and_runs_without_tk(tmp_path):
    image_path = tmp_path / "sample.png"
    image = np.zeros((120, 300, 3), dtype=np.uint8)
    image[:, :150] = (0, 128, 255)
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    encoded.tofile(str(image_path))
    media = TemplateMediaItem(
        path=str(image_path),
        name=image_path.name,
        media_type="image",
        size_bytes=image_path.stat().st_size,
        modified_at=datetime.fromtimestamp(image_path.stat().st_mtime),
    )

    decoded = RecorderView._decode_media_thumbnail(None, media)

    assert decoded is not None
    pil_image, width, height = decoded
    assert pil_image.size == (width, height)
    assert 1 <= width <= 54
    assert 1 <= height <= 54


def test_recorder_thumbnail_loading_stays_off_tk_thread():
    source = (ROOT / "src" / "ui" / "recorder_view.py").read_text(encoding="utf-8-sig")

    assert "self._recordings_loader.submit(_load)" in source
    assert "submit_thumbnail_task(_load)" in source
    assert "ui_post(_apply)" in source
    assert "deque(maxlen=192)" in source


def test_background_ui_results_use_dispatchers_in_recorder_and_monitoring_editor():
    recorder_source = (ROOT / "src" / "ui" / "recorder_view.py").read_text(encoding="utf-8-sig")
    monitoring_source = (ROOT / "src" / "ui" / "monitoring_editor.py").read_text(encoding="utf-8-sig")

    assert "self._recorder_ui_post(lambda: self._on_recording_stopped(result))" in recorder_source
    assert "self._recorder_ui_post(self._on_start_recording)" in recorder_source
    assert "self._recorder_ui_post(self._on_stop_recording)" in recorder_source
    assert monitoring_source.count("ui_post(show_result)") >= 2
    assert "self.after(0, show_result)" not in monitoring_source
