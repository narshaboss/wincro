import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from src.analyzer.automation_models import AutomationPlan
from src.player.rule_executor import RuleExecutor
from src.utils.auto_list import AUTO_LIST_MODE_UNTIL_EXHAUSTED


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = PROJECT_ROOT / "data" / "plans" / "plan_20260727_105458.json"


def _load_plan():
    raw = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    return raw, AutomationPlan.from_dict(raw)


def _walk_rules(rules, prefix=""):
    for index, rule in enumerate(rules, 1):
        step = f"{prefix}.{index}" if prefix else str(index)
        yield step, rule
        yield from _walk_rules(getattr(rule, "children", []) or [], step)


def _auto_list_rule(plan):
    matches = [
        (step, rule)
        for step, rule in _walk_rules(plan.initial_rules)
        if rule.action_type == "auto_list"
    ]
    assert len(matches) == 1
    assert matches[0][0] == "4.3"
    return matches[0][1]


def test_yeonmaseok_plan_tree_and_assets_are_distribution_safe():
    raw, plan = _load_plan()

    assert raw["name"] == "연마석공장"
    assert len(plan.initial_rules) == 4

    seen_ids = set()
    for _step, rule in _walk_rules(plan.initial_rules):
        assert rule.rule_id
        assert rule.rule_id not in seen_ids
        seen_ids.add(rule.rule_id)

        for image_path in [
            getattr(rule, "target_image", None),
            getattr(rule, "trigger_image", None),
            getattr(rule, "auto_list_repeat_confirm_image", None),
        ]:
            if image_path:
                assert Path(image_path).is_file(), image_path

        for image_path in getattr(rule, "target_images", []) or []:
            assert Path(image_path).is_file(), image_path

        for child in getattr(rule, "children", []) or []:
            assert child.parent_id == rule.rule_id

    raw_text = PLAN_PATH.read_text(encoding="utf-8")
    assert str(PROJECT_ROOT) not in raw_text


def test_yeonmaseok_auto_list_configuration_is_complete_and_portable():
    raw, plan = _load_plan()
    rule = _auto_list_rule(plan)
    config = rule.auto_list_config

    assert config["processing_mode"] == AUTO_LIST_MODE_UNTIL_EXHAUSTED
    assert config["reselect_each_cycle"] is True
    assert config["skip_missing_item"] is True
    assert config["max_value"] == 10
    assert config["min_value"] == 1
    assert config["item_timeout"] == 0.5
    assert config["quantity_region"] == [531, 559, 633, 573]
    assert config["status_region"] == [495, 522, 605, 544]
    assert config["item_search_region"] == [13, 181, 265, 706]
    assert len(config["items"]) == 9
    assert all(item["enabled"] for item in config["items"])
    assert all(item["search_region"] == config["item_search_region"] for item in config["items"])
    assert all(Path(item["image"]).is_file() for item in config["items"])

    saved_config = plan.to_dict()["initial_rules"][3]["children"][2]["auto_list_config"]
    raw_config = raw["initial_rules"][3]["children"][2]["auto_list_config"]
    assert saved_config == raw_config
    assert all(not Path(item["image"]).is_absolute() for item in saved_config["items"])


def test_yeonmaseok_auto_list_all_missing_finishes_without_running_children(monkeypatch):
    _raw, plan = _load_plan()
    rule = _auto_list_rule(plan)
    executor = RuleExecutor()
    child_runs = []

    monkeypatch.setattr(executor, "_auto_list_wait_for_item", lambda *_args: None)
    monkeypatch.setattr(
        executor,
        "_execute_child_rules_for_auto_list",
        lambda *_args: child_runs.append("unexpected") or None,
    )

    result = executor._execute_fixed_action(rule, datetime.now())

    assert result.success is True
    assert "총 0" in result.message
    assert child_runs == []
    descendant_ids = {
        child.rule_id
        for _step, child in _walk_rules(rule.children)
    }
    assert descendant_ids <= executor._child_rules_executed_with_parent


def test_yeonmaseok_auto_list_runs_each_processing_child_once_per_batch(monkeypatch):
    _raw, plan = _load_plan()
    rule = _auto_list_rule(plan)
    executor = RuleExecutor()
    executed = []

    def execute_rule(active_rule, *args, **kwargs):
        executed.append(active_rule.rule_id)
        return executor._make_result(active_rule, True, "ok", datetime.now())

    monkeypatch.setattr(executor, "_execute_rule_with_retry", execute_rule)
    monkeypatch.setattr(executor, "_wait_after_rule_result", lambda *_args: None)

    result = executor._execute_child_rules_for_auto_list(
        rule,
        datetime.now(),
        10,
        rule.auto_list_config["items"][0],
    )

    assert result is None
    expected = [child.rule_id for _step, child in _walk_rules(rule.children)]
    assert executed == expected


