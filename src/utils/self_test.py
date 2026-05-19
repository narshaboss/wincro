"""
WinCro 자체 테스트 모듈

프로그램 내에서 실행되는 자체 진단 테스트를 제공합니다.
테스트 결과를 로그로 출력하여 문제점을 파악하고 수정할 수 있게 합니다.
"""

import time
import threading
from pathlib import Path
from typing import Callable, Optional, List, Dict, Any
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime

from .logger import get_logger
from .config import DATA_DIR

logger = get_logger(__name__)


class TestStatus(Enum):
    """테스트 상태"""
    __test__ = False
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class TestResult:
    """테스트 결과"""
    __test__ = False
    name: str
    status: TestStatus
    message: str = ""
    duration_ms: float = 0
    details: Dict[str, Any] = field(default_factory=dict)

    def to_log_string(self) -> str:
        """로그용 문자열 반환"""
        status_icon = {
            TestStatus.PASSED: "✓",
            TestStatus.FAILED: "✗",
            TestStatus.SKIPPED: "○",
            TestStatus.RUNNING: "⋯",
            TestStatus.PENDING: "·",
        }.get(self.status, "?")

        result = f"{status_icon} {self.name}"
        if self.duration_ms > 0:
            result += f" ({self.duration_ms:.0f}ms)"
        if self.message:
            result += f"\n   {self.message}"
        if self.details:
            for key, value in self.details.items():
                result += f"\n   {key}: {value}"
        return result


