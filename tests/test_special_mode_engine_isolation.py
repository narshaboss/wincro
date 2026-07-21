import ast
import copy
import hashlib
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.analyzer.automation_models import AutomationPlan, GameModeConfig
from src.player.special_mode.akgui_v2 import AkguiV2CoordinateRunner
from src.player.special_mode.engines import get_special_mode_engine
from src.special_mode_profiles import (
    AKGUI_V2_PROFILE,
    WONGAK_LEGACY_PROFILE,
    get_special_mode_profile,
    normalize_special_mode_profile,
)
from src.ui.player_view import GameModeDialog


ROOT = Path(__file__).resolve().parents[1]
PLAYER_VIEW = ROOT / "src" / "ui" / "player_view.py"
RULE_EXECUTOR = ROOT / "src" / "player" / "rule_executor.py"
ACTION_PLAYER = ROOT / "src" / "player" / "action_player.py"
AKGUI_ENGINE = ROOT / "src" / "player" / "special_mode" / "akgui_v2.py"
ENGINE_OWNERSHIP = (
    ROOT / "src" / "player" / "special_mode" / "ENGINE_OWNERSHIP.json"
)
WONGAK_PLAN = ROOT / "data" / "plans" / "plan_20260205_000742.json"
AKGUI_PLAN = ROOT / "data" / "plans" / "plan_20260708_121550.json"
PLANS_DIR = ROOT / "data" / "plans"


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _make_map_view(profile_id: str, rule_id: str):
    view = GameModeDialog.__new__(GameModeDialog)
    view._config = SimpleNamespace(
        engine_profile=profile_id,
        name="isolation-test",
        waypoints=[
            [
                10,
                10,
                "segment",
                {
                    "route_starts": [{"x": 1, "y": 1}],
                    "route_ends": [{"x": 10, "y": 10}],
                },
            ]
        ],
    )
    view._config_rule_id = rule_id
    return view


def test_profile_ids_are_distinct_and_unknown_values_fail_closed():
    wongak = get_special_mode_profile(WONGAK_LEGACY_PROFILE)
    akgui = get_special_mode_profile(AKGUI_V2_PROFILE)

    assert wongak.profile_id != akgui.profile_id
    assert wongak.map_namespace != akgui.map_namespace
    assert wongak.behavior_version != akgui.behavior_version
    with pytest.raises(ValueError):
        normalize_special_mode_profile("unknown-engine")


def test_dispatcher_invokes_only_the_selected_engine():
    class Host:
        def __init__(self, profile_id):
            self.calls = []
            self._config = SimpleNamespace(engine_profile=profile_id)

        def _run_wongak_legacy_coordinate_loop(self):
            self.calls.append(WONGAK_LEGACY_PROFILE)

        def _run_akgui_v2_coordinate_loop(self):
            self.calls.append(AKGUI_V2_PROFILE)

    wongak_host = Host(WONGAK_LEGACY_PROFILE)
    get_special_mode_engine(WONGAK_LEGACY_PROFILE).run(wongak_host)
    assert wongak_host.calls == [WONGAK_LEGACY_PROFILE]

    akgui_host = Host(AKGUI_V2_PROFILE)
    get_special_mode_engine(AKGUI_V2_PROFILE).run(akgui_host)
    assert akgui_host.calls == [AKGUI_V2_PROFILE]


def test_dispatcher_rejects_engine_profile_mismatch():
    host = SimpleNamespace(
        _config=SimpleNamespace(engine_profile=WONGAK_LEGACY_PROFILE),
        _run_akgui_v2_coordinate_loop=lambda: None,
    )

    with pytest.raises(RuntimeError, match="engine/profile mismatch"):
        get_special_mode_engine(AKGUI_V2_PROFILE).run(host)


def test_game_mode_config_round_trip_preserves_explicit_profile():
    original = GameModeConfig(
        enabled=True,
        engine_profile=AKGUI_V2_PROFILE,
        waypoints=[[10, 10, "악귀문1굴", {"route_ends": [{"x": 10, "y": 10}]}]],
    )

    restored = GameModeConfig.from_dict(original.to_dict())

    assert restored.engine_profile == AKGUI_V2_PROFILE
    assert restored.to_dict()["engine_profile"] == AKGUI_V2_PROFILE


