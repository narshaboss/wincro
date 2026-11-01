"""Opt-in isolated Windows checks. Never run this on an operator's desktop."""

import json
import os
from pathlib import Path
import time
import sys

import pytest

if os.environ.get("WINCRO_AI_EDITOR_UI_QA") != "1" or os.name != "nt":
    pytest.skip("Opt-in isolated Windows runner only", allow_module_level=True)

from src.agent_bridge.contracts import BridgeError
from src.agent_bridge.service import DraftService
from src.agent_bridge.transport import LocalHost, call


def _job_diagnostics(window):
    import threading
    import traceback
    frames = sys._current_frames()
    info = [f"closed={window.closed} queued_results={window.results.qsize()} serial={window.serial}/{window.service.change_serial}",
            "callback_errors=" + repr(getattr(window._root(), "_ai_callback_errors", []))]
    for thread in threading.enumerate():
        if thread.name.startswith("ai-draft-ui") and thread.ident in frames:
            info.append(thread.name + "\n" + "".join(traceback.format_stack(frames[thread.ident])))
    return "\n".join(info)


def _pump_until(root, predicate, timeout):
    """Use production's mainloop, including Tk's cross-thread event dispatch."""
    deadline = time.monotonic() + timeout
    passed = False
    pending = None

    def check():
        nonlocal passed, pending
        passed = bool(predicate())
        if passed or time.monotonic() >= deadline:
            pending = None
            root.quit()
        else:
            pending = root.after(15, check)

    pending = root.after(0, check)
    root.mainloop()
    if pending is not None:
        root.after_cancel(pending)
    return passed


def test_real_local_pipe_auth_scope_and_shutdown(tmp_path):
    service = DraftService(tmp_path / "plans", tmp_path / "templates", tmp_path / "drafts")
    host = LocalHost(service, tmp_path / "connections")
    try:
        descriptor = host.start()
        assert not host.client_connected  # Starting the app-side listener alone is OFF.
        assert call(descriptor, "connection_status", {})["result"]["live_execution"] is False
        assert not host.client_connected  # Local status reads are not external-client heartbeats.
        assert call(descriptor, "client_heartbeat", {"session_id": "a" * 32,
            "sent_at": time.monotonic() - 60})["code"] == "STALE_HEARTBEAT"
        assert not host.client_connected
        result = call(descriptor, "open_draft", {"name": "Native draft"})
        assert result["ok"]
        draft = result["result"]
        result = call(descriptor, "stage_edits", {"draft_id": draft["draft_id"], "expected_revision": 1,
            "request_id": "native", "operations": [{"op": "add_rule", "action_type": "hotkey", "fields": {"action_keys": ["enter"]}}]})
        assert result["ok"]
        assert call(descriptor, "approve_publication", {})["code"] == "TOOL_DENIED"
        assert call(descriptor, "run_test", {})["code"] == "TOOL_DENIED"
        info = json.loads(Path(descriptor).read_text(encoding="utf-8"))
        from multiprocessing.connection import Client
        from src.agent_bridge.contracts import decode, encoded
        with Client(info["address"], family="AF_PIPE") as peer:
            peer.send_bytes(encoded({"protocol": 1, "instance_id": service.instance_id,
                "token": "bad", "method": "open_draft", "arguments": {"name": "unauthorized"}}))
            assert decode(peer.recv_bytes())["code"] == "PERMISSION_REQUIRED"
        assert not host.client_connected
        with Client(info["address"], family="AF_PIPE") as peer:
            peer.send_bytes(encoded({"protocol": 1, "instance_id": service.instance_id,
                "token": "bad", "method": "client_heartbeat", "arguments": {"session_id": "a" * 32}}))
            assert decode(peer.recv_bytes())["code"] == "PERMISSION_REQUIRED"
        assert not host.client_connected
        assert len(service.list_drafts()) == 1
        for _ in range(60):
            assert call(descriptor, "connection_status", {})["ok"]
    finally:
        host.close()
        host.thread.join(5)
    assert not Path(descriptor).exists()
    assert not host.thread.is_alive()


def test_real_mcp_stdio_to_live_pipe_and_durable_publication(tmp_path):
    import asyncio
    import cv2
    import numpy as np
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from src.agent_bridge.media import MediaTools
    service = DraftService(tmp_path / "plans", tmp_path / "templates", tmp_path / "drafts")
    evidence = tmp_path / "evidence.png"
    evidence.write_bytes(cv2.imencode(".png", np.full((40, 50, 3), 150, np.uint8))[1].tobytes())
    recording = MediaTools(service).register(evidence)["recording_id"]
    host = LocalHost(service, tmp_path / "connections")
    descriptor = host.start()

    async def exercise():
        server = Path(__file__).resolve().parents[1] / "tools/wincro_mcp_server.py"
        params = StdioServerParameters(command=sys.executable, args=[str(server), "--connection-file", descriptor])
        async with stdio_client(params) as streams:
            await asyncio.sleep(.3)
            assert not host.client_connected  # A spawned server is not yet an attached GPT client.
            async with ClientSession(*streams) as client:
                await client.initialize()
                await client.list_tools()
                assert not host.client_connected  # The first real status/tool call confirms attachment.

                async def invoke(name, arguments):
                    result = await client.call_tool(name, arguments)
                    assert not result.isError, result
                    response = json.loads(result.content[0].text)
                    assert response["ok"], response
                    return response["result"]

                await invoke("connection_status", {})
                deadline = time.monotonic() + 6
                while not host.client_connected and time.monotonic() < deadline:
                    await asyncio.sleep(.05)
                assert host.client_connected
                await asyncio.sleep(3.2)
                assert host.client_connected  # Actual client ping replies renew the idle connection.
                draft = await invoke("open_draft", {"name": "MCP end-to-end"})
                await invoke("stage_edits", {"draft_id": draft["draft_id"], "expected_revision": 1,
                    "request_id": "mcp-add", "operations": [{"op": "add_rule", "action_type": "hotkey", "fields": {"action_keys": ["enter"]}}]})
                draft = await invoke("read_draft", {"draft_id": draft["draft_id"]})
                frames = await client.call_tool("get_frames", {"recording_id": recording, "timestamps": [0]})
                assert not frames.isError
                assert any(item.type == "image" for item in frames.content)
                trace = json.loads(frames.content[0].text)["frame_trace_id"]
                key = draft["plan"]["initial_rules"][0]["rule_id"]
                report = await invoke("record_analysis", {"draft_id": draft["draft_id"], "expected_revision": draft["revision"],
                    "goal": "Confirm selection", "steps": [{"rule_id": key, "recording_id": recording, "seconds": 0, "frame_trace_id": trace,
                    "observation": "A confirmation control is visible", "purpose": "Confirm the current selection",
                    "expected_result": "Move to the next screen"}]})
                # Owner-side UI authority only; there is deliberately no approval tool.
                revision = report["revision"]
                for direction in ("video_to_plan", "plan_to_video"):
                    latest = await invoke("read_draft", {"draft_id": draft["draft_id"]})
                    frames = await client.call_tool("get_frames", {"recording_id": recording, "timestamps": [0]})
                    trace = json.loads(frames.content[0].text)["frame_trace_id"]
                    checked = await invoke("record_crosscheck", {"draft_id": draft["draft_id"], "expected_revision": latest["revision"],
                        "direction": direction, "checks": [{"rule_id": key, "frame_trace_id": trace, "result": "match", "note": "The action matches the observed confirmation step"}]})
                    revision = checked["revision"]
                service.approve_publication(draft["draft_id"], revision)
                published = await invoke("publish_new_plan", {"draft_id": draft["draft_id"], "expected_revision": revision})
                assert published["saved_and_reloaded"]
                saved = json.loads((service.plans_dir / (published["plan_id"] + ".json")).read_text(encoding="utf-8"))
                assert saved["initial_rules"][0]["action_keys"] == ["enter"]
        deadline = time.monotonic() + 5
        while host.client_connected and time.monotonic() < deadline:
            await asyncio.sleep(.05)
        assert not host.client_connected
    try:
        asyncio.run(asyncio.wait_for(exercise(), timeout=60))
    finally:
        host.close()
        host.thread.join(5)


