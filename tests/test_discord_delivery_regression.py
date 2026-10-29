"""Exercise detection -> dispatch -> HTTP receipt with no live webhook or HID."""

import ast
import io
import json
import logging
import ssl
import subprocess
import sys
import threading
import types
import urllib.error
import urllib.parse
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.ui import main_window
from src.utils import discord_notifier as notifier
from src.utils.config import AppConfig
from src.utils.notification_delivery import DiscordDeliveryState


URL = "https://discord.com/api/webhooks/123456789012345678/abcdefghijklmnopqrstuvwxyzABCDE"
RECEIPT = {"id": "123456789012345678", "channel_id": "987654321012345678"}


class Response:
    def __init__(self, status=200, body=None):
        self.status = status
        self.body = json.dumps(RECEIPT).encode() if body is None else body

    def read(self, limit):
        return self.body[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def context(monkeypatch, now):
    config = AppConfig()
    config.system.pc_number = "7"
    config.notification.discord_enabled = True
    config.notification.discord_webhook_url = URL
    monkeypatch.setattr(main_window, "get_config", lambda: config)
    monkeypatch.setattr(main_window.time, "monotonic", lambda: now[0])
    ctx = SimpleNamespace(
        _mini_notification_last_sent_at={},
        _mini_notification_delivery_state=DiscordDeliveryState(clock=lambda: now[0]),
        _mini_active_plan=SimpleNamespace(name="test-plan"),
        _is_running=True, _is_paused=False, _mini_stop_requested=False,
        _mini_playback_generation=1,
        _mini_notification_last_progress_at=0.0,
        _mini_notification_last_progress_is_monitoring=False,
    )
    for name in ("_mini_notification_config", "_mini_notification_enabled", "_mini_send_discord_alert", "_mini_is_current_playback_generation"):
        method = getattr(main_window.MainWindow, name)
        setattr(ctx, name, lambda *args, _method=method, **kwargs: _method(ctx, *args, **kwargs))
    return ctx, config


def test_wait_true_preserves_thread_and_requires_server_receipt(monkeypatch):
    requests = []
    monkeypatch.setattr(notifier, "_open_discord_request", lambda req, timeout: requests.append(req) or Response())
    result = notifier.send_discord_webhook(URL + "?thread_id=999&wait=false", {"content": "PC first"})
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(requests[0].full_url).query)
    assert query == {"thread_id": ["999"], "wait": ["true"]}
    assert requests[0].method == "POST"
    assert result.ok and result.message_id == RECEIPT["id"] and result.channel_id == RECEIPT["channel_id"]


@pytest.mark.parametrize("status,body", [(204, b""), (200, b"{}"), (200, b"[]"), (200, b"bad JSON"), (200, b"x" * 16385)])
def test_empty_or_invalid_receipt_cannot_be_reported_as_delivered(monkeypatch, status, body):
    monkeypatch.setattr(notifier, "_open_discord_request", lambda req, timeout: Response(status, body))
    assert not notifier.send_discord_webhook(URL, {"content": "test"}).ok


def test_http_429_preserves_retry_after_and_closes_response(monkeypatch):
    body = io.BytesIO(b'{"retry_after":61.5,"code":20028}')
    def fail(*args, **kwargs):
        raise urllib.error.HTTPError(URL, 429, "Too many requests", {"Retry-After": "60"}, body)
    monkeypatch.setattr(notifier, "_open_discord_request", fail)
    result = notifier.send_discord_webhook(URL, {"content": "test"})
    assert not result.ok and result.status == "http_429" and result.retry_after == 61.5
    assert "20028" in result.detail and body.closed


def test_tls_fallback_retries_only_certificate_errors(monkeypatch):
    contexts = [object(), object()]
    monkeypatch.setattr(notifier, "_verified_ssl_contexts", lambda: list(zip(("first", "second"), contexts)))
    calls = []
    def build(*handlers):
        ctx = handlers[0]._context
        def open_url(request, timeout):
            calls.append(ctx)
            if ctx is contexts[0]:
                raise urllib.error.URLError(ssl.SSLCertVerificationError("missing issuer"))
            return Response()
        return SimpleNamespace(open=open_url)
    monkeypatch.setattr(notifier.urllib.request, "build_opener", build)
    assert notifier.send_discord_webhook(URL, {"content": "test"}).ok
    assert calls == contexts


