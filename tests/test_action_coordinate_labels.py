"""Display-only coordinate names must never rewrite saved automation data."""

import json
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import Mock

import pytest

from src.analyzer.automation_models import AutomationPlan, AutomationRule
from src.database.models import Action, Sequence
from src.player.rule_executor import RuleExecutor
from src.ui import player_view
from src.ui.analyzer_view import ImageCropDialog


DEFAULT_NAME = "\ub354\ube14\ud074\ub9ad (1301, 530)"


def make_action(kind, description=DEFAULT_NAME, **fields):
    if kind == "plan":
        return AutomationRule(action_type="double_click", description=description,
            action_x=421, action_y=474, **fields)
    return Action(action_type="double_click", description=description, x=421, y=474, **fields)


@pytest.mark.parametrize("kind", ["plan", "sequence"])
@pytest.mark.parametrize("description", [
    DEFAULT_NAME, "\ud074\ub9ad (0, 0)", "\uc6b0\ud074\ub9ad (-100, 530)",
])
def test_only_redundant_recorded_coordinate_names_are_hidden(kind, description):
    action = make_action(kind, description)
    before = action.to_dict()
    assert player_view._display_action_description(action) == ""
    assert action.to_dict() == before


@pytest.mark.parametrize("description", [
    "", "\ud638\ubc15", "Confirm (1301, 530)", DEFAULT_NAME + " confirm",
    "Custom " + DEFAULT_NAME, "\ud074\ub9ad (not coordinates)",
])
def test_user_names_are_preserved_exactly(description):
    action = make_action("plan", description)
    assert player_view._display_action_description(action) == description


def test_names_on_other_action_types_or_missing_live_coordinates_are_preserved():
    action = make_action("plan")
    action.action_type = "hotkey"
    assert player_view._display_action_description(action) == DEFAULT_NAME
    action.action_type = "double_click"
    action.action_x = None
    assert player_view._display_action_description(action) == DEFAULT_NAME


@pytest.mark.parametrize("kind", ["plan", "sequence"])
@pytest.mark.parametrize("compact", [False, True])
def test_existing_rows_and_new_labels_use_live_coordinates_without_recreation(kind, compact):
    action = make_action(kind)
    before = action.to_dict()
    widgets = {key: Mock() for key in ("widget", "type_label", "name_label", "detail_label")}
    if kind == "plan":
        owner = object.__new__(player_view.PlanDetailDialog)
        owner._plan = AutomationPlan(name="QA", initial_rules=[action])
        owner._rule_widgets = {action.rule_id: widgets}
        owner._compact_rule_rows = compact
        owner._selected_rule = None
        owner._active_partial_rule_id = None
        owner._update_rule_buttons = Mock()
        assert owner._update_rule_row_in_place(action)
        fresh_text = owner._compact_rule_label_text(action)
        assert DEFAULT_NAME not in owner._rule_display_text(action)
        coordinate = "(421, 474)"
    else:
        owner = object.__new__(player_view.SequenceDetailDialog)
        owner._sequence = Sequence(name="QA", actions=[action])
        owner._action_widgets = {action.action_id: widgets}
        owner._compact_action_rows = compact
        owner._selected_action = None
        owner._update_action_buttons = Mock()
        assert owner._update_compact_action_row(action)
        fresh_text = owner._compact_action_label_text(action)
        coordinate = "(421,474)" if compact else "(421, 474)"
    assert "1301" not in fresh_text and "530" not in fresh_text
    current = widgets["name_label"].configure.call_args.kwargs["text"]
    if compact:
        assert current == fresh_text
    else:
        assert current == ""
        current = widgets["detail_label"].configure.call_args.kwargs["text"]
    assert coordinate in current
    widgets["widget"].destroy.assert_not_called()
    assert action.to_dict() == before


def test_search_region_save_reload_keeps_original_name_but_displays_only_new_coordinates(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    original_file = root / "data/plans/plan_20260118_174859.json"
    original_bytes = original_file.read_bytes()
    raw = json.loads(original_bytes)["initial_rules"][12]["children"][1]
    rule = AutomationRule.from_dict(raw, root / "data/templates")
    assert rule.description == DEFAULT_NAME
    editor = SimpleNamespace(_rule=rule, _refresh_rule_setting_controls=Mock(), _on_search_radius_change=Mock())
    editor._normalize_search_region_value = ImageCropDialog._normalize_search_region_value
    editor._set_rule_search_center = MethodType(ImageCropDialog._set_rule_search_center, editor)
    editor._invoke_image_callback = MethodType(ImageCropDialog._invoke_image_callback, editor)
    assert ImageCropDialog._apply_search_region_to_rule(editor, [600, 401, 800, 601])
    assert RuleExecutor._image_search_region_for_rule(None, rule) == [600, 401, 800, 601]
    assert (rule.action_x, rule.action_y) == (700, 501)
    plan = AutomationPlan(name="Coordinate display QA", initial_rules=[rule])
    form = SimpleNamespace(_plan=plan, _name_entry=SimpleNamespace(get=lambda: ""), _modified=True)
    monkeypatch.setattr(player_view, "PLANS_DIR", tmp_path)
    assert player_view.PlanDetailDialog._save_plan(form, show_message=False)
    saved = json.loads((tmp_path / f"{plan.plan_id}.json").read_text(encoding="utf-8"))
    loaded = AutomationPlan.from_dict(saved, root / "data/templates")
    assert loaded.to_dict() == plan.to_dict()
    assert loaded.initial_rules[0].description == DEFAULT_NAME
    assert loaded.initial_rules[0].search_region == [600, 401, 800, 601]
    assert player_view._display_action_description(loaded.initial_rules[0]) == ""
    assert original_file.read_bytes() == original_bytes
