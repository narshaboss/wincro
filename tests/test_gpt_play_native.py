"""Opt-in isolated Windows UI/pipe verification. Never run on operator PC."""

import os
from pathlib import Path
from types import SimpleNamespace
import tkinter as tk

import pytest

if os.name != "nt" or os.environ.get("WINCRO_GPT_PLAY_UI_QA") != "1":
    pytest.skip("Isolated Windows runner only", allow_module_level=True)


@pytest.mark.parametrize("scale,width,height", [(1, 640, 680), (1.25, 800, 700), (1.5, 960, 700)])
def test_learning_tabs_read_durable_memory_without_input(tmp_path, ui_root, monkeypatch, scale, width, height):
    import time
    from src.ui.gpt_play_panel import GPTPlayPanel
    from src.ui.recording_workspace import RecordingWorkspace
    from src.ai_play.experience import ExperienceStore, memory_root
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    store = ExperienceStore(memory_root())
    task = store.create("NPC 입장 후 클리어하고 보상 확인", {"executable": "game.exe", "title": "테스트 게임"})
    store.put(task, "attempt", "attempt_1", {"attempt_id": "attempt_1", "status": "defeat", "stage": "전투",
        "strategy": "오른쪽 이동 후 스킬 사용", "observation": "결과창에서 실패 확인",
        "hypothesis": "사거리 부족일 가능성", "next_experiment": "이동 시간만 200ms 늘려 비교"})
    import hashlib
    import io
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGB", (100, 80), "#258164").save(buffer, "PNG")
    raw = buffer.getvalue()
    store.save_frame(task, {"frame_id": "frame_ci_evidence", "sha256": hashlib.sha256(raw).hexdigest()}, raw)
    for index in range(12):
        store.event(task, "fact", {"frame_id": "frame_ci_evidence", "observation": f"독립 관찰 {index}"})
    ctk.set_widget_scaling(scale)
    from src.ui.theme import COLORS
    window = ctk.CTkToplevel(ui_root, fg_color=COLORS["bg_dark"])
    window.geometry(f"{width}x{height - 40}+0+0")
    workspace = SimpleNamespace(connection=SimpleNamespace(connected=False), _notify_publication=lambda _: None,
        tabs=SimpleNamespace(set=lambda _: None))
    workspace._button = lambda parent, text, command, **kwargs: RecordingWorkspace._button(workspace, parent, text, command, **kwargs)
    panel = GPTPlayPanel(window, workspace, tmp_path, tmp_path / "plans", tmp_path / "templates")
    panel.pack(fill="both", expand=True)
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not panel.memory_rows:
            window.update()
            time.sleep(.02)
        assert panel.memory_rows
        label = next(iter(panel.memory_rows))
        panel.memory_selection.set(label)
        panel._select_memory(label)
        assert "NPC 입장" in panel.goal.get("1.0", "end")
        panel.tabs.set("경험 기록")
        panel._request_memory()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            window.update()
            if "사거리 부족" in panel.memory_text.get("1.0", "end"):
                break
            time.sleep(.02)
        assert "원인 후보(미확정)" in panel.memory_text.get("1.0", "end")
        assert panel.jobs.session is None and not panel.jobs.active
        assert panel.start_button.cget("state") == "disabled"
        for control in (panel.memory_selection, panel.memory_refresh, panel.memory_text,
                        panel.header_stop, panel.header_pause, panel.memory_next_button, panel.evidence_button):
            assert control.winfo_width() > 40
            assert control.winfo_rootx() + control.winfo_width() <= window.winfo_rootx() + window.winfo_width()
            assert control.winfo_rooty() + control.winfo_height() <= window.winfo_rooty() + window.winfo_height()
        assert panel.header_stop.winfo_ismapped() and panel.header_pause.winfo_ismapped()
        artifacts = Path("artifacts/gpt_play")
        artifacts.mkdir(parents=True, exist_ok=True)
        ImageGrab.grab(bbox=(0, 0, width, height)).save(artifacts / f"learning_memory_{scale}.png")
        assert panel.memory_next_button.cget("state") == "normal"
        panel.memory_next_button.invoke()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and panel.memory_previous.cget("state") != "normal":
            window.update()
            time.sleep(.02)
        assert panel.memory_offset == 10
        assert "독립 관찰 0" in panel.memory_text.get("1.0", "end")
        panel.evidence_button.invoke()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not any(isinstance(w, ctk.CTkToplevel) for w in panel.winfo_children()):
            window.update()
            time.sleep(.02)
        previews = [w for w in panel.winfo_children() if isinstance(w, ctk.CTkToplevel)]
        assert len(previews) == 1 and "현재 게임 화면 아님" in previews[0].title()
        previews[0].destroy()
        panel.tabs.set("플랜 검수")
        window.update()
        for control in (panel.verification_text, panel.verification_permission):
            assert control.winfo_ismapped()
            assert control.winfo_rootx() + control.winfo_width() <= window.winfo_rootx() + window.winfo_width()
            assert control.winfo_rooty() + control.winfo_height() <= window.winfo_rooty() + window.winfo_height()
        assert panel.verification_permission.cget("state") == "disabled"
        assert panel.verification_permission._text_label.winfo_reqwidth() <= panel.verification_permission.winfo_width()
        ImageGrab.grab(bbox=(0, 0, width, height)).save(artifacts / f"verification_permission_{scale}.png")
    finally:
        panel.close()
        panel.destroy()
        window.destroy()
        ctk.set_widget_scaling(1)

