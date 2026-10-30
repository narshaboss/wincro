"""Real Win32 QA, opt-in only on an isolated GitHub Actions Windows desktop.

Ordinary collection skips before importing native/project code or launching a
process. No input emulation, WinCro GUI, foreground-policy bypass, or mutation
of an existing window is permitted. Only the disposable Tk child is controlled.
Run serially, separately from other GUI QA on the isolated runner.
"""

import os
import sys

import pytest


def _ci_enabled():
    return (
        os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("WINCRO_WINDOW_CONTROL_UI_QA") == "1"
        and sys.platform == "win32"
    )


if not _ci_enabled():
    pytest.skip(
        "Native window QA requires GITHUB_ACTIONS=true, WINCRO_WINDOW_CONTROL_UI_QA=1, and win32.",
        allow_module_level=True,
    )


import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import subprocess
import time
from types import SimpleNamespace
import uuid

from src.utils.window_control import WindowController, WindowControlResult, _WindowsBackend


RESULTS_PATH = Path(__file__).resolve().parents[1] / "artifacts/window_control/native_results.json"
OPERATION_SECONDS = 3.0
POLL_SECONDS = 0.05
CHILD_SCRIPT = """
import sys
import tkinter as tk

root = tk.Tk()
root.title(sys.argv[1])
root.geometry("300x180+80+80")
root.state("normal")
root.after(60000, root.destroy)
root.mainloop()
"""


def _require_ci():
    if not _ci_enabled():
        raise AssertionError("Native QA opt-in was revoked; refusing any further native requests.")


def _check(condition, message):
    if not condition:
        # Controlled messages never serialize a WindowInfo, HWND, or child PID.
        raise AssertionError(message)


def _pause(deadline):
    _require_ci()
    remaining = deadline - time.monotonic()
    if remaining > 0:
        time.sleep(min(POLL_SECONDS, remaining))


class _UserObjectFlags(ctypes.Structure):
    _fields_ = [
        ("inherit", wintypes.BOOL),
        ("reserved", wintypes.BOOL),
        ("flags", wintypes.DWORD),
    ]


