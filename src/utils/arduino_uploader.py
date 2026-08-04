"""
WinCro Arduino 펌웨어 자동 업로드 모듈

arduino-cli를 사용하여 Arduino Leonardo에 펌웨어를 자동 업로드합니다.
"""

import os
import subprocess
import time
import shutil
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional, Tuple

from .logger import get_logger
from .config import PROJECT_ROOT
from .update_service import safe_extract_zip
import sys

logger = get_logger(__name__)

# 경로 설정 (exe와 스크립트 실행 구분)
if getattr(sys, 'frozen', False):
    # exe로 실행 중 - _internal 폴더 안에 있음
    ARDUINO_DIR = Path(sys.executable).parent / "_internal" / "arduino"
else:
    # 스크립트로 실행 중
    ARDUINO_DIR = PROJECT_ROOT / "arduino"

ARDUINO_CLI_DIR = ARDUINO_DIR / "arduino-cli"
ARDUINO_CLI_EXE = ARDUINO_CLI_DIR / "arduino-cli.exe"
SKETCH_PATH = ARDUINO_DIR / "wincro_hid"
SKETCH_FILE = SKETCH_PATH / "wincro_hid.ino"
_LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA", str(ARDUINO_DIR)))
ARDUINO_STATE_DIR = _LOCALAPPDATA / "WinCro" / "arduino"
ARDUINO_CONFIG_DIR = ARDUINO_STATE_DIR / "config"
ARDUINO_BUILD_DIR = ARDUINO_STATE_DIR / "build"
ARDUINO_OUTPUT_DIR = ARDUINO_STATE_DIR / "output"

# Arduino CLI 다운로드 URL (Windows 64-bit)
ARDUINO_CLI_URL = "https://downloads.arduino.cc/arduino-cli/arduino-cli_latest_Windows_64bit.zip"


def _arduino_cli_cmd(*args: str) -> list[str]:
    """Run arduino-cli with WinCro-owned config/data paths."""
    ARDUINO_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    return [str(ARDUINO_CLI_EXE), "--config-dir", str(ARDUINO_CONFIG_DIR), *args]


def _ensure_build_dirs(clean: bool = False) -> None:
    """Create isolated build/output dirs and optionally clear stale artifacts."""
    ARDUINO_STATE_DIR.mkdir(parents=True, exist_ok=True)
    if clean:
        _safe_remove_tree(ARDUINO_BUILD_DIR)
        _safe_remove_tree(ARDUINO_OUTPUT_DIR)
    ARDUINO_BUILD_DIR.mkdir(parents=True, exist_ok=True)
    ARDUINO_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def _safe_remove_tree(path: Path) -> None:
    """Remove only paths inside the WinCro Arduino state dir."""
    try:
        resolved = path.resolve()
        root = ARDUINO_STATE_DIR.resolve()
        if resolved == root or root not in resolved.parents:
            logger.warning(f"Arduino cache cleanup skipped outside state dir: {path}")
            return
        if resolved.exists():
            shutil.rmtree(resolved, ignore_errors=True)
    except Exception as e:
        logger.warning(f"Arduino cache cleanup failed for {path}: {e}")


def _run_arduino_cli(*args: str, timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        _arduino_cli_cmd(*args),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout
    )


def _combined_output(result: subprocess.CompletedProcess) -> str:
    return "\n".join(part for part in (result.stdout, result.stderr) if part)


def _is_recoverable_avr_core_compile_error(output: str) -> bool:
    needles = (
        "undefined reference to `main'",
        "undefined reference to `PluggableUSB",
        "undefined reference to `USB_Send",
        "undefined reference to `USB_SendControl",
        "crtatmega32u4.o",
    )
    return any(needle in output for needle in needles)


def _repair_avr_core(progress_callback=None) -> bool:
    """Clear WinCro-owned Arduino core/cache and reinstall arduino:avr once."""
    if progress_callback:
        progress_callback("Arduino AVR 코어 복구 중...")

    logger.warning("[Arduino] AVR 코어/캐시 복구 시작")
    try:
        _run_arduino_cli("core", "uninstall", "arduino:avr", timeout=120)
    except Exception as e:
        logger.debug(f"AVR core uninstall ignored during repair: {e}")

    _safe_remove_tree(ARDUINO_CONFIG_DIR / "packages" / "arduino")
    _ensure_build_dirs(clean=True)
    return install_avr_core(progress_callback)


def _compile_firmware(clean: bool = True) -> subprocess.CompletedProcess:
    _ensure_build_dirs(clean=clean)
    return _run_arduino_cli(
        "compile",
        "--fqbn", "arduino:avr:leonardo",
        "--build-path", str(ARDUINO_BUILD_DIR),
        "--output-dir", str(ARDUINO_OUTPUT_DIR),
        "--jobs", "1",
        "--clean",
        str(SKETCH_PATH),
        timeout=120
    )


