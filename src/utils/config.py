"""
WinCro 설정 관리 모듈

애플리케이션 설정을 로드, 저장, 관리하는 기능을 제공합니다.
모든 설정은 로컬 JSON 파일에 저장됩니다.
"""

import json
import os
import threading
from pathlib import Path
from typing import Any, List, Optional
from dataclasses import dataclass, field, asdict, fields


# 프로젝트 루트 디렉토리
# PyInstaller로 빌드된 exe인 경우 _internal 폴더 기준, 아니면 스크립트 위치 기준
import sys
if getattr(sys, 'frozen', False):
    # exe로 실행 중 - _internal 폴더 안에 data/logs가 있음
    PROJECT_ROOT = Path(sys.executable).parent
    INTERNAL_DIR = PROJECT_ROOT / "_internal"
    DATA_DIR = INTERNAL_DIR / "data"
    LOGS_DIR = INTERNAL_DIR / "logs"
else:
    # 스크립트로 실행 중
    PROJECT_ROOT = Path(__file__).parent.parent.parent
    DATA_DIR = PROJECT_ROOT / "data"
    LOGS_DIR = PROJECT_ROOT / "logs"

CONFIG_FILE = DATA_DIR / "config.json"

# 현재 프로그램 버전 (GitHub Release와 비교용)
APP_VERSION = "1.0.116"


@dataclass
class RecordingConfig:
    """녹화 설정"""
    fps: int = 30  # 프레임 레이트
    quality: str = "high"  # low, medium, high
    include_cursor: bool = True  # 마우스 커서 포함
    include_clicks: bool = True  # 클릭 효과 표시
    save_input_log: bool = True  # 입력 로그 저장

    # 드래그 감지 임계값
    drag_threshold_distance: int = 25  # 드래그로 인식할 최소 이동 거리 (픽셀)
    drag_threshold_time: float = 0.15  # 드래그로 인식할 최소 누름 시간 (초)


@dataclass
class AnalyzerConfig:
    """분석 설정"""
    template_match_threshold: float = 0.8  # 템플릿 매칭 임계값
    ocr_language: str = "kor+eng"  # OCR 언어
    ocr_confidence_threshold: float = 0.6  # OCR 신뢰도 임계값
    action_merge_threshold_ms: int = 100  # 액션 병합 시간 임계값
    dialog_timeout_seconds: int = 300  # 분석 대화상자 타임아웃 (초)


@dataclass
class PlayerConfig:
    """재생 설정"""
    speed_multiplier: float = 1.0  # 재생 속도 (0.5 ~ 2.0)
    default_wait_ms: int = 500  # 기본 대기 시간
    mouse_move_duration: float = 0.2  # 마우스 이동 시간
    typing_interval: float = 0.05  # 타이핑 간격 (글자당 50ms)
    retry_count: int = 3  # 실패 시 재시도 횟수
    retry_delay_ms: int = 1000  # 재시도 대기 시간
    emergency_stop_key: str = "escape"  # 긴급 중지 키
    emergency_stop_count: int = 2  # 긴급 중지 키 입력 횟수
    auto_run_enabled: bool = False  # 시작 시 자동 실행 활성화
    plan_sequence: List[str] = field(default_factory=list)  # 플랜 순서 실행 리스트 (파일 경로)
    plan_sequence_repeats: List[int] = field(default_factory=list)  # 플랜별 반복횟수


@dataclass
class UIConfig:
    """UI 설정"""
    theme: str = "dark"  # dark, light
    language: str = "ko"  # ko, en
    window_width: int = 1200
    window_height: int = 800
    window_mode: str = "editor"  # play, editor (창 모드)
    show_tooltips: bool = True
    confirm_before_run: bool = True
    minimize_on_run: bool = True
    show_help_on_startup: bool = False  # 시작 시 사용법 표시
    run_as_admin: bool = False  # 시작 시 관리자 권한으로 실행
    app_name: str = "Desktop"  # 프로그램 이름 (사용자 지정 가능)
    random_name_mode: bool = False  # 랜덤 이름 모드
    auto_start: bool = False  # 윈도우 시작시 자동실행


@dataclass
class ArduinoConfig:
    """아두이노 설정"""
    enabled: bool = False  # 아두이노 사용 여부
    com_port: str = ""  # COM 포트 (예: COM3)
    baud_rate: int = 115200  # 통신 속도
    auto_connect: bool = False  # 시작 시 자동 연결


@dataclass
class UpdateConfig:
    """업데이트 설정"""
    github_repo: str = "narshaboss/wincro"  # GitHub 저장소
    last_update: str = ""  # 마지막 업데이트 시간
    last_version: str = ""  # 마지막 확인 버전
    auto_check: bool = False  # 시작 시 업데이트 확인


@dataclass
class PerformanceConfig:
    """성능 설정"""
    debug_logging: bool = False  # DEBUG 로그 활성화 (비활성화하면 성능 향상)
    thread_pool_size: int = 4  # 백그라운드 작업용 스레드풀 크기


