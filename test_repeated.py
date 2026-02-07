"""
WinCro 분석 모듈 반복 테스트
안정성 확인을 위해 테스트를 여러 번 반복합니다.
"""
import sys
import os
import time
import random
import numpy as np
import tempfile

# 프로젝트 경로 추가
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.analyzer.action_extractor import ActionExtractor
from src.analyzer.template_matcher import TemplateMatcher
from src.recorder.input_logger import InputEvent, InputEventType
from src.database.models import Action, ActionType


def generate_random_events(count: int):
    """랜덤 이벤트 생성"""
    events = []
    timestamp = 0.0

    for _ in range(count):
        event_type = random.choice([
            InputEventType.MOUSE_CLICK.value,
            InputEventType.MOUSE_SCROLL.value,
            InputEventType.KEY_PRESS.value,
        ])

        if event_type == InputEventType.MOUSE_CLICK.value:
            # 클릭 이벤트 (press + release)
            events.append(InputEvent(
                event_type=event_type,
                timestamp=timestamp,
                x=random.randint(0, 1920),
                y=random.randint(0, 1080),
                button=random.choice(["left", "right"]),
                pressed=True
            ))
            timestamp += random.uniform(0.05, 0.15)
            events.append(InputEvent(
                event_type=event_type,
                timestamp=timestamp,
                x=events[-1].x,
                y=events[-1].y,
                button=events[-1].button,
                pressed=False
            ))
        elif event_type == InputEventType.MOUSE_SCROLL.value:
            events.append(InputEvent(
                event_type=event_type,
                timestamp=timestamp,
                x=random.randint(0, 1920),
                y=random.randint(0, 1080),
                scroll_dy=random.choice([-3, -2, -1, 1, 2, 3])
            ))
        else:
            events.append(InputEvent(
                event_type=InputEventType.KEY_PRESS.value,
                timestamp=timestamp,
                key=random.choice(["a", "b", "c", "Key.space", "Key.enter"])
            ))

        timestamp += random.uniform(0.1, 1.0)

    return events


def test_action_extractor_stress(iterations: int = 10):
    """액션 추출기 스트레스 테스트"""
    print(f"\n[Test] ActionExtractor - {iterations}회 반복")
    extractor = ActionExtractor()

    success = 0
    total_events = 0
    total_actions = 0

    for i in range(iterations):
        event_count = random.randint(10, 100)
        events = generate_random_events(event_count)

        try:
            actions = extractor.extract_from_events(events)
            success += 1
            total_events += len(events)
            total_actions += len(actions)
        except Exception as e:
            print(f"  [FAIL] 반복 {i+1}: {e}")

    print(f"  결과: {success}/{iterations} 성공")
    print(f"  처리: 총 {total_events}개 이벤트 -> {total_actions}개 액션")
    return success == iterations


def test_template_matcher_stress(iterations: int = 10):
    """템플릿 매처 스트레스 테스트"""
    print(f"\n[Test] TemplateMatcher - {iterations}회 반복")
    matcher = TemplateMatcher()

    success = 0
    import cv2

    for i in range(iterations):
        # 랜덤 화면 생성
        screen_w = random.randint(800, 1920)
        screen_h = random.randint(600, 1080)
        screen = np.random.randint(0, 255, (screen_h, screen_w, 3), dtype=np.uint8)

        # 랜덤 템플릿 생성 (화면보다 작게)
        template_w = random.randint(20, min(200, screen_w - 10))
        template_h = random.randint(20, min(200, screen_h - 10))
        template = np.random.randint(0, 255, (template_h, template_w, 3), dtype=np.uint8)

        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
            cv2.imwrite(f.name, template)
            temp_path = f.name

        try:
            # 일반 매칭
            result1 = matcher.match(screen, temp_path, threshold=0.99)

            # 스케일 매칭
            result2 = matcher.match_with_scale(
                screen, temp_path,
                threshold=0.99,
                scale_range=(0.8, 1.2),
                scale_steps=3
            )

            # 회전 매칭
            result3 = matcher.match_with_rotation(
                screen, temp_path,
                threshold=0.99,
                angle_range=(-5, 5),
                angle_steps=3
            )

            success += 1
        except Exception as e:
            print(f"  [FAIL] 반복 {i+1}: {e}")
        finally:
            os.unlink(temp_path)
            matcher.clear_cache()

    print(f"  결과: {success}/{iterations} 성공")
    return success == iterations