@pytest.fixture(scope="module")
def ui_root():
    """Production owns one Tcl interpreter; reuse it for successive child editors."""
    import customtkinter as ctk
    from customtkinter.windows.widgets.scaling.scaling_tracker import ScalingTracker
    old_widget, old_window = ScalingTracker.widget_scaling, ScalingTracker.window_scaling
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(ScalingTracker, "deactivate_automatic_dpi_awareness", False)
        patch.setattr(ScalingTracker, "get_window_dpi_scaling", classmethod(lambda cls, window: 1.0))
        ctk.set_widget_scaling(1.0)
        ctk.set_window_scaling(1.0)
        root = ctk.CTk()
        root.withdraw()
        root._ai_callback_errors = []
        import traceback
        root.report_callback_exception = lambda kind, value, trace: root._ai_callback_errors.append(
            "".join(traceback.format_exception(kind, value, trace)))
        try:
            yield root
        finally:
            root.destroy()
            ctk.set_widget_scaling(old_widget)
            ctk.set_window_scaling(old_window)


@pytest.mark.parametrize("scale", [1.0, 1.25, 1.5])
@pytest.mark.parametrize("cycle", range(2))
def test_draft_ui_layout_manual_conflict_and_close(tmp_path, scale, cycle, ui_root):
    import customtkinter as ctk
    from src.ui.ai_draft_editor import AIDraftEditor, MediaConsentDialog, NO_REFERENCE
    from PIL import ImageGrab
    import tkinter as tk
    ctk.set_widget_scaling(scale)
    root = ui_root
    root._ai_callback_errors.clear()
    plans, templates = tmp_path / "plans", tmp_path / "templates"
    plans.mkdir()
    templates.mkdir()
    window = AIDraftEditor(root, plans, templates, workspace=tmp_path / "workspace")
    window.overrideredirect(True)
    tk.Wm.geometry(window, "1024x768+0+0")
    try:
        for _ in range(5):
            root.update()
            time.sleep(.03)
        for label in window._wrapped:
            if label.winfo_ismapped():
                actual = label.winfo_width() / label._get_widget_scaling()
                assert abs(label.cget("wraplength") - max(120, int(actual) - 4)) <= 2
        assert window.active_page == "prepare"
        assert window.connection_status.get() == "GPT 연결 OFF"
        artifacts = Path("artifacts/ai_draft")
        artifacts.mkdir(parents=True, exist_ok=True)
        ImageGrab.grab(bbox=(0, 0, 1024, 768), all_screens=True).save(artifacts / f"ai_prepare_{int(scale*100)}pct.png")
        copied = []
        window._copy_text = copied.append  # No real clipboard interaction is needed for these assertions.
        window.request_entry.insert("1.0", "제작하고 종료해 줘")
        window._copy_request()
        assert not copied and "OFF" in window.status.get()
        from types import SimpleNamespace
        window.host = SimpleNamespace(client_connected=True, error=None, close=lambda: None)
        window.plans.set("참고 플랜")
        window.plan_options["참고 플랜"] = "plan_reference"
        window._update_connection_indicator()
        window._copy_request()
        assert window.connection_status.get() == "GPT 연결 ON"
        assert "녹화 없음" in copied[-1] and "plan_reference" in copied[-1]
        window.host = None
        window.plans.set(NO_REFERENCE)
        window._update_connection_indicator()
        assert window.connection_status.get() == "GPT 연결 OFF"
        help_dialog = window._show_help()
        for _ in range(7):
            root.update()
            time.sleep(.05)
        assert help_dialog.winfo_ismapped()
        help_dialog.lift()
        root.update()
        ImageGrab.grab(bbox=(0, 0, 1024, 768), all_screens=True).save(artifacts / f"ai_help_{int(scale*100)}pct.png")
        help_dialog.destroy()
        accepted = []
        consent = MediaConsentDialog(window, "민감정보를제거한작업녹화_긴파일이름.mp4", lambda *args: accepted.append(args) or True)
        root.update()
        assert not accepted
        assert not hasattr(consent, "consent") and not hasattr(consent, "consent_check")
        consent.log = "selected-input.json"
        assert not hasattr(consent, "exclusions")
        assert not hasattr(consent, "exclude_entry")
        assert consent.log_button.cget("state") == "normal"
        assert consent.add_button.cget("state") == "normal"
        assert "선택한 자료만" in consent.error_label.cget("text")
        for _ in range(7):
            root.update()
            time.sleep(.05)
        assert consent.winfo_ismapped()
        consent.lift()
        root.update()
        ImageGrab.grab(bbox=(0, 0, 1024, 768), all_screens=True).save(artifacts / f"ai_consent_{int(scale*100)}pct.png")
        consent._accept()
        assert accepted == [("민감정보를제거한작업녹화_긴파일이름.mp4", "selected-input.json", ())]
        assert not window.service.selected_media  # The fake acceptance above never registered data.
        window._show_page("review")
        root.update()
        draft = window.service.open_draft("AI 초안 레이아웃 검수")
        window.service.stage_edits(draft["draft_id"], 1, "setup", [
            {"op": "add_rule", "action_type": "hotkey", "fields": {"description": "확인 키 입력", "action_keys": ["enter"]}},
            {"op": "add_rule", "action_type": "click", "fields": {"description": "다음 화면 선택"}},
            {"op": "add_rule", "action_type": "type", "fields": {"description": "수량 입력", "action_text": "10"}}])
        assert _pump_until(root, lambda: window.current is not None, 4)
        assert window.current is not None  # MCP-created draft is visible without manual selection.
        assert window.plans.get() == NO_REFERENCE
        assert "AI 초안 레이아웃 검수" in window.drafts.get()
        key = window.current["plan"]["initial_rules"][0]["rule_id"]
        window.tree.selection_set(key)
        for _ in range(5):
            root.update()
            time.sleep(.03)
        window._selected_rule()
        old_revision = window.form_context[1]
        window.entries["wait_after"].delete(0, "end")
        window.entries["wait_after"].insert(0, "7.5")
        window._dirty()
        window.service.stage_edits(draft["draft_id"], old_revision, "external", [
            {"op": "update_rule", "rule_id": key, "fields": {"wait_after": 3.2}}])
        from unittest.mock import Mock
        original_item, original_move = window.tree.item, window.tree.move
        window.tree.item = Mock(wraps=original_item)
        window.tree.move = Mock(wraps=original_move)
        window._render(window.service.read_draft(draft["draft_id"]))
        assert window.tree.item.call_count == 1
        assert window.tree.move.call_count == 0
        window.tree.item, window.tree.move = original_item, original_move
        assert window.entries["wait_after"].get() == "7.5"
        assert window.form_context[1] == old_revision
        assert window.current["revision"] > old_revision
        window._apply_fields()
        assert _pump_until(root, lambda: not window.busy, 4), _job_diagnostics(window)
        assert not window.busy
        assert "REVISION_CONFLICT" in window.status.get()
        window._reload_form()
        root.update()
        assert window.entries["wait_after"].get() == "3.2"
        assert "GPT" in window.review_status.get()
        assert window.publish_button.cget("state") == "disabled"
        assert window.publish_button.cget("fg_color") != "#E0B341"
        assert "입력 키: ENTER" in window.details.get("1.0", "end")
        window.entries["wait_after"].delete(0, "end")
        window.entries["wait_after"].insert(0, "3.7")
        window._apply_fields()
        assert _pump_until(root, lambda: not window.busy, 5), _job_diagnostics(window)
        assert not window.busy and not window.form_dirty, _job_diagnostics(window)
        assert "수정 사항을 초안 파일에 저장" in window.status.get()
        restored = DraftService(plans, templates, window.service.workspace).read_draft(draft["draft_id"])
        assert restored["plan"] == window.current["plan"]
        assert restored["plan"]["initial_rules"][0]["wait_after"] == 3.7
        window._save()
        assert _pump_until(root, lambda: not window.busy, 5), _job_diagnostics(window)
        assert "저장·재로딩 확인 완료" in window.status.get()
        assert not root._ai_callback_errors, root._ai_callback_errors
        # Text/controls stay inside the window's client bounds at each scaling.
        for widget in [window.tree, window.drafts, window.publish_button, window.connection_badge, *window.entries.values()]:
            assert widget.winfo_width() > 40
            assert window.winfo_rootx() <= widget.winfo_rootx()
            assert widget.winfo_rootx() + widget.winfo_width() <= window.winfo_rootx() + window.winfo_width() + 2
        assert window.winfo_screenwidth() >= 1024 and window.winfo_screenheight() >= 768
        ImageGrab.grab(bbox=(0, 0, 1024, 768), all_screens=True).save(artifacts / f"ai_draft_{int(scale*100)}pct.png")
    finally:
        window.form_dirty = False
        window._close()
        root.update()
        ctk.set_widget_scaling(1.0)
    assert window.service.closed


