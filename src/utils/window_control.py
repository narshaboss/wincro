"""Snapshot-based Windows window control, without input emulation.

Only selector/config strings are suitable for persistence. WindowInfo and its
process/window identifiers are transient observations, not saved targets.
Each step makes at most one asynchronous show request or foreground request;
the caller owns retries, cancellation and deadlines. There are no sleeps,
message sends, input-queue attachments, hooks or foreground-policy workarounds.

Win32 cannot atomically pin an HWND. Revalidation detects observed reuse,
including PID reuse via process creation time, but cannot eliminate destruction
and same-process/same-thread HWND reuse between the final check and a call.
"""

from __future__ import annotations

import ctypes
import ntpath
import os
import re
import sys
from collections.abc import Mapping
from ctypes import wintypes
from dataclasses import dataclass
from typing import Protocol


WINDOW_CONTROL_ACTION_TYPE = "window_control"
WINDOW_CONTROL_OPERATIONS = {
    "maximize": "최대화",
    "minimize": "최소화",
    "foreground": "앞으로 가져오기",
}

_INVALID_EXE_CHARACTERS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_SHOW_COMMANDS = {"maximize": 3, "minimize": 7, "restore": 9}


def normalize_window_control_config(config: Mapping | None = None) -> dict[str, str]:
    """Copy only persistent strings; default the operation only when absent.

    Incomplete editor values remain incomplete. Use validate before execution.
    Explicit empty, None or unknown operations never default to maximize.
    """
    if config is None:
        config = {}
    if not isinstance(config, Mapping):
        raise ValueError("창 제어 설정은 사전 형식이어야 합니다.")
    result = {}
    field_labels = {"executable_name": "실행 파일명", "title_contains": "창 제목", "operation": "창 동작"}
    for key, default in (
        ("executable_name", ""), ("title_contains", ""), ("operation", "maximize")
    ):
        value = config.get(key, default)
        if not isinstance(value, str):
            raise ValueError(f"{field_labels[key]}: 문자열이 필요합니다.")
        result[key] = value.strip()
    result["operation"] = result["operation"].casefold()
    # Omit the default key for legacy documents; never broaden their selectors.
    if "match_mode" in config:
        mode = config["match_mode"]
        if not isinstance(mode, str):
            raise ValueError("찾기 방식: 문자열이 필요합니다.")
        result["match_mode"] = mode.strip().casefold()
    return result


def validate_window_control_config(config: Mapping | None) -> dict[str, str]:
    """Return a normalized copy or raise ValueError, without backend access."""
    result = normalize_window_control_config(config)
    name = result["executable_name"]
    if (
        _INVALID_EXE_CHARACTERS.search(name)
        or not name.casefold().endswith(".exe")
        or not name[:-4].strip(" .")
        or ntpath.basename(name) != name
    ):
        raise ValueError("실행 파일명은 경로 없이 .exe로 끝나야 합니다.")
    mode = result.get("match_mode", "title")
    if mode not in {"title", "executable"}:
        raise ValueError("찾기 방식은 실행 파일명만 또는 실행 파일명 + 창 제목이어야 합니다.")
    if "\x00" in result["title_contains"] or (mode == "title" and not result["title_contains"]):
        raise ValueError("창 제목은 비어 있거나 NUL 문자를 포함할 수 없습니다.")
    if result["operation"] not in WINDOW_CONTROL_OPERATIONS:
        raise ValueError("창 동작은 최대화, 최소화, 앞으로 가져오기만 지원합니다.")
    return result


def format_window_control_summary(config: Mapping | None) -> str:
    """Describe a valid config without breaking rows for incomplete imports."""
    try:
        value = validate_window_control_config(config)
    except (ValueError, TypeError):
        return "창 제어 설정 필요"
    label = WINDOW_CONTROL_OPERATIONS[value["operation"]]
    if value.get("match_mode", "title") == "executable":
        return f'{label} | {value["executable_name"]} | 제목 무관'
    return f'{label} | {value["executable_name"]} | {value["title_contains"]}'


@dataclass(frozen=True)
class WindowInfo:
    handle: int
    process_id: int
    executable_name: str
    title: str
    minimized: bool | None = None
    maximized: bool | None = None
    process_creation_time: int | None = None
    thread_id: int | None = None


@dataclass(frozen=True)
class WindowControlResult:
    """Statuses: ready, missing, ambiguous, pending, or unsupported."""

    status: str
    message: str

    @property
    def ready(self) -> bool:
        return self.status == "ready"

    @property
    def detail(self) -> str:
        return self.message


