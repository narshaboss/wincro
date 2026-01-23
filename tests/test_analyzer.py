"""
WinCro 분석 모듈 테스트
"""

import pytest
import numpy as np
from pathlib import Path
from unittest.mock import Mock, patch

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analyzer.template_matcher import TemplateMatcher, MatchResult
from src.analyzer.action_extractor import ActionExtractor
from src.analyzer.video_analyzer import VideoAnalyzer, AnalysisProgress, AnalysisResult
from src.database.models import Action, ActionType
from src.recorder.input_logger import InputEvent, InputEventType


class TestMatchResult:
    """MatchResult 테스트"""

    def test_found_result(self):
        """찾은 결과 테스트"""
        result = MatchResult(
            found=True,
            x=100,
            y=200,
            width=50,
            height=50,
            confidence=0.95,
            center_x=125,
            center_y=225,
        )

        assert result.found is True
        assert result.center == (125, 225)
        assert result.region == (100, 200, 50, 50)
        assert result.confidence == 0.95

    def test_not_found_result(self):
        """못 찾은 결과 테스트"""
        result = MatchResult(found=False, confidence=0.3)

        assert result.found is False
        assert result.confidence == 0.3
        assert result.center == (0, 0)


class TestTemplateMatcher:
    """TemplateMatcher 테스트"""

    def setup_method(self):
        """테스트 설정"""
        self.matcher = TemplateMatcher()

    def test_compare_identical_images(self):
        """동일한 이미지 비교 테스트"""
        image = np.zeros((100, 100, 3), dtype=np.uint8)
        image[40:60, 40:60] = 255  # 흰색 사각형

        similarity = self.matcher.compare_images(image, image)

        assert similarity == 1.0

    def test_compare_different_images(self):
        """다른 이미지 비교 테스트"""
        image1 = np.zeros((100, 100, 3), dtype=np.uint8)
        image2 = np.ones((100, 100, 3), dtype=np.uint8) * 255

        similarity = self.matcher.compare_images(image1, image2)

        assert similarity < 0.5

    def test_cache_operations(self):
        """캐시 작업 테스트"""
        self.matcher.clear_cache()
        assert len(self.matcher._cache) == 0


class TestActionExtractor:
    """ActionExtractor 테스트"""

    def setup_method(self):
        """테스트 설정"""
        self.extractor = ActionExtractor()

    def test_extract_empty_events(self):
        """빈 이벤트 목록 처리 테스트"""
        actions = self.extractor.extract_from_events([])
        assert len(actions) == 0

    def test_extract_click_events(self):
        """클릭 이벤트 추출 테스트"""
        events = [
            InputEvent(
                event_type=InputEventType.MOUSE_CLICK.value,
                timestamp=0.0,
                x=100,
                y=200,
                button="left",
                pressed=True,
            ),
            InputEvent(
                event_type=InputEventType.MOUSE_CLICK.value,
                timestamp=0.1,
                x=100,
                y=200,
                button="left",
                pressed=False,
            ),
        ]

        actions = self.extractor.extract_from_events(events)

        assert len(actions) >= 1
        click_actions = [a for a in actions if a.action_type == ActionType.CLICK.value]
        assert len(click_actions) >= 1

    def test_extract_scroll_events(self):
        """스크롤 이벤트 추출 테스트"""
        events = [
            InputEvent(
                event_type=InputEventType.MOUSE_SCROLL.value,
                timestamp=0.0,
                x=100,
                y=200,
                scroll_dy=3,
            ),
        ]

        actions = self.extractor.extract_from_events(events)

        scroll_actions = [a for a in actions if a.action_type == ActionType.SCROLL.value]
        assert len(scroll_actions) >= 1

    def test_optimize_empty_actions(self):
        """빈 액션 목록 최적화 테스트"""
        actions = self.extractor.optimize_actions([])
        assert len(actions) == 0

    def test_optimize_removes_empty_text(self):
        """빈 텍스트 입력 제거 테스트"""
        actions = [
            Action(action_type=ActionType.TYPE.value, text=""),
            Action(action_type=ActionType.TYPE.value, text="Hello"),
        ]

        optimized = self.extractor.optimize_actions(actions)

        text_actions = [a for a in optimized if a.action_type == ActionType.TYPE.value]
        assert all(a.text for a in text_actions)


class TestVideoAnalyzer:
    """VideoAnalyzer 테스트"""

    def setup_method(self):
        """테스트 설정"""
        self.analyzer = VideoAnalyzer()

    def test_initial_state(self):
        """초기 상태 테스트"""
        assert not self.analyzer.is_analyzing

    def test_progress_property(self):
        """진행 상태 속성 테스트"""
        progress = self.analyzer.progress

        assert isinstance(progress, AnalysisProgress)
        assert progress.total_frames == 0
        assert progress.progress_percent == 0.0

    def test_get_video_info_invalid_path(self):
        """잘못된 경로 영상 정보 조회 테스트"""
        info = self.analyzer.get_video_info("nonexistent.mp4")
        assert info == {}


class TestAnalysisProgress:
    """AnalysisProgress 테스트"""

    def test_progress_percent_zero_total(self):
        """전체가 0일 때 진행률 테스트"""
        progress = AnalysisProgress(total_frames=0, processed_frames=0)
        assert progress.progress_percent == 0.0

    def test_progress_percent_calculation(self):
        """진행률 계산 테스트"""
        progress = AnalysisProgress(total_frames=100, processed_frames=50)
        assert progress.progress_percent == 50.0


class TestAnalysisResult:
    """AnalysisResult 테스트"""

    def test_success_result(self):
        """성공 결과 테스트"""
        result = AnalysisResult(
            success=True,
            action_count=10,
            frame_count=1000,
            analysis_time_seconds=5.0,
        )

        assert result.success is True
        assert result.action_count == 10
        assert result.error_message is None

    def test_failure_result(self):
        """실패 결과 테스트"""
        result = AnalysisResult(
            success=False,
            error_message="파일을 찾을 수 없습니다",
        )

        assert result.success is False
        assert result.error_message == "파일을 찾을 수 없습니다"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
