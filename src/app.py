"""
WinCro 메인 앱 클래스

애플리케이션의 전체 생명주기를 관리합니다.
"""

from datetime import datetime
from typing import Optional
from concurrent.futures import ThreadPoolExecutor
import atexit
import threading
import sys

from .utils.logger import get_logger, set_log_level, apply_performance_config
from .utils.config import (
    get_config,
    save_config,
    DATA_DIR,
    get_config_load_status,
    get_config_load_error,
    is_startup_config_save_safe,
)
from .utils.plan_sequence_groups import get_active_plan_sequence
from pathlib import Path
from .database import get_db

# 플레이 모드 경량화: MainWindow만 먼저 import, 나머지는 필요할 때 import
from .ui.main_window import MainWindow

logger = get_logger(__name__)

# 초기화 지연 시간 상수 (밀리초)
HELP_DELAY_MS = 500               # 사용법 표시 지연
ARDUINO_DELAY_MS = 1500           # 아두이노 자동연결 지연
UPDATE_CHECK_DELAY_MS = 2000      # 업데이트 확인 지연
AUTO_RUN_DELAY_MS = 5000          # 자동 실행 지연

# 전역 스레드풀 (백그라운드 작업용) - Double-check locking 적용
_thread_pool: Optional[ThreadPoolExecutor] = None
_thread_pool_lock = threading.Lock()


def get_thread_pool() -> ThreadPoolExecutor:
    """전역 스레드풀 반환 (지연 초기화, 스레드 안전)"""
    global _thread_pool
    if _thread_pool is None:
        with _thread_pool_lock:
            # Double-check locking
            if _thread_pool is None:
                config = get_config()
                pool_size = getattr(config.performance, 'thread_pool_size', 4)
                _thread_pool = ThreadPoolExecutor(max_workers=pool_size, thread_name_prefix="task_bg")
                atexit.register(_shutdown_thread_pool)
    return _thread_pool


