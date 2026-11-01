"""Check AI-provided measurements; never infer actions or timings from pixels."""

from .contracts import BridgeError, number


def validate_step_timing(service, rule, step):
    timing = step.get("timing")
    if timing is None:
        return False
    if not isinstance(timing, dict):
        raise BridgeError("INVALID_TIMING", "timing must be an object")
    record = service.selected_media[step["recording_id"]]
    fps, duration = record.get("fps"), record.get("duration_seconds")
    if not number(fps, .1, 1000) or not number(duration, 0, 86400):
        raise BridgeError("ANALYSIS_REQUIRED", "Read recording metadata before reporting timing")
    precision = timing.get("precision_seconds")
    if not number(precision, 1 / fps - .00001, max(.1, 1 / fps) + .00001):
        raise BridgeError("INVALID_TIMING", "State video-frame precision; do not claim sub-frame accuracy")
    start, end, next_start = (timing.get(k) for k in ("start_seconds", "end_seconds", "next_start_seconds"))
    if not (number(start, 0, duration) and number(end, start, duration) and number(next_start, end, duration)):
        raise BridgeError("INVALID_TIMING", "Use ordered source-video start/end/next-start times")
    for name, seconds in (("start_frame_trace_id", start), ("end_frame_trace_id", end)):
        trace = service.frame_traces.get(timing.get(name))
        if not trace or trace["recording_id"] != step["recording_id"] or trace["source_sha256"] != record["sha256"]:
            raise BridgeError("INVALID_TIMING", "Timing anchors need frames from the selected recording")
        if abs(trace["requested_seconds"] - seconds) > precision + .00001:
            raise BridgeError("INVALID_TIMING", "Inspect frames at the measured action boundaries")
    gap = next_start - end
    if rule.get("wait_random") or abs(rule.get("wait_after", .5) - gap) > .001:
        raise BridgeError("INVALID_TIMING", f"wait_after must equal next_start_seconds-end_seconds ({gap:.6f}); disable wait_random")
    mode = timing.get("text_mode", "not_text")
    if rule.get("action_type") == "type":
        if mode not in {"typed", "paste"}:
            raise BridgeError("INVALID_TIMING", "Decide from footage whether text grows gradually (typed) or appears at once (paste)")
        offsets = rule.get("typing_timestamps") or []
        if mode == "typed":
            if not offsets or abs(offsets[-1] - (end - start)) > precision + .00001:
                raise BridgeError("INVALID_TIMING", "Gradual typing needs typing_timestamps spanning the observed first-to-last character time; typing_delay alone may be ignored")
        elif offsets:
            raise BridgeError("INVALID_TIMING", "Do not claim bulk paste with a per-character timeline")
    return True