def test_tls_fallback_does_not_blindly_repeat_a_timed_out_post(monkeypatch):
    monkeypatch.setattr(notifier, "_verified_ssl_contexts", lambda: [("first", object()), ("second", object())])
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise TimeoutError("socket timeout")
    monkeypatch.setattr(notifier.urllib.request, "build_opener", lambda *args: SimpleNamespace(open=fail))
    assert not notifier.send_discord_webhook(URL, {"content": "test"}).ok
    assert len(calls) == 1


def test_webhook_redirect_is_rejected_before_following_it():
    handler = notifier._RejectWebhookRedirects()
    with pytest.raises(urllib.error.HTTPError):
        handler.redirect_request(SimpleNamespace(full_url=URL), None, 302, "redirect", {}, "http://example.invalid")


def test_secret_is_never_copied_into_failure_detail(monkeypatch):
    token = URL.rsplit("/", 1)[-1]
    def fail(*args, **kwargs):
        raise OSError(f"Failed to send to {URL} token={token}")
    monkeypatch.setattr(notifier, "_open_discord_request", fail)
    result = notifier.send_discord_webhook(URL, {"content": "test"})
    assert token not in result.detail and "discord.com" not in result.detail


@pytest.mark.parametrize("status,attempts", [("http_503", 3), ("http_429", 3), ("http_403", 1), ("invalid_webhook", 1)])
def test_worker_retries_are_bounded_and_nonblocking(monkeypatch, status, attempts):
    calls, completed, delays = [], [], []
    def fail(*args, **kwargs):
        calls.append(1)
        return notifier.DiscordSendResult(False, status)
    monkeypatch.setattr(notifier, "send_discord_webhook", fail)
    monkeypatch.setattr(notifier.time, "sleep", delays.append)
    thread = notifier.send_discord_alert_async(URL, notifier.DiscordAlert("test"), on_complete=completed.append)
    thread.join(timeout=2)
    assert not thread.is_alive() and len(calls) == attempts and len(completed) == 1
    assert len(delays) == attempts - 1


def test_long_rate_limit_does_not_leave_a_sleeping_sender_thread(monkeypatch):
    monkeypatch.setattr(notifier, "send_discord_webhook", lambda *a, **k: notifier.DiscordSendResult(False, "http_429", retry_after=120))
    monkeypatch.setattr(notifier.time, "sleep", lambda delay: pytest.fail("Must defer long backoff to gate"))
    completed = []
    thread = notifier.send_discord_alert_async(URL, notifier.DiscordAlert("test"), on_complete=completed.append)
    thread.join(timeout=2)
    assert not thread.is_alive() and completed[0].retry_after == 120


