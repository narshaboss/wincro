"""Opt-in Codex app-server job with MCP tools and NO local environment."""

import json
import hashlib
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import threading
import time

from .contracts import BridgeError


DISABLED_FEATURES = (
    "shell_tool", "shell_snapshot", "apps", "plugins", "hooks", "plugin_hooks", "multi_agent",
    "browser_use", "browser_use_external", "computer_use", "in_app_browser", "code_mode",
    "code_mode_only", "image_generation", "memories", "remote_control", "tool_suggest",
    "request_permissions_tool", "workspace_dependencies", "skill_mcp_dependency_install",
    "tool_search", "apply_patch_freeform", "unavailable_dummy_tools",
)
CRITICAL_FEATURES = {"shell_tool", "apps", "plugins", "hooks", "multi_agent", "browser_use", "computer_use"}
MAX_EVENT_BYTES = 8_000_000
SUPPORTED_CODEX_VERSIONS = {"codex-cli 0.130.0-alpha.5"}


def default_runtime():
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "OpenAI" / "Codex" / "bin" / "codex.exe"
    candidate = local if local.is_file() else Path(shutil.which("codex.exe") or "")
    return {"codex": str(candidate) if candidate.is_file() else "", "python": sys.executable if not getattr(sys, "frozen", False) else "",
            "model": "", "max_seconds": 1200}


def load_runtime(path):
    from .storage import read_json
    values = default_runtime()
    try:
        saved = read_json(path)
    except FileNotFoundError:
        return values
    if not isinstance(saved, dict):
        raise ValueError("연결 설정은 JSON 객체여야 합니다.")
    for key in ("codex", "python", "model", "reference"):
        if key in saved and not (isinstance(saved[key], str) or key == "reference" and saved[key] is None):
            raise ValueError("연결 설정 형식이 잘못되었습니다: " + key)
    timeout = saved.get("max_seconds", 1200)
    if type(timeout) is not int or not 30 <= timeout <= 3600:
        raise ValueError("분석 제한 시간은 30~3600초여야 합니다.")
    values.update({key: saved[key] for key in (*values, "reference") if key in saved})
    return values


def clean_message(message):
    text = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", str(message))
    text = re.sub(r"(?i)(bearer\s+|sk-[a-z0-9_-]*)([a-z0-9_.-]{12,})", "[redacted]", text)
    return text[-6000:]


def hidden_options():
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}


