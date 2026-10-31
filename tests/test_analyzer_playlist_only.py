"""Playlist-only view lifecycle and read-only loading; no desktop input."""

import ast
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import Mock

import pytest

from src.analyzer.automation_models import AutomationPlan
from src.ui import analyzer_view
from src.ui.analyzer_view import AnalyzerView


def host():
    view = SimpleNamespace(
        _closed=False,
        _deferred_load_id="initial-load",
        _plans_load_generation=0,
        _plan_lock_cache={},
        _plan_content_cache={},
        _plan_items=[],
        _plans_scroll=Mock(),
        _plans_empty_label=Mock(),
        _db=Mock(),
        _plans_loader=Mock(),
        _ui_dispatcher=Mock(),
        after_cancel=Mock(),
    )
    view._analyzer_ui_post = lambda callback: callback()
    for name in ("_load_plans_async", "_load_plans", "_apply_plans", "_deferred_load", "refresh", "cleanup"):
        setattr(view, name, MethodType(getattr(AnalyzerView, name), view))
    return view


def test_playlist_controller_cannot_launch_legacy_analysis_or_save_a_generated_plan():
    source = Path(analyzer_view.__file__).read_text(encoding="utf-8")
    parsed = ast.parse(source)
    view = next(node for node in parsed.body if isinstance(node, ast.ClassDef) and node.name == "AnalyzerView")
    methods = {node.name for node in view.body if isinstance(node, ast.FunctionDef)}
    removed = {"_on_analyze", "_on_cancel", "_save_automation_plan", "_setup_recordings_card",
               "_setup_analyze_card", "_setup_result_card", "_load_recordings_async"}
    assert not removed & methods
    owned = ast.get_source_segment(source, view)
    assert "get_video_analyzer" not in owned
    assert "_recordings_loader" not in owned
    assert "_load_recordings(" not in owned
    assert "_analyze_btn" not in owned
    assert "_result_status" not in owned


def test_refresh_loads_plans_and_lock_metadata_without_writing(tmp_path, monkeypatch):
    plans = tmp_path / "plans"
    plans.mkdir()
    path = plans / "plan_fixture.json"
    original = '{"plan_id":"plan_fixture","name":"Existing","initial_rules":[],"monitoring_rules":[],"unknown":[1,2]}'
    path.write_text(original, encoding="utf-8")
    monkeypatch.setattr(analyzer_view, "PLANS_DIR", plans)
    monkeypatch.setattr(analyzer_view, "DATA_DIR", tmp_path)
    forbidden_save = Mock(side_effect=AssertionError("Reading a playlist must never save it"))
    monkeypatch.setattr(analyzer_view, "dump_json_file", forbidden_save)
    view = host()
    view._db.get_all_recordings.return_value = [SimpleNamespace(automation_plan_id="plan_fixture", locked=True)]
    jobs = []
    view._plans_loader.submit.side_effect = jobs.append

    view._deferred_load()
    view.refresh()
    assert view._deferred_load_id is None
    assert len(jobs) == 2
    jobs[1]()
    jobs[0]()  # An older worker finishing late cannot overwrite the new list.
    assert len(view._plan_items) == 1
    assert view._plan_items[0].name == "Existing"
    assert view._plan_lock_cache == {"plan_fixture": True}
    assert view._plans_scroll.set_items.call_count == 1
    assert path.read_text(encoding="utf-8") == original
    assert list(plans.iterdir()) == [path]
    forbidden_save.assert_not_called()


