"""Progress estimates and UI wiring without desktop input or real inference."""

from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from src.ui.analysis_progress import AnalysisProgress
from src.ui.recording_workspace import RecordingWorkspace
from src.ui.theme import COLORS


def state(status, job="job_one"):
    return {"job_id": job, "status": status}


def test_estimated_animation_is_smooth_monotonic_and_never_completes_by_time():
    progress = AnalysisProgress()
    assert progress.update(state("starting"), True, now=0).percent == 1
    values = []
    for tick in range(1, 60):
        values.append(progress.update(state("starting"), True, now=tick / 10).value)
    for tick in range(60, 1200):
        view = progress.update(state("running"), True, now=tick / 10)
        values.append(view.value)
    assert values == sorted(values)
    assert all(0 < value <= .95 for value in values)
    assert len(set(values)) > 100
    assert progress.update(state("running"), True, now=10**8).percent == 95


@pytest.mark.parametrize("status,busy", [
    ("starting", True), ("running", True), ("running", False), ("review", True),
    ("needs_review", False), ("error", False), ("cancelled", False),
    ("interrupted", False), ("unconfirmed", False), ("unknown", True),
])
def test_only_confirmed_review_after_worker_exit_can_show_100(status, busy):
    progress = AnalysisProgress()
    progress.update(state("starting"), True, now=0)
    view = progress.update(state(status), busy, now=10**8)
    assert view.percent < 100
    assert progress.update(state("review"), False, now=10**8 + 1).percent == 100
    assert progress.update(state(status), busy, now=10**8 + 2).percent < 100


def test_cancel_freezes_and_next_job_starts_at_one_instead_of_old_percentage():
    progress = AnalysisProgress()
    progress.update(state("running"), True, now=0)
    before = progress.update(state("running"), True, now=30)
    frozen = progress.update(state("running"), True, cancelling=True, now=60)
    assert frozen.phase == "cancelling" and frozen.value == before.value
    assert progress.update(state("cancelled"), False, now=3600).value == frozen.value
    assert progress.update(state("starting", "job_two"), True, now=3601).percent == 1
    assert progress.update(None, False, now=3602).percent == 0
    assert progress.update(state("review"), False, now=3603).percent == 100
    assert progress.update(state("starting", "job_three"), True, now=3604).percent == 1


def test_idle_and_clock_rollback_do_not_advance_progress():
    progress = AnalysisProgress()
    assert progress.update(None, False, now=0).phase == "idle"
    assert progress.update(state("running"), True, now=10).percent == 1
    assert progress.update(state("running"), True, now=5).percent == 1
    assert progress.update(state("running"), False, now=500).phase == "unconfirmed"


class Widget:
    def __init__(self, **values):
        self.values = values
        self.configures = 0
        self.sets = []

    def cget(self, key):
        return self.values.get(key)

    def configure(self, **values):
        self.configures += 1
        self.values.update(values)

    def set(self, value):
        self.sets.append(value)


def workspace():
    view = object.__new__(RecordingWorkspace)
    view._analysis_progress = AnalysisProgress()
    view._progress_job_id = "job_one"
    view._progress_bar_value = 0.0
    view.selected = SimpleNamespace(path="recording.mp4")
    view.jobs = SimpleNamespace(cancel_event=threading.Event())
    view.progress_label = Widget()
    view.progress_percent = Widget()
    view.progress_bar = Widget()
    return view


def test_idle_ui_has_no_repeated_relayout_or_redraw_and_no_new_timer():
    view = workspace()
    view._refresh_analysis_progress(None, False)
    widgets = [view.progress_label, view.progress_percent, view.progress_bar]
    initial = [(widget.configures, len(widget.sets)) for widget in widgets]
    for _ in range(200):
        view._refresh_analysis_progress(None, False)
    assert [(widget.configures, len(widget.sets)) for widget in widgets] == initial
    import inspect
    source = inspect.getsource(RecordingWorkspace._refresh_analysis_progress)
    assert ".after(" not in source and ".start(" not in source


def test_stale_job_cannot_show_completion_for_a_different_recording():
    view = workspace()
    view._refresh_analysis_progress(state("review"), False)
    assert view.progress_percent.cget("text") == "100%"
    assert view.progress_bar.cget("progress_color") == COLORS["success"]
    view._progress_job_id = "job_two"
    view._refresh_analysis_progress(state("review"), False)
    assert view.progress_percent.cget("text") == "0%"
    assert view.progress_bar.sets[-1] == 0


def test_cancel_and_error_do_not_turn_green_or_finish_the_bar():
    clock = [0]
    view = workspace()
    view._analysis_progress = AnalysisProgress(clock=lambda: clock[0])
    view._refresh_analysis_progress(state("running"), True)
    clock[0] = 30
    view._refresh_analysis_progress(state("running"), True)
    before = view.progress_bar.sets[-1]
    view.jobs.cancel_event.set()
    clock[0] = 60
    view._refresh_analysis_progress(state("running"), True)
    assert view.progress_bar.sets[-1] == before
    view._refresh_analysis_progress(state("error"), False)
    assert view.progress_percent.cget("text") != "100%"
    assert view.progress_bar.cget("progress_color") == COLORS["error"]


def test_removing_selected_recording_clears_old_completion_but_does_not_hide_active_work():
    view = workspace()
    view._refresh_analysis_progress(state("review"), False)
    assert view.progress_percent.cget("text") == "100%"
    view.selected = None
    view._refresh_analysis_progress(state("review"), False)
    assert view.progress_percent.cget("text") == "0%"
    view._refresh_analysis_progress(state("running"), True)
    assert view.progress_percent.cget("text") == "1%"


def test_ui_estimate_does_not_write_job_or_plan_files():
    root = Path(__file__).resolve().parents[1]
    source = (root / "src/ui/analysis_progress.py").read_text(encoding="utf-8")
    for forbidden in ("write_json", "_persist", "save_draft", "to_dict", "get_frames"):
        assert forbidden not in source


def test_status_fits_one_line_and_keeps_the_full_detail_for_the_dialog():
    view = object.__new__(RecordingWorkspace)
    view.status = Widget()
    view.status.winfo_width = lambda: 180
    view.status._get_widget_scaling = lambda: 1.5
    view._status_font = SimpleNamespace(measure=lambda text: len(text) * 8)
    message = "Analysis activity and full diagnostic details " * 50
    view.set_status(message)
    short = view.status.cget("text")
    assert view._status_font.measure(short) <= 112
    assert view._status_detail == message
    view._refresh_status_text(width=600)
    assert len(view.status.cget("text")) > len(short)
    assert view._status_detail == message
    view.set_status("Ready")
    assert view.status.cget("text") == "Ready"
