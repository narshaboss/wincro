"""PC-local playback options for specially named plan actions."""

from __future__ import annotations

from typing import Any


PUMPKIN_ACTION_NAME = "호박"
LOGIN_COUNT_ACTION_NAME = "로그인횟수"
DEFAULT_LOGIN_ACTION_REPEAT_COUNT = 4
MAX_LOGIN_ACTION_REPEAT_COUNT = 1000


def action_name(action: Any) -> str:
    """Return the user-visible action name used by runtime option matching."""
    return str(getattr(action, "description", "") or "").strip()


def is_pumpkin_action(action: Any) -> bool:
    """Match only the exact action name, never partial names such as 남은호박."""
    return action_name(action) == PUMPKIN_ACTION_NAME


def is_login_count_action(action: Any) -> bool:
    return action_name(action) == LOGIN_COUNT_ACTION_NAME


def should_skip_pumpkin_action(action: Any, player_config: Any) -> bool:
    return is_pumpkin_action(action) and not bool(
        getattr(player_config, "pumpkin_action_enabled", True)
    )


def is_runtime_action_enabled(action: Any, player_config: Any) -> bool:
    """호박 액션은 플랜 활성값 대신 PC 토글을 단일 실행 기준으로 사용한다."""
    if is_pumpkin_action(action):
        return True
    return bool(getattr(action, "enabled", True))


def normalize_login_action_repeat_count(value: Any) -> int:
    try:
        repeat_count = int(value)
    except (TypeError, ValueError):
        repeat_count = DEFAULT_LOGIN_ACTION_REPEAT_COUNT
    return max(1, min(MAX_LOGIN_ACTION_REPEAT_COUNT, repeat_count))


def effective_action_repeat_count(action: Any, player_config: Any) -> int:
    """Return the runtime repeat count without modifying the plan object."""
    if is_login_count_action(action):
        return normalize_login_action_repeat_count(
            getattr(
                player_config,
                "login_action_repeat_count",
                DEFAULT_LOGIN_ACTION_REPEAT_COUNT,
            )
        )

    try:
        repeat_count = int(getattr(action, "repeat_count", 1) or 1)
    except (TypeError, ValueError):
        repeat_count = 1
    return max(1, repeat_count)
