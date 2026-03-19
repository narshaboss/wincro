"""
WinCro 애플리케이션 진입점

애플리케이션을 시작하는 메인 스크립트입니다.
"""

import sys
import os

# 프로젝트 루트를 Python 경로에 추가
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def main() -> int:
    """
    애플리케이션 메인 함수

    Returns:
        int: 종료 코드
    """
    try:
        # 로깅 초기화 (가장 먼저)
        from src.utils.logger import get_logger, set_log_level
        logger = get_logger(__name__)
        logger.info("=" * 50)
        logger.info("작업도우미 시작")
        logger.info("=" * 50)

        # 의존성 확인
        if not check_dependencies():
            logger.error("필수 의존성이 설치되지 않았습니다")
            print("오류: 필수 패키지를 설치해주세요.")
            print("pip install -r requirements.txt")
            return 1

        # 관리자 권한 자동 상승 확인
        if check_admin_elevation():
            # 관리자 권한으로 재시작됨 - 현재 프로세스 종료
            return 0

        # 애플리케이션 실행
        from src.app import run_app
        return run_app()

    except ImportError as e:
        print(f"모듈 임포트 오류: {e}")
        print("필수 패키지를 설치해주세요: pip install -r requirements.txt")
        return 1

    except Exception as e:
        print(f"예상치 못한 오류: {e}")
        return 1


def check_admin_elevation() -> bool:
    """
    관리자 권한 자동 상승 확인

    설정에서 '시작 시 관리자 권한 요청'이 활성화되어 있고
    현재 관리자 권한이 아니면 관리자 권한으로 재시작합니다.

    Returns:
        bool: 재시작이 요청되었으면 True (현재 프로세스 종료 필요)
    """
    try:
        from src.utils.admin import is_admin, run_as_admin
        from src.utils.config import get_config

        # 이미 관리자 권한이면 패스
        if is_admin():
            return False

        # 설정 확인
        config = get_config()
        if not config.ui.run_as_admin:
            return False

        # 관리자 권한으로 재시작 시도
        print("관리자 권한으로 재시작 중...")
        if run_as_admin():
            return True  # 재시작 성공 - 현재 프로세스 종료 필요

        return False

    except Exception as e:
        print(f"관리자 권한 확인 오류: {e}")
        return False


def check_dependencies() -> bool:
    """
    필수 의존성 확인

    Returns:
        bool: 모든 의존성이 설치되었는지 여부
    """
    required_packages = [
        ("customtkinter", "customtkinter"),
        ("cv2", "opencv-python"),
        ("numpy", "numpy"),
        ("mss", "mss"),
        ("pyautogui", "pyautogui"),
        ("pynput", "pynput"),
        ("PIL", "Pillow"),
        ("cryptography", "cryptography"),
    ]

    missing = []
    for import_name, package_name in required_packages:
        try:
            __import__(import_name)
        except ImportError:
            missing.append(package_name)

    if missing:
        print("누락된 패키지:")
        for pkg in missing:
            print(f"  - {pkg}")
        return False

    return True


def cli() -> None:
    """CLI 진입점"""
    sys.exit(main())


if __name__ == "__main__":
    cli()
