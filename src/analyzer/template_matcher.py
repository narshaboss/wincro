"""
WinCro ????? ???????? ???

OpenCV???????????? ???????? ?????????????
"""

import cv2
import numpy as np
import time
from pathlib import Path
from typing import Optional, List, Tuple, Dict, Any
from dataclasses import dataclass

from ..utils.logger import get_logger
from ..utils.config import get_config, DATA_DIR

logger = get_logger(__name__)


@dataclass
class MatchResult:
    """???????? ???"""
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
        """??? ???"""
        return (self.center_x, self.center_y)

    @property
    def region(self) -> Tuple[int, int, int, int]:
        """??? (x, y, width, height)"""
        return (self.x, self.y, self.width, self.height)


class TemplateMatcher:
    """
    ???????? ?????

    ?????? ??? ?????????? ?????????????
    ???????? ??????????????? ????? ????????
    """

    # ??? ??? ??? (???????? ???)
    _MAX_CACHE_SIZE = 50

    def __init__(self):
        """Initialize template matcher."""
        self._config = get_config()
        self._cache: Dict[str, np.ndarray] = {}
        self._mask_cache: Dict[str, np.ndarray] = {}

    def _load_template(self, template_path: str) -> Optional[np.ndarray]:
        """
        ?????????? ???

        Args:
            template_path: ?????????? ???

        Returns:
            Optional[np.ndarray]: ?????????? ??? None
        """
        # ??? ???
        if template_path in self._cache:
            return self._cache[template_path]

        try:
            path = Path(template_path)
            if not path.exists():
                logger.error(f"????????????? ?????: {template_path}")
                return None

            # ??? ??? ????
            img_array = np.fromfile(str(path), np.uint8)
            template = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
            if template is None:
                logger.error(f"?????????? ??? ???: {template_path}")
                return None

            # ??? ??? ??? - FIFO ?????? ??????? ???????? ???
            if len(self._cache) >= self._MAX_CACHE_SIZE:
                try:
                    oldest_key = next(iter(self._cache))
                    del self._cache[oldest_key]
                except (StopIteration, KeyError):
                    pass

            # ?????????
            self._cache[template_path] = template
            return template

        except Exception as e:
            logger.error(f"???????? ???: {e}")
            return None

    def _load_mask(self, template_path: str) -> Optional[np.ndarray]:
        """
        ?????? ????????????????? ???

        ?????????? ??????_mask.png
        """
        # ??? ???
        if template_path in self._mask_cache:
            return self._mask_cache[template_path]

        try:
            path = Path(template_path)
            mask_path = path.parent / f"{path.stem}_mask{path.suffix}"

            if not mask_path.exists():
                return None

            # ???????? (??????????
            img_array = np.fromfile(str(mask_path), np.uint8)
            mask = cv2.imdecode(img_array, cv2.IMREAD_GRAYSCALE)

            if mask is None:
                return None

            # ??? ??? ???
            if len(self._mask_cache) >= self._MAX_CACHE_SIZE:
                try:
                    oldest_key = next(iter(self._mask_cache))
                    del self._mask_cache[oldest_key]
                except (StopIteration, KeyError):
                    pass

            self._mask_cache[template_path] = mask
            logger.debug(f"???????? ???: {mask_path}")
            return mask

        except Exception as e:
            logger.debug(f"???????? ???: {e}")
            return None

    @staticmethod
    def _inspect_template_localization_image(
        template: np.ndarray,
        mask: Optional[np.ndarray] = None,
    ) -> Dict[str, Any]:
        """위치 추정용 템플릿 구조를 점검한다."""
        if template is None:
            return {"valid": False, "reason": "missing_template"}

        if len(template.shape) == 3:
            gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
        else:
            gray = template.copy()

        h, w = gray.shape[:2]
        area = int(h * w)
        unique_cols = 0
        mean_col_delta = 0.0
        if w > 0:
            unique_cols = len({gray[:, i].tobytes() for i in range(w)})
        if w > 1:
            mean_col_delta = float(np.mean([
                np.mean(np.abs(gray[:, i].astype(np.float32) - gray[:, i + 1].astype(np.float32)))
                for i in range(w - 1)
            ]))

        has_sidecar_mask = mask is not None and mask.shape[:2] == gray.shape[:2] and int(np.count_nonzero(mask)) > 0
        invalid = bool(
            not has_sidecar_mask
            and area < 128
            and w <= 6
            and unique_cols <= 2
            and mean_col_delta < 2.5
        )

        return {
            "valid": not invalid,
            "reason": "degenerate_tiny_template" if invalid else "ok",
            "width": int(w),
            "height": int(h),
            "area": area,
            "unique_cols": int(unique_cols),
            "mean_col_delta": float(mean_col_delta),
            "has_sidecar_mask": bool(has_sidecar_mask),
        }

    def inspect_template_localization(self, template_path: str) -> Dict[str, Any]:
        template = self._load_template(template_path)
        if template is None:
            return {"valid": False, "reason": "missing_template"}
        mask = self._load_mask(template_path)
        return self._inspect_template_localization_image(template, mask)

    def match(
        self,
        screen: np.ndarray,
        template_path: str,
        threshold: Optional[float] = None,
        method: int = cv2.TM_CCOEFF_NORMED,
        use_mask: bool = True,
        allow_flip: bool = False,
    ) -> MatchResult:
        """
        ?????? ?????????? ??? (?????????
        Args:
            screen: ????? ??? ?????
            template_path: ?????????? ???
            threshold: ??? ?????(None??? ????????)
            method: OpenCV ???????? ???
            use_mask: ???????? ??? (??? True, ???????? ???????? ???)
            allow_flip: ???????? ??? fallback ??? ???
        Returns:
            MatchResult: ??? ???
        """
        template = self._load_template(template_path)
        if template is None:
            return MatchResult(found=False)
        if threshold is None:
            threshold = self._config.analyzer.template_match_threshold
        mask = None
        if use_mask:
            mask = self._load_mask(template_path)
        _template_info = self._inspect_template_localization_image(template, mask)
        if not _template_info.get("valid", True):
            logger.warning(
                "위치 추정에 부적합한 템플릿 차단: %s (%sx%s, cols=%s, delta=%.2f)",
                Path(template_path).name,
                _template_info.get("width"),
                _template_info.get("height"),
                _template_info.get("unique_cols"),
                _template_info.get("mean_col_delta", 0.0),
            )
            return MatchResult(found=False)
        try:
            screen_h, screen_w = screen.shape[:2]
            variants = [("base", template, mask)]
            if allow_flip:
                variants.append(("flip", cv2.flip(template, 1), cv2.flip(mask, 1) if mask is not None else None))
            best_result = MatchResult(found=False)
            for variant_name, variant_template, variant_mask in variants:
                template_h, template_w = variant_template.shape[:2]
                if template_w > screen_w or template_h > screen_h:
                    continue
                if variant_mask is not None:
                    result = cv2.matchTemplate(screen, variant_template, cv2.TM_CCORR_NORMED, mask=variant_mask)
                    result = np.nan_to_num(result, nan=-1.0, posinf=-1.0, neginf=-1.0)
                    time.sleep(0)
                    _, max_val, _, max_loc = cv2.minMaxLoc(result)
                    confidence = float(max_val)
                    top_left = max_loc
                    logger.debug(f"???????? ???: {Path(template_path).name} [{variant_name}], ????? {confidence:.3f}")
                else:
                    result = cv2.matchTemplate(screen, variant_template, method)
                    time.sleep(0)
                    min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)
                    if method in [cv2.TM_SQDIFF, cv2.TM_SQDIFF_NORMED]:
                        confidence = float(1 - min_val)
                        top_left = min_loc
                    else:
                        confidence = float(max_val)
                        top_left = max_loc
                current = MatchResult(
                    found=confidence >= threshold,
                    x=int(top_left[0]),
                    y=int(top_left[1]),
                    width=int(template_w),
                    height=int(template_h),
                    confidence=confidence,
                    center_x=int(top_left[0]) + template_w // 2,
                    center_y=int(top_left[1]) + template_h // 2,
                )
                if current.confidence > best_result.confidence:
                    best_result = current
            return best_result
        except Exception as e:
            logger.error(f"???????? ???: {e}")
            return MatchResult(found=False)
    def match_binary(
        self,
        screen: np.ndarray,
        template_path: str,
        threshold: Optional[float] = None,
        allow_flip: bool = False,
    ) -> MatchResult:
        """
        ???????? ??????????????, ??? ??? ???
        ??????????????????????? ??????,
        matchTemplate??mask ????????????????? ????????
        """
        template = self._load_template(template_path)
        if template is None:
            return MatchResult(found=False)
        if threshold is None:
            threshold = 0.5
        sidecar_mask = self._load_mask(template_path)
        _template_info = self._inspect_template_localization_image(template, sidecar_mask)
        if not _template_info.get("valid", True):
            logger.warning(
                "이진 매칭에 부적합한 템플릿 차단: %s (%sx%s, cols=%s, delta=%.2f)",
                Path(template_path).name,
                _template_info.get("width"),
                _template_info.get("height"),
                _template_info.get("unique_cols"),
                _template_info.get("mean_col_delta", 0.0),
            )
            return MatchResult(found=False)
        try:
            if len(screen.shape) == 3:
                screen_gray = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)
            else:
                screen_gray = screen.copy()
            variants = [("base", template, sidecar_mask)]
            if allow_flip:
                variants.append(("flip", cv2.flip(template, 1), cv2.flip(sidecar_mask, 1) if sidecar_mask is not None else None))
            best_result = MatchResult(found=False)
            screen_h, screen_w = screen_gray.shape[:2]
            for variant_name, variant_template, variant_sidecar_mask in variants:
                template_h, template_w = variant_template.shape[:2]
                if template_w > screen_w or template_h > screen_h:
                    continue
                if len(variant_template.shape) == 3:
                    tmpl_gray = cv2.cvtColor(variant_template, cv2.COLOR_BGR2GRAY)
                else:
                    tmpl_gray = variant_template.copy()
                mask = variant_sidecar_mask
                if mask is None:
                    _, tmpl_bin = cv2.threshold(tmpl_gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
                    if np.mean(tmpl_bin) > 127:
                        mask = cv2.bitwise_not(tmpl_bin)
                    else:
                        mask = tmpl_bin.copy()
                    kernel = np.ones((2, 2), np.uint8)
                    mask = cv2.dilate(mask, kernel, iterations=1)
                total_pixels = mask.shape[0] * mask.shape[1]
                mask_pixels = int(np.count_nonzero(mask))
                mask_ratio = mask_pixels / total_pixels if total_pixels > 0 else 0
                min_mask_pixels = max(8, int(total_pixels * 0.08))
                if mask_ratio < 0.10 or mask_pixels < min_mask_pixels:
                    logger.debug(f"[???????? {variant_name}] ??????????? ??? ({mask_ratio:.1%}, {mask_pixels}px) ?????????? ???")
                    result = cv2.matchTemplate(screen_gray, tmpl_gray, cv2.TM_SQDIFF_NORMED)
                    time.sleep(0)
                else:
                    result = cv2.matchTemplate(screen_gray, tmpl_gray, cv2.TM_SQDIFF_NORMED, mask=mask)
                    result = np.nan_to_num(result, nan=1.0, posinf=1.0, neginf=1.0)
                    time.sleep(0)
                min_val, _, min_loc, _ = cv2.minMaxLoc(result)
                confidence = 1.0 - float(min_val)
                current = MatchResult(
                    found=confidence >= threshold,
                    x=int(min_loc[0]),
                    y=int(min_loc[1]),
                    width=int(template_w),
                    height=int(template_h),
                    confidence=confidence,
                    center_x=int(min_loc[0]) + template_w // 2,
                    center_y=int(min_loc[1]) + template_h // 2,
                )
                if current.confidence > best_result.confidence:
                    best_result = current
            return best_result
        except Exception as e:
            logger.error(f"???????? ???: {e}")
            return MatchResult(found=False)
    def match_all(
        self,
        screen: np.ndarray,
        template_path: str,
        threshold: Optional[float] = None,
        max_results: int = 10,
    ) -> List[MatchResult]:
        """
        ?????? ??????????????? ??? ???

        Args:
            screen: ????? ??? ?????
            template_path: ?????????? ???
            threshold: ??? ?????
            max_results: ??? ??? ??

        Returns:
            List[MatchResult]: ??? ??? ???
        """
        template = self._load_template(template_path)
        if template is None:
            return []

        if threshold is None:
            threshold = self._config.analyzer.template_match_threshold

        try:
            h, w = template.shape[:2]
            screen_h, screen_w = screen.shape[:2]

            # ??? ???
            if h > screen_h or w > screen_w:
                logger.warning(f"?????{w}x{h})?????({screen_w}x{screen_h})??? ??- ???")
                return []

            result = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
            time.sleep(0)  # GIL ???

            # ????????????? ??? ???
            locations = np.where(result >= threshold)
            matches = []

            for pt in zip(*locations[::-1]):  # x, y ?????????
                confidence = result[pt[1], pt[0]]
                center_x = pt[0] + w // 2
                center_y = pt[1] + h // 2

                # ??? ??? (????????)
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

            # ?????????????
            matches.sort(key=lambda m: m.confidence, reverse=True)
            return matches

        except Exception as e:
            logger.error(f"???????? ???: {e}")
            return []

    def match_with_scale(
        self,
        screen: np.ndarray,
        template_path: str,
        threshold: Optional[float] = None,
        scale_range: Tuple[float, float] = (0.8, 1.2),
        scale_steps: int = 5,
        allow_flip: bool = False,
    ) -> MatchResult:
        """
        ??????????????????????? ???
        Args:
            screen: ????? ??? ?????
            template_path: ?????????? ???
            threshold: ??? ?????
            scale_range: ???????? (min, max)
            scale_steps: ???????? ??
            allow_flip: ???????? ??? fallback ??? ???
        Returns:
            MatchResult: ??? ??? ???
        """
        template = self._load_template(template_path)
        if template is None:
            return MatchResult(found=False)
        if threshold is None:
            threshold = self._config.analyzer.template_match_threshold
        _template_info = self._inspect_template_localization_image(template, self._load_mask(template_path))
        if not _template_info.get("valid", True):
            logger.warning(
                "스케일 매칭에 부적합한 템플릿 차단: %s (%sx%s, cols=%s, delta=%.2f)",
                Path(template_path).name,
                _template_info.get("width"),
                _template_info.get("height"),
                _template_info.get("unique_cols"),
                _template_info.get("mean_col_delta", 0.0),
            )
            return MatchResult(found=False)
        best_result = MatchResult(found=False)
        scales = np.linspace(scale_range[0], scale_range[1], scale_steps)
        screen_h, screen_w = screen.shape[:2]
        variants = [template]
        if allow_flip:
            variants.append(cv2.flip(template, 1))
        for scale in scales:
            for variant_template in variants:
                h, w = variant_template.shape[:2]
                new_w = int(w * scale)
                new_h = int(h * scale)
                if new_w < 10 or new_h < 10:
                    continue
                if new_w > screen_w or new_h > screen_h:
                    continue
                scaled_template = cv2.resize(variant_template, (new_w, new_h))
                try:
                    result_map = cv2.matchTemplate(screen, scaled_template, cv2.TM_CCOEFF_NORMED)
                    time.sleep(0)
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
                    logger.debug(f"?????{scale:.2f} ??? ???: {e}")
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
        ???????? ?????? ?????????? ???

        Args:
            screen: ????? ??? ?????
            template_path: ?????????? ???
            threshold: ??? ?????
            angle_range: ??? ??? ???
            angle_steps: ??? ??? ??

        Returns:
            MatchResult: ??? ??? ???
        """
        template = self._load_template(template_path)
        if template is None:
            return MatchResult(found=False)

        if threshold is None:
            threshold = self._config.analyzer.template_match_threshold

        best_result = MatchResult(found=False)
        angles = np.linspace(angle_range[0], angle_range[1], angle_steps)

        for angle in angles:
            # ????????
            h, w = template.shape[:2]
            center = (w // 2, h // 2)
            rotation_matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
            rotated_template = cv2.warpAffine(template, rotation_matrix, (w, h))

            try:
                # ??????????? ?????? ??? ???
                screen_h, screen_w = screen.shape[:2]
                if w > screen_w or h > screen_h:
                    continue

                result_map = cv2.matchTemplate(screen, rotated_template, cv2.TM_CCOEFF_NORMED)
                time.sleep(0)  # GIL ???
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
                logger.debug(f"??? {angle:.1f}????? ???: {e}")
                continue

        return best_result

    def save_template(
        self,
        image: np.ndarray,
        name: str,
        region: Optional[Tuple[int, int, int, int]] = None,
    ) -> Optional[str]:
        """
        ?????????? ????

        Args:
            image: ??? ?????
            name: ????????
            region: ????? ??? (x, y, w, h), None??? ???

        Returns:
            Optional[str]: ????? ??? ??? ??? None
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
            logger.info(f"????????? {path}")
            return str(path)

        except Exception as e:
            logger.error(f"????????????: {e}")
            return None

    def clear_cache(self) -> None:
        """Initialize template matcher."""
        self._cache.clear()
        self._mask_cache.clear()
        logger.debug("cache cleared")

    def compare_images(
        self,
        image1: np.ndarray,
        image2: np.ndarray,
    ) -> float:
        """
        ?????????????????

        Args:
            image1: ????? ?????
            image2: ????? ?????

        Returns:
            float: ?????(0 ~ 1)
        """
        try:
            # ??? ?????
            if image1.shape != image2.shape:
                image2 = cv2.resize(image2, (image1.shape[1], image1.shape[0]))

            # ??????????????
            if len(image1.shape) == 3:
                gray1 = cv2.cvtColor(image1, cv2.COLOR_BGR2GRAY)
            else:
                gray1 = image1

            if len(image2.shape) == 3:
                gray2 = cv2.cvtColor(image2, cv2.COLOR_BGR2GRAY)
            else:
                gray2 = image2

            # ????????????? (??? ???)
            diff = cv2.absdiff(gray1, gray2)
            similarity = 1.0 - (np.mean(diff) / 255.0)

            return float(similarity)

        except Exception as e:
            logger.error(f"????? ??? ???: {e}")
            return 0.0


def generate_text_mask(image: np.ndarray, method: str = "auto") -> np.ndarray:
    """
    ????????????????? ???????? (??? ???, ????? ???)

    Args:
        image: BGR ?????
        method: ???????? ???
            - "auto": ??? ??? (??? + ??? ???)
            - "edge": ??? ???
            - "color": ??? ??? (????????)

    Returns:
        np.ndarray: ?????????? (255=??????, 0=??????)
    """
    if image is None or image.size == 0:
        return np.ones((10, 10), dtype=np.uint8) * 255

    h, w = image.shape[:2]

    # ??????????????
    if len(image.shape) == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image.copy()

    if method == "edge":
        # ??? ??? ?????
        edges = cv2.Canny(gray, 50, 150)
        # ??? ??? (dilate)
        kernel = np.ones((3, 3), np.uint8)
        mask = cv2.dilate(edges, kernel, iterations=2)
        return mask

    elif method == "color":
        # ??? ??? ?????(??? ????????????????)
        corners = [
            image[0, 0],
            image[0, w-1],
            image[h-1, 0],
            image[h-1, w-1]
        ]
        # ??????? ??? ??????????? ???
        bg_color = np.median(corners, axis=0).astype(np.uint8)

        # ?????? ??? ??? = ??? (????
        diff = np.abs(image.astype(np.int16) - bg_color.astype(np.int16))
        diff_sum = np.sum(diff, axis=2) if len(image.shape) == 3 else diff
        mask = (diff_sum > 30).astype(np.uint8) * 255

        # ????????
        kernel = np.ones((2, 2), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        return mask

    else:  # auto
        # ???: ???????? ????????? ???

        if len(image.shape) == 3:
            # ??? ?????
            # 1. ???????? (4??????????)
            corners = np.array([
                image[0, 0],
                image[0, w-1],
                image[h-1, 0],
                image[h-1, w-1]
            ], dtype=np.float32)
            bg_color = np.mean(corners, axis=0)

            # 2. ????????????? ??? ???
            diff = np.sqrt(np.sum((image.astype(np.float32) - bg_color) ** 2, axis=2))

            # 3. ???????? ??? (Otsu ???)
            diff_normalized = (diff / diff.max() * 255).astype(np.uint8)
            _, mask = cv2.threshold(diff_normalized, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        else:
            # ??????????
            corners = [gray[0, 0], gray[0, w-1], gray[h-1, 0], gray[h-1, w-1]]
            bg_val = np.mean(corners)
            diff = np.abs(gray.astype(np.float32) - bg_val)
            diff_normalized = (diff / max(diff.max(), 1) * 255).astype(np.uint8)
            _, mask = cv2.threshold(diff_normalized, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        # 4. ???????? ????? ?????
        kernel = np.ones((3, 3), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)  # ??? ?????
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)   # ????????

        return mask


def save_image_with_mask(image: np.ndarray, save_path: str) -> Tuple[str, Optional[str]]:
    """
    ??????? ?????? ??? ????

    Args:
        image: BGR ?????
        save_path: ???????

    Returns:
        Tuple[str, Optional[str]]: (????? ???, ???????? or None)
    """
    try:
        # ????? ????
        cv2.imwrite(save_path, image)

        # ???????? ??????
        mask = generate_text_mask(image)
        path = Path(save_path)
        mask_path = path.parent / f"{path.stem}_mask{path.suffix}"
        cv2.imwrite(str(mask_path), mask)

        logger.info(f"?????+????????? {save_path}")
        return save_path, str(mask_path)

    except Exception as e:
        logger.error(f"?????+????????????: {e}")
        return save_path, None


def generate_mask_for_existing_image(image_path: str) -> Optional[str]:
    """
    ??? ???????????????????

    Args:
        image_path: ????? ???

    Returns:
        Optional[str]: ???????? or None
    """
    try:
        path = Path(image_path)
        if not path.exists():
            return None

        # ????? ???
        img_array = np.fromfile(str(path), np.uint8)
        image = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        if image is None:
            return None

        # ????????
        mask = generate_text_mask(image)

        # ?????????
        mask_path = path.parent / f"{path.stem}_mask{path.suffix}"
        cv2.imwrite(str(mask_path), mask)

        logger.info(f"????????: {mask_path}")
        return str(mask_path)

    except Exception as e:
        logger.error(f"???????? ???: {e}")
        return None


# ??? ???????? ??????
template_matcher = TemplateMatcher()


def get_template_matcher() -> TemplateMatcher:
    """???????? ??? ???"""
    return template_matcher




