from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import importlib

from src.database.models import Action, Sequence
from src.player.action_player import ActionPlayer, PlaybackProgress, PlayerState


PLAYER_VIEW = Path(r"C:\Projects\wincro\src\ui\player_view.py")
ACTION_PLAYER_MODULE = importlib.import_module("src.player.action_player")


def test_action_enabled_round_trip():
    action = Action(action_type="click", x=10, y=20, enabled=False)
    payload = action.to_dict()

    assert payload["enabled"] is False

    rebuilt = Action.from_dict(payload)

    assert rebuilt.enabled is False


def test_sequence_enabled_action_count():
    sequence = Sequence(
        name="test",
        actions=[
            Action(action_type="click", enabled=True),
            Action(action_type="click", enabled=False),
            Action(action_type="click", enabled=True),
        ],
    )

    assert sequence.action_count == 3
    assert sequence.enabled_action_count == 2
    assert len(sequence.enabled_actions) == 2


def test_player_rejects_all_disabled_actions():
    cfg = SimpleNamespace(
        player=SimpleNamespace(
            emergency_stop_key="escape",
            emergency_stop_count=2,
            speed_multiplier=1.0,
        )
    )
    db = Mock()
    db.create_execution_log.return_value = 1

    with (
        patch.object(ACTION_PLAYER_MODULE, "get_config", return_value=cfg),
        patch.object(ACTION_PLAYER_MODULE, "get_db", return_value=db),
        patch.object(ACTION_PLAYER_MODULE, "get_template_matcher", return_value=Mock()),
        patch.object(ACTION_PLAYER_MODULE, "get_screen_recorder", return_value=Mock()),
        patch.object(ACTION_PLAYER_MODULE, "create_execution_logger", return_value=Mock()),
    ):
        player = ActionPlayer()
        sequence = Sequence(name="disabled", actions=[Action(action_type="click", enabled=False)])

        assert player.play(sequence) is False


def test_player_progress_uses_only_enabled_actions():
    cfg = SimpleNamespace(
        player=SimpleNamespace(
            emergency_stop_key="escape",
            emergency_stop_count=2,
            speed_multiplier=1.0,
        )
    )
    db = Mock()
    db.create_execution_log.return_value = 1
    fake_thread = Mock()

    with (
        patch.object(ACTION_PLAYER_MODULE, "get_config", return_value=cfg),
        patch.object(ACTION_PLAYER_MODULE, "get_db", return_value=db),
        patch.object(ACTION_PLAYER_MODULE, "get_template_matcher", return_value=Mock()),
        patch.object(ACTION_PLAYER_MODULE, "get_screen_recorder", return_value=Mock()),
        patch.object(ACTION_PLAYER_MODULE, "create_execution_logger", return_value=Mock()),
        patch.object(ACTION_PLAYER_MODULE.threading, "Thread", return_value=fake_thread),
    ):
        player = ActionPlayer()
        sequence = Sequence(
            name="enabled-only",
            actions=[
                Action(action_type="click", enabled=False),
                Action(action_type="type", text="abc", enabled=True),
            ],
        )

        assert player.play(sequence, repeat_count=3) is True
        assert player.progress.total_steps == 3
        assert player._execution_log.total_steps == 1
        fake_thread.start.assert_called_once()


def test_play_loop_skips_disabled_actions():
    cfg = SimpleNamespace(
        player=SimpleNamespace(
            emergency_stop_key="escape",
            emergency_stop_count=2,
            speed_multiplier=1.0,
        )
    )
    db = Mock()
    db.create_execution_log.return_value = 1

    with (
        patch.object(ACTION_PLAYER_MODULE, "get_config", return_value=cfg),
        patch.object(ACTION_PLAYER_MODULE, "get_db", return_value=db),
        patch.object(ACTION_PLAYER_MODULE, "get_template_matcher", return_value=Mock()),
        patch.object(ACTION_PLAYER_MODULE, "get_screen_recorder", return_value=Mock()),
        patch.object(ACTION_PLAYER_MODULE, "create_execution_logger", return_value=Mock()),
    ):
        player = ActionPlayer()
        sequence = Sequence(
            name="loop",
            actions=[
                Action(action_type="click", enabled=False),
                Action(action_type="click", x=1, y=2, enabled=True),
                Action(action_type="type", text="skip", enabled=False),
            ],
        )
        player._state = PlayerState.RUNNING
        player._progress = PlaybackProgress(state=PlayerState.RUNNING)
        player._execution_logger = Mock()
        player._execution_log = Mock()

        with (
            patch.object(player, "_execute_action", return_value=(True, "")) as exec_mock,
            patch.object(player, "_finalize_execution") as finalize_mock,
            patch.object(player, "_update_progress"),
        ):
            player._play_loop(sequence, repeat_count=1, speed_multiplier=1.0)

        assert exec_mock.call_count == 1
        assert exec_mock.call_args.args[0] is sequence.actions[1]
        finalize_mock.assert_called_once()


def test_player_view_contains_action_enable_toggle():
    src = PLAYER_VIEW.read_text(encoding="utf-8")

    assert "def _toggle_action_enabled(self, action: Action):" in src
    assert 'label="비활성화" if getattr(a, "enabled", True) else "활성화"' in src
    assert 'type_text = f"[비활성] {type_text}"' in src
