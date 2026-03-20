"""
Application configuration management.

This module keeps local JSON configuration in a single place and exposes
helpers that the rest of the app already imports.
"""

from __future__ import annotations

import json
import threading
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, List, Optional

from .app_identity import PRIMARY_APP_NAME


if getattr(sys, "frozen", False):
    PROJECT_ROOT = Path(sys.executable).parent
    INTERNAL_DIR = PROJECT_ROOT / "_internal"
    DATA_DIR = INTERNAL_DIR / "data"
    LOGS_DIR = INTERNAL_DIR / "logs"
else:
    PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
    DATA_DIR = PROJECT_ROOT / "data"
    LOGS_DIR = PROJECT_ROOT / "logs"

CONFIG_FILE = DATA_DIR / "config.json"

APP_VERSION = "1.0.166"


@dataclass
class RecordingConfig:
    fps: int = 30
    quality: str = "high"
    include_cursor: bool = True
    include_clicks: bool = True
    save_input_log: bool = True
    drag_threshold_distance: int = 25
    drag_threshold_time: float = 0.15


@dataclass
class AnalyzerConfig:
    template_match_threshold: float = 0.8
    ocr_language: str = "kor+eng"
    ocr_confidence_threshold: float = 0.6
    action_merge_threshold_ms: int = 100
    dialog_timeout_seconds: int = 300


@dataclass
class PlayerConfig:
    speed_multiplier: float = 1.0
    default_wait_ms: int = 500
    mouse_move_duration: float = 0.2
    typing_interval: float = 0.05
    retry_count: int = 3
    retry_delay_ms: int = 1000
    emergency_stop_key: str = "escape"
    emergency_stop_count: int = 2
    auto_run_enabled: bool = False
    plan_sequence: List[str] = field(default_factory=list)
    plan_sequence_repeats: List[int] = field(default_factory=list)


@dataclass
class UIConfig:
    theme: str = "dark"
    language: str = "ko"
    window_width: int = 1200
    window_height: int = 800
    window_mode: str = "editor"
    show_tooltips: bool = True
    confirm_before_run: bool = True
    minimize_on_run: bool = True
    show_help_on_startup: bool = False
    run_as_admin: bool = False
    app_name: str = PRIMARY_APP_NAME
    random_name_mode: bool = False
    random_name_alias: str = ""
    auto_start: bool = False


@dataclass
class ArduinoConfig:
    enabled: bool = False
    require_for_playback: bool = True
    strict_mode: bool = True
    com_port: str = ""
    baud_rate: int = 115200
    auto_connect: bool = False


@dataclass
class UpdateConfig:
    github_repo: str = "narshaboss/wincro"
    last_update: str = ""
    last_version: str = ""
    auto_check: bool = False


@dataclass
class PerformanceConfig:
    debug_logging: bool = False
    thread_pool_size: int = 4


