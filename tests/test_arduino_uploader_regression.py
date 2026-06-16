from pathlib import Path
from types import SimpleNamespace

from src.utils import arduino_uploader


def _result(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def test_compile_uses_wincro_owned_config_and_build_paths(monkeypatch, tmp_path):
    calls = []
    state_dir = tmp_path / "state"
    config_dir = state_dir / "config"
    build_dir = state_dir / "build"
    output_dir = state_dir / "output"
    sketch_dir = tmp_path / "wincro_hid"
    sketch_dir.mkdir()

    monkeypatch.setattr(arduino_uploader, "ARDUINO_STATE_DIR", state_dir)
    monkeypatch.setattr(arduino_uploader, "ARDUINO_CONFIG_DIR", config_dir)
    monkeypatch.setattr(arduino_uploader, "ARDUINO_BUILD_DIR", build_dir)
    monkeypatch.setattr(arduino_uploader, "ARDUINO_OUTPUT_DIR", output_dir)
    monkeypatch.setattr(arduino_uploader, "SKETCH_PATH", sketch_dir)
    monkeypatch.setattr(arduino_uploader, "ARDUINO_CLI_EXE", tmp_path / "arduino-cli.exe")
    monkeypatch.setattr(
        arduino_uploader.subprocess,
        "run",
        lambda args, **kwargs: calls.append((args, kwargs)) or _result(),
    )

    assert arduino_uploader._compile_firmware(clean=True).returncode == 0

    args, kwargs = calls[0]
    assert "--config-dir" in args
    assert str(config_dir) in args
    assert "compile" in args
    assert "--build-path" in args
    assert str(build_dir) in args
    assert "--output-dir" in args
    assert str(output_dir) in args
    assert "--clean" in args
    assert kwargs["encoding"] == "utf-8"
    assert kwargs["errors"] == "replace"
    assert kwargs["timeout"] == 120


def test_upload_firmware_repairs_avr_core_after_leonardo_link_error(monkeypatch):
    compile_results = [
        _result(
            1,
            stderr=(
                "undefined reference to `PluggableUSB()'\n"
                "undefined reference to `USB_Send(unsigned char, void const*, int)'\n"
                "undefined reference to `main'"
            ),
        ),
        _result(0),
    ]
    repair_calls = []
    upload_calls = []

    monkeypatch.setattr(arduino_uploader, "is_arduino_cli_installed", lambda: True)
    monkeypatch.setattr(arduino_uploader, "is_avr_core_installed", lambda: True)
    monkeypatch.setattr(arduino_uploader, "ensure_sketch_directory", lambda: True)
    monkeypatch.setattr(arduino_uploader, "_run_arduino_cli", lambda *args, timeout: _result())
    monkeypatch.setattr(
        arduino_uploader,
        "_compile_firmware",
        lambda clean=True: compile_results.pop(0),
    )
    monkeypatch.setattr(
        arduino_uploader,
        "_repair_avr_core",
        lambda progress_callback=None: repair_calls.append(True) or True,
    )
    monkeypatch.setattr(
        arduino_uploader,
        "_upload_compiled_firmware",
        lambda port: upload_calls.append(port) or _result(),
    )

    assert arduino_uploader.upload_firmware("COM7") == (True, "펌웨어 업로드 성공")
    assert repair_calls == [True]
    assert upload_calls == ["COM7"]
    assert compile_results == []


def test_leonardo_link_error_is_recoverable_avr_core_error():
    output = (
        "HID.cpp:154: undefined reference to `PluggableUSB()'\n"
        "HID.cpp:91: undefined reference to `USB_Send(unsigned char, void const*, int)'\n"
        "crtatmega32u4.o:(.init9+0x0): undefined reference to `main'"
    )

    assert arduino_uploader._is_recoverable_avr_core_compile_error(output) is True
    assert arduino_uploader._is_recoverable_avr_core_compile_error("syntax error") is False
