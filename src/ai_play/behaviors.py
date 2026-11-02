"""GPT-authored, guarded routines. Same executor in learning, trials and player."""

import base64
import copy
import hashlib
import io
import time

from PIL import Image, ImageChops, ImageStat

from ..agent_bridge.contracts import BridgeError, digest, number
from ..agent_bridge.storage import new_id
from .actions import validate_action
from .learning import required


ACTION_TYPE = "gpt_play_behavior"


def _rectangle(rect, size):
    return (isinstance(rect, list) and len(rect) == 4 and all(type(x) is int for x in rect)
            and 0 <= rect[0] < rect[2] <= size[0] and 0 <= rect[1] < rect[3] <= size[1]
            and 4 <= rect[2] - rect[0] <= 512 and 4 <= rect[3] - rect[1] <= 512)


def _image(guard):
    try:
        raw = base64.b64decode(guard["png"], validate=True)
        if len(raw) > 200_000 or hashlib.sha256(raw).hexdigest() != guard["sha256"]:
            raise ValueError("hash/size")
        with Image.open(io.BytesIO(raw)) as image:
            rect = guard["region"]
            if image.format != "PNG" or image.size != (rect[2] - rect[0], rect[3] - rect[1]):
                raise ValueError("dimensions")
            return image.convert("RGB")
    except Exception as exc:
        raise BridgeError("INVALID_GUARD", "화면 조건 이미지가 손상되었거나 크기가 다릅니다.") from exc


def validate_program(program):
    if (not isinstance(program, dict) or set(program) != {"version", "size", "target", "steps"}
            or program["version"] != 1 or not isinstance(program["size"], list) or len(program["size"]) != 2
            or any(type(n) is not int or not 1 <= n <= 8000 for n in program["size"])
            or not isinstance(program["target"], dict) or set(program["target"]) != {"executable", "title"}
            or any(not isinstance(v, str) or not v or len(v) > 300 for v in program["target"].values())
            or not isinstance(program["steps"], list) or not 1 <= len(program["steps"]) <= 20):
        raise BridgeError("INVALID_BEHAVIOR", "GPT 전용 행동 정의 형식이 올바르지 않습니다.")
    encoded_size = 0
    for step in program["steps"]:
        if not isinstance(step, dict) or set(step) != {"before", "action", "after", "timeout", "purpose", "expected_result"}:
            raise BridgeError("INVALID_BEHAVIOR", "시작·입력·결과·시간제한·목적이 모두 필요합니다.")
        action = validate_action(step["action"])
        if action["kind"] == "click" and not (action["x"] < program["size"][0] and action["y"] < program["size"][1]):
            raise BridgeError("OUTSIDE_TARGET", "클릭이 대상 창 밖입니다.")
        if not number(step["timeout"], .1, 5):
            raise BridgeError("INVALID_BEHAVIOR", "단계 결과 확인 제한은 0.1~5초입니다.")
        required(step["purpose"])
        required(step["expected_result"])
        for guard in (step["before"], step["after"]):
            if (not isinstance(guard, dict) or set(guard) != {"region", "threshold", "sha256", "png", "frame_id"}
                    or not _rectangle(guard["region"], program["size"])
                    or not number(guard["threshold"], .9, 1) or not isinstance(guard["png"], str)
                    or len(guard["png"]) > 270_000):
                raise BridgeError("INVALID_GUARD", "관찰 화면의 구체적인 조건 영역과 90~100% 기준이 필요합니다.")
            image = _image(guard)
            if max(ImageStat.Stat(image).stddev) < 3:
                raise BridgeError("WEAK_GUARD", "단색 배경은 시작·성공 조건으로 사용할 수 없습니다.")
            encoded_size += len(guard["png"])
        if step["before"]["sha256"] == step["after"]["sha256"]:
            raise BridgeError("WEAK_GUARD", "입력 전후가 같은 이미지인 조건은 결과 확인을 대신할 수 없습니다.")
    if encoded_size > 2_000_000:
        raise BridgeError("BEHAVIOR_TOO_LARGE", "재사용 구간 근거 용량 한도입니다. 의미 있는 작은 영역으로 나누세요.")
    return copy.deepcopy(program)


def _guard(session, value, size):
    if not isinstance(value, dict) or set(value) != {"frame_id", "region", "threshold"} or not _rectangle(value["region"], size):
        raise BridgeError("INVALID_GUARD", "frame_id, region=[x1,y1,x2,y2], threshold가 필요합니다.")
    frame = session.read_frame(value["frame_id"]) if value["frame_id"] in session.frames else session.learning.frame(value["frame_id"])
    region = frame["region"]
    if [region[2] - region[0], region[3] - region[1]] != size:
        raise BridgeError("ENVIRONMENT_CHANGED", "다른 크기의 화면 근거를 섞을 수 없습니다.")
    with Image.open(io.BytesIO(base64.b64decode(frame["png_base64"]))) as image:
        buffer = io.BytesIO()
        image.convert("RGB").crop(value["region"]).save(buffer, format="PNG")
    raw = buffer.getvalue()
    return {**value, "sha256": hashlib.sha256(raw).hexdigest(), "png": base64.b64encode(raw).decode()}


