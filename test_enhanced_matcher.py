"""Regression tests for the enhanced template matcher."""

from __future__ import annotations

import os
import sys
import tempfile

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.analyzer.enhanced_matcher import EnhancedMatcher


def create_test_template():
    template = np.zeros((50, 50, 3), dtype=np.uint8)
    cv2.rectangle(template, (10, 10), (40, 40), (255, 255, 255), -1)
    cv2.circle(template, (25, 25), 10, (0, 0, 255), -1)

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        cv2.imwrite(f.name, template)
        return f.name, template


def create_test_screen(template, position, modifications=None):
    screen = np.zeros((720, 1280, 3), dtype=np.uint8)
    screen[:, :] = (50, 50, 50)

    x, y = position
    h, w = template.shape[:2]
    modified_template = template.copy()

    if modifications:
        if "brightness" in modifications:
            modified_template = cv2.add(
                modified_template,
                np.ones_like(modified_template) * modifications["brightness"],
            )
        if "scale" in modifications:
            scale = modifications["scale"]
            new_w = int(w * scale)
            new_h = int(h * scale)
            modified_template = cv2.resize(modified_template, (new_w, new_h))
            w, h = new_w, new_h
        if "noise" in modifications:
            noise = np.random.normal(0, modifications["noise"], modified_template.shape)
            modified_template = np.clip(modified_template + noise, 0, 255).astype(np.uint8)

    if y + h <= screen.shape[0] and x + w <= screen.shape[1]:
        screen[y : y + h, x : x + w] = modified_template

    return screen


def test_basic_matching():
    matcher = EnhancedMatcher()
    template_path, template = create_test_template()

    try:
        screen = create_test_screen(template, (500, 300))
        result = matcher.match_adaptive(screen, template_path, threshold=0.8)
        expected_x, expected_y = 525, 325

        assert result.found
        assert abs(result.center_x - expected_x) < 5
        assert abs(result.center_y - expected_y) < 5
    finally:
        os.unlink(template_path)
        matcher.clear_cache()


def test_brightness_change():
    matcher = EnhancedMatcher()
    template_path, template = create_test_template()

    try:
        screen = create_test_screen(template, (400, 200), {"brightness": 50})
        result = matcher.match_adaptive(screen, template_path, threshold=0.7)

        assert result.found
    finally:
        os.unlink(template_path)
        matcher.clear_cache()


def test_scale_change():
    matcher = EnhancedMatcher()
    template_path, template = create_test_template()

    try:
        screen = create_test_screen(template, (300, 400), {"scale": 1.2})
        result = matcher.match_adaptive(screen, template_path, threshold=0.7)

        assert result.found
    finally:
        os.unlink(template_path)
        matcher.clear_cache()


def test_noise():
    matcher = EnhancedMatcher()
    template_path, template = create_test_template()

    try:
        screen = create_test_screen(template, (600, 350), {"noise": 20})
        result = matcher.match_adaptive(screen, template_path, threshold=0.6)

        assert result.found
    finally:
        os.unlink(template_path)
        matcher.clear_cache()


def test_roi_speedup():
    matcher = EnhancedMatcher()
    template_path, template = create_test_template()

    try:
        screen = create_test_screen(template, (500, 300))
        result1 = matcher.match_with_roi(screen, template_path)
        result2 = matcher.match_with_roi(screen, template_path)

        assert result1.found
        assert result2.found
    finally:
        os.unlink(template_path)
        matcher.clear_cache()


def test_best_effort():
    matcher = EnhancedMatcher()
    template_path, template = create_test_template()

    try:
        screen = create_test_screen(
            template,
            (450, 250),
            {"brightness": 30, "noise": 15},
        )
        matcher.match_best_effort(screen, template_path, min_threshold=0.5)
    finally:
        os.unlink(template_path)
        matcher.clear_cache()


def run_all_tests():
    print("=" * 60)
    print("Enhanced matcher tests")
    print("=" * 60)

    tests = [
        ("basic matching", test_basic_matching),
        ("brightness change", test_brightness_change),
        ("scale change", test_scale_change),
        ("noise", test_noise),
        ("roi speedup", test_roi_speedup),
        ("best effort", test_best_effort),
    ]

    results = []
    for name, test_func in tests:
        try:
            passed = test_func()
            if passed is None:
                passed = True
            results.append((name, passed))
        except Exception as e:
            print(f"  [ERROR] {e}")
            import traceback

            traceback.print_exc()
            results.append((name, False))

    print("\n" + "=" * 60)
    print("Results:")
    passed_count = sum(1 for _, passed in results if passed)
    for name, passed in results:
        print(f"  {'PASS' if passed else 'FAIL'} {name}")
    print(f"\nSummary: {passed_count}/{len(results)} passed")


if __name__ == "__main__":
    run_all_tests()
