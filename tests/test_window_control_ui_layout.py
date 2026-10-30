"""Opt-in Windows GUI QA. Never run on a user's desktop.

Run serially (without xdist) on an isolated interactive Windows runner with a desktop at least
1024x768 and WINCRO_WINDOW_CONTROL_UI_QA=1. Screenshots go to
artifacts/window_control (override with WINCRO_WINDOW_CONTROL_UI_QA_ARTIFACTS).
The 1024x768 root is a test surface, not a desktop-resolution change. CTk DPI
autodetection is mocked for deterministic explicit 100/125/150 percent scales;
normal CTk process DPI awareness is retained to avoid coordinate virtualization.
All target enumeration is mocked. No target action, input emulation, process
launch, external activation, get_result(), or modal wait is used here.
Save callbacks test the UI contract only, not durable parent persistence.
One module-scoped CTk root owns every case; only its child widgets are replaced.
"""

import os
from importlib import import_module
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


if os.environ.get("WINCRO_WINDOW_CONTROL_UI_QA") != "1":
    pytest.skip("Isolated Windows GUI QA must be explicitly enabled.", allow_module_level=True)
if sys.platform != "win32":
    pytest.skip("Window-control GUI QA requires an interactive Windows runner.", allow_module_level=True)

import tkinter as tk

import customtkinter as ctk
from customtkinter.windows.widgets.scaling.scaling_tracker import ScalingTracker
from PIL import ImageGrab

from src.ui import window_control_dialog as dialog_module
from src.ui.theme import COLORS, IOS_FONTS, IOS_METRICS
from src.ui.window_control_editor import WindowControlEditorMixin
from src.utils import window_control as backend_module


SCREEN = (1024, 768)
LONG_TITLE = (
    "WinCro QA 전용 가상 대상 - 아주 긴 프로젝트 제목과 문서 이름을 줄임 없이 표시합니다 " * 4
    + "끝까지 표시되는 제목"
)
DUPLICATE_TITLE = "WinCro QA 전용 동일한 제목"


def _pump(root, seconds=0.2):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        root.update_idletasks()
        root.update()
        time.sleep(0.01)


def _rect(widget):
    return (
        widget.winfo_rootx(), widget.winfo_rooty(),
        widget.winfo_rootx() + widget.winfo_width(),
        widget.winfo_rooty() + widget.winfo_height(),
    )


def _inside(inner, outer, tolerance=2):
    assert inner[0] >= outer[0] - tolerance, (inner, outer)
    assert inner[1] >= outer[1] - tolerance, (inner, outer)
    assert inner[2] <= outer[2] + tolerance, (inner, outer)
    assert inner[3] <= outer[3] + tolerance, (inner, outer)


def _assert_wrapped_label(label):
    # Inspect actual Tk text requisitions, not just the configured wraplength.
    assert label.cget("wraplength") > 0
    native = label._label
    assert native.winfo_reqwidth() <= label.winfo_width() + 4
    assert native.winfo_reqheight() <= label.winfo_height() + 4


def _assert_dialog_layout(dialog):
    bounds = _rect(dialog)
    _inside(bounds, (0, 0, *SCREEN))
    footer = dialog._save_button.master
    assert footer.master is dialog
    assert dialog._cancel_button.master is footer
    for button in (dialog._save_button, dialog._cancel_button):
        assert button.winfo_viewable()
        assert button.winfo_height() >= 20
        _inside(_rect(button), bounds)
    assert _rect(dialog._save_button)[2] <= _rect(dialog._cancel_button)[0]
    list_card = dialog._window_list._parent_frame.master
    editor_viewport = dialog._editor._parent_frame
    assert _rect(list_card)[3] <= _rect(editor_viewport)[1] + 2
    assert _rect(editor_viewport)[3] <= _rect(footer)[1] + 2
    assert dialog._window_list._parent_canvas.winfo_height() >= 10
    assert dialog._editor._parent_canvas.winfo_height() >= 10
    for label in dialog._row_labels + dialog._editor_labels:
        _assert_wrapped_label(label)
    for title, executable in zip(dialog._row_labels[::2], dialog._row_labels[1::2]):
        assert _rect(title)[3] <= _rect(executable)[1] + 2
    for (_, first), (_, second) in zip(dialog._rows, dialog._rows[1:]):
        assert _rect(first)[3] <= _rect(second)[1] + 2
    editor_children = sorted(
        (child for child in dialog._editor.winfo_children() if child.grid_info()),
        key=lambda child: int(child.grid_info()["row"]),
    )
    for first, second in zip(editor_children, editor_children[1:]):
        assert _rect(first)[3] <= _rect(second)[1] + 2
    if dialog._error_label.cget("text"):
        _assert_wrapped_label(dialog._error_label)
        _inside(_rect(dialog._error_label), bounds)
        assert _rect(dialog._error_label)[3] <= _rect(dialog._save_button)[1] + 2