def test_unchanged_rows_are_reused_but_saved_edits_and_lock_changes_refresh_only_their_row():
    view = host()
    first = AutomationPlan(plan_id="a", name="First")
    second = AutomationPlan(plan_id="b", name="Second")
    view._apply_plans([first, second], lock_cache={"a": False}, content_cache={"a": "old", "b": "same"})
    view._plans_scroll.refresh_item.assert_not_called()  # First render already has fresh rows.
    view._plans_scroll.reset_mock()
    view._apply_plans([first, second], lock_cache={"a": False}, content_cache={"a": "old", "b": "same"})
    view._plans_scroll.refresh_item.assert_not_called()

    renamed = AutomationPlan(plan_id="a", name="Renamed")
    view._apply_plans([renamed, second], lock_cache={"a": False}, content_cache={"a": "new", "b": "same"})
    view._plans_scroll.refresh_item.assert_called_once_with(0)
    assert view._plan_items[0] is renamed
    view._plans_scroll.reset_mock()
    view._apply_plans([renamed, second], lock_cache={"a": True}, content_cache={"a": "new", "b": "same"})
    view._plans_scroll.refresh_item.assert_called_once_with(0)


def test_empty_and_repopulated_lists_release_stale_virtual_rows():
    view = host()
    view._apply_plans([AutomationPlan(plan_id="a")])
    view._apply_plans([])
    assert view._plan_items == []
    view._plans_scroll.set_items.assert_called_with([], preserve_scroll=False)
    view._plans_scroll.pack_forget.assert_called_once()
    view._plans_empty_label.pack.assert_called_once()
    view._apply_plans([AutomationPlan(plan_id="b")])
    assert view._plan_items[0].plan_id == "b"
    view._plans_empty_label.pack_forget.assert_called()


def test_close_cancels_initial_load_and_rejects_queued_refresh_results():
    view = host()
    view.cleanup()
    view.after_cancel.assert_called_once_with("initial-load")
    view._plans_loader.close.assert_called_once()
    view._ui_dispatcher.close.assert_called_once()
    view.refresh()
    view._deferred_load()
    view._apply_plans([AutomationPlan()], generation=0)
    view._apply_plans([AutomationPlan()])
    view._plans_loader.submit.assert_not_called()
    view._plans_scroll.set_items.assert_not_called()
    view.cleanup()  # Main-window cleanup may be called again before destruction.
    assert view._closed


def test_live_scale_update_only_rebuilds_rows_when_the_pixel_height_changes():
    view = host()
    view._apply_widget_scaling = lambda value: value * 1.5
    rows = view._plans_scroll
    rows._item_height = 56
    rows._canvas.yview.return_value = (.25, .75)
    AnalyzerView._sync_playlist_scale(view)
    assert rows._item_height == 84
    rows.refresh.assert_called_once()
    rows._canvas.yview_moveto.assert_called_once_with(.25)
    AnalyzerView._sync_playlist_scale(view)
    assert rows.refresh.call_count == 1
    view._closed = True
    view._apply_widget_scaling = lambda value: value
    AnalyzerView._sync_playlist_scale(view)
    assert rows.refresh.call_count == 1


def test_long_playlist_name_fits_its_cell_without_allocating_a_new_font():
    view = host()
    current = {"text": "Initial"}
    label = SimpleNamespace(_label=Mock(), tk=Mock(), configure=Mock())
    label.tk.call.side_effect = lambda command, operation, font, text: len(text) * 6
    label.cget = current.__getitem__
    label.configure.side_effect = lambda **values: current.update(values)
    AnalyzerView._fit_playlist_name(view, label, "a" * 100, 80)
    assert current["text"].endswith("...")
    assert len(current["text"]) * 6 <= 74
    AnalyzerView._fit_playlist_name(view, label, "Short", 180)
    assert current["text"] == "Short"
    label.configure.reset_mock()
    AnalyzerView._fit_playlist_name(view, label, "Short", 180)
    label.configure.assert_not_called()
    view._closed = True
    AnalyzerView._fit_playlist_name(view, label, "Longer name", 80)
    label.configure.assert_not_called()


