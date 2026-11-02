"""Screen-only digit calibration and GPT-selected bounded movement intent.

No game memory, OCR model downloads, special-mode imports, or persistent walls.
"""

import base64
import copy
import hashlib
import io
import time

import numpy as np
from PIL import Image

from ..agent_bridge.contracts import BridgeError, number


def rectangle(value, size):
    return (isinstance(value, list) and len(value) == 4 and all(type(n) is int for n in value)
            and 0 <= value[0] < value[2] <= size[0] and 0 <= value[1] < value[3] <= size[1]
            and value[2] - value[0] <= 256 and value[3] - value[1] <= 128)


def mask(image, light, level):
    pixels = np.asarray(image.convert("L"))
    return pixels >= level if light else pixels <= level


def glyph(value):
    ys, xs = np.where(value)
    if not len(xs):
        raise BridgeError("COORDINATE_UNCERTAIN", "좌표 숫자의 전경 픽셀이 없습니다.")
    crop = value[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    if crop.size < 4 or crop.mean() > .95:
        raise BridgeError("COORDINATE_UNCERTAIN", "구별 가능한 숫자 모양이 아닙니다.")
    normalized = Image.fromarray(crop.astype("uint8") * 255).resize((16, 24), Image.Resampling.NEAREST)
    return np.asarray(normalized) > 0


def validate_profile(profile):
    if (not isinstance(profile, dict) or set(profile) != {"version", "size", "x_region", "y_region", "light", "level", "digits"}
            or profile["version"] != 1 or not isinstance(profile["size"], list) or len(profile["size"]) != 2
            or any(type(v) is not int or not 1 <= v <= 8000 for v in profile["size"])
            or type(profile["light"]) is not bool or type(profile["level"]) is not int
            or not 1 <= profile["level"] <= 254 or not isinstance(profile["digits"], list)
            or not 1 <= len(profile["digits"]) <= 30):
        raise BridgeError("INVALID_COORDINATE_PROFILE", "좌표 보정 형식이 올바르지 않습니다.")
    for name in ("x_region", "y_region"):
        if not rectangle(profile[name], profile["size"]):
            raise BridgeError("INVALID_COORDINATE_PROFILE", "XY 각각의 숫자 영역을 지정하세요.")
    for digit in profile["digits"]:
        if (not isinstance(digit, dict) or set(digit) != {"digit", "png", "sha256"}
                or type(digit["digit"]) is not int or not 0 <= digit["digit"] <= 9
                or not isinstance(digit["png"], str) or len(digit["png"]) > 20_000):
            raise BridgeError("INVALID_COORDINATE_PROFILE", "화면에서 관찰한 숫자 표본이 필요합니다.")
        try:
            raw = base64.b64decode(digit["png"], validate=True)
            if hashlib.sha256(raw).hexdigest() != digit["sha256"]:
                raise ValueError("hash")
            with Image.open(io.BytesIO(raw)) as sample:
                if sample.format != "PNG" or not 2 <= sample.width <= 64 or not 2 <= sample.height <= 96:
                    raise ValueError("size")
                glyph(mask(sample, profile["light"], profile["level"]))
        except Exception as exc:
            raise BridgeError("INVALID_COORDINATE_PROFILE", "숫자 표본 무결성 검사 실패") from exc
    if sum(len(d["png"]) for d in profile["digits"]) > 100_000:
        raise BridgeError("INVALID_COORDINATE_PROFILE", "좌표 표본은 작은 숫자 영역으로 제한하세요.")
    return copy.deepcopy(profile)


class CoordinateReader:
    def __init__(self, profile):
        self.profile = validate_profile(profile)
        self.templates = []
        for digit in profile["digits"]:
            with Image.open(io.BytesIO(base64.b64decode(digit["png"]))) as sample:
                self.templates.append((digit["digit"], glyph(mask(sample, profile["light"], profile["level"]))))

    def read(self, image):
        p = self.profile
        if list(image.size) != p["size"]:
            raise BridgeError("ENVIRONMENT_CHANGED", "좌표 보정 때와 창 크기가 다릅니다.")
        result, confidence = [], 1.0
        for name in ("x_region", "y_region"):
            value = mask(image.crop(p[name]), p["light"], p["level"])
            columns = value.any(axis=0)
            spans, start = [], None
            for i, visible in enumerate(list(columns) + [False]):
                if visible and start is None:
                    start = i
                elif not visible and start is not None:
                    spans.append((start, i))
                    start = None
            if not 1 <= len(spans) <= 4:
                raise BridgeError("COORDINATE_UNCERTAIN", "숫자 분리 실패: 라벨·부호 없이 숫자 영역만 보정하세요.")
            digits = []
            for left, right in spans:
                current = glyph(value[:, left:right])
                scores = {}
                for digit, template in self.templates:
                    score = float(np.logical_and(current, template).sum() / np.logical_or(current, template).sum())
                    scores[digit] = max(scores.get(digit, 0), score)
                ranked = sorted(scores.items(), key=lambda row: row[1], reverse=True)
                best, score = ranked[0]
                # Strict, dedicated boundary: ordinary OCR/recognition is untouched.
                if score < .92 or (len(ranked) > 1 and score - ranked[1][1] < .04):
                    raise BridgeError("COORDINATE_UNCERTAIN", "미학습·모호한 숫자입니다. 새 표본을 확인하세요.")
                confidence = min(confidence, score)
                digits.append(str(best))
            result.append(int("".join(digits)))
        return {"xy": result, "confidence": confidence, "source": "screen_digit_templates"}


def calibrate(session, observation_id, x_region, y_region, light, level, samples):
    session.check()
    session._frame(observation_id, fresh=True)
    frame = session.read_frame(observation_id)
    with Image.open(io.BytesIO(base64.b64decode(frame["png_base64"]))) as image:
        size = list(image.size)
        digits = []
        if not isinstance(samples, list) or not 1 <= len(samples) <= 30:
            raise BridgeError("INVALID_COORDINATE_PROFILE", "관찰된 숫자 표본 1~30개가 필요합니다.")
        for sample in samples:
            if not isinstance(sample, dict) or set(sample) != {"digit", "frame_id", "region"}:
                raise BridgeError("INVALID_COORDINATE_PROFILE", "표본에 digit, frame_id, region을 지정하세요.")
            evidence = session.read_frame(sample["frame_id"])
            with Image.open(io.BytesIO(base64.b64decode(evidence["png_base64"]))) as source:
                if list(source.size) != size or not rectangle(sample["region"], size):
                    raise BridgeError("INVALID_COORDINATE_PROFILE", "표본 크기·영역 불일치")
                output = io.BytesIO()
                source.crop(sample["region"]).save(output, format="PNG")
            raw = output.getvalue()
            digits.append({"digit": sample["digit"], "png": base64.b64encode(raw).decode(),
                           "sha256": hashlib.sha256(raw).hexdigest()})
        profile = validate_profile({"version": 1, "size": size, "x_region": x_region, "y_region": y_region,
                                    "light": light, "level": level, "digits": digits})
        reading = CoordinateReader(profile).read(image)
    session.learning.store.put(session.learning.task, "coordinate_profile", "active", profile)
    return {"saved_and_reloaded": True, "reading": reading,
            "observed_digits": sorted({d["digit"] for d in digits}), "unseen_digits_are_rejected": True}


def validate_navigation(action):
    from .behaviors import _image, _rectangle
    if (set(action) != {"kind", "profile", "target", "axis_order", "hold_ms", "max_ms", "scene_guard"}
            or not isinstance(action["target"], list) or len(action["target"]) != 2
            or any(type(v) is not int or not 0 <= v <= 9999 for v in action["target"])
            or action["axis_order"] not in {"xy", "yx"}
            or type(action["hold_ms"]) is not int or not 50 <= action["hold_ms"] <= 500
            or type(action["max_ms"]) is not int or not 100 <= action["max_ms"] <= 4000):
        raise BridgeError("INVALID_NAVIGATION", "좌표 이동은 GPT 목표·축 순서·50~500ms 유지·최대 4초가 필요합니다.")
    profile = validate_profile(action["profile"])
    guard = action["scene_guard"]
    if (not isinstance(guard, dict) or set(guard) != {"region", "threshold", "sha256", "png", "frame_id"}
            or not _rectangle(guard["region"], profile["size"]) or not number(guard["threshold"], .9, 1)):
        raise BridgeError("INVALID_NAVIGATION", "맵 이름 등 이동 중에도 유지되는 장면 조건이 필요합니다.")
    from PIL import ImageStat
    if max(ImageStat.Stat(_image(guard)).stddev) < 3:
        raise BridgeError("WEAK_GUARD", "단색 배경은 이동 장면 조건으로 사용할 수 없습니다.")
    return copy.deepcopy(action)


def execute_navigation(action, backend, region, check, *, clock=time.monotonic):
    from .behaviors import matches
    action = validate_navigation(action)
    reader = CoordinateReader(action["profile"])
    target = action["target"]
    started = clock()
    previous, previous_direction, stagnant = None, None, 0
    trajectory = []
    completed = 0
    reason = "time_slice_complete"
    # GPT owns intent and route choice; this loop performs only mechanical
    # feedback. Blocked movement returns to GPT, never learns permanent walls.
    for _ in range(40):
        check()
        if (clock() - started) * 1000 >= action["max_ms"]:
            break
        image, current = backend.capture()
        if list(current) != list(region):
            raise BridgeError("TARGET_MOVED", "이동 중 창 위치가 바뀌었습니다.")
        if not matches(image, action["scene_guard"])[0]:
            reason = "scene_changed"
            break
        try:
            reading = reader.read(image)
        except BridgeError as exc:
            if exc.code != "COORDINATE_UNCERTAIN":
                raise
            reason = "coordinate_uncertain"
            break
        xy = reading["xy"]
        reading["elapsed_ms"] = round((clock() - started) * 1000, 1)
        trajectory.append(reading)
        if previous is not None:
            delta = abs(xy[0] - previous[0]) + abs(xy[1] - previous[1])
            if delta > 8:
                reason = "coordinate_jump"
                break
            stagnant = stagnant + 1 if delta == 0 else 0
            if stagnant >= 2:
                reason = "blocked"
                break
            axis = 0 if previous_direction in {"left", "right"} else 1
            expected_sign = 1 if previous_direction in {"right", "down"} else -1
            if delta and ((xy[axis] - previous[axis]) * expected_sign <= 0 or xy[1-axis] != previous[1-axis]):
                reason = "unexpected_motion"
                break
            if (target[axis] - previous[axis]) * (target[axis] - xy[axis]) < 0:
                reason = "overshot"
                break
        if xy == target:
            # Confirm with a separate current capture, never infer arrival from ACK.
            confirmation, confirmation_region = backend.capture()
            if list(confirmation_region) != list(region):
                raise BridgeError("TARGET_MOVED", "도착 확인 중 창 위치 변경")
            try:
                arrived = matches(confirmation, action["scene_guard"])[0] and reader.read(confirmation)["xy"] == target
                reason = "arrived" if arrived else "arrival_uncertain"
            except BridgeError as exc:
                if exc.code != "COORDINATE_UNCERTAIN":
                    raise
                reason = "arrival_uncertain"
            break
        axes = [0, 1] if action["axis_order"] == "xy" else [1, 0]
        axis = next(a for a in axes if xy[a] != target[a])
        direction = (("right" if target[0] > xy[0] else "left") if axis == 0
                     else ("down" if target[1] > xy[1] else "up"))
        distance = abs(target[axis] - xy[axis])
        duration = min(action["hold_ms"], 100 if distance <= 2 else action["hold_ms"])
        if (clock() - started) * 1000 + duration > action["max_ms"]:
            break
        check()
        try:
            if not backend.send({"kind": "hold", "keys": [direction], "duration_ms": duration}, region, check):
                raise BridgeError("DELIVERY_UNKNOWN", "좌표 이동 입력 결과 불확실. 재전송하지 않습니다.")
        except BridgeError as exc:
            if completed and exc.code == "TIMED_INPUT_UNSUPPORTED":
                raise BridgeError("DELIVERY_UNKNOWN", "좌표 이동 일부 실행 후 장치 기능이 사라졌습니다.") from exc
            raise
        reading["input"] = {"direction": direction, "duration_ms": duration}
        completed += 1
        previous, previous_direction = xy, direction
    return {"reason": reason, "target": target, "trajectory": trajectory,
            "elapsed_ms": round((clock() - started) * 1000, 1), "game_success": False}
