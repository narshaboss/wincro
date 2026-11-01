"""Presentation-only estimates, never a measure of GPT comprehension or quality."""

from dataclasses import dataclass
import math
import time


@dataclass(frozen=True)
class ProgressState:
    value: float
    phase: str

    @property
    def percent(self):
        return round(self.value * 100)


class AnalysisProgress:
    def __init__(self, clock=None):
        self._clock = clock or time.monotonic
        self.reset()

    def reset(self):
        self._job_id = None
        self._value = 0.0
        self._started = self._last = self._running_since = None

    def update(self, state, busy, *, cancelling=False, now=None):
        if not state or not state.get("job_id"):
            self.reset()
            return ProgressState(0.0, "idle")
        now = self._clock() if now is None else now
        identity = (state["job_id"], state.get("attempt", 1))
        if identity != self._job_id:
            self._job_id = identity
            self._value = .01
            self._started = self._last = now
            self._running_since = None
        elapsed = max(0.0, now - self._last)
        self._last = now
        status = state.get("status")
        if status == "review" and not busy:
            self._value = 1.0
            return ProgressState(self._value, "complete")
        self._value = min(self._value, .99)
        if status in {"error", "cancelled", "interrupted", "unconfirmed", "needs_review"} or not busy:
            self._value = min(self._value, .95)
            phase = status if status in {"error", "cancelled", "needs_review"} else "unconfirmed"
            return ProgressState(self._value, phase)
        if cancelling or status == "cancelling":
            return ProgressState(self._value, "cancelling")
        if status == "review":
            target, phase = .99, "finishing"
        elif status == "running":
            if self._running_since is None:
                self._running_since = now
            target = .15 + .8 * (1 - math.exp(-max(0.0, now - self._running_since) / 90))
            target, phase = min(.95, target), "running"
        elif status == "starting":
            target = .01 + .11 * (1 - math.exp(-max(0.0, now - self._started) / 8))
            phase = "starting"
        else:
            return ProgressState(min(self._value, .95), "unconfirmed")
        # Smooth phase transitions; elapsed time alone must never produce 100%.
        self._value = max(self._value, self._value + (target - self._value) * min(1.0, elapsed * 3))
        return ProgressState(self._value, phase)