class _NativeProbe:
    """Read-only desktop and geometry probes using the same real ctypes DLLs."""

    def __init__(self, api):
        _require_ci()
        self.u = api.user32
        self.k = api.kernel32
        bindings = (
            (self.u.GetWindowRect, [wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
            (self.u.GetClientRect, [wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
            (self.u.GetProcessWindowStation, [], wintypes.HANDLE),
            (self.u.GetThreadDesktop, [wintypes.DWORD], wintypes.HANDLE),
            (self.k.GetCurrentThreadId, [], wintypes.DWORD),
            (self.u.OpenInputDesktop, [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            (self.u.CloseDesktop, [wintypes.HANDLE], wintypes.BOOL),
            (self.u.GetUserObjectInformationW, [
                wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID,
                wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
            ], wintypes.BOOL),
        )
        for function, arguments, result in bindings:
            function.argtypes = arguments
            function.restype = result

    def _desktop_name(self, desktop):
        name = ctypes.create_unicode_buffer(256)
        needed = wintypes.DWORD()
        if not self.u.GetUserObjectInformationW(
            desktop, 2, name, ctypes.sizeof(name), ctypes.byref(needed),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return name.value

    def desktop_status(self):
        _require_ci()
        station = self.u.GetProcessWindowStation()
        if not station:
            raise ctypes.WinError(ctypes.get_last_error())
        flags = _UserObjectFlags()
        needed = wintypes.DWORD()
        if not self.u.GetUserObjectInformationW(
            station, 1, ctypes.byref(flags), ctypes.sizeof(flags), ctypes.byref(needed),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        if not flags.flags & 1:  # WSF_VISIBLE
            return {"accessible": False, "reason": "The runner window station is not visible."}
        desktop = self.u.OpenInputDesktop(0, False, 1)  # DESKTOP_READOBJECTS only
        if not desktop:
            return {
                "accessible": False,
                "reason": f"OpenInputDesktop failed (WinError {ctypes.get_last_error()}).",
            }
        try:
            thread_desktop = self.u.GetThreadDesktop(self.k.GetCurrentThreadId())
            if not thread_desktop:
                raise ctypes.WinError(ctypes.get_last_error())
            names = {
                "input_desktop": self._desktop_name(desktop),
                "thread_desktop": self._desktop_name(thread_desktop),
            }
            if names["input_desktop"] != names["thread_desktop"]:
                return {"accessible": False, "reason": "The runner desktop is not the input desktop.", **names}
            return {"accessible": True, **names}
        finally:
            if not self.u.CloseDesktop(desktop):
                raise ctypes.WinError(ctypes.get_last_error())

    def snapshot(self, window):
        _require_ci()
        _check(bool(self.u.IsWindow(window.handle)), "The fixture window no longer exists.")
        rectangle = wintypes.RECT()
        client = wintypes.RECT()
        if not self.u.GetWindowRect(window.handle, ctypes.byref(rectangle)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not self.u.GetClientRect(window.handle, ctypes.byref(client)):
            raise ctypes.WinError(ctypes.get_last_error())
        return {
            "minimized": bool(self.u.IsIconic(window.handle)),
            "maximized": bool(self.u.IsZoomed(window.handle)),
            "foreground_matches": self.u.GetForegroundWindow() == window.handle,
            "window_size": [rectangle.right - rectangle.left, rectangle.bottom - rectangle.top],
            "client_size": [client.right - client.left, client.bottom - client.top],
        }


class _FixtureBackend(_WindowsBackend):
    """Real native backend with child-only mutation guards and return-value tracing."""

    def __init__(self, title, report):
        _require_ci()
        super().__init__()
        self.child = None
        self.title = title
        self.report = report
        self.phase = "setup"

    def _is_fixture(self, window):
        return (
            window is not None
            and self.child is not None
            and window.process_id == self.child.pid
            and window.title == self.title
        )

    def get_window(self, handle):
        window = super().get_window(handle)
        return window if self._is_fixture(window) else None

    def require_owned(self, window):
        _require_ci()
        _check(self.child is not None and self.child.poll() is None, "The fixture child exited unexpectedly.")
        _check(self._is_fixture(window), "Refusing to access a window not owned by the fixture child.")
        _check(window.executable_name.casefold() == "python.exe", "The fixture must be enumerated as python.exe.")
        current = self.get_window(window.handle)
        _check(
            current is not None
            and current.process_creation_time == window.process_creation_time
            and current.thread_id == window.thread_id,
            "The fixture window identity changed; refusing a native request.",
        )

    def _record(self, kind, operation, accepted):
        _check(type(accepted) is bool, "A native backend mutator did not return a bool.")
        self.report["requests"].append({
            "phase": self.phase, "kind": kind, "operation": operation,
            "backend_return": accepted,
        })
        return accepted

    def show_window_async(self, window, operation):
        self.require_owned(window)
        return self._record("show_window", operation, super().show_window_async(window, operation))

    def set_foreground_window(self, window):
        self.require_owned(window)
        return self._record("foreground", "foreground", super().set_foreground_window(window))


def _require_desktop(probe, report):
    status = probe.desktop_status()
    report["desktop"] = status
    if not status["accessible"]:
        report["status"] = "skipped_desktop_inaccessible"
        report["limitations"].append({"kind": "desktop_inaccessible", "detail": status["reason"]})
        pytest.skip(f"Isolated runner desktop inaccessible: {status['reason']}")


def _snapshot(fixture):
    fixture.backend.require_owned(fixture.window)
    return fixture.probe.snapshot(fixture.window)


def _exercise(fixture, label, operation, *, maximized=False, preserve=None):
    _require_ci()
    _require_desktop(fixture.probe, fixture.report)
    fixture.backend.phase = label
    record = {"label": label, "operation": operation, "samples": [], "outcome": "running"}
    fixture.report["operations"].append(record)
    config = {**fixture.config, "operation": operation}
    start = time.monotonic()
    deadline = start + OPERATION_SECONDS
    while time.monotonic() < deadline:
        _require_ci()
        _require_desktop(fixture.probe, fixture.report)
        fixture.backend.require_owned(fixture.window)
        result = fixture.controller.step(config)
        _require_desktop(fixture.probe, fixture.report)
        _check(isinstance(result, WindowControlResult), "step() returned the wrong result type.")
        _check(result.status in {"ready", "pending"}, "The established native target became unavailable.")
        _check(type(result.ready) is bool and result.ready == (result.status == "ready"), "Invalid ready/status contract.")
        state = _snapshot(fixture)
        record["samples"].append({
            "elapsed_seconds": round(time.monotonic() - start, 4),
            "status": result.status, "ready": result.ready, "message": result.message,
            **state,
        })
        geometry_ok = (
            state["minimized"] if operation == "minimize"
            else not state["minimized"] and state["maximized"] == maximized
        )
        if preserve is not None and not state["minimized"]:
            _check(state["maximized"] == maximized, "An idempotent/foreground request changed maximization.")
            _check(state["window_size"] == preserve["window_size"], "The request resized the fixture window.")
            _check(state["client_size"] == preserve["client_size"], "The request resized the fixture client area.")
        if result.ready:
            _check(geometry_ok, "step() claimed ready without the required native geometry state.")
            _check(
                operation == "minimize" or state["foreground_matches"],
                "step() claimed ready although GetForegroundWindow did not match the target.",
            )
            record.update(outcome="verified_ready", geometry_verified=True, controller_ready=True)
            return state
        _pause(deadline)
    _require_desktop(fixture.probe, fixture.report)
    _check(bool(record["samples"]), "The bounded operation made no verification attempt.")
    final = record["samples"][-1]
    geometry_ok = (
        final["minimized"] if operation == "minimize"
        else not final["minimized"] and final["maximized"] == maximized
    )
    _check(geometry_ok, f"{label}: native minimize/restore/maximize state was not applied within three seconds.")
    if operation != "minimize" and not final["foreground_matches"]:
        requests = [event for event in fixture.report["requests"] if event["phase"] == label]
        _check(
            any(event["kind"] == "foreground" for event in requests),
            f"{label}: no foreground request was exercised; cannot attribute pending to foreground policy.",
        )
        record.update(outcome="foreground_limited", geometry_verified=True, controller_ready=False)
        fixture.report["limitations"].append({
            "kind": "foreground_not_granted", "operation": label,
            "detail": "Required geometry verified, but foreground was not granted/retained; no policy bypass attempted.",
            "foreground_request_returned_false": any(
                event["kind"] == "foreground" and not event["backend_return"] for event in requests
            ),
        })
        return final
    raise AssertionError(f"{label}: native state was reached but step() never reported ready within three seconds.")


def _require_show_request(fixture, label, operation):
    _check(
        any(
            event["phase"] == label and event["kind"] == "show_window" and event["operation"] == operation
            for event in fixture.report["requests"]
        ),
        f"{label}: the native show request was not exercised.",
    )


@pytest.fixture
def native_fixture():
    _require_ci()
    title = f"Window-control isolated CI fixture {uuid.uuid4().hex}"
    report = {
        "status": "running", "target": {"title_contains": title},
        "operations": [], "requests": [], "limitations": [],
        "limits": {"operation_seconds": OPERATION_SECONDS, "poll_seconds": POLL_SECONDS},
        "fixture_started": False,
    }
    child = None
    try:
        backend = _FixtureBackend(title, report)
        probe = _NativeProbe(backend._api)
        _require_desktop(probe, report)
        _require_ci()
        child = subprocess.Popen(
            [sys.executable, "-I", "-c", CHILD_SCRIPT, title],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        report["fixture_started"] = True
        backend.child = child
        controller = WindowController(backend)
        deadline = time.monotonic() + OPERATION_SECONDS
        window = None
        while time.monotonic() < deadline:
            _require_ci()
            _require_desktop(probe, report)
            _check(child.poll() is None, "The isolated Tk fixture exited before enumeration.")
            candidates = [
                item for item in controller.list_windows()
                if item.process_id == child.pid and item.title == title
            ]
            _check(len(candidates) <= 1, "More than one window matched the fixture child and unique title.")
            if candidates:
                window = candidates[0]
                break
            _pause(deadline)
        _require_desktop(probe, report)
        _check(window is not None, "Real native enumeration did not include the fixture within three seconds.")
        backend.require_owned(window)
        config = {"executable_name": window.executable_name, "title_contains": title}
        report["target"] = dict(config)
        report["enumeration_verified"] = True
        fixture = SimpleNamespace(
            backend=backend, controller=controller, probe=probe, window=window,
            config=config, report=report,
        )
        initial = _snapshot(fixture)
        report["initial_state"] = initial
        _check(not initial["minimized"] and not initial["maximized"], "The Tk fixture did not start normal.")
        _check(initial["client_size"] == [300, 180], "The Tk fixture did not start with a 300x180 client area.")
        fixture.initial = initial
        yield fixture
    except pytest.skip.Exception:
        raise
    except BaseException as exc:
        report["status"] = "failed"
        report["failure"] = {"type": type(exc).__name__, "detail": str(exc)}
        raise
    finally:
        cleanup_error = None
        if child is not None:
            try:
                if child.poll() is None:
                    child.terminate()
                child.wait(timeout=OPERATION_SECONDS)
                report["cleanup"] = {"child_exited": True, "returncode": child.returncode}
            except (OSError, subprocess.TimeoutExpired) as exc:
                cleanup_error = str(exc)
                report["cleanup"] = {"child_exited": False, "error": cleanup_error}
                report["status"] = "failed"
        _require_ci()
        RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        RESULTS_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if cleanup_error is not None:
            pytest.fail(f"Could not terminate/wait for the fixture child: {cleanup_error}")


def test_native_child_window_state_and_ready_contract(native_fixture):
    fixture = native_fixture
    try:
        _exercise(fixture, "minimize", "minimize")
        _require_show_request(fixture, "minimize", "minimize")
        _exercise(fixture, "restore_foreground", "foreground", preserve=fixture.initial)
        _require_show_request(fixture, "restore_foreground", "restore")
        maximized = _exercise(fixture, "maximize", "maximize", maximized=True)
        _require_show_request(fixture, "maximize", "maximize")
        offset = len(fixture.report["requests"])
        _exercise(fixture, "maximize_repeat", "maximize", maximized=True, preserve=maximized)
        _check(
            all(event["kind"] != "show_window" for event in fixture.report["requests"][offset:]),
            "Repeating maximize must preserve an already-maximized window without another show request.",
        )
        offset = len(fixture.report["requests"])
        _exercise(fixture, "foreground_already_maximized", "foreground", maximized=True, preserve=maximized)
        _check(
            all(event["kind"] != "show_window" for event in fixture.report["requests"][offset:]),
            "Foreground-only must not restore or resize an already-maximized window.",
        )
        fixture.report["status"] = (
            "geometry_verified_foreground_limited" if fixture.report["limitations"] else "verified_ready"
        )
    except pytest.skip.Exception:
        raise
    except BaseException as exc:
        fixture.report["status"] = "failed"
        fixture.report["failure"] = {"type": type(exc).__name__, "detail": str(exc)}
        raise
