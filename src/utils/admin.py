"""
WinCro 관리자 권한 유틸리티

관리자 권한 확인 및 권한 상승 기능을 제공합니다.
"""

import sys
import ctypes
from typing import Optional

from .logger import get_logger

logger = get_logger(__name__)


def is_admin() -> bool:
    """
    현재 프로세스가 관리자 권한으로 실행 중인지 확인합니다.

    Returns:
        bool: 관리자 권한이면 True
    """
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def run_as_admin(script_path: Optional[str] = None, wait: bool = False) -> bool:
    """
    관리자 권한으로 스크립트를 재실행합니다.

    Args:
        script_path: 실행할 스크립트 경로 (None이면 현재 스크립트)
        wait: True면 새 프로세스가 종료될 때까지 대기

    Returns:
        bool: 성공 여부
    """
    if is_admin():
        logger.info("이미 관리자 권한으로 실행 중")
        return True

    try:
        if script_path is None:
            script_path = sys.argv[0]

        # Python 실행 파일 경로
        python_exe = sys.executable

        # 명령줄 인자
        params = ' '.join([f'"{arg}"' for arg in sys.argv[1:]])
        if script_path.endswith('.py'):
            cmd = f'"{script_path}" {params}'
        else:
            cmd = params

        logger.info(f"관리자 권한으로 재실행: {python_exe} {cmd}")

        # ShellExecute로 UAC 권한 상승 요청
        # SW_SHOWNORMAL = 1
        ret = ctypes.windll.shell32.ShellExecuteW(
            None,           # hwnd
            "runas",        # lpOperation (관리자 권한 요청)
            python_exe,     # lpFile
            f'"{script_path}" {params}',  # lpParameters
            None,           # lpDirectory
            1               # nShowCmd (SW_SHOWNORMAL)
        )

        # ShellExecuteW returns > 32 on success
        if ret > 32:
            logger.info("관리자 권한 실행 요청 성공")
            return True
        else:
            logger.error(f"관리자 권한 실행 실패: 에러 코드 {ret}")
            return False

    except Exception as e:
        logger.error(f"관리자 권한 실행 오류: {e}")
        return False


def restart_as_admin() -> bool:
    """
    현재 앱을 관리자 권한으로 재시작합니다.
    성공하면 현재 프로세스는 종료됩니다.

    Returns:
        bool: 요청 성공 여부 (성공 시 프로세스 종료됨)
    """
    if is_admin():
        logger.info("이미 관리자 권한으로 실행 중")
        return True

    if run_as_admin():
        logger.info("관리자 권한으로 재시작 - 현재 프로세스 종료")
        sys.exit(0)

    return False


def get_admin_status_text() -> str:
    """
    현재 관리자 권한 상태를 텍스트로 반환합니다.

    Returns:
        str: 상태 텍스트
    """
    if is_admin():
        return "관리자 권한으로 실행 중"
    else:
        return "일반 사용자 권한으로 실행 중"
