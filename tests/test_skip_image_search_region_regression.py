from pathlib import Path
from datetime import datetime

from src.analyzer.automation_models import AutomationRule
from src.database.models import Action
from src.player.rule_executor import RuleExecutor


ROOT = Path(__file__).resolve().parents[1]
PLAYER_VIEW = ROOT / "src" / "ui" / "player_view.py"
RULE_EXECUTOR = ROOT / "src" / "player" / "rule_executor.py"
MONITORING_EDITOR = ROOT / "src" / "ui" / "monitoring_editor.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_legacy_action_persists_skip_image_search_region():
    action = Action.from_dict(
        {
            "action_type": "click",
            "target_image": "data/templates/sample.png",
            "skip_on_not_found": True,
            "search_radius": 44,
            "search_region": [10, 20, 110, 120],
        }
    )

    assert action.skip_on_not_found is True
    assert action.search_radius == 44
    assert action.search_region == [10, 20, 110, 120]
    assert action.to_dict()["search_region"] == [10, 20, 110, 120]


def test_search_region_normalizer_is_strict_for_explicit_regions():
    region, explicit = RuleExecutor._normalize_search_region([100, 80, 20, 10], 200, 200)
    assert explicit is True
    assert region == [20, 10, 100, 80]

    region, explicit = RuleExecutor._normalize_search_region([10, 20, 10, 40], 200, 200)
    assert explicit is True
    assert region is None

    region, explicit = RuleExecutor._normalize_search_region(None, 200, 200)
    assert explicit is False
    assert region is None


def test_rule_executor_does_not_fullscreen_fallback_when_region_is_requested():
    text = _read(RULE_EXECUTOR)

    assert "def _image_search_region_for_rule(self, rule: AutomationRule) -> Optional[list]:" in text
    assert "normalized_region, explicit_region = self._normalize_search_region(search_region, w, h)" in text
    assert "if explicit_region:\n                if normalized_region is None:\n                    return None" in text
    assert "normalized_region, explicit_region = self._normalize_search_region(search_region, screen_w, screen_h)" in text
    assert "if explicit_region:\n                if normalized_region is None:\n                    return []" in text
    assert "if all_target_images and rule_search_region is not None and not self._point_in_search_region" in text
    assert "search_radius=0 if has_rule_search_region else" in text


def test_playback_and_monitor_conversions_keep_search_region():
    player_text = _read(PLAYER_VIEW)
    monitoring_text = _read(MONITORING_EDITOR)

    assert 'monitor_action["search_region"] = getattr(action, \'search_region\', None)' in player_text
    assert 'monitor_action["search_region"] = getattr(clipboard, \'search_region\', None)' in player_text
    assert "rule=action" in player_text
    assert 'search_region=getattr(action, "search_region", None),' in player_text
    assert 'ma["search_region"] = getattr(act, \'search_region\', None)' in monitoring_text


def test_next_screen_wait_checks_multi_target_images(monkeypatch):
    executor = RuleExecutor()
    current_rule = AutomationRule(
        action_type="click",
        action_x=10,
        action_y=20,
        description="current click",
    )
    next_rule = AutomationRule(
        action_type="click",
        target_image="primary.png",
        target_images=["alt.png"],
        skip_on_not_found=True,
        wait_after=0.5,
        description="next multi image",
    )
    searched = []

    def fake_execute_rule(rule, step_num=""):
        return executor._make_result(rule, True, "click ok", datetime.now())

    def fake_find_image(image_path, confidence, search_region=None):
        searched.append(image_path)
        if image_path == "alt.png":
            return (100, 200, 0.9)
        return None

    monkeypatch.setattr("src.player.rule_executor.time.sleep", lambda _seconds: None)
    monkeypatch.setattr(executor, "_execute_rule", fake_execute_rule)
    monkeypatch.setattr(executor, "_find_image_on_screen", fake_find_image)

    result = executor._execute_rule_with_retry(
        current_rule,
        RuleExecutor._target_images_for_rule(next_rule),
        max_retries=1,
        next_rule=next_rule,
        step_num="1",
    )

    assert result.success is True
    assert searched == ["primary.png", "alt.png"]
