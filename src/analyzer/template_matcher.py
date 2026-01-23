"""
WinCro 이미지 템플릿 매칭 모듈

OpenCV를 사용한 이미지 템플릿 매칭 기능을 제공합니다.
"""

import cv2
import numpy as np
from pathlib import Path
from typing import Optional, List, Tuple, Dict, Any
from dataclasses import dataclass

from ..utils.logger import get_logger
from ..utils.config import get_config, DATA_DIR

logger = get_logger(__name__)


@dataclass
class MatchResult:
    """템플릿 매칭 결과"""
    found: bool
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0
    confidence: float = 0.0
    center_x: int = 0
    center_y: int = 0

    @property
    def center(self) -> Tuple[int, int]:
        """중심 좌표"""
        return (self.center_x, self.center_y)

    @property
    def region(self) -> Tuple[int, int, int, int]:
        """영역 (x, y, width, height)"""
        return (self.x, self.y, self.width, self.height)


class TemplateMatcher:
    """
    템플릿 매칭 클래스

    화면에서 특정 이미지를 찾는 기능을 제공합니다.
    """

    def __init__(self):
        """템플릿 매처 초기화"""
        self._config = get_config()
        self._cache: Dict[str, np.ndarray] = {}

    def _load_template(self, template_path: str) -> Optional[np.ndarray]:
        """
        템플릿 이미지 로드

        Args:
            template_path: 템플릿 이미지 경로

        Returns:
            Optional[np.ndarray]: 로드된 이미지 또는 None
        """
        # 캐시 확인
        if template_path in self._cache:
            return self._cache[template_path]

        try:
            path = Path(template_path)
            if not path.exists():
                logger.error(f"템플릿 파일을 찾을 수 없음: {template_path}")
                return None

            template = cv2.imread(str(path))
            if template is None:
                logger.error(f"템플릿 이미지 로드 실패: {template_path}")
                return None

            # 캐시에 저장
            self._cache[template_path] = template
            return template

        except Exception as e:
            logger.error(f"템플릿 로드 오류: {e}")
            return None

    def match(
        self,
        screen: np.ndarray,
        template_path: str,
        threshold: Optional[float] = None,
        method: int = cv2.TM_CCOEFF_NORMED,
    ) -> MatchResult:
        """
        화면에서 템플릿 이미지 찾기

        Args:
            screen: 검색할 화면 이미지
            template_path: 템플릿 이미지 경로
            threshold: 매칭 임계값 (None이면 설정값 사용)
            method: OpenCV 템플릿 매칭 방법

        Returns:
            MatchResult: 매칭 결과
        """
        template = self._load_template(template_path)
        if template is None:
            return MatchResult(found=False)

        if threshold is None:
            threshold = self._config.analyzer.template_match_threshold

        try:
            # 템플릿 크기 검증 (화면보다 크면 매칭 불가)
            screen_h, screen_w = screen.shape[:2]
            template_h, template_w = template.shape[:2]
            if template_w > screen_w or template_h > screen_h:
                logger.warning(f"템플릿이 화면보다 큼: 템플릿({template_w}x{template_h}) > 화면({screen_w}x{screen_h})")
                return MatchResult(found=False)

            # 템플릿 매칭 수행
            result = cv2.matchTemplate(screen, template, method)
            min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)

            # 방법에 따라 최적 위치 선택
            if method in [cv2.TM_SQDIFF, cv2.TM_SQDIFF_NORMED]:
                confidence = 1 - min_val
                top_left = min_loc
            else:
                confidence = max_val
                top_left = max_loc

            # 임계값 확인
            if confidence >= threshold:
                h, w = template.shape[:2]
                center_x = top_left[0] + w // 2
                center_y = top_left[1] + h // 2

                return MatchResult(
                    found=True,
                    x=top_left[0],
                    y=top_left[1],
                    width=w,
                    height=h,
                    confidence=confidence,
                    center_x=center_x,
                    center_y=center_y,
                )

            return MatchResult(found=False, confidence=confidence)

        except Exception as e:
            logger.error(f"템플릿 매칭 오류: {e}")
            return MatchResult(found=False)

    def match_all(
        self,
        screen: np.ndarray,
        template_path: str,
        threshold: Optional[float] = None,
        max_results: int = 10,
    ) -> List[MatchResult]:
        """
        화면에서 템플릿 이미지의 모든 위치 찾기

        Args:
            screen: 검색할 화면 이미지
            template_path: 템플릿 이미지 경로
            threshold: 매칭 임계값
            max_results: 최대 결과 수

        Returns:
            List[MatchResult]: 매칭 결과 목록
        """
        template = self._load_template(template_path)
        if template is None:
            return []

        if threshold is None:
            threshold = self._config.analyzer.template_match_threshold

        try:
            h, w = template.shape[:2]
            screen_h, screen_w = screen.shape[:2]

            # 크기 체크
            if h > screen_h or w > screen_w:
                logger.warning(f"템플릿({w}x{h})이 화면({screen_w}x{screen_h})보다 큼 - 스킵")
                return []

            result = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)

            # 임계값 이상인 모든 위치 찾기
            locations = np.where(result >= threshold)
            matches = []

            for pt in zip(*locations[::-1]):  # x, y 순서로 변환
                confidence = result[pt[1], pt[0]]
                center_x = pt[0] + w // 2
                center_y = pt[1] + h // 2

                # 중복 제거 (근접한 위치)
                is_duplicate = False
                for existing in matches:
                    if abs(existing.x - pt[0]) < w // 2 and abs(existing.y - pt[1]) < h // 2:
                        is_duplicate = True
                        break

                if not is_duplicate:
                    matches.append(MatchResult(
                        found=True,
                        x=pt[0],
                        y=pt[1],
                        width=w,
                        height=h,
                        confidence=confidence,
                        center_x=center_x,
                        center_y=center_y,
                    ))

                if len(matches) >= max_results:
                    break

            # 신뢰도 순으로 정렬
            matches.sort(key=lambda m: m.confidence, reverse=True)
            return matches

        except Exception as e:
            logger.error(f"템플릿 매칭 오류: {e}")
            return []

    def match_with_scale(
        self,
        screen: np.ndarray,
        template_path: str,
        threshold: Optional[float] = None,
        scale_range: Tuple[float, float] = (0.8, 1.2),
        scale_steps: int = 5,
    ) -> MatchResult:
        """
        다양한 스케일에서 템플릿 이미지 찾기

        Args:
            screen: 검색할 화면 이미지
            template_path: 템플릿 이미지 경로
            threshold: 매칭 임계값
            scale_range: 스케일 범위 (min, max)
            scale_steps: 스케일 단계 수

        Returns:
            MatchResult: 최적 매칭 결과
        """
        template = self._load_template(template_path)
        if template is None:
            return MatchResult(found=False)

        if threshold is None:
            threshold = self._config.analyzer.template_match_threshold

        best_result = MatchResult(found=False)
        scales = np.linspace(scale_range[0], scale_range[1], scale_steps)

        screen_h, screen_w = screen.shape[:2]

        for scale in scales:
            # 템플릿 크기 조정
            h, w = template.shape[:2]
            new_w = int(w * scale)
            new_h = int(h * scale)

            # 템플릿이 너무 작거나 화면보다 큰 경우 스킵
            if new_w < 10 or new_h < 10:
                continue
            if new_w > screen_w or new_h > screen_h:
                continue

            scaled_template = cv2.resize(template, (new_w, new_h))

            try:
                # 스케일된 템플릿으로 직접 매칭 수행
                result_map = cv2.matchTemplate(screen, scaled_template, cv2.TM_CCOEFF_NORMED)
                _, max_val, _, max_loc = cv2.minMaxLoc(result_map)

                if max_val >= threshold and max_val > best_result.confidence:
                    center_x = max_loc[0] + new_w // 2
                    center_y = max_loc[1] + new_h // 2

                    best_result = MatchResult(
                        found=True,
                        x=max_loc[0],
                        y=max_loc[1],
                        width=new_w,
                        height=new_h,
                        confidence=max_val,
                        center_x=center_x,
                        center_y=center_y,
                    )
            except cv2.error as e:
                logger.debug(f"스케일 {scale:.2f} 매칭 오류: {e}")
                continue

        return best_result

    def match_with_rotation(
        self,
        screen: np.ndarray,
        template_path: str,
        threshold: Optional[float] = None,
        angle_range: Tuple[float, float] = (-15, 15),
        angle_steps: int = 5,
    ) -> MatchResult:
        """
        다양한 회전 각도에서 템플릿 이미지 찾기

        Args:
            screen: 검색할 화면 이미지
            template_path: 템플릿 이미지 경로
            threshold: 매칭 임계값
            angle_range: 회전 각도 범위
            angle_steps: 각도 단계 수

        Returns:
            MatchResult: 최적 매칭 결과
        """
        template = self._load_template(template_path)
        if template is None:
            return MatchResult(found=False)

        if threshold is None:
            threshold = self._config.analyzer.template_match_threshold

        best_result = MatchResult(found=False)
        angles = np.linspace(angle_range[0], angle_range[1], angle_steps)

        for angle in angles:
            # 템플릿 회전
            h, w = template.shape[:2]
            center = (w // 2, h // 2)
            rotation_matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
            rotated_template = cv2.warpAffine(template, rotation_matrix, (w, h))

            try:
                # 회전된 템플릿이 화면보다 큰지 확인
                screen_h, screen_w = screen.shape[:2]
                if w > screen_w or h > screen_h:
                    continue

                result_map = cv2.matchTemplate(screen, rotated_template, cv2.TM_CCOEFF_NORMED)
                _, max_val, _, max_loc = cv2.minMaxLoc(result_map)

                if max_val >= threshold and max_val > best_result.confidence:
                    best_result = MatchResult(
                        found=True,
                        x=max_loc[0],
                        y=max_loc[1],
                        width=w,
                        height=h,
                        confidence=max_val,
                        center_x=max_loc[0] + w // 2,
                        center_y=max_loc[1] + h // 2,
                    )
            except cv2.error as e:
                logger.debug(f"회전 {angle:.1f}도 매칭 오류: {e}")
                continue

        return best_result

    def save_template(
        self,
        image: np.ndarray,
        name: str,
        region: Optional[Tuple[int, int, int, int]] = None,
    ) -> Optional[str]:
        """
        템플릿 이미지 저장

        Args:
            image: 소스 이미지
            name: 템플릿 이름
            region: 저장할 영역 (x, y, w, h), None이면 전체

        Returns:
            Optional[str]: 저장된 파일 경로 또는 None
        """
        try:
            templates_dir = DATA_DIR / "templates"
            templates_dir.mkdir(parents=True, exist_ok=True)

            if region:
                x, y, w, h = region
                image = image[y:y+h, x:x+w]

            safe_name = "".join(c if c.isalnum() or c in '-_' else '_' for c in name)
            path = templates_dir / f"{safe_name}.png"

            cv2.imwrite(str(path), image)
            logger.info(f"템플릿 저장: {path}")
            return str(path)

        except Exception as e:
            logger.error(f"템플릿 저장 실패: {e}")
            return None

    def clear_cache(self) -> None:
        """템플릿 캐시 초기화"""
        self._cache.clear()
        logger.debug("템플릿 캐시 초기화")

    def compare_images(
        self,
        image1: np.ndarray,
        image2: np.ndarray,
    ) -> float:
        """
        두 이미지의 유사도 비교

        Args:
            image1: 첫 번째 이미지
            image2: 두 번째 이미지

        Returns:
            float: 유사도 (0 ~ 1)
        """
        try:
            # 크기 맞추기
            if image1.shape != image2.shape:
                image2 = cv2.resize(image2, (image1.shape[1], image1.shape[0]))

            # 그레이스케일 변환
            if len(image1.shape) == 3:
                gray1 = cv2.cvtColor(image1, cv2.COLOR_BGR2GRAY)
            else:
                gray1 = image1

            if len(image2.shape) == 3:
                gray2 = cv2.cvtColor(image2, cv2.COLOR_BGR2GRAY)
            else:
                gray2 = image2

            # 구조적 유사도 계산 (간단 버전)
            diff = cv2.absdiff(gray1, gray2)
            similarity = 1.0 - (np.mean(diff) / 255.0)

            return float(similarity)

        except Exception as e:
            logger.error(f"이미지 비교 오류: {e}")
            return 0.0


# 전역 템플릿 매처 인스턴스
template_matcher = TemplateMatcher()


def get_template_matcher() -> TemplateMatcher:
    """템플릿 매처 헬퍼 함수"""
    return template_matcher
