import json
from pathlib import Path

from src.utils import config as config_module
from src.utils.config import (
    AUTO_RUN_FACTORY_GROUP_ID,
    AUTO_RUN_FACTORY_GROUP_NAME,
    AUTO_RUN_FACTORY_GROUP_REPEAT,
    AUTO_RUN_FACTORY_PLANS,
    AUTO_RUN_PROFILE_FORCE_APP_VERSION,
    AUTO_RUN_PROFILE_GROUP_ID,
    AUTO_RUN_PROFILE_GROUP_NAME,
    AUTO_RUN_PROFILE_GROUP_REPEAT,
    AUTO_RUN_PROFILE_PLANS,
    AUTO_RUN_PROFILE_GROUPS,
    AUTO_RUN_PROFILE_VERSION,
    APP_VERSION,
    BRANDING_PROFILE_VERSION,
    AppConfig,
    ArduinoConfig,
    ConfigManager,
    DATA_DIR,
    PlayerConfig,
    UIConfig,
)
from src.utils.plan_sequence_groups import (
    get_active_plan_sequence,
    make_plan_sequence_group,
    mirror_active_group_to_legacy,
    normalize_plan_sequence_groups,
    sync_plan_repeat_in_groups,
)


def test_legacy_plan_sequence_migrates_to_default_group():
    player = PlayerConfig(
        plan_sequence=[r"C:\Projects\wincro\data\plans\a.json", r"C:\Projects\wincro\data\plans\b.json"],
        plan_sequence_repeats=[2, 3],
    )

    groups = normalize_plan_sequence_groups(player)

    assert len(groups) == 1
    assert groups[0]["name"] == "기본 그룹"
    assert player.active_plan_sequence_group_id == groups[0]["group_id"]
    assert groups[0]["entries"] == [
        {"plan_path": r"C:\Projects\wincro\data\plans\a.json", "repeat_count": 2},
        {"plan_path": r"C:\Projects\wincro\data\plans\b.json", "repeat_count": 3},
    ]


def test_active_group_only_resolves_for_startup_autorun():
    group_a = make_plan_sequence_group(
        "자동사냥",
        [{"plan_path": r"C:\plans\hunt.json", "repeat_count": 1}],
        group_id="a",
    )
    group_b = make_plan_sequence_group(
        "원각공장",
        [{"plan_path": r"C:\plans\factory.json", "repeat_count": 7}],
        group_id="b",
    )
    player = PlayerConfig(plan_sequence_groups=[group_a, group_b], active_plan_sequence_group_id="b")

    paths, repeats, group = get_active_plan_sequence(player)

    assert group["name"] == "원각공장"
    assert paths == [r"C:\plans\factory.json"]
    assert repeats == [7]


def test_group_repeat_expands_active_sequence_in_group_order():
    group = make_plan_sequence_group(
        "원각공장",
        [
            {"plan_path": r"C:\plans\factory.json", "repeat_count": 7},
            {"plan_path": r"C:\plans\hunt.json", "repeat_count": 2},
        ],
        group_id="factory",
        repeat_count=3,
    )
    player = PlayerConfig(plan_sequence_groups=[group], active_plan_sequence_group_id="factory")

    paths, repeats, resolved_group = get_active_plan_sequence(player)

    assert resolved_group["repeat_count"] == 3
    assert paths == [
        r"C:\plans\factory.json",
        r"C:\plans\hunt.json",
        r"C:\plans\factory.json",
        r"C:\plans\hunt.json",
        r"C:\plans\factory.json",
        r"C:\plans\hunt.json",
    ]
    assert repeats == [7, 2, 7, 2, 7, 2]


def test_active_group_is_mirrored_to_legacy_flat_sequence():
    player = PlayerConfig(
        plan_sequence_groups=[
            make_plan_sequence_group("A", [{"plan_path": r"C:\plans\a.json", "repeat_count": 2}], group_id="a"),
            make_plan_sequence_group("B", [{"plan_path": r"C:\plans\b.json", "repeat_count": 5}], group_id="b"),
        ],
        active_plan_sequence_group_id="b",
    )

    mirror_active_group_to_legacy(player)

    assert player.plan_sequence == [r"C:\plans\b.json"]
    assert player.plan_sequence_repeats == [5]