def test_shipped_plans_have_explicit_non_overlapping_profiles():
    wongak = AutomationPlan.from_dict(_load_json(WONGAK_PLAN))
    akgui = AutomationPlan.from_dict(_load_json(AKGUI_PLAN))

    assert wongak.game_modes
    assert akgui.game_modes
    assert {
        config.engine_profile for config in wongak.game_modes.values()
    } == {WONGAK_LEGACY_PROFILE}
    assert {
        config.engine_profile for config in akgui.game_modes.values()
    } == {AKGUI_V2_PROFILE}


def test_every_shipped_special_mode_has_an_explicit_known_profile():
    untagged = []
    unknown = []
    for path in sorted(PLANS_DIR.glob("*.json")):
        data = _load_json(path)
        for rule_id, config in (data.get("game_modes") or {}).items():
            profile_id = config.get("engine_profile")
            if not profile_id:
                untagged.append((path.name, rule_id))
                continue
            try:
                normalize_special_mode_profile(profile_id)
            except ValueError:
                unknown.append((path.name, rule_id, profile_id))

    assert untagged == []
    assert unknown == []


def test_legacy_untagged_plans_migrate_once_without_runtime_name_matching():
    akgui_data = copy.deepcopy(_load_json(AKGUI_PLAN))
    for config in akgui_data["game_modes"].values():
        config.pop("engine_profile", None)
    migrated_akgui = AutomationPlan.from_dict(akgui_data)
    assert {
        config.engine_profile for config in migrated_akgui.game_modes.values()
    } == {AKGUI_V2_PROFILE}

    wongak_data = copy.deepcopy(_load_json(WONGAK_PLAN))
    for config in wongak_data["game_modes"].values():
        config.pop("engine_profile", None)
    wongak_data["name"] = "이름을 바꿔도 프로필 추론에 사용하지 않음"
    migrated_wongak = AutomationPlan.from_dict(wongak_data)
    assert {
        config.engine_profile for config in migrated_wongak.game_modes.values()
    } == {WONGAK_LEGACY_PROFILE}


def test_profile_and_rule_map_namespaces_cannot_collide():
    wongak = _make_map_view(WONGAK_LEGACY_PROFILE, "rule_sameid")
    akgui = _make_map_view(AKGUI_V2_PROFILE, "rule_sameid")
    other_rule = _make_map_view(AKGUI_V2_PROFILE, "rule_otherid")

    wongak_path = Path(wongak._get_segment_map_name(0))
    akgui_path = Path(akgui._get_segment_map_name(0))
    other_rule_path = Path(other_rule._get_segment_map_name(0))

    assert WONGAK_LEGACY_PROFILE in wongak_path.parts
    assert AKGUI_V2_PROFILE in akgui_path.parts
    assert wongak_path != akgui_path
    assert akgui_path.parent != other_rule_path.parent


def test_akgui_map_file_cannot_escape_to_wongak_namespace():
    view = _make_map_view(AKGUI_V2_PROFILE, "rule_akguitest")
    foreign = (
        ROOT
        / "data"
        / "maps"
        / WONGAK_LEGACY_PROFILE
        / "foreign-rule"
        / "akguitest_00_segment_map.json"
    )
    view._config.waypoints[0][3]["map_file"] = str(foreign)

    resolved = Path(view._resolve_segment_map_load_path(0))

    assert resolved == Path(view._get_segment_map_name(0))
    assert AKGUI_V2_PROFILE in resolved.parts


def test_akgui_map_file_cannot_escape_to_another_rule_namespace(monkeypatch):
    view = _make_map_view(AKGUI_V2_PROFILE, "rule_akguitest")
    foreign = (
        ROOT
        / "data"
        / "maps"
        / AKGUI_V2_PROFILE
        / "foreign-rule"
        / "akguitest_00_segment_map.json"
    )
    own = Path(view._get_segment_map_name(0))
    monkeypatch.setattr(
        "os.path.exists",
        lambda path: Path(path) == foreign,
    )
    view._config.waypoints[0][3]["map_file"] = str(foreign)

    resolved = Path(view._resolve_segment_map_load_path(0))

    assert resolved == own
    assert resolved.parent.name == "akguitest"


def test_akgui_source_has_no_wongak_runtime_dependency():
    tree = ast.parse(AKGUI_ENGINE.read_text(encoding="utf-8"))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    called_attributes = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
    }

    assert not any(module.endswith("player_view") for module in imported_modules)
    assert "_run_wongak_legacy_coordinate_loop" not in called_attributes
    assert "_switch_segment_map" not in called_attributes
    assert "_switch_akgui_v2_segment_map" in called_attributes


