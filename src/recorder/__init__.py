"""
화면 녹화 모듈

- screen_recorder.py: 화면 캡처
- input_logger.py: 입력 이벤트 로깅
"""

from .screen_recorder import (
    ScreenRecorder,
    screen_recorder,
    get_screen_recorder,
    RecordingRegion,
)

from .input_logger import (
    InputLogger,
    input_logger,
    get_input_logger,
    InputEvent,
    InputEventType,
    RecordingSession,
)

__all__ = [
    # Screen Recorder
    "ScreenRecorder",
    "screen_recorder",
    "get_screen_recorder",
    "RecordingRegion",
    # Input Logger
    "InputLogger",
    "input_logger",
    "get_input_logger",
    "InputEvent",
    "InputEventType",
    "RecordingSession",
]
