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

# Arduino CLI 다운로드 URL (Windows 64-bit)
ARDUINO_CLI_URL = "https://downloads.arduino.cc/arduino-cli/arduino-cli_latest_Windows_64bit.zip"


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
            zip_ref.extractall(ARDUINO_CLI_DIR)

        # zip 파일 삭제
        zip_path.unlink()

        # Arduino AVR 코어 설치
        if ARDUINO_CLI_EXE.exists():
            logger.info("Arduino AVR 코어 설치 중...")
            if progress_callback:
                progress_callback("Arduino 코어 설치 중...")

            # 코어 인덱스 업데이트
            update_result = subprocess.run(
                [str(ARDUINO_CLI_EXE), "core", "update-index"],
                capture_output=True,
                text=True,
                timeout=120
            )
            if update_result.returncode != 0:
                logger.warning(f"코어 인덱스 업데이트 실패: {update_result.stderr}")

            # AVR 코어 설치
            install_result = subprocess.run(
                [str(ARDUINO_CLI_EXE), "core", "install", "arduino:avr"],
                capture_output=True,
                text=True,
                timeout=300
            )
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
        result = subprocess.run(
            [str(ARDUINO_CLI_EXE), "core", "list"],
            capture_output=True,
            text=True,
            timeout=30
        )
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
        subprocess.run(
            [str(ARDUINO_CLI_EXE), "core", "update-index"],
            capture_output=True,
            timeout=120
        )

        # AVR 코어 설치
        result = subprocess.run(
            [str(ARDUINO_CLI_EXE), "core", "install", "arduino:avr"],
            capture_output=True,
            text=True,
            timeout=300
        )

        if result.returncode != 0:
            logger.error(f"AVR 코어 설치 실패: {result.stderr}")
            return False

        logger.info("AVR 코어 설치 완료")

        # Mouse/Keyboard 라이브러리 설치
        if progress_callback:
            progress_callback("HID 라이브러리 설치 중...")

        subprocess.run(
            [str(ARDUINO_CLI_EXE), "lib", "install", "Mouse"],
            capture_output=True,
            timeout=120
        )
        subprocess.run(
            [str(ARDUINO_CLI_EXE), "lib", "install", "Keyboard"],
            capture_output=True,
            timeout=120
        )

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
            subprocess.run(
                [str(ARDUINO_CLI_EXE), "lib", "install", "Mouse"],
                capture_output=True,
                timeout=120
            )
            subprocess.run(
                [str(ARDUINO_CLI_EXE), "lib", "install", "Keyboard"],
                capture_output=True,
                timeout=120
            )
            logger.info("HID 라이브러리 설치 완료")
        except Exception as e:
            logger.warning(f"HID 라이브러리 설치 중 경고: {e}")

        # 4. 스케치 파일 확인
        if not ensure_sketch_directory():
            return False, "스케치 파일을 찾을 수 없습니다"

        # 4. 컴파일
        if progress_callback:
            progress_callback("펌웨어 컴파일 중...")

        logger.info("펌웨어 컴파일 시작...")
        compile_result = subprocess.run(
            [
                str(ARDUINO_CLI_EXE),
                "compile",
                "--fqbn", "arduino:avr:leonardo",
                str(SKETCH_PATH)
            ],
            capture_output=True,
            text=True,
            timeout=120
        )

        if compile_result.returncode != 0:
            logger.error(f"컴파일 실패: {compile_result.stderr}")
            return False, f"컴파일 실패: {compile_result.stderr[:200]}"

        # 4. 업로드
        if progress_callback:
            progress_callback("펌웨어 업로드 중...")

        logger.info(f"펌웨어 업로드 시작: {port}")
        upload_result = subprocess.run(
            [
                str(ARDUINO_CLI_EXE),
                "upload",
                "-p", port,
                "--fqbn", "arduino:avr:leonardo",
                str(SKETCH_PATH)
            ],
            capture_output=True,
            text=True,
            timeout=60
        )

        if upload_result.returncode != 0:
            logger.error(f"업로드 실패: {upload_result.stderr}")
            return False, f"업로드 실패: {upload_result.stderr[:200]}"

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
