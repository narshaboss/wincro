"""CI-only WM_CONTEXTMENU reproduction on an owned, hidden Win32 window.

No global mouse/keyboard input, cursor movement, activation or user window access.
The mocked mouseUp adapter sends only a message to this disposable HWND.
"""

import os
import sys

import pytest

if not (sys.platform == "win32" and os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("WINCRO_INPUT_RELEASE_NATIVE_QA") == "1"):
    pytest.skip("Native input cleanup QA is restricted to opted-in isolated Windows CI.", allow_module_level=True)

import ctypes
from ctypes import wintypes
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import uuid

from src.utils import input_controller as ic


def test_idle_cleanup_does_not_generate_native_context_menu(monkeypatch):
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    wndproc_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

    class WindowClass(ctypes.Structure):
        _fields_ = [("style", wintypes.UINT), ("wndproc", wndproc_type),
                    ("class_extra", ctypes.c_int), ("window_extra", ctypes.c_int),
                    ("instance", wintypes.HINSTANCE), ("icon", wintypes.HANDLE),
                    ("cursor", wintypes.HANDLE), ("background", wintypes.HBRUSH),
                    ("menu", wintypes.LPCWSTR), ("class_name", wintypes.LPCWSTR)]

    kernel32.GetModuleHandleW.argtypes, kernel32.GetModuleHandleW.restype = [wintypes.LPCWSTR], wintypes.HMODULE
    user32.RegisterClassW.argtypes, user32.RegisterClassW.restype = [ctypes.POINTER(WindowClass)], wintypes.ATOM
    user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                     ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                     wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.DefWindowProcW.restype = ctypes.c_ssize_t
    user32.SendMessageW.argtypes = user32.DefWindowProcW.argtypes
    user32.SendMessageW.restype = ctypes.c_ssize_t
    user32.DestroyWindow.argtypes, user32.DestroyWindow.restype = [wintypes.HWND], wintypes.BOOL
    user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
    user32.UnregisterClassW.restype = wintypes.BOOL
    messages = []

    @wndproc_type
    def wndproc(hwnd, message, wparam, lparam):
        if message in (0x0204, 0x0205, 0x0202, 0x0208, 0x007B):
            messages.append(message)
        if message == 0x007B:  # Record, but never display a popup menu.
            return 0
        return user32.DefWindowProcW(hwnd, message, wparam, lparam)

    instance = kernel32.GetModuleHandleW(None)
    name = "WinCroInputCleanupQA_" + uuid.uuid4().hex
    definition = WindowClass(wndproc=wndproc, instance=instance, class_name=name)
    assert user32.RegisterClassW(ctypes.byref(definition))
    hwnd = None
    evidence = {}
    try:
        hwnd = user32.CreateWindowExW(0, name, name, 0, 0, 0, 320, 200, None, None, instance, None)
        assert hwnd

        def mouse_up(button="left", **_kwargs):
            message = {"left": 0x0202, "right": 0x0205, "middle": 0x0208}[button]
            user32.SendMessageW(hwnd, message, 0, 0)

        # Reproduce the old cleanup policy with a genuine DefWindowProc path.
        for button in ("left", "right", "middle"):
            mouse_up(button)
        assert messages.count(0x007B) == 1 and 0x0204 not in messages
        evidence["old_idle_cleanup_context_requests"] = messages.count(0x007B)
        messages.clear()

        monkeypatch.setattr(ic.pyautogui, "mouseUp", mouse_up)
        monkeypatch.setattr(ic.pyautogui, "keyUp", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(ic, "_get_pressed_mouse_buttons", lambda: set())
        release_hid = Mock(return_value=True)
        monkeypatch.setattr(ic, "_get_arduino", lambda: SimpleNamespace(is_connected=True, release_all=release_hid))
        controller = ic.InputController()
        for _ in range(12):
            assert controller.release_all() is True
        assert messages == [] and release_hid.call_count == 12
        evidence["new_idle_cleanup_mouse_messages"] = len(messages)
        evidence["hid_cleanup_calls"] = release_hid.call_count

        controller._track_button("left", True)
        assert controller.release_all() is True
        assert messages == [0x0202]
        evidence["tracked_left_release_without_context_menu"] = True
    finally:
        if hwnd:
            assert user32.DestroyWindow(hwnd)
        assert user32.UnregisterClassW(name, instance)
        evidence["owned_window_destroyed"] = True
        path = Path(__file__).resolve().parents[1] / "artifacts/window_control/input_release_native_results.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
