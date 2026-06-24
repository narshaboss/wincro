"""
WinCro 로깅 모듈

애플리케이션 전체의 로깅 설정을 관리합니다.
파일 로깅과 콘솔 로깅을 모두 지원하며,
로그 파일은 날짜별로 자동 분리됩니다.
"""

import logging
import sys
import queue
import threading
import atexit
from datetime import datetime
from pathlib import Path
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler, QueueHandler, QueueListener
from typing import Optional

from .app_identity import PRIMARY_PACKAGE_DIR_NAME


# 로그 디렉토리
PROJECT_ROOT = Path(__file__).parent.parent.parent
LOGS_DIR = PROJECT_ROOT / "logs"

# 로그 포맷
DEFAULT_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DETAILED_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(filename)s:%(lineno)d | %(message)s"
SIMPLE_FORMAT = "%(levelname)s: %(message)s"

# 로그 레벨 매핑
LOG_LEVELS = {
    'DEBUG': logging.DEBUG,
    'INFO': logging.INFO,
    'WARNING': logging.WARNING,
    'ERROR': logging.ERROR,
    'CRITICAL': logging.CRITICAL
}


class ColoredFormatter(logging.Formatter):
    """
    콘솔 출력용 컬러 포매터

    로그 레벨에 따라 다른 색상으로 출력합니다.
    """

    # ANSI 색상 코드
    COLORS = {
        'DEBUG': '\033[36m',     # 청록색
        'INFO': '\033[32m',      # 녹색
        'WARNING': '\033[33m',   # 노란색
        'ERROR': '\033[31m',     # 빨간색
        'CRITICAL': '\033[35m',  # 자주색
    }
    RESET = '\033[0m'

    def format(self, record: logging.LogRecord) -> str:
        """로그 레코드를 색상이 적용된 문자열로 포맷"""
        color = self.COLORS.get(record.levelname, self.RESET)
        record.levelname = f"{color}{record.levelname}{self.RESET}"
        return super().format(record)


class DropOldestLogQueue(queue.Queue):
    """Bounded log queue that keeps the process alive during log bursts."""

    def __init__(self, maxsize: int):
        super().__init__(maxsize=max(1, int(maxsize)))
        self.dropped_count = 0

    def put(self, item, block=True, timeout=None):
        while True:
            try:
                return super().put(item, block=False)
            except queue.Full:
                try:
                    self.get_nowait()
                    self.task_done()
                    self.dropped_count += 1
                except queue.Empty:
                    continue


