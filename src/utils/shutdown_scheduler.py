"""Windows scheduled shutdown integration for WinCro."""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Optional

from .logger import get_logger


logger = get_logger(__name__)

TASK_NAME = "WinCroDailyShutdown"
_TIME_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*$")


@dataclass(frozen=True)
class ShutdownScheduleResult:
    ok: bool
    status: str
    detail: str = ""


def normalize_shutdown_time(value: str) -> str:
    """Return a Task Scheduler compatible HH:MM time."""
    match = _TIME_RE.match(str(value or ""))
    if not match:
        raise ValueError("시간은 HH:MM 형식이어야 합니다")
    hour = int(match.group(1))
    minute = int(match.group(2))
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError("시간은 00:00-23:59 범위여야 합니다")
    return f"{hour:02d}:{minute:02d}"


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def _run_schtasks(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["schtasks", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        check=False,
    )


def is_shutdown_task_registered() -> bool:
    if not _is_windows():
        return False
    result = _run_schtasks(["/Query", "/TN", TASK_NAME])
    return result.returncode == 0


def get_shutdown_task_status() -> ShutdownScheduleResult:
    if not _is_windows():
        return ShutdownScheduleResult(False, "미지원", "Windows에서만 지원됩니다")
    result = _run_schtasks(["/Query", "/TN", TASK_NAME])
    if result.returncode == 0:
        return ShutdownScheduleResult(True, "등록됨")
    return ShutdownScheduleResult(False, "미등록", (result.stderr or result.stdout or "").strip())


def register_shutdown_task(shutdown_time: str, force: bool = True) -> ShutdownScheduleResult:
    if not _is_windows():
        return ShutdownScheduleResult(False, "미지원", "Windows에서만 지원됩니다")

    try:
        normalized_time = normalize_shutdown_time(shutdown_time)
    except ValueError as exc:
        return ShutdownScheduleResult(False, "시간 오류", str(exc))

    shutdown_args = "shutdown.exe /s"
    if force:
        shutdown_args += " /f"
    shutdown_args += " /t 0"

    result = _run_schtasks([
        "/Create",
        "/TN", TASK_NAME,
        "/SC", "DAILY",
        "/ST", normalized_time,
        "/TR", shutdown_args,
        "/F",
    ])
    if result.returncode == 0:
        logger.info(f"[PC자동종료] 작업 등록 완료: time={normalized_time} force={force}")
        return ShutdownScheduleResult(True, "등록됨", f"{normalized_time} force={force}")

    detail = (result.stderr or result.stdout or "").strip()
    logger.error(f"[PC자동종료] 작업 등록 실패: {detail}")
    return ShutdownScheduleResult(False, "등록 실패", detail)


def unregister_shutdown_task() -> ShutdownScheduleResult:
    if not _is_windows():
        return ShutdownScheduleResult(False, "미지원", "Windows에서만 지원됩니다")

    result = _run_schtasks(["/Delete", "/TN", TASK_NAME, "/F"])
    output = (result.stderr or result.stdout or "").strip()
    if result.returncode == 0:
        logger.info("[PC자동종료] 작업 삭제 완료")
        return ShutdownScheduleResult(True, "미등록")
    if "cannot find" in output.lower() or "찾을 수" in output:
        return ShutdownScheduleResult(True, "미등록")

    logger.error(f"[PC자동종료] 작업 삭제 실패: {output}")
    return ShutdownScheduleResult(False, "삭제 실패", output)


def sync_shutdown_task_from_config(system_config) -> ShutdownScheduleResult:
    enabled = bool(getattr(system_config, "shutdown_enabled", True))
    shutdown_time = str(getattr(system_config, "shutdown_time", "00:00") or "00:00")
    force = bool(getattr(system_config, "shutdown_force", True))
    if enabled:
        return register_shutdown_task(shutdown_time, force=force)
    return unregister_shutdown_task()
