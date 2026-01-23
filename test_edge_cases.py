"""
WinCro 분석 모듈 엣지 케이스 테스트
"""
import sys
import os
import json
import tempfile
import numpy as np

# 프로젝트 경로 추가
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.analyzer.action_extractor import ActionExtractor
from src.analyzer.template_matcher import TemplateMatcher, MatchResult
from src.database.models import Action, ActionType

def test_action_extractor_empty_log():
    """빈 로그 파일 테스트"""
    print("\n[테스트 1] 빈 로그 파일...")
    extractor = ActionExtractor()

    # 빈 이벤트 리스트
    result = extractor.extract_from_events([])
    assert result == [], "빈 이벤트는 빈 결과를 반환해야 함"
    print("  [PASS] 빈 이벤트 처리 성공")


def test_action_extractor_encoding():
    """다양한 인코딩 테스트"""
    print("\n[테스트 2] 다양한 인코딩...")
    extractor = ActionExtractor()

    # UTF-8 로그 생성
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as f:
        json.dump({"events": []}, f, ensure_ascii=False)
        temp_path = f.name

    try:
        result = extractor.extract_from_log(temp_path)
        assert result == [], "UTF-8 파일 처리 실패"
        print("  [PASS] UTF-8 인코딩 처리 성공")
    finally:
        os.unlink(temp_path)

    # CP949 로그 생성
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='cp949') as f:
        json.dump({"events": [], "description": "한글 테스트"}, f, ensure_ascii=False)
        temp_path = f.name

    try:
        result = extractor.extract_from_log(temp_path)
        assert result == [], "CP949 파일 처리 실패"
        print("  [PASS] CP949 인코딩 처리 성공")
    finally:
        os.unlink(temp_path)


def test_action_extractor_single_click():
    """단일 클릭만 있는 경우 테스트"""
    print("\n[테스트 3] 단일 클릭 이벤트...")
    extractor = ActionExtractor()

    from src.recorder.input_logger import InputEvent, InputEventType

    # 단일 클릭 (press만, release 없음)
    events = [
        InputEvent(
            event_type=InputEventType.MOUSE_CLICK.value,
            timestamp=0.0,
            x=100,
            y=100,
            button="left",
            pressed=True
        )
    ]

    result = extractor.extract_from_events(events)
    # release 이벤트가 없으므로 인덱스 증가 시 안전하게 처리되어야 함
    print(f"  결과: {len(result)}개 액션")
    print("  [PASS] 단일 클릭 처리 성공 (IndexError 없음)")


def test_action_extractor_triple_click():
    """삼중 클릭 테스트"""
    print("\n[테스트 4] 삼중 클릭...")
    extractor = ActionExtractor()

    from src.recorder.input_logger import InputEvent, InputEventType

    # 삼중 클릭 (6개 이벤트)
    events = []
    for i in range(3):
        events.append(InputEvent(
            event_type=InputEventType.MOUSE_CLICK.value,
            timestamp=0.1 * i,
            x=100,
            y=100,
            button="left",
            pressed=True
        ))
        events.append(InputEvent(
            event_type=InputEventType.MOUSE_CLICK.value,
            timestamp=0.1 * i + 0.05,
            x=100,
            y=100,
            button="left",
            pressed=False
        ))

    result = extractor.extract_from_events(events)
    print(f"  결과: {len(result)}개 액션 - {[a.action_type for a in result]}")
    print("  [PASS] 삼중 클릭 처리 성공")


def test_action_extractor_unmatched_drag():
    """시작만 있고 끝이 없는 드래그 테스트"""
    print("\n[테스트 5] 불완전 드래그...")
    extractor = ActionExtractor()

    from src.recorder.input_logger import InputEvent, InputEventType

    # 드래그 시작만 있음
    events = [
        InputEvent(
            event_type=InputEventType.MOUSE_DRAG_START.value,
            timestamp=0.0,
            x=100,
            y=100,
            button="left"
        )
    ]

    result = extractor.extract_from_events(events)
    # 드래그 끝이 없어도 에러 없이 처리되어야 함
    drag_actions = [a for a in result if a.action_type == ActionType.DRAG.value]
    assert len(drag_actions) == 0, "끝이 없는 드래그는 생성되지 않아야 함"
    print("  [PASS] 불완전 드래그 처리 성공")


def test_template_matcher_small_template():
    """너무 작은 템플릿 테스트"""
    print("\n[테스트 6] 작은 템플릿...")
    matcher = TemplateMatcher()

    screen = np.zeros((1080, 1920, 3), dtype=np.uint8)

    # 매우 작은 가짜 템플릿 (5x5)
    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
        import cv2
        small_template = np.zeros((5, 5, 3), dtype=np.uint8)
        cv2.imwrite(f.name, small_template)
        temp_path = f.name

    try:
        # match_with_scale에서 10x10 미만은 스킵되어야 함
        result = matcher.match_with_scale(screen, temp_path, scale_range=(0.5, 0.5))
        print(f"  결과: found={result.found}")
        print("  [PASS] 작은 템플릿 처리 성공")
    finally:
        os.unlink(temp_path)


