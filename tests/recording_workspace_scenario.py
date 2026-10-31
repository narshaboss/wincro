"""Isolated Windows UI-to-MCP simulation; never operates an operator desktop."""

import asyncio
import json
import os
from pathlib import Path
import re
import sys
import threading
import time

import pytest

if os.environ.get("WINCRO_AI_EDITOR_UI_QA") != "1" or os.name != "nt":
    pytest.skip("Opt-in isolated Windows UI only", allow_module_level=True)



class MCPPipelineActor:
    """A deterministic client, not a model; validates the job's real MCP boundary."""
    release = threading.Event()
    def __init__(self, settings, folder):
        pass
    def cancel(self):
        self.release.set()
    def run(self, descriptor, prompt, cancelled, progress):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        draft_id = re.search(r"draft_id=(draft_[a-z0-9]+)", prompt)[1]
        recording_id = re.search(r"recording_id=(recording_[a-z0-9]+)", prompt)[1]
        async def run():
            server = Path(__file__).resolve().parents[1] / "tools" / "wincro_mcp_server.py"
            params = StdioServerParameters(command=sys.executable, args=[str(server), "--connection-file", str(descriptor)])
            async with stdio_client(params) as streams:
                async with ClientSession(*streams) as client:
                    await client.initialize()
                    async def invoke(name, arguments):
                        result = await client.call_tool(name, arguments)
                        response = json.loads(result.content[0].text)
                        assert response["ok"], response
                        return response["result"]
                    await invoke("connection_status", {})
                    metadata = await invoke("read_recording", {"recording_id": recording_id})
                    assert metadata["duration_seconds"] > 0
                    deadline = time.monotonic() + 8
                    while not self.release.is_set() and time.monotonic() < deadline:
                        await asyncio.sleep(.04)
                    state = await invoke("read_draft", {"draft_id": draft_id})
                    await invoke("stage_edits", dict(draft_id=draft_id, expected_revision=state["revision"], request_id="native-add",
                        operations=[{"op": "add_rule", "action_type": "hotkey", "fields": {"description": "Synthetic confirmation", "action_keys": ["enter"]}}]))
                    state = await invoke("read_draft", {"draft_id": draft_id})
                    key = state["plan"]["initial_rules"][0]["rule_id"]
                    async def frame():
                        result = await client.call_tool("get_frames", {"recording_id": recording_id, "timestamps": [0]})
                        assert any(content.type == "image" for content in result.content)
                        return json.loads(result.content[0].text)["frame_trace_id"]
                    trace = await frame()
                    await invoke("record_analysis", dict(draft_id=draft_id, expected_revision=state["revision"], goal="Synthetic test",
                        steps=[dict(rule_id=key, recording_id=recording_id, seconds=0, frame_trace_id=trace,
                            observation="Synthetic rectangle", purpose="Confirm test", expected_result="Synthetic next step")]))
                    for direction in ("video_to_plan", "plan_to_video"):
                        state = await invoke("read_draft", {"draft_id": draft_id})
                        trace = await frame()
                        await invoke("record_crosscheck", dict(draft_id=draft_id, expected_revision=state["revision"], direction=direction,
                            checks=[dict(rule_id=key, frame_trace_id=trace, result="match", note="Deterministic simulation only")]))
            return {"exit_code": 0, "message": "Simulation only; real GPT semantics were not tested."}
        return asyncio.run(run())


class MCPIdleActor:
    """Real idle MCP transport, without a model or any media access."""
    def __init__(self, *_):
        pass
    def cancel(self):
        pass
    def connect(self, descriptor, cancelled, ready):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        async def run():
            server = Path(__file__).resolve().parents[1] / "tools" / "wincro_mcp_server.py"
            params = StdioServerParameters(command=sys.executable, args=[str(server), "--connection-file", str(descriptor)])
            async with stdio_client(params) as streams:
                async with ClientSession(*streams) as client:
                    await client.initialize()
                    response = await client.call_tool("connection_status", {})
                    value = json.loads(response.content[0].text)
                    assert value["ok"]
                    ready(value["result"])
                    while not cancelled.is_set():
                        await asyncio.sleep(.03)
        return asyncio.run(run())


