"""Discord webhook notification helpers.

The player must never block on a network call, so public send helpers are
non-blocking by default and keep webhook secrets out of logs/messages.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Iterable


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
    return value[: max(0, limit - 1)] + "..."


def _format_fields(fields: Iterable[tuple[str, str]]) -> list[str]:
    lines: list[str] = []
    for key, value in fields:
        key_text = _trim(key, 40)
        value_text = _trim(value, 500)
        if key_text or value_text:
            lines.append(f"- {key_text}: {value_text}")
    return lines


def build_discord_payload(alert: DiscordAlert) -> dict:
    lines = [f"**{_trim(alert.title, 120)}**"]
    if alert.pc_number:
        lines.append(f"PC번호: {default_pc_name(alert.pc_number)}")
    if alert.description:
        lines.append(_trim(alert.description, 700))
    field_lines = _format_fields(alert.fields)
    if field_lines:
        lines.append("")
        lines.extend(field_lines)

    content = "\n".join(lines)
    return {"content": _trim(content, 1900)}


def send_discord_webhook(webhook_url: str, payload: dict, timeout: float = 5.0) -> DiscordSendResult:
    if not is_valid_discord_webhook_url(webhook_url):
        return DiscordSendResult(False, "invalid_webhook", "Discord 웹훅 URL 형식이 올바르지 않습니다.")

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        webhook_url.strip(),
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "WinCro-Discord-Notifier",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            status = getattr(response, "status", 0) or response.getcode()
            if 200 <= int(status) < 300:
                return DiscordSendResult(True, str(status), "")
            return DiscordSendResult(False, str(status), f"HTTP {status}")
    except urllib.error.HTTPError as exc:
        return DiscordSendResult(False, f"http_{exc.code}", f"HTTP {exc.code}")
    except Exception as exc:
        return DiscordSendResult(False, type(exc).__name__, str(exc))


def send_discord_alert_async(
    webhook_url: str,
    alert: DiscordAlert,
    timeout: float = 5.0,
    on_complete: Callable[[DiscordSendResult], None] | None = None,
) -> threading.Thread:
    def _worker() -> None:
        try:
            result = send_discord_webhook(webhook_url, build_discord_payload(alert), timeout=timeout)
        except Exception as exc:
            result = DiscordSendResult(False, type(exc).__name__, str(exc))
        if on_complete is not None:
            try:
                on_complete(result)
            except Exception:
                pass

    thread = threading.Thread(target=_worker, name="WinCroDiscordNotifier", daemon=True)
    thread.start()
    return thread
