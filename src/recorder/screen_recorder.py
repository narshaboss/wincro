"""
WinCro 화면 녹화 모듈

dxcam(DirectX) 우선 사용, 실패 시 mss(GDI)로 폴백합니다.

핵심 원칙:
- dxcam은 캡처 스레드에서만 초기화/사용 (COM 스레드 친화성)
- ThreadPoolExecutor 사용 안함 (데드락 방지)
- 단순한 구조 유지
"""

import time
import threading
from pathlib import Path
from datetime import datetime
from typing import Optional
from dataclasses import dataclass

import mss
import numpy as np
import cv2

from ..utils.logger import get_logger
from ..utils.config import get_config, DATA_DIR

logger = get_logger(__name__)

# comtypes DEBUG 로깅 비활성화 (DirectX COM 객체 Release 로그가 너무 많아 성능 저하 및 스레드 경합 유발)
import logging
logging.getLogger('comtypes').setLevel(logging.WARNING)


@dataclass
class RecordingRegion:
    """녹화 영역 정보"""
    left: int
    top: int
    width: int
    height: int

    def to_dict(self) -> dict:
        return {
            "left": self.left,
            "top": self.top,
            "width": self.width,
            "height": self.height,
        }

    @classmethod
    def full_screen(cls, monitor_index: int = 1) -> 'RecordingRegion':
        """전체 화면 영역 생성"""
        with mss.mss() as sct:
            if monitor_index < 0 or monitor_index >= len(sct.monitors):
                monitor_index = 1 if len(sct.monitors) > 1 else 0
            monitor = sct.monitors[monitor_index]
            return cls(
                left=monitor["left"],
                top=monitor["top"],
                width=monitor["width"],
                height=monitor["height"],
            )


