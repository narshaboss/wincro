"""Discord webhook notification helpers.

The player must never block on a network call, so public send helpers are
non-blocking by default and keep webhook secrets out of logs/messages.
"""

from __future__ import annotations

import json
import logging
import math
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable, Iterable

from .update_service import _verified_ssl_contexts


logger = logging.getLogger(__name__)
_SEND_SLOTS = threading.BoundedSemaphore(4)


DISCORD_WEBHOOK_PREFIXES = (
    "https://discord.com/api/webhooks/",
    "https://discordapp.com/api/webhooks/",
)


@dataclass(frozen=True)
class DiscordAlert:
    title: str
    description: str = ""
    pc_number: str = ""
    fields: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class DiscordSendResult:
    ok: bool
    status: str
    detail: str = ""
    retry_after: float = 0.0
    message_id: str = ""
    channel_id: str = ""


def is_valid_discord_webhook_url(url: str) -> bool:
    value = (url or "").strip()
    return any(value.startswith(prefix) for prefix in DISCORD_WEBHOOK_PREFIXES) and len(value) > 60


def redact_webhook_url(url: str) -> str:
    value = (url or "").strip()
    if not value:
        return ""
    if len(value) <= 16:
        return "***"
    return f"{value[:10]}...{value[-6:]}"


def default_pc_name(pc_number: str = "") -> str:
    number = (pc_number or "").strip()
    if not number:
        return "미지정"
    if number.endswith("번"):
        return number
    return f"{number}번"


def _trim(text: object, limit: int) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    if limit <= 3:
        return value[:max(0, limit)]
    return value[: max(0, limit - 3)] + "..."


def _format_fields(fields: Iterable[tuple[str, str]]) -> list[str]:
    lines: list[str] = []
    for key, value in fields:
        key_text = _trim(key, 40)
        value_text = _trim(value, 500)
        if key_text or value_text:
            lines.append(f"- {key_text}: {value_text}")
    return lines


def build_discord_payload(alert: DiscordAlert) -> dict:
    lines = []
    if alert.pc_number:
        lines.append(f"PC번호: {default_pc_name(alert.pc_number)}")
    lines.append(f"**{_trim(alert.title, 120)}**")
    if alert.description:
        lines.append(_trim(alert.description, 700))
    field_lines = _format_fields(alert.fields)
    if field_lines:
        lines.append("")
        lines.extend(field_lines)

    content = "\n".join(lines)
    return {"content": _trim(content, 1900)}


def _safe_error_detail(error: object, webhook_url: str = "") -> str:
    detail = str(error)
    if webhook_url:
        detail = detail.replace(webhook_url, "[webhook]")
        token = urllib.parse.urlsplit(webhook_url).path.rsplit("/", 1)[-1]
        if token:
            detail = detail.replace(token, "[token]")
    return re.sub(r"https?://\S+", "[url]", detail)[:400]


def _retry_delay(value: object) -> float:
    try:
        seconds = float(value)
        return seconds if math.isfinite(seconds) and seconds > 0 else 0.0
    except (TypeError, ValueError, OverflowError):
        return 0.0


class _RejectWebhookRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "Webhook redirect rejected", headers, fp)


def _open_discord_request(request, timeout):
    """Retry only pre-POST certificate failures, never blindly repeat a POST."""
    deadline = time.monotonic() + timeout
    last_error = None
    for _name, context in _verified_ssl_contexts():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Discord TLS connection timed out")
        opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=context), _RejectWebhookRedirects(),
        )
        try:
            return opener.open(request, timeout=remaining)
        except urllib.error.URLError as exc:
            if not isinstance(exc.reason, ssl.SSLCertVerificationError):
                raise
            last_error = exc
        except ssl.SSLCertVerificationError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise ssl.SSLError("No verified Discord TLS context available")


