"""Shared runtime-only direction blocking for coordinate movement engines."""

from __future__ import annotations

from typing import Callable, Dict, Optional, Tuple


DirectionKey = Tuple[int, int, str]


class TransientDirectionBlocker:
    """Track short-lived blocked movement directions by iteration.

    This is intentionally runtime-only. It prevents both coordinate engines from
    repeatedly choosing the same failed edge while keeping permanent map data
    clean when a random obstacle disappears.
    """

    def __init__(
        self,
        *,
        default_ttl: int,
        min_ttl: int = 1,
        max_ttl: Optional[int] = None,
    ) -> None:
        self.default_ttl = max(1, int(default_ttl))
        self.min_ttl = max(1, int(min_ttl))
        self.max_ttl = int(max_ttl) if max_ttl is not None else None
        self.expires: Dict[DirectionKey, int] = {}

    @staticmethod
    def key(x: int, y: int, direction: str) -> DirectionKey:
        return (int(x), int(y), str(direction))

    def _normalize_ttl(self, ttl: Optional[int]) -> int:
        value = self.default_ttl if ttl is None else int(ttl)
        value = max(self.min_ttl, value)
        if self.max_ttl is not None:
            value = min(self.max_ttl, value)
        return value

    def register(self, x: int, y: int, direction: str, now_iter: int, ttl: Optional[int] = None) -> int:
        expire_at = int(now_iter) + self._normalize_ttl(ttl)
        edge = self.key(x, y, direction)
        self.expires[edge] = max(int(self.expires.get(edge, 0) or 0), expire_at)
        return self.expires[edge]

    def is_blocked(self, x: int, y: int, direction: str, now_iter: int) -> bool:
        edge = self.key(x, y, direction)
        expire_at = self.expires.get(edge)
        if expire_at is None:
            return False
        if int(now_iter) >= int(expire_at):
            self.expires.pop(edge, None)
            return False
        return True

    def get_expire(self, x: int, y: int, direction: str) -> Optional[int]:
        return self.expires.get(self.key(x, y, direction))

    def pop(self, x: int, y: int, direction: str) -> Optional[int]:
        return self.expires.pop(self.key(x, y, direction), None)

    def clear(self) -> None:
        self.expires.clear()

    def cleanup(
        self,
        now_iter: int,
        *,
        on_expire: Optional[Callable[[DirectionKey], None]] = None,
    ) -> int:
        removed = 0
        for edge, expire_at in list(self.expires.items()):
            if int(now_iter) < int(expire_at):
                continue
            self.expires.pop(edge, None)
            removed += 1
            if on_expire is not None:
                on_expire(edge)
        return removed

    def register_map_edge(self, game_map, x: int, y: int, direction: str, now_iter: int, ttl: Optional[int] = None) -> bool:
        expire_at = self.register(x, y, direction, now_iter, ttl)
        edge = self.key(x, y, direction)
        self.expires[edge] = expire_at
        return bool(game_map.mark_blocked_edge(edge[0], edge[1], edge[2]))

    def cleanup_map_edges(self, game_map, now_iter: int) -> int:
        def _clear(edge: DirectionKey) -> None:
            game_map.clear_blocked_edge(edge[0], edge[1], edge[2])

        return self.cleanup(now_iter, on_expire=_clear)