@pytest.fixture(scope="module")
def ui_root():
    """Match production's single Tcl interpreter instead of recreating Tk."""
    old_widget_scale = ScalingTracker.widget_scaling
    old_window_scale = ScalingTracker.window_scaling
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(ScalingTracker, "deactivate_automatic_dpi_awareness", False)
        patch.setattr(ScalingTracker, "get_window_dpi_scaling", classmethod(lambda cls, window: 1.0))
        ctk.set_widget_scaling(1.0)
        ctk.set_window_scaling(1.0)
        root = ctk.CTk(fg_color=COLORS["bg_content"])
        try:
            assert root.winfo_screenwidth() >= SCREEN[0] and root.winfo_screenheight() >= SCREEN[1], (
                "The isolated GUI runner needs a desktop of at least 1024x768."
            )
            root.title("Window-control isolated CI QA")
            root.overrideredirect(True)
            tk.Wm.geometry(root, "1024x768+0+0")
            _pump(root)
            yield root
        finally:
            root.destroy()
            ctk.set_widget_scaling(old_widget_scale)
            ctk.set_window_scaling(old_window_scale)


def _cleanup_gui_case(root, bindings, baseline_commands):
    # Keep root-owned CTk polling alive, but cancel widget timers before their
    # registered Tcl commands disappear with the widgets.
    root_commands = set(root._tclCommands or ())
    live_root_timers = set()
    for timer in root.tk.splitlist(root.tk.call("after", "info")):
        command = str(root.tk.call("after", "info", timer)[0])
        if command in root_commands:
            live_root_timers.add(command)
        else:
            root.tk.call("after", "cancel", timer)
    for sequence in root.bind_all():
        if sequence not in bindings:
            root.unbind_all(sequence)
    for sequence, script in bindings.items():
        root.tk.call("bind", "all", sequence, script)
    for child in list(root.winfo_children()):
        child.destroy()
    current_grab = root.grab_current()
    if current_grab is not None:
        current_grab.grab_release()
    # CTkScrollableFrame.bind_all registers commands on the root, not the frame.
    for command in set(root._tclCommands or ()) - baseline_commands - live_root_timers:
        root.deletecommand(command)
    assert not root.winfo_children()
    assert root.grab_current() is None


@pytest.fixture(params=(1.0, 1.25, 1.5), ids=("100pct", "125pct", "150pct"))
def gui(request, monkeypatch, ui_root):
    scale = request.param
    root = ui_root
    bindings = {sequence: root.bind_all(sequence) for sequence in root.bind_all()}
    baseline_commands = set(root._tclCommands or ())
    ctk.set_widget_scaling(scale)
    ctk.set_window_scaling(scale)
    # Scaling an existing CTk root temporarily pins its min/max dimensions.
    root._set_scaled_min_max()
    tk.Wm.geometry(root, "1024x768+0+0")
    # Native target access is forbidden even if future dialog code bypasses its mock.
    forbidden = Mock(side_effect=AssertionError("Native target control is forbidden in UI QA."))
    monkeypatch.setattr(backend_module, "_Win32API", forbidden)
    monkeypatch.setattr(backend_module.WindowController, "step", forbidden)
    windows = [
        backend_module.WindowInfo(101, 201, "qa-long-title.exe", LONG_TITLE),
        backend_module.WindowInfo(102, 202, "qa-duplicate.exe", DUPLICATE_TITLE),
        backend_module.WindowInfo(103, 203, "qa-duplicate.exe", DUPLICATE_TITLE),
    ]
    controller = Mock()
    controller.list_windows.return_value = windows
    controller_factory = Mock(return_value=controller)
    monkeypatch.setattr(dialog_module, "WindowController", controller_factory)
    monkeypatch.setattr(dialog_module.WindowControlDialog, "winfo_screenwidth", lambda self: SCREEN[0])
    monkeypatch.setattr(dialog_module.WindowControlDialog, "winfo_screenheight", lambda self: SCREEN[1])
    errors = []
    artifact_dir = Path(os.environ.get(
        "WINCRO_WINDOW_CONTROL_UI_QA_ARTIFACTS", "artifacts/window_control",
    ))
    artifact_dir.mkdir(parents=True, exist_ok=True)
    name = request.node.name.replace("[", "_").replace("]", "")

    def screenshot(phase):
        _pump(root)
        image = ImageGrab.grab(bbox=(0, 0, *SCREEN), all_screens=True)
        assert image.size == SCREEN
        path = artifact_dir / f"{name}_{phase}.png"
        image.save(path)
        assert any(high - low > 20 for low, high in image.convert("RGB").getextrema()), (
            "Blank screenshot: the isolated runner needs an unlocked interactive desktop."
        )

    try:
        root.report_callback_exception = lambda *exception: errors.append(exception)
        ctk.CTkLabel(
            root, text=f"Isolated UI QA / 1024x768 / {scale * 100:.0f}%",
            font=ctk.CTkFont(family=IOS_FONTS["family"], size=12),
            text_color=COLORS["text_muted"],
        ).place(relx=0.5, rely=1.0, anchor="s", y=-8)
        _pump(root)
        yield SimpleNamespace(
            root=root, scale=scale, windows=windows, controller=controller,
            factory=controller_factory, screenshot=screenshot, errors=errors,
        )
    finally:
        try:
            if root.winfo_exists():
                screenshot("final")
        finally:
            _cleanup_gui_case(root, bindings, baseline_commands)
            ctk.set_widget_scaling(1.0)
            ctk.set_window_scaling(1.0)
            root._set_scaled_min_max()
        assert not errors, errors
        forbidden.assert_not_called()


