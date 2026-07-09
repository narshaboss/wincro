from src.player.game_map import GameMap
from src.player.transient_direction_blocker import TransientDirectionBlocker


def test_transient_direction_blocker_clamps_and_expires_direction():
    blocker = TransientDirectionBlocker(default_ttl=6, min_ttl=6, max_ttl=36)

    expire_at = blocker.register(10, 20, "right", now_iter=100, ttl=2)

    assert expire_at == 106
    assert blocker.is_blocked(10, 20, "right", 105) is True
    assert blocker.is_blocked(10, 20, "right", 106) is False
    assert blocker.get_expire(10, 20, "right") is None


def test_transient_direction_blocker_extends_existing_expire_only_forward():
    blocker = TransientDirectionBlocker(default_ttl=10)

    assert blocker.register(1, 2, "up", now_iter=50, ttl=30) == 80
    assert blocker.register(1, 2, "up", now_iter=55, ttl=5) == 80


def test_transient_direction_blocker_can_sync_game_map_edges():
    game_map = GameMap("transient-edge")
    game_map.mark_passable(1, 1)
    game_map.mark_passable(2, 1)
    blocker = TransientDirectionBlocker(default_ttl=3)

    assert blocker.register_map_edge(game_map, 1, 1, "right", 10) is True
    assert game_map.is_edge_blocked(1, 1, "right") is True

    assert blocker.cleanup_map_edges(game_map, 12) == 0
    assert game_map.is_edge_blocked(1, 1, "right") is True
    assert blocker.cleanup_map_edges(game_map, 13) == 1
    assert game_map.is_edge_blocked(1, 1, "right") is False
