"""Shared timing helpers for bulk action editing."""

from __future__ import annotations

import random
from typing import Any, Callable, Iterable, Optional, Tuple


BULK_RANDOM_BASE_WAIT_MIN = 0.5
BULK_RANDOM_BASE_WAIT_MAX = 1.5
BULK_RANDOM_RANGE_MIN = 0.5
BULK_RANDOM_RANGE_MAX = 1.0
BULK_RANDOM_PRESERVE_WAIT_FROM = 2.0


def apply_bulk_random_waits(
    items: Iterable[Any],
    *,
    random_value: Optional[Callable[[float, float], float]] = None,
) -> Tuple[int, int]:
    """Enable random waits recursively and return (total, base_wait_updated)."""
    choose = random_value or random.uniform
    total = 0
    base_wait_updated = 0

    for item in items:
        total += 1
        try:
            current_wait = float(getattr(item, "wait_after", 0.0) or 0.0)
        except (TypeError, ValueError):
            current_wait = 0.0

        item.wait_random = True
        if current_wait < BULK_RANDOM_PRESERVE_WAIT_FROM:
            item.wait_after = round(
                choose(BULK_RANDOM_BASE_WAIT_MIN, BULK_RANDOM_BASE_WAIT_MAX),
                2,
            )
            base_wait_updated += 1
        item.wait_random_range = round(
            choose(BULK_RANDOM_RANGE_MIN, BULK_RANDOM_RANGE_MAX),
            2,
        )

        children = getattr(item, "children", None) or []
        if children:
            child_total, child_updated = apply_bulk_random_waits(
                children,
                random_value=choose,
            )
            total += child_total
            base_wait_updated += child_updated

    return total, base_wait_updated