def test_dialog_layout_selection_and_save_contract(gui):
    gui.root.grab_set()
    callback = Mock(return_value=False)
    dialog = dialog_module.WindowControlDialog(gui.root, on_save=callback)
    gui.screenshot("long_title")
    assert dialog._get_window_scaling() == pytest.approx(gui.scale)
    assert dialog._window_list._get_widget_scaling() == pytest.approx(gui.scale)
    _assert_dialog_layout(dialog)
    gui.controller.list_windows.assert_called_once_with()
    assert gui.root.grab_current() is dialog
    assert dialog._save_button.cget("text") == "저장"
    assert dialog._operation_var.get() == "최대화"
    assert any(
        "저장 시 현재 플랜/재생목록도 함께 저장됩니다" in label.cget("text")
        for label in dialog._editor_labels
    )
    title_label = dialog._row_labels[0]
    assert title_label.cget("text") == LONG_TITLE
    assert title_label.winfo_height() > dialog._row_labels[1].winfo_height()
    assert dialog._window_list._parent_canvas.yview()[1] < 1

    dialog._select_window(gui.windows[1])
    assert dialog._executable_entry.cget("state") == "readonly"
    assert "2개" in dialog._match_note.cget("text")
    assert "중복" in dialog._match_note.cget("text")
    rows = list(dialog._rows)
    for title in ("WinCro", "QA 전용", DUPLICATE_TITLE):
        dialog._title_var.set(title)
    assert dialog._rows == rows
    gui.controller.list_windows.assert_called_once_with()
    dialog._editor._parent_canvas.yview_moveto(1)
    gui.screenshot("ambiguous")
    _assert_dialog_layout(dialog)

    initial_bottom = _rect(dialog._save_button)[3]
    dialog._save_button.invoke()
    gui.screenshot("callback_false")
    assert dialog.winfo_exists() and dialog._result is None
    assert gui.root.grab_current() is dialog
    assert "변경사항은 편집기에 남아 있습니다" in dialog._error_label.cget("text")
    assert dialog._save_button.cget("state") == "normal"
    assert _rect(dialog._save_button)[3] == pytest.approx(initial_bottom, abs=2)
    _assert_dialog_layout(dialog)
    callback.side_effect = OSError("CI 전용 저장 실패: disk full " * 30)
    dialog._save_button.invoke()
    gui.screenshot("callback_exception")
    assert dialog.winfo_exists() and dialog._result is None
    assert gui.root.grab_current() is dialog
    assert "disk full" in dialog._error_label.cget("text")
    assert "변경사항은 편집기에 남아 있습니다" in dialog._error_label.cget("text")
    _assert_dialog_layout(dialog)

    minimum = tk.Wm.minsize(dialog)
    assert minimum[0] <= dialog.winfo_width() and minimum[1] <= dialog.winfo_height()
    dialog.geometry("1x1")
    _pump(gui.root)
    assert dialog.winfo_width() >= minimum[0]
    assert dialog.winfo_height() >= minimum[1]
    gui.screenshot("minimum_with_error")
    _assert_dialog_layout(dialog)
    dialog._editor._parent_canvas.yview_moveto(1)
    _pump(gui.root)
    assert _rect(dialog._save_button)[3] <= _rect(dialog)[3]

    # A user-requested refresh must not replace an edited title or operation.
    dialog._title_var.set("사용자가 편집한 부분 제목")
    dialog._operation_var.set("최소화")
    dialog._refresh_button.invoke()
    assert gui.controller.list_windows.call_count == 2
    assert dialog._title_var.get() == "사용자가 편집한 부분 제목"
    assert dialog._operation_var.get() == "최소화"

    def save_success(config):
        assert dialog._saving
        assert dialog._title_entry.cget("state") == "disabled"
        assert dialog._operation_menu.cget("state") == "disabled"
        # A callback processing Tk events must not re-enter or cancel its save.
        dialog._cancel()
        dialog._save()
        assert dialog.winfo_exists()
        config["title_contains"] = "콜백이 전달받은 복사본만 변경"
        return True

    callback.side_effect = save_success
    dialog._save_button.invoke()
    assert not dialog.winfo_exists()
    assert gui.root.grab_current() is gui.root
    assert gui.root.grab_status() == "local"
    assert dialog._result == {
        "executable_name": "qa-duplicate.exe",
        "title_contains": "사용자가 편집한 부분 제목",
        "operation": "minimize",
    }
    assert set(callback.call_args.args[0]) == {"executable_name", "title_contains", "operation"}


