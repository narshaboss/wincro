"""Compile AI-authored evidence into NEW native plans, never input-log conversion."""

import copy
import hashlib
import io

from PIL import Image

from ..agent_bridge.contracts import BridgeError, digest, encoded, number
from ..agent_bridge.storage import new_id, publish_bytes, read_json, write_json, within
from .session import text
from .key_policy import reserved_keys


def required_trials(session):
    return 10 if session.learning else 3


def success_streak(session):
    count = 0
    for trial in reversed(session.trials):
        if not trial.get("success") or trial.get("interrupted"):
            break
        if session.learning and (not session.candidate or
                trial.get("revision") != session.candidate["revision"] or
                trial.get("candidate_digest") != session.candidate["digest"]):
            break
        count += 1
    return count


def build(session, title, steps, remaining_issues):
    session.check(input_allowed=not bool(session.live))
    if session.state == "verifying":
        raise BridgeError("OBSERVER_ONLY", "검수가 끝난 뒤 후보를 수정하세요.")
    if not isinstance(steps, list) or not 1 <= len(steps) <= 100:
        raise BridgeError("INVALID_PLAN", "후보는 1~100개 동작으로 구성하세요.")
    if not isinstance(remaining_issues, list) or len(remaining_issues) > 30 or any(not isinstance(x, str) or len(x) > 2000 for x in remaining_issues):
        raise BridgeError("INVALID_PLAN", "미해결 항목은 문자열 목록이어야 합니다.")
    from ..analyzer.automation_models import AutomationPlan, AutomationRule
    rules, evidence, assets = [], [], {}
    for step in steps:
        if not isinstance(step, dict) or set(step) - {"kind", "key", "frame_id", "crop", "purpose", "expected_result", "wait_after", "repeat_count", "repeat_delay", "confidence", "behavior_id"}:
            raise BridgeError("INVALID_PLAN", "지원되지 않는 후보 필드입니다. 임의 분기·특화모드로 우회하지 마세요.")
        purpose = text(step.get("purpose"), "동작 목적", 200)
        expected = text(step.get("expected_result"), "예상 결과")
        frame = session._frame(step.get("frame_id"))
        wait = step.get("wait_after", .5)
        delay = step.get("repeat_delay", .5)
        repeat = step.get("repeat_count", 1)
        if not number(wait, 0, 30) or not number(delay, .05, 30) or type(repeat) is not int or not 1 <= repeat <= 30:
            raise BridgeError("INVALID_PLAN", "대기 0~30초, 반복 1~30회, 반복 대기 0.05~30초만 허용합니다.")
        kind = step.get("kind")
        if session.learning and kind != "behavior":
            raise BridgeError("GUARDED_EXPORT_REQUIRED", "경험 기반 플랜은 검수된 재사용 구간으로 작성하세요. 키 유지·화면 조건을 일반 키 액션으로 바꾸지 않습니다.")
        rule = AutomationRule(description="GPT: " + purpose, wait_after=wait,
            repeat_count=repeat, repeat_delay=delay, wait_random=False, repeat_delay_random=False)
        if kind == "behavior" and session.learning:
            from .behaviors import ACTION_TYPE, validate_program
            memory = session.learning
            behavior = memory.store.get(memory.task, "behavior", step.get("behavior_id"))
            accepted = {"candidate", "provisional", "repeated"} if session.live else {"repeated"}
            if behavior["goal_revision"] != memory.revision or behavior["status"] not in accepted:
                raise BridgeError("BEHAVIOR_UNVERIFIED", "같은 목표·정의에서 화면 조건 확인 3회 이상인 구간만 후보로 옮깁니다.")
            program = validate_program(behavior["program"])
            if digest(program) != behavior["digest"]:
                raise BridgeError("BEHAVIOR_CHANGED", "재사용 정의 해시가 다릅니다.")
            rule.action_type = ACTION_TYPE
            rule.gpt_play_config = program
        elif kind == "key" and step.get("key") in session.keys and step["key"] not in reserved_keys():
            rule.action_type, rule.action_keys = "key_press", [step["key"]]
        elif kind == "click":
            crop = step.get("crop")
            rect = frame["region"]
            if (not isinstance(crop, list) or len(crop) != 4 or any(type(x) is not int for x in crop)
                    or not 0 <= crop[0] < crop[2] <= rect[2] - rect[0]
                    or not 0 <= crop[1] < crop[3] <= rect[3] - rect[1]):
                raise BridgeError("INVALID_CROP", "관찰 이미지 내부의 [x1,y1,x2,y2] 범위가 필요합니다.")
            confidence = step.get("confidence", .9)
            if not number(confidence, .8, 1):
                raise BridgeError("INVALID_PLAN", "새 후보의 인식률은 80~100%로 명시하세요. 기존 설정은 바꾸지 않습니다.")
            frame_path = within(session.folder / "frames" / (frame["frame_id"] + ".png"), session.folder / "frames")
            if hashlib.sha256(frame_path.read_bytes()).hexdigest() != frame["sha256"]:
                raise BridgeError("EVIDENCE_CHANGED", "관찰 원본이 변경되었습니다.")
            with Image.open(frame_path) as image:
                cropped = image.crop(crop)
                if min(cropped.size) < 4:
                    raise BridgeError("INVALID_CROP", "템플릿이 너무 작습니다.")
                output = io.BytesIO()
                cropped.save(output, format="PNG")
            filename = new_id("gpt_play_") + ".png"
            publish_bytes(session.folder / "assets", filename, output.getvalue())
            assets[filename] = hashlib.sha256(output.getvalue()).hexdigest()
            rule.action_type, rule.target_image = "click", filename
            rule.search_region = list(rect)
            rule.action_x, rule.action_y = rect[0] + (crop[0] + crop[2]) // 2, rect[1] + (crop[1] + crop[3]) // 2
            rule.confidence = confidence
            rule.verify_image_color = rule.verify_image_brightness = True
        else:
            raise BridgeError("UNREPRESENTABLE", "현재 안전 후보는 이미지 클릭·단일 키와 유한 반복만 지원합니다. 동적 분기나 긴 키 유지가 필수면 미완료로 남기세요.")
        rules.append(rule)
        evidence.append({"rule_id": rule.rule_id, "frame_id": frame["frame_id"], "purpose": purpose, "expected_result": expected})
    plan = AutomationPlan(name=text(title, "제목", 200), initial_rules=rules).to_dict()
    plan["plan_id"] = new_id("plan_")
    plan["total_repeat_count"] = 1
    plan["user_verified"] = False
    if len(encoded(plan)) > 1_000_000:
        raise BridgeError("CANDIDATE_TOO_LARGE", "후보 근거 용량이 1MB를 넘습니다. 작은 조건 영역으로 나누고 불필요한 중복을 줄이세요.")
    candidate = {"plan": plan, "revision": (session.candidate or {}).get("revision", 0) + 1,
        "evidence": evidence, "assets": assets, "remaining_issues": remaining_issues, "digest": digest(plan)}
    from .diagnostics import action_text
    candidate["ui_steps"] = [str(index + 1) + ". " + rule["description"] + (
        "\n   " + " → ".join(action_text(step["action"]) for step in rule["gpt_play_config"]["steps"])
        if rule["action_type"] == "gpt_play_behavior" else "") for index, rule in enumerate(plan["initial_rules"])]
    if session.learning:
        candidate["goal_revision"] = session.learning.revision
    with session.state_lock:
        session.check(input_allowed=not bool(session.live))
        write_json(session.folder / "candidate.json", candidate)
        # Round-trip the actual player model, not just JSON serialization.
        restored = AutomationPlan.from_dict(read_json(session.folder / "candidate.json")["plan"], session.folder / "assets").to_dict()
        if restored != plan:
            raise BridgeError("SAVE_STATE_UNKNOWN", "후보 플랜을 실제 모델로 다시 읽은 결과가 다릅니다.")
        session.candidate, session.reviews, session.trials, session.trial = candidate, [], [], None
        session.state, session.message = "exploring", "후보 저장 완료: 양방향 대조와 독립 재생 검수가 필요합니다."
        session._persist()
    return session.snapshot()["candidate"]