def author(session, name, steps):
    session.check()  # Authoring is read-only even after the goal freezes input.
    memory = session.learning
    if not isinstance(steps, list) or not 1 <= len(steps) <= 20:
        raise BridgeError("INVALID_BEHAVIOR", "재사용 구간은 1~20단계입니다.")
    if not isinstance(steps[0], dict) or not isinstance(steps[0].get("before"), dict) or "frame_id" not in steps[0]["before"]:
        raise BridgeError("INVALID_BEHAVIOR", "시작 화면 근거가 필요합니다.")
    source = session._frame(steps[0]["before"]["frame_id"])
    region = source["region"]
    size = [region[2] - region[0], region[3] - region[1]]
    environment = memory.store.get(memory.task, "task", memory.task)["environment"]
    compiled = []
    for step in steps:
        if not isinstance(step, dict) or set(step) != {"before", "action", "after", "timeout", "purpose", "expected_result"}:
            raise BridgeError("INVALID_BEHAVIOR", "재사용 단계의 필드가 올바르지 않습니다.")
        action = step["action"]
        if isinstance(action, dict) and action.get("kind") == "recorded_action":
            if set(action) != {"kind", "request_id"}:
                raise BridgeError("INVALID_BEHAVIOR", "검수 동작 참조에는 request_id만 지정하세요.")
            receipt = session.receipt_for("input", required(action["request_id"], 100))
            if (not receipt or receipt.get("state") != "sent_not_game_success"
                    or receipt.get("observation_id") != step["before"].get("frame_id")):
                raise BridgeError("INVALID_BEHAVIOR", "이 시작 화면에서 전송 확인된 입력만 참조하세요.")
            action = receipt["action"]
        validate_action(action, session.keys)
        compiled.append({**step, "action": copy.deepcopy(action), "before": _guard(session, step["before"], size),
                         "after": _guard(session, step["after"], size)})
    program = validate_program({"version": 1, "size": size, "target": environment, "steps": compiled})
    record = {"behavior_id": new_id("behavior_"), "name": required(name, 200), "program": program,
        "digest": digest(program), "goal_revision": memory.revision, "status": "candidate",
        "successes": 0, "failures": 0, "created": time.time()}
    memory.store.put(memory.task, "behavior", record["behavior_id"], record)
    session.trace.emit("behavior_authored", "새 재사용 후보 저장 · " + name + " · 실제 결과 검수 전")
    return {k: v for k, v in record.items() if k != "program"}


def matches(image, guard, template=None):
    template = template or _image(guard)
    crop = image.convert("RGB").crop(guard["region"])
    # This opt-in aligned-ROI metric never changes ordinary image recognition.
    error = ImageStat.Stat(ImageChops.difference(crop, template)).mean
    score = 1 - sum(error) / (3 * 255)
    # An empty background must not match a small distinctive glyph surrounded by
    # black pixels. Include the brightest DIFFERING pixels, not only global mean.
    diff = ImageChops.difference(crop, template).convert("L")
    histogram = diff.histogram()
    remaining = max(1, int(crop.width * crop.height * .1))
    total, count = 0, remaining
    for value in range(255, -1, -1):
        taken = min(remaining, histogram[value])
        total += taken * value
        remaining -= taken
        if not remaining:
            break
    worst_score = 1 - total / (count * 255)
    return score >= guard["threshold"] and worst_score >= guard["threshold"], min(score, worst_score)


def confirm_before_send(backend, region, guard):
    """Recheck after durable write-ahead; storage latency can change the scene."""
    image, current = backend.capture()
    if (list(current) != list(region) or image.size != (region[2] - region[0], region[3] - region[1])
            or not matches(image, guard)[0]):
        raise BridgeError("BEHAVIOR_GUARD_CHANGED", "기록 저장 중 시작 화면이 바뀌었습니다. 입력하지 않고 다시 판단합니다.")