def test_offline_apply_validation_and_enumeration_failure(gui):
    gui.root.grab_set()
    dialog = dialog_module.WindowControlDialog(gui.root, config={
        "executable_name": "qa-offline.exe", "title_contains": "오프라인 제목",
        "handle": 999, "process_id": 888, "path": "C:/not-persisted.exe",
    })
    gui.screenshot("offline_apply")
    assert dialog._save_button.cget("text") == "적용"
    assert dialog._executable_entry.cget("state") == "normal"
    assert any("상위 창에서 저장" in label.cget("text") for label in dialog._editor_labels)
    _assert_dialog_layout(dialog)
    dialog._select_window(gui.windows[0])
    assert dialog._executable_entry.cget("state") == "readonly"
    dialog._manual_button.invoke()
    assert dialog._executable_entry.cget("state") == "normal"
    dialog._title_var.set("오프라인 제목")
    dialog._executable_var.set("C:/not-a-basename.exe")
    dialog._save_button.invoke()
    gui.screenshot("invalid_path")
    assert dialog.winfo_exists() and dialog._result is None
    assert "파일명" in dialog._error_label.cget("text")
    _assert_dialog_layout(dialog)
    dialog._executable_var.set("qa-offline.exe")
    dialog._operation_var.set("앞으로 가져오기")
    gui.controller.list_windows.side_effect = OSError("CI 전용 목록 조회 실패")
    dialog._refresh_button.invoke()
    gui.screenshot("enumeration_failure")
    assert not dialog._windows
    assert "조회 실패" in dialog._match_note.cget("text")
    assert dialog._executable_var.get() == "qa-offline.exe"
    assert dialog._title_var.get() == "오프라인 제목"
    _assert_dialog_layout(dialog)
    dialog._save_button.invoke()
    assert not dialog.winfo_exists()
    assert gui.root.grab_current() is gui.root
    assert dialog._result == {
        "executable_name": "qa-offline.exe", "title_contains": "오프라인 제목",
        "operation": "foreground",
    }

    cancelled = dialog_module.WindowControlDialog(
        gui.root, config=dialog._result, on_save=Mock(return_value=False),
    )
    cancelled._save_button.invoke()
    assert cancelled.winfo_exists() and gui.root.grab_current() is cancelled
    cancelled._cancel_button.invoke()
    assert not cancelled.winfo_exists() and cancelled._result is None
    assert gui.root.grab_current() is gui.root

    invalid_operation = dialog_module.WindowControlDialog(gui.root, config={
        "executable_name": "qa-offline.exe", "title_contains": "오프라인 제목",
        "operation": "unknown-operation",
    })
    assert invalid_operation._operation_var.get() != "최대화"
    invalid_operation._save_button.invoke()
    gui.screenshot("invalid_operation")
    assert invalid_operation.winfo_exists() and invalid_operation._result is None
    assert "동작" in invalid_operation._error_label.cget("text")
    _assert_dialog_layout(invalid_operation)
    invalid_operation._cancel_button.invoke()
    assert gui.root.grab_current() is gui.root


