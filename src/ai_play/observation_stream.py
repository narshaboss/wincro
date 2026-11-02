"""Bounded, ephemeral selected-window observations; never an input authority."""

from collections import deque
import threading
import time


class ObservationStream:
    def __init__(self, capture, active, *, clock=time.monotonic, interval=.25, capacity=6):
        self.capture, self.active, self.clock = capture, active, clock
        self.interval = interval
        self.frames = deque(maxlen=capacity)
        self.lock = threading.Lock()
        self.capture_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.worker = None
        self.error = None

    def sample(self):
        with self.capture_lock:
            started = self.clock()
            image, region = self.capture()
            sample = {"time": started, "capture_ms": (self.clock() - started) * 1000,
                      "image": image.copy(), "region": list(region)}
            with self.lock:
                if self.frames and self.frames[-1]["region"] != sample["region"]:
                    self.frames.clear()
                self.frames.append(sample)
                while len(self.frames) > 1 and sum(s["image"].width * s["image"].height * 3 for s in self.frames) > 24_000_000:
                    self.frames.popleft()
                self.error = None
            return sample

    def start(self):
        if self.worker and self.worker.is_alive():
            return
        def run():
            while not self.stop_event.is_set():
                started = self.clock()
                if self.active():
                    try:
                        self.sample()
                    except Exception as exc:
                        with self.lock:
                            self.frames.clear()
                            self.error = str(exc)[:300]
                else:
                    self.clear()
                self.stop_event.wait(max(.02, self.interval - (self.clock() - started)))
        self.worker = threading.Thread(target=run, name="gpt-play-observations", daemon=True)
        self.worker.start()

    def recent(self, count=3, max_age=1.5):
        with self.lock:
            now = self.clock()
            return list(s for s in self.frames if 0 <= now - s["time"] <= max_age)[-count:]

    def clear(self):
        with self.lock:
            self.frames.clear()

    def close(self):
        self.stop_event.set()
        if self.worker and self.worker is not threading.current_thread():
            self.worker.join(2)
        self.clear()
        return not (self.worker and self.worker.is_alive())