def review(session, revision, direction, checks, remaining_issues):
    session.check()
    candidate = session.candidate
    if not candidate or revision != candidate["revision"]:
        raise BridgeError("STALE_CANDIDATE", "현재 후보 버전을 확인하세요.")
    if session.learning and candidate.get("goal_revision") != session.learning.revision:
        raise BridgeError("STALE_CANDIDATE", "사용자 지시가 바뀌었습니다. 이전 후보를 그대로 재검수하지 말고 새로 작성하세요.")
    if direction not in {"experience_to_plan", "plan_to_experience"} or not isinstance(checks, list):
        raise BridgeError("INVALID_REVIEW", "경험→플랜 / 플랜→경험 대조가 필요합니다.")
    expected = {item["rule_id"] for item in candidate["evidence"]}
    seen = set()
    for check in checks:
        if not isinstance(check, dict) or check.get("rule_id") not in expected or check["rule_id"] in seen:
            raise BridgeError("INVALID_REVIEW", "현재 후보의 모든 액션을 한 번씩 대조하세요.")
        session._frame(check.get("frame_id"))
        text(check.get("note"), "비교 근거")
        if check.get("result") not in {"match", "mismatch", "uncertain"}:
            raise BridgeError("INVALID_REVIEW", "비교 결과를 명시하세요.")
        seen.add(check["rule_id"])
    if seen != expected or not isinstance(remaining_issues, list) or any(not isinstance(x, str) or len(x) > 2000 for x in remaining_issues):
        raise BridgeError("INVALID_REVIEW", "액션 대조가 누락되었거나 미해결 목록이 올바르지 않습니다.")
    record = {"revision": revision, "direction": direction, "checks": checks,
        "remaining_issues": remaining_issues, "passed": not remaining_issues and all(x["result"] == "match" for x in checks)}
    if session.learning:
        expected_direction = "experience_to_plan" if not session.reviews or session.reviews[-1]["direction"] == "plan_to_experience" else "plan_to_experience"
        if direction != expected_direction:
            raise BridgeError("REVIEW_ORDER", "경험→플랜 다음 플랜→경험 순서로 교차 확인하세요.")
    session.reviews = [r for r in session.reviews if r["direction"] != direction] + [record]
    session._persist()
    return record