def test_group_repeat_is_mirrored_to_legacy_flat_sequence():
    player = PlayerConfig(
        plan_sequence_groups=[
            make_plan_sequence_group(
                "B",
                [{"plan_path": r"C:\plans\b.json", "repeat_count": 5}],
                group_id="b",
                repeat_count=2,
            ),
        ],
        active_plan_sequence_group_id="b",
    )

    mirror_active_group_to_legacy(player)

    assert player.plan_sequence == [r"C:\plans\b.json", r"C:\plans\b.json"]
    assert player.plan_sequence_repeats == [5, 5]


def test_repeat_sync_updates_matching_group_entries_by_filename():
    player = PlayerConfig(
        plan_sequence_groups=[
            make_plan_sequence_group(
                "A",
                [
                    {"plan_path": r"C:\old\factory.json", "repeat_count": 1},
                    {"plan_path": r"C:\old\other.json", "repeat_count": 2},
                ],
                group_id="a",
            )
        ],
        active_plan_sequence_group_id="a",
    )

    changed = sync_plan_repeat_in_groups(player, r"D:\new\factory.json", 9)

    assert changed is True
    assert player.plan_sequence_groups[0]["entries"][0]["repeat_count"] == 9
    assert player.plan_sequence_repeats == [9, 2]


def test_repeat_sync_updates_active_group_only_by_default():
    player = PlayerConfig(
        plan_sequence_groups=[
            make_plan_sequence_group("A", [{"plan_path": r"C:\plans\factory.json", "repeat_count": 1}], group_id="a"),
            make_plan_sequence_group("B", [{"plan_path": r"C:\plans\factory.json", "repeat_count": 5}], group_id="b"),
        ],
        active_plan_sequence_group_id="a",
    )

    changed = sync_plan_repeat_in_groups(player, r"C:\plans\factory.json", 3)

    assert changed is True
    assert player.plan_sequence_groups[0]["entries"][0]["repeat_count"] == 3
    assert player.plan_sequence_groups[1]["entries"][0]["repeat_count"] == 5
    assert player.plan_sequence_repeats == [3]


def test_packaged_auto_run_profile_updates_only_player_playback_defaults():
    config = AppConfig(
        player=PlayerConfig(auto_run_enabled=False),
        ui=UIConfig(app_name="pc-local-name", window_mode="editor"),
        arduino=ArduinoConfig(com_port="COM9", enabled=True),
    )

    ConfigManager()._apply_packaged_player_defaults(config)

    assert config.ui.app_name == "pc-local-name"
    assert config.ui.window_mode == "editor"
    assert config.arduino.com_port == "COM9"
    assert config.arduino.enabled is True
    assert config.player.auto_run_enabled is True
    assert config.player.auto_run_profile_version == AUTO_RUN_PROFILE_VERSION
    assert config.player.active_plan_sequence_group_id == AUTO_RUN_PROFILE_GROUP_ID
    assert len(config.player.plan_sequence_groups) == len(AUTO_RUN_PROFILE_GROUPS)

    group = config.player.plan_sequence_groups[0]
    assert group["group_id"] == AUTO_RUN_PROFILE_GROUP_ID
    assert group["name"] == AUTO_RUN_PROFILE_GROUP_NAME
    assert group["repeat_count"] == AUTO_RUN_PROFILE_GROUP_REPEAT
    assert [Path(entry["plan_path"]).name for entry in group["entries"]] == [
        file_name for file_name, _repeat in AUTO_RUN_PROFILE_PLANS
    ]
    assert [entry["repeat_count"] for entry in group["entries"]] == [
        repeat for _file_name, repeat in AUTO_RUN_PROFILE_PLANS
    ]
    factory_group = config.player.plan_sequence_groups[1]
    assert factory_group["group_id"] == AUTO_RUN_FACTORY_GROUP_ID
    assert factory_group["name"] == AUTO_RUN_FACTORY_GROUP_NAME
    assert factory_group["repeat_count"] == AUTO_RUN_FACTORY_GROUP_REPEAT
    assert [Path(entry["plan_path"]).name for entry in factory_group["entries"]] == [
        file_name for file_name, _repeat in AUTO_RUN_FACTORY_PLANS
    ]
    assert [entry["repeat_count"] for entry in factory_group["entries"]] == [
        repeat for _file_name, repeat in AUTO_RUN_FACTORY_PLANS
    ]
    assert config.player.plan_sequence == [
        str(DATA_DIR / "plans" / file_name)
        for _ in range(AUTO_RUN_PROFILE_GROUP_REPEAT)
        for file_name, _repeat in AUTO_RUN_PROFILE_PLANS
    ]
    assert config.player.plan_sequence_repeats == [
        repeat
        for _ in range(AUTO_RUN_PROFILE_GROUP_REPEAT)
        for _file_name, repeat in AUTO_RUN_PROFILE_PLANS
    ]


