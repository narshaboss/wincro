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
    마스크 기반 매칭을 지원하여 배경 변화에 강건합니다.
    """

    # 캐시 크기 제한 (메모리 누수 방지)
    _MAX_CACHE_SIZE = 50

    def __init__(self):
        """템플릿 매처 초기화"""
        self._config = get_config()
        self._cache: Dict[str, np.ndarray] = {}
        self._mask_cache: Dict[str, np.ndarray] = {}

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

            # 한글 경로 지원
            img_array = np.fromfile(str(path), np.uint8)
            template = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
            if template is None:
                logger.error(f"템플릿 이미지 로드 실패: {template_path}")
                return None

            # 캐시 크기 제한 - FIFO 방식으로 가장 먼저 추가된 항목 제거
            if len(self._cache) >= self._MAX_CACHE_SIZE:
                try:
                    oldest_key = next(iter(self._cache))
                    del self._cache[oldest_key]
                except (StopIteration, KeyError):
                    pass

            # 캐시에 저장
            self._cache[template_path] = template
            return template

        except Exception as e:
            logger.error(f"템플릿 로드 오류: {e}")
            return None

    def _load_mask(self, template_path: str) -> Optional[np.ndarray]:
        """
        템플릿에 대응하는 마스크 이미지 로드

        마스크 파일명: 원본이름_mask.png
        """
        # 캐시 확인
        if template_path in self._mask_cache:
            return self._mask_cache[template_path]

        try:
            path = Path(template_path)
            mask_path = path.parent / f"{path.stem}_mask{path.suffix}"

            if not mask_path.exists():
                return None

            # 마스크 로드 (그레이스케일)
            img_array = np.fromfile(str(mask_path), np.uint8)
            mask = cv2.imdecode(img_array, cv2.IMREAD_GRAYSCALE)

            if mask is None:
                return None

            # 캐시 크기 제한
            if len(self._mask_cache) >= self._MAX_CACHE_SIZE:
                try:
                    oldest_key = next(iter(self._mask_cache))
                    del self._mask_cache[oldest_key]
                except (StopIteration, KeyError):
                    pass

            self._mask_cache[template_path] = mask
            logger.debug(f"마스크 로드 성공: {mask_path}")
            return mask

        except Exception as e:
            logger.debug(f"마스크 로드 실패: {e}")
            return None

    def match(
        self,
        screen: np.ndarray,
        template_path: str,
        threshold: Optional[float] = None,
        method: int = cv2.TM_CCOEFF_NORMED,
        use_mask: bool = True,
    ) -> MatchResult:
        """
        화면에서 템플릿 이미지 찾기 (마스크 지원)

        Args:
            screen: 검색할 화면 이미지
            template_path: 템플릿 이미지 경로
            threshold: 매칭 임계값 (None이면 설정값 사용)
            method: OpenCV 템플릿 매칭 방법
            use_mask: 마스크 사용 여부 (기본 True, 마스크 파일 있으면 자동 적용)

        Returns:
            MatchResult: 매칭 결과
        """
        template = self._load_template(template_path)
        if template is None:
            return MatchResult(found=False)

        if threshold is None:
            threshold = self._config.analyzer.template_match_threshold

        # 마스크 로드 시도
        mask = None
        if use_mask:
            mask = self._load_mask(template_path)

        try:
            # 템플릿 크기 검증 (화면보다 크면 매칭 불가)
            screen_h, screen_w = screen.shape[:2]
            template_h, template_w = template.shape[:2]
            if template_w > screen_w or template_h > screen_h:
                logger.warning(f"템플릿이 화면보다 큼: 템플릿({template_w}x{template_h}) > 화면({screen_w}x{screen_h})")
                return MatchResult(found=False)

            # 마스크가 있으면 마스크 매칭 사용
            if mask is not None:
                # 마스크 매칭은 TM_CCORR_NORMED 또는 TM_SQDIFF 사용
                # TM_CCORR_NORMED가 마스크와 가장 잘 작동
                result = cv2.matchTemplate(screen, template, cv2.TM_CCORR_NORMED, mask=mask)
                _, max_val, _, max_loc = cv2.minMaxLoc(result)
                confidence = max_val
                top_left = max_loc
                logger.debug(f"마스크 매칭 사용: {Path(template_path).name}, 신뢰도: {confidence:.3f}")
            else:
                # 일반 매칭
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

    def match_binary(
        self,
        screen: np.ndarray,
        template_path: str,
        threshold: Optional[float] = None,
    ) -> MatchResult:
        """
        마스크 매칭 — 글자 픽셀만 비교, 배경 완전 무시

        템플릿에서 글자 영역만 마스크로 추출하고,
        matchTemplate의 mask 파라미터로 글자 부분만 비교합니다.
        """
        template = self._load_template(template_path)
        if template is None:
            return MatchResult(found=False)

        if threshold is None:
            threshold = 0.5

        try:
            screen_h, screen_w = screen.shape[:2]
            template_h, template_w = template.shape[:2]
            if template_w > screen_w or template_h > screen_h:
                return MatchResult(found=False)

            # 그레이스케일 변환
            if len(screen.shape) == 3:
                screen_gray = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)
            else:
                screen_gray = screen.copy()
            if len(template.shape) == 3:
                tmpl_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
            else:
                tmpl_gray = template.copy()

            # 마스크 생성: Otsu로 글자 영역 추출
            _, tmpl_bin = cv2.threshold(tmpl_gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            # 글자가 소수(어두운 쪽)면 반전 → 글자=255
            if np.mean(tmpl_bin) > 127:
                mask = cv2.bitwise_not(tmpl_bin)
            else:
                mask = tmpl_bin.copy()

            # 마스크 약간 팽창 (글자 경계 포함)
            kernel = np.ones((2, 2), np.uint8)
            mask = cv2.dilate(mask, kernel, iterations=1)

            # TM_SQDIFF_NORMED + mask: 차이가 클수록 벌점 (0=완벽, 1=최악)
            result = cv2.matchTemplate(screen_gray, tmpl_gray, cv2.TM_SQDIFF_NORMED, mask=mask)
            min_val, _, min_loc, _ = cv2.minMaxLoc(result)

            # 신뢰도 변환: 0(최악)~1(완벽)
            confidence = 1.0 - min_val

            if confidence >= threshold:
                h, w = template.shape[:2]
                return MatchResult(
                    found=True,
                    x=min_loc[0], y=min_loc[1],
                    width=w, height=h,
                    confidence=confidence,
                    center_x=min_loc[0] + w // 2,
                    center_y=min_loc[1] + h // 2,
                )

            return MatchResult(found=False, confidence=confidence)

        except Exception as e:
            logger.error(f"마스크 매칭 오류: {e}")
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
        self._mask_cache.clear()
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


def generate_text_mask(image: np.ndarray, method: str = "auto") -> np.ndarray:
    """
    텍스트/글씨 이미지에서 마스크 생성 (배경 제외, 글씨만 포함)

    Args:
        image: BGR 이미지
        method: 마스크 생성 방법
            - "auto": 자동 감지 (엣지 + 색상 조합)
            - "edge": 엣지 기반
            - "color": 색상 기반 (배경색 제외)

    Returns:
        np.ndarray: 마스크 이미지 (255=매칭영역, 0=무시영역)
    """
    if image is None or image.size == 0:
        return np.ones((10, 10), dtype=np.uint8) * 255

    h, w = image.shape[:2]

    # 그레이스케일 변환
    if len(image.shape) == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image.copy()

    if method == "edge":
        # 엣지 기반 마스크
        edges = cv2.Canny(gray, 50, 150)
        # 엣지 확장 (dilate)
        kernel = np.ones((3, 3), np.uint8)
        mask = cv2.dilate(edges, kernel, iterations=2)
        return mask

    elif method == "color":
        # 색상 기반 마스크 (코너 픽셀을 배경색으로 추정)
        corners = [
            image[0, 0],
            image[0, w-1],
            image[h-1, 0],
            image[h-1, w-1]
        ]
        # 가장 흔한 코너 색상을 배경으로 추정
        bg_color = np.median(corners, axis=0).astype(np.uint8)

        # 배경색과 다른 픽셀 = 전경 (글씨)
        diff = np.abs(image.astype(np.int16) - bg_color.astype(np.int16))
        diff_sum = np.sum(diff, axis=2) if len(image.shape) == 3 else diff
        mask = (diff_sum > 30).astype(np.uint8) * 255

        # 노이즈 제거
        kernel = np.ones((2, 2), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        return mask

    else:  # auto
        # 자동: 배경색 추정 후 글씨 영역 추출

        if len(image.shape) == 3:
            # 컬러 이미지
            # 1. 배경색 추정 (4개 코너의 평균)
            corners = np.array([
                image[0, 0],
                image[0, w-1],
                image[h-1, 0],
                image[h-1, w-1]
            ], dtype=np.float32)
            bg_color = np.mean(corners, axis=0)

            # 2. 각 픽셀과 배경색의 차이 계산
            diff = np.sqrt(np.sum((image.astype(np.float32) - bg_color) ** 2, axis=2))

            # 3. 임계값 자동 결정 (Otsu 방식)
            diff_normalized = (diff / diff.max() * 255).astype(np.uint8)
            _, mask = cv2.threshold(diff_normalized, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        else:
            # 그레이스케일
            corners = [gray[0, 0], gray[0, w-1], gray[h-1, 0], gray[h-1, w-1]]
            bg_val = np.mean(corners)
            diff = np.abs(gray.astype(np.float32) - bg_val)
            diff_normalized = (diff / max(diff.max(), 1) * 255).astype(np.uint8)
            _, mask = cv2.threshold(diff_normalized, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        # 4. 노이즈 제거 및 구멍 메우기
        kernel = np.ones((3, 3), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)  # 구멍 메우기
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)   # 노이즈 제거

        return mask


def save_image_with_mask(image: np.ndarray, save_path: str) -> Tuple[str, Optional[str]]:
    """
    이미지와 마스크를 함께 저장

    Args:
        image: BGR 이미지
        save_path: 저장 경로

    Returns:
        Tuple[str, Optional[str]]: (이미지 경로, 마스크 경로 or None)
    """
    try:
        # 이미지 저장
        cv2.imwrite(save_path, image)

        # 마스크 생성 및 저장
        mask = generate_text_mask(image)
        path = Path(save_path)
        mask_path = path.parent / f"{path.stem}_mask{path.suffix}"
        cv2.imwrite(str(mask_path), mask)

        logger.info(f"이미지+마스크 저장: {save_path}")
        return save_path, str(mask_path)

    except Exception as e:
        logger.error(f"이미지+마스크 저장 실패: {e}")
        return save_path, None


def generate_mask_for_existing_image(image_path: str) -> Optional[str]:
    """
    기존 이미지에 대한 마스크 생성

    Args:
        image_path: 이미지 경로

    Returns:
        Optional[str]: 마스크 경로 or None
    """
    try:
        path = Path(image_path)
        if not path.exists():
            return None

        # 이미지 로드
        img_array = np.fromfile(str(path), np.uint8)
        image = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        if image is None:
            return None

        # 마스크 생성
        mask = generate_text_mask(image)

        # 마스크 저장
        mask_path = path.parent / f"{path.stem}_mask{path.suffix}"
        cv2.imwrite(str(mask_path), mask)

        logger.info(f"마스크 생성: {mask_path}")
        return str(mask_path)

    except Exception as e:
        logger.error(f"마스크 생성 실패: {e}")
        return None


# 전역 템플릿 매처 인스턴스
template_matcher = TemplateMatcher()


def get_template_matcher() -> TemplateMatcher:
    """템플릿 매처 헬퍼 함수"""
    return template_matcher