def ready(session):
    candidate = session.candidate
    if session.learning and candidate and candidate.get("goal_revision") != session.learning.revision:
        return False
    return bool(candidate and not candidate["remaining_issues"] and
        {r["direction"] for r in session.reviews if r["passed"] and r["revision"] == candidate["revision"]}
        == {"experience_to_plan", "plan_to_experience"})


def verify_assets(session):
    current = session.candidate
    if not current or digest(current["plan"]) != current["digest"]:
        raise BridgeError("CANDIDATE_CHANGED", "Candidate content changed after review.")
    for name, expected in current["assets"].items():
        path = within(session.folder / "assets" / name, session.folder / "assets")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise BridgeError("ASSET_CHANGED", "Candidate image changed after review.")


def publish(session, plans_dir, templates_dir):
    """App-owner-only: agent cannot invoke filesystem publication."""
    count = required_trials(session)
    if session.state != "complete" or not ready(session) or success_streak(session) < count:
        raise BridgeError("VERIFICATION_REQUIRED", f"현재 후보의 독립 재생 {count}회와 양방향 대조가 필요합니다.")
    verify_assets(session)
    plan = copy.deepcopy(session.candidate["plan"])
    plan["user_verified"] = True  # Listing permission only, not human review.
    payload = encoded(plan)
    intent = {"plan_id": plan["plan_id"], "sha256": hashlib.sha256(payload).hexdigest(),
        "revision": session.candidate["revision"], "user_reviewed": False, "playlist_changed": False}
    intent_path = session.folder / "publish-intent.json"
    if intent_path.exists() and read_json(intent_path) != intent:
        raise BridgeError("SAVE_STATE_UNKNOWN", "이전 등록 결과를 확인해야 합니다.")
    write_json(intent_path, intent)
    for name, sha in session.candidate["assets"].items():
        data = within(session.folder / "assets" / name, session.folder / "assets").read_bytes()
        if hashlib.sha256(data).hexdigest() != sha:
            raise BridgeError("ASSET_CHANGED", "후보 이미지가 변경되었습니다.")
        target = templates_dir / name
        if target.exists():
            if within(target, templates_dir).read_bytes() != data:
                raise BridgeError("SAVE_STATE_UNKNOWN", "이미지 이름 충돌: 덮어쓰지 않습니다.")
        else:
            publish_bytes(templates_dir, name, data)
    target = plans_dir / (plan["plan_id"] + ".json")
    if target.exists():
        if within(target, plans_dir).read_bytes() != payload:
            raise BridgeError("SAVE_STATE_UNKNOWN", "기존 플랜을 덮어쓰지 않습니다.")
    else:
        publish_bytes(plans_dir, target.name, payload)
    from ..analyzer.automation_models import AutomationPlan
    if read_json(target) != plan or AutomationPlan.from_dict(read_json(target), templates_dir).to_dict() != plan:
        raise BridgeError("SAVE_STATE_UNKNOWN", "등록 플랜 재로딩 검증 실패")
    session.publication = {**intent, "saved_and_reloaded": True, "name": plan["name"]}
    session._persist()
    return session.publication