def send_discord_webhook(webhook_url: str, payload: dict, timeout: float = 5.0) -> DiscordSendResult:
    if not is_valid_discord_webhook_url(webhook_url):
        return DiscordSendResult(False, "invalid_webhook", "Discord 웹훅 URL 형식이 올바르지 않습니다.")

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    parts = urllib.parse.urlsplit(webhook_url.strip())
    query = [(key, value) for key, value in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
             if key != "wait"]
    query.append(("wait", "true"))
    confirmed_url = urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query), fragment=""))
    req = urllib.request.Request(
        confirmed_url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "WinCro-Discord-Notifier",
        },
    )
    try:
        with _open_discord_request(req, timeout=timeout) as response:
            status = getattr(response, "status", 0) or response.getcode()
            if int(status) != 200:
                return DiscordSendResult(False, f"unconfirmed_{status}", "Discord message creation was not confirmed")
            raw = response.read(16385)
            if len(raw) > 16384:
                return DiscordSendResult(False, "unconfirmed", "Discord receipt exceeded size limit")
            receipt = json.loads(raw.decode("utf-8"))
            message_id = str(receipt.get("id", "")) if isinstance(receipt, dict) else ""
            channel_id = str(receipt.get("channel_id", "")) if isinstance(receipt, dict) else ""
            if not message_id.isdigit() or not channel_id.isdigit():
                return DiscordSendResult(False, "unconfirmed", "Discord receipt has no message/channel ID")
            return DiscordSendResult(True, str(status), message_id=message_id, channel_id=channel_id)
    except urllib.error.HTTPError as exc:
        retry_after = 0.0
        code = ""
        try:
            retry_after = _retry_delay((exc.headers or {}).get("Retry-After"))
            error = json.loads(exc.read(4096).decode("utf-8"))
            if isinstance(error, dict):
                retry_after = max(retry_after, _retry_delay(error.get("retry_after")))
                code = str(error.get("code", ""))
        except (OSError, ValueError, TypeError):
            pass
        finally:
            exc.close()
        code_detail = f" code={code}" if code.isdigit() else ""
        return DiscordSendResult(False, f"http_{exc.code}", f"HTTP {exc.code}{code_detail}", retry_after=retry_after)
    except Exception as exc:
        return DiscordSendResult(False, type(exc).__name__, _safe_error_detail(exc, webhook_url.strip()))


def is_retryable_discord_result(result: DiscordSendResult) -> bool:
    if result.ok or result.status == "invalid_webhook":
        return False
    if result.status.startswith("http_"):
        try:
            code = int(result.status[5:])
        except ValueError:
            return False
        return code in (408, 429) or 500 <= code < 600
    return True


def send_discord_alert_async(
    webhook_url: str,
    alert: DiscordAlert,
    timeout: float = 5.0,
    on_complete: Callable[[DiscordSendResult], None] | None = None,
    max_attempts: int = 3,
) -> threading.Thread:
    def _worker() -> None:
        acquired = _SEND_SLOTS.acquire(blocking=False)
        try:
            if not acquired:
                result = DiscordSendResult(False, "busy", "Discord sender capacity is occupied")
            else:
                payload = build_discord_payload(alert)
                attempts = max(1, min(3, max_attempts))
                for attempt in range(attempts):
                    result = send_discord_webhook(webhook_url, payload, timeout=timeout)
                    if result.ok or not is_retryable_discord_result(result) or attempt + 1 == attempts:
                        break
                    delay = max(float(2 ** attempt), result.retry_after)
                    # Long rate limits belong to the delivery gate, not sleeping
                    # notifier threads that accumulate after playback changes.
                    if delay > 30:
                        break
                    logger.warning("[디스코드알림] 전송 재시도: status=%s attempt=%s/%s wait=%.1fs",
                                   result.status, attempt + 1, attempts, delay)
                    time.sleep(delay)
        except Exception as exc:
            result = DiscordSendResult(False, type(exc).__name__, _safe_error_detail(exc, webhook_url))
        finally:
            if acquired:
                _SEND_SLOTS.release()
        if on_complete is not None:
            try:
                on_complete(result)
            except Exception:
                pass

    thread = threading.Thread(target=_worker, name="WinCroDiscordNotifier", daemon=True)
    thread.start()
    return thread
