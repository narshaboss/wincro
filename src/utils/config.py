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
from .plan_sequence_groups import (
    make_plan_sequence_group,
    mirror_active_group_to_legacy,
    normalize_plan_sequence_groups,
)


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
TEMPLATES_DIR = DATA_DIR / "templates"
PACKAGED_NOTIFICATION_DEFAULTS_FILE = DATA_DIR / "notification_defaults.json"

APP_VERSION = "1.0.285"
NOTIFICATION_PROFILE_VERSION = "discord_alerts_stuck180_v2"
AUTO_RUN_PROFILE_VERSION = "auto_hunt_raid_factory_raid5_v9"
# Force only this release to refresh the packaged auto-run playback group.
# When APP_VERSION changes on the next release, this guard stops touching PC-local settings.
AUTO_RUN_PROFILE_FORCE_APP_VERSION = "1.0.232"
AUTO_RUN_PROFILE_GROUP_ID = "packaged_auto_hunt_raid"
AUTO_RUN_PROFILE_GROUP_NAME = "자동사냥+레이드"
AUTO_RUN_PROFILE_GROUP_REPEAT = 4
AUTO_RUN_PROFILE_PLANS = (
    ("plan_20260118_174859.json", 1),
    ("plan_20260605_123819.json", 5),
    ("plan_20260605_140615.json", 1),
)
AUTO_RUN_FACTORY_GROUP_ID = "packaged_wongak_factory"
AUTO_RUN_FACTORY_GROUP_NAME = "원각공장"
AUTO_RUN_FACTORY_GROUP_REPEAT = 1
AUTO_RUN_FACTORY_PLANS = (
    ("plan_20260205_000742.json", 1000),
)
AUTO_RUN_PROFILE_GROUPS = (
    (
        AUTO_RUN_PROFILE_GROUP_ID,
        AUTO_RUN_PROFILE_GROUP_NAME,
        AUTO_RUN_PROFILE_GROUP_REPEAT,
        AUTO_RUN_PROFILE_PLANS,
    ),
    (
        AUTO_RUN_FACTORY_GROUP_ID,
        AUTO_RUN_FACTORY_GROUP_NAME,
        AUTO_RUN_FACTORY_GROUP_REPEAT,
        AUTO_RUN_FACTORY_PLANS,
    ),
)
BRANDING_PROFILE_VERSION = "business_support_tool_v1"
LEGACY_BRAND_NAMES = {
    "",
    "WinCro",
    "dwm",
    "결재 도우미",
    "결제 도우미",
    "결제도우미",
    "작업도우미",
}


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
    plan_sequence_groups: List[dict] = field(default_factory=list)
    active_plan_sequence_group_id: str = ""
    auto_run_profile_version: str = ""
    pumpkin_action_enabled: bool = True
    login_action_repeat_count: int = 4
    image_search_region_a: Optional[List[int]] = None
    image_search_region_b: Optional[List[int]] = None


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
    branding_profile_version: str = ""
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
class NotificationConfig:
    discord_enabled: bool = False
    discord_webhook_url: str = ""
    discord_notify_on_stuck: bool = True
    discord_notify_on_failure: bool = True
    discord_stuck_seconds: int = 180
    discord_cooldown_seconds: int = 300
    discord_profile_version: str = ""


@dataclass
class PerformanceConfig:
    debug_logging: bool = False
    thread_pool_size: int = 4


@dataclass
class SystemConfig:
    pc_number: str = ""
    shutdown_enabled: bool = True
    shutdown_time: str = "00:00"
    shutdown_force: bool = True