def test_rule_executor_coordinate_bypass_fails_before_legacy_engine_body():
    tree = ast.parse(RULE_EXECUTOR.read_text(encoding="utf-8-sig"))
    method = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "execute_game_mode_coordinate"
    )
    raise_line = next(
        node.lineno for node in method.body if isinstance(node, ast.Raise)
    )
    matcher_import_line = next(
        node.lineno
        for node in method.body
        if isinstance(node, ast.ImportFrom)
        and node.module == "utils.digit_templates"
    )

    assert raise_line < matcher_import_line


def test_action_player_rejects_direct_isolated_game_mode_execution():
    text = ACTION_PLAYER.read_text(encoding="utf-8-sig")
    start = text.index("    def play_automation_plan(")
    end = text.index("    def stop_automation_plan(", start)
    body = text[start:end]

    assert "_contains_isolated_game_mode" in body
    assert "GameModeDialog 프로필 디스패처" in body
    assert body.index("_contains_isolated_game_mode(plan.initial_rules)") < body.index(
        "self._current_automation_plan = plan"
    )
    assert "_contains_isolated_game_mode(plan.monitoring_rules)" in body


def test_player_dialog_dispatches_by_profile_not_plan_name_or_shape():
    text = PLAYER_VIEW.read_text(encoding="utf-8-sig")
    start = text.index("    def _run_selected_special_mode_engine(self):")
    end = text.index("    def _run_akgui_v2_coordinate_loop(self):", start)
    body = text[start:end]

    assert "engine_profile" in body
    assert "get_special_mode_engine" in body
    assert "plan.name" not in body
    assert "route_starts" not in body
    assert "waypoints" not in body


def test_shared_player_view_profile_references_are_boundary_only():
    tree = ast.parse(PLAYER_VIEW.read_text(encoding="utf-8-sig"))
    allowed_methods = {
        "__init__",
        "_apply_settings",
        "_build_engine_profile_ui",
        "_get_segment_map_name",
        "_on_engine_profile_selected",
        "_resolve_segment_map_load_path",
        "_run_selected_special_mode_engine",
        "_save_config",
        "_save_game_mode_defaults",
        "_switch_akgui_v2_segment_map",
        "_switch_segment_map",
        "_validate_engine_profile_config",
    }
    violations = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        profile_reference = any(
            (
                isinstance(child, ast.Attribute)
                and child.attr in {"engine_profile", "_original_engine_profile"}
            )
            or (
                isinstance(child, ast.Name)
                and child.id in {"AKGUI_V2_PROFILE", "WONGAK_LEGACY_PROFILE"}
            )
            for child in ast.walk(node)
        )
        if profile_reference and node.name not in allowed_methods:
            violations.append(node.name)

    assert violations == []


def test_akgui_validation_rejects_wongak_only_waypoint_policy():
    config = GameModeConfig(
        engine_profile=AKGUI_V2_PROFILE,
        waypoints=[
            [
                0,
                0,
                "invalid",
                {
                    "target_image": "boss.png",
                    "route_ends": [{"x": 0, "y": 0}],
                },
            ]
        ],
    )

    with pytest.raises(ValueError, match="원각 보스 이미지"):
        AkguiV2CoordinateRunner.validate_config(config)


def test_wongak_legacy_engine_matches_frozen_ownership_hash():
    ownership = _load_json(ENGINE_OWNERSHIP)
    text = PLAYER_VIEW.read_text(encoding="utf-8-sig")
    tree = ast.parse(text)
    dialog = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "GameModeDialog"
    )
    method = next(
        node
        for node in dialog.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_run_wongak_legacy_coordinate_loop"
    )
    source = ast.get_source_segment(text, method).replace("\r\n", "\n")
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()

    assert digest == ownership[WONGAK_LEGACY_PROFILE]["sha256"]


def test_akgui_dynamic_obstacles_never_promote_walls_during_normal_playback():
    class Map:
        def __init__(self):
            self.blocked = []

        def mark_blocked(self, x, y):
            self.blocked.append((x, y))

    game_map = Map()
    host = SimpleNamespace(
        _config=SimpleNamespace(engine_profile=AKGUI_V2_PROFILE),
        _stop_event=SimpleNamespace(),
        _game_map=game_map,
        _is_mapping=False,
        _is_mapping_test=False,
        _no_save_mode=False,
    )
    runner = AkguiV2CoordinateRunner(host)
    segment = SimpleNamespace(map_locked=False)

    for _ in range(runner.wall_promotion_failures + 3):
        runner._record_failed_move(segment, (10, 10), "right")

    assert game_map.blocked == []


