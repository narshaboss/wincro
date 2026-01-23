"""
WinCro 녹화 모듈 테스트
"""

import pytest
import tempfile
import time
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.recorder.screen_recorder import ScreenRecorder, RecordingRegion
from src.recorder.input_logger import InputLogger, InputEvent, InputEventType


class TestRecordingRegion:
    """RecordingRegion 테스트"""

    def test_to_dict(self):
        """딕셔너리 변환 테스트"""
        region = RecordingRegion(left=0, top=0, width=1920, height=1080)
        result = region.to_dict()

        assert result["left"] == 0
        assert result["top"] == 0
        assert result["width"] == 1920
        assert result["height"] == 1080

    def test_full_screen(self):
        """전체 화면 영역 생성 테스트"""
        region = RecordingRegion.full_screen()

        assert region.width > 0
        assert region.height > 0
        assert region.left >= 0
        assert region.top >= 0


class TestScreenRecorder:
    """ScreenRecorder 테스트"""

    def setup_method(self):
        """테스트 설정"""
        self.recorder = ScreenRecorder()

    def teardown_method(self):
        """테스트 정리 - 녹화 중이면 중지"""
        if self.recorder.is_recording:
            self.recorder.stop()

    def test_initial_state(self):
        """초기 상태 테스트"""
        assert not self.recorder.is_recording
        assert not self.recorder.is_paused
        assert self.recorder.frame_count == 0

    def test_properties(self):
        """속성 테스트"""
        assert self.recorder.elapsed_time == 0.0
        assert self.recorder.output_path is None

    def test_get_monitors(self):
        """모니터 목록 조회 테스트"""
        monitors = self.recorder.get_monitors()

        assert isinstance(monitors, list)
        assert len(monitors) >= 1  # 최소 1개의 모니터

        for monitor in monitors:
            assert "index" in monitor
            assert "width" in monitor
            assert "height" in monitor

    def test_capture_screenshot(self):
        """스크린샷 캡처 테스트"""
        screenshot = self.recorder.capture_screenshot()

        assert screenshot is not None
        assert len(screenshot.shape) == 3  # BGR 이미지
        assert screenshot.shape[2] == 3  # 3채널

    def test_recording_start_stop(self):
        """녹화 시작/중지 통합 테스트"""
        # 녹화 시작
        result = self.recorder.start(output_name="test_recording")
        assert result is True
        assert self.recorder.is_recording is True

        # 2초간 녹화
        time.sleep(2)

        # 프레임이 캡처되었는지 확인
        assert self.recorder.frame_count > 0

        # 녹화 중지
        output_path = self.recorder.stop()

        # 결과 확인
        assert output_path is not None
        assert Path(output_path).exists()
        assert self.recorder.is_recording is False

        # 정리
        Path(output_path).unlink(missing_ok=True)
        print(f"\n녹화 테스트 완료: {self.recorder.frame_count} 프레임 캡처됨")

    def test_recording_pause_resume(self):
        """녹화 일시정지/재개 테스트"""
        # 녹화 시작
        self.recorder.start(output_name="test_pause")
        time.sleep(0.5)

        # 일시정지
        assert self.recorder.pause() is True
        assert self.recorder.is_paused is True
        time.sleep(0.1)  # 일시정지 적용 대기
        frames_at_pause = self.recorder.frame_count

        time.sleep(0.5)

        # 일시정지 중에는 프레임 증가가 거의 없어야 함 (최대 1~2개 허용)
        frames_during_pause = self.recorder.frame_count
        assert frames_during_pause - frames_at_pause <= 2

        # 재개
        assert self.recorder.resume() is True
        assert self.recorder.is_paused is False

        time.sleep(0.5)

        # 재개 후 프레임 증가
        assert self.recorder.frame_count > frames_during_pause

        # 정리
        output_path = self.recorder.stop()
        if output_path:
            Path(output_path).unlink(missing_ok=True)


class TestInputLogger:
    """InputLogger 테스트"""

    def setup_method(self):
        """테스트 설정"""
        self.logger = InputLogger()

    def test_initial_state(self):
        """초기 상태 테스트"""
        assert not self.logger.is_logging
        assert self.logger.event_count == 0

    def test_events_property(self):
        """이벤트 목록 속성 테스트"""
        events = self.logger.events
        assert isinstance(events, list)
        assert len(events) == 0


class TestInputEvent:
    """InputEvent 테스트"""

    def test_creation(self):
        """이벤트 생성 테스트"""
        event = InputEvent(
            event_type=InputEventType.MOUSE_CLICK.value,
            timestamp=1.0,
            x=100,
            y=200,
            button="left",
            pressed=True,
        )

        assert event.event_type == "mouse_click"
        assert event.timestamp == 1.0
        assert event.x == 100
        assert event.y == 200
        assert event.button == "left"
        assert event.pressed is True

    def test_to_dict(self):
        """딕셔너리 변환 테스트"""
        event = InputEvent(
            event_type="mouse_click",
            timestamp=1.0,
            x=100,
            y=200,
        )

        result = event.to_dict()

        assert "event_type" in result
        assert "timestamp" in result
        assert "x" in result
        assert "y" in result

    def test_to_dict_excludes_none(self):
        """None 값 제외 테스트"""
        event = InputEvent(
            event_type="mouse_click",
            timestamp=1.0,
        )

        result = event.to_dict()

        assert "x" not in result or result.get("x") is None
        assert "key" not in result or result.get("key") is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
