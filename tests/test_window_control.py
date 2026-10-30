"""Simulation-only tests: no real DLL, window, process or input operations."""

import ast
import ctypes
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.utils import window_control as wc


CONFIG = {"executable_name": "Game.exe", "title_contains": "Adventure"}


@pytest.fixture(autouse=True)
def forbid_native_loading(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Simulation attempted to load native APIs")

    monkeypatch.setattr(ctypes, "WinDLL", forbidden, raising=False)


def window(**changes):
    values = dict(
        handle=101, process_id=42, executable_name="Game.exe",
        title="Adventure - Player", minimized=False, maximized=False,
        process_creation_time=123456789, thread_id=57,
    )
    values.update(changes)
    return wc.WindowInfo(**values)


class FakeBackend:
    supported = True

    def __init__(self, windows=()):
        self.windows = list(windows)
        self.foreground = 0
        self.calls = []
        self.snapshots = 0
        self.reads = 0
        self.foreground_reads = 0
        self.apply_show = False
        self.apply_foreground = True
        self.show_accepted = True
        self.foreground_accepted = True
        self.restore_maximized = False
        self.hung = False
        self.on_list = None
        self.on_get = None
        self.on_mutation = None

    def list_windows(self):
        self.snapshots += 1
        if self.on_list:
            self.on_list(self)
        return list(self.windows)

    def get_window(self, handle):
        self.reads += 1
        if self.on_get:
            self.on_get(self)
        return next((item for item in self.windows if item.handle == handle), None)

    def get_foreground_window(self):
        self.foreground_reads += 1
        return self.foreground

    def is_hung(self, handle):
        return self.hung

    def show_window_async(self, target, operation):
        self.calls.append((operation, target.handle))
        if self.apply_show:
            if operation == "minimize":
                updated = replace(target, minimized=True)
            elif operation == "maximize":
                updated = replace(target, minimized=False, maximized=True)
            else:
                assert operation == "restore"
                updated = replace(target, minimized=False, maximized=self.restore_maximized)
            self.windows = [updated if item.handle == target.handle else item for item in self.windows]
        if self.on_mutation:
            self.on_mutation(self)
        return self.show_accepted

    def set_foreground_window(self, target):
        self.calls.append(("foreground", target.handle))
        if self.apply_foreground:
            self.foreground = target.handle
        if self.on_mutation:
            self.on_mutation(self)
        return self.foreground_accepted


def test_config_normalization_is_a_detached_persistent_only_copy():
    source = {
        "executable_name": "  Game.EXE  ", "title_contains": " Adventure ",
        "operation": " FOREGROUND ", "handle": 101, "process_id": 42,
    }
    result = wc.normalize_window_control_config(source)
    assert result == {
        "executable_name": "Game.EXE", "title_contains": "Adventure", "operation": "foreground",
    }
    assert source["executable_name"] == "  Game.EXE  "
    result["title_contains"] = "changed"
    assert source["title_contains"] == " Adventure "
    assert wc.normalize_window_control_config()["operation"] == "maximize"
    assert wc.validate_window_control_config(CONFIG)["operation"] == "maximize"


@pytest.mark.parametrize("name", [
    "", ".exe", "..exe", "game", "game.executable", "game.exe.bak",
    r"C:\Games\game.exe", "games/game.exe", r"games\game.exe", "C:game.exe",
    "game?.exe", "game*.exe", "game|.exe", "game<.exe", "game\x00.exe", "game\n.exe",
])
def test_validation_rejects_non_executable_basenames(name):
    with pytest.raises(ValueError, match="실행 파일명"):
        wc.validate_window_control_config({**CONFIG, "executable_name": name})


@pytest.mark.parametrize("operation", ["", " ", "typo", "restore", "close", None, 1, False])
def test_unknown_or_wrong_typed_operations_never_fall_back(operation):
    backend = FakeBackend([window()])
    with pytest.raises(ValueError, match="창 동작"):
        wc.WindowController(backend).step({**CONFIG, "operation": operation})
    assert backend.snapshots == 0
    assert backend.calls == []


@pytest.mark.parametrize("config", [
    [], "Game.exe", 1, {}, None,
    {**CONFIG, "executable_name": None}, {**CONFIG, "title_contains": None},
    {**CONFIG, "title_contains": "   "}, {**CONFIG, "title_contains": "a\x00b"},
])
def test_config_errors_precede_all_backend_access(config):
    backend = FakeBackend([window()])
    with pytest.raises(ValueError):
        wc.WindowController(backend).step(config)
    assert backend.snapshots == 0
    assert backend.calls == []


def test_constants_summary_and_optional_snapshot_states():
    assert wc.WINDOW_CONTROL_ACTION_TYPE == "window_control"
    assert set(wc.WINDOW_CONTROL_OPERATIONS) == {"maximize", "minimize", "foreground"}
    for operation, label in wc.WINDOW_CONTROL_OPERATIONS.items():
        summary = wc.format_window_control_summary({**CONFIG, "operation": operation})
        assert summary == f"{label} | Game.exe | Adventure"
    assert wc.format_window_control_summary({
        "executable_name": "chrome.exe", "title_contains": "미꾸라지",
    }) == "최대화 | chrome.exe | 미꾸라지"
    snapshot = wc.WindowInfo(1, 2, "a.exe", "title")
    assert snapshot.minimized is None and snapshot.maximized is None
    with pytest.raises(FrozenInstanceError):
        snapshot.handle = 3


@pytest.mark.parametrize("config", [
    None, {}, [], "broken import", {"handle": 123},
    {**CONFIG, "operation": "wrong"}, {**CONFIG, "operation": None},
    {**CONFIG, "title_contains": ""}, {**CONFIG, "executable_name": "not a file"},
])
def test_invalid_summary_does_not_crash(config):
    assert wc.format_window_control_summary(config) == "창 제어 설정 필요"
    with pytest.raises(ValueError):
        wc.validate_window_control_config(config)


def test_summary_handles_typeerror_without_weakening_runtime_validation(monkeypatch):
    def malformed(config):
        raise TypeError("malformed imported config")

    monkeypatch.setattr(wc, "validate_window_control_config", malformed)
    assert wc.format_window_control_summary(CONFIG) == "창 제어 설정 필요"
    with pytest.raises(TypeError):
        wc.WindowController(FakeBackend()).step(CONFIG)


def test_list_windows_is_a_fresh_read_only_snapshot():
    backend = FakeBackend([window()])
    controller = wc.WindowController(backend)
    first = controller.list_windows()
    first.clear()
    assert controller.list_windows() == [window()]
    assert backend.snapshots == 2 and backend.calls == []


@pytest.mark.parametrize("platform", ["linux", "darwin", "freebsd"])
def test_default_backend_is_lazy_and_non_windows_is_explicitly_unsupported(monkeypatch, platform):
    def forbidden_backend():
        raise AssertionError("Unsupported platform constructed a Windows backend")

    monkeypatch.setattr(wc.sys, "platform", platform)
    monkeypatch.setattr(wc, "_WindowsBackend", forbidden_backend)
    controller = wc.WindowController()
    assert controller.list_windows() == []
    result = controller.step(CONFIG)
    assert not result.ready and result.status == "unsupported" and "Windows" in result.message
    assert result.detail == result.message


def test_windows_controller_construction_does_not_load_dlls(monkeypatch):
    monkeypatch.setattr(wc.sys, "platform", "win32")
    controller = wc.WindowController()
    assert controller._backend is None


def test_injected_unsupported_backend_is_not_called():
    backend = FakeBackend([window()])
    backend.supported = False
    controller = wc.WindowController(backend)
    assert controller.step(CONFIG).status == "unsupported"
    assert controller.list_windows() == []
    assert backend.snapshots == 0 and backend.calls == []


def test_matching_requires_casefolded_exact_exe_and_title_substring():
    backend = FakeBackend([
        window(handle=1, executable_name="other.exe"),
        window(handle=2, executable_name="NotGame.exe"),
        window(handle=3, title="Different title"),
        window(handle=4, executable_name="gAME.EXE", title="An ADVENTURE awaits", maximized=True),
    ])
    backend.foreground = 4
    assert wc.WindowController(backend).step(CONFIG).ready
    assert backend.calls == []


def test_matching_uses_unicode_casefold_not_only_lower():
    backend = FakeBackend([window(title="STRASSE", maximized=True)])
    backend.foreground = 101
    assert wc.WindowController(backend).step({**CONFIG, "title_contains": "Stra\u00dfe"}).ready


def test_missing_duplicates_and_reappearance_with_new_handles_do_not_cache_target():
    backend = FakeBackend()
    controller = wc.WindowController(backend)
    assert controller.step(CONFIG).status == "missing"
    backend.windows = [window(), window(handle=202, process_id=52)]
    assert controller.step(CONFIG).status == "ambiguous"
    assert backend.calls == []
    backend.windows = [window(handle=303, process_id=63, maximized=True)]
    backend.foreground = 303
    assert controller.step(CONFIG).ready
    backend.windows = []
    assert controller.step(CONFIG).status == "missing"
    backend.windows = [window(handle=404, process_id=74, maximized=True, process_creation_time=999)]
    backend.foreground = 404
    assert controller.step(CONFIG).ready
    assert backend.calls == []


@pytest.mark.parametrize("changes", [
    {"handle": 202}, {"process_id": 99}, {"process_creation_time": 888}, {"thread_id": 66},
    {"executable_name": "other.exe"}, {"title": "Wrong title"},
    {"title": "Adventure - another window"},
])
def test_identity_mutation_between_snapshot_and_direct_read_blocks_side_effects(changes):
    backend = FakeBackend([window()])

    def mutate(fake):
        fake.on_get = None
        fake.windows = [replace(fake.windows[0], **changes)]

    backend.on_get = mutate
    result = wc.WindowController(backend).step(CONFIG)
    assert not result.ready and result.status == "pending"
    assert backend.calls == []


def test_duplicate_appearing_immediately_before_mutation_blocks_selection():
    backend = FakeBackend([window()])

    def duplicate(fake):
        if fake.snapshots == 3:
            fake.windows.append(window(handle=202))

    backend.on_list = duplicate
    assert wc.WindowController(backend).step(CONFIG).status == "ambiguous"
    assert backend.calls == []


@pytest.mark.parametrize("after", ["missing", "duplicate", "reuse"])
def test_post_side_effect_identity_and_uniqueness_are_revalidated(after):
    backend = FakeBackend([window()])
    backend.apply_show = True
    backend.foreground = 101

    def mutate(fake):
        if after == "missing":
            fake.windows = []
        elif after == "duplicate":
            fake.windows.append(window(handle=202))
        else:
            fake.windows = [replace(fake.windows[0], process_creation_time=987)]

    backend.on_mutation = mutate
    result = wc.WindowController(backend).step(CONFIG)
    assert not result.ready
    assert result.status == {"missing": "missing", "duplicate": "ambiguous", "reuse": "pending"}[after]
    assert backend.calls == [("maximize", 101)]


def test_step_uses_detached_config_even_when_caller_changes_it():
    config = dict(CONFIG)
    backend = FakeBackend([window()])
    backend.on_list = lambda fake: config.update(operation="minimize", handle=999)
    assert wc.WindowController(backend).step(config).status == "pending"
    assert backend.calls == [("maximize", 101)]


def test_maximize_uses_at_most_one_request_per_step_and_requires_all_three_states():
    backend = FakeBackend([window(minimized=True)])
    backend.apply_show = True
    controller = wc.WindowController(backend)
    assert controller.step(CONFIG).status == "pending"
    assert backend.calls == [("maximize", 101)]
    assert controller.step(CONFIG).ready
    assert backend.calls == [("maximize", 101), ("foreground", 101)]


@pytest.mark.parametrize("minimized,maximized,foreground,expected", [
    (False, True, 101, "ready"), (False, True, 0, "pending"),
    (True, True, 101, "pending"), (False, False, 101, "pending"),
])
def test_maximize_does_not_claim_success_from_only_foreground_or_zoomed(
    minimized, maximized, foreground, expected,
):
    backend = FakeBackend([window(minimized=minimized, maximized=maximized)])
    backend.foreground = foreground
    backend.apply_foreground = False
    assert wc.WindowController(backend).step(CONFIG).status == expected
    if maximized and not minimized:
        assert all(operation != "maximize" for operation, _ in backend.calls)


def test_already_maximized_window_is_not_resized_or_restored():
    backend = FakeBackend([window(maximized=True)])
    assert wc.WindowController(backend).step(CONFIG).ready
    assert backend.calls == [("foreground", 101)]
    assert backend.windows[0].maximized is True


@pytest.mark.parametrize("maximized", [False, True])
def test_foreground_preserves_normal_or_maximized_state(maximized):
    backend = FakeBackend([window(maximized=maximized)])
    assert wc.WindowController(backend).step({**CONFIG, "operation": "foreground"}).ready
    assert backend.calls == [("foreground", 101)]
    assert backend.windows[0].maximized == maximized


@pytest.mark.parametrize("restore_maximized", [False, True])
def test_foreground_restores_only_minimized_windows_then_retries_externally(restore_maximized):
    backend = FakeBackend([window(minimized=True)])
    backend.apply_show = True
    backend.restore_maximized = restore_maximized
    controller = wc.WindowController(backend)
    config = {**CONFIG, "operation": "foreground"}
    assert controller.step(config).status == "pending"
    assert backend.calls == [("restore", 101)]
    assert backend.windows[0].maximized == restore_maximized
    assert controller.step(config).ready
    assert backend.calls == [("restore", 101), ("foreground", 101)]


@pytest.mark.parametrize("already_minimized", [False, True])
def test_minimize_never_requests_or_reads_foreground(already_minimized):
    backend = FakeBackend([window(minimized=already_minimized, maximized=True)])
    backend.foreground = 999
    backend.apply_show = True
    assert wc.WindowController(backend).step({**CONFIG, "operation": "minimize"}).ready
    assert backend.calls == ([] if already_minimized else [("minimize", 101)])
    assert backend.foreground == 999 and backend.foreground_reads == 0


def test_accepted_minimize_is_pending_until_iconic_is_observed():
    backend = FakeBackend([window()])
    assert wc.WindowController(backend).step({**CONFIG, "operation": "minimize"}).status == "pending"
    assert backend.calls == [("minimize", 101)]


@pytest.mark.parametrize("accepted,applied", [(True, False), (False, False), (False, True)])
def test_foreground_verification_not_return_code_decides_success(accepted, applied):
    backend = FakeBackend([window(maximized=True)])
    backend.foreground_accepted = accepted
    backend.apply_foreground = applied
    result = wc.WindowController(backend).step(CONFIG)
    assert result.ready is applied
    assert result.status == ("ready" if applied else "pending")
    assert backend.calls == [("foreground", 101)]


def test_hung_foreground_target_is_not_activated():
    backend = FakeBackend([window(maximized=True)])
    backend.hung = True
    result = wc.WindowController(backend).step(CONFIG)
    assert result.status == "pending" and "응답하지 않아" in result.message
    assert backend.calls == []


@pytest.mark.parametrize("changes", [{"minimized": None}, {"maximized": None}])
def test_unknown_state_is_not_assumed_ready(changes):
    backend = FakeBackend([window(**changes)])
    result = wc.WindowController(backend).step(CONFIG)
    assert result.status == "pending" and backend.calls == []


def test_state_change_before_mutation_defers_instead_of_using_stale_action():
    backend = FakeBackend([window()])

    def maximize_elsewhere(fake):
        if fake.snapshots == 3:
            fake.windows = [replace(fake.windows[0], maximized=True)]

    backend.on_list = maximize_elsewhere
    assert wc.WindowController(backend).step(CONFIG).status == "pending"
    assert backend.calls == []


def test_identity_changed_while_reading_foreground_is_not_reported_ready():
    backend = FakeBackend([window(maximized=True)])
    backend.foreground = 101

    def mutate_on_final_read(fake):
        if fake.reads == 3:
            fake.windows = [replace(fake.windows[0], process_id=123)]

    backend.on_get = mutate_on_final_read
    assert wc.WindowController(backend).step(CONFIG).status == "pending"
    assert backend.calls == []


def test_snapshot_failure_never_uses_partial_results():
    backend = FakeBackend([window()])

    def fail(fake):
        raise OSError("enumeration failed")

    backend.on_list = fail
    controller = wc.WindowController(backend)
    result = controller.step(CONFIG)
    assert result.status == "pending" and "enumeration failed" in result.message
    with pytest.raises(OSError):
        controller.list_windows()
    assert backend.calls == []


class NativeSimulation:
    """Python objects with the Win32 ABI's output-buffer calling convention."""

    def __init__(self):
        self.handle = 0x12345678ABCDEF
        self.process_handle = 0x23456789ABCDEF
        self.records = {self.handle: dict(
            pid=42, thread=57, image=r"C:\Games\Adventure\Game.exe", title="Adventure - Player",
            visible=True, minimized=False, maximized=False, style=0, creation=0x123456789AB,
            root=self.handle,
        )}
        self.calls = []
        self.caption_reads = []
        self.closed = []
        self.waits = []
        self.foreground = 0
        self.hung = False
        self.query_ok = True
        self.times_ok = True
        self.wait_result = 0x102
        self.enum_ok = True
        self.on_caption = None
        self.on_show = None
        self.on_foreground = None
        self.enum_callback = lambda callback: callback
        self.user32 = SimpleNamespace(
            EnumWindows=self.enumerate,
            IsWindow=lambda handle: handle in self.records,
            GetAncestor=lambda handle, flag: self.records.get(handle, {}).get("root", 0),
            IsWindowVisible=lambda handle: self.records.get(handle, {}).get("visible", False),
            IsIconic=lambda handle: self.records.get(handle, {}).get("minimized", False),
            IsZoomed=lambda handle: self.records.get(handle, {}).get("maximized", False),
            IsHungAppWindow=lambda handle: self.hung,
            GetWindowThreadProcessId=self.owner,
            GetWindowTextW=self.caption,
            ShowWindowAsync=self.show,
            SetForegroundWindow=self.focus,
            GetForegroundWindow=lambda: self.foreground,
        )
        self.kernel32 = SimpleNamespace(
            OpenProcess=self.open_process,
            CloseHandle=self.close,
            QueryFullProcessImageNameW=self.query,
            GetProcessTimes=self.times,
            WaitForSingleObject=self.wait,
        )

    @property
    def record(self):
        return self.records[self.handle]

    def extended_style(self, handle):
        return self.records.get(handle, {}).get("style")

    def enumerate(self, callback, parameter):
        for handle in list(self.records):
            if not callback(handle, parameter):
                return False
        return self.enum_ok

    def owner(self, handle, pid):
        record = self.records.get(handle, {})
        pid._obj.value = record.get("pid", 0)
        return record.get("thread", 0)

    def open_process(self, access, inherit, pid):
        assert access == 0x101000 and inherit is False
        assert pid == 42 or any(record["pid"] == pid for record in self.records.values())
        return self.process_handle

    def close(self, handle):
        self.closed.append(handle)
        return True

    def wait(self, handle, timeout):
        self.waits.append((handle, timeout))
        return self.wait_result

    def query(self, handle, flags, image, length):
        assert handle == self.process_handle and flags == 0
        image.value = self.record["image"]
        length._obj.value = len(image.value)
        return self.query_ok

    def times(self, handle, creation, exit_time, kernel, user):
        value = self.record["creation"]
        creation._obj.dwHighDateTime = value >> 32
        creation._obj.dwLowDateTime = value & 0xFFFFFFFF
        return self.times_ok

    def caption(self, handle, caption, capacity):
        self.caption_reads.append(handle)
        caption.value = self.records[handle]["title"][:capacity - 1]
        if self.on_caption:
            self.on_caption(self)
        return len(caption.value)

    def show(self, handle, command):
        self.calls.append(("show", handle, command))
        if self.on_show:
            self.on_show(self)
        return True

    def focus(self, handle):
        self.calls.append(("foreground", handle))
        if self.on_foreground:
            self.on_foreground(self)
        return True


def native_backend():
    api = NativeSimulation()
    return api, wc._WindowsBackend(api=api, own_pid=999)


def test_native_snapshot_uses_full_path_basename_and_64bit_transient_identity():
    api, backend = native_backend()
    snapshots = backend.list_windows()
    assert snapshots == [window(
        handle=api.handle, process_creation_time=api.record["creation"],
    )]
    assert api.closed == [api.process_handle]
    assert api.calls == []
    assert all(timeout == 0 for _, timeout in api.waits)


@pytest.mark.parametrize("changes", [
    {"pid": 999}, {"style": 0x80}, {"style": None}, {"root": 1},
    {"visible": False, "minimized": False}, {"pid": 0}, {"thread": 0},
])
def test_native_excludes_own_pid_tool_hidden_child_and_unverifiable_windows(changes):
    api, backend = native_backend()
    api.record.update(changes)
    assert backend.list_windows() == []
    assert api.caption_reads == [] and api.calls == []


def test_native_hidden_iconic_window_is_enumerated_without_restoring_it():
    api, backend = native_backend()
    api.record.update(visible=False, minimized=True)
    assert backend.list_windows()[0].minimized is True
    assert api.calls == []


@pytest.mark.parametrize("failure", ["query", "times", "exited", "wait_failed", "caption", "truncated"])
def test_native_process_handles_are_closed_on_all_read_failures(failure):
    api, backend = native_backend()
    if failure == "query":
        api.query_ok = False
    elif failure == "times":
        api.times_ok = False
    elif failure == "exited":
        api.wait_result = 0
    elif failure == "wait_failed":
        api.wait_result = 0xFFFFFFFF
    elif failure == "caption":
        api.record["title"] = ""
    else:
        api.record["title"] = "x" * 32768
    assert backend.get_window(api.handle) is None
    assert api.closed == [api.process_handle]
    assert api.calls == []


def test_native_inaccessible_process_is_skipped_without_caption_read():
    api, backend = native_backend()
    api.kernel32.OpenProcess = lambda *args: 0
    assert backend.list_windows() == []
    assert api.closed == [] and api.caption_reads == []


def test_native_owner_mutation_during_caption_read_is_rejected():
    api, backend = native_backend()
    api.on_caption = lambda fake: fake.record.update(pid=77)
    assert backend.get_window(api.handle) is None
    assert api.closed == [api.process_handle]


def test_native_failed_enumeration_discards_collected_handles():
    api, backend = native_backend()
    api.enum_ok = False
    with pytest.raises(OSError, match="불완전한 결과를 폐기"):
        backend.list_windows()
    assert api.caption_reads == [] and api.calls == []


@pytest.mark.parametrize("operation,command", [("restore", 9), ("maximize", 3), ("minimize", 7)])
def test_native_show_commands_are_async_and_minimize_never_activates(operation, command):
    api, backend = native_backend()
    api.record["minimized"] = operation == "restore"
    target = backend.get_window(api.handle)
    assert backend.show_window_async(target, operation)
    assert api.calls == [("show", api.handle, command)]


def test_native_maximize_is_idempotent_and_restore_cannot_unmaximize():
    api, backend = native_backend()
    api.record["maximized"] = True
    target = backend.get_window(api.handle)
    assert backend.show_window_async(target, "maximize")
    assert not backend.show_window_async(target, "restore")
    with pytest.raises(ValueError):
        backend.show_window_async(target, "wrong")
    assert api.calls == []


@pytest.mark.parametrize("changes", [
    {"pid": 77}, {"creation": 987}, {"thread": 88},
    {"image": r"C:\Games\Other.exe"}, {"title": "Adventure - Reused"},
])
@pytest.mark.parametrize("operation", ["show", "foreground"])
def test_native_mutators_recheck_identity_not_only_iswindow(changes, operation):
    api, backend = native_backend()
    target = backend.get_window(api.handle)
    api.record.update(changes)
    result = (
        backend.show_window_async(target, "maximize") if operation == "show"
        else backend.set_foreground_window(target)
    )
    assert result is False and api.calls == []


@pytest.mark.parametrize("operation", ["show", "foreground"])
def test_native_mutators_recheck_identity_after_native_request(operation):
    api, backend = native_backend()
    target = backend.get_window(api.handle)

    def reuse(fake):
        fake.record["creation"] = 987

    if operation == "show":
        api.on_show = reuse
        assert not backend.show_window_async(target, "maximize")
    else:
        api.on_foreground = reuse
        assert not backend.set_foreground_window(target)
    assert len(api.calls) == 1


@pytest.mark.parametrize("hung,minimized", [(True, False), (False, True)])
def test_native_foreground_skips_hung_and_minimized_windows(hung, minimized):
    api, backend = native_backend()
    api.hung = hung
    api.record["minimized"] = minimized
    target = backend.get_window(api.handle)
    assert not backend.set_foreground_window(target)
    assert api.calls == []


def test_controller_verifies_native_foreground_instead_of_trusting_success_code():
    api, backend = native_backend()
    api.record["maximized"] = True
    result = wc.WindowController(backend).step(CONFIG)
    assert result.status == "pending"
    assert api.calls == [("foreground", api.handle)]
    api.foreground = api.handle
    assert wc.WindowController(backend).step(CONFIG).ready
    assert api.calls == [("foreground", api.handle)]


class PrototypeFunction:
    def __call__(self, *args):
        raise AssertionError("Prototype configuration invoked a Win32 function")


def test_native_bindings_declare_pointer_sized_handles_and_all_function_signatures(monkeypatch):
    names = (
        "EnumWindows IsWindow GetAncestor IsWindowVisible IsIconic IsZoomed IsHungAppWindow "
        "GetWindowThreadProcessId GetWindowTextW ShowWindowAsync SetForegroundWindow GetForegroundWindow "
        "GetWindowLongPtrW GetWindowLongW OpenProcess CloseHandle QueryFullProcessImageNameW "
        "GetProcessTimes WaitForSingleObject"
    ).split()
    functions = {name: PrototypeFunction() for name in names}
    api = object.__new__(wc._Win32API)
    api.user32 = SimpleNamespace(**functions)
    api.kernel32 = SimpleNamespace(**functions)
    callback_signatures = []

    def callback_type(*signature):
        callback_signatures.append(signature)
        return lambda callback: callback

    monkeypatch.setattr(ctypes, "WINFUNCTYPE", callback_type, raising=False)
    api._configure_prototypes()
    unused = "GetWindowLongW" if ctypes.sizeof(ctypes.c_void_p) == 8 else "GetWindowLongPtrW"
    for name, function in functions.items():
        if name != unused:
            assert hasattr(function, "argtypes") and hasattr(function, "restype"), name
    for name in ("OpenProcess", "GetForegroundWindow", "GetAncestor"):
        assert ctypes.sizeof(functions[name].restype) == ctypes.sizeof(ctypes.c_void_p)
    assert api.get_window_long.restype is ctypes.c_ssize_t
    assert functions["EnumWindows"].argtypes[1] is ctypes.c_ssize_t
    assert callback_signatures == [(wc.wintypes.BOOL, wc.wintypes.HWND, ctypes.c_ssize_t)]
    assert ctypes.sizeof(api.get_window_long.restype) == ctypes.sizeof(ctypes.c_void_p)

    w = wc.wintypes
    assert functions["OpenProcess"].argtypes == [w.DWORD, w.BOOL, w.DWORD]
    for name in ("CloseHandle", "QueryFullProcessImageNameW", "GetProcessTimes", "WaitForSingleObject"):
        assert functions[name].argtypes[0] is w.HANDLE
        assert ctypes.sizeof(functions[name].argtypes[0]) == ctypes.sizeof(ctypes.c_void_p)
    for name in (
        "IsWindow", "GetAncestor", "IsWindowVisible", "IsIconic", "IsZoomed", "IsHungAppWindow",
        "GetWindowThreadProcessId", "GetWindowTextW", "ShowWindowAsync", "SetForegroundWindow",
    ):
        assert functions[name].argtypes[0] is w.HWND
        assert ctypes.sizeof(functions[name].argtypes[0]) == ctypes.sizeof(ctypes.c_void_p)
    assert functions["GetWindowThreadProcessId"].argtypes == [w.HWND, ctypes.POINTER(w.DWORD)]
    assert functions["GetWindowThreadProcessId"].restype is w.DWORD
    assert functions["GetWindowTextW"].argtypes == [w.HWND, w.LPWSTR, ctypes.c_int]
    assert functions["QueryFullProcessImageNameW"].argtypes == [
        w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD),
    ]
    assert functions["GetProcessTimes"].argtypes == [w.HANDLE] + [ctypes.POINTER(w.FILETIME)] * 4
    assert functions["ShowWindowAsync"].argtypes == [w.HWND, ctypes.c_int]
    assert functions["SetForegroundWindow"].argtypes == [w.HWND]
    assert functions["GetForegroundWindow"].argtypes == []
    assert functions["WaitForSingleObject"].argtypes == [w.HANDLE, w.DWORD]


