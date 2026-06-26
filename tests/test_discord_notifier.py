from src.utils.discord_notifier import (
    DiscordAlert,
    build_discord_payload,
    default_pc_name,
    is_valid_discord_webhook_url,
    redact_webhook_url,
)


def test_discord_webhook_validation_and_redaction():
    valid = "https://discord.com/api/webhooks/123456789012345678/abcdefghijklmnopqrstuvwxyzABCDE"

    assert is_valid_discord_webhook_url(valid)
    assert not is_valid_discord_webhook_url("https://example.com/webhook")
    assert "abcdefghijklmnopqrstuvwxyzABCDE" not in redact_webhook_url(valid)


def test_discord_payload_includes_pc_number_and_fields():
    payload = build_discord_payload(
        DiscordAlert(
            title="WinCro 테스트",
            description="실패 알림",
            pc_number="03",
            fields=(("재생목록", "자동사냥"), ("사유", "멈춤")),
        )
    )

    content = payload["content"]
    assert "WinCro 테스트" in content
    assert "PC번호: 03번" in content
    assert "재생목록" in content
    assert "자동사냥" in content
    assert len(content) <= 1900


def test_default_pc_name_preserves_number():
    assert default_pc_name("07") == "07번"
    assert default_pc_name("7번") == "7번"
    assert default_pc_name("") == "미지정"
