import json
from types import SimpleNamespace

import pytest

from src.utils import discord_notifier
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
    assert content.splitlines()[:2] == ["PC번호: 03번", "**WinCro 테스트**"]
    assert "WinCro 테스트" in content
    assert "PC번호: 03번" in content
    assert "재생목록" in content
    assert "자동사냥" in content
    assert len(content) <= 1900


def test_default_pc_name_preserves_number():
    assert default_pc_name("07") == "07번"
    assert default_pc_name("7번") == "7번"
    assert default_pc_name("") == "미지정"


@pytest.mark.parametrize("title", ["WinCro 장시간 진행 없음", "WinCro 재생 실패", "WinCro 그룹 실행 완료", "WinCro 테스트"])
@pytest.mark.parametrize("pc,expected", [("03", "03번"), ("7번", "7번")])
def test_all_alert_titles_put_pc_number_before_title(title, pc, expected):
    content = build_discord_payload(DiscordAlert(title=title, pc_number=pc))["content"]
    assert content.splitlines()[:2] == [f"PC번호: {expected}", f"**{title}**"]


def test_async_delivery_sends_pc_first_utf8_json_without_a_real_network_request(monkeypatch):
    requests = []
    class Response:
        status = 200
        def read(self, limit):
            return b'{"id":"123456789012345678","channel_id":"987654321012345678"}'
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
    def fake_open(request, timeout):
        requests.append(json.loads(request.data.decode("utf-8")))
        return Response()
    monkeypatch.setattr(discord_notifier, "_open_discord_request", fake_open)
    completed = []
    thread = discord_notifier.send_discord_alert_async(
        "https://discord.com/api/webhooks/123456789012345678/abcdefghijklmnopqrstuvwxyzABCDE",
        DiscordAlert(title="WinCro 장시간 진행 없음", pc_number="03"), on_complete=completed.append)
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert len(completed) == 1 and completed[0].ok
    assert requests[0]["content"].splitlines()[0] == "PC번호: 03번"


@pytest.mark.parametrize("event_key", ["stuck", "failure", "group_complete"])
@pytest.mark.parametrize("uptime", [0.0, 10.0, 1000.0])
def test_real_main_window_alert_path_preserves_pc_number_and_cooldown(event_key, uptime, monkeypatch):
    from src.ui import main_window
    from src.utils.config import AppConfig
    config = AppConfig()
    config.system.pc_number = "03"
    monkeypatch.setattr(main_window, "get_config", lambda: config)
    monkeypatch.setattr(main_window.time, "monotonic", lambda: uptime)
    context = SimpleNamespace(
        _mini_notification_enabled=lambda _: True,
        _mini_notification_config=lambda: config.notification,
        _mini_notification_last_sent_at={},
        _mini_active_plan=SimpleNamespace(name="test-plan"),
    )
    delivered = []
    def send(url, alert, **kwargs):
        delivered.append(alert)
        kwargs["on_complete"](discord_notifier.DiscordSendResult(True, "200", message_id="123", channel_id="456"))
    monkeypatch.setattr(main_window, "send_discord_alert_async", send)
    for _ in range(2):
        main_window.MainWindow._mini_send_discord_alert(context, event_key, "test-alert", "test")
    assert len(delivered) == 1
    assert build_discord_payload(delivered[0])["content"].splitlines()[0] == "PC번호: 03번"
