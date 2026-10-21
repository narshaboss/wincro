"""Thread-safe delivery state: pending, failed/backoff, and confirmed cooldown."""

import hashlib
import threading
import time
from dataclasses import dataclass

from .discord_notifier import DiscordSendResult, is_retryable_discord_result


@dataclass(frozen=True)
class DeliveryTicket:
    key: tuple[str, str]
    token: object


@dataclass
class _DeliveryEntry:
    token: object | None = None
    pending_since: float = 0.0
    last_success: float | None = None
    retry_at: float = 0.0
    failures: int = 0


class DiscordDeliveryState:
    def __init__(self, clock=None):
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._entries: dict[tuple[str, str], _DeliveryEntry] = {}

    def begin(self, event_key: str, destination: str, cooldown: float):
        fingerprint = hashlib.sha256(destination.strip().encode("utf-8")).hexdigest()[:12]
        key = (event_key, fingerprint)
        now = self._clock()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                if len(self._entries) >= 128:
                    disposable = next((k for k, v in self._entries.items() if v.token is None), None)
                    if disposable is None:
                        return None, "capacity", 30.0
                    del self._entries[disposable]
                entry = self._entries[key] = _DeliveryEntry()
            if entry.token is not None and now - entry.pending_since < 120:
                return None, "pending", 120 - (now - entry.pending_since)
            if entry.last_success is not None and now - entry.last_success < cooldown:
                return None, "cooldown", cooldown - (now - entry.last_success)
            if now < entry.retry_at:
                return None, "retry_backoff", entry.retry_at - now
            entry.token = object()
            entry.pending_since = now
            return DeliveryTicket(key, entry.token), "ready", 0.0

    def finish(self, ticket: DeliveryTicket, result: DiscordSendResult):
        now = self._clock()
        with self._lock:
            entry = self._entries.get(ticket.key)
            if entry is None or entry.token is not ticket.token:
                return False, 0.0
            entry.token = None
            if result.ok:
                entry.last_success = now
                entry.failures = 0
                entry.retry_at = 0.0
                return True, 0.0
            entry.failures += 1
            # Permanent HTTP errors should not be hammered by many PCs. A
            # transient failure is retryable without consuming success cooldown.
            delay = min(300.0, 30.0 * 2 ** min(entry.failures - 1, 4))
            if not is_retryable_discord_result(result):
                delay = 300.0
            delay = max(delay, result.retry_after)
            entry.retry_at = now + delay
            return True, delay