@dataclass
class AppConfig:
    recording: RecordingConfig = field(default_factory=RecordingConfig)
    analyzer: AnalyzerConfig = field(default_factory=AnalyzerConfig)
    player: PlayerConfig = field(default_factory=PlayerConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    arduino: ArduinoConfig = field(default_factory=ArduinoConfig)
    update: UpdateConfig = field(default_factory=UpdateConfig)
    notification: NotificationConfig = field(default_factory=NotificationConfig)
    performance: PerformanceConfig = field(default_factory=PerformanceConfig)
    system: SystemConfig = field(default_factory=SystemConfig)
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
                    local_defaults_changed = self._normalize_loaded_local_config(self._config)
                    self._load_status = "loaded"
                    self._load_error = ""
                    if local_defaults_changed:
                        self._persist_loaded_config_sections(self._config, ("player", "notification"))
                except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
                    self._config = AppConfig()
                    self._seed_packaged_defaults(self._config)
                    self._load_status = "error"
                    self._load_error = f"{type(e).__name__}: {e}"
            else:
                self._config = AppConfig()
                self._seed_packaged_defaults(self._config)
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
            "notification": asdict(config.notification),
            "performance": asdict(config.performance),
            "system": asdict(config.system),
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
            notification=NotificationConfig(**filter_known_keys(NotificationConfig, data.get("notification", {}))),
            performance=PerformanceConfig(**filter_known_keys(PerformanceConfig, data.get("performance", {}))),
            system=SystemConfig(**filter_known_keys(SystemConfig, data.get("system", {}))),
            version=data.get("version", "1.0.0"),
            first_run=data.get("first_run", True),
            last_opened=data.get("last_opened", ""),
        )

    def _seed_packaged_defaults(self, config: AppConfig) -> None:
        """Seed defaults only for a fresh or unrecoverable local config."""
        self._apply_packaged_player_defaults(config)
        self._apply_packaged_notification_defaults(config)
        self._apply_packaged_ui_branding(config)

    def _normalize_loaded_local_config(self, config: AppConfig) -> bool:
        """Keep existing PC-local settings intact while normalizing shape only."""
        normalize_plan_sequence_groups(config.player, mutate=True)
        player_changed = self._apply_release_player_profile_once(config)
        if not player_changed:
            mirror_active_group_to_legacy(config.player)
        notification_changed = self._apply_packaged_notification_defaults(config)
        return player_changed or notification_changed

    def _load_packaged_notification_defaults(self) -> dict[str, Any]:
        try:
            if not PACKAGED_NOTIFICATION_DEFAULTS_FILE.exists():
                return {}
            with open(PACKAGED_NOTIFICATION_DEFAULTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return {}

    def _apply_packaged_notification_defaults(self, config: AppConfig) -> bool:
        """Apply packaged Discord defaults without touching PC-local system settings."""
        defaults = self._load_packaged_notification_defaults()
        if not defaults:
            return False

        profile_version = str(defaults.get("profile_version") or NOTIFICATION_PROFILE_VERSION)
        notification = config.notification
        if getattr(notification, "discord_profile_version", "") == profile_version:
            return False

        notification.discord_enabled = bool(defaults.get("discord_enabled", False))
        notification.discord_webhook_url = str(defaults.get("discord_webhook_url", "") or "")
        notification.discord_notify_on_stuck = bool(defaults.get("discord_notify_on_stuck", True))
        notification.discord_notify_on_failure = bool(defaults.get("discord_notify_on_failure", True))
        try:
            notification.discord_stuck_seconds = max(10, int(defaults.get("discord_stuck_seconds", 180) or 180))
        except (TypeError, ValueError):
            notification.discord_stuck_seconds = 180
        try:
            notification.discord_cooldown_seconds = max(10, int(defaults.get("discord_cooldown_seconds", 300) or 300))
        except (TypeError, ValueError):
            notification.discord_cooldown_seconds = 300
        notification.discord_profile_version = profile_version
        return True

    def _apply_release_player_profile_once(self, config: AppConfig) -> bool:
        """Apply this release's playback defaults once without touching other settings."""
        player = config.player
        if getattr(player, "auto_run_profile_version", "") == AUTO_RUN_PROFILE_VERSION:
            return self._repair_packaged_player_group_paths(player)
        if self._should_seed_missing_player_profile(player):
            self._apply_packaged_player_defaults(config)
            return True
        if APP_VERSION != AUTO_RUN_PROFILE_FORCE_APP_VERSION:
            return False
        self._apply_packaged_player_defaults(config)
        return True

    def _should_seed_missing_player_profile(self, player: PlayerConfig) -> bool:
        """Seed packaged playback only when an old config has no playback setup at all."""
        if getattr(player, "auto_run_profile_version", ""):
            return False

        for path in getattr(player, "plan_sequence", []) or []:
            if str(path or "").strip():
                return False

        for group in getattr(player, "plan_sequence_groups", []) or []:
            if not isinstance(group, dict):
                continue
            for entry in group.get("entries", []) or []:
                if isinstance(entry, dict) and str(entry.get("plan_path", "") or "").strip():
                    return False

        return True

    def _apply_packaged_player_defaults(self, config: AppConfig) -> None:
        """Apply release-seeded playback defaults to a fresh config object."""
        player = config.player
        if getattr(player, "auto_run_profile_version", "") == AUTO_RUN_PROFILE_VERSION:
            self._repair_packaged_player_group_paths(player)
            return

        plans_dir = DATA_DIR / "plans"
        packaged_groups = []
        for group_id, group_name, group_repeat, group_plans in AUTO_RUN_PROFILE_GROUPS:
            entries = [
                {
                    "plan_path": str(plans_dir / file_name),
                    "repeat_count": repeat_count,
                }
                for file_name, repeat_count in group_plans
            ]
            packaged_groups.append(
                make_plan_sequence_group(
                    group_name,
                    entries,
                    group_id=group_id,
                    repeat_count=group_repeat,
                )
            )

        player.plan_sequence_groups = packaged_groups
        player.active_plan_sequence_group_id = AUTO_RUN_PROFILE_GROUP_ID
        player.auto_run_enabled = True
        player.auto_run_profile_version = AUTO_RUN_PROFILE_VERSION
        mirror_active_group_to_legacy(player)

    def _repair_packaged_player_group_paths(self, player: PlayerConfig) -> bool:
        """Keep release-managed group paths tied to the current install data dir."""
        groups = normalize_plan_sequence_groups(player, mutate=True)
        expected_by_group = {
            group_id: {
                file_name: repeat_count
                for file_name, repeat_count in group_plans
            }
            for group_id, _group_name, _group_repeat, group_plans in AUTO_RUN_PROFILE_GROUPS
        }
        changed = False
        for group in groups:
            expected = expected_by_group.get(group.get("group_id"))
            if not expected:
                continue
            for entry in group.get("entries", []) or []:
                file_name = Path(str(entry.get("plan_path", ""))).name
                if file_name not in expected:
                    continue
                target_path = str(DATA_DIR / "plans" / file_name)
                if entry.get("plan_path") != target_path:
                    entry["plan_path"] = target_path
                    changed = True
        if changed:
            player.plan_sequence_groups = groups
            mirror_active_group_to_legacy(player)
        return changed

    def _persist_loaded_config_sections(self, config: AppConfig, sections: tuple[str, ...]) -> bool:
        """Persist selected migrated sections, preserving other PC-local settings."""
        try:
            raw_data: dict[str, Any] = {}
            if CONFIG_FILE.exists():
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    raw_data = loaded
            if "player" in sections:
                raw_data["player"] = asdict(config.player)
            if "notification" in sections:
                raw_data["notification"] = asdict(config.notification)
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(raw_data, f, ensure_ascii=False, indent=2)
            return True
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return False

    def _persist_loaded_player_config(self, config: AppConfig) -> bool:
        """Persist only player settings after a release migration, preserving PC-local settings."""
        return self._persist_loaded_config_sections(config, ("player",))

    def _apply_packaged_ui_branding(self, config: AppConfig) -> None:
        """Migrate legacy/random display names to the fixed Korean product brand."""
        ui = config.ui
        if getattr(ui, "branding_profile_version", "") == BRANDING_PROFILE_VERSION:
            return

        current_name = (getattr(ui, "app_name", "") or "").strip()
        should_apply = bool(getattr(ui, "random_name_mode", False)) or current_name in LEGACY_BRAND_NAMES
        if should_apply:
            ui.app_name = PRIMARY_APP_NAME
            ui.random_name_mode = False
            ui.random_name_alias = ""

        ui.branding_profile_version = BRANDING_PROFILE_VERSION

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






