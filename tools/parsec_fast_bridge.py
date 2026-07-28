"""Fast Parsec bridge for Codex-driven visual control.

This tool is intentionally separate from WinCro's runtime. It provides a small
CLI that can capture the Parsec window, run OpenCV template matching, and send
mouse/keyboard input through Win32 SendInput when explicitly requested.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import cv2
import mss
import numpy as np


DEFAULT_TITLE_PATTERN = r"Parsec"
DEFAULT_PARSECD_PATHS = (
    r"C:\Program Files\Parsec\parsecd.exe",
    r"C:\Program Files (x86)\Parsec\parsecd.exe",
)
PARSEC_CONNECTED_STATUS = "0"
PARSEC_CONNECTING_STATUS = "20"
PARSEC_IDLE_STATUS = "-3"
PARSEC_FAILURE_PATTERNS = (
    "error",
    "failed",
    "fail",
    "denied",
    "refused",
    "timeout",
    "invalid",
)


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["width"] = self.width
        data["height"] = self.height
        return data


@dataclass(frozen=True)
class MatchResult:
    score: float
    x: int
    y: int
    width: int
    height: int
    threshold: float

    @property
    def center_x(self) -> int:
        return self.x + self.width // 2

    @property
    def center_y(self) -> int:
        return self.y + self.height // 2

    @property
    def ok(self) -> bool:
        return self.score >= self.threshold

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["center"] = [self.center_x, self.center_y]
        data["ok"] = self.ok
        return data


def _now_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _json_result(**kwargs: Any) -> str:
    return json.dumps(kwargs, ensure_ascii=False, indent=2)


def find_parsecd_exe(explicit_path: str | None = None) -> Path:
    candidates: list[Path] = []
    if explicit_path:
        candidates.append(Path(explicit_path))
    candidates.extend(Path(path) for path in DEFAULT_PARSECD_PATHS)
    local_appdata = os.environ.get("LOCALAPPDATA")
    appdata = os.environ.get("APPDATA")
    if local_appdata:
        candidates.append(Path(local_appdata) / "Parsec" / "parsecd.exe")
    if appdata:
        candidates.append(Path(appdata) / "Parsec" / "parsecd.exe")
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError("parsecd.exe not found")


def default_parsec_log_path() -> Path:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA is not set")
    return Path(appdata) / "Parsec" / "log_cl.txt"


def default_parsec_app_log_path() -> Path:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA is not set")
    return Path(appdata) / "Parsec" / "log.txt"


def file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0


def read_new_text(path: Path, offset: int) -> tuple[str, int]:
    if not path.exists():
        return "", offset
    size = path.stat().st_size
    if size < offset:
        # Log rotated or truncated. Start from the new file beginning.
        offset = 0
    if size == offset:
        return "", offset
    with path.open("rb") as handle:
        handle.seek(offset)
        data = handle.read()
        new_offset = handle.tell()
    return data.decode("utf-8", errors="replace"), new_offset


def parse_parsec_log_timestamp(line: str) -> datetime | None:
    match = re.match(r"\[[A-Z]\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\]", line)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def classify_parsec_status_lines(lines: list[str]) -> dict[str, Any]:
    statuses: list[str] = []
    evidence: list[str] = []
    failures: list[str] = []
    latest_status_line: str | None = None
    latest_connected_line: str | None = None
    has_net = False
    has_crypto = False
    has_host_ready = False

    for line in lines:
        lower = line.lower()
        status_match = re.search(r"Client status (?:changed to|received):\s*(-?\d+)", line, re.IGNORECASE)
        if status_match:
            status = status_match.group(1)
            statuses.append(status)
            latest_status_line = line
            if status == PARSEC_CONNECTED_STATUS:
                latest_connected_line = line
            evidence.append(line)
        if re.search(r"\bnet\s*=", line):
            has_net = True
            evidence.append(line)
        if "bud aes" in lower:
            has_crypto = True
            evidence.append(line)
        if "host's virtual microphone is enabled" in lower:
            has_host_ready = True
            evidence.append(line)
        if any(pattern in lower for pattern in PARSEC_FAILURE_PATTERNS):
            failures.append(line)

    latest_cycle = statuses
    if PARSEC_IDLE_STATUS in statuses:
        last_idle_index = max(idx for idx, status in enumerate(statuses) if status == PARSEC_IDLE_STATUS)
        if last_idle_index == len(statuses) - 1:
            latest_cycle = statuses[last_idle_index:]
        elif len(statuses) > 1:
            latest_cycle = statuses[last_idle_index + 1 :]
    latest_status = statuses[-1] if statuses else None
    connected = latest_status == PARSEC_CONNECTED_STATUS
    connecting = latest_status == PARSEC_CONNECTING_STATUS
    idle = latest_status == PARSEC_IDLE_STATUS
    connecting_then_connected = False
    if connected:
        try:
            connecting_then_connected = (
                latest_cycle.index(PARSEC_CONNECTING_STATUS)
                <= latest_cycle.index(PARSEC_CONNECTED_STATUS)
            )
        except ValueError:
            connecting_then_connected = False

    state = "unknown"
    if connected:
        state = "connected"
    elif idle:
        state = "idle"
    elif failures:
        state = "failed"
    elif connecting:
        state = "connecting"

    # Preserve insertion order while limiting noisy duplicates.
    compact_evidence = list(dict.fromkeys(evidence))[-20:]
    return {
        "state": state,
        "connected": connected,
        "connecting_then_connected": connecting_then_connected,
        "statuses": statuses,
        "latest_status": latest_status,
        "latest_status_line": latest_status_line,
        "latest_connected_line": latest_connected_line,
        "latest_cycle": latest_cycle,
        "has_net": has_net,
        "has_crypto": has_crypto,
        "has_host_ready": has_host_ready,
        "failures": failures[-10:],
        "evidence": compact_evidence,
    }


def collect_parsec_status_lines_from_text(text: str, *, since: datetime | None = None) -> list[str]:
    lines: list[str] = []
    for line in text.splitlines():
        stamp = parse_parsec_log_timestamp(line)
        if since is not None and stamp is not None and stamp < since.replace(microsecond=0):
            continue
        if re.search(
            r"Client status (?:changed to|received)|net\s*=|BUD AES|Host's virtual microphone|error|fail|denied|refused|timeout|invalid",
            line,
            re.IGNORECASE,
        ):
            lines.append(line)
    return lines


def read_current_parsec_status(
    log_paths: list[Path],
    *,
    max_age_s: float,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now()
    since = now.timestamp() - max(1.0, max_age_s)
    recent_lines: list[str] = []
    for path in log_paths:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")[-40000:]
        except OSError:
            continue
        for line in collect_parsec_status_lines_from_text(text):
            stamp = parse_parsec_log_timestamp(line)
            if stamp is not None and stamp.timestamp() < since:
                continue
            recent_lines.append(line)
    result = classify_parsec_status_lines(recent_lines)
    result["method"] = "current_status"
    result["max_age_s"] = max_age_s
    return result


def wait_parsec_connection_status(
    log_path: Path,
    *,
    offset: int,
    started_at: datetime,
    timeout_s: float,
    poll_s: float = 0.2,
    fallback_log_paths: list[Path] | None = None,
    current_status_max_age_s: float = 1800,
) -> dict[str, Any]:
    deadline = time.perf_counter() + max(0.1, timeout_s)
    lines: list[str] = []
    current_offset = offset

    def collect_relevant(text: str) -> None:
        lines.extend(collect_parsec_status_lines_from_text(text, since=started_at))

    while time.perf_counter() < deadline:
        text, current_offset = read_new_text(log_path, current_offset)
        if text:
            collect_relevant(text)
        classification = classify_parsec_status_lines(lines)
        if classification["state"] in {"connected", "failed"}:
            classification.update(
                {
                    "log_path": str(log_path),
                    "log_offset": current_offset,
                    "timed_out": False,
                }
            )
            return classification
        time.sleep(poll_s)

    # Parsec can flush connection lines before or around our offset snapshot when
    # a second parsecd instance forwards the command to the existing client.
    # Re-scan a small recent tail by timestamp before declaring timeout.
    try:
        tail_text = log_path.read_text(encoding="utf-8", errors="replace")[-20000:]
        collect_relevant(tail_text)
    except OSError:
        pass
    classification = classify_parsec_status_lines(lines)
    if not classification["connected"] and fallback_log_paths:
        current = read_current_parsec_status(
            fallback_log_paths,
            max_age_s=current_status_max_age_s,
        )
        if current.get("connected"):
            current.update(
                {
                    "log_path": str(log_path),
                    "log_offset": current_offset,
                    "timed_out": True,
                    "note": "No new connect log was written; current Parsec status is connected. Peer identity is not verified by log fallback.",
                }
            )
            return current
    classification.update(
        {
            "log_path": str(log_path),
            "log_offset": current_offset,
            "timed_out": True,
        }
    )
    return classification


def wait_parsec_disconnect_status(
    log_path: Path,
    *,
    offset: int,
    started_at: datetime,
    timeout_s: float,
    poll_s: float = 0.2,
    fallback_log_paths: list[Path] | None = None,
    current_status_max_age_s: float = 1800,
) -> dict[str, Any]:
    deadline = time.perf_counter() + max(0.1, timeout_s)
    lines: list[str] = []
    current_offset = offset

    def collect_relevant(text: str) -> None:
        lines.extend(collect_parsec_status_lines_from_text(text, since=started_at))

    while time.perf_counter() < deadline:
        text, current_offset = read_new_text(log_path, current_offset)
        if text:
            collect_relevant(text)
        classification = classify_parsec_status_lines(lines)
        if classification["state"] == "idle":
            classification.update(
                {
                    "log_path": str(log_path),
                    "log_offset": current_offset,
                    "timed_out": False,
                }
            )
            return classification
        time.sleep(poll_s)

    try:
        tail_text = log_path.read_text(encoding="utf-8", errors="replace")[-20000:]
        collect_relevant(tail_text)
    except OSError:
        pass
    classification = classify_parsec_status_lines(lines)
    if classification["state"] != "idle" and fallback_log_paths:
        current = read_current_parsec_status(
            fallback_log_paths,
            max_age_s=current_status_max_age_s,
        )
        if current.get("state") == "idle":
            current.update(
                {
                    "log_path": str(log_path),
                    "log_offset": current_offset,
                    "timed_out": True,
                    "note": "No new disconnect log was written; current Parsec status is idle.",
                }
            )
            return current
    classification.update(
        {
            "log_path": str(log_path),
            "log_offset": current_offset,
            "timed_out": True,
        }
    )
    return classification


def _require_windows() -> None:
    if os.name != "nt":
        raise RuntimeError("This bridge requires Windows for window/input control")


def set_dpi_awareness() -> None:
    """Make Win32 coordinates align with physical pixels where possible."""
    if os.name != "nt":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _user32() -> Any:
    _require_windows()
    return ctypes.windll.user32


def enum_windows(title_pattern: str = DEFAULT_TITLE_PATTERN) -> list[WindowInfo]:
    _require_windows()
    set_dpi_awareness()
    user32 = _user32()
    pattern = re.compile(title_pattern, re.IGNORECASE)
    windows: list[WindowInfo] = []

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value
        if not pattern.search(title):
            return True
        rect = RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        if rect.right <= rect.left or rect.bottom <= rect.top:
            return True
        windows.append(
            WindowInfo(
                hwnd=int(hwnd),
                title=title,
                left=int(rect.left),
                top=int(rect.top),
                right=int(rect.right),
                bottom=int(rect.bottom),
            )
        )
        return True

    user32.EnumWindows(EnumWindowsProc(callback), 0)
    return windows


def find_window(title_pattern: str = DEFAULT_TITLE_PATTERN) -> WindowInfo:
    windows = enum_windows(title_pattern)
    if not windows:
        raise RuntimeError(f"No visible window matched title pattern: {title_pattern}")
    # Prefer the largest visible Parsec window. Small helper/pop-up windows are less useful.
    return max(windows, key=lambda item: item.width * item.height)


def activate_window(window: WindowInfo) -> None:
    _require_windows()
    set_dpi_awareness()
    user32 = _user32()
    hwnd = ctypes.c_void_p(window.hwnd)
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.05)


def close_window(window: WindowInfo) -> None:
    _require_windows()
    user32 = _user32()
    hwnd = ctypes.c_void_p(window.hwnd)
    WM_CLOSE = 0x0010
    if not user32.PostMessageW(hwnd, WM_CLOSE, 0, 0):
        raise RuntimeError(f"PostMessageW(WM_CLOSE) failed for hwnd={window.hwnd}")


def capture_window(window: WindowInfo) -> np.ndarray:
    if window.width <= 0 or window.height <= 0:
        raise RuntimeError("Window has invalid capture size")
    monitor = {
        "left": window.left,
        "top": window.top,
        "width": window.width,
        "height": window.height,
    }
    with mss.mss() as sct:
        frame = np.array(sct.grab(monitor))
    return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)


def read_image(path: str | Path) -> np.ndarray:
    image_path = Path(path)
    if not image_path.exists():
        raise FileNotFoundError(str(image_path))
    data = np.fromfile(str(image_path), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError(f"Unable to read image: {image_path}")
    return image


def parse_region(value: str | None) -> tuple[int, int, int, int] | None:
    if not value:
        return None
    parts = [part.strip() for part in value.split(",")]
    if len(parts) != 4:
        raise ValueError("region must be x,y,w,h")
    x, y, w, h = [int(float(part)) for part in parts]
    return x, y, w, h


def clamp_region(
    region: tuple[int, int, int, int] | None,
    width: int,
    height: int,
) -> tuple[int, int, int, int]:
    if region is None:
        return 0, 0, width, height
    x, y, w, h = region
    x1 = max(0, min(width, x))
    y1 = max(0, min(height, y))
    x2 = max(x1, min(width, x + max(0, w)))
    y2 = max(y1, min(height, y + max(0, h)))
    return x1, y1, x2 - x1, y2 - y1


def match_template(
    screen_bgr: np.ndarray,
    template_bgr: np.ndarray,
    *,
    threshold: float = 0.85,
    region: tuple[int, int, int, int] | None = None,
    grayscale: bool = False,
) -> MatchResult:
    if screen_bgr.size == 0:
        raise ValueError("screen image is empty")
    if template_bgr.size == 0:
        raise ValueError("template image is empty")

    screen_h, screen_w = screen_bgr.shape[:2]
    tmpl_h, tmpl_w = template_bgr.shape[:2]
    x, y, w, h = clamp_region(region, screen_w, screen_h)
    if w < tmpl_w or h < tmpl_h:
        return MatchResult(0.0, x, y, tmpl_w, tmpl_h, threshold)

    haystack = screen_bgr[y : y + h, x : x + w]
    needle = template_bgr
    if grayscale:
        haystack = cv2.cvtColor(haystack, cv2.COLOR_BGR2GRAY)
        needle = cv2.cvtColor(needle, cv2.COLOR_BGR2GRAY)

    method = cv2.TM_CCOEFF_NORMED
    result = cv2.matchTemplate(haystack, needle, method)
    _min_val, max_val, _min_loc, max_loc = cv2.minMaxLoc(result)
    return MatchResult(
        score=float(max_val),
        x=int(x + max_loc[0]),
        y=int(y + max_loc[1]),
        width=int(tmpl_w),
        height=int(tmpl_h),
        threshold=float(threshold),
    )


def save_debug_match(image_bgr: np.ndarray, match: MatchResult, output: str | Path) -> None:
    view = image_bgr.copy()
    color = (0, 220, 0) if match.ok else (0, 0, 255)
    cv2.rectangle(view, (match.x, match.y), (match.x + match.width, match.y + match.height), color, 2)
    cv2.drawMarker(
        view,
        (match.center_x, match.center_y),
        color,
        markerType=cv2.MARKER_CROSS,
        markerSize=18,
        thickness=2,
    )
    cv2.imencode(".png", view)[1].tofile(str(output))


class _MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _KeyBdInput(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _HardwareInput(ctypes.Structure):
    _fields_ = [
        ("uMsg", ctypes.c_ulong),
        ("wParamL", ctypes.c_short),
        ("wParamH", ctypes.c_ushort),
    ]


class _InputUnion(ctypes.Union):
    _fields_ = [
        ("mi", _MouseInput),
        ("ki", _KeyBdInput),
        ("hi", _HardwareInput),
    ]


class _Input(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_ulong),
        ("ii", _InputUnion),
    ]


INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002


VK_ALIASES: dict[str, int] = {
    "backspace": 0x08,
    "tab": 0x09,
    "enter": 0x0D,
    "return": 0x0D,
    "shift": 0x10,
    "shift_l": 0xA0,
    "shift_r": 0xA1,
    "ctrl": 0x11,
    "control": 0x11,
    "control_l": 0xA2,
    "control_r": 0xA3,
    "alt": 0x12,
    "menu": 0x12,
    "escape": 0x1B,
    "esc": 0x1B,
    "space": 0x20,
    "pageup": 0x21,
    "pagedown": 0x22,
    "end": 0x23,
    "home": 0x24,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "insert": 0x2D,
    "delete": 0x2E,
    "`": 0xC0,
    "~": 0xC0,
    "grave": 0xC0,
    "tilde": 0xC0,
}

EXTENDED_VKS = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E}
DANGEROUS_KEY_COMBOS = {
    frozenset({0x12, 0x73}),  # Alt+F4
}


def key_to_vk(key: str) -> int:
    normalized = key.strip().lower().replace("-", "_")
    if not normalized:
        raise ValueError("empty key")
    if normalized in VK_ALIASES:
        return VK_ALIASES[normalized]
    if re.fullmatch(r"f([1-9]|1[0-9]|2[0-4])", normalized):
        return 0x70 + int(normalized[1:]) - 1
    if len(normalized) == 1:
        ch = normalized.upper()
        if "A" <= ch <= "Z" or "0" <= ch <= "9":
            return ord(ch)
    if normalized.startswith("vk_"):
        return int(normalized[3:], 16)
    raise ValueError(f"Unsupported key: {key}")


def parse_key_combo(keys: str) -> list[int]:
    return [key_to_vk(part) for part in re.split(r"\s*\+\s*", keys.strip()) if part.strip()]


def assert_key_combo_allowed(vks: list[int], *, force: bool = False) -> None:
    if force:
        return
    normalized = frozenset(vks)
    if normalized in DANGEROUS_KEY_COMBOS:
        raise ValueError("Blocked dangerous key combo. Use --force-dangerous only if explicitly required.")


def _send_input(inputs: Iterable[_Input]) -> None:
    _require_windows()
    user32 = _user32()
    items = list(inputs)
    if not items:
        return
    array_type = _Input * len(items)
    array = array_type(*items)
    sent = user32.SendInput(len(items), ctypes.byref(array), ctypes.sizeof(_Input))
    if sent != len(items):
        raise RuntimeError(f"SendInput sent {sent}/{len(items)} events")


def _mouse_input(flags: int) -> _Input:
    extra = ctypes.pointer(ctypes.c_ulong(0))
    return _Input(
        type=INPUT_MOUSE,
        ii=_InputUnion(mi=_MouseInput(0, 0, 0, flags, 0, extra)),
    )


def _key_input(vk: int, key_up: bool = False) -> _Input:
    extra = ctypes.pointer(ctypes.c_ulong(0))
    flags = KEYEVENTF_KEYUP if key_up else 0
    if vk in EXTENDED_VKS:
        flags |= KEYEVENTF_EXTENDEDKEY
    return _Input(
        type=INPUT_KEYBOARD,
        ii=_InputUnion(ki=_KeyBdInput(vk, 0, flags, 0, extra)),
    )


def send_click(screen_x: int, screen_y: int, *, button: str = "left") -> None:
    _require_windows()
    set_dpi_awareness()
    user32 = _user32()
    if not user32.SetCursorPos(int(screen_x), int(screen_y)):
        raise RuntimeError("SetCursorPos failed")
    button_name = button.lower()
    if button_name in {"left", "l"}:
        events = [_mouse_input(MOUSEEVENTF_LEFTDOWN), _mouse_input(MOUSEEVENTF_LEFTUP)]
    elif button_name in {"right", "r"}:
        events = [_mouse_input(MOUSEEVENTF_RIGHTDOWN), _mouse_input(MOUSEEVENTF_RIGHTUP)]
    else:
        raise ValueError(f"Unsupported mouse button: {button}")
    _send_input(events)


def send_key_combo(keys: str, *, hold_ms: int = 20) -> None:
    vks = parse_key_combo(keys)
    if not vks:
        raise ValueError("No keys provided")
    events: list[_Input] = []
    for vk in vks:
        events.append(_key_input(vk, key_up=False))
    for vk in reversed(vks):
        events.append(_key_input(vk, key_up=True))
    if hold_ms <= 0:
        _send_input(events)
        return
    # Keep modifier timing predictable: press all keys, hold briefly, then release.
    _send_input(events[: len(vks)])
    time.sleep(hold_ms / 1000)
    _send_input(events[len(vks) :])


def command_windows(args: argparse.Namespace) -> dict[str, Any]:
    started = time.perf_counter()
    windows = enum_windows(args.title)
    return {
        "ok": True,
        "action": "windows",
        "elapsed_ms": _now_ms(started),
        "windows": [window.to_json() for window in windows],
    }


def command_screenshot(args: argparse.Namespace) -> dict[str, Any]:
    started = time.perf_counter()
    window = find_window(args.title)
    image = capture_window(window)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    cv2.imencode(".png", image)[1].tofile(str(output))
    return {
        "ok": True,
        "action": "screenshot",
        "elapsed_ms": _now_ms(started),
        "window": window.to_json(),
        "output": str(output),
        "shape": list(image.shape),
    }


def _find_match_from_args(args: argparse.Namespace) -> tuple[WindowInfo, np.ndarray, MatchResult]:
    window = find_window(args.title)
    screen = capture_window(window)
    template = read_image(args.template)
    match = match_template(
        screen,
        template,
        threshold=args.threshold,
        region=parse_region(args.region),
        grayscale=args.grayscale,
    )
    return window, screen, match


def command_find(args: argparse.Namespace) -> dict[str, Any]:
    started = time.perf_counter()
    window, screen, match = _find_match_from_args(args)
    if args.debug_output:
        save_debug_match(screen, match, args.debug_output)
    return {
        "ok": match.ok,
        "action": "find",
        "elapsed_ms": _now_ms(started),
        "window": window.to_json(),
        "template": str(args.template),
        "match": match.to_json(),
        "debug_output": args.debug_output,
    }


def command_click(args: argparse.Namespace) -> dict[str, Any]:
    started = time.perf_counter()
    window = find_window(args.title) if args.relative == "window" else None
    screen_x = int(args.x + (window.left if window else 0))
    screen_y = int(args.y + (window.top if window else 0))
    if args.execute:
        if window:
            activate_window(window)
        send_click(screen_x, screen_y, button=args.button)
    return {
        "ok": True,
        "action": "click",
        "elapsed_ms": _now_ms(started),
        "dry_run": not args.execute,
        "relative": args.relative,
        "window": window.to_json() if window else None,
        "point": {"screen": [screen_x, screen_y], "input": [args.x, args.y]},
        "button": args.button,
    }


def command_click_template(args: argparse.Namespace) -> dict[str, Any]:
    started = time.perf_counter()
    window, screen, match = _find_match_from_args(args)
    screen_x = window.left + match.center_x
    screen_y = window.top + match.center_y
    if match.ok and args.execute:
        activate_window(window)
        send_click(screen_x, screen_y, button=args.button)
    if args.debug_output:
        save_debug_match(screen, match, args.debug_output)
    return {
        "ok": match.ok,
        "action": "click-template",
        "elapsed_ms": _now_ms(started),
        "dry_run": not args.execute,
        "clicked": bool(match.ok and args.execute),
        "window": window.to_json(),
        "template": str(args.template),
        "match": match.to_json(),
        "point": {"screen": [screen_x, screen_y], "window": [match.center_x, match.center_y]},
        "button": args.button,
        "debug_output": args.debug_output,
    }


def command_key(args: argparse.Namespace) -> dict[str, Any]:
    started = time.perf_counter()
    window = None
    vks = parse_key_combo(args.keys)
    assert_key_combo_allowed(vks, force=args.force_dangerous)
    if args.execute and args.activate:
        window = find_window(args.title)
    if args.execute:
        if not window:
            raise RuntimeError("Refusing to send executable key input without target window activation")
        if window:
            activate_window(window)
        send_key_combo(args.keys, hold_ms=args.hold_ms)
    return {
        "ok": True,
        "action": "key",
        "elapsed_ms": _now_ms(started),
        "dry_run": not args.execute,
        "window": window.to_json() if window else None,
        "activated": bool(window),
        "keys": args.keys,
        "hold_ms": args.hold_ms,
        "vk": vks,
    }


def command_connect_peer(args: argparse.Namespace) -> dict[str, Any]:
    started = time.perf_counter()
    parsecd = find_parsecd_exe(args.parsecd)
    log_path = Path(args.log_path) if args.log_path else default_parsec_log_path()
    log_offset = file_size(log_path)
    started_at = datetime.now()
    peer_arg = f"peer_id={args.peer_id}"
    if args.settings:
        clean_settings = [item.strip() for item in args.settings if item.strip()]
        if clean_settings:
            peer_arg = ":".join([peer_arg, *clean_settings])
    command = [str(parsecd), peer_arg]
    pid = None
    if args.execute:
        process = subprocess.Popen(command, close_fds=True)
        pid = process.pid
    status = None
    if args.wait_status:
        if not args.execute:
            raise RuntimeError("--wait-status requires --execute")
        status = wait_parsec_connection_status(
            log_path,
            offset=log_offset,
            started_at=started_at,
            timeout_s=args.timeout,
            fallback_log_paths=[log_path, Path(args.app_log_path) if args.app_log_path else default_parsec_app_log_path()],
            current_status_max_age_s=args.current_status_max_age,
        )
    return {
        "ok": True if status is None else bool(status.get("connected")),
        "action": "connect-peer",
        "elapsed_ms": _now_ms(started),
        "dry_run": not args.execute,
        "parsecd": str(parsecd),
        "peer_id": args.peer_id,
        "command": command,
        "pid": pid,
        "status": status,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fast Parsec image/input bridge")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_title(p: argparse.ArgumentParser) -> None:
        p.add_argument("--title", default=DEFAULT_TITLE_PATTERN, help="Target window title regex")

    windows = subparsers.add_parser("windows", help="List matching windows")
    add_title(windows)
    windows.set_defaults(func=command_windows)

    screenshot = subparsers.add_parser("screenshot", help="Capture the target window")
    add_title(screenshot)
    screenshot.add_argument("--output", required=True)
    screenshot.set_defaults(func=command_screenshot)

    find = subparsers.add_parser("find", help="Find template in target window")
    add_title(find)
    find.add_argument("--template", required=True)
    find.add_argument("--threshold", type=float, default=0.85)
    find.add_argument("--region", help="Window-relative x,y,w,h")
    find.add_argument("--grayscale", action="store_true")
    find.add_argument("--debug-output")
    find.set_defaults(func=command_find)

    click = subparsers.add_parser("click", help="Click a point")
    add_title(click)
    click.add_argument("--x", type=int, required=True)
    click.add_argument("--y", type=int, required=True)
    click.add_argument("--relative", choices=["window", "screen"], default="window")
    click.add_argument("--button", default="left", choices=["left", "right", "l", "r"])
    click.add_argument("--execute", action="store_true", help="Actually send input")
    click.set_defaults(func=command_click)

    click_template = subparsers.add_parser("click-template", help="Find a template and click its center")
    add_title(click_template)
    click_template.add_argument("--template", required=True)
    click_template.add_argument("--threshold", type=float, default=0.85)
    click_template.add_argument("--region", help="Window-relative x,y,w,h")
    click_template.add_argument("--grayscale", action="store_true")
    click_template.add_argument("--button", default="left", choices=["left", "right", "l", "r"])
    click_template.add_argument("--debug-output")
    click_template.add_argument("--execute", action="store_true", help="Actually send input")
    click_template.set_defaults(func=command_click_template)

    key = subparsers.add_parser("key", help="Send a key or key combo")
    add_title(key)
    key.add_argument("--keys", required=True, help="Example: Enter, Shift+Up, Ctrl+A")
    key.add_argument("--hold-ms", type=int, default=20)
    key.add_argument("--activate", action=argparse.BooleanOptionalAction, default=True)
    key.add_argument("--force-dangerous", action="store_true")
    key.add_argument("--execute", action="store_true", help="Actually send input")
    key.set_defaults(func=command_key)

    connect_peer = subparsers.add_parser("connect-peer", help="Start Parsec and connect to a peer_id")
    connect_peer.add_argument("--peer-id", required=True)
    connect_peer.add_argument("--parsecd", help="Explicit parsecd.exe path")
    connect_peer.add_argument(
        "--settings",
        action="append",
        default=[],
        help="Optional Parsec setting, appended as :key=value",
    )
    connect_peer.add_argument("--wait-status", action="store_true", help="Watch Parsec log for connect result")
    connect_peer.add_argument("--timeout", type=float, default=8.0, help="Status wait timeout seconds")
    connect_peer.add_argument("--log-path", help="Explicit Parsec client log path")
    connect_peer.add_argument("--app-log-path", help="Explicit Parsec app log path for current-status fallback")
    connect_peer.add_argument("--current-status-max-age", type=float, default=1800.0)
    connect_peer.add_argument("--execute", action="store_true", help="Actually launch Parsec")
    connect_peer.set_defaults(func=command_connect_peer)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = args.func(args)
        print(_json_result(**result))
        return 0 if result.get("ok", False) else 2
    except Exception as exc:
        print(_json_result(ok=False, action=getattr(args, "command", None), error=str(exc)), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
