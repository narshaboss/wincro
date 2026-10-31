"""Actual pinned Codex binary against an offline, loopback-only fake model.

This verifies the real CLI flags/tool surface/stream protocol, NOT GPT judgment.
Never uses operator credentials, captures the desktop, or generates PC input.
"""

import json
import os
from pathlib import Path
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

if os.environ.get("WINCRO_RECORDING_CODEX_QA") != "1" or os.name != "nt":
    pytest.skip("Opt-in isolated Windows Codex transport QA only", allow_module_level=True)

from src.agent_bridge.codex_runner import CodexRunner
from src.agent_bridge.recording_jobs import RecordingJobs
from src.agent_bridge.recording_connection import RecordingConnection


def test_packaged_mcp_client_starts_with_only_its_explicit_source_files(tmp_path):
    import shutil
    import subprocess
    from src.agent_bridge.codex_runner import hidden_options
    root = Path(__file__).resolve().parents[1]
    files = ["src/__init__.py", "src/agent_bridge/__init__.py", "src/agent_bridge/contracts.py",
             "src/agent_bridge/storage.py", "src/agent_bridge/transport.py", "tools/wincro_mcp_server.py"]
    for name in files:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / name, target)
    result = subprocess.run([os.sys.executable, str(tmp_path / "tools/wincro_mcp_server.py"), "--help"],
        cwd=tmp_path, capture_output=True, timeout=15, **hidden_options())
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert b"--connection-file" in result.stdout


@pytest.mark.parametrize("requires_credentials", [False, True])
def test_actual_manual_connection_precedes_media_and_stays_on_until_off_without_model_requests(tmp_path, monkeypatch, requires_credentials):
    home = tmp_path / "auth"
    home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))
    config = home / "config.toml"
    config.write_text('service_tier="priority"\n', encoding="utf-8")
    config_before = config.read_bytes()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("CODEX_API_KEY", raising=False)
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_POST(self):
            calls.append(self.path)
            self.send_error(500, "A connection toggle must not start a model request")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    original = CodexRunner.arguments
    def arguments(self, *values):
        args = original(self, *values)
        extras = {"model_provider": "offline", "model": "gpt-6.1-sol",
            "model_providers.offline.name": "Offline connection test",
            "model_providers.offline.base_url": f"http://127.0.0.1:{server.server_address[1]}/v1",
            "model_providers.offline.wire_api": "responses", "model_providers.offline.requires_openai_auth": requires_credentials}
        for key, value in extras.items():
            args += ["-c", key + "=" + json.dumps(value)]
        return args
    monkeypatch.setattr(CodexRunner, "arguments", arguments)
    connection = RecordingConnection(tmp_path / "connection")
    settings = {"codex": os.environ["WINCRO_TEST_CODEX_EXE"], "python": os.sys.executable, "model": ""}
    try:
        connection.enable(settings)
        deadline = time.monotonic() + 35
        while not connection.connected and connection.busy and time.monotonic() < deadline:
            time.sleep(.05)
        if requires_credentials:
            assert not connection.connected and not connection.busy, connection.snapshot()
            assert connection.snapshot()["state"] == "error"
            assert "LOGIN_REQUIRED" in connection.snapshot()["message"]
            assert not calls
            assert config.read_bytes() == config_before
            return
        assert connection.connected, connection.snapshot()
        runner = connection.runner
        assert runner.mcp_config["enabled_tools"] == ["connection_status"]
        time.sleep(7)
        assert connection.connected, connection.snapshot()
        connection.disable()
        assert not connection.connected
        connection.worker.join(8)
        assert not connection.busy and runner.process.poll() is not None
        assert not calls
        assert config.read_bytes() == config_before
        assert not list(connection.root.rglob("draft_*.json"))
        artifacts = Path("artifacts/recording_analysis")
        artifacts.mkdir(parents=True, exist_ok=True)
        (artifacts / "manual-connection.json").write_text(json.dumps({"actual_connection_confirmed": True,
            "idle_keepalive_confirmed": True, "off_revoked": True, "model_requests": len(calls),
            "media_shared": False, "tools": runner.mcp_config["enabled_tools"]}, indent=2), encoding="utf-8")
    finally:
        connection.close()
        if connection.worker:
            connection.worker.join(8)
        server.shutdown()
        server.server_close()
        thread.join(2)