def exercise_recording_pipeline(tmp_path, monkeypatch, ui_root, _pump_until):
    import customtkinter as ctk
    import cv2
    import numpy as np
    from types import SimpleNamespace
    from unittest.mock import Mock
    from PIL import ImageGrab
    from src.ui import recorder_view, recording_workspace, main_window
    from src.ui.ai_draft_editor import MediaConsentDialog
    from src.database.models import Recording
    from src.agent_bridge.recording_jobs import RecordingJobs
    from src.agent_bridge.service import DraftService

    data = tmp_path / "data"
    (data / "recordings").mkdir(parents=True)
    (data / "plans").mkdir()
    original = data / "plans" / "plan_original.json"
    original.write_text('{"plan_id":"plan_original","name":"Original","initial_rules":[],"monitoring_rules":[]}', encoding="utf-8")
    before = original.read_bytes()
    video = data / "recordings" / "workflow.mp4"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"), 5, (120, 80))
    assert writer.isOpened()
    for index in range(10):
        image = np.full((80, 120, 3), 50, dtype=np.uint8)
        cv2.rectangle(image, (10, 10), (70, 40), (80, 160, 220), -1)
        writer.write(image)
    writer.release()
    database, session, capture = Mock(), Mock(), Mock()
    database.get_all_recordings.return_value = [Recording(id=1, name="Synthetic workflow", video_path=str(video))]
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(recording_workspace, "DATA_DIR", data)
    monkeypatch.setattr(recorder_view, "DATA_DIR", data)
    monkeypatch.setattr(main_window, "get_config", lambda: SimpleNamespace(recording=SimpleNamespace(fps=30)))
    monkeypatch.setattr(recorder_view, "get_db", lambda: database)
    monkeypatch.setattr(recorder_view, "RecordingSession", lambda: session)
    monkeypatch.setattr(recorder_view, "get_screen_recorder", lambda: capture)
    monkeypatch.setattr(recorder_view.RecorderView, "_setup_hotkeys", Mock())
    ctk.set_widget_scaling(1)
    ui_root._ai_callback_errors.clear()
    window = ctk.CTkToplevel(ui_root)
    window.geometry("1024x740+0+0")
    view = recorder_view.RecorderView(window)
    view.pack(fill="both", expand=True)
    workspace = view._workspace
    workspace.jobs.runner_factory = MCPPipelineActor
    workspace.connection.runner_factory = MCPIdleActor
    MCPPipelineActor.release.clear()
    try:
        assert _pump_until(ui_root, lambda: len(view._recording_items) == 1 and not workspace.busy, 8)
        workspace._settings()
        settings = next(child for child in workspace.winfo_children() if isinstance(child, ctk.CTkToplevel))
        assert _pump_until(ui_root, lambda: not workspace.busy, 5)
        save_settings = next(child for child in settings.winfo_children()
            if isinstance(child, ctk.CTkButton) and child.cget("text") == "설정 저장")
        save_settings.invoke()
        assert _pump_until(ui_root, lambda: not settings.winfo_exists() and not workspace.busy, 5)
        from src.agent_bridge.codex_runner import load_runtime
        assert load_runtime(workspace.settings_path) == workspace.runtime
        assert workspace.badge.cget("text_color") == recording_workspace.COLORS["error"]
        workspace.badge.invoke()
        assert _pump_until(ui_root, lambda: workspace.connection.connected and workspace.badge.cget("text") == "GPT 연결 ON", 12)
        assert workspace.badge.cget("text_color") == recording_workspace.COLORS["success"]
        assert workspace.jobs.snapshot() is None
        artifacts = Path("artifacts/ai_draft")
        artifacts.mkdir(parents=True, exist_ok=True)
        ImageGrab.grab(bbox=(0, 0, 1024, 740), all_screens=True).save(artifacts / "recording_manual_connected.png")
        assert _pump_until(ui_root, lambda: 0 in view._recordings_scroll._visible_widgets, 5)
        row = view._recordings_scroll._visible_widgets[0]
        workspace.choose(view._recording_items[0])
        assert view._recordings_scroll._visible_widgets[0] is row
        workspace.choose(view._recording_items[0])
        assert view._recordings_scroll._visible_widgets[0] is row
        badge_update = Mock(wraps=workspace.badge.configure)
        monkeypatch.setattr(workspace.badge, "configure", badge_update)
        deadline = time.monotonic() + .7
        _pump_until(ui_root, lambda: time.monotonic() > deadline, 2)
        badge_update.assert_not_called()
        view._refresh_recordings_list_async()
        assert _pump_until(ui_root, lambda: view._catalog_loaded and not workspace.busy, 5)
        deadline = time.monotonic() + .2
        _pump_until(ui_root, lambda: time.monotonic() > deadline, 2)
        assert view._recordings_scroll._visible_widgets[0] is row
        workspace.analyze.invoke()
        consent = next(child for child in workspace.winfo_children() if isinstance(child, MediaConsentDialog))
        consent.consent.set(True)
        view._is_recording = True
        consent.add_button.invoke()
        assert consent.winfo_exists() and not workspace.jobs.busy and workspace.jobs.snapshot() is None
        view._is_recording = False
        consent.add_button.invoke()
        assert _pump_until(ui_root, lambda: workspace.badge.cget("text") == "GPT 연결 ON", 12), workspace.jobs.snapshot()
        MCPPipelineActor.release.set()
        assert _pump_until(ui_root, lambda: workspace.result.current is not None and not workspace.result.busy and not workspace.jobs.busy, 15)
        assert workspace.jobs.snapshot()["status"] == "review"
        assert workspace.connection.connected  # Analysis completion must not turn OFF.
        workspace.tabs.set("분석 결과 편집")
        pane = workspace.result
        assert _pump_until(ui_root, lambda: pane.context is not None, 5)
        move = Mock(wraps=pane.tree.move)
        monkeypatch.setattr(pane.tree, "move", move)
        pane.variables["wait_after"].set("1.25")
        pane.save.invoke()
        assert _pump_until(ui_root, lambda: not pane.busy and not pane.dirty and pane.current["plan"]["initial_rules"][0]["wait_after"] == 1.25, 8)
        move.assert_not_called()
        fresh = DraftService(pane.service.plans_dir, pane.service.templates_dir, pane.service.workspace, allow_publication=False)
        restored = fresh.read_draft(pane.current["draft_id"])
        assert restored == pane.current
        assert restored["analysis"] is None
        assert workspace.jobs.list_jobs()[0]["status"] == "needs_review"
        assert _pump_until(ui_root, lambda: "추가 확인 필요" in pane.jobs.get(), 5)
        assert _pump_until(ui_root, lambda: not workspace.busy, 5)
        from tkinter import messagebox, filedialog
        monkeypatch.setattr(messagebox, "askyesno", lambda *args, **kwargs: True)
        video_before = video.read_bytes()
        view._remove_media_item(view._recording_items[0])
        assert _pump_until(ui_root, lambda: not view._recording_items and not workspace.busy, 5)
        from src.agent_bridge.recording_catalog import RecordingVisibility
        assert str(video.resolve()).casefold() in RecordingVisibility(workspace.visibility.path).hidden()
        assert not view._load_template_media_items()
        assert video.read_bytes() == video_before
        database.delete_recording.assert_not_called()
        monkeypatch.setattr(filedialog, "askopenfilename", lambda **kwargs: str(video))
        view._add_recording_file()
        assert _pump_until(ui_root, lambda: len(view._recording_items) == 1 and not workspace.busy, 5)
        assert not RecordingVisibility(workspace.visibility.path).hidden()
        workspace.choose(view._recording_items[0])
        MCPPipelineActor.release.clear()
        workspace.analyze.invoke()
        consent = next(child for child in workspace.winfo_children() if isinstance(child, MediaConsentDialog))
        consent.consent.set(True)
        consent.add_button.invoke()
        assert _pump_until(ui_root, lambda: workspace.jobs.busy and workspace.jobs.runner is not None, 8)
        workspace.badge.invoke()
        assert _pump_until(ui_root, lambda: not workspace.connection.busy and workspace.badge.cget("text") == "GPT 연결 OFF", 8)
        assert _pump_until(ui_root, lambda: not workspace.jobs.busy, 8)
        assert workspace.jobs.snapshot()["status"] == "cancelled"
        assert workspace.badge.cget("text_color") == recording_workspace.COLORS["error"]
        assert workspace.analyze.cget("state") == "disabled"
        assert not workspace.jobs.busy
        assert original.read_bytes() == before
        assert list((data / "plans").iterdir()) == [original]
        session.start.assert_not_called()
        capture.start.assert_not_called()
        database.create_recording.assert_not_called()
        assert not ui_root._ai_callback_errors, ui_root._ai_callback_errors
        deadline = time.monotonic() + .3
        _pump_until(ui_root, lambda: time.monotonic() > deadline, 2)
        artifacts = Path("artifacts/ai_draft")
        artifacts.mkdir(parents=True, exist_ok=True)
        ImageGrab.grab(bbox=(0, 0, 1024, 740), all_screens=True).save(artifacts / "recording_pipeline_result.png")
    finally:
        MCPPipelineActor.release.set()
        view.cleanup()
        window.destroy()
        ui_root.update()
