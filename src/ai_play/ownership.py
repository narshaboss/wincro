"""Input admission only. No navigation, recognition or existing playback policy."""

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import threading
import weakref


_lock = threading.RLock()
_players = weakref.WeakKeyDictionary()
_owner = None
_authorized = ContextVar("wincro_play_owner", default=None)


def lab_active():
    with _lock:
        return _owner is not None


def device_change_allowed():
    """Permit explicit recovery only after all managed input has stopped."""
    with _lock:
        if any(starting or _active(player) for player, starting in list(_players.items())):
            return False
        if _owner is None:
            return True
        session = getattr(_owner, "session", None)
        return bool(session and session.state in {"paused", "stopped", "needs_review", "error"}
                    and not session.io_lock.locked()
                    and not (session.playback and session.playback.busy)
                    and not _owner.preparing.is_set())


def _active(player):
    for name in ("_execution_thread", "_play_thread", "_thread"):
        thread = getattr(player, name, None)
        if thread is not None and thread.is_alive():
            return True
    if getattr(player, "is_running", False):
        return True
    state = getattr(getattr(player, "state", None), "value", "")
    return state in {"running_initial", "monitoring", "paused"}


def legacy_start(method):
    @wraps(method)
    def guarded(self, *args, **kwargs):
        with _lock:
            if _owner is not None and _authorized.get() is not _owner:
                return False
            _players[self] = _players.get(self, 0) + 1
        try:
            return method(self, *args, **kwargs)
        finally:
            with _lock:
                _players[self] -= 1
    return guarded


def acquire(owner):
    global _owner
    with _lock:
        if _owner is not None or any(starting or _active(p) for p, starting in list(_players.items())):
            raise ValueError("기존 재생 또는 다른 GPT 플레이가 실행 중입니다. 먼저 중지하세요.")
        _owner = owner


def release(owner):
    global _owner
    with _lock:
        if _owner is owner:
            _owner = None


@contextmanager
def authorized(owner):
    with _lock:
        if _owner is not owner:
            raise ValueError("GPT 플레이 입력 소유권이 없습니다.")
    token = _authorized.set(owner)
    try:
        yield
    finally:
        _authorized.reset(token)
