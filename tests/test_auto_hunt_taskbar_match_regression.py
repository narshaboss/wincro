from copy import copy
from pathlib import Path
import threading

import cv2
import numpy as np

from src.analyzer.automation_models import AutomationPlan
from src.player import rule_executor as runtime
from src.utils.json_utils import load_json_file


def load_action():
    root = Path(__file__).resolve().parents[1]
    plan = AutomationPlan.from_dict(load_json_file(root / "data/plans/plan_20260118_174859.json"),
                                    templates_dir=root / "data/templates")
    return plan.initial_rules[3].children[1]


def executor():
    instance = object.__new__(runtime.RuleExecutor)
    instance._stop_event = threading.Event()
    instance._current_step_num = "4-2"
    return instance


def test_taskbar_action_keeps_version_300_threshold_and_other_search_settings():
    rule = load_action()
    assert rule.confidence == 0.8081818181818181
    assert rule.search_region == [2, 990, 1919, 1079]
    assert rule.verify_image_color and rule.verify_image_brightness
    assert len(runtime.RuleExecutor._target_images_for_rule(rule)) == 2


def test_real_opencv_match_reproduces_302_miss_and_restored_300_detection(monkeypatch):
    rule = load_action()
    template = cv2.imdecode(np.fromfile(rule.target_image, np.uint8), cv2.IMREAD_COLOR)
    noise = np.random.default_rng(42).normal(0, 1, template.shape[:2])[..., None]
    candidate = np.clip(template.astype(float) + noise * 20, 0, 255).astype(np.uint8)
    frame = np.full((1080, 1920, 3), 128, dtype=np.uint8)
    height, width = template.shape[:2]
    frame[1020:1020 + height, 900:900 + width] = candidate
    monkeypatch.setattr(runtime, "_grab_screen_bgr", lambda: frame)
    engine = executor()
    images = engine._target_images_for_rule(rule)
    old_302 = copy(rule)
    old_302.confidence = 0.9056818181818181
    assert engine._find_rule_image_click_target(old_302, images) is None
    found = engine._find_rule_image_click_target(rule, images)
    assert found is not None
    assert rule.confidence <= found["confidence"] < old_302.confidence
    assert (found["x"], found["y"]) == (900 + width // 2, 1020 + height // 2)


def test_restored_threshold_does_not_match_a_blank_frame(monkeypatch):
    rule = load_action()
    frame = np.full((1080, 1920, 3), 128, dtype=np.uint8)
    monkeypatch.setattr(runtime, "_grab_screen_bgr", lambda: frame)
    engine = executor()
    assert engine._find_rule_image_click_target(rule, engine._target_images_for_rule(rule)) is None


def test_restored_threshold_does_not_search_outside_the_configured_region(monkeypatch):
    rule = load_action()
    template = cv2.imdecode(np.fromfile(rule.target_image, np.uint8), cv2.IMREAD_COLOR)
    frame = np.full((1080, 1920, 3), 128, dtype=np.uint8)
    height, width = template.shape[:2]
    frame[200:200 + height, 900:900 + width] = template
    monkeypatch.setattr(runtime, "_grab_screen_bgr", lambda: frame)
    engine = executor()
    assert engine._find_rule_image_click_target(rule, engine._target_images_for_rule(rule)) is None
