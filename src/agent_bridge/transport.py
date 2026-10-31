"""Windows local-only named pipe, JSON framing, explicit user ACL and session key."""

import ctypes
from ctypes import wintypes as wt
import hmac
import os
from pathlib import Path
import secrets
import threading

from .contracts import BridgeError, MAX_MESSAGE, PROTOCOL, decode, encoded
from .storage import new_id, read_json, write_json


def _security():
    if os.name != "nt":
        raise BridgeError("UNSUPPORTED_PLATFORM", "The live bridge requires Windows")
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wt.HANDLE
    kernel.CloseHandle.argtypes = [wt.HANDLE]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    adv.OpenProcessToken.argtypes = [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)]
    adv.GetTokenInformation.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD)]
    adv.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.LPWSTR)]
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wt.LPCWSTR, wt.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    token = wt.HANDLE()
    if not adv.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        size = wt.DWORD()
        adv.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        if not adv.GetTokenInformation(token, 1, buffer, size, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        text = wt.LPWSTR()
        if not adv.ConvertSidToStringSidW(sid, ctypes.byref(text)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            current_sid = text.value
        finally:
            kernel.LocalFree(ctypes.cast(text, ctypes.c_void_p))
    finally:
        kernel.CloseHandle(token)
    descriptor = ctypes.c_void_p()
    # No inherited Everyone/anonymous ACL; deny network identities explicitly.
    sddl = f"D:P(D;OICI;GA;;;NU)(A;OICI;GA;;;{current_sid})"
    if not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
        raise ctypes.WinError(ctypes.get_last_error())
    return adv, kernel, descriptor


def protect_directory(path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    adv, kernel, descriptor = _security()
    try:
        present, defaulted = wt.BOOL(), wt.BOOL()
        dacl = ctypes.c_void_p()
        adv.GetSecurityDescriptorDacl.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.BOOL), ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wt.BOOL)]
        adv.SetNamedSecurityInfoW.argtypes = [wt.LPWSTR, wt.DWORD, wt.DWORD,
                                             ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        if not adv.GetSecurityDescriptorDacl(descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)):
            raise ctypes.WinError(ctypes.get_last_error())
        result = adv.SetNamedSecurityInfoW(str(path), 1, 4 | 0x80000000, None, None, dacl, None)
        if result:
            raise ctypes.WinError(result)
    finally:
        kernel.LocalFree(descriptor)


