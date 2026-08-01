"""Utility package with compatibility-preserving lazy exports."""

from importlib import import_module
from typing import TYPE_CHECKING


_LAZY_EXPORTS = {
    "ConfigManager": (".config", "ConfigManager"),
    "config_manager": (".config", "config_manager"),
    "get_config": (".config", "get_config"),
    "save_config": (".config", "save_config"),
    "AppConfig": (".config", "AppConfig"),
    "RecordingConfig": (".config", "RecordingConfig"),
    "AnalyzerConfig": (".config", "AnalyzerConfig"),
    "PlayerConfig": (".config", "PlayerConfig"),
    "UIConfig": (".config", "UIConfig"),
    "NotificationConfig": (".config", "NotificationConfig"),
    "SystemConfig": (".config", "SystemConfig"),
    "PROJECT_ROOT": (".config", "PROJECT_ROOT"),
    "DATA_DIR": (".config", "DATA_DIR"),
    "LOGS_DIR": (".config", "LOGS_DIR"),
    "SecurityManager": (".security", "SecurityManager"),
    "security_manager": (".security", "security_manager"),
    "encrypt_api_key": (".security", "encrypt_api_key"),
    "decrypt_api_key": (".security", "decrypt_api_key"),
    "validate_api_key": (".security", "validate_api_key"),
    "LoggerManager": (".logger", "LoggerManager"),
    "logger_manager": (".logger", "logger_manager"),
    "get_logger": (".logger", "get_logger"),
    "set_log_level": (".logger", "set_log_level"),
    "create_execution_logger": (".logger", "create_execution_logger"),
}

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str):
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = target
    value = getattr(import_module(module_name, __name__), attribute_name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))


if TYPE_CHECKING:
    from .config import (
        AnalyzerConfig,
        AppConfig,
        ConfigManager,
        DATA_DIR,
        LOGS_DIR,
        NotificationConfig,
        PlayerConfig,
        PROJECT_ROOT,
        RecordingConfig,
        SystemConfig,
        UIConfig,
        config_manager,
        get_config,
        save_config,
    )
    from .logger import (
        LoggerManager,
        create_execution_logger,
        get_logger,
        logger_manager,
        set_log_level,
    )
    from .security import (
        SecurityManager,
        decrypt_api_key,
        encrypt_api_key,
        security_manager,
        validate_api_key,
    )