class LoggerManager:
    """
    로거 관리자 클래스

    애플리케이션 전체의 로깅을 설정하고 관리합니다.
    """

    _instance: Optional['LoggerManager'] = None
    _initialized: bool = False

    def __new__(cls) -> 'LoggerManager':
        """싱글톤 인스턴스 생성"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """로거 관리자 초기화"""
        if not self._initialized:
            self._ensure_log_directory()
            self._setup_root_logger()
            LoggerManager._initialized = True

    def _ensure_log_directory(self) -> None:
        """로그 디렉토리 생성"""
        LOGS_DIR.mkdir(parents=True, exist_ok=True)

    def _setup_root_logger(self) -> None:
        """루트 로거 설정 - QueueHandler 사용 (멀티스레드 데드락 방지)"""
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.DEBUG)

        # 기존 핸들러 제거
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)

        # 파일 핸들러 (일별 로테이션)
        log_file = LOGS_DIR / f"{PRIMARY_PACKAGE_DIR_NAME}_{datetime.now().strftime('%Y%m%d')}.log"
        file_handler = TimedRotatingFileHandler(
            filename=log_file,
            when='midnight',
            interval=1,
            backupCount=30,
            encoding='utf-8'
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(DETAILED_FORMAT))

        # 콘솔 핸들러
        self._console_handler = logging.StreamHandler(sys.stdout)
        self._console_handler.setLevel(logging.INFO)
        self._console_handler.setFormatter(ColoredFormatter(DEFAULT_FORMAT))

        # QueueHandler + QueueListener 설정 (멀티스레드 안전)
        # 무제한 큐는 특화모드 로그 폭주 시 메모리를 계속 잡아먹으므로 상한을 둔다.
        self._log_queue = DropOldestLogQueue(maxsize=20000)
        queue_handler = QueueHandler(self._log_queue)
        root_logger.addHandler(queue_handler)

        # 실제 핸들러들을 처리하는 리스너 (별도 스레드에서 실행)
        self._queue_listener = QueueListener(
            self._log_queue,
            file_handler,
            self._console_handler,
            respect_handler_level=True
        )
        self._queue_listener.start()

        # 프로그램 종료 시 리스너 정리
        atexit.register(self._queue_listener.stop)

    def get_logger(self, name: str) -> logging.Logger:
        """
        지정된 이름의 로거를 반환합니다.

        Args:
            name: 로거 이름 (보통 모듈명)

        Returns:
            logging.Logger: 설정된 로거 인스턴스
        """
        return logging.getLogger(name)

    def set_level(self, level: str) -> None:
        """
        전역 로그 레벨을 설정합니다.

        Args:
            level: 로그 레벨 (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        """
        log_level = LOG_LEVELS.get(level.upper(), logging.INFO)
        logging.getLogger().setLevel(log_level)

    def set_console_level(self, level: str) -> None:
        """
        콘솔 출력 로그 레벨을 설정합니다.

        Args:
            level: 로그 레벨
        """
        log_level = LOG_LEVELS.get(level.upper(), logging.INFO)
        if hasattr(self, '_console_handler'):
            self._console_handler.setLevel(log_level)

    def add_file_handler(
        self,
        logger_name: str,
        filename: str,
        level: str = 'DEBUG',
        max_bytes: int = 10 * 1024 * 1024,  # 10MB
        backup_count: int = 5
    ) -> None:
        """
        특정 로거에 파일 핸들러를 추가합니다.

        Args:
            logger_name: 로거 이름
            filename: 로그 파일명
            level: 로그 레벨
            max_bytes: 최대 파일 크기
            backup_count: 백업 파일 수
        """
        logger = logging.getLogger(logger_name)
        log_file = LOGS_DIR / filename

        handler = RotatingFileHandler(
            filename=log_file,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding='utf-8'
        )
        handler.setLevel(LOG_LEVELS.get(level.upper(), logging.DEBUG))
        handler.setFormatter(logging.Formatter(DETAILED_FORMAT))
        logger.addHandler(handler)

    def create_execution_logger(self, sequence_name: str) -> logging.Logger:
        """
        시퀀스 실행 전용 로거를 생성합니다.

        각 시퀀스 실행마다 별도의 로그 파일을 생성합니다.

        Args:
            sequence_name: 시퀀스 이름

        Returns:
            logging.Logger: 실행 로거
        """
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        safe_name = "".join(c if c.isalnum() or c in '-_' else '_' for c in sequence_name)
        logger_name = f"execution.{safe_name}_{timestamp}"
        log_filename = f"execution_{safe_name}_{timestamp}.log"

        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.DEBUG)

        # 실행 로그용 파일 핸들러
        log_file = LOGS_DIR / log_filename
        handler = logging.FileHandler(log_file, encoding='utf-8')
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter(DETAILED_FORMAT))
        logger.addHandler(handler)

        return logger


# 전역 로거 관리자 인스턴스
logger_manager = LoggerManager()


def get_logger(name: str) -> logging.Logger:
    """
    로거를 가져오는 헬퍼 함수

    Args:
        name: 로거 이름 (보통 __name__ 사용)

    Returns:
        logging.Logger: 설정된 로거
    """
    return logger_manager.get_logger(name)


def set_log_level(level: str) -> None:
    """전역 로그 레벨 설정 헬퍼 함수"""
    logger_manager.set_level(level)


def apply_performance_config() -> None:
    """성능 설정에 따라 로그 레벨 조정"""
    try:
        from .config import get_config
        config = get_config()
        if not config.performance.debug_logging:
            # DEBUG 로그 비활성화 - INFO 이상만 출력
            logger_manager.set_level('INFO')
            logging.getLogger().info("[성능] DEBUG 로그 비활성화됨")
    except Exception:
        pass  # 설정 로드 실패 시 무시


def create_execution_logger(sequence_name: str) -> logging.Logger:
    """실행 로거 생성 헬퍼 함수"""
    return logger_manager.create_execution_logger(sequence_name)
