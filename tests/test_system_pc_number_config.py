import json

from src.utils import config as config_module
from src.utils.config import AppConfig, ConfigManager


def test_system_pc_number_roundtrips_without_numeric_cast():
    manager = ConfigManager()
    config = AppConfig()
    config.system.pc_number = "02-A"

    restored = manager._dict_to_config(manager._config_to_dict(config))

    assert restored.system.pc_number == "02-A"


def test_missing_system_pc_number_defaults_to_empty_string():
    manager = ConfigManager()

    restored = manager._dict_to_config({"system": {"shutdown_enabled": False}})

    assert restored.system.pc_number == ""
    assert restored.system.shutdown_enabled is False


def test_notification_config_roundtrips_and_defaults():
    manager = ConfigManager()
    config = AppConfig()
    config.notification.discord_enabled = True
    config.notification.discord_webhook_url = "https://discord.com/api/webhooks/1234567890/token-token-token-token"
    config.notification.discord_stuck_seconds = 45
    config.notification.discord_cooldown_seconds = 90

    restored = manager._dict_to_config(manager._config_to_dict(config))

    assert restored.notification.discord_enabled is True
    assert restored.notification.discord_webhook_url.startswith("https://discord.com/api/webhooks/")
    assert restored.notification.discord_stuck_seconds == 45
    assert restored.notification.discord_cooldown_seconds == 90

    missing = manager._dict_to_config({})
    assert missing.notification.discord_enabled is False
    assert missing.notification.discord_notify_on_stuck is True


def test_packaged_notification_defaults_preserve_pc_number(monkeypatch, tmp_path):
    defaults_path = tmp_path / "notification_defaults.json"
    defaults_path.write_text(
        json.dumps(
            {
                "profile_version": "discord_alerts_test",
                "discord_enabled": True,
                "discord_webhook_url": "https://discord.com/api/webhooks/1234567890/token-token-token-token",
                "discord_notify_on_stuck": True,
                "discord_notify_on_failure": True,
                "discord_stuck_seconds": 33,
                "discord_cooldown_seconds": 77,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(config_module, "PACKAGED_NOTIFICATION_DEFAULTS_FILE", defaults_path)
    manager = ConfigManager()
    config = AppConfig()
    config.system.pc_number = "PC-7"

    assert manager._apply_packaged_notification_defaults(config) is True
    assert config.system.pc_number == "PC-7"
    assert config.notification.discord_enabled is True
    assert config.notification.discord_stuck_seconds == 33
    assert config.notification.discord_cooldown_seconds == 77
    assert config.notification.discord_profile_version == "discord_alerts_test"

    config.notification.discord_enabled = False
    assert manager._apply_packaged_notification_defaults(config) is False
    assert config.notification.discord_enabled is False


def test_persist_loaded_notification_preserves_pc_local_system(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "system": {"pc_number": "11", "shutdown_enabled": False},
                "notification": {"discord_enabled": False},
                "player": {"auto_run_enabled": False},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(config_module, "CONFIG_FILE", config_path)
    manager = ConfigManager()
    config = AppConfig()
    config.system.pc_number = "SHOULD_NOT_OVERWRITE"
    config.notification.discord_enabled = True
    config.notification.discord_webhook_url = "https://discord.com/api/webhooks/1234567890/token-token-token-token"

    assert manager._persist_loaded_config_sections(config, ("notification",)) is True

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["system"]["pc_number"] == "11"
    assert saved["system"]["shutdown_enabled"] is False
    assert saved["notification"]["discord_enabled"] is True
    assert saved["player"]["auto_run_enabled"] is False