import customtkinter as ctk
from PIL import ImageGrab


@pytest.fixture(scope="module")
def ui_root():
    root = ctk.CTk()
    root.withdraw()
    yield root
    root.destroy()


def test_textbox_state_reads_tk_state_on_ctk5_and_does_not_redraw_when_unchanged(ui_root, monkeypatch):
    from src.ui.recording_workspace import configure_changed
    from unittest.mock import Mock
    original_cget = ctk.CTkTextbox.cget
    def legacy_cget(self, name):
        if name == "state":
            raise ValueError("'state' is not a supported argument.")
        return original_cget(self, name)
    monkeypatch.setattr(ctk.CTkTextbox, "cget", legacy_cget)
    box = ctk.CTkTextbox(ui_root)
    configure = Mock(wraps=box.configure)
    monkeypatch.setattr(box, "configure", configure)
    try:
        configure_changed(box, state="disabled")
        assert box._textbox.cget("state") == "disabled"
        for _ in range(10):
            configure_changed(box, state="disabled")
        assert configure.call_count == 1
        configure_changed(box, state="normal")
        assert box._textbox.cget("state") == "normal"
        assert configure.call_count == 2
        with pytest.raises(ValueError):
            configure_changed(box, not_a_widget_option=True)
    finally:
        box.destroy()


