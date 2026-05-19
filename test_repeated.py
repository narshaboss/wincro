"""Repeated stress checks for analyzer-related components."""

from __future__ import annotations

import os
import random
import sys
import tempfile
import threading
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.analyzer.action_extractor import ActionExtractor
from src.analyzer.template_matcher import TemplateMatcher
from src.database.models import Action
from src.recorder.input_logger import InputEvent, InputEventType


def generate_random_events(count: int):
    events = []
    timestamp = 0.0

    for _ in range(count):
        event_type = random.choice(
            [
                InputEventType.MOUSE_CLICK.value,
                InputEventType.MOUSE_SCROLL.value,
                InputEventType.KEY_PRESS.value,
            ]
        )

        if event_type == InputEventType.MOUSE_CLICK.value:
            events.append(
                InputEvent(
                    event_type=event_type,
                    timestamp=timestamp,
                    x=random.randint(0, 1920),
                    y=random.randint(0, 1080),
                    button=random.choice(["left", "right"]),
                    pressed=True,
                )
            )
            timestamp += random.uniform(0.05, 0.15)
            events.append(
                InputEvent(
                    event_type=event_type,
                    timestamp=timestamp,
                    x=events[-1].x,
                    y=events[-1].y,
                    button=events[-1].button,
                    pressed=False,
                )
            )
        elif event_type == InputEventType.MOUSE_SCROLL.value:
            events.append(
                InputEvent(
                    event_type=event_type,
                    timestamp=timestamp,
                    x=random.randint(0, 1920),
                    y=random.randint(0, 1080),
                    scroll_dy=random.choice([-3, -2, -1, 1, 2, 3]),
                )
            )
        else:
            events.append(
                InputEvent(
                    event_type=InputEventType.KEY_PRESS.value,
                    timestamp=timestamp,
                    key=random.choice(["a", "b", "c", "Key.space", "Key.enter"]),
                )
            )

        timestamp += random.uniform(0.1, 1.0)

    return events


def test_action_extractor_stress(iterations: int = 10):
    extractor = ActionExtractor()
    success = 0

    for _ in range(iterations):
        events = generate_random_events(random.randint(10, 100))
        actions = extractor.extract_from_events(events)
        assert isinstance(actions, list)
        success += 1

    assert success == iterations


def test_template_matcher_stress(iterations: int = 10):
    matcher = TemplateMatcher()
    success = 0

    for _ in range(iterations):
        screen_w = random.randint(800, 1920)
        screen_h = random.randint(600, 1080)
        screen = np.random.randint(0, 255, (screen_h, screen_w, 3), dtype=np.uint8)

        template_w = random.randint(20, min(200, screen_w - 10))
        template_h = random.randint(20, min(200, screen_h - 10))
        template = np.random.randint(0, 255, (template_h, template_w, 3), dtype=np.uint8)

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            cv2.imwrite(f.name, template)
            temp_path = f.name

        try:
            matcher.match(screen, temp_path, threshold=0.99)
            matcher.match_with_scale(
                screen,
                temp_path,
                threshold=0.99,
                scale_range=(0.8, 1.2),
                scale_steps=3,
            )
            matcher.match_with_rotation(
                screen,
                temp_path,
                threshold=0.99,
                angle_range=(-5, 5),
                angle_steps=3,
            )
            success += 1
        finally:
            os.unlink(temp_path)
            matcher.clear_cache()

    assert success == iterations


def test_action_from_dict_stress(iterations: int = 100):
    success = 0

    for _ in range(iterations):
        data = {
            "action_type": random.choice(["click", "double_click", "type", "scroll"]),
            "x": random.randint(0, 1920),
            "y": random.randint(0, 1080),
        }

        for _ in range(random.randint(0, 5)):
            data[f"unknown_field_{random.randint(1000, 9999)}"] = random.choice(
                [None, random.randint(0, 100), "random_string", {"nested": "data"}, [1, 2, 3]]
            )

        action = Action.from_dict(data)
        assert action.action_type == data["action_type"]
        assert action.x == data["x"]
        assert action.y == data["y"]
        success += 1

    assert success == iterations


def test_concurrent_operations(iterations: int = 5):
    extractor = ActionExtractor()
    matcher = TemplateMatcher()
    results = []

    def worker(worker_id):
        try:
            events = generate_random_events(20)
            actions = extractor.extract_from_events(events)
            screen = np.zeros((100, 100, 3), dtype=np.uint8)
            result = matcher.match(screen, "/nonexistent/path.png")
            results.append((worker_id, isinstance(actions, list), result is not None))
        except Exception as e:
            results.append((worker_id, False, str(e)))

    for _ in range(iterations):
        results.clear()
        threads = []

        for worker_id in range(3):
            thread = threading.Thread(target=worker, args=(worker_id,))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join(timeout=5.0)

        assert all(item[1] is True for item in results), results


def run_all_tests():
    print("=" * 60)
    print("Repeated analyzer tests")
    print("=" * 60)

    start_time = time.time()
    tests = [
        ("action extractor stress", lambda: test_action_extractor_stress(10)),
        ("template matcher stress", lambda: test_template_matcher_stress(10)),
        ("action from dict stress", lambda: test_action_from_dict_stress(100)),
        ("concurrent operations", lambda: test_concurrent_operations(5)),
    ]

    results = []
    for name, test_func in tests:
        try:
            passed = test_func()
            if passed is None:
                passed = True
            results.append((name, passed))
        except Exception as e:
            print(f"  [ERROR] {name}: {e}")
            results.append((name, False))

    elapsed = time.time() - start_time
    print("\n" + "=" * 60)
    print("Summary:")
    passed_count = sum(1 for _, passed in results if passed)
    for name, passed in results:
        print(f"  {'PASS' if passed else 'FAIL'} {name}")
    print(f"\nSummary: {passed_count}/{len(results)} passed in {elapsed:.2f}s")


if __name__ == "__main__":
    run_all_tests()
