import subprocess

import pytest

from src.utils import shutdown_scheduler as scheduler
from src.utils.config import AppConfig, ConfigManager


def test_system_config_defaults_shutdown_on():
    config = AppConfig()

    assert config.system.shutdown_enabled is True
    assert config.system.shutdown_time == "00:00"
    assert config.system.shutdown_force is True


def test_missing_system_config_loads_shutdown_on_defaults():
    config = ConfigManager()._dict_to_config({
        "ui": {"show_tooltips": False},
        "version": "1.0.211",
    })

    assert config.system.shutdown_enabled is True
    assert config.system.shutdown_time == "00:00"
    assert config.system.shutdown_force is True


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0:00", "00:00"),
        ("00:00", "00:00"),
        ("23:59", "23:59"),
        (" 7:05 ", "07:05"),
    ],
)
def test_normalize_shutdown_time_accepts_valid_hhmm(value, expected):
    assert scheduler.normalize_shutdown_time(value) == expected


@pytest.mark.parametrize("value", ["", "24:00", "12:60", "7", "07:5"])
def test_normalize_shutdown_time_rejects_invalid_hhmm(value):
    with pytest.raises(ValueError):
        scheduler.normalize_shutdown_time(value)


def test_register_shutdown_task_builds_schtasks_command(monkeypatch):
    calls = []

    monkeypatch.setattr(scheduler.sys, "platform", "win32")

    def fake_run(args):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="ok", stderr="")

    monkeypatch.setattr(scheduler, "_run_schtasks", fake_run)

    result = scheduler.register_shutdown_task("0:00", force=True)

    assert result.ok is True
    assert calls == [[
        "/Create",
        "/TN",
        scheduler.TASK_NAME,
        "/SC",
        "DAILY",
        "/ST",
        "00:00",
        "/TR",
        "shutdown.exe /s /f /t 0",
        "/F",
    ]]


def test_register_shutdown_task_can_disable_force(monkeypatch):
    calls = []

    monkeypatch.setattr(scheduler.sys, "platform", "win32")

    def fake_run(args):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="ok", stderr="")

    monkeypatch.setattr(scheduler, "_run_schtasks", fake_run)

    result = scheduler.register_shutdown_task("23:30", force=False)

    assert result.ok is True
    assert calls[0][calls[0].index("/TR") + 1] == "shutdown.exe /s /t 0"


def test_sync_shutdown_task_from_config_unregisters_when_disabled(monkeypatch):
    calls = []
    config = AppConfig()
    config.system.shutdown_enabled = False

    monkeypatch.setattr(scheduler.sys, "platform", "win32")

    def fake_run(args):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="ok", stderr="")

    monkeypatch.setattr(scheduler, "_run_schtasks", fake_run)

    result = scheduler.sync_shutdown_task_from_config(config.system)

    assert result.ok is True
    assert calls == [
        ["/Query", "/TN", scheduler.TASK_NAME],
        ["/Delete", "/TN", scheduler.TASK_NAME, "/F"],
    ]


def test_unregister_shutdown_task_treats_missing_task_as_success(monkeypatch):
    calls = []

    monkeypatch.setattr(scheduler.sys, "platform", "win32")

    def fake_run(args):
        calls.append(args)
        return subprocess.CompletedProcess(
            args,
            1,
            stdout="",
            stderr="ERROR: The system cannot find the file specified.",
        )

    monkeypatch.setattr(scheduler, "_run_schtasks", fake_run)

    result = scheduler.unregister_shutdown_task()

    assert result.ok is True
    assert result.status == "미등록"
    assert calls == [["/Query", "/TN", scheduler.TASK_NAME]]


def test_shutdown_task_missing_output_handles_localized_messages():
    assert scheduler._task_missing_output("ERROR: The system cannot find the file specified.")
    assert scheduler._task_missing_output("작업을 찾을 수 없습니다.")
