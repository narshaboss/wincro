from src.analyzer.automation_models import GameModeConfig
from src.player.rule_executor import RuleExecutor


def test_game_mode_config_serializes_coordinate_anchor_fields(tmp_path):
    x_anchor = tmp_path / "x_anchor.png"
    y_anchor = tmp_path / "y_anchor.png"
    x_anchor.write_bytes(b"x")
    y_anchor.write_bytes(b"y")

    config = GameModeConfig(
        coord_x_region=[10, 20, 40, 36],
        coord_y_region=[50, 20, 80, 36],
        coord_anchor_enabled=True,
        coord_x_anchor_image=str(x_anchor),
        coord_y_anchor_image=str(y_anchor),
        coord_anchor_search_region=[1, 2, 120, 26],
        coord_x_anchor_offset=[12, -2, 42, 14],
        coord_y_anchor_offset=[13, -2, 43, 14],
    )

    data = config.to_dict()

    assert data["coord_anchor_enabled"] is True
    assert data["coord_x_anchor_image"] == "x_anchor.png"
    assert data["coord_y_anchor_image"] == "y_anchor.png"
    assert data["coord_anchor_search_region"] == [1, 2, 120, 26]
    assert data["coord_x_anchor_offset"] == [12, -2, 42, 14]
    assert data["coord_y_anchor_offset"] == [13, -2, 43, 14]

    restored = GameModeConfig.from_dict(data, templates_dir=tmp_path)

    assert restored.coord_anchor_enabled is True
    assert restored.coord_x_anchor_image == str(x_anchor)
    assert restored.coord_y_anchor_image == str(y_anchor)
    assert restored.coord_anchor_search_region == [1, 2, 120, 26]
    assert restored.coord_x_anchor_offset == [12, -2, 42, 14]
    assert restored.coord_y_anchor_offset == [13, -2, 43, 14]


def test_rule_executor_accepts_split_xy_bar_anchor_config(tmp_path):
    x_anchor = tmp_path / "x_anchor.png"
    y_anchor = tmp_path / "y_anchor.png"
    x_anchor.write_bytes(b"x")
    y_anchor.write_bytes(b"y")

    config = GameModeConfig(
        coord_x_region=[10, 20, 80, 45],
        coord_y_region=[90, 20, 160, 45],
        coord_anchor_enabled=True,
        coord_x_anchor_image=str(x_anchor),
        coord_y_anchor_image=str(y_anchor),
    )

    assert RuleExecutor()._has_coordinate_reader_config(config) is True


def test_rule_executor_rejects_incomplete_split_xy_bar_anchor_config(tmp_path):
    x_anchor = tmp_path / "x_anchor.png"
    x_anchor.write_bytes(b"x")

    config = GameModeConfig(
        coord_x_region=[10, 20, 80, 45],
        coord_y_region=[90, 20, 160, 45],
        coord_anchor_enabled=True,
        coord_x_anchor_image=str(x_anchor),
        coord_y_anchor_image="",
    )

    assert RuleExecutor()._has_coordinate_reader_config(config) is False
