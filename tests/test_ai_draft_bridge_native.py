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
        consent._accept()
        assert not accepted
        consent.consent.set(True)
        consent.log = "selected-input.json"
        consent.exclusions.set("0-5")
        assert consent.log is None
        assert consent.log_button.cget("state") == "disabled"
        assert consent.add_button.cget("state") == "normal"
        assert "동의 확인됨" in consent.error_label.cget("text")
        for _ in range(7):
            root.update()
            time.sleep(.05)
        assert consent.winfo_ismapped()
        consent.lift()
        root.update()
        ImageGrab.grab(bbox=(0, 0, 1024, 768), all_screens=True).save(artifacts / f"ai_consent_{int(scale*100)}pct.png")
        consent._accept()
        assert accepted == [("민감정보를제거한작업녹화_긴파일이름.mp4", None, [[0, 5]])]
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
def test_recording_tab_gpt_entry_layout_and_launch_without_capture(ui_root, monkeypatch, scale):
    import customtkinter as ctk
    import tkinter as tk
    from types import SimpleNamespace
    from unittest.mock import Mock
    from PIL import ImageGrab
    from src.ui import ai_draft_editor, main_window, recorder_view

    config = SimpleNamespace(recording=SimpleNamespace(fps=30, include_cursor=True, save_input_log=True))
    session, capture, database = Mock(), Mock(), Mock()
    monkeypatch.setattr(main_window, "get_config", lambda: config)
    monkeypatch.setattr(recorder_view, "RecordingSession", Mock(return_value=session))
    monkeypatch.setattr(recorder_view, "get_screen_recorder", Mock(return_value=capture))
    monkeypatch.setattr(recorder_view, "get_db", Mock(return_value=database))
    monkeypatch.setattr(recorder_view, "UiCallbackDispatcher", Mock())
    monkeypatch.setattr(recorder_view, "LatestOnlyWorker", Mock())
    monkeypatch.setattr(recorder_view.RecorderView, "_setup_hotkeys", Mock())
    monkeypatch.setattr(recorder_view.RecorderView, "_refresh_recordings_list_async", Mock())
    opened = Mock()
    monkeypatch.setattr(ai_draft_editor, "open_ai_draft_editor", opened)
    ctk.set_widget_scaling(scale)
    ui_root._ai_callback_errors.clear()
    window = ctk.CTkToplevel(ui_root)
    window.overrideredirect(True)
    tk.Wm.geometry(window, "1024x768+0+0")
    view = None
    try:
        view = recorder_view.RecorderView(window)
        view.pack(fill="both", expand=True)
        for _ in range(8):
            ui_root.update()
            time.sleep(.04)
        button = view._ai_draft_button
        assert button.winfo_ismapped()
        assert window.winfo_rootx() <= button.winfo_rootx()
        assert button.winfo_rootx() + button.winfo_width() <= window.winfo_rootx() + window.winfo_width()
        assert window.winfo_rooty() <= button.winfo_rooty() < window.winfo_rooty() + 90
        assert button.winfo_rooty() < view._start_btn.winfo_rooty()
        button.invoke()
        opened.assert_called_once_with(view)
        session.start.assert_not_called()
        capture.start.assert_not_called()
        database.create_recording.assert_not_called()
        assert not ui_root._ai_callback_errors, ui_root._ai_callback_errors
        artifacts = Path("artifacts/ai_draft")
        artifacts.mkdir(parents=True, exist_ok=True)
        ImageGrab.grab(bbox=(0, 0, 1024, 768), all_screens=True).save(artifacts / f"recorder_gpt_entry_{int(scale*100)}pct.png")
    finally:
        if view is not None:
            view.cleanup()
        window.destroy()
        ui_root.update()
        ctk.set_widget_scaling(1.0)


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
        assert _pump_until(ui_root, lambda: len(view._plan_items) == 24 and bool(view._plans_scroll._visible_widgets), 10)
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
        for button in buttons(view):
            # Rows outside the viewport may be pre-rendered in the bounded buffer.
            if not button.winfo_ismapped():
                continue
            assert view.winfo_rootx() <= button.winfo_rootx()
            assert button.winfo_rootx() + button.winfo_width() <= view.winfo_rootx() + view.winfo_width(), button.cget("text")
            assert button._text_label.winfo_reqwidth() <= button.winfo_width() - 4, button.cget("text")
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
        assert _pump_until(ui_root, lambda: view._plan_items[0].name == data["name"], 10)
        row = view._plans_scroll._visible_widgets[0]
        changed_labels = [child.cget("text") for child in descendants(row) if isinstance(child, ctk.CTkLabel)]
        assert data["name"] in changed_labels
        edit = Mock()
        view._edit_plan = edit
        next(button for button in buttons(row) if button.cget("text") == "수정").invoke()
        assert edit.call_args.args[0].name == data["name"]

        view._plans_scroll.scroll_to_item(23)
        assert _pump_until(ui_root, lambda: 23 in view._plans_scroll._visible_widgets, 5)
        last = view._plans_scroll._visible_widgets[23]
        assert viewport.winfo_rooty() <= last.winfo_rooty()
        assert last.winfo_rooty() + last.winfo_height() <= viewport.winfo_rooty() + viewport.winfo_height() + 2
        assert len(view._plans_scroll._visible_widgets) < 24
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
                    artifact_dir / f"analyzer_playlist_end_{int(scale * 100)}pct.png")
            finally:
                view.cleanup()
        window.destroy()
        ui_root.update()
        ctk.set_widget_scaling(1.0)