@dataclass
class AppConfig:
    recording: RecordingConfig = field(default_factory=RecordingConfig)
    analyzer: AnalyzerConfig = field(default_factory=AnalyzerConfig)
    player: PlayerConfig = field(default_factory=PlayerConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    arduino: ArduinoConfig = field(default_factory=ArduinoConfig)
    update: UpdateConfig = field(default_factory=UpdateConfig)
    performance: PerformanceConfig = field(default_factory=PerformanceConfig)
    version: str = "1.0.0"
    first_run: bool = True
    last_opened: str = ""


class ConfigManager:
    _instance: Optional["ConfigManager"] = None
    _config: Optional[AppConfig] = None
    _lock: threading.RLock = threading.RLock()

    def __new__(cls) -> "ConfigManager":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if not hasattr(self, "_load_status"):
            self._load_status = "unknown"
        if not hasattr(self, "_load_error"):
            self._load_error = ""
        if self._config is None:
            self._ensure_directories()
            self.load()

    def _ensure_directories(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        (DATA_DIR / "recordings").mkdir(exist_ok=True)
        (DATA_DIR / "templates").mkdir(exist_ok=True)
        (DATA_DIR / "sequences").mkdir(exist_ok=True)

    def load(self) -> AppConfig:
        with self._lock:
            if CONFIG_FILE.exists():
                try:
                    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    self._config = self._dict_to_config(data)
                    self._load_status = "loaded"
                    self._load_error = ""
                except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
                    self._config = AppConfig()
                    self._load_status = "error"
                    self._load_error = f"{type(e).__name__}: {e}"
            else:
                self._config = AppConfig()
                self._load_status = "missing"
                self._load_error = ""
            return self._config

    def save(self) -> bool:
        with self._lock:
            if self._config is None:
                return False
            try:
                data = self._config_to_dict(self._config)
                with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                self._load_status = "loaded"
                self._load_error = ""
                return True
            except IOError:
                return False

    def get(self) -> AppConfig:
        with self._lock:
            if self._config is None:
                self.load()
            return self._config

    def reset(self) -> AppConfig:
        with self._lock:
            self._config = AppConfig()
        self.save()
        return self._config

    def update(self, **kwargs: Any) -> bool:
        with self._lock:
            if self._config is None:
                self.load()
            if self._config is None:
                return False

            try:
                for key, value in kwargs.items():
                    if hasattr(self._config, key):
                        setattr(self._config, key, value)
                return self.save()
            except (AttributeError, TypeError, ValueError):
                return False

    def _config_to_dict(self, config: AppConfig) -> dict:
        return {
            "recording": asdict(config.recording),
            "analyzer": asdict(config.analyzer),
            "player": asdict(config.player),
            "ui": asdict(config.ui),
            "arduino": asdict(config.arduino),
            "update": asdict(config.update),
            "performance": asdict(config.performance),
            "version": config.version,
            "first_run": config.first_run,
            "last_opened": config.last_opened,
        }

    def _dict_to_config(self, data: dict) -> AppConfig:
        def filter_known_keys(cls, values: Optional[dict]) -> dict:
            if not values:
                return {}
            known = {field_info.name for field_info in fields(cls)}
            return {key: value for key, value in values.items() if key in known}

        return AppConfig(
            recording=RecordingConfig(**filter_known_keys(RecordingConfig, data.get("recording", {}))),
            analyzer=AnalyzerConfig(**filter_known_keys(AnalyzerConfig, data.get("analyzer", {}))),
            player=PlayerConfig(**filter_known_keys(PlayerConfig, data.get("player", {}))),
            ui=UIConfig(**filter_known_keys(UIConfig, data.get("ui", {}))),
            arduino=ArduinoConfig(**filter_known_keys(ArduinoConfig, data.get("arduino", {}))),
            update=UpdateConfig(**filter_known_keys(UpdateConfig, data.get("update", {}))),
            performance=PerformanceConfig(**filter_known_keys(PerformanceConfig, data.get("performance", {}))),
            version=data.get("version", "1.0.0"),
            first_run=data.get("first_run", True),
            last_opened=data.get("last_opened", ""),
        )

    def get_load_status(self) -> str:
        with self._lock:
            return self._load_status

    def get_load_error(self) -> str:
        with self._lock:
            return self._load_error

    def is_startup_save_safe(self) -> bool:
        with self._lock:
            return self._load_status in ("loaded", "missing")


config_manager = ConfigManager()


def get_config() -> AppConfig:
    return config_manager.get()


def save_config() -> bool:
    return config_manager.save()


def load_config() -> AppConfig:
    return config_manager.load()


def reset_config() -> AppConfig:
    return config_manager.reset()


def get_app_version() -> str:
    return APP_VERSION


def is_first_run() -> bool:
    return get_config().first_run


def mark_first_run_complete() -> bool:
    config = get_config()
    if not config.first_run:
        return True
    config.first_run = False
    return save_config()


def get_config_load_status() -> str:
    return config_manager.get_load_status()


def get_config_load_error() -> str:
    return config_manager.get_load_error()


def is_startup_config_save_safe() -> bool:
    return config_manager.is_startup_save_safe()