class WindowBackend(Protocol):
    """Injectable snapshot backend; implementations must not wait on windows.

    Enumeration/get_window must exclude own-process, non-top-level, hidden
    non-iconic and tool windows. Mutators must revalidate the supplied transient
    identity themselves. Native snapshots include creation time and thread ID;
    simulation backends may omit these optional fields.
    """

    supported: bool

    def list_windows(self) -> list[WindowInfo]: ...
    def get_window(self, handle: int) -> WindowInfo | None: ...
    def get_foreground_window(self) -> int: ...
    def is_hung(self, handle: int) -> bool: ...
    def show_window_async(self, window: WindowInfo, operation: str) -> bool: ...
    def set_foreground_window(self, window: WindowInfo) -> bool: ...


def _same_identity(left: WindowInfo, right: WindowInfo) -> bool:
    # State changes are expected; title changes require a fresh external step.
    return (
        left.handle == right.handle
        and left.process_id == right.process_id
        and left.executable_name.casefold() == right.executable_name.casefold()
        and left.title == right.title
        and left.process_creation_time == right.process_creation_time
        and left.thread_id == right.thread_id
    )


def _matches(window: WindowInfo, config: Mapping[str, str]) -> bool:
    return (
        window.handle > 0
        and window.process_id > 0
        and window.executable_name.casefold() == config["executable_name"].casefold()
        and (
            config.get("match_mode", "title") == "executable"
            or config["title_contains"].casefold() in window.title.casefold()
        )
    )


class WindowController:
    """A stateless target resolver with externally driven, bounded steps.

    The default native backend is constructed lazily on Windows only. Passing
    a WindowBackend enables simulation on any platform. No HWND/PID is cached
    between steps. Invalid configuration raises ValueError before any backend
    call. Native access failures return pending rather than false success.
    """

    def __init__(self, backend: WindowBackend | None = None):
        self._backend = backend

    def _get_backend(self) -> WindowBackend | None:
        if self._backend is None and sys.platform == "win32":
            self._backend = _WindowsBackend()
        return self._backend

    def list_windows(self) -> list[WindowInfo]:
        """Take a fresh read-only snapshot, or [] on non-Windows.

        Enumeration failures raise OSError; never use a partial snapshot.
        """
        backend = self._get_backend()
        return list(backend.list_windows()) if backend is not None and backend.supported else []

    @staticmethod
    def _select(backend: WindowBackend, config: Mapping[str, str]):
        matches = [window for window in backend.list_windows() if _matches(window, config)]
        if not matches:
            return WindowControlResult("missing", "대상 창을 찾을 수 없습니다.")
        if len(matches) != 1:
            return WindowControlResult("ambiguous", f"대상 창이 {len(matches)}개입니다. 선택하지 않았습니다.")
        return matches[0]

    def _revalidate(self, backend, config, expected):
        selected = self._select(backend, config)
        if isinstance(selected, WindowControlResult):
            return selected
        current = backend.get_window(expected.handle)
        if (
            not _same_identity(expected, selected)
            or current is None
            or not _same_identity(expected, current)
            or not _matches(current, config)
        ):
            return WindowControlResult("pending", "대상 창 정보가 변경되었습니다. 다시 확인하세요.")
        return current

    @staticmethod
    def _next_action(backend, window, operation):
        if window.minimized is None or (operation == "maximize" and window.maximized is None):
            return "unknown"
        if operation == "minimize":
            return None if window.minimized else "minimize"
        if operation == "maximize" and (window.minimized or not window.maximized):
            return "maximize"
        if window.minimized:
            return "restore"
        return None if backend.get_foreground_window() == window.handle else "foreground"

    def _verify(self, backend, config, expected, accepted):
        current = self._revalidate(backend, config, expected)
        if isinstance(current, WindowControlResult):
            return current
        foreground = (
            backend.get_foreground_window() if config["operation"] != "minimize" else None
        )
        # Guard handle/PID changes during foreground verification as well.
        final = backend.get_window(expected.handle)
        if final is None or not _same_identity(expected, final) or not _matches(final, config):
            return WindowControlResult("pending", "결과 확인 중 대상 창이 변경되었습니다.")
        if config["operation"] == "minimize":
            ready = final.minimized is True
        else:
            ready = final.minimized is False and foreground == final.handle
            if config["operation"] == "maximize":
                ready = ready and final.maximized is True
        if ready:
            return WindowControlResult("ready", "창 제어 상태 확인 완료")
        detail = "요청 후 상태 확인 대기 중입니다." if accepted else "요청이 거부되었거나 아직 적용되지 않았습니다."
        return WindowControlResult("pending", f"{detail} 재확인이 필요합니다.")

    def step(self, config: Mapping | None) -> WindowControlResult:
        value = validate_window_control_config(config)
        try:
            backend = self._get_backend()
            if backend is None or not backend.supported:
                return WindowControlResult("unsupported", "창 제어는 Windows에서만 지원합니다.")
            selected = self._select(backend, value)
            if isinstance(selected, WindowControlResult):
                return selected
            current = self._revalidate(backend, value, selected)
            if isinstance(current, WindowControlResult):
                return current
            action = self._next_action(backend, current, value["operation"])
            if action == "unknown":
                return WindowControlResult("pending", "창 상태를 확인할 수 없어 요청하지 않았습니다.")
            accepted = False
            if action is not None:
                if action == "foreground" and backend.is_hung(current.handle):
                    return WindowControlResult("pending", "창이 응답하지 않아 전면 표시를 건너뛰었습니다.")
                current = self._revalidate(backend, value, selected)
                if isinstance(current, WindowControlResult):
                    return current
                if self._next_action(backend, current, value["operation"]) != action:
                    return WindowControlResult("pending", "창 상태가 변경되었습니다. 다시 확인하세요.")
                if action == "foreground":
                    accepted = backend.set_foreground_window(current)
                else:
                    accepted = backend.show_window_async(current, action)
            return self._verify(backend, value, selected, accepted)
        except OSError as exc:
            return WindowControlResult("pending", f"창 제어 접근 오류: {exc}")


