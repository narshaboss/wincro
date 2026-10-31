"""Offline, explicitly selected media only. No screen capture or input hooks."""

import base64
import hashlib
from pathlib import Path

from .contracts import BridgeError, number
from .storage import new_id, publish_bytes


class MediaTools:
    def __init__(self, service):
        self.service = service

    def register(self, path, input_log=None, excluded=()):
        """UI-only consent boundary; never exposed as an MCP tool."""
        path = Path(path).resolve(strict=True)
        if path.suffix.lower() not in {".mp4", ".avi", ".mkv", ".webm", ".png"} or not path.is_file():
            raise BridgeError("INVALID_MEDIA", "Select a supported recording/image file")
        if path.stat().st_size > 1_000_000_000:
            raise BridgeError("LIMIT_REACHED", "Select a recording under 1GB")
        intervals = []
        for pair in excluded:
            if not (isinstance(pair, (list, tuple)) and len(pair) == 2
                    and number(pair[0], 0, 86400) and number(pair[1], pair[0], 86400)):
                raise BridgeError("INVALID_FIELD", "Invalid private time interval")
            intervals.append(list(pair))
        identity = self._hash(path)
        log = None
        if input_log:
            log = Path(input_log).resolve(strict=True)
            if not log.is_file() or log.stat().st_size > 2_000_000:
                raise BridgeError("LIMIT_REACHED", "Select an input log under 2MB")
        key = new_id("recording_")
        with self.service.lock:
            self.service._check()
            self.service.selected_media[key] = {"path": path, "sha256": identity,
                "stat": (path.stat().st_size, path.stat().st_mtime_ns),
                "input_log": log, "log_sha256": self._hash(log) if log else None,
                "excluded": intervals}
        return {"recording_id": key, "name": path.name, "sha256": identity}

    @staticmethod
    def _hash(path):
        hasher = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1_000_000), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    def _get(self, recording_id):
        self.service._check()
        record = self.service.selected_media.get(recording_id)
        if not record:
            raise BridgeError("PERMISSION_REQUIRED", "Select this recording in WinCro first")
        stat = record["path"].stat()
        if (stat.st_size, stat.st_mtime_ns) != record["stat"] or self._hash(record["path"]) != record["sha256"]:
            raise BridgeError("MEDIA_NOT_READY", "Selected recording changed; select it again")
        return record

    @staticmethod
    def _private(record, seconds):
        if any(start - .1 <= seconds <= end + .1 for start, end in record["excluded"]):
            raise BridgeError("PRIVATE_INTERVAL", "This interval was excluded by the user")

    def _frame(self, record, seconds):
        import cv2
        if not number(seconds, 0, 86400):
            raise BridgeError("INVALID_FIELD", "Invalid recording time")
        self._private(record, seconds)
        if record["path"].suffix.lower() == ".png":
            if seconds != 0:
                raise BridgeError("INVALID_FIELD", "Image timestamp must be zero")
            import numpy as np
            from PIL import Image
            with Image.open(record["path"]) as header:
                if header.width * header.height > 16_000_000:
                    raise BridgeError("LIMIT_REACHED", "Image exceeds 16 megapixels")
            frame = cv2.imdecode(np.frombuffer(record["path"].read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
            actual = 0.0
        else:
            cap = cv2.VideoCapture(str(record["path"]))
            try:
                fps, count = cap.get(cv2.CAP_PROP_FPS), cap.get(cv2.CAP_PROP_FRAME_COUNT)
                if not number(fps, .1, 1000) or not number(count, 1, 10_000_000) or seconds >= count / fps:
                    raise BridgeError("INVALID_MEDIA", "Time is outside a decodable recording")
                if not 1 <= cap.get(cv2.CAP_PROP_FRAME_WIDTH) * cap.get(cv2.CAP_PROP_FRAME_HEIGHT) <= 16_000_000:
                    raise BridgeError("LIMIT_REACHED", "Frame exceeds 16 megapixels")
                index = int(seconds * fps)
                cap.set(cv2.CAP_PROP_POS_FRAMES, index)
                ok, frame = cap.read()
                if not ok:
                    frame = None
                actual = max(0, cap.get(cv2.CAP_PROP_POS_FRAMES) - 1) / fps
                self._private(record, actual)
            finally:
                cap.release()
        if frame is None or frame.shape[0] * frame.shape[1] > 16_000_000:
            raise BridgeError("INVALID_MEDIA", "Frame is missing or over 16 megapixels")
        return frame, actual

    def read_recording(self, recording_id):
        record = self._get(recording_id)
        return {"recording_id": recording_id, "name": record["path"].name,
                "sha256": record["sha256"], "input_log_selected": bool(record["input_log"]),
                "excluded_intervals": record["excluded"],
                "timing_status": "legacy_timing_unverified", "coordinate_origin": "user_must_confirm"}

    def get_frames(self, recording_id, timestamps):
        import cv2
        if not isinstance(timestamps, list) or not 1 <= len(timestamps) <= 4:
            raise BridgeError("LIMIT_REACHED", "Request 1 to 4 frames at a time")
        record = self._get(recording_id)
        result = []
        if len(self.service.frame_traces) + len(timestamps) > 2048:
            raise BridgeError("LIMIT_REACHED", "Frame trace budget reached; open a new editor session")
        for seconds in timestamps:
            frame, actual = self._frame(record, seconds)
            h, w = frame.shape[:2]
            scale = min(1.0, 1280 / w, 900 / h)
            preview = cv2.resize(frame, (round(w * scale), round(h * scale))) if scale < 1 else frame
            ok, data = cv2.imencode(".jpg", preview, [cv2.IMWRITE_JPEG_QUALITY, 85])
            if not ok:
                raise BridgeError("INVALID_MEDIA", "Frame encoding failed")
            result.append({"requested_seconds": seconds, "actual_seconds": actual,
                           "original_width": w, "original_height": h, "preview_scale": scale,
                           "mime_type": "image/jpeg", "base64": base64.b64encode(data).decode("ascii")})
            trace_id = new_id("frame_")
            self.service.frame_traces[trace_id] = {"recording_id": recording_id,
                "requested_seconds": seconds, "source_sha256": record["sha256"]}
            result[-1]["frame_trace_id"] = trace_id
        return {"frames": result}

    def get_input_events(self, recording_id, start=0, end=60, offset=0):
        if not number(start, 0, 86400) or not number(end, start, min(86400, start + 120)) or type(offset) is not int or offset < 0:
            raise BridgeError("INVALID_FIELD", "Use a valid interval up to 120 seconds")
        record = self._get(recording_id)
        if not record["input_log"] or self._hash(record["input_log"]) != record["log_sha256"]:
            raise BridgeError("MEDIA_NOT_READY", "Select an unchanged input log in WinCro")
        from .storage import read_json
        rows = read_json(record["input_log"]).get("events", [])
        allowed = {"event_type", "timestamp", "x", "y", "button", "pressed", "key", "modifiers", "scroll_dx", "scroll_dy", "drag_duration"}
        selected = []
        for row in rows:
            seconds = row.get("timestamp")
            if not number(seconds, start, end) or any(a - .1 <= seconds <= b + .1 for a, b in record["excluded"]):
                continue
            selected.append({k: v for k, v in row.items() if k in allowed})
        return {"events": selected[offset:offset + 200], "total": len(selected),
                "next_offset": offset + 200 if offset + 200 < len(selected) else None,
                "timing_status": "legacy_timing_unverified"}

    def create_asset_crop(self, recording_id, seconds, region, draft_id, expected_revision):
        import cv2
        with self.service.lock:
            state = self.service._load(draft_id)
            self.service._expect(state, expected_revision)
            if len(state["asset_files"]) >= 200:
                raise BridgeError("LIMIT_REACHED", "Keep at most 200 draft assets")
            record = self._get(recording_id)
            frame, actual = self._frame(record, seconds)
            h, w = frame.shape[:2]
            if not (isinstance(region, list) and len(region) == 4 and all(type(v) is int for v in region)
                    and 0 <= region[0] < region[2] <= w and 0 <= region[1] < region[3] <= h
                    and (region[2] - region[0]) * (region[3] - region[1]) <= 1_000_000):
                raise BridgeError("INVALID_FIELD", "Use original-frame crop coordinates, at most 1MP")
            crop = frame[region[1]:region[3], region[0]:region[2]]
            ok, data = cv2.imencode(".png", crop)
            if not ok:
                raise BridgeError("INVALID_MEDIA", "Crop encoding failed")
            name = new_id("ai_") + ".png"
            publish_bytes(self.service.workspace / "assets", name, data.tobytes())
            state["asset_files"][name] = {"sha256": hashlib.sha256(data.tobytes()).hexdigest(),
                "source_sha256": record["sha256"], "seconds": actual, "region": region}
            state["revision"] += 1
            state["approval"] = None
            state["analysis"] = None
            self.service._save(state)
            return {"asset_name": name, "revision": state["revision"], "region": region,
                    "source_seconds": actual, "saved_and_reloaded": True}