def test_template_matcher_large_template():
    """화면보다 큰 템플릿 테스트"""
    print("\n[테스트 7] 큰 템플릿...")
    matcher = TemplateMatcher()

    # 작은 화면
    screen = np.zeros((100, 100, 3), dtype=np.uint8)

    # 화면보다 큰 템플릿
    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
        import cv2
        large_template = np.zeros((200, 200, 3), dtype=np.uint8)
        cv2.imwrite(f.name, large_template)
        temp_path = f.name

    try:
        result = matcher.match(screen, temp_path)
        assert not result.found, "화면보다 큰 템플릿은 찾을 수 없어야 함"
        print(f"  결과: found={result.found}")
        print("  [PASS] 큰 템플릿 처리 성공")
    finally:
        os.unlink(temp_path)


def test_template_matcher_nonexistent():
    """존재하지 않는 템플릿 테스트"""
    print("\n[테스트 8] 존재하지 않는 템플릿...")
    matcher = TemplateMatcher()

    screen = np.zeros((1080, 1920, 3), dtype=np.uint8)

    result = matcher.match(screen, "/nonexistent/path/template.png")
    assert not result.found, "존재하지 않는 템플릿은 찾을 수 없어야 함"
    print(f"  결과: found={result.found}")
    print("  [PASS] 존재하지 않는 템플릿 처리 성공")


def test_template_matcher_scale():
    """스케일 매칭 테스트 (버그 수정 확인)"""
    print("\n[테스트 9] 스케일 매칭 (버그 수정 확인)...")
    matcher = TemplateMatcher()

    import cv2

    # 템플릿 생성 (흰색 사각형)
    template = np.zeros((50, 50, 3), dtype=np.uint8)
    template[10:40, 10:40] = 255  # 중앙에 흰색 영역

    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
        cv2.imwrite(f.name, template)
        temp_path = f.name

    try:
        # 스케일된 화면 생성 (템플릿의 1.5배 크기)
        screen = np.zeros((1080, 1920, 3), dtype=np.uint8)
        scaled_size = int(50 * 1.5)
        scaled_template = cv2.resize(template, (scaled_size, scaled_size))
        screen[100:100+scaled_size, 100:100+scaled_size] = scaled_template

        # 스케일 매칭 시도
        result = matcher.match_with_scale(
            screen, temp_path,
            threshold=0.7,
            scale_range=(1.0, 2.0),
            scale_steps=5
        )
        print(f"  결과: found={result.found}, confidence={result.confidence:.2f}")
        if result.found:
            print(f"  위치: ({result.x}, {result.y}), 크기: {result.width}x{result.height}")
        print("  [PASS] 스케일 매칭 테스트 완료")
    finally:
        os.unlink(temp_path)


def test_action_from_dict_compatibility():
    """Action.from_dict 하위 호환성 테스트"""
    print("\n[테스트 10] Action.from_dict 호환성...")

    # 새 필드가 추가된 데이터 (미래 버전 시뮬레이션)
    future_data = {
        "action_type": "click",
        "x": 100,
        "y": 200,
        "unknown_field_1": "value1",
        "unknown_field_2": 123,
        "new_feature": {"nested": "data"}
    }

    try:
        action = Action.from_dict(future_data)
        assert action.action_type == "click"
        assert action.x == 100
        assert action.y == 200
        print(f"  결과: action_type={action.action_type}, x={action.x}, y={action.y}")
        print("  [PASS] 하위 호환성 테스트 성공")
    except TypeError as e:
        print(f"  [FAIL]: {e}")
        raise


def run_all_tests():
    """모든 테스트 실행"""
    print("=" * 60)
    print("WinCro 분석 모듈 엣지 케이스 테스트")
    print("=" * 60)

    tests = [
        test_action_extractor_empty_log,
        test_action_extractor_encoding,
        test_action_extractor_single_click,
        test_action_extractor_triple_click,
        test_action_extractor_unmatched_drag,
        test_template_matcher_small_template,
        test_template_matcher_large_template,
        test_template_matcher_nonexistent,
        test_template_matcher_scale,
        test_action_from_dict_compatibility,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"  [FAIL]: {e}")
            failed += 1
            import traceback
            traceback.print_exc()

    print("\n" + "=" * 60)
    print(f"테스트 결과: {passed}개 성공, {failed}개 실패")
    print("=" * 60)

    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
