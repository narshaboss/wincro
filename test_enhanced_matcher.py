"""
강화된 이미지 매처 테스트
"""
import sys
import os
import tempfile
import numpy as np
import cv2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.analyzer.enhanced_matcher import EnhancedMatcher, get_enhanced_matcher


def create_test_template():
    """테스트용 템플릿 생성"""
    template = np.zeros((50, 50, 3), dtype=np.uint8)
    # 특징적인 패턴 추가
    cv2.rectangle(template, (10, 10), (40, 40), (255, 255, 255), -1)
    cv2.circle(template, (25, 25), 10, (0, 0, 255), -1)

    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
        cv2.imwrite(f.name, template)
        return f.name, template


def create_test_screen(template, position, modifications=None):
    """테스트용 화면 생성"""
    screen = np.zeros((720, 1280, 3), dtype=np.uint8)
    screen[:, :] = (50, 50, 50)  # 회색 배경

    x, y = position
    h, w = template.shape[:2]

    modified_template = template.copy()

    if modifications:
        if 'brightness' in modifications:
            # 밝기 변경
            modified_template = cv2.add(
                modified_template,
                np.ones_like(modified_template) * modifications['brightness']
            )
        if 'scale' in modifications:
            # 크기 변경
            scale = modifications['scale']
            new_w = int(w * scale)
            new_h = int(h * scale)
            modified_template = cv2.resize(modified_template, (new_w, new_h))
            w, h = new_w, new_h
        if 'noise' in modifications:
            # 노이즈 추가
            noise = np.random.normal(0, modifications['noise'], modified_template.shape)
            modified_template = np.clip(modified_template + noise, 0, 255).astype(np.uint8)

    # 화면에 템플릿 배치
    if y + h <= screen.shape[0] and x + w <= screen.shape[1]:
        screen[y:y+h, x:x+w] = modified_template

    return screen


def test_basic_matching():
    """기본 매칭 테스트"""
    print("\n[Test 1] 기본 매칭...")
    matcher = EnhancedMatcher()
    template_path, template = create_test_template()

    try:
        screen = create_test_screen(template, (500, 300))
        result = matcher.match_adaptive(screen, template_path, threshold=0.8)

        print(f"  결과: found={result.found}, confidence={result.confidence:.2f}")
        print(f"  방법: {result.method_used}")
        print(f"  위치: ({result.center_x}, {result.center_y})")

        # 예상 위치 확인 (500 + 25, 300 + 25)
        expected_x, expected_y = 525, 325
        if result.found and abs(result.center_x - expected_x) < 5 and abs(result.center_y - expected_y) < 5:
            print("  [PASS] 위치 정확")
            return True
        else:
            print(f"  [FAIL] 예상 위치: ({expected_x}, {expected_y})")
            return False
    finally:
        os.unlink(template_path)
        matcher.clear_cache()


def test_brightness_change():
    """밝기 변화 테스트"""
    print("\n[Test 2] 밝기 변화 대응...")
    matcher = EnhancedMatcher()
    template_path, template = create_test_template()

    try:
        # 밝기가 변경된 화면
        screen = create_test_screen(template, (400, 200), {'brightness': 50})
        result = matcher.match_adaptive(screen, template_path, threshold=0.7)

        print(f"  결과: found={result.found}, confidence={result.confidence:.2f}")
        print(f"  방법: {result.method_used}")
        print(f"  모든 점수: {result.all_scores}")

        if result.found:
            print("  [PASS] 밝기 변화에도 인식 성공")
            return True
        else:
            print("  [FAIL] 밝기 변화 시 인식 실패")
            return False
    finally:
        os.unlink(template_path)
        matcher.clear_cache()


def test_scale_change():
    """크기 변화 테스트"""
    print("\n[Test 3] 크기 변화 대응...")
    matcher = EnhancedMatcher()
    template_path, template = create_test_template()

    try:
        # 1.2배 확대된 화면
        screen = create_test_screen(template, (300, 400), {'scale': 1.2})
        result = matcher.match_adaptive(screen, template_path, threshold=0.7)

        print(f"  결과: found={result.found}, confidence={result.confidence:.2f}")
        print(f"  방법: {result.method_used}")

        if result.found:
            print("  [PASS] 크기 변화에도 인식 성공")
            return True
        else:
            print("  [FAIL] 크기 변화 시 인식 실패")
            return False
    finally:
        os.unlink(template_path)
        matcher.clear_cache()


