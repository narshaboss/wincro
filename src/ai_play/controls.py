"""User-only F10/F11 registration, owned and cleaned up by the watch thread."""

import ctypes
from ctypes import wintypes


class PlayHotkeys:
    bindings = {0x5C10: (0x79, "intervene"), 0x5C11: (0x7A, "pause")}

    def __init__(self):
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.user.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        self.user.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                         wintypes.UINT, wintypes.UINT, wintypes.UINT]
        self.registered, self.failed = [], []
        try:
            for identity, (key, name) in self.bindings.items():
                if self.user.RegisterHotKey(None, identity, 0x4000, key):
                    self.registered.append(identity)
                else:
                    self.failed.append((name, ctypes.get_last_error()))
        except Exception:
            self.close()
            raise

    def poll(self):
        result = []
        message = wintypes.MSG()
        for _ in range(32):
            if not self.user.PeekMessageW(ctypes.byref(message), None, 0x0312, 0x0312, 1):
                break
            identity = int(message.wParam)
            if identity in self.registered:
                result.append(self.bindings[identity][1])
        return result

    def close(self):
        for identity in self.registered:
            self.user.UnregisterHotKey(None, identity)
        self.registered.clear()
