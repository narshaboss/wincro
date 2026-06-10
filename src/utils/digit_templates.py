"""
WinCro 숫자 템플릿 매칭 유틸리티

게임 폰트의 숫자 0~9 템플릿을 저장하고 매칭하여 좌표를 인식합니다.
OCR보다 정확합니다.
"""

import json
import time
import numpy as np
import threading
from pathlib import Path
from typing import Dict, Optional, List, Tuple
from PIL import Image, ImageGrab

try:
    import cv2
except ImportError:
    cv2 = None

from .logger import get_logger

logger = get_logger(__name__)


def _safe_grab(timeout: float = 2.0, stop_event=None) -> Optional[Image.Image]:
    """ImageGrab.grab()을 타임아웃과 함께 실행 (DWM 행 방지, stop_event로 조기 종료)"""
    if stop_event and stop_event.is_set():
        return None

    result = [None]
    error = [None]

    def _grab():
        try:
            result[0] = ImageGrab.grab()
        except Exception as e:
            error[0] = e

    t = threading.Thread(target=_grab, daemon=True)
    t.start()
    # 0.1초 단위로 체크하며 대기 (stop_event 즉시 반응)
    elapsed = 0.0
    while t.is_alive() and elapsed < timeout:
        if stop_event and stop_event.is_set():
            logger.debug("[템플릿] _safe_grab 중지 요청으로 조기 종료")
            return None
        t.join(timeout=0.1)
        elapsed += 0.1
    if t.is_alive():
        logger.error(f"[템플릿] ImageGrab.grab() 타임아웃 ({timeout}초)")
        return None
    if error[0]:
        raise error[0]
    return result[0]

# 템플릿 저장 경로
TEMPLATE_DIR = Path(__file__).parent.parent.parent / "data" / "digit_templates"
TEMPLATE_FILE = TEMPLATE_DIR / "templates.json"