def test_packaged_auto_run_profile_v3_resets_existing_playback_groups_once():
    old_packaged_group = make_plan_sequence_group(
        AUTO_RUN_PROFILE_GROUP_NAME,
        [{"plan_path": r"C:\plans\old_hunt.json", "repeat_count": 9}],
        group_id=AUTO_RUN_PROFILE_GROUP_ID,
        repeat_count=1,
    )
    custom_group = make_plan_sequence_group(
        "custom",
        [{"plan_path": r"C:\plans\custom.json", "repeat_count": 7}],
        group_id="custom",
    )
    config = AppConfig(
        player=PlayerConfig(
            auto_run_enabled=False,
            plan_sequence_groups=[old_packaged_group, custom_group],
            active_plan_sequence_group_id="custom",
            auto_run_profile_version="auto_hunt_raid_v1",
        ),
        ui=UIConfig(app_name="pc-local-name", window_mode="player"),
        arduino=ArduinoConfig(com_port="COM7", enabled=True),
    )

    ConfigManager()._apply_packaged_player_defaults(config)

    assert config.ui.app_name == "pc-local-name"
    assert config.ui.window_mode == "player"
    assert config.arduino.com_port == "COM7"
    assert config.arduino.enabled is True
    assert config.player.auto_run_enabled is True
    assert config.player.auto_run_profile_version == AUTO_RUN_PROFILE_VERSION
    assert config.player.active_plan_sequence_group_id == AUTO_RUN_PROFILE_GROUP_ID
    assert config.player.plan_sequence_groups[0]["group_id"] == AUTO_RUN_PROFILE_GROUP_ID
    assert config.player.plan_sequence_groups[0]["repeat_count"] == AUTO_RUN_PROFILE_GROUP_REPEAT
    assert [Path(entry["plan_path"]).name for entry in config.player.plan_sequence_groups[0]["entries"]] == [
        file_name for file_name, _repeat in AUTO_RUN_PROFILE_PLANS
    ]
    assert len(config.player.plan_sequence_groups) == len(AUTO_RUN_PROFILE_GROUPS)
    assert config.player.plan_sequence_groups[0] != custom_group
    assert config.player.plan_sequence_groups[1]["group_id"] == AUTO_RUN_FACTORY_GROUP_ID


def test_packaged_auto_run_profile_does_not_override_after_marker():
    existing_group = make_plan_sequence_group(
        "사용자그룹",
        [{"plan_path": r"C:\plans\custom.json", "repeat_count": 7}],
        group_id="custom",
    )
    config = AppConfig(
        player=PlayerConfig(
            auto_run_enabled=False,
            plan_sequence_groups=[existing_group],
            active_plan_sequence_group_id="custom",
            auto_run_profile_version=AUTO_RUN_PROFILE_VERSION,
        )
    )

    ConfigManager()._apply_packaged_player_defaults(config)

    assert config.player.auto_run_enabled is False
    assert config.player.active_plan_sequence_group_id == "custom"
    assert config.player.plan_sequence_groups == [existing_group]