def test_executable_only_mode_is_explicit_persisted_and_keeps_title_disabled(gui):
    callback = Mock(return_value=False)
    dialog = dialog_module.WindowControlDialog(gui.root, on_save=callback)
    assert dialog._match_mode_var.get() == "실행 파일명 + 창 제목"
    dialog._select_window(gui.windows[0])
    original_title = dialog._title_var.get()
    rows = list(dialog._rows)
    dialog._match_mode_var.set("실행 파일명만")
    dialog._match_mode_changed()
    assert dialog._rows == rows
    assert dialog._title_entry.cget("state") == "disabled"
    assert "사용 안 함" in dialog._title_label.cget("text")
    assert dialog._title_var.get() == original_title
    expected = {
        "executable_name": gui.windows[0].executable_name,
        "title_contains": "", "operation": "maximize", "match_mode": "executable",
    }
    assert dialog._collect_config() == expected
    event_window = backend_module.WindowInfo(
        gui.windows[0].handle, gui.windows[0].process_id,
        gui.windows[0].executable_name, "이벤트 안내 - 새 페이지",
    )
    gui.controller.list_windows.return_value = [event_window]
    dialog._refresh_button.invoke()
    assert dialog._matching_windows() == [event_window]
    assert dialog._title_var.get() == original_title
    gui.controller.list_windows.return_value = [
        event_window,
        backend_module.WindowInfo(404, 505, event_window.executable_name, "다른 브라우저 창"),
    ]
    dialog._refresh_button.invoke()
    assert len(dialog._matching_windows()) == 2 and "중복" in dialog._match_note.cget("text")
    gui.controller.list_windows.return_value = [event_window]
    dialog._refresh_button.invoke()
    gui.screenshot("executable_only")
    _assert_dialog_layout(dialog)
    dialog._save_button.invoke()
    assert dialog.winfo_exists() and dialog._result is None
    assert dialog._title_entry.cget("state") == "disabled"
    assert dialog._match_mode_menu.cget("state") == "normal"
    callback.assert_called_once_with(expected)
    dialog._match_mode_var.set("실행 파일명 + 창 제목")
    dialog._match_mode_changed()
    assert dialog._title_entry.cget("state") == "normal"
    assert dialog._title_var.get() == original_title
    assert not dialog._matching_windows()
    dialog._match_mode_var.set("실행 파일명만")
    dialog._match_mode_changed()
    callback.return_value = True
    dialog._save_button.invoke()
    assert not dialog.winfo_exists() and dialog._result == expected
    reopened = dialog_module.WindowControlDialog(gui.root, config=dialog._result)
    assert reopened._match_mode_var.get() == "실행 파일명만"
    assert reopened._title_entry.cget("state") == "disabled"
    assert reopened._collect_config() == expected
    reopened._cancel()
    invalid = dialog_module.WindowControlDialog(gui.root, config={**expected, "match_mode": "unknown"})
    assert invalid._match_mode_var.get() == "찾기 방식을 선택하세요"
    invalid._save_button.invoke()
    assert invalid.winfo_exists() and invalid._result is None
    assert "찾기 방식" in invalid._error_label.cget("text")
    invalid._cancel()


def test_direct_destroy_respects_newer_or_destroyed_grab_owner(gui):
    gui.root.grab_set()
    dialog = dialog_module.WindowControlDialog(gui.root)
    newer_modal = ctk.CTkToplevel(gui.root)
    newer_modal.title("CI-only nested modal grab")
    newer_modal.grab_set()
    dialog.destroy()
    assert gui.root.grab_current() is newer_modal
    assert dialog._result is None
    newer_modal.destroy()

    previous_modal = ctk.CTkToplevel(gui.root)
    previous_modal.grab_set()
    dialog = dialog_module.WindowControlDialog(gui.root)
    previous_modal.destroy()
    dialog.destroy()
    assert gui.root.grab_current() is None
    assert dialog._result is None


@pytest.mark.parametrize("is_plan", (True, False), ids=("plan", "playlist"))
def test_action_button_mixin_below_existing_six_rows(gui, is_plan):
    class ButtonArea(WindowControlEditorMixin):
        _window_control_is_plan = is_plan

        def _add_window_control_action(self):
            raise AssertionError("Layout QA must not execute an editor action.")

    area = ctk.CTkFrame(gui.root, fg_color=COLORS["bg_glass"])
    area.pack(anchor="nw", padx=18, pady=18)
    ctk.CTkLabel(
        area, text="플랜 액션" if is_plan else "재생목록 액션",
        font=ctk.CTkFont(family=IOS_FONTS["family"], size=18, weight="bold"),
        text_color=COLORS["text_primary"],
    ).pack(anchor="w", pady=(0, 8))
    rows = []
    buttons = []
    for index in range(6):
        row = ctk.CTkFrame(area, fg_color="transparent")
        row.pack(fill="x", pady=(3, 0))
        rows.append(row)
        texts = ("액션 호출", "자동 목록 처리") if index == 5 else (f"기존 {index + 1}-1", f"기존 {index + 1}-2")
        if index == 5 and not is_plan:
            texts = texts[::-1]
        for column, text in enumerate(texts):
            button = ctk.CTkButton(
                row, text=text, command=lambda: None, width=110, height=28,
                fg_color=COLORS["accent_blue"], hover_color=COLORS["hover_blue"],
                text_color=COLORS["text_on_accent"],
                font=ctk.CTkFont(family=IOS_FONTS["family"], size=12, weight="bold"),
                corner_radius=IOS_METRICS["control_radius_small"],
            )
            button.pack(side="left", padx=(0, 5) if column == 0 else 0)
            buttons.append(button)
    ButtonArea()._add_window_control_button(area)
    gui.screenshot("action_button_area")
    added_row = area.winfo_children()[-1]
    added = added_row.winfo_children()[0]
    assert added.cget("text") == "창 제어"
    assert _rect(rows[-1])[3] <= _rect(added_row)[1]
    for button in buttons + [added]:
        assert button.winfo_viewable()
        _inside(_rect(button), (0, 0, *SCREEN))
        assert button._text_label.winfo_reqwidth() <= button.winfo_width() + 2
    for first, second in zip(buttons[::2], buttons[1::2]):
        assert _rect(first)[2] <= _rect(second)[0]