def _upload_compiled_firmware(port: str) -> subprocess.CompletedProcess:
    return _run_arduino_cli(
        "upload",
        "-p", port,
        "--fqbn", "arduino:avr:leonardo",
        "--build-path", str(ARDUINO_BUILD_DIR),
        str(SKETCH_PATH),
        timeout=60
    )


def is_arduino_cli_installed() -> bool:
    """arduino-cli가 설치되어 있는지 확인"""
    return ARDUINO_CLI_EXE.exists()


def download_arduino_cli(progress_callback=None) -> bool:
    """arduino-cli 다운로드 및 설치"""
    try:
        ARDUINO_CLI_DIR.mkdir(parents=True, exist_ok=True)
        zip_path = ARDUINO_CLI_DIR / "arduino-cli.zip"

        logger.info("arduino-cli 다운로드 중...")
        if progress_callback:
            progress_callback("arduino-cli 다운로드 중...")

        # 다운로드
        urllib.request.urlretrieve(ARDUINO_CLI_URL, zip_path)

        logger.info("압축 해제 중...")
        if progress_callback:
            progress_callback("압축 해제 중...")

        # 압축 해제
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            safe_extract_zip(
                zip_ref,
                ARDUINO_CLI_DIR,
                max_entries=5000,
                max_total_size=1024 * 1024 * 1024,
                max_file_size=512 * 1024 * 1024,
            )

        # zip 파일 삭제
        zip_path.unlink()

        # Arduino AVR 코어 설치
        if ARDUINO_CLI_EXE.exists():
            logger.info("Arduino AVR 코어 설치 중...")
            if progress_callback:
                progress_callback("Arduino 코어 설치 중...")

            # 코어 인덱스 업데이트
            logger.info("[Arduino] 코어 인덱스 업데이트 시작 (최대 120초 소요)...")
            update_result = _run_arduino_cli("core", "update-index", timeout=120)
            if update_result.returncode != 0:
                logger.warning(f"코어 인덱스 업데이트 실패: {update_result.stderr}")

            # AVR 코어 설치
            logger.info("[Arduino] AVR 코어 설치 시작 (최대 180초 소요)...")
            install_result = _run_arduino_cli("core", "install", "arduino:avr", timeout=180)
            if install_result.returncode != 0:
                logger.warning(f"AVR 코어 설치 실패: {install_result.stderr}")
                return False

            logger.info("arduino-cli 설치 완료")
            return True

        return False

    except Exception as e:
        logger.error(f"arduino-cli 설치 실패: {e}")
        return False


def ensure_sketch_directory():
    """스케치 디렉토리 구조 확인 및 생성"""
    # arduino-cli는 스케치가 같은 이름의 폴더 안에 있어야 함
    SKETCH_PATH.mkdir(parents=True, exist_ok=True)

    # 기존 .ino 파일이 arduino 폴더 바로 아래에 있으면 이동
    old_sketch = ARDUINO_DIR / "wincro_hid.ino"
    if old_sketch.exists() and not SKETCH_FILE.exists():
        shutil.move(str(old_sketch), str(SKETCH_FILE))

    return SKETCH_FILE.exists()


def is_avr_core_installed() -> bool:
    """AVR 코어가 설치되어 있는지 확인"""
    if not ARDUINO_CLI_EXE.exists():
        return False

    try:
        result = _run_arduino_cli("core", "list", timeout=30)
        return result.returncode == 0 and result.stdout and "arduino:avr" in result.stdout
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError) as e:
        logger.debug(f"AVR 코어 확인 실패: {e}")
        return False


def install_avr_core(progress_callback=None) -> bool:
    """AVR 코어 설치"""
    try:
        logger.info("Arduino AVR 코어 설치 중...")
        if progress_callback:
            progress_callback("AVR 코어 설치 중...")

        # 코어 인덱스 업데이트
        logger.info("[Arduino] 코어 인덱스 업데이트 시작 (최대 120초 소요)...")
        _run_arduino_cli("core", "update-index", timeout=120)

        # AVR 코어 설치
        logger.info("[Arduino] AVR 코어 설치 시작 (최대 180초 소요)...")
        result = _run_arduino_cli("core", "install", "arduino:avr", timeout=180)

        if result.returncode != 0:
            logger.error(f"AVR 코어 설치 실패: {result.stderr}")
            return False

        logger.info("AVR 코어 설치 완료")

        # Mouse/Keyboard 라이브러리 설치
        if progress_callback:
            progress_callback("HID 라이브러리 설치 중...")

        logger.info("[Arduino] Mouse 라이브러리 설치 시작 (최대 120초 소요)...")
        _run_arduino_cli("lib", "install", "Mouse", timeout=120)
        logger.info("[Arduino] Keyboard 라이브러리 설치 시작 (최대 120초 소요)...")
        _run_arduino_cli("lib", "install", "Keyboard", timeout=120)

        logger.info("HID 라이브러리 설치 완료")
        return True

    except Exception as e:
        logger.error(f"AVR 코어 설치 오류: {e}")
        return False