def test_packaged_auto_run_profile_repairs_managed_group_paths_after_marker(monkeypatch, tmp_path):
    install_data_dir = tmp_path / "installed" / "_internal" / "data"
    old_data_dir = Path(r"C:\Projects\wincro\data")
    packaged_group = make_plan_sequence_group(
        AUTO_RUN_PROFILE_GROUP_NAME,
        [
            {"plan_path": str(old_data_dir / "plans" / file_name), "repeat_count": repeat}
            for file_name, repeat in AUTO_RUN_PROFILE_PLANS
        ],
        group_id=AUTO_RUN_PROFILE_GROUP_ID,
        repeat_count=AUTO_RUN_PROFILE_GROUP_REPEAT,
    )
    factory_group = make_plan_sequence_group(
        AUTO_RUN_FACTORY_GROUP_NAME,
        [
            {"plan_path": str(old_data_dir / "plans" / file_name), "repeat_count": repeat}
            for file_name, repeat in AUTO_RUN_FACTORY_PLANS
        ],
        group_id=AUTO_RUN_FACTORY_GROUP_ID,
        repeat_count=AUTO_RUN_FACTORY_GROUP_REPEAT,
    )
    config = AppConfig(
        player=PlayerConfig(
            auto_run_enabled=True,
            plan_sequence_groups=[packaged_group, factory_group],
            active_plan_sequence_group_id=AUTO_RUN_PROFILE_GROUP_ID,
            auto_run_profile_version=AUTO_RUN_PROFILE_VERSION,
        )
    )

    monkeypatch.setattr(config_module, "DATA_DIR", install_data_dir)
    ConfigManager()._apply_packaged_player_defaults(config)

    assert [
        Path(entry["plan_path"]).parent
        for group in config.player.plan_sequence_groups
        for entry in group["entries"]
    ] == [
        install_data_dir / "plans"
        for group in config.player.plan_sequence_groups
        for _entry in group["entries"]
    ]
    assert config.player.plan_sequence == [
        str(install_data_dir / "plans" / file_name)
        for _ in range(AUTO_RUN_PROFILE_GROUP_REPEAT)
        for file_name, _repeat in AUTO_RUN_PROFILE_PLANS
    ]


