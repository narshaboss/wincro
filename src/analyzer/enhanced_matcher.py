"""
WinCro 강화된 이미지 매칭 모듈

여러 매칭 기법을 조합하여 인식률을 높입니다.
"""

import time
import threading
from collections import OrderedDict

import cv2
import numpy as np
from pathlib import Path
from typing import Optional, List, Tuple, Dict, Any
from dataclasses import dataclass, field
from enum import Enum

from ..utils.logger import get_logger
from ..utils.config import get_config, DATA_DIR

logger = get_logger(__name__)


def _validate_template_size(screen: np.ndarray, template: np.ndarray) -> bool:
    """템플릿이 화면보다 작은지 확인"""
    if screen is None or template is None:
        return False
    screen_h, screen_w = screen.shape[:2]
    templ_h, templ_w = template.shape[:2]
    if templ_h > screen_h or templ_w > screen_w:
        logger.warning(f"템플릿({templ_w}x{templ_h})이 화면({screen_w}x{screen_h})보다 큼 - 스킵")
        return False
    if templ_h < 5 or templ_w < 5:
        logger.warning(f"템플릿({templ_w}x{templ_h})이 너무 작음 - 스킵")
        return False
    return True


class MatchMethod(Enum):
    """매칭 방법"""
    TEMPLATE = "template"           # 기본 템플릿 매칭
    TEMPLATE_GRAY = "template_gray" # 그레이스케일 템플릿 매칭
    EDGE = "edge"                   # 엣지 기반 매칭
    FEATURE = "feature"             # 특징점 기반 매칭 (ORB)
    MULTI = "multi"                 # 복합 매칭 (여러 방법 조합)


@dataclass
class EnhancedMatchResult:
    """강화된 매칭 결과"""
    found: bool
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0
    confidence: float = 0.0
    center_x: int = 0
    center_y: int = 0
    method_used: str = ""
    all_scores: Dict[str, float] = field(default_factory=dict)

    @property
    def center(self) -> Tuple[int, int]:
        return (self.center_x, self.center_y)


