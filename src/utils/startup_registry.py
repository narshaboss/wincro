"""
Windows startup registration helpers.

The app name changed several times during development.  Startup registration
must therefore repair stale Run entries instead of only toggling the current
entry when the checkbox changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import sys
from typing import Iterable, Optional

from .app_identity import (
    LEGACY_EXECUTABLE_ALIASES,
    PRIMARY_APP_NAME,
    get_startup_entry_name,
)
from .logger import get_logger

logger = get_logger(__name__)

RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
LEGACY_STARTUP_ENTRY_NAMES = (
    "WinCro",
    "결재 도우미",
    "결제 도우미",
    "결제도우미",
    "작업도우미",
    "dwm",
)


@dataclass
class StartupSyncResult:
    ok: bool
    enabled: bool
    entry_name: str = ""
    command: str = ""
    removed_entries: list[str] = field(default_factory=list)
    detail: str = ""


def _quote(value: str) -> str:
    escaped = str(value).replace('"', r'\"')
    return f'"{escaped}"'


def build_startup_command() -> str:
    """Return the command that should be stored in HKCU Run."""
    if getattr(sys, "frozen", False):
        return _quote(sys.executable)

    python_exe = Path(sys.executable)
    pythonw_exe = python_exe
    if python_exe.name.lower() == "python.exe":
        candidate = python_exe.with_name("pythonw.exe")
        if candidate.exists():
            pythonw_exe = candidate

    project_root = Path(__file__).resolve().parents[2]
    main_script = project_root / "src" / "main.py"
    return f"{_quote(str(pythonw_exe))} {_quote(str(main_script))}"


def get_auto_start_entry_candidates(ui_config, extra_candidates: Optional[Iterable[str]] = None) -> list[str]:
    candidates = {
        PRIMARY_APP_NAME,
        (getattr(ui_config, "app_name", "") or "").strip(),
        (getattr(ui_config, "random_name_alias", "") or "").strip(),
        get_startup_entry_name(ui_config),
        *LEGACY_STARTUP_ENTRY_NAMES,
    }
    for exe_name in LEGACY_EXECUTABLE_ALIASES:
        stem = Path(exe_name).stem.strip()
        if stem:
            candidates.add(stem)
    if extra_candidates:
        for candidate in extra_candidates:
            name = (candidate or "").strip()
            if name:
                candidates.add(name)
    return sorted(name for name in candidates if name)


def sync_auto_start_registry(
    ui_config,
    enable: bool,
    *,
    command: Optional[str] = None,
    extra_candidates: Optional[Iterable[str]] = None,
    winreg_module=None,
) -> StartupSyncResult:
    """Synchronize HKCU Run with the current app identity.

    When enabled, stale legacy names are removed and the current startup entry is
    always overwritten with the current executable command.  When disabled, all
    known app startup entries are removed.
    """
    try:
        if winreg_module is None:
            import winreg as winreg_module  # type: ignore
    except Exception as e:
        return StartupSyncResult(False, bool(enable), detail=f"winreg unavailable: {e}")

    entry_name = get_startup_entry_name(ui_config)
    startup_command = command or build_startup_command()
    removed_entries: list[str] = []
    key = None

    try:
        key = winreg_module.OpenKey(
            winreg_module.HKEY_CURRENT_USER,
            RUN_KEY_PATH,
            0,
            winreg_module.KEY_SET_VALUE | winreg_module.KEY_QUERY_VALUE,
        )

        for candidate in get_auto_start_entry_candidates(ui_config, extra_candidates):
            if enable and candidate == entry_name:
                continue
            try:
                winreg_module.DeleteValue(key, candidate)
                removed_entries.append(candidate)
            except FileNotFoundError:
                pass

        if enable:
            winreg_module.SetValueEx(key, entry_name, 0, winreg_module.REG_SZ, startup_command)
            logger.info(
                "[자동시작] 레지스트리 동기화: %s -> %s (removed=%s)",
                entry_name,
                startup_command,
                removed_entries,
            )
        else:
            logger.info("[자동시작] 레지스트리 비활성화 동기화: removed=%s", removed_entries)

        return StartupSyncResult(
            True,
            bool(enable),
            entry_name=entry_name,
            command=startup_command if enable else "",
            removed_entries=removed_entries,
            detail="synced",
        )
    except Exception as e:
        logger.error("[자동시작] 레지스트리 동기화 실패: %s", e)
        return StartupSyncResult(
            False,
            bool(enable),
            entry_name=entry_name,
            command=startup_command if enable else "",
            removed_entries=removed_entries,
            detail=str(e),
        )
    finally:
        if key is not None:
            try:
                winreg_module.CloseKey(key)
            except Exception:
                pass
