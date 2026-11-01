"""Candidate-only adapter around native rule execution; GPT is observer-only.

Only the small compiler-owned image-click/key subset can enter here. Special
mode, arbitrary imported plans and application-wide playback are not routed here.
"""

import threading

from ..agent_bridge.contracts import BridgeError, digest
from ..agent_bridge.storage import new_id
from . import candidate
from .session import text
from .diagnostics import action_text, error_text


class CandidatePlayback:
    def __init__(self, session, executor_factory=None):
        self.session = session
        self.worker = self.executor = None
        self.executor_factory = executor_factory or native_executor

    @property
    def busy(self):
        return bool(self.worker and self.worker.is_alive())

    def start(self, revision, start_frame_id, fresh_round_evidence):
        s = self.session
        s.check(input_allowed=True)
        if self.busy or s.state == "verifying":
            raise BridgeError("PLAYBACK_BUSY", "이미 후보 재생 중입니다.")
        if not candidate.ready(s) or revision != s.candidate["revision"]:
            raise BridgeError("REVIEW_REQUIRED", "현재 후보의 미해결 항목과 양방향 대조부터 끝내세요.")
        candidate.verify_assets(s)
        frame = s._frame(start_frame_id, fresh=True)
        if s.trials and frame["time"] < s.trials[-1]["reviewed_at"]:
            raise BridgeError("FRESH_ROUND_REQUIRED", "이전 결과 확인 이후 새 시작 화면을 관찰하세요.")
        trial = {"trial_id": new_id("trial_"), "revision": revision, "candidate_digest": s.candidate["digest"],
            "start_frame_id": start_frame_id, "start_sha256": frame["sha256"],
            "fresh_round_evidence": text(fresh_round_evidence, "새 시도 근거"), "state": "running",
            "started_at": s.clock(), "start_action_count": s.action_count, "success": False}
        self.executor = self.executor_factory(s)
        with s.state_lock:
            s.check(input_allowed=True)
            s.trial = trial
            s.state, s.message = "verifying", "후보 플랜 독립 재생 중: GPT 입력은 차단됩니다."
            s.latest = None
            s._persist()
        s.trace.emit("trial_start", f"후보 v{revision} 독립 재생 시작 · GPT 입력은 차단됩니다.", phase="후보 독립 재생")
        self.worker = threading.Thread(target=self._run, daemon=True, name="gpt-play-candidate")
        self.worker.start()
        return trial.copy()

    def _run(self):
        s, trial = self.session, self.session.trial
        try:
            ok = bool(self.executor.run(s.candidate["plan"], s.folder / "assets"))
            with s.state_lock:
                if s.state != "verifying":
                    ok = False
                trial.update(state="executed" if ok else "failed", execution_ok=ok, ended_at=s.clock(), end_action_count=s.action_count)
        except Exception as exc:
            s.trace.emit("trial_failed", error_text(exc), level="error")
            with s.state_lock:
                trial.update(state="failed", execution_ok=False, ended_at=s.clock(), end_action_count=s.action_count, error=str(exc)[:1000])
        finally:
            try:
                with s.io_lock:
                    released = s.backend.release()
            except Exception:
                released = False
            with s.state_lock:
                if not released:
                    trial.update(state="failed", execution_ok=False, error="input release unconfirmed")
                if s.state == "verifying":
                    s.state, s.message = "reviewing_trial", "재생 종료: GPT가 실제 클리어·보상 결과를 확인합니다."
                s.latest = None
                s._persist()
            s.trace.emit("trial_end", "후보 실행 종료 · 실행 결과=" + str(trial["execution_ok"])
                         + " · 실제 클리어·보상 확인은 별도입니다.", phase="후보 결과 확인")

    def stop(self):
        if self.executor:
            self.executor.stop()

    def result(self, trial_id, end_frame_id, success, observation):
        s = self.session
        s.check()
        trial = s.trial
        if self.busy or not trial or trial["trial_id"] != trial_id or trial["state"] not in {"executed", "failed"}:
            raise BridgeError("INVALID_TRIAL", "완료된 현재 검수의 결과만 기록할 수 있습니다.")
        frame = s._frame(end_frame_id, fresh=True)
        if type(success) is not bool or frame["time"] < trial["ended_at"]:
            raise BridgeError("INVALID_TRIAL", "검수 종료 후 새 관찰이 필요합니다.")
        if success and (not trial["execution_ok"] or trial.get("interrupted")
                        or s.action_count != trial["end_action_count"] or frame["sha256"] == trial["start_sha256"]
                        or s.action_count <= trial["start_action_count"]):
            raise BridgeError("INVALID_SUCCESS", "입력·화면 변화·재생 완료를 확인하지 못했습니다.")
        record = {**trial, "success": success, "end_frame_id": end_frame_id,
            "observation": text(observation, "실제 결과 근거"), "state": "reviewed", "human_verified": False,
            "reviewed_at": s.clock()}
        with s.state_lock:
            s.check()
            if trial.get("interrupted") and success:
                raise BridgeError("INVALID_SUCCESS", "Interrupted replay cannot be successful.")
            s.trials.append(record)
            s.trial = record
            s.state, s.message = "exploring", "검수 결과 보관: " + ("성공 관찰" if success else "실패·후보 보완 필요")
            s._persist()
        s.trace.emit("trial_result", "GPT 결과 관찰: " + ("성공" if success else "실패") + " · " + observation,
                     phase="GPT 판단 대기")
        return record


