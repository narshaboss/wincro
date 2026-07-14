import logging
import threading
from pathlib import Path
from types import SimpleNamespace

from src.player.game_map import GameMap
from src.ui.player_map_runtime import GameModeMapRuntime


class _DummyOwner:
    def __init__(self, tmp_path: Path):
        self._tmp_path = tmp_path
        self._config = SimpleNamespace(name="dummy-mode")
        self._current_segment_idx = 0
        self._game_map = None
        self._map_pathfinder = None
        self._map_explorer = None
        self._segment_switch_in_progress = False
        self._runtime_reload_segment_idx = None
        self._runtime_reload_cooldown_until = 0.0
        self._mapping_segment_completion_committed_idx = None
        self._start_registered = False
        self._boss_segment_active = False
        self._is_mapping = False
        self._is_mapping_test = False
        self._locked_segments = set()
        self._has_map_backup = False
        self._map_save_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._repair_calls = []
        self._repair_return = False
        self._repair_mutator = None
        self._sanitize_calls = []
        self.logged = []

    def _sanitize_segment_start_pos(self, _game_map_ref, _segment_idx):
        self._sanitize_calls.append(("start", _segment_idx))
        return None

    def _sanitize_segment_placeholder_target_tile(self, _game_map_ref, _segment_idx):
        self._sanitize_calls.append(("target", _segment_idx))
        return None

    def _should_persist_segment_end(self, _segment_idx):
        return True

    def _is_segment_map_locked(self, segment_idx):
        return segment_idx in self._locked_segments

    def _get_segment_display_name(self, segment_idx):
        return f"segment-{segment_idx}"

    def _get_segment_map_name(self, segment_idx):
        return str(self._tmp_path / f"segment_{segment_idx}_map.json")

    def _resolve_segment_map_load_path(self, segment_idx):
        return self._get_segment_map_name(segment_idx)

    def _uses_transient_local_map(self, _segment_idx):
        return False

    def _repair_segment_map_connectivity_from_backups(self, game_map_ref, segment_idx, map_path):
        self._repair_calls.append((segment_idx, str(map_path)))
        if callable(self._repair_mutator):
            self._repair_mutator(game_map_ref)
        return bool(self._repair_return)

    def _append_log(self, message: str, force: bool = False):
        self.logged.append((message, force))

    def _update_mapping_status(self):
        return None

    def after(self, _ms, func=None, *args):
        if func is not None:
            return func(*args)
        return None


def _make_map(name: str = "map") -> GameMap:
    game_map = GameMap(name=name)
    game_map.passable = {(1, 1), (1, 2), (2, 2)}
    game_map.blocked = {(2, 1)}
    game_map.soft_blocked = {}
    return game_map


def test_game_map_is_bound_to_segment_on_runtime_loads():
    owner = _DummyOwner(Path("."))
    runtime = GameModeMapRuntime(owner)
    game_map = _make_map()

    runtime.bind_game_map_segment(game_map, 4)

    assert getattr(game_map, "_segment_idx", None) == 4


def test_auto_save_is_blocked_outside_mapping_session(tmp_path):
    owner = _DummyOwner(tmp_path)
    runtime = GameModeMapRuntime(owner)
    game_map = _make_map("normal-play")
    owner._game_map = game_map

    saved_path = runtime.auto_save_map(segment_idx=0, game_map_ref=game_map, critical=True)

    assert saved_path == ""
    assert not Path(owner._get_segment_map_name(0)).exists()
    assert owner._sanitize_calls == []


def test_normal_play_cannot_overwrite_existing_map(tmp_path):
    owner = _DummyOwner(tmp_path)
    runtime = GameModeMapRuntime(owner)
    stored_map = _make_map("stored")
    map_path = Path(owner._get_segment_map_name(0))
    stored_map.save(str(map_path))
    before = map_path.read_bytes()

    runtime_map = _make_map("runtime")
    runtime_map.passable.add((99, 99))
    saved_path = runtime.auto_save_map(
        segment_idx=0,
        game_map_ref=runtime_map,
        critical=True,
        reason="normal-play-test",
    )

    assert saved_path == ""
    assert map_path.read_bytes() == before
    assert owner._sanitize_calls == []


