from pathlib import Path
import textwrap


ROOT = Path(__file__).resolve().parents[1]
PLAYER_VIEW = ROOT / "src" / "ui" / "player_view.py"


def _load_explore_flap_helper():
    text = PLAYER_VIEW.read_text(encoding="utf-8-sig")
    start = text.index("            def _maybe_block_explore_flap(")
    end = text.index("            def _can_take_path_dir(", start)
    block = textwrap.dedent(text[start:end])

    namespace = {}
    exec(
        "def _factory(*, strict=False, frontier=False, target_pos=(20, 10), recent_positions=None, "
        "last_dir='right', manhattan=10, current_path_len=11, cx=16, cy=3, tx=20, ty=10, iteration=123):\n"
        "    current_path = [None] * current_path_len\n"
        "    path_index = 0\n"
        "    path_pos_index = {}\n"
        "    explored_from = {}\n"
        "    current_pos = (cx, cy)\n"
        "    _strict_route_mode = strict\n"
        "    _frontier_probe_phase = frontier\n"
        "    _manhattan = manhattan\n"
        "    recent_positions = list(recent_positions or [])\n"
        "    _ui_update_ok = False\n"
        "    calls = {'blocked': [], 'invalidated': 0}\n"
        "    class _Pathfinder:\n"
        "        def invalidate_path(self_inner):\n"
        "            calls['invalidated'] += 1\n"
        "    pathfinder = _Pathfinder()\n"
        "    class _Self:\n"
        "        def after(self_inner, *args, **kwargs):\n"
        "            raise AssertionError('UI path should stay disabled in test')\n"
        "    self = _Self()\n"
        "    def _register_dir_block(x, y, d, it, ttl=0):\n"
        "        calls['blocked'].append((x, y, d, it, ttl))\n"
        + textwrap.indent(block, "    ")
        + "\n    return _maybe_block_explore_flap, calls, explored_from\n",
        namespace,
    )
    return namespace["_factory"]


def test_explore_flap_blocks_immediate_reverse_into_recent_tile():
    factory = _load_explore_flap_helper()
    helper, calls, explored_from = factory(
        recent_positions=[(16, 3), (17, 3), (16, 3), (17, 3), (16, 3), (15, 3), (16, 3), (17, 3)],
        last_dir="right",
        manhattan=10,
        current_path_len=11,
        cx=16,
        cy=3,
        tx=20,
        ty=10,
    )

    blocked = helper("left", 15, 3)

    assert blocked is True
    assert calls["invalidated"] == 1
    assert calls["blocked"] == [(16, 3, "left", 123, 8)]
    assert explored_from[(16, 3)] == {"left"}


def test_explore_flap_does_not_block_outside_short_scope():
    factory = _load_explore_flap_helper()
    helper, calls, explored_from = factory(
        recent_positions=[(16, 3), (17, 3), (16, 3), (17, 3), (16, 3), (15, 3), (16, 3), (17, 3)],
        last_dir="right",
        manhattan=30,
        current_path_len=25,
        cx=16,
        cy=3,
        tx=40,
        ty=10,
    )

    blocked = helper("left", 15, 3)

    assert blocked is False
    assert calls["invalidated"] == 0
    assert calls["blocked"] == []
    assert explored_from == {}


def test_explore_flap_blocks_recent_reentry_even_without_explicit_reverse_dir():
    factory = _load_explore_flap_helper()
    helper, calls, explored_from = factory(
        recent_positions=[(21, 5), (20, 4), (19, 3), (18, 3), (17, 3), (16, 3), (17, 3), (18, 3)],
        last_dir=None,
        manhattan=11,
        current_path_len=10,
        cx=18,
        cy=3,
        tx=21,
        ty=10,
    )

    blocked = helper("right", 19, 3)

    assert blocked is True
    assert calls["invalidated"] == 1
    assert calls["blocked"] == [(18, 3, "right", 123, 8)]
    assert explored_from[(18, 3)] == {"right"}


def test_source_uses_explore_flap_guard_in_explore_and_backtrack_branches():
    text = PLAYER_VIEW.read_text(encoding="utf-8-sig")

    assert "if _maybe_block_explore_flap(_d0, cx + _ndx0, cy + _ndy0):" in text
    assert "if _maybe_block_explore_flap(chosen, _nx, _ny):" not in text
    assert "if _maybe_block_explore_flap(d, pos[0], pos[1]):" not in text