class _ProcessTree:
    """Own only this process tree; closing the Windows job also reaps MCP children."""

    def __init__(self, process):
        self.process = process
        self.handle = None
        if os.name != "nt":
            return
        import ctypes
        from ctypes import wintypes

        class Basic(ctypes.Structure):
            _fields_ = [("ProcessTime", ctypes.c_longlong), ("JobTime", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSet", ctypes.c_size_t),
                ("MaximumWorkingSet", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [(field, ctypes.c_ulonglong) for field in (
                "ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes", "OtherBytes")]

        class Limits(ctypes.Structure):
            _fields_ = [("Basic", Basic), ("IO", IO), ("ProcessMemory", ctypes.c_size_t),
                ("JobMemory", ctypes.c_size_t), ("PeakProcessMemory", ctypes.c_size_t), ("PeakJobMemory", ctypes.c_size_t)]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
        kernel.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        self.kernel = kernel
        handle = kernel.CreateJobObjectW(None, None)
        limits = Limits()
        limits.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not handle or not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            if handle:
                kernel.CloseHandle(handle)
            process.kill()
            raise OSError("분석 프로세스 격리 준비에 실패했습니다.")
        if not kernel.AssignProcessToJobObject(handle, wintypes.HANDLE(int(process._handle))):
            kernel.CloseHandle(handle)
            process.kill()
            raise OSError("분석 프로세스 트리를 격리하지 못했습니다.")
        self.handle = handle

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
        elif self.process.poll() is None:
            if os.name == "nt":
                self.process.kill()
            else:
                import signal
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass


class CodexRunner:
    def __init__(self, settings, folder):
        self.settings = dict(settings)
        self.folder = Path(folder).resolve()
        self.server_name = "wincroRecording_" + hashlib.sha256(str(self.folder).encode()).hexdigest()[:16]
        self.process = None
        self.tree = None
        self.lock = threading.Lock()

    def preflight(self):
        codex = Path(self.settings.get("codex", "")).expanduser()
        python = Path(self.settings.get("python", "")).expanduser()
        if not codex.is_file() or (os.name == "nt" and codex.suffix.lower() != ".exe"):
            raise BridgeError("RUNTIME_MISSING", "Codex 실행 파일(.exe)을 연결 설정에서 선택하세요. 깨진 cmd 바로가기는 사용하지 않습니다.")
        if not python.is_file() or (os.name == "nt" and python.suffix.lower() != ".exe"):
            raise BridgeError("RUNTIME_MISSING", "MCP용 Python 실행 파일(.exe)을 선택하세요.")
        version = subprocess.run([str(codex), "--version"], capture_output=True, timeout=10, **hidden_options())
        if version.returncode or version.stdout.decode("utf-8", "replace").strip() not in SUPPORTED_CODEX_VERSIONS:
            raise BridgeError("RUNTIME_UNSUPPORTED", "이 Codex 버전의 도구 격리는 아직 검수되지 않았습니다. 검수된 실행기를 지정하거나 호환성 패치를 기다리세요.")
        server = Path(__file__).resolve().parents[2] / "tools" / "wincro_mcp_server.py"
        if not server.is_file():
            raise BridgeError("RUNTIME_MISSING", "이 설치에는 GPT 연결 서버가 없습니다. 편집 도구 설치가 필요합니다.")
        help_result = subprocess.run([str(codex), "app-server", "--help"], capture_output=True, timeout=15, **hidden_options())
        output = help_result.stdout.decode("utf-8", "replace")
        if help_result.returncode or not all(flag in output for flag in ("--listen", "stdio://")):
            raise BridgeError("RUNTIME_UNSUPPORTED", "이 Codex 실행기는 안전한 자동 분석 실행 옵션을 지원하지 않습니다.")
        probe = self.folder / "capability-probe"
        probe.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, CODEX_HOME=str(probe))
        feature_args = [str(codex), "features", "list"]
        for feature in CRITICAL_FEATURES:
            feature_args += ["--disable", feature]
        features = subprocess.run(feature_args, env=env, capture_output=True, timeout=15, **hidden_options())
        values = {line.split()[0]: line.split()[-1] for line in features.stdout.decode("utf-8", "replace").splitlines() if len(line.split()) >= 3}
        if features.returncode or any(values.get(name) != "false" for name in CRITICAL_FEATURES):
            missing = ", ".join(sorted(name for name in CRITICAL_FEATURES if values.get(name) != "false"))
            raise BridgeError("RUNTIME_UNSUPPORTED", "셸·앱·PC 제어 도구 비활성화를 확인하지 못했습니다: " + missing)
        dependency = subprocess.run([str(python), "-c", "import importlib.metadata; from mcp.server.fastmcp import FastMCP; assert importlib.metadata.version('mcp') == '1.25.0'; print('WINCRO_MCP_READY')"],
                                    capture_output=True, timeout=15, **hidden_options())
        if dependency.returncode or b"WINCRO_MCP_READY" not in dependency.stdout:
            raise BridgeError("MCP_MISSING", "표준 입출력이 가능한 Python과 검수한 mcp==1.25.0 설치가 필요합니다.")
        return str(codex.resolve()), str(python.resolve()), str(server)

    def arguments(self, codex, python, server, descriptor):
        from .recording_jobs import JOB_TOOLS
        args = [codex, "app-server", "--listen", "stdio://"]
        configs = {
            "approval_policy": "never", "approvals_reviewer": "user", "web_search": "disabled",
            "sandbox_mode": "read-only", "project_doc_max_bytes": 0,
            "model_reasoning_effort": "high", "notify": [], "analytics.enabled": False,
            f"mcp_servers.{self.server_name}.command": python,
            f"mcp_servers.{self.server_name}.args": [server, "--connection-file", str(descriptor)],
            f"mcp_servers.{self.server_name}.cwd": str(Path(server).parent.parent),
            f"mcp_servers.{self.server_name}.required": True,
            f"mcp_servers.{self.server_name}.enabled_tools": sorted(JOB_TOOLS | {"connection_status"}),
        }
        for key, value in configs.items():
            args += ["-c", key + "=" + ("{}" if isinstance(value, dict) else json.dumps(value, ensure_ascii=False))]
        for feature in DISABLED_FEATURES:
            args += ["-c", f"features.{feature}=false"]
        model = self.settings.get("model", "").strip()
        if model:
            if len(model) > 100 or not re.fullmatch(r"[a-zA-Z0-9_.:-]+", model):
                raise BridgeError("INVALID_FIELD", "모델 이름이 올바르지 않습니다.")
            args += ["-c", "model=" + json.dumps(model)]
        return args

    def thread_parameters(self, servers=()):
        return {"cwd": str(self.folder), "ephemeral": True, "environments": [],
            "approvalPolicy": "never", "approvalsReviewer": "user", "sandbox": "read-only", "serviceTier": None,
            "config": {f"mcp_servers.{json.dumps(name)}.enabled": False for name in servers if name != self.server_name}}

    def run(self, descriptor, prompt, cancelled, progress):
        codex, python, server = self.preflight()
        if cancelled.is_set():
            raise BridgeError("JOB_CANCELLED", "분석이 취소되었습니다.")
        args = self.arguments(codex, python, server, descriptor)
        timeout = self.settings.get("max_seconds", 1200)
        if type(timeout) is not int or not 30 <= timeout <= 3600:
            raise BridgeError("INVALID_FIELD", "분석 제한 시간은 30~3600초여야 합니다.")
        events = queue.Queue(maxsize=4)
        stderr = bytearray()
        stop_reading = threading.Event()
        process = subprocess.Popen(args, cwd=self.folder, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, **hidden_options())
        with self.lock:
            self.process = process
            try:
                self.tree = _ProcessTree(process)
            except Exception:
                process.kill()
                process.wait(timeout=5)
                raise

        def read_events():
            try:
                while not stop_reading.is_set():
                    line = process.stdout.readline(MAX_EVENT_BYTES + 1)
                    if not line:
                        break
                    value = {"oversized": True} if len(line) > MAX_EVENT_BYTES else line
                    while not stop_reading.is_set():
                        try:
                            events.put(value, timeout=.1)
                            break
                        except queue.Full:
                            continue
                    if isinstance(value, dict):
                        break
            finally:
                while not stop_reading.is_set():
                    try:
                        events.put(None, timeout=.1)
                        break
                    except queue.Full:
                        continue

        def read_errors():
            while True:
                chunk = process.stderr.read(2048)
                if not chunk:
                    break
                stderr.extend(chunk)
                del stderr[:-8000]

        threads = [threading.Thread(target=read_events, daemon=True), threading.Thread(target=read_errors, daemon=True)]
        for thread in threads:
            thread.start()
        final = ""
        thread_id = None
        count = 0
        started = time.monotonic()
        try:
            if cancelled.is_set():
                raise BridgeError("JOB_CANCELLED", "분석 취소")
            def send(value):
                process.stdin.write((json.dumps(value, ensure_ascii=False) + "\n").encode("utf-8"))
                process.stdin.flush()
            send({"id": 1, "method": "initialize", "params": {"clientInfo": {
                "name": "wincro_recording", "title": "WinCro recording analyst", "version": "1.0"},
                "capabilities": {"experimentalApi": True}}})
            while True:
                if cancelled.is_set():
                    raise BridgeError("JOB_CANCELLED", "분석 취소")
                if time.monotonic() - started > timeout:
                    raise BridgeError("GPT_TIMEOUT", "분석 제한 시간이 지났습니다. 저장된 초안은 결과 탭에서 확인할 수 있습니다.")
                try:
                    line = events.get(timeout=.15)
                except queue.Empty:
                    continue
                if line is None:
                    raise BridgeError("GPT_EXECUTION_FAILED", "GPT 연결이 정상 턴 종료 전에 닫혔습니다. " + clean_message(stderr.decode("utf-8", "replace")))
                if isinstance(line, dict):
                    raise BridgeError("LIMIT_REACHED", "GPT 응답이 허용 크기를 초과했습니다.")
                if not line.strip():
                    continue
                count += 1
                if count > 10000:
                    raise BridgeError("LIMIT_REACHED", "분석 이벤트 제한에 도달했습니다. 초안을 확인하세요.")
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeError):
                    sample = clean_message(line.decode("utf-8", "replace"))[:300]
                    raise BridgeError("GPT_PROTOCOL_ERROR", "GPT 실행기 JSON 응답을 해석할 수 없습니다: " + repr(sample))
                if not isinstance(event, dict):
                    raise BridgeError("GPT_PROTOCOL_ERROR", "GPT 실행기의 이벤트 형식이 잘못되었습니다.")
                if "id" in event and "method" in event:
                    send({"id": event["id"], "error": {"code": -32601, "message": "Interactive or permission requests are disabled"}})
                    raise BridgeError("PERMISSION_REQUIRED", "GPT가 허용되지 않은 승인·대화 요청을 보냈습니다. 초안을 확인하세요.")
                if "error" in event:
                    raise BridgeError("GPT_EXECUTION_FAILED", clean_message(event["error"]))
                if event.get("id") == 1:
                    send({"method": "initialized"})
                    send({"id": 10, "method": "config/read", "params": {"includeLayers": False}})
                    continue
                if event.get("id") == 10:
                    servers = event.get("result", {}).get("config", {}).get("mcp_servers", {})
                    if not isinstance(servers, dict) or self.server_name not in servers or len(servers) > 128:
                        raise BridgeError("RUNTIME_UNSUPPORTED", "작업 외 MCP 연결 차단을 위한 설정 목록을 확인하지 못했습니다.")
                    send({"id": 2, "method": "thread/start", "params": self.thread_parameters(servers)})
                    continue
                if event.get("id") == 2:
                    result = event.get("result", {})
                    thread_id = result.get("thread", {}).get("id")
                    if not isinstance(thread_id, str) or result.get("approvalPolicy") != "never" or result.get("sandbox", {}).get("type") not in {"readOnly", "read-only"}:
                        raise BridgeError("RUNTIME_UNSUPPORTED", "작업 전용 읽기 제한을 확인하지 못했습니다.")
                    send({"id": 3, "method": "turn/start", "params": {"threadId": thread_id,
                        "environments": [], "input": [{"type": "text", "text": prompt}]}})
                    continue
                method = event.get("method", "")
                params = event.get("params", {})
                if method == "turn/completed" and params.get("threadId") == thread_id:
                    turn = params.get("turn", {})
                    if turn.get("status") != "completed":
                        raise BridgeError("GPT_EXECUTION_FAILED", clean_message(turn.get("error") or turn.get("status")))
                    return {"exit_code": 0, "message": final}
                item = params.get("item", {})
                kind = item.get("type")
                if kind in {"commandExecution", "fileChange", "webSearch", "collabAgentToolCall", "imageView", "imageGeneration"}:
                    raise BridgeError("UNSAFE_TOOL", "허용하지 않은 실행 도구가 감지되어 분석을 중단했습니다.")
                if method == "error":
                    if params.get("willRetry") is True:
                        progress("GPT 연결 오류로 재시도하고 있습니다. 저장된 초안은 유지됩니다.")
                        continue
                    raise BridgeError("GPT_EXECUTION_FAILED", clean_message(params.get("error", "GPT 오류")))
                if kind == "mcpToolCall" and method == "item/started":
                    tool = item.get("tool", "")
                    progress({"get_frames": "GPT가 실제 녹화 장면을 확인하고 있습니다.",
                        "stage_edits": "GPT가 독립 초안의 액션을 편집하고 있습니다.",
                        "record_analysis": "GPT가 행동 목적과 영상 근거를 정리하고 있습니다.",
                        "record_crosscheck": "GPT가 영상과 초안을 번갈아 교차검수하고 있습니다.",
                        "save_draft": "초안을 파일에 저장하고 다시 읽고 있습니다."}.get(tool, "GPT가 선택한 자료와 초안을 확인하고 있습니다."))
                if kind == "agentMessage" and method == "item/completed":
                    final = clean_message(item.get("text", ""))
        finally:
            stop_reading.set()
            self.cancel()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            for thread in threads:
                thread.join(timeout=2)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()

    def cancel(self):
        with self.lock:
            if self.tree:
                self.tree.close()
            elif self.process and self.process.poll() is None:
                self.process.kill()
