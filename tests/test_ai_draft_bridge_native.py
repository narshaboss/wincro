"""Opt-in isolated Windows checks. Never run this on an operator's desktop."""

import json
import os
from pathlib import Path
import time

import pytest

if os.environ.get("WINCRO_AI_EDITOR_UI_QA") != "1" or os.name != "nt":
    pytest.skip("Opt-in isolated Windows runner only", allow_module_level=True)

from src.agent_bridge.contracts import BridgeError
from src.agent_bridge.service import DraftService
from src.agent_bridge.transport import LocalHost, call


def test_real_local_pipe_auth_scope_and_shutdown(tmp_path):
    service = DraftService(tmp_path / "plans", tmp_path / "templates", tmp_path / "drafts")
    host = LocalHost(service, tmp_path / "connections")
    try:
        descriptor = host.start()
        assert call(descriptor, "connection_status", {})["result"]["live_execution"] is False
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
        assert len(service.list_drafts()) == 1
    finally:
        host.close()
        host.thread.join(5)
    assert not Path(descriptor).exists()
    assert not host.thread.is_alive()


@pytest.mark.parametrize("scale", [1.0, 1.25, 1.5])
def test_draft_ui_layout_manual_conflict_and_close(tmp_path, scale):
    import customtkinter as ctk
    from src.ui.ai_draft_editor import AIDraftEditor
    from PIL import ImageGrab
    ctk.set_widget_scaling(scale)
    root = ctk.CTk()
    root.withdraw()
    plans, templates = tmp_path / "plans", tmp_path / "templates"
    plans.mkdir()
    templates.mkdir()
    window = AIDraftEditor(root, plans, templates, workspace=tmp_path / "workspace")
    try:
        draft = window.service.open_draft("AI 초안 레이아웃 검수")
        window.service.stage_edits(draft["draft_id"], 1, "setup", [
            {"op": "add_rule", "action_type": "hotkey", "fields": {"description": "확인 키 입력", "action_keys": ["enter"]}}])
        window._render(window.service.read_draft(draft["draft_id"]))
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
        window._render(window.service.read_draft(draft["draft_id"]))
        assert window.entries["wait_after"].get() == "7.5"
        assert window.form_context[1] == old_revision
        assert window.current["revision"] > old_revision
        window._apply_fields()
        deadline = time.monotonic() + 4
        while window.busy and time.monotonic() < deadline:
            root.update()
            time.sleep(.02)
        assert not window.busy
        assert "REVISION_CONFLICT" in window.status.get()
        window._reload_form()
        assert window.entries["wait_after"].get() == "3.2"
        # Text/controls stay inside the window's client bounds at each scaling.
        for widget in [window.tree, window.plans, window.drafts, *window.entries.values()]:
            assert widget.winfo_width() > 40
            assert window.winfo_rootx() <= widget.winfo_rootx()
            assert widget.winfo_rootx() + widget.winfo_width() <= window.winfo_rootx() + window.winfo_width() + 2
        artifacts = Path("artifacts/window_control")
        artifacts.mkdir(parents=True, exist_ok=True)
        ImageGrab.grab(bbox=(window.winfo_rootx(), window.winfo_rooty(),
            window.winfo_rootx() + window.winfo_width(), window.winfo_rooty() + window.winfo_height())).save(artifacts / f"ai_draft_{int(scale*100)}pct.png")
    finally:
        window.form_dirty = False
        window._close()
        root.destroy()
        ctk.set_widget_scaling(1.0)
    assert window.service.closed
