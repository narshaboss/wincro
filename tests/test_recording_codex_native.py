"""Actual pinned Codex binary against an offline, loopback-only fake model.

This verifies the real CLI flags/tool surface/stream protocol, NOT GPT judgment.
Never uses operator credentials, captures the desktop, or generates PC input.
"""

import json
import os
from pathlib import Path
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

if os.environ.get("WINCRO_RECORDING_CODEX_QA") != "1" or os.name != "nt":
    pytest.skip("Opt-in isolated Windows Codex transport QA only", allow_module_level=True)

from src.agent_bridge.codex_runner import CodexRunner
from src.agent_bridge.recording_jobs import RecordingJobs


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


@pytest.mark.parametrize("cancel_job", [False, True])
def test_actual_codex_has_no_pc_input_or_original_plan_writer_and_does_not_fake_completion(tmp_path, monkeypatch, cancel_job):
    executable = os.environ["WINCRO_TEST_CODEX_EXE"]
    auth_home = tmp_path / "empty-auth-home"
    auth_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(auth_home))
    config_path = auth_home / "config.toml"
    bridge = str(Path(__file__).resolve().parents[1] / "tools/wincro_mcp_server.py")
    config_path.write_text('model_reasoning_effort="ultra"\nnotify=["wincro-notify-must-not-run.exe"]\n'
        + '[mcp_servers.unselected]\ncommand=' + json.dumps(os.sys.executable) + '\nargs='
        + json.dumps([bridge, "--connection-file", str(tmp_path / "never-consented.json")]) + '\n', encoding="utf-8")
    config_before = config_path.read_bytes()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("CODEX_API_KEY", raising=False)
    requests = []
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
            events = [
                {"type": "response.created", "response": {"id": "resp_offline", "status": "in_progress", "output": []}},
                {"type": "response.output_item.added", "output_index": 0, "item": dict(item, status="in_progress")},
                {"type": "response.output_text.delta", "item_id": "msg_offline", "output_index": 0, "content_index": 0,
                 "delta": item["content"][0]["text"]},
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
            "model_provider": "wincro_offline", "model": "gpt-5.4", "features.enable_request_compression": False,
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
        jobs.start(image, "Offline transport QA only. Return the supplied test response; do not use tools.",
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
        artifacts = Path("artifacts/recording_analysis")
        artifacts.mkdir(parents=True, exist_ok=True)
        filename = "cancelled-tool-surface.json" if cancel_job else "codex-tool-surface.json"
        (artifacts / filename).write_text(json.dumps({"tool_names": sorted(tools), "status": state["status"],
            "real_model_called": False, "original_plan_unchanged": True}, indent=2), encoding="utf-8")
    finally:
        release_response.set()
        jobs.close()
        if jobs.worker:
            jobs.worker.join(8)
        server.shutdown()
        server.server_close()
        server_thread.join(2)