class EnhancedMatcher:
    """
    강화된 이미지 매처

    여러 매칭 기법을 조합하여 더 정확한 이미지 인식을 제공합니다.
    """

    MAX_CACHE_SIZE = 50
    MAX_POSITIONS_SIZE = 200

    def __init__(self):
        self._config = get_config()
        self._cache: OrderedDict = OrderedDict()  # LRU 캐시
        self._last_positions: OrderedDict = OrderedDict()  # LRU 위치 기록
        self._cache_lock = threading.Lock()

        # ORB 특징점 검출기 (지연 로딩)
        self._orb = None
        self._bf_matcher = None

        # 화면 전처리 캐시 (동일 화면 반복 호출 방지, 튜플로 원자적 갱신)
        self._screen_cache = None  # (shape, mean, result) 또는 None

    def _get_orb(self):
        """ORB 검출기 지연 로딩"""
        if self._orb is None:
            self._orb = cv2.ORB_create(nfeatures=500)
        return self._orb

    def _get_bf_matcher(self):
        """BFMatcher 지연 로딩"""
        if self._bf_matcher is None:
            self._bf_matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
        return self._bf_matcher

    def _load_and_preprocess(self, template_path: str) -> Optional[Dict[str, np.ndarray]]:
        """템플릿 로드 및 전처리 (캐시 사용, 파일 수정시간 체크)"""
        try:
            path = Path(template_path)
            if not path.exists():
                logger.error(f"템플릿 파일 없음: {template_path}")
                # 캐시에서도 제거
                with self._cache_lock:
                    if template_path in self._cache:
                        del self._cache[template_path]
                    if template_path in self._last_positions:
                        del self._last_positions[template_path]
                return None

            # 파일 수정 시간 체크
            current_mtime = path.stat().st_mtime

            # 캐시에 있고 수정시간이 같으면 캐시 사용
            with self._cache_lock:
                if template_path in self._cache:
                    cached = self._cache[template_path]
                    if cached.get('_mtime') == current_mtime:
                        self._cache.move_to_end(template_path)
                        return cached
                    else:
                        # 파일이 변경됨 - 캐시와 위치 기록 삭제
                        logger.debug(f"템플릿 파일 변경 감지, 캐시 갱신: {path.name}")
                        del self._cache[template_path]
                        if template_path in self._last_positions:
                            del self._last_positions[template_path]

            # 한글 경로 지원 (cv2.imread는 한글 경로 못 읽음)
            img_array = np.fromfile(str(path), np.uint8)
            original = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
            if original is None:
                logger.error(f"템플릿 로드 실패: {template_path}")
                return None

            # 다양한 전처리 버전 생성
            preprocessed = {
                'original': original,
                'gray': cv2.cvtColor(original, cv2.COLOR_BGR2GRAY),
                '_mtime': current_mtime,  # 수정 시간 저장
            }

            # 히스토그램 균등화 (그레이스케일)
            preprocessed['gray_eq'] = cv2.equalizeHist(preprocessed['gray'])

            # 엣지 검출
            preprocessed['edges'] = cv2.Canny(preprocessed['gray'], 50, 150)

            # 가우시안 블러 (노이즈 제거)
            preprocessed['gray_blur'] = cv2.GaussianBlur(preprocessed['gray'], (3, 3), 0)

            # ORB 특징점
            keypoints, descriptors = self._get_orb().detectAndCompute(preprocessed['gray'], None)
            preprocessed['orb_kp'] = keypoints
            preprocessed['orb_desc'] = descriptors

            with self._cache_lock:
                self._cache[template_path] = preprocessed
                # LRU: 캐시 크기 제한
                while len(self._cache) > self.MAX_CACHE_SIZE:
                    self._cache.popitem(last=False)
            return preprocessed

        except Exception as e:
            logger.error(f"전처리 오류: {e}")
            return None

    def _preprocess_screen(self, screen: np.ndarray) -> Dict[str, np.ndarray]:
        """화면 이미지 전처리 (동일 화면이면 캐시 반환)"""
        # 빠른 근사 비교: shape + sampled hash
        cur_shape = screen.shape
        cur_mean = hash(screen[::50, ::50].tobytes())

        cache = self._screen_cache  # 원자적 읽기
        if cache is not None and cache[0] == cur_shape and cache[1] == cur_mean:
            return cache[2]

        result = {'original': screen}

        if len(screen.shape) == 3:
            result['gray'] = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)
        else:
            result['gray'] = screen

        result['gray_eq'] = cv2.equalizeHist(result['gray'])
        result['edges'] = cv2.Canny(result['gray'], 50, 150)
        result['gray_blur'] = cv2.GaussianBlur(result['gray'], (3, 3), 0)

        # 캐시 갱신 (튜플로 원자적 쓰기)
        self._screen_cache = (cur_shape, cur_mean, result)

        return result

    def match_template_gray(
        self,
        screen: np.ndarray,
        template_path: str,
        threshold: float = 0.8
    ) -> Tuple[bool, float, Tuple[int, int, int, int]]:
        """그레이스케일 템플릿 매칭"""
        templates = self._load_and_preprocess(template_path)
        if templates is None:
            return False, 0.0, (0, 0, 0, 0)

        screen_proc = self._preprocess_screen(screen)

        # 여러 그레이스케일 버전으로 시도
        best_score = 0.0
        best_loc = (0, 0)

        for gray_type in ['gray', 'gray_eq', 'gray_blur']:
            try:
                screen_img = screen_proc[gray_type]
                templ_img = templates[gray_type]

                # 크기 체크
                if not _validate_template_size(screen_img, templ_img):
                    continue

                result = cv2.matchTemplate(
                    screen_img,
                    templ_img,
                    cv2.TM_CCOEFF_NORMED
                )
                time.sleep(0)  # GIL 해제
                _, max_val, _, max_loc = cv2.minMaxLoc(result)

                if max_val > best_score:
                    best_score = max_val
                    best_loc = max_loc
            except cv2.error:
                continue

        h, w = templates['gray'].shape[:2]
        found = best_score >= threshold
        return found, best_score, (best_loc[0], best_loc[1], w, h)

    def match_edge_based(
        self,
        screen: np.ndarray,
        template_path: str,
        threshold: float = 0.6
    ) -> Tuple[bool, float, Tuple[int, int, int, int]]:
        """엣지 기반 매칭 - 색상/밝기 변화에 강함"""
        templates = self._load_and_preprocess(template_path)
        if templates is None:
            return False, 0.0, (0, 0, 0, 0)

        screen_proc = self._preprocess_screen(screen)

        try:
            # 크기 체크
            if not _validate_template_size(screen_proc['edges'], templates['edges']):
                return False, 0.0, (0, 0, 0, 0)

            result = cv2.matchTemplate(
                screen_proc['edges'],
                templates['edges'],
                cv2.TM_CCOEFF_NORMED
            )
            time.sleep(0)  # GIL 해제
            _, max_val, _, max_loc = cv2.minMaxLoc(result)

            h, w = templates['edges'].shape[:2]
            found = max_val >= threshold
            return found, max_val, (max_loc[0], max_loc[1], w, h)
        except cv2.error as e:
            logger.debug(f"엣지 매칭 오류: {e}")
            return False, 0.0, (0, 0, 0, 0)

    def match_feature_based(
        self,
        screen: np.ndarray,
        template_path: str,
        min_matches: int = 10
    ) -> Tuple[bool, float, Tuple[int, int, int, int]]:
        """ORB 특징점 기반 매칭 - 스케일/회전/부분가림에 강함"""
        templates = self._load_and_preprocess(template_path)
        if templates is None:
            return False, 0.0, (0, 0, 0, 0)

        if templates['orb_desc'] is None:
            return False, 0.0, (0, 0, 0, 0)

        screen_proc = self._preprocess_screen(screen)

        try:
            # 화면에서 특징점 검출
            screen_kp, screen_desc = self._get_orb().detectAndCompute(screen_proc['gray'], None)

            if screen_desc is None or len(screen_kp) < min_matches:
                return False, 0.0, (0, 0, 0, 0)

            # 특징점 매칭
            matches = self._get_bf_matcher().match(templates['orb_desc'], screen_desc)
            matches = sorted(matches, key=lambda x: x.distance)

            good_matches = [m for m in matches if m.distance < 50]

            if len(good_matches) >= min_matches:
                # 매칭된 점들의 위치로 영역 추정
                src_pts = np.float32([templates['orb_kp'][m.queryIdx].pt for m in good_matches])
                dst_pts = np.float32([screen_kp[m.trainIdx].pt for m in good_matches])

                # 중심점 계산
                center_x = int(np.mean(dst_pts[:, 0]))
                center_y = int(np.mean(dst_pts[:, 1]))

                h, w = templates['gray'].shape[:2]
                x = center_x - w // 2
                y = center_y - h // 2

                confidence = len(good_matches) / len(templates['orb_kp']) if templates['orb_kp'] else 0
                return True, min(confidence, 1.0), (x, y, w, h)

            return False, len(good_matches) / max(min_matches, 1), (0, 0, 0, 0)

        except Exception as e:
            logger.debug(f"특징점 매칭 오류: {e}")
            return False, 0.0, (0, 0, 0, 0)

    def match_with_center_mask(
        self,
        screen: np.ndarray,
        template_path: str,
        threshold: float = 0.7,
        mask_size: int = 40
    ) -> Tuple[bool, float, Tuple[int, int, int, int]]:
        """
        중앙 마스크 매칭 - 마우스 커서가 가리는 중앙 부분을 무시하고 매칭

        템플릿의 가장자리 영역만 사용하여 매칭합니다.
        마우스 커서가 타겟 이미지 위에 있어도 인식 가능합니다.
        """
        templates = self._load_and_preprocess(template_path)
        if templates is None:
            return False, 0.0, (0, 0, 0, 0)

        screen_proc = self._preprocess_screen(screen)
        template_gray = templates['gray']
        screen_gray = screen_proc['gray']

        h, w = template_gray.shape[:2]

        # 템플릿이 마스크보다 작으면 일반 매칭으로 대체
        if w < mask_size * 2 or h < mask_size * 2:
            return self.match_template_gray(screen, template_path, threshold)

        # 마스크 생성 (중앙 영역을 0으로, 가장자리를 255로)
        mask = np.ones_like(template_gray, dtype=np.uint8) * 255

        # 중앙 영역 계산
        center_x, center_y = w // 2, h // 2
        half_mask = mask_size // 2

        # 중앙 원형 영역을 마스크 처리 (마우스 커서 모양에 가까움)
        cv2.circle(mask, (center_x, center_y), half_mask, 0, -1)

        try:
            # 크기 체크
            if not _validate_template_size(screen_gray, template_gray):
                return False, 0.0, (0, 0, 0, 0)

            # TM_CCORR_NORMED는 마스크를 지원
            result = cv2.matchTemplate(
                screen_gray,
                template_gray,
                cv2.TM_CCORR_NORMED,
                mask=mask
            )
            time.sleep(0)  # GIL 해제
            _, max_val, _, max_loc = cv2.minMaxLoc(result)

            found = max_val >= threshold
            return found, max_val, (max_loc[0], max_loc[1], w, h)

        except cv2.error as e:
            logger.debug(f"마스크 매칭 오류: {e}")
            return False, 0.0, (0, 0, 0, 0)

    def match_edge_regions(
        self,
        screen: np.ndarray,
        template_path: str,
        threshold: float = 0.65
    ) -> Tuple[bool, float, Tuple[int, int, int, int]]:
        """
        가장자리 영역 분할 매칭 - 상/하/좌/우 가장자리를 개별적으로 매칭

        마우스 커서가 중앙을 가려도 가장자리로 위치를 추정합니다.
        """
        templates = self._load_and_preprocess(template_path)
        if templates is None:
            return False, 0.0, (0, 0, 0, 0)

        screen_proc = self._preprocess_screen(screen)
        template_gray = templates['gray']
        screen_gray = screen_proc['gray']

        h, w = template_gray.shape[:2]
        margin = min(w, h) // 4  # 가장자리 두께

        if margin < 10:
            return self.match_template_gray(screen, template_path, threshold)

        # 4개 가장자리 영역 추출
        regions = {
            'top': template_gray[:margin, :],           # 상단
            'bottom': template_gray[-margin:, :],       # 하단
            'left': template_gray[:, :margin],          # 좌측
            'right': template_gray[:, -margin:],        # 우측
        }

        matches = []
        screen_h, screen_w = screen_gray.shape[:2]

        for name, region in regions.items():
            if region.size == 0:
                continue

            # 크기 체크
            if not _validate_template_size(screen_gray, region):
                continue

            try:
                result = cv2.matchTemplate(screen_gray, region, cv2.TM_CCOEFF_NORMED)
                time.sleep(0)  # GIL 해제
                _, max_val, _, max_loc = cv2.minMaxLoc(result)

                if max_val >= threshold:
                    # 원본 템플릿 위치로 변환 (max_loc는 매칭된 부분 영역의 좌상단)
                    if name == 'top':
                        est_x, est_y = max_loc[0], max_loc[1]
                    elif name == 'bottom':
                        est_x, est_y = max_loc[0], max_loc[1] - (h - margin)
                    elif name == 'left':
                        est_x, est_y = max_loc[0], max_loc[1]
                    elif name == 'right':
                        est_x, est_y = max_loc[0] - (w - margin), max_loc[1]

                    matches.append({
                        'region': name,
                        'score': max_val,
                        'x': est_x,
                        'y': est_y
                    })
            except cv2.error:
                continue

        if len(matches) >= 2:  # 최소 2개 영역이 일치해야 신뢰
            # 가장 높은 점수의 매치 사용
            best = max(matches, key=lambda m: m['score'])
            avg_score = sum(m['score'] for m in matches) / len(matches)

            return True, avg_score, (best['x'], best['y'], w, h)

        return False, 0.0, (0, 0, 0, 0)

    def match_multi_scale(
        self,
        screen: np.ndarray,
        template_path: str,
        threshold: float = 0.8,
        scale_range: Tuple[float, float] = (0.7, 1.3),
        scale_steps: int = 7
    ) -> Tuple[bool, float, Tuple[int, int, int, int]]:
        """다중 스케일 그레이스케일 매칭"""
        templates = self._load_and_preprocess(template_path)
        if templates is None:
            return False, 0.0, (0, 0, 0, 0)

        screen_proc = self._preprocess_screen(screen)
        template_gray = templates['gray']
        screen_gray = screen_proc['gray']

        screen_h, screen_w = screen_gray.shape[:2]
        best_score = 0.0
        best_result = (0, 0, 0, 0)

        scales = np.linspace(scale_range[0], scale_range[1], scale_steps)

        for scale in scales:
            h, w = template_gray.shape[:2]
            new_w = int(w * scale)
            new_h = int(h * scale)

            if new_w < 10 or new_h < 10 or new_w > screen_w or new_h > screen_h:
                continue

            scaled = cv2.resize(template_gray, (new_w, new_h))

            try:
                result = cv2.matchTemplate(screen_gray, scaled, cv2.TM_CCOEFF_NORMED)
                time.sleep(0)  # GIL 해제
                _, max_val, _, max_loc = cv2.minMaxLoc(result)

                if max_val > best_score:
                    best_score = max_val
                    best_result = (max_loc[0], max_loc[1], new_w, new_h)
            except cv2.error:
                continue

        found = best_score >= threshold
        return found, best_score, best_result

    def match_with_roi(
        self,
        screen: np.ndarray,
        template_path: str,
        threshold: float = 0.8,
        roi_padding: int = 100
    ) -> EnhancedMatchResult:
        """
        ROI 기반 매칭 - 마지막 위치 근처에서 먼저 검색
        """
        # 마지막 위치 근처에서 먼저 검색 (더 빠름)
        last_pos = None
        with self._cache_lock:
            if template_path in self._last_positions:
                last_pos = self._last_positions[template_path]
        if last_pos is not None:
            last_x, last_y = last_pos

            h, w = screen.shape[:2]
            roi_x1 = max(0, last_x - roi_padding)
            roi_y1 = max(0, last_y - roi_padding)
            roi_x2 = min(w, last_x + roi_padding)
            roi_y2 = min(h, last_y + roi_padding)

            roi = screen[roi_y1:roi_y2, roi_x1:roi_x2]

            if roi.size > 0:
                result = self.match_adaptive(roi, template_path, threshold)
                if result.found:
                    # ROI 오프셋 적용
                    result.x += roi_x1
                    result.y += roi_y1
                    result.center_x += roi_x1
                    result.center_y += roi_y1
                    return result

        # ROI에서 못 찾으면 전체 화면 검색
        return self.match_adaptive(screen, template_path, threshold)

    def match_adaptive(
        self,
        screen: np.ndarray,
        template_path: str,
        threshold: float = 0.8
    ) -> EnhancedMatchResult:
        """
        적응형 복합 매칭 - 여러 방법을 순차적으로 시도

        1. 그레이스케일 템플릿 매칭 (빠름)
        2. 엣지 기반 매칭 (색상 변화에 강함)
        3. 다중 스케일 매칭 (크기 변화 대응)
        4. 특징점 매칭 (부분 가림/변형 대응)
        5. 중앙 마스크 매칭 (마우스 커서 가림 대응)
        6. 가장자리 분할 매칭 (마우스 커서 가림 대응)
        """
        all_scores = {}

        # 임계값 캐스케이드 (일관되게 감소)
        t1 = threshold                          # Level 1: 원본
        t2 = max(threshold - 0.05, 0.55)        # Level 2: -0.05
        t3 = max(threshold - 0.10, 0.50)        # Level 3: -0.10
        t4 = max(threshold - 0.15, 0.50)        # Level 5: -0.15
        t5 = max(threshold - 0.20, 0.45)        # Level 6: -0.20

        # 1. 기본 그레이스케일 매칭 (빠름)
        found, score, region = self.match_template_gray(screen, template_path, t1)
        all_scores['template_gray'] = score

        if found and score >= t1:
            result = self._create_result(True, region, score, 'template_gray', all_scores)
            self._update_last_position(template_path, result)
            return result

        # 2. 엣지 기반 매칭 (색상/밝기 변화에 강함)
        found, score, region = self.match_edge_based(screen, template_path, t2)
        all_scores['edge'] = score

        if found:
            result = self._create_result(True, region, score, 'edge', all_scores)
            self._update_last_position(template_path, result)
            return result

        # 3. 다중 스케일 매칭 (크기 변화 대응)
        found, score, region = self.match_multi_scale(screen, template_path, t3)
        all_scores['multi_scale'] = score

        if found:
            result = self._create_result(True, region, score, 'multi_scale', all_scores)
            self._update_last_position(template_path, result)
            return result

        # 4. 특징점 매칭 (회전/변형 대응)
        found, score, region = self.match_feature_based(screen, template_path, min_matches=8)
        all_scores['feature'] = score

        if found:
            result = self._create_result(True, region, score, 'feature', all_scores)
            self._update_last_position(template_path, result)
            return result

        # 5. 중앙 마스크 매칭 (마우스 커서가 중앙을 가릴 때)
        found, score, region = self.match_with_center_mask(screen, template_path, t4)
        all_scores['center_mask'] = score

        if found:
            result = self._create_result(True, region, score, 'center_mask', all_scores)
            self._update_last_position(template_path, result)
            return result

        # 6. 가장자리 분할 매칭 (최후의 수단)
        found, score, region = self.match_edge_regions(screen, template_path, t5)
        all_scores['edge_regions'] = score

        if found:
            result = self._create_result(True, region, score, 'edge_regions', all_scores)
            self._update_last_position(template_path, result)
            return result

        # 모든 방법 실패 시 가장 높은 점수 반환
        best_method = max(all_scores, key=all_scores.get)
        return EnhancedMatchResult(
            found=False,
            confidence=all_scores[best_method],
            method_used=best_method,
            all_scores=all_scores
        )

    def match_best_effort(
        self,
        screen: np.ndarray,
        template_path: str,
        min_threshold: float = 0.5
    ) -> EnhancedMatchResult:
        """
        최선 노력 매칭 - 임계값을 점진적으로 낮추며 시도
        """
        thresholds = [0.9, 0.85, 0.8, 0.75, 0.7, 0.65, 0.6, 0.55, min_threshold]

        for threshold in thresholds:
            result = self.match_adaptive(screen, template_path, threshold)
            if result.found:
                # 디버그 로그 제거 (초보자에게 불필요)
                return result

        return result  # 마지막 결과 반환 (실패해도 점수는 있음)

    def _create_result(
        self,
        found: bool,
        region: Tuple[int, int, int, int],
        confidence: float,
        method: str,
        all_scores: Dict[str, float]
    ) -> EnhancedMatchResult:
        """결과 객체 생성"""
        x, y, w, h = region
        return EnhancedMatchResult(
            found=found,
            x=x,
            y=y,
            width=w,
            height=h,
            confidence=confidence,
            center_x=x + w // 2,
            center_y=y + h // 2,
            method_used=method,
            all_scores=all_scores
        )

    def _update_last_position(self, template_path: str, result: EnhancedMatchResult):
        """마지막 발견 위치 업데이트"""
        if result.found:
            with self._cache_lock:
                self._last_positions[template_path] = (result.center_x, result.center_y)
                # LRU: 위치 기록 크기 제한
                while len(self._last_positions) > self.MAX_POSITIONS_SIZE:
                    self._last_positions.popitem(last=False)

    def clear_cache(self):
        """캐시 및 위치 기록 초기화"""
        with self._cache_lock:
            self._cache.clear()
            self._last_positions.clear()
        self._screen_cache = None  # 원자적 초기화
        logger.debug("강화 매처 캐시 초기화")

    def invalidate_template(self, template_path: str):
        """특정 템플릿의 캐시 및 위치 기록 제거"""
        with self._cache_lock:
            if template_path in self._cache:
                del self._cache[template_path]
                logger.debug(f"템플릿 캐시 제거: {Path(template_path).name}")
            if template_path in self._last_positions:
                del self._last_positions[template_path]
                logger.debug(f"템플릿 위치 기록 제거: {Path(template_path).name}")


# 전역 인스턴스
enhanced_matcher = EnhancedMatcher()


def get_enhanced_matcher() -> EnhancedMatcher:
    """강화 매처 헬퍼 함수"""
    return enhanced_matcher
