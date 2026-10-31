"""User-controlled pre-analysis connection; no media or draft authority."""

from pathlib import Path
import threading

from .contracts import BridgeError
from .storage import new_id
from .transport import LocalHost, protect_directory


class ConnectionScope:
    def __init__(self):
        self.lock = threading.RLock()
        self.instance_id = new_id("connection_")

    def dispatch(self, method, arguments):
        raise BridgeError("TOOL_DENIED", "Connection preparation cannot read recordings, plans or drafts")


class RecordingConnection:
    def __init__(self, root, runner_factory=None):
        self.root = Path(root)
        self.runner_factory = runner_factory
        self.lock = threading.RLock()
        self.worker = self.runner = self.host = None
        self.cancel_event = threading.Event()
        self.closed = False
        self.handshake = False
        self.state = "off"
        self.message = "GPT 연결 OFF를 눌러 먼저 연결하세요."

    @property
    def busy(self):
        return bool(self.worker and self.worker.is_alive())

    @property
    def connected(self):
        with self.lock:
            return bool(self.handshake and not self.cancel_event.is_set() and self.host and self.host.client_connected and self.busy)

    def snapshot(self):
        with self.lock:
            return {"state": "on" if self.connected else self.state, "message": self.message, "busy": self.busy}

    def enable(self, settings):
        with self.lock:
            if self.closed or self.busy:
                raise BridgeError("CONNECTION_BUSY", "이전 연결이 종료된 뒤 다시 눌러주세요.")
            self.cancel_event = threading.Event()
            self.handshake = False
            self.state, self.message = "connecting", "Codex 로그인과 WinCro 통신을 확인하고 있습니다."
            self.worker = threading.Thread(target=self._run, args=(dict(settings), self.cancel_event),
                name="wincro-gpt-connection", daemon=True)
            self.worker.start()

    def _run(self, settings, cancelled):
        host = runner = None
        try:
            folder = self.root / new_id("connection_")
            protect_directory(folder)
            scope = ConnectionScope()
            host = LocalHost(scope, folder / "pipe")
            with self.lock:
                self.host = host
            descriptor = host.start()
            if cancelled.is_set():
                return
            if self.runner_factory is None:
                from .codex_runner import CodexRunner
                runner = CodexRunner(settings, folder, tools={"connection_status"})
            else:
                runner = self.runner_factory(settings, folder)
            with self.lock:
                self.runner = runner
            def ready(info):
                if info.get("instance_id") != scope.instance_id:
                    raise BridgeError("WRONG_INSTANCE", "다른 WinCro 인스턴스의 연결 응답입니다.")
                with self.lock:
                    if not cancelled.is_set():
                        self.handshake = True
                        self.message = "GPT 연결 준비 완료. 녹화를 선택하고 분석 시작을 누르세요."
            runner.connect(descriptor, cancelled, ready)
            if not cancelled.is_set():
                raise BridgeError("CONNECTION_LOST", "GPT 연결이 종료되었습니다. ON을 눌러 다시 연결하세요.")
        except Exception as exc:
            with self.lock:
                if not cancelled.is_set():
                    self.state, self.message = "error", f"{getattr(exc, 'code', type(exc).__name__)}: {exc}"[:2000]
        finally:
            if host:
                host.close()
            if runner:
                runner.cancel()
            with self.lock:
                self.handshake = False
                self.runner = None
                if cancelled.is_set():
                    self.state = "off"

    def disable(self):
        with self.lock:
            self.cancel_event.set()
            self.handshake = False
            self.state, self.message = "off", "GPT 연결을 해제했습니다. OFF 상태에서는 분석하지 않습니다."
            host, runner = self.host, self.runner
        if host:
            host.close()
        if runner:
            runner.cancel()

    def close(self):
        self.closed = True
        self.disable()