def execute(program, backend, check, transmit, *, clock=time.monotonic, sleep=time.sleep):
    program = validate_program(program)
    started = clock()
    region = None
    for index, step in enumerate(program["steps"]):
        check()
        if clock() - started >= 60:
            return {"status": "mismatch", "step": index + 1, "reason": "routine_time_limit"}
        image, current = backend.capture()
        if list(image.size) != program["size"] or (region is not None and list(current) != list(region)):
            raise BridgeError("ENVIRONMENT_CHANGED", "재사용 구간 실행 중 창 크기·위치가 바뀌었습니다.")
        region = current
        hit, score = matches(image, step["before"])
        if not hit:
            return {"status": "mismatch", "step": index + 1, "reason": "start_condition", "score": score}
        check()
        try:
            transmit(step["action"], region, step["purpose"], step["before"])
        except BridgeError as exc:
            if exc.code != "BEHAVIOR_GUARD_CHANGED":
                raise
            return {"status": "mismatch", "step": index + 1, "reason": "condition_changed_before_send"}
        deadline = clock() + step["timeout"]
        template = _image(step["after"])
        while True:
            check()
            image, current = backend.capture()
            if list(current) != list(region):
                raise BridgeError("ENVIRONMENT_CHANGED", "결과 확인 중 창 위치가 바뀌었습니다.")
            hit, score = matches(image, step["after"], template)
            if hit:
                break
            if clock() >= deadline:
                return {"status": "mismatch", "step": index + 1, "reason": "result_condition", "score": score}
            sleep(.05)
    return {"status": "conditions_matched", "steps": len(program["steps"]), "elapsed": clock() - started,
            "game_success": "not_inferred_from_screen_match"}


def run_behavior(session, behavior_id, observation_id, request_id):
    session.check(input_allowed=True)
    request_id = required(request_id, 100)
    memory = session.learning
    record = memory.store.get(memory.task, "behavior", behavior_id)
    fingerprint = digest({"behavior": record["digest"], "observation": observation_id})
    old = session.receipt_for("behavior", request_id)
    if old:
        if old["digest"] != fingerprint:
            raise BridgeError("REQUEST_REUSED", "이미 사용한 재사용 요청 ID입니다.")
        return copy.deepcopy(old)
    session._frame(observation_id, fresh=True)
    if digest(record["program"]) != record["digest"]:
        raise BridgeError("BEHAVIOR_CHANGED", "재사용 정의가 검수 후 변경되었습니다.")
    if (record["status"] == "revalidate" and record["goal_revision"] == memory.revision
            and memory.store.only_entry_mismatches(memory.task, behavior_id, record["failures"])):
        # Repair old entry-only quarantines only with a complete failure audit.
        # Missing history or any actual result failure remains quarantined.
        record["not_applicable"] = record.get("not_applicable", 0) + record["failures"]
        record["failures"] = 0
        record["status"] = "repeated" if record["successes"] >= 3 else "provisional"
        memory.store.put(memory.task, "behavior", behavior_id, record)
    if record["goal_revision"] != memory.revision or record["status"] in {"disabled", "revalidate"}:
        raise BridgeError("BEHAVIOR_REVALIDATION", "목표가 바뀌었거나 실패한 재사용 구간입니다. 근거를 다시 확인해 새 후보를 작성하세요.")
    receipt = {"request_id": request_id, "digest": fingerprint, "behavior_id": behavior_id, "status": "unknown"}
    session.behavior_receipts[request_id] = receipt
    session._persist()
    epoch = session.epoch
    def check():
        session.check(input_allowed=True)
        if epoch != session.epoch:
            raise BridgeError("STALE_EXECUTION", "중지·개입 이전의 재사용 요청입니다.")
    def transmit(action, region, purpose, guard):
        check()
        # Each step gets a durable fresh observation and the same write-ahead path.
        current = session.observe()
        session.backend.validate(region)
        return session.act(current["frame_id"], new_id("routine_"), action, purpose, _guard=guard)
    try:
        result = execute(record["program"], session.backend, check, transmit)
        receipt.update(result)
        if result["status"] == "conditions_matched":
            record["successes"] += 1
            record["status"] = "repeated" if record["successes"] >= 3 else "provisional"
        elif result.get("step") == 1 and result.get("reason") in {"start_condition", "condition_changed_before_send"}:
            # Not being at this routine's entry scene is not execution failure.
            record["not_applicable"] = record.get("not_applicable", 0) + 1
        else:
            record["failures"] += 1
            record["status"] = "revalidate"
        memory.store.put(memory.task, "behavior", behavior_id, record)
        memory.store.event(memory.task, "behavior_run", receipt)
        session.trace.emit("behavior_result", record["name"] + " · " + result["status"] +
                           " · 실제 클리어 판정은 GPT가 새 화면으로 확인합니다.")
    except Exception:
        receipt["status"] = "unknown"
        memory.pending.append(copy.deepcopy(receipt))
        raise
    finally:
        session.latest = None
        session._persist()
    return copy.deepcopy(receipt)