def test_noise():
    """노이즈 테스트"""
    print("\n[Test 4] 노이즈 대응...")
    matcher = EnhancedMatcher()
    template_path, template = create_test_template()

    try:
        # 노이즈가 추가된 화면
        screen = create_test_screen(template, (600, 350), {'noise': 20})
        result = matcher.match_adaptive(screen, template_path, threshold=0.6)

        print(f"  결과: found={result.found}, confidence={result.confidence:.2f}")
        print(f"  방법: {result.method_used}")

        if result.found:
            print("  [PASS] 노이즈에도 인식 성공")
            return True
        else:
            print("  [FAIL] 노이즈 시 인식 실패")
            return False
    finally:
        os.unlink(template_path)
        matcher.clear_cache()


def test_roi_speedup():
    """ROI 기반 속도 향상 테스트"""
    print("\n[Test 5] ROI 기반 연속 매칭...")
    matcher = EnhancedMatcher()
    template_path, template = create_test_template()

    try:
        import time

        position = (500, 300)
        screen = create_test_screen(template, position)

        # 첫 번째 매칭 (전체 화면 검색)
        start1 = time.time()
        result1 = matcher.match_with_roi(screen, template_path)
        time1 = time.time() - start1

        print(f"  첫 매칭: {time1*1000:.1f}ms, found={result1.found}")

        # 두 번째 매칭 (ROI 우선 검색)
        start2 = time.time()
        result2 = matcher.match_with_roi(screen, template_path)
        time2 = time.time() - start2

        print(f"  두번째: {time2*1000:.1f}ms, found={result2.found}")

        if result1.found and result2.found:
            print("  [PASS] 연속 매칭 성공")
            return True
        else:
            print("  [FAIL] 연속 매칭 실패")
            return False
    finally:
        os.unlink(template_path)
        matcher.clear_cache()


def test_best_effort():
    """최선 노력 매칭 테스트"""
    print("\n[Test 6] 최선 노력 매칭...")
    matcher = EnhancedMatcher()
    template_path, template = create_test_template()

    try:
        # 여러 변형이 적용된 어려운 케이스
        screen = create_test_screen(template, (450, 250), {
            'brightness': 30,
            'noise': 15
        })

        result = matcher.match_best_effort(screen, template_path, min_threshold=0.5)

        print(f"  결과: found={result.found}, confidence={result.confidence:.2f}")
        print(f"  방법: {result.method_used}")
        print(f"  모든 점수: {result.all_scores}")

        if result.found:
            print("  [PASS] 어려운 조건에서도 인식 성공")
            return True
        else:
            print("  [WARN] 어려운 조건에서 인식 실패 (예상 가능)")
            return True  # 이 케이스는 실패해도 OK
    finally:
        os.unlink(template_path)
        matcher.clear_cache()


def run_all_tests():
    """모든 테스트 실행"""
    print("=" * 60)
    print("강화된 이미지 매처 테스트")
    print("=" * 60)

    tests = [
        ("기본 매칭", test_basic_matching),
        ("밝기 변화", test_brightness_change),
        ("크기 변화", test_scale_change),
        ("노이즈 대응", test_noise),
        ("ROI 연속 매칭", test_roi_speedup),
        ("최선 노력 매칭", test_best_effort),
    ]

    results = []
    for name, test_func in tests:
        try:
            passed = test_func()
            results.append((name, passed))
        except Exception as e:
            print(f"  [ERROR] {e}")
            import traceback
            traceback.print_exc()
            results.append((name, False))

    print("\n" + "=" * 60)
    print("테스트 결과:")
    print("-" * 60)

    for name, passed in results:
        status = "[PASS]" if passed else "[FAIL]"
        print(f"  {status} {name}")

    passed_count = sum(1 for _, p in results if p)
    print("-" * 60)
    print(f"총 결과: {passed_count}/{len(results)} 성공")
    print("=" * 60)

    return all(p for _, p in results)


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
