"""Bounded live tools with evidence, durable receipts and explicit human questions.

All controls and storage are injected. Importing this module cannot send input.
"""

import base64
import copy
import io
from pathlib import Path
import threading
import time

from ..agent_bridge.contracts import BridgeError, digest, encoded, number
from ..agent_bridge.storage import new_id, publish_bytes, write_json, within
from .diagnostics import PlayTrace, action_text, concise, error_text
from .key_policy import SAFE_KEYS, reserved_keys
from .actions import validate_action, input_cost


TOOLS = {"connection_status", "play_observe", "play_frame", "play_act", "play_status", "play_question",
         "play_note", "play_candidate", "play_review", "play_verify", "play_trial_result", "play_finish"}
TOOLS.update({"play_experience", "play_memory", "play_memory_frame", "play_attempt",
              "play_behavior", "play_run_behavior", "play_reconcile"})


def text(value, label, maximum=3000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise BridgeError("INVALID_ARGUMENT", label + " 내용을 입력하세요.")
    return value.strip()


class PlaySession:
    def __init__(self, folder, goal, keys, backend, connected, *, max_seconds=900,
                 max_actions=500, clock=time.monotonic, memory=None, task_id=None):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.instance_id = new_id("play_")
        self.trace = PlayTrace(self.folder, self.instance_id, clock=clock)
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
        self.input_cancelled = threading.Event()
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
        self.release_confirmed = threading.Event()
        self.release_confirmed.set()
        self.commands = []
        self.storage_failed = False
        self.learning = None
        self.behavior_receipts = {}
        self._ui_cache = None
        if memory is not None:
            from .learning import Learning
            self.learning = Learning(memory, task_id, self)
            checkpoint = self.learning.context.get("checkpoint") or {}
            self.commands = copy.deepcopy(checkpoint.get("commands", []))
            self.answers = copy.deepcopy(checkpoint.get("answers", []))
            previous_id = checkpoint.get("session_id")
            if previous_id:
                previous = memory.get(task_id, "session", previous_id)
                saved = previous.get("candidate")
                if saved:
                    from .behaviors import ACTION_TYPE, validate_program
                    if digest(saved["plan"]) != saved["digest"] or saved.get("assets"):
                        raise BridgeError("CANDIDATE_CHANGED", "저장된 경험 후보의 무결성을 확인하지 못했습니다. 원본은 보존했습니다.")
                    for rule in saved["plan"]["initial_rules"]:
                        if rule["action_type"] != ACTION_TYPE:
                            raise BridgeError("CANDIDATE_UNSUPPORTED", "경험 이어하기는 GPT 검수 구간 후보만 지원합니다.")
                        validate_program(rule["gpt_play_config"])
                    self.candidate = copy.deepcopy(saved)
                    self.reviews = copy.deepcopy(previous.get("reviews", []))
                    self.trials = copy.deepcopy(previous.get("trials", []))
                    self.publication = copy.deepcopy(previous.get("publication"))
        self._initial_answers = len(self.answers)
        self._initial_commands = len(self.commands)
        self._persist()
        self.trace.emit("session_ready", "세션 생성 완료 · 아직 화면 관찰과 입력은 시작하지 않았습니다.")

    def _persist(self):
        try:
            value = self.snapshot(internal=True)
            if len(encoded(value)) > 3_800_000:
                raise BridgeError("SESSION_STORAGE_LIMIT", "세션 기록 크기 한도입니다. 마지막 정상 저장을 보존하며 입력을 중단합니다.")
            write_json(self.folder / "session.json", value)
            if self.learning:
                self.learning.persist(value)
        except Exception:
            # Never continue sending inputs after a failed write-ahead/outcome save.
            self.storage_failed = True
            self.latest = None
            self._release_async()
            raise

    def snapshot(self, internal=False):
        with self.state_lock:
            value = {"session_id": self.instance_id, "goal": self.goal, "allowed_keys": self.keys,
                "device_capabilities": copy.deepcopy(getattr(self.backend, "capabilities", {})),
                "state": self.state, "message": self.message, "question": copy.deepcopy(self.question),
                "answers": copy.deepcopy(self.answers), "commands": copy.deepcopy(self.commands), "notes": copy.deepcopy(self.notes),
                "action_count": self.action_count, "max_actions": self.max_actions,
                "latest_observation": self.latest, "candidate": copy.deepcopy(self.candidate),
                "reviews": copy.deepcopy(self.reviews), "trials": copy.deepcopy(self.trials),
                "trial": copy.deepcopy(self.trial), "publication": self.publication,
                "remaining_seconds": max(0, int(self.deadline - self.clock())),
                "game_paused": False, "full_playlist_replay_verified": False,
                "candidate_replay_successes": sum(bool(t.get("success")) for t in self.trials)}
            if internal:
                value.update(frames=copy.deepcopy(self.frames), receipts=copy.deepcopy(self.receipts),
                             behavior_receipts=copy.deepcopy(self.behavior_receipts))
            elif value["candidate"]:
                current = value["candidate"]
                value["candidate"] = {k: current[k] for k in
                    ("revision", "digest", "evidence", "remaining_issues")}
                value["candidate"]["compiled_steps"] = [
                    {key: rule.get(key) for key in ("rule_id", "action_type", "description", "action_keys",
                        "target_image", "search_region", "confidence", "wait_after", "repeat_count", "repeat_delay")}
                    for rule in current["plan"]["initial_rules"]]
                for summary, rule in zip(value["candidate"]["compiled_steps"], current["plan"]["initial_rules"]):
                    if rule.get("action_type") == "gpt_play_behavior":
                        program = rule["gpt_play_config"]
                        summary["guarded_program"] = {**program, "steps": [
                            {**step, **{key: {k: v for k, v in step[key].items() if k != "png"}
                                        for key in ("before", "after")}} for step in program["steps"]]}
            if self.learning:
                value["learning"] = self.learning.summary()
            return value

    def ui_snapshot(self):
        # UI polling must not repeatedly copy all evidence and native rule fields.
        if not self.state_lock.acquire(blocking=False):
            return self._ui_cache
        try:
            from .candidate import success_streak
            phase, quiet = self.trace.status()
            phase = {"paused": "일시정지", "question": "답변 대기", "stopped": "중지"}.get(self.state, phase)
            self._ui_cache = {"state": self.state, "message": self.message, "question": copy.deepcopy(self.question),
                "action_count": self.action_count, "max_actions": self.max_actions,
                "phase": phase, "quiet_seconds": quiet, "frame_count": len(self.frames),
                "publication": copy.deepcopy(self.publication), "trial_count": len(self.trials),
                "candidate": {"revision": self.candidate["revision"]} if self.candidate else None,
                "learning": self.learning.summary() if self.learning else None,
                "verification": {"revision": self.candidate["revision"],
                    "required": 10 if self.learning else 3,
                    "successes": success_streak(self),
                    "steps": list(self.candidate.get("ui_steps", [])),
                    "remaining": list(self.candidate["remaining_issues"])} if self.candidate else None}
            return self._ui_cache
        finally:
            self.state_lock.release()

    def model_context(self):
        value = self.snapshot()
        # Keep user authority intact, but don't resend the whole observation diary.
        value["notes"] = value["notes"][-8:]
        value["trials"] = value["trials"][-10:]
        return value

    def check(self, *, input_allowed=False, playback=False):
        if self.stop_event.is_set() or self.storage_failed:
            raise BridgeError("SESSION_STOPPED", "중지 또는 저장 실패 상태입니다. 확인 후 새 실행으로 이어가세요.")
        if self.input_cancelled.is_set():
            raise BridgeError("WAITING_FOR_USER", self.message)
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
        if input_allowed and self.learning and self.learning.pending:
            raise BridgeError("RECONCILIATION_REQUIRED", "과거 입력 결과가 불확실합니다. 새 화면을 확인해 play_reconcile로 정리한 뒤 입력하세요.")

    def start(self):
        self.check()
        self.backend.validate()
        with self.state_lock:
            self.check()
            self.state = "exploring"
            self.message = "GPT가 선택한 게임 화면을 관찰합니다."
            self._persist()
        self.trace.emit("session_start", self.message, phase="GPT 실행 준비")

    def pause(self, reason="사용자 일시정지"):
        # Signal hardware cancellation before waiting for a slow disk/state lock.
        self.input_cancelled.set()
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
            self.release_confirmed.clear()
        self.trace.emit("paused", reason, level="warning", phase="답변 대기" if self.state == "question" else "일시정지")
        if self.playback:
            self.playback.stop()
        # No key is held between requests. Release is best effort after in-flight I/O.
        self._release_async()
        self._persist()

    def _release_async(self):
        def release():
            with self.io_lock:
                try:
                    if self.backend.release():
                        self.release_confirmed.set()
                    else:
                        self.release_confirmed.clear()
                        with self.state_lock:
                            self.message += " / 입력 장치 해제 확인 실패"
                        self.trace.emit("release_failed", "입력 장치 해제 확인 실패", level="error")
                except Exception as exc:
                    self.release_confirmed.clear()
                    with self.state_lock:
                        self.message += " / 입력 장치 해제 확인 실패"
                    self.trace.emit("release_failed", error_text(exc), level="error")
        worker = threading.Thread(target=release, daemon=True, name="gpt-play-release")
        with self.state_lock:
            self.release_workers = [w for w in self.release_workers if w.is_alive()]
            self.release_workers.append(worker)
            worker.start()

    def stop(self):
        self.stop_event.set()
        try:
            self.pause("사용자 중지")
        finally:
            # A failed disk write must never leave a stopped process resumable.
            with self.state_lock:
                self.state = "stopped"
            try:
                self._persist()
            finally:
                self.trace.emit("stopped", "사용자 중지 · 이전 실패 원인은 이 로그에 보존됩니다.", phase="중지")

    def resume(self, answer=None):
        with self.state_lock:
            if self.stop_event.is_set() or self.state not in {"paused", "question"}:
                raise BridgeError("INVALID_STATE", "대기 중인 세션만 이어갈 수 있습니다.")
            if not self.connected():
                raise BridgeError("CONNECTION_OFF", "먼저 GPT 연결을 ON으로 바꾸세요.")
            if self.clock() >= self.deadline or self.action_count >= self.max_actions:
                raise BridgeError("BUDGET_EXHAUSTED", "한도가 끝났습니다. 기록을 확인하고 새 세션을 시작하세요.")
            if self.playback and self.playback.busy:
                raise BridgeError("PLAYBACK_BUSY", "이전 후보 재생이 멈추는 중입니다.")
            if not self.input_released:
                raise BridgeError("INPUT_RELEASE_PENDING", "이전 입력 해제 확인을 기다리고 있습니다. 실패 로그가 있으면 장치를 먼저 확인하세요.")
            previous = self.state, self.message, self.question, list(self.answers), self.epoch, self.latest
            if self.question:
                answer = text(answer, "답변")
                self.answers.append({**self.question, "answer": answer})
                self.question = None
            self.epoch += 1
            self.latest = None
            self.state, self.message = "exploring", "현재 화면부터 다시 확인합니다."
            try:
                self._persist()
            except Exception:
                self.state, self.message, self.question, self.answers, self.epoch, self.latest = previous
                raise
            self.input_cancelled.clear()
        self.trace.emit("resumed", "사용자 계속 요청 · 새 화면 확인 후에만 입력합니다.", phase="게임 창 준비")

    @property
    def input_released(self):
        if not self.state_lock.acquire(blocking=False):
            return False
        try:
            return (self.release_confirmed.is_set() and not self.io_lock.locked()
                    and not any(worker.is_alive() for worker in self.release_workers)
                    and not (self.playback and self.playback.busy))
        finally:
            self.state_lock.release()

    def add_command(self, command):
        command = text(command, "중간 명령", 2000)
        with self.state_lock:
            if self.state not in {"paused", "question"} or self.stop_event.is_set():
                raise BridgeError("INVALID_STATE", "개입 또는 일시정지 후 명령을 전달하세요.")
            if len(self.commands) - self._initial_commands >= 100:
                raise BridgeError("LIMIT_REACHED", "중간 명령은 세션당 100개까지 보관합니다.")
            previous = self.reviews, self.trials, self.trial
            self.commands.append({"command": command, "action_count": self.action_count,
                                  "time": self.clock()})
            # New instructions invalidate verification of the previous intent.
            self.reviews, self.trials, self.trial = [], [], None
            try:
                self._persist()
            except Exception:
                self.commands.pop()
                self.reviews, self.trials, self.trial = previous
                raise
        self.trace.emit("user_command", "사용자 중간 명령: " + concise(command))

    def observe(self):
        self.check()
        self.trace.emit("observe_begin", "선택한 게임 창의 현재 화면을 읽습니다.", phase="화면 관찰")
        with self.io_lock:
            self.check()
            epoch = self.epoch
            observed_at, observed_actions = self.clock(), self.action_count
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
        metadata = {"frame_id": frame_id, "time": observed_at, "epoch": epoch,
            "region": list(region), "sha256": __import__("hashlib").sha256(raw).hexdigest(),
            "action_count": observed_actions, "bytes": len(raw)}
        if self.learning:
            try:
                self.learning.store.save_frame(self.learning.task, metadata, raw)
            except Exception:
                self.storage_failed = True
                self._release_async()
                raise
        with self.state_lock:
            self.check()
            if epoch != self.epoch or observed_actions != self.action_count:
                raise BridgeError("STALE_OBSERVATION", "저장 중 실행 상태가 바뀐 관찰은 입력에 사용하지 않습니다.")
            previous = next(reversed(self.frames.values()), None) if self.frames else None
            self.frames[frame_id] = metadata
            self.latest = frame_id
        self._persist()
        change = "첫 화면" if previous is None else "이전 관찰과 동일" if previous["sha256"] == metadata["sha256"] else "이전 관찰과 화면 변화 있음"
        self.trace.emit("observe_done", f"화면 {len(self.frames)}장 확인: {image.width}x{image.height} 범위={list(region)} · {change} "
                        "· 캐릭터 이동·목표 달성 여부는 GPT 확인 필요", phase="GPT 판단 대기")
        return {**metadata, "width": image.width, "height": image.height,
            "coordinate_space": "image pixels, relative to client area", "png_base64": base64.b64encode(raw).decode()}

    def _frame(self, frame_id, fresh=False):
        frame = self.frames.get(frame_id)
        if not frame and not fresh and self.learning:
            return self.learning.store.get(self.learning.task, "frame", frame_id)
        if not frame:
            raise BridgeError("EVIDENCE_REQUIRED", "이 세션에서 관찰한 화면 ID가 필요합니다.")
        if fresh and (frame_id != self.latest or frame["epoch"] != self.epoch
                      or self.clock() - frame["time"] > 20 or frame["action_count"] != self.action_count):
            raise BridgeError("STALE_OBSERVATION", "화면이 오래됐습니다. 다시 관찰하고 판단하세요.")
        return frame

    def read_frame(self, frame_id):
        self.check()
        if frame_id not in self.frames and self.learning:
            return self.learning.frame(frame_id)
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
            self.trace.emit("duplicate_request", f"이미 처리한 요청 {request_id}: 재전송하지 않음 · 상태={old['state']}")
            return copy.deepcopy(old)
        action = validate_action(action, self.keys)
        cost = input_cost(action)
        if self.action_count + cost > self.max_actions:
            self.pause("최대 입력 횟수에 도달했습니다.")
            raise BridgeError("BUDGET_EXHAUSTED", self.message)
        kind = action.get("kind")
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
                self.action_count += cost
            self._persist()  # Write-ahead: uncertain delivery must never be resent blindly.
            target = f" 화면좌표({rect[0] + action['x']}, {rect[1] + action['y']})" if kind == "click" else ""
            self.trace.emit("action_begin", f"요청 {self.action_count}: {action_text(action)}{target} · 목적={concise(purpose, 250)}", phase="입력 수행")
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
                self.trace.emit("action_sent", f"{action_text(action)} · " + ("대기 완료" if kind == "wait" else "장치 전송 완료")
                    + " · 게임 반응은 아직 미확인, 다음 화면으로 확인합니다.", phase="다음 화면 확인 대기")
            except Exception as exc:
                if kind == "hold" and getattr(exc, "code", "") == "TIMED_INPUT_UNSUPPORTED":
                    receipt["state"] = "not_sent"
                if self.learning and receipt["state"] == "unknown":
                    self.learning.pending.append(copy.deepcopy(receipt))
                self.trace.emit("action_failed", action_text(action) + " · " + error_text(exc), level="error")
                self.pause("동작 확인 실패: " + error_text(exc) + " · 원인을 확인한 뒤 계속을 누르세요.")
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
        if len(self.answers) - self._initial_answers >= 40:
            raise BridgeError("LIMIT_REACHED", "질문 한도에 도달했습니다.")
        self.question = {"id": new_id("question_"), "observation_id": observation_id,
            "question": text(question, "질문"), "context": text(context, "관찰·불확실한 점")}
        self.state = "question"
        self.trace.emit("question", "GPT 질문: " + self.question["question"], phase="답변 대기")
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
        self.trace.emit("observation_note", "GPT 관찰: " + concise(observation, 250) + " · 목적: " + concise(purpose, 250)
                        + " · 예상 결과: " + concise(expected_result, 250))
        return {"saved_and_reloaded": True}

    def dispatch(self, method, arguments):
        if method in {"play_experience", "play_memory", "play_memory_frame", "play_attempt", "play_behavior", "play_run_behavior", "play_reconcile"}:
            self.check()
            if not self.learning:
                raise BridgeError("MEMORY_UNAVAILABLE", "경험 저장소가 연결되지 않았습니다.")
            from .behaviors import author, run_behavior
            learning_methods = {"play_experience": self.learning.record,
                "play_memory": lambda offset=0: self.learning.store.context(self.learning.task, offset),
                "play_memory_frame": self.learning.frame, "play_attempt": self.learning.attempt_event,
                "play_reconcile": self.learning.reconcile,
                "play_behavior": lambda **kw: author(self, **kw),
                "play_run_behavior": lambda **kw: run_behavior(self, **kw)}
            try:
                return learning_methods[method](**arguments)
            except Exception as exc:
                if isinstance(exc, OSError) or getattr(exc, "code", "") in {
                        "MEMORY_IO", "MEMORY_FULL", "SAVE_STATE_UNKNOWN", "EVIDENCE_CHANGED"}:
                    self.storage_failed = True
                    self.input_cancelled.set()
                    self._release_async()
                raise
        methods = {"play_observe": self.observe, "play_frame": self.read_frame, "play_act": self.act, "play_status": self.snapshot,
            "play_question": self.ask, "play_note": self.note}
        if method not in methods:
            raise BridgeError("TOOL_DENIED", "허용되지 않은 GPT 플레이 도구입니다.")
        if method == "play_status" and arguments:
            raise BridgeError("INVALID_ARGUMENT", "상태 조회 인자는 없습니다.")
        return methods[method](**arguments)