def _shutdown_thread_pool():
    """프로그램 종료 시 스레드풀 정리"""
    global _thread_pool
    with _thread_pool_lock:
        if _thread_pool:
            try:
                _thread_pool.shutdown(wait=True, cancel_futures=True)
            except TypeError:
                # Python 3.8 이하: cancel_futures 미지원
                _thread_pool.shutdown(wait=False)
            _thread_pool = None


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

        # 성능 설정 적용 (DEBUG 로그 비활성화 등)
        apply_performance_config()

        # 뷰 인스턴스 (지연 import이므로 문자열 타입 힌트 사용)
        self._recorder_view: Optional['RecorderView'] = None
        self._analyzer_view: Optional['AnalyzerView'] = None
        self._player_view: Optional['PlayerView'] = None
        self._settings_view: Optional['SettingsView'] = None
        self._guide_view: Optional['GuideView'] = None

        logger.info("업무지원도구 애플리케이션 초기화")
        logger.debug(f"[설정 로드] window_mode={self._config.ui.window_mode}, auto_check={self._config.update.auto_check}, github_repo={self._config.update.github_repo}")

    def initialize(self) -> bool:
        """
        애플리케이션 초기화

        Returns:
            bool: 초기화 성공 여부
        """
        try:
            # 설정 업데이트
            startup_save_safe = is_startup_config_save_safe()
            self._config.last_opened = datetime.now().isoformat()
            if self._config.first_run:
                self._config.first_run = False
                logger.info("첫 실행 감지")

            if startup_save_safe:
                if not save_config():
                    logger.warning("설정 저장 실패 - 기본 설정으로 계속")
            else:
                logger.warning(
                    f"[config] startup save skipped: status={get_config_load_status()} error={get_config_load_error()}"
                )

            # Packaged playlists are authoritative after update; old plan backups are discarded.
            self._sync_shutdown_schedule_async()
            self._sync_startup_registry_async()
            self._refresh_shortcut_icons_async()
            self._sanitize_transient_local_maps_async()
            self._discard_legacy_plan_backup()

            # 메인 윈도우 생성
            self._main_window = MainWindow()

            # 플레이 모드가 아닐 때만 뷰 생성
            if self._config.ui.window_mode != "play":
                self._create_views()

            logger.info("애플리케이션 초기화 완료")
            return True

        except Exception as e:
            logger.error(f"애플리케이션 초기화 실패: {e}")
            return False

    def _sync_shutdown_schedule_async(self) -> None:
        """환경설정의 PC 자동종료 예약을 Windows 작업 스케줄러와 동기화한다."""
        if get_config_load_status() == "error":
            logger.warning(f"[PC자동종료] 설정 로드 오류로 예약 동기화 건너뜀: {get_config_load_error()}")
            return

        def _worker():
            try:
                from .utils.shutdown_scheduler import sync_shutdown_task_from_config

                result = sync_shutdown_task_from_config(self._config.system)
                if result.ok:
                    logger.info(f"[PC자동종료] 시작 동기화 완료: {result.status} {result.detail}")
                else:
                    logger.warning(f"[PC자동종료] 시작 동기화 실패: {result.status} {result.detail}")
            except Exception as e:
                logger.error(f"[PC자동종료] 시작 동기화 예외: {e}", exc_info=True)

        threading.Thread(target=_worker, daemon=True).start()

    def _sync_startup_registry_async(self) -> None:
        """Repair Windows startup registration after app/exe name changes."""
        if get_config_load_status() == "error":
            logger.warning(f"[자동시작] 설정 로드 오류로 레지스트리 동기화 건너뜀: {get_config_load_error()}")
            return

        def _worker():
            try:
                from .utils.startup_registry import sync_auto_start_registry

                result = sync_auto_start_registry(self._config.ui, self._config.ui.auto_start)
                if result.ok:
                    logger.info(
                        f"[자동시작] 시작 동기화 완료: enabled={result.enabled} "
                        f"entry={result.entry_name} removed={result.removed_entries}"
                    )
                else:
                    logger.warning(f"[자동시작] 시작 동기화 실패: {result.detail}")
            except Exception as e:
                logger.error(f"[자동시작] 시작 동기화 예외: {e}", exc_info=True)

        threading.Thread(target=_worker, daemon=True).start()

    def _refresh_shortcut_icons_async(self) -> None:
        """Repair stale desktop/taskbar shortcuts after older updaters install us."""
        if not getattr(sys, "frozen", False):
            return

        def _worker():
            try:
                from .utils.update_service import refresh_existing_shortcut_icons

                refresh_existing_shortcut_icons()
            except Exception as e:
                logger.warning(f"[아이콘] 시작 자가복구 예외: {e}")

        threading.Thread(target=_worker, daemon=True, name="shortcut-icon-refresh").start()

    def _sanitize_transient_local_maps_async(self) -> None:
        """Clean stale runtime obstacle state from packaged local map files."""
        if not getattr(sys, "frozen", False):
            return

        def _worker():
            try:
                from .player.game_map import sanitize_transient_local_maps

                changed = sanitize_transient_local_maps(str(DATA_DIR / "maps"))
                if changed:
                    logger.info(f"[맵핑] local map 동적장애물 시작 정리 완료: {changed}개")
            except Exception as e:
                logger.warning(f"[맵핑] local map 동적장애물 시작 정리 예외: {e}")

        threading.Thread(target=_worker, daemon=True, name="local-map-sanitize").start()

    def _create_views(self) -> None:
        """뷰 팩토리 등록 (실제 생성은 탭 클릭 시 지연 생성)"""
        container = self._main_window.get_view_container()

        def create_recorder():
            from .ui.recorder_view import RecorderView
            self._recorder_view = RecorderView(container)
            return self._recorder_view

        def create_analyzer():
            from .ui.analyzer_view import AnalyzerView
            self._analyzer_view = AnalyzerView(container)
            return self._analyzer_view

        def create_player():
            from .ui.player_view import PlayerView
            self._player_view = PlayerView(container)
            return self._player_view

        def create_settings():
            from .ui.settings_view import SettingsView
            self._settings_view = SettingsView(container)
            return self._settings_view

        def create_guide():
            from .ui.guide_view import GuideView
            self._guide_view = GuideView(container)
            return self._guide_view

        self._main_window.register_view_factory("recorder", create_recorder)
        self._main_window.register_view_factory("analyzer", create_analyzer)
        self._main_window.register_view_factory("player", create_player)
        self._main_window.register_view_factory("settings", create_settings)
        self._main_window.register_view_factory("guide", create_guide)

        # 첫 번째 탭(녹화)만 즉시 생성
        self._main_window.switch_to_first_view()

        logger.debug("뷰 팩토리 등록 완료 (지연 생성 방식)")

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
                from .ui.help_dialog import show_help_dialog
                self._main_window.after(HELP_DELAY_MS, lambda: show_help_dialog(self._main_window))

            # 아두이노 자동 연결 (설정된 경우)
            if self._config.arduino.enabled and self._config.arduino.auto_connect and self._config.arduino.com_port:
                self._main_window.after(ARDUINO_DELAY_MS, self._auto_connect_arduino)

            # 자동 업데이트 확인 (설정된 경우)
            # 업데이트 확인 완료 후에 자동 실행 시작 (업데이트 있으면 실행 안 함)
            logger.info(f"[자동업데이트] auto_check={self._config.update.auto_check}, github_repo={self._config.update.github_repo}, window_mode={self._config.ui.window_mode}")
            if self._config.update.auto_check and self._config.update.github_repo:
                logger.info("[자동업데이트] 2초 후 업데이트 확인 예약 (완료 후 자동실행 판단)")
                self._main_window.after(UPDATE_CHECK_DELAY_MS, self._auto_check_update)
            else:
                logger.warning(f"[자동업데이트] 자동 업데이트가 비활성화됨 - auto_check={self._config.update.auto_check}")
                # 업데이트 확인 비활성화 시 바로 자동 실행 시작
                plan_paths, _repeats, group = get_active_plan_sequence(self._config.player)
                if self._config.ui.window_mode == "play" and self._config.player.auto_run_enabled and plan_paths:
                    group_name = group.get("name", "그룹") if group else "그룹"
                    logger.info(f"[자동실행] 5초 후 자동실행 그룹 예약: {group_name} ({len(plan_paths)}개 플랜)")
                    self._main_window.after(AUTO_RUN_DELAY_MS, self._auto_run_sequence)

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
            # 생성된 뷰만 정리 (지연 생성으로 None일 수 있음)
            for view in (self._recorder_view, self._analyzer_view, self._player_view,
                         self._settings_view, self._guide_view):
                if view and hasattr(view, 'cleanup'):
                    try:
                        view.cleanup()
                    except Exception as e:
                        logger.debug(f"뷰 정리 오류: {e}")

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

    def _auto_run_sequence(self) -> None:
        """시작 시 자동 실행 - 플랜 순서"""
        try:
            plan_paths, repeats, group = get_active_plan_sequence(self._config.player)
            if not plan_paths:
                logger.warning("[자동실행-시퀀스] 활성 그룹의 플랜 목록이 비어있음")
                return

            # 저장된 절대 경로를 현재 DATA_DIR 기준으로 변환
            converted_paths = []
            plans_dir = DATA_DIR / "plans"
            for path in plan_paths:
                filename = Path(path).name  # 파일명만 추출
                converted_path = str(plans_dir / filename)
                converted_paths.append(converted_path)
            group_name = group.get("name", "그룹") if group else "그룹"
            logger.info(f"[자동실행-시퀀스] 그룹 실행: {group_name}, 경로 변환: {len(converted_paths)}개 플랜")

            if self._main_window and hasattr(self._main_window, 'auto_run_sequence'):
                self._main_window.auto_run_sequence(converted_paths, repeats, group_name=group_name)
            else:
                logger.error("[자동실행-시퀀스] main_window에 auto_run_sequence 메서드 없음")
        except Exception as e:
            logger.error(f"[자동실행-시퀀스] 오류: {e}")

    def _auto_connect_arduino(self) -> None:
        """아두이노 자동 연결 (백그라운드 스레드에서 실행)"""
        def _connect_task():
            try:
                from .utils.arduino_hid import get_arduino_hid
                arduino = get_arduino_hid()
                if arduino.connect():
                    logger.info("아두이노 자동 연결 성공")
                else:
                    logger.warning("아두이노 자동 연결 실패 - 수동으로 연결하세요")
            except Exception as e:
                logger.error(f"아두이노 자동 연결 오류: {e}")

        import threading
        threading.Thread(target=_connect_task, daemon=True).start()

    def _auto_check_update(self) -> None:
        """시작 시 자동 업데이트 확인 및 자동 적용"""
        logger.info(f"[자동업데이트] 업데이트 확인 시작 (window_mode={self._config.ui.window_mode})")

        def check_task():
            try:
                from .utils.updater import check_for_update
                from .utils.config import APP_VERSION

                repo = self._config.update.github_repo
                logger.info(f"[자동업데이트] 서버 확인 중... repo={repo}, current={APP_VERSION}")
                # Startup auto-update must bypass any packaged cache file.
                result = check_for_update(repo, APP_VERSION, force=True)
                logger.info(f"[자동업데이트] 서버 응답: {result}")

                if result and result.get("update_available"):
                    new_version = result.get("version")
                    release_data = result.get("release_data")

                    # 자동 업데이트 수행 (확인 없이) - 자동 실행은 하지 않음
                    logger.info(f"새 버전 발견: v{new_version} - 자동 업데이트 시작 (자동실행 건너뜀)")
                    if self._main_window:
                        self._main_window.after(0, lambda: self._perform_auto_update(new_version, release_data))
                else:
                    # 업데이트 없음 → 자동 실행 시작
                    logger.info("[자동업데이트] 새 버전 없음 - 자동 실행 확인")
                    if self._main_window:
                        self._main_window.after(0, self._start_auto_run_if_needed)

            except Exception as e:
                logger.error(f"자동 업데이트 확인 오류: {e}")
                # 오류 발생해도 자동 실행은 시작
                if self._main_window:
                    self._main_window.after(0, self._start_auto_run_if_needed)

        # 스레드풀에서 실행
        get_thread_pool().submit(check_task)

    def _start_auto_run_if_needed(self) -> None:
        """조건 충족 시 자동 실행 시작"""
        if not self._main_window:
            return
        plan_paths, _repeats, group = get_active_plan_sequence(self._config.player)
        if self._config.ui.window_mode == "play" and self._config.player.auto_run_enabled and plan_paths:
            group_name = group.get("name", "그룹") if group else "그룹"
            logger.info(f"[자동실행] 3초 후 자동실행 그룹 예약: {group_name} ({len(plan_paths)}개 플랜)")
            self._main_window.after(3000, self._auto_run_sequence)
        else:
            logger.info("[자동실행] 자동 실행 조건 미충족")

    def _perform_auto_update(self, new_version: str, release_data: dict) -> None:
        """자동 업데이트 수행 (확인 없이 바로 진행)"""
        from .utils.config import APP_VERSION
        from .utils.update_service import find_zip_asset

        zip_asset = find_zip_asset(release_data)
        if not zip_asset:
            logger.error("다운로드 가능한 zip 파일이 없습니다")
            return

        download_url = zip_asset.get("browser_download_url")
        if not download_url:
            logger.error("다운로드 URL을 찾을 수 없습니다")
            return

        logger.info(f"업데이트 다운로드 시작: v{APP_VERSION} → v{new_version}")

        # 스레드풀에서 다운로드 실행 (매번 스레드 생성 대신)
        get_thread_pool().submit(self._download_update_thread, zip_asset, new_version)

    def _download_update_thread(self, asset: dict, version: str) -> None:
        """업데이트 다운로드 스레드 (자동 업데이트용)"""
        import os
        import sys
        import time
        from .utils.update_service import (
            ssl_fallback_connect, download_file,
            extract_and_find_exe, save_update_config,
            get_update_paths, build_shortcut_icon_refresh_batch,
        )

        # 짧은 초기화 대기
        time.sleep(0.1)

        try:
            download_url = asset.get("browser_download_url")
            file_name = asset.get("name", "update.zip")

            if not download_url:
                logger.error("다운로드 URL이 없습니다")
                return

            if not file_name.endswith(".zip"):
                file_name = f"{file_name}.zip"

            logger.info(f"다운로드 시작: {download_url}")

            paths = get_update_paths()
            temp_path = os.path.join(paths["temp_dir"], file_name)

            # SSL 다중 폴백 방식으로 연결 시도
            try:
                response = ssl_fallback_connect(download_url)
            except ConnectionError as e:
                logger.error(str(e))
                return

            try:
                with response:
                    download_file(response, temp_path)
            except Exception as download_err:
                logger.error(f"다운로드 처리 오류: {download_err}")
                return

            # 개발 모드 체크
            if not getattr(sys, 'frozen', False):
                logger.info(f"개발 모드 - 다운로드 완료: {temp_path}")
                return

            # 압축 해제 및 exe 찾기
            try:
                new_app_dir, new_exe_name = extract_and_find_exe(temp_path, paths["extract_dir"])
            except FileNotFoundError as e:
                logger.error(str(e))
                return

            # 배치 파일 생성
            current_exe = paths["current_exe"]
            app_dir = paths["app_dir"]
            exe_name = paths["exe_name"]
            temp_dir = paths["temp_dir"]
            extract_dir = paths["extract_dir"]
            batch_path = paths["batch_path"]
            data_dir = paths["data_dir"]
            data_backup = paths["data_backup"]
            internal_backup = os.path.join(app_dir, "_internal_old")
            exe_backup = os.path.join(app_dir, f"{exe_name}.old")
            recovery_bat = os.path.join(app_dir, "recovery.bat")
            shortcut_refresh_batch = build_shortcut_icon_refresh_batch(app_dir)

            batch_content = f'''@echo off
chcp 65001 >nul
echo.
echo ========================================
echo   업무지원도구 자동 업데이트 v{version}
echo ========================================
echo.

echo [1/8] 프로그램 강제 종료 중...
taskkill /f /im "{exe_name}" >nul 2>&1
timeout /t 2 /nobreak >nul

echo [2/8] 사용자 데이터 백업 중...
if exist "{data_dir}" (
    xcopy /E /I /Y /Q "{data_dir}" "{data_backup}" >nul 2>&1
)

echo [3/8] 기존 파일 백업 중... (삭제 아님)
if exist "{internal_backup}" rd /s /q "{internal_backup}" 2>nul
if exist "{exe_backup}" del /q "{exe_backup}" 2>nul
if exist "{app_dir}\\_internal" (
    ren "{app_dir}\\_internal" "_internal_old" 2>nul
)
if exist "{current_exe}" (
    ren "{current_exe}" "{exe_name}.old" 2>nul
)

echo [4/8] 새 파일 복사 중...
xcopy /E /I /Y /Q "{new_app_dir}\\*" "{app_dir}\\" >nul 2>&1
if errorlevel 1 (
    echo.
    echo [오류] 파일 복사 실패! 롤백 중...
    echo.

    REM 롤백: 백업된 파일 복원
    if exist "{internal_backup}" (
        if exist "{app_dir}\\_internal" rd /s /q "{app_dir}\\_internal" 2>nul
        ren "{internal_backup}" "_internal" 2>nul
    )
    if exist "{exe_backup}" (
        if exist "{current_exe}" del /q "{current_exe}" 2>nul
        ren "{exe_backup}" "{exe_name}" 2>nul
    )

    echo [복구 완료] 이전 버전으로 복원되었습니다.
    echo 프로그램을 다시 시작합니다...
    timeout /t 3 /nobreak >nul
    cd /d "{app_dir}"
    start "" "{exe_name}"
    exit /b 1
)

echo [5/8] 새 파일 무결성 검사 중...
set "NEW_EXE_NAME={new_exe_name}"
if not exist "{app_dir}\\{new_exe_name}" (
    set "NEW_EXE_NAME="
    for %%F in ("{app_dir}\\*.exe") do set "NEW_EXE_NAME=%%~nxF"
)
if not defined NEW_EXE_NAME (
    echo.
    echo [오류] exe 파일이 없습니다! 롤백 중...
    echo.
    if exist "{internal_backup}" (
        if exist "{app_dir}\\_internal" rd /s /q "{app_dir}\\_internal" 2>nul
        ren "{internal_backup}" "_internal" 2>nul
    )
    if exist "{exe_backup}" (
        ren "{exe_backup}" "{exe_name}" 2>nul
    )
    echo [복구 완료] 이전 버전으로 복원되었습니다.
    timeout /t 3 /nobreak >nul
    cd /d "{app_dir}"
    start "" "{exe_name}"
    exit /b 1
)
if not exist "{app_dir}\\_internal" (
    echo.
    echo [오류] _internal 폴더가 없습니다! 롤백 중...
    echo.
    if exist "{internal_backup}" (
        ren "{internal_backup}" "_internal" 2>nul
    )
    if exist "{exe_backup}" (
        if exist "{current_exe}" del /q "{current_exe}" 2>nul
        ren "{exe_backup}" "{exe_name}" 2>nul
    )
    echo [복구 완료] 이전 버전으로 복원되었습니다.
    timeout /t 3 /nobreak >nul
    cd /d "{app_dir}"
    start "" "{exe_name}"
    exit /b 1
)

echo [6/8] 설정 파일 복원 중...
if exist "{data_backup}\\업무지원도구.db" (
    copy /y "{data_backup}\\업무지원도구.db" "{app_dir}\\_internal\\data\\업무지원도구.db" >nul 2>&1
)
if exist "{data_backup}\\작업도우미.db" (
    copy /y "{data_backup}\\작업도우미.db" "{app_dir}\\_internal\\data\\작업도우미.db" >nul 2>&1
)
if exist "{data_backup}\\wincro.db" (
    copy /y "{data_backup}\\wincro.db" "{app_dir}\\_internal\\data\\wincro.db" >nul 2>&1
)
if exist "{data_backup}\\window_positions.json" (
    copy /y "{data_backup}\\window_positions.json" "{app_dir}\\_internal\\data\\window_positions.json" >nul 2>&1
)
if exist "{data_backup}\\.keyfile" (
    copy /y "{data_backup}\\.keyfile" "{app_dir}\\_internal\\data\\.keyfile" >nul 2>&1
)
if exist "{data_backup}\\config.json" (
    copy /y "{data_backup}\\config.json" "{app_dir}\\_internal\\data\\config.json" >nul 2>&1
)

echo [7/8] 사용자 데이터 병합 중...
if exist "{data_backup}\\recordings" (
    xcopy /E /I /Y /Q "{data_backup}\\recordings\\*" "{app_dir}\\_internal\\data\\recordings\\" >nul 2>&1
)
if exist "{data_backup}\\sequences" (
    xcopy /E /I /Y /Q "{data_backup}\\sequences\\*" "{app_dir}\\_internal\\data\\sequences\\" >nul 2>&1
)
if exist "{data_backup}\\templates" (
    xcopy /E /I /Y /Q "{data_backup}\\templates\\*" "{app_dir}\\_internal\\data\\templates\\" >nul 2>&1
)
if exist "{data_backup}\\triggers" (
    xcopy /E /I /Y /Q "{data_backup}\\triggers\\*" "{app_dir}\\_internal\\data\\triggers\\" >nul 2>&1
)
{shortcut_refresh_batch}
echo [8/8] 정리 중...
rd /s /q "{data_backup}" 2>nul
rd /s /q "{internal_backup}" 2>nul
if exist "{exe_backup}" del /q "{exe_backup}" 2>nul

echo.
echo ========================================
echo   업데이트 완료! 재시작 중...
echo ========================================
timeout /t 2 /nobreak >nul

REM 프로그램 재시작 (작업 디렉토리 변경 후 실행)
cd /d "{app_dir}"
if defined NEW_EXE_NAME (
    start "" "%NEW_EXE_NAME%"
) else (
    echo [오류] exe 파일을 찾을 수 없습니다: %NEW_EXE_NAME%
    pause
)

rd /s /q "{extract_dir}" 2>nul
del /q "{temp_path}" 2>nul
del "%~f0"
'''

            # 복구 배치 파일 생성 (앱이 깨졌을 때 수동 복구용)
            recovery_content = f'''@echo off
chcp 65001 >nul
echo.
echo ========================================
echo   업무지원도구 복구 도구
echo ========================================
echo.

set "APP_DIR={app_dir}"
set "EXE_NAME={exe_name}"

REM 백업된 _internal 폴더 복원
if exist "%APP_DIR%\\_internal_old" (
    echo [발견] 백업된 _internal_old 폴더
    echo 복원 중...
    if exist "%APP_DIR%\\_internal" rd /s /q "%APP_DIR%\\_internal" 2>nul
    ren "%APP_DIR%\\_internal_old" "_internal" 2>nul
    echo _internal 복원 완료!
    echo.
)

REM 백업된 exe 파일 복원
if exist "%APP_DIR%\\%EXE_NAME%.old" (
    echo [발견] 백업된 %EXE_NAME%.old 파일
    if exist "%APP_DIR%\\%EXE_NAME%" del /q "%APP_DIR%\\%EXE_NAME%" 2>nul
    ren "%APP_DIR%\\%EXE_NAME%.old" "%EXE_NAME%" 2>nul
    echo exe 복원 완료!
    echo.
)

REM 복원 가능한 백업이 없는 경우
if not exist "%APP_DIR%\\_internal" (
    if not exist "%APP_DIR%\\_internal_old" (
        echo [경고] 복원할 백업 파일이 없습니다.
        echo.
        echo 수동 복구 방법:
        echo 1. GitHub에서 최신 버전 다운로드
        echo    https://github.com/narshaboss/wincro/releases
        echo 2. 압축 해제 후 이 폴더에 덮어쓰기
        echo.
        pause
        exit /b 1
    )
)

echo.
echo ========================================
echo   복구 완료!
echo ========================================
echo.
echo 프로그램을 시작하시겠습니까? (Y/N)
set /p choice=
if /i "%choice%"=="Y" (
    cd /d "%APP_DIR%"
    start "" "%EXE_NAME%"
)
'''

            # 복구 배치 파일 저장 (앱 폴더에)
            try:
                with open(recovery_bat, 'w', encoding='utf-8-sig') as f:
                    f.write(recovery_content)
                logger.info(f"복구 스크립트 생성: {recovery_bat}")
            except Exception as e:
                logger.warning(f"복구 스크립트 생성 실패: {e}")

            with open(batch_path, 'w', encoding='utf-8-sig') as f:
                f.write(batch_content)

            # 설정 저장
            save_update_config(version)

            # 배치 파일 실행 및 종료
            self._main_window.after(0, lambda: self._start_auto_update(batch_path))

        except Exception as e:
            logger.error(f"자동 업데이트 오류: {e}", exc_info=True)

    def _start_auto_update(self, batch_path: str) -> None:
        """배치 파일 실행 후 자동 종료"""
        import subprocess
        import os

        if not os.path.exists(batch_path):
            logger.error("업데이트 스크립트를 찾을 수 없습니다")
            return

        logger.info(f"자동 업데이트 적용 중... 프로그램이 재시작됩니다.")

        # 실행 중인 작업 먼저 중지 (프리징 방지)
        try:
            if hasattr(self._main_window, '_rule_executor') and self._main_window._rule_executor:
                self._main_window._rule_executor.stop()
                self._main_window._is_running = False
        except Exception as e:
            logger.warning(f"실행 중 작업 중지 실패: {e}")

        # 키보드 리스너 정리
        try:
            if hasattr(self._main_window, '_keyboard_listener') and self._main_window._keyboard_listener:
                self._main_window._keyboard_listener.stop()
                self._main_window._keyboard_listener = None
        except (OSError, RuntimeError):
            pass

        try:
            subprocess.Popen(
                ['cmd', '/c', 'start', 'cmd', '/c', batch_path],
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            )
        except Exception as e:
            logger.error(f"배치 파일 실행 실패: {e}")
            return

        # 데이터베이스 정리 (DatabaseManager는 컨텍스트 매니저 기반이므로 별도 close 불필요)
        try:
            save_config()
        except Exception:
            pass

        try:
            self._main_window.destroy()
        except Exception:
            pass

        # 스레드풀 정리
        _shutdown_thread_pool()

        # os._exit으로 즉시 종료 (sys.exit는 스레드 정리 중 블로킹 가능)
        import os
        os._exit(0)

    def _discard_legacy_plan_backup(self) -> None:
        """Remove old update leftovers so packaged playlists stay authoritative."""
        import shutil
        from .utils.config import DATA_DIR

        backup_dir = DATA_DIR / "plans_user_backup"

        if not backup_dir.exists():
            return

        try:
            shutil.rmtree(backup_dir)
            logger.info("Legacy plan backup discarded; packaged playlists remain active")
        except Exception as e:
            logger.error(f"Failed to discard legacy plan backup: {e}")


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
