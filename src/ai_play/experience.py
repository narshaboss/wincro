"""Task-scoped, durable experience. No desktop, model or input side effects."""

import copy
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import time

from ..agent_bridge.contracts import BridgeError
from ..agent_bridge.storage import new_id, publish_bytes, within


def memory_root():
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "WinCro" / "gpt_play_memory"


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


class TaskLease:
    """OS-owned lock, released on process death; never guess a stale PID."""

    def __init__(self, path):
        self.stream = open(path, "a+b")
        try:
            if os.fstat(self.stream.fileno()).st_size == 0:
                self.stream.write(b"0")
                self.stream.flush()
            self.stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.stream.close()
            raise BridgeError("EXPERIENCE_BUSY", "다른 실행이 이 경험을 사용 중입니다.") from exc

    def close(self):
        if not self.stream.closed:
            if os.name == "nt":
                import msvcrt
                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            self.stream.close()


class ExperienceStore:
    VERSION = 1

    def __init__(self, root, quota=2_000_000_000):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "experience.db"
        self.quota = quota
        self._quota_lock = threading.Lock()
        self._asset_bytes = sum(p.stat().st_size for p in (self.root / "evidence").rglob("*") if p.is_file())
        # Never replace/reset an unreadable or newer database.
        with self.connection() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, self.VERSION):
                raise BridgeError("MEMORY_VERSION", "경험 DB 버전을 지원하지 않습니다. 원본을 보존했습니다.")
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise BridgeError("MEMORY_CORRUPT", "경험 DB 무결성 검사 실패. 원본을 보존했습니다.")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS documents (
                    task TEXT NOT NULL, kind TEXT NOT NULL, id TEXT NOT NULL,
                    updated REAL NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(task,kind,id));
                CREATE INDEX IF NOT EXISTS document_recent ON documents(task,kind,updated);
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, task TEXT NOT NULL,
                    kind TEXT NOT NULL, created REAL NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS event_recent ON events(task,kind,seq);
            """)
            db.execute("PRAGMA user_version=1")

    @contextmanager
    def connection(self):
        try:
            db = sqlite3.connect(self.path, timeout=2)
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            with db:
                yield db
        except sqlite3.Error as exc:
            raise BridgeError("MEMORY_IO", "경험 저장소 오류. 기록을 삭제하지 않았습니다: " + str(exc)) from exc
        finally:
            if "db" in locals():
                db.close()

    def _space(self, addition=0):
        # Do not walk every screenshot for every input receipt.
        size = self._asset_bytes + sum(p.stat().st_size for p in self.root.glob("*.db*") if p.is_file())
        if size + addition > self.quota:
            raise BridgeError("MEMORY_FULL", "경험 저장 한도 도달. 기존 기록을 지우지 않고 실행을 중단합니다.")

    def create(self, goal, environment):
        task = new_id("task_")
        self.put(task, "task", task, {"task_id": task, "goal": goal,
            "environment": environment, "created": time.time()})
        return task

    def claim(self, task):
        self.get(task, "task", task)
        return TaskLease(self.root / (task + ".lock"))

    def put(self, task, kind, ident, value):
        self.put_many(task, [(kind, ident, value)])
        return copy.deepcopy(value)

    def put_many(self, task, rows):
        bodies = [(kind, ident, packed(value)) for kind, ident, value in rows]
        self._space(sum(len(body.encode("utf-8")) for _, _, body in bodies))
        with self.connection() as db:
            db.executemany("INSERT INTO documents VALUES(?,?,?,?,?) ON CONFLICT(task,kind,id) "
                           "DO UPDATE SET updated=excluded.updated,payload=excluded.payload",
                           [(task, kind, ident, time.time(), body) for kind, ident, body in bodies])
        for kind, ident, value in rows:
            if self.get(task, kind, ident) != value:
                raise BridgeError("SAVE_STATE_UNKNOWN", "경험 재로딩 검증 결과가 다릅니다.")

    def get(self, task, kind, ident):
        with self.connection() as db:
            row = db.execute("SELECT payload FROM documents WHERE task=? AND kind=? AND id=?",
                             (task, kind, ident)).fetchone()
        if not row:
            raise BridgeError("MEMORY_NOT_FOUND", "선택한 경험 기록이 없습니다.")
        return json.loads(row[0])

    def documents(self, task, kind, limit=20, offset=0):
        with self.connection() as db:
            rows = db.execute("SELECT payload FROM documents WHERE task=? AND kind=? "
                              "ORDER BY updated DESC LIMIT ? OFFSET ?", (task, kind, min(100, limit), offset)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def tasks(self, limit=50):
        with self.connection() as db:
            rows = db.execute("SELECT payload FROM documents WHERE kind='task' ORDER BY updated DESC LIMIT ?", (limit,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def event(self, task, kind, value):
        body = packed(value)
        self._space(len(body.encode("utf-8")))
        with self.connection() as db:
            cursor = db.execute("INSERT INTO events(task,kind,created,payload) VALUES(?,?,?,?)",
                                (task, kind, time.time(), body))
            seq = cursor.lastrowid
        with self.connection() as db:
            actual = db.execute("SELECT payload FROM events WHERE seq=?", (seq,)).fetchone()
        if not actual or actual[0] != body:
            raise BridgeError("SAVE_STATE_UNKNOWN", "경험 이벤트 재로딩 실패")
        return seq

    def context(self, task):
        with self.connection() as db:
            rows = db.execute("SELECT kind,payload FROM events WHERE task=? ORDER BY seq DESC LIMIT 10", (task,)).fetchall()
        checkpoints = self.documents(task, "checkpoint", 1)
        return {"task": self.get(task, "task", task), "recent": [
            {"kind": kind, "record": json.loads(body)} for kind, body in reversed(rows)],
            "attempts": self.documents(task, "attempt", 10),
            "behaviors": [{k: v for k, v in b.items() if k != "program"}
                          for b in self.documents(task, "behavior", 20)],
            "checkpoint": checkpoints[0] if checkpoints else None,
            "historical_not_current": True}

    def save_frame(self, task, metadata, raw):
        sha = hashlib.sha256(raw).hexdigest()
        if sha != metadata["sha256"]:
            raise BridgeError("EVIDENCE_CHANGED", "관찰 장면 해시 불일치")
        root = self.root / "evidence" / task
        path = root / (sha + ".png")
        if not path.exists():
            with self._quota_lock:
                self._space(len(raw))
                publish_bytes(root, path.name, raw)
                self._asset_bytes += len(raw)
        if within(path, root).read_bytes() != raw:
            raise BridgeError("EVIDENCE_CHANGED", "영구 관찰 장면 검증 실패")
        self.put(task, "frame", metadata["frame_id"], metadata)

    def read_frame(self, task, ident):
        meta = self.get(task, "frame", ident)
        root = self.root / "evidence" / task
        data = within(root / (meta["sha256"] + ".png"), root).read_bytes()
        if hashlib.sha256(data).hexdigest() != meta["sha256"]:
            raise BridgeError("EVIDENCE_CHANGED", "영구 근거가 변경됐습니다.")
        return meta, data

    def backup(self):
        folder = self.root / "backups"
        folder.mkdir(exist_ok=True)
        path = folder / (new_id("memory_") + ".db")
        with self.connection() as source, sqlite3.connect(path) as target:
            source.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise BridgeError("BACKUP_FAILED", "경험 백업 검증 실패")
        return path