def test_callback_exception_does_not_leak_notifier_thread(monkeypatch):
    monkeypatch.setattr(notifier, "send_discord_webhook", lambda *a, **k: notifier.DiscordSendResult(True, "200"))
    def fail(_result):
        raise RuntimeError("closed window")
    thread = notifier.send_discord_alert_async(URL, notifier.DiscordAlert("test"), on_complete=fail)
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_sender_capacity_prevents_accumulating_hung_network_workers(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(notifier, "_SEND_SLOTS", threading.BoundedSemaphore(1))
    def block(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=3)
        return notifier.DiscordSendResult(True, "200")
    monkeypatch.setattr(notifier, "send_discord_webhook", block)
    first = notifier.send_discord_alert_async(URL, notifier.DiscordAlert("first"))
    assert entered.wait(timeout=2)
    completed = []
    second = notifier.send_discord_alert_async(URL, notifier.DiscordAlert("second"), on_complete=completed.append)
    try:
        second.join(timeout=2)
        assert not second.is_alive() and completed[0].status == "busy"
    finally:
        release.set()
        first.join(timeout=2)
    assert not first.is_alive()


def test_gate_records_success_only_after_receipt_and_retries_failures():
    now = [12406.0]
    state = DiscordDeliveryState(clock=lambda: now[0])
    ticket, reason, _ = state.begin("stuck", URL, 300)
    assert reason == "ready"
    assert state.begin("stuck", URL, 300)[1] == "pending"
    assert state.finish(ticket, notifier.DiscordSendResult(False, "URLError")) == (True, 30.0)
    assert state.begin("stuck", URL, 300)[1] == "retry_backoff"
    now[0] += 30
    ticket, reason, _ = state.begin("stuck", URL, 300)
    assert reason == "ready"
    now[0] += 4
    state.finish(ticket, notifier.DiscordSendResult(True, "200"))
    assert state.begin("stuck", URL, 300)[1] == "cooldown"
    now[0] += 300
    assert state.begin("stuck", URL, 300)[1] == "ready"


def test_destination_change_is_not_suppressed_by_old_webhook_cooldown():
    state = DiscordDeliveryState(clock=lambda: 0.0)
    ticket, _, _ = state.begin("stuck", URL, 300)
    state.finish(ticket, notifier.DiscordSendResult(True, "200"))
    assert state.begin("stuck", URL + "?thread_id=2", 300)[1] == "ready"
    assert URL not in repr(state._entries)


def test_gate_honors_rate_limit_and_rejects_stale_callbacks():
    now = [0.0]
    state = DiscordDeliveryState(clock=lambda: now[0])
    old, _, _ = state.begin("stuck", URL, 300)
    now[0] = 120.0
    new, reason, _ = state.begin("stuck", URL, 300)
    assert reason == "ready" and new.token is not old.token
    assert state.finish(old, notifier.DiscordSendResult(True, "200")) == (False, 0.0)
    assert state.finish(new, notifier.DiscordSendResult(False, "http_429", retry_after=600)) == (True, 600)
    now[0] += 599
    assert state.begin("stuck", URL, 300)[1] == "retry_backoff"


def test_gate_is_thread_safe_and_bounds_state_growth():
    state = DiscordDeliveryState(clock=lambda: 0.0)
    ready = []
    def begin():
        ticket, _, _ = state.begin("stuck", URL, 300)
        if ticket:
            ready.append(ticket)
    threads = [threading.Thread(target=begin) for _ in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)
    assert len(ready) == 1
    for index in range(200):
        state.begin(str(index), URL, 300)
    assert len(state._entries) <= 128


@pytest.mark.parametrize("initial_wait,action", [
    (12406.0, "[6-3-6] 프리미엄 경험치 활성화"),
    (15394.0, "[10] 호동 자동사냥 결과찾기"),
])
def test_actual_watchdog_log_scenario_failure_then_delivery(monkeypatch, caplog, initial_wait, action):
    now = [initial_wait]
    ctx, config = context(monkeypatch, now)
    ctx._mini_record_game_mode_notification_activity = lambda: None
    ctx._mini_build_stuck_diagnostic_fields = lambda elapsed, threshold: (("현재 액션", action),)
    scheduled = []
    ctx._mini_start_notification_watchdog = lambda generation: scheduled.append(generation)
    calls = []
    def send(url, alert, **kwargs):
        calls.append(alert)
        result = (notifier.DiscordSendResult(False, "URLError", "offline") if len(calls) == 1
                  else notifier.DiscordSendResult(True, "200", message_id="123", channel_id="456"))
        kwargs["on_complete"](result)
    monkeypatch.setattr(main_window, "send_discord_alert_async", send)
    with caplog.at_level(logging.INFO):
        for moment in (initial_wait, initial_wait + 30, initial_wait + 60):
            now[0] = float(moment)
            main_window.MainWindow._mini_check_notification_watchdog(ctx, 1)
    assert len(calls) == 2 and len(scheduled) == 3
    assert ctx._mini_notification_last_sent_at["stuck"] == initial_wait + 30
    assert "전송 실패" in caplog.text and "서버 수신 확인" in caplog.text and "reason=cooldown" in caplog.text
    assert calls[-1].pc_number == "7" and ctx._is_running and not ctx._mini_stop_requested


def test_diagnostic_log_is_not_false_proof_of_delivery(monkeypatch, caplog):
    now = [12406.0]
    ctx, config = context(monkeypatch, now)
    config.notification.discord_notify_on_stuck = False
    monkeypatch.setattr(main_window, "send_discord_alert_async", lambda *a, **k: pytest.fail("Disabled"))
    with caplog.at_level(logging.INFO):
        ctx._mini_send_discord_alert("stuck", "test", "test")
    assert "reason=disabled_or_invalid_config" in caplog.text


@pytest.mark.parametrize("initial_wait,action", [
    (12406.0, "[6-3-6] 프리미엄 경험치 활성화"),
    (15394.0, "[10] 호동 자동사냥 결과찾기"),
])
def test_watchdog_to_real_async_http_path_recovers_without_live_discord(monkeypatch, initial_wait, action):
    now = [initial_wait]
    ctx, _ = context(monkeypatch, now)
    ctx._mini_record_game_mode_notification_activity = lambda: None
    ctx._mini_build_stuck_diagnostic_fields = lambda elapsed, threshold: (("현재 액션", action),)
    ctx._mini_start_notification_watchdog = lambda generation: None
    requests, threads = [], []
    def http(request, timeout):
        requests.append(request)
        if len(requests) <= 3:
            raise urllib.error.URLError("temporary network failure")
        return Response()
    def send(*args, **kwargs):
        thread = notifier.send_discord_alert_async(*args, **kwargs)
        threads.append(thread)
        return thread
    monkeypatch.setattr(notifier, "_open_discord_request", http)
    monkeypatch.setattr(notifier.time, "sleep", lambda delay: None)
    monkeypatch.setattr(main_window, "send_discord_alert_async", send)
    for moment in (initial_wait, initial_wait + 30, initial_wait + 60):
        now[0] = float(moment)
        main_window.MainWindow._mini_check_notification_watchdog(ctx, 1)
        for thread in threads:
            thread.join(timeout=2)
            assert not thread.is_alive()
    assert len(requests) == 4 and len(threads) == 2
    assert ctx._mini_notification_last_sent_at["stuck"] == initial_wait + 30
    assert "wait=true" in requests[-1].full_url
    payload = json.loads(requests[-1].data.decode("utf-8"))
    assert payload["content"].splitlines()[0] == "PC번호: 7번"
    assert action in payload["content"] and ctx._is_running


def test_failed_thread_start_releases_pending_gate_and_does_not_stop_playback(monkeypatch, caplog):
    now = [12406.0]
    ctx, _ = context(monkeypatch, now)
    def fail(*args, **kwargs):
        raise RuntimeError("thread creation failed")
    monkeypatch.setattr(main_window, "send_discord_alert_async", fail)
    with caplog.at_level(logging.WARNING):
        ctx._mini_send_discord_alert("stuck", "test", "test")
    assert "dispatch_error" in caplog.text and not ctx._mini_notification_last_sent_at
    assert ctx._mini_notification_delivery_state.begin("stuck", URL, 300)[1] == "retry_backoff"
    assert ctx._is_running


def test_legacy_v304_consumed_cooldown_even_when_post_failed(monkeypatch):
    """Prove the old defect from immutable shipped source, without its GUI."""
    root = Path(__file__).resolve().parents[1]
    old_source = subprocess.check_output(["git", "show", "980a1dcb52953427ed04d79eda0169cde535ccaa:src/ui/main_window.py"], cwd=root, encoding="utf-8")
    cls = next(n for n in ast.parse(old_source).body if isinstance(n, ast.ClassDef) and n.name == "MainWindow")
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_mini_send_discord_alert")
    now = [12406.0]
    ctx, config = context(monkeypatch, now)
    calls = []
    def send(url, alert, **kwargs):
        calls.append(alert)
        kwargs["on_complete"](notifier.DiscordSendResult(False, "URLError", "offline"))
    namespace = {"time": SimpleNamespace(monotonic=lambda: now[0]), "get_config": lambda: config,
                 "DiscordAlert": notifier.DiscordAlert, "send_discord_alert_async": send, "logger": logging.getLogger("legacy-test")}
    exec(compile(ast.Module(body=[method], type_ignores=[]), "legacy_sender", "exec"), namespace)
    old_send = namespace["_mini_send_discord_alert"]
    old_send(ctx, "stuck", "test", "test")
    now[0] += 30
    old_send(ctx, "stuck", "test", "test")
    assert len(calls) == 1 and ctx._mini_notification_last_sent_at["stuck"] == 12406


def test_large_payload_preserves_pc_first_and_existing_notification_policy():
    payload = notifier.build_discord_payload(notifier.DiscordAlert("test", "x" * 9000, "7", tuple((str(i), "@everyone" * 100) for i in range(20))))
    assert len(payload["content"]) <= 1900
    assert payload["content"].splitlines()[0] == "PC번호: 7번"
    assert set(payload) == {"content"}
    assert "flags" not in payload and "allowed_mentions" not in payload


def test_legacy_v304_accepted_204_without_message_confirmation(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    source = subprocess.check_output(["git", "show", "980a1dcb52953427ed04d79eda0169cde535ccaa:src/utils/discord_notifier.py"], cwd=root, encoding="utf-8")
    module = types.ModuleType("legacy_discord_receipt_probe")
    monkeypatch.setitem(sys.modules, module.__name__, module)
    exec(compile(source, "legacy_discord_notifier", "exec"), module.__dict__)
    requests = []
    monkeypatch.setattr(module.urllib.request, "urlopen", lambda req, timeout: requests.append(req) or Response(204, b""))
    result = module.send_discord_webhook(URL, {"content": "test"})
    assert result.ok and "wait=" not in requests[0].full_url
