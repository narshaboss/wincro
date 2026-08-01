"""Playback package with compatibility-preserving lazy exports.

Importing one engine must not construct the unrelated ActionPlayer singleton.
"""

from importlib import import_module
from typing import TYPE_CHECKING


_LAZY_EXPORTS = {
    "ActionPlayer": (".action_player", "ActionPlayer"),
    "action_player": (".action_player", "action_player"),
    "get_action_player": (".action_player", "get_action_player"),
    "PlayerState": (".action_player", "PlayerState"),
    "PlaybackProgress": (".action_player", "PlaybackProgress"),
    "EmergencyStopHandler": (".action_player", "EmergencyStopHandler"),
    "RuleExecutor": (".rule_executor", "RuleExecutor"),
    "get_rule_executor": (".rule_executor", "get_rule_executor"),
    "ExecutionState": (".rule_executor", "ExecutionState"),
    "ExecutionProgress": (".rule_executor", "ExecutionProgress"),
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
    from .action_player import (
        ActionPlayer,
        EmergencyStopHandler,
        PlaybackProgress,
        PlayerState,
        action_player,
        get_action_player,
    )
    from .rule_executor import (
        ExecutionProgress,
        ExecutionState,
        RuleExecutor,
        get_rule_executor,
    )