class ScreenRecorder:
    """화면 녹화 클래스"""

    def __init__(self):
        self._config = get_config()
        self._recording_event = threading.Event()  # 스레드 안전한 녹화 상태
        self._paused = False
        self._thread: Optional[threading.Thread] = None
        self._writer: Optional[cv2.VideoWriter] = None
        self._writer_lock = threading.Lock()  # VideoWriter 이중 해제 방지
        self._output_path: Optional[Path] = None
        self._region: Optional[RecordingRegion] = None
        self._frame_count = 0
        self._start_time: Optional[float] = None
        self._pause_start: Optional[float] = None
        self._total_pause_duration = 0.0

        # 녹화 시작 동기화
        self._ready_event = threading.Event()
        self._init_error: Optional[str] = None

        # 캡처 엔진
        self._engine = "mss"  # "dxcam" 또는 "mss"

        # 마지막 캡처 프레임 (녹화 중 스크린샷용)
        self._last_frame: Optional[np.ndarray] = None
        self._frame_lock = threading.Lock()

        # pause/resume 상태 보호용 락
        self._pause_lock = threading.Lock()

    @property
    def is_recording(self) -> bool:
        return self._recording_event.is_set()

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def frame_count(self) -> int:
        return self._frame_count

    @property
    def start_time(self) -> Optional[float]:
        return self._start_time

    @property
    def _recording(self) -> bool:
        """하위 호환용 프로퍼티"""
        return self._recording_event.is_set()

    @_recording.setter
    def _recording(self, value: bool):
        if value:
            self._recording_event.set()
        else:
            self._recording_event.clear()

    @property
    def elapsed_time(self) -> float:
        if self._start_time is None:
            return 0.0
        with self._pause_lock:
            now = time.time()
            elapsed = now - self._start_time - self._total_pause_duration
            if self._paused and self._pause_start:
                elapsed -= (now - self._pause_start)
        return max(0.0, elapsed)

    @property
    def output_path(self) -> Optional[str]:
        return str(self._output_path) if self._output_path else None

    @property
    def capture_engine(self) -> str:
        return self._engine

    def start(
        self,
        region: Optional[RecordingRegion] = None,
        output_name: Optional[str] = None,
    ) -> bool:
        """녹화 시작"""
        if self._recording:
            logger.warning("이미 녹화 중")
            return False

        # 초기화
        self._region = region or RecordingRegion.full_screen()

        if output_name is None:
            output_name = f"recording_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        recordings_dir = DATA_DIR / "recordings"
        recordings_dir.mkdir(parents=True, exist_ok=True)
        self._output_path = recordings_dir / f"{output_name}.mp4"

        self._recording = True
        self._paused = False
        self._frame_count = 0
        self._start_time = None
        self._total_pause_duration = 0.0
        self._pause_start = None
        self._ready_event.clear()
        self._init_error = None
        self._engine = "mss"

        # 녹화 스레드 시작
        self._thread = threading.Thread(target=self._record_loop, daemon=True)
        self._thread.start()

        # 초기화 완료 대기 (최대 10초)
        if not self._ready_event.wait(timeout=10.0):
            self._recording = False
            logger.error("녹화 시작 타임아웃")
            return False

        if self._init_error:
            self._recording = False
            logger.error(f"녹화 시작 실패: {self._init_error}")
            return False

        logger.info(f"녹화 시작: {self._output_path} (엔진: {self._engine})")
        return True

    def stop(self) -> Optional[str]:
        """녹화 중지"""
        if not self._recording:
            return None

        self._recording = False

        # 스레드 종료 대기 (최대 3초)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
            if self._thread.is_alive():
                logger.warning("녹화 스레드가 3초 후에도 응답 없음")

        # 스레드가 완전히 멈출 시간 확보
        time.sleep(0.2)

        output_path = str(self._output_path) if self._output_path and self._output_path.exists() else None

        # VideoWriter 안전하게 정리
        self._release_writer()

        logger.info(f"녹화 완료: {output_path} ({self._frame_count} 프레임)")

        self._thread = None
        self._region = None
        with self._frame_lock:
            self._last_frame = None  # 프레임 버퍼 정리

        return output_path

    def pause(self) -> bool:
        """일시 정지"""
        if not self._recording or self._paused:
            return False
        with self._pause_lock:
            self._paused = True
            self._pause_start = time.time()
        logger.info("녹화 일시 정지")
        return True

    def resume(self) -> bool:
        """재개"""
        if not self._recording or not self._paused:
            return False
        with self._pause_lock:
            if self._pause_start:
                self._total_pause_duration += time.time() - self._pause_start
                self._pause_start = None
            self._paused = False
        logger.info("녹화 재개")
        return True

    def _release_writer(self) -> None:
        """VideoWriter 안전하게 해제 (이중 해제 방지)"""
        with self._writer_lock:
            if self._writer:
                try:
                    self._writer.release()
                except Exception as e:
                    logger.warning(f"VideoWriter 정리 중 오류: {e}")
                finally:
                    self._writer = None

    def _record_loop(self) -> None:
        """녹화 메인 루프 (별도 스레드)"""
        fps = self._config.recording.fps
        frame_interval = 1.0 / fps
        dxcam_camera = None

        try:
            # 1. VideoWriter 초기화
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            self._writer = cv2.VideoWriter(
                str(self._output_path),
                fourcc,
                fps,
                (self._region.width, self._region.height),
            )

            if not self._writer.isOpened():
                self._init_error = "VideoWriter 초기화 실패"
                self._ready_event.set()
                return

            # 2. dxcam 초기화 시도 (현재 스레드에서 직접 - COM 스레드 친화성)
            try:
                import dxcam
                dxcam_camera = dxcam.create(output_color="BGR")
                if dxcam_camera:
                    # 테스트 캡처
                    test = dxcam_camera.grab()
                    if test is None:
                        time.sleep(0.1)
                        test = dxcam_camera.grab()
                    if test is not None:
                        self._engine = "dxcam"
                        logger.info("dxcam 엔진 사용")
                    else:
                        dxcam_camera = None
            except Exception as e:
                logger.debug(f"dxcam 초기화 실패, mss 사용: {e}")
                dxcam_camera = None

            if dxcam_camera is None:
                self._engine = "mss"
                logger.info("mss 엔진 사용")

            # 3. 준비 완료 신호
            self._start_time = time.time()
            self._ready_event.set()

            # 4. UI 업데이트 시간 확보 (메인 스레드가 UI 처리할 시간)
            time.sleep(0.3)

            # 5. 캡처 루프
            if dxcam_camera:
                self._loop_dxcam(dxcam_camera, frame_interval)
            else:
                self._loop_mss(frame_interval)

        except Exception as e:
            logger.error(f"녹화 루프 오류: {e}")
            self._init_error = str(e)
            self._ready_event.set()
        finally:
            # VideoWriter 정리 (예외 발생 시에도 반드시 정리)
            self._release_writer()

            # dxcam 정리 (DirectX 리소스 명시적 해제)
            if dxcam_camera:
                try:
                    import dxcam
                    dxcam.delete(dxcam_camera)
                except Exception:
                    pass

    def _loop_dxcam(self, camera, frame_interval: float) -> None:
        """dxcam 캡처 루프"""
        fail_count = 0

        while self._recording:
            loop_start = time.time()

            if not self._paused:
                try:
                    frame = camera.grab()

                    if frame is not None:
                        fail_count = 0

                        # 영역 크롭
                        h, w = frame.shape[:2]
                        left = max(0, self._region.left)
                        top = max(0, self._region.top)
                        right = min(w, left + self._region.width)
                        bottom = min(h, top + self._region.height)
                        frame = frame[top:bottom, left:right]

                        # 크기 맞추기
                        if frame.shape[1] != self._region.width or frame.shape[0] != self._region.height:
                            frame = cv2.resize(frame, (self._region.width, self._region.height))

                        self._writer.write(frame)
                        self._frame_count += 1

                        # 마지막 프레임 저장 (스크린샷용)
                        with self._frame_lock:
                            self._last_frame = frame.copy()
                    else:
                        fail_count += 1
                        if fail_count >= 10:
                            logger.warning("dxcam 연속 실패, mss로 전환")
                            self._engine = "mss"
                            self._loop_mss(frame_interval)
                            return
                except Exception as e:
                    logger.error(f"dxcam 캡처 오류: {e}")
                    fail_count += 1
                    if fail_count >= 10:
                        self._engine = "mss"
                        self._loop_mss(frame_interval)
                        return

            # 프레임 레이트 유지
            sleep_time = frame_interval - (time.time() - loop_start)
            if sleep_time > 0:
                time.sleep(sleep_time)

    def _loop_mss(self, frame_interval: float) -> None:
        """mss 캡처 루프"""
        with mss.mss() as sct:
            monitor = {
                "left": self._region.left,
                "top": self._region.top,
                "width": self._region.width,
                "height": self._region.height,
            }

            while self._recording:
                loop_start = time.time()

                if not self._paused:
                    try:
                        screenshot = sct.grab(monitor)
                        frame = np.array(screenshot)
                        frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
                        self._writer.write(frame)
                        self._frame_count += 1

                        # 마지막 프레임 저장 (스크린샷용)
                        with self._frame_lock:
                            self._last_frame = frame.copy()
                    except Exception as e:
                        logger.error(f"mss 캡처 오류: {e}")

                sleep_time = frame_interval - (time.time() - loop_start)
                if sleep_time > 0:
                    time.sleep(sleep_time)

    def get_monitors(self) -> list:
        """모니터 목록"""
        with mss.mss() as sct:
            return [
                {
                    "index": i,
                    "left": m["left"],
                    "top": m["top"],
                    "width": m["width"],
                    "height": m["height"],
                }
                for i, m in enumerate(sct.monitors) if i > 0
            ]

    def capture_screenshot(self, region: Optional[RecordingRegion] = None) -> Optional[np.ndarray]:
        """스크린샷 캡처 (mss 사용)"""
        if region is None:
            region = RecordingRegion.full_screen()
        try:
            with mss.mss() as sct:
                monitor = {
                    "left": region.left,
                    "top": region.top,
                    "width": region.width,
                    "height": region.height,
                }
                screenshot = sct.grab(monitor)
                frame = np.array(screenshot)
                return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
        except Exception as e:
            logger.error(f"스크린샷 실패: {e}")
            return None

    def get_current_frame(self) -> Optional[np.ndarray]:
        """
        현재 프레임 반환
        - 녹화 중: 마지막 캡처된 프레임 반환 (dxcam/mss)
        - 녹화 중 아님: mss로 새로 캡처
        """
        if self._recording and self._last_frame is not None:
            with self._frame_lock:
                return self._last_frame.copy()
        return self.capture_screenshot()

    def save_screenshot(
        self,
        path: Optional[str] = None,
        region: Optional[RecordingRegion] = None,
    ) -> Optional[str]:
        """스크린샷 저장"""
        frame = self.capture_screenshot(region)
        if frame is None:
            return None

        if path is None:
            templates_dir = DATA_DIR / "templates"
            templates_dir.mkdir(parents=True, exist_ok=True)
            path = str(templates_dir / f"screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png")

        try:
            cv2.imwrite(path, frame)
            return path
        except Exception as e:
            logger.error(f"스크린샷 저장 실패: {e}")
            return None


# 전역 인스턴스
screen_recorder = ScreenRecorder()

def get_screen_recorder() -> ScreenRecorder:
    return screen_recorder
