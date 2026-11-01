"""Opt-in isolated Windows UI/pipe verification. Never run on operator PC."""

import os
from pathlib import Path
from types import SimpleNamespace
import tkinter as tk

import pytest

if os.name != "nt" or os.environ.get("WINCRO_GPT_PLAY_UI_QA") != "1":
    pytest.skip("Isolated Windows runner only", allow_module_level=True)

import customtkinter as ctk
from PIL import ImageGrab


@pytest.fixture(scope="module")
def ui_root():
    root = ctk.CTk()
    root.withdraw()
    yield root
    root.destroy()


@pytest.mark.parametrize("scale,width,height", [(1, 640, 680), (1.25, 800, 720), (1.5, 960, 780)])
def test_panel_on_off_question_controls_and_layout(tmp_path, ui_root, scale, width, height):
    from src.ui.gpt_play_panel import GPTPlayPanel
    from src.ui.recording_workspace import RecordingWorkspace
    from src.ai_play.session import PlaySession
    from tests.test_gpt_play import Backend
    ctk.set_widget_scaling(scale)
    root = ctk.CTkToplevel(ui_root)
    root.geometry(f"{width}x{height}+0+0")
    root.title("GPT Play isolated UI QA")
    connection = SimpleNamespace(connected=False)
    selected_tabs = []
    workspace = SimpleNamespace(connection=connection, _button=None, _notify_publication=lambda _: None,
        tabs=SimpleNamespace(set=selected_tabs.append))
    workspace._button = lambda parent, text, command, **kwargs: RecordingWorkspace._button(workspace, parent, text, command, **kwargs)
    panel = GPTPlayPanel(root, workspace, tmp_path, tmp_path / "plans", tmp_path / "templates")
    panel.pack(fill="both", expand=True)
    try:
        root.update()
        panel.test_environment.set(True)
        panel._refresh_controls()
        assert panel.start_button.cget("state") == "disabled"
        connection.connected = True
        panel._refresh_controls()
        panel.status.configure(text="GPT 연결 ON · 시작 대기", text_color="#22c55e")
        root.update_idletasks()
        assert panel.start_button.cget("state") == "normal"
        for widget in (panel.target, panel.goal, panel.keys, panel.start_button, panel.stop_button):
            assert widget.winfo_width() > 30
            assert widget.winfo_rootx() >= root.winfo_rootx()
            assert widget.winfo_rootx() + widget.winfo_width() <= root.winfo_rootx() + root.winfo_width()
        artifacts = Path("artifacts/gpt_play")
        artifacts.mkdir(parents=True, exist_ok=True)
        ImageGrab.grab(bbox=(0, 0, width, height)).save(artifacts / f"panel_{scale}.png")
        session = PlaySession(tmp_path / "session", "Clear", ["enter"], Backend(), lambda: connection.connected)
        session.start()
        reference = session.observe()["frame_id"]
        session.ask(reference, "무료 부활은 어떤 버튼인가요?", "사망 화면입니다. 자동 퇴장 여부를 확인하지 못했습니다.")
        panel.jobs.session = session
        panel._poll()
        root.update()
        assert panel.question_card.winfo_manager() == "grid"
        assert panel.answer_button.cget("state") == "normal"
        assert panel.answer.winfo_exists()
        assert selected_tabs[-1] == "GPT 플레이"
        root.update_idletasks()
        ImageGrab.grab(bbox=(0, 0, width, height)).save(artifacts / f"question_{scale}.png")
        connection.connected = False
        panel._refresh_controls()
        assert panel.answer_button.cget("state") == "disabled"
        assert session.state == "question"
    finally:
        panel.close()
        root.destroy()
        ctk.set_widget_scaling(1)


def test_live_pipe_uses_separate_authenticated_authority(tmp_path):
    from src.ai_play.jobs import PlayHost, PlayScope
    from src.ai_play.session import PlaySession
    from src.agent_bridge.transport import call
    from tests.test_gpt_play import Backend
    session = PlaySession(tmp_path / "session", "Clear", ["enter"], Backend(), lambda: True)
    session.start()
    host = PlayHost(PlayScope(session), tmp_path / "pipe")
    try:
        descriptor = host.start()
        result = call(descriptor, "connection_status", {})["result"]
        assert result["live_execution"] is True
        assert result["recording_authority"] is False
        assert call(descriptor, "publish_new_plan", {})["code"] == "TOOL_DENIED"
        assert call(descriptor, "read_plan", {"plan_id": "arbitrary"})["code"] == "TOOL_DENIED"
        assert call(descriptor, "play_observe", {})["ok"]
        session.pause()
        assert call(descriptor, "play_observe", {})["code"] == "WAITING_FOR_USER"
    finally:
        session.stop()
        host.close()
        host.thread.join(3)


def test_real_play_mcp_schema_images_actions_questions_and_denied_recording_tools(tmp_path):
    import asyncio
    import json
    import sys
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from src.ai_play.jobs import PlayHost, PlayScope
    from src.ai_play.session import PlaySession, TOOLS
    from tests.test_gpt_play import Backend
    session = PlaySession(tmp_path / "session", "Clear", ["enter"], Backend(), lambda: True)
    session.start()
    host = PlayHost(PlayScope(session), tmp_path / "pipe")
    descriptor = host.start()
    async def scenario():
        script = Path(__file__).resolve().parents[1] / "tools/wincro_play_mcp_server.py"
        params = StdioServerParameters(command=sys.executable, args=[str(script), "--connection-file", descriptor])
        async with stdio_client(params) as streams:
            async with ClientSession(*streams) as client:
                await client.initialize()
                assert {t.name for t in (await client.list_tools()).tools} == TOOLS
                status = await client.call_tool("connection_status", {})
                assert json.loads(status.content[0].text)["result"]["live_execution"]
                response = await client.call_tool("play_observe", {})
                assert any(c.type == "image" for c in response.content)
                metadata = json.loads(next(c.text for c in response.content if c.type == "text"))
                action = await client.call_tool("play_act", {"observation_id": metadata["frame_id"],
                    "request_id": "one", "action": {"kind": "key", "key": "enter"}, "purpose": "join"})
                assert json.loads(action.content[0].text)["ok"]
                response = await client.call_tool("play_observe", {})
                metadata = json.loads(next(c.text for c in response.content if c.type == "text"))
                question = await client.call_tool("play_question", {"observation_id": metadata["frame_id"],
                    "question": "Which NPC?", "context": "Two candidates visible"})
                assert json.loads(question.content[0].text)["result"]["waiting_for_user"]
                assert session.state == "question"
    try:
        asyncio.run(scenario())
        assert session.backend.sent == [{"kind": "key", "key": "enter"}]
    finally:
        session.stop()
        host.close()
        host.thread.join(3)