def _descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from _descendants(child)


@pytest.mark.parametrize("kind", ["plan", "sequence"])
def test_multi_candidates_toggle_order_and_durable_save(gui, tmp_path, kind):
    from copy import deepcopy
    from src.analyzer.automation_models import AutomationPlan, AutomationRule
    from src.database.models import Action, Sequence
    from src.utils.json_utils import dump_json_file, load_json_file
    from tests.test_window_control_persistence import _manager_at, _assert_sequence_saved

    single = {"executable_name": "msedge.exe", "title_contains": "Home", "operation": "maximize"}
    if kind == "plan":
        item = AutomationRule(action_type="window_control", window_control_config=single)
        document = AutomationPlan(plan_id="multi-ui", initial_rules=[item])
    else:
        item = Action(action_type="window_control", window_control_config=single)
        document = Sequence(name="Multi UI", actions=[item])
    path = tmp_path / ("plan.json" if kind == "plan" else "sequence.db")
    db = _manager_at(path, create=True) if kind == "sequence" else None
    if db is not None:
        document.id = db.create_sequence(document)
    failed = [True]
    writes = []

    def persist(config):
        if failed[0]:
            raise OSError("CI multi save failure " * 20)
        item.window_control_config = deepcopy(config)
        if kind == "plan":
            dump_json_file(path, document.to_dict())
            loaded = AutomationPlan.from_dict(load_json_file(path), templates_dir=tmp_path)
            assert loaded.to_dict() == document.to_dict()
            actual = loaded.initial_rules[0].window_control_config
        else:
            assert db.update_sequence(document)
            loaded = _assert_sequence_saved(path, document)
            actual = loaded.actions[0].window_control_config
        assert actual == config
        writes.append(deepcopy(actual))
        return True

    dialog = dialog_module.WindowControlDialog(gui.root, config=single, on_save=persist)
    _pump(gui.root)
    assert not dialog._multi_var.get() and not dialog._target_panel.winfo_ismapped()
    assert dialog._collect_config() == single
    dialog._multi_toggle.toggle()
    assert dialog._multi_var.get() and len(dialog._targets) == 1
    original_entry = dialog._executable_entry
    dialog._target_add.invoke()
    dialog._executable_var.set("mudrun_tauri.exe")
    dialog._title_var.set("Mudfish")
    dialog._match_mode_var.set("실행 파일명만")
    dialog._match_mode_changed()
    dialog._target_up.invoke()
    assert dialog._executable_entry is original_entry, "Reordering must not rebuild the form"
    assert dialog._targets[0]["executable_name"] == "mudrun_tauri.exe"
    dialog._multi_toggle.toggle()
    assert dialog._executable_var.get() == single["executable_name"]
    assert dialog._title_var.get() == single["title_contains"]
    disabled = dialog._collect_config()
    assert disabled["multi_enabled"] is False and len(disabled["targets"]) == 2
    dialog._multi_toggle.toggle()
    expected = dialog._collect_config()
    assert expected["multi_enabled"] is True and expected["executable_name"] == "msedge.exe"
    assert expected["targets"] == [
        {"executable_name": "mudrun_tauri.exe", "title_contains": "", "match_mode": "executable"},
        {"executable_name": "msedge.exe", "title_contains": "Home"},
    ]
    dialog._refresh_windows()
    assert dialog._collect_config() == expected
    dialog._editor._parent_canvas.yview_moveto(0)
    gui.screenshot(f"multi_{kind}_ordered")
    _assert_dialog_layout(dialog)
    _assert_wrapped_label(dialog._target_hint)
    _inside(_rect(dialog._multi_toggle), _rect(dialog))
    for button in (dialog._target_up, dialog._target_down, dialog._target_add, dialog._target_delete):
        assert button._text_label.winfo_reqwidth() <= button.winfo_width() + 4
    assert _rect(dialog._target_menu)[2] <= _rect(dialog._target_up)[0]
    assert _rect(dialog._target_up)[2] <= _rect(dialog._target_down)[0]
    dialog._save_button.invoke()
    assert dialog.winfo_exists() and not writes and dialog._result is None
    assert dialog._collect_config() == expected
    assert dialog._target_down.cget("state") == "normal" and dialog._target_up.cget("state") == "disabled"
    assert "CI multi save failure" in dialog._error_label.cget("text")
    dialog.geometry("1x1")
    gui.screenshot(f"multi_{kind}_minimum_save_failure")
    _assert_dialog_layout(dialog)
    failed[0] = False
    dialog._save_button.invoke()
    assert not dialog.winfo_exists() and writes == [expected]
    reopened = dialog_module.WindowControlDialog(gui.root, config=writes[0])
    assert reopened._collect_config() == expected
    reopened._multi_toggle.toggle()
    restored = reopened._collect_config()
    assert restored == {**single, "multi_enabled": False, "targets": expected["targets"]}
    reopened._cancel_button.invoke()
    assert writes == [expected]


