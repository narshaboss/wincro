"""
WinCro 동작 재현 모듈 테스트
"""

import pytest
from unittest.mock import Mock, patch, MagicMock

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.player.action_player import (
    ActionPlayer,
    PlayerState,
    PlaybackProgress,
    EmergencyStopHandler,
)
from src.database.models import Action, ActionType, Sequence


class TestPlayerState:
    """PlayerState 테스트"""

    def test_all_states_exist(self):
        """모든 상태가 존재하는지 테스트"""
        assert PlayerState.IDLE.value == "idle"
        assert PlayerState.RUNNING.value == "running"
        assert PlayerState.PAUSED.value == "paused"
        assert PlayerState.STOPPED.value == "stopped"
        assert PlayerState.COMPLETED.value == "completed"
        assert PlayerState.FAILED.value == "failed"


class TestPlaybackProgress:
    """PlaybackProgress 테스트"""

    def test_initial_state(self):
        """초기 상태 테스트"""
        progress = PlaybackProgress()

        assert progress.current_step == 0
        assert progress.total_steps == 0
        assert progress.current_action is None
        assert progress.state == PlayerState.IDLE

    def test_progress_percent_zero_total(self):
        """전체가 0일 때 진행률 테스트"""
        progress = PlaybackProgress(current_step=0, total_steps=0)
        assert progress.progress_percent == 0.0

    def test_progress_percent_calculation(self):
        """진행률 계산 테스트"""
        progress = PlaybackProgress(current_step=5, total_steps=10)
        assert progress.progress_percent == 50.0


class TestEmergencyStopHandler:
    """EmergencyStopHandler 테스트"""

    def test_initial_state(self):
        """초기 상태 테스트"""
        handler = EmergencyStopHandler()
        assert not handler.is_triggered

    def test_reset(self):
        """리셋 테스트"""
        handler = EmergencyStopHandler()
        handler._triggered = True
        handler.reset()
        assert not handler.is_triggered


class TestActionPlayer:
    """ActionPlayer 테스트"""

    def test_initial_state(self):
        """초기 상태 테스트 - 전역 인스턴스 사용"""
        from src.player.action_player import action_player
        assert action_player.state == PlayerState.IDLE
        assert not action_player.is_running

    def test_progress_property(self):
        """진행 상태 속성 테스트 - 전역 인스턴스 사용"""
        from src.player.action_player import action_player
        progress = action_player.progress
        assert isinstance(progress, PlaybackProgress)

    def test_play_empty_sequence(self):
        """빈 시퀀스 실행 테스트 - 전역 인스턴스 사용"""
        from src.player.action_player import action_player
        sequence = Sequence(name="빈 시퀀스", actions=[])
        result = action_player.play(sequence)
        assert result is False


class TestAction:
    """Action 테스트"""

    def test_click_action(self):
        """클릭 액션 테스트"""
        action = Action(
            action_type=ActionType.CLICK.value,
            x=100,
            y=200,
            button="left",
        )

        assert action.action_type == "click"
        assert action.x == 100
        assert action.y == 200

    def test_type_action(self):
        """입력 액션 테스트"""
        action = Action(
            action_type=ActionType.TYPE.value,
            text="Hello World",
        )

        assert action.action_type == "type"
        assert action.text == "Hello World"

    def test_to_dict(self):
        """딕셔너리 변환 테스트"""
        action = Action(
            action_type=ActionType.CLICK.value,
            x=100,
            y=200,
        )

        result = action.to_dict()

        assert "action_type" in result
        assert "x" in result
        assert "y" in result

    def test_from_dict(self):
        """딕셔너리에서 생성 테스트"""
        data = {
            "action_type": "click",
            "x": 100,
            "y": 200,
            "button": "left",
        }

        action = Action.from_dict(data)

        assert action.action_type == "click"
        assert action.x == 100
        assert action.y == 200

    def test_str_representation(self):
        """문자열 표현 테스트"""
        action = Action(
            action_type=ActionType.CLICK.value,
            x=100,
            y=200,
        )

        string = str(action)
        assert "100" in string
        assert "200" in string


class TestSequence:
    """Sequence 테스트"""

    def test_empty_sequence(self):
        """빈 시퀀스 테스트"""
        sequence = Sequence(name="테스트")

        assert sequence.action_count == 0
        assert sequence.success_rate == 0.0

    def test_success_rate_calculation(self):
        """성공률 계산 테스트"""
        sequence = Sequence(
            name="테스트",
            run_count=10,
            success_count=8,
        )

        assert sequence.success_rate == 80.0

    def test_to_dict(self):
        """딕셔너리 변환 테스트"""
        sequence = Sequence(
            name="테스트 시퀀스",
            description="설명",
            actions=[],
        )

        result = sequence.to_dict()

        assert "name" in result
        assert "description" in result
        assert "actions" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