@pytest.mark.parametrize("cancel_job", [False, True])
def test_actual_codex_has_no_pc_input_or_original_plan_writer_and_does_not_fake_completion(tmp_path, monkeypatch, cancel_job):
    executable = os.environ["WINCRO_TEST_CODEX_EXE"]
    auth_home = tmp_path / "empty-auth-home"
    auth_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(auth_home))
    config_path = auth_home / "config.toml"
    bridge = str(Path(__file__).resolve().parents[1] / "tools/wincro_mcp_server.py")
    foreign_marker = tmp_path / "foreign-started.txt"
    foreign_wrapper = tmp_path / "foreign_mcp.py"
    foreign_wrapper.write_text("from pathlib import Path\nimport runpy\nPath(" + repr(str(foreign_marker))
        + ").write_text('unexpected startup')\nrunpy.run_path(" + repr(bridge) + ", run_name='__main__')\n", encoding="utf-8")
    config_path.write_text('service_tier="priority"\nmodel_reasoning_effort="ultra"\nnotify=["wincro-notify-must-not-run.exe"]\n'
        + '[features]\nshell_tool=true\napps=true\nbrowser_use=true\ncomputer_use=true\n'
        + '[mcp_servers.unselected]\ncommand=' + json.dumps(os.sys.executable) + '\nargs='
        + json.dumps([str(foreign_wrapper), "--connection-file", str(tmp_path / "never-consented.json")]) + '\n', encoding="utf-8")
    config_before = config_path.read_bytes()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("CODEX_API_KEY", raising=False)
    requests = []
    connected_signals = []
    release_response = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_POST(self):
            payload = self.rfile.read(int(self.headers["Content-Length"]))
            requests.append(json.loads(payload))
            if cancel_job:
                release_response.wait(30)
            item = {"id": "msg_offline", "type": "message", "role": "assistant", "status": "completed",
                "content": [{"type": "output_text", "text": "Offline transport-only simulation; no actual GPT analysis.", "annotations": []}]}
            number = len(requests)
            if not cancel_job and number <= 2:
                namespace = next(tool["name"] for tool in requests[0]["tools"]
                    if tool.get("name", "").startswith("mcp__wincroRecording_"))
                recording = re.search(r"recording_id=(recording_[a-z0-9]+)", json.dumps(requests[0]))[1]
                name = "connection_status" if number == 1 else "get_frames"
                arguments = {} if number == 1 else {"recording_id": recording, "timestamps": [0]}
                item = {"id": f"fc_offline_{number}", "type": "function_call", "call_id": f"call_offline_{number}",
                    "namespace": namespace, "name": name, "arguments": json.dumps(arguments)}
            elif not cancel_job:
                deadline = time.monotonic() + 4
                while not jobs.connected and time.monotonic() < deadline:
                    time.sleep(.05)
                connected_signals.append(jobs.connected)
            events = [
                {"type": "response.created", "response": {"id": "resp_offline", "status": "in_progress", "output": []}},
                {"type": "response.output_item.added", "output_index": 0, "item": dict(item, status="in_progress")},
                {"type": "response.output_item.done", "output_index": 0, "item": item},
                {"type": "response.completed", "response": {"id": "resp_offline", "status": "completed", "output": [item],
                    "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}},
            ]
            body = "".join("event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n" for event in events).encode()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except OSError:
                pass  # The isolated owned client may have been cancelled.

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    original_arguments = CodexRunner.arguments
    port = server.server_address[1]
    def arguments(self, *values):
        args = original_arguments(self, *values)
        extras = {
            "model_provider": "wincro_offline", "model": "gpt-6.1-sol", "features.enable_request_compression": False,
            "model_providers.wincro_offline.name": "WinCro offline test",
            "model_providers.wincro_offline.base_url": f"http://127.0.0.1:{port}/v1",
            "model_providers.wincro_offline.wire_api": "responses",
            "model_providers.wincro_offline.requires_openai_auth": False,
        }
        for key, value in extras.items():
            args += ["-c", key + "=" + json.dumps(value)]
        return args
    monkeypatch.setattr(CodexRunner, "arguments", arguments)
    from PIL import Image
    image = tmp_path / "synthetic.png"
    Image.new("RGB", (40, 30), "gray").save(image)
    plans = tmp_path / "plans"
    plans.mkdir()
    original = plans / "plan_untouched.json"
    original.write_text('{"plan_id":"plan_untouched","name":"Original","initial_rules":[],"monitoring_rules":[]}', encoding="utf-8")
    before = original.read_bytes()
    jobs = RecordingJobs(tmp_path / "jobs", plans, tmp_path / "templates")
    try:
        jobs.start(image, "Offline transport QA only. Follow the deterministic fixture tool sequence; do not perform real analysis.",
                   {"codex": executable, "python": os.sys.executable, "model": "", "max_seconds": 45})
        runner = None
        if cancel_job:
            deadline = time.monotonic() + 25
            while not requests and jobs.busy and time.monotonic() < deadline:
                time.sleep(.05)
            runner = jobs.runner
            jobs.cancel()
            release_response.set()
        jobs.worker.join(65)
        assert not jobs.busy
        state = jobs.snapshot()
        artifacts = Path("artifacts/recording_analysis")
        artifacts.mkdir(parents=True, exist_ok=True)
        filename = "cancellation-state.json" if cancel_job else "execution-state.json"
        (artifacts / filename).write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        assert state["status"] == ("cancelled" if cancel_job else "needs_review"), json.dumps(state, ensure_ascii=False)
        assert requests, state
        assert all(request.get("service_tier") in (None, "default") for request in requests), "Bootstrap tier leaked into inference"
        if cancel_job:
            assert runner is not None and runner.process.poll() is not None
            assert not jobs.connected
        def names(value):
            if isinstance(value, dict):
                if isinstance(value.get("name"), str):
                    yield value["name"]
                for item in value.values():
                    yield from names(item)
            elif isinstance(value, list):
                for item in value:
                    yield from names(item)
        tools = set(names(requests[0].get("tools", [])))
        filename = "cancelled-tool-surface.json" if cancel_job else "codex-tool-surface.json"
        (artifacts / filename).write_text(json.dumps({"tool_names": sorted(tools), "status": state["status"],
            "real_model_called": False, "original_plan_unchanged": original.read_bytes() == before}, indent=2), encoding="utf-8")
        assert any("get_frames" in name for name in tools), tools
        forbidden = ("exec_command", "shell", "apply_patch", "computer", "browser", "spawn_agent", "publish_new_plan", "open_draft", "view_image", "unselected")
        assert not any(any(word in name for word in forbidden) for name in tools), tools
        assert original.read_bytes() == before
        assert list(plans.iterdir()) == [original]
        assert config_path.read_bytes() == config_before
        assert not foreign_marker.exists(), "An unconsented global MCP process was started"
        frames_received = not cancel_job and len(requests) >= 3 and "data:image/" in json.dumps(requests[2])
        if not cancel_job:
            assert frames_received, "Real Codex did not receive the fixture MCP image"
            assert any(connected_signals), "Actual tool traffic did not confirm a live connection"
        artifacts = Path("artifacts/recording_analysis")
        artifacts.mkdir(parents=True, exist_ok=True)
        filename = "cancelled-tool-surface.json" if cancel_job else "codex-tool-surface.json"
        (artifacts / filename).write_text(json.dumps({"tool_names": sorted(tools), "status": state["status"],
            "real_model_called": False, "original_plan_unchanged": True, "actual_mcp_image_received": frames_received,
            "actual_mcp_connected": any(connected_signals), "unselected_mcp_started": False}, indent=2), encoding="utf-8")
    finally:
        release_response.set()
        jobs.close()
        if jobs.worker:
            jobs.worker.join(8)
        server.shutdown()
        server.server_close()
        server_thread.join(2)