class DigitTemplateMatcher:
    """숫자 템플릿 매칭 클래스"""

    def __init__(self):
        self.templates: dict = {}  # {digit: numpy array}
        self._template_mask_cache: Dict[str, np.ndarray] = {}
        self._load_templates()

    def _load_templates(self):
        """저장된 템플릿 불러오기"""
        if not TEMPLATE_FILE.exists():
            logger.debug("[템플릿] 저장된 템플릿 없음")
            return

        try:
            with open(TEMPLATE_FILE, 'r') as f:
                data = json.load(f)

            for digit, template_list in data.items():
                self.templates[digit] = np.array(template_list, dtype=np.uint8)
                logger.debug(f"[템플릿] 숫자 '{digit}' 로드됨")

            self._template_mask_cache.clear()
            logger.info(f"[템플릿] {len(self.templates)}개 숫자 템플릿 로드 완료")
        except Exception as e:
            logger.error(f"[템플릿] 로드 실패: {e}")

    def save_templates(self):
        """템플릿 저장"""
        TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)

        try:
            data = {}
            for digit, template in self.templates.items():
                data[digit] = template.tolist()

            with open(TEMPLATE_FILE, 'w') as f:
                json.dump(data, f)

            logger.info(f"[템플릿] {len(self.templates)}개 숫자 템플릿 저장 완료")
            return True
        except Exception as e:
            logger.error(f"[템플릿] 저장 실패: {e}")
            return False

    def capture_digit_template(self, digit: str, region: List[int]) -> bool:
        """
        화면에서 숫자 템플릿 캡처

        Args:
            digit: 캡처할 숫자 ('0'~'9')
            region: 캡처 영역 [x1, y1, x2, y2]

        Returns:
            성공 여부
        """
        if cv2 is None:
            logger.error("[템플릿] OpenCV가 설치되지 않음")
            return False

        try:
            screenshot = _safe_grab()
            if screenshot is None:
                logger.error("[템플릿] 스크린샷 캡처 실패 (타임아웃)")
                return False
            x1, y1, x2, y2 = region
            cropped = screenshot.crop((x1, y1, x2, y2))

            # 그레이스케일 변환
            cropped_np = np.array(cropped)
            if len(cropped_np.shape) == 3:
                gray = cv2.cvtColor(cropped_np, cv2.COLOR_RGB2GRAY)
            else:
                gray = cropped_np

            # 이진화
            _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

            self.templates[digit] = binary
            self._template_mask_cache.clear()
            logger.info(f"[템플릿] 숫자 '{digit}' 캡처 완료 (크기: {binary.shape})")

            # 디버그용 이미지 저장
            debug_path = TEMPLATE_DIR / f"digit_{digit}.png"
            TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(debug_path), binary)

            return True
        except Exception as e:
            logger.error(f"[템플릿] 캡처 실패: {e}")
            return False

    def has_all_templates(self) -> bool:
        """모든 숫자(0~9) 템플릿이 있는지 확인"""
        for i in range(10):
            if str(i) not in self.templates:
                return False
        return True

    def get_missing_digits(self) -> List[str]:
        """누락된 숫자 목록 반환"""
        missing = []
        for i in range(10):
            if str(i) not in self.templates:
                missing.append(str(i))
        return missing

    def _get_template_size(self) -> Tuple[int, int]:
        """템플릿의 평균 크기 반환"""
        if not self.templates:
            return 10, 15  # 기본값

        widths = []
        heights = []
        for template in self.templates.values():
            h, w = template.shape[:2]
            widths.append(w)
            heights.append(h)

        return int(np.mean(widths)), int(np.mean(heights))

    @staticmethod
    def _trim_foreground_mask(mask: np.ndarray) -> Optional[np.ndarray]:
        ys, xs = np.where(mask > 0)
        if len(xs) == 0:
            return None
        return mask[int(ys.min()):int(ys.max()) + 1, int(xs.min()):int(xs.max()) + 1]

    @staticmethod
    def _foreground_mask_from_binary(binary: np.ndarray) -> np.ndarray:
        dark = binary < 128
        light = binary >= 128
        foreground = dark if int(np.count_nonzero(dark)) <= int(np.count_nonzero(light)) else light
        return (foreground.astype(np.uint8) * 255)

    def _get_template_masks(self) -> Dict[str, np.ndarray]:
        if self._template_mask_cache:
            return self._template_mask_cache

        masks: Dict[str, np.ndarray] = {}
        for digit, template in self.templates.items():
            mask = self._foreground_mask_from_binary(template)
            trimmed = self._trim_foreground_mask(mask)
            if trimmed is not None:
                masks[str(digit)] = trimmed
        self._template_mask_cache = masks
        return masks

    @staticmethod
    def _component_boxes_from_mask(mask: np.ndarray) -> List[Tuple[int, int, int, int]]:
        if cv2 is None or mask.size == 0:
            return []

        h, w = mask.shape[:2]
        try:
            num, _labels, stats, _centroids = cv2.connectedComponentsWithStats(mask, 8)
        except Exception:
            return []

        boxes: List[Tuple[int, int, int, int]] = []
        min_area = max(3, int(h * w * 0.006))
        max_width = max(18, int(w * 0.70))
        for idx in range(1, num):
            x, y, bw, bh, area = [int(v) for v in stats[idx]]
            if area < min_area:
                continue
            if bh < max(5, int(h * 0.35)) or bh > int(h * 1.05):
                continue
            if bw < 2 or bw > max_width:
                continue
            center_y = y + (bh / 2)
            if center_y < h * 0.12 or center_y > h * 0.92:
                continue
            boxes.append((x, y, x + bw, y + bh))

        boxes.sort(key=lambda b: (b[0], b[1]))
        return boxes

    @staticmethod
    def _trim_sparse_edges(mask: np.ndarray) -> Optional[np.ndarray]:
        trimmed = DigitTemplateMatcher._trim_foreground_mask(mask)
        if trimmed is None:
            return None

        result = trimmed
        # Keep the original candidate elsewhere; this variant only removes single-pixel
        # edge protrusions that come from screen noise touching a digit.
        while result.shape[1] > 4 and int(np.count_nonzero(result[:, 0])) <= 1:
            result = result[:, 1:]
        while result.shape[1] > 4 and int(np.count_nonzero(result[:, -1])) <= 1:
            result = result[:, :-1]
        while result.shape[0] > 6 and int(np.count_nonzero(result[0, :])) <= 1:
            result = result[1:, :]
        while result.shape[0] > 6 and int(np.count_nonzero(result[-1, :])) <= 1:
            result = result[:-1, :]
        return result

    def _score_digit_component(self, mask: np.ndarray) -> Optional[Tuple[str, float, Dict[str, float]]]:
        component = self._trim_foreground_mask(mask)
        if component is None or cv2 is None:
            return None

        variants = [component]
        sparse_trimmed = self._trim_sparse_edges(component)
        if sparse_trimmed is not None and sparse_trimmed.shape != component.shape:
            variants.append(sparse_trimmed)

        scores: Dict[str, float] = {}
        for variant in variants:
            for digit, template_mask in self._get_template_masks().items():
                tmpl_h, tmpl_w = template_mask.shape[:2]
                if tmpl_h <= 0 or tmpl_w <= 0:
                    continue
                resized = cv2.resize(variant, (tmpl_w, tmpl_h), interpolation=cv2.INTER_AREA)
                _, resized = cv2.threshold(resized, 127, 255, cv2.THRESH_BINARY)
                candidate = resized > 0
                template_fg = template_mask > 0
                intersection = int(np.logical_and(candidate, template_fg).sum())
                union = int(np.logical_or(candidate, template_fg).sum())
                score = intersection / max(1, union)
                if score > scores.get(digit, -1.0):
                    scores[digit] = score

        if not scores:
            return None
        best_digit = max(scores, key=scores.get)
        return best_digit, scores[best_digit], scores

    def _resolve_zero_one_ambiguity(
        self,
        digit: str,
        score: float,
        scores: Dict[str, float],
        component_mask: np.ndarray,
    ) -> str:
        if digit != "1":
            return digit

        zero_score = scores.get("0", 0.0)
        if zero_score < score - 0.12:
            return digit

        trimmed = self._trim_foreground_mask(component_mask)
        if trimmed is None:
            return digit

        h, w = trimmed.shape[:2]
        if h < 6 or w < 4:
            return digit

        mid = trimmed[int(h * 0.25):max(int(h * 0.75), int(h * 0.25) + 1), :]
        left = int(np.count_nonzero(mid[:, :max(1, int(w * 0.35))]))
        center = int(np.count_nonzero(mid[:, int(w * 0.35):max(int(w * 0.65), int(w * 0.35) + 1)]))
        right = int(np.count_nonzero(mid[:, int(w * 0.65):]))

        # A cut or thin zero still has ink on both side walls; a real one is mostly one-sided.
        if left >= 2 and right >= 2 and center <= max(left, right) * 1.25:
            return "0"
        return digit

    def _read_number_by_components(self, mask: np.ndarray, expected_digits: int = 3) -> Optional[int]:
        boxes = self._component_boxes_from_mask(mask)
        if len(boxes) < expected_digits:
            return None

        min_digit_score = 0.42
        min_ambiguous_margin = 0.10
        best_result: Optional[Tuple[float, int]] = None
        for start in range(0, len(boxes) - expected_digits + 1):
            group = boxes[start:start + expected_digits]
            digits: List[str] = []
            scores: List[float] = []
            for x1, y1, x2, y2 in group:
                component_mask = mask[y1:y2, x1:x2]
                scored = self._score_digit_component(component_mask)
                if scored is None:
                    digits = []
                    break
                digit, score, score_map = scored
                digit = self._resolve_zero_one_ambiguity(digit, score, score_map, component_mask)
                sorted_scores = sorted(score_map.values(), reverse=True)
                digit_score = score_map.get(digit, score)
                second_score = sorted_scores[1] if len(sorted_scores) > 1 else 0.0
                if digit_score < min_digit_score:
                    digits = []
                    break
                if digit_score < 0.65 and (digit_score - second_score) < min_ambiguous_margin:
                    digits = []
                    break
                digits.append(digit)
                scores.append(digit_score)

            if len(digits) != expected_digits:
                continue
            avg_score = sum(scores) / len(scores)
            if avg_score < 0.24:
                continue

            # Prefer the group with the most digit-like shapes; this ignores X/Y labels or edge noise.
            value = int("".join(digits))
            if best_result is None or avg_score > best_result[0]:
                best_result = (avg_score, value)

        if best_result is None:
            return None
        logger.debug(f"[템플릿] 컴포넌트 인식: {best_result[1]} (신뢰도: {best_result[0]:.2f})")
        return best_result[1]

    def read_number_from_region(
        self,
        region: List[int],
        screenshot: Optional[Image.Image] = None,
        expected_digits: Optional[int] = None,
        prefer_components: bool = False,
    ) -> Optional[int]:
        """
        영역에서 여러 자리 숫자 읽기 (슬라이딩 윈도우 방식)

        Args:
            region: 전체 숫자 영역 [x1, y1, x2, y2]
            screenshot: 미리 캡처한 스크린샷

        Returns:
            인식된 숫자 또는 None
        """
        if cv2 is None or not self.templates:
            logger.debug(f"[템플릿] 매칭 불가: cv2={cv2 is not None}, templates={len(self.templates)}")
            return None

        try:
            if screenshot is None:
                screenshot = _safe_grab()
                if screenshot is None:
                    logger.error("[템플릿] 스크린샷 캡처 실패 (타임아웃)")
                    return None

            x1, y1, x2, y2 = region
            cropped = screenshot.crop((x1, y1, x2, y2))
            cropped_np = np.array(cropped)

            # 그레이스케일 변환
            if len(cropped_np.shape) == 3:
                gray = cv2.cvtColor(cropped_np, cv2.COLOR_RGB2GRAY)
            else:
                gray = cropped_np

            # 이진화 (여러 방법 시도)
            _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            binary_inv = cv2.bitwise_not(binary)

            if prefer_components and expected_digits:
                for component_mask in (binary_inv, binary):
                    result = self._read_number_by_components(component_mask, expected_digits=expected_digits)
                    if result is not None:
                        return result

            # 두 가지 버전으로 시도
            for img_binary in [binary, binary_inv]:
                result = self._find_digits_sliding_window(img_binary, max_digits=expected_digits)
                if result is not None:
                    return result

            # 디버그용 이미지 저장 (처음 몇 번만)
            if not hasattr(self, '_debug_count'):
                self._debug_count = 0
            if self._debug_count < 3:
                self._debug_count += 1
                debug_path = TEMPLATE_DIR / f"debug_region_{self._debug_count}.png"
                cv2.imwrite(str(debug_path), binary)
                logger.info(f"[템플릿] 디버그 이미지 저장: {debug_path}")

            logger.debug(f"[템플릿] 인식된 숫자 없음")
            return None

        except Exception as e:
            logger.error(f"[템플릿] 숫자 읽기 오류: {e}")
            return None

    def _find_digits_sliding_window(
        self,
        binary: np.ndarray,
        threshold: float = 0.35,
        max_digits: Optional[int] = None,
    ) -> Optional[int]:
        """
        슬라이딩 윈도우 방식으로 숫자 찾기

        Args:
            binary: 이진화된 이미지
            threshold: 매칭 임계값

        Returns:
            인식된 숫자 또는 None
        """
        if not self.templates:
            return None

        img_h, img_w = binary.shape[:2]

        # 템플릿 평균 너비 계산 (최대 자릿수 제한용)
        avg_tmpl_w, _ = self._get_template_size()
        if max_digits is None:
            max_digits = max(1, int(img_w / (avg_tmpl_w * 0.7)))  # 영역 너비 기준 최대 자릿수
        else:
            max_digits = max(1, int(max_digits))

        # 모든 템플릿에 대해 멀티스케일 매칭
        all_matches = []  # (center_x, x_pos, digit, score, width)

        for digit, template in self.templates.items():
            tmpl_h, tmpl_w = template.shape[:2]

            # 다양한 스케일로 시도 (스케일 범위 축소)
            for scale in [0.9, 1.0, 1.1]:
                new_w = max(3, int(tmpl_w * scale))
                new_h = max(3, int(tmpl_h * scale))

                # 이미지보다 템플릿이 크면 스킵
                if new_w > img_w or new_h > img_h:
                    continue

                try:
                    scaled_template = cv2.resize(template, (new_w, new_h), interpolation=cv2.INTER_AREA)

                    # 템플릿 매칭
                    result = cv2.matchTemplate(binary, scaled_template, cv2.TM_CCOEFF_NORMED)
                    time.sleep(0)  # GIL 해제

                    # 각 x 위치에서 최대값만 사용 (같은 숫자 중복 방지)
                    for pt_x in range(result.shape[1]):
                        col_max = np.max(result[:, pt_x])
                        if col_max >= threshold:
                            pt_y = np.argmax(result[:, pt_x])
                            score = col_max
                            center_x = pt_x + new_w // 2
                            all_matches.append((center_x, pt_x, digit, score, new_w))

                except Exception as e:
                    logger.debug(f"[템플릿] 스케일 {scale} 매칭 오류: {e}")
                    continue

        if not all_matches:
            return None

        # NMS (Non-Maximum Suppression) - 겹치는 매칭 제거
        all_matches.sort(key=lambda x: x[3], reverse=True)  # 점수순 정렬

        final_matches = []
        used_ranges = []  # (start_x, end_x) 범위 저장

        for center_x, x_pos, digit, score, width in all_matches:
            # 최대 자릿수 제한
            if len(final_matches) >= max_digits:
                break

            # 이미 사용된 범위와 겹치는지 확인
            is_overlap = False
            new_start = x_pos
            new_end = x_pos + width

            for start_x, end_x in used_ranges:
                # 범위가 겹치는지 확인 (50% 이상 겹치면 제외)
                overlap_start = max(new_start, start_x)
                overlap_end = min(new_end, end_x)
                overlap_width = max(0, overlap_end - overlap_start)

                if overlap_width > width * 0.5:  # 50% 이상 겹치면 제외
                    is_overlap = True
                    break

            if not is_overlap:
                final_matches.append((x_pos, digit, score, width))
                used_ranges.append((new_start, new_end))

        if not final_matches:
            return None

        # x 좌표순으로 정렬하여 숫자 조합
        final_matches.sort(key=lambda x: x[0])

        # 인접한 숫자들 간의 간격 검증 (너무 가까우면 중복일 가능성)
        validated_matches = [final_matches[0]]
        for i in range(1, len(final_matches)):
            prev_x, prev_digit, prev_score, prev_width = validated_matches[-1]
            curr_x, curr_digit, curr_score, curr_width = final_matches[i]

            # 이전 숫자의 끝 위치와 현재 숫자의 시작 위치 비교
            gap = curr_x - (prev_x + prev_width)

            # 간격이 너비의 -30% 이상이면 유효 (약간 겹쳐도 허용)
            if gap >= -prev_width * 0.3:
                validated_matches.append(final_matches[i])

        digits = [m[1] for m in validated_matches]
        scores = [m[2] for m in validated_matches]

        if digits:
            result = int(''.join(digits))
            avg_score = sum(scores) / len(scores)
            logger.debug(f"[템플릿] 인식: {result} (신뢰도: {avg_score:.2f})")
            return result

        return None

    @staticmethod
    def _is_valid_region(region) -> bool:
        try:
            return (
                isinstance(region, (list, tuple)) and
                len(region) == 4 and
                int(region[2]) > int(region[0]) and
                int(region[3]) > int(region[1])
            )
        except Exception:
            return False

    @staticmethod
    def _normalize_anchor_offset(offset: Optional[List[int]]) -> Optional[List[int]]:
        try:
            if not isinstance(offset, (list, tuple)) or len(offset) != 4:
                return None
            normalized = [int(v) for v in offset]
            if normalized[2] <= normalized[0] or normalized[3] <= normalized[1]:
                return None
            return normalized
        except Exception:
            return None

    @staticmethod
    def _clamp_region(region: List[int], image_size: Tuple[int, int]) -> Optional[List[int]]:
        try:
            width, height = image_size
            x1 = max(0, min(int(region[0]), width))
            y1 = max(0, min(int(region[1]), height))
            x2 = max(0, min(int(region[2]), width))
            y2 = max(0, min(int(region[3]), height))
            if x2 <= x1 or y2 <= y1:
                return None
            return [x1, y1, x2, y2]
        except Exception:
            return None

    def find_anchor_bbox(
        self,
        screenshot: Image.Image,
        anchor_image_path: Optional[str],
        *,
        threshold: float = 0.72,
        search_region: Optional[List[int]] = None,
    ) -> Optional[Tuple[int, int, int, int, float]]:
        """Find a coordinate label anchor image and return (x1, y1, x2, y2, score)."""
        if cv2 is None or not anchor_image_path:
            return None
        try:
            path = Path(str(anchor_image_path))
            if not path.exists():
                return None
            with Image.open(path) as template_img:
                template_rgb = template_img.convert("RGB")
            screen_rgb = screenshot.convert("RGB")
            origin_x = 0
            origin_y = 0
            if self._is_valid_region(search_region):
                clamped_search = self._clamp_region(list(search_region), screen_rgb.size)
                if clamped_search is not None:
                    origin_x, origin_y = clamped_search[0], clamped_search[1]
                    screen_rgb = screen_rgb.crop(tuple(clamped_search))
            screen_arr = np.array(screen_rgb)
            template_arr = np.array(template_rgb)
            th, tw = template_arr.shape[:2]
            sh, sw = screen_arr.shape[:2]
            if th <= 0 or tw <= 0 or th > sh or tw > sw:
                return None
            result = cv2.matchTemplate(screen_arr, template_arr, cv2.TM_CCOEFF_NORMED)
            _min_val, max_val, _min_loc, max_loc = cv2.minMaxLoc(result)
            if float(max_val) < float(threshold):
                return None
            x1, y1 = int(max_loc[0]) + origin_x, int(max_loc[1]) + origin_y
            return (x1, y1, x1 + int(tw), y1 + int(th), float(max_val))
        except Exception as e:
            logger.debug(f"[템플릿] 좌표 앵커 탐색 실패: {e}")
            return None

    def _resolve_anchor_region(
        self,
        screenshot: Image.Image,
        manual_region: Optional[List[int]],
        anchor_image_path: Optional[str],
        anchor_offset: Optional[List[int]],
        *,
        threshold: float = 0.72,
    ) -> Tuple[Optional[List[int]], dict]:
        meta = {
            "anchor_image": str(anchor_image_path or ""),
            "anchor_found": False,
            "manual_region": list(manual_region) if self._is_valid_region(manual_region) else None,
        }
        if not self._is_valid_region(manual_region):
            meta["error"] = "invalid_manual_region"
            return None, meta
        if not anchor_image_path:
            meta["region"] = list(manual_region)
            return list(manual_region), meta

        # Preferred mode: the selected region contains the label anchor and its digits.
        # Read only the area to the right of the anchor inside that same region.
        bbox = self.find_anchor_bbox(
            screenshot,
            anchor_image_path,
            threshold=threshold,
            search_region=manual_region,
        )
        meta["anchor_search_region"] = self._clamp_region(list(manual_region), screenshot.size)
        if bbox is not None:
            ax1, ay1, ax2, ay2, score = bbox
            meta["anchor_found"] = True
            meta["anchor_bbox"] = [ax1, ay1, ax2, ay2]
            meta["anchor_score"] = score

            offset = self._normalize_anchor_offset(anchor_offset)
            if offset:
                region = [ax1 + offset[0], ay1 + offset[1], ax1 + offset[2], ay1 + offset[3]]
                meta["offset"] = list(offset)
            else:
                region = [ax2 + 1, int(manual_region[1]), int(manual_region[2]), int(manual_region[3])]
                meta["offset"] = None
                meta["mode"] = "anchor_inside_region"

            clamped = self._clamp_region(region, screenshot.size)
            if clamped is not None and (int(clamped[2]) - int(clamped[0])) >= 4:
                meta["region"] = clamped
                return clamped, meta

        # Legacy compatibility: older configs selected only the digit box and used
        # nearby X/Y label images as external anchors.
        anchor_search_region = [
            int(manual_region[0]) - 90,
            int(manual_region[1]) - 45,
            int(manual_region[2]) + 30,
            int(manual_region[3]) + 45,
        ]
        bbox = self.find_anchor_bbox(
            screenshot,
            anchor_image_path,
            threshold=threshold,
            search_region=anchor_search_region,
        )
        meta["anchor_search_region"] = self._clamp_region(anchor_search_region, screenshot.size)
        if bbox is None:
            meta["region"] = list(manual_region)
            meta["fallback"] = "manual_region"
            return list(manual_region), meta

        ax1, ay1, ax2, ay2, score = bbox
        meta["anchor_found"] = True
        meta["anchor_bbox"] = [ax1, ay1, ax2, ay2]
        meta["anchor_score"] = score

        offset = self._normalize_anchor_offset(anchor_offset)
        if offset:
            region = [ax1 + offset[0], ay1 + offset[1], ax1 + offset[2], ay1 + offset[3]]
            meta["offset"] = list(offset)
        else:
            width = int(manual_region[2]) - int(manual_region[0])
            height = int(manual_region[3]) - int(manual_region[1])
            region = [ax2 + 2, ay1, ax2 + 2 + width, ay1 + height]
            meta["offset"] = None
            meta["fallback"] = "anchor_right_region_size"

        clamped = self._clamp_region(region, screenshot.size)
        if clamped is None:
            meta["region"] = list(manual_region)
            meta["fallback"] = "manual_region_after_clamp_fail"
            return list(manual_region), meta
        meta["region"] = clamped
        return clamped, meta

    def _resolve_anchor_pair_regions(
        self,
        screenshot: Image.Image,
        search_region: Optional[List[int]],
        x_anchor_image: Optional[str],
        y_anchor_image: Optional[str],
        *,
        threshold: float = 0.72,
    ) -> Tuple[Optional[List[int]], Optional[List[int]], dict]:
        meta = {
            "search_region": None,
            "x_anchor_found": False,
            "y_anchor_found": False,
        }
        if not self._is_valid_region(search_region):
            meta["error"] = "invalid_anchor_search_region"
            return None, None, meta

        clamped_search = self._clamp_region(list(search_region), screenshot.size)
        if clamped_search is None:
            meta["error"] = "invalid_anchor_search_region"
            return None, None, meta
        meta["search_region"] = clamped_search

        x_bbox = self.find_anchor_bbox(
            screenshot,
            x_anchor_image,
            threshold=threshold,
            search_region=clamped_search,
        )
        y_bbox = self.find_anchor_bbox(
            screenshot,
            y_anchor_image,
            threshold=threshold,
            search_region=clamped_search,
        )
        if x_bbox is None or y_bbox is None:
            meta["x_anchor_found"] = x_bbox is not None
            meta["y_anchor_found"] = y_bbox is not None
            meta["fallback"] = "manual_regions"
            return None, None, meta

        xx1, xy1, xx2, xy2, x_score = x_bbox
        yx1, yy1, yx2, yy2, y_score = y_bbox
        meta.update(
            {
                "x_anchor_found": True,
                "y_anchor_found": True,
                "x_anchor_bbox": [xx1, xy1, xx2, xy2],
                "y_anchor_bbox": [yx1, yy1, yx2, yy2],
                "x_anchor_score": x_score,
                "y_anchor_score": y_score,
            }
        )

        if yx1 <= xx2:
            meta["error"] = "anchor_order_invalid"
            meta["fallback"] = "manual_regions"
            return None, None, meta

        sx1, sy1, sx2, sy2 = clamped_search
        pad = 1
        x_region = [xx2 + pad, sy1, yx1 - pad, sy2]
        y_region = [yx2 + pad, sy1, sx2, sy2]
        x_region = self._clamp_region(x_region, screenshot.size)
        y_region = self._clamp_region(y_region, screenshot.size)
        if x_region is None or y_region is None:
            meta["error"] = "anchor_digit_region_invalid"
            meta["fallback"] = "manual_regions"
            return None, None, meta

        meta["x_region"] = x_region
        meta["y_region"] = y_region
        return x_region, y_region, meta

    def read_both_coordinates(
        self,
        x_region: Optional[List[int]],
        y_region: Optional[List[int]],
        x_prefix: str = "X",
        y_prefix: str = "Y",
        stop_event=None,
        x_anchor_image: Optional[str] = None,
        y_anchor_image: Optional[str] = None,
        x_anchor_offset: Optional[List[int]] = None,
        y_anchor_offset: Optional[List[int]] = None,
        anchor_search_region: Optional[List[int]] = None,
        anchor_threshold: float = 0.72,
    ) -> Tuple[Optional[int], Optional[int]]:
        """
        X, Y 좌표 동시 읽기 (OCR 대체)

        한 번의 스크린샷으로 두 좌표를 모두 읽습니다.

        Args:
            x_region: X 좌표 영역 [x1, y1, x2, y2]
            y_region: Y 좌표 영역 [x1, y1, x2, y2]
            x_prefix: X 좌표 접두사 (사용되지 않음, 호환성용)
            y_prefix: Y 좌표 접두사 (사용되지 않음, 호환성용)
            stop_event: 중지 이벤트 (설정 시 즉시 반환)

        Returns:
            Tuple[Optional[int], Optional[int]]: (X 좌표, Y 좌표)
        """
        try:
            screenshot = _safe_grab(stop_event=stop_event)
            if screenshot is None:
                if not (stop_event and stop_event.is_set()):
                    logger.error("[템플릿] 스크린샷 캡처 실패 (타임아웃)")
                return None, None
        except Exception as e:
            logger.error(f"[템플릿] 스크린샷 캡처 실패: {e}")
            return None, None

        # 스크린샷 캐시 (보스 이미지 검색 등에서 재사용)
        self._last_screenshot = screenshot

        pair_meta = None
        x_read_region = None
        y_read_region = None
        if x_anchor_image and y_anchor_image and self._is_valid_region(anchor_search_region):
            x_read_region, y_read_region, pair_meta = self._resolve_anchor_pair_regions(
                screenshot,
                anchor_search_region,
                x_anchor_image,
                y_anchor_image,
                threshold=anchor_threshold,
            )

        if not self._is_valid_region(x_read_region) or not self._is_valid_region(y_read_region):
            x_read_region, x_anchor_meta = self._resolve_anchor_region(
                screenshot,
                x_region,
                x_anchor_image,
                x_anchor_offset,
                threshold=anchor_threshold,
            )
            y_read_region, y_anchor_meta = self._resolve_anchor_region(
                screenshot,
                y_region,
                y_anchor_image,
                y_anchor_offset,
                threshold=anchor_threshold,
            )
            method = "anchor" if (x_anchor_image or y_anchor_image) else "individual"
        else:
            x_anchor_meta = pair_meta
            y_anchor_meta = pair_meta
            method = "anchor_pair"

        if not self._is_valid_region(x_read_region) or not self._is_valid_region(y_read_region):
            self.last_coordinate_read_meta = {
                "method": method,
                "success": False,
                "error": "invalid_individual_region",
                "x_anchor": x_anchor_meta,
                "y_anchor": y_anchor_meta,
                "anchor_pair": pair_meta,
            }
            return None, None

        x_coord = self.read_number_from_region(
            x_read_region,
            screenshot,
            expected_digits=3,
            prefer_components=True,
        )
        y_coord = self.read_number_from_region(
            y_read_region,
            screenshot,
            expected_digits=3,
            prefer_components=True,
        )
        self.last_coordinate_read_meta = {
            "method": method,
            "success": x_coord is not None and y_coord is not None,
            "x_region": list(x_read_region),
            "y_region": list(y_read_region),
            "x_anchor": x_anchor_meta,
            "y_anchor": y_anchor_meta,
            "anchor_pair": pair_meta,
        }

        return x_coord, y_coord


# 전역 인스턴스
_matcher_instance: Optional[DigitTemplateMatcher] = None


def get_digit_matcher() -> DigitTemplateMatcher:
    """템플릿 매처 인스턴스 가져오기 (싱글톤)"""
    global _matcher_instance
    if _matcher_instance is None:
        _matcher_instance = DigitTemplateMatcher()
    return _matcher_instance