@pytest.mark.parametrize("value,error,expected", [(0, 0, 0), (0, 5, None), (0x80, 0, 0x80)])
def test_native_style_query_distinguishes_zero_style_from_failure(monkeypatch, value, error, expected):
    api = object.__new__(wc._Win32API)
    errors_cleared = []
    monkeypatch.setattr(ctypes, "set_last_error", errors_cleared.append, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: error, raising=False)
    api.get_window_long = lambda handle, index: value
    assert api.extended_style(0x12345678ABCDEF) == expected
    assert errors_cleared == [0]


def test_public_labels_and_validation_errors_are_korean():
    assert wc.WINDOW_CONTROL_OPERATIONS == {
        "maximize": "최대화", "minimize": "최소화", "foreground": "앞으로 가져오기",
    }
    for config in ([], {}, {**CONFIG, "operation": "typo"}, {**CONFIG, "title_contains": None}):
        with pytest.raises(ValueError) as error:
            wc.validate_window_control_config(config)
        assert any("\uac00" <= character <= "\ud7a3" for character in str(error.value))


def test_module_has_no_input_controller_gui_or_eager_native_initialization():
    source = Path(wc.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = [
        alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
    ] + [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert not any(any(part in name for part in (
        "input_controller", "pyautogui", "pynput", "tkinter", "win32gui",
    )) for name in imports)
    forbidden = {
        "SendInput", "keybd_event", "mouse_event", "AttachThreadInput", "SetWindowsHookExW",
        "SendMessageW", "SetWindowPos", "BringWindowToTop", "SwitchToThisWindow", "ShowWindow",
    }
    assert not any(isinstance(node, ast.Attribute) and node.attr in forbidden for node in ast.walk(tree))
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.Expr)):
            assert not any(isinstance(child, ast.Call) for child in ast.walk(node)) or (
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Attribute)
                and isinstance(node.value.func.value, ast.Name)
                and node.value.func.value.id == "re"
                and node.value.func.attr == "compile"
            )