def test_multi_long_candidates_bounds_add_delete_and_no_full_refresh(gui):
    targets = [{"executable_name": f"candidate_{index}_" + "x" * 65 + ".exe",
                "title_contains": LONG_TITLE} for index in range(20)]
    config = {"executable_name": "single.exe", "title_contains": "Single", "operation": "maximize",
              "multi_enabled": True, "targets": targets}
    dialog = dialog_module.WindowControlDialog(gui.root, config=config)
    _pump(gui.root)
    assert dialog._target_add.cget("state") == "disabled"
    selected = list(dialog._target_choices)[-1]
    dialog._choose_target(selected)
    assert dialog._target_down.cget("state") == "disabled"
    assert dialog._executable_var.get() == targets[-1]["executable_name"]
    before_entry, before_rows = dialog._executable_entry, list(dialog._rows)
    dialog._target_up.invoke()
    assert dialog._executable_entry is before_entry and dialog._rows == before_rows
    assert dialog._targets[-2] == targets[-1]
    dialog._target_delete.invoke()
    assert len(dialog._targets) == 19 and dialog._target_add.cget("state") == "normal"
    dialog._editor._parent_canvas.yview_moveto(0)
    gui.screenshot("multi_long_candidates")
    _assert_dialog_layout(dialog)
    assert dialog._target_menu._text_label.winfo_reqwidth() <= dialog._target_menu.winfo_width() + 4
    _assert_wrapped_label(dialog._target_hint)
    gui.controller.list_windows.assert_called_once_with()
    dialog._cancel_button.invoke()