def test_edit_and_create_return_to_playlist_without_removed_recording_widgets(monkeypatch):
    import sys
    factory = Mock(return_value="modal-placeholder")
    monkeypatch.setitem(sys.modules, "src.ui.player_view", SimpleNamespace(PlanDetailDialog=factory))
    view = SimpleNamespace(wait_window=Mock(), _load_plans=Mock())
    plan = AutomationPlan(name="Keep original")
    AnalyzerView._edit_plan(view, plan)
    factory.assert_called_once_with(view, plan)
    view.wait_window.assert_called_once_with("modal-placeholder")
    view._load_plans.assert_called_once()
    AnalyzerView._create_plan(view)
    assert factory.call_count == 2
    assert view._load_plans.call_count == 2
    assert plan.name == "Keep original"


def test_lock_toggle_writes_database_and_reloads_without_touching_plan_json(tmp_path, monkeypatch):
    import importlib
    from src.database.models import Recording
    from tkinter import messagebox

    db_manager = importlib.import_module("src.database.db_manager")
    monkeypatch.setattr(db_manager, "DATA_DIR", tmp_path)
    database = object.__new__(db_manager.DatabaseManager)
    database._db_path = tmp_path / "fixture.db"
    database._wal_initialized = False
    database._ensure_database()
    key = database.create_recording(Recording(name="source", video_path="not-opened.mp4"))
    assert database.update_recording_ai_analyzed(key, "a")
    plan_file = tmp_path / "plan_a.json"
    plan_file.write_text('{"plan_id":"a","name":"Untouched"}', encoding="utf-8")
    before = plan_file.read_bytes()
    view = SimpleNamespace(_db=database, _load_plans=Mock())
    error = Mock()
    monkeypatch.setattr(messagebox, "showerror", error)
    confirm = Mock(return_value=True)
    monkeypatch.setattr(messagebox, "askyesno", confirm)

    plan = AutomationPlan(plan_id="a")
    AnalyzerView._toggle_plan_lock(view, plan)
    # A separate manager opens a fresh SQLite connection, not an in-memory object.
    fresh = object.__new__(db_manager.DatabaseManager)
    fresh._db_path = database._db_path
    fresh._wal_initialized = False
    assert bool(fresh.get_recording(key).locked) is True
    confirm.assert_not_called()
    AnalyzerView._toggle_plan_lock(view, plan)
    assert bool(fresh.get_recording(key).locked) is False
    confirm.assert_called_once()
    assert view._load_plans.call_count == 2
    assert plan_file.read_bytes() == before
    error.assert_not_called()


@pytest.mark.parametrize("failure", ["update_false", "reload_missing", "reload_mismatch", "database_error"])
def test_lock_failures_are_visible_and_not_reported_as_saved(monkeypatch, failure):
    from tkinter import messagebox
    view = SimpleNamespace(_db=Mock(), _load_plans=Mock())
    view._db.get_recording_by_plan_id.return_value = SimpleNamespace(id=1, locked=False)
    view._db.update_recording_locked.return_value = failure != "update_false"
    view._db.get_recording.return_value = None if failure == "reload_missing" else SimpleNamespace(locked=False)
    if failure == "database_error":
        view._db.update_recording_locked.side_effect = OSError("database unavailable")
    error = Mock()
    monkeypatch.setattr(messagebox, "showerror", error)
    AnalyzerView._toggle_plan_lock(view, AutomationPlan(plan_id="a"))
    error.assert_called_once()
    view._load_plans.assert_called_once()


def test_unlock_cancel_and_missing_link_do_not_write(monkeypatch):
    from tkinter import messagebox
    view = SimpleNamespace(_db=Mock(), _load_plans=Mock())
    view._db.get_recording_by_plan_id.return_value = SimpleNamespace(id=1, locked=True)
    monkeypatch.setattr(messagebox, "askyesno", Mock(return_value=False))
    AnalyzerView._toggle_plan_lock(view, AutomationPlan(plan_id="a"))
    view._db.update_recording_locked.assert_not_called()
    view._load_plans.assert_not_called()
    view._db.get_recording_by_plan_id.return_value = None
    AnalyzerView._toggle_plan_lock(view, AutomationPlan(plan_id="a"))
    view._db.update_recording_locked.assert_not_called()
    view._load_plans.assert_called_once()