class _Win32API:
    """Loaded only by an explicit Windows backend operation, never at import."""

    def __init__(self):
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._configure_prototypes()

    def _configure_prototypes(self):
        # LPARAM/LONG_PTR and all HANDLE/HWND returns must stay pointer-sized.
        callback_factory = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
        self.enum_callback = callback_factory(wintypes.BOOL, wintypes.HWND, ctypes.c_ssize_t)
        u, k = self.user32, self.kernel32
        bindings = [
            (u.EnumWindows, [self.enum_callback, ctypes.c_ssize_t], wintypes.BOOL),
            (u.IsWindow, [wintypes.HWND], wintypes.BOOL),
            (u.GetAncestor, [wintypes.HWND, wintypes.UINT], wintypes.HWND),
            (u.IsWindowVisible, [wintypes.HWND], wintypes.BOOL),
            (u.IsIconic, [wintypes.HWND], wintypes.BOOL),
            (u.IsZoomed, [wintypes.HWND], wintypes.BOOL),
            (u.IsHungAppWindow, [wintypes.HWND], wintypes.BOOL),
            (u.GetWindowThreadProcessId, [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
            (u.GetWindowTextW, [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
            (u.ShowWindowAsync, [wintypes.HWND, ctypes.c_int], wintypes.BOOL),
            (u.SetForegroundWindow, [wintypes.HWND], wintypes.BOOL),
            (u.GetForegroundWindow, [], wintypes.HWND),
            (k.OpenProcess, [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            (k.CloseHandle, [wintypes.HANDLE], wintypes.BOOL),
            (k.QueryFullProcessImageNameW, [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                         ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
            (k.GetProcessTimes, [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4, wintypes.BOOL),
            (k.WaitForSingleObject, [wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
        ]
        if ctypes.sizeof(ctypes.c_void_p) == 8:
            self.get_window_long = u.GetWindowLongPtrW
        else:
            self.get_window_long = u.GetWindowLongW
        bindings.append((self.get_window_long, [wintypes.HWND, ctypes.c_int], ctypes.c_ssize_t))
        for function, arguments, result in bindings:
            function.argtypes = arguments
            function.restype = result

    def extended_style(self, handle):
        ctypes.set_last_error(0)
        style = self.get_window_long(handle, -20)
        return None if style == 0 and ctypes.get_last_error() else style


class _WindowsBackend:
    supported = True

    def __init__(self, api=None, own_pid: int | None = None):
        # The API injection is also used for native-boundary simulations.
        if api is None and sys.platform != "win32":
            raise OSError("창 제어는 Windows에서만 지원합니다.")
        self._api = api if api is not None else _Win32API()
        self._own_pid = os.getpid() if own_pid is None else own_pid

    def _owner(self, handle):
        pid = wintypes.DWORD()
        thread_id = self._api.user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
        return int(pid.value), int(thread_id)

    def _eligible(self, handle):
        u = self._api.user32
        style = self._api.extended_style(handle)
        return (
            bool(u.IsWindow(handle))
            and u.GetAncestor(handle, 2) == handle
            and (bool(u.IsWindowVisible(handle)) or bool(u.IsIconic(handle)))
            and style is not None
            and not (style & 0x80)  # WS_EX_TOOLWINDOW
        )

    def get_window(self, handle: int) -> WindowInfo | None:
        if not self._eligible(handle):
            return None
        pid, thread_id = self._owner(handle)
        if not pid or not thread_id or pid == self._own_pid:
            return None
        k, u = self._api.kernel32, self._api.user32
        # QUERY_LIMITED_INFORMATION | SYNCHRONIZE; no VM/debug privileges.
        process = k.OpenProcess(0x1000 | 0x100000, False, pid)
        if not process:
            return None
        try:
            # Zero timeout only: reject exited processes without waiting.
            if k.WaitForSingleObject(process, 0) != 0x102:  # WAIT_TIMEOUT
                return None
            image = ctypes.create_unicode_buffer(32768)
            length = wintypes.DWORD(len(image))
            if not k.QueryFullProcessImageNameW(process, 0, image, ctypes.byref(length)):
                return None
            creation, exit_time, kernel, user = (wintypes.FILETIME() for _ in range(4))
            if not k.GetProcessTimes(process, ctypes.byref(creation), ctypes.byref(exit_time),
                                     ctypes.byref(kernel), ctypes.byref(user)):
                return None
            if self._owner(handle) != (pid, thread_id):
                return None
            # Foreign captions are read from system storage, not WM_GETTEXT.
            # Check own PID before this call to avoid own-process synchronous messaging.
            caption = ctypes.create_unicode_buffer(32768)
            copied = u.GetWindowTextW(handle, caption, len(caption))
            if copied <= 0 or copied >= len(caption) - 1:
                return None
            minimized, maximized = bool(u.IsIconic(handle)), bool(u.IsZoomed(handle))
            if (
                self._owner(handle) != (pid, thread_id)
                or not self._eligible(handle)
                or k.WaitForSingleObject(process, 0) != 0x102
            ):
                return None
            executable_name = ntpath.basename(image.value)
            if not executable_name:
                return None
            return WindowInfo(
                handle, pid, executable_name, caption.value, minimized, maximized,
                (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime), thread_id,
            )
        finally:
            k.CloseHandle(process)

    def list_windows(self) -> list[WindowInfo]:
        handles = []

        @self._api.enum_callback
        def collect(handle, _):
            handles.append(int(handle))
            return True

        if not self._api.user32.EnumWindows(collect, 0):
            raise OSError("창 목록 조회에 실패하여 불완전한 결과를 폐기했습니다.")
        return [window for handle in handles if (window := self.get_window(handle)) is not None]

    def get_foreground_window(self) -> int:
        return int(self._api.user32.GetForegroundWindow() or 0)

    def is_hung(self, handle: int) -> bool:
        return bool(self._api.user32.IsHungAppWindow(handle))

    def show_window_async(self, window: WindowInfo, operation: str) -> bool:
        if operation not in _SHOW_COMMANDS:
            raise ValueError("지원하지 않는 창 상태 변경입니다.")
        current = self.get_window(window.handle)
        if current is None or not _same_identity(window, current):
            return False
        if operation == "restore" and not current.minimized:
            return False  # Never unmaximize a foreground-only target.
        if operation == "maximize" and current.maximized and not current.minimized:
            return True
        if operation == "minimize" and current.minimized:
            return True
        accepted = bool(self._api.user32.ShowWindowAsync(window.handle, _SHOW_COMMANDS[operation]))
        after = self.get_window(window.handle)
        return accepted and after is not None and _same_identity(window, after)

    def set_foreground_window(self, window: WindowInfo) -> bool:
        if self.is_hung(window.handle):
            return False
        current = self.get_window(window.handle)
        if current is None or not _same_identity(window, current) or current.minimized:
            return False
        accepted = bool(self._api.user32.SetForegroundWindow(window.handle))
        after = self.get_window(window.handle)
        return accepted and after is not None and _same_identity(window, after)