@pytest.mark.parametrize("gui", (1.0,), indirect=True, ids=("100pct",))
@pytest.mark.parametrize("kind", ("plan", "playlist"))
@pytest.mark.parametrize("multi", [False, True], ids=("single", "multi"))
def test_actual_editor_compact_window_control_row(gui, monkeypatch, kind, multi):
    """Real constructors/renderers at 100%; not a scaled-editor fit claim.

    Existing PlanDetailDialog requires at least 980x700 logical pixels. Its
    whole-window small-screen/high-DPI layout is intentionally outside this QA.
    """
    from src.analyzer.automation_models import AutomationPlan, AutomationRule
    from src.database.models import Action, Sequence
    from src.utils import input_controller

    config = {"executable_name": "qa.exe", "title_contains": "QA", "operation": "minimize"}
    if multi:
        config.update(multi_enabled=True, targets=[
            {"executable_name": "msedge.exe", "title_contains": "", "match_mode": "executable"},
            {"executable_name": "mudrun_tauri.exe", "title_contains": "", "match_mode": "executable"},
        ])
    action = Action(
        action_type="window_control", description="QA", window_control_config=dict(config),
        wait_after=0.5,
    )
    rule = AutomationRule(
        action_type="window_control", description="QA", window_control_config=dict(config),
        wait_after=0.5,
    )
    sequence = Sequence(id=987654, name="CI-only compact playlist", actions=[action])
    plan = AutomationPlan(
        plan_id="ci_only_window_control_row", name="CI-only compact plan", initial_rules=[rule],
    )
    forbidden = Mock(side_effect=AssertionError("Compact-row QA must not execute, inject input, or save."))
    fake_db = SimpleNamespace(
        get_sequence=Mock(return_value=sequence),
        get_all_sequences=Mock(return_value=[sequence]),
        get_all_recordings=Mock(return_value=[]),
        get_recording=Mock(return_value=None),
        update_sequence=forbidden,
        create_sequence=forbidden,
        delete_sequence=forbidden,
    )
    database = import_module("src.database")
    db_manager = import_module("src.database.db_manager")
    read_db = Mock(return_value=fake_db)
    monkeypatch.setattr(db_manager.DatabaseManager, "_get_connection", forbidden)
    # Install input barriers before importing the heavyweight editor module.
    for name in (
        "moveTo", "moveRel", "click", "doubleClick", "rightClick", "mouseDown", "mouseUp",
        "dragTo", "dragRel", "scroll", "hscroll", "vscroll", "press", "keyDown", "keyUp",
        "hotkey", "write", "typewrite",
    ):
        monkeypatch.setattr(input_controller.pyautogui, name, forbidden)

    player_view = import_module("src.ui.player_view")
    # Patch imported aliases after import so teardown does not leave test doubles
    # cached inside another module's namespace for subsequent tests.
    monkeypatch.setattr(input_controller, "get_input_controller", forbidden)
    monkeypatch.setattr(database, "get_db", read_db)
    monkeypatch.setattr(db_manager, "get_db", read_db)
    for module_name in ("src.player.action_player", "src.player.rule_executor"):
        runtime = sys.modules.get(module_name)
        if runtime is not None:
            monkeypatch.setattr(runtime, "get_input_controller", forbidden)
    position_setup = Mock()
    monkeypatch.setattr(player_view, "setup_window_position", position_setup)
    monkeypatch.setattr(player_view, "get_db", read_db)
    for name in ("get_input_controller", "get_action_player", "RuleExecutor", "save_config", "dump_json_file"):
        monkeypatch.setattr(player_view, name, forbidden)
    monkeypatch.setattr(player_view.PlanDetailDialog, "_save_plan", forbidden)
    monkeypatch.setattr(player_view.PlanDetailDialog, "_test_run_rule", forbidden)
    monkeypatch.setattr(player_view.SequenceDetailDialog, "_save_sequence", forbidden)
    monkeypatch.setattr(player_view.SequenceDetailDialog, "_save_sequence_silent", forbidden)

    if kind == "plan":
        editor = player_view.PlanDetailDialog(gui.root, plan)
        item, item_id, model_key = rule, rule.rule_id, "rule"
        widget_map = editor._rule_widgets
        assert editor._compact_rule_rows is True
    else:
        editor = player_view.SequenceDetailDialog(gui.root, sequence, fake_db)
        item, item_id, model_key = action, action.action_id, "action"
        widget_map = editor._action_widgets
        assert editor._compact_action_rows is True

    try:
        # Resize only this test instance within the existing minimum. Production
        # editor geometry/minimums and all row construction paths remain intact.
        editor.geometry("1000x700+0+0")
        deadline = time.monotonic() + 3
        while item_id not in widget_map and time.monotonic() < deadline:
            _pump(gui.root, seconds=0.05)
        gui.screenshot(f"actual_compact_{kind}")
        assert item_id in widget_map, "The real virtual-scroll renderer did not create its compact row."
        data = widget_map[item_id]
        assert data[model_key] is item
        assert "type_label" not in data and "detail_label" not in data
        assert editor._scrollable._visible_widgets[0] is data["wrapper"]
        assert gui.scale == 1.0
        _inside(_rect(editor), (0, 0, *SCREEN))
        row = data["widget"]
        _inside(_rect(row), _rect(editor._scrollable._canvas))
        buttons = [widget for widget in _descendants(row) if isinstance(widget, ctk.CTkButton)]
        settings_buttons = [button for button in buttons if button.cget("text") == "설정"]
        assert len(settings_buttons) == 1
        settings = settings_buttons[0]
        assert settings.cget("state") == "normal"
        assert settings.winfo_viewable()
        assert settings.winfo_width() >= 46 and settings.winfo_height() >= 24
        assert settings._text_label.winfo_reqwidth() <= settings.winfo_width() + 2

        name_label = data["name_label"]
        expected_text = (
            editor._compact_rule_label_text(rule) if kind == "plan"
            else editor._compact_action_label_text(action)
        )
        assert name_label.cget("text") == expected_text
        assert "창 제어" in expected_text and "QA" in expected_text
        assert backend_module.format_window_control_summary(config) in expected_text
        assert name_label.winfo_width() >= 120
        assert name_label._label.winfo_reqwidth() <= name_label.winfo_width() + 2
        assert _rect(name_label)[2] <= _rect(settings.master)[0] + 2
        _inside(_rect(name_label), _rect(row))
        for index, button in enumerate(buttons):
            assert button.winfo_viewable()
            _inside(_rect(button), _rect(row))
            first = _rect(button)
            for other in buttons[index + 1:]:
                second = _rect(other)
                overlap_width = min(first[2], second[2]) - max(first[0], second[0])
                overlap_height = min(first[3], second[3]) - max(first[1], second[1])
                assert overlap_width <= 2 or overlap_height <= 2, (button.cget("text"), other.cget("text"))

        # Verify the new compact button targets this model without opening a
        # modal editor, persisting anything, or invoking playback/input actions.
        edit_requested = Mock()
        monkeypatch.setattr(editor, "_edit_window_control_action", edit_requested)
        settings.invoke()
        edit_requested.assert_called_once_with(item)
        if kind == "plan":
            monkeypatch.setattr(editor, "_edit_monitoring_mode", forbidden)
            monkeypatch.setattr(editor, "_edit_trigger_image", forbidden)
            unsupported = [button for button in buttons if str(button.cget("text")).casefold() in {"m", "c"}]
            assert {str(button.cget("text")).casefold() for button in unsupported} == {"m", "c"}
            assert len(unsupported) == 2
            for button in unsupported:
                assert button.cget("state") == "disabled"
                button.invoke()
        position_setup.assert_called_once_with(editor, type(editor).__name__)
        gui.factory.assert_not_called()
        forbidden.assert_not_called()
        assert item.window_control_config == config and not editor._modified
    finally:
        # Do not call _on_close: it is an application lifecycle/save path.
        editor.destroy()