class LocalHost:
    def __init__(self, service, connection_dir):
        self.service = service
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.error = None
        self.token = secrets.token_hex(32)
        connection_id = new_id("app_")
        self.address = "\\\\.\\pipe\\wincro-ai-" + connection_id
        connection_dir = Path(connection_dir)
        protect_directory(connection_dir)
        self.connection_file = connection_dir / (connection_id + ".json")
        self.thread = threading.Thread(target=self._serve, daemon=True, name="wincro-ai-pipe")

    def start(self):
        self.thread.start()
        if not self.ready.wait(4) or self.error:
            self.close()
            raise BridgeError("CONNECTION_FAILED", "The local editor connection could not start")
        write_json(self.connection_file, {"protocol": PROTOCOL, "instance_id": self.service.instance_id,
                    "address": self.address, "token": self.token})
        return str(self.connection_file)

    def _create(self):
        adv, kernel, descriptor = _security()

        class Attributes(ctypes.Structure):
            _fields_ = [("length", wt.DWORD), ("descriptor", ctypes.c_void_p), ("inherit", wt.BOOL)]

        attributes = Attributes(ctypes.sizeof(Attributes), descriptor, False)
        kernel.CreateNamedPipeW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, wt.DWORD,
                                          wt.DWORD, wt.DWORD, wt.DWORD, ctypes.POINTER(Attributes)]
        kernel.CreateNamedPipeW.restype = wt.HANDLE
        try:
            handle = kernel.CreateNamedPipeW(self.address, 3 | 0x40000000 | 0x80000,
                                             4 | 2 | 8, 1, 65536, 65536, 0, ctypes.byref(attributes))
            if handle == ctypes.c_void_p(-1).value:
                raise ctypes.WinError(ctypes.get_last_error())
            return int(handle)
        finally:
            kernel.LocalFree(descriptor)

    def _serve(self):
        import _winapi
        from multiprocessing.connection import PipeConnection
        while not self.stop.is_set():
            handle = None
            try:
                handle = self._create()
                self.ready.set()
                try:
                    pending = _winapi.ConnectNamedPipe(handle, overlapped=True)
                except OSError as exc:
                    if exc.winerror != 535:  # ERROR_PIPE_CONNECTED
                        raise
                else:
                    while _winapi.WaitForSingleObject(pending.event, 100) != 0:
                        if self.stop.is_set():
                            pending.cancel()
                            pending.GetOverlappedResult(True)
                            return
                    pending.GetOverlappedResult(True)
                connection = PipeConnection(handle)
                handle = None
                with connection:
                    if not connection.poll(3):
                        continue
                    request = decode(connection.recv_bytes(MAX_MESSAGE))
                    response = self.handle_request(request)
                    data = encoded(response)
                    if len(data) > MAX_MESSAGE:
                        data = encoded({"ok": False, "code": "MESSAGE_TOO_LARGE", "message": "Read fewer frames/actions"})
                    connection.send_bytes(data)
            except (OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
                if not self.ready.is_set():
                    self.error = type(exc).__name__
                    self.ready.set()
                    return
            finally:
                if handle is not None:
                    _winapi.CloseHandle(handle)

    def handle_request(self, request):
        try:
            if self.stop.is_set():
                raise BridgeError("APP_NOT_CONNECTED", "Connection is closed")
            if not isinstance(request, dict) or request.get("protocol") != PROTOCOL:
                raise BridgeError("PROTOCOL_MISMATCH", "Unsupported protocol")
            if request.get("instance_id") != self.service.instance_id:
                raise BridgeError("WRONG_INSTANCE", "Wrong application instance")
            token = request.get("token")
            if not isinstance(token, str) or not token.isascii() or not hmac.compare_digest(token, self.token):
                raise BridgeError("PERMISSION_REQUIRED", "Invalid session credential")
            method = request.get("method")
            if method == "connection_status":
                return {"ok": True, "result": {"protocol": PROTOCOL, "instance_id": self.service.instance_id,
                    "editing_only": True, "live_execution": False, "source": "saved_files_only"}}
            arguments = request.get("arguments", {})
            if not isinstance(arguments, dict):
                raise BridgeError("INVALID_REQUEST", "Tool arguments must be an object")
            with self.service.lock:
                if self.stop.is_set():
                    raise BridgeError("APP_NOT_CONNECTED", "Connection is closed")
                return {"ok": True, "result": self.service.dispatch(method, arguments)}
        except BridgeError as exc:
            return {"ok": False, "code": exc.code, "message": str(exc)}
        except Exception:
            return {"ok": False, "code": "OPERATION_FAILED", "message": "Operation failed; reread state before retrying"}

    def close(self):
        self.stop.set()
        self.connection_file.unlink(missing_ok=True)


def call(connection_file, method, arguments):
    from multiprocessing.connection import Client
    info = read_json(connection_file)
    if info.get("protocol") != PROTOCOL or not str(info.get("address", "")).startswith("\\\\.\\pipe\\wincro-ai-app_"):
        raise BridgeError("PROTOCOL_MISMATCH", "Select a WinCro AI connection descriptor")
    with Client(info["address"], family="AF_PIPE") as connection:
        connection.send_bytes(encoded({"protocol": PROTOCOL, "instance_id": info["instance_id"],
            "token": info["token"], "method": method, "arguments": arguments}))
        if not connection.poll(45):
            raise BridgeError("RESULT_UNKNOWN", "Reply timed out; check the draft before retrying")
        return decode(connection.recv_bytes(MAX_MESSAGE))