@pytest.mark.parametrize("start_fails", [False, True])
def test_closing_during_connection_start_cannot_leave_a_listener_or_reopen_service(tmp_path, ui_root, monkeypatch, start_fails):
    from threading import Event
    from src.ui.ai_draft_editor import AIDraftEditor
    from src.agent_bridge import transport
    entered, release, stopped = Event(), Event(), Event()

    class DelayedHost:
        def start(self):
            entered.set()
            assert release.wait(5)
            if start_fails:
                raise OSError("simulated connection start failure")
            return "connection.json"

        def close(self):
            stopped.set()

    window = AIDraftEditor(ui_root, tmp_path / "plans", tmp_path / "templates", workspace=tmp_path / "workspace")
    monkeypatch.setattr(transport, "LocalHost", lambda *_: DelayedHost())
    try:
        window._toggle()
        assert entered.wait(3)
        window._close()
        release.set()
        assert stopped.wait(5)
        assert window.closed and window.service.closed and window.host is None
    finally:
        release.set()
        window.form_dirty = False
        window._close()
        ui_root.update()


@pytest.mark.parametrize("scale", [1.0, 1.25, 1.5])
def test_recording_workspace_layout_and_selection_without_capture(ui_root, monkeypatch, tmp_path, scale):
    import customtkinter as ctk
    import tkinter as tk
    from types import SimpleNamespace
    from unittest.mock import Mock
    from PIL import ImageGrab
    from src.ui import main_window, recorder_view, recording_workspace
    from datetime import datetime

    config = SimpleNamespace(recording=SimpleNamespace(fps=30, include_cursor=True, save_input_log=True))
    session, capture, database = Mock(), Mock(), Mock()
    database.get_all_recordings.return_value = []
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(recording_workspace, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(recorder_view, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(main_window, "get_config", lambda: config)
    monkeypatch.setattr(recorder_view, "RecordingSession", Mock(return_value=session))
    monkeypatch.setattr(recorder_view, "get_screen_recorder", Mock(return_value=capture))
    monkeypatch.setattr(recorder_view, "get_db", Mock(return_value=database))
    monkeypatch.setattr(recorder_view.RecorderView, "_setup_hotkeys", Mock())
    monkeypatch.setattr(recorder_view.RecorderView, "_decode_media_thumbnail", lambda *_: None)
    ctk.set_widget_scaling(scale)
    ui_root._ai_callback_errors.clear()
    window = ctk.CTkToplevel(ui_root)
    window.overrideredirect(True)
    tk.Wm.geometry(window, "1024x768+0+0")
    window.grid_columnconfigure(1, weight=1)
    window.grid_rowconfigure(0, weight=1)
    sidebar = ctk.CTkFrame(window, width=180)
    sidebar.grid(row=0, column=0, sticky="ns")
    sidebar.grid_propagate(False)
    log_panel = ctk.CTkFrame(window, height=100)
    log_panel.grid(row=1, column=0, columnspan=2, sticky="ew")
    view = None
    try:
        view = recorder_view.RecorderView(window)
        view.grid(row=0, column=1, sticky="nsew")
        assert _pump_until(ui_root, lambda: view._workspace.analyze.winfo_ismapped() and not view._workspace.busy, 5)
        deadline = time.monotonic() + .25
        _pump_until(ui_root, lambda: time.monotonic() > deadline, 2)
        workspace = view._workspace
        assert workspace.badge.cget("text") == "GPT 연결 OFF"
        assert workspace.jobs.host is None and not workspace.jobs.busy
        assert workspace.progress_percent.cget("text") == "0%"
        for widget in (workspace.progress_label, workspace.progress_bar, workspace.progress_percent):
            assert widget.winfo_ismapped()
            assert widget.winfo_rootx() + widget.winfo_width() <= window.winfo_rootx() + window.winfo_width()
            assert widget.winfo_rooty() + widget.winfo_height() <= window.winfo_rooty() + window.winfo_height()
        for button in (view._start_btn, view._stop_btn, workspace.analyze, workspace.cancel_button):
            assert button.winfo_ismapped()
            assert window.winfo_rootx() <= button.winfo_rootx()
            assert button.winfo_rootx() + button.winfo_width() <= window.winfo_rootx() + window.winfo_width()
            assert button.winfo_rooty() + button.winfo_height() <= window.winfo_rooty() + window.winfo_height()
            assert button._text_label.winfo_reqwidth() <= button.winfo_width() - 4
        assert not hasattr(view, "_fps_combo")
        assert _pump_until(ui_root, lambda: view._catalog_loaded, 5)
        sample = recorder_view.TemplateMediaItem(path=str(tmp_path / "sample.mp4"),
            name="긴 녹화 이름도 선택·삭제 버튼을 밀거나 잘라서는 안 됩니다", media_type="video", size_bytes=1, modified_at=datetime.now())
        view._apply_recordings_list([sample])
        assert _pump_until(ui_root, lambda: 0 in view._recordings_scroll._visible_widgets, 5)
        deadline = time.monotonic() + .2
        _pump_until(ui_root, lambda: time.monotonic() > deadline, 2)
        row = view._recordings_scroll._visible_widgets[0]
        def row_buttons(widget):
            for child in widget.winfo_children():
                if isinstance(child, ctk.CTkButton):
                    yield child
                yield from row_buttons(child)
        for button in row_buttons(row):
            assert button.winfo_rootx() + button.winfo_width() <= view.winfo_rootx() + view.winfo_width()
            assert button._text_label.winfo_reqwidth() <= button.winfo_width() - 4
            canvas = view._recordings_scroll._canvas
            assert button.winfo_rooty() >= canvas.winfo_rooty()
            assert button.winfo_rooty() + button.winfo_height() <= canvas.winfo_rooty() + canvas.winfo_height()
            for sibling in button.master.winfo_children():
                if isinstance(sibling, ctk.CTkFrame) and sibling.cget("width") == 40 and sibling.cget("height") == 40:
                    assert sibling.winfo_rooty() >= canvas.winfo_rooty()
                    assert sibling.winfo_rooty() + sibling.winfo_height() <= canvas.winfo_rooty() + canvas.winfo_height()
        workspace.analyze.invoke()
        assert "선택" in workspace.status.cget("text")
        session.start.assert_not_called()
        capture.start.assert_not_called()
        database.create_recording.assert_not_called()
        assert not ui_root._ai_callback_errors, ui_root._ai_callback_errors
        artifacts = Path("artifacts/ai_draft")
        artifacts.mkdir(parents=True, exist_ok=True)
        ImageGrab.grab(bbox=(0, 0, 1024, 768), all_screens=True).save(artifacts / f"recording_workspace_{int(scale*100)}pct.png")
        workspace.tabs.set("분석 결과 편집")
        deadline = time.monotonic() + .25
        _pump_until(ui_root, lambda: time.monotonic() > deadline, 2)
        assert workspace.result.save.winfo_ismapped()
        assert workspace.result.save.winfo_rooty() + workspace.result.save.winfo_height() <= window.winfo_height()
        pane = workspace.result
        ImageGrab.grab(bbox=(0, 0, 1024, 768), all_screens=True).save(artifacts / f"recording_result_{int(scale*100)}pct.png")
        for widget in (pane.title_entry, pane.register_button, pane.continue_button, pane.registration_status, pane.summary, pane.tree):
            assert widget.winfo_ismapped()
            assert widget.winfo_rootx() >= pane.winfo_rootx()
            assert widget.winfo_rootx() + widget.winfo_width() <= pane.winfo_rootx() + pane.winfo_width()
            assert widget.winfo_rooty() >= pane.winfo_rooty()
            assert widget.winfo_rooty() + widget.winfo_height() <= pane.winfo_rooty() + pane.winfo_height()
        assert pane.register_button._text_label.winfo_reqwidth() <= pane.register_button.winfo_width() - 4
        assert pane.continue_button._text_label.winfo_reqwidth() <= pane.continue_button.winfo_width() - 4
        assert pane.registration_status._label.winfo_reqwidth() <= pane.registration_status.winfo_width()
        assert pane.tree.winfo_height() >= 50, "Registration controls must not hide the action list"
        assert pane.title_entry.winfo_rootx() + pane.title_entry.winfo_width() <= pane.register_button.winfo_rootx()
        for entry in pane.entries.values():
            assert entry.winfo_width() >= round(50 * scale)
            assert entry.winfo_rootx() + entry.winfo_width() <= pane.winfo_rootx() + pane.winfo_width()
        for label in pane.entries["description"].master.winfo_children():
            if isinstance(label, ctk.CTkLabel):
                assert label._label.winfo_reqwidth() <= label.winfo_width()
                assert label.winfo_rootx() + label.winfo_width() <= pane.winfo_rootx() + pane.winfo_width()
        assert _pump_until(ui_root, lambda: not workspace.busy, 5)
        ctk.set_window_scaling(scale)
        workspace._settings()
        settings = next(child for child in workspace.winfo_children() if isinstance(child, ctk.CTkToplevel))
        assert _pump_until(ui_root, lambda: settings.winfo_ismapped() and not workspace.busy, 5)
        deadline = time.monotonic() + .3
        _pump_until(ui_root, lambda: time.monotonic() > deadline, 2)
        save_settings = next(child for child in settings.winfo_children()
            if isinstance(child, ctk.CTkButton) and child.cget("text") == "설정 저장")
        assert save_settings.winfo_rooty() + save_settings.winfo_height() <= settings.winfo_rooty() + settings.winfo_height()
        assert settings.winfo_rootx() + settings.winfo_width() <= settings.winfo_screenwidth()
        assert settings.winfo_rooty() + settings.winfo_height() <= settings.winfo_screenheight()
        ImageGrab.grab(bbox=(0, 0, 1024, 768), all_screens=True).save(artifacts / f"recording_settings_{int(scale*100)}pct.png")
        settings.destroy()
        ctk.set_window_scaling(1)
        tk.Wm.geometry(window, "1024x768+0+0")
        assert _pump_until(ui_root, lambda: not workspace.busy, 5)
        workspace.tabs.set("녹화·분석")
        # Isolate animation rendering; the separate pipeline test uses real job events.
        workspace.after_cancel(workspace._after_id)
        workspace._after_id = None
        from src.ui.analysis_progress import AnalysisProgress
        workspace.choose(sample)
        long_status = "GPT가 녹화 장면을 확인하고 초안을 편집하고 있습니다. " * 5
        workspace.set_status(long_status)
        clock = [0.0]
        workspace._analysis_progress = AnalysisProgress(clock=lambda: clock[0])
        workspace._progress_job_id = "job_progress"
        progress_state = {"job_id": "job_progress", "status": "running"}
        workspace._refresh_analysis_progress(progress_state, True)
        ui_root.update_idletasks()
        widgets = (workspace.progress_label, workspace.progress_bar, workspace.progress_percent)
        positions = [(widget.winfo_x(), widget.winfo_y(), widget.winfo_width(), widget.winfo_height()) for widget in widgets]
        values = []
        for tick in range(1, 10):
            clock[0] = tick * 5
            workspace._refresh_analysis_progress(progress_state, True)
            ui_root.update_idletasks()
            values.append(workspace.progress_bar.get())
            assert [(widget.winfo_x(), widget.winfo_y(), widget.winfo_width(), widget.winfo_height()) for widget in widgets] == positions
        assert values == sorted(values) and len(set(values)) > 1
        assert workspace.progress_percent.cget("text") != "100%"
        assert workspace.progress_percent._label.winfo_reqwidth() <= workspace.progress_percent.winfo_width()
        assert workspace.progress_label._label.winfo_reqwidth() <= workspace.progress_label.winfo_width()
        assert workspace._status_detail == long_status
        assert workspace.status._label.winfo_reqwidth() <= workspace.status.winfo_width()
        assert workspace.status._label.winfo_reqheight() <= workspace.status.winfo_height()
        deadline = time.monotonic() + .3
        assert _pump_until(ui_root, lambda: time.monotonic() >= deadline, 2)
        ImageGrab.grab(bbox=(0, 0, 1024, 768), all_screens=True).save(artifacts / f"recording_progress_{int(scale*100)}pct.png")
        progress_row = workspace.progress_bar.master
        left, top = progress_row.winfo_rootx(), progress_row.winfo_rooty()
        assert left >= 0 and top >= 0
        assert left + progress_row.winfo_width() <= min(window.winfo_rootx() + window.winfo_width(), window.winfo_screenwidth())
        assert top + progress_row.winfo_height() <= min(window.winfo_rooty() + window.winfo_height(), window.winfo_screenheight())
        ImageGrab.grab(bbox=(left, top, left+progress_row.winfo_width(), top+progress_row.winfo_height()),
            all_screens=True).save(artifacts / f"recording_progress_bar_{int(scale*100)}pct.png")
        from PIL import ImageColor
        bar = workspace.progress_bar
        x, y = bar.winfo_rootx(), bar.winfo_rooty()
        pixels = ImageGrab.grab(bbox=(x, y, x+bar.winfo_width(), y+bar.winfo_height()), all_screens=True).convert("RGB")
        gold = ImageColor.getrgb(recording_workspace.COLORS["accent"])
        assert sum(1 for pixel in pixels.getdata() if max(abs(a-b) for a, b in zip(pixel, gold)) <= 5) > pixels.width * pixels.height * .1
        progress_state["status"] = "review"
        workspace._refresh_analysis_progress(progress_state, False)
        assert workspace.progress_percent.cget("text") == "100%"
        assert workspace.progress_bar.cget("progress_color") == recording_workspace.COLORS["success"]
        workspace._refresh_analysis_progress(dict(progress_state, status="error"), False)
        assert workspace.progress_percent.cget("text") != "100%"
        assert workspace.progress_bar.cget("progress_color") == recording_workspace.COLORS["error"]
        assert not ui_root._ai_callback_errors, ui_root._ai_callback_errors
    finally:
        if view is not None:
            view.cleanup()
        window.destroy()
        ui_root.update()
        ctk.set_widget_scaling(1.0)
        ctk.set_window_scaling(1.0)


def test_recorded_character_timing_visible_in_owned_text_widget(monkeypatch, ui_root):
    """Actual Tk clipboard/paste states on CI; no global mouse/keyboard injection."""
    import queue
    import threading
    import tkinter as tk
    from types import SimpleNamespace
    from PIL import ImageGrab
    from src.player import rule_executor
    from src.player.rule_executor import RuleExecutor

    window = tk.Toplevel(ui_root)
    window.title("Recorded typing QA")
    window.geometry("600x220+0+0")
    text = tk.Text(window, font=("Malgun Gothic", 28), width=20, height=3)
    text.pack()
    Path("artifacts/ai_draft").mkdir(parents=True, exist_ok=True)
    pending = queue.Queue()
    snapshots = []
    finished = threading.Event()
    result = []
    executor = object.__new__(RuleExecutor)
    executor._stop_event = threading.Event()
    executor._wait_for_resume = lambda: False
    executor._auto_list_wait = lambda seconds: not executor._stop_event.wait(seconds)
    def paste(*keys):
        assert keys == ('ctrl', 'v')
        ack = threading.Event()
        pending.put(ack)
        return ack.wait(3)
    monkeypatch.setattr(rule_executor, "get_input_controller", lambda: SimpleNamespace(hotkey=paste))
    def play():
        try:
            result.append(executor._type_rule_text(SimpleNamespace(action_text="안녕하세요",
                typing_timestamps=[0, .25, .6, 1.1, 1.5], typing_random=False)))
        finally:
            finished.set()
    def pump_paste():
        try:
            ack = pending.get_nowait()
        except queue.Empty:
            pass
        else:
            text.event_generate("<<Paste>>")
            snapshots.append(text.get("1.0", "end-1c"))
            if snapshots[-1] == "안녕":
                ui_root.update_idletasks()
                ImageGrab.grab(bbox=(0, 0, 620, 250)).save(Path("artifacts/ai_draft/typing_greeting_in_progress.png"))
            ack.set()
        return finished.is_set()
    worker = threading.Thread(target=play, daemon=True)
    try:
        ui_root.update()
        worker.start()
        assert _pump_until(ui_root, pump_paste, 8)
        worker.join(2)
        assert result == [True]
        assert snapshots == ["안", "안녕", "안녕하", "안녕하세", "안녕하세요"]
        ImageGrab.grab(bbox=(0, 0, 620, 250)).save(Path("artifacts/ai_draft/typing_greeting_complete.png"))
    finally:
        executor._stop_event.set()
        worker.join(4)
        window.destroy()


def test_recording_pipeline_autonomous_without_consent_or_question_dialogs(tmp_path, monkeypatch, ui_root):
    import importlib.util
    spec = importlib.util.spec_from_file_location("recording_workspace_scenario", Path(__file__).with_name("recording_workspace_scenario.py"))
    scenario = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scenario)
    scenario.exercise_recording_pipeline(tmp_path, monkeypatch, ui_root, _pump_until)


@pytest.mark.parametrize("scale,size", [(1.0, 40), (1.25, 60), (1.5, 100)])
def test_plan_video_thumbnail_opens_current_video_before_load_and_from_cache(tmp_path, monkeypatch, ui_root, scale, size):
    from collections import deque
    import threading
    import customtkinter as ctk
    from PIL import ImageGrab
    from src.ui import player_view, analyzer_view
    from src.ui.ui_batcher import UiCallbackDispatcher
    from src.analyzer.automation_models import AutomationRule, AutomationPlan

    root = Path(__file__).resolve().parents[1]
    plan_file = root / "data/plans/plan_20260118_174859.json"
    plan_before = plan_file.read_bytes()
    state = json.loads(plan_before)
    saved_rule = dict(state["initial_rules"][12]["children"][0])
    saved_rule["target_image"] = "template_video_20260703_154056_306.mp4"
    rule = AutomationRule.from_dict(saved_rule, root / "data/templates")
    video = Path(rule.target_image)
    assert video.suffix == ".mp4"
    video_before = video.read_bytes()
    monkeypatch.setattr(player_view, "setup_window_position", lambda *args, **kwargs: None)
    monkeypatch.setattr(analyzer_view, "setup_window_position", lambda *args, **kwargs: None)
    ctk.set_widget_scaling(scale)
    ctk.set_window_scaling(1)
    owner = player_view.PlanDetailDialog.__new__(player_view.PlanDetailDialog)
    ctk.CTkToplevel.__init__(owner, ui_root)
    owner.title("13-1 video thumbnail QA")
    owner.geometry("400x280+0+0")
    owner.configure(fg_color="#0d0b07")
    owner._plan = AutomationPlan(name="Thumbnail only", initial_rules=[rule])
    owner._thumbnail_closed = False
    owner._thumbnail_refs = deque(maxlen=32)
    owner._image_rule_cache = None
    owner._ui_dispatcher = UiCallbackDispatcher(owner)
    gate = threading.Event()
    original_loader = player_view.load_video_thumbnail
    reads = []
    def held_loader(path, requested_size):
        reads.append(path)
        assert gate.wait(10)
        return original_loader(path, requested_size)
    monkeypatch.setattr(player_view, "load_video_thumbnail", held_loader)
    player_view.invalidate_thumbnail_cache(str(video))
    frame = ctk.CTkFrame(owner, width=size+8, height=size+8, fg_color="#0d0b07")
    frame.pack(pady=15)
    frame.pack_propagate(False)
    opened = []
    def close_editor():
        dialogs = [child for child in owner.winfo_children() if isinstance(child, analyzer_view.ImageCropDialog)]
        assert len(dialogs) == 1
        editor = dialogs[0]
        opened.append((editor._image_path, editor._is_video, editor._original_image is not None))
        editor.destroy()
    original_open = owner._open_image_editor
    def open_and_verify(path, target):
        assert target is rule
        ui_root.after(200, close_editor)
        original_open(path, target)
    owner._open_image_editor = open_and_verify
    try:
        owner._display_thumbnail(frame, rule, size)
        placeholder = frame.winfo_children()[0]
        assert isinstance(placeholder, ctk.CTkButton)
        assert placeholder.cget("text") == "▶"
        placeholder.invoke()
        assert opened == [(str(video), True, True)]
        gate.set()
        assert _pump_until(ui_root, lambda: not placeholder.winfo_exists() and bool(frame.winfo_children()), 8)
        button = frame.winfo_children()[0]
        assert button.cget("image") is not None and button.cget("text") == ""
        owner.lift()
        settled = time.monotonic() + .35
        assert _pump_until(ui_root, lambda: time.monotonic() >= settled and button.winfo_ismapped(), 3)
        owner.update_idletasks()
        assert button.winfo_width() <= frame.winfo_width()
        assert button.winfo_height() <= frame.winfo_height()
        artifacts = Path("artifacts/ai_draft")
        artifacts.mkdir(parents=True, exist_ok=True)
        left, top = frame.winfo_rootx(), frame.winfo_rooty()
        screenshot = ImageGrab.grab(bbox=(left, top, left+frame.winfo_width(), top+frame.winfo_height()), all_screens=True)
        screenshot.save(artifacts / f"action_video_thumbnail_{int(scale*100)}pct.png")
        assert sum(1 for pixel in screenshot.convert("RGB").getdata()
            if all(abs(a-b) <= 5 for a, b in zip(pixel, (96, 165, 250)))) > 20, "Video frame/play badge was not painted"
        cached_frame = ctk.CTkFrame(owner, width=size+8, height=size+8)
        cached_frame.pack()
        cached_frame.pack_propagate(False)
        owner._display_thumbnail(cached_frame, rule, size)
        cached = cached_frame.winfo_children()[0]
        assert cached.cget("image") is button.cget("image")
        cached.invoke()
        assert opened == [(str(video), True, True)] * 2
        assert reads == [str(video)]
        assert video.read_bytes() == video_before
        assert plan_file.read_bytes() == plan_before
        assert not ui_root._ai_callback_errors, ui_root._ai_callback_errors
    finally:
        gate.set()
        owner._thumbnail_closed = True
        owner._ui_dispatcher.close()
        owner.destroy()
        ctk.set_widget_scaling(1)
        ctk.set_window_scaling(1)


@pytest.mark.parametrize("scale", [1.0, 1.25, 1.5])
@pytest.mark.parametrize("compact", [False, True])
def test_action_coordinate_labels_after_region_edit_and_durable_reload(ui_root, monkeypatch, tmp_path, scale, compact):
    from collections import deque
    from types import SimpleNamespace
    from unittest.mock import Mock
    import customtkinter as ctk
    from PIL import ImageGrab
    from src.analyzer.automation_models import AutomationPlan, AutomationRule
    from src.ui import analyzer_view, player_view
    from src.ui.ui_batcher import UiCallbackDispatcher

    ui_root._ai_callback_errors.clear()
    root = Path(__file__).resolve().parents[1]
    plan_file = root / "data/plans/plan_20260118_174859.json"
    original_bytes = plan_file.read_bytes()
    raw_rules = json.loads(original_bytes)["initial_rules"][12]["children"][:2]
    rules = [AutomationRule.from_dict(raw, root / "data/templates") for raw in raw_rules]
    rules[0].target_image = str(root / "data/templates/template_video_20260703_154056_306.mp4")
    original_name = rules[1].description
    assert "1301" in original_name and "530" in original_name
    monkeypatch.setattr(player_view, "setup_window_position", lambda *args, **kwargs: None)
    monkeypatch.setattr(analyzer_view, "setup_window_position", lambda *args, **kwargs: None)
    monkeypatch.setattr(player_view, "PLANS_DIR", tmp_path / "plans")
    original_editor = player_view.ImageCropDialog

    def initialized_editor(*args, **kwargs):
        editor = original_editor(*args, **kwargs)
        editor._coordinate_qa_ready = True
        return editor

    monkeypatch.setattr(player_view, "ImageCropDialog", initialized_editor)
    ctk.set_widget_scaling(scale)
    # DPI scales both the window and its controls, not controls alone.
    ctk.set_window_scaling(scale)
    owner = player_view.PlanDetailDialog.__new__(player_view.PlanDetailDialog)
    ctk.CTkToplevel.__init__(owner, ui_root)
    owner.title("Action coordinate display QA")
    owner.geometry("1080x760+0+0")
    owner.minsize(980, 700)
    owner._plan = AutomationPlan(name="Coordinate display QA", initial_rules=rules)
    owner._name_entry = SimpleNamespace(get=lambda: "")
    owner._font_cache = {}
    owner._rule_widgets = {}
    owner._collapsed_items = set()
    owner._selected_rule = None
    owner._active_partial_rule_id = None
    owner._running_rule_id = None
    owner._compact_rule_rows = compact
    owner._thumbnail_closed = False
    owner._thumbnail_refs = deque(maxlen=32)
    owner._image_rule_cache = None
    owner._ui_dispatcher = UiCallbackDispatcher(owner)
    owner._refresh_action_list = Mock(side_effect=AssertionError("Unexpected full-list refresh"))
    owner._refresh_rule_row = Mock(side_effect=AssertionError("Unexpected row recreation"))
    try:
        for index, rule in enumerate(rules, 1):
            if compact:
                wrapper = owner._create_compact_rule_item(owner, rule, index_str=f"13-{index}")
            else:
                wrapper = owner._create_action_item(owner, rule, index_str=f"13-{index}", use_pack=False)
            wrapper.pack(fill="x")
        widget_data = owner._rule_widgets[rules[1].rule_id]
        row_before, label_before = widget_data["widget"], widget_data["name_label"]
        assert "1301" not in label_before.cget("text")
        applied = []
        timeout_state = []
        poll_after = [None]

        def edit_region_and_close():
            poll_after[0] = None
            dialogs = [child for child in owner.winfo_children() if isinstance(child, analyzer_view.ImageCropDialog)]
            if len(dialogs) != 1 or not dialogs[0].__dict__.get("_coordinate_qa_ready"):
                poll_after[0] = ui_root.after(25, edit_region_and_close)
                return
            editor = dialogs[0]
            try:
                applied.append(editor._apply_search_region_to_rule([600, 401, 800, 601]))
                assert editor._current_search_region() == [600, 401, 800, 601]
                assert "200x200" in editor._search_radius_btn.cget("text")
            finally:
                editor.destroy()

        def abort_editor_wait():
            state = {"callback_errors": list(ui_root._ai_callback_errors),
                "children": [type(child).__name__ for child in owner.winfo_children()]}
            timeout_state.append(state)
            artifacts = Path("artifacts/ai_draft")
            artifacts.mkdir(parents=True, exist_ok=True)
            (artifacts / f"coordinate_timeout_{compact}_{scale}.json").write_text(
                json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            for child in owner.winfo_children():
                if isinstance(child, analyzer_view.ImageCropDialog):
                    child.destroy()

        poll_after[0] = ui_root.after(25, edit_region_and_close)
        watchdog = ui_root.after(8000, abort_editor_wait)
        try:
            owner._open_image_editor(rules[1].target_image, rules[1])
        finally:
            ui_root.after_cancel(watchdog)
            if poll_after[0] is not None:
                ui_root.after_cancel(poll_after[0])
        assert not timeout_state, timeout_state
        assert applied == [True]
        assert widget_data["widget"] is row_before and widget_data["name_label"] is label_before
        label_text = label_before.cget("text")
        if compact:
            assert "(700, 501)" in label_text
        else:
            assert label_text == ""
            label_text = widget_data["detail_label"].cget("text")
            assert "(700, 501)" in label_text
        assert "1301" not in label_text and "530" not in label_text
        assert rules[1].description == original_name
        owner._refresh_action_list.assert_not_called()
        owner._refresh_rule_row.assert_not_called()
        assert owner._save_plan(show_message=False)
        saved = json.loads((player_view.PLANS_DIR / f"{owner._plan.plan_id}.json").read_text(encoding="utf-8"))
        reloaded = AutomationPlan.from_dict(saved, root / "data/templates")
        assert reloaded.to_dict() == owner._plan.to_dict()
        assert reloaded.initial_rules[1].description == original_name
        assert reloaded.initial_rules[1].search_region == [600, 401, 800, 601]
        assert owner._compact_rule_label_text(reloaded.initial_rules[1]) == owner._compact_rule_label_text(rules[1])
        assert plan_file.read_bytes() == original_bytes
        owner.lift()
        settled = time.monotonic() + .35
        assert _pump_until(ui_root, lambda: time.monotonic() >= settled and label_before.winfo_ismapped(), 3)
        coordinate_label = label_before if compact else widget_data["detail_label"]
        assert coordinate_label._label.winfo_reqwidth() <= coordinate_label.winfo_width()
        artifacts = Path("artifacts/ai_draft")
        artifacts.mkdir(parents=True, exist_ok=True)
        left, top = owner.winfo_rootx(), owner.winfo_rooty()
        mode = "compact" if compact else "detail"
        ImageGrab.grab(bbox=(left, top, left+min(owner.winfo_width(), ui_root.winfo_screenwidth()),
            top+min(owner.winfo_height(), ui_root.winfo_screenheight())), all_screens=True).save(
                artifacts / f"action_coordinates_{mode}_{int(scale*100)}pct.png")
        assert not ui_root._ai_callback_errors, ui_root._ai_callback_errors
    finally:
        owner._thumbnail_closed = True
        owner._ui_dispatcher.close()
        owner.destroy()
        ctk.set_widget_scaling(1)
        ctk.set_window_scaling(1)


@pytest.mark.parametrize("scale", [1.0, 1.25, 1.5])
def test_analyzer_only_playlist_layout_refresh_and_edit_without_plan_writes(ui_root, monkeypatch, tmp_path, scale):
    import customtkinter as ctk
    import tkinter as tk
    from types import SimpleNamespace
    from unittest.mock import Mock
    from PIL import ImageGrab
    from src.analyzer.automation_models import AutomationPlan
    from src.ui import analyzer_view, main_window

    def descendants(widget):
        for child in widget.winfo_children():
            yield child
            yield from descendants(child)

    def buttons(widget):
        return [child for child in descendants(widget) if isinstance(child, ctk.CTkButton)]

    def settle_layout():
        # Mapping a Tk window is earlier than its final geometry/paint on Windows.
        deadline = time.monotonic() + .3
        assert _pump_until(ui_root, lambda: time.monotonic() >= deadline, 2)
        ui_root.update_idletasks()

    def assert_controls_fit():
        for button in buttons(view):
            if not button.winfo_ismapped():
                continue
            assert view.winfo_rootx() <= button.winfo_rootx(), button.cget("text")
            assert button.winfo_rootx() + button.winfo_width() <= view.winfo_rootx() + view.winfo_width(), button.cget("text")
            assert button._text_label.winfo_reqwidth() <= button.winfo_width() - 4, button.cget("text")

    plans = tmp_path / "plans"
    plans.mkdir()
    expected = {}
    for index in range(24):
        plan = AutomationPlan(plan_id=f"plan_{index:02d}", name=f"{index + 1:02d}. 분석된 재생목록 이름이 길 때도 버튼이 잘리지 않아야 합니다")
        path = plans / f"{plan.plan_id}.json"
        path.write_text(json.dumps(plan.to_dict(), ensure_ascii=False), encoding="utf-8")
        expected[path.name] = path.read_bytes()
    database = Mock()
    database.get_all_recordings.return_value = [SimpleNamespace(automation_plan_id="plan_00", locked=True)]
    monkeypatch.setattr(analyzer_view, "get_db", lambda: database)
    monkeypatch.setattr(analyzer_view, "DATA_DIR", tmp_path)
    monkeypatch.setattr(analyzer_view, "PLANS_DIR", plans)
    monkeypatch.setattr(main_window, "get_config", lambda: SimpleNamespace())
    forbidden_save = Mock(side_effect=AssertionError("Opening and refreshing must not save a plan"))
    monkeypatch.setattr(analyzer_view, "dump_json_file", forbidden_save)
    ctk.set_widget_scaling(scale)
    ui_root._ai_callback_errors.clear()
    window = ctk.CTkToplevel(ui_root)
    window.overrideredirect(True)
    tk.Wm.geometry(window, "1024x768+0+0")
    window.grid_columnconfigure(1, weight=1)
    window.grid_rowconfigure(0, weight=1)
    sidebar = ctk.CTkFrame(window, width=180)
    sidebar.grid(row=0, column=0, sticky="ns")
    sidebar.grid_propagate(False)
    log_panel = ctk.CTkFrame(window, height=120)
    log_panel.grid(row=1, column=0, columnspan=2, sticky="ew")
    view = None
    try:
        view = analyzer_view.AnalyzerView(window)
        view.grid(row=0, column=1, sticky="nsew")
        assert _pump_until(ui_root, lambda: len(view._plan_items) == 24
            and 0 in view._plans_scroll._visible_widgets
            and view._plans_scroll._visible_widgets[0].winfo_ismapped(), 10)
        settle_layout()
        labels = [child.cget("text") for child in descendants(view) if isinstance(child, ctk.CTkLabel)]
        assert "분석된 재생 목록" in labels
        assert not {"녹화 목록", "분석 실행", "분석 결과"} & set(labels)
        assert not hasattr(view, "_recordings_scroll")
        assert not hasattr(view, "_video_analyzer")
        viewport = view._plans_scroll._canvas
        assert viewport.winfo_width() > view.winfo_width() * .85
        assert viewport.winfo_height() > view.winfo_height() * .5
        row = view._plans_scroll._visible_widgets[0]
        first_buttons = {button.cget("text"): button for button in buttons(row)}
        assert {"수정", "삭제", "잠금 해제"} <= set(first_buttons)
        assert all(button.winfo_ismapped() for button in first_buttons.values())
        assert_controls_fit()
        artifact_dir = Path("artifacts/ai_draft")
        artifact_dir.mkdir(parents=True, exist_ok=True)
        ImageGrab.grab(bbox=(0, 0, 1024, 768), all_screens=True).save(
            artifact_dir / f"analyzer_playlist_only_{int(scale * 100)}pct.png")

        first_path = plans / "plan_00.json"
        data = json.loads(first_path.read_text(encoding="utf-8"))
        data["name"] = "이름을 수정한 재생목록"
        first_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        expected[first_path.name] = first_path.read_bytes()
        view.refresh()
        assert _pump_until(ui_root, lambda: view._plan_items[0].name == data["name"]
            and view._plans_scroll._visible_widgets[0].winfo_ismapped(), 10)
        ui_root.update_idletasks()
        row = view._plans_scroll._visible_widgets[0]
        changed_labels = [child.cget("text") for child in descendants(row) if isinstance(child, ctk.CTkLabel)]
        assert data["name"] in changed_labels
        edit = Mock()
        view._edit_plan = edit
        next(button for button in buttons(row) if button.cget("text") == "수정").invoke()
        assert edit.call_args.args[0].name == data["name"]

        view._plans_scroll.scroll_to_item(23)
        assert _pump_until(ui_root, lambda: 23 in view._plans_scroll._visible_widgets
            and view._plans_scroll._visible_widgets[23].winfo_ismapped(), 5)
        settle_layout()
        last = view._plans_scroll._visible_widgets[23]
        assert viewport.winfo_rooty() <= last.winfo_rooty()
        assert last.winfo_rooty() + last.winfo_height() <= viewport.winfo_rooty() + viewport.winfo_height() + 2
        assert len(view._plans_scroll._visible_widgets) < 24

        next_scale = 1.5 if scale == 1.0 else 1.0
        ctk.set_widget_scaling(next_scale)
        assert _pump_until(ui_root, lambda: view._plans_scroll._item_height == round(56 * next_scale), 5)
        view._plans_scroll.scroll_to_item(23)
        assert _pump_until(ui_root, lambda: 23 in view._plans_scroll._visible_widgets
            and view._plans_scroll._visible_widgets[23].winfo_ismapped(), 5)
        settle_layout()
        last = view._plans_scroll._visible_widgets[23]
        assert viewport.winfo_rooty() <= last.winfo_rooty()
        assert last.winfo_rooty() + last.winfo_height() <= viewport.winfo_rooty() + viewport.winfo_height() + 2
        assert_controls_fit()
        assert {path.name: path.read_bytes() for path in plans.iterdir()} == expected
        forbidden_save.assert_not_called()
        database.create_recording.assert_not_called()
        database.update_recording_ai_analyzed.assert_not_called()
        assert not ui_root._ai_callback_errors, ui_root._ai_callback_errors
    finally:
        if view is not None:
            try:
                artifact_dir = Path("artifacts/ai_draft")
                artifact_dir.mkdir(parents=True, exist_ok=True)
                ImageGrab.grab(bbox=(0, 0, 1024, 768), all_screens=True).save(
                    artifact_dir / f"analyzer_playlist_end_{int(scale * 100)}_to_{int(view._get_widget_scaling() * 100)}pct.png")
            finally:
                view.cleanup()
        window.destroy()
        ui_root.update()
        ctk.set_widget_scaling(1.0)