def test_auto_save_prefers_bound_segment_idx_during_mapping(tmp_path, caplog):
    owner = _DummyOwner(tmp_path)
    owner._is_mapping = True
    runtime = GameModeMapRuntime(owner)
    game_map = _make_map("bound-save")
    owner._game_map = game_map
    runtime.bind_game_map_segment(game_map, 3)

    with caplog.at_level(logging.INFO):
        saved_path = runtime.auto_save_map(segment_idx=0, game_map_ref=game_map, critical=True)

    assert saved_path == str(tmp_path / "segment_3_map.json")
    assert Path(saved_path).exists()
    assert runtime.verify_saved_map_file(saved_path, expected_passable=3) is True
    assert "세그먼트 보정: 0->3" in caplog.text


def test_locked_map_is_never_saved_even_with_explicit_mapping_capture(tmp_path):
    owner = _DummyOwner(tmp_path)
    owner._locked_segments.add(3)
    runtime = GameModeMapRuntime(owner)
    stored_map = _make_map("stored-locked")
    map_path = Path(owner._get_segment_map_name(3))
    stored_map.save(str(map_path))
    before = map_path.read_bytes()
    game_map = _make_map("locked")
    game_map.passable.add((99, 99))
    runtime.bind_game_map_segment(game_map, 3)

    saved_path = runtime.auto_save_map(
        segment_idx=3,
        game_map_ref=game_map,
        critical=True,
        mapping_session=True,
        reason="test",
    )

    assert saved_path == ""
    assert map_path.read_bytes() == before
    assert owner._sanitize_calls == []


def test_switch_segment_map_does_not_persist_during_normal_play(tmp_path):
    owner = _DummyOwner(tmp_path)
    runtime = GameModeMapRuntime(owner)
    owner._game_map = _make_map("current")
    owner._game_map.passable.add((7, 7))
    owner._current_segment_idx = 4
    runtime.bind_game_map_segment(owner._game_map, 4)

    assert runtime.switch_segment_map(2, skip_save=False) is True

    assert not Path(owner._get_segment_map_name(4)).exists()


def test_switch_segment_map_persists_previous_segment_during_mapping(tmp_path):
    owner = _DummyOwner(tmp_path)
    owner._is_mapping = True
    runtime = GameModeMapRuntime(owner)
    current_map = _make_map("current")
    owner._game_map = current_map
    owner._current_segment_idx = 4
    runtime.bind_game_map_segment(current_map, 4)
    calls = []
    original = runtime._persist_map_snapshot

    def wrapped(**kwargs):
        calls.append(dict(kwargs))
        return original(**kwargs)

    runtime._persist_map_snapshot = wrapped

    assert runtime.switch_segment_map(2, skip_save=False) is True

    assert len(calls) == 1
    assert calls[0]["segment_idx"] == 4
    assert calls[0]["game_map_ref"] is current_map
    assert calls[0]["critical"] is True
    assert calls[0]["allow_during_switch"] is True
    assert calls[0]["emit_saved_ui_log"] is False
    assert calls[0]["mapping_session"] is True
    assert calls[0]["reason"] == "segment-switch"
    assert Path(owner._get_segment_map_name(4)).exists()


def test_switch_segment_map_loads_new_segment_and_binds_runtime_map(tmp_path):
    owner = _DummyOwner(tmp_path)
    runtime = GameModeMapRuntime(owner)
    owner._game_map = _make_map("current")
    runtime.bind_game_map_segment(owner._game_map, 0)

    next_map = _make_map("next")
    next_map.passable.add((5, 5))
    next_path = Path(owner._get_segment_map_name(2))
    next_map.save(str(next_path))

    assert runtime.switch_segment_map(2, skip_save=True) is True
    assert owner._current_segment_idx == 2
    assert getattr(owner._game_map, "_segment_idx", None) == 2
    assert (5, 5) in owner._game_map.passable


def test_switch_segment_map_load_repair_stays_in_memory(tmp_path):
    owner = _DummyOwner(tmp_path)
    owner._repair_return = True
    owner._repair_mutator = lambda game_map_ref: game_map_ref.mark_passable(9, 9)
    runtime = GameModeMapRuntime(owner)
    owner._game_map = _make_map("current")
    runtime.bind_game_map_segment(owner._game_map, 0)

    next_map = _make_map("next")
    next_path = Path(owner._get_segment_map_name(2))
    next_map.save(str(next_path))

    assert runtime.switch_segment_map(2, skip_save=True) is True
    assert owner._repair_calls == [(2, str(next_path))]
    assert (9, 9) in owner._game_map.passable

    saved_map = GameMap(name="saved-next")
    assert saved_map.load(str(next_path)) is True
    assert (9, 9) not in saved_map.passable