def test_action_from_dict_stress(iterations: int = 100):
    """Action.from_dict 스트레스 테스트"""
    print(f"\n[Test] Action.from_dict - {iterations}회 반복")

    success = 0

    for i in range(iterations):
        # 랜덤 필드 포함 데이터 생성
        data = {
            "action_type": random.choice(["click", "double_click", "type", "scroll"]),
            "x": random.randint(0, 1920),
            "y": random.randint(0, 1080),
        }

        # 랜덤 미지원 필드 추가
        for _ in range(random.randint(0, 5)):
            data[f"unknown_field_{random.randint(1000, 9999)}"] = random.choice([
                None,
                random.randint(0, 100),
                "random_string",
                {"nested": "data"},
                [1, 2, 3]
            ])

        try:
            action = Action.from_dict(data)
            assert action.action_type == data["action_type"]
            assert action.x == data["x"]
            assert action.y == data["y"]
            success += 1
        except Exception as e:
            print(f"  [FAIL] 반복 {i+1}: {e}")

    print(f"  결과: {success}/{iterations} 성공")
    return success == iterations


def test_concurrent_operations(iterations: int = 5):
    """동시 작업 테스트"""
    print(f"\n[Test] 동시 작업 시뮬레이션 - {iterations}회 반복")
    import threading

    extractor = ActionExtractor()
    matcher = TemplateMatcher()
    results = []

    def worker(worker_id):
        try:
            # 액션 추출
            events = generate_random_events(20)
            actions = extractor.extract_from_events(events)

            # 템플릿 매칭
            screen = np.zeros((100, 100, 3), dtype=np.uint8)
            result = matcher.match(screen, "/nonexistent/path.png")

            results.append((worker_id, True))
        except Exception as e:
            results.append((worker_id, False, str(e)))

    for i in range(iterations):
        results.clear()
        threads = []

        for j in range(3):
            t = threading.Thread(target=worker, args=(j,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=5.0)

        success = all(r[1] for r in results)
        if not success:
            print(f"  [FAIL] 반복 {i+1}: {results}")
            return False

    print(f"  결과: {iterations}/{iterations} 성공")
    return True


def run_all_tests():
    """모든 반복 테스트 실행"""
    print("=" * 60)
    print("WinCro 분석 모듈 반복 안정성 테스트")
    print("=" * 60)

    start_time = time.time()

    tests = [
        ("ActionExtractor 스트레스", lambda: test_action_extractor_stress(10)),
        ("TemplateMatcher 스트레스", lambda: test_template_matcher_stress(10)),
        ("Action.from_dict 스트레스", lambda: test_action_from_dict_stress(100)),
        ("동시 작업 시뮬레이션", lambda: test_concurrent_operations(5)),
    ]

    results = []
    for name, test_func in tests:
        try:
            passed = test_func()
            results.append((name, passed))
        except Exception as e:
            print(f"  [ERROR] {name}: {e}")
            results.append((name, False))

    elapsed = time.time() - start_time

    print("\n" + "=" * 60)
    print("반복 테스트 결과 요약:")
    print("-" * 60)

    for name, passed in results:
        status = "[PASS]" if passed else "[FAIL]"
        print(f"  {status} {name}")

    passed_count = sum(1 for _, p in results if p)
    total_count = len(results)

    print("-" * 60)
    print(f"총 결과: {passed_count}/{total_count} 성공")
    print(f"소요 시간: {elapsed:.2f}초")
    print("=" * 60)

    return all(p for _, p in results)


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
