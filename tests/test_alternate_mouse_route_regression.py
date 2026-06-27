from pathlib import Path

from src.analyzer.automation_models import AutomationRule
from src.database.models import Action


ROOT = Path(__file__).resolve().parents[1]
PLAYER_VIEW = ROOT / "src" / "ui" / "player_view.py"
RULE_EXECUTOR = ROOT / "src" / "player" / "rule_executor.py"
CONSTANTS = ROOT / "src" / "ui" / "constants.py"
MONITORING_EDITOR = ROOT / "src" / "ui" / "monitoring_editor.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_alternate_mouse_route_persists_for_rule_and_legacy_action(tmp_path):
    rule = AutomationRule(
        action_type="click",
        target_image=str(tmp_path / "target.png"),
        alternate_mouse_route=True,
    )
    saved = rule.to_dict()

    assert saved["alternate_mouse_route"] is True
    assert AutomationRule.from_dict(saved, templates_dir=tmp_path).alternate_mouse_route is True

    action = Action.from_dict(
        {
            "action_type": "click",
            "target_image": str(tmp_path / "target.png"),
            "alternate_mouse_route": True,
        }
    )
    assert action.alternate_mouse_route is True
    assert action.to_dict()["alternate_mouse_route"] is True


def test_player_context_menu_exposes_alternate_mouse_route_for_image_clicks():
    text = _read(PLAYER_VIEW)

    assert "마우스 이동경로 변경" in text
    assert "def _toggle_rule_alternate_mouse_route(self, rule: AutomationRule):" in text
    assert "def _toggle_action_alternate_mouse_route(self, action: Action):" in text
    assert 'getattr(r, "target_image", None)' in text
    assert 'getattr(a, "target_image", None)' in text
    assert 'details.append("이동경로 변경")' in text


def test_rule_executor_uses_alternate_route_for_image_and_monitor_clicks():
    text = _read(RULE_EXECUTOR)

    assert "def _build_alternate_mouse_route(self, target_x: int, target_y: int)" in text
    assert "def _move_mouse_to(self, x: int, y: int, *, duration: Optional[float] = None, alternate_route: bool = False)" in text
    assert "raw_points = [" in text
    assert "(approach_x, detour_y)" in text
    assert "(target_x, approach_y)" in text
    assert "segment_duration = duration / max(len(points), 1)" in text
    assert 'alternate_route = bool(getattr(rule, "alternate_mouse_route", False) and image_click)' in text
    assert "image_click=bool(all_target_images)" in text
    assert "self._move_mouse_to(click_x, click_y, alternate_route=alternate_route)" in text
    assert "alternate_route = bool(monitor_action.get('alternate_mouse_route', False))" in text
    assert "self._move_mouse_to(x, y, alternate_route=True)" in text


def test_monitor_action_conversions_keep_alternate_mouse_route():
    player_text = _read(PLAYER_VIEW)
    constants_text = _read(CONSTANTS)
    monitoring_text = _read(MONITORING_EDITOR)

    assert 'monitor_action["alternate_mouse_route"] = getattr(action, \'alternate_mouse_route\', False)' in player_text
    assert "'alternate_mouse_route'" in constants_text
    assert '("alternate_mouse_route", False)' in monitoring_text
