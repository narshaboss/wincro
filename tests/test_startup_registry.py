from pathlib import Path

from src.utils import startup_registry


class DummyUI:
    def __init__(self, app_name="업무지원도구", random_name_alias=""):
        self.app_name = app_name
        self.random_name_alias = random_name_alias


class FakeWinreg:
    HKEY_CURRENT_USER = object()
    KEY_SET_VALUE = 1
    KEY_QUERY_VALUE = 2
    REG_SZ = 1

    def __init__(self, initial=None):
        self.values = dict(initial or {})
        self.closed = False

    def OpenKey(self, *_args):
        return self

    def SetValueEx(self, _key, name, _reserved, _reg_type, value):
        self.values[name] = value

    def DeleteValue(self, _key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]

    def CloseKey(self, _key):
        self.closed = True


def test_startup_command_source_run_uses_main_entrypoint(monkeypatch, tmp_path):
    python_exe = tmp_path / "python.exe"
    pythonw_exe = tmp_path / "pythonw.exe"
    python_exe.write_text("", encoding="utf-8")
    pythonw_exe.write_text("", encoding="utf-8")

    monkeypatch.setattr(startup_registry.sys, "executable", str(python_exe))
    monkeypatch.setattr(startup_registry.sys, "frozen", False, raising=False)

    command = startup_registry.build_startup_command()

    assert str(pythonw_exe) in command
    assert str(Path("src") / "main.py") in command
    assert "app.py" not in command


def test_startup_candidates_include_legacy_names():
    candidates = startup_registry.get_auto_start_entry_candidates(DummyUI())

    assert "업무지원도구" in candidates
    assert "WinCro" in candidates
    assert "결재 도우미" in candidates
    assert "결제도우미" in candidates
    assert "dwm" in candidates


def test_sync_auto_start_registry_replaces_legacy_entries():
    fake = FakeWinreg({
        "WinCro": "old",
        "결재 도우미": "old",
        "Discord": "keep",
    })

    result = startup_registry.sync_auto_start_registry(
        DummyUI(),
        True,
        command='"C:\\App\\업무지원도구.exe"',
        winreg_module=fake,
    )

    assert result.ok is True
    assert fake.values["업무지원도구"] == '"C:\\App\\업무지원도구.exe"'
    assert "WinCro" not in fake.values
    assert "결재 도우미" not in fake.values
    assert fake.values["Discord"] == "keep"
    assert fake.closed is True


def test_sync_auto_start_registry_disable_removes_all_known_entries():
    fake = FakeWinreg({
        "업무지원도구": "current",
        "작업도우미": "old",
        "dwm": "old",
        "Discord": "keep",
    })

    result = startup_registry.sync_auto_start_registry(
        DummyUI(),
        False,
        winreg_module=fake,
    )

    assert result.ok is True
    assert "업무지원도구" not in fake.values
    assert "작업도우미" not in fake.values
    assert "dwm" not in fake.values
    assert fake.values["Discord"] == "keep"
