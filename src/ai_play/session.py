"""Bounded live tools with evidence, durable receipts and explicit human questions.

All controls and storage are injected. Importing this module cannot send input.
"""

import base64
import copy
import io
from pathlib import Path
import threading
import time

from ..agent_bridge.contracts import BridgeError, digest, number
from ..agent_bridge.storage import new_id, publish_bytes, write_json, within


SAFE_KEYS = frozenset("up down left right enter esc space tab pageup pagedown home end insert delete backspace".split()) | frozenset("abcdefghijklmnopqrstuvwxyz0123456789")
TOOLS = {"connection_status", "play_observe", "play_frame", "play_act", "play_status", "play_question",
         "play_note", "play_candidate", "play_review", "play_verify", "play_trial_result", "play_finish"}


def text(value, label, maximum=3000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise BridgeError("INVALID_ARGUMENT", label + " 내용을 입력하세요.")
    return value.strip()


class PlaySession:
    def __init__(self, folder, goal, keys, backend, connected, *, max_seconds=900,
                 max_actions=500, clock=time.monotonic):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.instance_id = new_id("play_")
        self.lock = threading.RLock()  # transport serialization, never held by UI stop
        self.state_lock = threading.RLock()
        self.io_lock = threading.Lock()
        self.backend, self.connected, self.clock = backend, connected, clock
        self.goal = text(goal, "목표")
        if not isinstance(keys, list) or not keys or any(k not in SAFE_KEYS for k in keys):
            raise BridgeError("INVALID_KEYS", "허용할 방향키·스킬키를 확인하세요. 조합키·시스템키는 허용하지 않습니다.")
        if type(max_seconds) is not int or not 30 <= max_seconds <= 3600:
            raise BridgeError("INVALID_LIMIT", "실행 제한은 30~3600초입니다.")
        if type(max_actions) is not int or not 1 <= max_actions <= 2000:
            raise BridgeError("INVALID_LIMIT", "최대 입력 횟수는 1~2000회입니다.")
        self.keys = sorted(set(keys))
        self.deadline = clock() + max_seconds
        self.max_actions = max_actions
        self.stop_event = threading.Event()
        self.state = "ready"
        self.message = "GPT 플레이 준비"
        self.question = None
        self.answers, self.notes, self.frames, self.receipts = [], [], {}, {}
        self.latest = None
        self.action_count = 0
        self.epoch = 0
        self.candidate = None
        self.reviews, self.trials = [], []
        self.trial = None
        self.playback = None
        self.publication = None
        self.release_workers = []
        self._persist()

    def _persist(self):
        with self.state_lock:
            write_json(self.folder / "session.json", self.snapshot(internal=True))

    def snapshot(self, internal=False):
        with self.state_lock:
            value = {"session_id": self.instance_id, "goal": self.goal, "allowed_keys": self.keys,
                "state": self.state, "message": self.message, "question": copy.deepcopy(self.question),
                "answers": copy.deepcopy(self.answers), "notes": copy.deepcopy(self.notes),
                "action_count": self.action_count, "max_actions": self.max_actions,
                "latest_observation": self.latest, "candidate": copy.deepcopy(self.candidate),
                "reviews": copy.deepcopy(self.reviews), "trials": copy.deepcopy(self.trials),
                "trial": copy.deepcopy(self.trial), "publication": self.publication,
                "remaining_seconds": max(0, int(self.deadline - self.clock())),
                "game_paused": False, "full_playlist_replay_verified": False,
                "candidate_replay_successes": sum(bool(t.get("success")) for t in self.trials)}
            if internal:
                value.update(frames=copy.deepcopy(self.frames), receipts=copy.deepcopy(self.receipts))
            elif value["candidate"]:
                value["candidate"] = {k: value["candidate"][k] for k in
                    ("revision", "digest", "evidence", "remaining_issues")}
            return value

    def check(self, *, input_allowed=False, playback=False):
        if not self.connected():
            self.pause("GPT 연결 OFF: 다시 연결한 뒤 계속하세요.")
            raise BridgeError("CONNECTION_OFF", self.message)
        if self.stop_event.is_set() or self.state in {"stopped", "complete", "needs_review", "error"}:
            raise BridgeError("SESSION_STOPPED", self.message)
        if self.clock() >= self.deadline or self.action_count > self.max_actions:
            self.pause("시간 또는 입력 한도에 도달했습니다. 세션 기록은 보관합니다.")
            raise BridgeError("BUDGET_EXHAUSTED", self.message)
        if self.state in {"paused", "question"}:
            raise BridgeError("WAITING_FOR_USER", self.message)
        if input_allowed and self.state in {"verifying", "reviewing_trial"} and not playback:
            raise BridgeError("OBSERVER_ONLY", "후보 재생 중 GPT는 관찰만 할 수 있습니다.")

    def start(self):
        self.check()
        self.backend.validate()
        self.state = "exploring"
        self.message = "GPT가 선택한 게임 화면을 관찰합니다."
        self._persist()

    def pause(self, reason="사용자 일시정지"):
        with self.state_lock:
            if self.state in {"stopped", "complete"}:
                return
            if self.trial and self.trial.get("state") in {"running", "executed"}:
                self.trial.update(execution_ok=False, interrupted=True)
            if self.state != "question":
                self.state = "paused"
            self.message = reason
            self.epoch += 1
            self.latest = None
        if self.playback:
            self.playback.stop()
        # No key is held between requests. Release is best effort after in-flight I/O.
        self._release_async()
        self._persist()

    def _release_async(self):
        def release():
            with self.io_lock:
                try:
                    if not self.backend.release():
                        with self.state_lock:
                            self.message += " / 입력 장치 해제 확인 실패"
                except Exception:
                    with self.state_lock:
                        self.message += " / 입력 장치 해제 확인 실패"
        worker = threading.Thread(target=release, daemon=True, name="gpt-play-release")
        with self.state_lock:
            self.release_workers = [w for w in self.release_workers if w.is_alive()]
            self.release_workers.append(worker)
            worker.start()

    def stop(self):
        self.stop_event.set()
        self.pause("사용자 중지")
        self.state = "stopped"
        self._persist()

    def resume(self, answer=None):
        if self.stop_event.is_set() or self.state not in {"paused", "question"}:
            raise BridgeError("INVALID_STATE", "대기 중인 세션만 이어갈 수 있습니다.")
        if not self.connected():
            raise BridgeError("CONNECTION_OFF", "먼저 GPT 연결을 ON으로 바꾸세요.")
        if self.clock() >= self.deadline or self.action_count >= self.max_actions:
            raise BridgeError("BUDGET_EXHAUSTED", "한도가 끝났습니다. 기록을 확인하고 새 세션을 시작하세요.")
        if self.playback and self.playback.busy:
            raise BridgeError("PLAYBACK_BUSY", "이전 후보 재생이 멈추는 중입니다.")
        if self.question:
            answer = text(answer, "답변")
            self.answers.append({**self.question, "answer": answer})
            self.question = None
        self.epoch += 1
        self.latest = None
        self.state, self.message = "exploring", "현재 화면부터 다시 확인합니다."
        self._persist()

    def observe(self):
        self.check()
        with self.io_lock:
            self.check()
            epoch = self.epoch
            image, region = self.backend.capture()
            self.check()
            if epoch != self.epoch:
                raise BridgeError("STALE_OBSERVATION", "상태가 변경되어 화면을 폐기했습니다.")
        if len(self.frames) >= 600:
            raise BridgeError("LIMIT_REACHED", "화면 근거 600장 한도에 도달했습니다.")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        raw = buffer.getvalue()
        if len(raw) > 2_500_000:
            raise BridgeError("FRAME_TOO_LARGE", "선택 창이 너무 큽니다. 테스트 창 크기를 줄이세요.")
        if sum(f.get("bytes", 0) for f in self.frames.values()) + len(raw) > 128_000_000:
            raise BridgeError("LIMIT_REACHED", "세션 화면 근거 128MB 한도에 도달했습니다.")
        frame_id = new_id("frame_")
        publish_bytes(self.folder / "frames", frame_id + ".png", raw)
        metadata = {"frame_id": frame_id, "time": self.clock(), "epoch": epoch,
            "region": list(region), "sha256": __import__("hashlib").sha256(raw).hexdigest(),
            "action_count": self.action_count, "bytes": len(raw)}
        with self.state_lock:
            self.frames[frame_id] = metadata
            self.latest = frame_id
        self._persist()
        return {**metadata, "width": image.width, "height": image.height,
            "coordinate_space": "image pixels, relative to client area", "png_base64": base64.b64encode(raw).decode()}

    def _frame(self, frame_id, fresh=False):
        frame = self.frames.get(frame_id)
        if not frame:
            raise BridgeError("EVIDENCE_REQUIRED", "이 세션에서 관찰한 화면 ID가 필요합니다.")
        if fresh and (frame_id != self.latest or frame["epoch"] != self.epoch
                      or self.clock() - frame["time"] > 20 or frame["action_count"] != self.action_count):
            raise BridgeError("STALE_OBSERVATION", "화면이 오래됐습니다. 다시 관찰하고 판단하세요.")
        return frame

    def read_frame(self, frame_id):
        self.check()
        metadata = self._frame(frame_id)
        path = within(self.folder / "frames" / (frame_id + ".png"), self.folder / "frames")
        data = path.read_bytes()
        if __import__("hashlib").sha256(data).hexdigest() != metadata["sha256"]:
            raise BridgeError("EVIDENCE_CHANGED", "저장된 관찰 장면이 변경되었습니다.")
        return {**metadata, "historical_not_current": True, "png_base64": base64.b64encode(data).decode()}

    def act(self, observation_id, request_id, action, purpose):
        self.check(input_allowed=True)
        request_id = text(request_id, "요청 ID", 100)
        purpose = text(purpose, "행동 목적")
        fingerprint = digest({"observation_id": observation_id, "action": action, "purpose": purpose})
        old = self.receipts.get(request_id)
        if old:
            if old["digest"] != fingerprint:
                raise BridgeError("REQUEST_REUSED", "같은 ID로 다른 행동을 보낼 수 없습니다.")
            return copy.deepcopy(old)
        if self.action_count >= self.max_actions:
            self.pause("최대 입력 횟수에 도달했습니다.")
            raise BridgeError("BUDGET_EXHAUSTED", self.message)
        if not isinstance(action, dict):
            raise BridgeError("INVALID_ACTION", "행동 형식이 잘못됐습니다.")
        kind = action.get("kind")
        if kind == "key":
            if set(action) != {"kind", "key"} or action.get("key") not in self.keys:
                raise BridgeError("KEY_DENIED", "허용 목록의 단일 키만 사용할 수 있습니다.")
        elif kind == "click":
            if set(action) != {"kind", "x", "y"} or any(type(action.get(k)) is not int for k in ("x", "y")):
                raise BridgeError("INVALID_ACTION", "클릭에는 정수 x, y만 필요합니다.")
        elif kind == "wait":
            if set(action) != {"kind", "seconds"} or not number(action.get("seconds"), .05, 2):
                raise BridgeError("INVALID_ACTION", "대기는 0.05~2초입니다.")
        else:
            raise BridgeError("ACTION_DENIED", "단일 키, 왼쪽 클릭, 짧은 대기만 허용합니다.")
        frame = self._frame(observation_id, fresh=True)
        rect = frame["region"]
        if kind == "click" and not (0 <= action["x"] < rect[2] - rect[0] and 0 <= action["y"] < rect[3] - rect[1]):
            raise BridgeError("OUTSIDE_TARGET", "선택 창 안의 좌표만 클릭할 수 있습니다.")
        receipt = {"digest": fingerprint, "request_id": request_id, "state": "unknown",
            "action": copy.deepcopy(action), "purpose": purpose, "observation_id": observation_id}
        with self.io_lock:
            self.check(input_allowed=True)
            self._frame(observation_id, fresh=True)
            self.backend.validate(rect)
            with self.state_lock:
                self.receipts[request_id] = receipt
                self.action_count += 1
            self._persist()  # Write-ahead: uncertain delivery must never be resent blindly.
            try:
                if kind == "wait":
                    end = self.clock() + action["seconds"]
                    while self.clock() < end:
                        self.check(input_allowed=True)
                        self.stop_event.wait(min(.05, end - self.clock()))
                    ok = True
                else:
                    ok = self.backend.send(action, rect, lambda: self.check(input_allowed=True))
                if not ok:
                    raise BridgeError("DELIVERY_UNKNOWN", "입력 완료를 확인하지 못했습니다. 재전송하지 말고 화면을 확인하세요.")
                receipt["state"] = "sent_not_game_success"
            except Exception:
                self.pause("입력 결과 불확실: 화면과 장치 연결을 확인하세요.")
                raise
            finally:
                self.latest = None
                self._persist()
        return copy.deepcopy(receipt)

    def ask(self, observation_id, question, context):
        self.check()
        self._frame(observation_id, fresh=True)
        if self.state in {"verifying", "reviewing_trial"}:
            raise BridgeError("OBSERVER_ONLY", "검수 중 개입하면 해당 검수는 중단해야 합니다.")
        if len(self.answers) >= 40:
            raise BridgeError("LIMIT_REACHED", "질문 한도에 도달했습니다.")
        self.question = {"id": new_id("question_"), "observation_id": observation_id,
            "question": text(question, "질문"), "context": text(context, "관찰·불확실한 점")}
        self.state = "question"
        self.pause("사용자 답변 대기: 게임 자체는 계속 진행될 수 있습니다.")
        return {"waiting_for_user": True, "instruction": "End this turn now. WinCro restarts you after the answer. Do not poll."}

    def note(self, observation_id, observation, purpose, expected_result):
        self.check()
        self._frame(observation_id)
        if len(self.notes) >= 500:
            raise BridgeError("LIMIT_REACHED", "관찰 기록 한도에 도달했습니다.")
        self.notes.append({"frame_id": observation_id, "observation": text(observation, "관찰"),
            "purpose": text(purpose, "목적"), "expected_result": text(expected_result, "예상 결과")})
        self._persist()
        return {"saved_and_reloaded": True}

    def dispatch(self, method, arguments):
        methods = {"play_observe": self.observe, "play_frame": self.read_frame, "play_act": self.act, "play_status": self.snapshot,
            "play_question": self.ask, "play_note": self.note}
        if method not in methods:
            raise BridgeError("TOOL_DENIED", "허용되지 않은 GPT 플레이 도구입니다.")
        if method == "play_status" and arguments:
            raise BridgeError("INVALID_ARGUMENT", "상태 조회 인자는 없습니다.")
        return methods[method](**arguments)
