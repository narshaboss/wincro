"""Bounded, incremental play diagnostics; never log credentials or model reasoning."""

from collections import deque
from datetime import datetime
import json
import logging
from pathlib import Path
import threading
import time

from ..agent_bridge.codex_runner import clean_message


logger = logging.getLogger(__name__)


def concise(value, limit=800):
    return " ".join(clean_message(value).split())[:limit]


def error_text(exc):
    return f"{getattr(exc, 'code', type(exc).__name__)}: {concise(exc)}"


def action_text(action):
    kind = action.get("kind")
    if kind == "key":
        return "키 " + concise(action.get("key"), 30).upper()
    if kind == "click":
        return f"왼쪽 클릭 창내({action.get('x')}, {action.get('y')})"
    if kind == "wait":
        return f"대기 {action.get('seconds')}초"
    return "허용되지 않은 동작"


class PlayTrace:
    max_rows = 300
    max_file_bytes = 4_000_000

    def __init__(self, folder=None, identity="start", *, clock=time.monotonic):
        self.identity, self.clock = identity, clock
        self.path = Path(folder) / "events.jsonl" if folder else None
        self.lock = threading.Lock()
        self.rows = deque(maxlen=self.max_rows)
        self.sequence = self.file_bytes = 0
        self.file_disabled = False
        self.phase = "시작 대기"
        self.last_activity = clock()
        self.last_heartbeat = clock()
        self._last_progress = None

    def emit(self, event, message, *, level="info", phase=None, activity=True):
        message = concise(message)
        with self.lock:
            now = self.clock()
            if phase is not None:
                self.phase = concise(phase, 80)
            if activity:
                self.last_activity = now
            self.sequence += 1
            row = {"seq": self.sequence, "time": datetime.now().astimezone().isoformat(timespec="seconds"),
                   "event": event, "level": level, "phase": self.phase, "message": message}
            self.rows.append(row)
            if self.path and not self.file_disabled:
                raw = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
                try:
                    if self.file_bytes + len(raw) > self.max_file_bytes:
                        raise OSError("세션 로그 용량 한도 도달")
                    with self.path.open("ab") as stream:
                        stream.write(raw)
                    self.file_bytes += len(raw)
                except OSError as exc:
                    self.file_disabled = True
                    logger.warning("[GPT플레이][%s] 세션 로그 파일 기록 중단: %s; 화면·일반 로그 유지",
                                   self.identity, concise(exc))
                    self.sequence += 1
                    self.rows.append({**row, "seq": self.sequence, "event": "log_file_error", "level": "warning",
                        "message": "세션 로그 파일 기록 중단: " + concise(exc) + " · 화면·일반 로그는 유지됩니다."})
            logger.log(logging.WARNING if level == "warning" else logging.ERROR if level == "error" else logging.INFO,
                       "[GPT플레이][%s][%s] %s", self.identity, event, message)

    def progress(self, message):
        message = concise(message)
        with self.lock:
            if message == self._last_progress:
                return
            self._last_progress = message
        self.emit("model_progress", message)

    def heartbeat(self, state, actions, frames):
        with self.lock:
            now = self.clock()
            if now - self.last_heartbeat < 15:
                return
            self.last_heartbeat = now
            phase, quiet = self.phase, int(now - self.last_activity)
        phase = {"paused": "일시정지", "question": "답변 대기", "stopped": "중지"}.get(state, phase)
        self.emit("waiting", f"진행 확인: 단계={phase} 상태={state} 관찰={frames}회 요청={actions}회 "
                  f"마지막 진행 후 {quiet}초 · 전송 완료가 게임 성공을 의미하지는 않습니다.", activity=False)

    def since(self, sequence):
        with self.lock:
            return [dict(row) for row in self.rows if row["seq"] > sequence]

    def status(self):
        with self.lock:
            return self.phase, int(self.clock() - self.last_activity)