@dataclass
class AppConfig:
    """전체 애플리케이션 설정"""
    recording: RecordingConfig = field(default_factory=RecordingConfig)
    analyzer: AnalyzerConfig = field(default_factory=AnalyzerConfig)
    player: PlayerConfig = field(default_factory=PlayerConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    arduino: ArduinoConfig = field(default_factory=ArduinoConfig)
    update: UpdateConfig = field(default_factory=UpdateConfig)
    performance: PerformanceConfig = field(default_factory=PerformanceConfig)

    # 메타 정보
    version: str = "1.0.0"
    first_run: bool = True
    last_opened: str = ""


class ConfigManager:
    """
    설정 관리자 클래스

    싱글톤 패턴으로 구현되어 애플리케이션 전체에서 하나의 인스턴스만 사용합니다.
    스레드 안전을 위해 Lock을 사용합니다.
    """

    _instance: Optional['ConfigManager'] = None
    _config: Optional[AppConfig] = None
    _lock: threading.RLock = threading.RLock()  # RLock으로 변경 - 중첩 락 허용

    def __new__(cls) -> 'ConfigManager':
        """싱글톤 인스턴스 생성 (스레드 안전)"""
        if cls._instance is None:
            with cls._lock:
                # Double-checked locking
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """설정 관리자 초기화"""
        if self._config is None:
            self._ensure_directories()
            self.load()

    def _ensure_directories(self) -> None:
        """필요한 디렉토리 생성"""
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        (DATA_DIR / "recordings").mkdir(exist_ok=True)
        (DATA_DIR / "templates").mkdir(exist_ok=True)
        (DATA_DIR / "sequences").mkdir(exist_ok=True)

    def load(self) -> AppConfig:
        """
        설정 파일에서 설정을 로드합니다 (스레드 안전).

        Returns:
            AppConfig: 로드된 설정 객체
        """
        with self._lock:
            if CONFIG_FILE.exists():
                try:
                    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    self._config = self._dict_to_config(data)
                except (json.JSONDecodeError, KeyError) as e:
                    # 설정 파일이 손상된 경우 기본값 사용
                    self._config = AppConfig()
            else:
                self._config = AppConfig()

            return self._config

    def save(self) -> bool:
        """
        현재 설정을 파일에 저장합니다 (스레드 안전).

        Returns:
            bool: 저장 성공 여부
        """
        with self._lock:
            if self._config is None:
                return False

            try:
                data = self._config_to_dict(self._config)
                with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                return True
            except IOError:
                return False

    def get(self) -> AppConfig:
        """
        현재 설정을 반환합니다.

        Returns:
            AppConfig: 현재 설정 객체
        """
        with self._lock:
            if self._config is None:
                self.load()
            return self._config

    def reset(self) -> AppConfig:
        """
        설정을 기본값으로 초기화합니다.

        Returns:
            AppConfig: 초기화된 설정 객체
        """
        with self._lock:
            self._config = AppConfig()
        self.save()
        return self._config

    def update(self, **kwargs: Any) -> bool:
        """
        설정의 특정 값을 업데이트합니다 (스레드 안전).

        Args:
            **kwargs: 업데이트할 설정 키-값 쌍

        Returns:
            bool: 업데이트 성공 여부
        """
        with self._lock:
            # 설정이 로드되었는지 확인 (락 내부에서 직접 로드)
            if self._config is None:
                if CONFIG_FILE.exists():
                    try:
                        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                        self._config = self._dict_to_config(data)
                    except (json.JSONDecodeError, KeyError):
                        self._config = AppConfig()
                else:
                    self._config = AppConfig()
            try:
                for key, value in kwargs.items():
                    if hasattr(self._config, key):
                        setattr(self._config, key, value)
                # save()도 락을 사용하므로 내부 로직만 실행
                if self._config is None:
                    return False
                data = self._config_to_dict(self._config)
                with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                return True
            except (AttributeError, TypeError, ValueError, IOError):
                return False

    def _config_to_dict(self, config: AppConfig) -> dict:
        """설정 객체를 딕셔너리로 변환"""
        return {
            'recording': asdict(config.recording),
            'analyzer': asdict(config.analyzer),
            'player': asdict(config.player),
            'ui': asdict(config.ui),
            'arduino': asdict(config.arduino),
            'update': asdict(config.update),
            'performance': asdict(config.performance),
            'version': config.version,
            'first_run': config.first_run,
            'last_opened': config.last_opened
        }

    def _dict_to_config(self, data: dict) -> AppConfig:
        """딕셔너리를 설정 객체로 변환"""
        # 각 설정 클래스의 알려진 필드만 추출 (이전 버전 호환성)
        def filter_known_keys(cls, d: dict) -> dict:
            if not d:
                return {}
            known = {f.name for f in fields(cls)}
            return {k: v for k, v in d.items() if k in known}

        return AppConfig(
            recording=RecordingConfig(**filter_known_keys(RecordingConfig, data.get('recording', {}))),
            analyzer=AnalyzerConfig(**filter_known_keys(AnalyzerConfig, data.get('analyzer', {}))),
            player=PlayerConfig(**filter_known_keys(PlayerConfig, data.get('player', {}))),
            ui=UIConfig(**filter_known_keys(UIConfig, data.get('ui', {}))),
            arduino=ArduinoConfig(**filter_known_keys(ArduinoConfig, data.get('arduino', {}))),
            update=UpdateConfig(**filter_known_keys(UpdateConfig, data.get('update', {}))),
            performance=PerformanceConfig(**filter_known_keys(PerformanceConfig, data.get('performance', {}))),
            version=data.get('version', '1.0.0'),
            first_run=data.get('first_run', True),
            last_opened=data.get('last_opened', '')
        )


# 전역 설정 관리자 인스턴스
config_manager = ConfigManager()


def get_config() -> AppConfig:
    """전역 설정을 가져오는 헬퍼 함수"""
    return config_manager.get()


def save_config() -> bool:
    """전역 설정을 저장하는 헬퍼 함수"""
    return config_manager.save()