@pytest.mark.parametrize("fail_play_init", [False, True])
def test_recorder_survives_play_init_failure_and_supports_ctk5(tmp_path, ui_root, monkeypatch, fail_play_init):
    import time
    from unittest.mock import Mock
    from src.ui import main_window, recorder_view, recording_workspace
    from src.ui.gpt_play_panel import GPTPlayPanel

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(recording_workspace, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(recorder_view, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(main_window, "get_config", lambda: SimpleNamespace(recording=SimpleNamespace(fps=30)))
    database, session, capture = Mock(), Mock(), Mock()
    database.get_all_recordings.return_value = []
    monkeypatch.setattr(recorder_view, "get_db", lambda: database)
    monkeypatch.setattr(recorder_view, "RecordingSession", lambda: session)
    monkeypatch.setattr(recorder_view, "get_screen_recorder", lambda: capture)
    monkeypatch.setattr(recorder_view.RecorderView, "_setup_hotkeys", Mock())
    original_cget = ctk.CTkTextbox.cget
    def legacy_cget(self, name):
        if name == "state":
            raise ValueError("'state' is not a supported argument.")
        return original_cget(self, name)
    monkeypatch.setattr(ctk.CTkTextbox, "cget", legacy_cget)
    original_refresh = GPTPlayPanel._refresh_controls
    failed_panels = []
    def fail(self):
        failed_panels.append(self)
        raise RuntimeError("Injected optional GPT panel initialization failure")
    if fail_play_init:
        monkeypatch.setattr(GPTPlayPanel, "_refresh_controls", fail)
    window = ctk.CTkToplevel(ui_root)
    window.geometry("1024x740+0+0")
    view = None
    try:
        view = recorder_view.RecorderView(window)
        view.pack(fill="both", expand=True)
        workspace = view._workspace
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            ui_root.update()
            if workspace.analyze.winfo_ismapped() and not workspace.busy and view._catalog_loaded:
                break
            time.sleep(.02)
        for widget in (workspace.badge, view._start_btn, view._stop_btn, workspace.play_button,
                       workspace.analyze, view._recordings_empty_label):
            assert widget.winfo_ismapped(), type(widget).__name__
        assert workspace.badge.cget("text") == "GPT 연결 OFF"
        assert workspace.play_button.cget("state") == "disabled"
        assert not workspace._play_busy
        if fail_play_init:
            assert workspace.play_panel is None
            assert failed_panels and failed_panels[0].closed
            assert not failed_panels[0].winfo_exists()
            assert any(isinstance(child, ctk.CTkLabel) and "녹화·분석 기능" in child.cget("text")
                       for child in workspace.play_page.winfo_children())
            # Existing recording/analysis controls remain responsive after failure.
            workspace._analyze()
            assert "선택" in workspace.status.cget("text")
            monkeypatch.setattr(GPTPlayPanel, "_refresh_controls", original_refresh)
            workspace._build_play_panel()
        assert workspace.play_panel is not None
        assert workspace.play_panel.start_button.cget("state") == "disabled"
        workspace.tabs.set("GPT 플레이")
        ui_root.update()
        assert workspace.play_panel.goal.winfo_ismapped()
        workspace.tabs.set("녹화·분석")
        ui_root.update()
        artifacts = Path("artifacts/gpt_play")
        artifacts.mkdir(parents=True, exist_ok=True)
        ImageGrab.grab(bbox=(0, 0, 1024, 740)).save(artifacts / f"recorder_ctk5_recovery_{fail_play_init}.png")
        session.start.assert_not_called()
        capture.start.assert_not_called()
        assert not (tmp_path / "data/plans").exists()
    finally:
        if view:
            view.cleanup()
            view.destroy()
        window.destroy()


@pytest.mark.parametrize("scale,width,height", [(1, 640, 680), (1.25, 800, 720), (1.5, 960, 780)])
def test_panel_on_off_question_controls_and_layout(tmp_path, ui_root, monkeypatch, scale, width, height):
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
        assert panel.test_environment.get() is True
        assert panel.confirm.cget("state") == "disabled"
        assert "keys" not in panel.__dict__
        panel._refresh_controls()
        assert panel.start_button.cget("state") == "disabled"
        connection.connected = True
        panel._refresh_controls()
        panel.status.configure(text="GPT 연결 ON · 시작 대기", text_color="#22c55e")
        root.update_idletasks()
        assert panel.start_button.cget("state") == "normal"
        for widget in (panel.target, panel.goal, panel.keys_summary, panel.start_button, panel.stop_button, panel.detail, panel.copy_log,
                       panel.interrupt_button, panel.command, panel.command_button):
            assert widget.winfo_width() > 30
            assert widget.winfo_rootx() >= root.winfo_rootx()
            assert widget.winfo_rootx() + widget.winfo_width() <= root.winfo_rootx() + root.winfo_width()
        artifacts = Path("artifacts/gpt_play")
        artifacts.mkdir(parents=True, exist_ok=True)
        ImageGrab.grab(bbox=(0, 0, width, height)).save(artifacts / f"panel_{scale}.png")
        from unittest.mock import Mock
        panel.jobs.trace.emit("focus_requested", "전면 전환 확인 중 · 대상 hwnd=7 현재 전면 hwnd=8")
        panel.jobs.trace.emit("action_sent", "키 UP · 장치 전송 완료 · 게임 반응은 아직 미확인")
        panel._render_log()
        previous_text = panel.detail.get("1.0", "end")
        configure = Mock(wraps=panel.detail.configure)
        monkeypatch.setattr(panel.detail, "configure", configure)
        for _ in range(5):
            panel._render_log()
        assert configure.call_count == 0
        assert panel.detail.get("1.0", "end") == previous_text
        assert previous_text.count("키 UP") == 1
        panel.copy_log.invoke()
        assert "hwnd=7" in root.clipboard_get()
        panel.body._parent_canvas.yview_moveto(1.0)
        root.update()
        ImageGrab.grab(bbox=(0, 0, width, height)).save(artifacts / f"play_logs_{scale}.png")
        session = PlaySession(tmp_path / "session", "Clear", ["enter"], Backend(), lambda: connection.connected)
        session.start()
        reference = session.observe()["frame_id"]
        session.ask(reference, "무료 부활은 어떤 버튼인가요?", "사망 화면입니다. 자동 퇴장 여부를 확인하지 못했습니다.")
        panel.jobs.session = session
        for worker in session.release_workers:
            worker.join(2)
        panel._poll()
        root.update()
        assert panel.question_card.winfo_manager() == "grid"
        assert panel.answer_button.cget("state") == "normal"
        assert panel.answer.winfo_exists()
        assert selected_tabs[-1] == "GPT 플레이"
        assert "사용자 답변 대기" in panel.detail.get("1.0", "end")
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
        assert call(descriptor, "play_observe", {})["code"] == "STALE_EXECUTION"
    finally:
        session.stop()
        host.close()
        host.thread.join(3)


def test_panel_intervention_start_command_resume_and_durable_log(tmp_path, ui_root, monkeypatch):
    import time
    import threading
    from src.ai_play import jobs
    from src.agent_bridge.storage import read_json
    from src.ui.gpt_play_panel import GPTPlayPanel
    from src.ui.recording_workspace import RecordingWorkspace
    from tests.test_gpt_play import Backend, FakeHost
    monkeypatch.setattr(jobs, "PlayHost", FakeHost)
    monkeypatch.setattr(jobs, "protect_directory", lambda p: Path(p).mkdir(parents=True, exist_ok=True))
    backend, first_turn = Backend(), threading.Event()
    prompts = []
    class Actor:
        def __init__(self, *_):
            pass
        def cancel(self):
            pass
        def run(self, descriptor, prompt, cancel, progress):
            scope = FakeHost.hosts[descriptor].scope
            prompts.append(prompt)
            observation = scope.dispatch("play_observe", {})["frame_id"]
            if len(prompts) == 1:
                first_turn.set()
                assert cancel.wait(5)
            else:
                from tests.test_gpt_play import review_live_memory
                review_live_memory(scope, observation)
                scope.dispatch("play_act", {"observation_id": observation, "request_id": "after-command",
                    "action": {"kind": "key", "key": "up"}, "purpose": "user instruction"})
            return "turn done"
    def pump(predicate, seconds=5):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            ui_root.update()
            if predicate():
                return True
            time.sleep(.02)
        return False
    workspace = SimpleNamespace(connection=SimpleNamespace(connected=True), busy=False, runtime={},
        owner=SimpleNamespace(_is_recording=False, _starting=False, _stopping=False),
        jobs=SimpleNamespace(busy=False), tabs=SimpleNamespace(set=lambda _: None), _notify_publication=lambda _: None)
    workspace._button = lambda parent, text, command, **kw: RecordingWorkspace._button(workspace, parent, text, command, **kw)
    window = ctk.CTkToplevel(ui_root)
    window.geometry("850x740+0+0")
    panel = GPTPlayPanel(window, workspace, tmp_path, tmp_path / "plans", tmp_path / "templates")
    panel.pack(fill="both", expand=True)
    panel.jobs.runner_factory = Actor
    original_start = panel.jobs.start
    monkeypatch.setattr(panel.jobs, "start", lambda *args, **kw: original_start(*args, **kw, backend=backend))
    panel.targets = {"owned fake game": None}
    target = SimpleNamespace(executable_name="ci-game.exe", title="Owned CI fake", handle=1)
    panel.targets["owned fake game"] = target
    panel.target.set("owned fake game")
    try:
        assert panel.test_environment.get()
        assert pump(lambda: panel.start_button.cget("state") == "normal")
        panel.start_button.invoke()
        assert pump(first_turn.is_set)
        assert pump(lambda: panel.interrupt_button.cget("state") == "normal")
        panel.interrupt_button.invoke()
        assert pump(lambda: panel.command_button.cget("state") == "normal" and window.focus_get() == panel.command._textbox)
        assert panel.jobs.session.latest is None and not backend.sent
        assert window.focus_get() == panel.command._textbox
        panel.command.insert("1.0", "NPC부터 다시 확인해")
        panel.command_button.invoke()
        assert pump(lambda: len(prompts) == 2 and not panel.jobs.busy)
        assert backend.sent == [{"kind": "key", "key": "up"}]
        saved = read_json(panel.jobs.session.folder / "session.json")
        assert saved["commands"][0]["command"] == "NPC부터 다시 확인해"
        assert "NPC부터 다시 확인해" in prompts[-1]
        assert (panel.jobs.session.folder / "events.jsonl").is_file()
        assert not (tmp_path / "plans").exists()
        assert panel.command.get("1.0", "end").strip() == ""
        panel.jobs.stop()
        assert pump(lambda: not panel.jobs.active)
    finally:
        panel.close()
        if panel.jobs.worker:
            panel.jobs.worker.join(3)
        if panel.jobs.session:
            panel.jobs._release_owner(panel.jobs.session)
        panel.destroy()
        window.destroy()


def test_real_hotkey_registration_message_routing_and_cleanup():
    if os.environ.get("GITHUB_ACTIONS") != "true":
        pytest.skip("Global hotkey registration only on isolated CI")
    import ctypes
    from ctypes import wintypes
    from src.ai_play.controls import PlayHotkeys
    keys = PlayHotkeys()
    try:
        assert not keys.failed
        assert keys.poll() == []
        kernel, user = ctypes.WinDLL("kernel32"), ctypes.WinDLL("user32")
        kernel.GetCurrentThreadId.restype = wintypes.DWORD
        user.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        assert user.PostThreadMessageW(kernel.GetCurrentThreadId(), 0x0312, 0x5C10, 0)
        assert keys.poll() == ["intervene"]
    finally:
        keys.close()
    again = PlayHotkeys()
    try:
        assert not again.failed
    finally:
        again.close()


def test_window_io_captures_and_sends_only_to_owned_ci_game(tmp_path, monkeypatch):
    if os.environ.get("GITHUB_ACTIONS") != "true":
        pytest.skip("Real input is only permitted on an isolated CI desktop")
    import json
    import subprocess
    import sys
    import time
    import uuid
    from src.ai_play.windows import WindowIO
    from src.utils import input_controller as ic
    from src.utils.window_control import _WindowsBackend
    from src.agent_bridge.contracts import BridgeError
    title = "WinCroOwnedPlayQA_" + uuid.uuid4().hex
    events = tmp_path / "events.txt"
    script = '''
import sys
import tkinter as tk
root = tk.Tk()
root.title(sys.argv[1])
root.geometry("320x200+80+80")
root.configure(bg="#195b37")
def record(value):
    with open(sys.argv[2], "a", encoding="utf-8") as output:
        output.write(value + "\\n")
root.bind("<KeyPress>", lambda e: record("key:" + e.keysym))
root.bind("<Button-1>", lambda e: record("left"))
root.after(200, root.focus_force)
root.after(45000, root.destroy)
root.mainloop()
'''
    child = subprocess.Popen([sys.executable, "-c", script, title, str(events)],
        creationflags=subprocess.CREATE_NO_WINDOW)
    backend = None
    try:
        api = _WindowsBackend()
        target = None
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and target is None:
            target = next((w for w in api.list_windows() if w.title == title and w.process_id == child.pid), None)
            time.sleep(.05)
        assert target is not None
        controller = ic.InputController()
        monkeypatch.setattr(ic, "get_config", lambda: SimpleNamespace(arduino=SimpleNamespace(enabled=False, require_for_playback=False)))
        monkeypatch.setattr(ic, "get_input_controller", lambda: controller)
        monkeypatch.setattr(ic, "is_arduino_enabled", lambda: False)
        monkeypatch.setattr(ic, "is_arduino_strict_enabled", lambda: False)
        monkeypatch.setattr(ic, "is_automation_input_blocked", lambda: False)
        monkeypatch.setattr(controller, "_use_arduino", lambda: False)
        # Production GPT play is Arduino-only. Inject an explicit CI-only adapter
        # for this OWNED window test; never re-enable production software fallback.
        monkeypatch.setattr(controller, "play_lab_prepare", lambda: True)
        monkeypatch.setattr(controller, "play_lab_capabilities", lambda: {"arduino_only": True, "timed_input_gl1": False})
        def move(x, y, check, duration=.2):
            check()
            ic.pyautogui.moveTo(x, y, duration=duration)
            return True
        def tap(key):
            ic.pyautogui.press(key)
            return True
        def click():
            ic.pyautogui.click(button="left")
            return True
        monkeypatch.setattr(controller, "play_lab_move_to", move)
        monkeypatch.setattr(controller, "play_lab_tap", tap)
        monkeypatch.setattr(controller, "play_lab_click", click)
        monkeypatch.setattr(controller, "play_lab_release", lambda _: True)
        backend = WindowIO(target)
        backend.prepare()
        image, region = backend.capture()
        assert image.size == (region[2] - region[0], region[3] - region[1])
        assert backend.send({"kind": "click", "x": 100, "y": 100}, region, backend.validate)
        assert backend.send({"kind": "key", "key": "enter"}, region, backend.validate)
        deadline = time.monotonic() + 4
        recorded = []
        while time.monotonic() < deadline:
            recorded = events.read_text(encoding="utf-8").splitlines() if events.exists() else []
            if "left" in recorded and "key:Return" in recorded:
                break
            time.sleep(.05)
        assert "left" in recorded and "key:Return" in recorded
        assert backend.release()
        child.terminate()
        child.wait(5)
        with pytest.raises(BridgeError) as error:
            backend.validate()
        assert error.value.code == "TARGET_CLOSED"
        artifacts = Path("artifacts/gpt_play")
        artifacts.mkdir(parents=True, exist_ok=True)
        image.save(artifacts / "owned_game.png")
        (artifacts / "owned-game-input.json").write_text(json.dumps({"isolated_CI": True,
            "owned_window_only": True, "software_input": recorded, "closed_target_rejected": True,
            "Arduino_verified": False}), encoding="utf-8")
    finally:
        if backend:
            backend.release()
        if child.poll() is None:
            child.terminate()
            child.wait(5)


@pytest.mark.parametrize("enhanced", [False, True])
def test_real_play_mcp_schema_images_actions_questions_and_denied_recording_tools(tmp_path, enhanced):
    import asyncio
    import json
    import sys
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from src.ai_play.jobs import PlayHost, PlayScope
    from src.ai_play.session import PlaySession, TOOLS
    from tests.test_gpt_play import Backend
    from src.ai_play.experience import ExperienceStore
    store = ExperienceStore(tmp_path / "memory") if enhanced else None
    task = store.create("Clear", {"executable": "test.exe", "title": "Test"}) if store else None
    session = PlaySession(tmp_path / "session", "Clear", ["enter"], Backend(), lambda: True,
                          memory=store, task_id=task, live_policy=enhanced)
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
                response = await client.call_tool("play_situation" if enhanced else "play_observe", {})
                assert any(c.type == "image" for c in response.content)
                metadata = json.loads([c.text for c in response.content if c.type == "text"][-1])
                if enhanced:
                    action = await client.call_tool("play_decide", {"observation_id": metadata["observation"]["frame_id"],
                        "request_id": "one", "action": {"kind": "key", "key": "enter"}, "purpose": "join",
                        "retrieval_id": metadata["memory"]["retrieval_id"], "references": [], "decision": "No prior knowledge; bounded fixture probe."})
                    assert any(c.type == "image" for c in action.content)
                    assert json.loads(action.content[0].text)["receipt"]["state"] == "sent_not_game_success"
                else:
                    action = await client.call_tool("play_act", {"observation_id": metadata["frame_id"],
                        "request_id": "one", "action": {"kind": "key", "key": "enter"}, "purpose": "join"})
                    assert json.loads(action.content[0].text)["ok"]
                response = await client.call_tool("play_observe", {})
                metadata = json.loads([c.text for c in response.content if c.type == "text"][-1])
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
