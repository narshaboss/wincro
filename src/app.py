"""
WinCro 메인 앱 클래스

애플리케이션의 전체 생명주기를 관리합니다.
"""

from datetime import datetime
from typing import Optional

from .utils.logger import get_logger, set_log_level
from .utils.config import get_config, save_config
from .database import get_db
from .ui import MainWindow, RecorderView, AnalyzerView, PlayerView, SettingsView, GuideView, show_help_dialog

logger = get_logger(__name__)


class WinCroApp:
    """
    WinCro 애플리케이션 클래스

    애플리케이션의 초기화, 실행, 종료를 관리합니다.
    """

    def __init__(self):
        """애플리케이션 초기화"""
        self._config = get_config()
        self._db = get_db()
        self._main_window: Optional[MainWindow] = None

        # 뷰 인스턴스
        self._recorder_view: Optional[RecorderView] = None
        self._analyzer_view: Optional[AnalyzerView] = None
        self._player_view: Optional[PlayerView] = None
        self._settings_view: Optional[SettingsView] = None
        self._guide_view: Optional[GuideView] = None

        logger.info("WinCro 애플리케이션 초기화")

    def initialize(self) -> bool:
        """
        애플리케이션 초기화

        Returns:
            bool: 초기화 성공 여부
        """
        try:
            # 설정 업데이트
            self._config.last_opened = datetime.now().isoformat()
            if self._config.first_run:
                self._config.first_run = False
                logger.info("첫 실행 감지")

            if not save_config():
                logger.warning("설정 저장 실패 - 기본 설정으로 계속")

            # 업데이트 후 플랜 파일 병합 (plans_user_backup이 있으면)
            self._merge_user_plans()

            # 메인 윈도우 생성
            self._main_window = MainWindow()

            # 작은창 모드가 아닐 때만 뷰 생성
            if self._config.ui.window_mode != "small":
                self._create_views()

            logger.info("애플리케이션 초기화 완료")
            return True

        except Exception as e:
            logger.error(f"애플리케이션 초기화 실패: {e}")
            return False

    def _create_views(self) -> None:
        """뷰 생성 및 메인 윈도우에 연결"""
        # 뷰 컨테이너 가져오기
        container = self._main_window.get_view_container()

        # 녹화 뷰
        self._recorder_view = RecorderView(container)
        self._main_window.set_recorder_view(self._recorder_view)

        # 분석 뷰
        self._analyzer_view = AnalyzerView(container)
        self._main_window.set_analyzer_view(self._analyzer_view)

        # 실행 뷰
        self._player_view = PlayerView(container)
        self._main_window.set_player_view(self._player_view)

        # 설정 뷰
        self._settings_view = SettingsView(container)
        self._main_window.set_settings_view(self._settings_view)

        # 가이드 뷰
        self._guide_view = GuideView(container)
        self._main_window.set_guide_view(self._guide_view)

        logger.debug("모든 뷰 생성 완료")

    def run(self) -> int:
        """
        애플리케이션 실행

        Returns:
            int: 종료 코드 (0: 정상, 1: 오류)
        """
        try:
            if not self._main_window:
                if not self.initialize():
                    return 1

            # 시작 시 사용법 표시 (설정된 경우에만)
            if self._config.ui.show_help_on_startup:
                self._main_window.after(500, lambda: show_help_dialog(self._main_window))

            # 아두이노 자동 연결 (설정된 경우)
            if self._config.arduino.enabled and self._config.arduino.auto_connect and self._config.arduino.com_port:
                self._main_window.after(1500, self._auto_connect_arduino)

            # 자동 업데이트 확인 (설정된 경우)
            if self._config.update.auto_check and self._config.update.github_repo:
                self._main_window.after(2000, self._auto_check_update)

            logger.info("애플리케이션 실행")
            self._main_window.mainloop()

            return 0

        except KeyboardInterrupt:
            logger.info("사용자에 의해 중단됨")
            return 0

        except Exception as e:
            logger.error(f"애플리케이션 실행 중 오류: {e}")
            return 1

        finally:
            self._cleanup()

    def _cleanup(self) -> None:
        """리소스 정리"""
        try:
            # 뷰 정리
            if self._recorder_view:
                self._recorder_view.cleanup()
            if self._analyzer_view:
                self._analyzer_view.cleanup()
            if self._player_view:
                self._player_view.cleanup()

            # 설정 저장
            save_config()

            logger.info("리소스 정리 완료")

        except Exception as e:
            logger.error(f"리소스 정리 중 오류: {e}")

    def show_view(self, view_name: str) -> None:
        """
        특정 뷰로 전환

        Args:
            view_name: 뷰 이름 (recorder, analyzer, player, settings)
        """
        if self._main_window:
            self._main_window.set_tab(view_name)

    def get_statistics(self) -> dict:
        """애플리케이션 통계 조회"""
        return self._db.get_statistics()

    def _auto_connect_arduino(self) -> None:
        """아두이노 자동 연결"""
        try:
            from .utils.arduino_hid import get_arduino_hid
            arduino = get_arduino_hid()
            if arduino.connect():
                logger.info("아두이노 자동 연결 성공")
            else:
                logger.warning("아두이노 자동 연결 실패 - 수동으로 연결하세요")
        except Exception as e:
            logger.error(f"아두이노 자동 연결 오류: {e}")

    def _auto_check_update(self) -> None:
        """시작 시 자동 업데이트 확인"""
        import threading

        def check_thread():
            try:
                from .utils.updater import check_for_update
                from .utils.config import APP_VERSION

                repo = self._config.update.github_repo
                result = check_for_update(repo, APP_VERSION)

                if result and result.get("update_available"):
                    new_version = result.get("version")
                    release_data = result.get("release_data")

                    # 메인 스레드에서 UI 업데이트
                    self._main_window.after(0, lambda: self._show_update_dialog(new_version, release_data))

            except Exception as e:
                logger.error(f"자동 업데이트 확인 오류: {e}")

        thread = threading.Thread(target=check_thread, daemon=True)
        thread.start()

    def _show_update_dialog(self, new_version: str, release_data: dict) -> None:
        """업데이트 알림 다이얼로그"""
        from tkinter import messagebox
        from .utils.config import APP_VERSION

        result = messagebox.askyesno(
            "업데이트 알림",
            f"새 버전이 있습니다!\n\n"
            f"현재 버전: v{APP_VERSION}\n"
            f"새 버전: v{new_version}\n\n"
            f"지금 업데이트하시겠습니까?"
        )

        if result:
            # 설정 탭으로 이동해서 업데이트 진행
            self._main_window.set_tab("settings")
            if self._settings_view:
                self._settings_view._latest_release = release_data
                self._settings_view._perform_update()

    def _merge_user_plans(self) -> None:
        """업데이트 후 사용자 플랜 파일 병합 (새 버전 우선, 같은 이름은 덮어쓰기)"""
        import shutil
        from .utils.config import DATA_DIR

        plans_dir = DATA_DIR / "plans"
        backup_dir = DATA_DIR / "plans_user_backup"

        if not backup_dir.exists():
            return  # 백업 폴더 없으면 병합할 것 없음

        logger.info("사용자 플랜 파일 병합 시작")
        plans_dir.mkdir(parents=True, exist_ok=True)

        restored_count = 0
        for backup_file in backup_dir.glob("*.json"):
            target_file = plans_dir / backup_file.name

            if not target_file.exists():
                # 새 버전에 없는 파일만 복원 (사용자가 직접 만든 플랜)
                shutil.copy2(backup_file, target_file)
                logger.info(f"플랜 복원: {backup_file.name}")
                restored_count += 1
            # 같은 이름 있으면 새 버전 유지 (덮어쓰기 안 함 = 새 버전 우선)

        # 백업 폴더 삭제
        try:
            shutil.rmtree(backup_dir)
            logger.info(f"사용자 플랜 병합 완료: {restored_count}개 복원")
        except Exception as e:
            logger.error(f"백업 폴더 삭제 실패: {e}")


# 전역 앱 인스턴스
_app_instance: Optional[WinCroApp] = None


def get_app() -> WinCroApp:
    """앱 인스턴스 반환"""
    global _app_instance
    if _app_instance is None:
        _app_instance = WinCroApp()
    return _app_instance


def run_app() -> int:
    """애플리케이션 실행 헬퍼 함수"""
    app = get_app()
    return app.run()
