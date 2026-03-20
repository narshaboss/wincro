from pathlib import Path
import textwrap


ROOT = Path(__file__).resolve().parents[1]
PLAYER_VIEW = ROOT / "src" / "ui" / "player_view.py"


def _load_coalesce_helper():
    text = PLAYER_VIEW.read_text(encoding="utf-8-sig")
    start = text.index("        def _coalesce_boss_chase_target(")
    end = text.index("        def _boss_transition_locked()", start)
    block = text[start:end]
    block = textwrap.dedent(block)

    namespace = {}
    exec(
        "def _factory(_boss_chasing, _chase_tx, _chase_ty):\n"
        + textwrap.indent(block, "    ")
        + "\n    return _coalesce_boss_chase_target\n",
        namespace,
    )
    return namespace["_factory"]


def test_boss_chase_target_keeps_previous_target_on_small_jitter():
    factory = _load_coalesce_helper()
    helper = factory(True, 27, 7)

    tx, ty, reused, shift = helper(28, 5, 13, 9)

    assert (tx, ty) == (27, 7)
    assert reused is True
    assert shift == 3


def test_boss_chase_target_accepts_material_target_change():
    factory = _load_coalesce_helper()
    helper = factory(True, 27, 7)

    tx, ty, reused, shift = helper(33, 3, 13, 9)

    assert (tx, ty) == (33, 3)
    assert reused is False
    assert shift == 10


def test_boss_chase_target_passthrough_when_not_chasing():
    factory = _load_coalesce_helper()
    helper = factory(False, 27, 7)

    tx, ty, reused, shift = helper(28, 5, 13, 9)

    assert (tx, ty) == (28, 5)
    assert reused is False
    assert shift is None