def test_akgui_wall_promotion_is_confined_to_explicit_unlocked_mapping():
    class Map:
        def __init__(self):
            self.blocked = []

        def mark_blocked(self, x, y):
            self.blocked.append((x, y))

    class Host:
        def __init__(self):
            self._config = SimpleNamespace()
            self._config.engine_profile = AKGUI_V2_PROFILE
            self._stop_event = SimpleNamespace()
            self._game_map = Map()
            self._is_mapping = True
            self._is_mapping_test = False
            self._no_save_mode = False
            self.logs = []

        def _append_log(self, message):
            self.logs.append(message)

    host = Host()
    runner = AkguiV2CoordinateRunner(host)
    segment = SimpleNamespace(map_locked=False)

    for _ in range(runner.wall_promotion_failures):
        runner._record_failed_move(segment, (10, 10), "right")

    assert host._game_map.blocked == [(11, 10)]
    assert any("악귀문V2" in message for message in host.logs)


def test_akgui_state_machine_transitions_and_completes_without_wongak(monkeypatch):
    class Matcher:
        def has_all_templates(self):
            return True

    class Map:
        def is_blocked(self, x, y):
            return False

    class Host:
        def __init__(self):
            self._config = SimpleNamespace(
                engine_profile=AKGUI_V2_PROFILE,
                waypoints=[
                    [1, 0, "악귀문1굴", {"route_ends": [{"x": 1, "y": 0}]}],
                    [21, 20, "악귀문2굴", {"route_ends": [{"x": 21, "y": 20}]}],
                ],
                final_waypoint_idx=-1,
                analysis_interval=0.001,
            )
            self._stop_event = threading.Event()
            self._game_map = Map()
            self._map_pathfinder = None
            self._is_mapping = False
            self._is_mapping_test = False
            self._no_save_mode = True
            self._single_waypoint_mode = False
            self._key_press_count = 0
            self._coordinates = iter(
                [
                    (0, 0),
                    (1, 0),
                    (20, 20),
                    (20, 20),
                    (20, 20),
                    (21, 20),
                ]
            )
            self.switched = []
            self.directions = []
            self.logs = []
            self.completed = False
            self.stopped = []

        def _read_game_coordinates(self, matcher, *, stop_event=None):
            return next(self._coordinates)

        def _remember_runtime_coordinate(self, *args, **kwargs):
            return None

        def _switch_akgui_v2_segment_map(self, index):
            self.switched.append(index)
            return True

        def _press_direction_key(self, direction):
            self.directions.append(direction)

        def _append_log(self, message):
            self.logs.append(message)

        def _queue_normal_completion(self):
            self.completed = True

        def _request_stop_execution(self, reason, detail):
            self.stopped.append((reason, detail))
            self._stop_event.set()

    monkeypatch.setattr(
        "src.utils.digit_templates.get_digit_matcher",
        lambda: Matcher(),
    )
    host = Host()

    AkguiV2CoordinateRunner(host)._run_state_machine()

    assert host.completed is True
    assert host.stopped == []
    assert host.switched == [0, 1]
    assert host.directions == ["right", "right"]
    assert any("맵 전환" in message for message in host.logs)


def test_akgui_runner_rejects_foreign_profile():
    host = SimpleNamespace(
        _config=SimpleNamespace(engine_profile=WONGAK_LEGACY_PROFILE),
        _stop_event=threading.Event(),
    )

    with pytest.raises(RuntimeError, match="foreign profile"):
        AkguiV2CoordinateRunner(host)


def test_generic_map_switch_is_blocked_inside_akgui_runtime():
    view = GameModeDialog.__new__(GameModeDialog)
    view._active_engine_profile = AKGUI_V2_PROFILE

    with pytest.raises(RuntimeError, match="일반 맵 전환 호출이 차단"):
        view._switch_segment_map(0)


def test_engine_selector_is_built_before_shared_coordinate_controls():
    text = PLAYER_VIEW.read_text(encoding="utf-8-sig")
    start = text.index("    def _build_ui(self):")
    end = text.index("    def _build_coordinate_ui(self):", start)
    body = text[start:end]

    assert body.index("self._build_engine_profile_ui(main)") < body.index(
        "self._build_coordinate_ui()"
    )
    assert "selector.configure(state=\"disabled\")" in text
    assert "self._original_engine_profile = self._config.engine_profile" in text
