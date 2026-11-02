"""Evidence-backed attempts and checkpointing; GPT supplies interpretations."""

import base64
import copy
import time

from ..agent_bridge.contracts import BridgeError, digest
from ..agent_bridge.storage import new_id


def required(value, limit=2000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise BridgeError("INVALID_EXPERIENCE", "근거·목적·전략은 비어 있지 않은 짧은 문장으로 기록하세요.")
    return value.strip()


class Learning:
    def __init__(self, store, task, session):
        self.store, self.task, self.session = store, task, session
        self.attempt = None
        self.attempt_count = 0
        self.context = store.context(task)
        previous_checkpoint = self.context.get("checkpoint") or {}
        self.pending = copy.deepcopy(previous_checkpoint.get("uncertain_requests", []))
        self.resolved = set()
        # An unclosed attempt from a previous process is not a game defeat.
        for previous in self.context["attempts"]:
            if previous["status"] == "running":
                previous.update(status="incomplete", result="이전 실행 결과 미기록", ended=time.time())
                store.put(task, "attempt", previous["attempt_id"], previous)

    @property
    def revision(self):
        return digest({"goal": self.session.goal, "commands": self.session.commands})

    def persist(self, snapshot):
        uncertain = list(self.pending)
        for ident, receipt in snapshot["receipts"].items():
            if receipt["state"] == "unknown" and ident not in self.resolved:
                value = {**receipt, "request_id": ident}
                if not any(r.get("request_id") == ident for r in uncertain):
                    uncertain.append(value)
        for ident, receipt in snapshot.get("behavior_receipts", {}).items():
            if receipt["status"] == "unknown" and ident not in self.resolved:
                if not any(r.get("request_id") == ident for r in uncertain):
                    uncertain.append({**receipt, "request_id": ident, "kind": "behavior"})
        checkpoint = {
            "session_id": self.session.instance_id, "state": snapshot["state"],
            "message": snapshot["message"], "latest_observation": snapshot["latest_observation"],
            "attempt": copy.deepcopy(self.attempt), "goal_revision": self.revision,
            "commands": snapshot["commands"], "answers": snapshot["answers"],
            "action_count": snapshot["action_count"], "remaining_seconds": snapshot["remaining_seconds"],
            "uncertain_requests": uncertain,
            "resume_requires_fresh_observation": True}
        self.store.put_many(self.task, [("session", self.session.instance_id, snapshot),
                                       ("checkpoint", "latest", checkpoint)])

    def reconcile(self, request_id, observation_id, result, observation):
        s = self.session
        s.check()
        s._frame(observation_id, fresh=True)
        if result not in {"observed_applied", "observed_not_applied", "unknown"}:
            raise BridgeError("INVALID_EXPERIENCE", "실행됨·실행되지 않음·불확실을 화면 근거로 구분하세요.")
        if not any(r.get("request_id") == request_id for r in self.pending):
            raise BridgeError("UNKNOWN_REQUEST", "재확인할 불확실 요청이 없습니다.")
        record = {"request_id": request_id, "frame_id": observation_id, "result": result,
                  "observation": required(observation), "automatically_resent": False}
        self.store.event(self.task, "reconciliation", record)
        if result != "unknown":
            self.pending = [r for r in self.pending if r.get("request_id") != request_id]
            self.resolved.add(request_id)
        s._persist()
        return record

    def frame(self, ident):
        metadata, raw = self.store.read_frame(self.task, ident)
        return {**metadata, "historical_not_current": True, "png_base64": base64.b64encode(raw).decode()}

    def attempt_event(self, event, observation_id, stage, strategy, outcome="", observation="", hypothesis="", next_experiment=""):
        s = self.session
        s.check(input_allowed=event == "start")
        frame = s._frame(observation_id, fresh=True)
        stage, strategy = required(stage, 200), required(strategy)
        if event == "start":
            if self.attempt and self.attempt["status"] == "running":
                raise BridgeError("ATTEMPT_ACTIVE", "현재 시도의 결과를 먼저 기록하세요.")
            if self.attempt_count >= 10:
                raise BridgeError("ATTEMPT_LIMIT", "이번 실행의 10회 시도 한도에 도달했습니다. 기록 확인 후 이어가세요.")
            value = {"attempt_id": new_id("attempt_"), "session_id": s.instance_id,
                "status": "running", "stage": stage, "strategy": strategy, "goal_revision": self.revision,
                "started": time.time(), "start_frame": observation_id, "start_sha256": frame["sha256"],
                "start_action_count": s.action_count}
            self.store.put(self.task, "attempt", value["attempt_id"], value)
            self.attempt, self.attempt_count = value, self.attempt_count + 1
        elif event == "result":
            if not self.attempt or self.attempt["status"] != "running":
                raise BridgeError("NO_ATTEMPT", "시작된 시도가 없습니다.")
            if outcome not in {"success", "defeat", "technical_error", "incomplete"}:
                raise BridgeError("INVALID_EXPERIENCE", "게임 성공·패배·기술 오류·미완료를 구분하세요.")
            if outcome == "success" and (s.action_count <= self.attempt["start_action_count"] or
                                          frame["sha256"] == self.attempt["start_sha256"]):
                raise BridgeError("OUTCOME_UNCONFIRMED", "입력과 새 결과 화면 없이 성공으로 기록할 수 없습니다.")
            value = {**self.attempt, "status": outcome, "stage": stage, "strategy": strategy,
                "observation": required(observation), "hypothesis": str(hypothesis)[:2000],
                "hypothesis_is_proven": False, "next_experiment": str(next_experiment)[:2000],
                "end_frame": observation_id, "ended": time.time()}
            self.store.put(self.task, "attempt", value["attempt_id"], value)
            self.attempt = value
        else:
            raise BridgeError("INVALID_EXPERIENCE", "시도 시작(start) 또는 결과(result)를 지정하세요.")
        s._persist()
        s.trace.emit("attempt", f"시도 {self.attempt_count}/10 · {stage} · {value['status']} · {strategy}")
        return copy.deepcopy(value)

    def record(self, observation_id, kind, observation, hypothesis="", next_experiment="", stage=""):
        s = self.session
        s.check()
        s._frame(observation_id)
        if kind not in {"fact", "hypothesis", "experiment", "recovery", "outcome"}:
            raise BridgeError("INVALID_EXPERIENCE", "경험 유형을 구분하세요.")
        value = {"frame_id": observation_id, "kind": kind, "observation": required(observation),
            "hypothesis": str(hypothesis)[:2000], "next_experiment": str(next_experiment)[:2000],
            "stage": str(stage)[:200], "attempt_id": (self.attempt or {}).get("attempt_id"),
            "goal_revision": self.revision, "session_id": s.instance_id, "proven_cause": False}
        self.store.event(self.task, kind, value)
        s.trace.emit("experience", "경험 저장 · " + kind + " · " + value["observation"])
        return {"saved_and_reloaded": True}

    def summary(self):
        return {"task_id": self.task, "attempt_count": self.attempt_count,
                "attempt": copy.deepcopy(self.attempt), "goal_revision": self.revision,
                "uncertain_requests": copy.deepcopy(self.pending)}
