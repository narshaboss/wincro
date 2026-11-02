"""App-owned experience admission and goal/verification separation."""

import copy
import time
import threading

from ..agent_bridge.contracts import BridgeError, digest
from ..agent_bridge.storage import new_id
from .learning import required


class LivePolicy:
    def __init__(self, session):
        self.session = session
        self.retrieval = None
        self.review = None
        self.stage = "start"
        self.goal = None
        self.verification_permits = 0
        self.metrics = {}
        self.last_tool_end = None
        self.navigation = None
        self.metrics_lock = threading.RLock()

    def measure(self, name, milliseconds):
        with self.metrics_lock:
            values = self.metrics.setdefault(name, [])
            values.append(round(max(0, milliseconds), 1))
            del values[:-64]

    def snapshot(self):
        with self.metrics_lock:
            metrics = {k: {"last": v[-1], "mean": round(sum(v) / len(v), 1), "samples": len(v)}
                       for k, v in self.metrics.items() if v}
        return {"stage": self.stage, "experience_review": copy.deepcopy(self.review),
                "goal": copy.deepcopy(self.goal), "verification_permits": self.verification_permits,
                "navigation": copy.deepcopy(self.navigation),
                "timings_ms": metrics}

    def invalidate(self):
        self.review = None
        self.retrieval = None

    def recall(self, stage="start", offset=0):
        s = self.session
        stage = required(stage, 200)
        if self.stage != stage:
            self.invalidate()
        self.stage = stage
        context = s.learning.store.relevant(s.learning.task, stage, offset)
        ident = new_id("recall_")
        self.retrieval = {"id": ident, "epoch": s.epoch, "revision": s.learning.revision,
                          "stage": stage, "records": context["records"]}
        return {**context, "retrieval_id": ident, "review_required": True}

    def use(self, observation_id, retrieval_id, references, decision):
        s, recalled = self.session, self.retrieval
        s.check()
        s._frame(observation_id, fresh=True)
        if (not recalled or recalled["id"] != retrieval_id or recalled["epoch"] != s.epoch
                or recalled["revision"] != s.learning.revision):
            raise BridgeError("MEMORY_REVIEW_REQUIRED", "현재 단계의 경험을 먼저 조회하세요.")
        available = {r["reference"] for r in recalled["records"]}
        if (not isinstance(references, list) or len(references) > 40
                or any(not isinstance(r, str) or r not in available for r in references)):
            raise BridgeError("INVALID_MEMORY_REFERENCE", "이번 조회에 포함된 경험 ID만 인용하세요.")
        if available and not references:
            raise BridgeError("MEMORY_REVIEW_REQUIRED", "검토한 경험 ID와 적용 또는 미적용 이유가 필요합니다.")
        record = {"retrieval_id": retrieval_id, "references": references, "decision": required(decision),
                  "stage": self.stage, "frame_id": observation_id, "epoch": s.epoch,
                  "revision": s.learning.revision, "time": time.time()}
        s.learning.store.event(s.learning.task, "memory_review", record)
        self.review = record
        s._persist()
        s.trace.emit("memory_review", "경험 검토 · " + self.stage + " · " + record["decision"])
        return {"saved_and_reloaded": True, "references": references, "understanding_not_proven": True}

    def admit(self, *, playback=False):
        s = self.session
        if self.goal and not playback:
            raise BridgeError("GOAL_INPUT_FROZEN", "목표 달성 후 입력은 중단되었습니다. 기록과 플랜 작성만 진행하세요.")
        if playback:
            return
        if (not self.review or self.review["epoch"] != s.epoch
                or self.review["revision"] != s.learning.revision):
            raise BridgeError("MEMORY_REVIEW_REQUIRED", "play_memory 조회 후 play_use_experience로 적용 근거를 기록하세요.")

    def achieved(self, observation_id, observation):
        s = self.session
        s.check()
        s._frame(observation_id, fresh=True)
        record = {"frame_id": observation_id, "observation": required(observation),
                  "action_count": s.action_count, "time": time.time(), "verified_by": "GPT"}
        # Freeze BEFORE disk I/O; a failed save must never allow another click.
        self.goal = record
        s.latest = None
        s._release_async()
        s.learning.store.event(s.learning.task, "goal_achieved", record)
        s._persist()
        s.trace.emit("goal_achieved", "목표 달성 · 게임 입력 종료 · 기록 기반 플랜 작성으로 전환")
        return {"input_frozen": True, "plan_complete": False,
                "instruction": "Author/review from evidence only. Independent replay requires owner permission."}

    def authorize_verification(self):
        s = self.session
        if not self.goal or not s.input_released or not s.candidate:
            raise BridgeError("VERIFICATION_NOT_READY", "목표 달성·입력 해제·후보 작성 후 검수 재생을 허용하세요.")
        self.verification_permits = 1
        s._persist()

    def consume_verification(self):
        if not self.goal or self.verification_permits != 1:
            raise BridgeError("OWNER_REPLAY_REQUIRED", "실제 재입장은 플랜 검수 탭의 '검수 재생 1회 허용'을 눌러야 합니다.")
        self.verification_permits = 0
        self.session._persist()
