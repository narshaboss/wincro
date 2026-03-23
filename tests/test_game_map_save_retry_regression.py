import json
from pathlib import Path

from src.player.game_map import GameMap
import src.player.game_map as game_map_module


def test_save_retries_on_transient_permission_error(monkeypatch, tmp_path):
    gm = GameMap(name="retry-test")
    gm.mark_passable(0, 0)
    gm.mark_blocked(1, 0)

    map_path = tmp_path / "retry_map.json"
    attempts = {"count": 0}
    real_replace = game_map_module.os.replace

    def flaky_replace(src, dst):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise PermissionError(5, "Access is denied", str(dst))
        return real_replace(src, dst)

    monkeypatch.setattr(game_map_module.os, "replace", flaky_replace)
    gm.save(str(map_path))

    assert attempts["count"] >= 2
    assert map_path.exists()
    data = json.loads(Path(map_path).read_text(encoding="utf-8"))
    assert [0, 0] in data["passable"]
    assert [1, 0] in data["blocked"]
