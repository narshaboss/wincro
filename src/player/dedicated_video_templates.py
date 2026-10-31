"""Bounded, content-aware frame banks for dedicated video actions only."""

from collections import OrderedDict
import hashlib
from pathlib import Path
import threading

import cv2
import numpy as np


MAX_PROBES = 240
MAX_FRAMES = 32
MAX_PROBE_PIXELS = 4_000_000
MAX_CACHE_BYTES = 16_000_000
MAX_ENCODED_BYTES = 64_000_000
_cache = OrderedDict()
_lock = threading.Lock()


def get_dedicated_video_frames(path, stop_event=None):
    """None requests legacy fallback; empty means unreadable/cancelled media."""
    stopped = lambda: stop_event is not None and stop_event.is_set()
    if stopped():
        return []
    path = Path(path)
    try:
        with path.open("rb") as source:
            encoded = source.read(MAX_ENCODED_BYTES + 1)
        if len(encoded) > MAX_ENCODED_BYTES:
            return None
        key = (str(path.resolve()), hashlib.sha256(encoded).digest())
        del encoded
        with _lock:
            if key in _cache:
                _cache.move_to_end(key)
                return list(_cache[key])
        cap = cv2.VideoCapture(str(path))
        frames = []
        try:
            if not cap.isOpened():
                return []
            count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            area = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) * int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            if count <= 0 or area <= 0:
                return None
            probes = min(count, MAX_PROBES, MAX_PROBE_PIXELS // area)
            if probes < min(8, count):
                return None
            wanted = np.linspace(0, count - 1, probes, dtype=int)
            pixels = 0
            for index in wanted:
                if stopped():
                    return []
                if probes != count:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, int(index))
                ok, frame = cap.read()
                if not ok or frame is None:
                    continue
                pixels += frame.shape[0] * frame.shape[1]
                if pixels > MAX_PROBE_PIXELS:
                    return None
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                if min(gray.shape) < 4 or float(gray.std()) < 1.0:
                    continue
                frames.append((gray, gray.shape[0], gray.shape[1], frame, f"frame={int(index)}"))
        finally:
            cap.release()
        if not frames or stopped():
            return []
        vectors = np.asarray([f[0].reshape(-1) for f in frames], dtype=np.float32)
        vectors -= vectors.mean(axis=1, keepdims=True)
        vectors /= np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-8)
        labs = [cv2.cvtColor(f[3], cv2.COLOR_BGR2LAB).astype(np.float32) for f in frames]
        selected = list(dict.fromkeys(np.linspace(0, len(frames) - 1, min(8, len(frames)), dtype=int).tolist()))
        best = np.full(len(frames), -1.0, dtype=np.float32)

        def cover(index):
            scores = vectors @ vectors[index]
            for i in np.flatnonzero(scores >= 0.95):
                if abs(float(labs[i][:, :, 0].mean() - labs[index][:, :, 0].mean())) > 28:
                    continue
                if float(np.linalg.norm(labs[i][:, :, 1:3] - labs[index][:, :, 1:3], axis=2).mean()) > 18:
                    continue
                best[i] = max(best[i], scores[i])
            best[index] = 1.0

        for index in selected:
            cover(index)
        while len(selected) < min(MAX_FRAMES, len(frames)) and float(best.min()) < 0.95:
            if stopped():
                return []
            index = int(best.argmin())
            selected.append(index)
            cover(index)
        result = tuple(frames[i] for i in selected)
        for gray, _h, _w, bgr, _label in result:
            gray.flags.writeable = False
            bgr.flags.writeable = False
        size = sum(f[0].nbytes + f[3].nbytes for f in result)
        with _lock:
            for old in list(_cache):
                if old[0] == key[0]:
                    del _cache[old]
            while _cache and (len(_cache) >= 4 or sum(f[0].nbytes + f[3].nbytes for bank in _cache.values() for f in bank) + size > MAX_CACHE_BYTES):
                _cache.popitem(last=False)
            if size <= MAX_CACHE_BYTES:
                _cache[key] = result
        return list(result)
    except (OSError, ValueError, cv2.error):
        return []