def upload_firmware(port: str, progress_callback=None) -> Tuple[bool, str]:
    """
    Arduino Leonardo에 펌웨어 업로드

    Args:
        port: COM 포트 (예: "COM7")
        progress_callback: 진행상황 콜백 함수

    Returns:
        (성공여부, 메시지)
    """
    try:
        # 1. arduino-cli 확인/설치
        if not is_arduino_cli_installed():
            if progress_callback:
                progress_callback("arduino-cli 설치 중... (최초 1회)")

            if not download_arduino_cli(progress_callback):
                return False, "arduino-cli 설치 실패"

        # 2. AVR 코어 확인/설치
        if not is_avr_core_installed():
            if progress_callback:
                progress_callback("AVR 코어 설치 중... (최초 1회)")

            if not install_avr_core(progress_callback):
                return False, "AVR 코어 설치 실패"

        # 3. HID 라이브러리 설치 (Mouse, Keyboard)
        if progress_callback:
            progress_callback("HID 라이브러리 확인 중...")

        try:
            logger.info("[Arduino] Mouse 라이브러리 설치 확인 (최대 120초 소요)...")
            _run_arduino_cli("lib", "install", "Mouse", timeout=120)
            logger.info("[Arduino] Keyboard 라이브러리 설치 확인 (최대 120초 소요)...")
            _run_arduino_cli("lib", "install", "Keyboard", timeout=120)
            logger.info("HID 라이브러리 설치 완료")
        except Exception as e:
            logger.warning(f"HID 라이브러리 설치 중 경고: {e}")

        # 4. 스케치 파일 확인
        if not ensure_sketch_directory():
            return False, "스케치 파일을 찾을 수 없습니다"

        # 4. 컴파일
        if progress_callback:
            progress_callback("펌웨어 컴파일 중...")

        logger.info("[Arduino] 펌웨어 컴파일 시작 (최대 120초 소요)...")
        compile_result = _compile_firmware(clean=True)

        if compile_result.returncode != 0:
            compile_output = _combined_output(compile_result)
            if _is_recoverable_avr_core_compile_error(compile_output):
                logger.warning("[Arduino] AVR 코어 링크 오류 감지, 코어 복구 후 1회 재시도")
                if progress_callback:
                    progress_callback("컴파일 캐시/코어 복구 후 재시도 중...")
                if _repair_avr_core(progress_callback):
                    compile_result = _compile_firmware(clean=True)
                    compile_output = _combined_output(compile_result)

            if compile_result.returncode != 0:
                logger.error(f"컴파일 실패: {compile_output}")
                return False, f"컴파일 실패: {compile_output[:200]}"

        # 4. 업로드
        if progress_callback:
            progress_callback("펌웨어 업로드 중...")

        logger.info(f"[Arduino] 펌웨어 업로드 시작: {port} (최대 60초 소요)...")
        upload_result = _upload_compiled_firmware(port)

        if upload_result.returncode != 0:
            upload_output = _combined_output(upload_result)
            logger.error(f"업로드 실패: {upload_output}")
            return False, f"업로드 실패: {upload_output[:200]}"

        logger.info("펌웨어 업로드 완료")
        if progress_callback:
            progress_callback("펌웨어 업로드 완료!")

        return True, "펌웨어 업로드 성공"

    except subprocess.TimeoutExpired:
        return False, "업로드 시간 초과"
    except Exception as e:
        logger.error(f"펌웨어 업로드 오류: {e}")
        return False, f"오류: {str(e)}"


def check_firmware_installed(serial_conn) -> bool:
    """
    펌웨어가 설치되어 있는지 확인 (PING 테스트)

    Args:
        serial_conn: 열린 시리얼 연결

    Returns:
        펌웨어 설치 여부
    """
    try:
        serial_conn.reset_input_buffer()
        serial_conn.write(b"PING\n")
        serial_conn.flush()

        time.sleep(0.3)

        if serial_conn.in_waiting:
            response = serial_conn.readline().decode().strip()
            return response == "PONG"

        return False
    except Exception as e:
        logger.debug(f"펌웨어 확인 실패: {e}")
        return False