def test_yeonmaseok_auto_list_processes_one_batch_then_continues_all_items(monkeypatch):
    _raw, plan = _load_plan()
    rule = _auto_list_rule(plan)
    executor = RuleExecutor()
    items = rule.auto_list_config["items"]
    searches = []
    entered = []
    child_runs = []
    first_item_searches = 0

    def find_item(item, timeout):
        nonlocal first_item_searches
        searches.append((item["name"], timeout, item["search_region"]))
        if item["name"] == items[0]["name"]:
            first_item_searches += 1
            if first_item_searches == 1:
                return (40, 200, 0.99)
        return None

    monkeypatch.setattr(executor, "_auto_list_wait_for_item", find_item)
    monkeypatch.setattr(
        executor,
        "_execute_click_at",
        lambda *_args, **_kwargs: SimpleNamespace(success=True),
    )
    monkeypatch.setattr(executor, "_auto_list_wait", lambda *_args: True)
    monkeypatch.setattr(
        executor,
        "_auto_list_input_value",
        lambda region, value: entered.append((region, value)) or True,
    )
    monkeypatch.setattr(
        executor,
        "_auto_list_colour_state",
        lambda _config: SimpleNamespace(
            state="available",
            is_available=True,
            red_pixels=0,
            green_pixels=10,
        ),
    )
    monkeypatch.setattr(
        executor,
        "_execute_child_rules_for_auto_list",
        lambda _rule, _started, accepted, item: child_runs.append(
            (accepted, item["name"])
        )
        or None,
    )

    result = executor._execute_fixed_action(rule, datetime.now())

    assert result.success is True
    assert "총 10" in result.message
    assert entered == [([531, 559, 633, 573], 10)]
    assert child_runs == [(10, items[0]["name"])]
    assert [name for name, _timeout, _region in searches] == [
        items[0]["name"],
        items[0]["name"],
        *[item["name"] for item in items[1:]],
    ]
    assert all(timeout == 0.5 for _name, timeout, _region in searches)
    assert all(region == [13, 181, 265, 706] for _name, _timeout, region in searches)


def test_yeonmaseok_extraction_selector_uses_registered_list_region():
    _raw, plan = _load_plan()
    rule = _auto_list_rule(plan)
    selector = next(
        child
        for child in rule.children
        if getattr(child, "repeat_from_auto_list_quantity", False)
    )

    assert selector.action_type == "click"
    assert selector.description == "제작수량만큼 추출 선택"
    assert selector.auto_list_repeat_confirm_image
    assert Path(selector.auto_list_repeat_confirm_image).is_file()
    assert selector.auto_list_repeat_confirm_region == [18, 181, 257, 702]
    assert selector.auto_list_repeat_confirm_confidence == 0.9
    assert all(item["search_region"] == [13, 181, 265, 706] for item in rule.auto_list_config["items"])


def test_yeonmaseok_partial_extraction_uses_all_registered_regions_and_continues(monkeypatch):
    _raw, plan = _load_plan()
    parent = _auto_list_rule(plan)
    selector_index = next(
        index
        for index, child in enumerate(parent.children)
        if getattr(child, "repeat_from_auto_list_quantity", False)
    )
    selector = parent.children[selector_index]
    next_rule = parent.children[selector_index + 1]
    executor = RuleExecutor()
    executor._current_plan = SimpleNamespace(_original_initial_rules=plan.initial_rules)
    first_registered_image = parent.auto_list_config["items"][0]["image"]
    searched = []
    clicked = []

    def find_all(image_path, *_args, **kwargs):
        if str(image_path) == str(selector.auto_list_repeat_confirm_image):
            return [(220, y, 0.99) for _x, y in clicked]
        searched.append(
            (
                str(image_path),
                kwargs.get("search_region"),
                kwargs.get("search_radius"),
            )
        )
        if str(image_path) == str(first_registered_image):
            return [(40, 200, 0.99)]
        return []

    def click_at(active_rule, _kind, x, y, started, **_kwargs):
        clicked.append((x, y))
        return executor._make_result(active_rule, True, "click complete", started)

    monkeypatch.setattr(executor, "_find_all_images_on_screen", find_all)
    monkeypatch.setattr(executor, "_execute_click_at", click_at)
    monkeypatch.setattr(
        executor,
        "_find_image_on_screen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("registered selector must not enter generic next-screen wait")
        ),
    )

    result = executor._execute_rule_with_retry(
        selector,
        next_target_images=[next_rule.target_image],
        next_rule=next_rule,
    )

    assert result.success is True
    assert clicked == [(40, 200)]
    assert len(searched) == 9
    assert {image for image, _region, _radius in searched} == {
        item["image"] for item in parent.auto_list_config["items"]
    }
    assert all(region == [13, 181, 265, 706] for _image, region, _radius in searched)
    assert all(radius == 0 for _image, _region, radius in searched)