def native_executor(session):
    from ..analyzer.automation_models import AutomationPlan
    from ..player.rule_executor import RuleExecutor, ExecutionState

    class GuardedExecutor(RuleExecutor):
        def check(self):
            if self._stop_event.is_set():
                raise BridgeError("PLAYBACK_STOPPED", "후보 재생 중지")
            session.check(input_allowed=True, playback=True)
            session.backend.validate()

        def run(self, plan, assets):
            if digest(plan) != session.candidate["digest"]:
                raise BridgeError("CANDIDATE_CHANGED", "검수 중 후보가 변경되었습니다.")
            self._current_plan = AutomationPlan.from_dict(plan, assets)
            self._state = ExecutionState.RUNNING_INITIAL
            try:
                for index, rule in enumerate(self._current_plan.initial_rules, 1):
                    self.check()
                    result = self._execute_rule_tree_once(rule, str(index))
                    if result is not None:
                        return False
                return not self._stop_event.is_set()
            finally:
                self._state = ExecutionState.IDLE

        def stop(self):
            # Own candidate only; never call the normal player's global cleanup.
            self._stop_event.set()
            self._pause_event.set()

        def _wait_for_resume(self):
            try:
                self.check()
                return False
            except Exception:
                self._stop_event.set()
                return True

        def _execute_fixed_action(self, rule, start_time):
            self.check()
            if rule.action_type == "key_press":
                action = {"kind": "key", "key": rule.action_keys[0]}
                ok = self._send(action, session.backend.validate())
                return self._make_result(rule, ok, "GPT 후보 키 전송" if ok else "키 전송 실패", start_time)
            if rule.action_type != "click":
                raise BridgeError("ACTION_DENIED", "후보에서 허용하지 않은 실행 유형입니다.")
            return super()._execute_fixed_action(rule, start_time)

        def _find_rule_image_click_target(self, rule, valid_images):
            self.check()
            candidate.verify_assets(session)
            session.backend.validate(rule.search_region)
            result = super()._find_rule_image_click_target(rule, valid_images)
            if result and len(result["locations"]) != 1:
                self._stop_event.set()
                raise BridgeError("AMBIGUOUS_TARGET", "후보 이미지가 여러 곳과 일치합니다. 더 구체적인 이미지를 작성하세요.")
            return result

        def _send(self, action, region):
            with session.io_lock:
                self.check()
                if session.action_count >= session.max_actions:
                    raise BridgeError("BUDGET_EXHAUSTED", "입력 한도 도달")
                request_id = new_id("trial_input_")
                receipt = {"request_id": request_id, "state": "unknown", "action": action,
                    "trial_id": session.trial["trial_id"], "candidate_digest": session.candidate["digest"]}
                with session.state_lock:
                    session.action_count += 1
                    session.latest = None
                    session.receipts[request_id] = receipt
                session._persist()
                session.trace.emit("trial_input_begin", action_text(action) + f" · 요청 {session.action_count}")
                try:
                    ok = session.backend.send(action, region, self.check)
                    if not ok:
                        raise BridgeError("DELIVERY_UNKNOWN", "후보 입력 전송 결과가 불확실합니다.")
                    receipt["state"] = "sent_not_game_success"
                    session.trace.emit("trial_input_sent", action_text(action) + " · 전송 완료, 게임 반응 미확인")
                    return True
                except Exception as exc:
                    session.trace.emit("trial_input_failed", action_text(action) + " · " + error_text(exc), level="error")
                    self._stop_event.set()
                    raise
                finally:
                    session._persist()

        def _execute_click_at(self, rule, action_type, click_x, click_y, start_time, *, image_click=False):
            self.check()
            region = session.backend.validate(rule.search_region)
            if not region[0] <= click_x < region[2] or not region[1] <= click_y < region[3]:
                raise BridgeError("OUTSIDE_TARGET", "인식 결과가 선택 창 밖입니다.")
            ok = self._send({"kind": "click", "x": click_x - region[0], "y": click_y - region[1]}, region)
            return self._make_result(rule, ok, "GPT 후보 이미지 클릭" if ok else "클릭 실패", start_time)

    return GuardedExecutor()