def test_existing_config_load_preserves_pc_local_player_and_ui_settings(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "plans").mkdir()
    custom_plan = tmp_path / "custom_plan.json"
    custom_group = make_plan_sequence_group(
        "custom-pc-group",
        [{"plan_path": str(custom_plan), "repeat_count": 7}],
        group_id="custom",
        repeat_count=2,
    )
    existing = AppConfig(
        player=PlayerConfig(
            auto_run_enabled=False,
            plan_sequence_groups=[custom_group],
            active_plan_sequence_group_id="custom",
            auto_run_profile_version="older_release_marker",
        ),
        ui=UIConfig(
            app_name="Custom PC Tool",
            random_name_mode=True,
            random_name_alias="Local Alias",
            branding_profile_version="older_brand_marker",
        ),
    )
    manager = ConfigManager()
    config_path.write_text(
        json.dumps(manager._config_to_dict(existing), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    monkeypatch.setattr(config_module, "CONFIG_FILE", config_path)
    monkeypatch.setattr(config_module, "DATA_DIR", data_dir)
    monkeypatch.setattr(config_module, "AUTO_RUN_PROFILE_FORCE_APP_VERSION", "0.0.0")
    manager._config = None

    loaded = manager.load()

    assert loaded.player.auto_run_enabled is False
    assert loaded.player.auto_run_profile_version == "older_release_marker"
    assert loaded.player.active_plan_sequence_group_id == "custom"
    assert len(loaded.player.plan_sequence_groups) == 1
    assert loaded.player.plan_sequence_groups[0]["name"] == "custom-pc-group"
    assert loaded.player.plan_sequence_groups[0]["repeat_count"] == 2
    assert loaded.player.plan_sequence_groups[0]["entries"] == [
        {"plan_path": str(custom_plan), "repeat_count": 7}
    ]
    assert loaded.ui.app_name == "Custom PC Tool"
    assert loaded.ui.random_name_mode is True
    assert loaded.ui.random_name_alias == "Local Alias"
    assert loaded.ui.branding_profile_version == "older_brand_marker"


def test_current_release_forces_only_player_auto_run_group_once(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "plans").mkdir()
    custom_group = make_plan_sequence_group(
        "custom-pc-group",
        [{"plan_path": r"C:\plans\custom.json", "repeat_count": 7}],
        group_id="custom",
        repeat_count=2,
    )
    existing = AppConfig(
        player=PlayerConfig(
            auto_run_enabled=False,
            plan_sequence_groups=[custom_group],
            active_plan_sequence_group_id="custom",
            auto_run_profile_version="auto_hunt_raid_factory_v4",
        ),
        ui=UIConfig(app_name="PC Local", window_mode="editor"),
        arduino=ArduinoConfig(com_port="COM9", enabled=True),
    )
    manager = ConfigManager()
    raw_existing = manager._config_to_dict(existing)
    raw_existing["ui"]["local_unknown"] = "keep-ui"
    raw_existing["local_unknown_root"] = "keep-root"
    config_path.write_text(
        json.dumps(raw_existing, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    monkeypatch.setattr(config_module, "CONFIG_FILE", config_path)
    monkeypatch.setattr(config_module, "DATA_DIR", data_dir)
    monkeypatch.setattr(config_module, "AUTO_RUN_PROFILE_FORCE_APP_VERSION", AUTO_RUN_PROFILE_FORCE_APP_VERSION)
    manager._config = None

    loaded = manager.load()

    assert loaded.ui.app_name == "PC Local"
    assert loaded.ui.window_mode == "editor"
    assert loaded.arduino.com_port == "COM9"
    assert loaded.arduino.enabled is True
    assert loaded.player.auto_run_enabled is True
    assert loaded.player.auto_run_profile_version == AUTO_RUN_PROFILE_VERSION
    assert loaded.player.active_plan_sequence_group_id == AUTO_RUN_PROFILE_GROUP_ID
    assert len(loaded.player.plan_sequence_groups) == len(AUTO_RUN_PROFILE_GROUPS)
    auto_group = loaded.player.plan_sequence_groups[0]
    assert auto_group["group_id"] == AUTO_RUN_PROFILE_GROUP_ID
    assert auto_group["repeat_count"] == AUTO_RUN_PROFILE_GROUP_REPEAT
    assert [Path(entry["plan_path"]).name for entry in auto_group["entries"]] == [
        file_name for file_name, _repeat in AUTO_RUN_PROFILE_PLANS
    ]
    assert [entry["repeat_count"] for entry in auto_group["entries"]] == [
        repeat for _file_name, repeat in AUTO_RUN_PROFILE_PLANS
    ]
    assert [Path(path).name for path in loaded.player.plan_sequence] == [
        file_name
        for _ in range(AUTO_RUN_PROFILE_GROUP_REPEAT)
        for file_name, _repeat in AUTO_RUN_PROFILE_PLANS
    ]
    assert loaded.player.plan_sequence_repeats == [
        repeat
        for _ in range(AUTO_RUN_PROFILE_GROUP_REPEAT)
        for _file_name, repeat in AUTO_RUN_PROFILE_PLANS
    ]
    persisted = json.loads(config_path.read_text(encoding="utf-8"))
    assert persisted["player"]["auto_run_profile_version"] == AUTO_RUN_PROFILE_VERSION
    assert persisted["player"]["plan_sequence_groups"][0]["entries"][1]["repeat_count"] == 5
    assert persisted["ui"]["app_name"] == "PC Local"
    assert persisted["ui"]["local_unknown"] == "keep-ui"
    assert persisted["arduino"]["com_port"] == "COM9"
    assert persisted["local_unknown_root"] == "keep-root"


def test_current_release_reapplies_over_previous_marker_once(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "plans").mkdir()
    stale_auto_group = make_plan_sequence_group(
        AUTO_RUN_PROFILE_GROUP_NAME,
        [
            {"plan_path": r"C:\old\hunt.json", "repeat_count": 1},
            {"plan_path": r"C:\old\raid.json", "repeat_count": 4},
        ],
        group_id=AUTO_RUN_PROFILE_GROUP_ID,
        repeat_count=1,
    )
    existing = AppConfig(
        player=PlayerConfig(
            auto_run_enabled=False,
            plan_sequence_groups=[stale_auto_group],
            active_plan_sequence_group_id=AUTO_RUN_PROFILE_GROUP_ID,
            auto_run_profile_version="auto_hunt_raid_factory_v5",
        ),
        ui=UIConfig(app_name="PC Local", window_mode="player"),
        arduino=ArduinoConfig(com_port="COM3", enabled=True),
    )
    manager = ConfigManager()
    config_path.write_text(
        json.dumps(manager._config_to_dict(existing), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    monkeypatch.setattr(config_module, "CONFIG_FILE", config_path)
    monkeypatch.setattr(config_module, "DATA_DIR", data_dir)
    monkeypatch.setattr(config_module, "AUTO_RUN_PROFILE_FORCE_APP_VERSION", APP_VERSION)
    manager._config = None

    loaded = manager.load()

    assert loaded.ui.app_name == "PC Local"
    assert loaded.ui.window_mode == "player"
    assert loaded.arduino.com_port == "COM3"
    assert loaded.arduino.enabled is True
    assert loaded.player.auto_run_enabled is True
    assert loaded.player.auto_run_profile_version == AUTO_RUN_PROFILE_VERSION
    assert loaded.player.active_plan_sequence_group_id == AUTO_RUN_PROFILE_GROUP_ID
    assert loaded.player.plan_sequence_groups[0]["repeat_count"] == AUTO_RUN_PROFILE_GROUP_REPEAT
    assert [Path(entry["plan_path"]).name for entry in loaded.player.plan_sequence_groups[0]["entries"]] == [
        file_name for file_name, _repeat in AUTO_RUN_PROFILE_PLANS
    ]
    assert [entry["repeat_count"] for entry in loaded.player.plan_sequence_groups[0]["entries"]] == [
        repeat for _file_name, repeat in AUTO_RUN_PROFILE_PLANS
    ]
    assert loaded.player.plan_sequence_repeats == [
        repeat
        for _ in range(AUTO_RUN_PROFILE_GROUP_REPEAT)
        for _file_name, repeat in AUTO_RUN_PROFILE_PLANS
    ]


def test_packaged_auto_hunt_raid_default_keeps_raid_at_five_repeats():
    plan_repeats = dict(AUTO_RUN_PROFILE_PLANS)

    assert plan_repeats["plan_20260605_123819.json"] == 5


def test_missing_config_load_seeds_packaged_defaults(monkeypatch, tmp_path):
    config_path = tmp_path / "missing_config.json"
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "plans").mkdir()
    manager = ConfigManager()
    monkeypatch.setattr(config_module, "CONFIG_FILE", config_path)
    monkeypatch.setattr(config_module, "DATA_DIR", data_dir)
    manager._config = None

    loaded = manager.load()

    assert loaded.player.auto_run_enabled is True
    assert loaded.player.auto_run_profile_version == AUTO_RUN_PROFILE_VERSION
    assert loaded.player.active_plan_sequence_group_id == AUTO_RUN_PROFILE_GROUP_ID
    assert len(loaded.player.plan_sequence_groups) == len(AUTO_RUN_PROFILE_GROUPS)


def test_packaged_ui_branding_migrates_legacy_random_name_to_fixed_korean_brand():
    config = AppConfig(
        ui=UIConfig(
            app_name="작업도우미",
            random_name_mode=True,
            random_name_alias="총무 관리",
        )
    )

    ConfigManager()._apply_packaged_ui_branding(config)

    assert config.ui.app_name == "업무지원도구"
    assert config.ui.random_name_mode is False
    assert config.ui.random_name_alias == ""
    assert config.ui.branding_profile_version == BRANDING_PROFILE_VERSION


def test_packaged_ui_branding_preserves_user_custom_fixed_name():
    config = AppConfig(
        ui=UIConfig(
            app_name="회사전용도구",
            random_name_mode=False,
            random_name_alias="",
        )
    )

    ConfigManager()._apply_packaged_ui_branding(config)

    assert config.ui.app_name == "회사전용도구"
    assert config.ui.random_name_mode is False
    assert config.ui.branding_profile_version == BRANDING_PROFILE_VERSION
