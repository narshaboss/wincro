from types import SimpleNamespace

from src.utils.action_timing import apply_bulk_random_waits


def _item(wait_after, *, children=None):
    return SimpleNamespace(
        wait_after=wait_after,
        wait_random=False,
        wait_random_range=0.0,
        children=list(children or []),
    )


def test_bulk_random_waits_randomizes_short_waits_and_preserves_two_seconds_or_more():
    child = _item(2.0)
    short = _item(0.2, children=[child])
    long = _item(5.0)
    values = iter([1.25, 0.75, 0.6, 0.9])

    total, updated = apply_bulk_random_waits(
        [short, long],
        random_value=lambda _minimum, _maximum: next(values),
    )

    assert (total, updated) == (3, 1)
    assert short.wait_after == 1.25
    assert child.wait_after == 2.0
    assert long.wait_after == 5.0
    assert short.wait_random_range == 0.75
    assert child.wait_random_range == 0.6
    assert long.wait_random_range == 0.9
    assert short.wait_random is True
    assert child.wait_random is True
    assert long.wait_random is True


def test_bulk_random_waits_keeps_generated_values_in_requested_ranges():
    items = [_item(0.0) for _ in range(100)]

    apply_bulk_random_waits(items)

    assert all(0.5 <= item.wait_after <= 1.5 for item in items)
    assert all(0.5 <= item.wait_random_range <= 1.0 for item in items)