class SelfTester:
    """
    자체 테스트 실행기

    프로그램의 각 기능을 테스트하고 결과를 보고합니다.
    """

    def __init__(self):
        self._results: List[TestResult] = []
        self._on_progress: Optional[Callable[[str, TestResult], None]] = None
        self._on_complete: Optional[Callable[[List[TestResult]], None]] = None
        self._running = False
        self._cancelled = False

    def set_callbacks(
        self,
        on_progress: Optional[Callable[[str, TestResult], None]] = None,
        on_complete: Optional[Callable[[List[TestResult]], None]] = None,
    ) -> None:
        """콜백 설정"""
        self._on_progress = on_progress
        self._on_complete = on_complete

    def run_all_tests(self, async_mode: bool = True) -> None:
        """모든 테스트 실행"""
        if self._running:
            logger.warning("테스트가 이미 실행 중입니다")
            return

        if async_mode:
            threading.Thread(target=self._run_tests, daemon=True).start()
        else:
            self._run_tests()

    def cancel(self) -> None:
        """테스트 취소"""
        self._cancelled = True

    def _run_tests(self) -> None:
        """테스트 실행 (내부)"""
        self._running = True
        self._cancelled = False
        self._results.clear()

        logger.info("=" * 50)
        logger.info("자체 테스트 시작")
        logger.info("=" * 50)

        test_methods = [
            ("모듈 임포트", self._test_imports),
            ("설정 파일", self._test_config),
            ("데이터 폴더", self._test_data_folders),
            ("데이터베이스", self._test_database),
            ("화면 캡처", self._test_screen_capture),
            ("녹화 시작/중지", self._test_recording),
            ("녹화 중 UI 응답성", self._test_recording_ui_responsiveness),
            ("입력 로거", self._test_input_logger),
            ("이미지 매칭", self._test_image_matching),
            ("마우스 제어", self._test_mouse_control),
            ("키보드 제어", self._test_keyboard_control),
        ]

        for test_name, test_func in test_methods:
            if self._cancelled:
                logger.info("테스트 취소됨")
                break

            self._report_progress(test_name, TestResult(
                name=test_name,
                status=TestStatus.RUNNING,
            ))

            result = self._run_single_test(test_name, test_func)
            self._results.append(result)
            self._report_progress(test_name, result)
            logger.info(result.to_log_string())

        # 결과 요약
        self._log_summary()

        self._running = False
        if self._on_complete:
            self._on_complete(self._results)

    def _run_single_test(self, name: str, func: Callable) -> TestResult:
        """단일 테스트 실행"""
        start_time = time.time()
        try:
            result = func()
            duration = (time.time() - start_time) * 1000

            if isinstance(result, TestResult):
                result.duration_ms = duration
                return result
            elif result is True:
                return TestResult(
                    name=name,
                    status=TestStatus.PASSED,
                    duration_ms=duration,
                )
            elif result is False:
                return TestResult(
                    name=name,
                    status=TestStatus.FAILED,
                    duration_ms=duration,
                )
            else:
                return TestResult(
                    name=name,
                    status=TestStatus.PASSED,
                    message=str(result) if result else "",
                    duration_ms=duration,
                )
        except Exception as e:
            duration = (time.time() - start_time) * 1000
            return TestResult(
                name=name,
                status=TestStatus.FAILED,
                message=f"예외 발생: {type(e).__name__}: {str(e)}",
                duration_ms=duration,
            )

    def _report_progress(self, test_name: str, result: TestResult) -> None:
        """진행 상황 보고"""
        if self._on_progress:
            try:
                self._on_progress(test_name, result)
            except Exception as e:
                logger.warning(f"진행 콜백 오류: {e}")

    def _log_summary(self) -> None:
        """결과 요약 로그"""
        passed = sum(1 for r in self._results if r.status == TestStatus.PASSED)
        failed = sum(1 for r in self._results if r.status == TestStatus.FAILED)
        skipped = sum(1 for r in self._results if r.status == TestStatus.SKIPPED)
        total = len(self._results)

        logger.info("=" * 50)
        logger.info(f"테스트 완료: {passed}/{total} 성공, {failed} 실패, {skipped} 스킵")

        if failed > 0:
            logger.info("")
            logger.info("실패한 테스트:")
            for r in self._results:
                if r.status == TestStatus.FAILED:
                    logger.info(f"  - {r.name}: {r.message}")

        logger.info("=" * 50)

    # ========== 개별 테스트 메서드 ==========

    def _test_imports(self) -> TestResult:
        """필수 모듈 임포트 테스트"""
        failed_imports = []

        modules = [
            ("customtkinter", "UI 프레임워크"),
            ("cv2", "OpenCV (영상 처리)"),
            ("numpy", "NumPy (수치 연산)"),
            ("mss", "화면 캡처"),
            ("pynput", "입력 제어"),
            ("pyautogui", "마우스/키보드 자동화"),
        ]

        for module_name, description in modules:
            try:
                __import__(module_name)
            except ImportError as e:
                failed_imports.append(f"{module_name} ({description}): {e}")

        if failed_imports:
            return TestResult(
                name="모듈 임포트",
                status=TestStatus.FAILED,
                message=f"{len(failed_imports)}개 모듈 임포트 실패",
                details={"실패 목록": "\n".join(failed_imports)},
            )

        return TestResult(
            name="모듈 임포트",
            status=TestStatus.PASSED,
            message=f"{len(modules)}개 모듈 정상",
        )

    def _test_config(self) -> TestResult:
        """설정 파일 테스트"""
        from .config import get_config, CONFIG_FILE

        try:
            config = get_config()

            # 필수 설정 확인
            checks = {
                "recording.fps": config.recording.fps,
                "player.speed_multiplier": config.player.speed_multiplier,
                "ui.theme": config.ui.theme,
            }

            return TestResult(
                name="설정 파일",
                status=TestStatus.PASSED,
                message=f"설정 파일 위치: {CONFIG_FILE}",
                details=checks,
            )
        except Exception as e:
            return TestResult(
                name="설정 파일",
                status=TestStatus.FAILED,
                message=str(e),
            )

    def _test_data_folders(self) -> TestResult:
        """데이터 폴더 테스트"""
        folders = [
            DATA_DIR / "recordings",
            DATA_DIR / "templates",
            DATA_DIR / "logs",
        ]

        results = {}
        for folder in folders:
            try:
                folder.mkdir(parents=True, exist_ok=True)
                # 쓰기 테스트
                test_file = folder / ".write_test"
                test_file.write_text("test")
                test_file.unlink()
                results[str(folder.name)] = "OK (읽기/쓰기 가능)"
            except Exception as e:
                results[str(folder.name)] = f"실패: {e}"

        failed = [k for k, v in results.items() if "실패" in v]

        if failed:
            return TestResult(
                name="데이터 폴더",
                status=TestStatus.FAILED,
                message=f"{len(failed)}개 폴더 문제",
                details=results,
            )

        return TestResult(
            name="데이터 폴더",
            status=TestStatus.PASSED,
            message=f"{len(folders)}개 폴더 정상",
            details=results,
        )

    def _test_database(self) -> TestResult:
        """데이터베이스 테스트"""
        from ..database import get_db

        try:
            db = get_db()

            # 조회 테스트
            recordings = db.get_all_recordings()

            return TestResult(
                name="데이터베이스",
                status=TestStatus.PASSED,
                message=f"녹화 {len(recordings)}개 조회됨",
            )
        except Exception as e:
            return TestResult(
                name="데이터베이스",
                status=TestStatus.FAILED,
                message=str(e),
            )

    def _test_screen_capture(self) -> TestResult:
        """화면 캡처 테스트"""
        from ..recorder import get_screen_recorder

        try:
            recorder = get_screen_recorder()

            # 스크린샷 캡처
            screenshot = recorder.capture_screenshot()

            if screenshot is None:
                return TestResult(
                    name="화면 캡처",
                    status=TestStatus.FAILED,
                    message="스크린샷 캡처 실패 (None 반환)",
                )

            # 이미지 검증
            height, width = screenshot.shape[:2]
            channels = screenshot.shape[2] if len(screenshot.shape) > 2 else 1

            return TestResult(
                name="화면 캡처",
                status=TestStatus.PASSED,
                message=f"캡처 성공: {width}x{height}, {channels}채널",
                details={
                    "해상도": f"{width}x{height}",
                    "채널": channels,
                    "데이터 타입": str(screenshot.dtype),
                },
            )
        except Exception as e:
            return TestResult(
                name="화면 캡처",
                status=TestStatus.FAILED,
                message=str(e),
            )

    def _test_recording(self) -> TestResult:
        """녹화 기능 테스트"""
        from ..recorder import ScreenRecorder

        try:
            recorder = ScreenRecorder()

            # 녹화 시작
            if not recorder.start(output_name="self_test_recording"):
                return TestResult(
                    name="녹화 시작/중지",
                    status=TestStatus.FAILED,
                    message="녹화 시작 실패",
                )

            # 1초 녹화
            time.sleep(1)

            frame_count = recorder.frame_count

            # 녹화 중지
            output_path = recorder.stop()

            # 결과 검증
            if output_path is None:
                return TestResult(
                    name="녹화 시작/중지",
                    status=TestStatus.FAILED,
                    message="녹화 중지 실패 (출력 파일 없음)",
                )

            # 테스트 파일 삭제
            try:
                Path(output_path).unlink(missing_ok=True)
            except (OSError, PermissionError):
                pass

            return TestResult(
                name="녹화 시작/중지",
                status=TestStatus.PASSED,
                message=f"1초간 {frame_count} 프레임 녹화",
                details={
                    "프레임 수": frame_count,
                    "예상 FPS": frame_count,
                },
            )
        except Exception as e:
            return TestResult(
                name="녹화 시작/중지",
                status=TestStatus.FAILED,
                message=str(e),
            )

    def _test_recording_ui_responsiveness(self) -> TestResult:
        """녹화 중 UI 응답성 테스트 (블로킹 감지)"""
        from ..recorder import ScreenRecorder
        import concurrent.futures

        try:
            recorder = ScreenRecorder()
            blocking_detected = []
            BLOCKING_THRESHOLD_MS = 500  # 500ms 이상이면 블로킹으로 판단

            def measure_operation_time(operation_name: str, func, *args) -> float:
                """작업 실행 시간 측정 (ms)"""
                start = time.time()
                try:
                    func(*args)
                except Exception as e:
                    pass
                return (time.time() - start) * 1000

            # 1. 녹화 시작 시간 측정
            start_time = measure_operation_time(
                "녹화 시작",
                recorder.start,
                None,
                "ui_test_recording"
            )
            if start_time > BLOCKING_THRESHOLD_MS:
                blocking_detected.append(f"녹화 시작: {start_time:.0f}ms (블로킹!)")

            if not recorder.is_recording:
                return TestResult(
                    name="녹화 중 UI 응답성",
                    status=TestStatus.FAILED,
                    message="녹화 시작 실패",
                )

            # 2. 녹화 중 UI 작업 시뮬레이션 (여러 번 측정)
            ui_response_times = []
            for i in range(5):
                # 간단한 작업 (딕셔너리 생성, 문자열 처리 등)
                start = time.time()
                _ = {"test": i, "data": "x" * 100}
                _ = f"녹화 중 테스트 {i}"
                elapsed = (time.time() - start) * 1000
                ui_response_times.append(elapsed)
                time.sleep(0.1)

            avg_ui_time = sum(ui_response_times) / len(ui_response_times)

            # 3. 녹화 중지 시간 측정 (가장 중요!)
            stop_time = measure_operation_time("녹화 중지", recorder.stop)
            if stop_time > BLOCKING_THRESHOLD_MS:
                blocking_detected.append(f"녹화 중지: {stop_time:.0f}ms (블로킹!)")

            # 4. 테스트 파일 삭제
            try:
                import os
                for f in Path(DATA_DIR / "recordings").glob("ui_test_recording*"):
                    f.unlink(missing_ok=True)
            except (OSError, PermissionError):
                pass

            # 결과 판단
            if blocking_detected:
                return TestResult(
                    name="녹화 중 UI 응답성",
                    status=TestStatus.FAILED,
                    message=f"UI 블로킹 감지됨! ({len(blocking_detected)}건)",
                    details={
                        "블로킹 항목": "\n".join(blocking_detected),
                        "녹화 시작": f"{start_time:.0f}ms",
                        "녹화 중지": f"{stop_time:.0f}ms",
                        "임계값": f"{BLOCKING_THRESHOLD_MS}ms",
                    },
                )

            return TestResult(
                name="녹화 중 UI 응답성",
                status=TestStatus.PASSED,
                message=f"블로킹 없음 (시작:{start_time:.0f}ms, 중지:{stop_time:.0f}ms)",
                details={
                    "녹화 시작": f"{start_time:.0f}ms",
                    "녹화 중지": f"{stop_time:.0f}ms",
                    "UI 평균 응답": f"{avg_ui_time:.2f}ms",
                    "임계값": f"{BLOCKING_THRESHOLD_MS}ms",
                },
            )

        except Exception as e:
            return TestResult(
                name="녹화 중 UI 응답성",
                status=TestStatus.FAILED,
                message=str(e),
            )

    def _test_input_logger(self) -> TestResult:
        """입력 로거 테스트"""
        from ..recorder import InputLogger

        try:
            input_logger = InputLogger()

            # 로깅 시작
            if not input_logger.start(output_name="self_test_input"):
                return TestResult(
                    name="입력 로거",
                    status=TestStatus.FAILED,
                    message="입력 로깅 시작 실패",
                )

            # 0.5초 대기
            time.sleep(0.5)

            event_count = input_logger.event_count

            # 로깅 중지
            output_path = input_logger.stop()

            # 테스트 파일 삭제
            if output_path:
                try:
                    Path(output_path).unlink(missing_ok=True)
                except (OSError, PermissionError):
                    pass

            return TestResult(
                name="입력 로거",
                status=TestStatus.PASSED,
                message=f"입력 로깅 정상 ({event_count}개 이벤트)",
            )
        except Exception as e:
            return TestResult(
                name="입력 로거",
                status=TestStatus.FAILED,
                message=str(e),
            )

    def _test_image_matching(self) -> TestResult:
        """이미지 매칭 테스트"""
        try:
            import cv2
            import numpy as np
            from ..recorder import get_screen_recorder

            recorder = get_screen_recorder()
            screenshot = recorder.capture_screenshot()

            if screenshot is None:
                return TestResult(
                    name="이미지 매칭",
                    status=TestStatus.FAILED,
                    message="스크린샷 캡처 실패",
                )

            # 스크린샷의 일부를 템플릿으로 사용 (100x100 영역)
            h, w = screenshot.shape[:2]
            template = screenshot[100:200, 100:200].copy()

            # 템플릿 매칭
            result = cv2.matchTemplate(screenshot, template, cv2.TM_CCOEFF_NORMED)
            min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)

            # 예상 위치 (100, 100)와 비교
            expected_x, expected_y = 100, 100
            actual_x, actual_y = max_loc

            diff = abs(actual_x - expected_x) + abs(actual_y - expected_y)

            if diff <= 5 and max_val > 0.99:
                return TestResult(
                    name="이미지 매칭",
                    status=TestStatus.PASSED,
                    message=f"매칭 정확도: {max_val:.4f}",
                    details={
                        "예상 위치": f"({expected_x}, {expected_y})",
                        "실제 위치": f"({actual_x}, {actual_y})",
                        "신뢰도": f"{max_val:.4f}",
                    },
                )
            else:
                return TestResult(
                    name="이미지 매칭",
                    status=TestStatus.FAILED,
                    message=f"매칭 오차 과다 (diff={diff}, confidence={max_val:.4f})",
                )
        except Exception as e:
            return TestResult(
                name="이미지 매칭",
                status=TestStatus.FAILED,
                message=str(e),
            )

    def _test_mouse_control(self) -> TestResult:
        """마우스 제어 테스트"""
        try:
            import pyautogui

            # 현재 위치 저장
            original_pos = pyautogui.position()

            # 테스트 위치로 이동
            test_x, test_y = 100, 100
            pyautogui.moveTo(test_x, test_y, duration=0.1)
            time.sleep(0.1)

            # 실제 위치 확인
            actual = pyautogui.position()

            # 원래 위치로 복원
            pyautogui.moveTo(original_pos.x, original_pos.y, duration=0.1)

            diff_x = abs(actual.x - test_x)
            diff_y = abs(actual.y - test_y)

            if diff_x <= 5 and diff_y <= 5:
                return TestResult(
                    name="마우스 제어",
                    status=TestStatus.PASSED,
                    message=f"이동 정확도: 오차 x={diff_x}, y={diff_y}",
                )
            else:
                return TestResult(
                    name="마우스 제어",
                    status=TestStatus.FAILED,
                    message=f"이동 오차 과다: 목표({test_x},{test_y}), 실제({actual.x},{actual.y})",
                    details={
                        "목표": f"({test_x}, {test_y})",
                        "실제": f"({actual.x}, {actual.y})",
                        "오차": f"x={diff_x}, y={diff_y}",
                    },
                )
        except Exception as e:
            return TestResult(
                name="마우스 제어",
                status=TestStatus.FAILED,
                message=str(e),
            )

    def _test_keyboard_control(self) -> TestResult:
        """키보드 제어 테스트 (실제 입력 없이 모듈 로드만 확인)"""
        try:
            import pyautogui
            from pynput import keyboard

            # 키보드 리스너 생성 테스트
            listener = keyboard.Listener(on_press=lambda k: None)

            return TestResult(
                name="키보드 제어",
                status=TestStatus.PASSED,
                message="키보드 모듈 정상 로드",
            )
        except Exception as e:
            return TestResult(
                name="키보드 제어",
                status=TestStatus.FAILED,
                message=str(e),
            )

    def get_results_as_text(self) -> str:
        """결과를 텍스트로 반환"""
        lines = [
            "=" * 50,
            f"자체 테스트 결과",
            f"실행 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "=" * 50,
            "",
        ]

        for result in self._results:
            lines.append(result.to_log_string())
            lines.append("")

        # 요약
        passed = sum(1 for r in self._results if r.status == TestStatus.PASSED)
        failed = sum(1 for r in self._results if r.status == TestStatus.FAILED)
        total = len(self._results)

        lines.append("=" * 50)
        lines.append(f"결과: {passed}/{total} 성공, {failed} 실패")
        lines.append("=" * 50)

        return "\n".join(lines)


# 전역 인스턴스
_self_tester: Optional[SelfTester] = None


def get_self_tester() -> SelfTester:
    """자체 테스터 인스턴스 반환"""
    global _self_tester
    if _self_tester is None:
        _self_tester = SelfTester()
    return _self_tester
